from __future__ import annotations

import pytest

from likhit.extractors.font_based import FontBasedStrategy
from likhit.extractors.legacy_maps import (
    _decode_ascii_bracketed_number,
    get_converter,
    get_converter_for_map,
    get_output_converter_for_map,
    is_legacy_font,
)


# VOL-166: a legacy keyboard layout's own bracket glyph is never on the
# literal ASCII '(' / ')' keys -- Fontasy Himali's '(' key is a
# keyboard-layout consonant slot, and the same map's real bracket comes from
# '-'/'_' instead. A bare list/outline marker like "(1)" was placed directly
# by whatever authored the numbering, sharing the body font for visual
# consistency with the digit only -- it was never typed on this layout, so
# running the full character map over it corrupts the parens. Reproduced
# directly from the source PDF (see run comment on VOL-166): the raw span
# text behind the 35th annual report's 168 corrupted markers is plain ASCII
# "(1)".."(13)" under font "Fontasy Himali", not Kalimati and not Unicode.
@pytest.mark.parametrize(
    ("font", "raw", "expected"),
    [
        ("Fontasy Himali", "(1)", "(१)"),
        ("Fontasy Himali", "(12)", "(१२)"),
        ("FontasyHimali", "(2)", "(२)"),
        ("FONTASY_HIMALI_TT", "(13)", "(१३)"),
    ],
)
def test_ascii_bracketed_list_marker_decodes_digit_only(font, raw, expected):
    convert = get_converter(font)
    assert convert is not None
    assert convert(raw) == expected


def test_ascii_bracketed_marker_preserves_surrounding_whitespace():
    convert = get_converter("Fontasy Himali")
    assert convert is not None
    assert convert(" (1) ") == " (१) "


@pytest.mark.parametrize(
    ("font", "raw", "expected"),
    [
        # A plain digit run (no brackets) already decodes correctly through
        # FONTASY_HIMALI_TT's own digit row -- must stay on that path.
        ("Fontasy Himali", "100", "१००"),
        # Not asserting this is *correct* ('.' and '%' are shifted-row
        # characters this issue does not investigate) -- only that this fix
        # does not change it, since the whole span is not a bare "(N)" shape.
        ("Fontasy Himali", "33.8%", "३३।८छ"),
        # The layout's REAL bracket-producing keys ('-'/'_') must still go
        # through the full map: this is the same list-marker semantics as
        # "(1)", typed via different keys, and it already decodes correctly.
        ("Fontasy Himali", "-1_", "(१)"),
    ],
)
def test_non_bracketed_spans_unaffected(font, raw, expected):
    convert = get_converter(font)
    assert convert is not None
    assert convert(raw) == expected


def test_real_prose_through_a_registry_font_is_unaffected():
    convert = get_converter("Preeti")
    assert convert is not None
    assert convert("kl/R5]b") == "परिच्छेद"
    assert convert("sf7df8f}+") == "काठमाडौं"


@pytest.mark.parametrize(
    "text",
    [
        "(1) देखि",  # mixed content, not a bare marker
        "()",  # no digits
        "(1",  # unbalanced
        "1)",  # unbalanced
        "(1a)",  # not purely digits
    ],
)
def test_decode_ascii_bracketed_number_declines_non_bare_shapes(text):
    assert _decode_ascii_bracketed_number(text) is None


def test_kalimati_never_reaches_the_legacy_map():
    # VOL-166's issue text attributed the 168 corrupted markers to
    # already-Unicode "Kalimati" spans passing through a legacy map. Verified
    # false against the actual PDF (font is "Fontasy Himali", raw is plain
    # ASCII) -- pinned here so the wrong premise cannot resurface as a "fix".
    assert is_legacy_font("Kalimati") is False
    assert get_converter("Kalimati") is None


def test_get_converter_for_map_is_unaffected_by_the_bracket_gate():
    # get_converter_for_map is the SCORING primitive: choose_legacy_map runs every
    # candidate map over a span and keeps the best, so that comparison must see
    # each map's raw output. Gating here would change which map wins, not just
    # what the winner emits. Output paths use get_output_converter_for_map.
    convert = get_converter_for_map("FONTASY_HIMALI_TT")
    assert convert("(1)") == "ढ१ण्"


def test_output_converter_for_map_carries_the_gate():
    # The output counterpart of the scoring primitive above.
    convert = get_output_converter_for_map("FONTASY_HIMALI_TT")
    assert convert("(1)") == "(१)"
    assert convert("(12)") == "(१२)"
    # ...without disturbing anything that is not the bare marker shape.
    assert convert("-1_") == "(१)"
    assert convert("100") == "१००"


def test_content_based_span_conversion_is_gated_too():
    """The content-based path must not reintroduce the corruption.

    VOL-166's fix first shipped wired only into ``get_converter`` (the
    name-based path). ``_convert_span_text`` checks ``content_legacy_maps``
    *first* and returns from that branch directly, so a font detected by content
    rather than by name still produced ``"(1)" -> "ढ१ण्"``. That branch is not
    reachable in the CIAA corpus -- ``detect_content_legacy_fonts`` returns ``{}``
    for all 13 PDFs, measured -- so no corpus check could have caught it, and
    ``fix/content-legacy-name-agnostic`` widens exactly this path. Pinned here
    because it is invisible to every corpus gate we have.
    """

    strategy = FontBasedStrategy()
    # A mislabeled font: the name classifier calls it "correct", content
    # detection is what identifies it as legacy.
    content_maps = {"ABCDE+Helvetica": "FONTASY_HIMALI_TT"}
    converted = strategy._convert_span_text(
        "(1)",
        "ABCDE+Helvetica",
        {"Helvetica": "correct"},
        needs_reorder=False,
        content_legacy_maps=content_maps,
    )
    assert converted == "(१)"

    # Real prose on that same content-detected path still decodes in full.
    assert (
        strategy._convert_span_text(
            "kl/R5]b",
            "ABCDE+Times",
            {"Times": "correct"},
            needs_reorder=False,
            content_legacy_maps={"ABCDE+Times": "Preeti"},
        )
        == "परिच्छेद"
    )
