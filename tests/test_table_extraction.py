"""Cell text extraction: what happens to a cell that swallowed a sub-table.

`find_tables()` sometimes fails to resolve a nested grid, and one outer cell's
bbox then covers a whole register of values. Those values arrive as one fragment
per inner *cell*, several to a printed line, and the association that says what
each amount is an amount *of* lives entirely in their geometry. VOL-91.
"""

from __future__ import annotations

import re

from likhit.extractors.base import TextFragment
from likhit.extractors.tables import (
    _drop_frame_cells,
    _extract_cell_text,
    _same_printed_position,
    detect_page_tables,
)
from likhit.models import TableCell

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


def test_a_repeat_in_a_barely_overlapping_column_survives() -> None:
    # Overprint means the same drawing origin, not merely an overlapping one.
    # Adjacent columns of a register overlap, and treating that as overprint deleted
    # a genuine repeat of `- डिल्लि धिमाल` on
    # `3172__1613896170विराटनगर महानगरपालिका`.
    #
    # NB the overlap built here is 10 points (100-200 against 190-290), not the
    # "point or two" the prose elsewhere describes. Ten is well clear of the 1.5-pt
    # tolerance, so this fixture pins that an overlap is not an origin match -- it
    # does NOT pin the tolerance boundary, and it should not be read as doing so.
    fragments = [
        fragment("- डिल्लि धिमाल", 100.0, 100.0, 200.0, 109.0),
        fragment("- डिल्लि धिमाल", 190.0, 100.0, 290.0, 109.0),
    ]

    text = _extract_cell_text(fragments, (90.0, 90.0, 300.0, 120.0))

    assert text == "- डिल्लि धिमाल - डिल्लि धिमाल"


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
    # identical consecutive single-fragment lines collapse to one.
    fragments = [
        fragment("7980", 20.0, 100.0, 60.0, 109.0),
        fragment("7980", 20.0, 112.0, 60.0, 121.0),
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 70.0, 130.0))

    assert text == "7980"


def test_a_register_row_printed_twice_is_kept_twice() -> None:
    # The other half of that dedupe, and the one regression grouping can cause.
    # `5852__pLpP31685271785मिर्चैया नगरपालिका, २०७८।७९` prints this row three
    # times; while one fragment meant one line the three copies produced
    # different line sequences and survived, but once a whole row is a single
    # joined line the consecutive-line dedupe would collapse them and delete two
    # of the three. The dedupe therefore stays fragment-scoped.
    row = [
        ("१", 20.0, 40.0),
        ("Bhulli Devi Mahara", 50.0, 150.0),
        ("४२८५६", 160.0, 200.0),
        ("१५३०९४५२३", 210.0, 270.0),
    ]
    fragments = [
        fragment(value, x0, 100.0 + 12.0 * line, x1, 109.0 + 12.0 * line)
        for line in range(3)
        for value, x0, x1 in row
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 280.0, 150.0))

    assert text.splitlines() == ["१ Bhulli Devi Mahara ४२८५६ १५३०९४५२३"] * 3


def stacked_rows_in_one_group() -> list[TextFragment]:
    """One tall fragment plus three stacked rows that group onto its visual line.

    This is the geometry VOL-119 was measured on. Grouping anchors on a group's
    *first* fragment, so a label tall enough to span the register pulls all three
    rows into one group even though the rows do not overlap each other. Each row
    then repeats `10000` at the same x and a different y.
    """

    fragments = [fragment("बाल विवाह न्यूनिकरण", 20.0, 100.0, 140.0, 136.0)]
    for row_index in range(3):
        y0 = 100.0 + 12.0 * row_index
        fragments.append(fragment("10000", 160.0, y0, 200.0, y0 + 9.0))
    return fragments


def test_a_figure_repeated_down_a_column_of_a_grouped_register_survives() -> None:
    # VOL-119. Same x, different y, all in one group: three register rows each
    # carrying `10000`. Suppressing on horizontal position alone read that as
    # overprint and deleted two of the three -- 15 tokens over 5 documents, worst
    # `2446__16126986953_Ghyanglekh RM_Sindhuli`, whose count fell 8 -> 6.
    text = _extract_cell_text(stacked_rows_in_one_group(), (10.0, 90.0, 210.0, 145.0))

    assert text == "बाल विवाह न्यूनिकरण 10000 10000 10000"


