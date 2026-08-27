"""Page anchors: the positions page-keyed data attaches to.

Without these, per-page results (OCR output, per-page provenance) have nowhere
to be merged into a transcript, because the rendered Markdown carries no record
of where one source page ends and the next begins.
"""

from __future__ import annotations

from pathlib import Path

import fitz
from markitdown import MarkItDown
import pytest

from likhit.converters.nepali_pdf import (
    _assemble_with_page_anchors,
    _markdown_quality_score,
)
from likhit.errors import ScannedPdfError
from likhit.extractors.base import TextFragment
import likhit.extractors.font_based as font_based_module
from likhit.extractors.font_based import FontBasedStrategy
from likhit.handlers.content_blocks import build_content_blocks
from likhit.models import ParagraphBlock
from likhit.renderers.markdown import (
    page_anchor,
    page_anchor_numbers,
    strip_page_anchors,
)

ROOT = Path(__file__).resolve().parents[1]


def _convert(sample: Path) -> str:
    return MarkItDown(enable_plugins=True).convert(str(sample)).text_content or ""


def test_page_anchor_round_trips_through_its_own_reader() -> None:
    markdown = f"{page_anchor(1)}\n\ntext\n\n{page_anchor(2)}\n\nmore"

    assert page_anchor_numbers(markdown) == [1, 2]


def test_page_anchor_is_an_html_comment_so_it_renders_invisibly() -> None:
    rendered = page_anchor(7)

    assert rendered.startswith("<!--") and rendered.endswith("-->")
    assert "7" in rendered


def test_strip_page_anchors_leaves_the_content() -> None:
    markdown = f"{page_anchor(1)}\n\nरकम\n\n{page_anchor(2)}\n\nजम्मा"

    assert page_anchor_numbers(strip_page_anchors(markdown)) == []
    assert "रकम" in strip_page_anchors(markdown)
    assert "जम्मा" in strip_page_anchors(markdown)


def test_every_page_is_anchored_even_when_it_produced_nothing() -> None:
    # The load-bearing case: a scanned page has an empty text layer, so it
    # contributes no parts. It still needs an anchor, because that anchor is
    # where its OCR text gets merged in. Page 2 here is that page.
    assembled = _assemble_with_page_anchors(
        [(1, "first"), (3, "third")], page_numbers=[1, 2, 3]
    )

    assert page_anchor_numbers(assembled) == [1, 2, 3]


def test_anchors_are_monotonic_and_precede_their_own_content() -> None:
    assembled = _assemble_with_page_anchors(
        [(1, "alpha"), (2, "beta")], page_numbers=[1, 2]
    )
    lines = [line for line in assembled.splitlines() if line.strip()]

    assert lines == [page_anchor(1), "alpha", page_anchor(2), "beta"]


def test_a_part_with_no_known_page_is_kept_not_dropped() -> None:
    # page_number 0 means "producer has no page concept"; such a part must still
    # appear, under the first anchor.
    assembled = _assemble_with_page_anchors([(0, "orphan")], page_numbers=[1])

    assert "orphan" in assembled
    assert page_anchor_numbers(assembled) == [1]


def test_parts_beyond_the_declared_pages_are_still_emitted() -> None:
    assembled = _assemble_with_page_anchors(
        [(1, "kept"), (9, "also kept")], page_numbers=[1]
    )

    assert "kept" in assembled and "also kept" in assembled


def test_no_page_numbers_means_no_anchors() -> None:
    # Producers with no page concept (DOCX) must render exactly as before.
    assembled = _assemble_with_page_anchors([(0, "a"), (0, "b")], page_numbers=[])

    assert assembled == "a\n\nb"
    assert page_anchor_numbers(assembled) == []


def test_quality_score_ignores_page_anchors() -> None:
    # Scoring decides which candidate conversion wins. If anchors counted, the
    # page count would sway that choice.
    body = "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा सञ्चालित कार्यक्रम"
    anchored = f"{page_anchor(1)}\n\n{body}\n\n{page_anchor(2)}\n\n{body}"
    plain = f"{body}\n\n{body}"

    assert _markdown_quality_score(anchored) == _markdown_quality_score(plain)


