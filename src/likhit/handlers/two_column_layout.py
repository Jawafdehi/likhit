"""Two-column article and journal style handler."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from statistics import median

from likhit.errors import ExtractionError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment
from likhit.extractors.docx_based import DocxBasedStrategy
from likhit.extractors.font_based import FontBasedStrategy
from likhit.handlers.base import StructureHandler
from likhit.handlers.content_blocks import blocks_to_text, build_content_blocks
from likhit.models import DocumentType, ExtractionResult, ParagraphBlock, Section
from likhit.models.types import ContentBlock

_NOISE_ONLY_PATTERN = re.compile(r"^(?:\d+|[A-Za-z+\-^&*/\\|=()]+)$")
_HEADER_Y_MAX = 80.0
_COLUMN_GUTTER = 20.0
_LAYOUT_BLOCK_GAP_MIN = 18.0


def _clean_paragraph(text: str) -> str:
    cleaned = text.replace("\ufffd", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class TwoColumnLayoutHandler(StructureHandler):
    """Handle dense two-column document layouts."""

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
        blocks = self._build_blocks(raw_document)
        paragraphs = [
            block.text for block in blocks if isinstance(block, ParagraphBlock)
        ]
        if not paragraphs:
            raise ExtractionError("No text content found in document")

        title = metadata.get("title") or self._extract_title(paragraphs)
        body = blocks_to_text(blocks).strip()
        section = Section(heading=None, body=body, level=1, blocks=blocks)
        return ExtractionResult(
            title=title,
            doc_type=DocumentType.TWO_COLUMN_LAYOUT,
            source_url=metadata.get("source_url"),
            publication_date=metadata.get("publication_date"),
            sections=[section],
            metadata={"layout_type": DocumentType.TWO_COLUMN_LAYOUT.value},
        )

    def _extract_title(self, paragraphs: list[str]) -> str:
        for paragraph in paragraphs:
            if not self._is_noise_only(paragraph):
                return paragraph
        return "दुई-स्तम्भ दस्तावेज"

    def _build_blocks(self, raw_document: RawDocument) -> list[ContentBlock]:
        if not raw_document.fragments:
            return [
                ParagraphBlock(text=cleaned)
                for paragraph in raw_document.paragraphs
                if (cleaned := _clean_paragraph(paragraph))
            ]

        ordered_fragments: list[TextFragment] = []
        by_page: dict[int, list[TextFragment]] = defaultdict(list)
        table_top_by_page: dict[int, float] = {}
        for fragment in raw_document.fragments:
            cleaned = _clean_paragraph(fragment.text)
            if not cleaned:
                continue
            by_page[fragment.page_number].append(fragment)
        for table in raw_document.tables:
            for region in table.regions:
                current_top = table_top_by_page.get(region.page_number)
                if current_top is None or region.y0 < current_top:
                    table_top_by_page[region.page_number] = region.y0

        for page_number in sorted(by_page):
            page_fragments = by_page[page_number]
            first_table_y0 = table_top_by_page.get(page_number)
            if first_table_y0 is None:
                ordered_fragments.extend(
                    self._order_fragments_by_detected_blocks(page_fragments)
                )
                continue

            pre_table = [
                fragment for fragment in page_fragments if fragment.y0 < first_table_y0
            ]
            post_table = [
                fragment for fragment in page_fragments if fragment.y0 >= first_table_y0
            ]
            ordered_fragments.extend(
                self._order_fragments_by_detected_blocks(pre_table)
            )
            ordered_fragments.extend(
                self._order_fragments_by_detected_blocks(post_table)
            )

        return build_content_blocks(
            ordered_fragments,
            raw_document.tables,
            self._merge_fragments_to_paragraphs,
        )

    def _order_fragments_by_detected_blocks(
        self, fragments: list[TextFragment]
    ) -> list[TextFragment]:
        ordered_fragments: list[TextFragment] = []
        for block in self._split_layout_blocks(fragments):
            if self._looks_like_row_aligned_block(block):
                ordered_fragments.extend(
                    sorted(block, key=lambda fragment: (fragment.y0, fragment.x0))
                )
            elif self._looks_like_two_column_block(block):
                ordered_fragments.extend(self._order_page_fragments(block))
            else:
                ordered_fragments.extend(
                    sorted(block, key=lambda fragment: (fragment.y0, fragment.x0))
                )
        return ordered_fragments

    def _split_layout_blocks(
        self, fragments: list[TextFragment]
    ) -> list[list[TextFragment]]:
        ordered = sorted(fragments, key=lambda fragment: (fragment.y0, fragment.x0))
        if not ordered:
            return []

        typical_line_height = self._typical_line_height(ordered)
        block_gap_threshold = max(_LAYOUT_BLOCK_GAP_MIN, typical_line_height * 1.35)
        blocks: list[list[TextFragment]] = []
        current_block: list[TextFragment] = []
        previous_y1: float | None = None

        for fragment in ordered:
            if (
                current_block
                and previous_y1 is not None
                and fragment.y0 - previous_y1 > block_gap_threshold
            ):
                blocks.append(current_block)
                current_block = []

            current_block.append(fragment)
            previous_y1 = (
                fragment.y1 if previous_y1 is None else max(previous_y1, fragment.y1)
            )

        if current_block:
            blocks.append(current_block)
        return blocks

    def _looks_like_row_aligned_block(self, fragments: list[TextFragment]) -> bool:
        line_groups = self._group_fragments_by_line(fragments)
        if len(line_groups) < 3:
            return False

        multi_column_rows = [
            group
            for group in line_groups
            if len(group) >= 3 and self._has_separated_columns(group)
        ]
        return (
            len(multi_column_rows) >= 3
            and len(multi_column_rows) >= len(line_groups) * 0.35
        )

    def _looks_like_two_column_block(self, fragments: list[TextFragment]) -> bool:
        body = [fragment for fragment in fragments if fragment.y0 > _HEADER_Y_MAX]
        if len(body) < 12:
            return False

        page_left = min(fragment.x0 for fragment in body)
        page_right = max(fragment.x1 for fragment in body)
        split_x = (page_left + page_right) / 2

        left = 0
        right = 0
        centered = 0
        for fragment in body:
            center_x = (fragment.x0 + fragment.x1) / 2
            if center_x <= split_x - _COLUMN_GUTTER:
                left += 1
            elif center_x >= split_x + _COLUMN_GUTTER:
                right += 1
            else:
                centered += 1

        if left < 4 or right < 4:
            return False

        paired_density = (left + right) / max(len(body), 1)
        return paired_density >= 0.6 and centered <= max(6, len(body) // 3)

    def _group_fragments_by_line(
        self, fragments: list[TextFragment]
    ) -> list[list[TextFragment]]:
        ordered = sorted(fragments, key=lambda fragment: (fragment.y0, fragment.x0))
        if not ordered:
            return []

        line_threshold = max(1.5, self._typical_line_height(ordered) * 0.18)
        groups: list[list[TextFragment]] = []
        current_group: list[TextFragment] = []

        for fragment in ordered:
            if not current_group:
                current_group.append(fragment)
                continue

            current_y0 = min(item.y0 for item in current_group)
            if abs(fragment.y0 - current_y0) <= line_threshold:
                current_group.append(fragment)
                continue

            groups.append(current_group)
            current_group = [fragment]

        if current_group:
            groups.append(current_group)
        return groups

    def _has_separated_columns(self, fragments: list[TextFragment]) -> bool:
        ordered = sorted(fragments, key=lambda fragment: fragment.x0)
        gaps = [
            next_fragment.x0 - fragment.x1
            for fragment, next_fragment in zip(ordered, ordered[1:], strict=False)
        ]
        return any(gap >= _COLUMN_GUTTER for gap in gaps)

    def _typical_line_height(self, fragments: list[TextFragment]) -> float:
        heights = [fragment.y1 - fragment.y0 for fragment in fragments]
        return min(median(heights), 24.0) if heights else 12.0

    def _order_page_fragments(
        self, fragments: list[TextFragment]
    ) -> list[TextFragment]:
        if not fragments:
            return []

        ordered = sorted(fragments, key=lambda fragment: (fragment.y0, fragment.x0))
        header = [fragment for fragment in ordered if fragment.y0 <= _HEADER_Y_MAX]
        body = [fragment for fragment in ordered if fragment.y0 > _HEADER_Y_MAX]
        if not body:
            return header

        page_left = min(fragment.x0 for fragment in body)
        page_right = max(fragment.x1 for fragment in body)
        split_x = (page_left + page_right) / 2

        left: list[TextFragment] = []
        right: list[TextFragment] = []
        centered: list[TextFragment] = []

        for fragment in body:
            center_x = (fragment.x0 + fragment.x1) / 2
            spans_center = (
                fragment.x0 < split_x - _COLUMN_GUTTER
                and fragment.x1 > split_x + _COLUMN_GUTTER
            )
            if spans_center:
                centered.append(fragment)
            elif center_x <= split_x - _COLUMN_GUTTER:
                left.append(fragment)
            elif center_x >= split_x + _COLUMN_GUTTER:
                right.append(fragment)
            else:
                centered.append(fragment)

        if not left or not right:
            return header + body

        left = sorted(left, key=lambda fragment: (fragment.y0, fragment.x0))
        right = sorted(right, key=lambda fragment: (fragment.y0, fragment.x0))
        centered = sorted(centered, key=lambda fragment: (fragment.y0, fragment.x0))
        first_column_y0 = min(
            fragment.y0 for fragment in left + right if fragment.y0 > _HEADER_Y_MAX
        )
        preamble_centered = [
            fragment for fragment in centered if fragment.y0 < first_column_y0
        ]
        remaining_centered = [
            fragment for fragment in centered if fragment.y0 >= first_column_y0
        ]
        return header + preamble_centered + left + right + remaining_centered

    def _merge_fragments_to_paragraphs(
        self, fragments: list[TextFragment]
    ) -> list[str]:
        if not fragments:
            return []

        typical_line_height = min(
            sorted(fragment.y1 - fragment.y0 for fragment in fragments)[
                len(fragments) // 2
            ],
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
                _clean_paragraph(fragment.text)
                for fragment in ordered_line
                if _clean_paragraph(fragment.text)
            ).strip()
            if text:
                merged_lines.append((page_number, y0, y1, text, gap_before))
            current_line.clear()

        for fragment in fragments:
            if not current_line:
                current_line.append(fragment)
                continue

            current_y0 = min(item.y0 for item in current_line)
            if abs(fragment.y0 - current_y0) <= line_merge_threshold:
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
            if previous_page is not None and page_number != previous_page:
                flush_paragraph()
            elif previous_y1 is not None:
                gap = y0 - previous_y1
                starts_gap_paragraph = (
                    gap_before is not None and gap_before > paragraph_gap_threshold
                )
                if starts_gap_paragraph or gap < -line_merge_threshold:
                    flush_paragraph()
            current_paragraph.append(text)
            previous_page = page_number
            previous_y1 = y1

        flush_paragraph()
        return paragraphs

    def _is_noise_only(self, text: str) -> bool:
        compact = text.replace(" ", "")
        return bool(_NOISE_ONLY_PATTERN.fullmatch(compact))
