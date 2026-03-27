"""Canonical PDF extraction exports."""

from likhit.extraction.pdf.font_based import FontBasedStrategy
from likhit.extraction.pdf.font_classifier import classify_font, scan_pdf_fonts
from likhit.extraction.pdf.kalimati import (
    fix_kalimati_cmap,
    normalize_devanagari_spacing,
    reorder_devanagari,
)
from likhit.extraction.pdf.legacy_maps import get_converter
from likhit.extraction.pdf.repair import (
    extract_repaired_text_blocks,
    is_pdf_stream,
    needs_nepali_pdf_repair,
)
from likhit.extraction.pdf.tables import detect_page_tables, merge_continuation_tables

__all__ = [
    "FontBasedStrategy",
    "classify_font",
    "detect_page_tables",
    "extract_repaired_text_blocks",
    "fix_kalimati_cmap",
    "get_converter",
    "is_pdf_stream",
    "merge_continuation_tables",
    "needs_nepali_pdf_repair",
    "normalize_devanagari_spacing",
    "reorder_devanagari",
    "scan_pdf_fonts",
]
