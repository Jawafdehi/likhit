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
    _choose_fragment_text,
    _has_severe_noise,
    _is_garbled_orphan,
    _merge_fragment_variants,
    _text_quality_penalty,
    join_spans_with_layout,
    join_words_with_spacing,
    normalize_extracted_word,
    normalize_press_release_paragraph,
    parse_page_range,
)
from likhit.extractors.kalimati import _get_font_correction_map
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
            assert mode == "dict"
            del flags
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "font": "ABCDEF+Preeti",
                                        "text": "abc",
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
        lambda _font_name: (lambda text: f"converted:{text}"),
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
            assert mode == "dict"
            del flags
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "font": "Kalimati",
                                        "text": "परीक्षण",
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

    assert (
        not worker.is_alive()
    ), "_analyze_gsub did not terminate: the ligature fixpoint is unbounded"
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


def test_normalize_press_release_paragraph_turns_leading_replacement_char_into_bullet() -> (
    None
):
    assert (
        normalize_press_release_paragraph("� अपराध गर्ने व्यक्तिको पीडितसंगको")
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
