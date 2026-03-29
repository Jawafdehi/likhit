"""
NepaliDocxConverter — markitdown DocumentConverter for Nepali DOCX/DOC files.

Always intercepts .docx and .doc files and routes them through likhit's
existing extraction pipeline.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo

from likhit.handlers import convert_with_detected_structure

_DOCX_EXTENSIONS = {".docx", ".doc"}
_DOCX_MIMETYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
class NepaliDocxConverter(DocumentConverter):
    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        del file_stream, kwargs
        ext = (stream_info.extension or "").lower()
        mime = (stream_info.mimetype or "").lower()
        return ext in _DOCX_EXTENSIONS or mime in _DOCX_MIMETYPES

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        del kwargs
        ext = (stream_info.extension or "").lower()
        suffix = ext if ext in _DOCX_EXTENSIONS else ".docx"
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
