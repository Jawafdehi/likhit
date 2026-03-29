"""Integration tests for end-to-end conversion success."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from markitdown import MarkItDown
import pytest

from .conftest import (
    TEST_DATA_DIR,
    assert_fixture_size_under_threshold,
    compute_total_fixture_size,
    discover_all_fixtures,
    discover_fixtures_by_extension,
)

IS_WINDOWS = platform.system() == "Windows"


def _has_working_doc_runtime() -> bool:
    """Return True when at least one DOC extractor runtime is available."""
    if shutil.which("textutil"):
        return True

    antiword_bin = shutil.which("antiword")
    if antiword_bin:
        return True

    try:
        import pyantiword

        bundled = Path(pyantiword.__file__).resolve().parent / "antiword"
        if not bundled.exists():
            return False
        subprocess.run(
            [str(bundled), "-h"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return True
    except (OSError, ImportError):
        return False


DOC_EXTRACTION_AVAILABLE = (not IS_WINDOWS) and _has_working_doc_runtime()
SKIP_DOC_WHEN_UNAVAILABLE = pytest.mark.skipif(
    not DOC_EXTRACTION_AVAILABLE,
    reason=(
        "DOC extraction requires a working runtime (antiword or macOS textutil). "
        "On macOS you can also install antiword with: brew install antiword"
    ),
)


def _md() -> MarkItDown:
    return MarkItDown(enable_plugins=True)


class TestFixtureGovernance:
    """Tests to ensure fixture directory meets requirements."""

    def test_fixture_directory_exists(self) -> None:
        assert TEST_DATA_DIR.exists(), f"Test data directory not found: {TEST_DATA_DIR}"
        assert TEST_DATA_DIR.is_dir(), f"Test data path is not a directory: {TEST_DATA_DIR}"

    def test_fixture_size_under_threshold(self) -> None:
        assert_fixture_size_under_threshold(threshold_mb=50)

        total_bytes = compute_total_fixture_size()
        total_mb = total_bytes / (1024 * 1024)
        print(f"\nTotal fixture size: {total_mb:.2f} MB")

    def test_required_formats_present(self) -> None:
        pdf_fixtures = discover_fixtures_by_extension(".pdf")
        docx_fixtures = discover_fixtures_by_extension(".docx")
        doc_fixtures = discover_fixtures_by_extension(".doc")

        assert len(pdf_fixtures) > 0, "No PDF fixtures found"
        assert len(docx_fixtures) > 0, "No DOCX fixtures found"
        assert len(doc_fixtures) > 0, "No DOC fixtures found"

        print(
            f"\nFound {len(pdf_fixtures)} PDF, {len(docx_fixtures)} DOCX, {len(doc_fixtures)} DOC fixtures"
        )


class TestPluginConversion:
    """Integration tests for plugin-backed MarkItDown conversion."""

    @pytest.mark.parametrize("fixture_path", discover_all_fixtures())
    def test_convert_produces_nonempty_output(self, fixture_path: Path) -> None:
        if fixture_path.suffix.lower() == ".doc" and not DOC_EXTRACTION_AVAILABLE:
            pytest.skip(
                "DOC extraction requires a working runtime (antiword or macOS textutil)"
            )

        markdown = _md().convert(str(fixture_path)).text_content

        assert markdown, f"Empty output for {fixture_path.name}"
        assert len(markdown) > 0, f"Zero-length output for {fixture_path.name}"
        assert isinstance(markdown, str), f"Output is not a string for {fixture_path.name}"

    def test_notice_style_pdf_contains_expected_markers(self) -> None:
        notice_pdf = TEST_DATA_DIR / "ciaa_pressrelease_sample.pdf"
        if not notice_pdf.exists():
            pytest.skip("Notice-style PDF sample not found")

        markdown = _md().convert(str(notice_pdf)).text_content

        assert any(
            marker in markdown for marker in ["आरोपपत्र", "विषय", "मिति"]
        ), "Notice markers not found in output"

    def test_two_column_pdf_contains_expected_markers(self) -> None:
        two_column_pdf = TEST_DATA_DIR / "kanun_patrika_sample.pdf"
        if not two_column_pdf.exists():
            pytest.skip("Two-column PDF sample not found")

        markdown = _md().convert(str(two_column_pdf)).text_content

        assert any(
            marker in markdown for marker in ["निर्णय नं", "कानून पत्रिका"]
        ), "Two-column markers not found in output"

    def test_notice_style_docx_contains_expected_markers(self) -> None:
        notice_docx = TEST_DATA_DIR / "ciaa_pressrelease_sample.docx"
        if not notice_docx.exists():
            pytest.skip("Notice-style DOCX sample not found")

        markdown = _md().convert(str(notice_docx)).text_content

        assert any(
            marker in markdown for marker in ["विषय", "मिति", "प्रवक्ता"]
        ), "Notice markers not found in DOCX output"

    @SKIP_DOC_WHEN_UNAVAILABLE
    def test_notice_style_doc_contains_expected_markers(self) -> None:
        notice_doc = TEST_DATA_DIR / "ciaa_legacy_sample.doc"
        if not notice_doc.exists():
            pytest.skip("Notice-style DOC sample not found")

        markdown = _md().convert(str(notice_doc)).text_content

        assert any(
            marker in markdown for marker in ["विषय", "प्रेस", "मिति"]
        ), "Notice markers not found in DOC output"
