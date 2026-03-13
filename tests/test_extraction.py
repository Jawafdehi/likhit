from __future__ import annotations

from pathlib import Path
import builtins

import pytest

from likhit.errors import ExtractionError, ValidationError
from likhit.core import derive_output_name, extract, render_markdown
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.font_classifier import classify_font
import likhit.extractors.font_based as font_based_module
from likhit.extractors.kalimati import _get_font_correction_map
from likhit.extractors.font_based import (
    FontBasedStrategy,
    join_words_with_spacing,
    normalize_extracted_word,
    parse_page_range,
)
from likhit.handlers.ciaa_press_release import CIAAPressReleaseHandler
from likhit.handlers.kanun_patrika import KanunPatrikaHandler
from likhit.models import DocumentType


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
PRESS_RELEASE_ALT = _sample_path("Press Release.pdf", "Press_Release.pdf")
KANUN_PATRIKA = _sample_path("kanunpatrika.pdf")


@pytest.mark.parametrize(
    ("sample_path", "expected_date"),
    [
        (PRESS_RELEASE, "2081-10-24"),
        (PRESS_RELEASE_ALT, "2082-01-14"),
    ],
)
def test_extract_press_release_samples(sample_path: Path, expected_date: str) -> None:
    result = extract(str(sample_path), DocumentType.CIAA_PRESS_RELEASE)

    assert result.doc_type is DocumentType.CIAA_PRESS_RELEASE
    assert result.publication_date == expected_date
    assert result.title == "आरोपपत्र दायर गररएको"
    assert result.sections
    assert result.sections[0].body


def test_render_markdown_includes_frontmatter_and_heading() -> None:
    result = extract(str(PRESS_RELEASE), "ciaa-press-release")

    markdown = render_markdown(result)

    assert markdown.startswith("---\n")
    assert "doc_type: ciaa-press-release" in markdown
    assert "publication_date: '2081-10-24'" in markdown
    assert "# आरोपपत्र दायर गररएको" in markdown


def test_render_markdown_does_not_break_mid_paragraph_for_press_release_alt() -> None:
    result = extract(str(PRESS_RELEASE_ALT), "ciaa-press-release")

    markdown = render_markdown(result)

    assert "स्पष्ट\n\nआधार" not in markdown


def test_extract_kanun_patrika_sample() -> None:
    result = extract(str(KANUN_PATRIKA), DocumentType.KANUN_PATRIKA)

    assert result.doc_type is DocumentType.KANUN_PATRIKA
    assert result.title.startswith("नेपाल कानून पत्रिका")
    assert result.sections
    assert "निर्णय नं.७९७३" in result.sections[0].body
    assert result.sections[0].body.index("सर्बोच्च अदालत विशेष इजलास") < result.sections[
        0
    ].body.index("जवर्जस्ती करणीको महलमा भएको")


def test_render_markdown_for_kanun_patrika_includes_doc_type() -> None:
    result = extract(str(KANUN_PATRIKA), DocumentType.KANUN_PATRIKA)

    markdown = render_markdown(result)

    assert "doc_type: kanun-patrika" in markdown
    assert "नेपाल कानून पत्रिका" in markdown


def test_handler_merges_continuation_lines_within_a_paragraph() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("पहिलो अनुच्छेदको पहिलो लाइन", 1, 45, 200, 400, 220),
            TextFragment("पहिलो अनुच्छेदको दोस्रो लाइन", 1, 45, 220, 420, 240),
        ],
    )

    result = handler.build_result(
        raw_document,
        {"title": "परीक्षण शीर्षक", "publication_date": "2082-01-14"},
    )

    assert (
        result.sections[0].body == "पहिलो अनुच्छेदको पहिलो लाइन पहिलो अनुच्छेदको दोस्रो लाइन"
    )


