"""Compatibility wrapper for Nepali PDF repair helpers."""

from likhit.extraction.pdf.repair import (
    extract_repaired_text_blocks,
    is_pdf_stream,
    needs_nepali_pdf_repair,
)

__all__ = [
    "extract_repaired_text_blocks",
    "is_pdf_stream",
    "needs_nepali_pdf_repair",
]
