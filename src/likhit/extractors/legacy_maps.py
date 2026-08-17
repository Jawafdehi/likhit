"""Legacy Nepali font conversion helpers."""

from __future__ import annotations

from functools import lru_cache
import os
import re
import threading
from typing import Callable
import warnings

from likhit.errors import ExtractionError

# Font name -> npttf2utf map key. A font NAME is only ever a hint about the encoding
# the bytes actually use, and for one family the hint is wrong: names in the "Himalb"
# family carry PREETI-encoded bytes while naming Himali.
#
# Preeti and FONTASY_HIMALI_TT are near-clones that EXCHANGE their two number rows --
# each map's unshifted digit row is the other's shifted row, exactly (asserted in
# tests/test_legacy_maps.py). So the wrong one of the pair is silently destructive in
# both directions: it puts a Devanagari digit inside a word, and it puts a consonant
# where a year belongs. Neither shows up as damage -- both characters are Devanagari,
# so there is no U+FFFD, no drop in the Devanagari ratio, and no garble tell.
#
# Measured over the 13 CIAA annual reports, per name rather than pooled -- pooling is
# what made the first version of this correction move two names too many:
#
#   name           spans   alpha chars   in-word Devanagari digits
#                                        under Preeti   under FONTASY_HIMALI_TT
#   Himalb         15,831       30,258              0                    1,021
#   Himalb,Bold     2,360        9,243              0                      417
#
# 1,438 corruptions under the shipped routing, none under Preeti. "spans" counts text
# spans whose font name reaches the "himalb" key; "alpha chars" counts ASCII ALPHABETIC
# keystrokes in those spans (not total characters, which are 87,020 and 18,767).
#
# The cost, measured on the same spans rather than assumed. All 6 "-N_" list markers in
# these spans get WORSE: both maps read '-'/'_' as the real brackets, but the interior
# digit becomes a consonant, so "(५)" -> "(छ)" in every one of the six -- checked
# individually, Preeti's marker carries no Devanagari digit in any of them. Zero
# whole-span ASCII "(N)" markers are affected, so the gate below loses nothing.
#
# Against that, 4 of those 6 spans carry a body beyond the marker and 3 of the 4 bodies
# are REPAIRED, e.g. "भि८ियो ... सीसी६ीभीको" -> "भिडियो ... सीसीटीभीको". So the trade is
# 6 marker digits for 3 repaired bodies plus the 1,438 above.
#
# 🛑 The discriminator used above is BLIND to a numeral-only font: with no surrounding
# letters a pure-numeral span scores zero under BOTH maps, so "0 under Preeti" means
# "no evidence", not "clean". The "Fontasy*" spellings carry 1 and 0 ASCII alphabetic
# keystrokes across 26,699 and 19,744 characters of span text, so nothing here licenses
# moving them and they stay on the Himali map. A metric that cannot tell *clean* from
# *not applicable* must not be read as support.
_REGISTRY: dict[str, str] = {
    "preeti": "Preeti",
    "fontasy_himali": "FONTASY_HIMALI_TT",
    "fontasyhimali": "FONTASY_HIMALI_TT",
    "himali": "FONTASY_HIMALI_TT",
    # PREETI-encoded despite naming Himali. Reached by "Himalb", "Himalb,Bold" and
    # "HimalBold" -- _match_font strips a ",Bold" suffix and matches on a substring.
    "himalb": "Preeti",
    "kantipur": "Kantipur",
    "pcs nepali": "PCS NEPALI",
    "pcs_nepali": "PCS NEPALI",
    "pcsnepali": "PCS NEPALI",
    "sagarmatha": "Sagarmatha",
    # VOL-704. "ARAP 11" is a legacy Devanagari keystroke font, not a symbol
    # font, despite carrying a symbol-style (3,0) cmap that pushes its bytes into
    # the private use area as byte + 0xF000. Its spans therefore arrive as
    # U+F020-U+F0FF and must be un-lifted before conversion -- see
    # `pua_maps.unlift_symbol_pua`, applied on the legacy path in
    # `font_based._convert_span_text`.
    #
    # Evidence it is a text font, not a symbol font: name table family
    # "ARAP 11", PANOSE bFamilyType=0 (text) against 5 (pictorial) for every
    # Symbol subset in the same corpus, 100% of its output in the PUA (1,363 of
    # 1,363 glyphs) in long unbroken runs rather than isolated glyphs, and
    # Devanagari letterform contours when the glyphs are rendered.
    #
    # FONTASY_HIMALI_TT rather than Preeti, decided on two external oracles and
    # not on the content gate -- `choose_legacy_map` scores every map at hits=2
    # and cannot separate them, so it picks Preeti, which is wrong:
    #
    #   1. Preeti/Kantipur/Sagarmatha map `# * ) &` to the Devanagari digits
    #      `३ ८ ० ७`, emitting 10 digits across two pages of proper names and
    #      titles ("८ा. ग०ोशराज" for "डा. गणेशराज"). FONTASY_HIMALI_TT and
    #      PCS NEPALI emit 0.
    #   2. Between those two, the corpus's own correctly-encoded Unicode text is
    #      the oracle: FONTASY_HIMALI_TT reads `b'?kof]u` as दुरुपयोग, which the
    #      13 reports spell that way 3,449 times, against PCS NEPALI's दुरूपयोग
    #      at 30. Every other load-bearing token is identical under both.
    "arap": "FONTASY_HIMALI_TT",
    # Siddhi is its own layout, NOT a name for one of the five npttf2utf maps.
    # It matches both spellings the corpus carries -- 'Siddhi' and 'SiddhiNormal'
    # -- because _match_font is a substring test. See SIDDHI_MAP_KEY below for the
    # table and how it was derived.
    #
    # Routed by NAME on purpose. `classify_font` returns "legacy_remap" for
    # anything in this registry and `detect_content_legacy_fonts` skips every font
    # that is not "correct", so a registry entry takes these faces OUT of the
    # content-based candidate ranking entirely. That is what keeps the blast
    # radius to the 79 documents that carry the name: adding the key to
    # ALL_MAP_KEYS instead would put a seventh candidate in front of every
    # gate-passing aggregate in the corpus.
    #
    # The `fontasyhimali` trap above does not apply here. That one was a
    # numeral-only face destroyed by moving it to a map with the number rows
    # exchanged; Siddhi's number rows are the NORMAL ones, so its numeral-only
    # aggregates -- and several of the 79 are almost pure figures, e.g.
    # '158.25 67235.67 0 0' in 3120__इलाम नगरपालिका -- decode to Devanagari
    # digits with a real decimal point, which is what the page draws.
    "siddhi": "Siddhi",
}

