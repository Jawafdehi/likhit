from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import fitz
from markitdown import DocumentConverterResult
import pytest

import likhit.converters.nepali_pdf as nepali_pdf_module
from likhit.converters.nepali_pdf import NepaliPdfConverter
import likhit.extractors.font_based as font_based_module
from likhit.extractors.font_based import FontBasedStrategy
import likhit.extractors.numeric_boundaries as numeric_boundaries_module
from likhit.extractors.numeric_boundaries import (
    _Character,
    NumericBoundaryEvidence,
    _extract_vertical_edges,
    _MAX_PARTITION_SEGMENTS,
    _plausible_span_partition_cuts,
    _select_minimal_rule_cuts,
    NumericBoundaryRepair,
    apply_line_numeric_boundary_repairs,
    collect_document_numeric_boundary_evidence,
    collect_document_numeric_boundary_repairs,
    collect_page_numeric_boundary_evidence,
    collect_page_numeric_boundary_repairs,
    collect_page_repairs_by_line,
    group_repairs_by_line,
    line_origin_key,
    repair_markdown_numeric_boundaries,
    requires_geometry_aware_candidate,
)
import likhit.nepali_pdf_repair as nepali_pdf_repair_module
from likhit.nepali_pdf_repair import extract_repaired_text_blocks


def _ruled_numeric_pdf(
    text: str,
    *,
    cuts: tuple[int, ...],
) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=360, height=160)
    x, baseline, size = 40.0, 80.0, 12.0
    page.insert_text(
        (x, baseline),
        text,
        fontname="helv",
        fontsize=size,
    )
    characters = page.get_text(
        "rawdict",
        flags=fitz.TEXT_PRESERVE_WHITESPACE,
    )["blocks"][0]["lines"][0]["spans"][0]["chars"]
    for cut in cuts:
        boundary_x = float(characters[cut]["origin"][0])
        page.draw_line(
            (boundary_x, 55),
            (boundary_x, 92),
            width=0.5,
        )
    raw = doc.tobytes()
    doc.close()
    return raw


def _spaced_decimal_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=360, height=160)
    page.insert_text(
        (40, 80),
        "1083534.59 1089241.46",
        fontname="helv",
        fontsize=12,
    )
    raw = doc.tobytes()
    doc.close()
    return raw


def _repair(
    merged_text: str,
    parts: tuple[str, ...],
    *,
    line_text: str | None = None,
    occurrence_index: int = 0,
) -> NumericBoundaryRepair:
    return NumericBoundaryRepair(
        page_number=1,
        block_number=0,
        line_number=0,
        start_index=0,
        merged_text=merged_text,
        parts=parts,
        line_text=line_text or merged_text,
        occurrence_index=occurrence_index,
    )


