"""
NepaliPdfConverter — markitdown DocumentConverter for Nepali PDFs.

Intercepts born-digital PDFs that contain Kalimati broken-CMap fonts or
legacy Nepali fonts and applies likhit's existing extraction pipeline before
emitting Markdown.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import io
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

from likhit.devanagari import (
    DOUBLED_MATRA_PATTERN,
    ORPHAN_MATRA_PATTERN,
    VIRAMA_MATRA_PATTERN,
)
from likhit.errors import ExtractionError, ScannedPdfError, ValidationError
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.font_based import FontBasedStrategy, parse_page_range
from likhit.extractors.font_classifier import classify_ocr_page
from likhit.extractors.numeric_boundaries import (
    NumericBoundaryEvidence,
    collect_document_numeric_boundary_evidence,
    repair_markdown_numeric_boundaries,
    requires_geometry_aware_candidate,
)
from likhit.font_classifier import classify_fonts_from_stream
from likhit.handlers.content_blocks import build_content_blocks
from likhit.handlers.two_column_layout import TwoColumnLayoutHandler
from likhit.models import ParagraphBlock, TableBlock
from likhit.pdf_page_analysis import (
    _is_vowel_poor_latin_token,
)

# Imported rather than redefined. These used to exist twice -- once here and once in
# the renderer, byte-identical -- and both copies decided the same question about the
# same block on either side of the extractor/renderer seam. A fix applied to one would
# have been a silent divergence, and the pending fix to _looks_like_page_furniture
# would have had to be landed twice. That fix is strip_page_furniture_lines, and it
# arrives through this same import.
#
# 🛑 This comment used to describe that pending fix as "a length bound, so a
# 216-character paragraph that merely mentions a running-head phrase is not
# discarded". Measurement REFUTES a length bound: over all 13 CIAA annual reports the
# smallest wrongly-dropped block is 82 characters and the largest CORRECTLY dropped
# one is 137, so no threshold separates them. The fix is per-LINE stripping instead.
# Recorded rather than quietly deleted, because the wrong approach was written down
# confidently and would otherwise be proposed again.
from likhit.renderers.markdown import (
    PAGE_ANCHOR_PATTERN,
    _looks_like_page_furniture,
    _paragraph_ends_with_caption,
    _render_paragraph_markdown,
    page_anchor,
    render_table_page_chunks,
    strip_page_anchors,
    strip_page_furniture_lines,
)

logger = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"\S+")
_DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097F]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_CID_GARBAGE_PATTERN = re.compile(r"\(cid:\d+\)")
_SUSPICIOUS_LATIN_TOKEN_PATTERN = re.compile(
    r"""[\\\[\]\{\}\$^&*_+=<>]|[A-Za-z]\d|\d[A-Za-z]"""
)
_OCR_SERIAL_PATTERN = re.compile(r"^\s*([०-९0-9]{1,2}[.)।])\s+(.*\S)\s*$")
_TABLE_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-+:?$")
_UNESCAPED_PIPE_PATTERN = re.compile(r"(?<!\\)\|")
_PAGE_SPEC_PATTERN = re.compile(r"^\d+(?:-\d+)?$")
_MAX_REASONABLE_WHITESPACE_RATIO = 0.35
_MAX_REASONABLE_SINGLE_TOKEN_RATIO = 0.35
_EXCESS_SINGLE_TOKEN_PENALTY = 6
_MATRA_DAMAGE_PENALTY = 8
_OCR_DEFAULT_DPI = 300
_OCR_MAX_RENDER_PIXELS = 40_000_000
_OCR_MAX_ENCODED_BYTES = 5 * 1024 * 1024
_OCR_MAX_RENDER_ATTEMPTS = 4
_OCR_RENDER_SCALE_STEP = 0.75
# The rest of _markdown_quality_score's weights. These were inline literals in one
# arithmetic expression alongside the two named above, which made the guard in
# tests/test_tuning_constants.py cover half of one function's tuning surface --
# and worse, the two names above state their derivations RELATIVE to the U+FFFD/NUL
# rate below ("half the U+FFFD/NUL rate of 12"), so the pinned values were anchored
# to an unpinned one. Measured: changing that rate from 12 to 1 left the entire
# suite green at 875 passed, the full baseline.
_DEVANAGARI_CHAR_CREDIT = 3
_SUSPICIOUS_TOKEN_PENALTY = 8
_VOWEL_POOR_TOKEN_PENALTY = 3
_PIPE_HEAVY_LINE_PENALTY = 4
_CID_GARBAGE_PENALTY = 12
_UNDECODED_GLYPH_PENALTY = 12
_GEMINI_OPENAI_COMPAT_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai/"
)

NEEDS_OCR_MARKER_PATTERN = re.compile(
    r"<!-- likhit:needs-ocr pages=([0-9,]+) reason=([a-z-]+) -->"
)
CONVERSION_ERROR_MARKER_PATTERN = re.compile(r"<!-- likhit:error kind=([a-z-]+) -->")


@dataclass(frozen=True)
class _PreparedPdf:
    raw: bytes
    page_count: int
    ocr_pages: dict[int, str]
    source_page_numbers: tuple[int, ...]


@dataclass(frozen=True)
class _PageOcrResult:
    text_by_page: dict[int, str]
    failed_pages: tuple[int, ...]


class NepaliPdfConverter(DocumentConverter):
    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        ext = (stream_info.extension or "").lower()
        mime = (stream_info.mimetype or "").lower()
        if ext != ".pdf" and mime != "application/pdf":
            return False

        raw = file_stream.read()
        file_stream.seek(0)
        return bool(raw)

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        pages_arg = kwargs.pop("pages", None)
        llm_client = kwargs.pop("llm_client", None)
        llm_model = kwargs.pop("llm_model", None)
        llm_prompt = kwargs.pop("llm_prompt", None)
        raw = file_stream.read()
        if not raw:
            raise ExtractionError(
                "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
            )

        try:
            prepared = _prepare_pdf(raw, pages_arg)
        except ValidationError as exc:
            return _conversion_error_result("invalid-pages", exc)
        except ExtractionError as exc:
            return _conversion_error_result("password-protected", exc)

        if kwargs:
            logger.debug(
                "PDF converter: ignoring unsupported convert kwargs: %s",
                ", ".join(sorted(kwargs)),
            )

        raw = prepared.raw
        ocr_service = _build_ocr_service(
            llm_client=llm_client,
            llm_model=llm_model,
            llm_prompt=llm_prompt,
        )

        numeric_evidence = _try_collect_numeric_boundary_evidence(raw)
        prefetched_likhit: DocumentConverterResult | None = None
        # Set by EITHER likhit attempt below, and CLEARED by a likhit attempt that
        # succeeds.
        #
        # 🛑 Clearing is not tidiness, it prevents a false positive that a mutation
        # test caught: a MemoryError can be transient, so the first attempt can fail
        # and the second succeed. Keeping the reason then stamps a transcript likhit
        # actually produced -- measured, with all 3 page anchors present. A marker on
        # a healthy transcript is worse than no marker, because a corpus sweep would
        # quarantine good documents on it.
        #
        # If likhit fails BOTH times the reason survives, and if the second attempt is
        # never reached the first attempt's reason survives, because neither
        # assignment runs.
        degraded_reason: str | None = None
        classifications = classify_fonts_from_stream(io.BytesIO(raw))
        if _has_known_nepali_repair_font(classifications):
            logger.info(
                "PDF converter: known Nepali repair fonts detected; using geometry-aware likhit extraction directly."
            )
            likhit_result, likhit_needs_ocr, reason = _try_convert_with_likhit(
                raw,
                ocr_pages=prepared.ocr_pages,
            )
            degraded_reason = (
                None if likhit_result is not None else (degraded_reason or reason)
            )
            if likhit_result is not None and not likhit_needs_ocr:
                return _repair_result_numeric_boundaries(
                    likhit_result,
                    numeric_evidence,
                )
            elif likhit_needs_ocr:
                logger.info(
                    "PDF converter: likhit flagged pages needing OCR (%s).",
                    likhit_needs_ocr,
                )
                prefetched_likhit = likhit_result
            else:
                logger.warning(
                    "PDF converter: likhit extraction failed after repair-font detection; falling back to default extraction."
                )

        if prepared.ocr_pages:
            return _convert_pages_requiring_ocr(
                raw,
                prepared,
                ocr_service,
                numeric_evidence,
                prefetched_likhit=prefetched_likhit,
                degraded_reason=degraded_reason,
            )

        logger.info("PDF converter: running default MarkItDown PDF extraction first.")
        default_result = _repair_result_numeric_boundaries(
            _run_default_pdf_converter(raw, stream_info),
            numeric_evidence,
        )
        prefer_geometry_aware = requires_geometry_aware_candidate(
            numeric_evidence.repairs,
            markdown=default_result.markdown,
        )
        candidates: list[tuple[DocumentConverterResult, bool]] = [
            (default_result, False)
        ]
        if prefer_geometry_aware or _default_pdf_result_needs_likhit(
            default_result.markdown
        ):
            if prefer_geometry_aware:
                logger.info(
                    "PDF converter: plausible numeric cell merges require geometry-aware extraction."
                )
            else:
                logger.info(
                    "PDF converter: default extraction looks suspicious for Nepali text; retrying with likhit extraction."
                )
            likhit_result, _, reason = _try_convert_with_likhit(
                raw,
                ocr_pages=prepared.ocr_pages,
            )
            degraded_reason = (
                None if likhit_result is not None else (degraded_reason or reason)
            )
            if likhit_result is not None:
                logger.info("PDF converter: likhit re-extraction produced a candidate.")
                candidates.append(
                    (
                        _repair_result_numeric_boundaries(
                            likhit_result,
                            numeric_evidence,
                        ),
                        True,
                    )
                )
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
            return _stamp_degraded(candidates[0][0], degraded_reason)

        scored_candidates = [
            (
                result,
                _markdown_quality_score(result.markdown),
                geometry_aware
                or not requires_geometry_aware_candidate(
                    numeric_evidence.repairs,
                    markdown=result.markdown,
                ),
            )
            for result, geometry_aware in candidates
        ]
        # Safety outranks score. A candidate flagged unsafe still carries a
        # merged value that a global replacement could rewrite into a wrong
        # figure, and a wrong figure is worse than a lower-scoring page --
        # ranking on score first would leave this preference deciding nothing
        # but exact ties.
        best_result, best_score, best_safe = max(
            scored_candidates,
            key=lambda item: (item[2], item[1]),
        )
        logger.info(
            "PDF converter: selected best candidate after comparison (candidates=%d, score=%d, geometry_safe=%s).",
            len(scored_candidates),
            best_score,
            best_safe,
        )
        return _stamp_degraded(best_result, degraded_reason)


def _prepare_pdf(raw: bytes, pages: object) -> _PreparedPdf:
    """Validate, select, and classify a PDF in one PyMuPDF pass."""

    normalized_pages: str | int | None
    if pages is None:
        normalized_pages = None
    elif isinstance(pages, bool) or not isinstance(pages, (str, int)):
        raise ValidationError("pages must be an integer or a string like '5' or '2-4'")
    elif isinstance(pages, int):
        if pages < 1:
            raise ValidationError("pages must name a positive 1-based page")
        normalized_pages = pages
    else:
        normalized_pages = pages.strip()
        if not normalized_pages:
            normalized_pages = None
        elif not _PAGE_SPEC_PATTERN.fullmatch(normalized_pages):
            raise ValidationError("Invalid page range format. Use format: '1-3' or '5'")

    try:
        document = fitz.open(stream=raw, filetype="pdf")
    except Exception:  # noqa: BLE001 - existing conversion reports malformed PDFs
        return _PreparedPdf(raw, 0, {}, ())

    try:
        if document.needs_pass:
            raise ExtractionError("Password-protected PDFs are not supported")

        page_count = document.page_count
        if page_count == 0:
            return _PreparedPdf(raw, 0, {}, ())

        page_start, page_end = 0, page_count - 1
        if isinstance(normalized_pages, int):
            if normalized_pages > page_count:
                raise ValidationError(
                    f"Requested page range starts beyond document length ({page_count} pages)"
                )
            page_start = page_end = normalized_pages - 1
        elif normalized_pages is not None:
            page_start, page_end = parse_page_range(normalized_pages, page_count)

        source_page_numbers = tuple(range(page_start + 1, page_end + 2))
        ocr_pages: dict[int, str] = {}
        for local_page_number, page_index in enumerate(
            range(page_start, page_end + 1),
            start=1,
        ):
            marker = classify_ocr_page(document, page_index)
            if marker is not None:
                ocr_pages[local_page_number] = marker

        if page_start == 0 and page_end == page_count - 1:
            selected_raw = raw
        else:
            selected = fitz.open()
            try:
                selected.insert_pdf(
                    document,
                    from_page=page_start,
                    to_page=page_end,
                )
                selected_raw = selected.tobytes()
            finally:
                selected.close()

        return _PreparedPdf(
            selected_raw,
            len(source_page_numbers),
            ocr_pages,
            source_page_numbers,
        )
    finally:
        document.close()


def _conversion_error_result(
    kind: str,
    error: ValidationError | ExtractionError,
) -> DocumentConverterResult:
    return DocumentConverterResult(
        markdown=f"<!-- likhit:error kind={kind} -->\n\n{error}"
    )


def _convert_pages_requiring_ocr(
    raw: bytes,
    prepared: _PreparedPdf,
    ocr_service: LLMVisionOCRService | None,
    numeric_evidence: NumericBoundaryEvidence,
    *,
    prefetched_likhit: DocumentConverterResult | None,
    degraded_reason: str | None,
) -> DocumentConverterResult:
    """Return only text that is safe for a document containing scanned pages."""

    safe_result = prefetched_likhit
    if safe_result is None and len(prepared.ocr_pages) < prepared.page_count:
        safe_result, _, reason = _try_convert_with_likhit(
            raw,
            ocr_pages=prepared.ocr_pages,
        )
        degraded_reason = (
            None if safe_result is not None else (degraded_reason or reason)
        )

    if safe_result is None:
        safe_result = DocumentConverterResult(
            markdown="\n\n".join(
                page_anchor(page_number)
                for page_number in range(1, prepared.page_count + 1)
            )
        )
    safe_result = _repair_result_numeric_boundaries(safe_result, numeric_evidence)
    safe_result = _stamp_degraded(safe_result, degraded_reason)

    required_local_pages = tuple(sorted(prepared.ocr_pages))
    if ocr_service is None:
        return _stamp_needs_ocr(
            safe_result,
            _source_page_numbers(prepared, required_local_pages),
            reason="not-configured",
        )

    ocr = _run_page_ocr(raw, ocr_service, required_local_pages)
    merged = DocumentConverterResult(
        markdown=_merge_page_ocr(safe_result.markdown, ocr.text_by_page),
        title=safe_result.title,
    )
    if ocr.failed_pages:
        return _stamp_needs_ocr(
            merged,
            _source_page_numbers(prepared, ocr.failed_pages),
            reason="ocr-failed",
        )
    return merged


def _source_page_numbers(
    prepared: _PreparedPdf,
    local_page_numbers: Sequence[int],
) -> tuple[int, ...]:
    return tuple(
        prepared.source_page_numbers[page_number - 1]
        for page_number in local_page_numbers
    )


def _stamp_needs_ocr(
    result: DocumentConverterResult,
    page_numbers: Sequence[int],
    *,
    reason: str,
) -> DocumentConverterResult:
    pages = ",".join(str(page_number) for page_number in page_numbers)
    marker = f"<!-- likhit:needs-ocr pages={pages} reason={reason} -->"
    markdown = f"{marker}\n\n{result.markdown}".strip()
    return DocumentConverterResult(markdown=markdown, title=result.title)


def _merge_page_ocr(markdown: str, text_by_page: dict[int, str]) -> str:
    """Insert OCR text under matching page anchors."""

    merged = markdown
    matches = list(PAGE_ANCHOR_PATTERN.finditer(merged))
    anchored_pages = {int(match.group(1)) for match in matches}
    for match in reversed(matches):
        page_number = int(match.group(1))
        text = text_by_page.get(page_number)
        if text:
            merged = f"{merged[: match.end()]}\n\n{text}{merged[match.end() :]}"

    for page_number in sorted(set(text_by_page) - anchored_pages):
        text = text_by_page[page_number]
        merged = f"{merged}\n\n{page_anchor(page_number)}\n\n{text}".strip()
    return merged.strip()


def _try_collect_numeric_boundary_evidence(
    raw: bytes,
) -> NumericBoundaryEvidence:
    try:
        return collect_document_numeric_boundary_evidence(raw)
    except Exception as exc:  # noqa: BLE001 - degrade to no repairs, never fail
        logger.debug("PDF converter: numeric boundary analysis failed: %s", exc)
        return NumericBoundaryEvidence((), frozenset())


def _repair_result_numeric_boundaries(
    result: DocumentConverterResult,
    evidence: NumericBoundaryEvidence,
) -> DocumentConverterResult:
    if not evidence.repairs:
        return result
    markdown = repair_markdown_numeric_boundaries(
        result.markdown,
        evidence.repairs,
        unsplit_runs=evidence.unsplit_runs,
    )
    if markdown == result.markdown:
        return result
    return DocumentConverterResult(markdown=markdown, title=result.title)


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


def _run_full_page_ocr(
    raw: bytes,
    ocr_service: LLMVisionOCRService,
) -> DocumentConverterResult | None:
    """OCR every page, retaining an in-band marker for partial failure."""

    document = fitz.open(stream=raw, filetype="pdf")
    try:
        page_numbers = tuple(range(1, document.page_count + 1))
    finally:
        document.close()

    run = _run_page_ocr(raw, ocr_service, page_numbers)
    if not run.text_by_page:
        return None

    skeleton = "\n\n".join(page_anchor(page) for page in page_numbers)
    result = DocumentConverterResult(
        markdown=_merge_page_ocr(skeleton, run.text_by_page)
    )
    if run.failed_pages:
        return _stamp_needs_ocr(result, run.failed_pages, reason="ocr-failed")
    return result


def _run_page_ocr(
    raw: bytes,
    ocr_service: LLMVisionOCRService,
    page_numbers: Sequence[int],
) -> _PageOcrResult:
    text_by_page: dict[int, str] = {}
    failures: list[tuple[int, str]] = []
    doc = fitz.open(stream=raw, filetype="pdf")

    try:
        for page_number in page_numbers:
            page = doc[page_number - 1]
            try:
                rendered = _render_page_for_ocr(page)
                if rendered is None:
                    failures.append(
                        (
                            page_number,
                            "page cannot be rendered within the image size limit",
                        )
                    )
                    continue
                ocr_result = ocr_service.extract_text(io.BytesIO(rendered))
            except Exception as exc:  # noqa: BLE001 - preserve other OCR pages
                message = str(exc) or "<no message>"
                failures.append((page_number, f"{type(exc).__name__}: {message}"))
                continue

            if ocr_result.error:
                failures.append((page_number, ocr_result.error))
                continue

            extracted_text = ocr_result.text.strip()
            if extracted_text:
                text_by_page[page_number] = _format_full_page_ocr_text(extracted_text)
            else:
                failures.append((page_number, "OCR returned no text"))
    finally:
        doc.close()

    if failures:
        logger.warning(
            "OCR failed on %d of %d page(s): %s",
            len(failures),
            len(page_numbers),
            "; ".join(f"p{page}: {reason}" for page, reason in failures[:5]),
        )
    return _PageOcrResult(
        text_by_page=text_by_page,
        failed_pages=tuple(page for page, _reason in failures),
    )


def _ocr_render_matrix(page: fitz.Page) -> fitz.Matrix:
    """Start at configured DPI, bounding only pathological raster allocations."""

    width = page.rect.width
    height = page.rect.height
    if width <= 0 or height <= 0:
        return fitz.Matrix(1, 1)

    scale = _ocr_initial_dpi() / 72
    rendered_pixels = width * height * scale * scale
    if rendered_pixels > _OCR_MAX_RENDER_PIXELS:
        scale *= (_OCR_MAX_RENDER_PIXELS / rendered_pixels) ** 0.5
    return fitz.Matrix(scale, scale)


def _ocr_initial_dpi() -> float:
    raw = os.getenv("LIKHIT_OCR_DPI")
    if raw is None:
        return float(_OCR_DEFAULT_DPI)
    try:
        dpi = float(raw)
    except ValueError:
        dpi = 0
    if dpi <= 0:
        logger.warning(
            "Ignoring invalid LIKHIT_OCR_DPI=%r; using %d DPI",
            raw,
            _OCR_DEFAULT_DPI,
        )
        return float(_OCR_DEFAULT_DPI)
    return dpi


def _base64_encoded_size(raw_size: int) -> int:
    return ((raw_size + 2) // 3) * 4


def _render_page_for_ocr(page: fitz.Page) -> bytes | None:
    """Render a PNG that stays below the providers' 5 MiB base64 limit."""

    matrix = _ocr_render_matrix(page)
    for attempt in range(_OCR_MAX_RENDER_ATTEMPTS):
        pixmap = page.get_pixmap(matrix=matrix)
        png = pixmap.tobytes("png")
        encoded_size = _base64_encoded_size(len(png))
        if encoded_size <= _OCR_MAX_ENCODED_BYTES:
            return png
        if attempt == _OCR_MAX_RENDER_ATTEMPTS - 1:
            break
        matrix = fitz.Matrix(
            matrix.a * _OCR_RENDER_SCALE_STEP,
            matrix.d * _OCR_RENDER_SCALE_STEP,
        )
        logger.info(
            "OCR: page %d render was %.2f MiB base64; retrying at scale %.3f",
            page.number + 1,
            encoded_size / (1024 * 1024),
            matrix.a,
        )
    return None


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
    return "|" in header and _is_table_separator_line(lines[1])


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