def test_paragraph_blocks_carry_the_page_they_came_from() -> None:
    fragments = [
        TextFragment("पहिलो", 1, 45.0, 100.0, 400.0, 120.0, 0, 0),
        TextFragment("दोस्रो", 4, 45.0, 100.0, 400.0, 120.0, 0, 0),
    ]

    blocks = build_content_blocks(fragments, [], lambda chunk: [f.text for f in chunk])

    assert [
        block.page_number for block in blocks if isinstance(block, ParagraphBlock)
    ] == [1, 4]


@pytest.mark.parametrize(
    "sample",
    ["pressrelease.pdf", "my-table.pdf", "Press Release.pdf", "table.pdf"],
)
def test_anchor_count_equals_the_pdf_page_count(sample: str) -> None:
    # The release gate: a transcript must account for every page of its source.
    path = ROOT / "samples" / sample
    if not path.exists():
        pytest.skip(f"{sample} not available")
    with fitz.open(path) as document:
        page_count = document.page_count

    anchors = page_anchor_numbers(_convert(path))

    assert anchors == list(range(1, page_count + 1))


def test_an_image_dominant_pdf_requires_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OCR-only document must not fall back to an unanchored junk transcript."""

    path = ROOT / "samples" / "nirnaya.pdf"
    assert path.exists()
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "MARKITDOWN_OCR_MODEL",
        "OPENAI_MODEL",
        "GEMINI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ScannedPdfError) as exc_info:
        _convert(path)

    assert exc_info.value.needs_ocr_pages == [1, 2]


def test_page_numbers_cover_a_suppressed_scanned_page(tmp_path: Path) -> None:
    """A page that produced no text must still be counted.

    This is the case that broke on the real corpus. likhit suppresses a scanned
    raster page, so it contributes no fragments; deriving the page list from the
    output dropped it, losing the anchor for exactly the page `needs_ocr_pages`
    says needs OCR merged in. Page 2 here is that page.
    """

    source = tmp_path / "scanmix.pdf"
    document = fitz.open()
    for index in range(3):
        page = document.new_page(width=595, height=842)
        if index == 1:
            # A noisy raster, so it reads as a scan rather than a flat fill.
            noise = bytes(
                (index * 37 + value * 71) % 135 + 120 for value in range(300 * 400)
            )
            pixmap = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 300, 400), False)
            pixmap.samples_mv[:] = noise
            page.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pixmap)
        else:
            page.insert_text((72, 72), "लेखापरीक्षण प्रतिवेदन", fontname="helv")
    document.save(source)
    document.close()

    raw = FontBasedStrategy().extract_text(str(source))

    assert raw.needs_ocr_pages == [2], "fixture must actually suppress page 2"
    assert sorted({fragment.page_number for fragment in raw.fragments}) == [1, 3]
    assert raw.page_numbers == [1, 2, 3]


def test_page_numbers_honour_a_requested_page_range(tmp_path: Path) -> None:
    source = tmp_path / "ranged.pdf"
    document = fitz.open()
    for index in range(5):
        document.new_page().insert_text((72, 72), f"page {index + 1}", fontname="helv")
    document.save(source)
    document.close()

    raw = FontBasedStrategy().extract_text(str(source), pages="2-4")

    assert raw.page_numbers == [2, 3, 4]


def test_page_numbers_ignore_which_pages_yielded_fragments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The requested range is authoritative; the extraction's output is not.

    A broken-CMap document is rebuilt from merged fragments alone, so on the real
    corpus a suppressed scanned page -- which yields no fragments -- lost its
    anchor entirely. No bundled sample has both properties at once, so page 3's
    fragments are discarded here to reproduce it.
    """

    sample = ROOT / "samples" / "pressrelease.pdf"
    merge = font_based_module._merge_fragment_variants

    def drop_third_page(original: list, repaired: list) -> list:
        return [
            fragment
            for fragment in merge(original, repaired)
            if fragment.page_number != 3
        ]

    monkeypatch.setattr(font_based_module, "_merge_fragment_variants", drop_third_page)

    raw = FontBasedStrategy().extract_text(str(sample))

    assert sorted({fragment.page_number for fragment in raw.fragments}) == [1, 2]
    assert raw.page_numbers == [1, 2, 3]
