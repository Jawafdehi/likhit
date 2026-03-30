"""
NepaliPdfConverter — markitdown DocumentConverter for Nepali PDFs.

Intercepts born-digital PDFs that contain Kalimati broken-CMap fonts or
legacy Nepali fonts and applies likhit's existing extraction pipeline before
emitting Markdown.
"""

from __future__ import annotations

import io
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
from likhit.models import ParagraphBlock, TableBlock


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
            markdown = _render_layout_preserving_markdown(raw_document)
            if not markdown.strip():
                raise ExtractionError(
                    "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
                )
            return DocumentConverterResult(markdown=markdown)
        finally:
            tmp_path.unlink(missing_ok=True)


def _render_layout_preserving_markdown(raw_document: RawDocument) -> str:
    blocks = build_content_blocks(
        raw_document.fragments,
        raw_document.tables,
        _build_layout_paragraphs,
    )
    rendered: list[str] = []
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            rendered.append(block.text.strip())
        elif isinstance(block, TableBlock):
            rendered.append(table_to_plain_text(block.table))
    return "\n\n".join(part for part in rendered if part).strip()


def _build_layout_paragraphs(fragments: list[TextFragment]) -> list[str]:
    if not fragments:
        return []

    line_heights = [fragment.y1 - fragment.y0 for fragment in fragments]
    typical_line_height = median(line_heights) if line_heights else 12.0
    paragraph_gap_threshold = max(8.0, typical_line_height * 0.65)

    paragraphs: list[str] = []
    current_lines: list[str] = []
    previous_page: int | None = None

    def flush() -> None:
        if current_lines:
            paragraphs.append("\n".join(current_lines).strip())
            current_lines.clear()

    for fragment in fragments:
        text = fragment.text.strip()
        if not text:
            continue

        starts_new_paragraph = (
            previous_page is not None
            and fragment.page_number != previous_page
        ) or (
            fragment.gap_before is not None
            and fragment.gap_before >= paragraph_gap_threshold
        )
        if starts_new_paragraph:
            flush()

        current_lines.append(text)
        previous_page = fragment.page_number

    flush()
    return paragraphs
