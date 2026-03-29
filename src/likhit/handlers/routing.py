"""Structure-based routing helpers."""

from __future__ import annotations

from pathlib import Path

from likhit.extractors.docx_based import DocxBasedStrategy
from likhit.extractors.font_based import FontBasedStrategy
from likhit.handlers.single_column_notice import SingleColumnNoticeHandler
from likhit.handlers.structure_detection import detect_structure
from likhit.handlers.two_column_layout import TwoColumnLayoutHandler
from likhit.models import DocumentType, ExtractionResult
from likhit.renderers import MarkdownRenderer


def convert_with_detected_structure(file_path: str) -> str | None:
    """Return structure-aware markdown when a supported layout is detected."""

    strategy = _strategy_for_suffix(Path(file_path).suffix.lower())
    raw_document = strategy.extract_text(file_path)
    doc_type = detect_structure(raw_document)
    if doc_type is None:
        return None

    handler = _resolve_handler(doc_type)
    result = handler.build_result(
        raw_document,
        {
            "title": None,
            "publication_date": None,
            "source_url": None,
        },
    )
    return render_markdown_without_frontmatter(result)


def render_markdown_without_frontmatter(result: ExtractionResult) -> str:
    markdown = MarkdownRenderer().render(result).lstrip()
    if not markdown.startswith("---\n"):
        return markdown.strip()

    parts = markdown.split("\n---\n", 1)
    if len(parts) != 2:
        return markdown.strip()
    return parts[1].strip()


def _strategy_for_suffix(suffix: str) -> FontBasedStrategy | DocxBasedStrategy:
    if suffix in {".docx", ".doc"}:
        return DocxBasedStrategy()
    return FontBasedStrategy()


def _resolve_handler(doc_type: DocumentType) -> SingleColumnNoticeHandler | TwoColumnLayoutHandler:
    if doc_type is DocumentType.SINGLE_COLUMN_NOTICE:
        return SingleColumnNoticeHandler()
    if doc_type is DocumentType.TWO_COLUMN_LAYOUT:
        return TwoColumnLayoutHandler()
    raise ValueError(f"Unsupported structure type: {doc_type.value}")
