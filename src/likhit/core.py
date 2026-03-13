"""Public extraction entry points."""

from __future__ import annotations

import re
from pathlib import Path

from likhit.errors import ValidationError
from likhit.handlers import CIAAPressReleaseHandler, KanunPatrikaHandler
from likhit.models import DocumentType, ExtractionResult
from likhit.renderers import MarkdownRenderer

DOC_TYPE_PREFIXES = {
    DocumentType.CIAA_PRESS_RELEASE: "pressrelease",
    DocumentType.KANUN_PATRIKA: "kanunpatrika",
}


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


def _resolve_handler(doc_type: DocumentType) -> CIAAPressReleaseHandler | KanunPatrikaHandler:
    if doc_type is DocumentType.CIAA_PRESS_RELEASE:
        return CIAAPressReleaseHandler()
    if doc_type is DocumentType.KANUN_PATRIKA:
        return KanunPatrikaHandler()
    raise ValidationError(f"Unsupported document type: {doc_type.value}")


def extract(
    file_path: str,
    doc_type: str | DocumentType,
    *,
    title: str | None = None,
    publication_date: str | None = None,
    source_url: str | None = None,
    pages: str | None = None,
) -> ExtractionResult:
    resolved_doc_type = (
        doc_type if isinstance(doc_type, DocumentType) else DocumentType.parse(doc_type)
    )
    handler = _resolve_handler(resolved_doc_type)
    strategy = handler.get_extraction_strategy()
    raw_document = strategy.extract_text(file_path, pages=pages)
    return handler.build_result(
        raw_document,
        _metadata_from_options(title, publication_date, source_url),
    )


def render_markdown(result: ExtractionResult) -> str:
    return MarkdownRenderer().render(result)


def _slugify_for_filename(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:48] or "document"


def derive_output_name(
    result: ExtractionResult, source_path: str, existing: set[str]
) -> str:
    base_name = DOC_TYPE_PREFIXES.get(result.doc_type, "document")
    if result.publication_date:
        base_name = f"{base_name}-{result.publication_date}"
    else:
        base_name = Path(source_path).stem

    candidate = f"{base_name}.md"
    counter = 2
    title_slug = _slugify_for_filename(result.title)
    while candidate in existing:
        candidate = f"{base_name}-{title_slug}-{counter}.md"
        counter += 1
    existing.add(candidate)
    return candidate


def extract_many(
    file_paths: list[str],
    doc_type: str | DocumentType,
    *,
    title: str | None = None,
    publication_date: str | None = None,
    source_url: str | None = None,
    pages: str | None = None,
) -> list[tuple[str, ExtractionResult]]:
    return [
        (
            file_path,
            extract(
                file_path,
                doc_type,
                title=title,
                publication_date=publication_date,
                source_url=source_url,
                pages=pages,
            ),
        )
        for file_path in file_paths
    ]