#: Map key for the Spins layout, which npttf2utf does not ship. It is not an
#: npttf2utf key, so everything that consumes a map key must go through
#: :func:`get_converter_for_map`.
SPINS_MAP_KEY = "Spins"

#: The npttf2utf map the Spins layout is a permutation of.
_SPINS_BASE_MAP_KEY = "Preeti"

# Spins reads as Preeti except on three keyboard pairs, which are rotated: its
# "-" is Preeti's "=", its "=" is Preeti's "[", its "[" is Preeti's "-", and the
# shifted forms rotate the same way. So the layout is expressed by translating
# those six codes and then running the Preeti map.
#
# Derived by measurement on OAG's annual reports, not from a font specification.
# On the 2070 report's 113k-character body font, the rotation takes canonical-term
# findability from 10/17 to 17/17 -- it is what recovers the repha in वार्षिक,
# कार्यालय and अर्थ, the anusvara in संस्था, the ृ in स्वीकृत, and the decimal point in
# every figure (५९(९८ -> ५९.९८) -- while dictionary hits rise 22 -> 25 and the
# garble penalty per Devanagari falls 0.030 -> 0.012. Independent of any word
# list: parentheses go from 1,978 "(" against 368 ")" to a balanced 102/102, which
# is what a rotation that had put the real "." on the "(" key would produce.
#
# ``"<": "/"`` is a seventh entry and belongs to a different measurement: on
# ``Spins_EXT`` the ``0x3c`` slot draws र, page-verified on OAG ``3861`` p5
# (``gu< k|x<L xjnbf<`` drawing नगर प्रहरी हवलदार).
# It is spelled out here rather than inherited from Preeti because Preeti's own
# ``0x3c`` draws a question mark and decodes faithfully as one -- see
# :data:`_RA_KEYSTROKE_MAPS`. `str.translate` is simultaneous, so this cannot chain
# with the rotation above, and nothing in the rotation maps TO ``"<"``.
_SPINS_TO_PREETI_KEYS = str.maketrans(
    {"-": "=", "=": "[", "[": "-", "_": "+", "+": "{", "{": "_", "<": "/"}
)