def _build_ocr_service(
    *,
    llm_client: Any = None,
    llm_model: Any = None,
    llm_prompt: Any = None,
) -> LLMVisionOCRService | None:
    if llm_client is not None and llm_model:
        return LLMVisionOCRService(
            client=llm_client,
            model=str(llm_model),
            default_prompt=str(llm_prompt) if llm_prompt is not None else None,
        )

    api_key, model, base_url = _resolve_ocr_env()
    if not api_key or not model:
        return None

    from openai import OpenAI

    client_kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    prompt = (
        str(llm_prompt)
        if llm_prompt is not None
        else os.getenv("MARKITDOWN_OCR_PROMPT")
    )
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


def _convert_with_likhit(
    raw: bytes,
    *,
    ocr_pages: dict[int, str] | None = None,
) -> tuple[DocumentConverterResult, list[int]]:
    """Return the likhit markdown plus the 1-based pages it dropped as scanned."""

    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)

    try:
        raw_document = FontBasedStrategy().extract_text(
            str(tmp_path),
            _ocr_pages=ocr_pages,
        )
        markdown = _render_structure_aware_markdown(raw_document)
        if not markdown.strip():
            raise ExtractionError(
                "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
            )
        return DocumentConverterResult(markdown=markdown), list(
            raw_document.needs_ocr_pages
        )
    finally:
        tmp_path.unlink(missing_ok=True)


