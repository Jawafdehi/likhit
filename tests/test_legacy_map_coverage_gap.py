"""Source 0x3c decodes to र, not to a literal `?` that deletes the letter.

Five maps decoded source code point 0x3c to a bare `?`, discarding a glyph the font
carries. PCS NEPALI was the only one that covered it at all, and it emits `्र` -- the
subjoined form, mis-ordered rather than correct. So every map in the family was wrong
there: five by destruction and one by ordering.

🛑 0x3c is `र` on the evidence of a rendered PAGE, not of a sibling map, and that
distinction is the whole reason the original report of this defect named the wrong
outlier. The page (font Siddhi, 26 occurrences) reads `आय रकम रु.`,
`९. सामाजिक सुरक्षा अनुदान`, `२. राजश्व` and `११. गरिवसंग विश्वेश्वर कार्यक्रम` --
initial, medial and final position, always a standalone `र`, and two of them are
standard OAG budget-line vocabulary. The embedded Siddhi cmap agrees a real glyph is
there: 0x3c is gid 63, one contour, advance 1047, against advance 2 for the genuinely
zero-width slots.

🛑 Repaired in the OUTPUT, and that is not a shortcut -- it is the only side that works.
npttf2utf reorders the matras around the gap slot as though it were the consonant it
should have emitted, so substituting in the input lands the र inside the cluster.
`test_the_word_case_that_discriminates_the_two_designs` is the case that proves it:
splitting the span at 0x3c and converting the pieces yields `गिर` instead of `गरि`.
"""

from __future__ import annotations

import pytest

from likhit.extractors.legacy_maps import (
    DECODABLE_MAP_KEYS,
    SHIPPED_MAP_KEYS,
    SYNTHESISED_MAP_KEYS,
    _REPLACEMENT_CHAR_MAPS,
    _REPLACEMENT_TARGET,
    get_converter_for_map,
)

#: Source code point 0x3c, the gap slot.
GAP_KEYSTROKE = "<"

#: Source 0x3f. Decodes to रु/रू under every map, which is what makes an output `?`
#: unambiguously a destroyed character rather than a passthrough.
NOT_A_GAP = "?"


def test_the_repair_target_is_devanagari_letter_ra() -> None:
    assert _REPLACEMENT_TARGET == "र"
    assert len(_REPLACEMENT_TARGET) == 1


@pytest.mark.parametrize("map_key", sorted(_REPLACEMENT_CHAR_MAPS))
def test_a_repaired_map_decodes_the_gap_as_ra(map_key: str) -> None:
    assert get_converter_for_map(map_key)(GAP_KEYSTROKE) == _REPLACEMENT_TARGET


@pytest.mark.parametrize("map_key", sorted(SYNTHESISED_MAP_KEYS))
def test_a_synthesised_map_inherits_the_repair_from_its_base(map_key: str) -> None:
    """Neither synthesised map is in `_REPLACEMENT_CHAR_MAPS`, and neither needs to be.

    Each reaches `?` through its base map's table -- Spins through Preeti, Siddhi through
    FONTASY_HIMALI_TT -- so the repair arrives with the base converter. Listing them
    would double-apply a substitution that is idempotent anyway, and would imply they
    have tables of their own.
    """

    assert map_key not in _REPLACEMENT_CHAR_MAPS
    assert get_converter_for_map(map_key)(GAP_KEYSTROKE) == _REPLACEMENT_TARGET


def test_no_decodable_map_still_emits_a_literal_question_mark_for_the_gap() -> None:
    """The population-level claim, so a map added later cannot quietly reintroduce it."""

    emitting = [
        key
        for key in DECODABLE_MAP_KEYS
        if "?" in get_converter_for_map(key)(GAP_KEYSTROKE)
    ]
    assert emitting == [], emitting


def test_pcs_nepali_is_deliberately_not_repaired() -> None:
    """Pinned as a decision, not left as an oversight.

    Its gap is at 0xa9, not 0x3c. Preeti, Kantipur and Spins all emit र there, but that
    is a SIBLING inference with no page read behind it -- exactly the reasoning that made
    the original report of this defect wrong about which map was the outlier. It needs
    its own rendered evidence first.
    """

    assert "PCS NEPALI" in SHIPPED_MAP_KEYS
    assert "PCS NEPALI" not in _REPLACEMENT_CHAR_MAPS
    # It covers 0x3c already, just in the subjoined form, so it loses nothing here.
    assert get_converter_for_map("PCS NEPALI")(GAP_KEYSTROKE) == "्र"


def test_the_word_case_that_discriminates_the_two_designs() -> None:
    """Output-side repair versus input-side, decided by one word.

    Splitting the span at 0x3c and converting the pieces separately strands the prefix
    matra: `ul<` comes out `गिर` instead of `गरि`. The four other word cases in the
    original derivation do NOT discriminate the two designs, because none of them puts a
    pending prefix matra before the gap -- which is why this one is here by itself.
    """

    assert get_converter_for_map("Preeti")("ul" + GAP_KEYSTROKE) == "गरि"


@pytest.mark.parametrize("map_key", sorted(DECODABLE_MAP_KEYS))
def test_source_0x3f_is_still_ra_plus_a_vowel_sign(map_key: str) -> None:
    """The premise that makes an output `?` safe to substitute at all.

    If any map decoded 0x3f to a literal `?`, replacing `?` in the output would corrupt
    a legitimate character instead of repairing a destroyed one.
    """

    decoded = get_converter_for_map(map_key)(NOT_A_GAP)
    assert "?" not in decoded
    assert decoded.startswith("र")
