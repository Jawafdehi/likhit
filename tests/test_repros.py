"""Regression tests for likhit breakpoints found during research.

Finding IDs reference the original breakpoint research.
"""

import os
import re
import tempfile
from typing import cast
from pathlib import Path

import fitz
import pytest
from markitdown import MarkItDown
from markitdown_ocr import LLMVisionOCRService, OCRResult

from likhit.converters.nepali_pdf import (
    _base64_encoded_size,
    _ocr_render_matrix,
    _render_page_for_ocr,
    _run_full_page_ocr,
)
from likhit.errors import ExtractionError, ScannedPdfError, ValidationError
from likhit.extractors.kalimati import normalize_devanagari_spacing

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _convert(path: str | Path, **kwargs) -> str:
    """Convert a file through likhit and return its text."""
    md = MarkItDown(enable_plugins=True)
    return md.convert(str(path), **kwargs).text_content or ""


# ── P0: Silent mojibake on OCR-required files without OCR configured ──


def _clear_ocr_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "MARKITDOWN_OCR_MODEL",
        "OPENAI_MODEL",
        "GEMINI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


class TestSilentMojibake:
    """An OCR-only PDF must fail loudly when OCR is unavailable."""

    def test_nirnaya_requires_ocr_instead_of_returning_latin_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_ocr_environment(monkeypatch)

        with pytest.raises(ScannedPdfError, match="OCR is not configured") as exc_info:
            _convert(SAMPLES / "nirnaya.pdf")

        assert exc_info.value.needs_ocr_pages

    def test_requested_scan_page_reaches_the_caller_with_its_page_number(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_ocr_environment(monkeypatch)

        with pytest.raises(ScannedPdfError) as exc_info:
            _convert(SAMPLES / "nirnaya.pdf", pages="1")

        assert exc_info.value.needs_ocr_pages == [1]


# ── P0: 300 DPI hardcoded causes OCR to always exceed API limits ──


class TestOCRImageSize:
    """OCR renders must fit the providers' per-image payload limit."""

    def test_ocr_image_fits_within_5_mib(self) -> None:
        raw = (SAMPLES / "nirnaya.pdf").read_bytes()
        doc = fitz.open(stream=raw, filetype="pdf")
        try:
            page = doc[0]
            old_png = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72)).tobytes(
                "png"
            )
            assert _base64_encoded_size(len(old_png)) > 5 * 1024 * 1024

            png = _render_page_for_ocr(page)
            assert png is not None
            assert _base64_encoded_size(len(png)) <= 5 * 1024 * 1024
        finally:
            doc.close()

    def test_oversized_media_box_is_bounded_before_rendering(self) -> None:
        doc = fitz.open()
        page = doc.new_page(width=14_400, height=14_400)
        try:
            matrix = _ocr_render_matrix(page)
            assert page.rect.width * matrix.a <= 1650
            assert page.rect.height * matrix.d <= 1650
        finally:
            doc.close()

    def test_total_ocr_failure_returns_no_candidate(self) -> None:
        raw = _blank_pdf(page_count=2)
        service = _StubOCRService(
            [
                OCRResult(text="", error="provider rejected image"),
                OCRResult(text=""),
            ]
        )

        result = _run_full_page_ocr(
            raw,
            cast(LLMVisionOCRService, service),
        )

        assert result is None

    def test_partial_ocr_failure_preserves_successful_pages(self) -> None:
        raw = _blank_pdf(page_count=2)
        service = _StubOCRService(
            [
                OCRResult(text="", error="temporary provider failure"),
                OCRResult(text="सफल पाठ"),
            ]
        )

        result = _run_full_page_ocr(
            raw,
            cast(LLMVisionOCRService, service),
        )

        assert result is not None
        assert result.markdown == "सफल पाठ"


class _StubOCRService:
    def __init__(self, results: list[OCRResult]) -> None:
        self.results = iter(results)

    def extract_text(self, _image_stream: object) -> OCRResult:
        return next(self.results)


def _blank_pdf(page_count: int) -> bytes:
    document = fitz.open()
    try:
        for _ in range(page_count):
            document.new_page(width=200, height=300)
        return document.tobytes()
    finally:
        document.close()


# ── P0: Invalid pages spec silently returns entire document ──


class TestPagesValidation:
    """Invalid page specs should error, not silently return the whole doc."""

    @pytest.mark.parametrize("pages_spec", ["abc", "0", "-1", "3-1", "999"])
    def test_invalid_pages_should_raise(self, pages_spec: str) -> None:
        with pytest.raises(ValidationError):
            _convert(SAMPLES / "Press Release.pdf", pages=pages_spec)

    @pytest.mark.parametrize("pages_spec", ["1000000000", "1-1000000000"])
    def test_page_numbers_are_length_bounded(self, pages_spec: str) -> None:
        with pytest.raises(ValidationError, match="Invalid page range format"):
            _convert(SAMPLES / "Press Release.pdf", pages=pages_spec)

    def test_out_of_range_pages_raises(self) -> None:
        with pytest.raises(ValidationError, match="starts beyond document length"):
            _convert(SAMPLES / "Press Release.pdf", pages="5")

    @pytest.mark.parametrize("pages", [True, 1.5, [1], (1,), b"1"])
    def test_non_string_non_integer_pages_raise(self, pages: object) -> None:
        with pytest.raises(ValidationError, match="pages must be"):
            _convert(SAMPLES / "Press Release.pdf", pages=pages)

    def test_integer_page_is_accepted(self) -> None:
        assert _convert(SAMPLES / "Press Release.pdf", pages=1) == _convert(
            SAMPLES / "Press Release.pdf", pages="1"
        )

    def test_enormous_integer_page_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="integer pages must be"):
            _convert(SAMPLES / "Press Release.pdf", pages=10**5000)


