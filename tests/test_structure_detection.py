from __future__ import annotations

from likhit.extractors.base import RawDocument, TextFragment
from likhit.handlers.structure_detection import detect_structure
from likhit.models import DocumentType


def test_detect_structure_uses_earliest_page_for_notice_detection() -> None:
    fragments = [
        TextFragment("पछिल्लो पृष्ठ", 2, 40, 120, 200, 140),
        TextFragment("अख्तियार दुरुपयोग अनुसन्धान आयोग", 1, 150, 40, 330, 60),
        TextFragment("मिति: २०८२।०१।१४", 1, 180, 70, 300, 90),
        TextFragment("विषय: परीक्षण शीर्षक", 1, 160, 100, 320, 120),
        TextFragment("मुख्य अनुच्छेद", 1, 40, 160, 420, 180),
    ]
    raw_document = RawDocument(
        paragraphs=[fragment.text for fragment in fragments],
        raw_text="\n".join(fragment.text for fragment in fragments),
        fragments=fragments,
        tables=[],
    )

    assert detect_structure(raw_document) is DocumentType.SINGLE_COLUMN_NOTICE
