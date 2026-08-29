"""Redact personal values stored separately from their labels in pipe tables.

This is intentionally a second pass after ``redact_personal.py``. The first pass
handles a label and value in one text span. This pass handles table layouts where
the label is a header or another cell and the value cell contains only digits and
date/identifier separators.

The precision guards are load-bearing:

* labels must be strict citizenship-certificate or date-of-birth forms;
* ``नागरिकता सिफारिस`` (citizenship recommendation) is excluded;
* a value must occupy the whole cell and meet the measured digit-length bounds;
* a body-row label chooses one adjacent candidate, or one unique candidate;
* rows with multiple non-adjacent candidates are refused.

Untouched files are copied byte-for-byte. Changed files retain every byte except
the complete numeric contents of journalled cells. Matched digits are never
written to the journal.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from ..devanagari import ANY_DIGITS as AD
from .placeholders import (
    TABLE_CITIZENSHIP as CITIZENSHIP_PLACEHOLDER,
)
from .placeholders import (
    TABLE_DATE_OF_BIRTH as DOB_PLACEHOLDER,
)
from .placeholders import (
    TABLE_PERSONAL_VALUE as AMBIGUOUS_PLACEHOLDER,
)

# These are narrower than the inline pass on purpose. A table header can govern
# thousands of cells, so a loose label has a much larger over-redaction radius.
CITIZENSHIP_LABEL = re.compile(
    r"(?:ना\.?\s*प्र\.?\s*नं\.?"
    r"|नागरिकता(?P<mid>[^|\n]{0,30})(?:प्रमाणपत्र|नम्बर|नं\.?))"
)
DOB_LABEL = re.compile(r"(?:जन्म\s*(?:मिति|दर्ता\s*मिति|दिन)|जन्ममिति)")
VALUE_CELL = re.compile(rf"[{AD}][{AD}\-/\.\s]{{3,24}}[{AD}]")

LIMITS = {
    "citizenship": (7, 16),
    "date_of_birth": (5, 10),
}
PLACEHOLDERS = {
    "citizenship": CITIZENSHIP_PLACEHOLDER,
    "date_of_birth": DOB_PLACEHOLDER,
    "ambiguous_personal_value": AMBIGUOUS_PLACEHOLDER,
}
PLACEHOLDER_KINDS = {
    CITIZENSHIP_PLACEHOLDER: {"citizenship"},
    DOB_PLACEHOLDER: {"date_of_birth"},
    AMBIGUOUS_PLACEHOLDER: {"citizenship", "date_of_birth"},
}
MECHANISM_ORDER = {
    "header_column": 0,
    "same_row_adjacent": 1,
    "same_row_unique": 2,
}


@dataclass(frozen=True, order=True)
class CellRef:
    line_index: int
    column_index: int


@dataclass(frozen=True)
class CandidateShape:
    digit_count: int
    had_separators: bool


@dataclass(frozen=True)
class TypedEvidence:
    kind: str
    mechanism: str
    shape: CandidateShape


@dataclass(frozen=True)
class Target:
    ref: CellRef
    classification: str
    evidence: tuple[TypedEvidence, ...]
    protected_kinds: tuple[str, ...]

    @property
    def placeholder(self) -> str:
        return PLACEHOLDERS[self.classification]

    @property
    def shape(self) -> CandidateShape:
        return self.evidence[0].shape


@dataclass(frozen=True)
class ParsedRow:
    line_index: int
    cells: tuple[str, ...]


def _logical_cells(line: str) -> tuple[str, ...]:
    """Split one pipe row exactly as the dataset frame builder does."""
    parts = line.split("|")
    start = 1 if line.lstrip().startswith("|") else 0
    end = len(parts) - 1 if line.rstrip().endswith("|") else len(parts)
    return tuple(parts[start:end])


def _blocks(text: str) -> Iterator[tuple[ParsedRow, ...]]:
    """Yield contiguous pipe-bearing rows.

    The corpus contains extractor tables without Markdown separator rows, so
    the first row is the structural header. This deliberately scans more
    broadly than the strict frame builder: every contiguous run of pipe-bearing
    lines is treated as a candidate block.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if "|" not in lines[index]:
            index += 1
            continue
        block = []
        while index < len(lines) and "|" in lines[index]:
            block.append(ParsedRow(index, _logical_cells(lines[index])))
            index += 1
        if block:
            yield tuple(block)


def _label_kinds(cell: str) -> tuple[str, ...]:
    cell = unicodedata.normalize("NFC", cell)
    kinds = []
    citizenship = CITIZENSHIP_LABEL.search(cell)
    if citizenship and "सिफारिस" not in citizenship.group(0):
        kinds.append("citizenship")
    if DOB_LABEL.search(cell):
        kinds.append("date_of_birth")
    return tuple(kinds)


def _candidate_shape(cell: str, kind: str) -> CandidateShape | None:
    core = cell.strip()
    if not VALUE_CELL.fullmatch(core):
        return None
    digit_count = sum(ch.isdigit() for ch in core)
    low, high = LIMITS[kind]
    if not low <= digit_count <= high:
        return None
    return CandidateShape(
        digit_count=digit_count,
        had_separators=bool(re.search(r"[-/.\s]", core)),
    )


