"""Manual content check for OCR-only sample PDFs.

This check calls the configured vision provider and may incur API cost. Run it
explicitly with the same OCR environment used in production.
"""

import os

from tests.integration.test_sample_pdfs import (
    OCR_REQUIRED_SAMPLE_CASES,
    _assert_required_markers_present,
    _assert_sample_shape,
    _convert_sample,
)


def test_ocr_required_samples_retain_expected_content() -> None:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    model = (
        os.getenv("MARKITDOWN_OCR_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("GEMINI_MODEL")
    )
    assert api_key and model, (
        "configure an OCR API key and model before running "
        "tests/manual/test_ocr_samples.py"
    )

    for case in OCR_REQUIRED_SAMPLE_CASES:
        sample = _convert_sample(case)
        _assert_sample_shape(sample)
        _assert_required_markers_present(sample)
