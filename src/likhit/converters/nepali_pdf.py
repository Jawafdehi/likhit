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

import fitz
from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo
from markitdown.converters import PdfConverter
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
from likhit.renderers.markdown import _caption_key, _render_table

logger = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"\S+")
_DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097F]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_CID_GARBAGE_PATTERN = re.compile(r"\(cid:\d+\)")
_SUSPICIOUS_LATIN_TOKEN_PATTERN = re.compile(
    r"""[\\\[\]\{\}\$^&*_+=<>]|[A-Za-z]\d|\d[A-Za-z]"""
)
_OCR_SERIAL_PATTERN = re.compile(r"^\s*([०-९0-9]{1,2}[.)।])\s+(.*\S)\s*$")
_GEMINI_OPENAI_COMPAT_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai/"
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
            likhit_result = _try_convert_with_likhit(raw)
            if likhit_result is not None:
                return likhit_result
            logger.warning(
                "PDF converter: likhit extraction failed after repair-font detection; falling back to default extraction."
            )

        logger.info("PDF converter: running default MarkItDown PDF extraction first.")
        default_result = _run_default_pdf_converter(raw, stream_info)
        candidates = [default_result]

        needs_ocr = pdf_likely_needs_ocr(raw)
        if needs_ocr:
            logger.info(
                "PDF converter: page analysis suggests OCR is needed because the PDF looks image-dominant with a suspicious text layer."
            )
            ocr_result = _run_ocr_pdf_converter(
                raw,
                stream_info,
                force_full_page_ocr=True,
            )
            if ocr_result is not None:
                logger.info("PDF converter: OCR candidate extracted successfully.")
                candidates.append(ocr_result)
            else:
                logger.warning(
                    "PDF converter: OCR appears necessary, but OCR is not configured. Set OPENAI_API_KEY or GEMINI_API_KEY, plus MARKITDOWN_OCR_MODEL, to enable markitdown-ocr."
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
            logger.info(
                "PDF converter: returning the only available extraction result."
            )
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
    *,
    force_full_page_ocr: bool = False,
) -> DocumentConverterResult | None:
    ocr_service = _build_ocr_service_from_env()
    if ocr_service is None:
        return None

    if force_full_page_ocr:
        return _run_full_page_ocr(raw, ocr_service)

    converter = PdfConverterWithOCR(ocr_service=ocr_service)
    return converter.convert(io.BytesIO(raw), stream_info)


def _run_full_page_ocr(
    raw: bytes,
    ocr_service: LLMVisionOCRService,
) -> DocumentConverterResult:
    markdown_parts: list[str] = []
    doc = fitz.open(stream=raw, filetype="pdf")

    try:
        for page_number in range(1, doc.page_count + 1):
            page = doc[page_number - 1]
            matrix = fitz.Matrix(300 / 72, 300 / 72)
            pixmap = page.get_pixmap(matrix=matrix)
            image_stream = io.BytesIO(pixmap.tobytes("png"))
            image_stream.seek(0)

            ocr_result = ocr_service.extract_text(image_stream)
            extracted_text = ocr_result.text.strip()
            formatted_text = _format_full_page_ocr_text(extracted_text)
            if extracted_text:
                markdown_parts.append(formatted_text)
            else:
                markdown_parts.append("")
            markdown_parts.append("")
    finally:
        doc.close()

    return DocumentConverterResult(markdown="\n".join(markdown_parts).strip())


def _format_full_page_ocr_text(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return text.strip()
    if _looks_like_markdown_table(lines):
        return "\n".join(lines).strip()

    formatted_table = _try_format_ocr_decision_table(lines)
    if formatted_table is not None:
        return formatted_table
    return "\n".join(lines).strip()


def _try_format_ocr_decision_table(lines: list[str]) -> str | None:
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "क्र." in line and "मन्त्रालय" in line
        ),
        None,
    )
    first_row_index = next(
        (index for index, line in enumerate(lines) if _OCR_SERIAL_PATTERN.match(line)),
        None,
    )
    if first_row_index is None:
        return None

    title_lines = [
        line.strip()
        for line in lines[: first_row_index if header_index is None else header_index]
        if line.strip()
    ]
    data_lines = lines[first_row_index:]

    split_rows = _parse_split_ocr_rows(data_lines)
    if split_rows and len(split_rows) >= 3:
        return _render_ocr_decision_table(title_lines, split_rows)

    inline_rows = _parse_inline_ocr_rows(data_lines)
    if len(inline_rows) >= 3:
        return _render_ocr_decision_table(title_lines, inline_rows)

    return None


