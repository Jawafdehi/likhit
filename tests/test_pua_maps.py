from __future__ import annotations

import inspect

import pytest

from likhit.extractors.font_based import FontBasedStrategy
from likhit.extractors.legacy_maps import (
    get_converter,
    get_converter_for_map,
    is_legacy_font,
)
from likhit.extractors.pua_maps import (
    KNOWN_UNMAPPABLE,
    SYMBOL_PUA,
    WINGDINGS2_PUA,
    WINGDINGS_PUA,
    is_symbol_pua_font,
    pua_table_for_font,
    remap_symbol_pua,
    unlift_symbol_pua,
)

# VOL-704. A PDF that draws a bullet in the Microsoft `Symbol` font and carries a
# ToUnicode CMap in the conventional "byte + 0xF000" form hands us U+F0B7, which
# is unassigned: it renders as a box, no Markdown parser sees a list, and nothing
# downstream can tell it from data. Measured on all 13 CIAA annual reports:
# 5,689 BMP private-use characters, 4,210 of them U+F0B7 (2,227 in leading
# position), and all nine pre-existing audit axes graded every report `clean`.
#
# The corpus splits into two populations with DIFFERENT fixes, and the whole
# design rests on keeping them apart:
#
#   Symbol / SymbolMT / Wingdings / Wingdings 2   4,369 glyphs, 10 codepoints
#   ARAP 11, a legacy DEVANAGARI keystroke font   1,363 glyphs, 45 codepoints
#
# U+F020 and U+F029 are emitted by BOTH families, so a codepoint-keyed table is
# provably wrong: the same codepoint is a symbol in one span and a Nepali space
# or keystroke in the next.


# --- the symbol tables ---------------------------------------------------------


@pytest.mark.parametrize(
    ("codepoint", "expected", "why"),
    [
        # The load-bearing entry: 4,210 of the corpus's 5,689 BMP PUA chars.
        (0xF0B7, "•", "Symbol 0xB7 is `bullet`"),
        # These two are the reason this is a TABLE and not a subtraction.
        # 0xF000-subtraction gives U+00B7 MIDDLE DOT and U+002D HYPHEN, and both
        # are the wrong glyph.
        (0xF02D, "−", "Symbol 0xB7 is `minus`, a long bar, not a hyphen"),
        (0xF020, " ", "Symbol 0x20 is space"),
        (0xF028, "(", "parenleft"),
        (0xF029, ")", "parenright"),
        (0xF02C, ",", "comma"),
        (0xF02E, ".", "period"),
    ],
    ids=["bullet", "minus", "space", "parenleft", "parenright", "comma", "period"],
)
def test_symbol_table_decodes_the_observed_codepoints(codepoint, expected, why) -> None:
    assert SYMBOL_PUA[codepoint] == expected, why
    assert remap_symbol_pua(chr(codepoint), "Symbol") == expected


def test_subtracting_the_lift_is_the_wrong_answer_for_the_symbol_bullet() -> None:
    """Pins the trap this module exists to avoid, so it cannot resurface as a fix.

    U+F0B7 - 0xF000 is U+00B7 MIDDLE DOT, a small centred dot. The glyph Symbol
    actually draws at 0xB7 is a large filled round bullet, U+2022. An arithmetic
    "fix" is silently wrong on 74% of this corpus's private-use characters.
    """

    assert unlift_symbol_pua("") == "·"  # what subtraction gives
    assert remap_symbol_pua("", "Symbol") == "•"  # what is correct
    assert "·" != "•"


@pytest.mark.parametrize(
    ("codepoint", "expected"),
    [(0xF0D8, "➢"), (0xF0A7, "▪")],
    ids=["wingdings-arrowhead", "wingdings-filled-square"],
)
def test_wingdings_table_is_separate_from_symbol(codepoint, expected) -> None:
    assert WINGDINGS_PUA[codepoint] == expected
    assert remap_symbol_pua(chr(codepoint), "Wingdings") == expected
    # The same byte under the Symbol table would be a different glyph entirely,
    # which is why the tables are keyed by font and never merged.
    assert codepoint not in SYMBOL_PUA