def test_overprint_inside_a_grouped_register_is_still_suppressed() -> None:
    # The other side of VOL-119's fix: adding the vertical test must not stop
    # catching real overprint just because grouping merged stacked rows. This
    # fragment sits at the same x *and* the same y as the middle row, so it is a
    # double-drawn glyph run and must not reach the output.
    #
    # Its x0 matches that row's exactly: the dedupe compares each fragment with
    # the previous *kept* one, and a group is ordered by x0, so an overprint has
    # to sort adjacent to its twin to be seen at all.
    fragments = stacked_rows_in_one_group()
    fragments.append(fragment("10000", 160.0, 112.2, 200.2, 121.2))

    text = _extract_cell_text(fragments, (10.0, 90.0, 210.0, 145.0))

    assert text == "बाल विवाह न्यूनिकरण 10000 10000 10000"


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


def test_table_is_accepted_before_duplicating_frame_cells_are_removed() -> None:
    """A frame may supply the second populated row and column at acceptance time."""

    framed = FakeFitzTable(
        bbox=(0.0, 0.0, 100.0, 100.0),
        rows=[
            FakeRow([(0.0, 0.0, 100.0, 100.0), None]),
            FakeRow([None, (50.0, 50.0, 100.0, 100.0)]),
        ],
        col_count=2,
    )
    fragments = [fragment("नेपाल", 60.0, 60.0, 90.0, 70.0)]

    tables = detect_page_tables(FakePage([framed]), fragments)

    assert len(tables) == 1
    assert [(cell.row, cell.col, cell.text) for cell in tables[0].cells] == [
        (1, 1, "नेपाल")
    ]


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


def test_a_blank_fragment_on_the_line_does_not_disable_the_repeat_suppression() -> None:
    """A defect in the fragment-scoping itself, fixed here rather than shipped.

    The scope test counted every fragment on the visual line, including ones whose text
    is blank and which therefore contribute nothing to the joined line. A single
    whitespace-only fragment sharing the line made it look multi-fragment and switched
    the suppression off, so an overprinted duplicate survived.

    All three cases are asserted together, because the fix is only correct if the middle
    one changes and the outer two do not.
    """

    bbox = (0.0, 90.0, 300.0, 160.0)

    def frag(text: str, x0: float, y0: float) -> TextFragment:
        return TextFragment(
            text=text, page_number=1, x0=x0, y0=y0, x1=x0 + 30, y1=y0 + 9
        )

    # one fragment per line, overprinted: suppressed, and always was
    plain = [frag("७९८०", 20, 100.0), frag("७९८०", 20, 112.0)]
    assert _extract_cell_text(plain, bbox).splitlines() == ["७९८०"]

    # the same, plus a whitespace-only fragment on each line: was NOT suppressed
    with_blank = [
        frag("७९८०", 20, 100.0),
        frag("   ", 60, 100.0),
        frag("७९८०", 20, 112.0),
        frag("   ", 60, 112.0),
    ]
    assert _extract_cell_text(with_blank, bbox).splitlines() == ["७९८०"]

    # genuinely multi-fragment: must still survive, or the scoping this change adds is
    # gone and a legitimately repeated register row would be deleted
    register = [
        frag("१", 20, 100.0),
        frag("Bhulli", 60, 100.0),
        frag("१", 20, 112.0),
        frag("Bhulli", 60, 112.0),
    ]
    assert _extract_cell_text(register, bbox).splitlines() == ["१ Bhulli", "१ Bhulli"]


# ------------------------------------------ overprint: one edge at a time, and the extent


def test_a_double_strike_bold_overprint_is_suppressed() -> None:
    """The regression an all-four-edges comparison let through, at corpus scale.

    Geometry from `11102__m6t-Annual Report 2067`: the same string at the same origin
    on the same baseline, about 5 pt narrower -- a bold double-strike. Comparing the
    EXTENT as well made this a non-match, so 1,308 such pairs across 13 documents were
    emitted twice, duplicating table headers and totals
    (`| जम्मा जम्मा | ... | ४,४५७ ४,४५७ |`, 185 lines in that one document).
    """

    fragments = [
        fragment("जम्मा", 20.0, 100.0, 60.0, 109.0),
        fragment("जम्मा", 20.0, 100.24, 55.0, 109.24),
    ]

    text = _extract_cell_text(fragments, (10.0, 90.0, 70.0, 120.0))

    assert text == "जम्मा"