def scan(text: str) -> tuple[list[Target], Counter]:
    """Return physical cells to redact and aggregate refusal counters."""
    typed: dict[tuple[CellRef, str], TypedEvidence] = {}
    protected_kinds: dict[CellRef, set[str]] = defaultdict(set)
    stats = Counter()

    def record(
        row: ParsedRow,
        column_index: int,
        kind: str,
        mechanism: str,
        shape: CandidateShape,
        label_context: tuple[str, ...],
    ) -> None:
        ref = CellRef(row.line_index, column_index)
        key = (ref, kind)
        typed.setdefault(key, TypedEvidence(kind, mechanism, shape))
        for context_kind in label_context:
            if _candidate_shape(row.cells[column_index], context_kind):
                protected_kinds[ref].add(context_kind)

    for block in _blocks(text):
        stats["table_blocks_seen"] += 1
        header = block[0]
        header_labels = [
            (column_index, kinds)
            for column_index, cell in enumerate(header.cells)
            if (kinds := _label_kinds(cell))
        ]

        for row in block:
            for column_index, kinds in header_labels:
                if column_index >= len(row.cells):
                    continue
                for kind in kinds:
                    shape = _candidate_shape(row.cells[column_index], kind)
                    if shape:
                        record(
                            row,
                            column_index,
                            kind,
                            "header_column",
                            shape,
                            kinds,
                        )

            already_redacted = set()
            for cell in row.cells:
                for placeholder, kinds in PLACEHOLDER_KINDS.items():
                    if placeholder in cell:
                        already_redacted.update(kinds)

            for label_column, cell in enumerate(row.cells):
                label_context = _label_kinds(cell)
                for kind in label_context:
                    # A prior invocation or another mechanism already made the
                    # row safe for this label. Do not let removing one of two
                    # ambiguous numbers make the other look unique on rerun.
                    #
                    # 🛑 Row safety is NOT per kind, because the placeholder this
                    # writes IS per kind. A citizenship value removed from the row
                    # shrinks the candidate set every other label in that row is
                    # measured against, so keying the guard on `kind` let a
                    # date-of-birth label whose two candidates were refused as
                    # ambiguous see one of them vanish and promote the survivor as
                    # `same_row_unique`. The rescan in `main` then found a target
                    # in output this pass had just written and aborted the whole
                    # run -- measured on v18 as exactly one document, 11862, and
                    # one cell. Any table placeholder means this row already gave
                    # a personal value up; refuse the rest of its labels.
                    if already_redacted:
                        stats[f"{kind}_refused_already_redacted_row"] += 1
                        continue
                    candidates = [
                        (column_index, shape)
                        for column_index, candidate_cell in enumerate(row.cells)
                        if (shape := _candidate_shape(candidate_cell, kind))
                    ]
                    adjacent = [
                        candidate
                        for candidate in candidates
                        if abs(candidate[0] - label_column) == 1
                    ]
                    if len(adjacent) == 1:
                        column_index, shape = adjacent[0]
                        mechanism = "same_row_adjacent"
                    elif len(candidates) == 1:
                        column_index, shape = candidates[0]
                        mechanism = "same_row_unique"
                    else:
                        if candidates:
                            stats[f"{kind}_refused_ambiguous_row"] += 1
                        continue
                    record(
                        row,
                        column_index,
                        kind,
                        mechanism,
                        shape,
                        label_context,
                    )

    physical: dict[CellRef, list[TypedEvidence]] = defaultdict(list)
    for (ref, _kind), evidence in typed.items():
        physical[ref].append(evidence)

    targets = []
    for ref, evidence in sorted(physical.items()):
        ordered = tuple(
            sorted(
                evidence,
                key=lambda item: (MECHANISM_ORDER[item.mechanism], item.kind),
            )
        )
        shapes = {item.shape for item in ordered}
        if len(shapes) != 1:
            raise AssertionError(f"candidate shape disagrees at {ref}")
        kinds = protected_kinds[ref] or {item.kind for item in ordered}
        classification = (
            next(iter(kinds)) if len(kinds) == 1 else "ambiguous_personal_value"
        )
        targets.append(Target(ref, classification, ordered, tuple(sorted(kinds))))
        stats[f"physical_{classification}"] += 1
        for item in ordered:
            stats[f"typed_{item.kind}"] += 1
            stats[f"mechanism_{item.mechanism}"] += 1

    stats["typed_candidate_links"] = len(typed)
    stats["physical_cells"] = len(targets)
    return targets, stats


def _line_body_and_ending(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body) :]


def apply_targets(text: str, targets: list[Target]) -> str:
    """Replace target cells while preserving all non-target bytes."""
    lines = text.splitlines(keepends=True)
    by_line: dict[int, list[Target]] = defaultdict(list)
    for target in targets:
        by_line[target.ref.line_index].append(target)

    for line_index, line_targets in by_line.items():
        body, ending = _line_body_and_ending(lines[line_index])
        parts = body.split("|")
        logical_start = 1 if body.lstrip().startswith("|") else 0
        logical_end = len(parts) - 1 if body.rstrip().endswith("|") else len(parts)
        logical_width = logical_end - logical_start

        for target in sorted(line_targets, key=lambda item: item.ref.column_index):
            column_index = target.ref.column_index
            if not 0 <= column_index < logical_width:
                raise AssertionError(f"target column vanished at {target.ref}")
            part_index = logical_start + column_index
            cell = parts[part_index]
            if not any(
                _candidate_shape(cell, item.kind) == item.shape
                for item in target.evidence
            ):
                raise AssertionError(f"target value changed at {target.ref}")

            leading = cell[: len(cell) - len(cell.lstrip())]
            trailing = cell[len(cell.rstrip()) :]
            parts[part_index] = leading + target.placeholder + trailing

        lines[line_index] = "|".join(parts) + ending

    return "".join(lines)


def redact_table_text(text: str) -> tuple[str, list[Target], Counter]:
    targets, stats = scan(text)
    return apply_targets(text, targets), targets, stats
