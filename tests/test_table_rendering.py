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


def test_render_table_keeps_width_for_multiline_cells():
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="पहिलो\nदोस्रो"),
            TableCell(row=0, col=1, text="एक"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| पहिलो | एक |  |\n| दोस्रो |  |  |\n```"
    )


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