#: Exceptions from likhit's extraction that can never mean "this PDF is unreadable".
#:
#: `raw` is the whole document in memory and `_convert_with_likhit` writes a second
#: full copy to a temp file, so a parallel pass over the 80MB annual reports
#: double-buffers several GB; MemoryError there is a property of the machine, and
#: OSError covers the same class (ENOSPC on the temp write, EMFILE).
#:
#: A malformed PDF genuinely IS a document verdict, so everything else still falls
#: back silently. The distinction is the whole point: a resource failure and an
#: unreadable document produced byte-identical evidence before this split, which is
#: how five documents shipped damaged in the v11 corpus generation with the run
#: recording zero errors.
_RESOURCE_FAILURES: tuple[type[BaseException], ...] = (MemoryError, OSError)

#: Marks a transcript that is NOT what likhit would have produced. Same
#: HTML-comment shape as `page_anchor`, so it survives Markdown rendering, any
#: transport, and a driver that discards stderr -- which is the case that matters,
#: because the v11 build captured the child's stderr and then threw it away on
#: success.
DEGRADED_MARKER_PATTERN = re.compile(r"<!-- likhit:degraded reason=(\w+) -->")


def degraded_marker(reason: str) -> str:
    """The in-band signal that this transcript came from the fallback path."""

    return f"<!-- likhit:degraded reason={reason} -->"


