"""
NepaliPdfConverter — markitdown DocumentConverter for Nepali PDFs.

Intercepts born-digital PDFs that contain Kalimati broken-CMap fonts or
legacy Nepali fonts and applies likhit's existing extraction pipeline before
emitting Markdown.
"""

from __future__ import annotations

import io
from collections import defaultdict
from pathlib import Path
from statistics import median
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo

from likhit.errors import ExtractionError
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.font_based import FontBasedStrategy
from likhit.font_classifier import classify_fonts_from_stream
from likhit.handlers.content_blocks import build_content_blocks, table_to_plain_text
from likhit.handlers.structure_detection import detect_structure
from likhit.handlers.two_column_layout import TwoColumnLayoutHandler
from likhit.models import DocumentType, ParagraphBlock, TableBlock


class NepaliPdfConverter(DocumentConverter):
    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        del kwargs
        ext = (stream_info.extension or "").lower()
        mime = (stream_info.mimetype or "").lower()
        if ext != ".pdf" and mime != "application/pdf":
            return False

        raw = file_stream.read()
        file_stream.seek(0)
        if not raw:
            return False

        classifications = classify_fonts_from_stream(io.BytesIO(raw))
        return any(
            strategy in {"broken_cmap", "legacy_remap"}
            for strategy in classifications.values()
        )

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        del stream_info, kwargs
        raw = file_stream.read()
        if not raw:
            raise ExtractionError(
                "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
            )

        with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        try:
            raw_document = FontBasedStrategy().extract_text(str(tmp_path))
            markdown = _render_structure_aware_markdown(raw_document)
            if not markdown.strip():
                raise ExtractionError(
                    "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
                )
            return DocumentConverterResult(markdown=markdown)
        finally:
            tmp_path.unlink(missing_ok=True)


def _render_layout_preserving_markdown(raw_document: RawDocument) -> str:
    return _render_markdown_from_blocks(
        build_content_blocks(
            raw_document.fragments,
            raw_document.tables,
            _build_layout_paragraphs,
        )
    )


def _render_markdown_from_blocks(blocks: list[ParagraphBlock | TableBlock]) -> str:
    rendered: list[str] = []
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            rendered.append(block.text.strip())
        elif isinstance(block, TableBlock):
            rendered.append(table_to_plain_text(block.table))
    return "\n\n".join(part for part in rendered if part).strip()


def _render_two_column_markdown(
    raw_document: RawDocument,
    handler: TwoColumnLayoutHandler,
    ordered_fragments: list[TextFragment],
) -> str:
    blocks = build_content_blocks(
        ordered_fragments,
        raw_document.tables,
        handler._merge_fragments_to_paragraphs,
    )
    return _render_markdown_from_blocks(blocks)


def _render_structure_aware_markdown(raw_document: RawDocument) -> str:
    if detect_structure(raw_document) is not DocumentType.TWO_COLUMN_LAYOUT:
        return _render_layout_preserving_markdown(raw_document)

    handler = TwoColumnLayoutHandler()
    fragments_by_page: dict[int, list[TextFragment]] = defaultdict(list)
    for fragment in raw_document.fragments:
        if fragment.text.strip():
            fragments_by_page[fragment.page_number].append(fragment)

    ordered_fragments: list[TextFragment] = []
    for page_number in sorted(fragments_by_page):
        ordered_fragments.extend(
            handler._order_page_fragments(fragments_by_page[page_number])
        )

    reordered_document = RawDocument(
        paragraphs=raw_document.paragraphs,
        raw_text=raw_document.raw_text,
        fragments=ordered_fragments,
        tables=raw_document.tables,
    )
    return _render_two_column_markdown(reordered_document, handler, ordered_fragments)


def _build_layout_paragraphs(fragments: list[TextFragment]) -> list[str]:
    if not fragments:
        return []

    typical_line_height = min(
        median(fragment.y1 - fragment.y0 for fragment in fragments),
        24.0,
    )
    line_merge_threshold = max(1.5, typical_line_height * 0.18)
    paragraph_gap_threshold = max(8.0, typical_line_height * 0.7)

    merged_lines: list[tuple[int, float, float, str, float | None]] = []
    current_line: list[TextFragment] = []

    def flush_line() -> None:
        if not current_line:
            return
        ordered_line = sorted(current_line, key=lambda fragment: fragment.x0)
        y0 = min(fragment.y0 for fragment in ordered_line)
        y1 = max(fragment.y1 for fragment in ordered_line)
        page_number = ordered_line[0].page_number
        gap_before = next(
            (
                fragment.gap_before
                for fragment in ordered_line
                if fragment.gap_before is not None
            ),
            None,
        )
        text = " ".join(fragment.text.strip() for fragment in ordered_line if fragment.text.strip()).strip()
        if text:
            merged_lines.append((page_number, y0, y1, text, gap_before))
        current_line.clear()

    for fragment in fragments:
        if not fragment.text.strip():
            continue
        if not current_line:
            current_line.append(fragment)
            continue

        current_page = current_line[0].page_number
        current_y0 = min(item.y0 for item in current_line)
        if (
            fragment.page_number == current_page
            and abs(fragment.y0 - current_y0) <= line_merge_threshold
        ):
            current_line.append(fragment)
            continue

        flush_line()
        current_line.append(fragment)

    flush_line()

    paragraphs: list[str] = []
    current_paragraph: list[str] = []
    previous_page: int | None = None
    previous_y1: float | None = None

    def flush_paragraph() -> None:
        if current_paragraph:
            paragraphs.append("\n".join(current_paragraph).strip())
            current_paragraph.clear()

    for page_number, y0, y1, text, gap_before in merged_lines:
        starts_new_paragraph = False
        if previous_page is not None and page_number != previous_page:
            starts_new_paragraph = True
        elif gap_before is not None:
            starts_new_paragraph = gap_before >= paragraph_gap_threshold

        if starts_new_paragraph:
            flush_paragraph()

        current_paragraph.append(text)
        previous_page = page_number
        previous_y1 = y1

    flush_paragraph()
    return paragraphs