def test_wingdings_2_quilt_ornament_resolves_to_ornamental_dingbats() -> None:
    """VOL-741. Wingdings 2 0x93 DOES have a faithful Unicode equivalent.

    An earlier revision recorded it as unmappable, having compared it only against
    the Dingbats block (U+2722-U+274B), where the near neighbours are genuinely
    different shapes. The match is in Ornamental Dingbats, which Unicode 7.0 added
    to encode the Wingdings/Webdings repertoire: U+1F668 HOLLOW QUILT SQUARE
    ORNAMENT, whose name describes the rendered glyph exactly.

    Asserted on the Unicode NAME as well as the codepoint, so a typo in the escape
    cannot pass: 0x1F668 and 0x1F669 differ by one hex digit and are the hollow and
    boxed variants of the same ornament.
    """

    import unicodedata

    assert WINGDINGS2_PUA[0xF093] == "\U0001f668"
    assert unicodedata.name(WINGDINGS2_PUA[0xF093]) == "HOLLOW QUILT SQUARE ORNAMENT"
    assert remap_symbol_pua("", "Wingdings 2") == "🙨"
    # It must leave the private use area, or the audit's PUA axis still counts it.
    assert unicodedata.category("") == "Co"
    assert unicodedata.category(WINGDINGS2_PUA[0xF093]) == "So"
    # Still font-scoped: the other tables must not gain the codepoint.
    assert 0xF093 not in SYMBOL_PUA
    assert 0xF093 not in WINGDINGS_PUA


def test_the_mapped_ornament_is_astral_and_survives_a_round_trip() -> None:
    """U+1F668 is outside the BMP, a first for these tables.

    Anything indexing the output by UTF-16 code unit would split it into a
    surrogate pair and corrupt the transcript. Python strings are code points, so
    this holds today; the test pins it so a future change that encodes to UTF-16 or
    measures length in bytes fails loudly instead of silently mangling 35 cells.
    """

    out = remap_symbol_pua("| 4  |", "ABCEEE+Wingdings 2")
    assert out == "| 4 🙨 |"
    assert len(out) == len("| 4 X |")  # one code point, not a surrogate pair
    assert ord(WINGDINGS2_PUA[0xF093]) > 0xFFFF
    assert out.encode("utf-8").decode("utf-8") == out


def test_known_unmappable_is_empty_but_its_policy_still_bites() -> None:
    """VOL-704 item 3's mechanism outlives its one entry.

    KNOWN_UNMAPPABLE is empty since VOL-741 resolved U+F093. The policy it encodes
    -- leave an unmappable codepoint in place so it stays countable, never drop it
    -- must still hold, so this asserts the behaviour on an UNLISTED codepoint
    rather than on the now-absent table entry. A dropped glyph is undetectable
    later; a left one is still measurable.
    """

    assert KNOWN_UNMAPPABLE == {}
    assert 0xF093 not in KNOWN_UNMAPPABLE

    # 0xF0AA is emitted by ARAP 11 in this corpus and is absent from every symbol
    # table, so it stands in for "a codepoint we have no mapping for".
    unlisted = "\uf0aa"
    assert 0xF0AA not in WINGDINGS2_PUA
    assert remap_symbol_pua(unlisted, "Wingdings 2") == unlisted, "must not be dropped"
    assert len(remap_symbol_pua(unlisted, "Wingdings 2")) == 1


def test_wingdings_2_does_not_silently_take_the_wingdings_table() -> None:
    """Registry lookup is longest-key-first, so "wingdings 2" wins over "wingdings".

    Without the ordering, a Wingdings 2 span would be remapped by the Wingdings
    table and U+F0D8 would become an arrowhead it never was.
    """

    assert pua_table_for_font("Wingdings 2") is WINGDINGS2_PUA
    assert pua_table_for_font("Wingdings") is WINGDINGS_PUA
    assert remap_symbol_pua("", "Wingdings 2") == ""
    assert remap_symbol_pua("", "Wingdings") == "➢"