#: Map key for the Siddhi layout, which npttf2utf does not ship either. Like
#: :data:`SPINS_MAP_KEY` it is not an npttf2utf key, so it must go through
#: :func:`get_converter_for_map`.
SIDDHI_MAP_KEY = "Siddhi"

#: The npttf2utf map the Siddhi layout is a permutation of. NOT Preeti: Siddhi's
#: number rows are Himali's, which is what picks the base.
_SIDDHI_BASE_MAP_KEY = "FONTASY_HIMALI_TT"

# Siddhi reads as FONTASY_HIMALI_TT except on six codes. Five of them are a key
# translation; the sixth cannot be one and is handled below.
#
# Derived by measurement (VOL-471, run d0121829), from rendered page pixels and
# from per-glyph crops of the embedded faces -- not from a font specification, and
# not by inference from a sibling map. Record:
# oag-corpus/runs/vol471-d0121829/{DERIVE-d0121829.json,CHECKPOINT-2-layout.md}.
#
#   code        page draws   base gives   translated to   evidence
#   '-' 0x2d    '-'          '('          (see below)     ट- सडक वोर्ड, ङ- संघीय
#   '.' 0x2e    '.'          '।'          '=' 0x3d        २. राजश्व, ३१०७९३३३.१०
#   '/' 0x2f    '/'          'र'          '÷' 0xf7        अमर/मजदुर जे.भि सुर्खेत
#   '<' 0x3c    'र'          '?'          '/' 0x2f        रकम, राजश्व, गरिवसंग
#   '_' 0x5f    'ं'          ')'          '+' 0x2b        पुंजिगत, संघ, ५ नं
#   'Š' 0x160   'ड'          'Š' (passes) '*' 0x2a        सडक, वाडफाट, बांडफांड
#
# The base is Himali and not Preeti because Siddhi's number rows are the normal
# ones: '235187839' draws २३५१८७८३९ on page 4 of 2688 (Preeti gives द्दघछज्ञडठडघढ),
# and the sub-item markers on page 4 of 2835 run क ख ग घ ङ च छ ... ट as
# 's- v- u- #- ª- r- %- ^-', which is Devanagari alphabetical order only under the
# Himali shifted row. That argument uses no word list.
#
# 0x2e is a Siddhi fact and not a table artefact: the SAME document's Preeti face
# draws a danda at 0x2e (a tall vertical bar) where the Siddhi face draws a square
# baseline dot. Both crops are in the record.
_SIDDHI_TO_HIMALI_KEYS = str.maketrans(
    {".": "=", "/": "÷", "<": "/", "_": "+", "Š": "*"}
)

# 0x2d is the exception, and it is not an oversight. Siddhi draws a HYPHEN there
# (glyph crop: a horizontal bar at mid height, 46 occurrences in 2835 alone) and
# **no map in the family emits "-" at any codepoint in 0x00-0x2FFF** -- swept, all
# six maps, zero hits -- so there is no code to translate 0x2d to. It is therefore
# handled as a separator: split the source on it, map each part, rejoin with the
# literal. That is exact because "-" never participates in a Devanagari cluster,
# and it is the only shape available without inventing a sentinel codepoint.
_SIDDHI_LITERAL_SEPARATOR = "-"

# Every legacy map content-based (name-agnostic) detection tries against a span.
# Order is NOT a tie-break. It was once, implicitly -- `choose_legacy_map` kept the
# first strict maximum, so two maps level on every axis were separated by their
# position here, and Spins being last meant it lost every exact tie to Preeti. That
# decided real documents wrongly on small spans (VOL-77).
#
# ⚠️ Two separate remedies replaced it and this comment used to run them together: the
# two added ranking AXES (`ratio`, then `devanagari`) resolve the VOL-77 document itself
# -- measured, it decodes as Spins and the abstention branch never runs on it -- and
# abstention handles only the residue no axis separates. See `choose_legacy_map`, which
# also records what abstention costs on this corpus.
#
# These tuples only fix the order candidates are walked and reported in; nothing
# behavioural depends on it.
#: The maps npttf2utf actually ships, i.e. the ones with an upstream table in its
#: vendored ``map.json``. Kept separate from :data:`ALL_MAP_KEYS` because
#: :data:`SPINS_MAP_KEY` has no upstream: a test that asks "does our compiled pipeline
#: agree with upstream" or "does this map use the translate fast path" has nothing to
#: compare against for a synthesised map, and sweeping it there fails for the wrong
#: reason.
SHIPPED_MAP_KEYS: tuple[str, ...] = (
    "Preeti",
    "Kantipur",
    "PCS NEPALI",
    "FONTASY_HIMALI_TT",
    "Sagarmatha",
)