def test_handler_starts_new_paragraph_for_indented_fragments() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("पहिलो अनुच्छेदको पहिलो लाइन", 1, 45, 200, 400, 220),
            TextFragment("पहिलो अनुच्छेदको दोस्रो लाइन", 1, 45, 220, 420, 240),
            TextFragment("दोस्रो अनुच्छेदको पहिलो लाइन", 1, 81, 260, 420, 280),
            TextFragment("दोस्रो अनुच्छेदको दोस्रो लाइन", 1, 45, 280, 420, 300),
        ],
    )

    result = handler.build_result(
        raw_document,
        {"title": "परीक्षण शीर्षक", "publication_date": "2082-01-14"},
    )

    assert result.sections[0].body == (
        "पहिलो अनुच्छेदको पहिलो लाइन पहिलो अनुच्छेदको दोस्रो लाइन\n\n"
        "दोस्रो अनुच्छेदको पहिलो लाइन दोस्रो अनुच्छेदको दोस्रो लाइन"
    )


def test_handler_preserves_body_text_from_inline_subject_fragment() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment(
                "विषय: आरोपपत्र दायर गररएको । यो पहिलो अनुच्छेदको सुरुवात हो।",
                1,
                180,
                130,
                500,
                150,
            ),
            TextFragment("अर्को वाक्य यहींबाट चल्छ।", 1, 45, 150, 420, 170),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert result.title == "आरोपपत्र दायर गररएको"
    assert "यो पहिलो अनुच्छेदको सुरुवात हो। अर्को वाक्य यहींबाट चल्छ।" in result.sections[0].body


def test_handler_keeps_header_content_in_body() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("कार्यालयको शीर्षक", 1, 180, 40, 420, 60),
            TextFragment("मुख्य कार्यालय", 1, 210, 70, 390, 90),
            TextFragment("मिति: २०८२।०१।१४", 1, 220, 100, 340, 120),
            TextFragment("प्रेस विज्ञप्ति", 1, 230, 130, 360, 150),
            TextFragment("विषय: परीक्षण शीर्षक ।", 1, 160, 160, 420, 180),
            TextFragment("मुख्य विवरण", 1, 45, 220, 320, 240),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "कार्यालयको शीर्षक" in result.sections[0].body
    assert "मुख्य कार्यालय" in result.sections[0].body
    assert "मिति: २०८२।०१।१४" in result.sections[0].body
    assert "प्रेस विज्ञप्ति" in result.sections[0].body
    assert "विषय: परीक्षण शीर्षक" in result.sections[0].body
    assert "मुख्य विवरण" in result.sections[0].body


def test_kanun_patrika_handler_uses_first_non_noise_paragraph_as_title() -> None:
    handler = KanunPatrikaHandler()
    raw_document = RawDocument(
        paragraphs=["123", "c+s ^", "नेपाल कानून पत्रिका", "मुख्य विवरण"],
        raw_text="",
        fragments=[],
    )

    result = handler.build_result(raw_document, {})

    assert result.title == "नेपाल कानून पत्रिका"
    assert result.sections[0].body == "123\n\nc+s ^\n\nनेपाल कानून पत्रिका\n\nमुख्य विवरण"


def test_kanun_patrika_handler_orders_header_then_left_then_right_columns() -> None:
    handler = KanunPatrikaHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("पृष्ठ शीर्षक", 1, 250, 50, 360, 65),
            TextFragment("दायाँ १", 1, 360, 100, 450, 115),
            TextFragment("बायाँ १", 1, 100, 100, 220, 115),
            TextFragment("दायाँ २", 1, 360, 120, 450, 135),
            TextFragment("बायाँ २", 1, 100, 120, 220, 135),
            TextFragment("664", 1, 300, 630, 325, 645),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert result.title == "पृष्ठ शीर्षक"
    assert (
        result.sections[0].body
        == "पृष्ठ शीर्षक\n\nबायाँ १\nबायाँ २\n\nदायाँ १\nदायाँ २\n\n664"
    )


def test_kanun_patrika_handler_merges_same_line_fragments_and_nearby_lines() -> None:
    handler = KanunPatrikaHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("शीर्षक", 1, 250, 50, 320, 65),
            TextFragment("समेतसंग", 1, 130, 280, 175, 296),
            TextFragment("वाझिएका", 1, 189, 280, 237, 296),
            TextFragment("कानूनलाई", 1, 251, 280, 305, 296),
            TextFragment("समानताको सिद्धान्त अनुसार कानून", 1, 130, 298, 300, 314),
            TextFragment("निर्माण गर्न परमादेश समेत जारी", 1, 130, 314, 300, 330),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert (
        result.sections[0].body
        == "शीर्षक\n\nसमेतसंग वाझिएका कानूनलाई\nसमानताको सिद्धान्त अनुसार कानून\nनिर्माण गर्न परमादेश समेत जारी"
    )


