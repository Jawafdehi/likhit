"""Compatibility wrapper for font classification helpers."""

from __future__ import annotations

from typing import BinaryIO

import fitz

from likhit.extractors.font_classifier import (
    classify_font,
    scan_pdf_fonts,
    scan_pdf_fonts_by_page,
)

__all__ = [
    "classify_font",
    "scan_pdf_fonts",
    "scan_pdf_fonts_by_page",
    "classify_fonts_from_stream",
]


def classify_fonts_from_stream(stream: BinaryIO) -> dict[str, str]:
    """
    Same as the path-based classifier but accepts a binary stream.
    The stream is consumed; callers must seek(0) before passing if needed again.
    """
    doc = fitz.open(stream=stream.read(), filetype="pdf")
    try:
        return scan_pdf_fonts(doc)
    finally:
        doc.close()
