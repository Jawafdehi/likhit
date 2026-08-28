"""VOL-560: page-grain refusal detection for staged transcripts.

WHY THIS EXISTS, AND WHY `ocr_refusal.py` IS NOT ENOUGH. VOL-560 item 1 diagnosed
the staging certification gate as vacuous on refusals because it scores a DOCUMENT
aggregate: `11356` certified `verdict: clean, failing: []` at `chars 14703,
devanagari 7901, dev_ratio 0.989` while its page 5 recovered no figures at all. The
grain is genuinely the first defect. It is not the only one.

Measured on the 7 staged pages of `11356` (run `204e9dc1`), VOL-547's row-level
detector called EVERY page `delivered`, page 5 included:

    page 5   chars 845   dev 548   latin 21   dev_ratio 0.9631   ->  delivered

`ocr_refusal.DEV_RATIO_FLOOR` is 0.05, so page 5 sits 19x above it. That detector's
conjunction assumes a refusal is LATIN PROSE -- "I cannot read this image" -- which
is what a refusal looks like in the pilot and lane shards. Page 5 refuses **in
Devanagari**: it emits a well-formed Nepali table whose data cells are the literal
placeholder `[अस्पष्ट]` ("unclear"), and closes with a Nepali note that the image
was too poor and too rotated to read. By script statistics it is indistinguishable
from a delivered Nepali page, because it IS a delivered Nepali page -- of
placeholders. So fixing only the grain reproduces the vacuity, one unit smaller.

THE DISCRIMINATOR IS THE TABLE, NOT THE PROSE. Measured over all 160 staged pages,
the quantity that separates cleanly is the share of a page's tabulated DATA CELLS
that hold a placeholder rather than a value:

    11356 p5   845 chars    4 table rows     9 data cells   4 bracketed   0.4444
    11382 p5  3294 chars   22 table rows   206 data cells   0 bracketed   0.0000

Those are the only two staged pages containing the substring `अस्पष्ट` at all, and a
presence test would fail both. It should not: `11382` p5 delivered a complete
22-row financial table and flagged one illegible span in prose. Naming one cell
unreadable inside otherwise-transcribed data is honest annotation; a table whose
data cells ARE the annotation is a refusal. Hence a share, with a minimum cell
count so a 2-cell table cannot reach the floor on one placeholder.

CELL SHARE IS NOT THE ONLY SHAPE, SO THE PREDICATE IS A DISJUNCTION OF THREE. A
refusal page need not contain a table at all, and the Latin-prose refusals really do
exist -- `12514` p6 and p8 are exactly that, and VOL-547's detector is the right
instrument for them. Each term is reported by name, so the gate can say WHICH shape
fired rather than returning a bare boolean:

  * `placeholder_cells`  -- share of table data cells that are placeholders
  * `placeholder_prose`  -- refusal vocabulary at density, for pages with no table
  * `row_refusal`        -- supplied by the caller; the VOL-547 Latin-prose shape,
                            which needs the originating OCR row and so is not this
                            module's question to answer

DO NOT COLLAPSE THE VOCABULARY INTO A BARE BRACKET TEST. 39 of the 160 staged pages
carry brackets with no refusal word in them (max 8 on one page) -- markdown links,
editorial insertions, `[सही]` sign-off marks. A bare-bracket predicate would fail 40
pages instead of 1. The vocabulary is what makes a bracket a declination, exactly as
`ocr_refusal`'s abstention families are what make low Devanagari a refusal.

THRESHOLD PROVENANCE. `PLACEHOLDER_CELL_SHARE_FLOOR = 0.25` sits in a measured gap:
the one refusal page is at 0.4444 and every other staged page with a table is at
0.0000, so nothing occupies 0 < share < 0.4444. Corpus exposure is reported by
`runs/vol560-204e9dc1/corpus_exposure_204e9dc1.py` against
`markdown-quality-v14r1`, the same negative control VOL-547 used; the floor is
placed from that sweep and not from the 12 staged documents alone.

DEFAULT OFF. This lands opt-in behind `--page-refusal`, following VOL-523 item 2's
ruling on the repha floor: a gate whose corpus exposure is a co-occurrence rather
than a proof does not go default-on until the generation owner says so. Turning it
on is a generation decision, not this module's.
"""