class _OriginGapPage:
    number = 0

    def __init__(self, boundary_extra: float) -> None:
        text = "1083534.591089241.46"
        boundary = len("1083534.59")
        origin = 40.0
        chars: list[dict[str, object]] = []
        for index, char in enumerate(text):
            if index == boundary:
                origin += boundary_extra
            width = 7.0 if char != "." else 3.0
            chars.append(
                {
                    "c": char,
                    "origin": (origin, 80.0),
                    "bbox": (origin, 68.0, origin + width, 83.0),
                }
            )
            origin += width
        self._raw = {
            "blocks": [
                {
                    "lines": [
                        {
                            "spans": [
                                {
                                    "font": "Kalimati",
                                    "size": 10.0,
                                    "chars": chars,
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    def get_text(self, mode: str, flags: int) -> dict[str, object]:
        assert mode == "rawdict"
        del flags
        return self._raw

    def get_cdrawings(self) -> list[dict[str, object]]:
        return []


class _SpanRuledPage:
    number = 0

    def __init__(self, parts: tuple[str, ...]) -> None:
        origin = 40.0
        spans: list[dict[str, object]] = []
        boundaries: list[float] = []
        for part_index, part in enumerate(parts):
            chars: list[dict[str, object]] = []
            for char in part:
                width = 6.0
                chars.append(
                    {
                        "c": char,
                        "origin": (origin, 80.0),
                        "bbox": (origin, 68.0, origin + width, 83.0),
                    }
                )
                origin += width
            spans.append(
                {
                    "font": "Kalimati",
                    "size": 10.0,
                    "chars": chars,
                }
            )
            if part_index < len(parts) - 1:
                boundaries.append(origin)

        self._raw = {
            "blocks": [
                {
                    "lines": [
                        {
                            "spans": spans,
                        }
                    ]
                }
            ]
        }
        rule_positions = [35.0, *boundaries, origin + 5.0]
        self._drawings = [
            {
                "items": [
                    ("l", (rule_x, 55.0), (rule_x, 92.0)) for rule_x in rule_positions
                ]
            }
        ]

    def get_text(self, mode: str, flags: int) -> dict[str, object]:
        assert mode == "rawdict"
        del flags
        return self._raw

    def get_cdrawings(self) -> list[dict[str, object]]:
        return self._drawings


class _GrazingRulePage(_SpanRuledPage):
    def __init__(self, text: str, cut: int, edge_offset: float) -> None:
        super().__init__((text,))
        following = self._raw["blocks"][0]["lines"][0]["spans"][0]["chars"][cut]
        rule_x = float(following["origin"][0]) + edge_offset
        self._drawings[0]["items"].insert(
            1,
            ("l", (rule_x, 55.0), (rule_x, 92.0)),
        )


def test_collects_multiple_boundaries_inside_one_pdf_span() -> None:
    raw = _ruled_numeric_pdf("123.45678.9099.10", cuts=(6, 12))

    repairs = collect_document_numeric_boundary_repairs(raw)

    assert len(repairs) == 1
    assert repairs[0].merged_text == "123.45678.9099.10"
    assert repairs[0].parts == ("123.45", "678.90", "99.10")


def test_collects_borderless_decimal_boundary_preserved_as_whitespace() -> None:
    repairs = collect_document_numeric_boundary_repairs(_spaced_decimal_pdf())

    assert len(repairs) == 1
    assert repairs[0].merged_text == "1083534.591089241.46"
    assert repairs[0].parts == ("1083534.59", "1089241.46")


def test_collects_conservative_decimal_origin_outlier() -> None:
    repairs = collect_page_numeric_boundary_repairs(_OriginGapPage(boundary_extra=2.0))

    assert len(repairs) == 1
    assert repairs[0].parts == ("1083534.59", "1089241.46")


def test_does_not_split_ordinary_decimal_origin_variation() -> None:
    repairs = collect_page_numeric_boundary_repairs(_OriginGapPage(boundary_extra=0.6))

    assert repairs == []


def test_prefers_six_numeric_spans_on_ruled_line() -> None:
    parts = ("12", "5", "500", "30000", "4500", "25500")

    repairs = collect_page_numeric_boundary_repairs(_SpanRuledPage(parts))

    assert [repair.parts for repair in repairs] == [parts]


def test_preserves_plausible_multi_span_number_without_cell_rules() -> None:
    page = _SpanRuledPage(("20", "7", "7"))
    page._drawings = []

    repairs = collect_page_numeric_boundary_repairs(page)

    assert repairs == []


def test_groups_fragmented_spans_into_plausible_amounts() -> None:
    parts = ("2", "7", "0,000.00", "270,000.00")
    page = _SpanRuledPage(parts)
    items = page._drawings[0]["items"]
    page._drawings[0]["items"] = [items[0], items[-1]]

    repairs = collect_page_numeric_boundary_repairs(page)

    assert [repair.parts for repair in repairs] == [("270,000.00", "270,000.00")]


def test_discards_rule_cut_that_creates_malformed_numeric_part() -> None:
    page = _SpanRuledPage(("2,00,000.002,00,0", "00.00"))

    repairs = collect_page_numeric_boundary_repairs(page)

    assert repairs == []


@pytest.mark.parametrize(
    ("text", "cuts", "expected"),
    [
        (
            "267,000.00267,000.00",
            {len("267,000.00"), len("267,000.002")},
            {len("267,000.00")},
        ),
        (
            "358,500.00358,500.00",
            {
                len("358,500.0"),
                len("358,500.00"),
                len("358,500.003"),
            },
            {len("358,500.00")},
        ),
    ],
)
def test_selects_unique_minimal_rule_partition(
    text: str,
    cuts: set[int],
    expected: set[int],
) -> None:
    characters = [
        _Character(
            text=character,
            origin_x=float(index),
            bbox=(float(index), 0.0, float(index + 1), 12.0),
            font="Kalimati",
            size=10.0,
            span_number=0,
        )
        for index, character in enumerate(text)
    ]

    selected = _select_minimal_rule_cuts(characters, cuts, set())

    assert selected == expected


def test_preserves_ambiguous_adjacent_small_rule_cells() -> None:
    text = "125500"
    characters = [
        _Character(
            text=character,
            origin_x=float(index),
            bbox=(float(index), 0.0, float(index + 1), 12.0),
            font="Kalimati",
            size=10.0,
            span_number=0,
        )
        for index, character in enumerate(text)
    ]

    selected = _select_minimal_rule_cuts(characters, {2, 3}, set())

    assert selected == {2, 3}


def test_splits_decimal_amount_from_following_serial() -> None:
    parts = ("380000.00", "23.")

    repairs = collect_page_numeric_boundary_repairs(_SpanRuledPage(parts))

    assert [repair.parts for repair in repairs] == [parts]


@pytest.mark.parametrize(
    "parts",
    [
        ("0", "3."),
        ("4.1.4", "5"),
    ],
)
def test_preserves_complete_serials_and_dotted_references(
    parts: tuple[str, ...],
) -> None:
    repairs = collect_page_numeric_boundary_repairs(_SpanRuledPage(parts))

    assert repairs == []


@pytest.mark.parametrize("edge_offset", [0.0, 0.25])
def test_preserves_valid_decimal_touched_by_rule(edge_offset: float) -> None:
    text = "2,273,614,315.05"
    cut = text.index("315") + 1

    repairs = collect_page_numeric_boundary_repairs(
        _GrazingRulePage(text, cut, edge_offset)
    )

    assert repairs == []


def test_line_repair_changes_only_geometry_scoped_duplicate() -> None:
    repair = _repair(
        "12500",
        ("1", "2500"),
        line_text="12500 12500",
        occurrence_index=1,
    )

    repaired = apply_line_numeric_boundary_repairs("12500 12500", [repair])

    assert repaired == "12500 1 2500"


def test_line_repair_matches_devanagari_equivalent() -> None:
    repair = _repair("380000.0023.", ("380000.00", "23."))

    repaired = apply_line_numeric_boundary_repairs("३८००००।००२३।", [repair])

    assert repaired == "३८००००।०० २३।"


def test_line_repair_never_writes_a_cell_delimiter() -> None:
    """An extracted line becomes a Markdown table CELL through its fragment.

    `renderers/markdown.py::_raw_table_row_lines` emits every row from the same
    `col_count` columns and does not escape a pipe already in the cell text, so a pipe
    written here states a column the table does not have and shifts every later cell of
    that row.
    """

    repair = _repair("123.45678.90", ("123.45", "678.90"))

    repaired = apply_line_numeric_boundary_repairs("123.45678.90", [repair])

    assert "|" not in repaired
    assert repaired == "123.45 678.90"


def test_markdown_repair_does_not_widen_a_table_row() -> None:
    """A contiguous run inside ONE cell must not be split into two cells.

    The row is rendered from a fixed column count, so the repaired row has to keep the
    cell count of the rest of its table. Only `_repair_pipe_lines_by_digit_signature`
    may write a pipe, because its pattern matches across delimiters that are already
    there (see `test_markdown_repair_recovers_values_from_misaligned_table_columns`).
    """

    repair = _repair("123.45678.90", ("123.45", "678.90"))

    repaired = repair_markdown_numeric_boundaries(
        "| a | 123.45678.90 | b |",
        [repair],
    )

    assert repaired == "| a | 123.45 678.90 | b |"
    assert repaired.count("|") == 4


def test_markdown_repair_still_writes_a_cell_delimiter_outside_a_table_row() -> None:
    """Outside a row a pipe is not a delimiter, so the cell claim is kept."""

    repair = _repair("123.45678.90", ("123.45", "678.90"))

    repaired = repair_markdown_numeric_boundaries("total 123.45678.90", [repair])

    assert repaired == "total 123.45 | 678.90"


def _reblocking(module: object) -> None:
    """Make `get_cid_marked_page_dict` re-block the page, as the CID flag does.

    `get_cid_marked_page_dict` re-extracts a page with
    `TEXT_USE_CID_FOR_UNKNOWN_UNICODE` whenever some glyph decodes to U+FFFD, and that
    extraction groups the same glyphs into a different number of blocks -- measured on
    document 11724 page 7 as 38 blocks against 39, diverging at block 4. Prepending an
    empty text block reproduces the only consequence that matters: every block index
    after the divergence names a different line than the collector measured.
    """

    real = module.get_cid_marked_page_dict

    def reblocked(page: object) -> dict:
        page_dict = real(page)
        page_dict["blocks"] = [{"lines": []}, *page_dict["blocks"]]
        return page_dict

    module.get_cid_marked_page_dict = reblocked


def test_line_origin_key_is_taken_over_every_span_including_empty_ones() -> None:
    """A caller that drops its empty spans first moves the minimum and loses the key.

    Measured over the 1,843 line-applicable repairs the 102 published `numeric_damage`
    documents produce: 1,843 resolve when every box is included, 1,379 when the boxes
    belonging to spans the caller discards are left out.
    """

    empty_span_box = (12.0, 68.0, 14.0, 83.0)
    text_span_box = (40.0, 68.4, 96.0, 83.0)

    assert line_origin_key([empty_span_box, text_span_box]) == (68.0, 12.0)
    assert line_origin_key([text_span_box]) == (68.4, 40.0)


def test_repairs_are_grouped_by_page_and_line_origin() -> None:
    """The index key is geometry, not the enumeration indices of one extraction."""

    repair = NumericBoundaryRepair(
        page_number=7,
        block_number=27,
        line_number=7,
        start_index=0,
        merged_text="123.45678.90",
        parts=("123.45", "678.90"),
        line_text="123.45678.90",
        line_origin=(253.8, 103.6),
    )

    grouped = group_repairs_by_line([repair])

    assert list(grouped) == [(7, 253.8, 103.6)]


def test_reusable_pdf_repair_survives_a_reblocked_second_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ruled.pdf"
    path.write_bytes(_ruled_numeric_pdf("123.45678.90", cuts=(6,)))
    monkeypatch.setattr(
        nepali_pdf_repair_module,
        "get_cid_marked_page_dict",
        nepali_pdf_repair_module.get_cid_marked_page_dict,
    )
    _reblocking(nepali_pdf_repair_module)

    blocks = extract_repaired_text_blocks(path)

    assert [block.text for block in blocks] == ["123.45 678.90"]


def test_font_based_extraction_survives_a_reblocked_second_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ruled.pdf"
    path.write_bytes(_ruled_numeric_pdf("123.45678.90", cuts=(6,)))
    monkeypatch.setattr(
        font_based_module,
        "get_cid_marked_page_dict",
        font_based_module.get_cid_marked_page_dict,
    )
    _reblocking(font_based_module)

    result = FontBasedStrategy().extract_text(str(path))

    assert result.raw_text == "123.45 678.90"


def test_markdown_repair_chooses_the_separator_per_line() -> None:
    """One markdown pass sees both kinds of line, so the choice cannot be global."""

    repair = _repair("123.45678.90", ("123.45", "678.90"))

    repaired = repair_markdown_numeric_boundaries(
        "total 123.45678.90\n| a | 123.45678.90 | b |\n",
        [repair],
    )

    assert repaired == "total 123.45 | 678.90\n| a | 123.45 678.90 | b |\n"


def test_font_based_extraction_inserts_ruled_numeric_boundaries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ruled.pdf"
    path.write_bytes(_ruled_numeric_pdf("123.45678.90", cuts=(6,)))

    result = FontBasedStrategy().extract_text(str(path))

    assert result.raw_text == "123.45 678.90"


def test_reusable_pdf_repair_inserts_ruled_numeric_boundaries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ruled.pdf"
    path.write_bytes(_ruled_numeric_pdf("123.45678.90", cuts=(6,)))

    blocks = extract_repaired_text_blocks(path)

    assert [block.text for block in blocks] == ["123.45 678.90"]


def test_markdown_repair_recovers_values_from_misaligned_table_columns() -> None:
    repair = _repair(
        "११८९०९९३.८३१५३६६९८४.६८१५४५२०९६.२८१२२१६७०.२३",
        (
            "११८९०९९३.८३",
            "१५३६६९८४.६८",
            "१५४५२०९६.२८",
            "१२२१६७०.२३",
        ),
    )
    markdown = "| कोष | ११८९०९९३ | ८३ १५३६६९८४ | ६८ १५४५२०९६ | २८ १२२१६७० | २३ |"

    repaired = repair_markdown_numeric_boundaries(markdown, [repair])

    assert repaired == (
        "| कोष | ११८९०९९३.८३ | १५३६६९८४.६८ | १५४५२०९६.२८ | १२२१६७०.२३ |"
    )


def test_markdown_repair_does_not_replace_a_shorter_numeric_run_prefix() -> None:
    shorter = _repair(
        "0.001,000.00",
        ("0.00", "1,000.00"),
    )
    longer = _repair(
        "0.001,00,000.00",
        ("0.00", "1,00,000.00"),
    )

    repaired = repair_markdown_numeric_boundaries(
        "0.001,00,000.00",
        [shorter, longer],
    )

    assert repaired == "0.00 | 1,00,000.00"


def test_markdown_repair_matches_devanagari_equivalent() -> None:
    repair = _repair("380000.0023.", ("380000.00", "23."))

    repaired = repair_markdown_numeric_boundaries("३८००००।००२३।", [repair])

    assert repaired == "३८००००।०० | २३।"


def test_markdown_repair_ignores_implausible_overlapping_partition() -> None:
    repairs = [
        _repair("0.00270,000.00", ("0.00", "270,000.00")),
        _repair(
            "270,000.00270,000.00",
            ("2", "7", "0,000.00270,000.00"),
        ),
    ]

    repaired = repair_markdown_numeric_boundaries(
        "खर्च 0.00270,000.00",
        repairs,
    )

    assert repaired == "खर्च 0.00 | 270,000.00"


def test_pipe_repair_matches_devanagari_digit_signature() -> None:
    repair = _repair("380000.0023.", ("380000.00", "23."))

    repaired = repair_markdown_numeric_boundaries(
        "| खर्च | ३८०००० | ०० २३ |",
        [repair],
    )

    assert repaired == "| खर्च | ३८००००.०० | २३. |"


def test_plausible_short_merge_requires_geometry_aware_candidate() -> None:
    repair = _repair("12500", ("1", "2500"))

    assert requires_geometry_aware_candidate([repair])
    assert requires_geometry_aware_candidate(
        [repair],
        markdown="the value is 12500",
    )
    assert not requires_geometry_aware_candidate(
        [repair],
        markdown="the value is already 1 | 2500",
    )
    assert repair_markdown_numeric_boundaries("12500", [repair]) == "12500"


def test_devanagari_plausible_merge_requires_geometry_aware_candidate() -> None:
    repair = _repair("12500", ("1", "2500"))

    assert requires_geometry_aware_candidate([repair], markdown="१२५००")


def test_markdown_repair_splits_geometry_proven_plausible_amount_merge() -> None:
    repair = _repair("19970531757000", ("1997053", "1757000"))

    repaired = repair_markdown_numeric_boundaries(
        "जम्मा १९९७०५३१७५७०००",
        [repair],
    )

    assert repaired == "जम्मा १९९७०५३ | १७५७०००"


def test_markdown_repair_preserves_plausible_value_beyond_geometry_count() -> None:
    repair = _repair("19970531757000", ("1997053", "1757000"))

    repaired = repair_markdown_numeric_boundaries(
        "19970531757000 and 19970531757000",
        [repair],
    )

    assert repaired == "19970531757000 and 19970531757000"


def test_markdown_repair_preserves_short_leading_field_before_long_amount() -> None:
    repair = _repair("19555184776", ("195", "55184776"))

    assert repair_markdown_numeric_boundaries("19555184776", [repair]) == "19555184776"


def test_markdown_repair_preserves_year_or_reference_field() -> None:
    repair = _repair("3688322080", ("368832", "2080"))

    assert repair_markdown_numeric_boundaries("3688322080", [repair]) == "3688322080"


def test_quality_score_penalizes_whitespace_dominated_candidate() -> None:
    readable = "नेपाल सरकारको लेखापरीक्षण प्रतिवेदन\n" * 20
    whitespace_dominated = "   ".join(readable.replace("\n", ""))

    assert nepali_pdf_module._markdown_quality_score(
        readable
    ) > nepali_pdf_module._markdown_quality_score(whitespace_dominated)


def test_quality_score_penalizes_malformed_matra_sequences() -> None:
    readable = "कार्यालयमा आर्थिक विवरण तयार भयो।\n" * 20
    matra_damaged = readable + ("ाा ्ा " * 50)

    assert nepali_pdf_module._markdown_quality_score(
        readable
    ) > nepali_pdf_module._markdown_quality_score(matra_damaged)


def test_converter_uses_successful_known_font_candidate_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _ruled_numeric_pdf("123.45", cuts=())
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_evidence",
        lambda _raw: NumericBoundaryEvidence(tuple([]), frozenset()),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Preeti": "legacy_remap"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: pytest.fail("default extraction should not run"),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw, **_kwargs: (
            DocumentConverterResult(markdown="geometry aware"),
            [],
            None,
        ),
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "geometry aware"


def test_converter_repairs_unambiguous_numeric_merges_in_known_font_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _ruled_numeric_pdf("123.45", cuts=())
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")
    repair = _repair(
        "267,000.00267,000.00",
        ("267,000.00", "267,000.00"),
    )

    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_evidence",
        lambda _raw: NumericBoundaryEvidence(tuple([repair]), frozenset()),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Preeti": "legacy_remap"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw, **_kwargs: (
            DocumentConverterResult(
                markdown="267,000.00267,000.00\n267,000.00267,000.00"
            ),
            [],
            None,
        ),
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "267,000.00 | 267,000.00\n267,000.00 | 267,000.00"


def test_converter_repairs_unambiguous_default_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _ruled_numeric_pdf("123.45678.90", cuts=(6,))
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: DocumentConverterResult(markdown="123.45678.90"),
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "123.45 | 678.90"


def test_converter_repairs_a_prefetched_likhit_candidate_forced_to_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prefetched likhit result must be repaired before OCR is merged.

    The repair-font path extracts likhit up front. When preclassification also
    finds a scanned page, that result becomes the safe born-digital base for OCR.
    The numeric repair must still run before the converter returns it with an
    in-band needs-OCR marker.
    """
    raw = _ruled_numeric_pdf("123.45", cuts=())
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")
    repair = _repair("267,000.00267,000.00", ("267,000.00", "267,000.00"))

    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_evidence",
        lambda _raw: NumericBoundaryEvidence(tuple([repair]), frozenset()),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Preeti": "legacy_remap"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_ocr_page",
        lambda _document, _page_index: "image_only",
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw, **_kwargs: (
            DocumentConverterResult(markdown="बेरुजु रकम तालिका\n267,000.00267,000.00"),
            [1],
            None,
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_build_ocr_service",
        lambda **_kwargs: None,
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert "needs-ocr pages=1 reason=not-configured" in result.markdown
    assert "बेरुजु रकम तालिका\n267,000.00 | 267,000.00" in result.markdown


def test_converter_repairs_a_likhit_re_extraction_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second unrepaired append: likhit re-extracted after a suspicious default.

    Reaching this branch needs care. `prefer_geometry_aware` is computed on the
    *already repaired* default, so a document whose every merge the text repair can
    fix never re-runs likhit at all -- an earlier version of this test passed
    without exercising the branch for exactly that reason. Two repairs are needed:
    an ambiguous short one, which `repair_markdown_numeric_boundaries` declines to
    apply and which therefore keeps `prefer_geometry_aware` true, and an
    unambiguous one, which is what proves the append must repair.
    """
    raw = _ruled_numeric_pdf("123.45", cuts=())
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")
    ambiguous = _repair("12500", ("1", "2500"))
    unambiguous = _repair("267,000.00267,000.00", ("267,000.00", "267,000.00"))

    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_evidence",
        lambda _raw: NumericBoundaryEvidence(
            tuple([ambiguous, unambiguous]), frozenset()
        ),
    )
    # No repair font, so the early return is not taken and the default runs first.
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Helvetica": "correct"},
    )
    # The default keeps the ambiguous merge whatever the repair does, so it stays
    # the unsafe candidate and likhit's geometry-aware one wins on safety.
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: DocumentConverterResult(
            markdown="legitimate 12500\n267,000.00267,000.00"
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw, **_kwargs: (
            DocumentConverterResult(markdown="merged 12500 ok\n267,000.00267,000.00"),
            [],
            None,
        ),
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "merged 12500 ok\n267,000.00 | 267,000.00"


def test_converter_prefers_geometry_candidate_for_ambiguous_short_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _ruled_numeric_pdf("12500", cuts=(1,))
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_evidence",
        lambda _raw: NumericBoundaryEvidence(
            tuple([_repair("12500", ("1", "2500"))]), frozenset()
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: DocumentConverterResult(
            markdown="legitimate 12500\nmerged 12500"
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw, **_kwargs: (
            DocumentConverterResult(markdown="legitimate 12500\nmerged 1 | 2500"),
            [],
            None,
        ),
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "legitimate 12500\nmerged 1 | 2500"


def _one_span_per_glyph(text: str) -> tuple[list[_Character], list[tuple[int, int]]]:
    characters = [
        _Character(
            text=character,
            origin_x=index * 6.0,
            bbox=(index * 6.0, 0.0, index * 6.0 + 5.0, 10.0),
            font="F0",
            size=10.0,
            span_number=index,
        )
        for index, character in enumerate(text)
    ]
    return characters, [(index, index + 1) for index in range(len(text))]


def test_partition_declines_a_pathologically_fragmented_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Grouping is explored exponentially and a bare digit run prunes nothing,
    # so a PDF that positions every glyph separately would hang the conversion.
    # Past the bound the run must be declined without being explored at all.
    consulted: list[str] = []
    real = numeric_boundaries_module._looks_like_plausible_single_number
    monkeypatch.setattr(
        numeric_boundaries_module,
        "_looks_like_plausible_single_number",
        lambda text: consulted.append(text) or real(text),
    )

    characters, segments = _one_span_per_glyph("1" * (_MAX_PARTITION_SEGMENTS + 1))

    assert _plausible_span_partition_cuts(characters, segments, set()) == set()
    assert consulted == []


def test_partition_still_groups_a_run_within_the_bound() -> None:
    characters, segments = _one_span_per_glyph("123.45678.90")

    # Within the bound the search still runs; it just may find no unique answer.
    assert isinstance(_plausible_span_partition_cuts(characters, segments, set()), set)


def test_line_repair_declines_an_occurrence_the_line_no_longer_has() -> None:
    # Geometry proved the third occurrence. Only one is left, so splitting it
    # would rewrite a value geometry never examined.
    repair = _repair("12500", ("1", "2500"), occurrence_index=2)

    assert (
        apply_line_numeric_boundary_repairs("legitimate 12500 only", [repair])
        == "legitimate 12500 only"
    )


def test_page_repair_collection_degrades_instead_of_failing() -> None:
    class ExplodingPage:
        # KeyError deliberately: the collector already swallows the
        # AttributeError/RuntimeError/TypeError/ValueError family around
        # `get_text`, so raising one of those would pass without the wrapper.
        def get_text(self, *_args: object, **_kwargs: object) -> object:
            raise KeyError("synthetic geometry failure")

        def get_cdrawings(self) -> object:
            return []

    assert collect_page_repairs_by_line(ExplodingPage(), page_number=1) == {}


def test_font_based_extraction_survives_numeric_geometry_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic geometry failure")

    monkeypatch.setattr(
        numeric_boundaries_module,
        "collect_page_numeric_boundary_repairs",
        explode,
    )

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "123.45 678.90")
    sample = tmp_path / "numbers.pdf"
    doc.save(str(sample))
    doc.close()

    result = FontBasedStrategy().extract_text(str(sample))

    assert "123.45" in result.raw_text


def test_vertical_edges_accept_an_unnormalized_rect() -> None:
    class RectPage:
        def get_cdrawings(self) -> list[dict[str, object]]:
            # y1 above y0: the same ruling, stored the other way round.
            return [{"items": [("re", (100.0, 90.0, 101.0, 40.0))]}]

    assert _extract_vertical_edges(RectPage())


def test_orphan_matra_pattern_accepts_a_decomposed_nukta() -> None:
    # NFC decomposes क़ into क + U+093C, so canonical Nepali uses this form.
    decomposed = "क़ानून"

    assert nepali_pdf_module._ORPHAN_MATRA_PATTERN.findall(decomposed) == []


def test_converter_prefers_a_safe_candidate_over_a_higher_scoring_unsafe_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The unsafe candidate still holds the merged value, so it must lose even
    # though it scores higher.
    raw = _ruled_numeric_pdf("12500", cuts=(1,))
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_evidence",
        lambda _raw: NumericBoundaryEvidence(
            tuple([_repair("12500", ("1", "2500"))]), frozenset()
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_repair_result_numeric_boundaries",
        lambda result, _repairs: result,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: DocumentConverterResult(markdown="merged 12500 padding"),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw, **_kwargs: (
            DocumentConverterResult(markdown="split 1 | 2500"),
            [],
            None,
        ),
    )
    scores = {"merged 12500 padding": 500, "split 1 | 2500": 10}
    monkeypatch.setattr(
        nepali_pdf_module,
        "_markdown_quality_score",
        lambda markdown: scores[markdown],
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "split 1 | 2500"


def test_markdown_repair_declines_a_duplicated_render_without_source_evidence() -> None:
    """The occurrence-count guard alone: two renders, one proof, no repair.

    Pinned so the fix below is measured against the behaviour it replaces rather
    than against an assumption about it.
    """
    repair = _repair("6976254161032", ("6976254", "161032"))
    markdown = "| 6976254161032 |\n| 6976254161032 |"

    assert repair_markdown_numeric_boundaries(markdown, [repair]) == markdown


def test_markdown_repair_fixes_every_render_of_a_run_geometry_never_saw_whole() -> None:
    """A renderer that emits one table twice must not cost the proven split.

    likhit renders a page's table both as a degenerate one-value-per-row table
    and as the wide one, so the merged value appears more often in the Markdown
    than geometry proved it. Counting occurrences cannot tell that from a second
    legitimate value carrying the same digits; the source runs can, and every
    occurrence here traces to the one run geometry split.
    """
    repair = _repair("6976254161032", ("6976254", "161032"))
    markdown = "| 6976254161032 |\n| 6976254161032 |"

    repaired = repair_markdown_numeric_boundaries(
        markdown,
        [repair],
        unsplit_runs=frozenset(),
    )

    assert repaired == "| 6976254 | 161032 |\n| 6976254 | 161032 |"


def test_markdown_repair_still_declines_a_value_geometry_saw_whole() -> None:
    """The risk the count guard existed for, tested directly.

    When the same digits also occur as a run geometry examined and left whole,
    one of the Markdown occurrences may be that legitimate value, and a global
    substitution would rewrite it into a wrong figure.
    """
    repair = _repair("6976254161032", ("6976254", "161032"))
    markdown = "| 6976254161032 |\n| 6976254161032 |"

    repaired = repair_markdown_numeric_boundaries(
        markdown,
        [repair],
        unsplit_runs=frozenset({"6976254161032"}),
    )

    assert repaired == markdown


class _PlainDigitSplitPage:
    """A plausible plain-digit run split by a ruling, optionally repeated whole.

    Plain digits are the shape the OAG corpus fails on, and the only cut that
    reaches them is a ruling backed by an origin-gap outlier -- the decimal
    path needs `.dd` on both halves.
    """

    number = 0

    def __init__(self, *, repeat_whole: bool) -> None:
        self._edges: list[dict[str, object]] = []
        lines = [self._line("6976254161032", boundary=7, gap=4.0)]
        if repeat_whole:
            lines.append(self._line("6976254161032", boundary=None, gap=0.0))
        # Annotated because `ty` otherwise infers this literal's own narrow
        # value type and reports `get_text` below as a return-type mismatch.
        # The two older stubs in this file carry that advisory diagnostic;
        # this stub is new, so it does not add a third.
        self._raw: dict[str, object] = {"blocks": [{"lines": lines}]}

    def _line(
        self,
        text: str,
        *,
        boundary: int | None,
        gap: float,
    ) -> dict[str, object]:
        origin = 40.0
        chars: list[dict[str, object]] = []
        for index, char in enumerate(text):
            if index == boundary:
                self._edges.append(
                    {
                        "items": [
                            ("l", (origin + gap / 2, 68.0), (origin + gap / 2, 84.0))
                        ]
                    }
                )
                origin += gap
            chars.append(
                {
                    "c": char,
                    "origin": (origin, 80.0),
                    "bbox": (origin, 68.0, origin + 7.0, 83.0),
                }
            )
            origin += 7.0
        return {"spans": [{"font": "Kalimati", "size": 10.0, "chars": chars}]}

    def get_text(self, mode: str, flags: int) -> dict[str, object]:
        assert mode == "rawdict"
        del flags
        return self._raw

    def get_cdrawings(self) -> list[dict[str, object]]:
        return self._edges


def _with_whole_run_page(stream: bytes, text: str) -> bytes:
    doc = fitz.open(stream=stream, filetype="pdf")
    page = doc.new_page(width=360, height=160)
    page.insert_text((40.0, 80.0), text, fontname="helv", fontsize=12.0)
    appended = doc.tobytes()
    doc.close()
    return appended


def test_document_evidence_unions_unsplit_runs_across_pages() -> None:
    """A run left whole on a later page reaches the document's evidence.

    The value here is the implausible shape, because a real PDF cannot easily be
    made to hold a *plausible* merged run -- MuPDF inserts a separator between two
    text objects on one baseline, which is not the erased-separator defect. The
    decline that `unsplit_runs` gates is pinned on the page fakes below; what this
    proves is that the set is document-scoped rather than per page.
    """
    merged = "267,000.00267,000.00"
    split_only = collect_document_numeric_boundary_evidence(
        _ruled_numeric_pdf(merged, cuts=(10,))
    )
    with_whole = collect_document_numeric_boundary_evidence(
        _with_whole_run_page(_ruled_numeric_pdf(merged, cuts=(10,)), merged)
    )

    assert [repair.parts for repair in split_only.repairs] == [
        ("267,000.00", "267,000.00")
    ]
    assert merged not in split_only.unsplit_runs
    assert merged in with_whole.unsplit_runs
    assert [repair.parts for repair in with_whole.repairs] == [
        ("267,000.00", "267,000.00")
    ]


def test_page_evidence_splits_a_plain_digit_run_and_reports_nothing_unsplit() -> None:
    """`unsplit_runs` is derived from the page, not asserted by the caller."""

    evidence = collect_page_numeric_boundary_evidence(
        _PlainDigitSplitPage(repeat_whole=False),
        page_number=1,
    )

    assert [repair.parts for repair in evidence.repairs] == [("6976254", "161032")]
    assert "6976254161032" not in evidence.unsplit_runs


def test_evidence_repairs_a_duplicated_render_end_to_end() -> None:
    """The 69-document shape: one proven split, two renders of it, both repaired."""

    evidence = collect_page_numeric_boundary_evidence(
        _PlainDigitSplitPage(repeat_whole=False),
        page_number=1,
    )
    markdown = "|  | 6976254161032 |\n| 18 | 6976254161032 | 0 |"

    repaired = repair_markdown_numeric_boundaries(
        markdown,
        evidence.repairs,
        unsplit_runs=evidence.unsplit_runs,
    )

    assert repaired == "|  | 6976254 | 161032 |\n| 18 | 6976254 | 161032 | 0 |"
    # Occurrence counting declines the same input, which is the V10 behaviour.
    assert repair_markdown_numeric_boundaries(markdown, evidence.repairs) == markdown


def test_evidence_declines_when_the_same_run_also_appears_whole() -> None:
    """A second occurrence geometry left whole may be a legitimate value."""

    evidence = collect_page_numeric_boundary_evidence(
        _PlainDigitSplitPage(repeat_whole=True),
        page_number=1,
    )
    markdown = "|  | 6976254161032 |\n| 18 | 6976254161032 | 0 |"

    assert "6976254161032" in evidence.unsplit_runs
    assert (
        repair_markdown_numeric_boundaries(
            markdown,
            evidence.repairs,
            unsplit_runs=evidence.unsplit_runs,
        )
        == markdown
    )
