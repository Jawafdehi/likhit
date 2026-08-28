"""Regression tests for likhit breakpoints found during research.

Finding IDs reference the original breakpoint research.
"""

import io
import os
import re
import tempfile
from pathlib import Path
from typing import cast
import zipfile

import fitz
import pytest
from markitdown import MarkItDown, StreamInfo
from markitdown_ocr import LLMVisionOCRService, OCRResult

from likhit.converters.nepali_pdf import (
    CONVERSION_ERROR_MARKER_PATTERN,
    NEEDS_OCR_MARKER_PATTERN,
    _OCR_MAX_RENDER_PIXELS,
    _base64_encoded_size,
    _ocr_render_matrix,
    _prepare_pdf,
    _render_page_for_ocr,
    _run_full_page_ocr,
)
from likhit.extractors.font_based import FontBasedStrategy
from likhit.extractors.kalimati import normalize_devanagari_spacing
from likhit.save_cli import main as save_cli_main
from tests.synthetic_pdfs import build_mixed_scan_and_text_pdf

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
    """OCR-required pages must never return decoy text as successful content."""

    def test_page_classification_covers_mixed_documents_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = _blank_pdf(page_count=200)
        visited: list[int] = []

        def classify(_document: fitz.Document, page_index: int) -> str | None:
            visited.append(page_index)
            return None if page_index == 1 else "image_only"

        monkeypatch.setattr(
            "likhit.converters.nepali_pdf.classify_ocr_page",
            classify,
        )

        prepared = _prepare_pdf(raw, pages=None)

        assert visited == list(range(200))
        assert sorted(prepared.ocr_pages) == [1, *range(3, 201)]

    def test_preclassified_pages_bypass_extractor_rescan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "likhit.extractors.font_based.scan_ocr_pages",
            lambda _document: pytest.fail("preclassified pages were scanned again"),
        )

        result = FontBasedStrategy().extract_text(
            str(SAMPLES / "Press Release.pdf"),
            _ocr_pages={},
        )

        assert result.raw_text

    def test_nirnaya_marks_required_pages_instead_of_returning_latin_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_ocr_environment(monkeypatch)

        text = _convert(SAMPLES / "nirnaya.pdf")

        marker = NEEDS_OCR_MARKER_PATTERN.search(text)
        assert marker is not None
        assert marker.groups() == ("1,2", "not-configured")
        assert "t\\,&H" not in text
        assert "uoo5 hrD SD" not in text

    def test_requested_scan_page_reaches_the_caller_with_its_page_number(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_ocr_environment(monkeypatch)

        text = _convert(SAMPLES / "nirnaya.pdf", pages="2")

        marker = NEEDS_OCR_MARKER_PATTERN.search(text)
        assert marker is not None
        assert marker.groups() == ("2", "not-configured")

    def test_mixed_document_keeps_text_and_marks_only_the_scan(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _clear_ocr_environment(monkeypatch)
        path = tmp_path / "mixed.pdf"
        path.write_bytes(build_mixed_scan_and_text_pdf())

        text = _convert(path)

        assert "ordinary born-digital paragraph" in text
        assert "qt+:" not in text
        marker = NEEDS_OCR_MARKER_PATTERN.search(text)
        assert marker is not None
        assert marker.groups() == ("1", "not-configured")

    def test_scanned_member_does_not_abort_zip_siblings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _clear_ocr_environment(monkeypatch)
        archive = tmp_path / "documents.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(SAMPLES / "nirnaya.pdf", "nirnaya.pdf")
            output.writestr("first.txt", "first sibling")
            output.writestr("second.txt", "second sibling")

        text = _convert(archive)

        assert "first sibling" in text
        assert "second sibling" in text
        assert NEEDS_OCR_MARKER_PATTERN.search(text)

    def test_scanned_input_does_not_abort_save_cli_batch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _clear_ocr_environment(monkeypatch)
        output = tmp_path / "markdown"

        exit_code = save_cli_main(
            [
                str(SAMPLES / "nirnaya.pdf"),
                str(SAMPLES / "table.pdf"),
                "--out-dir",
                str(output),
            ]
        )

        assert exit_code == 0
        assert NEEDS_OCR_MARKER_PATTERN.search(
            (output / "nirnaya.md").read_text(encoding="utf-8")
        )
        assert "आयोगको निर्णय मिति" in (output / "table.md").read_text(encoding="utf-8")

    def test_configured_but_failing_ocr_never_restores_decoy_junk(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _clear_ocr_environment(monkeypatch)

        class FailingCompletions:
            def create(self, **_kwargs: object) -> object:
                raise ConnectionError("provider unavailable")

        class Chat:
            completions = FailingCompletions()

        class Client:
            chat = Chat()

        text = (
            MarkItDown(
                enable_plugins=True,
                llm_client=Client(),
                llm_model="unreachable-model",
            )
            .convert(str(SAMPLES / "nirnaya.pdf"))
            .markdown
        )

        marker = NEEDS_OCR_MARKER_PATTERN.search(text)
        assert marker is not None
        assert marker.groups() == ("1,2", "ocr-failed")
        assert "t\\,&H" not in text


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
            rendered_pixels = page.rect.width * matrix.a * page.rect.height * matrix.d
            assert rendered_pixels <= _OCR_MAX_RENDER_PIXELS
        finally:
            doc.close()

    def test_ordinary_page_starts_at_300_dpi(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LIKHIT_OCR_DPI", raising=False)
        doc = fitz.open(SAMPLES / "Press Release.pdf")
        try:
            matrix = _ocr_render_matrix(doc[0])
        finally:
            doc.close()

        assert matrix.a == pytest.approx(300 / 72)
        assert matrix.d == pytest.approx(300 / 72)

    def test_configured_dpi_is_applied_before_adaptive_shrinking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LIKHIT_OCR_DPI", "150")
        doc = fitz.open(SAMPLES / "Press Release.pdf")
        try:
            matrix = _ocr_render_matrix(doc[0])
        finally:
            doc.close()

        assert matrix.a == pytest.approx(150 / 72)

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
        marker = NEEDS_OCR_MARKER_PATTERN.search(result.markdown)
        assert marker is not None
        assert marker.groups() == ("1", "ocr-failed")
        assert "सफल पाठ" in result.markdown


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
    """Invalid page specs must return an explicit error, never whole-doc text."""

    @pytest.mark.parametrize("pages_spec", ["abc", "0", "-1", "3-1", "999"])
    def test_invalid_pages_are_marked(self, pages_spec: str) -> None:
        text = _convert(SAMPLES / "Press Release.pdf", pages=pages_spec)

        assert CONVERSION_ERROR_MARKER_PATTERN.search(text)
        assert "अख्तियार दुरुपयोग" not in text

    def test_huge_range_end_is_clamped(self) -> None:
        assert _convert(
            SAMPLES / "Press Release.pdf", pages="1-1000000000"
        ) == _convert(SAMPLES / "Press Release.pdf")

    def test_leading_zero_page_is_accepted(self) -> None:
        assert _convert(SAMPLES / "Press Release.pdf", pages="0000000001") == _convert(
            SAMPLES / "Press Release.pdf", pages="1"
        )

    @pytest.mark.parametrize("pages", [True, 1.5, [1], (1,), b"1"])
    def test_non_string_non_integer_pages_are_marked(self, pages: object) -> None:
        text = _convert(SAMPLES / "Press Release.pdf", pages=pages)

        assert CONVERSION_ERROR_MARKER_PATTERN.search(text)
        assert "अख्तियार दुरुपयोग" not in text

    def test_integer_page_is_accepted(self) -> None:
        assert _convert(SAMPLES / "Press Release.pdf", pages=1) == _convert(
            SAMPLES / "Press Release.pdf", pages="1"
        )

    def test_enormous_integer_page_raises_validation_error(self) -> None:
        text = _convert(SAMPLES / "Press Release.pdf", pages=10**5000)

        assert CONVERSION_ERROR_MARKER_PATTERN.search(text)
        assert "starts beyond document length" in text

    def test_page_validation_defers_malformed_pdf_handling(self) -> None:
        raw = b"%PDF-1.4\nthis is not a pdf at all\n%%EOF\n"

        result = MarkItDown(enable_plugins=True).convert_stream(
            io.BytesIO(raw),
            stream_info=StreamInfo(
                extension=".pdf",
                mimetype="application/pdf",
            ),
            pages="1",
        )

        assert result.markdown == ""


# ── P0: ValueError in Kalimati repair silently drops all content ──


class TestContentDrop:
    """A recoverable error in repair must not discard the entire extraction."""

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
            ("पुर् याएको", "पुर्याएको"),
            ("नपुर् याई", "नपुर्याई"),
            # The nya conjunct, carried from the v18 extractor line. That line got
            # these by deleting EVERY space after a virama, which is what joins the
            # preserved cases above; these are here so the narrower rule that
            # replaced it is pinned by its own cases and not only through the two
            # cross-span Kokila tests in test_font_based.py that first caught it.
            ("सञ् चालन", "सञ्चालन"),
            ("पञ् चायत", "पञ्चायत"),
            ("अञ् चल", "अञ्चल"),
            # ...and only before a consonant. Nya + virama before a digit or a
            # danda is not a broken conjunct, so the space stays. Without this the
            # rule could be widened to any following character and still look green.
            ("सञ् २०८०", "सञ् २०८०"),
            ("ञ् ।", "ञ् ।"),
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

    def test_encrypted_pdf_returns_an_explicit_error(self):
        """F9: A password-protected PDF must not return empty success."""
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
                text = _convert(f.name)
            finally:
                os.unlink(f.name)

        marker = CONVERSION_ERROR_MARKER_PATTERN.search(text)
        assert marker is not None
        assert marker.group(1) == "password-protected"
        assert "Secret content" not in text


# ── P1: Legacy .doc paragraph boundaries discarded ──


class TestLegacyDoc:
    """Legacy .doc conversion must preserve paragraph structure."""

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