#: ``0x3c`` is a KEY, and which key it is depends on the FACE. That is the whole of this
#: block, and an earlier form of it got the direction wrong for the larger population.
#:
#: Every map in the family has exactly one source code point whose decode is a bare
#: ``?`` -- measured over 0x00-0x2FFF, not just the byte range. For five of the six it is
#: ``0x3c`` (Preeti, Kantipur, FONTASY_HIMALI_TT, Sagarmatha, and Spins through Preeti's
#: table); for ``PCS NEPALI`` it is ``0xa9``.
#:
#: \ud83d\uded1 **A bare ``?`` in the output is NOT always damage.** On the faces that dominate
#: both downstream corpora ``0x3c`` draws a real question mark, so npttf2utf's table is
#: right and ``?`` is the faithful read. The embedded ``BOFDOE+Preeti`` was extracted and
#: its slots rendered at 200 dpi: ``0x2f`` draws ``\u0930``, ``0x3f`` draws
#: ``\u0930\u0941``, and ``0x3c`` is a genuine two-contour question mark. Page-verified
#: ``?`` on ``Preeti``, ``Preeti,Bold`` and ``Kantipur`` across three corpora --
#: **914 occurrences under a formerly-repaired map key, 541 of them isolated, over 101
#: doc-font pairs** in all 6,236 OAG documents and all 35 CIAA reports. The decisive one
#: is OAG ``11115`` p296, where the sentence holding the mark reads
#: ``\u092d\u0928\u094d\u0928\u0947 \u092a\u094d\u0930\u0936\u094d\u0928\u092e\u093e``
#: ("in the question") -- so the ``?`` is not an inference from glyph shape at all.
#:
#: The converse population is equally real, which is why this is a per-face table and not
#: a deletion: ``0x3c`` is page-verified ``\u0930`` on ``FONTASY_ HIMALI_ TT`` (OAG
#: ``2335`` p21) and on ``Spins_EXT`` (OAG ``3861`` p5, ``gu< k|x<L xjnbf<`` drawing
#: \u0928\u0917\u0930 \u092a\u094d\u0930\u0939\u0930\u0940 \u0939\u0935\u0932\u0926\u093e\u0930).
#: 24 of the 101 doc-font pairs carry a word-internal ``0x3c``, 412 occurrences.
#:
#: \u26a0\ufe0f Position is NOT a proxy for what the face draws -- word-internal ``0x3c`` that the
#: page renders as ``?`` exists (OAG ``2933``, ``3699``), and isolated ``0x3c`` that the
#: page renders as ``\u0930`` exists. Only a per-face read decides, so do not reach for
#: the isolated/in-word split as a discriminator.
#:
#: ``Sagarmatha`` is deliberately absent: there is no page read for its ``0x3c`` either
#: way, and it has zero occurrences in both corpora. Repairing on a sibling inference is
#: the exact reasoning that made the original report of this defect wrong about which map
#: was the outlier -- the same reason ``PCS NEPALI`` (gap at ``0xa9``) stays out.
_RA_KEYSTROKE_MAPS: frozenset[str] = frozenset({"FONTASY_HIMALI_TT"})

#: ``0x3c`` -> ``0x2f``, applied to the keystrokes BEFORE the table decode.
#:
#: This is the shape ``Siddhi`` already uses for the identical case -- see
#: :data:`_SIDDHI_TO_HIMALI_KEYS`, whose ``'<' -> '/'`` entry is exactly this -- and it is
#: better than substituting on the output for two reasons. It cannot touch a ``?`` that
#: any other source produced, and it lets npttf2utf reorder the cluster from the right
#: consonant rather than around a hole. The reordering worry that motivated the output
#: form does not apply: it was about SPLITTING the span at ``0x3c`` and converting the
#: pieces, which strands a prefix matra. Translating one key does not split anything --
#: measured, ``ul<`` gives ``\u0917\u0930\u093f`` either way.
_RA_KEYSTROKE_TRANSLATION = str.maketrans({"<": "/"})