from __future__ import annotations

import re

#: A page whose content the transcriber declined to produce, as opposed to one it
#: produced badly. Kept as a string rather than an enum to match the verdict
#: vocabulary the rest of this package and its records already use.
REFUSAL = "refusal"

#: Bracketed span on one line. Length-bounded so a runaway `[` cannot swallow a page.
_BRACKET = re.compile(r"\[([^\[\]\n]{1,120})\]")

#: A markdown link or image reference is a bracket that is not a placeholder.
_LINK = re.compile(r"\[[^\]]*\]\s*[\(\[]")

#: Devanagari declination vocabulary, observed inside bracketed cells in the staged
#: set. Each is the model saying it could not read something -- not document content.
#: Kept as substrings rather than anchored patterns because they appear both bare
#: (`[अस्पष्ट]`) and qualified (`[संस्थागत नाम - अस्पष्ट]`, `[हस्ताक्षर - पठन योग्य छैन]`).
PLACEHOLDER_WORDS: tuple[str, ...] = (
    "अस्पष्ट",  # unclear
    "पठन योग्य छैन",  # not legible
    "पढ्न सकिएन",  # could not be read
    "पढ्न सकिँदैन",  # cannot be read
    "स्पष्ट छैन",  # not clear
    "अपठनीय",  # unreadable
    "देखिँदैन",  # not visible
    "illegible",
    "unreadable",
    "not legible",
)

#: Share of a page's table DATA cells that must be placeholders before the page is
#: called a refusal. Measured gap: 0.4444 (the one refusal page) vs 0.0000
#: (every other staged page carrying a table).
PLACEHOLDER_CELL_SHARE_FLOOR = 0.25

#: Below this many data cells the share is not a measurement. A 2-cell table with one
#: placeholder is 0.5 and means nothing.
MIN_DATA_CELLS = 4

#: For a page with no table: placeholder cells per 1,000 chars, and an absolute floor.
#: `11356` p5 is at 17.75 per kchar; the highest non-refusal staged page is 0.304.
PLACEHOLDER_PROSE_PER_KCHAR = 5.0
MIN_PROSE_PLACEHOLDERS = 3

#: A cell counts as a placeholder only if its bracketed declination spans at least this
#: share of the cell. THE VOCABULARY ALONE IS NOT A PREDICATE, and this is measured, not
#: cautious: a first version of this module tested `word in cell` with no bracket
#: requirement and scored 15 false positives over the 335,132 pages of
#: `markdown-quality-v14r1` (run `204e9dc1`,
#: `runs/vol560-204e9dc1/CORPUS-EXPOSURE-v14r1-204e9dc1.json`). Every one was ordinary
#: audit Nepali -- `अस्पष्ट` ("unclear") and `देखिँदैन` ("cannot be seen") are what an
#: auditor writes about a finding: "the partner's role and profit-sharing are unclear".
#: 14 of the 15 matched inside cells of 757-2,732 chars.
#:
#: Bracket dominance separates the two populations with no overlap at all: all four
#: placeholder cells of `11356` p5 score 1.000 (the cell IS `[अस्पष्ट]`), and all 15
#: false-positive cells score 0.000 (no bracket at any offset). 0.5 is the midpoint of
#: an empty gap, not a tuned value.
PLACEHOLDER_CELL_DOMINANCE = 0.5


def _is_link(text: str, match: re.Match[str]) -> bool:
    """Is this bracketed span markdown link/image syntax?

    Matched against the SOURCE at the span's offset, not against the span itself.
    `_LINK` needs to see the character after the closing bracket, and `m.group(0)`
    ends at that bracket -- so `_LINK.match(m.group(0))` can never match and the
    guard silently did nothing. Found by the mutation arm K14, which "survived"
    because removing a no-op guard changes no behaviour; the test that isolates it
    (`[अस्पष्ट](x)`, dominance 0.75) then failed against the real code.
    """
    return _LINK.match(text, match.start()) is not None


