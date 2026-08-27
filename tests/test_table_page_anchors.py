"""A table spanning pages must anchor each page's rows to that page.

`merge_continuation_tables` stitches a ruled table that runs across pages into
one table, so repeated headers collapse and a continuation row attaches to the
record it belongs to. Rendered as a single block that table can carry only one
page number, and the whole run therefore landed under the anchor of the page it
started on. A page anchor became a lower bound on provenance and never an upper
bound, which is a citation defect: these transcripts are primary sources, and the
long tables are the case-detail data -- the most citable content in them.

Measured on the landed CIAA corpus on 2026-08-12 (13 reports, 5,661 pages):
18 pages held none of their own text, the worst by 24 pages -- page 254 of the
27th annual report (FY 2073/74) sat under the anchor for page 230, whose section
held 74,852 characters against that page's own 2,622. The 30th and 31st show the
same shape at pages 337 and 270. The fixtures here use the 27th's real page span
because that is the case with the largest fold.
"""

from __future__ import annotations

from likhit.converters.nepali_pdf import _ordered_for_anchoring
from likhit.extractors.tables import merge_continuation_tables
from likhit.models import Table, TableCell, TableRegion
from likhit.renderers.markdown import (
    render_table_markdown,
    render_table_page_chunks,
)

# The 27th report's fold: a case-detail table running from page 230 to page 254.
FIRST_PAGE = 230
LAST_PAGE = 254
HEADER = ("सि.नं", "प्रतिवादीको नाम", "बिगो")


def _page_table(page_number: int, serial: str, *, height: float = 842.0) -> Table:
    """One page's slice of the run: the repeated header, then one data row.

    The geometry is what `_should_merge_tables` requires of a continuation: the
    same column edges, a table reaching the bottom of its page, and one starting
    at the top of the next.
    """

    cells = [TableCell(row=0, col=col, text=text) for col, text in enumerate(HEADER)]
    cells += [
        TableCell(row=1, col=0, text=serial),
        TableCell(row=1, col=1, text=f"प्रतिवादी {serial}"),
        TableCell(row=1, col=2, text=f"रु. {serial},००,०००"),
    ]
    return Table(
        row_count=2,
        col_count=3,
        cells=cells,
        regions=[
            TableRegion(
                page_number=page_number,
                x0=40.0,
                y0=60.0,
                x1=550.0,
                y1=800.0,
                page_height=height,
            )
        ],
    )


def _merged_run() -> Table:
    pages = [
        _page_table(page, str(page - FIRST_PAGE + 1))
        for page in range(FIRST_PAGE, LAST_PAGE + 1)
    ]
    merged = merge_continuation_tables(pages)
    assert len(merged) == 1, "fixture must merge into a single spanning table"
    return merged[0]


def test_the_fixture_reproduces_a_span_of_twenty_five_pages() -> None:
    merged = _merged_run()

    assert [region.page_number for region in merged.regions] == list(
        range(FIRST_PAGE, LAST_PAGE + 1)
    )
    assert LAST_PAGE - FIRST_PAGE == 24, "the measured worst fold is 24 pages"


def test_each_page_of_a_spanning_table_is_anchored_to_itself() -> None:
    # The regression. Before the fix every one of these 25 pages' rows rendered
    # as one chunk attributed to page 230, so pages 231-254 held nothing.
    chunks = render_table_page_chunks(_merged_run())

    assert [page for page, _chunk in chunks] == list(range(FIRST_PAGE, LAST_PAGE + 1))


def test_a_pages_rows_appear_under_that_pages_own_anchor() -> None:
    by_page = dict(render_table_page_chunks(_merged_run()))

    # Page 254 is 24 pages past the anchor that used to hold its text.
    assert "प्रतिवादी 25" in by_page[LAST_PAGE]
    assert "प्रतिवादी 25" not in by_page[FIRST_PAGE]
    assert "प्रतिवादी 1" in by_page[FIRST_PAGE]


def test_splitting_the_run_loses_no_row() -> None:
    """The split changes attribution only -- never what text is present."""

    merged = _merged_run()
    chunks = render_table_page_chunks(merged)

    whole = render_table_markdown(merged)
    split = "\n".join(chunk for _page, chunk in chunks)

    assert split == whole
    for serial in range(1, LAST_PAGE - FIRST_PAGE + 2):
        assert f"प्रतिवादी {serial}" in split


