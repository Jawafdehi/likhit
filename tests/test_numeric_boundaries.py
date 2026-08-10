from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import fitz
from markitdown import DocumentConverterResult
import pytest

import likhit.converters.nepali_pdf as nepali_pdf_module
from likhit.converters.nepali_pdf import NepaliPdfConverter
from likhit.extractors.font_based import FontBasedStrategy
from likhit.extractors.numeric_boundaries import (
    _Character,
    _select_minimal_rule_cuts,
    NumericBoundaryRepair,
    apply_line_numeric_boundary_repairs,
    collect_document_numeric_boundary_repairs,
    collect_page_numeric_boundary_repairs,
    repair_markdown_numeric_boundaries,
    requires_geometry_aware_candidate,
)
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

    assert repaired == "12500 1 | 2500"


def test_line_repair_matches_devanagari_equivalent() -> None:
    repair = _repair("380000.0023.", ("380000.00", "23."))

    repaired = apply_line_numeric_boundary_repairs("३८००००।००२३।", [repair])

    assert repaired == "३८००००।०० | २३।"


def test_font_based_extraction_inserts_ruled_numeric_boundaries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ruled.pdf"
    path.write_bytes(_ruled_numeric_pdf("123.45678.90", cuts=(6,)))

    result = FontBasedStrategy().extract_text(str(path))

    assert result.raw_text == "123.45 | 678.90"


def test_reusable_pdf_repair_inserts_ruled_numeric_boundaries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ruled.pdf"
    path.write_bytes(_ruled_numeric_pdf("123.45678.90", cuts=(6,)))

    blocks = extract_repaired_text_blocks(path)

    assert [block.text for block in blocks] == ["123.45 | 678.90"]


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
        "_try_collect_numeric_boundary_repairs",
        lambda _raw: [],
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Preeti": "legacy_remap"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "pdf_likely_needs_ocr",
        lambda _raw: False,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: pytest.fail("default extraction should not run"),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw: (DocumentConverterResult(markdown="geometry aware"), []),
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
        "_try_collect_numeric_boundary_repairs",
        lambda _raw: [repair],
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Preeti": "legacy_remap"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw: (
            DocumentConverterResult(
                markdown="267,000.00267,000.00\n267,000.00267,000.00"
            ),
            [],
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
        "pdf_likely_needs_ocr",
        lambda _raw: False,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: DocumentConverterResult(markdown="123.45678.90"),
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "123.45 | 678.90"


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
        "pdf_likely_needs_ocr",
        lambda _raw: False,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_repairs",
        lambda _raw: [_repair("12500", ("1", "2500"))],
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
        lambda _raw: (
            DocumentConverterResult(markdown="legitimate 12500\nmerged 1 | 2500"),
            [],
        ),
    )

    result = converter.convert(io.BytesIO(raw), stream_info)

    assert result.markdown == "legitimate 12500\nmerged 1 | 2500"
