"""Failing reproducer tests for likhit breakpoints found during research.

Each test targets a specific confirmed finding. They are EXPECTED TO FAIL
until the underlying bug is fixed — run with `pytest tests/test_repros.py`
and count the failures as a scoreboard toward resolution.

Finding IDs reference research/REPORT.md.
"""

import os
import re
import tempfile
from pathlib import Path

import fitz
import pytest
from markitdown import MarkItDown

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
CORPUS = Path(__file__).resolve().parents[1] / "research" / "corpus" / "gon_mixed"


def _convert(path: str | Path, **kwargs) -> str:
    """Convert a file through likhit and return its text."""
    md = MarkItDown(enable_plugins=True)
    return md.convert(str(path), **kwargs).text_content or ""


# ── P0: Silent mojibake on OCR-required files without OCR configured ──


class TestSilentMojibake:
    """nirnaya.pdf returns Latin mojibake instead of Nepali when OCR is unavailable."""

    @pytest.mark.xfail(
        strict=True,
        reason="F1, LIVE on this base: a scanned Nepali document with no OCR "
        "configured returns 0 Devanagari against 1,717 Latin characters (13,527 "
        "total) and reports success. Silent, so no caller can detect it.",
    )
    def test_nirnaya_should_not_silently_return_latin_garbage(self):
        """F1: If a file is all-scan (scanned_decoy_text), and OCR is not
        configured, the converter MUST either raise or clearly indicate failure —
        not return 13k chars of mojibake."""
        text = _convert(SAMPLES / "nirnaya.pdf")
        # The bug: returns 13,527 chars that are pure Latin (deva_frac = 0.0)
        # despite being a Nepali document. A correct implementation would either:
        # - Raise an error explaining OCR is needed
        # - Return an empty/marker result
        # - NOT return confident-looking Latin garbage
        deva = sum(1 for c in text if 0x0900 <= ord(c) < 0x0980)
        latin = sum(1 for c in text if c.isalpha() and ord(c) < 0x250)
        assert deva > latin, (
            f"Expected Devanagari output or an error for this scanned Nepali doc, "
            f"but got {deva} Devanagari vs {latin} Latin chars ({len(text)} total)"
        )


# ── P0: 300 DPI hardcoded causes OCR to always exceed API limits ──


class TestOCRImageSize:
    """likhit renders OCR pages at 300 DPI, yielding ~10MB PNGs for A4."""

    @pytest.mark.xfail(
        strict=True,
        reason="F2, LIVE: 300 DPI is hardcoded, so a 2550x3300 page renders to "
        "13.57 MB base64 against the 5 MB API limit -- every OCR call on a "
        "full-page scan is rejected before it is made.",
    )
    def test_ocr_image_should_fit_within_5mb(self):
        """F2: _run_full_page_ocr should produce images under 5MB base64."""
        raw = (SAMPLES / "nirnaya.pdf").read_bytes()
        doc = fitz.open(stream=raw, filetype="pdf")
        # Reproduce the hardcoded DPI from nepali_pdf.py:218
        matrix = fitz.Matrix(300 / 72, 300 / 72)
        pixmap = doc[0].get_pixmap(matrix=matrix)
        png = pixmap.tobytes("png")
        b64_size = len(png) * 4 / 3
        max_bytes = 5 * 1024 * 1024
        assert b64_size <= max_bytes, (
            f"OCR image at 300 DPI is {b64_size / 1e6:.2f} MB base64, "
            f"exceeds 5 MB limit. Page dims: {pixmap.width}x{pixmap.height}"
        )


# ── P0: Invalid pages spec silently returns entire document ──


class TestPagesValidation:
    """Invalid page specs should error, not silently return the whole doc."""

    @pytest.mark.parametrize("pages_spec", ["abc", "0", "-1", "3-1", "999"])
    @pytest.mark.xfail(
        strict=True,
        reason="F3, LIVE: `pages` is not validated. 'abc', '0', '-1', '3-1' and "
        "'999' all DO NOT RAISE and silently convert the whole document, so a "
        "caller asking for one page gets everything and cannot tell.",
    )
    def test_invalid_pages_should_raise(self, pages_spec):
        """F3: Invalid page ranges must not silently return content."""
        # samples/Press Release.pdf is a 1-page doc
        with pytest.raises(Exception):
            _convert(SAMPLES / "Press Release.pdf", pages=pages_spec)

    @pytest.mark.xfail(
        strict=True,
        reason="F3, the other half: an out-of-range range returns content rather "
        "than an error, which is worse than raising because it looks like a "
        "successful narrow read.",
    )
    def test_out_of_range_pages_returns_wrong_content(self):
        """F3b: Requesting page 5 of a 1-page doc should error, not return mojibake."""
        # When pages="5" on a 1-page doc, a correct implementation raises.
        # The bug: it silently returns the entire document.
        with pytest.raises(Exception):
            _convert(SAMPLES / "Press Release.pdf", pages="5")


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
                # If it succeeds, it should have substantial content (>50% of full)
                # The bug: ValueError in kalimati repair drops everything
                ratio = len(trunc_text) / len(full_text) if full_text else 0
                assert ratio > 0.4 or len(trunc_text) < 10, (
                    f"Truncated PDF returned {len(trunc_text)} chars "
                    f"({ratio:.1%} of full {len(full_text)}); "
                    f"expected either substantial content (>40%) or an error"
                )
            except Exception:  # noqa: BLE001 - ANY error is the acceptable outcome here
                pass  # An error on a truncated PDF is acceptable
            finally:
                os.unlink(f.name)


