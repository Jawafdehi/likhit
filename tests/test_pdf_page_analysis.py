from __future__ import annotations

import likhit.pdf_page_analysis as pdf_page_analysis_module
from likhit.pdf_page_analysis import PdfPageAnalysis, pdf_likely_needs_ocr


def _analysis(
    *,
    page_number: int,
    max_image_coverage: float = 0.0,
    token_count: int = 20,
    devanagari_char_count: int = 0,
    suspicious_latin_ratio: float = 0.0,
    vowel_poor_latin_ratio: float = 0.0,
) -> PdfPageAnalysis:
    return PdfPageAnalysis(
        page_number=page_number,
        image_count=1 if max_image_coverage else 0,
        max_image_coverage=max_image_coverage,
        text_length=token_count * 5,
        token_count=token_count,
        devanagari_char_count=devanagari_char_count,
        suspicious_latin_ratio=suspicious_latin_ratio,
        vowel_poor_latin_ratio=vowel_poor_latin_ratio,
    )


def test_image_dominant_page_with_empty_text_layer_triggers_ocr() -> None:
    analysis = _analysis(page_number=1, max_image_coverage=0.9, token_count=0)

    assert analysis.likely_needs_ocr is True


def test_pdf_likely_needs_ocr_uses_ceiling_half_threshold(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        pdf_page_analysis_module,
        "analyze_pdf_pages",
        lambda _source: [
            _analysis(page_number=1, max_image_coverage=0.9, token_count=0),
            _analysis(page_number=2),
            _analysis(page_number=3),
        ],
    )
    assert pdf_likely_needs_ocr("dummy.pdf") is False

    monkeypatch.setattr(
        pdf_page_analysis_module,
        "analyze_pdf_pages",
        lambda _source: [
            _analysis(page_number=1, max_image_coverage=0.9, token_count=0),
            _analysis(
                page_number=2,
                max_image_coverage=0.92,
                token_count=15,
                suspicious_latin_ratio=0.2,
                vowel_poor_latin_ratio=0.5,
            ),
            _analysis(page_number=3),
        ],
    )
    assert pdf_likely_needs_ocr("dummy.pdf") is True