def _stamp_degraded(
    result: DocumentConverterResult,
    reason: str | None,
) -> DocumentConverterResult:
    """Prepend the degradation marker, if a resource failure caused the fallback.

    Deliberately NOT applied when a document verdict caused it. A malformed PDF
    falling back is correct behaviour and stamping it would make the marker mean
    "likhit declined", which is common and uninteresting, instead of "the machine
    failed and this document should be retried", which is rare and actionable.
    """

    if reason is None:
        return result
    return DocumentConverterResult(
        markdown=f"{degraded_marker(reason)}\n\n{result.markdown}",
        title=result.title,
    )


def _try_convert_with_likhit(
    raw: bytes,
    *,
    ocr_pages: dict[int, str] | None = None,
) -> tuple[DocumentConverterResult | None, list[int], str | None]:
    """Returns (result, pages needing OCR, resource-failure reason).

    The third element is the fix for a failure mode that cost five documents: the
    caller cannot re-raise to signal a transient, because likhit is registered as a
    MarkItDown plugin (`_plugin.py`, priority -2.0) and MarkItDown's converter loop
    wraps every `convert()` call in `except Exception`. A raise there is recorded as
    a failed attempt, the loop moves on to the plain PdfConverter, and its output is
    returned with exit 0 -- measured 279,829 characters and 0 of 128 page anchors,
    *worse* than the fallback this function already takes. So the signal has to come
    back in-band.
    """

    try:
        return (*_convert_with_likhit(raw, ocr_pages=ocr_pages), None)
    except ScannedPdfError as exc:
        return None, list(exc.needs_ocr_pages), None
    except _RESOURCE_FAILURES as exc:
        # warning, not debug, and carrying the TYPE: at debug this was the only
        # trace of a fallback that changes every character of the transcript, and
        # the type is what separates a resource failure from a malformed PDF.
        logger.warning(
            "PDF converter: likhit extraction hit a resource failure (%s: %s); "
            "falling back to a DEGRADED transcript, which will be marked as such.",
            type(exc).__name__,
            exc,
        )
        return None, [], type(exc).__name__
    except Exception as exc:  # noqa: BLE001 - fall back to the default PDF path
        logger.debug("PDF converter: likhit extraction failed: %s", exc)
        return None, [], None


