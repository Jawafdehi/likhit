"""VOL-323: the digit-dominant legacy companion, and its digit row.

The module docstring in ``digit_companion.py`` carries the measurements. These tests pin
the behaviour, and they are written so that the ones which matter run WITHOUT a font on
the host: the decision is split out as
:func:`~likhit.extractors.digit_companion.decide_from_plain_row_signatures`, and the
pinned signature tables are themselves valid inputs to it. A test that needs a real font
file is skipped when the font is absent, and a skipped test proves nothing.
"""

from __future__ import annotations

import os
import pathlib

import pymupdf as fitz
import pytest

from likhit.extractors.digit_companion import (
    DIGIT_COMPANION_ENV,
    _EXTERNAL_DEVANAGARI_DIGITS,
    _EXTERNAL_LATIN_DIGITS,
    _FAMILY_DEVANAGARI_DIGITS,
    _MAX_ALPHA_SHARE,
    _ROW_MATCH_MIN,
    _SIG_CELLS,
    _hex_to_bits,
    content_is_digit_dominant,
    decide_from_plain_row_signatures,
    prefers_devanagari_over_latin,
    devanagarize_companion_digits,
    digit_companion_enabled,
    glyphs_draw_devanagari_digits,
)

#: Env var pointing at a directory of extracted companion/prose faces, for the
#: font-dependent tests. The faces themselves are run artifacts (VOL-317 run
#: `c8a8e41c`), not repo fixtures, so they cannot be committed here.
FACES_ENV = "LIKHIT_SPINS_FACES_DIR"

ALL_TABLES = (
    ("family Devanagari", _FAMILY_DEVANAGARI_DIGITS),
    ("external Devanagari", _EXTERNAL_DEVANAGARI_DIGITS),
    ("external Latin", _EXTERNAL_LATIN_DIGITS),
)


def _bits(table: tuple[str, ...]) -> list[tuple[int, ...] | None]:
    return [_hex_to_bits(entry) for entry in table]


# --- the tables themselves ---------------------------------------------------- #


@pytest.mark.parametrize(("name", "table"), ALL_TABLES)
def test_every_pinned_table_is_ten_well_formed_signatures(
    name: str, table: tuple[str, ...]
) -> None:
    """A truncated or mistyped table would silently change every verdict."""

    assert len(table) == 10, name
    for index, entry in enumerate(table):
        assert len(entry) == 64, f"{name}[{index}] is not 64 hex digits"
        assert len(_hex_to_bits(entry)) == _SIG_CELLS, f"{name}[{index}]"
        # Not all-ink and not all-blank: either would match everything or nothing.
        cells = _hex_to_bits(entry)
        assert 0 < sum(cells) < _SIG_CELLS, f"{name}[{index}] is degenerate"


def test_the_three_tables_are_distinct_from_each_other() -> None:
    """The Latin and Devanagari references must not have been generated from one row.

    If they had, the two-sided comparison would be `x < x` for every face and the whole
    instrument would be inert -- a no-op control, which is the failure mode that looks
    most like success.
    """

    assert _EXTERNAL_DEVANAGARI_DIGITS != _EXTERNAL_LATIN_DIGITS
    assert _FAMILY_DEVANAGARI_DIGITS != _EXTERNAL_DEVANAGARI_DIGITS
    assert _FAMILY_DEVANAGARI_DIGITS != _EXTERNAL_LATIN_DIGITS


# --- the decision, without any font ------------------------------------------- #


def test_the_family_reference_is_recognised_as_devanagari() -> None:
    """The page-verified companion row, fed back in, must decide True."""

    assert decide_from_plain_row_signatures(_bits(_FAMILY_DEVANAGARI_DIGITS)) is True


def test_a_latin_digit_row_is_rejected() -> None:
    """The acceptance gate's central requirement, at the decision layer.

    `_EXTERNAL_LATIN_DIGITS` is a real Latin digit row (`DroidSansDevanagari` at
    U+0030). Firing on it would transliterate genuine Latin figures.
    """

    assert decide_from_plain_row_signatures(_bits(_EXTERNAL_LATIN_DIGITS)) is False


def test_the_external_font_corroborates_the_pinned_family_table() -> None:
    """The family reference is the instrument's single point of failure. This checks it.

    If `_FAMILY_DEVANAGARI_DIGITS` were ever regenerated from the wrong row -- the prose
    face's consonants, or a Latin face -- every verdict would be confidently wrong and
    nothing WITHIN the family could tell. `DroidSansDevanagari` is unrelated to this
    corpus and carries both rows, so it can.

    🛑 This comparison used to be a second gate inside the decision, and a mutation sweep
    showed it was UNREACHABLE there: replacing it with `return True` left the suite green,
    because every input clearing the family match already prefers Devanagari. It earns
    its place here instead, where it bites.
    """

    assert prefers_devanagari_over_latin(_bits(_FAMILY_DEVANAGARI_DIGITS)) is True
    # The other direction, so this is not asserting a constant.
    assert prefers_devanagari_over_latin(_bits(_EXTERNAL_LATIN_DIGITS)) is False
    assert prefers_devanagari_over_latin(_bits(_EXTERNAL_DEVANAGARI_DIGITS)) is True
    assert prefers_devanagari_over_latin([None] * 10) is None


