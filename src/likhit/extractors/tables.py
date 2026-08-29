"""Native PDF table extraction helpers."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import re

from likhit.extractors.base import TextFragment
from likhit.models import Table, TableCell, TableRegion

#: Slack when clustering a table's cell-grid edges and testing bbox containment.
_EDGE_TOLERANCE = 1.5

#: Slack when deciding two glyph runs share a drawing ORIGIN, i.e. are one overprint.
#:
#: Numerically equal to :data:`_EDGE_TOLERANCE` and deliberately a separate name: the two
#: answer unrelated questions, and while they were one constant, tuning cell-grid snapping
#: would silently have moved overprint suppression. It is the smaller responsibility that
#: gets its own name, so a grid change cannot reach glyph dedupe by accident.
_OVERPRINT_TOLERANCE = 1.5


def detect_page_tables(
    page: object,
    page_fragments: list[TextFragment],
    index_offset: int = 0,
) -> list[Table]:
    """Extract accepted native PDF tables from a page."""

    if not hasattr(page, "find_tables"):
        return []

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        finder = page.find_tables()

    tables: list[Table] = []
    for offset, fitz_table in enumerate(finder.tables):
        table = _build_table(
            fitz_table,
            page_fragments,
            page_number=page.number + 1,
            page_height=float(page.rect.height),
            index=index_offset + offset,
        )
        if table is not None and _is_accepted_table(table):
            tables.append(replace(table, cells=_drop_frame_cells(table.cells)))
    return _drop_container_tables(tables)


def merge_continuation_tables(tables: list[Table]) -> list[Table]:
    """Merge obvious continuation tables across consecutive pages."""

    if not tables:
        return []

    ordered = sorted(tables, key=lambda table: (table.page_number, table.index))
    merged: list[Table] = []
    for table in ordered:
        if merged and _should_merge_tables(merged[-1], table):
            merged[-1] = _merge_table_pair(merged[-1], table)
            continue
        merged.append(table)
    return merged


def _build_table(
    fitz_table: object,
    page_fragments: list[TextFragment],
    *,
    page_number: int,
    page_height: float,
    index: int,
) -> Table | None:
    x_edges = _cluster_edges(
        [fitz_table.bbox[0], fitz_table.bbox[2]]
        + [cell[0] for row in fitz_table.rows for cell in row.cells if cell is not None]
        + [cell[2] for row in fitz_table.rows for cell in row.cells if cell is not None]
    )
    y_edges = _cluster_edges(
        [fitz_table.bbox[1], fitz_table.bbox[3]]
        + [cell[1] for row in fitz_table.rows for cell in row.cells if cell is not None]
        + [cell[3] for row in fitz_table.rows for cell in row.cells if cell is not None]
    )

    if (
        len(x_edges) != fitz_table.col_count + 1
        or len(y_edges) != fitz_table.row_count + 1
    ):
        return None

    cells: list[TableCell] = []
    for row_index, row in enumerate(fitz_table.rows):
        for col_index, bbox in enumerate(row.cells):
            if bbox is None:
                continue

            start_col = _closest_edge_index(x_edges, bbox[0])
            end_col = _closest_edge_index(x_edges, bbox[2])
            start_row = _closest_edge_index(y_edges, bbox[1])
            end_row = _closest_edge_index(y_edges, bbox[3])
            if (
                start_col is None
                or end_col is None
                or start_row is None
                or end_row is None
                or end_col <= start_col
                or end_row <= start_row
            ):
                return None

            text = _extract_cell_text(page_fragments, bbox)
            cells.append(
                TableCell(
                    row=start_row,
                    col=start_col,
                    text=text,
                    rowspan=end_row - start_row,
                    colspan=end_col - start_col,
                )
            )

    return Table(
        row_count=fitz_table.row_count,
        col_count=fitz_table.col_count,
        cells=cells,
        caption=_extract_caption(fitz_table, page_fragments),
        index=index,
        regions=[
            TableRegion(
                page_number=page_number,
                x0=float(fitz_table.bbox[0]),
                y0=float(fitz_table.bbox[1]),
                x1=float(fitz_table.bbox[2]),
                y1=float(fitz_table.bbox[3]),
                page_height=page_height,
            )
        ],
    )


#: Slack when testing whether one table's REGION contains another's, in points.
#: Larger than :data:`_EDGE_TOLERANCE` on purpose: this compares two independently
#: detected grids, whose ruled borders sit a stroke width apart, not two cells of one
#: grid.
_REGION_TOLERANCE = 3.0

#: A word of the corpus: a Devanagari run, or a run of digits in EITHER script. Used
#: only to ask whether one table's content is already covered by another's.
#:
#: ASCII digits are inside it deliberately, and the cost is known and accepted. A page
#: footer's counter (`11 of 13`, `Page 70 of 76`) therefore counts as content and can
#: stop a coarse table being stripped -- measured on 200 residual pages, that is the
#: SOLE blocker on 2 of them. Excluding ASCII digits to recover those 2 would put the
#: corpus's ~5.7 million genuine ASCII digits (VOL-323) outside the safety test, so a
#: container holding the only copy of an ASCII figure would be stripped and the figure
#: lost. Two doubled pages is the cheaper error.
_CONTENT_WORD = re.compile(r"[ऀ-ॿ‌‍]+|[0-9]+|[०-९]+")


def _drop_container_tables(tables: list[Table]) -> list[Table]:
    """Strip duplicated lines from a COARSE table that contains a finer one.

    `find_tables()` can return two grids for one printed table: a coarse one whose few
    cells swallow the whole page, and the real one. Both are accepted, both are
    rendered, and the page then says everything twice -- the same duplication
    :func:`_drop_frame_cells` fixes one level down, where the container is a cell of a
    single grid rather than a grid of its own.

    Witness (VOL-744): `12788__मालिकार्जुन गाउँपालिका, दार्चुला.pdf` p61 returns a 2x3
    grid of **5** cells over the whole page (0,0,792,612) and a 27x24 grid of **629**
    cells at (51.5,30.3,741.1,588.0). Both carry the same 674 tokens over the same 105
    distinct words, and the coarse one renders them as a single 2,009-character line.

    🛑 **The coarse table is stripped LINE BY LINE rather than dropped whole, and that is
    not fastidiousness -- dropping it whole loses real content.** On p61 the container
    also holds the page-furniture row (`8 of 10`, the NAMS URL, `Page 58 of 64`), and the
    fine grid does NOT: its last row is the 24th school. That footer is the only record
    of which printed page a transcript page came from, so a rule that deletes the
    container deletes it. Measured, on the first page this was tried against.

    The existing whole-cell rule is preserved. Within a cell it has to retain, a line
    goes only if its token sequence is an ordered subsequence of ONE contained table.
    This is stricter than set membership on purpose: the rejected set-union version
    moved 463 of 1,400 controls, including a line with two `१` tokens when the finer
    table held one, and `जम्मा ७१८६४` when those two tokens lived in different tables.
    Requiring one ordered sequence preserves multiplicity and cannot stitch coverage
    across tables. A one-token sequence has no order evidence, so it additionally has
    to be a complete line in the finer table rather than merely occur somewhere inside
    a longer line. Duplicate body lines still go while a header or footer the finer
    grid missed stays.

    ⚠️ **KNOWN LIMITATION, stated rather than fixed: order is bounded, distance is not.**
    A multi-token line needs only to occur in order *somewhere* in one contained table, at
    any separation -- so `जम्मा ७१८६४` is deleted even if those two tokens sit 40 tokens
    apart in the finer grid. A total row whose label is a column header and whose value is
    30 rows below it would go. Review raised this; it is unbounded by construction and it is
    **unobserved**: over the 13 documents this rule moves most, it removes 20,863 duplicated
    token occurrences and loses **0 distinct words**, measured by diffing the content-word
    multiset of every transcript against the same document without the rule. A distance
    bound would be a behaviour change needing its own corpus measurement, so it is not
    smuggled in here. `test_multi_token_coverage_is_not_distance_bounded` characterises the
    current behaviour so that adding one is a deliberate change with a failing test.

    Two conditions gate the table before any cell is examined:

    * **containment** -- the coarse region must enclose the fine one, within
      :data:`_REGION_TOLERANCE`;
    * **coarser** -- strictly fewer cells than the finest table it contains, so a rich
      table that merely encloses a small nested one is left alone, and two grids with
      equal cell counts leave each other alone rather than stripping both.

    A table stripped to nothing is dropped. `_is_accepted_table` is deliberately NOT
    re-run on a stripped table: it was accepted on the grid the page actually has, and
    re-testing it would delete the footer row for failing a two-populated-rows rule that
    the duplicate had been satisfying.
    """
    if len(tables) < 2:
        return tables

    words = [_table_content_words(table) for table in tables]
    token_streams = [_table_content_tokens(table) for table in tables]
    tokenized_lines = [_table_tokenized_lines(table) for table in tables]
    cell_counts = [len(table.cells) for table in tables]
    stripped: list[Table] = []
    for outer, table in enumerate(tables):
        contained = [
            inner
            for inner in range(len(tables))
            if inner != outer and _region_contains(table, tables[inner])
        ]
        if not contained or cell_counts[outer] >= max(
            cell_counts[inner] for inner in contained
        ):
            stripped.append(table)
            continue

        covered: set[str] = set()
        for inner in contained:
            covered |= words[inner]
        # Paired, not two parallel collections: the single-token rule needs both halves of
        # one table's evidence, and a union on either side lets a deletion be justified by
        # two tables jointly. See `_strip_covered_lines`.
        covered_tables = [
            (token_streams[inner], tokenized_lines[inner]) for inner in contained
        ]
        keep = []
        changed = False
        for cell in table.cells:
            # Preserve B3's established whole-cell behavior exactly. The ordered
            # predicate below governs only the new mixed-cell case.
            if _cell_is_covered_by(cell, covered):
                changed = True
                continue

            cell_or_none = _strip_covered_lines(cell, covered_tables)
            if cell_or_none is cell:
                keep.append(cell)
                continue
            changed = True
            if cell_or_none is not None:
                keep.append(cell_or_none)

        if not changed:
            stripped.append(table)
        elif any(cell.text.strip() for cell in keep):
            stripped.append(replace(table, cells=keep))
    return stripped


def _strip_covered_lines(
    cell: TableCell,
    covered_tables: list[tuple[list[str], set[tuple[str, ...]]]],
) -> TableCell | None:
    """Remove lines reproduced in order by ONE finer table.

    "One table" is load-bearing, so ``covered_tables`` pairs each contained table's token
    stream with its OWN line set: both pieces of evidence -- the ordered occurrence and, for
    a lone token, the proof that a table emitted it as a whole line -- come from one table.

    ⚠️ This shape replaces a per-table stream list beside a line set unioned across every
    contained table, which review flagged as letting a one-token line take its whole-line
    evidence from table A and its occurrence from table B. **That was unreachable, and the
    reason is worth keeping**: ``_table_tokenized_lines`` and ``_table_content_tokens`` walk
    the same cells, so a line ``("जम्मा",)`` in the union implies ``जम्मा`` in that same
    table's stream -- which then satisfies both halves by itself. The two forms agree on
    every input the caller can build; asserted in
    ``test_a_single_token_line_implies_its_token_in_the_same_table_stream``.

    It is fixed anyway because the union said something the rule does not mean, and the
    unreachability rests on a coupling between two helpers that nothing enforced. Deriving
    lines differently -- from rendered rows, say -- would have made a latent inconsistency
    into a live one silently.
    """

    kept = []
    changed = False
    for line in cell.text.splitlines():
        own = _CONTENT_WORD.findall(line)
        # A one-token subsequence proves only occurrence. Require the same finer table
        # to have emitted that token as a whole line before deleting it.
        if own and any(
            _is_ordered_subsequence(own, table_tokens)
            and (len(own) > 1 or tuple(own) in table_lines)
            for table_tokens, table_lines in covered_tables
        ):
            changed = True
            continue
        kept.append(line)

    if not changed:
        return cell
    text = "\n".join(kept)
    if not text.strip():
        return None
    return replace(cell, text=text)


def _is_ordered_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """Does ``needle`` occur in order and with multiplicity in ``haystack``?"""

    if not needle:
        return True
    position = 0
    for token in haystack:
        if token != needle[position]:
            continue
        position += 1
        if position == len(needle):
            return True
    return False


def _cell_is_covered_by(cell: TableCell, covered: set[str]) -> bool:
    """Is every content word of ``cell`` already held by the finer tables?

    An empty cell is never "covered": it carries nothing to duplicate, and treating the
    empty set as a subset would strip the grid's blank cells and shift every column.
    """
    own = set(_CONTENT_WORD.findall(cell.text))
    return bool(own) and own <= covered


def _table_content_words(table: Table) -> set[str]:
    return set(_CONTENT_WORD.findall(" ".join(cell.text for cell in table.cells)))


def _table_content_tokens(table: Table) -> list[str]:
    return [token for cell in table.cells for token in _CONTENT_WORD.findall(cell.text)]


def _table_tokenized_lines(table: Table) -> set[tuple[str, ...]]:
    lines = set()
    for cell in table.cells:
        for line in cell.text.splitlines():
            tokens = tuple(_CONTENT_WORD.findall(line))
            if tokens:
                lines.add(tokens)
    return lines


def _region_contains(outer: Table, inner: Table) -> bool:
    """Does ``outer``'s region enclose ``inner``'s, within the region tolerance?

    Compared on the tables' own regions rather than on their cells: a table detected
    over a whole page has a region that says so even when only five of its cells carry
    anything.
    """
    if not outer.regions or not inner.regions:
        return False
    left, right = outer.regions[0], inner.regions[0]
    if left.page_number != right.page_number:
        return False
    if (left.x0, left.y0, left.x1, left.y1) == (right.x0, right.y0, right.x1, right.y1):
        return False
    return (
        left.x0 <= right.x0 + _REGION_TOLERANCE
        and left.y0 <= right.y0 + _REGION_TOLERANCE
        and left.x1 >= right.x1 - _REGION_TOLERANCE
        and left.y1 >= right.y1 - _REGION_TOLERANCE
    )


def _drop_frame_cells(cells: list[TableCell]) -> list[TableCell]:
    """Drop cells that are the table's ruled FRAME rather than one of its cells.

    A frame cell's grid rectangle strictly contains another cell's, so
    :func:`_extract_cell_text` reads every fragment inside it a SECOND time -- once
    into the frame and once into the cell that actually owns it. The page then says
    everything twice.

    🛑 This is not a rounding error and it is not rare. Measured on the OAG corpus
    (VOL-744): **2,423 pages of 309,231 emitted their own content twice**, 634,084
    duplicated word tokens across 981 of 6,235 documents, identically in three
    successive generations. The witness is
    `11781__ललितपुर महानगरपालिका.pdf` p105, where `find_tables()` returns ONE 17x9 grid
    whose `r0 c1` spans 16x8 -- the whole grid -- and carries all 1,958 characters,
    while the 90 cells inside it carry the same 1,875. An independent vision read of
    that page returns the content ONCE over the same distinct vocabulary.

    🛑 **No character-level audit axis can see this**, which is why it survived so
    long: duplicated Devanagari is well-formed Devanagari, with no U+FFFD, no PUA,
    correct repha and correct matras. A line-grain repetition test misses it too --
    the two copies are laid out with different pipe padding, so on p105 only 4 of 83
    lines repeat while the token stream is 100% doubled.

    Containment, not span size, is the discriminator, and the distinction is
    load-bearing: a LEGITIMATE merged cell -- a header spanning three columns -- holds
    the only claim on its own rectangle, because `find_tables()` gives each grid
    region to exactly one cell. So a wide or tall cell is kept; only one that another
    cell sits *inside* is dropped. Sizing the rule on `rowspan`/`colspan` instead
    would delete real spanned headers.

    🛑 **Containment alone is NOT sufficient, and assuming it was deleted real content.**
    A spanning cell can enclose cells that are EMPTY, in which case it holds the only
    copy of what it read and dropping it destroys the page. Measured on the paired
    control sweep that exists to catch exactly this:
    `11727__भरतपुर महानगरपालिका.pdf` p101 has one 6x11 grid of 34 cells whose spanning
    cell carries 143 of the page's 169 tokens and 80 of its 101 distinct words, while
    the 33 cells inside it carry 26 tokens between them. Containment-only took that page
    from **101 distinct words to 21**, on a page that was never doubled -- 27 of 1,400
    control pages lost content the same way, which is a worse defect than the one being
    fixed. So a frame goes only when its content words are already held by the cells
    inside it, the same guard :func:`_drop_container_tables` uses one level up.

    Equal rectangles are deliberately NOT treated as containment. Two cells sharing a
    rectangle would also duplicate, but that is a different defect with a different
    fix (neither is the frame of the other, so dropping "the container" would pick one
    arbitrarily); it does not occur in this corpus and is left to argue with its own
    evidence.
    """

    # Only a multi-span cell can strictly contain another, so the scan is over those
    # rather than every pair -- a 1x1 cell can only contain a cell with its own exact
    # rectangle, which is excluded above.
    def rect(cell: TableCell) -> tuple[int, int, int, int]:
        return (cell.row, cell.col, cell.row + cell.rowspan, cell.col + cell.colspan)

    rects = [rect(cell) for cell in cells]
    spanning = [
        position
        for position, cell in enumerate(cells)
        if cell.rowspan > 1 or cell.colspan > 1
    ]
    if not spanning:
        return cells

    frames: set[int] = set()
    for outer in spanning:
        o_row0, o_col0, o_row1, o_col1 = rects[outer]
        covered: set[str] = set()
        contains_any = False
        for inner, (i_row0, i_col0, i_row1, i_col1) in enumerate(rects):
            if inner == outer:
                continue
            if (
                o_row0 <= i_row0
                and o_col0 <= i_col0
                and o_row1 >= i_row1
                and o_col1 >= i_col1
                and rects[outer] != rects[inner]
            ):
                contains_any = True
                covered |= set(_CONTENT_WORD.findall(cells[inner].text))
        if contains_any and _cell_is_covered_by(cells[outer], covered):
            frames.add(outer)

    if not frames:
        return cells
    return [cell for position, cell in enumerate(cells) if position not in frames]


def _extract_caption(
    fitz_table: object,
    page_fragments: list[TextFragment],
) -> str | None:
    header = getattr(fitz_table, "header", None)
    if header is None or not getattr(header, "external", False):
        return None

    header_bbox = getattr(header, "bbox", None)
    if header_bbox is not None:
        caption = _extract_cell_text(page_fragments, header_bbox)
        if caption:
            return caption

    caption_parts = [
        part.strip() for part in getattr(header, "names", []) if part and part.strip()
    ]
    if not caption_parts:
        return None
    return " ".join(dict.fromkeys(caption_parts))


def _extract_cell_text(
    page_fragments: list[TextFragment],
    bbox: tuple[float, float, float, float],
) -> str:
    matching = [
        fragment
        for fragment in page_fragments
        if _fragment_center_in_bbox(fragment, bbox)
    ]
    matching.sort(key=lambda fragment: (fragment.y0, fragment.x0))

    lines: list[str] = []
    single: list[bool] = []
    for visual_line in _group_into_visual_lines(matching):
        text = _join_visual_line(visual_line)
        if not text:
            continue
        # Suppress a repeated line, but only between lines that each came from one
        # fragment. That is the overprint this dedupe was written for, and keeping
        # it fragment-scoped is what stops it widening: a register legitimately
        # prints the same row more than once, and once a whole row is one joined
        # line, collapsing the repeat deletes it. Measured on
        # `5852__pLpP31685271785मिर्चैया नगरपालिका, २०७८।७९` -- three identical
        # `१ Bhulli Devi Mahara ४२८५६ १५३०९४५२३ ७९८० ७९८०` rows became one.
        # Count only the fragments that CONTRIBUTED to `text`. `len(visual_line)`
        # counts blank ones too, so a single whitespace-only fragment sharing the line
        # -- which adds nothing to the joined text -- made the line look
        # multi-fragment and switched the suppression off. Measured: two identical
        # overprinted lines collapse to one, and adding one blank fragment to each
        # leaves both.
        one_fragment = sum(1 for part in visual_line if part.text.strip()) == 1
        if lines and lines[-1] == text and single[-1] and one_fragment:
            continue
        lines.append(text)
        single.append(one_fragment)
    return "\n".join(lines)


def _group_into_visual_lines(
    fragments: list[TextFragment],
) -> list[list[TextFragment]]:
    """Split y-sorted fragments into the visual lines they were printed on.

    A fragment is one *line* of one text block, so a cell whose grid the detector
    resolved holds one fragment per visual line and every group below has exactly
    one member -- the output is then identical to joining the fragments directly.

    A cell whose bbox swallowed a nested sub-table holds one fragment per inner
    *cell*, several to a line. Emitting one output line each would put every value
    on its own row, destroying the inner row that says what the value belongs to,
    and dropping its x position destroys the column too. The fragments still carry
    that geometry here, so group by it instead.
    """

    groups: list[list[TextFragment]] = []
    for fragment in fragments:
        if groups and _shares_visual_line(groups[-1][0], fragment):
            groups[-1].append(fragment)
            continue
        groups.append([fragment])
    for group in groups:
        group.sort(key=lambda fragment: fragment.x0)
    return groups


def _shares_visual_line(anchor: TextFragment, fragment: TextFragment) -> bool:
    """Do two fragments sit on the same printed line?

    Decided by vertical overlap against the line's *first* fragment rather than
    its running extent, so a column of tightly-spaced lines cannot chain into one
    band through a series of small overlaps.
    """

    overlap = min(anchor.y1, fragment.y1) - max(anchor.y0, fragment.y0)
    shortest = min(anchor.y1 - anchor.y0, fragment.y1 - fragment.y0)
    if shortest <= 0:
        return abs(anchor.y0 - fragment.y0) <= _EDGE_TOLERANCE
    return overlap > shortest / 2


def _join_visual_line(fragments: list[TextFragment]) -> str:
    """Join one visual line's fragments, left to right.

    The separator is a single space, and deliberately not the alternatives:

    * `|` would terminate the cell in the enclosing Markdown table.
    * an empty separator would splice two adjacent figures into one number and
      manufacture the >=15-digit runs `verify_numeric_boundaries.py` gates.

    Overprinted text -- the same string drawn twice at the same place -- was
    suppressed by the caller's line-level dedupe while one fragment meant one
    line. It has to be suppressed here too, but only where the two fragments
    occupy the *same* place, which is what overprint means. Merely overlapping is
    not enough: adjacent columns of a register overlap by a point or two, and on
    `3172__1613896170विराटनगर महानगरपालिका` an overlap test deleted a genuine
    repeat of `- डिल्लि धिमाल`.

    Same place means same x *and* same y. Testing x alone was sufficient while a
    group held one printed line, because sharing a horizontal position then
    implied sharing a position outright. `_group_into_visual_lines` broke that
    implication: a tall fragment can overlap several shorter ones, so a group can
    span *stacked* register rows, and one column of three consecutive rows shares
    x while differing in y. Suppressing on x alone therefore deleted a genuine
    repeated figure -- 15 tokens across 5 documents, worst
    `2446__16126986953_Ghyanglekh RM_Sindhuli`, where three rows carrying `10000`
    each emitted it once and the document's count fell 8 -> 6.

    🛑 "Same place" is the drawing ORIGIN -- `x0` and `y0` -- and not the extent.
    An earlier form of this compared all four edges, and the two extent clauses
    carried 83% of its corpus effect while nothing tested them: measured over all
    6,236 OAG documents plus all 35 CIAA reports, 1,573 pairs changed verdict and
    **1,308 of them were kept by the `x1` clause alone**. Those are double-strike
    bold -- the same string at the same origin on the same baseline, ~5 pt narrower
    -- i.e. precisely the overprint this dedupe exists for. On
    `11102__m6t-Annual Report 2067` that duplicated 185 lines of table headers and
    totals (`| जम्मा जम्मा | ... | ४,४५७ ४,४५७ |`); comparing origins only takes it
    to 7 lines. A run's extent is a consequence of its font, so two equal strings
    at one origin with different widths are one drawing at two weights.
    """

    parts: list[str] = []
    kept: list[TextFragment] = []
    for fragment in fragments:
        text = _normalize_cell_text(fragment.text)
        if not text:
            continue
        if kept and parts[-1] == text and _same_printed_position(kept[-1], fragment):
            continue
        parts.append(text)
        kept.append(fragment)
    return " ".join(parts)


def _same_printed_position(left: TextFragment, right: TextFragment) -> bool:
    """Are both fragments drawn at the same spot -- overprint rather than a repeat?

    Both origin coordinates, and only those. See :func:`_join_visual_line` for why the
    extent is deliberately not compared, and `test_table_extraction.py`'s per-edge block
    for the fixtures that pin each of these two clauses on its own.
    """

    return (
        abs(left.x0 - right.x0) <= _OVERPRINT_TOLERANCE
        and abs(left.y0 - right.y0) <= _OVERPRINT_TOLERANCE
    )


def _normalize_cell_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _fragment_center_in_bbox(
    fragment: TextFragment,
    bbox: tuple[float, float, float, float],
) -> bool:
    center_x = (fragment.x0 + fragment.x1) / 2
    center_y = (fragment.y0 + fragment.y1) / 2
    return (
        bbox[0] - _EDGE_TOLERANCE <= center_x <= bbox[2] + _EDGE_TOLERANCE
        and bbox[1] - _EDGE_TOLERANCE <= center_y <= bbox[3] + _EDGE_TOLERANCE
    )


def _cluster_edges(values: list[float]) -> list[float]:
    if not values:
        return []

    ordered = sorted(float(value) for value in values)
    clusters: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if abs(value - clusters[-1][-1]) <= _EDGE_TOLERANCE:
            clusters[-1].append(value)
            continue
        clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _closest_edge_index(edges: list[float], target: float) -> int | None:
    for index, edge in enumerate(edges):
        if abs(edge - target) <= _EDGE_TOLERANCE:
            return index
    return None


def _is_accepted_table(table: Table) -> bool:
    if table.row_count < 2 or table.col_count < 2:
        return False

    nonempty_cells = [cell for cell in table.cells if cell.text.strip()]
    if len(nonempty_cells) < 2:
        return False

    populated_rows = {cell.row for cell in nonempty_cells}
    populated_cols = {cell.col for cell in nonempty_cells}
    return len(populated_rows) >= 2 and len(populated_cols) >= 2


def _should_merge_tables(current: Table, next_table: Table) -> bool:
    current_region = current.regions[-1]
    next_region = next_table.regions[0]

    if next_region.page_number != current_region.page_number + 1:
        return False
    if current.col_count != next_table.col_count:
        return False

    if current.caption and next_table.caption and current.caption != next_table.caption:
        return False

    if abs(current_region.x0 - next_region.x0) > 6.0:
        return False
    if abs(current_region.x1 - next_region.x1) > 6.0:
        return False

    current_bottom_cutoff = current_region.page_height * 0.7
    next_top_cutoff = next_region.page_height * 0.3
    if current_region.page_height and current_region.y1 < current_bottom_cutoff:
        return False
    if next_region.page_height and next_region.y0 > next_top_cutoff:
        return False

    return True


def _merge_table_pair(current: Table, next_table: Table) -> Table:
    drop_count = _shared_header_prefix(current, next_table)
    next_cells = []
    row_offset = current.row_count

    for cell in next_table.cells:
        if cell.row < drop_count:
            continue
        next_cells.append(
            TableCell(
                row=cell.row - drop_count + row_offset,
                col=cell.col,
                text=cell.text,
                rowspan=cell.rowspan,
                colspan=cell.colspan,
            )
        )

    next_regions = [
        replace(
            region,
            start_row=max(region.start_row - drop_count, 0) + row_offset,
        )
        for region in next_table.regions
    ]

    return Table(
        row_count=current.row_count + max(next_table.row_count - drop_count, 0),
        col_count=current.col_count,
        cells=current.cells + next_cells,
        caption=current.caption or next_table.caption,
        index=current.index,
        regions=current.regions + next_regions,
    )


def _shared_header_prefix(current: Table, next_table: Table) -> int:
    current_rows = _table_row_signatures(current)
    next_rows = _table_row_signatures(next_table)
    max_prefix = min(5, len(current_rows), len(next_rows))

    for size in range(max_prefix, 0, -1):
        if current_rows[:size] == next_rows[:size] and size < next_table.row_count:
            return size
    return 0


def _table_row_signatures(table: Table) -> list[tuple[str, ...]]:
    rows = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        rows[cell.row][cell.col] = _normalize_signature_text(cell.text)
    return [tuple(row) for row in rows]


def _normalize_signature_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