# --- font scoping: the reason this is not codepoint-keyed ----------------------


@pytest.mark.parametrize(
    "font",
    # "Webdings" was here and has been removed deliberately: it does not share
    # Wingdings' encoding, and it appears nowhere in the 13-report corpus, so the alias
    # was a guess. See test_webdings_and_zapfdingbats_are_deliberately_absent.
    ["Symbol", "SymbolMT", "ABCDEE+SymbolMT", "Symbol,Bold", "Wingdings"],
)
def test_symbol_fonts_are_recognized_through_subset_and_style_decoration(font) -> None:
    assert is_symbol_pua_font(font)


@pytest.mark.parametrize(
    "font",
    ["ARAP 11", "ABCDEE+ARAP 11", "Preeti", "Kantipur", "Kalimati", "Helvetica", ""],
)
def test_non_symbol_fonts_get_no_table_so_they_degrade_to_todays_behaviour(
    font,
) -> None:
    assert not is_symbol_pua_font(font)
    assert pua_table_for_font(font) is None
    # Unchanged, not mangled: an unrecognized font must behave exactly as before.
    assert remap_symbol_pua("", font) == ""


def test_the_same_codepoint_resolves_differently_per_font() -> None:
    """U+F020 is emitted by both Symbol and ARAP 11 in the CIAA corpus.

    Under Symbol it is a space. Under ARAP 11 it is byte 0x20 of a Devanagari
    keystroke encoding, which is also a space -- but it must reach that answer
    through the LEGACY converter, not this table, because its neighbours in the
    same span (0x66, 0x63) are Nepali letters and Symbol would call them Greek
    `phi` and `chi`. This test pins that the symbol table refuses to act on it.
    """

    assert remap_symbol_pua("", "Symbol") == " "
    assert remap_symbol_pua("", "ARAP 11") == ""


# --- the un-lift, and ARAP 11 -------------------------------------------------


def test_unlift_recovers_the_legacy_keystroke_bytes() -> None:
    lifted = ""
    assert unlift_symbol_pua(lifted) == "clVtof/"


@pytest.mark.parametrize(
    "text",
    [
        "clVtof/ b'?kof]u",  # already-ASCII legacy keystrokes: every existing font
        "अनुसन्धान आयोग",  # already-correct Unicode Devanagari
        "Annual Report 2074/75",
        "",
    ],
    ids=["ascii-keystrokes", "devanagari", "latin", "empty"],
)
def test_unlift_is_a_no_op_without_lifted_codepoints(text) -> None:
    """This is what makes wiring the un-lift into the legacy path safe.

    Every legacy font likhit already handled (Preeti, Kantipur, PCS NEPALI,
    Fontasy Himali, Sagarmatha) delivers ASCII keystrokes, so the transform cannot
    change their output and cannot regress them.
    """

    assert unlift_symbol_pua(text) == text


def test_unlift_leaves_likhits_own_reordering_markers_alone() -> None:
    """U+F000/U+F001 are `kalimati._PUA_REPH` / `_PUA_IKAR`, not lifted bytes.

    They sit just below the U+F020 floor, which is why `SYMBOL_PUA_RANGE` starts
    at F020 rather than at the start of the BMP private use area.
    """

    assert unlift_symbol_pua("") == ""


def test_arap_11_is_registered_as_a_legacy_devanagari_font() -> None:
    """It is a TEXT font, despite a symbol-style cmap that lifts its bytes.

    Confirmed by its name table (family "ARAP 11"), PANOSE bFamilyType=0 (text)
    against 5 (pictorial) for every Symbol subset in the same corpus, 100% of its
    output landing in the PUA (1,363/1,363 glyphs) in long unbroken runs, and
    Devanagari letterform contours when the glyphs are rendered.
    """

    assert is_legacy_font("ARAP 11")
    assert is_legacy_font("ABCDEE+ARAP 11")
    assert not is_symbol_pua_font("ARAP 11")


