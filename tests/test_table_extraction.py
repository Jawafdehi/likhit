"""Cell text extraction: what happens to a cell that swallowed a sub-table.

`find_tables()` sometimes fails to resolve a nested grid, and one outer cell's
bbox then covers a whole register of values. Those values arrive as one fragment
per inner *cell*, several to a printed line, and the association that says what
each amount is an amount *of* lives entirely in their geometry. VOL-91.
"""

from __future__ import annotations

import re

from likhit.extractors.base import TextFragment
from likhit.extractors.tables import _extract_cell_text, detect_page_tables

#: `tools/verify_numeric_boundaries.py`'s rule in the OAG corpus: a run this long
#: is a spliced figure, not a number anyone printed.
DIGIT_RUN = re.compile(r"[0-9०-९]{15,}")


def fragment(text: str, x0: float, y0: float, x1: float, y1: float) -> TextFragment:
    return TextFragment(text=text, page_number=1, x0=x0, y0=y0, x1=x1, y1=y1)


def inner_register_fragments() -> list[TextFragment]:
    """A 3-row, 4-column register printed inside one swallowing cell.

    Line spacing is 12pt against a 9pt glyph height, which is the shape the OAG
    documents have: consecutive lines do not overlap, values within a line do.
    """

    rows = [
        ("190", "SCA Dalit", "Seti Kamani", "7980"),
        ("191", "SCA Others", "Jibram Sunar", "12000"),
        ("192", "SCA Dalit", "Agabahadur Kami", "7980"),
    ]
    columns = [(20.0, 40.0), (50.0, 110.0), (120.0, 200.0), (210.0, 250.0)]
    fragments = []
    for row_index, row in enumerate(rows):
        y0 = 100.0 + 12.0 * row_index
        for (x0, x1), value in zip(columns, row, strict=True):
            fragments.append(fragment(value, x0, y0, x1, y0 + 9.0))
    return fragments


def test_swallowed_sub_table_keeps_each_inner_row_on_one_line() -> None:
    bbox = (10.0, 90.0, 260.0, 150.0)

    text = _extract_cell_text(inner_register_fragments(), bbox)

    assert text.splitlines() == [
        "190 SCA Dalit Seti Kamani 7980",
        "191 SCA Others Jibram Sunar 12000",
        "192 SCA Dalit Agabahadur Kami 7980",
    ]


def test_swallowed_values_are_ordered_left_to_right_not_by_arrival() -> None:
    # `page_fragments` arrives in block/line order, which need not be reading
    # order across a nested grid. The recovered row has to be in x order or the
    # values are reassociated with the wrong columns.
    fragments = list(reversed(inner_register_fragments()))

    text = _extract_cell_text(fragments, (10.0, 90.0, 260.0, 150.0))

    assert text.splitlines()[0] == "190 SCA Dalit Seti Kamani 7980"


def test_resolved_cell_keeps_one_line_per_wrapped_fragment() -> None:
    # The property that keeps this fix off the rest of the corpus: a cell the
    # detector resolved holds one fragment per visual line, so grouping by line
    # leaves it exactly as it was.
    fragments = [
        fragment("परिषद र खर्च कार्यविधि: वजेट", 20.0, 100.0, 200.0, 109.0),
        fragment("कार्यक्रम र योजना अख्तियारी", 20.0, 112.0, 200.0, 121.0),
        fragment("प्रदान गर्ने व्यावस्था छ। योजना", 20.0, 124.0, 200.0, 133.0),
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 210.0, 140.0))

    assert text.splitlines() == [
        "परिषद र खर्च कार्यविधि: वजेट",
        "कार्यक्रम र योजना अख्तियारी",
        "प्रदान गर्ने व्यावस्था छ। योजना",
    ]


def test_tightly_spaced_lines_do_not_chain_into_one_band() -> None:
    # Each line overlaps its neighbour slightly. Deciding "same line" against a
    # running band extent would chain all eight into one; the anchor is the
    # line's first fragment so it cannot.
    fragments = [
        fragment(f"{index}", 20.0, 100.0 + 8.0 * index, 40.0, 100.0 + 8.0 * index + 9.0)
        for index in range(8)
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 50.0, 180.0))

    assert text.splitlines() == [str(index) for index in range(8)]


def test_adjacent_figures_on_one_line_are_not_spliced_into_one_number() -> None:
    # A bare concatenation here would manufacture the >=15-digit runs the D7 gate
    # flags, which is why the renderer refuses to join these rows at all.
    fragments = [
        fragment("50057600070", 20.0, 100.0, 90.0, 109.0),
        fragment("1234567890", 100.0, 100.0, 170.0, 109.0),
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 180.0, 120.0))

    assert text == "50057600070 1234567890"
    assert not DIGIT_RUN.search(text)


