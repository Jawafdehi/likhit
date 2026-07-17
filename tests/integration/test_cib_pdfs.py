"""Regression coverage for real Nepal Police CIB press-release PDFs.

The fixtures under ``tests/fixtures/cib/`` are git-ignored because they carry
photographs, names, and addresses of arrested (not convicted) persons. These
tests therefore run only where the originals are present (local development,
manual verification) and are skipped in CI. Synthetic, PII-free stand-ins that
DO run in CI live in ``tests/test_scanned_pdf_detection.py``.

Every sampled CIB release is a full-page scanned raster: three carry a
non-embedded core-font decoy text layer that decodes to garbage, one is a pure
scan with no text layer. In all cases extraction must route to OCR (raise
``ScannedPdfError``) and must never emit the decoy keystrokes as if they were
text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from likhit.errors import ScannedPdfError
from likhit.extractors.font_based import FontBasedStrategy

CIB_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cib"

# Distinctive decoy keystroke fragments that must never survive extraction.
_DECOY_MARKERS = ("qt+:", "$TTDtit", "durdt{6r", "df6 dl@ilGrq")

# 1-based page numbers we expect to be routed to OCR for the known originals.
_KNOWN_CIB_OCR_PAGES = {
    "cib_346.pdf": [1, 2],
    "cib_391.pdf": [1],
    "cib_392.pdf": [1],
    "cib_489_scan.pdf": [1],
}


def _present_cib_pdfs() -> list[Path]:
    if not CIB_DIR.is_dir():
        return []
    return sorted(CIB_DIR.glob("*.pdf"))


_CIB_PDFS = _present_cib_pdfs()

pytestmark = pytest.mark.skipif(
    not _CIB_PDFS,
    reason="Real CIB fixtures are git-ignored (PII); none present locally.",
)


@pytest.mark.parametrize("pdf_path", _CIB_PDFS, ids=lambda p: p.name)
def test_cib_pdf_routes_to_ocr_and_never_emits_decoy(pdf_path: Path) -> None:
    try:
        result = FontBasedStrategy().extract_text(str(pdf_path))
    except ScannedPdfError as exc:
        # Fully scanned document: must name the pages that need OCR.
        assert exc.needs_ocr_pages, pdf_path.name
        return

    # A mixed/born-digital CIB release is allowed to return text, but the decoy
    # keystroke layer must never leak into it.
    for marker in _DECOY_MARKERS:
        assert marker not in result.raw_text, f"{pdf_path.name}: leaked {marker!r}"


@pytest.mark.parametrize(
    "name, expected_pages",
    sorted(_KNOWN_CIB_OCR_PAGES.items()),
)
def test_known_cib_originals_report_expected_ocr_pages(
    name: str, expected_pages: list[int]
) -> None:
    pdf_path = CIB_DIR / name
    if not pdf_path.exists():
        pytest.skip(f"known CIB fixture missing: {name}")

    with pytest.raises(ScannedPdfError) as exc_info:
        FontBasedStrategy().extract_text(str(pdf_path))

    assert exc_info.value.needs_ocr_pages == expected_pages
