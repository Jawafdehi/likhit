"""
LegacyWordConverter — MarkItDown DocumentConverter for legacy `.doc` files.

MarkItDown already handles `.docx`, so likhit only intercepts legacy `.doc`
inputs where it adds support that MarkItDown does not provide.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo

from likhit.handlers import convert_with_detected_structure

_DOC_EXTENSIONS = {".doc"}
_DOC_MIMETYPES = {"application/msword"}


class LegacyWordConverter(DocumentConverter):
    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        del file_stream, kwargs
        ext = (stream_info.extension or "").lower()
        mime = (stream_info.mimetype or "").lower()
        if ext:
            return ext in _DOC_EXTENSIONS
        return mime in _DOC_MIMETYPES

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        del kwargs
        ext = (stream_info.extension or "").lower()
        suffix = ext if ext in _DOC_EXTENSIONS else ".doc"
        raw = file_stream.read()

        with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        try:
            markdown = _convert_word_document(str(tmp_path))
            return DocumentConverterResult(markdown=markdown)
        finally:
            tmp_path.unlink(missing_ok=True)


def _convert_word_document(file_path: str) -> str:
    structured_markdown = convert_with_detected_structure(file_path)
    if structured_markdown is not None:
        return structured_markdown

    from likhit.extractors.docx_based import DocxBasedStrategy

    return DocxBasedStrategy().extract_text(file_path).raw_text


# Backwards-compatible import path for earlier releases.
NepaliDocxConverter = LegacyWordConverter