def test_the_repeated_header_survives_exactly_once() -> None:
    # The merge drops the header the continuation pages repeat. The split must
    # not resurrect it, and must leave the one surviving copy on the first page.
    chunks = render_table_page_chunks(_merged_run())
    split = "\n".join(chunk for _page, chunk in chunks)

    assert split.count("प्रतिवादीको नाम") == 1
    assert "प्रतिवादीको नाम" in dict(chunks)[FIRST_PAGE]


def test_a_single_page_table_renders_unchanged() -> None:
    # The overwhelming majority of tables sit on one page; those must render
    # exactly as they did, as one chunk on their own page.
    table = _page_table(FIRST_PAGE, "१")

    chunks = render_table_page_chunks(table)

    assert len(chunks) == 1
    assert chunks[0] == (FIRST_PAGE, render_table_markdown(table))


def test_a_table_with_no_regions_still_renders() -> None:
    # A producer with no geometry (a DOCX table) has no regions at all.
    table = Table(
        row_count=1,
        col_count=2,
        cells=[TableCell(row=0, col=0, text="क"), TableCell(row=0, col=1, text="ख")],
    )

    chunks = render_table_page_chunks(table)

    assert len(chunks) == 1
    assert "क" in chunks[0][1]


def test_the_caption_is_emitted_once_on_the_first_page() -> None:
    merged = _merged_run()
    merged.caption = "अनुसूची १: मुद्दा विवरण"

    chunks = render_table_page_chunks(merged)

    assert chunks[0][1].startswith("अनुसूची १: मुद्दा विवरण")
    assert sum(chunk.count("अनुसूची १") for _page, chunk in chunks) == 1


def test_a_continuation_page_carrying_only_a_header_anchors_that_header() -> None:
    """A header-only continuation page keeps its header, and now owns it.

    `_shared_header_prefix` will not drop a continuation page's every row --
    `size < next_table.row_count` stops it -- because that would erase the only
    text such a page has. So the repeated header survives as a real row, and the
    right anchor for it is the page it is printed on, not the page the run
    started on.
    """

    header_only = Table(
        row_count=1,
        col_count=3,
        cells=[TableCell(row=0, col=col, text=text) for col, text in enumerate(HEADER)],
        regions=[
            TableRegion(
                page_number=FIRST_PAGE + 1,
                x0=40.0,
                y0=60.0,
                x1=550.0,
                y1=800.0,
                page_height=842.0,
            )
        ],
    )
    merged = merge_continuation_tables([_page_table(FIRST_PAGE, "१"), header_only])
    assert len(merged) == 1

    chunks = dict(render_table_page_chunks(merged[0]))

    assert sorted(chunks) == [FIRST_PAGE, FIRST_PAGE + 1]
    assert "प्रतिवादीको नाम" in chunks[FIRST_PAGE + 1]
    # It owns only its own header row, not the previous page's data.
    assert "प्रतिवादी १" not in chunks[FIRST_PAGE + 1]


def test_merging_records_where_each_pages_rows_begin() -> None:
    merged = _merged_run()

    # Row 0 is the single surviving header; each page then contributes one row.
    assert [region.start_row for region in merged.regions[:4]] == [0, 2, 3, 4]
    assert merged.regions[-1].start_row == merged.row_count - 1


def test_ordering_keeps_a_part_with_no_page_beside_its_predecessor() -> None:
    # page 0 means "producer has no page concept". Sorting must not sweep such a
    # part to the front of the document.
    ordered = _ordered_for_anchoring([(1, "a"), (0, "still after a"), (2, "b")])

    assert ordered == [(1, "a"), (0, "still after a"), (2, "b")]


def test_ordering_puts_an_overtaken_paragraph_back_before_a_later_page() -> None:
    # A spanning table contributes its later pages at the position of the page it
    # started on, so a paragraph from a page in between arrives behind them.
    # Left alone, the interleave would attribute that paragraph to page 3.
    ordered = _ordered_for_anchoring(
        [(1, "table p1"), (2, "table p2"), (3, "table p3"), (2, "paragraph p2")]
    )

    assert ordered == [
        (1, "table p1"),
        (2, "table p2"),
        (2, "paragraph p2"),
        (3, "table p3"),
    ]


def test_ordering_leaves_already_ascending_parts_alone() -> None:
    parts = [(1, "a"), (1, "b"), (2, "c"), (5, "d")]

    assert _ordered_for_anchoring(parts) == parts
