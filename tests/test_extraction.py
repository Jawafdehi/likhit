from __future__ import annotations

from pathlib import Path
import builtins

import pytest

from likhit.errors import ExtractionError, ValidationError
from likhit.core import derive_output_name, extract, render_markdown
from likhit.extractors.base import RawDocument, TextFragment
import likhit.extractors.font_based as font_based_module
from likhit.extractors.kalimati import _get_font_correction_map
from likhit.extractors.font_based import (
    FontBasedStrategy,
    join_words_with_spacing,
    normalize_extracted_word,
    parse_page_range,
)
from likhit.handlers.ciaa_press_release import CIAAPressReleaseHandler
from likhit.models import DocumentType


ROOT = Path(__file__).resolve().parents[1]
PRESS_RELEASE = ROOT / "samples" / "pressrelease.pdf"
PRESS_RELEASE_ALT = ROOT / "samples" / "Press Release.pdf"


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
    assert "अख्तियार" not in result.sections[0].body


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
    assert "\n\nदेहाय:" not in markdown
    assert "\n\nसि.नं" not in markdown


def test_handler_merges_continuation_lines_within_a_paragraph() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
            TextFragment("पहिलो अनुच्छेदको पहिलो लाइन", 1, 45, 200, 400, 220),
            TextFragment("पहिलो अनुच्छेदको दोस्रो लाइन", 1, 45, 220, 420, 240),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert (
        result.sections[0].body == "पहिलो अनुच्छेदको पहिलो लाइन पहिलो अनुच्छेदको दोस्रो लाइन"
    )


def test_handler_starts_new_paragraph_for_indented_fragments() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
            TextFragment("पहिलो अनुच्छेदको पहिलो लाइन", 1, 45, 200, 400, 220),
            TextFragment("पहिलो अनुच्छेदको दोस्रो लाइन", 1, 45, 220, 420, 240),
            TextFragment("दोस्रो अनुच्छेदको पहिलो लाइन", 1, 81, 260, 420, 280),
            TextFragment("दोस्रो अनुच्छेदको दोस्रो लाइन", 1, 45, 280, 420, 300),
        ],
    )

    result = handler.build_result(raw_document, {})

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
    assert result.sections[0].body == "यो मुख्य भाग हो।"


def test_handler_starts_new_paragraph_for_large_line_gap_within_same_margin() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
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

    result = handler.build_result(raw_document, {})

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
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
            TextFragment("पहिलो पेजको अन्तिम लाइन", 1, 45, 700, 400, 720),
            TextFragment("दोस्रो पेजको पहिलो लाइन", 2, 45, 120, 420, 140),
        ],
    )

    result = handler.build_result(raw_document, {})

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


def test_font_based_strategy_rejects_non_pdf_input(tmp_path: Path) -> None:
    source = tmp_path / "document.docx"
    source.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(ValidationError, match="Please upload a PDF file"):
        FontBasedStrategy().extract_text(str(source))


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


def test_handler_keeps_numbered_prose_before_explicit_table_boundary() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
            TextFragment("1. पहिलो बुँदा", 1, 45, 200, 400, 220),
            TextFragment("यसको व्याख्या", 1, 45, 220, 420, 240),
            TextFragment("देहाय:", 1, 250, 260, 320, 280),
            TextFragment("सि.नं नामथर", 1, 45, 280, 420, 300),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "1. पहिलो बुँदा यसको व्याख्या" in result.sections[0].body
    assert "देहाय:" not in result.sections[0].body


def test_handler_excludes_footer_signature_from_non_tabular_body() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
            TextFragment("मुख्य अनुच्छेद", 1, 45, 200, 420, 220),
            TextFragment("प्रवक्ता", 1, 300, 500, 360, 520),
            TextFragment("राजेन्द्र कुमार पौडेल", 1, 260, 520, 420, 540),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert result.sections[0].body == "मुख्य अनुच्छेद"


def test_handler_raises_when_non_tabular_body_starts_at_boundary() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: आरोपपत्र दायर गररएको ।", 1, 180, 130, 340, 150),
            TextFragment("देहाय:", 1, 250, 200, 320, 220),
            TextFragment("सि.नं", 1, 45, 220, 120, 240),
        ],
    )

    with pytest.raises(ExtractionError, match="No non-tabular text content found"):
        handler.build_result(raw_document, {})


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