def test_arap_11_decodes_the_commissions_own_name_through_the_legacy_path() -> None:
    """The end-to-end class-B recovery, on the span that motivated the fix.

    This exact span is page 3 of the 28th annual report and the equivalent page of
    seven more (28th-35th): the Commission's officers by name and title. Before
    the fix the whole page rendered as private-use boxes with zero Devanagari.
    """

    convert = get_converter("ARAP 11")
    assert convert is not None
    lifted = ""
    assert convert(unlift_symbol_pua(lifted)) == ("अख्तियार दुरुपयोग अनुसन्धान आयोगका")


@pytest.mark.parametrize(
    ("lifted", "expected", "preeti_would_give", "byte"),
    [
        ("", "घिमिरे", "३िमिरे", "0x23 #"),
        ("", "डा.", "८ा.", "0x2A *"),
        ("", "गणेशराज", "ग०ोशराज", "0x29 )"),
        ("", "पाठक", "पा७क", "0x26 &"),
    ],
    ids=["ghimire", "dr", "ganeshraj", "pathak"],
)
def test_arap_11_map_choice_emits_no_devanagari_digits(
    lifted, expected, preeti_would_give, byte
) -> None:
    """FONTASY_HIMALI_TT, not Preeti -- and this is how the choice was decided.

    likhit's content-based `choose_legacy_map` cannot make this call: every map
    scores hits=2 against its dictionary and Preeti's errors are Devanagari
    DIGITS, which `_text_quality_penalty` does not charge. So Preeti wins at
    penalty_per_deva 0.0000 -- an apparently perfect score -- while corrupting four
    proper names on this one page.

    Every token here is one where Preeti and FONTASY_HIMALI_TT DISAGREE, and that
    is the point. An earlier version of this test used `प्रमुख`/`आयुक्त`/`नवीनकुमार`,
    on which the two maps agree, so swapping the registry entry to Preeti left the
    whole suite green. Mutation `arap-mapped-to-preeti` caught that; it is now
    pinned, along with the exact wrong value, because a digit substitution is
    invisible to every quality signal likhit has.

    On a page of proper names and titles a Devanagari digit is a direct error
    count, so the count must be zero.
    """

    convert = get_converter("ARAP 11")
    assert convert is not None
    decoded = convert(unlift_symbol_pua(lifted))
    assert decoded == expected, f"ARAP byte {byte}"
    assert not any("०" <= ch <= "९" for ch in decoded)
    assert decoded != preeti_would_give
    assert (
        get_converter_for_map("Preeti")(unlift_symbol_pua(lifted)) == preeti_would_give
    )


# --- the span choke point and the list-marker decision ------------------------


def test_symbol_span_is_remapped_at_the_span_choke_point() -> None:
    """A Symbol span classifies "correct", so without the new branch it falls through.

    That is precisely why 4,210 U+F0B7 reached the published Markdown: nothing was
    detected as broken, so nothing repaired it.
    """

    strategy = FontBasedStrategy()
    assert strategy._convert_span_text("", "Symbol", {}, needs_reorder=False) == "•"


def test_arap_span_is_remapped_at_the_span_choke_point() -> None:
    strategy = FontBasedStrategy()
    lifted = ""
    assert (
        strategy._convert_span_text(
            lifted, "ARAP 11", {"ARAP 11": "legacy_remap"}, needs_reorder=False
        )
        == "प्रमुख"
    )


def test_an_inline_bullet_stays_a_literal_glyph() -> None:
    """The position split, pinned. Leading is structure; inline is content.

    A leading bullet becomes "- " because that is real Markdown list syntax and a
    corpus of machine-readable primary sources needs the structure. An inline
    bullet is a character in a sentence, and rewriting it as a hyphen would change
    the sentence -- so it keeps the literal glyph. 1,983 of the corpus's 4,210
    U+F0B7 are inline.
    """

    from likhit.extractors.font_based import normalize_press_release_paragraph

    assert (
        normalize_press_release_paragraph("आयोगले • भ्रष्टाचार • मुद्दा दायर गरेको")
        == "आयोगले • भ्रष्टाचार • मुद्दा दायर गरेको"
    )
    # ...and the leading one on the very same glyph does convert.
    assert (
        normalize_press_release_paragraph("• भ्रष्टाचार निवारण ऐन")
        == "- भ्रष्टाचार निवारण ऐन"
    )