def _default_pdf_result_needs_likhit(markdown: str) -> bool:
    if not markdown.strip():
        return True

    cid_garbage_count = len(_CID_GARBAGE_PATTERN.findall(markdown))
    if cid_garbage_count >= 2:
        return True

    tokens = _TOKEN_PATTERN.findall(markdown)
    if not tokens:
        return True

    latin_tokens = [token for token in tokens if _LATIN_PATTERN.search(token)]
    if len(latin_tokens) < 12:
        return False

    # An absolute Devanagari floor used to short-circuit here: `devanagari_chars >= 20`
    # returned False before any of the terms below were computed. It is gone, because the
    # count it tested is not evidence that the *rest* of the document decoded. Measured on
    # `markdown-quality-v14` document `2997__1612859754Arnama Gaupalika`: 56 Devanagari
    # characters -- 0.12% of 45,325 non-space characters -- cleared the floor, so 25,217
    # characters of raw Preeti shipped as a transcript while every term below fired
    # (suspicious_ratio 0.5699 against 0.12, pipe_heavy_lines 319 against 4). likhit does
    # not raise on that PDF: it returns 40,481 Devanagari characters and outscores the
    # default candidate 130,202 to -25,795. It was simply never asked.
    #
    # Removing the floor cannot make this predicate *less* suspicious of any document, and
    # a True here only builds a second candidate -- `_markdown_quality_score` still decides
    # which ships, and it scores Devanagari heavily. The population where this predicate
    # returns False is exactly the transcripts that carry no page anchors (anchors are
    # emitted only by likhit), so the change is bounded by that set and measured within it:
    # over v14's 6,223 transcripts, 27 are anchor-free, the floor decided 2 of them, and 1
    # of those 2 never reaches this function because its Kalimati fonts take the
    # repair-font path first. Two documents change candidate set; one changes output.
    suspicious_tokens = [
        token for token in latin_tokens if _SUSPICIOUS_LATIN_TOKEN_PATTERN.search(token)
    ]
    vowel_poor_tokens = [
        token for token in latin_tokens if _is_vowel_poor_latin_token(token)
    ]
    pipe_heavy_lines = _pipe_heavy_line_count(markdown)

    suspicious_ratio = len(suspicious_tokens) / len(latin_tokens)
    vowel_poor_ratio = len(vowel_poor_tokens) / len(latin_tokens)
    return (
        suspicious_ratio >= 0.12
        or (suspicious_ratio >= 0.06 and vowel_poor_ratio >= 0.45)
        or (pipe_heavy_lines >= 4 and suspicious_ratio >= 0.05)
    )


