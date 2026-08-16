"""Legacy Nepali font conversion helpers."""

from __future__ import annotations

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
}

# The full set of npttf2utf map keys, used by content-based (name-agnostic)
# legacy-font detection to try every known legacy encoding against a span.
ALL_MAP_KEYS: tuple[str, ...] = (
    "Preeti",
    "Kantipur",
    "PCS NEPALI",
    "FONTASY_HIMALI_TT",
    "Sagarmatha",
)

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
    """

    mapper = _get_mapper()

    def _convert(text: str) -> str:
        return mapper.map_to_unicode(text, from_font=map_key)

    return _convert


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