#: Maps this library MODELS rather than gets from npttf2utf: a layout upstream does not
#: ship, expressed as a key translation onto a shipped map. There are two, and they work
#: the same way -- translate the keystrokes, then decode with the base map -- so they get
#: a name instead of being spelled out as exceptions wherever the distinction matters.
#:
#: The distinction is load-bearing for tests: anything asking "does this agree with
#: upstream" or "does this use the compiled translate fast path" has nothing to compare
#: against for these two, and `_get_compiled_map` raises for them.
SYNTHESISED_MAP_KEYS: tuple[str, ...] = (SPINS_MAP_KEY, SIDDHI_MAP_KEY)

#: Every map CONTENT-BASED detection may choose. Note what is missing: Siddhi is
#: decodable but is NOT a candidate here, deliberately. Adding it would put a seventh
#: candidate in front of every gate-passing aggregate in the corpus -- a corpus-wide
#: false-positive surface for a population the font NAME already identifies. That
#: decision is pinned in tests/test_siddhi_layout.py and is the thing to argue with if a
#: later run measures the surface and disagrees.
ALL_MAP_KEYS: tuple[str, ...] = SHIPPED_MAP_KEYS + (SPINS_MAP_KEY,)

#: Every key :func:`get_converter_for_map` accepts. This is a SUPERSET of
#: :data:`ALL_MAP_KEYS`, and the difference is the point: a font can be routed to a map
#: by NAME without that map being a content-detection candidate. The two sets coincided
#: until Siddhi, which is why code that conflated them was correct up to that point.
DECODABLE_MAP_KEYS: tuple[str, ...] = SHIPPED_MAP_KEYS + SYNTHESISED_MAP_KEYS

# No legacy keyboard layout in _REGISTRY puts its own bracket glyph on the
# literal ASCII '(' / ')' keys -- confirmed for FONTASY_HIMALI_TT (whose '('
# key is a keyboard-layout consonant slot, decoding to 'ढ') and for Preeti
# (whose '(' key decodes to '९'); both layouts render a real bracket from '-'
# instead. So when a whole span is nothing but an ASCII-bracketed number -- a
# list/outline marker like "(1)" -- the parens were placed directly by
# whatever authored the numbering, sharing the body font only for visual
# consistency with the digit, not typed on this keyboard layout at all.
# Running the full map over it anyway retargets the parens at whatever
# consonant sits in that layout's slot: Fontasy Himali's "(1)" becomes "ढ१ण्"
# even though the very same map correctly reads a same-shaped "-1_" marker as
# "(१)" (VOL-166). Verified against all 13 CIAA annual reports: this exact
# ASCII-bracketed shape never occurs under any other legacy map or font in
# the corpus, so digit-only conversion here changes no other document.
#
# The gate is therefore about the BRACKETS, and it assumes the digit between them
# is already read correctly by the map. That assumption is a property of the map,
# not of the shape, and it is false for three of the five in ALL_MAP_KEYS -- so the
# gate is applied only where _map_reads_ascii_digits_as_digits() holds. Applying it
# everywhere is what the first version of this fix did, and on Preeti it destroys a
# letter; the docstring on that predicate has the measurement.
_ASCII_BRACKETED_NUMBER = re.compile(r"^(\s*)\((\d+)\)(\s*)$")
_LATIN_TO_DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")

_ASCII_DIGITS = "0123456789"
_DEVANAGARI_DIGITS = "०१२३४५६७८९"
_ascii_digit_maps: dict[str, bool] = {}