def _pipe_delimited_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    parts = _UNESCAPED_PIPE_PATTERN.split(stripped)
    if stripped.startswith("|"):
        parts = parts[1:]
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        parts = parts[:-1]
    return [part.strip() for part in parts]


def _is_table_separator_line(line: str) -> bool:
    cells = _pipe_delimited_cells(line)
    return bool(cells) and all(
        cell and _TABLE_SEPARATOR_CELL_PATTERN.fullmatch(cell) for cell in cells
    )


def _blank_table_cell_count(markdown: str) -> int:
    count = 0
    for line in markdown.splitlines():
        cells = _pipe_delimited_cells(line)
        if cells is not None and not _is_table_separator_line(line):
            count += sum(not cell for cell in cells)
    return count


def _pipe_heavy_line_count(markdown: str) -> int:
    """Count lines carrying pipe spam, ignoring a row's enclosing delimiters.

    The signal being measured is "too many columns for this to be prose", so it
    counts the separators *between* values. Counting raw pipes instead would
    make the score depend on whether a renderer encloses its rows in pipes:
    `a | b` and `| a | b |` describe the same two columns, but the second has
    two extra pipes and would cross the threshold on its own. Raw table rows
    are enclosed (so leading and trailing blank cells stay visible), and
    Markdown tables are too, so without this the same table scores differently
    depending only on its delimiter style.
    """

    count = 0
    for line in markdown.splitlines():
        cells = _pipe_delimited_cells(line)
        if cells is not None and len(cells) >= 3:
            count += 1
    return count


