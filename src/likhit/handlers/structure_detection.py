"""Layout-based structure detection for extracted documents."""

from __future__ import annotations

import re
from statistics import median

from likhit.extractors.base import RawDocument, TextFragment
from likhit.models import DocumentType


def detect_structure(raw_document: RawDocument) -> DocumentType | None:
    """Infer supported whole-document structures from extracted signals."""

    fragments = [
        fragment for fragment in raw_document.fragments if fragment.text.strip()
    ]
    if not fragments:
        return None

    if _looks_like_single_column_notice(fragments):
        return DocumentType.SINGLE_COLUMN_NOTICE

    return None


def _looks_like_single_column_notice(fragments: list[TextFragment]) -> bool:
    if not fragments:
        return False

    first_page = min(fragment.page_number for fragment in fragments)
    page_fragments = [
        fragment for fragment in fragments if fragment.page_number == first_page
    ]
    if len(page_fragments) < 3:
        return False

    page_left = min(fragment.x0 for fragment in page_fragments)
    page_right = max(fragment.x1 for fragment in page_fragments)
    top_fragments = [fragment for fragment in page_fragments if fragment.y0 <= 220]

    centered_top_lines = sum(
        1
        for fragment in top_fragments
        if _looks_centered(fragment, page_left, page_right)
    )
    has_subject = any(_is_subject_line(fragment.text) for fragment in page_fragments)
    has_date = any(_is_date_line(fragment.text) for fragment in page_fragments)

    body_fragments = [fragment for fragment in page_fragments if fragment.y0 > 120]
    if len(body_fragments) < 3:
        body_fragments = page_fragments
    if len(body_fragments) < 3:
        return False

    line_heights = [fragment.y1 - fragment.y0 for fragment in body_fragments]
    typical_height = median(line_heights) if line_heights else 12.0
    has_paragraph_gap = any(
        fragment.gap_before is not None and fragment.gap_before > typical_height * 0.45
        for fragment in body_fragments
    )

    return (
        has_subject
        or (has_date and centered_top_lines >= 2)
        or (centered_top_lines >= 3 and has_paragraph_gap)
    )


def _looks_centered(
    fragment: TextFragment, page_left: float, page_right: float
) -> bool:
    page_center = (page_left + page_right) / 2
    line_center = (fragment.x0 + fragment.x1) / 2
    page_width = max(page_right - page_left, 1.0)
    line_width = fragment.x1 - fragment.x0
    return (
        line_width <= page_width * 0.75
        and abs(line_center - page_center) <= page_width * 0.12
    )


def _is_subject_line(text: str) -> bool:
    return bool(re.search(r"(?:विषय|मिषय)\s*:", text))


def _is_date_line(text: str) -> bool:
    return bool(re.search(r"(?:मिति|मिमि)\s*:", text))
