"""Markdown renderer for extracted documents."""

from __future__ import annotations

from collections import OrderedDict
import re

import yaml

from likhit.models import ExtractionResult, ParagraphBlock, Section, Table, TableBlock
from likhit.renderers.base import OutputRenderer

#: Marks where a source page begins. An HTML comment, so it is invisible in
#: rendered Markdown while staying greppable; namespaced, so it cannot collide
#: with a comment that was already in the source document. Page-keyed data (OCR
#: results, per-page provenance) has nothing to attach to without these.
PAGE_ANCHOR_PATTERN = re.compile(r"<!-- likhit:page (\d+) -->")

_SERIAL_PATTERN = re.compile(r"^[०-९0-9]+(?:[.)।])?$")
_DATE_CASE_PATTERN = re.compile(r"(?:/.*(?:CR-|२०८|208)|(?:CR-|२०८|208).*/)")


def page_anchor(page_number: int) -> str:
    """Render the anchor marking the start of `page_number`."""

    return f"<!-- likhit:page {page_number} -->"


def page_anchor_numbers(markdown: str) -> list[int]:
    """Return the anchored page numbers in the order they appear."""

    return [int(match.group(1)) for match in PAGE_ANCHOR_PATTERN.finditer(markdown)]


def strip_page_anchors(markdown: str) -> str:
    """Remove page anchors, for consumers that must not see them."""

    return PAGE_ANCHOR_PATTERN.sub("", markdown)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _caption_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.replace("\uf001", "")).casefold()


def _normalize_header_label(text: str) -> str:
    compact = _clean_text(text).replace(" ", "")
    aliases = {
        "आयोगकोनिर्णय": "आयोगको निर्णय",
        "मिति/मुद्दानंर": "मिति/मुद्दा नं र",
    }
    return aliases.get(compact, _clean_text(text))


def _anchor_grid(table: Table) -> list[list[str]]:
    grid = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        grid[cell.row][cell.col] = " ".join(
            _clean_text(part) for part in cell.text.splitlines() if part.strip()
        )
    return grid


def _expanded_grid(table: Table) -> list[list[str]]:
    grid = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        text = " ".join(
            _clean_text(part) for part in cell.text.splitlines() if part.strip()
        )
        for row in range(cell.row, min(cell.row + cell.rowspan, table.row_count)):
            for col in range(cell.col, min(cell.col + cell.colspan, table.col_count)):
                if not grid[row][col]:
                    grid[row][col] = text
    return grid


