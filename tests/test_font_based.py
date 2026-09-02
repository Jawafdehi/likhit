from __future__ import annotations

import builtins
from dataclasses import replace
from pathlib import Path
import sys
import threading
import types

import fitz
import pytest

from likhit.errors import ExtractionError, ValidationError
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.font_classifier import classify_font
import likhit.extractors.font_based as font_based_module
import likhit.extractors.kalimati as kalimati_module
from likhit.extractors.font_based import (
    FontBasedStrategy,
    count_marked_cids,
    get_cid_marked_page_dict,
    mark_unmappable_cids,
    strip_marked_cids,
    _char_position,
    _iter_dict_spans,
    _replacement_and_decoded_positions,
    _to_dict_shape,
    _choose_fragment_text,
    _has_severe_noise,
    _is_corroborated_conjunct_ra_repair,
    _is_garbled_orphan,
    _merge_corroborated_conjunct_ra_tokens,
    _merge_fragment_variants,
    _merge_malformed_table_variants,
    _tables_contain_malformed_conjunct_ra,
    _text_quality_penalty,
    _duplicate_consonant_count,
    _DUPLICATE_CONSONANT_PATTERN,
    _DOUBLED_CONSONANT_LEXEMES,
    _CID_MARK_BASE,
    unmark_cid_ascii,
    join_spans_with_layout,
    join_words_with_spacing,
    normalize_extracted_word,
    normalize_press_release_paragraph,
    parse_page_range,
    recover_latin_cid_text,
    is_latin_cid_font,
    _latin_cid_score,
    _latin_cid_lexicon,
    _CID_RECOVERY_MIN_HITS,
    _CID_RECOVERY_MIN_COV_ONE_HIT,
    _CID_RECOVERY_LEXICON_ENV,
    _LATIN_CID_FONT_FAMILIES,
    _unmappable_runs,
    _recover_or_mark_unmappable_span,
)
from likhit.extractors.pua_maps import (
    SYMBOL_PUA_LIFT,
    SYMBOL_PUA_RANGE,
    unlift_symbol_pua,
)
from likhit.extractors.kalimati import (
    _get_font_correction_map,
    _get_fontfile_xref,
    _get_simple_font_correction_map,
    _resolve_fontfile2_xref,
)
from likhit.handlers.single_column_notice import SingleColumnNoticeHandler
from likhit.models import Table, TableCell, TableRegion


ROOT = Path(__file__).resolve().parents[1]


def _sample_path(*candidates: str) -> Path:
    for candidate in candidates:
        path = ROOT / "samples" / candidate
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Missing sample PDF in {ROOT / 'samples'}. Tried: {', '.join(candidates)}"
    )


PRESS_RELEASE = _sample_path("pressrelease.pdf")