def _shifted(dx0: float = 0.0, dy0: float = 0.0, dx1: float = 0.0, dy1: float = 0.0):
    """The same run twice, with exactly one edge displaced."""

    return (
        fragment("10000", 160.0, 100.0, 200.0, 109.0),
        fragment("10000", 160.0 + dx0, 100.0 + dy0, 200.0 + dx1, 109.0 + dy1),
    )


def test_each_edge_is_pinned_on_its_own() -> None:
    """🛑 The four clauses were all mutation SURVIVORS, and this is why.

    `stacked_rows_in_one_group` moves `y0` and `y1` together and holds `x0` and `x1`
    together, so it cannot tell "same `x0`" from "same `x0` and `x1`", nor `y0` from
    `y1`. Measured against the full suite while that was the only fixture: deleting the
    `x1` clause, the `y1` clause or the `y0` clause each left 1068 passing. Only `x0`
    was pinned, and `x1` alone carried 83% of the change's corpus effect.

    Asserted on the predicate rather than end to end, deliberately: a y displacement
    large enough to be unambiguous also splits the pair across visual lines, where the
    *line*-level dedupe collapses it for an unrelated reason -- so an end-to-end
    fixture cannot isolate the vertical clause at all.
    """

    # Identical: overprint.
    assert _same_printed_position(*_shifted()) is True

    # ORIGIN displaced -> a different place, so not overprint. Both must bite.
    assert _same_printed_position(*_shifted(dx0=12.0)) is False, "x0 unpinned"
    assert _same_printed_position(*_shifted(dy0=12.0)) is False, "y0 unpinned"

    # EXTENT displaced only -> still one drawing, at two weights or sizes.
    assert _same_printed_position(*_shifted(dx1=6.0)) is True
    assert _same_printed_position(*_shifted(dy1=6.0)) is True


def test_the_origin_tolerance_is_the_boundary_the_corpus_needs() -> None:
    """Both sides of the 1.5-pt slack, and the CIAA case that fixes the upper side.

    The 0.24-pt baseline jitter of a double-strike must be inside it, and the CIAA
    bullet pair -- two distinct list items 3.723 pt apart on
    `2077-78` p256, each with its own text in its own block -- must be outside, or the
    dedupe deletes a real list marker.
    """

    assert _same_printed_position(*_shifted(dy0=0.24)) is True
    assert _same_printed_position(*_shifted(dy0=3.723)) is False


# ---------------------------------------------------------------------------
# VOL-744: a ruled FRAME read as a cell makes the page say everything twice.
#
# Measured on the OAG corpus: 2,423 pages of 309,231, 634,084 duplicated word
# tokens across 981 of 6,235 documents, in three successive generations. The
# witness is `11781__ललितपुर महानगरपालिका.pdf` p105, whose `find_tables()` grid is
# 17x9 with an `r0 c1` spanning 16x8 -- the whole grid -- carrying all 1,958
# characters that its 90 inner cells already carry.
#
# The scaffolding above (FakeFitzTable and friends) is reused deliberately: the
# defect is in how `_build_table` reads a grid, so it must be provable without a
# PDF.
# ---------------------------------------------------------------------------


def framed_register_table() -> FakeFitzTable:
    """A 3x3 grid whose row 0 holds one cell covering the WHOLE grid.

    This is the shape p105 has. The frame's bbox spans every column and every row,
    so `_extract_cell_text` reads each inner value into it a second time.
    """
    return FakeFitzTable(
        bbox=(10.0, 60.0, 250.0, 150.0),
        rows=[
            # The frame: one cell over the entire table.
            FakeRow([(10.0, 60.0, 250.0, 150.0)]),
            FakeRow(
                [
                    (10.0, 90.0, 90.0, 120.0),
                    (90.0, 90.0, 170.0, 120.0),
                    (170.0, 90.0, 250.0, 120.0),
                ]
            ),
            FakeRow(
                [
                    (10.0, 120.0, 90.0, 150.0),
                    (90.0, 120.0, 170.0, 150.0),
                    (170.0, 120.0, 250.0, 150.0),
                ]
            ),
        ],
        col_count=3,
    )


def framed_register_fragments() -> list[TextFragment]:
    return [
        fragment("१", 20.0, 100.0, 40.0, 109.0),
        fragment("सडक मर्मत", 100.0, 100.0, 160.0, 109.0),
        fragment("२४५९९९", 180.0, 100.0, 240.0, 109.0),
        fragment("२", 20.0, 130.0, 40.0, 139.0),
        fragment("खानेपानी", 100.0, 130.0, 160.0, 139.0),
        fragment("४९३२८०", 180.0, 130.0, 240.0, 139.0),
    ]


