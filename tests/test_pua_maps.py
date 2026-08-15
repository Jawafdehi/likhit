from __future__ import annotations

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


def test_known_unmappable_codepoints_are_left_in_place_not_dropped() -> None:
    """VOL-704 item 3: record the unmappable tail, never silently drop it.

    Wingdings 2 0x75 is a four-petal outline ornament with no faithful Unicode
    equivalent (35 occurrences, all in the 33rd report). Leaving it keeps it
    countable by `_private_use_count` and by the corpus audit's PUA axis; dropping
    it would make it undetectable later, which is strictly worse.
    """

    assert 0xF093 in KNOWN_UNMAPPABLE
    assert remap_symbol_pua("", "Wingdings 2") == ""
    assert 0xF093 not in SYMBOL_PUA
    assert 0xF093 not in WINGDINGS_PUA


def test_wingdings_2_does_not_silently_take_the_wingdings_table() -> None:
    """Registry lookup is longest-key-first, so "wingdings 2" wins over "wingdings".

    Without the ordering, a Wingdings 2 span would be remapped by the Wingdings
    table and U+F0D8 would become an arrowhead it never was.
    """

    assert pua_table_for_font("Wingdings 2") is not WINGDINGS_PUA
    assert pua_table_for_font("Wingdings") is WINGDINGS_PUA
    assert remap_symbol_pua("", "Wingdings 2") == ""
    assert remap_symbol_pua("", "Wingdings") == "➢"


# --- font scoping: the reason this is not codepoint-keyed ----------------------


@pytest.mark.parametrize(
    "font",
    ["Symbol", "SymbolMT", "ABCDEE+SymbolMT", "Symbol,Bold", "Wingdings", "Webdings"],
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