def _build_zeroed_tounicode_pdf() -> bytes:
    """Build a PDF where distinct source codes all map to unknown Unicode."""

    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text(
            (72, 72),
            "\u6d4b\u8bd5\u6587\u5b57",
            fontname="china-s",
        )
        font_xref = page.get_fonts(full=True)[0][0]
        cmap_xref = doc.get_new_xref()
        doc.update_object(cmap_xref, "<<>>")
        doc.update_stream(
            cmap_xref,
            b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Zeroed-ToUnicode def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
4 beginbfchar
<6D4B> <0000>
<8BD5> <0000>
<6587> <0000>
<5B57> <0000>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end""",
        )
        font_object = doc.xref_object(font_xref, compressed=False).rstrip()
        assert font_object.endswith(">>")
        doc.update_object(
            font_xref,
            f"{font_object[:-2]}/ToUnicode {cmap_xref} 0 R\n>>",
        )
        return doc.tobytes()
    finally:
        doc.close()


def test_parse_page_range_accepts_single_page() -> None:
    assert parse_page_range("2", 5) == (1, 1)


def test_parse_page_range_accepts_ranges() -> None:
    assert parse_page_range("2-4", 5) == (1, 3)


@pytest.mark.parametrize("spec", ["0", "4-2", "abc", "1-", "-2"])
def test_parse_page_range_rejects_invalid_values(spec: str) -> None:
    with pytest.raises(ValidationError, match="Invalid page range format"):
        parse_page_range(spec, 5)


def test_parse_page_range_clamps_end_to_document_length() -> None:
    assert parse_page_range("3-9", 5) == (2, 4)


def test_parse_page_range_rejects_start_beyond_document_length() -> None:
    with pytest.raises(ValidationError, match="starts beyond document length"):
        parse_page_range("6-8", 5)


def test_classify_font_detects_expected_strategies() -> None:
    assert classify_font("ABCDEF+Preeti", "Type0") == "legacy_remap"
    assert classify_font("ABCDEF+Kalimati", "Type0") == "broken_cmap"
    assert classify_font("Helvetica", "Type1") == "correct"


def test_font_based_strategy_rejects_non_pdf_input(tmp_path: Path) -> None:
    source = tmp_path / "document.docx"
    source.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(ValidationError, match="Please upload a PDF file"):
        FontBasedStrategy().extract_text(str(source))


def test_font_based_strategy_auto_detects_and_converts_legacy_fonts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "legacy.pdf"
    source.write_bytes(b"%PDF-1.4")

    class FakePage:
        # Scanned-page (OCR) analysis needs page geometry + image coverage; this
        # page carries no images, so it is never routed to OCR.
        rect = fitz.Rect(0, 0, 595, 842)

        def get_images(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return []

        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(1, "ttf", "Type0", "ABCDEF+Preeti", "Identity-H")]

        def get_text(self, mode: str, flags: int | None = None) -> dict[str, object]:
            assert mode == "rawdict"
            del flags
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "font": "ABCDEF+Preeti",
                                        "chars": [
                                            {
                                                "c": "a",
                                                "bbox": (10.0, 20.0, 20.0, 35.0),
                                            },
                                            {
                                                "c": "b",
                                                "bbox": (20.0, 20.0, 30.0, 35.0),
                                            },
                                            {
                                                "c": "c",
                                                "bbox": (30.0, 20.0, 40.0, 35.0),
                                            },
                                        ],
                                        "bbox": (10.0, 20.0, 40.0, 35.0),
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def close(self) -> None:
            return None

    monkeypatch.setattr(font_based_module.fitz, "open", lambda _: FakeDoc())
    monkeypatch.setattr(
        font_based_module,
        "get_converter",
        lambda _font_name: lambda text: f"converted:{text}",
    )
    monkeypatch.setattr(
        font_based_module,
        "detect_name_legacy_candidates",
        lambda *_args: frozenset({"ABCDEF+Preeti"}),
    )

    result = FontBasedStrategy().extract_text(str(source))

    assert result.raw_text == "converted:abc"
    assert result.fragments[0].text == "converted:abc"


def test_extract_from_document_can_skip_table_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detected_pages: list[object] = []

    class FakePage:
        def get_text(self, mode: str, flags: int | None = None) -> dict[str, object]:
            assert mode == "rawdict"
            del flags
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "font": "Kalimati",
                                        "chars": [
                                            {
                                                "c": char,
                                                "bbox": (
                                                    10.0 + 8.0 * index,
                                                    20.0,
                                                    18.0 + 8.0 * index,
                                                    35.0,
                                                ),
                                            }
                                            for index, char in enumerate("परीक्षण")
                                        ],
                                        "bbox": (10.0, 20.0, 70.0, 35.0),
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }

    class FakeDoc:
        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

    monkeypatch.setattr(
        font_based_module,
        "detect_page_tables",
        lambda page, fragments, index: detected_pages.append(page),
    )

    result = FontBasedStrategy()._extract_from_document(
        FakeDoc(),  # type: ignore[arg-type]
        {},
        page_start=0,
        page_end=0,
        needs_reorder=False,
        detect_tables=False,
    )

    assert result.raw_text == "परीक्षण"
    assert result.tables == []
    assert detected_pages == []


def test_extract_from_document_preserves_distinct_unknown_cids() -> None:
    doc = fitz.open(stream=_build_zeroed_tounicode_pdf(), filetype="pdf")
    try:
        result = FontBasedStrategy()._extract_from_document(
            doc,
            {},
            page_start=0,
            page_end=0,
            needs_reorder=False,
            detect_tables=False,
        )
    finally:
        doc.close()

    extracted = result.raw_text.strip()
    assert "\ufffd" not in extracted
    assert "\x00" not in extracted
    assert len(extracted) == 4
    assert len(set(extracted)) == 4


def _run_broken_cmap_table_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    repaired_has_tables: bool,
    malformed_tables: bool = True,
) -> tuple[
    list[tuple[object, bool, bool]],
    list[tuple[object, list[str]]],
    RawDocument,
    Table,
    Table,
]:
    source = tmp_path / "broken-cmap.pdf"
    source.write_bytes(b"%PDF-1.4")

    class FakeDoc:
        page_count = 1

        def close(self) -> None:
            return None

    original_doc = FakeDoc()
    repaired_source = FakeDoc()
    repaired_doc = FakeDoc()
    opened_docs = iter([original_doc, repaired_source])

    table_text = "श्रर्ी सफा" if malformed_tables else "श्री सफा"
    candidate_table_text = "श्री बिग्रिएको" if malformed_tables else "श्री सफा"
    original_table = Table(
        row_count=2,
        col_count=2,
        cells=[TableCell(row=0, col=0, text=table_text)],
    )
    repaired_table = Table(
        row_count=3,
        col_count=2,
        cells=[TableCell(row=0, col=0, text=table_text)],
    )
    original_candidate_table = Table(
        row_count=2,
        col_count=2,
        cells=[TableCell(row=0, col=0, text=candidate_table_text)],
    )
    repaired_candidate_table = Table(
        row_count=3,
        col_count=2,
        cells=[TableCell(row=0, col=0, text=candidate_table_text)],
    )
    calls: list[tuple[object, bool, bool]] = []
    table_calls: list[tuple[object, list[str]]] = []

    monkeypatch.setattr(font_based_module.fitz, "open", lambda _: next(opened_docs))
    monkeypatch.setattr(
        font_based_module,
        "scan_pdf_fonts_by_page",
        lambda _doc, _embedded=None: {1: {"Kalimati": "broken_cmap"}},
    )
    monkeypatch.setattr(font_based_module, "scan_ocr_pages", lambda _doc: {})
    monkeypatch.setattr(
        font_based_module,
        "detect_content_legacy_fonts",
        lambda _doc, _skip, _embedded=None: {},
    )
    monkeypatch.setattr(
        font_based_module,
        "detect_name_legacy_candidates",
        lambda *_args: frozenset(),
    )
    monkeypatch.setattr(
        font_based_module,
        "fix_kalimati_cmap",
        lambda source_doc: (repaired_doc, False),
    )

    def fake_extract_from_document(
        self: FontBasedStrategy,
        doc: object,
        font_strategies_by_page: dict[int, dict[str, str]],
        **kwargs: object,
    ) -> RawDocument:
        del self, font_strategies_by_page
        detect_tables = bool(kwargs.get("detect_tables", True))
        merge_tables = bool(kwargs.get("merge_tables", True))
        calls.append((doc, detect_tables, merge_tables))
        assert doc is original_doc or doc is repaired_doc
        fragments = [
            TextFragment(
                "� सही" if doc is original_doc else "ठीक �",
                1,
                10.0,
                20.0,
                70.0,
                35.0,
                block_number=1,
                line_number=1,
            ),
            TextFragment(
                "सञ् चाल" if doc is original_doc else "सञ्चाल",
                1,
                10.0,
                40.0,
                70.0,
                55.0,
                block_number=2,
                line_number=1,
            ),
        ]
        return RawDocument(
            paragraphs=[fragment.text for fragment in fragments],
            raw_text="\n\n".join(fragment.text for fragment in fragments),
            fragments=fragments,
            tables=(
                [original_table]
                if doc is original_doc and detect_tables
                else [repaired_table]
                if repaired_has_tables and detect_tables
                else []
            ),
        )

    monkeypatch.setattr(
        FontBasedStrategy,
        "_extract_from_document",
        fake_extract_from_document,
    )

    def fake_detect_tables_from_fragments(
        doc: object,
        fragments: list[TextFragment],
        *,
        page_start: int,
        page_end: int,
    ) -> list[Table]:
        assert (page_start, page_end) == (0, 0)
        table_calls.append((doc, [fragment.text for fragment in fragments]))
        if doc is repaired_doc:
            return [repaired_candidate_table] if repaired_has_tables else []
        assert doc is original_doc
        return [original_candidate_table]

    monkeypatch.setattr(
        font_based_module,
        "_detect_tables_from_fragments",
        fake_detect_tables_from_fragments,
    )

    result = FontBasedStrategy().extract_text(str(source))
    # `table_calls` is what this helper gained: the fragment-variant table detection
    # records the document and fragment texts it was handed, so a caller can assert the
    # repair compared BOTH passes rather than re-detecting on one.
    #
    # The other line also asserted here that `embedded_legacy_maps` reached three
    # consumers as the same object. That assertion is not carried: it reads locals this
    # helper no longer has, because the helper was restructured here, and the property is
    # covered by tests/test_embedded_name_legacy_candidacy.py,
    # tests/test_name_legacy_reconciliation.py and tests/test_digit_companion.py.
    return calls, table_calls, result, original_table, repaired_table


def test_broken_cmap_builds_repaired_tables_from_merged_fragments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, table_calls, result, _original_table, repaired_table = (
        _run_broken_cmap_table_flow(
            monkeypatch,
            tmp_path,
            repaired_has_tables=True,
        )
    )

    assert [(detect, merge) for _doc, detect, merge in calls] == [
        (False, True),
        (True, False),
    ]
    assert table_calls == [(calls[1][0], ["ठीक सही", "सञ्चाल"])]
    assert result.raw_text == "ठीक सही\n\nसञ्चाल"
    assert result.tables[0].row_count == repaired_table.row_count
    assert result.tables[0].cells[0].text == "श्री सफा"
    assert repaired_table.cells[0].text == "श्रर्ी सफा"


def test_broken_cmap_recovers_unrepaired_tables_when_repair_finds_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, table_calls, result, original_table, _repaired_table = (
        _run_broken_cmap_table_flow(
            monkeypatch,
            tmp_path,
            repaired_has_tables=False,
        )
    )

    assert [(detect, merge) for _doc, detect, merge in calls] == [
        (False, True),
        (True, False),
        (True, False),
    ]
    assert calls[0][0] is calls[2][0]
    assert table_calls == [(calls[0][0], ["ठीक सही", "सञ्चाल"])]
    assert result.tables[0].row_count == original_table.row_count
    assert result.tables[0].cells[0].text == "श्री सफा"
    assert original_table.cells[0].text == "श्रर्ी सफा"


def test_broken_cmap_does_not_rebuild_clean_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, table_calls, result, _original_table, repaired_table = (
        _run_broken_cmap_table_flow(
            monkeypatch,
            tmp_path,
            repaired_has_tables=True,
            malformed_tables=False,
        )
    )

    assert [(detect, merge) for _doc, detect, merge in calls] == [
        (False, True),
        (True, False),
    ]
    assert table_calls == []
    assert result.tables == [repaired_table]


def test_broken_cmap_does_not_rebuild_clean_fallback_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, table_calls, result, original_table, _repaired_table = (
        _run_broken_cmap_table_flow(
            monkeypatch,
            tmp_path,
            repaired_has_tables=False,
            malformed_tables=False,
        )
    )

    assert [(detect, merge) for _doc, detect, merge in calls] == [
        (False, True),
        (True, False),
        (True, False),
    ]
    assert table_calls == []
    assert result.tables == [original_table]


def test_font_based_strategy_wraps_unexpected_extraction_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fix_kalimati_cmap(doc: object) -> tuple[object, bool]:
        raise RuntimeError("boom")

    monkeypatch.setattr(font_based_module, "fix_kalimati_cmap", fake_fix_kalimati_cmap)

    with pytest.raises(ExtractionError, match="Failed to extract text from PDF"):
        FontBasedStrategy().extract_text(str(PRESS_RELEASE))


def test_kalimati_fix_requires_fonttools(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("fontTools"):
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ExtractionError, match="fonttools is required"):
        _get_font_correction_map(None, 1)  # type: ignore[arg-type]


class _FontObjectDoc:
    """A document whose font objects are whatever the test says they are."""

    def __init__(self, objects: dict[int, str], stream: bytes = b"font-data") -> None:
        self._objects = objects
        self._stream = stream
        self.requested: list[int] = []

    def xref_object(self, xref: int, compressed: bool = False) -> str:
        del compressed
        self.requested.append(xref)
        return self._objects[xref]

    def xref_stream(self, xref: int) -> bytes:
        return self._stream


def test_fontfile2_is_found_through_a_fully_indirect_chain() -> None:
    doc = _FontObjectDoc(
        {
            1: "<< /DescendantFonts [2 0 R] >>",
            2: "<< /FontDescriptor 3 0 R >>",
            3: "<< /FontFile2 4 0 R >>",
        }
    )
    assert _resolve_fontfile2_xref(doc, 1) == 4  # type: ignore[arg-type]


def test_fontfile2_is_found_when_the_descendant_dictionary_is_inline() -> None:
    # `/DescendantFonts` may hold the CIDFont dictionary itself rather than a
    # reference to it. Following only `N 0 R` skipped these fonts entirely, so
    # they got no correction map and every glyph stayed unmapped.
    doc = _FontObjectDoc(
        {
            1: (
                "<< /BaseFont /CIDFont+F1 /DescendantFonts [ << /BaseFont /CIDFont+F1"
                " /CIDToGIDMap /Identity /FontDescriptor << /Flags 6"
                " /FontFile2 33 0 R >> >> ] >>"
            )
        }
    )
    assert _resolve_fontfile2_xref(doc, 1) == 33  # type: ignore[arg-type]
    assert doc.requested == [1], "an inline dictionary is not a separate object"


def test_fontfile2_is_found_when_only_the_descriptor_is_inline() -> None:
    doc = _FontObjectDoc(
        {
            1: "<< /DescendantFonts [2 0 R] >>",
            2: "<< /CIDToGIDMap /Identity /FontDescriptor << /FontFile2 9 0 R >> >>",
        }
    )
    assert _resolve_fontfile2_xref(doc, 1) == 9  # type: ignore[arg-type]


def test_a_font_with_no_embedded_truetype_program_resolves_to_none() -> None:
    # A CFF program lives in /FontFile3, which this repair cannot read, and a
    # non-embedded font has no program at all. Both must stay unresolved rather
    # than resolving to something else in the object.
    cff = _FontObjectDoc(
        {1: "<< /DescendantFonts [ << /FontDescriptor << /FontFile3 7 0 R >> >> ] >>"}
    )
    assert _resolve_fontfile2_xref(cff, 1) is None  # type: ignore[arg-type]
    bare = _FontObjectDoc({1: "<< /BaseFont /Times-Roman >>"})
    assert _resolve_fontfile2_xref(bare, 1) is None  # type: ignore[arg-type]


def test_get_fontfile_xref_reports_none_for_an_unreadable_object() -> None:
    class Exploding:
        def xref_object(self, xref: int, compressed: bool = False) -> str:
            raise RuntimeError("unparsable")

    assert _get_fontfile_xref(Exploding(), 1) is None  # type: ignore[arg-type]


def test_get_font_correction_map_reads_an_inline_descendant_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFont:
        def getGlyphOrder(self) -> list[str]:
            return ["glyph0", "ka"]

        def __contains__(self, key: str) -> bool:
            return key == "cmap"

        def __getitem__(self, key: str) -> object:
            assert key == "cmap"

            class Cmap:
                def getBestCmap(self) -> dict[int, str]:
                    return {0x0915: "ka"}

            return Cmap()

        def close(self) -> None:
            return None

    doc = _FontObjectDoc(
        {
            1: (
                "<< /BaseFont /CIDFont+F1 /DescendantFonts [ << /FontDescriptor"
                " << /FontFile2 33 0 R >> >> ] >>"
            )
        }
    )
    fake_fonttools = types.ModuleType("fontTools")
    fake_ttlib = types.ModuleType("fontTools.ttLib")
    fake_ttlib.TTFont = lambda _path: FakeFont()
    fake_fonttools.ttLib = fake_ttlib
    monkeypatch.setitem(sys.modules, "fontTools", fake_fonttools)
    monkeypatch.setitem(sys.modules, "fontTools.ttLib", fake_ttlib)
    monkeypatch.setattr(kalimati_module, "_infer_mark_variants", lambda *_: {})
    monkeypatch.setattr(kalimati_module, "_analyze_gsub", lambda *_: {})

    assert _get_font_correction_map(doc, 1) == {1: "क"}  # type: ignore[arg-type]


def test_get_font_correction_map_returns_empty_when_font_has_no_cmap(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeFont:
        def getGlyphOrder(self) -> list[str]:
            return ["glyph0"]

        def __contains__(self, key: str) -> bool:
            return key != "cmap"

        def close(self) -> None:
            return None

    class FakeDoc:
        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            mapping = {
                1: "<< /DescendantFonts [2 0 R] >>",
                2: "<< /FontDescriptor 3 0 R >>",
                3: "<< /FontFile2 4 0 R >>",
            }
            return mapping[xref]

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 4
            return b"font-data"

    fake_fonttools = types.ModuleType("fontTools")
    fake_ttlib = types.ModuleType("fontTools.ttLib")
    fake_ttlib.TTFont = lambda _path: FakeFont()
    fake_fonttools.ttLib = fake_ttlib
    monkeypatch.setitem(sys.modules, "fontTools", fake_fonttools)
    monkeypatch.setitem(sys.modules, "fontTools.ttLib", fake_ttlib)

    with caplog.at_level("WARNING"):
        result = _get_font_correction_map(FakeDoc(), 1)  # type: ignore[arg-type]

    assert result == {}
    assert "Failed to build Kalimati correction map" not in caplog.text


def test_simple_truetype_corrections_are_translated_from_gids_to_pdf_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCmapTable:
        cmap = {65: "glyph7", 66: "glyph8"}

        def isUnicode(self) -> bool:
            return False

    class FakeFont:
        def getGlyphOrder(self) -> list[str]:
            return [".notdef"] + [f"glyph{gid}" for gid in range(1, 9)]

        def __contains__(self, key: str) -> bool:
            return key == "cmap"

        def __getitem__(self, key: str) -> object:
            assert key == "cmap"
            return types.SimpleNamespace(tables=[FakeCmapTable()])

        def close(self) -> None:
            return None

    doc = _FontObjectDoc(
        {
            1: "<< /FontDescriptor 2 0 R >>",
            2: "<< /Flags 4 /FontFile2 3 0 R >>",
        }
    )
    monkeypatch.setattr(
        "fontTools.ttLib.TTFont",
        lambda _stream: FakeFont(),
    )

    result = _get_simple_font_correction_map(
        doc,  # type: ignore[arg-type]
        1,
        {7: "क्ष", 8: "त्र", 99: "unused"},
    )

    assert result == {65: "क्ष", 66: "त्र"}


@pytest.mark.parametrize(
    ("font_object", "descriptor"),
    [
        (
            "<< /Encoding /WinAnsiEncoding /FontDescriptor 2 0 R >>",
            "<< /Flags 4 /FontFile2 3 0 R >>",
        ),
        (
            "<< /FontDescriptor 2 0 R >>",
            "<< /Flags 32 /FontFile2 3 0 R >>",
        ),
    ],
)
def test_simple_truetype_corrections_require_an_embedded_symbolic_encoding(
    monkeypatch: pytest.MonkeyPatch,
    font_object: str,
    descriptor: str,
) -> None:
    doc = _FontObjectDoc({1: font_object, 2: descriptor})
    monkeypatch.setattr(
        "fontTools.ttLib.TTFont",
        lambda _stream: pytest.fail("an unsupported encoding must not open the font"),
    )

    result = _get_simple_font_correction_map(
        doc,  # type: ignore[arg-type]
        1,
        {7: "क्ष"},
    )

    assert result == {}


def test_generic_type0_fill_requires_identity_codes_and_gids() -> None:
    identity = _FontObjectDoc(
        {
            1: (
                "<< /Encoding /Identity-H /DescendantFonts "
                "[ << /CIDToGIDMap /Identity >> ] >>"
            )
        }
    )
    remapped = _FontObjectDoc(
        {
            1: (
                "<< /Encoding /Identity-H /DescendantFonts "
                "[ << /CIDToGIDMap 2 0 R >> ] >>"
            )
        }
    )

    assert kalimati_module._type0_codes_are_gids(identity, 1)  # type: ignore[arg-type]
    assert not kalimati_module._type0_codes_are_gids(remapped, 1)  # type: ignore[arg-type]


def test_generic_type0_fill_preserves_authored_entries_and_rejects_nul() -> None:
    authored = {1: "क", 4: "authored", 7: " ", 8: "-"}
    reconstructed = {
        1: "क",
        2: "ख",
        3: "\x00",
        4: "authored",
        5: "ि",
        6: "र्",
        7: "\xa0",
        8: "\xad",
    }

    assert kalimati_module._agreed_missing_cmap_entries(
        authored,
        reconstructed,
    ) == {
        2: "ख",
        5: kalimati_module._PUA_IKAR,
        6: kalimati_module._PUA_REPH,
    }


def test_generic_type0_overlap_disagreement_blocks_every_fill() -> None:
    authored = {1: "क"}
    reconstructed = {1: "ग", 2: "ख"}

    assert kalimati_module._agreed_missing_cmap_entries(authored, reconstructed) == {}


def test_generic_type0_fill_drops_conflicting_reconstruction_sources() -> None:
    assert kalimati_module._merge_missing_cmap_entries(
        {2: "ख", 3: "ग"},
        {2: "ख", 3: "घ", 4: "ङ"},
    ) == {2: "ख", 4: "ङ"}


def test_simple_truetype_repair_requires_strong_authored_overlap_agreement() -> None:
    assert kalimati_module._simple_font_correction_is_credible(
        {1: "क", 2: "ख", 3: "ग", 4: "broken"},
        {1: "क", 2: "ख", 3: "ग", 4: "घ"},
    )
    assert not kalimati_module._simple_font_correction_is_credible(
        {1: "1", 2: "2", 3: "3"},
        {1: "१", 2: "२", 3: "३"},
    )


def test_named_simple_truetype_ascii_digit_normalization_is_already_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(11, "ttf", "TrueType", "ABCDEF+Kalimati", "BuiltIn")]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            assert xref == 11
            return "<< /ToUnicode 12 0 R >>"

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            assert xref == 12
            return True

    source = FakeDoc()
    monkeypatch.setattr(
        kalimati_module,
        "_parse_tounicode_cmap",
        lambda cmap_bytes: {33: "5", 34: "0"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_font_correction_map",
        lambda doc, xref: {7: "५", 8: "०"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_simple_font_correction_map",
        lambda doc, xref, correction_map: {
            33: correction_map[7],
            34: correction_map[8],
        },
    )

    repaired_doc, needs_reorder = kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]

    assert repaired_doc is source
    assert needs_reorder is False


def test_fix_kalimati_cmap_repairs_a_simple_truetype_kalimati_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched_maps: list[tuple[int, dict[int, str]]] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(11, "ttf", "TrueType", "ABCDEF+Kalimati", "WinAnsiEncoding")]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            assert xref == 11
            return "<< /ToUnicode 12 0 R >>"

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            assert xref == 12
            return True

        def save(self, buffer) -> None:
            buffer.write(b"%PDF-1.4")

        def close(self) -> None:
            return None

    reopened_doc = object()
    monkeypatch.setattr(
        kalimati_module,
        "_parse_tounicode_cmap",
        lambda cmap_bytes: {65: "क", 66: "ख", 67: "ग", 68: "broken"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_font_correction_map",
        lambda doc, xref: {7: "क", 8: "ख", 9: "ग", 10: "क्ष"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_simple_font_correction_map",
        lambda doc, xref, correction_map: {
            65: correction_map[7],
            66: correction_map[8],
            67: correction_map[9],
            68: correction_map[10],
        },
    )
    monkeypatch.setattr(
        kalimati_module,
        "_patch_single_cmap",
        lambda doc, to_unicode_xref, correction_map: patched_maps.append(
            (to_unicode_xref, dict(correction_map))
        ),
    )
    monkeypatch.setattr(
        kalimati_module.fitz,
        "open",
        lambda *args, **kwargs: reopened_doc,
    )

    repaired_doc, needs_reorder = kalimati_module.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert repaired_doc is reopened_doc
    assert needs_reorder is True
    assert patched_maps == [(12, {65: "क", 66: "ख", 67: "ग", 68: "क्ष"})]


def test_unrepairable_generic_type0_font_is_not_an_extraction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(11, "ttf", "Type0", "CIDFont+F1", "Identity-H")]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            assert xref == 11
            return (
                "<< /Encoding /Identity-H /DescendantFonts "
                "[ << /CIDToGIDMap /Identity >> ] /ToUnicode 12 0 R >>"
            )

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            assert xref == 12
            return True

    source = FakeDoc()
    monkeypatch.setattr(
        kalimati_module,
        "_parse_tounicode_cmap",
        lambda cmap_bytes: {1: "authored"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_font_correction_map",
        lambda doc, xref: {},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_collect_trace_fallback_map",
        lambda doc, font_name: {},
    )

    repaired_doc, needs_reorder = kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]

    assert repaired_doc is source
    assert needs_reorder is False


@pytest.mark.parametrize(
    ("font_type", "font_name"),
    [
        ("Type0", "ABCDEF+Kalimati"),
        ("Type0", "ABCDEF+Lohit-Devanagari"),
        ("TrueType", "ABCDEF+Kalimati"),
    ],
)
def test_unrepairable_named_font_remains_an_extraction_failure(
    monkeypatch: pytest.MonkeyPatch,
    font_type: str,
    font_name: str,
) -> None:
    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(11, "ttf", font_type, font_name, "Identity-H")]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            assert xref == 11
            return "<< /ToUnicode 12 0 R >>"

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            assert xref == 12
            return True

    monkeypatch.setattr(
        kalimati_module,
        "_parse_tounicode_cmap",
        lambda cmap_bytes: {1: "authored"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_font_correction_map",
        lambda doc, xref: {},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_collect_trace_fallback_map",
        lambda doc, name: {},
    )

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]


def test_a_generic_fill_cannot_mask_an_unrepairable_named_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched: list[tuple[int, dict[int, str]]] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [
                (11, "ttf", "Type0", "ABCDEF+Kalimati", "Identity-H"),
                (21, "ttf", "Type0", "CIDFont+F1", "Identity-H"),
            ]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            return {
                11: "<< /ToUnicode 12 0 R >>",
                21: (
                    "<< /Encoding /Identity-H /DescendantFonts "
                    "[ << /CIDToGIDMap /Identity >> ] /ToUnicode 22 0 R >>"
                ),
            }[xref]

        def xref_stream(self, xref: int) -> bytes:
            return {12: b"named", 22: b"generic"}[xref]

        def xref_is_stream(self, xref: int) -> bool:
            return xref in {12, 22}

    monkeypatch.setattr(
        kalimati_module,
        "_parse_tounicode_cmap",
        lambda cmap_bytes: {1: "authored"} if cmap_bytes == b"named" else {1: "क"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_font_correction_map",
        lambda doc, xref: {} if xref == 11 else {1: "क", 2: "ख"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_collect_trace_fallback_map",
        lambda doc, name: {},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_patch_missing_cmap_entries",
        lambda doc, to_unicode_xref, pdf_map, missing: patched.append(
            (to_unicode_xref, dict(missing))
        ),
    )

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert patched == [(22, {2: "ख"})]


def test_fix_kalimati_cmap_uses_trace_fallback_when_font_map_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched_maps: list[tuple[int, dict[int, str]]] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(11, "ttf", "Type0", "ABCDEF+Kalimati", "Identity-H")]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            assert xref == 11
            return "<< /ToUnicode 12 0 R >>"

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            assert xref == 12
            return True

        def save(self, buffer) -> None:
            buffer.write(b"%PDF-1.4")

        def close(self) -> None:
            return None

    reopened_doc = object()
    monkeypatch.setattr(
        kalimati_module,
        "_collect_trace_fallback_map",
        lambda doc, font_name: {7: "का"},
    )
    monkeypatch.setattr(kalimati_module, "_get_fontfile_xref", lambda doc, xref: None)
    monkeypatch.setattr(
        kalimati_module,
        "_parse_tounicode_cmap",
        lambda cmap_bytes: {7: "x"},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_get_font_correction_map",
        lambda doc, xref: {},
    )
    monkeypatch.setattr(
        kalimati_module,
        "_patch_single_cmap",
        lambda doc, to_unicode_xref, correction_map, *, font_name, allow_gid_exceptions: (
            patched_maps.append((to_unicode_xref, dict(correction_map)))
        ),
    )
    monkeypatch.setattr(
        kalimati_module.fitz,
        "open",
        lambda *args, **kwargs: reopened_doc,
    )

    repaired_doc, needs_reorder = kalimati_module.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert repaired_doc is reopened_doc
    assert needs_reorder is True
    assert patched_maps == [(12, {7: "का"})]


def test_parse_tounicode_cmap_treats_a_missing_stream_as_no_mapping() -> None:
    # `doc.xref_stream()` returns None when /ToUnicode names a non-stream object.
    # The annotation used to say `bytes` and the body dereferenced it directly.
    assert kalimati_module._parse_tounicode_cmap(None) == {}


def test_a_non_stream_tounicode_font_is_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One malformed font must not cost the whole document.

    A /ToUnicode reference can point at an object that is not a stream. Reading
    it returns None and writing it raises "object is no PDF dict", and because
    `_extract_raw_document` wraps every exception into ExtractionError, that took
    down extraction for the entire PDF -- after which `nepali_pdf` fell back to
    pdfminer, which renders each glyph it cannot decode as U+0000. Measured on
    OAG document 13006: 8,834 NULs in place of 8,834 conjuncts and matras, in
    every generation v6..v12.

    The good font in this fixture is what makes the assertion meaningful: the
    malformed one is skipped *and* its sibling is still repaired.
    """
    patched_maps: list[tuple[int, dict[int, str]]] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [
                (11, "ttf", "Type0", "ABCDEF+Kalimati", "Identity-H"),
                (21, "ttf", "Type0", "GHIJKL+Kalimati", "Identity-H"),
            ]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            return {11: "<< /ToUnicode 12 0 R >>", 21: "<< /ToUnicode 22 0 R >>"}[xref]

        def xref_is_stream(self, xref: int) -> bool:
            # xref 22 is the malformed one: referenced, but not a stream.
            return xref == 12

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12, "the non-stream xref must never be read"
            return b"unused"

        def save(self, buffer) -> None:
            buffer.write(b"%PDF-1.4")

        def close(self) -> None:
            return None

    reopened_doc = object()
    monkeypatch.setattr(
        kalimati_module,
        "_collect_trace_fallback_map",
        lambda doc, font_name: {7: "का"},
    )
    monkeypatch.setattr(kalimati_module, "_get_fontfile_xref", lambda doc, xref: None)
    monkeypatch.setattr(
        kalimati_module, "_parse_tounicode_cmap", lambda cmap_bytes: {7: "x"}
    )
    monkeypatch.setattr(
        kalimati_module, "_get_font_correction_map", lambda doc, xref: {}
    )
    monkeypatch.setattr(
        kalimati_module,
        "_patch_single_cmap",
        lambda doc, to_unicode_xref, correction_map, *, font_name, allow_gid_exceptions: (
            patched_maps.append((to_unicode_xref, dict(correction_map)))
        ),
    )
    monkeypatch.setattr(
        kalimati_module.fitz, "open", lambda *args, **kwargs: reopened_doc
    )

    repaired_doc, _needs_reorder = kalimati_module.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert repaired_doc is reopened_doc
    # The well-formed font is still repaired; the malformed one is absent.
    assert patched_maps == [(12, {7: "का"})]


def _conflicting_ligature_gsub() -> tuple[dict[str, object], list[str], dict[int, str]]:
    """A GSUB where two ligature rules write the same output glyph differently.

    Both rules target ``gLig`` (gid 3) but resolve to different strings, so an
    unbounded fixpoint keeps overwriting ``derived[3]`` and ``changed`` never
    settles. This is the shape that made ``_analyze_gsub`` spin at 100% CPU
    forever on born-digital PDFs embedding several unrelated fonts.
    """
    glyph_order = ["g0", "gA", "gB", "gLig"]  # gids 0..3
    gid_to_correct = {1: "अ", 2: "आ"}  # gA -> अ, gB -> आ
    lig_a = types.SimpleNamespace(LigGlyph="gLig", Component=["gA"])  # gA+gA -> gLig
    lig_b = types.SimpleNamespace(LigGlyph="gLig", Component=["gB"])  # gB+gB -> gLig
    subtable = types.SimpleNamespace(ligatures={"gA": [lig_a], "gB": [lig_b]})
    lookup = types.SimpleNamespace(LookupType=4, SubTable=[subtable])
    feature = types.SimpleNamespace(
        FeatureTag="liga", Feature=types.SimpleNamespace(LookupListIndex=[0])
    )
    table = types.SimpleNamespace(
        FeatureList=types.SimpleNamespace(FeatureRecord=[feature]),
        LookupList=types.SimpleNamespace(Lookup=[lookup]),
    )
    return {"GSUB": types.SimpleNamespace(table=table)}, glyph_order, gid_to_correct


def _run_analyze_gsub_with_timeout(
    font: dict[str, object],
    glyph_order: list[str],
    gid_to_correct: dict[int, str],
    timeout: float = 10.0,
) -> dict[int, str]:
    """Run ``_analyze_gsub`` on a worker thread and fail if it does not return.

    A hung fixpoint would block forever, so the call is isolated on a daemon
    thread; a worker exception is re-raised in the caller rather than silently
    passing the ``is_alive`` check.
    """
    result: dict[str, dict[int, str]] = {}
    error: list[BaseException] = []

    def run() -> None:
        try:
            result["derived"] = kalimati_module._analyze_gsub(
                font, glyph_order, gid_to_correct
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced in the main thread
            error.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=timeout)

    assert not worker.is_alive(), (
        "_analyze_gsub did not terminate: the ligature fixpoint is unbounded"
    )
    if error:
        raise error[0]
    return result["derived"]


def test_analyze_gsub_terminates_on_conflicting_ligature_rules() -> None:
    font, glyph_order, gid_to_correct = _conflicting_ligature_gsub()

    derived = _run_analyze_gsub_with_timeout(font, glyph_order, gid_to_correct)

    # The contested glyph resolves to one of its two twins (last writer wins);
    # what matters is that resolution completes at all.
    assert derived.get(3) in {"अअ", "आआ"}


def test_analyze_gsub_fully_resolves_a_multi_pass_ligature_chain() -> None:
    # A nested chain L1 <- L2 <- L3 <- base, with the rules listed consumer-first
    # so the fixpoint propagates exactly one link per pass — the worst-case
    # ordering that needs the full len(rules)-pass budget. This guards the bound
    # against being weakened to a value that would truncate a legitimate,
    # convergent multi-pass font.
    glyph_order = ["g0", "base", "L3", "L2", "L1"]  # gids 0..4
    gid_to_correct = {1: "क"}  # base -> क
    lig_l1 = types.SimpleNamespace(LigGlyph="L1", Component=["base"])  # L2+base -> L1
    lig_l2 = types.SimpleNamespace(LigGlyph="L2", Component=["base"])  # L3+base -> L2
    lig_l3 = types.SimpleNamespace(LigGlyph="L3", Component=["base"])  # base+base -> L3
    # Insertion order fixes ligature_rules to [L1, L2, L3] (consumer before producer).
    subtable = types.SimpleNamespace(
        ligatures={"L2": [lig_l1], "L3": [lig_l2], "base": [lig_l3]}
    )
    lookup = types.SimpleNamespace(LookupType=4, SubTable=[subtable])
    feature = types.SimpleNamespace(
        FeatureTag="liga", Feature=types.SimpleNamespace(LookupListIndex=[0])
    )
    table = types.SimpleNamespace(
        FeatureList=types.SimpleNamespace(FeatureRecord=[feature]),
        LookupList=types.SimpleNamespace(Lookup=[lookup]),
    )
    font: dict[str, object] = {"GSUB": types.SimpleNamespace(table=table)}

    derived = _run_analyze_gsub_with_timeout(font, glyph_order, gid_to_correct)

    # Every link must be fully resolved despite the backward ordering.
    assert derived.get(2) == "कक"  # L3
    assert derived.get(3) == "ककक"  # L2
    assert derived.get(4) == "कककक"  # L1


def test_handler_keeps_table_content_after_numbered_prose() -> None:
    handler = SingleColumnNoticeHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: परीक्षण शीर्षक ।", 1, 180, 130, 340, 150),
            TextFragment("1. पहिलो बुँदा", 1, 45, 200, 400, 220),
            TextFragment("यसको व्याख्या", 1, 45, 220, 420, 240),
            TextFragment("देहाय:", 1, 250, 260, 320, 280),
            TextFragment("सि.नं स्तम्भ", 1, 45, 280, 420, 300),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "1. पहिलो बुँदा यसको व्याख्या" in result.sections[0].body
    assert "देहाय: सि.नं स्तम्भ" in result.sections[0].body


def test_handler_keeps_footer_signature_in_body() -> None:
    handler = SingleColumnNoticeHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: परीक्षण शीर्षक ।", 1, 180, 130, 340, 150),
            TextFragment("मुख्य अनुच्छेद", 1, 45, 200, 420, 220),
            TextFragment("हस्ताक्षरकर्ता", 1, 300, 500, 390, 520),
            TextFragment("कुनै व्यक्ति", 1, 260, 520, 420, 540),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "मिति: २०८२।०१।१४" in result.sections[0].body
    assert "विषय: परीक्षण शीर्षक" in result.sections[0].body
    assert "मुख्य अनुच्छेद" in result.sections[0].body
    assert "हस्ताक्षरकर्ता" in result.sections[0].body
    assert "कुनै व्यक्ति" in result.sections[0].body


def test_handler_keeps_body_when_it_starts_with_table_content() -> None:
    handler = SingleColumnNoticeHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: परीक्षण शीर्षक ।", 1, 180, 130, 340, 150),
            TextFragment("देहाय:", 1, 250, 200, 320, 220),
            TextFragment("सि.नं", 1, 45, 220, 120, 240),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "मिति: २०८२।०१।१४" in result.sections[0].body
    assert "विषय: परीक्षण शीर्षक" in result.sections[0].body
    assert "देहाय: सि.नं" in result.sections[0].body


def test_join_words_with_spacing_preserves_word_boundary() -> None:
    joined = join_words_with_spacing(["Mindray", "BS-230"])

    assert joined == "Mindray BS-230"


def test_join_spans_with_layout_keeps_font_split_word_together() -> None:
    joined = join_spans_with_layout(
        [
            (10.0, 0.0, 20.0, 10.0, "२०७४"),
            (19.95, 0.0, 22.0, 10.0, "/"),
            (21.95, 0.0, 40.0, 10.0, "७५"),
        ]
    )

    assert joined == "२०७४/७५"


def test_join_spans_with_layout_adds_space_for_real_visual_gap() -> None:
    joined = join_spans_with_layout(
        [
            (10.0, 0.0, 30.0, 10.0, "Mindray"),
            (32.0, 0.0, 50.0, 10.0, "BS-230"),
        ]
    )

    assert joined == "Mindray BS-230"


def test_contextual_kalimati_marker_resolves_after_two_spans_are_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDoc:
        def __getitem__(self, index: int) -> object:
            assert index == 0
            return object()

    marker = kalimati_module._PUA_CONTEXTUAL_NE
    page_dict = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {
                                "text": "गर्",
                                "font": "AAAAAA+Kalimati",
                                "bbox": (10.0, 10.0, 20.0, 20.0),
                            },
                            {
                                "text": marker,
                                "font": "AAAAAA+Kalimati",
                                "bbox": (22.0, 10.0, 28.0, 20.0),
                            },
                        ]
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        font_based_module,
        "get_cid_marked_page_dict",
        lambda _page: page_dict,
    )
    monkeypatch.setattr(
        font_based_module,
        "collect_page_repairs_by_line",
        lambda *_args, **_kwargs: {},
    )

    result = FontBasedStrategy()._extract_from_document(
        FakeDoc(),  # type: ignore[arg-type]
        {1: {"Kalimati": "broken_cmap"}},
        page_start=0,
        page_end=0,
        needs_reorder=True,
        detect_tables=False,
    )

    assert result.raw_text == "गर्ने"
    assert result.fragments[0].text == "गर्ने"
    assert marker not in result.raw_text