def test_a_symbol_bullet_never_reaches_the_greek_alphabet() -> None:
    """The wrong-fix sentinel, pinned as a test.

    Symbol 0x66 is `phi` and 0x63 is `chi`. Mapping ARAP 11's spans by the Symbol
    table would turn the Commission's name into Greek letters -- irreversibly, and
    with no U+FFFD for any gate to notice. The corpus contains zero Greek
    characters, so any Greek output is a regression by construction.
    """

    strategy = FontBasedStrategy()
    decoded = strategy._convert_span_text(
        "",
        "ARAP 11",
        {"ARAP 11": "legacy_remap"},
        needs_reorder=False,
    )
    assert decoded == "आयोग"
    assert not any("Ͱ" <= ch <= "Ͽ" for ch in decoded)


def test_the_symbol_and_legacy_registries_are_disjoint() -> None:
    """No font name may be claimed by both registries.

    This is the invariant that makes the branch ORDER in `_convert_span_text`
    safe. The symbol branch is deliberately placed after the legacy-Devanagari
    branches, but with disjoint registries the order cannot change behaviour for
    any known font -- mutation `symbol-branch-moved-before-legacy` survives for
    exactly that reason, and it is an equivalent mutant rather than an unpinned
    behaviour.

    So this test guards the premise instead of the ordering: add a font to both
    registries and the order becomes load-bearing, and this fires to say so.
    """

    from likhit.extractors.legacy_maps import _REGISTRY as LEGACY_REGISTRY
    from likhit.extractors.pua_maps import _REGISTRY as PUA_REGISTRY

    claimed_by_both = [key for key in LEGACY_REGISTRY if is_symbol_pua_font(key)]
    claimed_by_both += [key for key in PUA_REGISTRY if is_legacy_font(key)]
    assert claimed_by_both == [], (
        "a font name is claimed by both the symbol and legacy registries, so the "
        "branch order in _convert_span_text is now load-bearing and needs a test "
        "that pins it directly"
    )


# --------------------------------------------------------------- router boundaries
#: Every font name in the 13 CIAA annual reports that must resolve to a PUA table.
#: Measured from the PDFs, not invented: 12 of 329 distinct names.
CORPUS_SYMBOL_FONTS = (
    "Symbol",
    "ABCDEE+Symbol",
    "ABCEEE+Symbol",
    "JMPPJA+SymbolMT",
    "KFKAEK+SymbolMT",
    "UYVFMY+Symbol-Identity-H",
    "UYVFMY+SymbolMT-Identity-H",
    "Wingdings",
    "ABCFEE+Wingdings",
    "ABCGEE+Wingdings",
    "KZTWBE+Wingdings-Identity-H",
    "ABCEEE+Wingdings 2",
)

#: Names that CONTAIN a registry key but are not that font family. The first two are
#: real -- they appear in the corpus, 7 occurrences between them.
NOT_SYMBOL_FONTS = (
    "SegoeUISymbol,Bold",
    "JNJOHL+SegoeUISymbol",
    "SomeSymbolicFont",
    "MySymbolizer",
)


@pytest.mark.parametrize("name", CORPUS_SYMBOL_FONTS)
def test_every_corpus_symbol_font_still_resolves(name: str) -> None:
    """The control on the boundary test below: tightening the router must not drop a
    single form the corpus actually contains -- subset prefixes, ``-Identity-H``
    suffixes, the space in "Wingdings 2", and the MT variants all have to survive."""

    assert pua_table_for_font(name) is not None, name


