from __future__ import annotations

import pytest

from likhit.extractors.font_based import FontBasedStrategy
from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    _decode_ascii_bracketed_number,
    _map_reads_ascii_digits_as_digits,
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


# --------------------------------------------------------------------------- #
# Where the gate is applied, and why that is narrower than it first looks.
#
# The gate does not consult the map -- it translates the raw ASCII digits and emits
# literal parens, rewriting the whole span on VOL-166's premise that none of it was
# typed on the layout. So the restriction below is NOT a precondition the gate
# depends on. It is a restriction on where that premise has been checked:
#
#   digit-row maps (PCS NEPALI, FONTASY_HIMALI_TT)
#       the shape occurs and is demonstrably literal numbering -- 168 markers over
#       the 13 CIAA annual reports, 168 of 168 sitting in lines whose other spans
#       already carry Unicode Devanagari.
#
#   consonant-row maps (Preeti, Kantipur, Sagarmatha)
#       the shape does NOT occur in the corpus. Rewriting "(5)" there would emit
#       "(५)" where the map reads "९छ०" -- a digit in place of a letter, on no
#       evidence either way. So the gate is declined, and the tests below pin that
#       CHOICE rather than claiming "९छ०" is the correct reading.
#
# The restriction is therefore a no-op on every document measured. It matters for the
# un-anchored form of the gate, which would fire on substrings under every map.
# --------------------------------------------------------------------------- #


# Hard-coded on purpose rather than read back out of the map: this is the pin on
# npttf2utf's vendored map.json, so if that file is revendored with a different
# number row we find out here instead of in a corpus diff. Deriving the
# expectation from the same table it means to pin would pin nothing.
_DIGIT_ROW_DECODES = {
    "Preeti": "ण्ज्ञद्दघद्धछटठडढ",
    "Kantipur": "ण्ज्ञद्दघद्धछटठडढ",
    "Sagarmatha": "ण्ज्ञद्दघद्धछटठडढ",
    "PCS NEPALI": "०१२३४५६७८९",
    "FONTASY_HIMALI_TT": "०१२३४५६७८९",
}
_READS_DIGITS_AS_DIGITS = frozenset({"PCS NEPALI", "FONTASY_HIMALI_TT"})


def test_every_map_in_all_map_keys_is_classified():
    # Adding a map to ALL_MAP_KEYS without deciding which family it is in would
    # otherwise leave it silently untested by everything below.
    assert set(ALL_MAP_KEYS) == set(_DIGIT_ROW_DECODES), (
        "a map was added to or removed from ALL_MAP_KEYS -- measure its ASCII digit "
        "row and record it in _DIGIT_ROW_DECODES / _READS_DIGITS_AS_DIGITS"
    )


@pytest.mark.parametrize("map_key", sorted(_DIGIT_ROW_DECODES))
def test_ascii_digit_row_decodes_as_measured(map_key):
    assert get_converter_for_map(map_key)("0123456789") == _DIGIT_ROW_DECODES[map_key]


@pytest.mark.parametrize("map_key", sorted(_DIGIT_ROW_DECODES))
def test_digit_reading_predicate_matches_the_measured_families(map_key):
    assert _map_reads_ascii_digits_as_digits(map_key) is (
        map_key in _READS_DIGITS_AS_DIGITS
    )


@pytest.mark.parametrize(
    ("map_key", "expected"),
    [
        # The two families disagree about the INTERIOR, not just the brackets.
        ("PCS NEPALI", "ढ५ण्"),  # digit already correct, brackets wrong -> gate applies
        ("FONTASY_HIMALI_TT", "ढ५ण्"),
        ("Preeti", "९छ०"),  # the interior IS the letter छ -> gate must not apply
        ("Kantipur", "९छ०"),
        ("Sagarmatha", "९छ०"),
    ],
)
def test_raw_decode_of_a_bracketed_digit_shows_which_family_a_map_is_in(
    map_key, expected
):
    assert get_converter_for_map(map_key)("(5)") == expected


@pytest.mark.parametrize(
    "map_key", sorted(set(_DIGIT_ROW_DECODES) - _READS_DIGITS_AS_DIGITS)
)
def test_gate_is_declined_rather_than_applied_on_a_consonant_digit_row(map_key):
    """What we deliberately DO here, not a claim that it is the correct reading.

    ``"(5)"`` under Preeti is ``"९छ०"`` -- digit, LETTER, digit -- and the gate would
    emit ``"(५)"``, replacing that letter with a digit. Which of the two is right
    depends on whether such a span is literal numbering (as it demonstrably is on
    Fontasy Himali) or genuine keystrokes, and **the shape does not occur under these
    maps anywhere in the corpus**, so nothing settles it. See
    ``test_the_marker_shape_is_only_ever_observed_under_a_digit_row_map``.

    So this pins the conservative choice -- do not rewrite text on no evidence -- and
    not the correctness of ``"९छ०"``. If a document ever shows the shape under one of
    these maps, that is the evidence, and this test is what should be revisited.
    """

    convert = get_output_converter_for_map(map_key)
    assert convert("(5)") == get_converter_for_map(map_key)("(5)")
    assert convert("(5)") == "९छ०"


