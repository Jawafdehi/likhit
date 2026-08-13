"""Native PDF table extraction helpers."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import re

from likhit.extractors.base import TextFragment
from likhit.models import Table, TableCell, TableRegion

_EDGE_TOLERANCE = 1.5


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
            tables.append(table)
    return tables


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
    """Are both fragments drawn at the same spot -- overprint rather than a repeat?"""

    return _same_horizontal_position(left, right) and _same_vertical_position(
        left, right
    )


def _same_horizontal_position(left: TextFragment, right: TextFragment) -> bool:
    return (
        abs(left.x0 - right.x0) <= _EDGE_TOLERANCE
        and abs(left.x1 - right.x1) <= _EDGE_TOLERANCE
    )


def _same_vertical_position(left: TextFragment, right: TextFragment) -> bool:
    return (
        abs(left.y0 - right.y0) <= _EDGE_TOLERANCE
        and abs(left.y1 - right.y1) <= _EDGE_TOLERANCE
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

    return Table(
        row_count=current.row_count + max(next_table.row_count - drop_count, 0),
        col_count=current.col_count,
        cells=current.cells + next_cells,
        caption=current.caption or next_table.caption,
        index=current.index,
        regions=current.regions + next_table.regions,
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
