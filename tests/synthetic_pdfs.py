"""Builders for small, PII-free synthetic PDFs used by the scanned-PDF tests.

These reproduce the structural signatures that the real (git-ignored, PII-bearing)
Nepal Police CIB press releases exhibit, so the detection logic can be exercised
in CI without shipping the sensitive originals:

- a scanned raster whose only text is a non-embedded core-font "decoy" layer,
- a pure image-only raster with no text layer,
- a bare Latin core font that actually carries legacy (Preeti) keystrokes,
- a mixed document with one scanned page and one born-digital page.

Everything is generated with PyMuPDF's built-in Helvetica (a non-embedded
standard-14 core font, exactly like the CIB decoy) and a flat gray placeholder
image, so no real document, font file, or personal data is involved.
"""

from __future__ import annotations

import fitz

_PAGE_WIDTH = 595.0
_PAGE_HEIGHT = 842.0

# Legacy-keystroke gibberish taken from the shape of a real CIB decoy layer
# (ASCII Preeti keystrokes that decode to nonsense under every legacy map).
_DECOY_LINES = (
    "durdt{6r{ df6 dl@ilGrq qt+: $TTDtit",
    "o I -v\\ I !, istgQ qzql l4itYo IFIT'M:",
    "[611 q0 dffiq + Erif,i.l CET{ITf,q wf",
    "c).e.i.xo\\e ffi:- 1oc? *a t t rrt risf",
    "qta: o q-xqlt, s.3q d65ilmel urn6r orrurmu",
)

# Real Preeti keystrokes that decode to common Nepali admin/legal words.
_PREETI_LINES = (
    "g]kfn ;/sf/",
    "cbfnt cg';Gwfg k|ltjfbL",
    "e|i6frf/ ;DjGwdf sf7df8f}+ lhNnf",
    "cg';Gwfg cfof]udf btf{ ePsf] d'2f",
)


def _fill_page_with_image(page: fitz.Page) -> None:
    """Cover the whole page with a flat gray placeholder raster."""

    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8))
    pixmap.set_rect(pixmap.irect, (212, 212, 212))
    page.insert_image(page.rect, pixmap=pixmap)


def _write_lines(page: fitz.Page, lines: tuple[str, ...], *, start_y: float) -> None:
    y = start_y
    for line in lines:
        page.insert_text((60.0, y), line, fontname="helv", fontsize=11)
        y += 18.0


def build_scanned_decoy_pdf(page_count: int = 2) -> bytes:
    """Full-page raster(s) with a non-embedded core-font decoy text layer."""

    doc = fitz.open()
    try:
        for _ in range(page_count):
            page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
            _fill_page_with_image(page)
            _write_lines(page, _DECOY_LINES, start_y=90.0)
        return doc.tobytes()
    finally:
        doc.close()


def build_pure_scan_pdf() -> bytes:
    """A single full-page raster with no text layer at all."""

    doc = fitz.open()
    try:
        page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        _fill_page_with_image(page)
        return doc.tobytes()
    finally:
        doc.close()


def build_mislabeled_preeti_pdf() -> bytes:
    """A born-digital page whose bare Helvetica font carries Preeti keystrokes."""

    doc = fitz.open()
    try:
        page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        _write_lines(page, _PREETI_LINES, start_y=100.0)
        return doc.tobytes()
    finally:
        doc.close()


def build_legacy_then_english_pdf() -> bytes:
    """Page 1 is mislabeled-Preeti Helvetica; page 2 is ordinary English Helvetica.

    Both pages share the base font name "Helvetica". Used to prove that
    content-based legacy detection is scoped to the requested page range: a
    ``pages='2'`` extraction must not let page 1's Preeti flip the gate and
    corrupt page 2's English.
    """

    doc = fitz.open()
    try:
        legacy_page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        _write_lines(legacy_page, _PREETI_LINES, start_y=100.0)

        english_page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        _write_lines(
            english_page,
            (
                "Ordinary English catalogue reference line one.",
                "Second English line with plain readable words.",
            ),
            start_y=100.0,
        )
        return doc.tobytes()
    finally:
        doc.close()


def build_mixed_scan_and_text_pdf() -> bytes:
    """Page 1 is a scanned decoy; page 2 is ordinary born-digital text."""

    doc = fitz.open()
    try:
        decoy_page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        _fill_page_with_image(decoy_page)
        _write_lines(decoy_page, _DECOY_LINES, start_y=90.0)

        text_page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        _write_lines(
            text_page,
            (
                "This is an ordinary born-digital paragraph with real words.",
                "It must survive extraction while page one is routed to OCR.",
            ),
            start_y=100.0,
        )
        return doc.tobytes()
    finally:
        doc.close()