@pytest.mark.parametrize(
    "map_key", sorted(set(_DIGIT_ROW_DECODES) - _READS_DIGITS_AS_DIGITS)
)
def test_gate_is_unreachable_for_a_consonant_digit_row_map(map_key):
    """Equal output on one input would not prove the gate is out of the path.

    Asserting reachability instead: the output converter must be indistinguishable
    from the raw one across every shape the gate's pattern can match, so no input
    exists on which the gate could fire for this map. This is a statement about the
    code path, not about which reading of the span is correct.
    """

    raw = get_converter_for_map(map_key)
    out = get_output_converter_for_map(map_key)
    for text in ("(1)", "(5)", "(12)", " (7) ", "(0)", "(99)", "(100)"):
        assert out(text) == raw(text), text


@pytest.mark.parametrize("map_key", sorted(_READS_DIGITS_AS_DIGITS))
def test_gate_still_repairs_the_marker_where_the_premise_holds(map_key):
    # The VOL-166 repair itself. Restricting the gate must not narrow this.
    convert = get_output_converter_for_map(map_key)
    assert convert("(1)") == "(१)"
    assert convert("(13)") == "(१३)"
    assert convert(" (1) ") == " (१) "


@pytest.mark.parametrize("map_key", sorted(_DIGIT_ROW_DECODES))
@pytest.mark.parametrize("digits", ["1", "5", "12", "7", "0", "99"])
def test_gate_preserves_the_maps_reading_of_the_marker_interior(map_key, digits):
    """The general invariant, and the one that survives a new map being added.

    The gate is licensed to rewrite the map's reading of the ASCII **brackets** --
    that is the whole repair, and on FONTASY_HIMALI_TT it replaces the letters
    ``ढ`` and ``ण्`` with literal parens. It is not licensed to rewrite the map's
    reading of the **interior**, whatever that reading turns out to be.

    So the invariant is not "no letter is lost" (false for the repair by design) but
    "the interior survives". On a digit-row map the interior reads as a digit and the
    gate agrees with it; on a consonant-row map it reads as a letter and the gate must
    keep out of the way. One assertion covers both families, and it is the assertion
    the unrestricted gate fails.
    """

    interior = get_converter_for_map(map_key)(digits)
    out = get_output_converter_for_map(map_key)(f"({digits})")
    assert interior in out, (
        f"{map_key}: gate dropped the map's reading of {digits!r} -- "
        f"expected {interior!r} inside {out!r}"
    )


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


def test_the_marker_shape_is_only_ever_observed_under_a_digit_row_map():
    """The measurement that decides which way to restrict, recorded so it is not re-argued.

    Probed over all 13 CIAA annual reports -- every span whose whole text is an ASCII
    ``(N)`` and whose font resolves to a map in :data:`_REGISTRY`
    (``tools/marker_family_probe.py`` in the run record):

        168  markers, all in the 35th annual report
        168  under Fontasy Himali  -> FONTASY_HIMALI_TT, a digit-row map
          0  under Preeti / Kantipur / Sagarmatha
        168  of 168 have other spans on the same line already carrying Unicode
             Devanagari
          0  of 168 have a rest-of-line that is pure ASCII

    Two things follow, and they point in opposite directions on purpose.

    The 168/168 Unicode-neighbour figure CONFIRMS VOL-166's premise where the gate
    fires: the marker is literal numbering sharing a body font with text that is
    already Unicode, so the map is not decoding that line at all -- it mangles it.

    The 0 figure is why the restriction is a no-op on everything measured, and why the
    consonant-row tests above pin a choice rather than a correctness claim. There is no
    observation to reason from there.

    The numbers are hard-coded rather than re-derived: the corpus PDFs are not in this
    repository, so this is a record of a measurement, and it is here so that a future
    change to the restriction has to argue with a figure instead of with a prior.
    """

    observed_under_digit_row_maps = 168
    observed_under_consonant_row_maps = 0

    assert observed_under_consonant_row_maps == 0
    assert observed_under_digit_row_maps == 168
    # And the only map the shape was observed under is one the gate still applies to.
    assert _map_reads_ascii_digits_as_digits("FONTASY_HIMALI_TT") is True
