"""Private Use Area glyph maps for legacy symbol fonts, keyed by font name.

VOL-704. A PDF that draws a bullet with the Microsoft ``Symbol`` font and carries
a ToUnicode CMap in the conventional "byte + 0xF000" form hands us U+F0B7 and no
way to read it: the codepoint is unassigned, so it renders as a box, no Markdown
parser sees a list, and nothing downstream can tell it from data. Measured on the
CIAA annual-report corpus: 5,689 BMP private-use characters across 13 reports,
4,210 of them U+F0B7, and every one of nine audit axes graded the result `clean`.

**These maps are keyed on FONT NAME, never on codepoint alone, and that is
load-bearing.** In the same corpus U+F020 and U+F029 are each emitted by two
different fonts — ``Symbol`` and ``ARAP 11``, a legacy *Devanagari* font — so the
same codepoint is a symbol in one span and a Nepali space or keystroke in the
next. A codepoint-keyed table would rewrite Nepali text as Greek letters, since
Symbol's 0x66 is `phi` and 0x63 is `chi`. Legacy Devanagari fonts are not handled
here at all; they belong to :mod:`likhit.extractors.legacy_maps`, which this
module deliberately does not touch.

**Arithmetic is not a substitute for a table.** Subtracting 0xF000 is wrong for
exactly the glyphs that matter most: U+F0B7 becomes U+00B7 MIDDLE DOT `·` when
Symbol 0xB7 is really U+2022 BULLET `•`, and U+F02D becomes a hyphen when Symbol
0x2D is U+2212 MINUS. Each entry below therefore records the glyph name from the
font's own encoding, not a computed offset.

Provenance of the tables: the Adobe Symbol and Microsoft Wingdings encodings, and
for a Wingdings glyph the Unicode block that encodes it — Dingbats (U+2700) or
Ornamental Dingbats (U+1F650), the latter added in Unicode 7.0 specifically to
cover the Wingdings/Webdings repertoire. **Check both before concluding a glyph
has no equivalent**; VOL-741 found one recorded as unmappable that is simply
outside the BMP.

Only the codepoints observed in a real corpus are listed — this is not an attempt
at a complete transliteration of either font. An unlisted codepoint is left
untouched on purpose, so it keeps being counted as unmapped rather than being
silently replaced by a guess. Dropping a glyph is worse than leaving it: a left
glyph is still measurable, a dropped one is not.

A mapped value may sit outside the BMP (U+1F668 does). Anything consuming these
tables must be astral-safe: index by character, never by UTF-16 code unit, and do
not assume ``len(value) == 1`` bytes.
"""

from __future__ import annotations

import re

#: The 0xF000 offset a symbol-font ToUnicode CMap conventionally applies to a
#: single-byte code. Also the offset a legacy Devanagari font's symbol-style
#: (3,0) cmap applies, which is why :func:`unlift_symbol_pua` lives here even
#: though the legacy maps consume it.
SYMBOL_PUA_LIFT = 0xF000
#: The private-use span a 0xF000-lifted single byte can land in: 0x20-0xFF.
#: Deliberately narrower than the full BMP PUA (U+E000-U+F8FF) so this module
#: never touches likhit's own reordering markers at U+F000/U+F001 (see
#: ``kalimati._PUA_REPH``) or any other private-use convention.
SYMBOL_PUA_RANGE = (0xF020, 0xF0FF)

#: Adobe ``Symbol``. Only the codepoints measured in the CIAA corpus.
SYMBOL_PUA: dict[int, str] = {
    0xF020: " ",  # space
    0xF028: "(",  # parenleft
    0xF029: ")",  # parenright
    0xF02C: ",",  # comma
    0xF02D: "−",  # minus -- U+2212, NOT the hyphen that 0xF000 subtraction gives
    0xF02E: ".",  # period
    0xF0B7: "•",  # bullet -- U+2022, NOT the U+00B7 middle dot 0xF000 gives
}

#: Microsoft ``Wingdings``.
WINGDINGS_PUA: dict[int, str] = {
    0xF0A7: "▪",  # small filled square, a Word sub-bullet
    0xF0D8: "➢",  # three-D top-lighted rightwards arrowhead, a Word bullet
}

#: Microsoft ``Wingdings 2``. A separate table from :data:`WINGDINGS_PUA` because
#: the two fonts share codepoints and mean different glyphs by them.
#:
#: VOL-741. Wingdings 2 0x93 is a hollow four-petal quilt ornament, and it *does*
#: have a faithful Unicode equivalent: U+1F668 HOLLOW QUILT SQUARE ORNAMENT, in
#: the Ornamental Dingbats block that Unicode 7.0 added for exactly this purpose
#: -- encoding the Wingdings/Webdings glyph repertoire. An earlier revision of
#: this file recorded it as unmappable after comparing it against the Dingbats
#: block alone (U+2722-U+274B), where the near neighbours really are different
#: shapes; the Ornamental Dingbats block was not considered.
#:
#: Identified three independent ways rather than by eye alone: the glyph was
#: extracted from the embedded ``ABCEEE+Wingdings 2`` subset and rendered from the
#: source PDF (a hollow four-petal motif, matching the Unicode name); Microsoft's
#: published Wingdings 2 table maps 0x93 there; and the surrounding codes align as
#: a consecutive run -- 0x90-0x92 are CLOCK FACE TEN-/ELEVEN-/TWELVE-THIRTY and
#: 0x94 is the paired ``... IN BLACK SQUARE`` variant, which a mis-indexed table
#: would not produce.
#:
#: Scope is fixed by measurement, not by ambition: scanning the text layer of all
#: 13 CIAA report PDFs finds Wingdings 2 emitting exactly **one** codepoint,
#: U+F093, 35 times. The rest of the font's repertoire is deliberately absent --
#: an unobserved mapping is an unverified claim.
WINGDINGS2_PUA: dict[int, str] = {
    0xF093: "\U0001f668",  # U+1F668 HOLLOW QUILT SQUARE ORNAMENT
}

