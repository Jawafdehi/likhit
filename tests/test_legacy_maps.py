from __future__ import annotations

import pytest

from likhit.extractors.legacy_maps import (
    _decode_ascii_bracketed_number,
    get_converter,
    get_converter_for_map,
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
    # The bracket gate lives in get_converter (the name-based path) only.
    # get_converter_for_map is the primitive content-based detection scores
    # candidates with (choose_legacy_map in font_based.py); it must keep
    # returning the raw, ungated conversion.
    convert = get_converter_for_map("FONTASY_HIMALI_TT")
    assert convert("(1)") == "ढ१ण्"
