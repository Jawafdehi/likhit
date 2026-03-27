"""Compatibility wrapper for public conversion entry points."""

from __future__ import annotations

import importlib
from pathlib import Path

from likhit.markitdown_integration import convert_pdf_to_markdown

_convert_module = importlib.import_module("likhit.application.convert")

_convert_with_detected_structure = _convert_module._convert_with_detected_structure
_detect_document_type = _convert_module._detect_document_type
_metadata_from_options = _convert_module._metadata_from_options
_render_markdown_without_frontmatter = (
    _convert_module._render_markdown_without_frontmatter
)
_resolve_handler = _convert_module._resolve_handler


def convert(file_path: str) -> str:
    """Backwards-compatible public convert entry point."""

    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        structured_markdown = _convert_with_detected_structure(file_path)
        if structured_markdown is not None:
            return structured_markdown
        return convert_pdf_to_markdown(file_path)
    return _convert_module.convert(file_path)


def derive_convert_output_name(source_path: str, existing: set[str]) -> str:
    return _convert_module.derive_convert_output_name(source_path, existing)


def convert_many(file_paths: list[str]) -> list[tuple[str, str]]:
    return [(file_path, convert(file_path)) for file_path in file_paths]


__all__ = [
    "convert",
    "convert_many",
    "convert_pdf_to_markdown",
    "derive_convert_output_name",
    "_convert_with_detected_structure",
    "_detect_document_type",
    "_metadata_from_options",
    "_render_markdown_without_frontmatter",
    "_resolve_handler",
]