def test_an_unrelated_devanagari_digit_row_is_also_rejected() -> None:
    """The documented SCOPE LIMIT, pinned so widening it has to argue with a test.

    Instrument A matches THIS legacy family's shapes, not "Devanagari digits" in
    general, so `DroidSansDevanagari`'s own `०-९` -- a different typeface -- is rejected.
    The failure direction is under-firing, which leaves a face exactly as it is today.
    """

    assert decide_from_plain_row_signatures(_bits(_EXTERNAL_DEVANAGARI_DIGITS)) is False


def test_too_few_readable_glyphs_abstains_rather_than_denying() -> None:
    """`None` is not `False`, and conflating them is the false negative VOL-317 named."""

    assert decide_from_plain_row_signatures([None] * 10) is None
    short = _bits(_FAMILY_DEVANAGARI_DIGITS)[: _ROW_MATCH_MIN - 1]
    assert decide_from_plain_row_signatures(short + [None] * 4) is None
    # ...and exactly at the floor it decides, so the floor is what abstained above.
    at_floor = _bits(_FAMILY_DEVANAGARI_DIGITS)[:_ROW_MATCH_MIN]
    assert decide_from_plain_row_signatures(at_floor + [None] * 3) is True


def test_a_mostly_wrong_row_is_rejected_even_with_enough_glyphs() -> None:
    """Readability is not agreement: the row count and the match count are separate."""

    mixed = _bits(_FAMILY_DEVANAGARI_DIGITS)[:2] + _bits(_EXTERNAL_LATIN_DIGITS)[2:]
    assert decide_from_plain_row_signatures(mixed) is False


# --- the transliteration ------------------------------------------------------ #


def test_only_the_digits_move() -> None:
    """`.` `,` and `/` are deliberately untouched.

    The companion draws a LITERAL ASCII period -- `8500.00` renders as `८५००.००` -- so
    mapping it to `।`, which every shipped map key would do, is a new corruption. VOL-323
    adjudicated the digit row and `.` only; `,` (192,549 occurrences) and `/` (518,336)
    are explicitly out of scope.
    """

    assert devanagarize_companion_digits("8500.00") == "८५००.००"
    assert devanagarize_companion_digits("2075/76") == "२०७५/७६"
    assert devanagarize_companion_digits("25,70,266/-") == "२५,७०,२६६/-"
    assert devanagarize_companion_digits("40001098.") == "४०००१०९८."
    # Idempotent, so a span converted twice cannot drift.
    once = devanagarize_companion_digits("34539000.0")
    assert devanagarize_companion_digits(once) == once
    # It is a digit-row transform and nothing else: Devanagari and Latin letters alike
    # pass through, which is what keeps it out of the legacy-remap business.
    assert devanagarize_companion_digits("क ख D 7") == "क ख D ७"


# --- the content gate --------------------------------------------------------- #


def test_the_content_gate_separates_the_companion_from_its_prose_sibling() -> None:
    """Condition 2. The measured gap is nearly three orders of magnitude wide.

    Corpus figures (VOL-317): the prose face is 54.7% ASCII-alphabetic, the two
    companions 0.42% and 0.85%. These fixtures stand in for those shapes.
    """

    companion = "40001098. 34539000.0 2075/76 8500.00 " * 3
    prose = "kflnsf ;+:yf hgxLtsf nflu jflif+s k|ltj]bg " * 3
    assert content_is_digit_dominant(companion) is True
    assert content_is_digit_dominant(prose) is False
    # The alpha share is what decides, not the digit share: a face with plenty of digits
    # AND plenty of letters is a prose face with figures in it, not a companion.
    mixed = "40001098 kathmandu 34539000 municipality 2075 report " * 3
    assert content_is_digit_dominant(mixed) is False


def test_a_tiny_font_is_not_evidence() -> None:
    """Three characters of digits is not a digit-dominant face."""

    assert content_is_digit_dominant("123") is False


def test_the_alpha_share_floor_is_what_rejects_the_prose_face() -> None:
    """Vary ONE edge: a string just over the alpha share fails, just under passes."""

    digits = "1234567890" * 10  # 100 non-space characters
    just_over = digits + "a" * 6  # 6/106 = 5.7% > 5%
    just_under = digits + "a" * 5  # 5/105 = 4.8% < 5%
    assert _MAX_ALPHA_SHARE == 0.05
    assert content_is_digit_dominant(just_over) is False
    assert content_is_digit_dominant(just_under) is True


# --- the kill switch ---------------------------------------------------------- #


def test_it_ships_on_but_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """VOL-323 lands ON in v17 (Damodaha, 2026-08-17).

    The switch exists so a paired-tree release gate can build the same tree with the
    change disabled; without it the gate would have to compare two different commits.
    """

    monkeypatch.delenv(DIGIT_COMPANION_ENV, raising=False)
    assert digit_companion_enabled() is True
    for value in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv(DIGIT_COMPANION_ENV, value)
        assert digit_companion_enabled() is False, value
    for value in ("1", "true", "yes"):
        monkeypatch.setenv(DIGIT_COMPANION_ENV, value)
        assert digit_companion_enabled() is True, value


