"""
NepaliPdfConverter — markitdown DocumentConverter for Nepali PDFs.

Intercepts born-digital PDFs that contain Kalimati broken-CMap fonts or
legacy Nepali fonts and applies likhit's existing extraction pipeline before
emitting Markdown.
"""

from __future__ import annotations

import io
from collections import defaultdict
import logging
import os
from pathlib import Path
import re
from statistics import median
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo
from markitdown.converters._pdf_converter import PdfConverter
from markitdown_ocr import LLMVisionOCRService
from markitdown_ocr import PdfConverterWithOCR

from likhit.errors import ExtractionError
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.font_based import FontBasedStrategy
from likhit.font_classifier import classify_fonts_from_stream
from likhit.handlers.content_blocks import build_content_blocks, table_to_plain_text
from likhit.handlers.structure_detection import detect_structure
from likhit.handlers.two_column_layout import TwoColumnLayoutHandler
from likhit.models import DocumentType, ParagraphBlock, TableBlock
from likhit.pdf_page_analysis import pdf_likely_needs_ocr

logger = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"\S+")
_DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097F]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_SUSPICIOUS_LATIN_TOKEN_PATTERN = re.compile(
    r"""[\\\[\]\{\}\$^&*_+=<>]|[A-Za-z]\d|\d[A-Za-z]"""
)


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

        return True

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        del kwargs
        raw = file_stream.read()
        if not raw:
            raise ExtractionError(
                "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
            )

        classifications = classify_fonts_from_stream(io.BytesIO(raw))
        if _has_known_nepali_repair_font(classifications):
            logger.info(
                "PDF converter: known Nepali repair fonts detected; using likhit extraction directly."
            )
            return _convert_with_likhit(raw)

        logger.info("PDF converter: running default MarkItDown PDF extraction first.")
        default_result = _run_default_pdf_converter(raw, stream_info)
        candidates = [default_result]

        needs_ocr = pdf_likely_needs_ocr(raw)
        if needs_ocr:
            logger.info(
                "PDF converter: page analysis suggests OCR is needed because the PDF looks image-dominant with a suspicious text layer."
            )
            ocr_result = _run_ocr_pdf_converter(raw, stream_info)
            if ocr_result is not None:
                logger.info("PDF converter: OCR candidate extracted successfully.")
                candidates.append(ocr_result)
            else:
                logger.warning(
                    "PDF converter: OCR appears necessary, but OCR is not configured. Set OPENAI_API_KEY and MARKITDOWN_OCR_MODEL to enable markitdown-ocr."
                )

        if _default_pdf_result_needs_likhit(default_result.markdown):
            logger.info(
                "PDF converter: default extraction looks suspicious for Nepali text; retrying with likhit extraction."
            )
            likhit_result = _try_convert_with_likhit(raw)
            if likhit_result is not None:
                logger.info("PDF converter: likhit re-extraction produced a candidate.")
                candidates.append(likhit_result)
            else:
                logger.warning(
                    "PDF converter: likhit re-extraction did not produce usable text; keeping the existing candidates."
                )
        else:
            logger.info(
                "PDF converter: default MarkItDown extraction looks usable; no Nepali re-extraction needed."
            )

        if len(candidates) == 1:
            logger.info("PDF converter: returning the only available extraction result.")
            return default_result

        scored_candidates = [
            (result, _markdown_quality_score(result.markdown)) for result in candidates
        ]
        best_result, best_score = max(scored_candidates, key=lambda item: item[1])
        logger.info(
            "PDF converter: selected best candidate after comparison (candidates=%d, score=%d).",
            len(scored_candidates),
            best_score,
        )
        return best_result


def _has_known_nepali_repair_font(classifications: dict[str, str]) -> bool:
    return any(
        strategy in {"broken_cmap", "legacy_remap"}
        for strategy in classifications.values()
    )


def _run_default_pdf_converter(
    raw: bytes,
    stream_info: StreamInfo,
) -> DocumentConverterResult:
    converter = PdfConverter()
    return converter.convert(io.BytesIO(raw), stream_info)


def _run_ocr_pdf_converter(
    raw: bytes,
    stream_info: StreamInfo,
) -> DocumentConverterResult | None:
    ocr_service = _build_ocr_service_from_env()
    if ocr_service is None:
        return None

    converter = PdfConverterWithOCR(ocr_service=ocr_service)
    return converter.convert(io.BytesIO(raw), stream_info)


def _build_ocr_service_from_env() -> LLMVisionOCRService | None:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("MARKITDOWN_OCR_MODEL") or os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        return None

    from openai import OpenAI

    base_url = os.getenv("OPENAI_BASE_URL")
    client_kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    prompt = os.getenv("MARKITDOWN_OCR_PROMPT")
    return LLMVisionOCRService(client=client, model=model, default_prompt=prompt)


def _convert_with_likhit(raw: bytes) -> DocumentConverterResult:
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


def _try_convert_with_likhit(raw: bytes) -> DocumentConverterResult | None:
    try:
        return _convert_with_likhit(raw)
    except ExtractionError as exc:
        logger.debug("PDF converter: likhit extraction failed: %s", exc)
        return None


def _default_pdf_result_needs_likhit(markdown: str) -> bool:
    if not markdown.strip():
        return True

    tokens = _TOKEN_PATTERN.findall(markdown)
    if not tokens:
        return True

    devanagari_chars = len(_DEVANAGARI_PATTERN.findall(markdown))
    latin_tokens = [token for token in tokens if _LATIN_PATTERN.search(token)]
    if devanagari_chars >= 20 or len(latin_tokens) < 12:
        return False

    suspicious_tokens = [
        token for token in latin_tokens if _SUSPICIOUS_LATIN_TOKEN_PATTERN.search(token)
    ]
    vowel_poor_tokens = [
        token
        for token in latin_tokens
        if _is_vowel_poor_latin_token(token)
    ]
    pipe_heavy_lines = sum(
        1 for line in markdown.splitlines() if line.count("|") >= 2
    )

    suspicious_ratio = len(suspicious_tokens) / len(latin_tokens)
    vowel_poor_ratio = len(vowel_poor_tokens) / len(latin_tokens)
    return (
        suspicious_ratio >= 0.12
        or (suspicious_ratio >= 0.06 and vowel_poor_ratio >= 0.45)
        or (pipe_heavy_lines >= 4 and suspicious_ratio >= 0.05)
    )


def _is_vowel_poor_latin_token(token: str) -> bool:
    letters = [char for char in token if char.isalpha()]
    if len(letters) < 4:
        return False
    vowels = sum(char in "aeiouAEIOU" for char in letters)
    return vowels / len(letters) < 0.2


def _markdown_quality_score(markdown: str) -> int:
    tokens = _TOKEN_PATTERN.findall(markdown)
    latin_tokens = [token for token in tokens if _LATIN_PATTERN.search(token)]
    suspicious_tokens = [
        token for token in latin_tokens if _SUSPICIOUS_LATIN_TOKEN_PATTERN.search(token)
    ]
    vowel_poor_tokens = [
        token for token in latin_tokens if _is_vowel_poor_latin_token(token)
    ]
    pipe_heavy_lines = sum(1 for line in markdown.splitlines() if line.count("|") >= 2)
    devanagari_chars = len(_DEVANAGARI_PATTERN.findall(markdown))
    return (
        devanagari_chars * 3
        + len(tokens)
        - len(suspicious_tokens) * 8
        - len(vowel_poor_tokens) * 3
        - pipe_heavy_lines * 4
        - markdown.count("\ufffd") * 12
    )


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
