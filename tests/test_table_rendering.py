from likhit.models import Table, TableCell
from likhit.renderers.markdown import render_table_preformatted_markdown


def test_render_table_preserves_leading_interior_and_trailing_blank_cells():
    table = Table(
        row_count=2,
        col_count=4,
        cells=[
            TableCell(row=0, col=1, text="नाम"),
            TableCell(row=0, col=3, text="रकम"),
            TableCell(row=1, col=0, text="१"),
            TableCell(row=1, col=2, text="१०००"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n|  | नाम |  | रकम |\n| १ |  | १००० |  |\n```"
    )


def test_render_table_rejoins_a_lone_wrapped_cell_into_one_row():
    # This used to render as two rows -- `| पहिलो | एक |  |` then
    # `| दोस्रो |  |  |` -- which states a row boundary the grid does not
    # contain and leaves the second line indistinguishable from a new logical
    # row carrying one value. Only column 0 wraps, so there is nothing for its
    # extra line to pair with and rejoining cannot lose an alignment.
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="पहिलो\nदोस्रो"),
            TableCell(row=0, col=1, text="एक"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| पहिलो दोस्रो | एक |  |\n```"
    )


def test_render_table_keeps_width_for_multiline_cells():
    # Rejoining must not narrow a row: every emitted line still carries the
    # table's full column count, which is what lets a reader recover the column
    # of a value from its pipe position.
    table = Table(
        row_count=1,
        col_count=4,
        cells=[
            TableCell(row=0, col=1, text="कार्यालय\nसञ्चालन"),
        ],
    )

    rendered = render_table_preformatted_markdown(table)

    assert rendered == "```text\n|  | कार्यालय सञ्चालन |  |  |\n```"
    assert rendered.splitlines()[1].count("|") == 5


def test_render_table_keeps_two_wrapped_cells_transposed():
    # Two columns each carry two lines, so the transposed form may be a real
    # aligned sub-row grid (पहिलो/एक, दोस्रो/दुई). The cell text cannot tell that
    # apart from two independent wraps, so the renderer must not collapse it.
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="पहिलो\nदोस्रो"),
            TableCell(row=0, col=1, text="एक\nदुई"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| पहिलो | एक |  |\n| दोस्रो | दुई |  |\n```"
    )


def test_render_table_does_not_join_a_figure_split_across_lines():
    # `185929593.` + `20` is one amount broken across two visual lines. A space
    # join would corrupt it to `185929593. 20`; a bare concatenation would build
    # the >=15-digit run `verify_numeric_boundaries.py` flags. Neither is this
    # renderer's decision to make, so the split stays visible.
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="१"),
            TableCell(row=0, col=1, text="185929593.\n20"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| १ | 185929593. |  |\n|  | 20 |  |\n```"
    )


def test_render_table_does_not_join_a_swallowed_sub_table():
    # A cell bbox that swallowed a nested register holds many lines, most of them
    # bare figures. Joining would mash a whole sub-table into one string. This is
    # the extraction defect M2, not a wrap, and the renderer leaves it alone.
    register = "\n".join(["190", "SCA Dalit", "2", "4", "205", "7980", "191", "2"])
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="क"),
            TableCell(row=0, col=1, text=register),
        ],
    )

    rendered = render_table_preformatted_markdown(table)

    assert "| 190 |" in rendered
    assert "190 SCA Dalit" not in rendered
    assert len(rendered.splitlines()) == 10  # fence, 8 rows, fence


def test_render_table_keeps_covered_colspan_positions():
    table = Table(
        row_count=2,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="शीर्षक", colspan=2),
            TableCell(row=0, col=2, text="रकम"),
            TableCell(row=1, col=0, text="१"),
            TableCell(row=1, col=1, text="विवरण"),
            TableCell(row=1, col=2, text="१००"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| शीर्षक |  | रकम |\n| १ | विवरण | १०० |\n```"
    )


def test_render_table_keeps_a_cell_anchored_inside_another_cells_span():
    # A malformed table can anchor a cell inside another's span. The span must
    # not blank it, because that silently drops text that was extracted.
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="शीर्षक", colspan=3),
            TableCell(row=0, col=1, text="१००"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| शीर्षक | १०० |  |\n```"
    )


def test_render_table_keeps_covered_rowspan_positions():
    table = Table(
        row_count=2,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="क्र.सं.", rowspan=2),
            TableCell(row=0, col=1, text="नाम"),
            TableCell(row=0, col=2, text="रकम"),
            TableCell(row=1, col=1, text="कार्यालय"),
            TableCell(row=1, col=2, text="१००"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| क्र.सं. | नाम | रकम |\n|  | कार्यालय | १०० |\n```"
    )