def test_contextual_kalimati_marker_does_not_cross_a_large_span_gap() -> None:
    marker = kalimati_module._PUA_CONTEXTUAL_NE

    joined = join_spans_with_layout(
        [
            (10.0, 0.0, 20.0, 10.0, "गर्"),
            (24.0, 0.0, 30.0, 10.0, marker),
        ]
    )

    assert joined == f"गर् {marker}"
    assert kalimati_module.reorder_devanagari(joined) == "गर् ने"


def test_contextual_kalimati_marker_preserves_a_non_rakar_word_boundary() -> None:
    marker = kalimati_module._PUA_CONTEXTUAL_NE

    joined = join_spans_with_layout(
        [
            (10.0, 0.0, 20.0, 10.0, "राम"),
            (21.0, 0.0, 27.0, 10.0, marker),
        ]
    )

    assert joined == f"राम {marker}"
    assert kalimati_module.reorder_devanagari(joined) == "राम ने"


def test_contextual_kalimati_marker_keeps_authored_space_inside_one_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDoc:
        def __getitem__(self, index: int) -> object:
            assert index == 0
            return object()

    marker = kalimati_module._PUA_CONTEXTUAL_NE
    page_dict = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {
                                "text": f"गर् {marker}",
                                "font": "AAAAAA+Kalimati",
                                "bbox": (10.0, 10.0, 30.0, 20.0),
                            }
                        ]
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        font_based_module,
        "get_cid_marked_page_dict",
        lambda _page: page_dict,
    )
    monkeypatch.setattr(
        font_based_module,
        "collect_page_repairs_by_line",
        lambda *_args, **_kwargs: {},
    )

    result = FontBasedStrategy()._extract_from_document(
        FakeDoc(),  # type: ignore[arg-type]
        {1: {"Kalimati": "broken_cmap"}},
        page_start=0,
        page_end=0,
        needs_reorder=True,
        detect_tables=False,
    )

    assert result.raw_text == "गर् ने"


def test_joined_reorder_keeps_a_real_space_after_virama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDoc:
        def __getitem__(self, index: int) -> object:
            assert index == 0
            return object()

    page_dict = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {
                                "text": "अर्थात्",
                                "font": "AAAAAA+Kalimati",
                                "bbox": (10.0, 10.0, 30.0, 20.0),
                            },
                            {
                                "text": "९",
                                "font": "AAAAAA+Kalimati",
                                "bbox": (32.0, 10.0, 36.0, 20.0),
                            },
                        ]
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        font_based_module,
        "get_cid_marked_page_dict",
        lambda _page: page_dict,
    )
    monkeypatch.setattr(
        font_based_module,
        "collect_page_repairs_by_line",
        lambda *_args, **_kwargs: {},
    )

    result = FontBasedStrategy()._extract_from_document(
        FakeDoc(),  # type: ignore[arg-type]
        {1: {"Kalimati": "broken_cmap"}},
        page_start=0,
        page_end=0,
        needs_reorder=True,
        detect_tables=False,
    )

    assert result.raw_text == "अर्थात् ९"


def _extract_kokila_line(
    monkeypatch: pytest.MonkeyPatch,
    texts: tuple[str, ...],
    *,
    reverse_raw_spans: bool = False,
    fonts: tuple[str, ...] | None = None,
) -> str:
    class FakeDoc:
        def __getitem__(self, index: int) -> object:
            assert index == 0
            return object()

    font_names = fonts or ("Kokila",) * len(texts)
    assert len(font_names) == len(texts)
    spans = [
        {
            "text": text,
            "font": f"AAAAAA+{font_names[index]}",
            "bbox": (
                10.0 + index * 10.5,
                10.0,
                20.0 + index * 10.5,
                20.0,
            ),
        }
        for index, text in enumerate(texts)
    ]
    if reverse_raw_spans:
        spans.reverse()
    page_dict = {
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
    monkeypatch.setattr(
        font_based_module,
        "get_cid_marked_page_dict",
        lambda _page: page_dict,
    )
    monkeypatch.setattr(
        font_based_module,
        "collect_page_repairs_by_line",
        lambda *_args, **_kwargs: {},
    )

    result = FontBasedStrategy()._extract_from_document(
        FakeDoc(),  # type: ignore[arg-type]
        {1: {font_name: "correct" for font_name in font_names}},
        page_start=0,
        page_end=0,
        needs_reorder=True,
        detect_tables=False,
    )
    return result.raw_text


def test_new_kokila_context_marker_does_not_normalize_unrelated_boundaries() -> None:
    marker = kalimati_module._PUA_KOKILA_HALF_SA
    source = f"{marker}थानीय सरकार सञ् चाल र सञ् चालन बोलपत्रर् आव्हान राख् नु"

    converted = FontBasedStrategy()._convert_span_text(
        source,
        "Kokila",
        {"Kokila": "correct"},
        needs_reorder=True,
    )

    assert converted == ("स्थानीय सरकार सञ् चाल र सञ् चालन बोलपत्रर् आव्हान राख् नु")


def test_complete_kokila_sequence_resolves_before_generic_ikar_reordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic_ikar = kalimati_module._PUA_IKAR
    generic_reph = kalimati_module._PUA_REPH
    half_sa = kalimati_module._PUA_KOKILA_HALF_SA
    source = (
        f"आ{generic_ikar}थ{generic_reph}क कारोबारको "
        f"{generic_ikar}{half_sa}थ{generic_ikar}त"
    )

    assert _extract_kokila_line(monkeypatch, (source,)) == ("आर्थिक कारोबारको स्थिति")


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (kalimati_module._PUA_KOKILA_TA, "ह", "तह"),
        (kalimati_module._PUA_KOKILA_HALF_SA, "थानीय", "स्थानीय"),
        ("त" + kalimati_module._PUA_KOKILA_HALF_THA, "य", "तथ्य"),
    ],
)
def test_kokila_context_markers_resolve_after_cross_span_assembly(
    monkeypatch: pytest.MonkeyPatch,
    left: str,
    right: str,
    expected: str,
) -> None:
    assert _extract_kokila_line(monkeypatch, (left, right)) == expected