def _is_record_key_header(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return compact in {
        "सि.नं",
        "सि.नं.",
        "क्र.सं",
        "क्र.सं.",
        "क्रसं",
        "क्रसं.",
        "sn",
        "s.n.",
        "no",
        "no.",
    }


def _row_nonempty_values(row: list[str]) -> list[str]:
    return [cell.strip() for cell in row if cell.strip()]


def _looks_like_title_row(row: list[str]) -> bool:
    values = _row_nonempty_values(row)
    return len(row) >= 4 and bool(values) and len(set(values)) == 1


def _is_placeholder_header(text: str) -> bool:
    return bool(re.fullmatch(r"स्तम्भ \d+", text.strip()))


def _first_nonempty_cell(row: list[str]) -> str:
    for cell in row:
        if cell.strip():
            return cell.strip()
    return ""


def _looks_like_data_key(value: str) -> bool:
    return bool(_SERIAL_PATTERN.fullmatch(value.strip()))


def _title_row_count(expanded: list[list[str]]) -> int:
    count = 0
    for row in expanded:
        if not _looks_like_title_row(row):
            break
        count += 1
    return count


def _data_start(expanded: list[list[str]], title_rows: int) -> int:
    for index in range(title_rows, len(expanded)):
        if any(_looks_like_data_key(cell) for cell in expanded[index] if cell.strip()):
            return index
    return min(title_rows + 1, len(expanded))


def _header_parts(
    expanded: list[list[str]],
    title_rows: int,
    data_start: int,
) -> list[list[str]]:
    headers: list[list[str]] = []
    for col in range(len(expanded[0]) if expanded else 0):
        seen: list[str] = []
        for row in range(title_rows, data_start):
            value = expanded[row][col].strip()
            if value and value not in seen:
                seen.append(value)
        headers.append(seen or [f"स्तम्भ {col + 1}"])
    return headers


def _compose_headers(header_parts: list[list[str]]) -> list[str]:
    return [" / ".join(parts) for parts in header_parts]


def _find_key_column(expanded: list[list[str]], data_start: int) -> int | None:
    best_column: int | None = None
    best_count = 0
    for col in range(len(expanded[0]) if expanded else 0):
        count = sum(
            1
            for row in expanded[data_start:]
            if row[col].strip() and _looks_like_data_key(row[col])
        )
        if count > best_count:
            best_column = col
            best_count = count
    return best_column if best_count > 0 else None


def _collect_column_values(
    anchor_rows: list[list[str]],
    row_indexes: list[int],
    col: int,
) -> list[str]:
    values: list[str] = []
    for row_index in row_indexes:
        value = anchor_rows[row_index][col].strip()
        if value and value not in values:
            values.append(value)
    return values


def _column_samples(
    anchor_rows: list[list[str]],
    col: int,
    limit: int = 4,
) -> list[str]:
    values: list[str] = []
    for row in anchor_rows:
        value = row[col].strip()
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def _is_decision_value(value: str) -> bool:
    compact = value.replace(" ", "")
    return "/" in compact and ("CR-" in compact or "२०८" in compact or "208" in compact)


def _is_claim_value(value: str) -> bool:
    return "दफा" in value or "रु." in value or "रु" in value


def _is_person_value(value: str) -> bool:
    markers = (
        "अधिकृत",
        "इन्जिनियर",
        "अध्यक्ष",
        "सचिव",
        "सदस्य",
        "उपाध्यक्ष",
        "उपसचिव",
        "निर्देशक",
        "प्रमुख",
        "प्रसाद",
        "कुमार",
        "प्रा.लि.",
    )
    return any(marker in value for marker in markers)


def _infer_header_parts(
    anchor_rows: list[list[str]],
    header_parts: list[list[str]],
    key_col: int,
) -> list[list[str]]:
    if not header_parts:
        return header_parts

    populated_cols = [
        col
        for col in range(len(header_parts))
        if col != key_col and _column_samples(anchor_rows, col)
    ]
    generic_cols = {
        col
        for col in populated_cols
        if all(_is_placeholder_header(part) for part in header_parts[col])
    }
    if not generic_cols:
        return header_parts

    text_cols: list[int] = []
    decision_col: int | None = None
    person_col: int | None = None
    claim_cols: list[int] = []

    for col in populated_cols:
        samples = _column_samples(anchor_rows, col)
        if not samples:
            continue
        joined = " ".join(samples)
        if decision_col is None and any(
            _is_decision_value(sample) for sample in samples
        ):
            decision_col = col
            continue
        if any(_is_claim_value(sample) for sample in samples):
            claim_cols.append(col)
            continue
        if person_col is None and any(_is_person_value(sample) for sample in samples):
            person_col = col
            continue
        if len(joined) > 20:
            text_cols.append(col)

    inferred = [parts[:] for parts in header_parts]

    if text_cols:
        inferred[text_cols[0]] = ["उजुरीको व्यहोरा"]
    if len(text_cols) > 1:
        inferred[text_cols[1]] = ["अनुसन्धानबाट पुष्टि भएको व्यहोरा"]
    if decision_col is not None:
        inferred[decision_col] = [
            "आयोगको निर्णय",
            "मिति/आरोपपत्र दायर",
            "मिति/मुद्दा नं र",
            "प्रतिवादी सङ्ख्या",
        ]
    if person_col is not None:
        inferred[person_col] = ["प्रतिवादीको नाम, पद र कार्यालय"]
    for col in claim_cols:
        inferred[col] = ["भ्रष्टाचारनिवारण ऐन, २०५९ बमोजिम कसुर/सजाय मागदाबी/बिगो"]

    return inferred


def _group_row_indexes(
    expanded_rows: list[list[str]],
    key_col: int,
    *,
    continuation_key: str | None = None,
) -> list[tuple[str, list[int]]]:
    groups: list[tuple[str, list[int]]] = []
    current_key: str | None = None
    current_rows: list[int] = []

    for row_index, row in enumerate(expanded_rows):
        key = row[key_col].strip()
        if key and _looks_like_data_key(key):
            if current_rows and current_key == key:
                current_rows.append(row_index)
                continue
            if current_rows:
                groups.append((current_key or str(len(groups) + 1), current_rows))
            current_key = key
            current_rows = [row_index]
            continue

        if current_rows:
            current_rows.append(row_index)
            continue

        if continuation_key is not None:
            current_key = continuation_key
            current_rows = [row_index]
            continue

        fallback_key = str(len(groups) + 1)
        groups.append((fallback_key, [row_index]))

    if current_rows:
        groups.append((current_key or str(len(groups) + 1), current_rows))
    return groups


def _render_field(
    lines: list[str],
    header_parts: list[str],
    values: list[str],
) -> None:
    if not values:
        return

    main_header = _normalize_header_label(header_parts[0])
    subheaders = [_normalize_header_label(part) for part in header_parts[1:]]
    values = _dedupe_overlapping_values(values)

    if not subheaders and len(values) == 1:
        lines.append(f"- **{main_header}:** {values[0]}")
        return

    if not subheaders:
        if _should_render_as_list(main_header, values):
            lines.append(f"- **{main_header}:**")
            for value in values:
                lines.append(f"  - {value}")
            return
        lines.append(f"- **{main_header}:** {_join_values(values)}")
        return

    lines.append(f"- **{main_header}:**")
    assignments = _assign_values_to_subheaders(subheaders, values)
    for subheader, subvalues in assignments:
        joined = _join_values(subvalues)
        if joined:
            lines.append(f"  - **{subheader}:** {joined}")


def _join_values(values: list[str]) -> str:
    return _clean_text(" ".join(value for value in values if value))


def _dedupe_overlapping_values(values: list[str]) -> list[str]:
    cleaned = [_clean_text(value) for value in values if _clean_text(value)]
    if len(cleaned) <= 1:
        return cleaned

    first = cleaned[0]
    rest = cleaned[1:]
    if rest and all(value in first for value in rest):
        return rest
    return cleaned


def _should_render_as_list(header: str, values: list[str]) -> bool:
    if len(values) <= 1:
        return False

    list_headers = (
        "प्रतिवादी",
        "कसुर",
        "मागदाबी",
        "बिगो",
    )
    if any(marker in header for marker in list_headers):
        return True

    if all(len(value) <= 36 for value in values):
        return False
    return False


def _looks_like_sparse_continuation_table(table: Table) -> bool:
    if table.caption:
        return False

    anchor = _anchor_grid(table)
    if not anchor:
        return False

    nonempty_rows = [row for row in anchor if any(cell.strip() for cell in row)]
    if len(nonempty_rows) < 3:
        return False

    key_hits = sum(
        1 for row in nonempty_rows[:6] for cell in row if _looks_like_data_key(cell)
    )
    populated_cells = sum(1 for row in anchor for cell in row if cell.strip())
    total_cells = max(table.row_count * table.col_count, 1)
    density = populated_cells / total_cells
    return density < 0.33 and key_hits <= 1


def _iter_sparse_group_texts(rows: list[list[str]]) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for cell in row:
            value = _clean_text(cell)
            if not value or value in seen:
                continue
            seen.add(value)
            texts.append(value)
    return texts


def _is_decision_fragment(value: str) -> bool:
    compact = value.replace(" ", "")
    return bool(_DATE_CASE_PATTERN.search(compact)) or (
        len(compact) <= 3 and compact.isdigit()
    )


def _is_law_fragment(value: str) -> bool:
    return "दफा" in value or "रु." in value or "रु" in value


def _is_investigation_fragment(value: str) -> bool:
    markers = (
        "मिलेमतोमा",
        "मूल्याङ्कन",
        "भुक्तानी",
        "वास्तविक",
        "कार्यसम्पन्न",
        "कामभन्दा",
        "छनोट गरी",
        "खरिद",
    )
    return any(marker in value for marker in markers)


def _is_complaint_fragment(value: str) -> bool:
    markers = (
        "भन्नेसमेत",
        "अनियमितता गरेको",
        "गुणस्तरहीन कार्य",
    )
    return any(marker in value for marker in markers)


def _classify_sparse_group_values(
    values: list[str],
) -> OrderedDict[str, list[str]]:
    fields: OrderedDict[str, list[str]] = OrderedDict(
        (
            ("उजुरीको व्यहोरा", []),
            ("अनुसन्धानबाट पुष्टि भएको व्यहोरा", []),
            ("आयोगको निर्णय", []),
            ("प्रतिवादीको नाम, पद र कार्यालय", []),
            ("भ्रष्टाचारनिवारण ऐन, २०५९ बमोजिम कसुर/सजाय मागदाबी/बिगो", []),
        )
    )

    for value in values:
        if _is_law_fragment(value):
            fields["भ्रष्टाचारनिवारण ऐन, २०५९ बमोजिम कसुर/सजाय मागदाबी/बिगो"].append(value)
            continue
        if _is_decision_fragment(value):
            fields["आयोगको निर्णय"].append(value)
            continue
        if _is_investigation_fragment(value):
            fields["अनुसन्धानबाट पुष्टि भएको व्यहोरा"].append(value)
            continue
        if _is_complaint_fragment(value):
            fields["उजुरीको व्यहोरा"].append(value)
            continue
        if _is_person_value(value) or "," in value:
            fields["प्रतिवादीको नाम, पद र कार्यालय"].append(value)
            continue
        if fields["प्रतिवादीको नाम, पद र कार्यालय"] and len(value) <= 80:
            fields["प्रतिवादीको नाम, पद र कार्यालय"].append(value)
            continue
        if not fields["उजुरीको व्यहोरा"]:
            fields["उजुरीको व्यहोरा"].append(value)
            continue
        if not fields["अनुसन्धानबाट पुष्टि भएको व्यहोरा"]:
            fields["अनुसन्धानबाट पुष्टि भएको व्यहोरा"].append(value)
            continue
        fields["प्रतिवादीको नाम, पद र कार्यालय"].append(value)

    return OrderedDict(
        (header, _dedupe_overlapping_values(values))
        for header, values in fields.items()
        if values
    )


def _render_sparse_continuation_records(
    table: Table,
    *,
    include_caption: bool = True,
    continuation_key: str | None = None,
) -> tuple[str, str | None]:
    anchor = _anchor_grid(table)
    nonempty_rows = [row for row in anchor if any(cell.strip() for cell in row)]
    if not nonempty_rows:
        return "", continuation_key

    groups: list[tuple[str, list[list[str]], bool]] = []
    current_key = continuation_key
    current_rows: list[list[str]] = []
    current_is_continuation = continuation_key is not None

    for row in nonempty_rows:
        row_first = _first_nonempty_cell(row)
        serial = row_first if _looks_like_data_key(row_first) else None
        cleaned_row = ["" if cell.strip() == serial else cell for cell in row]
        if serial is not None:
            if current_rows:
                groups.append(
                    (
                        current_key or str(len(groups) + 1),
                        current_rows,
                        current_is_continuation,
                    )
                )
            current_key = serial.rstrip(".।)")
            current_rows = [cleaned_row]
            current_is_continuation = False
            continue

        if current_key is None:
            current_key = str(len(groups) + 1)
            current_is_continuation = False
        current_rows.append(cleaned_row)

    if current_rows:
        groups.append(
            (current_key or str(len(groups) + 1), current_rows, current_is_continuation)
        )

    lines: list[str] = []
    if include_caption and table.caption:
        lines.append(table.caption)
        lines.append("")

    last_key = continuation_key
    for record_key, rows, is_continuation in groups:
        heading = f"{record_key} (जारी)" if is_continuation else record_key
        lines.append(f"**{heading}**")
        grouped_fields = _classify_sparse_group_values(_iter_sparse_group_texts(rows))
        for main_header, values in grouped_fields.items():
            _render_field(lines, [main_header], values)
        lines.append("")
        last_key = record_key

    return "\n".join(lines).strip(), last_key


def _assign_values_to_subheaders(
    subheaders: list[str],
    values: list[str],
) -> list[tuple[str, list[str]]]:
    if not subheaders:
        return []
    if len(subheaders) == 1:
        return [(subheaders[0], values)]
    if len(values) == len(subheaders):
        return [
            (header, [value]) for header, value in zip(subheaders, values, strict=False)
        ]
    if len(subheaders) == 2:
        return [
            (subheaders[0], values[:-1] or values),
            (subheaders[1], values[-1:] if len(values) > 1 else []),
        ]
    if len(subheaders) >= 3 and len(values) >= 3:
        return [
            (subheaders[0], values[:-2]),
            (subheaders[1], [values[-2]]),
            (subheaders[2], [values[-1]]),
        ]
    return [
        (header, values) if index == 0 else (header, [])
        for index, header in enumerate(subheaders)
    ]


def _render_simple_records(
    table: Table,
    *,
    include_caption: bool = True,
) -> tuple[str, str | None]:
    anchor = _anchor_grid(table)
    expanded = _expanded_grid(table)
    if not anchor:
        return "", None

    title_rows = _title_row_count(expanded)
    data_start = _data_start(expanded, title_rows)
    headers = _compose_headers(_header_parts(expanded, title_rows, data_start))
    body_rows = [
        row for row in anchor[data_start:] if any(cell.strip() for cell in row)
    ]
    if not body_rows:
        return "", None

    lines: list[str] = []
    if include_caption and table.caption:
        lines.append(table.caption)
        lines.append("")

    use_first_cell_as_heading = bool(headers) and _is_record_key_header(headers[0])
    normalized_rows = _merge_continuation_rows(body_rows, use_first_cell_as_heading)
    last_heading: str | None = None

    for row_index, row in enumerate(normalized_rows, start=1):
        row_heading = row[0].strip() if row and row[0].strip() else str(row_index)
        if use_first_cell_as_heading:
            lines.append(f"**{row_heading}**")
            pairs = zip(headers[1:], row[1:], strict=False)
            last_heading = row_heading
        else:
            lines.append(f"**पंक्ति {row_index}**")
            pairs = zip(headers, row, strict=False)

        for header, value in pairs:
            cleaned_value = value.strip()
            if not cleaned_value:
                continue
            lines.append(f"- **{header}:** {cleaned_value}")
        lines.append("")
    return "\n".join(lines).strip(), last_heading


def _render_grouped_records(
    table: Table,
    *,
    include_caption: bool = True,
    continuation_key: str | None = None,
) -> tuple[str, str | None]:
    anchor = _anchor_grid(table)
    expanded = _expanded_grid(table)
    if not anchor:
        return "", continuation_key

    title_rows = _title_row_count(expanded)
    data_start = _data_start(expanded, title_rows)
    header_parts = _header_parts(expanded, title_rows, data_start)
    key_col = _find_key_column(expanded, data_start)
    if key_col is None:
        return _render_simple_records(table, include_caption=include_caption)

    anchor_rows = anchor[data_start:]
    expanded_rows = expanded[data_start:]
    header_parts = _infer_header_parts(anchor_rows, header_parts, key_col)
    groups = _group_row_indexes(
        expanded_rows,
        key_col,
        continuation_key=continuation_key,
    )
    if not groups:
        return _render_simple_records(table, include_caption=include_caption)

    lines: list[str] = []
    if include_caption and table.caption:
        lines.append(table.caption)
        lines.append("")

    last_key: str | None = continuation_key
    for record_key, row_indexes in groups:
        heading = (
            f"{record_key} (जारी)"
            if continuation_key is not None
            and record_key == continuation_key
            and row_indexes[0] == 0
            else record_key
        )
        lines.append(f"**{heading}**")
        grouped_fields: OrderedDict[str, tuple[list[str], list[str]]] = OrderedDict()
        for col, parts in enumerate(header_parts):
            if col == key_col:
                continue
            values = _collect_column_values(anchor_rows, row_indexes, col)
            if not values:
                continue
            normalized_parts = [_normalize_header_label(part) for part in parts]
            main_header = normalized_parts[0]
            subheaders = normalized_parts[1:]
            existing = grouped_fields.get(main_header)
            if existing is None:
                grouped_fields[main_header] = (subheaders, values)
                continue
            existing_subheaders, existing_values = existing
            if existing_subheaders and not subheaders:
                continue
            if subheaders and not existing_subheaders:
                grouped_fields[main_header] = (subheaders, values)
                continue
            grouped_fields[main_header] = (
                existing_subheaders or subheaders,
                existing_values
                + [value for value in values if value not in existing_values],
            )
        for main_header, (subheaders, values) in grouped_fields.items():
            _render_field(lines, [main_header, *subheaders], values)
        lines.append("")
        last_key = record_key
        continuation_key = None
    return "\n".join(lines).strip(), last_key


def _merge_continuation_rows(
    rows: list[list[str]],
    use_first_cell_as_heading: bool,
) -> list[list[str]]:
    if not use_first_cell_as_heading:
        return rows

    merged: list[list[str]] = []
    for row in rows:
        if merged and not row[0].strip():
            previous = merged[-1]
            for index in range(1, len(row)):
                addition = row[index].strip()
                if not addition:
                    continue
                previous[index] = (
                    f"{previous[index]} {addition}".strip()
                    if previous[index].strip()
                    else addition
                )
            continue
        merged.append(row[:])
    return merged


#: A line that is nothing but a figure -- `verify_table_integrity.py`'s own
#: definition, so "is this line a bare figure" means the same in the renderer as
#: in the instrument that measures the renderer.
_BARE_FIGURE = re.compile(r"^[\s0-9०-९,.।-]+$")
_ANY_DIGIT = re.compile(r"[0-9०-९]")
#: A line whose last token is a figure. This is the tell that separates a REGISTER ROW
#: from a wrapped sentence, and it is needed because the bare-figure test below cannot
#: see one any more.
#:
#: Before the swallowed-sub-table extraction fix, a cell that had swallowed a register
#: held one value per line -- "190", "SCA Dalit", "7980" -- so several lines WERE bare
#: figures and `_wrapped_lines_are_one_row` refused to rejoin on that basis. The fix
#: space-joins each printed row instead, so the lines now read
#: "190 SCA Dalit Seti Kamani 7980". Every one of them contains letters, no line is a
#: bare figure, the guard stops firing, and the whole register is mashed into one row.
#: Measured on that fix's own fixture: 3 rows became 1, with the full suite green.
#:
#: A wrapped sentence breaks at an arbitrary point, so its non-final lines rarely end
#: on a figure. A register row always ends on its amount. Requiring EVERY line to end
#: that way keeps the rule conservative -- a false negative merely declines to rejoin,
#: which is the direction this whole guard already errs in.
_TRAILING_FIGURE = re.compile(r"[0-9०-९][0-9०-९,.]*[\s।]*$")


def _wrapped_lines_are_one_row(cell_lines: list[list[str]]) -> bool:
    """Is this row's multi-line text one wrapped row that can safely be rejoined?

    Only when both hold:

    **At most one column carries several lines.** The transposed form is otherwise
    ambiguous: `[["a1","a2"], ["b1","b2"]]` renders the same whether it is one row
    whose two cells each wrapped, or two sub-rows pairing a1 with b1 and a2 with
    b2. The cell text cannot tell those apart -- only the fragment y-coordinates
    could, and `_extract_cell_text` has discarded them by this point. When two or
    more columns wrap, a real pairing may exist and collapsing would destroy it.

    **None of that column's lines is a bare figure.** A line that is only a number
    is not a sentence continuation, and joining it would corrupt one of two things
    this corpus is full of:

    - a figure split across visual lines (`185929593.` + `20` is one amount). A
      space join yields `185929593. 20`; a bare concatenation builds the >=15-digit
      run `verify_numeric_boundaries.py` flags, feeding the D7 gate.
    - a cell bbox that swallowed a nested sub-table, so it holds a whole register
      of distinct values rather than one wrapped sentence. Joining would mash the
      sub-table into a single string. That is an extraction defect and is not the
      renderer's to paper over.

    So this deliberately leaves most of the corpus's one-cell rows alone. The share
    it does fix is measured, not assumed -- `runs/vol71/` in the OAG corpus.
    """

    wrapped = [parts for parts in cell_lines if len(parts) > 1]
    if len(wrapped) != 1:
        return not wrapped
    if _looks_like_register_rows(wrapped[0]):
        return False
    return not any(
        _BARE_FIGURE.match(part) and _ANY_DIGIT.search(part) for part in wrapped[0]
    )


def _looks_like_register_rows(parts: list[str]) -> bool:
    """Do these lines read as separate records rather than one wrapped sentence?

    See `_TRAILING_FIGURE`. Two or more lines, every one of them ending on a figure,
    and at least one carrying a letter -- the letter requirement keeps this from
    duplicating the bare-figure test, so the two clauses stay independently meaningful
    and a mutation of either is visible.
    """

    lines = [part for part in parts if part.strip()]
    if len(lines) < 2:
        return False
    if not all(_TRAILING_FIGURE.search(line) for line in lines):
        return False
    return any(re.search(r"[^\s0-9०-९,.।-]", line) for line in lines)


def _render_raw_table_lines(
    table: Table,
    *,
    include_caption: bool = True,
) -> tuple[str, str | None]:
    grid = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    covered = [[False for _ in range(table.col_count)] for _ in range(table.row_count)]
    # A malformed table can anchor one cell inside another's span. Blanking such
    # a position would silently drop text that was extracted, so anchors always
    # win over coverage -- the same precedence `_expanded_grid` gets from its
    # `if not grid[row][col]` guard.
    anchored = {(cell.row, cell.col) for cell in table.cells}
    for cell in table.cells:
        grid[cell.row][cell.col] = cell.text
        for row in range(cell.row, min(cell.row + cell.rowspan, table.row_count)):
            for col in range(cell.col, min(cell.col + cell.colspan, table.col_count)):
                if (row, col) not in anchored:
                    covered[row][col] = True
    lines: list[str] = []

    if include_caption and table.caption:
        lines.append(table.caption)
        lines.append("")

    for row_index, row in enumerate(grid):
        cell_lines = [
            (
                []
                if covered[row_index][col_index]
                else [
                    _clean_text(part) for part in cell.splitlines() if _clean_text(part)
                ]
            )
            for col_index, cell in enumerate(row)
        ]
        max_line_count = max(
            (len(parts) for parts in cell_lines),
            default=0,
        )
        if max_line_count == 0:
            continue
        if _wrapped_lines_are_one_row(cell_lines):
            # One logical row, whose text wrapped over several PDF lines. Emitting
            # a Markdown line per wrapped line would state a row boundary that the
            # grid does not contain, and every consumer that reads a line as a row
            # then reads one finding as several -- and cannot tell that a line
            # continues the previous cell rather than starting a new row, because
            # nothing in the output distinguishes the two. Rejoin instead, the same
            # way `_merge_continuation_rows` rejoins a continuation onto its anchor.
            values = [" ".join(parts) for parts in cell_lines]
            if any(values):
                lines.append(f"| {' | '.join(values)} |")
            continue
        for line_index in range(max_line_count):
            values = [
                parts[line_index] if line_index < len(parts) else ""
                for parts in cell_lines
            ]
            if any(values):
                lines.append(f"| {' | '.join(values)} |")

    return "\n".join(lines).strip(), None


def _render_table(
    table: Table,
    *,
    include_caption: bool = True,
    continuation_key: str | None = None,
) -> tuple[str, str | None]:
    del continuation_key
    return _render_raw_table_lines(
        table,
        include_caption=include_caption,
    )


def render_table_markdown(
    table: Table,
    *,
    include_caption: bool = True,
    continuation_key: str | None = None,
) -> str:
    rendered, _continuation_key = _render_table(
        table,
        include_caption=include_caption,
        continuation_key=continuation_key,
    )
    return rendered


def render_table_preformatted_markdown(
    table: Table,
    *,
    include_caption: bool = True,
    continuation_key: str | None = None,
) -> str:
    rendered = render_table_markdown(
        table,
        include_caption=include_caption,
        continuation_key=continuation_key,
    )
    if not rendered.strip():
        return ""
    return f"```text\n{rendered}\n```"


#: The running header, with the space the PDF prints it with compacted away. Named
#: rather than repeated because `strip_page_furniture_lines` has to reason about the
#: same token to find a header that WRAPS, and two literals would drift apart.
_RUNNING_HEADER = "वार्षिकप्रतिवेदन"

#: How many text-carrying lines a wrapped running header may be sought across. A
#: header wraps once, occasionally twice, so 3 covers every plausible layout.
#:
#: This is a bound on blast radius and cost, NOT a correctness threshold, and the
#: distinction is worth stating because a length cap was already refuted for this
#: rule. The scan can only ever match when the lines between the two halves of the
#: token are themselves token fragments -- body text between them stops the token
#: forming at all -- so a larger bound would not delete prose. What it would do is
#: make an unbounded O(n^2) pass over every block that mentions the header. A header
#: split across MORE than this many lines is therefore left rendered, and that is
#: the deliberate trade: the failure mode is a visible header, not deleted body.
_MAX_WRAPPED_HEADER_LINES = 3


def _looks_like_page_furniture(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    stripped = text.strip()
    return (
        bool(re.match(r"^\d+\s*परिच्छेद", text))
        or _RUNNING_HEADER in compact
        or (stripped.isdigit() and len(stripped) <= 3)
    )


def strip_page_furniture_lines(text: str) -> str:
    """Drop the running-header/footer LINES from a paragraph block's text.

    `_looks_like_page_furniture` is a substring test — "वार्षिकप्रतिवेदन"
    anywhere in the whitespace-stripped text — and it used to be asked about a
    whole `ParagraphBlock`. When the layout pass merges a page's running header
    into the same block as the page's body, that made the predicate true of the
    entire page, and the renderer discarded all of it.

    Measured on all 13 CIAA annual reports, at BLOCK grain: the rule drops **877**
    paragraph blocks, of which **793 are pure furniture and correctly dropped** —
    and **84 carry real body text, losing 86,812 characters**. Every one has a
    healthy PDF text layer, and each is armed by a table on the *neighbouring*
    page, because the adjacency test looks at neighbours in the flat cross-page
    block list.

    🛑 Two grains, both correct, so name which one a figure is: an earlier pass
    counted **pages left empty** and found **9**, over three reports (2072-73 p58,
    2073-74 p15, seven pages of 2081-82). A page that loses one block but keeps
    others is invisible to that count, which is why the block-grain figure is 84.
    Neither number is wrong; they measure different things.

    A length cap was considered and REFUTED on measurement — there is no
    separating threshold. The smallest wrongly-dropped block is **82** characters
    and the largest correctly-dropped one is **137**, so the ranges overlap: any
    cap at or below 137 keeps 118 blocks that should go, and any cap above 82 still
    deletes 7 that should stay. This matters because it contradicts what the
    converter's own import comment claimed the pending fix would be.

    Testing line by line keeps the behaviour that was wanted — a block that is
    nothing *but* furniture still renders as nothing, since every line is stripped
    and the caller skips the emptied block — while a page of body text can no
    longer be deleted by the header printed above it.

    🛑 Line grain alone does NOT keep that guarantee, and this is why the run scan
    below exists. Clause 2 of the predicate tests whitespace-**compacted** text, so
    a running header the layout wrapped across a line break —
    `वार्षिक\\nप्रतिवेदन` — matches at block grain and at **no** line grain. Testing
    only single lines therefore turned a block that was discarded whole into one
    rendered in full: the exact opposite of the promise above, and invisible to a
    suite whose header fixture is a single-line constant. Found in review.

    Dropping the whole block in that case is the wrong repair — it re-opens this
    defect for the mixed shape `header / header / body`, which is precisely the 84.
    So the scan instead drops the **shortest run** of consecutive lines whose joined
    text carries the header, which is exactly what the single-line rule would have
    done had the header not wrapped. It is therefore no more eager than the line
    rule already is: a body line containing `वार्षिक प्रतिवेदन` inline is dropped
    whole today, and a body line pair that straddles it is now dropped the same way.
    Shortest-run-first is what keeps the body: `वार्षिक / प्रतिवेदन / यो वाक्य` loses
    its first two lines and keeps the third.

    Two bounds matter. The run is capped at `_MAX_WRAPPED_HEADER_LINES`, or a stray
    `वार्षिक` and a `प्रतिवेदन` fifty lines apart would condemn everything between
    them. And "nothing but a header plus digits" was tried first and REJECTED: the
    real running header is `परिच्छेद-६, तामेली तथा मुल्तबी २४५ वार्षिक प्रतिवेदन,
    २०८१/८२`, so a strictness test on the token alone leaves the realistic wrap
    undetected and only catches a bare `वार्षिक\\nप्रतिवेदन` that no report prints.

    Measured over the 13 reports: **0** blocks change, because every body-carrying
    block in this corpus prints its header on its own line. The scan is for the
    shape the corpus happens not to contain, which is the only kind of hole a corpus
    measurement cannot close.

    Callers must apply this only to a block the whole-block predicate already
    rejected, i.e. one that was going to be discarded outright. Two of the three
    clauses are strictly more eager per line than per block — `^\\d+\\s*परिच्छेद`
    is anchored, so per line it matches at any line rather than only the first,
    and the bare-short-number clause per line strips a page number sitting inside
    a longer block. Applying this unconditionally therefore deletes text the old
    code kept: measured at 15 characters over the CIAA corpus (2069-70 −8,
    2071-72 −7). Gating on the block predicate makes the change purely additive —
    it can only ever return text that was about to be thrown away.
    """
    lines = text.splitlines()
    drop = [_looks_like_page_furniture(line) for line in lines]

    # The wrapped-header run scan. Gated on the token being in the compacted block,
    # which is both the only way a wrap can exist and the cheap common-case exit --
    # a block flagged by the chapter or bare-page-number clause never enters here.
    if _RUNNING_HEADER in re.sub(r"\s+", "", text):
        # Over the lines that carry text and are not already going: a blank line, or
        # a page number between the two halves, must not consume the run budget.
        live = [
            index
            for index, line in enumerate(lines)
            if line.strip() and not drop[index]
        ]
        # SHORTEST run first, so a header spanning two lines cannot swallow a third
        # that carries body text.
        for first in range(len(live)):
            limit = min(first + _MAX_WRAPPED_HEADER_LINES, len(live))
            for last in range(first + 2, limit + 1):
                run = "".join(lines[index] for index in live[first:last])
                if _RUNNING_HEADER in re.sub(r"\s+", "", run):
                    for index in live[first:last]:
                        drop[index] = True
                    break

    return "\n".join(line for line, dropped in zip(lines, drop) if not dropped)


def _render_paragraph_markdown(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return "  \n".join(line for line in lines if line.strip()).strip()


def _paragraph_ends_with_caption(text: str, caption: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return _caption_key(lines[-1]) == _caption_key(caption)


def _render_section(section: Section) -> list[str]:
    parts: list[str] = []
    if section.heading:
        parts.append(f"{'#' * section.level} {section.heading}")
        parts.append("")
    if section.blocks:
        previous_table_key: str | None = None
        for index, block in enumerate(section.blocks):
            paragraph_text = block.text if isinstance(block, ParagraphBlock) else ""
            if (
                isinstance(block, ParagraphBlock)
                and _looks_like_page_furniture(paragraph_text)
                and (
                    (index > 0 and isinstance(section.blocks[index - 1], TableBlock))
                    or (
                        index + 1 < len(section.blocks)
                        and isinstance(section.blocks[index + 1], TableBlock)
                    )
                )
            ):
                # This block was about to be discarded whole. Keep its non-furniture
                # lines instead: the predicate is a substring test, so a running
                # header merged into the body condemned the entire page (VOL-668).
                paragraph_text = strip_page_furniture_lines(paragraph_text)
                if not paragraph_text.strip():
                    continue
            if index:
                parts.append("")
            if isinstance(block, ParagraphBlock):
                parts.append(_render_paragraph_markdown(paragraph_text))
                previous_table_key = None
            elif isinstance(block, TableBlock):
                include_caption = True
                if (
                    index > 0
                    and isinstance(section.blocks[index - 1], ParagraphBlock)
                    and block.table.caption
                    and _paragraph_ends_with_caption(
                        section.blocks[index - 1].text,
                        block.table.caption,
                    )
                ):
                    include_caption = False
                rendered, previous_table_key = _render_table(
                    block.table,
                    include_caption=include_caption,
                    continuation_key=previous_table_key,
                )
                if rendered.strip():
                    parts.append(f"```text\n{rendered}\n```")
    else:
        parts.append(section.body)
    for subsection in section.subsections:
        parts.append("")
        parts.extend(_render_section(subsection))
    return parts


class MarkdownRenderer(OutputRenderer):
    """Render extraction results as Markdown with YAML frontmatter."""

    def render(self, result: ExtractionResult) -> str:
        frontmatter = OrderedDict(
            [
                ("title", result.title),
                ("doc_type", result.doc_type.value),
                ("likhit_version", result.likhit_version),
            ]
        )
        if result.publication_date:
            frontmatter["publication_date"] = result.publication_date
        if result.source_url:
            frontmatter["source_url"] = result.source_url

        body_lines: list[str] = []
        for index, section in enumerate(result.sections):
            if index:
                body_lines.append("")
            body_lines.extend(_render_section(section))

        frontmatter_text = yaml.safe_dump(
            dict(frontmatter),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        body = "\n".join(body_lines).strip()
        return f"---\n{frontmatter_text}\n---\n\n{body}\n"
