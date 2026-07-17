"""Tests for scanned-PDF (Part A) and content-based legacy-font (Part B) detection.

These exercise the extraction fixes for Nepal Police CIB press releases: a scanned
raster carrying a non-embedded core-font "decoy" text layer must be routed to OCR
(never emitted as garbage), while a genuinely mislabeled legacy font must still be
rescued. Synthetic, PII-free PDFs stand in for the git-ignored CIB originals; the
real ones are covered in ``tests/integration/test_cib_pdfs.py`` when present.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import fitz
import pytest

from likhit.errors import ScannedPdfError
from likhit.extractors.font_based import (
    FontBasedStrategy,
    _is_probably_legacy_ascii,
    _nepali_validity,
    choose_legacy_map,
    detect_content_legacy_fonts,
)
from likhit.extractors.font_classifier import (
    IMAGE_ONLY,
    SCANNED_DECOY_TEXT,
    _is_non_embedded_core_font,
    classify_ocr_page,
    is_core_font_name,
    scan_ocr_pages,
)
from likhit.extractors.legacy_maps import get_converter_for_map
from tests.synthetic_pdfs import (
    build_legacy_then_english_pdf,
    build_mislabeled_preeti_pdf,
    build_mixed_scan_and_text_pdf,
    build_pure_scan_pdf,
    build_scanned_decoy_pdf,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "samples"


def _has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def _write_pdf(tmp_path: Path, raw: bytes, name: str = "synthetic.pdf") -> str:
    path = tmp_path / name
    path.write_bytes(raw)
    return str(path)


# --- Part A: scanned-raster / decoy-layer detection ---------------------------


def test_scanned_decoy_pdf_raises_scanned_error(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_scanned_decoy_pdf(page_count=2))

    with pytest.raises(ScannedPdfError) as exc_info:
        FontBasedStrategy().extract_text(path)

    assert exc_info.value.needs_ocr_pages == [1, 2]


def test_pure_scan_pdf_raises_scanned_error(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_pure_scan_pdf())

    with pytest.raises(ScannedPdfError) as exc_info:
        FontBasedStrategy().extract_text(path)

    assert exc_info.value.needs_ocr_pages == [1]


def test_scanned_decoy_never_emits_decoy_text(tmp_path: Path) -> None:
    # The decoy keystrokes must never leak into extracted text under any path.
    path = _write_pdf(tmp_path, build_scanned_decoy_pdf(page_count=1))
    try:
        result = FontBasedStrategy().extract_text(path)
    except ScannedPdfError:
        return
    assert "qt+:" not in result.raw_text
    assert "$TTDtit" not in result.raw_text


def test_mixed_document_keeps_real_page_and_flags_scanned_page(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_mixed_scan_and_text_pdf())

    result = FontBasedStrategy().extract_text(path)

    # Page 1 (decoy) is flagged for OCR and suppressed; page 2 survives.
    assert result.needs_ocr_pages == [1]
    assert "ordinary born-digital paragraph" in result.raw_text
    assert "qt+:" not in result.raw_text


def test_classify_ocr_page_labels_synthetic_pages(tmp_path: Path) -> None:
    decoy = fitz.open(stream=build_scanned_decoy_pdf(page_count=1), filetype="pdf")
    scan = fitz.open(stream=build_pure_scan_pdf(), filetype="pdf")
    text = fitz.open(stream=build_mislabeled_preeti_pdf(), filetype="pdf")
    try:
        assert classify_ocr_page(decoy, 0) == SCANNED_DECOY_TEXT
        assert classify_ocr_page(scan, 0) == IMAGE_ONLY
        # A born-digital page (no full-page raster) is never an OCR page.
        assert classify_ocr_page(text, 0) is None
    finally:
        decoy.close()
        scan.close()
        text.close()


def test_is_non_embedded_core_font_matches_synthetic_helvetica() -> None:
    doc = fitz.open(stream=build_scanned_decoy_pdf(page_count=1), filetype="pdf")
    try:
        fonts = doc[0].get_fonts(full=True)
        assert fonts, "expected a decoy font on the page"
        assert all(_is_non_embedded_core_font(doc, font) for font in fonts)
    finally:
        doc.close()


def test_is_core_font_name_recognizes_standard_families() -> None:
    assert is_core_font_name("Helvetica")
    assert is_core_font_name("ABCDEF+Arial-BoldMT")
    assert is_core_font_name("Times New Roman,Bold")
    assert not is_core_font_name("ABCDEE+Kalimati")
    assert not is_core_font_name("BOFDOE+Preeti")


# --- Part A must NOT misfire on clean / legacy born-digital samples -----------


@pytest.mark.parametrize(
    "sample_name",
    ["pressrelease.pdf", "Press Release.pdf", "kanunpatrika.pdf"],
)
def test_clean_and_legacy_samples_are_not_flagged_for_ocr(sample_name: str) -> None:
    sample_path = SAMPLES_DIR / sample_name
    if not sample_path.exists():
        pytest.skip(f"sample missing: {sample_name}")

    result = FontBasedStrategy().extract_text(str(sample_path))

    assert result.needs_ocr_pages == []
    assert result.raw_text.strip()


def test_scan_ocr_pages_empty_for_born_digital_sample() -> None:
    sample_path = SAMPLES_DIR / "kanunpatrika.pdf"
    if not sample_path.exists():
        pytest.skip("sample missing: kanunpatrika.pdf")
    doc = fitz.open(str(sample_path))
    try:
        # Note: kanunpatrika is deva=0 legacy AND has non-embedded core fonts,
        # yet its zero image coverage keeps it off the OCR path.
        assert scan_ocr_pages(doc) == {}
    finally:
        doc.close()


# --- Part B: content-based legacy-font detection ------------------------------


def test_choose_legacy_map_accepts_real_preeti() -> None:
    # Real Preeti keystrokes decoding to several dictionary words.
    keystrokes = "g]kfn ;/sf/ cbfnt cg';Gwfg k|ltjfbL e|i6frf/"
    map_key, validity = choose_legacy_map(keystrokes)

    assert map_key == "Preeti"
    assert validity is not None and validity["hits"] >= 2
    assert get_converter_for_map(map_key)(keystrokes).startswith("नेपाल सरकार")


def test_choose_legacy_map_declines_english() -> None:
    map_key, _validity = choose_legacy_map(
        "The quick brown fox jumps over the lazy dog several times over"
    )
    assert map_key is None


def test_nepali_validity_flags_garble_low() -> None:
    # A wrong-map read produces Devanagari code points but no real words.
    garble = "मगचमर्तटर्चमाट म२िष्न्चित्र।८भस्भ्चंष,ष्।क्ष्िँक्ष"
    validity = _nepali_validity(garble)
    assert validity["hits"] == 0
    assert validity["ratio"] > 0.8  # high ratio is a mirage; hits is what matters


def test_is_probably_legacy_ascii() -> None:
    assert _is_probably_legacy_ascii("g]kfn ;/sf/ cbfnt cg';Gwfg")
    assert not _is_probably_legacy_ascii("नेपाल सरकार")  # already Devanagari
    assert not _is_probably_legacy_ascii("   ")


def test_detect_content_legacy_fonts_on_mislabeled_preeti() -> None:
    doc = fitz.open(stream=build_mislabeled_preeti_pdf(), filetype="pdf")
    try:
        assert detect_content_legacy_fonts(doc) == {"Helvetica": "Preeti"}
    finally:
        doc.close()


def test_detect_content_legacy_fonts_ignores_english() -> None:
    doc = fitz.open(stream=build_mixed_scan_and_text_pdf(), filetype="pdf")
    try:
        ocr_pages = scan_ocr_pages(doc)
        # Page 2 is plain English Helvetica; it must NOT be mapped as legacy.
        assert detect_content_legacy_fonts(doc, frozenset(ocr_pages)) == {}
    finally:
        doc.close()


def test_content_legacy_detection_is_scoped_to_requested_pages(tmp_path: Path) -> None:
    # Page 1 is mislabeled-Preeti Helvetica, page 2 is English Helvetica (same
    # base name). Extracting only page 2 must not let page 1's Preeti flip the
    # content-map gate and remap page 2's English into Devanagari garbage.
    path = _write_pdf(tmp_path, build_legacy_then_english_pdf())

    result = FontBasedStrategy().extract_text(path, pages="2")

    assert "English catalogue reference" in result.raw_text
    assert not _has_devanagari(result.raw_text)


def test_mislabeled_preeti_pdf_extracts_as_nepali(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_mislabeled_preeti_pdf())

    result = FontBasedStrategy().extract_text(path)

    assert result.needs_ocr_pages == []
    assert "नेपाल सरकार" in result.raw_text
    assert "प्रतिवादी" in result.raw_text
    # The raw keystrokes must be gone.
    assert "g]kfn" not in result.raw_text


# --- npttf2utf SyntaxWarning suppression --------------------------------------


def test_npttf2utf_syntaxwarning_is_suppressed(tmp_path: Path) -> None:
    """Building the mapper must not surface npttf2utf's invalid-escape warning.

    Forces a fresh compile of the bundled preetimapper under a strict
    ``error::SyntaxWarning`` filter; our import-site suppression must keep it
    from becoming fatal. Skips gracefully if the site-packages cache is not
    writable.
    """

    import npttf2utf

    pkg_dir = Path(npttf2utf.__file__).parent
    pyc_files = list(pkg_dir.rglob("preetimapper*.pyc"))
    try:
        for pyc in pyc_files:
            pyc.unlink()
    except OSError:
        pytest.skip("npttf2utf bytecode cache is not writable")

    script = textwrap.dedent(
        """
        import warnings
        from likhit.extractors.legacy_maps import _get_mapper
        warnings.simplefilter("error", SyntaxWarning)
        out = _get_mapper().map_to_unicode("g]kfn ;/sf/", "Preeti")
        assert out == "नेपाल सरकार", out
        print("SUPPRESSION-OK")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(ROOT),
    )
    assert "SUPPRESSION-OK" in completed.stdout, completed.stderr
    assert "invalid escape sequence" not in completed.stderr
