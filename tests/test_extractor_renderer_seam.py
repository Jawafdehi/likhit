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

import ast
import inspect
from pathlib import Path
import re
import textwrap

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
    # `Table`/`TableCell` are slotted dataclasses holding no page handle (see
    # models/types.py), so using the result after `doc.close()` is safe. Worth stating,
    # because "use an extraction result after closing the document" is normally a bug.
    doc, page = _ruled_table_page(**kwargs)
    try:
        tables = detect_page_tables(page, _page_fragments(page))
    finally:
        doc.close()
    assert len(tables) == 1, (
        f"expected exactly one table from this fixture, got {len(tables)}. Either the "
        "fixture stopped describing a ruled table, or table DETECTION regressed -- the "
        "second is the likelier cause and the more serious one."
    )
    return tables[0]


def _cell(table, row: int, col: int) -> str:
    # Raises rather than returning "": an `in`-style assertion against a missing cell
    # would otherwise read as a text mismatch, and a missing cell is a different and
    # more serious failure.
    for cell in table.cells:
        if cell.row == row and cell.col == col:
            return cell.text
    raise AssertionError(
        f"no cell at ({row}, {col}); table has "
        f"{sorted((c.row, c.col) for c in table.cells)}"
    )


# --------------------------------------------------------------------------- #
# 1. The round trip that did not exist.
# --------------------------------------------------------------------------- #


