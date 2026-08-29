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


def test_render_table_does_not_join_a_figure_carrying_a_stray_pipe():
    # The OCR text of a figure sometimes carries a stray `|` -- `८५०००|` rather
    # than `८५०००`. It is still a bare figure, and the swallowed-sub-table guard
    # has to see it as one: `रकम` above it is a header, not a sentence this value
    # continues, so joining would mash a header onto its own datum.
    #
    # The renderer read the raw cell text `८५०००|` and called it prose, while every
    # consumer reads the emitted line *after* splitting on `|` and so sees a clean
    # figure -- the two sides disagreed about the same value. That agreement is the
    # whole argument and it needs no corpus case.
    #
    # ⚠️ This shape was previously credited to `local-level-report/3876__NoRKt...भानु
    # नगरपालीका, २०७८`. Re-derived: that document changes on its fiscal-year labels
    # (`२०७५|०७६-` / `०७६|७७`), NOT on `८५०००|`, whose rejoin verdict is True under
    # both classes because the sub-table row beside it carries letters. See
    # `_BARE_FIGURE`'s comment. The fixture below is the shape, stated as a shape.
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="क"),
            TableCell(row=0, col=1, text="रकम\n८५०००|"),
        ],
    )

    rendered = render_table_preformatted_markdown(table)

    assert "रकम ८५०००" not in rendered
    assert rendered == "```text\n| क | रकम |  |\n|  | ८५०००| |  |\n```"


def test_render_table_does_not_join_a_pipe_separated_date():
    # `१०|२०७६|९|१३` is a voucher date written with `|` as the field separator. It
    # is not a wrapped sentence either, so the same guard must refuse it -- the
    # conservative direction, since leaving a row transposed only preserves the
    # status quo while joining is unrecoverable.
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="क"),
            TableCell(row=0, col=1, text="गो.भौ.न,मिति\n१०|२०७६|९|१३"),
        ],
    )

    rendered = render_table_preformatted_markdown(table)

    assert "गो.भौ.न,मिति १०" not in rendered
    assert len(rendered.splitlines()) == 4  # fence, 2 rows, fence


def test_render_table_still_joins_prose_containing_a_pipe():
    # Admitting `|` to the bare-figure test must not turn every pipe-bearing line
    # into a refusal. A wrapped line with letters in it is still prose and is
    # still rejoined; only a line that is *nothing but* figures and separators is
    # protected.
    table = Table(
        row_count=1,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="लक्ष्मी श्रेष्ठ|दिपेश विष्ट\nसञ्चिता घिमिरे"),
            TableCell(row=0, col=1, text="एक"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| लक्ष्मी श्रेष्ठ|दिपेश विष्ट सञ्चिता घिमिरे | एक |  |\n```"
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


# ------------------------------------------------ a two-level header is composed, not cut
#
# The renderer emits no `|---|` separator row, so nothing downstream can tell a
# second header row from data -- `hf_export/build_oag_hf_dataset.py` reads the
# header only when `SEPARATOR_ROW` matches, which for a likhit table is never
# (439,391 of the 443,516 published tables carry `header_json == "[]"`). A
# two-level header therefore has to become ONE rendered row, and each column's
# label has to be that column's own path through the header band.


def test_a_spanning_header_keeps_every_sub_column_label():
    """The shape of OAG `oag-11130`'s expenditure table, four columns of it.

    `विनियोजन` and `राजस्व` each span an `इकाई`/`रकम` pair. The sub-labels REPEAT
    across the groups, which is why the spanning label has to be carried into every
    column it covers rather than left at its anchor: without it the row would name
    two different columns `रकम`.
    """

    table = Table(
        row_count=3,
        col_count=6,
        cells=[
            TableCell(row=0, col=0, text="क्र.सं", rowspan=2),
            TableCell(row=0, col=1, text="मन्त्रालय/ निकायको नाम", rowspan=2),
            TableCell(row=0, col=2, text="विनियोजन", colspan=2),
            TableCell(row=0, col=4, text="राजस्व", colspan=2),
            TableCell(row=1, col=2, text="इकाई"),
            TableCell(row=1, col=3, text="रकम"),
            TableCell(row=1, col=4, text="इकाई"),
            TableCell(row=1, col=5, text="रकम"),
            TableCell(row=2, col=0, text="१"),
            TableCell(row=2, col=1, text="प्रधानमन्त्री कार्यालय"),
            TableCell(row=2, col=2, text="४"),
            TableCell(row=2, col=3, text="१२३"),
            TableCell(row=2, col=4, text="२"),
            TableCell(row=2, col=5, text="४५६"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n"
        "| क्र.सं | मन्त्रालय/ निकायको नाम | विनियोजन इकाई | विनियोजन रकम"
        " | राजस्व इकाई | राजस्व रकम |\n"
        "| १ | प्रधानमन्त्री कार्यालय | ४ | १२३ | २ | ४५६ |\n"
        "```"
    )