def _map_reads_ascii_digits_as_digits(map_key: str) -> bool:
    """Does ``map_key`` decode ASCII ``0``-``9`` to Devanagari ``०``-``९``?

    This is the precondition of the gate above, and it does **not** hold for every
    map. Measured against the maps themselves:

        PCS NEPALI, FONTASY_HIMALI_TT       ->  "०१२३४५६७८९"
        Preeti, Kantipur, Sagarmatha        ->  "ण्ज्ञद्दघद्धछटठडढ"

    On the second family an ASCII digit is a **consonant** keystroke, so the two
    families disagree about what the *interior* of the marker is, not just about the
    brackets:

        PCS NEPALI        "(5)"  ->  "ढ५ण्"   digit already correct, brackets wrong
        Preeti            "(5)"  ->  "९छ०"    the interior IS the letter छ

    That is the whole warrant for the gate. It exists because the map gets the
    brackets wrong while getting the digit right, which is true of the first family
    only. Applied to the second it replaces a letter with a digit -- ``"(5)"``
    becomes ``"(५)"`` and the ``छ`` is **destroyed**, which is strictly worse than
    the defect the gate repairs.

    Derived from the map rather than hardcoded, so a map added to
    :data:`ALL_MAP_KEYS` or :data:`_REGISTRY` is classified by what it actually does
    and this cannot silently go stale. Cached because it costs a map load per key.
    """

    cached = _ascii_digit_maps.get(map_key)
    if cached is None:
        cached = get_converter_for_map(map_key)(_ASCII_DIGITS) == _DEVANAGARI_DIGITS
        _ascii_digit_maps[map_key] = cached
    return cached


def _decode_ascii_bracketed_number(text: str) -> str | None:
    """Digit-only decode for a whole span shaped like ``"(12)"``, else ``None``.

    Callers must first establish :func:`_map_reads_ascii_digits_as_digits` for the
    map in hand; this function cannot check it, because it never sees the map.
    """

    match = _ASCII_BRACKETED_NUMBER.match(text)
    if match is None:
        return None
    lead, digits, trail = match.groups()
    return f"{lead}({digits.translate(_LATIN_TO_DEVANAGARI_DIGITS)}){trail}"


_mapper = None
_mapper_lock = threading.Lock()


def _match_font(font_name: str) -> str | None:
    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    base = base.split(",")[0]
    base_lower = base.lower().strip()
    for key, map_key in _REGISTRY.items():
        if key in base_lower:
            return map_key
    return None


def _get_mapper():
    global _mapper
    if _mapper is not None:
        return _mapper

    # Double-checked lock: build the mapper once even under concurrent PDF
    # conversions. This also confines the process-global warnings-filter mutation
    # in the catch_warnings block below to a single initializing thread.
    with _mapper_lock:
        if _mapper is not None:
            return _mapper

        try:
            # npttf2utf's bundled preetimapper uses a few non-raw string literals
            # ('b\\w' etc.) that emit SyntaxWarning when first compiled. The bug
            # is upstream (a raw-string PR is warranted); suppress it here so it
            # does not leak into our logs/output. The catch_warnings block scopes
            # this to the npttf2utf import only. A ``module=`` filter is
            # intentionally not used: the compile-time warning's module name does
            # not reliably match it, which would let a strict SyntaxWarning filter
            # turn it fatal.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                import npttf2utf
                from npttf2utf.base.fontmapper import FontMapper
        except ModuleNotFoundError as exc:
            raise ExtractionError(
                "npttf2utf is required for legacy Nepali font conversion but is not installed"
            ) from exc

        map_json = os.path.join(os.path.dirname(npttf2utf.__file__), "map.json")
        _mapper = FontMapper(map_json)
    return _mapper


# npttf2utf's own tokenizer, lifted verbatim from FontMapper.map_to_unicode.
# Every character of the input is either \s or \S, and the group makes findall
# return the tokens themselves, so "".join(tokens) == input -- the split is
# lossless and the per-word pipeline below sees exactly what upstream's does.
_WORD_SPLIT = re.compile(r"(\s+|\S+)")

# Bounded so a long corpus run cannot grow it without limit. 65536 is far above
# any single document: every span of the 128-page law-report sample holds 7,899
# distinct words, and five warm caches (one per map, which is what
# choose_legacy_map fills when it scores a span against every candidate) came to
# 39,495 entries and roughly 1.8 MiB.
_WORD_CACHE_SIZE = 65536


