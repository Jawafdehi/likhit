"""The seam between table extraction and table rendering.

Nothing tested it. ``test_table_rendering.py`` builds ``Table`` and ``TableCell``
objects by hand and renders them; ``detect_page_tables`` has no direct caller in the
suite at all, only transitive ones through ``FontBasedStrategy.extract_text``. So the
two halves are each covered and the join between them is not: a change to what the
extractor puts in a cell cannot fail a single renderer test.

That gap is not hypothetical. The renderer classifies rows by the **shape** of a
cell's text -- is this cell a bare serial number, a record-key header, a decision
fragment -- and the extractor decides what text a cell holds. The two agree today by
coincidence of authorship, not by contract, and a fix to either side can silently
reprice the other. The concrete precedent: a change that correctly kept a swallowed
sub-table's register rows separate in the extractor made those cells space-joined, so
they contained letters, so the renderer's bare-figure test stopped matching and its
rejoin mashed the whole register into one row. Each change was right on its own
fixture. Together they were worse than either alone, and no test could see it because
no test crossed the seam.

So this file asserts the *contract*, in both directions:

* what the extractor emits for a given page geometry, and
* which renderer verdicts that emission produces.

It deliberately pins current behaviour rather than changing it. Two of the
properties below are sharp edges rather than bugs -- fixing them changes rendered
output on the corpus, which is a generation decision and not a test's business.
Making them visible is.

The fixtures are ASCII on the built-in ``helv`` font, and that is a constraint rather
than laziness: the renderer's predicates accept Latin record keys (``no.``) and ASCII
serials natively, so nothing here needs a Devanagari face -- and a test that reads a
font off the host is a test that passes or fails by which machine ran it.
"""

from __future__ import annotations

import inspect
import re

import pymupdf as fitz
import pytest

from likhit.converters import nepali_pdf as nepali_pdf_module
from likhit.extractors.base import TextFragment
from likhit.extractors.tables import _EDGE_TOLERANCE, detect_page_tables
from likhit.renderers import markdown as markdown_module
from likhit.renderers.markdown import render_table_markdown

_COLUMN_EDGES = (40.0, 80.0, 240.0, 360.0)
_ROW_EDGES = (40.0, 65.0, 100.0, 125.0)
_ROWS = (
    ("no.", "particulars", "amount"),
    ("1", "office expenses", "1234.56"),
    ("2", "transport", "789.01"),
)


def _ruled_table_page(
    *,
    extra_fragments: tuple[tuple[float, float, str], ...] = (),
) -> tuple[fitz.Document, fitz.Page]:
    """A three-by-three ruled table, plus any extra text placed at (x, y)."""

    doc = fitz.open()
    page = doc.new_page(width=400, height=240)
    for x in _COLUMN_EDGES:
        page.draw_line((x, _ROW_EDGES[0]), (x, _ROW_EDGES[-1]), width=0.6)
    for y in _ROW_EDGES:
        page.draw_line((_COLUMN_EDGES[0], y), (_COLUMN_EDGES[-1], y), width=0.6)
    for row_index, row in enumerate(_ROWS):
        for col_index, text in enumerate(row):
            page.insert_text(
                (_COLUMN_EDGES[col_index] + 3, _ROW_EDGES[row_index] + 14),
                text,
                fontname="helv",
                fontsize=9,
            )
    for x, y, text in extra_fragments:
        page.insert_text((x, y), text, fontname="helv", fontsize=9)
    return doc, page


def _page_fragments(page: fitz.Page) -> list[TextFragment]:
    """One fragment per text line, as ``font_based._extract_from_document`` builds them.

    Mirrors the real construction rather than inventing fragments, because the thing
    under test is what ``detect_page_tables`` does with the fragments it is actually
    given.
    """

    fragments: list[TextFragment] = []
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block_number, block in enumerate(page_dict["blocks"]):
        for line_number, line in enumerate(block.get("lines", ())):
            text = "".join(span["text"] for span in line.get("spans", ())).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            fragments.append(
                TextFragment(
                    text=text,
                    page_number=1,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    block_number=block_number,
                    line_number=line_number,
                )
            )
    return fragments


def _extract_one_table(**kwargs):
    doc, page = _ruled_table_page(**kwargs)
    try:
        tables = detect_page_tables(page, _page_fragments(page))
    finally:
        doc.close()
    assert len(tables) == 1, f"fixture must yield exactly one table, got {len(tables)}"
    return tables[0]


def _cell(table, row: int, col: int) -> str:
    for cell in table.cells:
        if cell.row == row and cell.col == col:
            return cell.text
    return ""


# --------------------------------------------------------------------------- #
# 1. The round trip that did not exist.
# --------------------------------------------------------------------------- #


def test_detect_page_tables_output_renders_end_to_end():
    """PDF -> detect_page_tables -> render_table_markdown, in one test.

    Everything below narrows this. If this breaks, the seam moved and the narrower
    assertions will say where.
    """

    table = _extract_one_table()

    assert (table.row_count, table.col_count) == (3, 3)
    assert render_table_markdown(table) == (
        "| no. | particulars | amount |\n"
        "| 1 | office expenses | 1234.56 |\n"
        "| 2 | transport | 789.01 |"
    )


# --------------------------------------------------------------------------- #
# 2. What the extractor puts in a cell.
# --------------------------------------------------------------------------- #


def test_a_cell_with_one_fragment_carries_that_fragment_and_no_newline():
    """The precondition of every renderer shape test, stated where it is produced.

    ``_extract_cell_text`` joins the fragments whose centre falls in a cell with
    ``"\\n"``. With one fragment there is no separator, which is the only reason a
    cell text can be ``fullmatch``-ed at all.
    """

    table = _extract_one_table()

    assert _cell(table, 1, 0) == "1"
    assert "\n" not in _cell(table, 1, 0)


