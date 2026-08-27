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


def _required_fixture(name: str) -> Path:
    path = CIB_DIR / name
    assert path.is_file(), (
        f"missing private CIB fixture {path}; install the complete local fixture "
        "set before running tests/manual/test_cib_pdfs.py"
    )
    return path


@pytest.mark.parametrize("name", sorted(_KNOWN_CIB_OCR_PAGES))
def test_cib_pdf_routes_to_ocr_and_never_emits_decoy(name: str) -> None:
    pdf_path = _required_fixture(name)
    try:
        result = FontBasedStrategy().extract_text(str(pdf_path))
    except ScannedPdfError as exc:
        assert exc.needs_ocr_pages
        return

    for marker in _DECOY_MARKERS:
        assert marker not in result.raw_text, f"{name}: leaked {marker!r}"


@pytest.mark.parametrize(
    ("name", "expected_pages"),
    sorted(_KNOWN_CIB_OCR_PAGES.items()),
)
def test_known_cib_originals_report_expected_ocr_pages(
    name: str, expected_pages: list[int]
) -> None:
    with pytest.raises(ScannedPdfError) as exc_info:
        FontBasedStrategy().extract_text(str(_required_fixture(name)))

    assert exc_info.value.needs_ocr_pages == expected_pages
