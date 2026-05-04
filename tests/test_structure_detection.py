from __future__ import annotations

from likhit.extractors.base import RawDocument, TextFragment
from likhit.handlers.content_blocks import blocks_to_text
from likhit.handlers.structure_detection import detect_structure
from likhit.handlers.two_column_layout import TwoColumnLayoutHandler
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


def test_two_column_handler_assigns_layout_per_block_not_file() -> None:
    fragments = [
        TextFragment("Document title", 1, 160, 40, 360, 55),
        TextFragment("Intro text", 1, 72, 100, 500, 115),
        TextFragment("1.", 1, 72, 140, 88, 155),
        TextFragment("First heading", 1, 104, 140, 300, 155),
        TextFragment("2.", 1, 72, 180, 88, 195),
        TextFragment("Second heading", 1, 104, 180, 320, 195),
        TextFragment("No.", 2, 150, 120, 180, 135),
        TextFragment("Group", 2, 220, 120, 280, 135),
        TextFragment("Percent", 2, 380, 120, 430, 135),
        TextFragment("1.", 2, 150, 150, 180, 165),
        TextFragment("Dalit", 2, 220, 150, 260, 165),
        TextFragment("13.44", 2, 380, 150, 430, 165),
        TextFragment("2.", 2, 150, 180, 180, 195),
        TextFragment("Janajati", 2, 220, 180, 300, 195),
        TextFragment("28.72", 2, 380, 180, 430, 195),
        TextFragment("3.", 2, 150, 210, 180, 225),
        TextFragment("Khas Arya", 2, 220, 210, 310, 225),
        TextFragment("30.28", 2, 380, 210, 430, 225),
    ]
    raw_document = RawDocument(
        paragraphs=[fragment.text for fragment in fragments],
        raw_text="\n".join(fragment.text for fragment in fragments),
        fragments=fragments,
        tables=[],
    )

    blocks = TwoColumnLayoutHandler()._build_blocks(raw_document)
    text = blocks_to_text(blocks)

    assert "1. First heading" in text
    assert "2. Second heading" in text
    assert "1. Dalit 13.44" in text
    assert "2. Janajati 28.72" in text
    assert text.index("Intro text") < text.index("1. First heading")