def test_a_frame_cell_covering_the_grid_does_not_duplicate_the_page() -> None:
    """Every value appears exactly ONCE. Before the fix each appeared twice."""

    tables = detect_page_tables(
        FakePage([framed_register_table()]), framed_register_fragments()
    )

    assert len(tables) == 1
    table = tables[0]
    # The grid itself is unchanged -- the frame is not a detection error to be
    # re-detected, it is a cell that must not carry text.
    assert (table.row_count, table.col_count) == (3, 3)

    # Compared as a multiset of whole cell texts, never with `count()` on the
    # joined string: a Devanagari digit is a SUBSTRING of the longer figures here
    # (`२` sits inside `२४५९९९` and `४९३२८०`), so substring counting reports 3 for a
    # correct transcript. My own first version of this assertion failed that way.
    assert sorted(cell.text for cell in table.cells if cell.text) == sorted(
        ["१", "सडक मर्मत", "२४५९९९", "२", "खानेपानी", "४९३२८०"]
    )

    # And the frame is gone rather than merely blanked, so nothing downstream has
    # to know to skip it.
    assert not [cell for cell in table.cells if cell.rowspan == 3 and cell.colspan == 3]


def test_a_merged_header_that_contains_no_other_cell_survives() -> None:
    """The control that keeps the rule honest -- a real spanned header is NOT a frame.

    Sizing the rule on `rowspan`/`colspan` would delete this. Containment is what
    separates them: this header's rectangle holds no other cell, because
    `find_tables()` gives each grid region to exactly one cell.
    """

    merged = FakeFitzTable(
        bbox=(10.0, 60.0, 250.0, 120.0),
        rows=[
            FakeRow([(10.0, 60.0, 250.0, 90.0)]),
            FakeRow([(10.0, 90.0, 130.0, 120.0), (130.0, 90.0, 250.0, 120.0)]),
        ],
        col_count=2,
    )
    fragments = [
        fragment("आर्थिक विवरण", 20.0, 70.0, 200.0, 79.0),
        fragment("शीर्षक", 20.0, 100.0, 60.0, 109.0),
        fragment("रकम", 140.0, 100.0, 180.0, 109.0),
    ]

    tables = detect_page_tables(FakePage([merged]), fragments)

    assert len(tables) == 1
    header = next(cell for cell in tables[0].cells if (cell.row, cell.col) == (0, 0))
    assert header.colspan == 2
    assert header.text == "आर्थिक विवरण"


def test_only_a_containing_cell_is_dropped_and_equal_rectangles_are_kept() -> None:
    """The predicate on its own, at each edge that decides a real corpus page."""

    # A frame whose words the inner cells already hold: a duplicate, so it goes.
    frame = TableCell(row=0, col=0, text="एक दुई", rowspan=3, colspan=3)
    inner_one = TableCell(row=1, col=1, text="एक")
    inner_two = TableCell(row=1, col=2, text="दुई")
    assert _drop_frame_cells([frame, inner_one, inner_two]) == [inner_one, inner_two]

    # Tall-only and wide-only frames both contain, and both must go.
    tall = TableCell(row=0, col=0, text="एक", rowspan=3, colspan=1)
    in_column = TableCell(row=1, col=0, text="एक")
    assert _drop_frame_cells([tall, in_column]) == [in_column]
    wide = TableCell(row=0, col=0, text="एक", rowspan=1, colspan=3)
    in_row = TableCell(row=0, col=1, text="एक")
    assert _drop_frame_cells([wide, in_row]) == [in_row]

    # A span that contains nothing is a merged cell, not a frame.
    beside = TableCell(row=1, col=0, text="एक")
    assert _drop_frame_cells([wide, beside]) == [wide, beside]

    # Equal rectangles: neither is the other's frame, so the rule declines to
    # choose. A different defect with a different fix.
    twin_a = TableCell(row=0, col=0, text="क", rowspan=2, colspan=2)
    twin_b = TableCell(row=0, col=0, text="ख", rowspan=2, colspan=2)
    assert _drop_frame_cells([twin_a, twin_b]) == [twin_a, twin_b]

    # A grid with no spanning cell at all is returned untouched.
    plain = [TableCell(row=0, col=0, text="क"), TableCell(row=0, col=1, text="ख")]
    assert _drop_frame_cells(plain) == plain


