"""Canonical extraction exports."""

from likhit.extraction.base import ExtractionStrategy, RawDocument, TextFragment
from likhit.extraction.pdf.font_based import FontBasedStrategy
from likhit.extraction.word.docx_based import DocxBasedStrategy

__all__ = [
    "DocxBasedStrategy",
    "ExtractionStrategy",
    "FontBasedStrategy",
    "RawDocument",
    "TextFragment",
]