def _markdown_quality_score(markdown: str) -> int:
    # Anchors are structural, not content. Scoring them would let the page
    # count sway which candidate conversion wins.
    markdown = strip_page_anchors(markdown)
    content_lines = [
        line for line in markdown.splitlines() if not _is_table_separator_line(line)
    ]
    content_markdown = "\n".join(content_lines)
    tokens = _TOKEN_PATTERN.findall(content_markdown)
    content_tokens = [token for token in tokens if token != "|"]
    latin_tokens = [token for token in tokens if _LATIN_PATTERN.search(token)]
    suspicious_tokens = [
        token for token in latin_tokens if _SUSPICIOUS_LATIN_TOKEN_PATTERN.search(token)
    ]
    vowel_poor_tokens = [
        token for token in latin_tokens if _is_vowel_poor_latin_token(token)
    ]
    pipe_heavy_lines = _pipe_heavy_line_count(content_markdown)
    blank_table_cells = _blank_table_cell_count(content_markdown)
    devanagari_chars = len(_DEVANAGARI_PATTERN.findall(content_markdown))
    cid_garbage_count = len(_CID_GARBAGE_PATTERN.findall(content_markdown))
    whitespace_excess = max(
        0,
        sum(character.isspace() for character in content_markdown)
        - int(len(content_markdown) * _MAX_REASONABLE_WHITESPACE_RATIO),
    )
    # A bare "|" is table structure, not per-character garble. Counting it as a
    # single-character token made this penalty scale with a candidate's column
    # count, so the candidate that renders explicit cell boundaries paid for
    # doing so: measured on two OAG documents, 94.6% and 94.3% of likhit's
    # single-character tokens were table pipes.
    #
    # Pipes are excluded from BOTH sides of the ratio, which is the whole point.
    # Dropping them from the count alone would leave them in the population that
    # sets the allowance, so each added pipe would raise the tolerated number of
    # single-character tokens by _MAX_REASONABLE_SINGLE_TOKEN_RATIO and refund
    # 2.1 points of garble penalty -- enough that a candidate splitting every
    # syllable into its own cell outscored one carrying the same characters as
    # whole words, simply by padding empty columns. This term asks what share of
    # a candidate's *content* tokens are lone characters, so table syntax
    # belongs in neither the numerator nor the denominator. Pipe-heavy output is
    # still charged, by pipe_heavy_lines above -- the term about tables. The
    # generic positive token credit remains unchanged; explicit blank cells are
    # neutralized separately so padding cannot manufacture score.
    single_token_excess = max(
        0,
        sum(len(token) == 1 for token in content_tokens)
        - int(len(content_tokens) * _MAX_REASONABLE_SINGLE_TOKEN_RATIO),
    )
    matra_damage_count = (
        len(DOUBLED_MATRA_PATTERN.findall(content_markdown))
        + len(ORPHAN_MATRA_PATTERN.findall(content_markdown))
        + len(VIRAMA_MATRA_PATTERN.findall(content_markdown))
    )
    return (
        devanagari_chars * _DEVANAGARI_CHAR_CREDIT
        + len(tokens)
        - blank_table_cells
        - len(suspicious_tokens) * _SUSPICIOUS_TOKEN_PENALTY
        - len(vowel_poor_tokens) * _VOWEL_POOR_TOKEN_PENALTY
        - pipe_heavy_lines * _PIPE_HEAVY_LINE_PENALTY
        - cid_garbage_count * _CID_GARBAGE_PENALTY
        # Marked CIDs are deliberately absent from this comparison. Marking is a
        # likhit feature -- every rival candidate comes from pdfminer or the OCR
        # converter and can never carry a mark (measured over 58 cached candidate
        # pairs drawn from 38 OAG documents: 0 marks on the non-likhit side) --
        # so the term had a constant sign and only ever taxed the candidate that
        # labels its unmappable glyphs.
        #
        # The rival carries the same damage disguised as ASCII: for the word
        # likhit renders as marked glyphs, the rival emits `ूारिWभक`. A mark is
        # a Plane-15 private-use code point (chr(0xF0000 + ord(c)), see
        # font_based.py), not any kind of tag -- worth stating because the
        # bracket class in _SUSPICIOUS_LATIN_TOKEN_PATTERN would charge a literal
        # markup tag 8 per token, which is not what happens here. The disguise is
        # not free either: `ूारिWभक` is a vowel-poor Latin token, so it costs 3.
        # But 12 per marked *character* against 3 per disguised *token* ranked
        # hidden damage above declared damage, which is backwards.
        #
        # Not charging a mark is not the same as neutrality: against absent text
        # a marked token still collects the generic +1 token credit above. What
        # makes that sound is that a marked glyph forfeits the +3 Devanagari
        # credit it would have earned decoded, so a candidate that decodes always
        # outranks one that labels -- pinned by
        # test_marking_never_beats_decoding_the_same_glyphs.
        #
        # U+FFFD stays charged: any candidate can emit it, so it still
        # discriminates.
        #
        # U+0000 is charged with it, at the same rate. pdfminer's sentinel for a
        # glyph it cannot decode is a NUL where likhit emits U+FFFD or a mark, so
        # charging only U+FFFD compared the two converters on which sentinel they
        # chose rather than on how much they lost. Measured on OAG document 13006
        # (`\u0932\u0941\u0919\u0917\u094d\u0930\u0940 \u0917\u093e\u0909\u0901\u092a\u093e\u0932\u093f\u0915\u093e, \u0930\u094b\u0932\u094d\u092a\u093e`), whose text layer carries 8,861 Latin-side
        # legacy codepoints: pdfminer turns 8,834 of them into NULs -- `\u0930\u094b\u0932\u094d\u092a\u093e` ->
        # `\u0930\u094b\x00\u092a\u093e`, `\u0917\u093e\u0909\u0901\u092a\u093e\u0932\u093f\u0915\u093e` -> `\u0917\u093e\u0909\u0901\u092a\u093e\x00\u0932\u0915\u093e` -- and was charged nothing,
        # while likhit declared 8,661 of the same glyphs as U+FFFD and was charged
        # 103,932. The handicap ran against the converter that told the truth, and
        # the NUL-bearing candidate shipped in every generation v6..v12.
        #
        # A NUL is strictly worse to ship than a U+FFFD, which is why it must not
        # be the cheaper option: GNU grep classifies a NUL-bearing file as binary,
        # drops every match, and still exits 0, so a sweep over the corpus
        # undercounts by that document and reports success.
        - (content_markdown.count("\ufffd") + content_markdown.count("\x00"))
        * _UNDECODED_GLYPH_PENALTY
        - whitespace_excess
        - single_token_excess * _EXCESS_SINGLE_TOKEN_PENALTY
        - matra_damage_count * _MATRA_DAMAGE_PENALTY
    )