@pytest.mark.parametrize("name", NOT_SYMBOL_FONTS)
def test_a_name_that_merely_contains_a_key_is_not_routed(name: str) -> None:
    """The router matches a PREFIX, not a substring.

    Under a substring test "SegoeUISymbol" resolved to SYMBOL_PUA, and so did
    "SomeSymbolicFont". That matters beyond the remap itself: ``_convert_span_text``
    returns immediately on a symbol-font hit, so a misrouted font bypasses every other
    handler. It was latent rather than live -- SegoeUISymbol's spans carry 0
    private-use characters, so the remap was a no-op on them -- but the class is
    unbounded.
    """

    assert pua_table_for_font(name) is None, name


def test_webdings_and_zapfdingbats_are_deliberately_absent() -> None:
    """Neither font occurs in the corpus, and neither shares Wingdings' encoding.

    They were briefly aliased to WINGDINGS_PUA. A wrong glyph table is worse than no
    table here, because the output stays well-formed: there is no U+FFFD and no length
    change for any gate to notice. Scanned all 13 reports -- 329 distinct font names,
    Wingdings present, Webdings and ZapfDingbats absent -- so the aliases were guesses
    at fonts nobody has seen. Leave them unsupported until there is a measured table.
    """

    assert pua_table_for_font("Webdings") is None
    assert pua_table_for_font("ZapfDingbats") is None


# ------------------------------------------- the leading-bullet rule's PUA boundary


def test_the_leading_bullet_class_agrees_with_the_symbol_pua_range() -> None:
    """The rule's class is written as literal escapes; this stops it drifting.

    ``normalize_press_release_paragraph`` rewrites a leading bullet to "- ". Its
    private-use bounds must be SYMBOL_PUA_RANGE and nothing wider: a full
    ``\\ue000-\\uf8ff`` class also matches ``kalimati._PUA_REPH`` (U+F000) and
    ``_PUA_IKAR`` (U+F001), and the rule fires on POSITION, so a sentinel that reached
    the start of a line followed by whitespace was rewritten to "- " -- destroying it
    and disguising the failure as a list item.
    """

    import re as _re

    from likhit.extractors import font_based
    from likhit.extractors.pua_maps import SYMBOL_PUA_RANGE

    lo, hi = SYMBOL_PUA_RANGE
    source = inspect.getsource(font_based.normalize_press_release_paragraph)

    # Assert on the CHARACTER CLASS, not on the whole function source. The source also
    # carries a comment explaining why the wide form is wrong, and that comment
    # contains the wide form -- so a naive "not in source" check fails on the prose
    # that documents the fix.
    classes = _re.findall(r"\^\[([^]]*)\]", source)
    assert len(classes) == 1, f"expected one leading-character class, found {classes}"
    klass = classes[0]

    assert f"\\u{lo:04x}-\\u{hi:04x}" in klass, (
        f"the leading-bullet class must bound its private-use range at "
        f"SYMBOL_PUA_RANGE (U+{lo:04X}-U+{hi:04X}); got {klass!r}"
    )
    assert "\\ue000" not in klass and "\\uf8ff" not in klass, (
        f"the whole BMP private-use area is too wide -- it swallows the kalimati "
        f"sentinels at U+F000/U+F001; got {klass!r}"
    )


def test_a_leading_symbol_bullet_still_becomes_a_markdown_list_item() -> None:
    """The control: narrowing the class must not stop the rule working. U+F0B7 is the
    corpus's most common private-use character at 4,210 occurrences, and U+F0D8 also
    appears; both are inside the symbol range."""

    from likhit.extractors.font_based import normalize_press_release_paragraph

    for bullet in ("", ""):
        assert normalize_press_release_paragraph(f"{bullet} पहिलो").startswith("- ")


def test_a_leading_kalimati_sentinel_is_not_turned_into_a_bullet() -> None:
    from likhit.extractors.font_based import normalize_press_release_paragraph
    from likhit.extractors.kalimati import _PUA_IKAR, _PUA_REPH

    for sentinel in (_PUA_REPH, _PUA_IKAR):
        out = normalize_press_release_paragraph(f"{sentinel} पहिलो")
        assert out.startswith(sentinel), out