def test_a_span_leaves_a_column_carrying_no_label_of_its_own_empty():
    """The spanning label is context for a sub-label, not content of its own.

    Filling every covered column from the span would recover nothing -- the label is
    already at its anchor -- and would manufacture text: OAG `oag-11134` has a header
    cell spanning 31 columns with a sub-label under two of them, so the eager form
    writes the same phrase 29 extra times.
    """

    table = Table(
        row_count=3,
        col_count=4,
        cells=[
            TableCell(row=0, col=0, text="सि.नं.", rowspan=2),
            TableCell(row=0, col=1, text="कुल", colspan=3),
            TableCell(row=1, col=1, text="विवरण"),
            TableCell(row=2, col=0, text="१"),
            TableCell(row=2, col=1, text="अ"),
            TableCell(row=2, col=2, text="ब"),
            TableCell(row=2, col=3, text="स"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| सि.नं. | कुल विवरण |  |  |\n| १ | अ | ब | स |\n```"
    )


def test_a_header_cell_anchored_inside_a_span_keeps_its_own_label():
    """Anchor first, span only as the fallback.

    `_expanded_grid` fills under `if not grid[row][col]`, so a wider cell to the left
    reaches this position first and the anchor's own text is ABSENT from the expanded
    grid. Composing from the expanded grid alone would therefore replace `रकम` with
    `शीर्षक` -- a silent substitution, not a loss, so no character count would show it.
    """

    table = Table(
        row_count=3,
        col_count=3,
        cells=[
            TableCell(row=0, col=0, text="सि.नं.", rowspan=2),
            TableCell(row=0, col=1, text="शीर्षक", colspan=2),
            TableCell(row=0, col=2, text="रकम"),
            TableCell(row=1, col=1, text="क"),
            TableCell(row=1, col=2, text="ख"),
            TableCell(row=2, col=0, text="१"),
            TableCell(row=2, col=1, text="अ"),
            TableCell(row=2, col=2, text="ब"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n| सि.नं. | शीर्षक क | रकम ख |\n| १ | अ | ब |\n```"
    )


def test_a_single_header_label_split_across_rows_still_reads_as_one_phrase():
    """OAG 11113's lake table, whose header wraps INSIDE its cell.

    The band is one row here, so this pins the other half of the join: a header the
    detector emitted as one multi-line cell renders as ONE row, not one row per
    visual line. Disabling the join is the mutation it catches.

    ⚠️ It does NOT discriminate the space-vs-separator choice, and no test here does:
    a single-part column joins to itself whatever the separator. See
    `_composed_header_label` for what that choice actually rests on.
    """

    table = Table(
        row_count=2,
        col_count=5,
        cells=[
            TableCell(row=0, col=0, text="सि.नं."),
            TableCell(row=0, col=1, text="तालको नाम"),
            TableCell(row=0, col=2, text="तालको जम्मा क्षेत्रफल"),
            TableCell(row=0, col=3, text="अतिक्रमण/आवादी\nगरिएको क्षेत्रफल"),
            TableCell(row=0, col=4, text="आवादी गरिएको\nप्रतिशत"),
            TableCell(row=1, col=0, text="१"),
            TableCell(row=1, col=1, text="कमलपोखरी ताल"),
            TableCell(row=1, col=2, text="186-8-2-2"),
            TableCell(row=1, col=3, text="135-7-1-0"),
            TableCell(row=1, col=4, text="७२.६२"),
        ],
    )

    assert render_table_preformatted_markdown(table) == (
        "```text\n"
        "| सि.नं. | तालको नाम | तालको जम्मा क्षेत्रफल"
        " | अतिक्रमण/आवादी गरिएको क्षेत्रफल | आवादी गरिएको प्रतिशत |\n"
        "| १ | कमलपोखरी ताल | 186-8-2-2 | 135-7-1-0 | ७२.६२ |\n"
        "```"
    )


# --------------------------------------------- a register row is not a wrapped sentence
#: What a swallowed register looks like AFTER the extractor keeps its rows together --
#: one space-joined line per printed row. Must stay in step with what
#: `extractors.tables._extract_cell_text` produces for a swallowed sub-table; the test
#: below renders it, and `tests/test_table_extraction.py` pins the extractor side.
SPACE_JOINED_REGISTER = "\n".join(
    [
        "190 SCA Dalit Seti Kamani 7980",
        "191 SCA Others Jibram Sunar 12000",
        "192 SCA Dalit Agabahadur Kami 7980",
    ]
)


def test_a_space_joined_register_is_not_rejoined_into_one_row():
    """The interaction this guard exists for, and it is invisible to a green suite.

    `test_render_table_does_not_join_a_swallowed_sub_table` above uses the register's
    OLD shape -- one value per line, so several lines are bare figures and the
    bare-figure clause refuses the rejoin. Once the extractor keeps each printed row
    together, every line carries letters, no line is a bare figure, that clause stops
    firing, and the whole register collapses into one row. Measured: 3 rows became 1
    with the full suite still green at 893 passed, because the only test covering it
    was pinned to the shape the extractor no longer produces.
    """

    table = Table(
        row_count=1,
        col_count=2,
        cells=[
            TableCell(row=0, col=0, text="1"),
            TableCell(row=0, col=1, text=SPACE_JOINED_REGISTER),
        ],
    )
    rendered = render_table_preformatted_markdown(table)

    for serial in ("190", "191", "192"):
        assert serial in rendered
    # The tell: no output line may carry two register serials.
    for line in rendered.splitlines():
        assert not ("190" in line and "191" in line), line


def test_a_wrapped_sentence_is_still_rejoined():
    """The control. If the register rule is too eager it silently disables the rejoin
    this whole change is for, and every assertion above would still pass."""

    table = Table(
        row_count=1,
        col_count=2,
        cells=[
            TableCell(row=0, col=0, text="क"),
            TableCell(row=0, col=1, text="कार्यालय\nसञ्चालन"),
        ],
    )
    assert "कार्यालय सञ्चालन" in render_table_preformatted_markdown(table)


def test_the_register_rule_needs_a_letter_as_well_as_a_trailing_figure():
    """Keeps the two guard clauses independently meaningful.

    Without the letter requirement this rule would also cover the one-value-per-line
    register and the split figure, duplicating the bare-figure clause -- and a mutation
    of either would then be masked by the other.
    """

    from likhit.renderers.markdown import _looks_like_register_rows

    assert _looks_like_register_rows(SPACE_JOINED_REGISTER.splitlines())
    # figures only: left to the bare-figure clause, not claimed by this one
    assert not _looks_like_register_rows(["185929593.", "20"])
    assert not _looks_like_register_rows(["190", "7980"])
    # one line is never a register
    assert not _looks_like_register_rows(["190 SCA Dalit 7980"])
    # a line that does not end on a figure is a sentence
    assert not _looks_like_register_rows(["190 SCA Dalit 7980", "थप विवरण"])


def test_a_blank_line_inside_a_cell_never_reaches_the_register_predicate():
    """Raised in review: "blank lines are dropped before classification".

    They are dropped, but one level UP and before this function is reached, so nothing
    is lost at the point the review is about. `_render_raw_table_lines` builds
    `cell_lines` with `if _clean_text(part)`, so the predicate cannot observe a blank
    through the render path. Instrumented rather than argued: a cell whose text contains
    a blank line between two figure-ending lines hands the predicate TWO entries.

    Pinned because a reader of `_looks_like_register_rows` alone sees a filter that
    looks like it is discarding evidence, and will keep raising it.
    """

    import likhit.renderers.markdown as markdown_module
    from likhit.models import Table, TableCell, TableRegion

    table = Table(
        row_count=1,
        col_count=2,
        cells=[
            TableCell(row=0, col=0, text="क्र.सं. १०\n\nजम्मा २०"),
            TableCell(row=0, col=1, text="x"),
        ],
        regions=[
            TableRegion(page_number=1, x0=0, y0=0, x1=100, y1=50, page_height=800)
        ],
    )

    seen: list[list[str]] = []
    original = markdown_module._looks_like_register_rows

    def spy(parts: list[str]) -> bool:
        seen.append(list(parts))
        return original(parts)

    markdown_module._looks_like_register_rows = spy
    try:
        render_table_preformatted_markdown(table)
    finally:
        markdown_module._looks_like_register_rows = original

    assert seen, "the predicate was never called, so this test proves nothing"
    assert all(all(part.strip() for part in parts) for parts in seen), seen
    assert seen[0] == ["क्र.सं. १०", "जम्मा २०"]


def test_the_register_rule_stays_conservative_on_ambiguous_input():
    """Which direction is safe, pinned so the suggested inversion is not re-applied.

    True means "separate records", which makes the caller LEAVE THE ROWS ALONE. False is
    what permits the join, and joining is the corrupting act -- it splits a figure across
    visual lines and mashes a swallowed sub-table into one string. So on input carrying a
    blank, True is the conservative answer, not the aggressive one.
    """

    from likhit.renderers.markdown import (
        _looks_like_register_rows,
        _wrapped_lines_are_one_row,
    )

    ambiguous = ["क्र.सं. १०", "", "जम्मा २०"]
    assert _looks_like_register_rows(ambiguous) is True
    # ...and True is what stops the rejoin, which is the whole point.
    assert _wrapped_lines_are_one_row([ambiguous]) is False


def test_a_pipe_only_wrapped_line_does_not_block_the_rejoin():
    """🛑 `_ANY_DIGIT` became load-bearing when `|` entered `_BARE_FIGURE`, and nothing
    pinned it -- dropping the conjunct left the full suite green at 1070 passed.

    The widening is not just "one more separator character". These three lines match the
    new class and did NOT match the parent's: `|`, `| |`, `||`. A stray pipe "where a rule
    crossed the text" is exactly what leaves a line that is nothing but pipes, so without
    the digit conjunct a content-free line would count as a bare figure and REFUSE a
    rejoin that should happen.

    Asserted as a contrasting pair through the renderer, so it covers the conjunct at its
    use site: the same shape with and without a digit must render differently.
    """

    from likhit.renderers.markdown import _ANY_DIGIT, _BARE_FIGURE

    for line in ("|", "| |", "||"):
        assert _BARE_FIGURE.match(line), f"{line!r} should match the widened class"
        assert not _ANY_DIGIT.search(line), f"{line!r} carries no digit"

    def rendered(continuation: str) -> str:
        return render_table_preformatted_markdown(
            Table(
                row_count=1,
                col_count=2,
                cells=[
                    TableCell(row=0, col=0, text="\u0915"),
                    TableCell(row=0, col=1, text=continuation),
                ],
            )
        )

    # No digit: one wrapped value, rejoined into a single row.
    assert rendered("\u092f\u094b \u0935\u093e\u0915\u094d\u092f\n|") == (
        "```text\n| \u0915 | \u092f\u094b \u0935\u093e\u0915\u094d\u092f | |\n```"
    )
    # A digit: a real bare figure, so the rejoin is refused and the row stays split.
    assert rendered("\u0930\u0915\u092e\n\u096e\u096b\u0966\u0966\u0966|") == (
        "```text\n| \u0915 | \u0930\u0915\u092e |\n|  | \u096e\u096b\u0966\u0966\u0966| |\n```"
    )