def _render_layout_preserving_markdown(raw_document: RawDocument) -> str:
    return _render_markdown_from_blocks(
        build_content_blocks(
            raw_document.fragments,
            raw_document.tables,
            _build_layout_paragraphs,
        ),
        raw_document.page_numbers,
    )


def _block_page_number(block: ParagraphBlock | TableBlock) -> int:
    if isinstance(block, TableBlock):
        return block.table.page_number
    return block.page_number


def _assemble_with_page_anchors(
    parts: list[tuple[int, str]],
    page_numbers: Sequence[int],
) -> str:
    """Interleave rendered parts with one anchor per source page.

    Every page gets an anchor, including pages that produced nothing: a page
    whose text layer is empty is precisely where page-keyed OCR has to be merged
    in, so it needs a position in the document even though it has no content.
    """

    if not page_numbers:
        return "\n\n".join(part for _page, part in parts if part).strip()

    chunks: list[str] = []
    pending = list(parts)
    for page_number in page_numbers:
        chunks.append(page_anchor(page_number))
        # `<=` so a part whose page is unknown (0) or already passed still lands
        # under the earliest anchor rather than being dropped.
        while pending and pending[0][0] <= page_number:
            _page, part = pending.pop(0)
            if part:
                chunks.append(part)
    chunks.extend(part for _page, part in pending if part)
    return "\n\n".join(chunks).strip()


def _ordered_for_anchoring(parts: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Restore page order after a spanning table emits several page chunks."""

    keyed: list[tuple[int, tuple[int, str]]] = []
    last_page = 0
    for part in parts:
        if part[0]:
            last_page = part[0]
        keyed.append((last_page, part))
    keyed.sort(key=lambda item: item[0])
    return [part for _page, part in keyed]


def _render_markdown_from_blocks(
    blocks: list[ParagraphBlock | TableBlock],
    page_numbers: Sequence[int] = (),
) -> str:
    rendered: list[tuple[int, str]] = []
    for index, block in enumerate(blocks):
        page_number = _block_page_number(block)
        if isinstance(block, ParagraphBlock):
            text = block.text
            if _looks_like_page_furniture(text) and (
                (index > 0 and isinstance(blocks[index - 1], TableBlock))
                or (
                    index + 1 < len(blocks)
                    and isinstance(blocks[index + 1], TableBlock)
                )
            ):
                # This block was about to be discarded whole. Keep its
                # non-furniture lines instead: the predicate is a substring test,
                # so a running header merged into the page's body condemned the
                # entire page (VOL-668).
                text = strip_page_furniture_lines(text)
                # INSIDE the branch, matching `markdown._render_section`. Outside,
                # it also skips every whitespace-only ParagraphBlock, which is a
                # second behaviour change smuggled in with this one -- and it made
                # the two render paths differ again in exactly the place #61
                # deduplicated. Inert today only because `previous_table_key` is
                # dead (`_render_table` discards it); it stops being inert the
                # moment table continuation is implemented, and then
                # `Table | empty paragraph | Table` would merge at one site and
                # not the other. Found in review.
                if not text.strip():
                    continue
            rendered.append((page_number, _render_paragraph_markdown(text)))
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
            for table_page, chunk in render_table_page_chunks(
                block.table,
                include_caption=include_caption,
            ):
                rendered.append((table_page, f"```text\n{chunk}\n```"))
    return _assemble_with_page_anchors(_ordered_for_anchoring(rendered), page_numbers)


def _render_two_column_markdown(
    raw_document: RawDocument,
    handler: TwoColumnLayoutHandler,
    ordered_fragments: list[TextFragment],
) -> str:
    del ordered_fragments
    blocks = handler._build_blocks(raw_document)
    return _render_markdown_from_blocks(blocks, raw_document.page_numbers)


def _render_structure_aware_markdown(raw_document: RawDocument) -> str:
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
