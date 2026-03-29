"""
NepaliPdfConverter — markitdown DocumentConverter for Nepali PDFs.

Intercepts born-digital PDFs that contain Kalimati broken-CMap fonts or
legacy Nepali fonts and applies likhit's existing extraction pipeline before
emitting Markdown.
"""

from __future__ import annotations

import io
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo

from likhit.errors import ExtractionError
from likhit.font_classifier import classify_fonts_from_stream
from likhit.handlers import convert_with_detected_structure
from likhit.markdown_assembly import assemble_markdown
from likhit.nepali_pdf_repair import extract_repaired_text_blocks


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
            structured_markdown = convert_with_detected_structure(str(tmp_path))
            if structured_markdown is not None:
                return DocumentConverterResult(markdown=structured_markdown)

            repaired_blocks = extract_repaired_text_blocks(raw)
            markdown = assemble_markdown(repaired_blocks)
            if not markdown.strip():
                raise ExtractionError(
                    "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
                )
            return DocumentConverterResult(markdown=markdown)
        finally:
            tmp_path.unlink(missing_ok=True)