class _CompiledMap:
    """One npttf2utf map with its regexes compiled once instead of per word.

    Upstream ``FontMapper.map_to_unicode`` calls ``re.sub(re.compile(rule[0]), ...)``
    **inside** its loop over every word of every span. Each of the five maps
    carries 32 post-rules, so a full conversion of the 128-page ``kanunpatrika.pdf``
    sample -- which routes **9,460** spans through this class -- cost 3.19M
    ``re.sub`` and 6.36M ``re._compile`` calls.

    The speed figures below come from a different, larger instrument: **all 11,268
    non-empty spans** of that document pushed through the Preeti map directly, so
    they are not the 9,460 above and the two counts should not be mixed.

        upstream                          2.362s
        rules compiled once per map       0.445s   5.3x
        plus per-word memoization         0.073s  32.5x

    Two independent wins, and the second is the larger one. Memoizing whole
    *spans* would not have paid -- those 11,268 spans hold 8,757 distinct ones,
    only a 1.3x dedupe -- but words repeat far more: 90,352 word lookups resolve to
    7,899 distinct words, an **11.4x** dedupe (82,453 hits to 7,899 misses),
    because Nepali function words and the whitespace tokens recur on every line.

    End to end that is 3s off a full conversion of the sample: **14.11s -> 11.11s**.

    Output is byte-identical to upstream: 0 differences over 12,154 cases (every
    distinct span of the four legacy samples plus 16 hand-built edge cases) on all
    five maps, 60,770 conversions in total. The unknown-map and ``"unicode"``
    passthrough contracts are preserved too; see
    ``tests/test_legacy_maps_precompiled.py``.

    The rules still come from npttf2utf's own ``map.json`` via
    :func:`_get_mapper`, so that file stays the single source of truth and an
    upstream map change is picked up without touching this class.
    """

    __slots__ = (
        "_character_map",
        "_post_rules",
        "_pre_rules",
        "_translate_table",
        "convert_word",
    )

    def __init__(self, rules: dict) -> None:
        self._pre_rules = tuple(
            (re.compile(pattern), replacement)
            for pattern, replacement in rules["pre-rules"]
        )
        self._post_rules = tuple(
            (re.compile(pattern), replacement)
            for pattern, replacement in rules["post-rules"]
        )
        character_map = rules["character-map"]
        self._character_map = character_map
        # str.maketrans requires single-character keys. All five of npttf2utf's
        # maps satisfy that (measured: 120-144 entries each, every key one
        # character), but fall back to the fold rather than assume it, so a future
        # multi-character key costs speed instead of raising. It would be a dead
        # entry either way -- upstream folds over single characters too.
        self._translate_table = (
            str.maketrans(character_map)
            if all(len(key) == 1 for key in character_map)
            else None
        )
        self.convert_word = lru_cache(maxsize=_WORD_CACHE_SIZE)(self._convert_word)

    def _convert_word(self, word: str) -> str:
        for pattern, replacement in self._pre_rules:
            word = pattern.sub(replacement, word)

        if self._translate_table is not None:
            mapped = word.translate(self._translate_table)
        else:
            get = self._character_map.get
            mapped = "".join(get(character, character) for character in word)

        for pattern, replacement in self._post_rules:
            mapped = pattern.sub(replacement, mapped)
        return mapped

    def convert(self, text: str) -> str:
        convert_word = self.convert_word
        return "".join(convert_word(word) for word in _WORD_SPLIT.findall(text))


_compiled_maps: dict[str, _CompiledMap] = {}
_compiled_maps_lock = threading.Lock()


def _get_compiled_map(map_key: str) -> _CompiledMap:
    """Return the compiled pipeline for ``map_key``, building it once.

    Raises npttf2utf's own ``NoMapForOriginException`` for an unknown key, so
    callers see the same failure they saw when this went through
    ``FontMapper.map_to_unicode`` directly.
    """

    compiled = _compiled_maps.get(map_key)
    if compiled is not None:
        return compiled

    mapper = _get_mapper()
    with _compiled_maps_lock:
        compiled = _compiled_maps.get(map_key)
        if compiled is not None:
            return compiled

        if map_key not in mapper.all_rules:
            from npttf2utf.base.exceptions import NoMapForOriginException

            raise NoMapForOriginException
        compiled = _CompiledMap(mapper.all_rules[map_key]["rules"])
        _compiled_maps[map_key] = compiled
    return compiled


def get_converter(font_name: str) -> Callable[[str], str] | None:
    map_key = _match_font(font_name)
    if map_key is None:
        return None
    return get_output_converter_for_map(map_key)