def test_detection_returns_nothing_at_all_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The switch has to short-circuit BEFORE the page scan, or it only saves the render."""

    from likhit.extractors import digit_companion as module

    monkeypatch.setenv(DIGIT_COMPANION_ENV, "0")

    def explode(*_args, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("the disabled path must not read the document")

    monkeypatch.setattr(module, "_font_buffers", explode)
    doc = fitz.open()
    doc.new_page()
    try:
        assert module.detect_digit_companion_fonts(doc) == frozenset()
    finally:
        doc.close()


# --- font-dependent, skipped when the faces are absent ------------------------ #


def _faces_dir() -> pathlib.Path | None:
    raw = os.environ.get(FACES_ENV)
    if not raw:
        return None
    path = pathlib.Path(raw)
    return path if path.is_dir() else None


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # The two page-verified companions, from different documents.
        ("3653-Spins_EXT.ttf", True),
        ("3097-SpinsEXT.ttf", True),
        # The prose face of the same family: its plain row is CONSONANTS.
        ("3653-Spins.ttf", False),
        # A subset whose cmap is not unicode-addressable: no evidence either way.
        ("11102-Spins_EXT.ttf", None),
    ],
)
def test_the_extracted_faces_decide_as_read(
    filename: str, expected: bool | None
) -> None:
    """End to end from a real font program, against VOL-317's page-verified reads."""

    faces = _faces_dir()
    if faces is None:
        pytest.skip(f"set {FACES_ENV} to VOL-317's fonts-c8a8e41c directory")
    path = faces / filename
    if not path.exists():
        pytest.skip(f"{filename} not present in {faces}")
    assert glyphs_draw_devanagari_digits(path.read_bytes()) is expected


# --- the wiring, which is where an inert fix hides ---------------------------- #


def test_a_companion_span_is_transliterated_through_the_span_converter() -> None:
    """The fix has to be REACHED, not merely present.

    This is the shape of defect that shipped `unlift_symbol_pua` imported-but-never-
    called on this very merge: a correct transform wired to nothing. So this asserts the
    production entry point `_convert_span_text`, not the helper.
    """

    from likhit.extractors.font_based import FontBasedStrategy

    strategy = FontBasedStrategy()
    span = "8500.00"
    # Not a companion: unchanged, and this is the control -- without it the assertion
    # below could pass on a converter that transliterates everything.
    assert (
        strategy._convert_span_text(
            span, "CIDFont+F8", {"CIDFont+F8": "correct"}, False
        )
        == "8500.00"
    )
    # A companion: digits move, the literal ASCII period does not.
    assert (
        strategy._convert_span_text(
            span,
            "CIDFont+F8",
            {"CIDFont+F8": "correct"},
            False,
            digit_companion_fonts=frozenset({"CIDFont+F8"}),
        )
        == "८५००.००"
    )


def test_the_companion_branch_is_keyed_on_the_full_font_name() -> None:
    """Like `content_legacy_maps`, and for the same reason.

    Two subsets of the same family in one document are different font RESOURCES and can
    differ -- `Spins` and `Spins_EXT` have opposite digit rows under one family name --
    so a base-name key would apply one face's verdict to the other's spans.
    """

    from likhit.extractors.font_based import FontBasedStrategy

    strategy = FontBasedStrategy()
    assert (
        strategy._convert_span_text(
            "2075",
            "ABCDEF+Spins_EXT",
            {"Spins_EXT": "correct"},
            False,
            digit_companion_fonts=frozenset({"Spins_EXT"}),  # base name, not full
        )
        == "2075"
    )
    assert (
        strategy._convert_span_text(
            "2075",
            "ABCDEF+Spins_EXT",
            {"Spins_EXT": "correct"},
            False,
            digit_companion_fonts=frozenset({"ABCDEF+Spins_EXT"}),
        )
        == "२०७५"
    )


def test_a_name_routed_face_is_never_a_companion_so_cannot_double_convert() -> None:
    """Condition 1, asserted on the detector rather than trusted.

    `FONTASY_HIMALI_TT` and `PCS NEPALI` also draw ०-९ on the plain row, so they pass
    condition 3 -- they are excluded by being name-routed, and if that exclusion were
    dropped their digits would be transliterated a second time.
    """

    from likhit.extractors.legacy_maps import _matched_registry_key

    for routed in (
        "FONTASY_HIMALI_TT",
        "ABCDEF+FONTASY_HIMALI_TT,Bold",
        "PCS NEPALI",
        "Preeti",
        "Himalb",
        "ARAP 11",
        "SiddhiNormal",
    ):
        assert _matched_registry_key(routed) is not None, routed
    # ...and the companion names are NOT routed, or condition 1 would exclude them too.
    for companion in ("Spins_EXT", "SpinsEXT", "CIDFont+F8", "ABCDEE+Spins_EXT"):
        assert _matched_registry_key(companion) is None, companion