def test_detect_page_tables_output_renders_end_to_end():
    """PDF -> detect_page_tables -> render_table_markdown, in one test.

    Everything below narrows this. If this breaks, the seam moved and the narrower
    assertions will say where.

    ⚠️ A THIRD SHARP EDGE, disclosed here because this is the first assertion a reader
    meets: the output below is **not a GFM table**. There is no ``|---|---|---|``
    delimiter row, and ``render_table_markdown`` never emits one -- the only ``---`` in
    the renderer is frontmatter. Under GFM a pipe table without a delimiter row is three
    lines of literal text with pipes in them, not a table. That is the shipped behaviour
    and consumers treat it as text, so it is pinned as-is; it is recorded because a
    reader will otherwise take this assertion for the renderer's contract with Markdown,
    and it is not one.
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
    #
    # Also pinned in tests/test_tuning_constants.py, as one of a closed set. Two files
    # asserting one value is deliberate -- here for the reason it matters at the seam,
    # there so no tuning constant is free -- but they move together.
    assert _EDGE_TOLERANCE == 1.5


# --------------------------------------------------------------------------- #
# 3. What those emissions make the renderer decide.
#
# The sharp edge, pinned rather than fixed.
#
# 🛑 It is NOT true that every classifier here is newline-sensitive, and an earlier
# version of this file said so. Each has several legs, and only some legs care:
#
#   _looks_like_data_key         one leg,   `_SERIAL_PATTERN.fullmatch`
#   _is_record_key_header        no pattern at all -- whitespace-stripped set
#                                membership, so it is newline-sensitive by
#                                COLLAPSING, and no re flag could change that
#   _is_decision_fragment        two legs; `_DATE_CASE_PATTERN.search` is UNANCHORED
#                                and survives a newline in either position
#   _looks_like_page_furniture   three legs; `re.match(r"^\d+\s*परिच्छेद")` is
#                                start-anchored (so a PREFIX kills it, a SUFFIX does
#                                not), the `in compact` substring leg is newline-blind,
#                                and the isdigit leg dies on either
#
# So the table below names the LEG, and each row is a measured verdict rather than an
# instance of a rule. Rows that stay True are as load-bearing as the ones that go
# False: they are where "just add re.MULTILINE" would change nothing and a reader
# would conclude the guard was working.
# --------------------------------------------------------------------------- #

# (classifier, which leg decides it, positive input, transformation, verdict after)
_LEG_CASES = (
    ("_looks_like_data_key", "_SERIAL_PATTERN.fullmatch", "1", "prefix", False),
    ("_looks_like_data_key", "_SERIAL_PATTERN.fullmatch", "1", "suffix", False),
    (
        "_is_record_key_header",
        "whitespace-collapsed set membership",
        "no.",
        "prefix",
        False,
    ),
    (
        "_is_record_key_header",
        "whitespace-collapsed set membership",
        "no.",
        "suffix",
        False,
    ),
    ("_is_decision_fragment", "short-digit leg", "1", "prefix", False),
    (
        "_is_decision_fragment",
        "_DATE_CASE_PATTERN.search, UNANCHORED",
        "01/CR-2",
        "prefix",
        True,
    ),
    (
        "_is_decision_fragment",
        "_DATE_CASE_PATTERN.search, UNANCHORED",
        "01/CR-2",
        "suffix",
        True,
    ),
    (
        "_looks_like_page_furniture",
        "re.match on ^\\d+ परिच्छेद, start-anchored",
        "2 परिच्छेद",
        "prefix",
        False,
    ),
    (
        "_looks_like_page_furniture",
        "re.match ignores a trailing newline",
        "2 परिच्छेद",
        "suffix",
        True,
    ),
    (
        "_looks_like_page_furniture",
        "substring leg, newline-blind",
        "वार्षिक प्रतिवेदन",
        "prefix",
        True,
    ),
    ("_looks_like_page_furniture", "isdigit leg", "123", "prefix", False),
)


@pytest.mark.parametrize(
    ("name", "leg", "positive", "where", "after"),
    _LEG_CASES,
    ids=lambda v: str(v).replace(" ", "-"),
)
def test_each_classifier_leg_reacts_to_a_newline_as_measured(
    name, leg, positive, where, after
):
    classifier = getattr(markdown_module, name)
    joined = f"zzz\n{positive}" if where == "prefix" else f"{positive}\n(a)"

    assert classifier(positive) is True, (
        f"{name}: {leg} no longer fires on {positive!r}"
    )
    assert classifier(joined) is after, f"{name}: {leg}, {where} newline"


def test_no_shape_pattern_in_the_renderer_is_multiline_aware():
    """The cause, asserted over the SOURCE rather than the module namespace.

    ``vars(markdown_module)`` sees only patterns bound at module level -- three of them
    -- and `_looks_like_page_furniture` compiles its anchored pattern INLINE, so no
    ``re.Pattern`` object for it ever exists there. Measured: adding ``re.MULTILINE`` to
    that inline pattern in both copies of the predicate flips
    ``_looks_like_page_furniture("x\n2 परिच्छेद")`` from False to True -- any block whose
    *second* line starts a chapter heading is then discarded as page furniture -- and the
    full suite stayed at 551 passed, this branch's own baseline. Fully green.

    So the scan reads the source, which is the same reason
    `test_pymupdf_flag_words.py` scans source for its call sites: a guard is only as
    wide as the set of places it looks.

    This cannot cover `_is_record_key_header`, which has no pattern at all -- its
    newline-sensitivity comes from whitespace collapsing. `_LEG_CASES` above is what
    covers that one, and it is why both exist.
    """

    module_source = Path(markdown_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(module_source)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
        ):
            continue
        rendered = ast.unparse(node)
        if "MULTILINE" in rendered or re.search(r"\bre\.M\b", rendered):
            offenders.append(f"{node.lineno}: {rendered[:80]}")

    # And an inline (?m) in any string literal in the module, which no flag scan sees.
    inline = [
        f"{node.lineno}: {node.value[:60]}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "(?m" in node.value
    ]

    assert offenders + inline == [], (
        "a renderer pattern became MULTILINE. That makes a shape classifier match on "
        "any LINE of a cell rather than on the cell, which changes rendered output "
        f"corpus-wide -- see this file's section 3. {offenders + inline}"
    )

    # The namespace check is kept as well: it catches a pattern compiled at import time
    # with flags passed some way the source scan above does not model.
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


def test_the_page_furniture_predicate_is_now_a_single_definition():
    """``_looks_like_page_furniture`` used to be defined TWICE, byte-identical -- once
    in the converter and once in the renderer -- and both copies decided the same
    question about the same block on either side of this seam.

    The previous version of this test asserted the copies were distinct and agreed,
    and its own failure message said that merging them was the right outcome and that
    this should then become an identity assertion. This is that assertion.

    Why it matters concretely: the known pending fix to this predicate is a length
    bound, so a 216-character paragraph merely *mentioning* a running-head phrase is
    not discarded as furniture. With two copies that fix had to be landed twice, and
    landing it once would have been a silent divergence between the two paths.
    """

    renderer = markdown_module._looks_like_page_furniture
    converter = nepali_pdf_module._looks_like_page_furniture

    # The same function OBJECT, not merely equal source. That is what makes a future
    # one-sided fix impossible rather than merely detectable.
    assert renderer is converter

    converter_source = inspect.getsource(nepali_pdf_module)
    renderer_source = inspect.getsource(markdown_module)

    # Both call sites still exist. Merging a definition must not quietly remove one
    # path's USE of it -- that would change behaviour, not just shape, and an identity
    # assertion alone cannot tell the difference.
    assert "_looks_like_page_furniture(" in converter_source
    assert "_looks_like_page_furniture(" in renderer_source

    # And the converter reaches it by import rather than by a second definition.
    assert "def _looks_like_page_furniture" not in converter_source
    assert "def _looks_like_page_furniture" in renderer_source

    # The behaviour the merged definition must still have, including the 216-character
    # case the pending length bound is about -- kept from the previous version of this
    # test so the merge is not also a silent behaviour change.
    for text in ("12", "123", "1234", "0", "", "क" * 216):
        assert renderer(text) == converter(text), text


def _emptiness_skip_is_inside_the_furniture_branch(function) -> bool:
    """True when the `if not ....strip(): continue` guard sits INSIDE the `if` that
    tests `_looks_like_page_furniture`, rather than beside it."""

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))

    def is_skip(node) -> bool:
        return (
            isinstance(node, ast.If)
            and any(isinstance(child, ast.Continue) for child in node.body)
            and "strip()" in ast.unparse(node.test)
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "_looks_like_page_furniture" in ast.unparse(
            node.test
        ):
            return any(is_skip(child) for child in ast.walk(node))
    raise AssertionError("no furniture branch found -- this test is looking at nothing")


def test_both_render_paths_skip_an_emptied_block_at_the_same_point():
    """Sharing the PREDICATE is not the same as sharing the control flow around it.

    Found in review. The furniture fix added `if not text.strip(): continue` to both
    paths, but at different nesting -- inside the furniture branch in the renderer and
    outside it in the converter, where it also skipped every whitespace-only
    `ParagraphBlock`. That is a second behaviour change riding along with the first,
    and it re-opened this seam at the same join the merged predicate closed.

    🛑 Asserted on SHAPE, and that is a deliberate choice with a reason: the two
    placements produce identical output today, so a behavioural test cannot tell them
    apart. `_assemble_with_page_anchors` drops empty parts at all three of its sites,
    so the extra skip is unobservable -- which is exactly why nothing caught it, and
    why the first draft of this test passed against both arms. The divergence becomes
    observable the moment table continuation is implemented, since the skip also
    bypasses the `previous_table_key` reset; then `Table | empty paragraph | Table`
    would merge on one path and not the other. Pinning the shape now is what stops
    the two paths drifting before that day.
    """

    assert _emptiness_skip_is_inside_the_furniture_branch(
        markdown_module._render_section
    ), "renderer: the emptiness skip escaped the furniture branch"
    assert _emptiness_skip_is_inside_the_furniture_branch(
        nepali_pdf_module._render_markdown_from_blocks
    ), "converter: the emptiness skip escaped the furniture branch"


def test_assembly_drops_empty_parts_which_is_what_makes_that_seam_inert():
    """The invariant the test above leans on, pinned so its reasoning stays true.

    If assembly ever stops filtering empty parts, a whitespace-only block starts
    emitting a blank chunk and the placement above becomes output-visible.
    """

    assemble = nepali_pdf_module._assemble_with_page_anchors
    assert assemble([(1, "क"), (1, ""), (1, "ख")], [1]).count("\n\n") == 2
    assert assemble([(1, "क"), (1, ""), (1, "ख")], []) == "क\n\nख"