def test_a_spanning_cell_holding_the_only_copy_is_kept() -> None:
    """Containment alone is not enough, and assuming it was destroyed real pages.

    `11727__भरतपुर महानगरपालिका.pdf` p101 is one 6x11 grid whose spanning cell carries
    143 of the page's 169 tokens while the 33 cells inside it hold 26 between them --
    they are largely EMPTY, so the frame is the only copy. Containment-only took that
    page from 101 distinct words to 21, and did the same to 27 of 1,400 pages on the
    paired control sweep.
    """

    frame = TableCell(row=0, col=0, text="कुल जम्मा रकम", rowspan=3, colspan=3)
    inner = TableCell(row=1, col=1, text="रकम")
    empty = TableCell(row=1, col=2, text="")

    # `कुल` and `जम्मा` live nowhere else, so the frame is content, not a duplicate.
    assert _drop_frame_cells([frame, inner, empty]) == [frame, inner, empty]

    # Vary ONE thing: give the inner cells those two words as well, and the same
    # frame becomes a duplicate and goes.
    covered = TableCell(row=1, col=1, text="कुल जम्मा रकम")
    assert _drop_frame_cells([frame, covered, empty]) == [covered, empty]


# ---------------------------------------------------------------------------
# VOL-744, second cause: TWO grids for one printed table -- a coarse one whose
# few cells swallow the page, and the real one. The frame-cell rule above cannot
# see this, because the container is a grid of its own rather than a cell.
#
# Witness `12788__मालिकार्जुन गाउँपालिका, दार्चुला.pdf` p61: a 5-cell grid over the
# whole page (0,0,792,612) beside a 629-cell grid at (51.5,30.3,741.1,588.0),
# both carrying the same 674 tokens over the same 105 distinct words.
# ---------------------------------------------------------------------------


def coarse_and_fine_tables() -> list[FakeFitzTable]:
    """A coarse 2x3 grid over the page, and the real 2x3 grid inside it.

    The coarse grid's row 0 is one cell covering everything above the footer, so it
    swallows every value the fine grid resolves; its row 1 is the page-furniture
    footer, which the fine grid does NOT hold.

    The footer's THIRD column is left empty on purpose. p61's real footer has one
    (`| 8 of 10 | https://... |  |`), and a rule that strips empty cells as
    "covered" -- the empty set is a subset of anything -- would delete it and shift
    the row a column left.
    """
    coarse = FakeFitzTable(
        bbox=(0.0, 0.0, 300.0, 200.0),
        rows=[
            FakeRow([(0.0, 0.0, 300.0, 160.0)]),
            FakeRow(
                [
                    (0.0, 160.0, 100.0, 200.0),
                    (100.0, 160.0, 200.0, 200.0),
                    (200.0, 160.0, 300.0, 200.0),
                ]
            ),
        ],
        col_count=3,
    )
    fine = FakeFitzTable(
        bbox=(10.0, 10.0, 290.0, 150.0),
        rows=[
            FakeRow(
                [
                    (10.0, 10.0, 100.0, 80.0),
                    (100.0, 10.0, 190.0, 80.0),
                    (190.0, 10.0, 290.0, 80.0),
                ]
            ),
            FakeRow(
                [
                    (10.0, 80.0, 100.0, 150.0),
                    (100.0, 80.0, 190.0, 150.0),
                    (190.0, 80.0, 290.0, 150.0),
                ]
            ),
        ],
        col_count=3,
    )
    return [coarse, fine]


FINE_VALUES = ("क्र", "विद्यालय", "शिक्षक", "१", "श्री", "४")


def coarse_and_fine_fragments() -> list[TextFragment]:
    return [
        fragment("क्र", 20.0, 20.0, 60.0, 29.0),
        fragment("विद्यालय", 110.0, 20.0, 170.0, 29.0),
        fragment("शिक्षक", 200.0, 20.0, 260.0, 29.0),
        fragment("१", 20.0, 90.0, 60.0, 99.0),
        fragment("श्री", 110.0, 90.0, 170.0, 99.0),
        fragment("४", 200.0, 90.0, 260.0, 99.0),
        # The footer, which only the coarse grid covers.
        fragment("8 of 10", 5.0, 170.0, 80.0, 179.0),
        fragment("https://nams.oag.gov.np Page 58 of 64", 105.0, 170.0, 195.0, 179.0),
    ]