def get_converter_for_map(map_key: str) -> Callable[[str], str]:
    """Return the **raw** converter for an explicit npttf2utf map key.

    This is the scoring primitive. :func:`choose_legacy_map` runs every candidate
    map over a span and keeps the best-scoring one, and that comparison has to see
    each map's unmodified output -- a gate applied here would change which map
    wins, not just what the winner emits. So this deliberately does **not** carry
    the bracketed-marker gate.

    Anything producing *final text* wants :func:`get_output_converter_for_map`
    instead. The two call sites are easy to conflate, which is how VOL-166's fix
    first shipped covering only one of them: `font_based._convert_span_text` calls
    the content-based path before the name-based one and returns from it directly,
    so an ungated call there reintroduces `"(1)" -> "ढ१ण्"` even with
    :func:`get_converter` fixed.

    An unknown ``map_key`` raises ``NoMapForOriginException`` **here**, at
    construction. That is eager where this used to be lazy: it once returned a
    closure over ``FontMapper.map_to_unicode``, which raised only when the closure
    was called. Nothing in-repo can tell the difference -- every caller passes a
    key from :data:`ALL_MAP_KEYS` or :data:`_REGISTRY` and calls the result
    immediately -- but a caller that builds a converter early and uses it later
    would now fail at build time. :func:`get_output_converter_for_map` inherits
    this, since it resolves its base converter up front.
    """

    # Upstream's own passthrough: map_to_unicode returns the input untouched for a
    # case-insensitive "unicode" origin, before it ever looks at the rules. No
    # _REGISTRY entry or ALL_MAP_KEYS member reaches it, but keep the contract
    # identical -- this function is what both the scoring and output paths call.
    if map_key.lower() == "unicode":
        return lambda text: text

    # SPINS_MAP_KEY is synthesised rather than shipped: npttf2utf has no Spins
    # layout, so its keystrokes are translated onto Preeti's and decoded with Preeti's
    # map. Placed in the RAW converter deliberately -- this is the scoring primitive,
    # and choose_legacy_map has to be able to score Spins against the shipped maps.
    if map_key == SIDDHI_MAP_KEY:
        base_convert = get_converter_for_map(_SIDDHI_BASE_MAP_KEY)

        def _convert_siddhi(text: str) -> str:
            # str.translate is simultaneous, so '<' -> '/' and '/' -> '÷' do not
            # chain; a sequential two-pass replace would send '<' all the way to
            # '÷' and lose every र.
            return _SIDDHI_LITERAL_SEPARATOR.join(
                base_convert(part.translate(_SIDDHI_TO_HIMALI_KEYS))
                for part in text.split(_SIDDHI_LITERAL_SEPARATOR)
            )

        return _convert_siddhi

    if map_key == SPINS_MAP_KEY:
        base_convert = get_converter_for_map(_SPINS_BASE_MAP_KEY)

        def _convert_spins(text: str) -> str:
            return base_convert(text.translate(_SPINS_TO_PREETI_KEYS))

        return _convert_spins

    if map_key in _RA_KEYSTROKE_MAPS:
        base = _get_compiled_map(map_key).convert

        def _convert_ra_keystroke(text: str) -> str:
            # See _RA_KEYSTROKE_MAPS: on THIS face 0x3c draws र, so it is
            # translated to the key that decodes to र before the table runs.
            # Faces where 0x3c draws a question mark are absent from that set and
            # decode faithfully.
            return base(text.translate(_RA_KEYSTROKE_TRANSLATION))

        return _convert_ra_keystroke

    return _get_compiled_map(map_key).convert


def get_output_converter_for_map(map_key: str) -> Callable[[str], str]:
    """Return the converter to use when emitting final text for ``map_key``.

    :func:`get_converter_for_map` plus the bracketed-list-marker gate described
    at :data:`_ASCII_BRACKETED_NUMBER`. Use this from every path that produces
    output, whether the map was chosen by font name or by span content; use the
    raw converter only for scoring.

    The gate is applied only for maps that read ASCII digits as digits -- see
    :func:`_map_reads_ascii_digits_as_digits`, which is where the reasoning lives.
    For the others this returns the raw converter unchanged, so it is exactly
    :func:`get_converter_for_map`.
    """

    base_convert = get_converter_for_map(map_key)
    if not _map_reads_ascii_digits_as_digits(map_key):
        # This map reads an ASCII digit as a consonant, so there is no digit to lift
        # out of the brackets and the gate's premise does not hold. Returning the raw
        # converter is not a fallback: it is the correct reading of the span.
        return base_convert

    def _convert(text: str) -> str:
        decoded = _decode_ascii_bracketed_number(text)
        if decoded is not None:
            return decoded
        return base_convert(text)

    return _convert


def is_legacy_font(font_name: str) -> bool:
    return _match_font(font_name) is not None