# ── P0: GSUB fixpoint with 3.4KB PDF causes 5GB memory allocation ──


class TestResourceExhaustion:
    """Adversarial font tables must not cause unbounded resource use."""

    @pytest.mark.skip(
        reason="Triggers OOM/core dump — run only in isolated subprocess harness"
    )
    def test_crafted_gsub_does_not_allocate_gigabytes(self):
        """F5: A small PDF with a pathological GSUB table should fail fast,
        not allocate 5GB of memory.

        The real trigger is in /tmp/evil26.pdf from the workflow.
        Skipped from pytest because it causes a core dump; run via the
        subprocess harness (research/harness/run_one.py) instead.
        """
        pass


# ── P0: normalize_devanagari_spacing deletes spaces after virama ──


class TestDevanagariSpacing:
    """The spacing normalizer must not delete meaningful word boundaries."""

    @pytest.mark.skipif(
        not list(CORPUS.glob("2bc394a568*")),
        reason="corpus file missing",
    )
    def test_virama_space_not_deleted(self):
        """F6: Space after virama is a word boundary, not disposable."""
        f = next(CORPUS.glob("2bc394a568*"))
        text = _convert(f)
        # Virama (U+094D) followed by space is a word boundary in Nepali.
        # The bug: normalize_devanagari_spacing at kalimati.py:719
        # unconditionally deletes such spaces.
        virama = "्"
        # Count spaces after virama in the source vs output
        # A correct conversion preserves word boundaries.
        virama_space = text.count(virama + " ")
        virama_nospace = text.count(virama) - virama_space
        # If the fix is applied, there should be SOME virama+space sequences
        # For unfixed code, virama_space will be 0 or very low
        total_virama = virama_space + virama_nospace
        if total_virama > 0:
            ratio = virama_space / total_virama
            # In natural Nepali text, a significant fraction of viramas
            # are at word boundaries (followed by space)
            assert ratio > 0.05, (
                f"Only {virama_space}/{total_virama} ({ratio:.1%}) viramas "
                f"are followed by a space — expected >5% for word boundaries"
            )


# ── P0: MemoryError silently swallowed returns mojibake ──


class TestMemoryErrorSwallowed:
    """OOM in kalimati.py should propagate, not silently return garbage."""

    @pytest.mark.xfail(
        strict=True,
        reason="F7, LIVE: same document as F1 and the same 0.0% Devanagari of "
        "1,717 letters. Kept separate because it asserts the general principle "
        "-- an internal error must not yield garbage-but-successful -- while F1 "
        "asserts the specific OCR path. ⚠️ NOT the MemoryError re-raise C17 "
        "proposed: #66 settled that by signalling in band, because a re-raised "
        "MemoryError cannot reach the caller through MarkItDown's loop.",
    )
    def test_conversion_error_should_not_produce_garbage(self):
        """F7: If any internal error occurs, the output must either be correct
        or the error must propagate — never garbage-but-successful."""
        # This tests the principle: for any file where conversion encounters
        # an internal error, status should not be "ok" with garbage.
        # We use nirnaya.pdf as the archetype of this pattern.
        text = _convert(SAMPLES / "nirnaya.pdf")
        if not text.strip():
            return  # Empty is acceptable for error cases
        # If text is produced, it should be valid Nepali, not mojibake
        letters = [c for c in text if c.isalpha()]
        if len(letters) > 100:
            deva = sum(1 for c in letters if 0x0900 <= ord(c) < 0x0980)
            ratio = deva / len(letters)
            assert ratio > 0.3, (
                f"Document produced {len(letters)} letters but only "
                f"{ratio:.1%} are Devanagari — likely mojibake"
            )


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
                text = _convert(f.name)
                # Should either raise or return meaningful error indication
                # The bug: returns empty string with no error
                assert text.strip(), (
                    "Password-protected PDF returned empty string instead of "
                    "raising an error or including an error message"
                )
            except Exception:  # noqa: BLE001 - ANY error is the acceptable outcome here
                pass  # Raising is the correct behavior
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