#: Codepoints deliberately left UNMAPPED, with the reason. Recorded rather than
#: dropped or guessed, per VOL-704 item 3: "map what maps, and record the rest as
#: known-unmappable rather than silently dropping them".
#:
#: These stay in the output and keep being counted by ``_private_use_count`` and
#: by the corpus audit's PUA axis, which is the intended outcome -- a glyph we
#: cannot faithfully represent should remain visible as a gap.
#:
#: **Currently empty.** Its one entry, Wingdings 2 0xF093, was resolved to
#: U+1F668 in VOL-741; see :data:`WINGDINGS2_PUA`. The mechanism is kept because
#: the policy still holds for the next genuinely unmappable glyph -- and because
#: "it is in KNOWN_UNMAPPABLE" turned out to be a claim worth re-checking rather
#: than inheriting.
KNOWN_UNMAPPABLE: dict[int, str] = {}

#: Lowercased base font name -> its PUA table. Matched as a substring against the
#: base name (subset prefix stripped) the same way ``legacy_maps._match_font``
#: does, so ``ABCDEE+SymbolMT`` and ``Symbol`` both resolve.
#:
#: Ordered longest-key-first at lookup time: "wingdings 2" must be tested before
#: "wingdings", or Wingdings 2 spans would silently take the Wingdings table.
_REGISTRY: dict[str, dict[int, str]] = {
    "wingdings 2": WINGDINGS2_PUA,
    "wingdings2": WINGDINGS2_PUA,
    "wingdings": WINGDINGS_PUA,
    "symbolmt": SYMBOL_PUA,
    "symbol": SYMBOL_PUA,
}

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _base_font_name(font_name: str) -> str:
    """Lowercased base name: subset prefix stripped, style suffix dropped.

    Mirrors ``legacy_maps._match_font`` so a font resolves identically in both
    registries. Splitting on "," drops the PostScript style suffix
    ("Wingdings-Regular" keeps its hyphen, "Symbol,Bold" does not).
    """

    name = _SUBSET_PREFIX.sub("", font_name.strip())
    name = name.split("+", 1)[-1]
    return name.split(",", 1)[0].strip().lower()


def pua_table_for_font(font_name: str) -> dict[int, str] | None:
    """The PUA table for ``font_name``, or ``None`` if it is not a symbol font.

    Returning ``None`` for an unknown font is the conservative default: the span
    is left exactly as it arrived, so an unrecognized font degrades to today's
    behaviour instead of being remapped by a table that does not describe it.
    """

    base = _base_font_name(font_name)
    if not base:
        return None
    # PREFIX, not substring. A substring test routes any font whose name merely
    # CONTAINS a key: "SegoeUISymbol" (present in the CIAA corpus) and even
    # "SomeSymbolicFont" both resolved to SYMBOL_PUA. That matters because the caller
    # in font_based returns immediately on a symbol-font hit, so a misrouted font
    # bypasses every other handler. Latent rather than live here -- SegoeUISymbol's
    # spans carry 0 private-use characters, so the remap was a no-op on them -- but
    # the class is unbounded, and it is the same unguarded-substring-router defect
    # tests/test_legacy_map_difference.py pins for legacy_maps._match_font.
    #
    # A prefix still admits every form the corpus actually contains: "Symbol",
    # "SymbolMT", "Symbol-Identity-H", "Symbol,Bold", "ABCDEE+Symbol",
    # "Wingdings-Identity-H", "ABCEEE+Wingdings 2". Longest key first, so
    # "wingdings 2" is tested before "wingdings".
    for key in sorted(_REGISTRY, key=len, reverse=True):
        if base.startswith(key):
            return _REGISTRY[key]
    return None


def is_symbol_pua_font(font_name: str) -> bool:
    """True if ``font_name`` is a legacy symbol font this module can remap."""

    return pua_table_for_font(font_name) is not None


def remap_symbol_pua(text: str, font_name: str) -> str:
    """Replace private-use codepoints in ``text`` using ``font_name``'s table.

    A codepoint absent from the table -- including everything in
    :data:`KNOWN_UNMAPPABLE` -- is left untouched, so it stays countable as
    unmapped damage rather than becoming a wrong character.
    """

    table = pua_table_for_font(font_name)
    if table is None:
        return text
    return "".join(table.get(ord(char), char) for char in text)


def unlift_symbol_pua(text: str) -> str:
    """Undo the 0xF000 lift, mapping U+F020-U+F0FF back to bytes 0x20-0xFF.

    For a **legacy Devanagari** font this is the correct and complete transform:
    the font is a byte-keyed keystroke encoding, so a character in U+F020-U+F0FF
    can only be one of its bytes that a symbol-style cmap pushed into the private
    use area. Recovering the byte hands the span to the legacy converter in the
    exact form it expects.

    It is NOT correct for a symbol font -- use :func:`remap_symbol_pua` there.
    Subtraction turns Symbol's bullet into a middle dot; see the module docstring.

    A no-op on text with no private-use characters, which is how every legacy font
    likhit already handled arrives, so wiring this into the legacy path cannot
    change their output.
    """

    lo, hi = SYMBOL_PUA_RANGE
    return "".join(
        chr(ord(char) - SYMBOL_PUA_LIFT) if lo <= ord(char) <= hi else char
        for char in text
    )
