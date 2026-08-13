from __future__ import annotations

import builtins
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
    _is_garbled_orphan,
    _merge_fragment_variants,
    _text_quality_penalty,
    _duplicate_consonant_count,
    _DUPLICATE_CONSONANT_PATTERN,
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
from likhit.extractors.kalimati import (
    _get_font_correction_map,
    _get_fontfile_xref,
    _resolve_fontfile2_xref,
)
from likhit.handlers.single_column_notice import SingleColumnNoticeHandler
from likhit.models import Table


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
) -> tuple[list[tuple[object, bool]], RawDocument, Table, Table]:
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

    original_table = Table(row_count=2, col_count=2, cells=[])
    repaired_table = Table(row_count=3, col_count=2, cells=[])
    calls: list[tuple[object, bool]] = []

    monkeypatch.setattr(font_based_module.fitz, "open", lambda _: next(opened_docs))
    monkeypatch.setattr(
        font_based_module,
        "scan_pdf_fonts_by_page",
        lambda _doc: {1: {"Kalimati": "broken_cmap"}},
    )
    monkeypatch.setattr(font_based_module, "scan_ocr_pages", lambda _doc: {})
    monkeypatch.setattr(
        font_based_module,
        "detect_content_legacy_fonts",
        lambda _doc, _skip: {},
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
        calls.append((doc, detect_tables))
        if doc is original_doc:
            tables = [original_table] if detect_tables else []
        else:
            assert doc is repaired_doc
            tables = [repaired_table] if repaired_has_tables else []
        fragment = TextFragment("परीक्षण", 1, 10.0, 20.0, 70.0, 35.0)
        return RawDocument(
            paragraphs=[fragment.text],
            raw_text=fragment.text,
            fragments=[fragment],
            tables=tables,
        )

    monkeypatch.setattr(
        FontBasedStrategy,
        "_extract_from_document",
        fake_extract_from_document,
    )

    result = FontBasedStrategy().extract_text(str(source))
    return calls, result, original_table, repaired_table


def test_broken_cmap_skips_unrepaired_table_detection_when_repair_finds_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, result, _original_table, repaired_table = _run_broken_cmap_table_flow(
        monkeypatch,
        tmp_path,
        repaired_has_tables=True,
    )

    assert [detect_tables for _doc, detect_tables in calls] == [False, True]
    assert result.tables == [repaired_table]


def test_broken_cmap_recovers_unrepaired_tables_when_repair_finds_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, result, original_table, _repaired_table = _run_broken_cmap_table_flow(
        monkeypatch,
        tmp_path,
        repaired_has_tables=False,
    )

    assert [detect_tables for _doc, detect_tables in calls] == [False, True, True]
    assert calls[0][0] is calls[2][0]
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
        lambda doc, to_unicode_xref, correction_map: patched_maps.append(
            (to_unicode_xref, dict(correction_map))
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
