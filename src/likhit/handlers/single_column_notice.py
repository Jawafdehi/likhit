"""Single-column notice style handler."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from statistics import median
from typing import Iterable

from likhit.errors import ExtractionError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment
from likhit.extractors.docx_based import DocxBasedStrategy
from likhit.extractors.font_based import FontBasedStrategy
from likhit.handlers.base import StructureHandler
from likhit.handlers.content_blocks import blocks_to_text, build_content_blocks
from likhit.models import DocumentType, ExtractionResult, Section

NEPALI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_nepali_date(text: str) -> str | None:
    match = re.search(
        r"([०-९0-9]{4})[।./-]\s*([०-९0-9]{1,2})[।./-]\s*([०-९0-9]{1,2})",
        text,
    )
    if not match:
        return None
    year = match.group(1).translate(NEPALI_DIGITS).zfill(4)
    month = match.group(2).translate(NEPALI_DIGITS).zfill(2)
    day = match.group(3).translate(NEPALI_DIGITS).zfill(2)
    return f"{year}-{month}-{day}"


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


class SingleColumnNoticeHandler(StructureHandler):
    """Handle notices and press-release style single-column layouts."""

    def __init__(self) -> None:
        self._strategy = FontBasedStrategy()
        self._docx_strategy = DocxBasedStrategy()

    def get_extraction_strategy(self) -> FontBasedStrategy:
        return self._strategy

    def get_extraction_strategy_for_file(self, file_path: str) -> ExtractionStrategy:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".doc":
            return self._docx_strategy
        return self._strategy

    def build_result(
        self, raw_document: RawDocument, metadata: dict[str, str | None]
    ) -> ExtractionResult:
        fragments = [
            TextFragment(
                text=_clean_line(fragment.text),
                page_number=fragment.page_number,
                x0=fragment.x0,
                y0=fragment.y0,
                x1=fragment.x1,
                y1=fragment.y1,
                block_number=fragment.block_number,
                line_number=fragment.line_number,
                gap_before=fragment.gap_before,
            )
            for fragment in raw_document.fragments
            if _clean_line(fragment.text)
        ]
        fragments = self._split_inline_subject_fragments(fragments)
        paragraphs = [fragment.text for fragment in fragments]

        title = metadata.get("title") or self._extract_title(paragraphs)
        publication_date = metadata.get("publication_date") or self._extract_date(
            paragraphs
        )

        if not fragments:
            raise ExtractionError("No body text content found in document")

        blocks = build_content_blocks(
            fragments,
            raw_document.tables,
            self._merge_body_lines,
        )
        body = blocks_to_text(blocks).strip()
        section_heading = title if title != "सूचना" else None

        result_metadata = {
            "layout_type": DocumentType.SINGLE_COLUMN_NOTICE.value,
            "raw_publication_date": self._extract_raw_date(paragraphs),
        }
        if metadata.get("source_url"):
            result_metadata["source_url"] = metadata["source_url"]

        section = Section(heading=section_heading, body=body, level=1, blocks=blocks)
        return ExtractionResult(
            title=title,
            doc_type=DocumentType.SINGLE_COLUMN_NOTICE,
            source_url=metadata.get("source_url"),
            publication_date=publication_date,
            sections=[section],
            metadata=result_metadata,
        )

    def _extract_title(self, paragraphs: Iterable[str]) -> str:
        for paragraph in paragraphs:
            subject_match = re.search(r"(?:विषय|मिषय)\s*:\s*(.+)", paragraph)
            if subject_match:
                title_text, _body_text = self._split_subject_remainder(
                    subject_match.group(1).strip()
                )
                return title_text.strip(" ।")
        for paragraph in paragraphs:
            cleaned = paragraph.strip()
            if cleaned and not self._is_date_line(cleaned):
                return cleaned[:120]
        return "सूचना"

    def _extract_raw_date(self, paragraphs: Iterable[str]) -> str | None:
        for paragraph in paragraphs:
            if "मिति" in paragraph or "मिमि" in paragraph:
                match = re.search(
                    r"([०-९0-9]{4}[।./-]\s*[०-९0-9]{1,2}[।./-]\s*[०-९0-9]{1,2})",
                    paragraph,
                )
                if match:
                    return match.group(1)
        return None

    def _extract_date(self, paragraphs: Iterable[str]) -> str | None:
        for paragraph in paragraphs:
            if not self._is_date_line(paragraph):
                continue
            normalized = normalize_nepali_date(paragraph)
            if normalized:
                return normalized
        return None

    def _is_subject_line(self, text: str) -> bool:
        return bool(re.search(r"(?:विषय|मिषय)\s*:", text))

    def _is_date_line(self, text: str) -> bool:
        return bool(re.search(r"(?:मिति|मिमि)\s*:", text))

    def _split_subject_remainder(self, remainder: str) -> tuple[str, str]:
        title_text = remainder.strip()
        body_text = ""

        match = re.search(r"[।!?](?:\s+|(?=\S))", remainder)
        if match:
            title_text = remainder[: match.start()].strip()
            body_text = remainder[match.end() :].strip()

        return title_text, body_text

    def _split_inline_subject_fragments(
        self, fragments: list[TextFragment]
    ) -> list[TextFragment]:
        split_fragments: list[TextFragment] = []
        for fragment in fragments:
            text = fragment.text.strip()
            if not self._is_subject_line(text):
                split_fragments.append(fragment)
                continue

            match = re.search(r"((?:विषय|मिषय)\s*:\s*)(.+)", text)
            if not match:
                split_fragments.append(fragment)
                continue

            remainder = match.group(2).strip()
            title_text, body_text = self._split_subject_remainder(remainder)

            split_fragments.append(
                TextFragment(
                    text=f"विषय: {title_text.strip()}",
                    page_number=fragment.page_number,
                    x0=fragment.x0,
                    y0=fragment.y0,
                    x1=fragment.x1,
                    y1=fragment.y1,
                    block_number=fragment.block_number,
                    line_number=fragment.line_number,
                    gap_before=fragment.gap_before,
                )
            )
            if body_text:
                split_fragments.append(
                    TextFragment(
                        text=body_text,
                        page_number=fragment.page_number,
                        x0=fragment.x0,
                        y0=fragment.y0,
                        x1=fragment.x1,
                        y1=fragment.y1,
                        block_number=fragment.block_number,
                        line_number=fragment.line_number + 1,
                        gap_before=0.0,
                    )
                )
        return split_fragments

    def _merge_body_lines(self, fragments: list[TextFragment]) -> list[str]:
        merged: list[str] = []
        current: list[str] = []

        def flush() -> None:
            if current:
                merged.append(" ".join(current).strip())
                current.clear()

        def paragraph_indent_threshold() -> float | None:
            if not fragments:
                return None

            bucket_size = 4.0
            bucketed = [
                round(fragment.x0 / bucket_size) * bucket_size for fragment in fragments
            ]
            dominant_bucket, _count = Counter(bucketed).most_common(1)[0]
            dominant_values = [
                fragment.x0
                for fragment in fragments
                if abs(
                    (round(fragment.x0 / bucket_size) * bucket_size) - dominant_bucket
                )
                < 0.01
            ]
            base_margin = (
                median(dominant_values)
                if dominant_values
                else min(fragment.x0 for fragment in fragments)
            )
            candidate_margins = sorted(
                {
                    fragment.x0
                    for fragment in fragments
                    if fragment.x0 >= base_margin + 12
                }
            )
            if not candidate_margins:
                return None
            nearest_indent = candidate_margins[0]
            return base_margin + max(8.0, (nearest_indent - base_margin) / 2)

        typical_line_height = median(fragment.y1 - fragment.y0 for fragment in fragments)
        indent_threshold = paragraph_indent_threshold()

        def starts_indented_paragraph(fragment: TextFragment) -> bool:
            return indent_threshold is not None and fragment.x0 >= indent_threshold

        def starts_gap_paragraph(fragment: TextFragment) -> bool:
            return (
                fragment.gap_before is not None
                and fragment.gap_before > typical_line_height * 0.45
            )

        previous_fragment: TextFragment | None = None
        for fragment in fragments:
            text = fragment.text.strip()
            if not text:
                flush()
                continue

            if text in {"प्रवक्ता"}:
                flush()
                current.append(text)
                flush()
                previous_fragment = fragment
                continue

            if not current:
                current.append(text)
                previous_fragment = fragment
                continue

            if previous_fragment and fragment.page_number != previous_fragment.page_number:
                flush()
                current.append(text)
                previous_fragment = fragment
                continue

            if previous_fragment and (
                starts_indented_paragraph(fragment) or starts_gap_paragraph(fragment)
            ):
                flush()
                current.append(text)
                previous_fragment = fragment
                continue

            current.append(text)
            previous_fragment = fragment

        flush()
        return merged