def test_a_second_fragment_in_the_same_cell_is_newline_joined():
    # A footnote marker on a second line inside the serial cell -- an ordinary thing
    # for an audit table to contain.
    table = _extract_one_table(
        extra_fragments=((_COLUMN_EDGES[0] + 3, _ROW_EDGES[1] + 28, "(a)"),)
    )

    assert _cell(table, 1, 0) == "1\n(a)"


def test_cell_membership_is_decided_by_the_fragment_centre_not_by_overlap():
    """Overlap is not membership, and that is load-bearing at the seam.

    A wide fragment that starts inside the serial column but is centred in the next
    one belongs to the next one. If this were overlap-based, the serial cell would
    acquire prose and stop being a serial -- flipping every classifier in section 3.
    """

    # Measured, not computed: "carried forward" at 9pt helv is 60pt wide, so starting
    # it 10pt inside the serial column's right edge spans x=70..130 -- overlapping the
    # serial column by 10pt while centred at 100, inside the second column (80..240).
    table = _extract_one_table(
        extra_fragments=(
            (_COLUMN_EDGES[1] - 10, _ROW_EDGES[2] + 14, "carried forward"),
        )
    )

    assert _cell(table, 2, 0) == "2"
    assert "carried forward" in _cell(table, 2, 1)


def test_edge_tolerance_is_the_slack_on_that_centre_test():
    # Pinned exactly rather than to a range: a wider tolerance pulls neighbouring
    # fragments into a cell, which is the failure mode above.
    assert _EDGE_TOLERANCE == 1.5


# --------------------------------------------------------------------------- #
# 3. What those emissions make the renderer decide.
#
# This is the sharp edge, and it is pinned rather than fixed. Every one of these
# classifiers uses `fullmatch` or an anchored pattern with no `re.MULTILINE`, so a
# newline anywhere in a cell makes the verdict False -- whatever the first line says.
# --------------------------------------------------------------------------- #

_NEWLINE_SENSITIVE_CLASSIFIERS = (
    ("_looks_like_data_key", "1", "1\n(a)"),
    ("_is_record_key_header", "no.", "no.\n(a)"),
    ("_is_decision_fragment", "1", "1\n(a)"),
    ("_looks_like_page_furniture", "1", "1\n(a)"),
)


@pytest.mark.parametrize(
    ("name", "single", "joined"), _NEWLINE_SENSITIVE_CLASSIFIERS, ids=lambda v: str(v)
)
def test_a_newline_joined_cell_defeats_every_shape_classifier(name, single, joined):
    classifier = getattr(markdown_module, name)

    assert classifier(single) is True
    assert classifier(joined) is False


def test_no_shape_pattern_in_the_renderer_is_multiline_aware():
    """Which is *why* section 3 holds, kept separate so the cause is asserted too.

    A future author adding ``re.MULTILINE`` to make one of these newline-tolerant
    would be making a corpus-visible change, and this is where they find out.
    """

    for name, pattern in vars(markdown_module).items():
        if isinstance(pattern, re.Pattern):
            assert not pattern.flags & re.MULTILINE, name


def test_a_newline_joined_serial_cell_moves_the_data_start_and_adds_a_row():
    """The consequence on OUTPUT, which is what makes the classifiers above matter.

    Without this, section 3 would only pin predicate return values -- true but inert.
    One extra fragment in one cell moves ``_data_start`` and materialises a row that
    is not in the source table.
    """

    plain = _extract_one_table()
    footnoted = _extract_one_table(
        extra_fragments=((_COLUMN_EDGES[0] + 3, _ROW_EDGES[1] + 28, "(a)"),)
    )

    def data_start(table) -> int:
        grid = markdown_module._expanded_grid(table)
        return markdown_module._data_start(grid, markdown_module._title_row_count(grid))

    assert data_start(plain) == 1
    assert data_start(footnoted) == 2

    assert render_table_markdown(plain).count("\n") == 2
    assert render_table_markdown(footnoted) == (
        "| no. | particulars | amount |\n"
        "| 1 | office expenses | 1234.56 |\n"
        "| (a) |  |  |\n"
        "| 2 | transport | 789.01 |"
    )


# --------------------------------------------------------------------------- #
# 4. The predicate that exists twice on this seam.
# --------------------------------------------------------------------------- #


def test_the_page_furniture_predicate_is_defined_twice_and_the_copies_agree():
    """``_looks_like_page_furniture`` lives in BOTH the converter and the renderer.

    Byte-identical today. They are separate functions on either side of the seam, so
    a fix applied to one is a divergence -- and the two paths that call them decide
    the same question about the same block. The known pending fix is a length bound:
    the predicate discards any block containing a running-head phrase, including a
    216-character paragraph that merely mentions it. Whoever lands that must land it
    twice, and this is what tells them.

    Deliberately not merged here. Collapsing the copies means deciding which module
    owns the shared helper, which is a refactor with its own review, not a gap.
    """

    renderer = markdown_module._looks_like_page_furniture
    converter = nepali_pdf_module._looks_like_page_furniture

    assert renderer is not converter, (
        "the copies were merged -- good; delete this test and keep the agreement "
        "assertion below only if a shared helper still has two call sites"
    )
    assert inspect.getsource(renderer) == inspect.getsource(converter)

    for text in (
        "12",
        "123",
        "1234",
        "  7 ",
        "2 परिच्छेद",
        "वार्षिक प्रतिवेदन",
        "परिच्छेद",
        "0",
        "",
        "क" * 216,
    ):
        assert renderer(text) == converter(text), text
