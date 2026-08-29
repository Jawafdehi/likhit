"""Recover numeric cell boundaries that PDF text extraction can erase."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
import logging
from pathlib import Path
import re
from statistics import median
from typing import Iterable

import fitz

logger = logging.getLogger(__name__)


_DIGITS = frozenset("0123456789०१२३४५६७८९")
_NUMERIC_PUNCTUATION = frozenset(",.")
_NUMERIC_CHARS = _DIGITS | _NUMERIC_PUNCTUATION
_DECIMAL_AMOUNT_PATTERN = re.compile(r"^[0-9][0-9,]*\.[0-9]{1,2}$")
_PLAIN_NUMBER_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]{1,2})?$")
_INDIAN_GROUPED_NUMBER_PATTERN = re.compile(
    r"^[0-9]{1,3}(?:,[0-9]{2})*,[0-9]{3}(?:\.[0-9]{1,2})?$"
)
_WESTERN_GROUPED_NUMBER_PATTERN = re.compile(
    r"^[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?$"
)
_SERIAL_NUMBER_PATTERN = re.compile(r"^[0-9]{1,4}\.$")
_DOTTED_REFERENCE_PATTERN = re.compile(r"^[0-9]{1,4}(?:\.[0-9]{1,4}){2,}$")
_NUMERAL_TRANSLATION = str.maketrans("०१२३४५६७८९।", "0123456789.")
_EQUIVALENT_CHARACTER_PATTERNS = {
    "0": "[0०]",
    "1": "[1१]",
    "2": "[2२]",
    "3": "[3३]",
    "4": "[4४]",
    "5": "[5५]",
    "6": "[6६]",
    "7": "[7७]",
    "8": "[8८]",
    "9": "[9९]",
    ".": r"[.।]",
}
#: What a recovered boundary is written as when the text it sits in cannot become a
#: Markdown table cell: a pipe, which says "these were separate cells".
CELL_BOUNDARY_SEPARATOR = " | "
#: ...and what it is written as when the text CAN become one. A bare `|` inside a cell
#: is a cell delimiter, so writing one there states a column the table does not have:
#: `renderers/markdown.py::_raw_table_row_lines` renders every row of a table from the
#: same `col_count` columns and does NOT escape a pipe already in the cell text, so the
#: row comes out one cell wider than its own table and every later cell in that row
#: shifts. `tables._join_visual_line` already refuses `|` for exactly this reason.
#:
#: Measured on 17 documents / 20,721 rendered table rows, comparing each arm against
#: the modal cell count of its own table block: the line-level repair moved **173 rows
#: from aligned to misaligned** and 21 the other way, and the Markdown-level repair
#: moved **28** rows to misaligned and 0 the other way. A space keeps the grid and
#: still un-glues the digits, which is what the numeric axis measures.
INLINE_BOUNDARY_SEPARATOR = " "
_ADVANCE_OUTLIER_EM = 0.10
_BBOX_GAP_OUTLIER_EM = 0.20
_MIN_RULE_HEIGHT = 4.0
# Ceiling on the segment count `_plausible_span_partition_cuts` will partition,
# which it explores exponentially. Matches the spirit of the `> 12` guard in
# `_select_minimal_rule_cuts`; 12 segments is at most 2048 groupings.
_MAX_PARTITION_SEGMENTS = 12


@dataclass(frozen=True)
class NumericBoundaryRepair:
    """One geometry-proven numeric run and its original cell values.

    `block_number` and `line_number` locate the line in the extraction this repair was
    measured on and are diagnostic only. They are NOT how a caller finds the line again
    -- see :func:`group_repairs_by_line` and :data:`line_origin`.
    """

    page_number: int
    block_number: int
    line_number: int
    start_index: int
    merged_text: str
    parts: tuple[str, ...]
    line_text: str
    occurrence_index: int = 0
    #: Top-left corner of the line's glyph boxes, `(min y0, min x0)` rounded to 0.1pt.
    #: This is the key a caller looks the repair up by, because it survives a second
    #: extraction of the same page and an enumeration index does not.
    line_origin: tuple[float, float] = (0.0, 0.0)

    @property
    def repaired_text(self) -> str:
        return CELL_BOUNDARY_SEPARATOR.join(self.parts)


@dataclass(frozen=True)
class NumericBoundaryEvidence:
    """What geometry proved, and which runs it examined and left whole.

    `unsplit_runs` holds the canonical text of every maximal numeric run no
    repair covers. A merged value absent from it never appeared in the source
    as one cell, so every occurrence of it in the rendered Markdown is the
    proven merge -- however many times the renderer emitted the line.
    """

    repairs: tuple[NumericBoundaryRepair, ...]
    unsplit_runs: frozenset[str]


@dataclass(frozen=True)
class _Character:
    text: str
    origin_x: float
    bbox: tuple[float, float, float, float]
    font: str
    size: float
    span_number: int


@dataclass(frozen=True)
class _VerticalEdge:
    x: float
    y0: float
    y1: float


def collect_document_numeric_boundary_evidence(
    source: bytes | str | Path,
) -> NumericBoundaryEvidence:
    """Collect repairs and unsplit runs from every page in a PDF."""

    if isinstance(source, bytes):
        doc = fitz.open(stream=source, filetype="pdf")
    else:
        doc = fitz.open(Path(source))

    try:
        repairs: list[NumericBoundaryRepair] = []
        unsplit_runs: set[str] = set()
        for page_index in range(doc.page_count):
            evidence = collect_page_numeric_boundary_evidence(
                doc[page_index],
                page_number=page_index + 1,
            )
            repairs.extend(evidence.repairs)
            unsplit_runs |= evidence.unsplit_runs
        return NumericBoundaryEvidence(tuple(repairs), frozenset(unsplit_runs))
    finally:
        doc.close()


def collect_document_numeric_boundary_repairs(
    source: bytes | str | Path,
) -> list[NumericBoundaryRepair]:
    """Collect numeric boundary repairs from every page in a PDF."""

    return list(collect_document_numeric_boundary_evidence(source).repairs)


def collect_page_numeric_boundary_repairs(
    page: object,
    *,
    page_number: int | None = None,
) -> list[NumericBoundaryRepair]:
    """Find erased numeric separators using character origins and PDF rulings."""

    return list(
        collect_page_numeric_boundary_evidence(page, page_number=page_number).repairs
    )


def collect_page_numeric_boundary_evidence(
    page: object,
    *,
    page_number: int | None = None,
) -> NumericBoundaryEvidence:
    """Find erased numeric separators using character origins and PDF rulings."""

    empty = NumericBoundaryEvidence((), frozenset())
    if not hasattr(page, "get_text") or not hasattr(page, "get_cdrawings"):
        return empty

    try:
        # Non-additive on purpose, same as `font_based.py`'s two passes: OR-ing
        # `TEXTFLAGS_RAWDICT` in would set `TEXT_MEDIABOX_CLIP`, which deletes
        # 1,250,148 glyphs across 4,022 of 6,236 corpus documents -- and this
        # repair reads character origins, so a clipped glyph is a lost boundary.
        raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return empty

    lines = _extract_lines(raw)
    if not lines:
        return empty

    edges = _extract_vertical_edges(page)
    expected_advances = _expected_digit_advances(lines)
    resolved_page_number = page_number
    if resolved_page_number is None:
        resolved_page_number = int(getattr(page, "number", 0)) + 1

    repairs: list[NumericBoundaryRepair] = []
    run_positions: dict[str, set[tuple[int, int, int]]] = defaultdict(set)
    for block_number, line_number, characters in lines:
        line_text = "".join(character.text for character in characters)
        origin = line_origin_key(character.bbox for character in characters)
        for run_start, run_end in _maximal_numeric_runs(characters):
            run_text = "".join(
                character.text for character in characters[run_start:run_end]
            )
            run_positions[_canonical_numeric_text(run_text)].add(
                (block_number, line_number, run_start)
            )
        rule_cuts = _rule_boundary_cuts(characters, edges)
        rule_cuts, preferred_runs = _prefer_numeric_span_cuts(
            characters,
            rule_cuts,
            edges,
        )
        rule_cuts = _filter_plausible_rule_cuts(
            characters,
            rule_cuts,
            expected_advances,
            preferred_runs,
        )
        rule_cuts = _select_minimal_rule_cuts(
            characters,
            rule_cuts,
            preferred_runs,
        )
        advance_cuts = _decimal_advance_boundary_cuts(
            characters,
            expected_advances,
            excluded=rule_cuts,
        )
        repairs.extend(
            _repairs_for_contiguous_runs(
                characters,
                rule_cuts | advance_cuts,
                page_number=resolved_page_number,
                block_number=block_number,
                line_number=line_number,
                line_text=line_text,
                line_origin=origin,
            )
        )
        repairs.extend(
            _repairs_for_decimal_whitespace(
                characters,
                page_number=resolved_page_number,
                block_number=block_number,
                line_number=line_number,
                line_text=line_text,
                line_origin=origin,
            )
        )

    deduplicated = _deduplicate_repairs(repairs)
    repaired_positions: dict[str, set[tuple[int, int, int]]] = defaultdict(set)
    for repair in deduplicated:
        repaired_positions[_canonical_numeric_text(repair.merged_text)].add(
            (repair.block_number, repair.line_number, repair.start_index)
        )
    unsplit_runs = frozenset(
        text
        for text, positions in run_positions.items()
        if positions - repaired_positions[text]
    )
    return NumericBoundaryEvidence(tuple(deduplicated), unsplit_runs)


def _maximal_numeric_runs(
    characters: list[_Character],
) -> list[tuple[int, int]]:
    """Bound every maximal run of digits and numeric punctuation in a line."""

    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(characters):
        if characters[index].text not in _NUMERIC_CHARS:
            index += 1
            continue
        start = index
        while index < len(characters) and characters[index].text in _NUMERIC_CHARS:
            index += 1
        runs.append((start, index))
    return runs


def apply_line_numeric_boundary_repairs(
    text: str,
    repairs: Iterable[NumericBoundaryRepair],
) -> str:
    """Apply occurrence-scoped repairs to one extracted PDF line.

    The boundary is written as a space, not as a pipe. This text is a PDF line, and a
    PDF line becomes a Markdown table CELL through the fragment the caller builds from
    it (`tables.detect_page_tables` is passed those fragments), so a pipe written here
    ends up inside a cell -- where it is a cell delimiter that the renderer does not
    escape. See :data:`INLINE_BOUNDARY_SEPARATOR` for the measurement.
    """

    repaired = text
    ordered = sorted(
        repairs,
        key=lambda repair: (repair.start_index, repair.occurrence_index),
        reverse=True,
    )
    for repair in ordered:
        matches = list(
            _script_equivalent_pattern(repair.merged_text).finditer(repaired)
        )
        if repair.occurrence_index >= len(matches):
            # Geometry proved a specific occurrence. If the line no longer holds
            # that many, clamping to the last one would split a value geometry
            # never examined, so decline -- an unrepaired figure beats a
            # confidently wrong one.
            continue
        match = matches[repair.occurrence_index]
        replacement = _split_matched_text(
            match.group(),
            repair.parts,
            separator=INLINE_BOUNDARY_SEPARATOR,
        )
        repaired = repaired[: match.start()] + replacement + repaired[match.end() :]
    return repaired


#: Precision the line origin is rounded to before it is used as a key. 0.1pt is finer
#: than any inter-line or inter-column gap in this corpus and coarser than the float
#: noise between two extractions of the same page.
_LINE_ORIGIN_PRECISION = 1


def line_origin_key(
    boxes: Iterable[tuple[float, float, float, float]],
) -> tuple[float, float]:
    """The key a line is indexed by: `(min y0, min x0)` over ALL its glyph boxes.

    Callers must pass every box on the line, including ones whose text they are about to
    discard -- dropping the empty ones first moves the minimum and the key stops
    matching. Measured over the 1,843 repairs the 102 `numeric_damage` documents produce:
    resolved 1,843/1,843 including all boxes, 1,379/1,843 excluding the empty ones.
    """

    ys: list[float] = []
    xs: list[float] = []
    for box in boxes:
        xs.append(float(box[0]))
        ys.append(float(box[1]))
    if not ys:
        return (0.0, 0.0)
    return (
        round(min(ys), _LINE_ORIGIN_PRECISION),
        round(min(xs), _LINE_ORIGIN_PRECISION),
    )


def group_repairs_by_line(
    repairs: Iterable[NumericBoundaryRepair],
) -> dict[tuple[int, float, float], list[NumericBoundaryRepair]]:
    """Index repairs by 1-based page and the line's glyph-box origin.

    NOT by `(page, block, line)`, which is what this did and is the reason the repairs
    were computed and then thrown away. The block and line numbers are `enumerate()`
    indices over `page.get_text("rawdict", flags=TEXT_PRESERVE_WHITESPACE)`, while both
    callers enumerate `font_based.get_cid_marked_page_dict(page)` -- and that function
    re-extracts the page with `TEXT_USE_CID_FOR_UNKNOWN_UNICODE` whenever any glyph
    decodes to U+FFFD. The second extraction re-blocks the page, so every index after the
    first regrouped block is shifted and the lookup lands on a different line or on none.
    Measured on document 11724 page 7: 38 blocks plain, 39 with the CID flag, diverging
    at block 4.

    The geometry does not move -- `get_cid_marked_page_dict`'s own docstring records that
    glyph boxes survive the regrouping, which is why it pairs the two extractions by box.
    Measured over the 1,843 line-applicable repairs the 102 published `numeric_damage`
    documents produce, counting a key as resolved only when the line it names actually
    contains the merged text:

        (block, line) enumeration index   1,669 / 1,843   90.6%
        (min y0,)                         1,829 / 1,843   99.2%, 14 ambiguous
        (min y0, min x0)                  1,843 / 1,843  100.0%, 0 ambiguous

    A rounded float pair is only safe as a key because both sides round the same MuPDF
    box; the 100% above is the evidence, not the reasoning.
    """

    grouped: dict[tuple[int, float, float], list[NumericBoundaryRepair]] = defaultdict(
        list
    )
    for repair in repairs:
        grouped[repair.page_number, *repair.line_origin].append(repair)
    return dict(grouped)


def collect_page_repairs_by_line(
    page: object,
    *,
    page_number: int,
) -> dict[tuple[int, int, int], list[NumericBoundaryRepair]]:
    """Collect and index one page's repairs, degrading to none on failure.

    Both extraction entry points call this rather than pairing the collector
    with `group_repairs_by_line` themselves: a numeric-geometry failure must
    cost the page its repairs, not fail the extraction, following the
    `kalimati.py` precedent.
    """

    try:
        return group_repairs_by_line(
            collect_page_numeric_boundary_repairs(page, page_number=page_number)
        )
    except Exception as exc:  # noqa: BLE001 - degrade to no repair, never fail
        logger.warning(
            "Numeric boundary analysis failed for page=%s: %s",
            page_number,
            exc,
        )
        return {}


def repair_markdown_numeric_boundaries(
    markdown: str,
    repairs: Iterable[NumericBoundaryRepair],
    *,
    unsplit_runs: frozenset[str] | None = None,
) -> str:
    """Repair unambiguous merged values in a converter's Markdown output.

    Pass `unsplit_runs` from `NumericBoundaryEvidence` to decide safety from
    the source geometry rather than from how many times the merged value
    appears in the Markdown. Occurrence counting cannot tell a renderer that
    emitted one table twice from a second, legitimately unmerged value that
    happens to carry the same digits, and declines both.
    """

    repairs = list(repairs)
    unique = _unique_text_repairs(repairs)
    evidence_counts: dict[tuple[str, tuple[str, ...]], int] = defaultdict(int)
    for repair in repairs:
        key = (
            _canonical_numeric_text(repair.merged_text),
            tuple(_canonical_numeric_text(part) for part in repair.parts),
        )
        evidence_counts[key] += 1

    signature_partitions: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for merged_text, parts in unique:
        signature_partitions[_digit_signature(merged_text)].add(
            tuple(_canonical_numeric_text(part) for part in parts)
        )

    repaired = markdown
    for merged_text, parts in unique:
        if _looks_like_plausible_single_number(merged_text):
            if not _safe_plausible_global_repair(parts):
                continue
            pattern = _complete_numeric_run_pattern(merged_text)
            matches = pattern.findall(repaired)
            if not matches:
                continue
            if unsplit_runs is None:
                key = (
                    _canonical_numeric_text(merged_text),
                    tuple(_canonical_numeric_text(part) for part in parts),
                )
                if len(matches) > evidence_counts[key]:
                    continue
            elif _canonical_numeric_text(merged_text) in unsplit_runs:
                continue
        else:
            pattern = _complete_numeric_run_pattern(merged_text)
        repaired = _substitute_per_line(pattern, repaired, parts)
        signature = _digit_signature(merged_text)
        if len(signature) >= 8 and len(signature_partitions[signature]) == 1:
            repaired = _repair_pipe_lines_by_digit_signature(
                repaired,
                merged_text,
                parts,
            )
    return repaired


def _substitute_per_line(
    pattern: re.Pattern[str],
    markdown: str,
    parts: tuple[str, ...],
) -> str:
    """Substitute line by line so the separator can depend on the line.

    Equivalent to one whole-string `pattern.sub` outside table rows: every pattern
    :func:`_complete_numeric_run_pattern` builds is made of digit and numeric-punctuation
    classes and per-character escapes, so no match can span a newline.
    """

    lines = markdown.splitlines(keepends=True)
    for index, line in enumerate(lines):
        separator = (
            INLINE_BOUNDARY_SEPARATOR
            if _is_markdown_table_row(line)
            else CELL_BOUNDARY_SEPARATOR
        )
        lines[index] = pattern.sub(
            lambda match, separator=separator: _split_matched_text(
                match.group(),
                parts,
                separator=separator,
            ),
            line,
        )
    return "".join(lines)


def _safe_plausible_global_repair(parts: tuple[str, ...]) -> bool:
    """Allow global repair only when geometry includes a substantial value."""

    canonical_parts = tuple(_canonical_numeric_text(part) for part in parts)
    leading_digits = sum(character in _DIGITS for character in canonical_parts[0])
    if leading_digits <= 4:
        return False
    if any(re.fullmatch(r"20[6-9][0-9]?", part) for part in canonical_parts[1:]):
        return False
    return (
        max(
            (
                sum(character in _DIGITS for character in part)
                for part in canonical_parts
            ),
            default=0,
        )
        >= 5
    )


def requires_geometry_aware_candidate(
    repairs: Iterable[NumericBoundaryRepair],
    *,
    markdown: str | None = None,
) -> bool:
    """Return whether global Markdown replacement could alter valid numbers."""

    return any(
        _looks_like_plausible_single_number(repair.merged_text)
        and (
            markdown is None
            or _script_equivalent_pattern(repair.merged_text).search(markdown)
        )
        for repair in repairs
    )


def _extract_lines(
    raw: object,
) -> list[tuple[int, int, list[_Character]]]:
    if not isinstance(raw, dict):
        return []

    lines: list[tuple[int, int, list[_Character]]] = []
    for block_number, block in enumerate(raw.get("blocks", [])):
        if not isinstance(block, dict):
            continue
        for line_number, line in enumerate(block.get("lines", [])):
            if not isinstance(line, dict):
                continue
            characters: list[_Character] = []
            for span_number, span in enumerate(line.get("spans", [])):
                if not isinstance(span, dict):
                    continue
                font = str(span.get("font", ""))
                size = float(span.get("size", 1.0) or 1.0)
                for char in span.get("chars", []):
                    if not isinstance(char, dict):
                        continue
                    text = str(char.get("c", ""))
                    origin = char.get("origin")
                    bbox = char.get("bbox")
                    if (
                        not text
                        or not isinstance(origin, (list, tuple))
                        or len(origin) < 2
                        or not isinstance(bbox, (list, tuple))
                        or len(bbox) < 4
                    ):
                        continue
                    characters.append(
                        _Character(
                            text=text,
                            origin_x=float(origin[0]),
                            bbox=tuple(float(value) for value in bbox[:4]),
                            font=font,
                            size=size,
                            span_number=span_number,
                        )
                    )
            if characters:
                lines.append((block_number, line_number, characters))
    return lines


def _extract_vertical_edges(page: object) -> list[_VerticalEdge]:
    if not hasattr(page, "get_cdrawings"):
        return []

    try:
        drawings = page.get_cdrawings()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []

    edges: list[_VerticalEdge] = []
    for drawing in drawings:
        if not isinstance(drawing, dict):
            continue
        for item in drawing.get("items", []):
            if not isinstance(item, (list, tuple)) or not item:
                continue
            if item[0] == "re" and len(item) >= 2:
                rect = item[1]
                if not isinstance(rect, (list, tuple)) or len(rect) < 4:
                    continue
                x0, y0, x1, y1 = (float(value) for value in rect[:4])
                # Normalize as the "l" branch below does: an unnormalized rect
                # would give a negative height, silently yielding no edges for
                # the whole page and disabling ruled-line detection there.
                top, bottom = min(y0, y1), max(y0, y1)
                if bottom - top >= _MIN_RULE_HEIGHT:
                    edges.extend(
                        (
                            _VerticalEdge(x0, top, bottom),
                            _VerticalEdge(x1, top, bottom),
                        )
                    )
            elif item[0] == "l" and len(item) >= 3:
                start, end = item[1], item[2]
                if (
                    not isinstance(start, (list, tuple))
                    or len(start) < 2
                    or not isinstance(end, (list, tuple))
                    or len(end) < 2
                ):
                    continue
                x0, y0 = float(start[0]), float(start[1])
                x1, y1 = float(end[0]), float(end[1])
                if abs(x1 - x0) <= 0.35 and abs(y1 - y0) >= _MIN_RULE_HEIGHT:
                    edges.append(
                        _VerticalEdge(
                            (x0 + x1) / 2,
                            min(y0, y1),
                            max(y0, y1),
                        )
                    )
    return edges


def _expected_digit_advances(
    lines: list[tuple[int, int, list[_Character]]],
) -> dict[tuple[str, float, str] | tuple[str, float], float]:
    samples: dict[tuple[str, float, str] | tuple[str, float], list[float]] = (
        defaultdict(list)
    )
    for _block_number, _line_number, characters in lines:
        for current, following in zip(characters, characters[1:]):
            if current.text not in _DIGITS or following.text not in _DIGITS:
                continue
            if current.size <= 0:
                continue
            advance = (following.origin_x - current.origin_x) / current.size
            if advance <= 0 or advance > 2.0:
                continue
            size_key = round(current.size, 2)
            samples[(current.font, size_key, current.text)].append(advance)
            samples[(current.font, size_key)].append(advance)

    return {key: median(values) for key, values in samples.items() if len(values) >= 3}


def _rule_boundary_cuts(
    characters: list[_Character],
    edges: list[_VerticalEdge],
) -> set[int]:
    cuts: set[int] = set()
    if not edges:
        return cuts
    ordered_edges = sorted(edges, key=lambda edge: edge.x)
    edge_positions = [edge.x for edge in ordered_edges]

    for index, (current, following) in enumerate(
        zip(characters, characters[1:]),
        start=1,
    ):
        if current.text not in _DIGITS or following.text not in _DIGITS:
            continue

        overlap_top = max(current.bbox[1], following.bbox[1])
        overlap_bottom = min(current.bbox[3], following.bbox[3])
        center_y = (overlap_top + overlap_bottom) / 2
        left = current.bbox[2] - max(0.75, current.size * 0.08)
        right = following.origin_x
        if _has_edge_between(
            ordered_edges,
            edge_positions,
            left,
            right,
            center_y,
        ):
            cuts.add(index)
    return cuts


def _prefer_numeric_span_cuts(
    characters: list[_Character],
    rule_cuts: set[int],
    edges: list[_VerticalEdge],
) -> tuple[set[int], set[tuple[int, int]]]:
    adjusted = set(rule_cuts)
    preferred_runs: set[tuple[int, int]] = set()
    if not characters:
        return adjusted, preferred_runs

    center_y = median(
        (character.bbox[1] + character.bbox[3]) / 2 for character in characters
    )
    crossing_edge_positions = {
        round(edge.x) for edge in edges if edge.y0 - 1.0 <= center_y <= edge.y1 + 1.0
    }
    if len(crossing_edge_positions) < 2:
        return adjusted, preferred_runs

    for start, end in _maximal_numeric_runs(characters):
        segments: list[tuple[int, int]] = []
        segment_start = start
        for index in range(start + 1, end):
            if characters[index].span_number != characters[index - 1].span_number:
                segments.append((segment_start, index))
                segment_start = index
        segments.append((segment_start, end))
        if len(segments) < 2:
            continue

        parts = [
            "".join(character.text for character in characters[part_start:part_end])
            for part_start, part_end in segments
        ]
        if any(not any(character in _DIGITS for character in part) for part in parts):
            continue

        merged_text = "".join(parts)
        plausible = _looks_like_plausible_single_number(merged_text)
        segment_cuts = {part_start for part_start, _part_end in segments[1:]}
        if plausible:
            if (
                len(parts) == 2
                or not all(_looks_like_plausible_single_number(part) for part in parts)
                or not segment_cuts.issubset(rule_cuts)
            ):
                continue
            selected_cuts = segment_cuts
        else:
            selected_cuts = _plausible_span_partition_cuts(
                characters,
                segments,
                rule_cuts,
            )
            if not selected_cuts:
                continue

        adjusted.difference_update(cut for cut in rule_cuts if start < cut < end)
        adjusted.update(selected_cuts)
        preferred_runs.add((start, end))

    return adjusted, preferred_runs


def _plausible_span_partition_cuts(
    characters: list[_Character],
    segments: list[tuple[int, int]],
    rule_cuts: set[int],
) -> set[int]:
    """Group fragmented spans into the least ambiguous valid numeric cells."""

    # `visit` explores every way to group consecutive segments, so its cost is
    # exponential in the segment count. Nothing prunes a run of bare digits,
    # because every grouping of it is a plain number: measured on a run split
    # one span per glyph, 21 segments cost 2.7s, 23 cost 10.8s and 25 cost
    # 42.6s, all returning no repair. A PDF that positions each glyph
    # separately would hang the conversion. Past the bound, decline instead --
    # a fragmented run this ambiguous is not one geometry can resolve.
    if len(segments) > _MAX_PARTITION_SEGMENTS:
        return set()

    solutions: list[tuple[int, ...]] = []

    def visit(segment_index: int, cuts: tuple[int, ...]) -> None:
        if segment_index == len(segments):
            if cuts:
                solutions.append(cuts)
            return

        for next_index in range(segment_index + 1, len(segments) + 1):
            start = segments[segment_index][0]
            end = segments[next_index - 1][1]
            text = "".join(character.text for character in characters[start:end])
            if not _looks_like_plausible_single_number(text):
                continue
            next_cuts = cuts
            if next_index < len(segments):
                next_cuts += (segments[next_index][0],)
            visit(next_index, next_cuts)

    visit(0, ())
    if not solutions:
        return set()

    fewest_cells = min(len(cuts) for cuts in solutions)
    finalists = [cuts for cuts in solutions if len(cuts) == fewest_cells]
    most_rule_support = max(sum(cut in rule_cuts for cut in cuts) for cuts in finalists)
    finalists = [
        cuts
        for cuts in finalists
        if sum(cut in rule_cuts for cut in cuts) == most_rule_support
    ]
    if len(set(finalists)) != 1:
        return set()
    return set(finalists[0])


def _filter_plausible_rule_cuts(
    characters: list[_Character],
    rule_cuts: set[int],
    expected_advances: dict[
        tuple[str, float, str] | tuple[str, float],
        float,
    ],
    preferred_runs: set[tuple[int, int]],
) -> set[int]:
    filtered: set[int] = set()
    for cut in rule_cuts:
        start, end = _numeric_run_bounds(characters, cut)
        text = "".join(character.text for character in characters[start:end])
        if (start, end) in preferred_runs or not _looks_like_plausible_single_number(
            text
        ):
            filtered.add(cut)
            continue

        current, following = characters[cut - 1], characters[cut]
        if current.size <= 0:
            continue
        size_key = round(current.size, 2)
        expected = expected_advances.get(
            (current.font, size_key, current.text)
        ) or expected_advances.get((current.font, size_key))
        observed = (following.origin_x - current.origin_x) / current.size
        bbox_gap = (following.bbox[0] - current.bbox[2]) / current.size
        if (
            expected is not None and observed - expected >= _ADVANCE_OUTLIER_EM
        ) or bbox_gap >= _BBOX_GAP_OUTLIER_EM:
            filtered.add(cut)
    return filtered


def _select_minimal_rule_cuts(
    characters: list[_Character],
    rule_cuts: set[int],
    preferred_runs: set[tuple[int, int]],
) -> set[int]:
    """Discard neighboring false rules when one valid partition is unique.

    A candidate partition is valid when every part is a plausible single value -- the
    same predicate :func:`_repairs_for_contiguous_runs` applies when it decides whether
    to emit, so the two functions that must agree, do.

    This used to require every part to match `_DECIMAL_AMOUNT_PATTERN`,
    `^[0-9][0-9,]*\\.[0-9]{1,2}$`, which insists on a decimal point. An OAG beruju column
    is Indian-grouped integers, so no partition validated, no run was ever narrowed, and
    the spurious cut then took the correct one down with it. Traced on document 11754
    page 8: the run `३२,१०,६५,४९४७,३९,५२,३८८` has rule cuts at 12 and 21, under the
    decimal-only predicate 0 minimal partitions exist and the emitter rejects
    `['३२,१०,६५,४९४', '७,३९,५२,३', '८८']` whole; under this predicate there is exactly
    one, `(12,)` -> `['३२,१०,६५,४९४', '७,३९,५२,३८८']`.

    The uniqueness requirement below is what keeps the wider predicate safe: a run that
    can be partitioned two ways is still left alone.
    """

    cuts_by_run: dict[tuple[int, int], set[int]] = defaultdict(set)
    for cut in rule_cuts:
        start, end = _numeric_run_bounds(characters, cut)
        cuts_by_run[(start, end)].add(cut)

    selected = set(rule_cuts)
    for (start, end), run_cuts in cuts_by_run.items():
        if (start, end) in preferred_runs or len(run_cuts) < 2:
            continue

        ordered_cuts = sorted(run_cuts)
        if len(ordered_cuts) > 12:
            continue

        minimal_partitions: list[tuple[int, ...]] = []
        for cut_count in range(1, len(ordered_cuts) + 1):
            for candidate in combinations(ordered_cuts, cut_count):
                boundaries = (start, *candidate, end)
                parts = [
                    "".join(
                        character.text for character in characters[part_start:part_end]
                    )
                    for part_start, part_end in zip(boundaries, boundaries[1:])
                ]
                if all(
                    _looks_like_complete_amount(part)
                    and _looks_like_plausible_single_number(part)
                    for part in parts
                ):
                    minimal_partitions.append(candidate)
            if minimal_partitions:
                break

        if len(minimal_partitions) == 1:
            selected.difference_update(run_cuts)
            selected.update(minimal_partitions[0])

    return selected


def _has_edge_between(
    edges: list[_VerticalEdge],
    edge_positions: list[float],
    left: float,
    right: float,
    center_y: float,
) -> bool:
    index = bisect_left(edge_positions, left)
    while index < len(edges) and edges[index].x <= right:
        edge = edges[index]
        if edge.y0 - 1.0 <= center_y <= edge.y1 + 1.0:
            return True
        index += 1
    return False


def _decimal_advance_boundary_cuts(
    characters: list[_Character],
    expected_advances: dict[
        tuple[str, float, str] | tuple[str, float],
        float,
    ],
    *,
    excluded: set[int],
) -> set[int]:
    cuts: set[int] = set()
    for index, (current, following) in enumerate(
        zip(characters, characters[1:]),
        start=1,
    ):
        if (
            index in excluded
            or current.text not in _DIGITS
            or following.text not in _DIGITS
            or current.size <= 0
        ):
            continue

        start, end = _numeric_run_bounds(characters, index)
        text = "".join(character.text for character in characters[start:end])
        local_index = index - start
        if not _is_two_decimal_amount_join(text, local_index):
            continue

        size_key = round(current.size, 2)
        expected = expected_advances.get(
            (current.font, size_key, current.text)
        ) or expected_advances.get((current.font, size_key))
        if expected is None:
            continue
        observed = (following.origin_x - current.origin_x) / current.size
        if observed - expected >= _ADVANCE_OUTLIER_EM:
            cuts.add(index)
    return cuts


def _is_two_decimal_amount_join(text: str, cut: int) -> bool:
    return bool(
        _DECIMAL_AMOUNT_PATTERN.fullmatch(_canonical_numeric_text(text[:cut]))
        and _DECIMAL_AMOUNT_PATTERN.fullmatch(_canonical_numeric_text(text[cut:]))
    )


def _repairs_for_contiguous_runs(
    characters: list[_Character],
    cuts: set[int],
    *,
    page_number: int,
    block_number: int,
    line_number: int,
    line_text: str,
    line_origin: tuple[float, float],
) -> list[NumericBoundaryRepair]:
    if not cuts:
        return []

    cuts_by_run: dict[tuple[int, int], set[int]] = defaultdict(set)
    for cut in cuts:
        start, end = _numeric_run_bounds(characters, cut)
        if start < cut < end:
            cuts_by_run[(start, end)].add(cut)

    repairs: list[NumericBoundaryRepair] = []
    for (start, end), run_cuts in cuts_by_run.items():
        merged_text = "".join(character.text for character in characters[start:end])
        local_cuts = sorted(cut - start for cut in run_cuts)
        parts: list[str] = []
        previous = 0
        for cut in local_cuts:
            parts.append(merged_text[previous:cut])
            previous = cut
        parts.append(merged_text[previous:])
        if any(
            not part
            or not any(char in _DIGITS for char in part)
            or not _looks_like_plausible_single_number(part)
            for part in parts
        ):
            continue
        repairs.append(
            NumericBoundaryRepair(
                page_number=page_number,
                block_number=block_number,
                line_number=line_number,
                start_index=start,
                merged_text=merged_text,
                parts=tuple(parts),
                line_text=line_text,
                occurrence_index=_occurrence_index(line_text, merged_text, start),
                line_origin=line_origin,
            )
        )
    return repairs


def _repairs_for_decimal_whitespace(
    characters: list[_Character],
    *,
    page_number: int,
    block_number: int,
    line_number: int,
    line_text: str,
    line_origin: tuple[float, float],
) -> list[NumericBoundaryRepair]:
    repairs: list[NumericBoundaryRepair] = []
    index = 0
    while index < len(characters):
        if not characters[index].text.isspace():
            index += 1
            continue

        whitespace_start = index
        while index < len(characters) and characters[index].text.isspace():
            index += 1
        if (
            whitespace_start == 0
            or index >= len(characters)
            or characters[whitespace_start - 1].text not in _DIGITS
            or characters[index].text not in _DIGITS
        ):
            continue

        left_start = whitespace_start - 1
        while left_start > 0 and characters[left_start - 1].text in _NUMERIC_CHARS:
            left_start -= 1
        right_end = index + 1
        while (
            right_end < len(characters) and characters[right_end].text in _NUMERIC_CHARS
        ):
            right_end += 1

        left = "".join(
            character.text for character in characters[left_start:whitespace_start]
        )
        right = "".join(character.text for character in characters[index:right_end])
        if not (
            _DECIMAL_AMOUNT_PATTERN.fullmatch(_canonical_numeric_text(left))
            and _DECIMAL_AMOUNT_PATTERN.fullmatch(_canonical_numeric_text(right))
        ):
            continue
        repairs.append(
            NumericBoundaryRepair(
                page_number=page_number,
                block_number=block_number,
                line_number=line_number,
                start_index=left_start,
                merged_text=left + right,
                parts=(left, right),
                line_text=line_text,
                occurrence_index=0,
                line_origin=line_origin,
            )
        )
    return repairs


def _numeric_run_bounds(
    characters: list[_Character],
    cut: int,
) -> tuple[int, int]:
    start = cut - 1
    while start > 0 and characters[start - 1].text in _NUMERIC_CHARS:
        start -= 1
    end = cut
    while end < len(characters) and characters[end].text in _NUMERIC_CHARS:
        end += 1
    return start, end


def _occurrence_index(line_text: str, text: str, start_index: int) -> int:
    return sum(
        1
        for match in re.finditer(re.escape(text), line_text)
        if match.start() < start_index
    )


def _looks_like_complete_amount(text: str) -> bool:
    """A value a money column can hold as ONE cell: decimal or integer, grouped or not.

    Narrower than :func:`_looks_like_plausible_single_number` on purpose. That predicate
    also accepts a serial (`12.`) and a dotted reference (`4.1.4`), and a dotted reference
    swallows a genuine merge: on `123.45678.9099.10` with rule cuts at 6 and 12 it accepts
    `678.9099.10`, so the one-cut partition validates first and the correct cut at 12 is
    discarded. Measured -- it took
    `test_collects_multiple_boundaries_inside_one_pdf_span` from three parts to two.

    Wider than `_DECIMAL_AMOUNT_PATTERN`, which is what this replaced: that insists on a
    decimal point, and an OAG beruju column is Indian-grouped integers.

    The caller keeps asking BOTH this and `_looks_like_plausible_single_number`, and the
    conjunction is not redundant -- it is why the old predicate worked at all. `0358,500.00`
    matches `_DECIMAL_AMOUNT_PATTERN` (its `[0-9,]*` does not care that `0358` is a
    four-digit group) and fails every grouping rule, so only the intersection rejects it.
    Measured: dropping the second half re-partitions `358,500.00358,500.00` at 9 instead of
    at 10.
    """

    canonical = _canonical_numeric_text(text)
    return bool(
        _DECIMAL_AMOUNT_PATTERN.fullmatch(canonical)
        or _PLAIN_NUMBER_PATTERN.fullmatch(canonical)
        or _INDIAN_GROUPED_NUMBER_PATTERN.fullmatch(canonical)
        or _WESTERN_GROUPED_NUMBER_PATTERN.fullmatch(canonical)
    )


def _looks_like_plausible_single_number(text: str) -> bool:
    text = _canonical_numeric_text(text)
    return bool(
        _PLAIN_NUMBER_PATTERN.fullmatch(text)
        or _INDIAN_GROUPED_NUMBER_PATTERN.fullmatch(text)
        or _WESTERN_GROUPED_NUMBER_PATTERN.fullmatch(text)
        or _SERIAL_NUMBER_PATTERN.fullmatch(text)
        or _DOTTED_REFERENCE_PATTERN.fullmatch(text)
    )


def _repair_pipe_lines_by_digit_signature(
    markdown: str,
    merged_text: str,
    parts: tuple[str, ...],
) -> str:
    digits = [
        _canonical_numeric_text(character)
        for character in merged_text
        if character in _DIGITS
    ]
    if len(digits) < 4:
        return markdown

    digit_class = "0-9०-९"
    separator = r"[ \t|\x00]*"
    last_digit_index = max(
        index for index, character in enumerate(merged_text) if character in _DIGITS
    )
    trailing_exact = "".join(
        _EQUIVALENT_CHARACTER_PATTERNS.get(
            _canonical_numeric_text(character),
            re.escape(character),
        )
        for character in merged_text[last_digit_index + 1 :]
    )
    trailing_pattern = f"(?:{trailing_exact})?" if trailing_exact else ""
    pattern = re.compile(
        rf"(?<![{digit_class}])"
        + separator.join(_EQUIVALENT_CHARACTER_PATTERNS[digit] for digit in digits)
        + trailing_pattern
        + rf"(?![{digit_class}])"
    )
    lines = markdown.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "|" not in line:
            continue
        use_danda = "।" in line
        lines[index] = pattern.sub(
            lambda match: _render_parts_with_matched_digits(
                match.group(),
                parts,
                use_danda=use_danda,
            ),
            line,
        )
    return "".join(lines)


def _canonical_numeric_text(text: str) -> str:
    return text.translate(_NUMERAL_TRANSLATION)


@lru_cache(maxsize=4096)
def _script_equivalent_pattern(text: str) -> re.Pattern[str]:
    return re.compile(
        "".join(
            _EQUIVALENT_CHARACTER_PATTERNS.get(
                _canonical_numeric_text(character),
                re.escape(character),
            )
            for character in text
        )
    )


@lru_cache(maxsize=4096)
def _complete_numeric_run_pattern(text: str) -> re.Pattern[str]:
    """Match a script-equivalent numeric value without accepting a substring."""

    return re.compile(
        rf"(?<![0-9०-९,.।]){_script_equivalent_pattern(text).pattern}(?![0-9०-९,.।])"
    )


def _is_markdown_table_row(line: str) -> bool:
    """Is this line a rendered Markdown table row, where a `|` is a cell delimiter?"""

    return line.lstrip().startswith("|")


def _split_matched_text(
    matched_text: str,
    parts: tuple[str, ...],
    *,
    separator: str = CELL_BOUNDARY_SEPARATOR,
) -> str:
    split_parts: list[str] = []
    offset = 0
    for part in parts:
        end = offset + len(part)
        split_parts.append(matched_text[offset:end])
        offset = end
    return separator.join(split_parts)


def _render_parts_with_matched_digits(
    matched_text: str,
    parts: tuple[str, ...],
    *,
    use_danda: bool,
) -> str:
    """Re-render a run that already spans cell delimiters, keeping them.

    Deliberately joins with a pipe even inside a table row, unlike
    :func:`_split_matched_text`: this is only reached from
    :func:`_repair_pipe_lines_by_digit_signature`, whose pattern matches ACROSS existing
    `|` and whitespace, so the run it replaces already occupied several cells. Writing a
    space here would collapse them into one and destroy the column structure -- measured
    by `test_markdown_repair_recovers_values_from_misaligned_table_columns`, where the
    repair takes a 6-cell row to the correct 5.
    """

    matched_digits = iter(
        character for character in matched_text if character in _DIGITS
    )
    rendered_parts: list[str] = []
    for part in parts:
        rendered: list[str] = []
        for character in part:
            if character in _DIGITS:
                rendered.append(next(matched_digits))
            elif _canonical_numeric_text(character) == ".":
                rendered.append("।" if use_danda else ".")
            else:
                rendered.append(character)
        rendered_parts.append("".join(rendered))
    return CELL_BOUNDARY_SEPARATOR.join(rendered_parts)


def _unique_text_repairs(
    repairs: Iterable[NumericBoundaryRepair],
) -> list[tuple[str, tuple[str, ...]]]:
    grouped: dict[
        str,
        dict[tuple[str, ...], tuple[str, tuple[str, ...]]],
    ] = defaultdict(dict)
    for repair in repairs:
        if not all(_looks_like_plausible_single_number(part) for part in repair.parts):
            continue
        canonical_merged = _canonical_numeric_text(repair.merged_text)
        canonical_parts = tuple(_canonical_numeric_text(part) for part in repair.parts)
        grouped[canonical_merged].setdefault(
            canonical_parts,
            (repair.merged_text, repair.parts),
        )
    return [
        next(iter(partitions.values()))
        for partitions in grouped.values()
        if len(partitions) == 1
    ]


def _digit_signature(text: str) -> str:
    return "".join(
        _canonical_numeric_text(character) for character in text if character in _DIGITS
    )


def _deduplicate_repairs(
    repairs: Iterable[NumericBoundaryRepair],
) -> list[NumericBoundaryRepair]:
    unique: dict[
        tuple[int, int, int, int, str, tuple[str, ...]],
        NumericBoundaryRepair,
    ] = {}
    for repair in repairs:
        key = (
            repair.page_number,
            repair.block_number,
            repair.line_number,
            repair.start_index,
            repair.merged_text,
            repair.parts,
        )
        unique[key] = repair
    return sorted(
        unique.values(),
        key=lambda repair: (
            repair.page_number,
            repair.block_number,
            repair.line_number,
            repair.start_index,
        ),
    )