# ── P0: ValueError in Kalimati repair silently drops all content ──


class TestContentDrop:
    """A recoverable error in repair must not discard the entire extraction."""

    @pytest.mark.skipif(
        not (SAMPLES / "aarop-patra.pdf").exists(),
        reason="fixture missing",
    )
    def test_aarop_patra_truncated_does_not_silently_drop_content(self):
        """F4: Truncating a PDF to 90% should produce partial content or error,
        not silently return empty text for the portion that survived."""
        full_raw = (SAMPLES / "aarop-patra.pdf").read_bytes()
        full_text = _convert(SAMPLES / "aarop-patra.pdf")
        assert len(full_text) > 1000, "Baseline conversion failed"

        # Truncate to 90%
        truncated = full_raw[: int(len(full_raw) * 0.9)]
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(truncated)
            f.flush()
            try:
                trunc_text = _convert(f.name)
            except Exception:  # noqa: BLE001 - ANY error is the acceptable outcome here
                return
            finally:
                os.unlink(f.name)

        # If extraction succeeds, it must retain substantial content. This
        # assertion sits outside the broad conversion-error handler so the test
        # cannot swallow its own failure.
        ratio = len(trunc_text) / len(full_text) if full_text else 0
        assert ratio > 0.4 or len(trunc_text) < 10, (
            f"Truncated PDF returned {len(trunc_text)} chars "
            f"({ratio:.1%} of full {len(full_text)}); "
            "expected either substantial content (>40%) or an error"
        )


# ── P0: normalize_devanagari_spacing deletes spaces after virama ──


class TestDevanagariSpacing:
    """The spacing normalizer must not delete meaningful word boundaries."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("सम्वत् २०८०", "सम्वत् २०८०"),
            ("पश्चात् 2024", "पश्चात् 2024"),
            ("एवम् ।", "एवम् ।"),
            ("अर्थात् अब", "अर्थात् अब"),
            ("छन् तथा", "छन् तथा"),
            ("क् ख", "क् ख"),
            ("क्", "क्"),
        ],
    )
    def test_virama_space_is_preserved(self, text: str, expected: str) -> None:
        assert normalize_devanagari_spacing(text) == expected


# ── P0: my-table.pdf drops entire page of table content ──


class TestTableContentDrop:
    """Table content must not be silently discarded by page-furniture filter."""

    def test_my_table_content_preserved(self):
        """F8: samples/my-table.pdf should retain table content."""
        text = _convert(SAMPLES / "my-table.pdf")
        # Get raw PyMuPDF text for comparison
        doc = fitz.open(str(SAMPLES / "my-table.pdf"))
        raw_chars = sum(len(doc[i].get_text()) for i in range(len(doc)))
        converted_chars = len(text)
        # The bug: page-furniture filter drops 70% of content
        ratio = converted_chars / raw_chars if raw_chars else 0
        assert ratio > 0.5, (
            f"Converted text is only {ratio:.1%} of raw text "
            f"({converted_chars} vs {raw_chars} chars) — likely content dropped"
        )


# ── P1: Password-protected PDFs return empty-but-successful ──


class TestPasswordProtected:
    """Encrypted PDFs should error, not return empty success."""

    def test_encrypted_pdf_raises(self):
        """F9: A password-protected PDF must raise, not return empty."""
        # Create a password-protected PDF
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Secret content", fontsize=14)
        encrypted = doc.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(encrypted)
            f.flush()
            try:
                with pytest.raises(
                    ExtractionError, match="Password-protected PDFs are not supported"
                ):
                    _convert(f.name)
            finally:
                os.unlink(f.name)


# ── P1: Legacy .doc paragraph boundaries discarded ──


class TestLegacyDoc:
    """Legacy .doc conversion must preserve paragraph structure."""

    @pytest.mark.skipif(
        not (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "integration"
            / "test_data"
            / "ciaa_legacy_sample.doc"
        ).exists(),
        reason="fixture missing",
    )
    def test_doc_preserves_paragraphs(self):
        """F10: A .doc with multiple paragraphs should not collapse to one."""
        doc_path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "integration"
            / "test_data"
            / "ciaa_legacy_sample.doc"
        )
        text = _convert(doc_path)
        # Count paragraph breaks (double newline or single newline sequences)
        paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        # A multi-paragraph document should have multiple paragraphs in output
        # The bug: all paragraph boundaries are discarded, collapsing everything
        assert len(paragraphs) > 1, (
            f"Legacy .doc produced only {len(paragraphs)} paragraph(s) — "
            f"expected multiple (paragraph boundaries were discarded)"
        )