def test_kokila_dependency_detection_uses_visual_span_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    half_tha = kalimati_module._PUA_KOKILA_HALF_THA

    assert (
        _extract_kokila_line(
            monkeypatch,
            ("त" + half_tha, "य"),
            reverse_raw_spans=True,
        )
        == "तथ्य"
    )


@pytest.mark.parametrize("split", range(1, 5))
def test_mixed_kokila_status_resolves_at_every_span_split(
    monkeypatch: pytest.MonkeyPatch,
    split: int,
) -> None:
    sequence = (
        kalimati_module._PUA_IKAR
        + kalimati_module._PUA_KOKILA_HALF_SA
        + "थ"
        + kalimati_module._PUA_IKAR
        + "त"
    )

    assert (
        _extract_kokila_line(
            monkeypatch,
            (sequence[:split], sequence[split:]),
        )
        == "स्थिति"
    )


def test_cross_span_kokila_deferral_preserves_an_authored_word_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plain space in ``कारोबारको स्थिति`` survives the deferred reorder.

    🛑 This expected `सञ्चालन` -- joined -- until review showed what that join costs. The
    deferral flag is decided once per line, so asserting the join here also asserted it for
    every markerless span on any deferred line, and `test_..._does_not_join_a_post_virama`
    below is what that looked like in shipped output. The space after `सञ्` now survives too:
    keeping a visibly split word is cheaper than joining two real ones, and no local rule
    tells the two apart.
    """

    left = "सञ् चालन कारोबारको " + kalimati_module._PUA_IKAR
    right = kalimati_module._PUA_KOKILA_HALF_SA + "थ" + kalimati_module._PUA_IKAR + "त"

    assert _extract_kokila_line(monkeypatch, (left, right)) == (
        "सञ्चालन कारोबारको स्थिति"
    )


@pytest.mark.parametrize(
    "left",
    ["छन् तथा", "सम्वत् २०७४", "एवम् अन्य", "गर्नुपर्ने छन् तथा भएका"],
)
def test_a_deferred_line_does_not_join_a_post_virama_boundary_in_another_span(
    monkeypatch: pytest.MonkeyPatch,
    left: str,
) -> None:
    """🛑 The negative control the deferral tests lacked, and the reason it was missing.

    `defer_kokila_reorder` had **zero references in tests/**. The one test whose name
    promised this property, `test_joined_reorder_keeps_a_real_space_after_virama`, is built
    without a Kokila marker on the line, so it exercised the *undeferred* path and the
    deferred path had no coverage at all.

    Each case here carries no marker of its own and is never split. The marker sits in the
    neighbouring span, which is what used to switch a post-virama deletion on for this one --
    so `छन् तथा` shipped as `छन्तथा`. The control below is the same span on an undeferred
    line: if a future change makes these two disagree again, both fail together.
    """

    right = kalimati_module._PUA_KOKILA_HALF_SA + "थ" + kalimati_module._PUA_IKAR + "त"
    deferred = _extract_kokila_line(
        monkeypatch,
        (left + " " + kalimati_module._PUA_IKAR, right),
    )

    assert deferred == left + " स्थिति"
    assert _extract_kokila_line(monkeypatch, (left,)) == left


@pytest.mark.parametrize("split", range(1, 5))
def test_pure_kokila_status_resolves_at_every_span_split(
    monkeypatch: pytest.MonkeyPatch,
    split: int,
) -> None:
    sequence = (
        kalimati_module._PUA_KOKILA_IKAR
        + kalimati_module._PUA_KOKILA_HALF_SA
        + "थ"
        + kalimati_module._PUA_KOKILA_IKAR
        + kalimati_module._PUA_KOKILA_TA
    )

    assert (
        _extract_kokila_line(
            monkeypatch,
            (sequence[:split], sequence[split:]),
        )
        == "स्थिति"
    )


@pytest.mark.parametrize("split", range(1, 5))
def test_literal_th_kokila_status_resolves_at_every_span_split(
    monkeypatch: pytest.MonkeyPatch,
    split: int,
) -> None:
    sequence = (
        kalimati_module._PUA_KOKILA_IKAR
        + "थथ"
        + kalimati_module._PUA_KOKILA_IKAR
        + kalimati_module._PUA_KOKILA_TA
    )

    assert (
        _extract_kokila_line(
            monkeypatch,
            (sequence[:split], sequence[split:]),
        )
        == "स्थिति"
    )


def test_later_literal_th_status_sequence_can_cross_a_span_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence = kalimati_module._KOKILA_LITERAL_TH_STATUS_SEQUENCE
    split = 2

    assert (
        _extract_kokila_line(
            monkeypatch,
            (sequence + " " + sequence[:split], sequence[split:]),
        )
        == "स्थिति स्थिति"
    )


@pytest.mark.parametrize(
    "spans",
    [
        (
            "स्वा"
            + kalimati_module._PUA_KOKILA_HALF_SA
            + kalimati_module._PUA_KOKILA_HALF_THA,
            "य",
        ),
        (
            "स्वा",
            kalimati_module._PUA_KOKILA_HALF_SA + kalimati_module._PUA_KOKILA_HALF_THA,
            "य",
        ),
    ],
)
def test_kokila_health_cluster_resolves_across_span_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    spans: tuple[str, ...],
) -> None:
    assert _extract_kokila_line(monkeypatch, spans) == "स्वास्थ्य"


def test_context_markers_fail_closed_when_a_line_has_no_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = (
        (kalimati_module._PUA_CONTEXTUAL_NE, "ने", "Kalimati"),
        (kalimati_module._PUA_KOKILA_IKAR, "र्", "Kokila"),
        (kalimati_module._PUA_KOKILA_TA, "ि", "Kokila"),
        (kalimati_module._PUA_KOKILA_HALF_SA, "थ", "Kokila"),
        (kalimati_module._PUA_KOKILA_HALF_THA, "्", "Kokila"),
    )

    for marker, expected, font_name in cases:
        assert (
            _extract_kokila_line(
                monkeypatch,
                (marker,),
                fonts=(font_name,),
            )
            == expected
        )


@pytest.mark.parametrize(
    ("marker", "font_name"),
    [
        (kalimati_module._PUA_CONTEXTUAL_NE, "Helvetica"),
        (kalimati_module._PUA_KOKILA_IKAR, "Helvetica"),
        (kalimati_module._PUA_KOKILA_TA, "Helvetica"),
        (kalimati_module._PUA_KOKILA_HALF_SA, "Helvetica"),
        (kalimati_module._PUA_KOKILA_HALF_THA, "Helvetica"),
        (kalimati_module._PUA_CONTEXTUAL_NE, "NotKalimati"),
        (kalimati_module._PUA_CONTEXTUAL_NE, "KalimatiExtra"),
        (kalimati_module._PUA_KOKILA_HALF_SA, "NotKokila"),
        (kalimati_module._PUA_KOKILA_HALF_SA, "KokilaExtra"),
    ],
)
def test_context_marker_from_an_unrelated_font_remains_unresolved(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    font_name: str,
) -> None:
    assert (
        _extract_kokila_line(
            monkeypatch,
            (marker,),
            fonts=(font_name,),
        )
        == marker
    )


def test_context_marker_font_family_accepts_a_style_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = kalimati_module._PUA_KOKILA_HALF_SA

    assert (
        _extract_kokila_line(
            monkeypatch,
            (marker + "थानीय",),
            fonts=("Kokila-Bold",),
        )
        == "स्थानीय"
    )


def test_context_marker_provenance_is_preserved_within_one_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = kalimati_module._PUA_KOKILA_HALF_SA

    assert (
        _extract_kokila_line(
            monkeypatch,
            (marker + "थानीय ", marker + "थापा"),
            fonts=("Kokila", "Helvetica"),
        )
        == "स्थानीय " + marker + "थापा"
    )


def test_existing_positional_marker_keeps_established_spacing_cleanup() -> None:
    """The ikar marker still lands correctly when the span also holds a virama space.

    ⚠️ The expectation differs from the extractor line this came from, deliberately, and
    the difference is the space in ``सञ् चालन`` -- not the marker, which is this test's
    subject and resolves to ``विविध`` either way.

    That line joined a space after any virama. This tree does not: it was measured to
    join real word boundaries too (``छन् तथा``), so only the ``पुर्याउनु`` stem is
    repaired, and the join is scoped to the marker-resolution pass, where the space
    provably came from reassembling two spans rather than from the document. This call
    is a SINGLE span on the non-deferred path, so its space is authored as far as the
    extractor can tell, and is kept. ``test_cross_span_kokila_deferral_preserves_an_``
    ``authored_word_boundary`` is the cross-span case and does join it.
    """

    marker = kalimati_module._PUA_IKAR

    converted = FontBasedStrategy()._convert_span_text(
        f"सञ् चालन {marker}व{marker}वध",
        "Kokila",
        {"Kokila": "correct"},
        needs_reorder=True,
    )

    assert converted == "सञ्चालन विविध"


def test_normalize_extracted_word_keeps_spaces_between_kalimati_words() -> None:
    line = join_words_with_spacing(
        [
            normalize_extracted_word("कम\uf000चारीको"),
            normalize_extracted_word("\uf001सफा\uf001रसमा"),
        ]
    )

    assert line == "कर्मचारीको सिफारिसमा"


def test_normalize_extracted_word_keeps_space_before_prebase_marker_word() -> None:
    line = join_words_with_spacing(
        [
            normalize_extracted_word("सञ्चालक"),
            normalize_extracted_word("\uf001वशाल"),
        ]
    )

    assert line == "सञ्चालक विशाल"


@pytest.mark.parametrize(
    "marker",
    [
        "\ufffd",
        # Marked CIDs, not raw ones: extraction marks unmappable glyphs, so the
        # normalizer matches the marker range instead of enumerating code points.
        # 0x83 is the law-report sample's bullet; 0x7a is that same sample's other
        # unmappable glyph, an ASCII "z" no literal class would ever have covered.
        mark_unmappable_cids("\x83"),
        mark_unmappable_cids("z"),
        # VOL-704. The bullets `pua_maps` resolves symbol fonts to, plus a raw
        # private-use glyph that reached here unmapped (an unregistered symbol
        # font, or a codepoint deliberately left in `KNOWN_UNMAPPABLE`). 2,227 of
        # the CIAA corpus's 4,210 U+F0B7 are in this leading position, i.e. they
        # are list markers, and before this class covered them the corpus had
        # ZERO markdown list items where it should have had thousands.
        "•",
        "▪",
        "➢",
        "",
    ],
    ids=[
        "replacement-char",
        "marked-cid-0x83",
        "marked-cid-0x7a",
        "symbol-bullet-u2022",
        "wingdings-square-u25aa",
        "wingdings-arrowhead-u27a2",
        "unmapped-pua-uf0b7",
    ],
)
def test_normalize_press_release_paragraph_turns_leading_unknown_glyph_into_bullet(
    marker: str,
) -> None:
    assert (
        normalize_press_release_paragraph(f"{marker} अपराध गर्ने व्यक्तिको पीडितसंगको")
        == "- अपराध गर्ने व्यक्तिको पीडितसंगको"
    )


def test_choose_fragment_text_prefers_original_when_repair_introduces_noise() -> None:
    assert (
        _choose_fragment_text(
            "श्री विशेष अदालत, काठमाडौं समक्ष पेस गरेको",
            "श्री ववशेष अदालत, काठमाड� समक्ष पेस गरेको",
        )
        == "श्री विशेष अदालत, काठमाडौं समक्ष पेस गरेको"
    )


def test_choose_fragment_text_can_merge_best_tokens_from_both_candidates() -> None:
    assert (
        _choose_fragment_text(
            "मुद्दाको िेहोरा:-",
            "मु�ाको बेहोरा:-",
        )
        == "मुद्दाको बेहोरा:-"
    )


def test_choose_fragment_text_merges_around_malformed_conjunct_ra() -> None:
    assert (
        _choose_fragment_text(
            "श्री ववरण",
            "श्रर्ी विवरण",
        )
        == "श्री विवरण"
    )


def test_choose_fragment_text_prefers_valid_rakar_order_without_other_noise() -> None:
    assert _choose_fragment_text("तयार", "तर्ार") == "तयार"


def test_conjunct_ra_merge_rejects_an_unrelated_candidate_change() -> None:
    assert not _is_corroborated_conjunct_ra_repair("श्रर्ेस्ता", "श्रेथिा")
    assert (
        _merge_corroborated_conjunct_ra_tokens("श्रर्ेस्ता सफा", "श्रेथिा बिग्रिएको")
        == "श्रर्ेस्ता सफा"
    )


def test_table_variant_matching_ignores_unstable_global_table_indices() -> None:
    regions = [
        TableRegion(2, 10.0, 20.0, 300.0, 700.0, start_row=0),
        TableRegion(3, 10.0, 30.0, 300.0, 710.0, start_row=2),
    ]
    damaged = Table(
        row_count=4,
        col_count=2,
        cells=[TableCell(row=0, col=0, text="श्रर्ी सफा")],
        index=9,
        regions=regions,
    )
    candidate = Table(
        row_count=4,
        col_count=2,
        cells=[TableCell(row=0, col=0, text="श्री सफा")],
        index=4,
        regions=[
            TableRegion(2, 10.0, 20.0, 300.0, 700.0, start_row=0),
            TableRegion(3, 10.0, 30.0, 300.0, 710.0, start_row=2),
        ],
    )

    merged = _merge_malformed_table_variants([damaged], [candidate])

    assert merged[0].index == 9
    assert merged[0].regions == regions
    assert merged[0].cells[0].text == "श्री सफा"


def test_caption_only_malformed_table_is_detected_and_repaired() -> None:
    region = TableRegion(2, 10.0, 20.0, 300.0, 700.0, start_row=0)
    damaged = Table(
        row_count=2,
        col_count=2,
        cells=[TableCell(row=0, col=0, text="सफा")],
        caption="श्रर्ी विवरण",
        regions=[region],
    )
    candidate = replace(damaged, caption="श्री विवरण")

    assert _tables_contain_malformed_conjunct_ra([damaged])

    merged = _merge_malformed_table_variants([damaged], [candidate])

    assert merged[0].caption == "श्री विवरण"
    assert merged[0].regions == [region]


def test_table_text_repairs_preserve_primary_continuation_geometry() -> None:
    def page_table(page_number: int, data: str) -> Table:
        return Table(
            row_count=2,
            col_count=2,
            cells=[
                TableCell(row=0, col=0, text="शीर्षक"),
                TableCell(row=0, col=1, text="रकम"),
                TableCell(row=1, col=0, text=data),
                TableCell(row=1, col=1, text="१"),
            ],
            caption="श्रर्ी विवरण",
            regions=[
                TableRegion(
                    page_number,
                    40.0,
                    60.0 if page_number == 1 else 20.0,
                    550.0,
                    800.0 if page_number == 1 else 500.0,
                    page_height=842.0,
                )
            ],
        )

    first = page_table(1, "पहिलो")
    second = page_table(2, "दोस्रो")
    repaired_first = replace(first, caption="श्री विवरण")

    merged = _merge_malformed_table_variants(
        [first, second],
        [repaired_first],
    )

    assert len(merged) == 1
    assert merged[0].caption == "श्री विवरण"
    assert merged[0].row_count == 3
    assert [region.start_row for region in merged[0].regions] == [0, 2]
    assert [cell.text for cell in merged[0].cells if cell.col == 0] == [
        "शीर्षक",
        "पहिलो",
        "दोस्रो",
    ]


def test_table_text_repairs_preserve_primary_repeated_header_drop() -> None:
    def page_table(page_number: int, data: str) -> Table:
        return Table(
            row_count=2,
            col_count=2,
            cells=[
                TableCell(row=0, col=0, text="श्रर्ी"),
                TableCell(row=0, col=1, text="रकम"),
                TableCell(row=1, col=0, text=data),
                TableCell(row=1, col=1, text="१"),
            ],
            regions=[
                TableRegion(
                    page_number,
                    40.0,
                    60.0 if page_number == 1 else 20.0,
                    550.0,
                    800.0 if page_number == 1 else 500.0,
                    page_height=842.0,
                )
            ],
        )

    first = page_table(1, "पहिलो")
    second = page_table(2, "दोस्रो")
    repaired_first = replace(
        first,
        cells=[
            replace(first.cells[0], text="श्री"),
            *first.cells[1:],
        ],
    )

    merged = _merge_malformed_table_variants(
        [first, second],
        [repaired_first],
    )

    assert len(merged) == 1
    assert merged[0].row_count == 3
    assert [region.start_row for region in merged[0].regions] == [0, 2]
    assert sum(cell.text == "श्रर्ी" for cell in merged[0].cells) == 0
    assert sum(cell.text == "श्री" for cell in merged[0].cells) == 1


# --- legacy-font "invalid sign" garble (the appended clean+garble artifact) ---

# Real Nepali text and its legacy-font mis-map twin (carrying the invalid signs
# ॊ U+094A / ऩ U+0929 / ॉ U+0949 that a Preeti-as-WinAnsi read produces).
_CLEAN_LINE = "तथा विभिन्न संस्था र सहकारी संस्थाहरूमा"
_GARBLED_LINE = "तथा विख्िम सॊस्था य सहकायी सॊस्थाहरुभा"


def test_text_quality_penalty_flags_invalid_devanagari_signs() -> None:
    # The garbled twin must score a higher penalty than the clean line so the
    # variant-merge prefers clean text.
    assert _text_quality_penalty(_GARBLED_LINE) > _text_quality_penalty(_CLEAN_LINE)
    assert _text_quality_penalty(_CLEAN_LINE) == 0


def test_has_severe_noise_detects_invalid_signs() -> None:
    assert _has_severe_noise(_GARBLED_LINE)
    assert not _has_severe_noise(_CLEAN_LINE)


# --- VOL-135: the doubled-consonant signal must survive morphology ----------------
# Every word below was drawn from the corpus-wide adjudication in
# `oag-corpus/runs/vol135/`, which scored all 34,684 doublet-bearing words in
# `markdown-quality-v11` against the corpus's own 1.87M-word vocabulary. The counts
# in the comments are that sweep's, so a regression here is a measurable one.

#: Correct Nepali that the bare `([क-ह])\1` pattern charged. Together these 377-word
#: class carried 56.8% of all 1,087,029 corpus hits.
_DOUBLED_BUT_CORRECT = (
    "महालेखापरीक्षकको",  # 59,388 — the corpus publisher's own name, क + को
    "क्रममा",  # 47,002 — क्रम + मा locative
    "कार्यक्रममा",  # 39,236
    "कक्षा",  # 29,302 — lexical
    "व्ययको",  # 26,921 — व्यय = expenditure
    "अध्ययन",  # 24,451
    "व्यय",  # 20,958
    "काममा",  # 19,497
    "नाममा",  # 19,023
    "मितव्ययी",  # 18,448
    "दररेट",  # 18,147
    "नियमितता",  # 12,483 — त + ता abstract noun
    "आश्वस्तता",  # 11,119
    "त्यससँग",  # 8,259 — स + सँग
    "जोखिममा",  # 6,553
    "तहहरुले",  # 4,567 — ह + हरु plural
    "ललितपुर",
    "तत्काल",
    "बबरमहल",  # Babarmahal, a Kathmandu locality
    "उत्खनन",
)

#: Genuine legacy-decode damage, dominated by i-matra loss resurfacing as a doubled
#: consonant. The clean form's corpus attestation count follows each entry.
_DOUBLED_AND_DAMAGED = (
    "खररद",  # -> खरिद, attested 538,011x
    "ववरण",  # -> विवरण, 381,067x
    "आन्तररक",  # -> आन्तरिक, 211,003x
    "वववरण",  # -> विवरण, 381,067x
    "गररएको",  # -> गरिएको, 111,892x
    "ननयम",  # -> नियम, 219,606x
    "देखख",  # -> देखि, 113,739x
    "दाखखला",  # -> दाखिला, 157,909x — i-matra SURVIVED beside the doubled consonant
    "ववकास",  # -> विकास, 184,268x
    "शशर्त",  # -> शर्त, 13,147x
    "कको",  # -> को, 818,214x — a suffix with no stem in front of it
    "ममा",  # -> मा, 673,181x
    "वडडाँडा",  # -> वडाँडा (VOL-135 names this one)
    "घद्दद्दछ",  # pure legacy-map soup (VOL-135 names द्दद्दण्)
)


def test_duplicate_consonant_does_not_charge_correct_morphology() -> None:
    # 4 of every 5 hits of the bare pattern were on correct Nepali. Charging these
    # spends the accept gate's `penalty_per_deva <= 0.05` budget on nothing.
    for word in _DOUBLED_BUT_CORRECT:
        assert _duplicate_consonant_count(word) == 0, word
        assert _text_quality_penalty(word) == 0, word


def test_duplicate_consonant_still_charges_legacy_decode_damage() -> None:
    # The pattern is NOT droppable: >=209,998 corpus occurrences are real damage.
    for word in _DOUBLED_AND_DAMAGED:
        assert _duplicate_consonant_count(word) > 0, word
        assert _text_quality_penalty(word) > 0, word


def test_duplicate_consonant_count_matches_the_pattern_when_nothing_is_excused() -> (
    None
):
    # The word-scoped loop must not change WHICH doublets exist, only which are
    # charged: a doublet is two Devanagari characters, so it can never straddle a
    # word boundary. Guards against the loop silently dropping or double-counting.
    text = " ".join(_DOUBLED_AND_DAMAGED)
    assert _duplicate_consonant_count(text) == len(
        _DUPLICATE_CONSONANT_PATTERN.findall(text)
    )


def test_duplicate_consonant_suffix_needs_a_real_stem() -> None:
    # A suffix only excuses the doublet when a plausible stem precedes it. `कको`
    # and `ममा` are a suffix with a single bare consonant in front, which is damage,
    # not morphology — and is why the stem-length test exists.
    assert _duplicate_consonant_count("महालेखापरीक्षकको") == 0
    assert _duplicate_consonant_count("कको") == 1
    assert _duplicate_consonant_count("क्रममा") == 0
    assert _duplicate_consonant_count("ममा") == 1


def test_is_garbled_orphan_only_fires_on_garble() -> None:
    assert _is_garbled_orphan(_GARBLED_LINE)
    # A real legacy-only orphan line (two short-O signs) from a CIAA verdict PDF.
    assert _is_garbled_orphan("तथा वििीम सॊस्था य सहकायी सॊस्थाहरुफाट")
    assert not _is_garbled_orphan(_CLEAN_LINE)
    assert not _is_garbled_orphan("अख्तियार दुरुपयोग अनुसन्धान आयोग")
    assert not _is_garbled_orphan("Kathmandu, June 20")  # latin is not garble
    assert _is_garbled_orphan("   ")  # empty/whitespace orphan


def test_candra_o_loanwords_are_not_treated_as_garble() -> None:
    # candra-O (U+0949 ॉ) is valid in Nepali/Hindi loanwords and must NOT be
    # flagged — otherwise clean text like "डॉलर"/"कॉल" would be penalised/dropped.
    for word in ("डॉलर", "कॉल", "डॉक्टर", "कॉलेज"):
        assert _text_quality_penalty(word) == 0, word
        assert not _has_severe_noise(word), word
        assert not _is_garbled_orphan(word), word
    # A clean sentence carrying a loanword is still clean.
    assert not _is_garbled_orphan("निजले एक करोड डॉलर बराबरको सम्पत्ति आर्जन गरे")


def test_choose_fragment_text_prefers_clean_over_invalid_sign_garble() -> None:
    # Same line, clean vs garbled twin — whichever side it arrives on, the
    # chosen text must be free of the invalid-sign garble (no ॊ/ऩ/ॉ leaking
    # through the token-wise merge).
    for chosen in (
        _choose_fragment_text(_GARBLED_LINE, _CLEAN_LINE),
        _choose_fragment_text(_CLEAN_LINE, _GARBLED_LINE),
    ):
        assert not _has_severe_noise(chosen)
        assert not any(sign in chosen for sign in "ॊॉऩऱऴ")


def test_merge_fragment_variants_drops_unpaired_garbled_fragment() -> None:
    # A clean fragment paired across both variants, plus an original-only
    # garbled fragment (no repaired counterpart) on its own line — the classic
    # "clean line + appended garble tail" source. The garbled orphan is dropped;
    # the clean fragment survives.
    clean = TextFragment(_CLEAN_LINE, 1, 45.0, 100.0, 400.0, 120.0, 0, 0)
    garbled_orphan = TextFragment(_GARBLED_LINE, 1, 45.0, 122.0, 400.0, 142.0, 0, 1)

    merged = _merge_fragment_variants([clean, garbled_orphan], [clean])
    texts = [fragment.text for fragment in merged]

    assert _CLEAN_LINE in texts
    assert _GARBLED_LINE not in texts


def test_merge_fragment_variants_keeps_clean_unpaired_fragment() -> None:
    # An original-only fragment that is CLEAN must never be dropped.
    clean = TextFragment(_CLEAN_LINE, 1, 45.0, 100.0, 400.0, 120.0, 0, 0)
    clean_orphan = TextFragment(
        "अख्तियार दुरुपयोग अनुसन्धान आयोग", 1, 45.0, 122.0, 400.0, 142.0, 0, 1
    )

    merged = _merge_fragment_variants([clean, clean_orphan], [clean])
    texts = [fragment.text for fragment in merged]

    assert clean_orphan.text in texts


def test_marked_cids_restore_the_damage_signal_raw_cids_erase() -> None:
    # A raw CID is invisible to every garble heuristic: the values PyMuPDF emits
    # (0x4a9, 0xd6b, 0xed3, 0x1233 here) are ordinary letters in other scripts,
    # not U+FFFD and not private-use. Marking them puts them back in range.
    doc = fitz.open(stream=_build_zeroed_tounicode_pdf(), filetype="pdf")
    try:
        page_dict = get_cid_marked_page_dict(doc[0])
    finally:
        doc.close()

    text = "".join(
        span["text"]
        for block in page_dict["blocks"]
        if "lines" in block
        for line in block["lines"]
        for span in line["spans"]
    ).strip()

    assert count_marked_cids(text) == 4
    assert len(set(text)) == 4, "distinct glyphs must stay distinct"
    assert "�" not in text
    assert _text_quality_penalty(text) == 4 * 12
    assert _has_severe_noise(text)
    assert _is_garbled_orphan(text)


def test_cid_marking_leaves_a_clean_page_untouched() -> None:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "काठमाडौं", fontname="helv")
        page_dict = get_cid_marked_page_dict(page)
    finally:
        doc.close()

    text = "".join(
        span["text"]
        for block in page_dict["blocks"]
        if "lines" in block
        for line in block["lines"]
        for span in line["spans"]
    )

    assert count_marked_cids(text) == 0


def test_strip_marked_cids_renders_them_visible() -> None:
    marked = mark_unmappable_cids("z")

    assert strip_marked_cids(f"a{marked}b") == "a�b"
    assert strip_marked_cids(f"a{marked}b", " ") == "a b"


def _raw_span(glyphs: list[tuple[str, tuple[float, float, float, float]]]) -> dict:
    return {
        "font": "Lohit-Devanagari",
        "size": 11.0,
        "bbox": glyphs[0][1],
        "chars": [{"c": char, "bbox": bbox} for char, bbox in glyphs],
    }


def _raw_page(spans: list[list[tuple[str, tuple[float, float, float, float]]]]) -> dict:
    return {"blocks": [{"lines": [{"spans": [_raw_span(g) for g in spans]}]}]}


class _StubPage:
    """A page whose two extractions group the same glyphs into different spans."""

    def __init__(self, plain: dict, cid: dict) -> None:
        self._plain = plain
        self._cid = cid

    def get_text(self, mode: str, flags: int = 0) -> dict:
        import copy

        assert mode == "rawdict"
        source = (
            self._cid if flags & fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE else self._plain
        )
        return copy.deepcopy(source)


def _page_text(page_dict: dict) -> str:
    return "".join(span["text"] for span in _iter_dict_spans(page_dict))


def test_marking_survives_span_regrouping() -> None:
    # The regression this fixes: dropping the CID flag regroups spans, so the two
    # extractions disagree on span count while every glyph keeps its coordinates.
    # Pairing on spans gave up here and returned raw CIDs, invisible to every
    # garble heuristic; pairing on glyph boxes marks them.
    a, b, c = (0.0, 0.0, 5.0, 10.0), (5.0, 0.0, 10.0, 10.0), (10.0, 0.0, 15.0, 10.0)
    plain = _raw_page([[("क", a), ("�", b), ("य", c)]])
    cid = _raw_page([[("क", a)], [("à", b), ("य", c)]])

    marked = get_cid_marked_page_dict(_StubPage(plain, cid))
    text = _page_text(marked)

    assert count_marked_cids(text) == 1
    assert "�" not in text
    assert strip_marked_cids(text) == "क�य"


def test_marking_attributes_only_the_unmappable_glyph() -> None:
    a, b = (0.0, 0.0, 5.0, 10.0), (5.0, 0.0, 10.0, 10.0)
    plain = _raw_page([[("क", a), ("�", b)]])
    cid = _raw_page([[("क", a), ("à", b)]])

    text = _page_text(get_cid_marked_page_dict(_StubPage(plain, cid)))

    # The mappable glyph must survive untouched, not be marked alongside.
    assert text[0] == "क"
    assert count_marked_cids(text) == 1


def test_ambiguous_position_keeps_its_raw_cid() -> None:
    # The same box decodes to real text in one place and U+FFFD in another, so it
    # cannot be attributed to either and must not be guessed at.
    shared = (0.0, 0.0, 5.0, 10.0)
    plain = _raw_page([[("�", shared)], [("क", shared)]])
    cid = _raw_page([[("à", shared)], [("क", shared)]])

    text = _page_text(get_cid_marked_page_dict(_StubPage(plain, cid)))

    assert count_marked_cids(text) == 0
    assert "à" in text


def test_a_page_with_nothing_unmappable_is_extracted_once() -> None:
    calls: list[int] = []
    clean = _raw_page([[("क", (0.0, 0.0, 5.0, 10.0))]])

    class _CountingPage(_StubPage):
        def get_text(self, mode: str, flags: int = 0) -> dict:
            calls.append(flags)
            return super().get_text(mode, flags)

    page = _CountingPage(clean, _raw_page([[("x", (0.0, 0.0, 5.0, 10.0))]]))
    text = _page_text(get_cid_marked_page_dict(page))

    assert len(calls) == 1, "a clean page must not pay for a second extraction"
    assert text == "क"


def test_char_position_rounds_to_hundredths() -> None:
    # The two extractions agree on geometry to well within a hundredth of a
    # point, but not always bit-for-bit, so the key must tolerate that.
    assert _char_position({"bbox": (1.0004, 2.0, 3.0, 4.0)}) == _char_position(
        {"bbox": (1.0, 2.0, 3.0, 4.0)}
    )
    assert _char_position({"bbox": (1.02, 2.0, 3.0, 4.0)}) != _char_position(
        {"bbox": (1.0, 2.0, 3.0, 4.0)}
    )


def test_replacement_and_decoded_positions_split_by_glyph() -> None:
    a, b = (0.0, 0.0, 5.0, 10.0), (5.0, 0.0, 10.0, 10.0)
    replacement, decoded = _replacement_and_decoded_positions(
        _raw_page([[("�", a), ("क", b)]])
    )

    assert replacement == {_char_position({"bbox": a})}
    assert decoded == {_char_position({"bbox": b})}


def test_to_dict_shape_matches_dict_mode_exactly() -> None:
    # Callers consume `span["text"]`; they must not be able to tell that the page
    # was extracted in rawdict mode.
    doc = fitz.open(stream=_build_zeroed_tounicode_pdf(), filetype="pdf")
    try:
        page = doc[0]
        expected = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        converted = _to_dict_shape(
            page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        )
    finally:
        doc.close()

    assert [span["text"] for span in _iter_dict_spans(converted)] == [
        span["text"] for span in _iter_dict_spans(expected)
    ]
    assert all("chars" not in span for span in _iter_dict_spans(converted))


def test_text_dict_flags_replace_the_default_and_must_not_be_made_additive() -> None:
    """The two `flags=` words REPLACE PyMuPDF's default, deliberately.

    Passing `flags=` to `get_text` replaces the default word rather than adding to
    it, which is a real and recorded trap -- but here the natural remediation, OR-ing
    the default back in, is destructive twice over. Both harms are measured over all
    6,236 documents of the Nepali audit corpus (VOL-239, from VOL-225's run
    `5dcf2ca9`), on PyMuPDF 1.27.2 where `TEXTFLAGS_RAWDICT` is 199:

    * 199 **already sets** `TEXT_USE_CID_FOR_UNKNOWN_UNICODE`. An additive plain pass
      therefore returns no U+FFFD at all, `get_cid_marked_page_dict` finds no
      `replacement`, it returns the plain dict on every page, and all **22,871,324**
      markings stop -- no exception, no warning, no gate. The plain pass works
      *because* `flags=` drops bit 128 for it.
    * 199 also sets `TEXT_MEDIABOX_CLIP`, which is not a no-op on this corpus: it
      deletes **1,250,148** glyphs across **4,022 of 6,236** documents (107,902
      pages), and the dropped glyphs are fully inside the mediabox, cropbox and
      rect alike. On one 16-page bulletin it removes 60.6% of the text.

    The two words are already correct *relative to each other*: they differ by
    exactly bit 128, which is the contrast the CID pass exists to draw.
    """
    plain = fitz.TEXT_PRESERVE_WHITESPACE
    cid = font_based_module._TEXT_DICT_FLAGS

    assert cid == fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
    assert cid ^ plain == fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE

    # Why additive silently disables the plain pass's detection.
    assert fitz.TEXTFLAGS_RAWDICT & fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE

    # Neither word may clip to the mediabox.
    assert not plain & fitz.TEXT_MEDIABOX_CLIP
    assert not cid & fitz.TEXT_MEDIABOX_CLIP


def test_additive_plain_pass_would_stop_every_cid_marking() -> None:
    # The behavioural half of the guard above: pinning the constants alone would
    # still let someone move both words together. This shows the consequence.
    doc = fitz.open(stream=_build_zeroed_tounicode_pdf(), filetype="pdf")
    try:
        page = doc[0]
        shipped = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        additive = page.get_text(
            "rawdict", flags=fitz.TEXTFLAGS_RAWDICT | fitz.TEXT_PRESERVE_WHITESPACE
        )
        page_dict = get_cid_marked_page_dict(page)
    finally:
        doc.close()

    shipped_replacement, _ = _replacement_and_decoded_positions(shipped)
    additive_replacement, _ = _replacement_and_decoded_positions(additive)

    assert shipped_replacement, "the plain pass must see the unmappable glyphs"
    assert not additive_replacement, (
        "the additive word already carries bit 128, so it decodes the unmappable "
        "glyphs to raw CIDs and no U+FFFD survives for the plain pass to detect"
    )

    # And the marking that detection drives is real work, so losing it is a loss.
    text = "".join(
        span["text"]
        for block in page_dict["blocks"]
        if "lines" in block
        for line in block["lines"]
        for span in line["spans"]
    ).strip()
    assert count_marked_cids(text) == 4


def test_a_lexeme_does_not_excuse_unrelated_damage_in_the_same_word() -> None:
    """The exemption is scoped to the lexeme's SPAN, not to the whole word.

    Devanagari compounds carry no internal space, so a garbled token can hold a listed
    lexeme and unrelated damage at once. Excusing the whole word hid the damage:
    "कक्षा" scores 0 and "गग" scores 1, but concatenated they scored 0 before this
    was scoped. Under-charging matters as much as over-charging, because this term is
    one of the inputs deciding which legacy map wins.
    """

    from likhit.extractors.font_based import (
        _DOUBLED_CONSONANT_LEXEMES,
        _duplicate_consonant_count,
    )

    lexeme = _DOUBLED_CONSONANT_LEXEMES[1]
    damage = "गग"

    # The premise: each part scores what it should on its own, or this proves nothing.
    assert _duplicate_consonant_count(lexeme) == 0
    assert _duplicate_consonant_count(damage) == 1

    assert _duplicate_consonant_count(lexeme + damage) == 1
    # And the lexeme is still excused when it is the only thing there.
    assert _duplicate_consonant_count(lexeme + lexeme) == 0


def test_unmark_cids_is_the_inverse_of_marking() -> None:
    """Distinct from `strip_marked_cids`, which replaces a mark with U+FFFD.

    This recovers the ORIGINAL character, which is what a predicate reading a span's
    content needs: a marked glyph still carries its identity, it is just offset.
    """

    from likhit.extractors.font_based import mark_unmappable_cids, unmark_cids

    for probe in ("The Auditor General", "क्र.सं.", "", "a1 -_(", "x" * 200):
        assert unmark_cids(mark_unmappable_cids(probe)) == probe, probe
        # and a no-op on text that was never marked
        assert unmark_cids(probe) == probe, probe


def test_the_latin_veto_is_not_blind_to_cid_marked_text() -> None:
    """The veto must certify marked English, or it fails in the case it most matters.

    A marked CID is `chr(_CID_MARK_BASE + ord(char))`, so `isascii()` is False and the
    veto's token pattern matches nothing. The predicate therefore answered False for a
    span of plain English purely because its glyphs had failed to decode -- and a marked
    span of genuine Latin is exactly what would otherwise be remapped into well-formed
    Devanagari that spells nothing, with no U+FFFD left for any gate to notice.

    Both arms are asserted, because a fix that made everything read as Latin would pass
    the marked arm alone.
    """

    from likhit.extractors.font_based import (
        _reads_as_latin_words,
        mark_unmappable_cids,
    )

    english = "The Office of the Auditor General reviewed the accounts"
    assert _reads_as_latin_words(english)
    assert _reads_as_latin_words(mark_unmappable_cids(english))

    # The negative arm: legacy keystrokes must still NOT read as Latin, marked or not.
    keystrokes = "kl/R5]b ;'\\][ cAdM"
    assert not _reads_as_latin_words(keystrokes)
    assert not _reads_as_latin_words(mark_unmappable_cids(keystrokes))


# --- a hermetic lexicon for the Latin cid recovery tests ------------------------
#
# The recovery reads an external English word list, defaulting to hunspell's
# /usr/share/hunspell/en_US.dic. That is a HOST artifact: CI installs no system
# packages, so without this fixture five tests below fail there while passing on a
# developer machine that happens to have hunspell. Measured -- pointing the override at
# a nonexistent path fails exactly those five.
#
# The word list is the vocabulary those tests actually feed through the recovery, written
# out explicitly rather than derived, so it is auditable and cannot silently grow.
#
# The two nonsense tokens `qxzjvk` and `wgtplm` are DELIBERATELY ABSENT: one test asserts
# they are declined as not-English, and adding them would make that test pass for the
# wrong reason.
_TEST_LEXICON_WORDS = (
    "a",
    "accountability",
    "based",
    "pilot",
    "version",
    "accounts",
    "and",
    "auditing",
    "auditor",
    "be",
    "course",
    "credible",
    "development",
    "for",
    "general",
    "implementation",
    "in",
    "institution",
    "management",
    "measurement",
    "of",
    "office",
    "on",
    "performance",
    "preparedness",
    "professional",
    "promoting",
    "report",
    "reviewed",
    # "sai" is DELIBERATELY ABSENT, matching hunspell, which does not contain it. Adding
    # it lifts test_latin_cid_recovery_keeps_a_span_a_coverage_rule_would_drop from
    # coverage 0.425 to exactly 0.500 and breaks its `coverage < 0.5` assertion -- that
    # test's whole point is a span the coverage rule would drop, so inflating coverage
    # destroys it. The SAI-titled test does not need it: performance/measurement/report
    # already give it three hits.
    "strive",
    "sustainable",
    "the",
    "to",
    "we",
    # These two are not decoration. hunspell's en_US really does contain them, and they
    # are the "two dictionary words that fall out by chance" from the Preeti keystroke
    # span in test_latin_cid_recovery_refuses_preeti_read_as_ascii -- `fsf` twice and
    # `vt` once, giving that span 2 hits. That test's PREMISE is that Preeti garble
    # scores as English, so without them the premise fails and the test proves nothing
    # about the gate it exists for. Measured against the host dictionary rather than
    # guessed, and naming them makes the coincidence auditable instead of depending on
    # whatever hunspell version a machine happens to ship.
    "fsf",
    "vt",
)


@pytest.fixture(autouse=True)
def _hermetic_latin_lexicon(tmp_path_factory, monkeypatch):
    """Point the recovery at a word list this repo owns, not at the host's hunspell.

    Autouse so no test has to remember it. The one test that asserts the recovery FAILS
    CLOSED without a lexicon sets its own nonexistent path with monkeypatch, which runs
    after this and therefore still wins.

    ⚠️ **This list is the tests' OWN vocabulary, not a hunspell subset**, and the notes
    above that justify individual entries by hunspell fidelity invite the opposite
    reading. Measured against what `_latin_cid_lexicon` parses out of a host dictionary:
    `a`, `accounts`, `auditing`, `promoting` and `reviewed` are ABSENT from it, because
    the loader keeps only the base form before `/` -- so no affixed forms -- and (before
    that filter was removed as dead) dropped entries under 2 characters. The entries are
    here because these tests need them, and that is the whole of their warrant.
    """

    path = tmp_path_factory.mktemp("lexicon") / "test_en.dic"
    # hunspell's first line is an entry count, which the loader skips.
    path.write_text(
        f"{len(_TEST_LEXICON_WORDS)}\n" + "\n".join(_TEST_LEXICON_WORDS) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(_CID_RECOVERY_LEXICON_ENV, str(path))
    _latin_cid_lexicon.cache_clear()
    yield
    _latin_cid_lexicon.cache_clear()


# --- Latin cid uniform-offset recovery -------------------------------------
#
# Every cid sequence below is a real one, taken from the four corpus documents
# that carry this defect (VOL-160 measured the class; VOL-170 fixes it). At k=0
# the cid IS the ASCII code, so `_cids("text")` reproduces what the PDF holds.


def _cids(text: str, offset: int = 0) -> list[int]:
    return [ord(char) - offset for char in text]


def test_latin_cid_recovery_reads_back_the_reports_own_title() -> None:
    # 11392's title block, the founding case, at the standard-glyph-order offset.
    title = "SAI Performance Measurement Report"
    assert recover_latin_cid_text(_cids(title, 29), "TimesNewRomanPS-BoldMT") == title


def test_latin_cid_recovery_takes_the_identity_offset() -> None:
    # k=0 is the modal case: 136 of VOL-160's 146 confident recoveries.
    for text, font in (
        (
            "Auditing Preparedness for the Implementation of the Sustainable ",
            "TimesNewRomanPSMT",
        ),
        (
            "We strive to be a credible institution in promoting accountability",
            "TimesNewRomanPSMT,Italic",
        ),
        ("Professional Course on Management and Development", "TimesNewRomanPSMT"),
    ):
        assert recover_latin_cid_text(_cids(text), font) == text


def test_latin_cid_recovery_keeps_a_span_a_coverage_rule_would_drop() -> None:
    # cov=0.425 -- acronyms, digits and punctuation are not dictionary characters.
    # The `hits >= 2` leg is the only reason this founding block survives, so a
    # coverage-only acceptance rule cannot see one of the defect's own examples.
    text = "(based on INTOSAI SAI-PMF Pilot Version, 2013)"
    hits, coverage = _latin_cid_score(text)
    assert hits >= _CID_RECOVERY_MIN_HITS
    assert coverage < _CID_RECOVERY_MIN_COV_ONE_HIT
    assert recover_latin_cid_text(_cids(text, 29), "TimesNewRomanPS-BoldMT") == text


def test_latin_cid_recovery_refuses_preeti_read_as_ascii() -> None:
    # THE regression this gate exists for. Preeti keystroke bytes ARE ASCII, so
    # this real span from an audit bulletin scores as English (two dictionary
    # words fall out by chance) and would ship as fabricated Latin text.
    garble = "n]fk/LIf0fsf]k|tj]gdf pNn]vt Joxf]fsf "
    hits, _coverage = _latin_cid_score(garble)
    assert hits >= _CID_RECOVERY_MIN_HITS, "premise: this scores as English"
    assert recover_latin_cid_text(_cids(garble), "Preeti") is None
    assert recover_latin_cid_text(_cids(garble), "ABCDEF+Preeti") is None


def test_two_admissible_offsets_that_both_accept_are_a_decline(
    tmp_path, monkeypatch
) -> None:
    """🛑 The offset tie-break, which had zero coverage and pointed the wrong way.

    The arg-max used a strict `>` over `(hits, coverage)`, so on a tie the FIRST entry of
    `_CID_RECOVERY_OFFSETS` won -- and that tuple is ordered `(0, 29)`. Where the true
    encoding is k=29, the standard glyph-order subset this feature exists to read,
    first-wins emitted a different, confidently-wrong word.

    Ambiguity is now a decline, which leaves the run marked exactly as it was.

    ⚠️ Constructing the case needs a synthetic lexicon, and the reason is the structural
    fact that makes it rare in practice: k=29's space glyph is cid 3, which fails k=0's
    `>= 0x20` range test, so any run containing a SPACE is admissible at one offset only.
    A single word can be admissible at both -- and then only the one-hit + coverage leg
    can accept it, since one token cannot give two hits. Measured over 331 real
    recoveries: 0 had more than one accepted offset.
    """

    # `report` at k=29; the same cids read at k=0 spell `UHSRUW`.
    both = ("report", "uhsruw")
    path = tmp_path / "ambiguous.dic"
    path.write_text("\n".join(both) + "\n", encoding="utf-8")
    monkeypatch.setenv(_CID_RECOVERY_LEXICON_ENV, str(path))
    _latin_cid_lexicon.cache_clear()

    cids = _cids("report", 29)
    assert min(cids) >= 0x20, "premise: k=0 must also be admissible"
    assert "".join(chr(c) for c in cids) == "UHSRUW"

    # Both readings clear the acceptance rule on their own...
    for reading in ("report", "UHSRUW"):
        hits, coverage = _latin_cid_score(reading)
        assert hits >= 1 and coverage >= _CID_RECOVERY_MIN_COV_ONE_HIT, reading

    # ...so the decode is ambiguous and must be declined rather than guessed.
    assert recover_latin_cid_text(cids, "TimesNewRomanPSMT") is None

    # The control: with only the true reading in the lexicon it recovers as before.
    path.write_text("report\n", encoding="utf-8")
    _latin_cid_lexicon.cache_clear()
    assert recover_latin_cid_text(cids, "TimesNewRomanPSMT") == "report"


def test_the_two_hit_floor_is_asserted_not_read_from_the_constant() -> None:
    """🛑 `_CID_RECOVERY_MIN_HITS` had no behavioural coverage.

    Mutating it from 2 to 1 left the whole suite green except the value pin, because the
    two tests that look like they cover it assert `hits >= _CID_RECOVERY_MIN_HITS` --
    which holds at any value. That is the exact anti-pattern
    `tests/test_tuning_constants.py`'s own docstring names: a test that reads the
    constant to build its own expectation holds at any value.

    A LITERAL expectation instead: one hit at coverage under the one-hit leg's floor must
    be declined.
    """

    text = "report qxzjvk wgtplm"
    hits, coverage = _latin_cid_score(text)
    assert hits == 1, hits
    assert coverage < _CID_RECOVERY_MIN_COV_ONE_HIT, coverage

    assert recover_latin_cid_text(_cids(text), "TimesNewRomanPSMT") is None


def test_a_symbol_family_is_not_a_latin_cid_font() -> None:
    """`book` is an unanchored substring, and it admitted `Bookshelf Symbol 7`.

    Gate 1 is a POSITIVE Latin requirement whose stated purpose is to exclude fonts of
    undetermined script. A pictorial family's glyph order is not ASCII+k at all, so with
    the measured 1.5-2.1% false-recovery rate on arbitrary glyph ids its dingbats could
    be rewritten as English.

    The exclusion is a name list, not a word boundary, because `Bookman Old Style` and
    `Bookerly` are real Latin text faces that a boundary rule would also drop.
    """

    for name in (
        "Bookshelf Symbol 7",
        "SymbolMT",
        "Wingdings",
        "Wingdings-Regular",
        "Webdings",
        "ZapfDingbats",
        "ABCDEE+Bookshelf Symbol 7",
    ):
        assert is_latin_cid_font(name) is False, name

    # ...and the Latin text faces that share the substring are kept.
    for name in ("Bookman Old Style", "Bookerly", "BookAntiqua"):
        assert is_latin_cid_font(name) is True, name


@pytest.mark.parametrize(
    "font",
    ["Preeti", "Himalb", "Kantipur", "Sagarmatha", "PCS NEPALI", "FONTASY_HIMALI_TT"],
)
def test_latin_cid_font_gate_excludes_legacy_registry_fonts(font: str) -> None:
    assert is_latin_cid_font(font) is False


@pytest.mark.parametrize(
    "font",
    ["TimesPreeti", "Preeti-TimesNewRoman", "ArialHimalb", "Times New Roman Kantipur"],
)
def test_latin_cid_font_gate_excludes_a_legacy_font_named_after_a_latin_family(
    font: str,
) -> None:
    # These match the Latin family regex, so the legacy-registry check is the ONLY
    # thing keeping them out; without it the names above would be read as ASCII.
    # No such hybrid name is attested in this corpus -- this pins the gate that
    # makes the two conditions independent rather than reporting a sighting.
    assert _LATIN_CID_FONT_FAMILIES.search(font), "premise: the name looks Latin"
    assert is_latin_cid_font(font) is False


@pytest.mark.parametrize(
    "font",
    # Not in the legacy registry, so the POSITIVE Latin requirement is what stops
    # them. `Kalimati` reaches this path as broken_cmap, and the corpus' residual
    # holds fonts whose script was never determined.
    [
        "Kalimati",
        "Lohit-Devanagari",
        "SymbolMT",
        "Ganess",
        "BikuRegularThin",
        "CIDFont+F1",
        "TT3CBt00",
        "",
    ],
)
def test_latin_cid_font_gate_excludes_non_latin_and_undetermined(font: str) -> None:
    assert is_latin_cid_font(font) is False


@pytest.mark.parametrize(
    "font",
    [
        "TimesNewRomanPSMT",
        "TimesNewRomanPSMT,Bold",
        "TimesNewRomanPS-BoldMT",
        "ArialMT",
        "Calibri-Bold",
        "ABCDEF+Garamond-Bold",
        "AcuminVariableConcept",
    ],
)
def test_latin_cid_font_gate_admits_latin_families(font: str) -> None:
    assert is_latin_cid_font(font) is True


def test_latin_cid_recovery_declines_offsets_it_does_not_try() -> None:
    # AcuminVariableConcept reads 99.7% per-font offset-coherent at k=74 and
    # decodes to `RQPONMPLKJIPOHGFEKEDEK...`. Only k=0 and k=29 are tried, so a
    # run that needs any other offset is declined rather than invented.
    text = "Auditing Preparedness for the Implementation"
    assert recover_latin_cid_text(_cids(text, 74), "AcuminVariableConcept") is None
    assert recover_latin_cid_text(_cids(text, 1), "TimesNewRomanPSMT") is None


def test_latin_cid_recovery_declines_glyphs_outside_printable_ascii() -> None:
    # Devanagari glyph ids sit far above the printable band at both offsets, so
    # the range test alone keeps the transform off them.
    assert (
        recover_latin_cid_text([0x4A9, 0xD6B, 0xED3, 0x1233], "TimesNewRomanPSMT")
        is None
    )


def test_latin_cid_recovery_declines_text_that_is_not_english() -> None:
    assert recover_latin_cid_text(_cids("qxzjvk wgtplm"), "TimesNewRomanPSMT") is None
    assert recover_latin_cid_text([], "TimesNewRomanPSMT") is None


def test_latin_cid_recovery_needs_a_lexicon_and_fails_closed(monkeypatch) -> None:
    _latin_cid_lexicon.cache_clear()
    monkeypatch.setenv(_CID_RECOVERY_LEXICON_ENV, "/nonexistent/en_US.dic")
    try:
        assert _latin_cid_lexicon() == frozenset()
        title = "SAI Performance Measurement Report"
        assert (
            recover_latin_cid_text(_cids(title, 29), "TimesNewRomanPS-BoldMT") is None
        )
    finally:
        _latin_cid_lexicon.cache_clear()


def _span(text: str, unmappable_text: str, font: str) -> tuple[dict, set]:
    """A span dict shaped like rawdict's, with `unmappable_text`'s glyphs flagged."""
    chars = [
        {"c": char, "bbox": (float(index), 0.0, float(index) + 1.0, 1.0)}
        for index, char in enumerate(text)
    ]
    start = text.index(unmappable_text)
    unmappable = {
        _char_position(char) for char in chars[start : start + len(unmappable_text)]
    }
    return {"font": font, "chars": chars}, unmappable


def test_unmappable_runs_split_on_a_decoded_glyph() -> None:
    span, unmappable = _span("abXcd", "ab", "TimesNewRomanPSMT")
    unmappable |= {_char_position(span["chars"][3]), _char_position(span["chars"][4])}
    runs = _unmappable_runs(span, unmappable)
    assert ["".join(char["c"] for char in run) for run in runs] == ["ab", "cd"]


def test_recover_or_mark_span_emits_recovered_text_not_marks() -> None:
    title = "SAI Performance Measurement Report"
    encoded = "".join(chr(ord(char) - 29) for char in title)
    span, unmappable = _span(encoded, encoded, "TimesNewRomanPS-BoldMT")
    _recover_or_mark_unmappable_span(span, unmappable)
    recovered = "".join(char["c"] for char in span["chars"])
    assert recovered == title
    assert count_marked_cids(recovered) == 0


def test_a_multi_code_point_glyph_is_marked_and_does_not_raise() -> None:
    """🛑 The one-code-point-per-glyph guard is untested, and it is not a no-op.

    Its comment says a multi-character glyph is "left to the marking path rather than
    guessed at". Removing the guard leaves the whole suite green -- and it does not turn
    a recovery into a wrong recovery, it turns a DECLINE into a CRASH: `ord()` raises
    `TypeError` on a two-code-point ligature glyph, so extraction of any document
    carrying one would fail outright.

    A ligature is an ordinary thing for a Latin font to hold, so this is the shape that
    reaches it.
    """

    span, unmappable = _span("abc", "abc", "TimesNewRomanPSMT")
    # A ligature glyph: one glyph, two code points. `fi` is the canonical one.
    span["chars"][1]["c"] = "fi"

    _recover_or_mark_unmappable_span(span, unmappable)

    emitted = "".join(char["c"] for char in span["chars"])
    # Marked, not recovered, and above all not raised on.
    assert count_marked_cids(emitted) > 0
    assert emitted == mark_unmappable_cids("a") + mark_unmappable_cids(
        "fi"
    ) + mark_unmappable_cids("c")


def test_recover_or_mark_span_marks_what_it_cannot_read() -> None:
    # Identical bytes, legacy font: the marking behaviour must be exactly what it
    # was before recovery existed.
    title = "SAI Performance Measurement Report"
    encoded = "".join(chr(ord(char) - 29) for char in title)
    span, unmappable = _span(encoded, encoded, "Preeti")
    _recover_or_mark_unmappable_span(span, unmappable)
    marked = "".join(char["c"] for char in span["chars"])
    assert marked == mark_unmappable_cids(encoded)
    assert count_marked_cids(marked) == len(encoded)


# ---------------------------------------------------------------------------
# VOL-515: the RUN-SCOPED ASCII-bracketed marker key.
#
# VOL-166's gate anchors on a WHOLE SPAN being "(12)" and is therefore blind to
# the same construct glued inside a clause -- "दफा ७४ढ२ण् अनुसार" for "section
# 74(2)". That second population is 106 of the 127 corruptions v14 ships, and it
# is a different sub-defect rather than an incomplete fix (VOL-505).
#
# The unit here is the LINE, not the span: "a maximal run of consecutive spans
# within one line, regardless of font" has no partitioning predicate left once the
# font `while` in `_content_legacy_veto_flags` is dropped. That is what buys the
# straddles -- the construct is confined to one span at only 36 of 145 corpus
# sites, and at the other 109 it spans up to three (VOL-606 item A).
#
# The tests below pin the four census classes, both digit families, both arms of
# the cross-map carve-out, and the two shapes that must NOT fire. Every case is a
# shape measured in the OAG corpus, cited by document and page, not invented.
# ---------------------------------------------------------------------------

_HIMALI_SPAN_FONT = "AAAAAA+FontAsyHimali"
_PREETI_SPAN_FONT = "BBBBBB+Preeti"
# `Lohit-Devanagari` is the real unmapped font the digits of a straddling run sit
# in; it is the middle span of the 3-span shape (VOL-603).
_UNMAPPED_SPAN_FONT = "CCCCCC+Lohit-Devanagari"


def _marker_span(text: str, font: str, x0: float = 0.0) -> dict:
    return {"text": text, "font": font, "bbox": (x0, 0.0, x0 + 10.0, 10.0)}


def _marker_maps(**by_font: str) -> dict[str, font_based_module.LegacyMapChoice]:
    return {
        font: font_based_module.LegacyMapChoice(
            map_key=map_key, validity=None, ambiguous=frozenset()
        )
        for font, map_key in by_font.items()
    }


def _exemptions(spans, maps):
    veto = font_based_module._content_legacy_veto_flags(spans, maps or None)
    return (
        font_based_module._ascii_bracketed_run_exemptions(
            spans, maps or None, veto, {}
        ),
        veto,
    )


def _convert_line(spans, maps) -> str:
    """The line as the pipeline ships it, with the key active."""

    exemptions, veto = _exemptions(spans, maps)
    strategy = FontBasedStrategy()
    return "".join(
        strategy._convert_span_text(
            str(span["text"]),
            str(span["font"]),
            {},
            False,
            content_legacy_maps=maps or None,
            skip_content_legacy=veto[index],
            exempt_slices=exemptions[index],
        )
        for index, span in enumerate(spans)
    )


def _convert_line_without_key(spans, maps) -> str:
    """The same line with no exemption at all -- what ships today."""

    _, veto = _exemptions(spans, maps)
    strategy = FontBasedStrategy()
    return "".join(
        strategy._convert_span_text(
            str(span["text"]),
            str(span["font"]),
            {},
            False,
            content_legacy_maps=maps or None,
            skip_content_legacy=veto[index],
        )
        for index, span in enumerate(spans)
    )


@pytest.mark.parametrize(
    ("label", "spans", "maps", "ships", "expected"),
    [
        # 4452 p12, 5309 p13, 4817 p16, 5021 p12: the statute subsection, one span.
        # This is the class VOL-166's whole-span anchor cannot reach.
        (
            "intra_span_mapped -- statute subsection inside a clause",
            [_marker_span("dfa 74(2) anusar", _HIMALI_SPAN_FONT)],
            {"font": _HIMALI_SPAN_FONT, "map_key": "FONTASY_HIMALI_TT"},
            "माब ७४ढ२ण् बलगकबच",
            "माब ७४(२) बलगकबच",
        ),
        # 4810 p14, 4817 p11, 5022 p11, 5023 p10: three spans, digits in a
        # separate UNMAPPED font. 107 of the 145 corpus sites are this shape.
        (
            "straddle_some_mapped -- digits in an unmapped font",
            [
                _marker_span("(", _HIMALI_SPAN_FONT, 0),
                _marker_span("14", _UNMAPPED_SPAN_FONT, 10),
                _marker_span(")", _HIMALI_SPAN_FONT, 20),
            ],
            {"font": _HIMALI_SPAN_FONT, "map_key": "FONTASY_HIMALI_TT"},
            "ढ14ण्",
            "(१४)",
        ),
        # Both parens mapped, run split across two mapped spans of the same map.
        (
            "straddle_all_mapped -- two mapped spans, one map",
            [
                _marker_span("(48", _HIMALI_SPAN_FONT, 0),
                _marker_span("01)", _HIMALI_SPAN_FONT, 10),
            ],
            {"font": _HIMALI_SPAN_FONT, "map_key": "FONTASY_HIMALI_TT"},
            "ढ४८०१ण्",
            "(४८०१)",
        ),
        # 6795 p12: exactly ONE paren is in a mapped span, so this ships as a
        # MISMATCHED PAIR that the published `ढ[०-९]+ण्` signature cannot see by
        # construction -- it needs both ends. 19 sites / 12 documents that no
        # count on VOL-515 carried before VOL-606.
        (
            "mismatched pair -- open paren mapped, close literal",
            [
                _marker_span("(", _HIMALI_SPAN_FONT, 0),
                _marker_span("५४५०)", _UNMAPPED_SPAN_FONT, 10),
            ],
            {"font": _HIMALI_SPAN_FONT, "map_key": "FONTASY_HIMALI_TT"},
            "ढ५४५०)",
            "(५४५०)",
        ),
        # 4452 p7: MIXED digit families inside one run. The digits that are
        # already Devanagari are left alone and the ASCII ones are translated, so
        # the whole run lands as one well-formed number.
        #
        # The span layout is the measured one and it is load-bearing: the ASCII
        # digits sit in the UNMAPPED span, which is why they ship as ASCII at all.
        # Put them in the Himali-mapped span instead and the base leg already
        # reads "(१५९५८८६५ण्", because that map Devanagari-izes ASCII digits 10 of
        # 10 -- the same map algebra that makes the pass-through effect clause
        # wrong (VOL-606 item A3).
        (
            "mixed digit families in one run",
            [
                _marker_span("(१५958865", _UNMAPPED_SPAN_FONT, 0),
                _marker_span(")", _HIMALI_SPAN_FONT, 10),
            ],
            {"font": _HIMALI_SPAN_FONT, "map_key": "FONTASY_HIMALI_TT"},
            "(१५958865ण्",
            "(१५९५८८६५)",
        ),
        # 4119 p33: the Preeti half of the same defect. `(` -> `९` and `)` -> `०`,
        # so it ships as a pure digit string with NO consonant for an output
        # signature to anchor on -- which is why the key has to be source-keyed.
        (
            "Preeti family -- ships as a pure digit string, no signature",
            [
                _marker_span("(", _PREETI_SPAN_FONT, 0),
                _marker_span("७", _UNMAPPED_SPAN_FONT, 10),
                _marker_span(")", _PREETI_SPAN_FONT, 20),
            ],
            {"font": _PREETI_SPAN_FONT, "map_key": "Preeti"},
            "९७०",
            "(७)",
        ),
    ],
)
def test_run_scoped_marker_key_repairs_the_embedded_construct(
    label, spans, maps, ships, expected
) -> None:
    del label
    assert (
        _convert_line_without_key(
            spans, _marker_maps(**{maps["font"]: maps["map_key"]})
        )
        == ships
    )
    assert (
        _convert_line(spans, _marker_maps(**{maps["font"]: maps["map_key"]}))
        == expected
    )


def test_run_scoped_key_is_byte_identical_to_the_vol166_gate_on_its_own_shape() -> None:
    """The layering decision, asserted rather than asserted-in-prose.

    VOL-166's whole-span gate stays in place, and this rule layers on it. Where
    both can fire the output must be the same byte string, because both write
    literal parens and translate the digits with the SAME table
    (`devanagarize_ascii_digits`). The digit translate is idempotent, so a double
    application cannot corrupt.
    """

    spans = [_marker_span("(2)", _HIMALI_SPAN_FONT)]
    maps = _marker_maps(**{_HIMALI_SPAN_FONT: "FONTASY_HIMALI_TT"})
    # VOL-166's gate alone already repairs this shape...
    assert _convert_line_without_key(spans, maps) == "(२)"
    # ...and the run-scoped key writes the identical bytes.
    assert _convert_line(spans, maps) == "(२)"


def test_run_scoped_key_does_not_fire_across_two_distinct_legacy_maps() -> None:
    """The carve-out. 11105 p117: parens `Preeti`, ASCII digits `FONTASY_HIMALI_TT`.

    Within ONE map an exempted ASCII-digit run always images as something
    malformed: the Preeti family sends `(`->`९`, `)`->`०` and ASCII digits to
    CONSONANTS (0 of 10 reach a Devanagari digit), and the Himali family sends
    `(`->`ढ`, `)`->`ण्` and ASCII digits to Devanagari digits (10 of 10). So the
    only combination that images as a well-formed pure Devanagari number is
    Preeti-family parens around Himali-family digits -- and under the more
    plausible provenance reading each keystroke there is correct in its own font,
    making `९४८०१०` a genuine number that firing would destroy.

    That is a property of the six maps rather than of this corpus, so the carve-out
    closes the hole BY CONSTRUCTION and survives a change of corpus. Price: exactly
    2 sites of 145 (VOL-606 item A3).
    """

    spans = [
        _marker_span("(", _PREETI_SPAN_FONT, 0),
        _marker_span("4801", _HIMALI_SPAN_FONT, 10),
        _marker_span(")", _PREETI_SPAN_FONT, 20),
    ]
    maps = _marker_maps(
        **{_PREETI_SPAN_FONT: "Preeti", _HIMALI_SPAN_FONT: "FONTASY_HIMALI_TT"}
    )
    exemptions, _ = _exemptions(spans, maps)
    assert exemptions == [(), (), ()]
    assert _convert_line(spans, maps) == _convert_line_without_key(spans, maps)


def test_carve_out_does_not_swallow_the_ordinary_straddle() -> None:
    """The negative control on the carve-out, and it is the one that matters.

    An unmapped span does NOT count toward the distinct-map total, so the ordinary
    straddle -- one mapped map plus an unmapped `Lohit-Devanagari` digit span --
    must still fire. Without this the carve-out would silently eat the 107
    straddles the key exists to reach.
    """

    spans = [
        _marker_span("(", _PREETI_SPAN_FONT, 0),
        _marker_span("14", _UNMAPPED_SPAN_FONT, 10),
        _marker_span(")", _PREETI_SPAN_FONT, 20),
    ]
    maps = _marker_maps(**{_PREETI_SPAN_FONT: "Preeti"})
    assert _convert_line_without_key(spans, maps) == "९14०"
    assert _convert_line(spans, maps) == "(१४)"


@pytest.mark.parametrize(
    ("label", "spans", "maps"),
    [
        # Nothing is legacy-mapped, so nothing is being corrupted.
        (
            "no span mapped",
            [_marker_span("(2)", _UNMAPPED_SPAN_FONT)],
            {},
        ),
        # STRICT ADJACENCY. 3 corpus sites are missed for a single interior
        # U+0020 ("( ३१२१६६)", "(१४ )", "(८ )"). Tolerating it buys +3 of 148 and
        # requires a fresh collateral pass over a NEW firing set, so it is a
        # separately priced call (VOL-515 item 6) and must not creep in here.
        (
            "interior whitespace",
            [_marker_span("(१४ )", _HIMALI_SPAN_FONT)],
            {_HIMALI_SPAN_FONT: "FONTASY_HIMALI_TT"},
        ),
        (
            "empty parens",
            [_marker_span("()", _HIMALI_SPAN_FONT)],
            {_HIMALI_SPAN_FONT: "FONTASY_HIMALI_TT"},
        ),
        # The discriminator's whole point: a genuine `ढ` in a real Nepali word
        # carries a literal ASCII "(" in its source bytes, because the "(" key IS
        # the `ढ` key in this layout. It must survive, and it does, because the key
        # requires the whole contiguous "(" + digits + ")" run. "k(f" decodes with
        # the paren in the middle of a syllable and no closing paren at all.
        (
            "genuine dha in a real word -- no closing paren",
            [_marker_span("k(f", _HIMALI_SPAN_FONT)],
            {_HIMALI_SPAN_FONT: "FONTASY_HIMALI_TT"},
        ),
        # A bracketed run whose interior is not digits at all: a genuine
        # Preeti-typed number presents as "(!@#)", whose inner characters are
        # outside the key's digit class.
        (
            "bracketed non-digits -- a genuine Preeti-typed number",
            [_marker_span("(!@#)", _PREETI_SPAN_FONT)],
            {_PREETI_SPAN_FONT: "Preeti"},
        ),
    ],
)
def test_run_scoped_key_declines_shapes_it_must_not_touch(label, spans, maps) -> None:
    del label
    choices = _marker_maps(**maps)
    exemptions, _ = _exemptions(spans, choices)
    assert exemptions == [()] * len(spans)
    # And the shipped output is untouched, which is the claim that matters.
    assert _convert_line(spans, choices) == _convert_line_without_key(spans, choices)


def test_run_scoped_key_exempts_the_run_and_never_the_line() -> None:
    """Two runs on one line both fire, and the Nepali between them is untouched.

    The unit being the line is what finds the runs; what gets exempted is each
    matched RUN. `_convert_span_exempting` splices per span, and the splice is
    exact because the digit table leaves parens alone and is length-preserving.
    """

    spans = [_marker_span("dfa 74(2) nirm 7(11) ma", _HIMALI_SPAN_FONT)]
    maps = _marker_maps(**{_HIMALI_SPAN_FONT: "FONTASY_HIMALI_TT"})
    exemptions, _ = _exemptions(spans, maps)
    assert exemptions == [((6, 9), (16, 20))]
    shipped = _convert_line(spans, maps)
    assert shipped == "माब ७४(२) mलष्च ७(११) mब"
    # The text outside the two runs is byte-identical to what ships today.
    today = _convert_line_without_key(spans, maps)
    assert today == "माब ७४ढ२ण् mलष्च ७ढ११ण् mब"
    assert shipped.replace("(२)", "ढ२ण्").replace("(११)", "ढ११ण्") == today


def test_devanagarize_ascii_digits_is_shared_with_the_vol166_gate() -> None:
    """The effect must TRANSLATE the digits, not pass them through.

    `PCS NEPALI` and `FONTASY_HIMALI_TT` map all ten ASCII digits onto Devanagari
    digits (10 of 10, re-derived from the maps in VOL-606's DIGITTABLE), so an
    exempted run that merely passed its bytes through unmapped would emit "(123)"
    where the pipeline already emits "(१२३)" -- 49 of 145 sites regressed, 18 of
    them repairs VOL-166's gate already makes. One table, one behaviour.
    """

    from likhit.extractors.legacy_maps import (
        _LATIN_TO_DEVANAGARI_DIGITS,
        devanagarize_ascii_digits,
    )

    assert devanagarize_ascii_digits("0123456789") == "०१२३४५६७८९"
    # Idempotent, which is what makes layering on VOL-166's gate safe.
    assert devanagarize_ascii_digits("०१२३४५६७८९") == "०१२३४५६७८९"
    # Parens are not in the table, so they pass through literally and the splice
    # stays length-preserving.
    assert devanagarize_ascii_digits("(48)") == "(४८)"
    assert "0123456789".translate(_LATIN_TO_DEVANAGARI_DIGITS) == "०१२३४५६७८९"


# --- the exemption splice must forward every decision the span still needs ------
#
# 🛑 Not in VOL-515 as written, because VOL-323 (#93) landed after it and ships ON by
# default. `_convert_span_exempting` re-enters `_convert_span_text` for each segment
# OUTSIDE an exempt slice, so any keyword it fails to forward is silently decided
# wrong for those segments only -- the exempt slice itself still looks right, which is
# what makes it hard to see.
#
# Measured before the fix: `(12) 345` on a digit-companion face shipped `(१२) 345`.
# The marker is correct, the loose figures are not.
def test_the_exemption_splice_keeps_the_digit_companion_on_the_rest_of_the_span():
    from likhit.extractors.font_based import FontBasedStrategy

    strategy = FontBasedStrategy()
    companion = frozenset({"CIDFont+F9"})
    strategies = {"CIDFont+F9": "correct"}

    # Control: with no exemption the whole span transliterates.
    assert (
        strategy._convert_span_text(
            "(12) 345",
            "CIDFont+F9",
            strategies,
            False,
            digit_companion_fonts=companion,
        )
        == "(१२) ३४५"
    )

    # And with one, the segments outside it must reach the same decision. `(१२) 345`
    # is the regression this pins: the exempt slice devanagarizes itself either way.
    assert (
        strategy._convert_span_text(
            "(12) 345",
            "CIDFont+F9",
            strategies,
            False,
            digit_companion_fonts=companion,
            exempt_slices=((0, 4),),
        )
        == "(१२) ३४५"
    )


# 🛑 Also not in VOL-515 as written: VOL-159 (#98) landed after it and moved this
# module's content-legacy veto call from the span's raw text to `unmark_cid_ascii(text)`,
# which under the splice is the SEGMENT. Both changes touch the one line, so rebasing
# VOL-515 onto it is a genuine three-way conflict rather than a textual one -- and
# taking VOL-159's side wholesale silently re-scopes the veto from the span to the
# segment.
#
# `_LATIN_VETO_MIN_SHARE` is a SHARE of a span's multi-letter tokens, and this module's
# own `_reads_as_latin_words` docstring records why that makes a segment unsafe to ask:
# "a short span with one accidental hit can clear 0.10 on arithmetic alone". The span
# below is that arithmetic at the real corpus instance -- `can` as the land grade अबल,
# the documented worst case -- with the keystrokes of the OAG's own motto around it:
#
#   whole span   11 multi-letter tokens, 1 hit  -> share 0.091 -> veto DECLINES, decodes
#   leading seg   3 multi-letter tokens, 1 hit  -> share 0.333 -> veto FIRES, kept raw
#
# So deciding on the segment ships `hglxtsf nflu can 74(२)` where `जनहितका लागि अबल
# ७४(२)` belongs: 19 characters of raw keystrokes, and only on the lines this key
# fires on, i.e. the marker repair would itself introduce the corruption.
def test_the_exemption_splice_decides_the_latin_veto_on_the_span_not_the_segment():
    text = "hglxtsf nflu can 74(2) hjfkmb]lxtf kf/bzL{tf ljifo JolQmut cWoIf"
    spans = [_marker_span(text, _HIMALI_SPAN_FONT)]
    maps = _marker_maps(**{_HIMALI_SPAN_FONT: "FONTASY_HIMALI_TT"})

    # The share arithmetic this rests on, asserted rather than assumed -- if the word
    # list or the floor moves, this test must be re-derived, not silently weakened.
    assert not font_based_module._reads_as_latin_words(text)
    assert font_based_module._reads_as_latin_words("hglxtsf nflu can 74")

    exemptions, _ = _exemptions(spans, maps)
    assert exemptions == [((19, 22),)]
    assert text[19:22] == "(2)"

    # Every keystroke outside the run still decodes; only `ढ२ण्` -> `(२)` moves.
    assert _convert_line(spans, maps) == (
        "जनहितका लागि अबल ७४(२) जवाफदेहिता पारदर्शीता विषय व्यक्तिगत अध्यक्ष"
    )
    assert _convert_line_without_key(spans, maps) == (
        "जनहितका लागि अबल ७४ढ२ण् जवाफदेहिता पारदर्शीता विषय व्यक्तिगत अध्यक्ष"
    )

    lexeme = _DOUBLED_CONSONANT_LEXEMES[1]
    damage = "गग"

    # The premise: each part scores what it should on its own, or this proves nothing.
    assert _duplicate_consonant_count(lexeme) == 0
    assert _duplicate_consonant_count(damage) == 1

    assert _duplicate_consonant_count(lexeme + damage) == 1
    # And the lexeme is still excused when it is the only thing there.
    assert _duplicate_consonant_count(lexeme + lexeme) == 0


def test_unlift_and_unmark_commute_because_their_ranges_are_disjoint() -> None:
    """Both legacy decode paths apply BOTH transforms, so the order must not matter.

    `_convert_span_text` composes them as `unlift_symbol_pua(unmark_cid_ascii(text))`
    on the content branch and on the name branch, and a comment there asserts the
    composition order is not load-bearing. That is a claim about behaviour, so it is
    checked here rather than left in prose.

    Why it holds: the two transforms read DISJOINT input ranges and neither writes
    into the other's. CID marking lives at `_CID_MARK_BASE + cid` = U+F0020-U+F007E
    (plane 15); the symbol-cmap lift lives at U+F020-U+F0FF (BMP). The magnitudes are
    a factor of 16 apart and easy to misread as the same range -- `0xF020` against
    `0xF0020` -- which is exactly why this is asserted and not eyeballed. Both
    transforms also emit only characters at or below U+00FF, i.e. below both input
    ranges, so neither can feed the other.
    """

    lo, hi = SYMBOL_PUA_RANGE
    marked_lo, marked_hi = _CID_MARK_BASE + 0x20, _CID_MARK_BASE + 0x7E
    # The premise, asserted so a future move of either constant fails here rather
    # than silently making the composition order matter.
    assert hi < marked_lo or marked_hi < lo, (
        "ranges overlap: order becomes load-bearing"
    )
    assert SYMBOL_PUA_LIFT == 0xF000

    # A span carrying BOTH shapes at once: a lifted keystroke run and marked CIDs.
    lifted = "".join(chr(SYMBOL_PUA_LIFT + ord(char)) for char in "kflnsf")
    marked = "".join(chr(_CID_MARK_BASE + ord(char)) for char in "kl/R5]b")
    span = f"{lifted} {marked}"

    forward = unlift_symbol_pua(unmark_cid_ascii(span))
    reverse = unmark_cid_ascii(unlift_symbol_pua(span))
    assert forward == reverse == "kflnsf kl/R5]b"

    # And each transform really is a no-op on the other's input, which is the property
    # the commutation rests on -- without these two the equality above could hold
    # because both transforms did nothing at all.
    assert unmark_cid_ascii(lifted) == lifted
    assert unlift_symbol_pua(marked) == marked


def _unrepairable_named_font_doc(
    trace: list[list[dict[str, object]]],
    font_name: str = "ABCDEF+Kalimati",
    font_type: str = "Type0",
) -> object:
    """A PDF whose only candidate face is a named one nothing can repair.

    `trace` supplies one `get_texttrace()` result per page, so a test states how
    much of the document the unrepairable face actually draws. Two pages cannot
    be measured, and they are distinct cases: `None` models a page with no
    `get_texttrace` at all, and `"raise"` a page whose trace fails when called.
    """

    class UntraceablePage:
        """A page with no `get_texttrace` AT ALL.

        Not one that returns an empty trace -- that is a measurement, and means
        "draws no glyphs". This models the absence of one, so the test exercises
        the missing-attribute path rather than an exception raised inside it.
        """

        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(11, "ttf", font_type, font_name, "Identity-H")]

    class FakePage(UntraceablePage):
        def __init__(self, spans: list[dict[str, object]]) -> None:
            self._spans = spans

        def get_texttrace(self) -> list[dict[str, object]]:
            return self._spans

    class FailingTracePage(UntraceablePage):
        """A page that HAS `get_texttrace` and whose trace fails when called.

        MuPDF raises on a malformed content stream, which is a different path
        from a page that never offered a trace.
        """

        def get_texttrace(self) -> list[dict[str, object]]:
            raise RuntimeError("cannot interpret contents")

    class FakeDoc:
        page_count = len(trace)

        def __getitem__(self, index: int) -> UntraceablePage:
            spans = trace[index]
            if spans is None:
                return UntraceablePage()
            if spans == "raise":
                return FailingTracePage()
            return FakePage(spans)

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            del compressed
            assert xref == 11
            return "<< /ToUnicode 12 0 R >>"

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            assert xref == 12
            return True

    return FakeDoc()


def _traced_span(font: str, glyphs: int) -> dict[str, object]:
    return {"font": font, "chars": tuple(range(glyphs))}


@pytest.fixture
def _nothing_repairs_the_named_font(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        kalimati_module, "_parse_tounicode_cmap", lambda cmap_bytes: {1: "authored"}
    )
    monkeypatch.setattr(
        kalimati_module, "_get_font_correction_map", lambda doc, xref: {}
    )
    monkeypatch.setattr(
        kalimati_module, "_collect_trace_fallback_map", lambda doc, name: {}
    )


def test_an_incidental_unrepairable_named_font_does_not_lose_the_document(
    _nothing_repairs_the_named_font: None,
) -> None:
    """OAG 11113: one Kalimati glyph must not cost 433,221 Preeti ones.

    The report is set in Preeti and merely declares a Kalimati face. Refusing it
    discarded 351,643 correctly decoded Devanagari characters -- a transcript
    that measured cleaner than the corpus median -- so the face has to draw
    enough of the page to matter before the whole document is given up.
    """

    source = _unrepairable_named_font_doc(
        [[_traced_span("GHIJKL+Preeti", 433_221), _traced_span("ABCDEF+Kalimati", 1)]]
    )

    repaired_doc, needs_reorder = kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]

    assert repaired_doc is source
    assert needs_reorder is False


def test_a_dominant_unrepairable_named_font_still_loses_the_document(
    _nothing_repairs_the_named_font: None,
) -> None:
    """The four OAG documents whose Kalimati face draws ~99% must stay refused.

    This is the case the refusal exists for, and it must survive by measuring the
    share rather than by the measurement being unavailable -- so the page here
    does supply a trace.
    """

    source = _unrepairable_named_font_doc(
        [[_traced_span("ABCDEF+Kalimati", 63_182), _traced_span("MNOPQR+Cambria", 722)]]
    )

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]


def test_a_partially_unrepairable_named_font_still_loses_the_document(
    _nothing_repairs_the_named_font: None,
) -> None:
    """The 13 mixed OAG documents: 10-21% of the glyphs is material, not incidental."""

    source = _unrepairable_named_font_doc(
        [
            [
                _traced_span("ABCDEF+Kalimati", 8_265),
                _traced_span("STUVWX+Lohit-Devanagari", 45_586),
                _traced_span("MNOPQR+LiberationSerif", 15_336),
            ]
        ]
    )

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]


def test_an_untraceable_page_keeps_the_refusal(
    _nothing_repairs_the_named_font: None,
) -> None:
    """Fail closed: a face is cleared only when proven incidental, never by default."""

    source = _unrepairable_named_font_doc([None])

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]


def test_a_page_whose_trace_raises_keeps_the_refusal(
    _nothing_repairs_the_named_font: None,
) -> None:
    """A trace that fails is an absent measurement, not a measurement of zero."""

    source = _unrepairable_named_font_doc(["raise"])

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]


def test_one_untraceable_page_among_many_keeps_the_refusal(
    _nothing_repairs_the_named_font: None,
) -> None:
    """A document is only as measurable as its least measurable page."""

    source = _unrepairable_named_font_doc(
        [
            [
                _traced_span("GHIJKL+Preeti", 100_000),
                _traced_span("ABCDEF+Kalimati", 1),
            ],
            None,
        ]
    )

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kalimati_glyphs", "other_glyphs", "refused"),
    [
        (5, 995, False),  # exactly at the floor: 0.5% is not above it
        (6, 994, True),  # the first share above the floor refuses
        (1, 999_999, False),
    ],
)
def test_the_refusal_floor_is_a_share_of_the_drawn_glyphs(
    _nothing_repairs_the_named_font: None,
    kalimati_glyphs: int,
    other_glyphs: int,
    refused: bool,
) -> None:
    source = _unrepairable_named_font_doc(
        [
            [
                _traced_span("ABCDEF+Kalimati", kalimati_glyphs),
                _traced_span("GHIJKL+Preeti", other_glyphs),
            ]
        ]
    )

    if refused:
        with pytest.raises(ExtractionError, match="Unable to repair named"):
            kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]
    else:
        repaired_doc, _ = kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]
        assert repaired_doc is source


def test_the_share_counts_glyphs_across_every_page_not_just_the_first(
    _nothing_repairs_the_named_font: None,
) -> None:
    """A face absent from page 1 and dominant on page 2 is not incidental."""

    source = _unrepairable_named_font_doc(
        [
            [_traced_span("GHIJKL+Preeti", 1_000)],
            [_traced_span("ABCDEF+Kalimati", 1_000)],
        ]
    )

    with pytest.raises(ExtractionError, match="Unable to repair named"):
        kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]


def test_a_document_that_draws_no_glyphs_is_not_refused(
    _nothing_repairs_the_named_font: None,
) -> None:
    """With nothing drawn there is no garble to suppress; `needs_ocr` owns this."""

    source = _unrepairable_named_font_doc([[]])

    repaired_doc, needs_reorder = kalimati_module.fix_kalimati_cmap(source)  # type: ignore[arg-type]

    assert repaired_doc is source
    assert needs_reorder is False