def test_a_coarse_grid_over_a_finer_one_does_not_duplicate_the_page() -> None:
    tables = detect_page_tables(
        FakePage(coarse_and_fine_tables()), coarse_and_fine_fragments()
    )

    assert len(tables) == 2
    texts = [cell.text for table in tables for cell in table.cells if cell.text.strip()]
    for value in FINE_VALUES:
        assert sum(1 for text in texts if text == value) == 1, f"{value!r} in {texts}"
    # The swallowing cell held every value at once, so no surviving cell may hold
    # more than one of them.
    assert not [
        text for text in texts if sum(value in text for value in FINE_VALUES) > 1
    ]


def test_the_coarse_grid_keeps_the_page_footer_it_alone_holds() -> None:
    """Dropping the container WHOLE was tried and lost this row. It must survive.

    The footer is the only record of which printed page a transcript page came from,
    and the fine grid does not carry it.
    """

    tables = detect_page_tables(
        FakePage(coarse_and_fine_tables()), coarse_and_fine_fragments()
    )

    coarse = tables[0]
    texts = [cell.text for cell in coarse.cells]
    assert "8 of 10" in texts
    assert "https://nams.oag.gov.np Page 58 of 64" in texts
    # And the footer's empty third cell, or the row loses a column.
    assert sum(1 for text in texts if not text.strip()) == 1
    assert len(coarse.cells) == 3


def test_a_container_holding_content_of_its_own_is_left_alone() -> None:
    """The condition that makes the strip safe, exercised on its own.

    Vary ONE thing against the fixture above: the swallowing cell also holds a word
    the fine grid does not (a total). Its content is then no longer covered, so it
    is not a duplicate and must be kept whole -- otherwise the total is deleted.
    """

    fragments = [
        *coarse_and_fine_fragments(),
        # Inside the coarse row-0 cell, outside the fine grid's region.
        fragment("जम्मा", 292.0, 20.0, 299.0, 29.0),
    ]

    tables = detect_page_tables(FakePage(coarse_and_fine_tables()), fragments)

    joined = "\n".join(cell.text for table in tables for cell in table.cells)
    assert "जम्मा" in joined
    # Kept whole: the swallowing cell is still there, so every value appears twice.
    texts = [cell.text for table in tables for cell in table.cells]
    assert any("क्र" in text and "शिक्षक" in text for text in texts)


def test_a_grid_no_coarser_than_the_one_it_encloses_is_left_alone() -> None:
    """The `>=` boundary of the coarser guard, on a genuinely nested table.

    `outer` encloses `inner` and its bottom-right cell holds exactly `inner`'s four
    values, so the coverage rule ALONE would strip it. What stops that is the coarser
    guard: 4 cells against 4 is not coarser, so neither grid strips the other.

    ⚠️ The first version of this test gave `inner` `col_count=1`, which
    `_is_accepted_table` rejects outright -- so `outer` contained nothing, the guard was
    never reached, and the test passed with the guard mutated away. Both grids must be
    ACCEPTED for this to test anything.
    """

    outer = FakeFitzTable(
        bbox=(0.0, 0.0, 300.0, 200.0),
        rows=[
            FakeRow([(0.0, 0.0, 150.0, 100.0), (150.0, 0.0, 300.0, 100.0)]),
            FakeRow([(0.0, 100.0, 150.0, 200.0), (150.0, 100.0, 300.0, 200.0)]),
        ],
        col_count=2,
    )
    inner = FakeFitzTable(
        bbox=(160.0, 110.0, 290.0, 190.0),
        rows=[
            FakeRow([(160.0, 110.0, 225.0, 150.0), (225.0, 110.0, 290.0, 150.0)]),
            FakeRow([(160.0, 150.0, 225.0, 190.0), (225.0, 150.0, 290.0, 190.0)]),
        ],
        col_count=2,
    )
    fragments = [
        fragment("क", 10.0, 20.0, 60.0, 29.0),
        fragment("ख", 160.0, 20.0, 210.0, 29.0),
        fragment("ग", 10.0, 120.0, 60.0, 129.0),
        fragment("घ", 170.0, 120.0, 220.0, 129.0),
        fragment("ङ", 235.0, 120.0, 285.0, 129.0),
        fragment("च", 170.0, 160.0, 220.0, 169.0),
        fragment("छ", 235.0, 160.0, 285.0, 169.0),
    ]

    tables = detect_page_tables(FakePage([outer, inner]), fragments)

    assert len(tables) == 2
    assert [len(table.cells) for table in tables] == [4, 4]
    outer_built = tables[0]
    assert sorted(cell.text for cell in outer_built.cells) == sorted(
        ["क", "ख", "ग", "घ ङ\nच छ"]
    )
