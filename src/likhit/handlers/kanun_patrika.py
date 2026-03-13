"""Kanun Patrika document handler."""

from __future__ import annotations

from collections import defaultdict
import re

from likhit.errors import ExtractionError
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.font_based import FontBasedStrategy
from likhit.handlers.base import DocumentTypeHandler
from likhit.models import DocumentType, ExtractionResult, Section

_NOISE_ONLY_PATTERN = re.compile(r"^(?:\d+|[A-Za-z+\-^&*/\\|=()]+)$")
_HEADER_Y_MAX = 80.0
_COLUMN_GUTTER = 20.0


def _clean_paragraph(text: str) -> str:
    cleaned = text.replace("\ufffd", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class KanunPatrikaHandler(DocumentTypeHandler):
    """Handle Kanun Patrika journal-style documents."""

    def __init__(self) -> None:
        self._strategy = FontBasedStrategy()

    def get_extraction_strategy(self) -> FontBasedStrategy:
        return self._strategy

    def build_result(
        self, raw_document: RawDocument, metadata: dict[str, str | None]
    ) -> ExtractionResult:
        paragraphs = self._ordered_paragraphs(raw_document)
        if not paragraphs:
            raise ExtractionError("No text content found in document")

        title = metadata.get("title") or self._extract_title(paragraphs)
        body = "\n\n".join(paragraphs).strip()
        section = Section(heading=None, body=body, level=1)
        return ExtractionResult(
            title=title,
            doc_type=DocumentType.KANUN_PATRIKA,
            source_url=metadata.get("source_url"),
            publication_date=metadata.get("publication_date"),
            sections=[section],
            metadata={"source_name": "Kanun Patrika"},
        )

    def _extract_title(self, paragraphs: list[str]) -> str:
        for paragraph in paragraphs:
            if not self._is_noise_only(paragraph):
                return paragraph
        return "कानून पत्रिका"

    def _ordered_paragraphs(self, raw_document: RawDocument) -> list[str]:
        if not raw_document.fragments:
            return [
                _clean_paragraph(paragraph)
                for paragraph in raw_document.paragraphs
                if _clean_paragraph(paragraph)
            ]

        paragraphs: list[str] = []
        by_page: dict[int, list[TextFragment]] = defaultdict(list)
        for fragment in raw_document.fragments:
            cleaned = _clean_paragraph(fragment.text)
            if not cleaned:
                continue
            by_page[fragment.page_number].append(fragment)

        for page_number in sorted(by_page):
            page_fragments = by_page[page_number]
            ordered_fragments = self._order_page_fragments(page_fragments)
            paragraphs.extend(_clean_paragraph(fragment.text) for fragment in ordered_fragments)

        return paragraphs

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
            if center_x <= split_x - _COLUMN_GUTTER:
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
        return header + left + right + centered

    def _is_noise_only(self, text: str) -> bool:
        compact = text.replace(" ", "")
        if _NOISE_ONLY_PATTERN.fullmatch(compact):
            return True
        return False