def _bracketed(text: str) -> list[str]:
    """Every bracketed span that is not markdown link syntax. Returns inner text."""
    return [m.group(1) for m in _BRACKET.finditer(text) if not _is_link(text, m)]


def has_placeholder_word(text: str) -> bool:
    """Vocabulary test only. For text already known to be a bracketed span."""
    return any(word in text for word in PLACEHOLDER_WORDS)


def is_placeholder_cell(cell: str) -> bool:
    """Is this table cell a declination rather than a value that mentions one?

    Requires a bracketed placeholder that DOMINATES the cell. Audit prose uses the
    same words about its subject matter; a transcript refusing a cell writes only the
    placeholder. See `PLACEHOLDER_CELL_DOMINANCE`.
    """
    cell = cell.strip()
    if not cell:
        return False
    hits = [
        m
        for m in _BRACKET.finditer(cell)
        if not _is_link(cell, m) and has_placeholder_word(m.group(1))
    ]
    if not hits:
        return False
    covered = sum(len(m.group(0)) for m in hits)
    return covered / len(cell) >= PLACEHOLDER_CELL_DOMINANCE


def data_cells(body: str) -> list[str]:
    """The data cells of every pipe table on the page.

    Separator rows (`---`, `:--:`) are not data and are dropped, or a table would be
    penalised for having a header. A line needs two pipes to count as a table row, so
    a sentence containing one `|` does not create a one-cell table.
    """
    cells: list[str] = []
    for line in body.splitlines():
        if line.count("|") < 2:
            continue
        for cell in line.strip().strip("|").split("|"):
            cell = cell.strip()
            if cell and not set(cell) <= set("-: "):
                cells.append(cell)
    return cells


def classify_page(body: str, row_refusal: bool = False) -> dict:
    """Classify one page body.

    Returns a verdict plus every term's measurement, so a caller can report which
    shape fired and an auditor can recompute the decision from the record.

    ``row_refusal`` is the third term, and it is passed IN rather than computed here on
    purpose. It used to take the originating OCR result row and ask
    ``ocr_refusal.classify(row)`` -- which meant this module knew the schema of an OCR
    provider's response, and pulled 533 lines of transcription-pipeline code in behind it
    for one boolean. The two terms above are pure text analysis and belong in a library;
    whether a particular provider refused a particular request does not. A caller that has
    that row supplies the answer.
    """
    cells = data_cells(body)
    placeholder_cells = [c for c in cells if is_placeholder_cell(c)]
    share = len(placeholder_cells) / len(cells) if cells else 0.0

    # The prose term reads already-extracted bracket contents, so it applies the
    # vocabulary test directly; the bracket requirement is in the extraction.
    prose_hits = [b for b in _bracketed(body) if has_placeholder_word(b)]
    per_kchar = 1000 * len(prose_hits) / len(body) if body else 0.0

    terms: list[str] = []
    if len(cells) >= MIN_DATA_CELLS and share >= PLACEHOLDER_CELL_SHARE_FLOOR:
        terms.append("placeholder_cells")
    # Only for pages that are not table-shaped: a table page is already judged by the
    # cell term, and judging it twice would double-count the same placeholders.
    if (
        len(cells) < MIN_DATA_CELLS
        and len(prose_hits) >= MIN_PROSE_PLACEHOLDERS
        and per_kchar >= PLACEHOLDER_PROSE_PER_KCHAR
    ):
        terms.append("placeholder_prose")
    if row_refusal:
        terms.append("row_refusal")

    return {
        "verdict": REFUSAL if terms else "delivered",
        "terms": terms,
        "data_cells": len(cells),
        "placeholder_cells": len(placeholder_cells),
        "placeholder_cell_share": round(share, 4),
        "prose_placeholders": len(prose_hits),
        "placeholder_per_kchar": round(per_kchar, 3),
        "chars": len(body),
        "examples": placeholder_cells[:3] or prose_hits[:3],
    }


def is_refusal_page(body: str, row_refusal: bool = False) -> bool:
    return classify_page(body, row_refusal)["verdict"] == REFUSAL
