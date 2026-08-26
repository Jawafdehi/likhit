"""Page attribution for tables merged across consecutive PDF pages."""

from __future__ import annotations

from likhit.converters.nepali_pdf import _render_layout_preserving_markdown
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.tables import merge_continuation_tables
from likhit.models import Table, TableCell, TableRegion
from likhit.renderers.markdown import (
    page_anchor,
    render_table_markdown,
    render_table_page_chunks,
)

HEADER = ("सि.नं", "प्रतिवादीको नाम", "बिगो")


def _page_table(page_number: int, serial: str, *, wrapped: bool = False) -> Table:
    name = f"प्रतिवादी {serial}"
    if wrapped:
        name += "\nथप नाम"
    return Table(
        row_count=2,
        col_count=3,
        cells=[
            *[TableCell(row=0, col=col, text=text) for col, text in enumerate(HEADER)],
            TableCell(row=1, col=0, text=serial),
            TableCell(row=1, col=1, text=name),
            TableCell(row=1, col=2, text=f"रु. {serial},०००"),
        ],
        regions=[
            TableRegion(
                page_number=page_number,
                x0=40.0,
                y0=60.0,
                x1=550.0,
                y1=800.0,
                page_height=842.0,
            )
        ],
    )


def _merged_run() -> Table:
    merged = merge_continuation_tables(
        [
            _page_table(1, "१"),
            _page_table(2, "२", wrapped=True),
            _page_table(3, "३"),
        ]
    )
    assert len(merged) == 1
    return merged[0]


def test_merge_records_where_each_pages_rows_begin() -> None:
    merged = _merged_run()

    assert merged.row_count == 4
    assert [region.start_row for region in merged.regions] == [0, 2, 3]


def test_page_chunks_preserve_the_whole_render() -> None:
    merged = _merged_run()
    merged.caption = "अनुसूची: मुद्दा विवरण"

    chunks = render_table_page_chunks(merged)

    assert [page for page, _chunk in chunks] == [1, 2, 3]
    assert "\n".join(chunk for _page, chunk in chunks) == render_table_markdown(merged)
    assert sum(chunk.count("अनुसूची: मुद्दा विवरण") for _page, chunk in chunks) == 1
    assert sum(chunk.count("प्रतिवादीको नाम") for _page, chunk in chunks) == 1


def test_wrapped_row_keeps_its_page_after_splitting() -> None:
    chunks = dict(render_table_page_chunks(_merged_run()))

    assert "प्रतिवादी २ थप नाम" in chunks[2]
    assert "प्रतिवादी २" not in chunks[1]
    assert "प्रतिवादी २" not in chunks[3]


def test_layout_pipeline_reorders_an_overtaken_intermediate_paragraph() -> None:
    """Exercise block construction, table chunking, sorting, and page anchoring."""

    paragraph = "यो दोस्रो पृष्ठको तालिकाबाहिरको अनुच्छेद हो।"
    raw_document = RawDocument(
        paragraphs=[],
        raw_text=paragraph,
        fragments=[
            TextFragment(
                text=paragraph,
                page_number=2,
                x0=40.0,
                y0=815.0,
                x1=500.0,
                y1=830.0,
            )
        ],
        tables=[_merged_run()],
        page_numbers=[1, 2, 3],
    )

    markdown = _render_layout_preserving_markdown(raw_document)
    page_two = markdown.split(page_anchor(2), maxsplit=1)[1].split(
        page_anchor(3), maxsplit=1
    )[0]
    page_three = markdown.split(page_anchor(3), maxsplit=1)[1]

    assert "प्रतिवादी २ थप नाम" in page_two
    assert paragraph in page_two
    assert paragraph not in page_three
    assert "प्रतिवादी ३" in page_three


def test_single_page_table_still_renders_as_one_unchanged_chunk() -> None:
    table = _page_table(7, "७")

    assert render_table_page_chunks(table) == [(7, render_table_markdown(table))]