def _looks_like_markdown_table(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    header = lines[0].strip()
    divider = lines[1].strip()
    return header.startswith("|") and divider.startswith("|---")


def _parse_inline_ocr_rows(
    lines: list[str],
) -> list[tuple[str, str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in lines:
        if not line.strip():
            continue

        serial_match = _OCR_SERIAL_PATTERN.match(line)
        if serial_match:
            if current is not None and current["decision"]:
                rows.append(current)
            serial = serial_match.group(1)
            ministry, decision = _split_ocr_columns(serial_match.group(2))
            current = {
                "serial": serial,
                "ministry": ministry,
                "decision": decision,
            }
            continue

        if current is None:
            continue

        ministry_part, decision_part = _split_ocr_columns(line.strip())
        if ministry_part and decision_part:
            current["ministry"] = _append_text(current["ministry"], ministry_part)
            current["decision"] = _append_text(current["decision"], decision_part)
            continue

        if decision_part:
            current["decision"] = _append_text(current["decision"], decision_part)
            continue

        if _looks_like_decision_text(line.strip()):
            current["decision"] = _append_text(current["decision"], line.strip())
        else:
            current["ministry"] = _append_text(current["ministry"], line.strip())

    if current is not None and current["decision"]:
        rows.append(current)

    if not rows:
        return []
    if sum(1 for row in rows if row["decision"]) < max(3, len(rows) // 2):
        return []

    return [
        (row["serial"], row["ministry"], row["decision"])
        for row in rows
        if row["serial"] and row["ministry"] and row["decision"]
    ]


def _parse_split_ocr_rows(
    lines: list[str],
) -> list[tuple[str, str, str]]:
    ministries: list[tuple[str, str]] = []
    current_serial: str | None = None
    current_ministry: str = ""
    remainder_start: int | None = None
    blank_streak = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            blank_streak += 1
            continue

        serial_match = _OCR_SERIAL_PATTERN.match(line)
        if serial_match:
            if current_serial is not None:
                ministries.append((current_serial, current_ministry.strip()))
            ministry_part, decision_part = _split_ocr_columns(serial_match.group(2))
            if decision_part:
                return []
            current_serial = serial_match.group(1)
            current_ministry = ministry_part
            blank_streak = 0
            continue

        if current_serial is None:
            return []

        combined_tail = _split_ministry_decision_tail(stripped)
        if combined_tail is not None:
            ministry_tail, decision_start = combined_tail
            current_ministry = _append_text(current_ministry, ministry_tail)
            ministries.append((current_serial, current_ministry.strip()))
            remainder_lines = [decision_start, *lines[index + 1 :]]
            decisions = _collect_ocr_paragraphs(remainder_lines)
            if len(decisions) != len(ministries):
                return []
            return [
                (serial, ministry, decision)
                for (serial, ministry), decision in zip(
                    ministries,
                    decisions,
                    strict=False,
                )
                if serial and ministry and decision
            ]

        if blank_streak and _looks_like_decision_text(stripped):
            ministries.append((current_serial, current_ministry.strip()))
            remainder_start = index
            break

        current_ministry = _append_text(current_ministry, stripped)
        blank_streak = 0

    if current_serial is not None and remainder_start is None:
        ministries.append((current_serial, current_ministry.strip()))

    if remainder_start is None:
        return []

    decisions = _collect_ocr_paragraphs(lines[remainder_start:])
    if len(decisions) != len(ministries):
        return []

    return [
        (serial, ministry, decision)
        for (serial, ministry), decision in zip(ministries, decisions, strict=False)
        if serial and ministry and decision
    ]


def _collect_ocr_paragraphs(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        current.append(stripped)

    if current:
        paragraphs.append(" ".join(current).strip())
    return paragraphs


def _split_ocr_columns(text: str) -> tuple[str, str]:
    parts = re.split(r"\s{2,}", text.strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()

    if _looks_like_decision_text(text.strip()):
        return "", text.strip()
    return text.strip(), ""


def _split_ministry_decision_tail(text: str) -> tuple[str, str] | None:
    marker = "मन्त्रालय"
    if marker not in text:
        return None

    boundary = text.rfind(marker) + len(marker)
    ministry = text[:boundary].strip()
    decision = text[boundary:].strip()
    if not ministry or not decision:
        return None
    if len(decision) < 12 and not _looks_like_decision_text(decision):
        return None
    return ministry, decision


def _looks_like_decision_text(text: str) -> bool:
    if len(text) >= 48 and "मन्त्रालय" not in text:
        return True

    decision_markers = (
        "गर्ने",
        "दिने",
        "तोक्ने",
        "स्वीकृति",
        "सहमति",
        "नियुक्त",
        "मनोनयन",
        "भाग लिन",
        "खारेज",
        "प्रदान",
    )
    return any(marker in text for marker in decision_markers)


def _append_text(existing: str, new_text: str) -> str:
    new_text = new_text.strip()
    if not new_text:
        return existing.strip()
    if not existing:
        return new_text
    return f"{existing.strip()} {new_text}"


def _render_ocr_decision_table(
    title_lines: list[str],
    rows: list[tuple[str, str, str]],
) -> str:
    parts = title_lines[:]
    if parts:
        parts.append("")
    parts.extend(
        [
            "| क्र.स. | मन्त्रालय | निर्णयको संक्षिप्त व्यहोरा |",
            "|---|---|---|",
        ]
    )
    for serial, ministry, decision in rows:
        clean_serial = serial.replace(")", ".").strip()
        parts.append(f"| {clean_serial} | {ministry.strip()} | {decision.strip()} |")
    return "\n".join(parts).strip()


def _build_ocr_service_from_env() -> LLMVisionOCRService | None:
    api_key, model, base_url = _resolve_ocr_env()
    if not api_key or not model:
        return None

    from openai import OpenAI

    client_kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    prompt = os.getenv("MARKITDOWN_OCR_PROMPT")
    return LLMVisionOCRService(client=client, model=model, default_prompt=prompt)


def _resolve_ocr_env() -> tuple[str | None, str | None, str | None]:
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    api_key = openai_api_key or gemini_api_key
    model = (
        os.getenv("MARKITDOWN_OCR_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("GEMINI_MODEL")
    )
    base_url = os.getenv("OPENAI_BASE_URL")
    if not base_url and gemini_api_key and not openai_api_key:
        base_url = _GEMINI_OPENAI_COMPAT_BASE_URL
    return api_key, model, base_url


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
    except Exception as exc:
        logger.debug("PDF converter: likhit extraction failed: %s", exc)
        return None


def _default_pdf_result_needs_likhit(markdown: str) -> bool:
    if not markdown.strip():
        return True

    cid_garbage_count = len(_CID_GARBAGE_PATTERN.findall(markdown))
    if cid_garbage_count >= 2:
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
        token for token in latin_tokens if _is_vowel_poor_latin_token(token)
    ]
    pipe_heavy_lines = sum(1 for line in markdown.splitlines() if line.count("|") >= 2)

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
    cid_garbage_count = len(_CID_GARBAGE_PATTERN.findall(markdown))
    return (
        devanagari_chars * 3
        + len(tokens)
        - len(suspicious_tokens) * 8
        - len(vowel_poor_tokens) * 3
        - pipe_heavy_lines * 4
        - cid_garbage_count * 12
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
    previous_table_key: str | None = None
    for index, block in enumerate(blocks):
        if isinstance(block, ParagraphBlock):
            if _looks_like_page_furniture(block.text) and (
                (index > 0 and isinstance(blocks[index - 1], TableBlock))
                or (index + 1 < len(blocks) and isinstance(blocks[index + 1], TableBlock))
            ):
                continue
            rendered.append(_render_paragraph_markdown(block.text))
            previous_table_key = None
        elif isinstance(block, TableBlock):
            include_caption = True
            if (
                index > 0
                and isinstance(blocks[index - 1], ParagraphBlock)
                and block.table.caption
                and _paragraph_ends_with_caption(
                    blocks[index - 1].text,
                    block.table.caption,
                )
            ):
                include_caption = False
            rendered_table, previous_table_key = _render_table(
                block.table,
                include_caption=include_caption,
                continuation_key=previous_table_key,
            )
            if rendered_table.strip():
                rendered.append(f"```text\n{rendered_table}\n```")
    return "\n\n".join(part for part in rendered if part).strip()


def _looks_like_page_furniture(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    stripped = text.strip()
    return (
        bool(re.match(r"^\d+\s*परिच्छेद", text))
        or "वार्षिकप्रतिवेदन" in compact
        or (stripped.isdigit() and len(stripped) <= 3)
    )


def _render_paragraph_markdown(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return "  \n".join(line for line in lines if line.strip()).strip()


def _paragraph_ends_with_caption(text: str, caption: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return _caption_key(lines[-1]) == _caption_key(caption)


def _render_two_column_markdown(
    raw_document: RawDocument,
    handler: TwoColumnLayoutHandler,
    ordered_fragments: list[TextFragment],
) -> str:
    del ordered_fragments
    blocks = handler._build_blocks(raw_document)
    return _render_markdown_from_blocks(blocks)


def _render_structure_aware_markdown(raw_document: RawDocument) -> str:
    if detect_structure(raw_document) is not DocumentType.TWO_COLUMN_LAYOUT:
        return _render_layout_preserving_markdown(raw_document)

    handler = TwoColumnLayoutHandler()
    return _render_two_column_markdown(raw_document, handler, raw_document.fragments)


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
        text = " ".join(
            fragment.text.strip() for fragment in ordered_line if fragment.text.strip()
        ).strip()
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

    def flush_paragraph() -> None:
        if current_paragraph:
            paragraphs.append("\n".join(current_paragraph).strip())
            current_paragraph.clear()

    for page_number, y0, _y1, text, gap_before in merged_lines:
        starts_new_paragraph = False
        if previous_page is not None and page_number != previous_page:
            starts_new_paragraph = True
        elif gap_before is not None:
            starts_new_paragraph = gap_before >= paragraph_gap_threshold

        if starts_new_paragraph:
            flush_paragraph()

        current_paragraph.append(text)
        previous_page = page_number

    flush_paragraph()
    return paragraphs
