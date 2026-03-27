"""Application orchestration for document conversion."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Literal

from likhit.errors import ExtractionError, ValidationError
from likhit.document_types import CIAAPressReleaseHandler, KanunPatrikaHandler
from likhit.markitdown_integration import convert_pdf_to_markdown
from likhit.models import DocumentType, ExtractionResult
from likhit.markdown import MarkdownRenderer
from likhit.document_types.content_blocks import blocks_to_text
from likhit.extraction.pdf.font_based import FontBasedStrategy

_KANUN_MARKERS = (
    "नेपालकानूनपत्रिका",
    "नेपालकानुनपत्रिका",
    "निर्णय नं",
    "ने.का.प.",
)
_CIAA_MARKERS = (
    "अख्तियार दुरुपयोग अनुसन्धान आयोग",
    "प्रेस विज्ञ",
    "प्रेस मिज्ञ",
    "विषय:",
    "मिषय:",
    "आयोगकोनिर्णय",
    "अनुसन्धानबाट पुष्टि भएको",
)
OutputFormat = Literal["md", "txt"]


def _metadata_from_options(
    title: str | None,
    publication_date: str | None,
    source_url: str | None,
) -> dict[str, str | None]:
    return {
        "title": title,
        "publication_date": publication_date,
        "source_url": source_url,
    }


def _resolve_handler(
    doc_type: DocumentType,
) -> CIAAPressReleaseHandler | KanunPatrikaHandler:
    if doc_type is DocumentType.CIAA_PRESS_RELEASE:
        return CIAAPressReleaseHandler()
    if doc_type is DocumentType.KANUN_PATRIKA:
        return KanunPatrikaHandler()
    raise ValidationError(f"Unsupported document type: {doc_type.value}")


def _normalize_detection_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _detect_document_type(raw_text: str) -> DocumentType | None:
    normalized = _normalize_detection_text(raw_text)

    if any(
        _normalize_detection_text(marker) in normalized for marker in _KANUN_MARKERS
    ):
        return DocumentType.KANUN_PATRIKA
    if any(_normalize_detection_text(marker) in normalized for marker in _CIAA_MARKERS):
        return DocumentType.CIAA_PRESS_RELEASE
    return None


def _render_markdown_without_frontmatter(result: ExtractionResult) -> str:
    markdown = MarkdownRenderer().render(result).lstrip()
    if not markdown.startswith("---\n"):
        return markdown.strip()

    parts = markdown.split("\n---\n", 1)
    if len(parts) != 2:
        return markdown.strip()
    return parts[1].strip()


def _strip_markdown_frontmatter(markdown: str) -> str:
    markdown = markdown.lstrip()
    if not markdown.startswith("---\n"):
        return markdown

    parts = markdown.split("\n---\n", 1)
    if len(parts) != 2:
        return markdown
    return parts[1]


def _markdown_to_text(markdown: str) -> str:
    text = _strip_markdown_frontmatter(markdown).strip()
    if not text:
        return ""

    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"(?m)^\s*```[^\n]*\n?", "", text)
    text = re.sub(r"(?m)^\s*```$", "", text)
    text = re.sub(r"(?<!\*)\*\*(.+?)\*\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)__(.+?)__(?!_)", r"\1", text)
    text = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_(.+?)_(?!_)", r"\1", text)
    text = re.sub(r"\[(.*?)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _render_text_output(result: ExtractionResult) -> str:
    parts: list[str] = [result.title.strip()]
    for section in result.sections:
        if section.heading and section.heading.strip() != result.title.strip():
            parts.append(section.heading.strip())
        if section.blocks:
            body = blocks_to_text(section.blocks).strip()
        else:
            body = section.body.strip()
        if body:
            parts.append(body)
    return "\n\n".join(part for part in parts if part).strip()


def _render_output(result: ExtractionResult, output_format: OutputFormat) -> str:
    if output_format == "txt":
        return _render_text_output(result)
    return _render_markdown_without_frontmatter(result)


def _normalize_output_format(output_format: str) -> OutputFormat:
    normalized = output_format.lower()
    if normalized not in {"md", "txt"}:
        raise ValidationError(
            f"Unsupported output format: {output_format}. Supported formats: md, txt"
        )
    return normalized


def _convert_with_detected_structure(
    file_path: str, output_format: OutputFormat = "md"
) -> str | None:
    strategy = FontBasedStrategy()
    try:
        raw_document = strategy.extract_text(file_path)
    except ExtractionError as exc:
        if str(exc) == "No text content found in document":
            return None
        raise
    doc_type = _detect_document_type(raw_document.raw_text)
    if doc_type is None:
        return None

    handler = _resolve_handler(doc_type)
    result = handler.build_result(
        raw_document,
        _metadata_from_options(None, None, None),
    )
    return _render_output(result, output_format)


def convert(file_path: str, output_format: str = "md") -> str:
    """Convert a document (PDF, DOCX, or DOC) to Markdown.

    For PDFs, attempts structure-aware extraction first, then falls back to MarkItDown.
    For DOCX/DOC, extracts text and auto-detects document type (CIAA, Kanun Patrika, etc.).
    """
    normalized_output_format = _normalize_output_format(output_format)
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in {".docx", ".doc"}:
        temp_handler = CIAAPressReleaseHandler()
        strategy = temp_handler.get_extraction_strategy_for_file(file_path)
        raw_document = strategy.extract_text(file_path)

        doc_type = _detect_document_type(raw_document.raw_text)

        if doc_type is not None:
            handler = _resolve_handler(doc_type)
            try:
                strategy = handler.get_extraction_strategy_for_file(file_path)
                raw_document = strategy.extract_text(file_path)
            except ExtractionError:
                return raw_document.raw_text

            result = handler.build_result(
                raw_document,
                _metadata_from_options(None, None, None),
            )
            return _render_output(result, normalized_output_format)
        return raw_document.raw_text

    if suffix == ".pdf":
        structured_output = _convert_with_detected_structure(
            file_path, normalized_output_format
        )
        if structured_output is not None:
            return structured_output
        markdown = convert_pdf_to_markdown(file_path)
        if normalized_output_format == "txt":
            return _markdown_to_text(markdown)
        return markdown

    raise ValidationError(
        f"Unsupported input format: {suffix}. Supported formats: .pdf, .docx, .doc"
    )


def derive_convert_output_name(
    source_path: str, existing: set[str], output_format: str = "md"
) -> str:
    normalized_output_format = _normalize_output_format(output_format)
    base_name = Path(source_path).stem or "document"
    candidate = f"{base_name}.{normalized_output_format}"
    counter = 2
    while candidate in existing:
        candidate = f"{base_name}-{counter}.{normalized_output_format}"
        counter += 1
    existing.add(candidate)
    return candidate


def convert_many(
    file_paths: list[str], output_format: str = "md"
) -> list[tuple[str, str]]:
    return [
        (file_path, convert(file_path, output_format=output_format))
        for file_path in file_paths
    ]
