"""Manual regression coverage for the private Nepal Police CIB PDF fixtures.

The originals contain photographs, names, and addresses of arrested people and
must remain git-ignored. Run this module explicitly only on a machine carrying
all four fixtures. Missing fixtures fail instead of creating skipped outcomes.
"""

from pathlib import Path

import pytest

from likhit.errors import ScannedPdfError
from likhit.extractors.font_based import FontBasedStrategy

CIB_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cib"

_DECOY_MARKERS = ("qt+:", "$TTDtit", "durdt{6r", "df6 dl@ilGrq")
_KNOWN_CIB_OCR_PAGES = {
    "cib_346.pdf": [1, 2],
    "cib_391.pdf": [1],
    "cib_392.pdf": [1],
    "cib_489_scan.pdf": [1],
}


def _required_cib_pdfs() -> list[Path]:
    paths = sorted(CIB_DIR.glob("*.pdf"))
    missing = sorted(set(_KNOWN_CIB_OCR_PAGES) - {path.name for path in paths})
    assert not missing, (
        f"missing private CIB fixtures in {CIB_DIR}: {missing}; install the "
        "complete local fixture set before running tests/manual/test_cib_pdfs.py"
    )
    return paths


def test_every_cib_pdf_routes_to_ocr_or_returns_decoy_free_text() -> None:
    for pdf_path in _required_cib_pdfs():
        try:
            result = FontBasedStrategy().extract_text(str(pdf_path))
        except ScannedPdfError as exc:
            assert exc.needs_ocr_pages, pdf_path.name
            continue

        for marker in _DECOY_MARKERS:
            assert marker not in result.raw_text, f"{pdf_path.name}: leaked {marker!r}"


def test_known_cib_originals_report_expected_ocr_pages() -> None:
    pdfs = {path.name: path for path in _required_cib_pdfs()}
    for name, expected_pages in sorted(_KNOWN_CIB_OCR_PAGES.items()):
        with pytest.raises(ScannedPdfError) as exc_info:
            FontBasedStrategy().extract_text(str(pdfs[name]))

        assert exc_info.value.needs_ocr_pages == expected_pages