def test_cell_text_carries_no_pipe_that_would_break_the_enclosing_row() -> None:
    text = _extract_cell_text(inner_register_fragments(), (10.0, 90.0, 260.0, 150.0))

    assert "|" not in text


def test_overprinted_duplicate_on_one_line_is_suppressed() -> None:
    # The same string drawn twice at the same place. While one fragment meant one
    # line the caller's line-level dedupe caught this; it has to keep working now
    # that the two are joined onto one line.
    fragments = [
        fragment("७९८०", 20.0, 100.0, 60.0, 109.0),
        fragment("७९८०", 20.3, 100.2, 60.3, 109.2),
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 70.0, 120.0))

    assert text == "७९८०"


def test_a_figure_repeated_across_columns_survives() -> None:
    # The other side of that dedupe: a register legitimately prints the same
    # amount in two different columns of one row, and eating the second would
    # delete data.
    fragments = [
        fragment("7980", 20.0, 100.0, 60.0, 109.0),
        fragment("7980", 120.0, 100.0, 160.0, 109.0),
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 170.0, 120.0))

    assert text == "7980 7980"


def test_repeated_figure_on_consecutive_lines_is_still_deduped() -> None:
    # Pre-existing behaviour, pinned so the change does not quietly widen it:
    # identical consecutive *lines* collapse to one.
    fragments = [
        fragment("7980", 20.0, 100.0, 60.0, 109.0),
        fragment("7980", 20.0, 112.0, 60.0, 121.0),
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 70.0, 130.0))

    assert text == "7980"


class FakeRow:
    def __init__(self, cells: list[tuple[float, float, float, float] | None]) -> None:
        self.cells = cells


class FakeFitzTable:
    """The shape `_build_table` reads off a PyMuPDF table."""

    header = None

    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        rows: list[FakeRow],
        col_count: int,
    ) -> None:
        self.bbox = bbox
        self.rows = rows
        self.col_count = col_count
        self.row_count = len(rows)


class FakeFinder:
    def __init__(self, tables: list[FakeFitzTable]) -> None:
        self.tables = tables


class FakeRect:
    height = 800.0


class FakePage:
    number = 0
    rect = FakeRect()

    def __init__(self, tables: list[FakeFitzTable]) -> None:
        self._tables = tables

    def find_tables(self) -> FakeFinder:
        return FakeFinder(self._tables)


def test_detect_page_tables_recovers_the_register_through_the_public_path() -> None:
    # A 2x2 outer grid whose bottom-right cell swallowed the register. The whole
    # point is that the outer grid is *not* wrong -- it is coarse -- so the fix
    # has to work without changing the detected grid at all.
    outer = FakeFitzTable(
        bbox=(10.0, 60.0, 260.0, 150.0),
        rows=[
            FakeRow([(10.0, 60.0, 135.0, 90.0), (135.0, 60.0, 260.0, 90.0)]),
            FakeRow([(10.0, 90.0, 135.0, 150.0), (135.0, 90.0, 260.0, 150.0)]),
        ],
        col_count=2,
    )
    fragments = [
        fragment("क्र.सं.", 20.0, 70.0, 60.0, 79.0),
        fragment("विवरण", 150.0, 70.0, 200.0, 79.0),
        fragment("१", 20.0, 100.0, 40.0, 109.0),
        # The register sits inside the (row 1, col 1) cell.
        *[
            fragment(value, x0, y0, x1, y1)
            for value, x0, y0, x1, y1 in [
                ("190", 140.0, 100.0, 160.0, 109.0),
                ("Seti Kamani", 170.0, 100.0, 230.0, 109.0),
                ("7980", 235.0, 100.0, 255.0, 109.0),
                ("191", 140.0, 112.0, 160.0, 121.0),
                ("Jibram Sunar", 170.0, 112.0, 230.0, 121.0),
                ("12000", 235.0, 112.0, 255.0, 121.0),
            ]
        ],
    ]

    tables = detect_page_tables(FakePage([outer]), fragments)

    assert len(tables) == 1
    table = tables[0]
    assert (table.row_count, table.col_count) == (2, 2)
    swallowing = next(cell for cell in table.cells if (cell.row, cell.col) == (1, 1))
    assert swallowing.text.splitlines() == [
        "190 Seti Kamani 7980",
        "191 Jibram Sunar 12000",
    ]