def test_handler_does_not_split_subject_on_plain_periods() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: Procurement v2.0 rollout", 1, 180, 130, 420, 150),
            TextFragment("मुख्य सामग्री यहाँबाट सुरु हुन्छ।", 1, 45, 170, 420, 190),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert result.title == "Procurement v2.0 rollout"
    assert "मुख्य सामग्री यहाँबाट सुरु हुन्छ।" in result.sections[0].body


def test_handler_preserves_body_text_when_subject_body_has_no_space_after_punctuation() -> (
    None
):
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment(
                "विषय: आरोपपत्र दायर गररएको।यो मुख्य भाग हो।", 1, 180, 130, 430, 150
            ),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert result.title == "आरोपपत्र दायर गररएको"
    assert "यो मुख्य भाग हो।" in result.sections[0].body


def test_handler_starts_new_paragraph_for_large_line_gap_within_same_margin() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment(
                "पहिलो अनुच्छेदको पहिलो लाइन",
                1,
                45,
                200,
                400,
                218,
                gap_before=0.0,
            ),
            TextFragment(
                "पहिलो अनुच्छेदको दोस्रो लाइन",
                1,
                45,
                218,
                420,
                236,
                gap_before=0.0,
            ),
            TextFragment(
                "दोस्रो अनुच्छेदको पहिलो लाइन",
                1,
                45,
                252,
                420,
                270,
                gap_before=16.0,
            ),
        ],
    )

    result = handler.build_result(
        raw_document,
        {"title": "परीक्षण शीर्षक", "publication_date": "2082-01-14"},
    )

    assert result.sections[0].body == (
        "पहिलो अनुच्छेदको पहिलो लाइन पहिलो अनुच्छेदको दोस्रो लाइन\n\n"
        "दोस्रो अनुच्छेदको पहिलो लाइन"
    )


def test_handler_starts_new_paragraph_on_page_transition() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("पहिलो पेजको अन्तिम लाइन", 1, 45, 700, 400, 720),
            TextFragment("दोस्रो पेजको पहिलो लाइन", 2, 45, 120, 420, 140),
        ],
    )

    result = handler.build_result(
        raw_document,
        {"title": "परीक्षण शीर्षक", "publication_date": "2082-01-14"},
    )

    assert result.sections[0].body == "पहिलो पेजको अन्तिम लाइन\n\nदोस्रो पेजको पहिलो लाइन"


def test_handler_extracts_date_only_from_date_line() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
            TextFragment(
                "मुख्य विवरणमा २०७९।०१।२९ को अर्को मिति उल्लेख छ।", 1, 45, 200, 420, 220
            ),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert result.publication_date == "2082-01-14"


def test_derive_output_name_uses_publication_date() -> None:
    result = extract(str(PRESS_RELEASE), "ciaa-press-release")

    output_name = derive_output_name(result, str(PRESS_RELEASE), existing=set())

    assert output_name == "pressrelease-2081-10-24.md"


def test_parse_page_range_accepts_expected_formats() -> None:
    assert parse_page_range("1-2", total_pages=3) == (0, 1)
    assert parse_page_range("2", total_pages=3) == (1, 1)
    assert parse_page_range("2-9", total_pages=3) == (1, 2)


def test_parse_page_range_rejects_invalid_formats() -> None:
    with pytest.raises(ValidationError):
        parse_page_range("1-", total_pages=3)
    with pytest.raises(ValidationError):
        parse_page_range("9", total_pages=3)


def test_classify_font_detects_legacy_broken_and_correct_fonts() -> None:
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


def test_handler_keeps_table_content_after_numbered_prose() -> None:
    handler = CIAAPressReleaseHandler()
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
    handler = CIAAPressReleaseHandler()
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
    handler = CIAAPressReleaseHandler()
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
