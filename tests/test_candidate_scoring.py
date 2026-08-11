"""Cross-candidate comparison semantics of `_markdown_quality_score`.

The score has exactly one production caller: the `max()` that picks which
extraction ships. So a term only earns its place if it discriminates *between*
candidates. These tests pin the two terms that did not.
"""

import pytest

from likhit.converters.nepali_pdf import _markdown_quality_score
from likhit.extractors.font_based import _CID_MARK_BASE

CELLS = ("काठमाडौँ", "महानगरपालिका", "लेखापरीक्षण", "प्रतिवेदन")
ROWS = 40


def _table(blank_cells: int) -> str:
    """The same cell text, rendered with `blank_cells` explicit empty cells."""
    row = "| " + " | ".join(CELLS) + " |" + " |" * blank_cells
    return "\n".join([row] * ROWS)


def _mark(text: str) -> str:
    """Mark every character the way get_cid_marked_page_dict does."""
    return "".join(chr(_CID_MARK_BASE + ord(char)) for char in text)


def test_explicit_blank_table_cells_do_not_lower_the_score() -> None:
    # Rendering a table with explicit empty cells adds bare "|" tokens and
    # nothing else -- same words, same rows, same Devanagari. Counting those
    # pipes as single-character tokens made the excess-single-token penalty
    # scale with column count, so the candidate that declared its cell
    # boundaries scored below the one that dropped them.
    sparse = _table(blank_cells=0)
    explicit = _table(blank_cells=4)

    # >= rather than > so this keeps holding if the +len(tokens) bonus stops
    # counting table syntax as content (research fix-plan F05); it must never
    # go the other way.
    assert _markdown_quality_score(explicit) >= _markdown_quality_score(sparse)


def test_bare_pipes_do_not_trip_the_single_character_token_penalty() -> None:
    # Direct form of the same defect: pipes here are 62% of all tokens, well
    # past the 35% single-token ceiling, yet they are structure rather than the
    # per-character garble ("म ह ा ल े") the ceiling exists to catch.
    piped = _table(blank_cells=4)
    tokens = piped.split()
    assert sum(1 for token in tokens if token == "|") / len(tokens) > 0.35

    # Same words and rows without any table syntax at all. If pipes still
    # counted as single-character tokens, the piped form would be charged for
    # them and fall behind.
    prose = "\n".join([" ".join(CELLS)] * ROWS)
    assert _markdown_quality_score(piped) >= _markdown_quality_score(prose)


@pytest.mark.xfail(
    reason=(
        "Nothing charges a candidate per column. pipe_heavy_lines is flat per "
        "line while +len(tokens) credits +1 per pipe, so widening a table is "
        "pure gain (measured here: 6,516 against 5,000). Excusing pipes from "
        "the single-token ceiling removed the only term that happened to scale "
        "with column count, taking the net from -5 to +1 per pipe -- an "
        "accidental brake that charged likhit's real blank cells and "
        "markitdown's invented separator rows alike. The mechanism is the "
        "token credit, which is research fix-plan F05; this passes once F05 "
        "stops counting table syntax as content."
    ),
    strict=True,
)
def test_pipe_heavy_output_is_still_penalised() -> None:
    # The table signal ought to survive: excusing pipes from the single-token
    # ceiling should not leave runaway column counts uncharged.
    reasonable = _table(blank_cells=0)
    runaway = _table(blank_cells=40)

    assert _markdown_quality_score(runaway) < _markdown_quality_score(reasonable)


def test_marked_cids_do_not_decide_the_candidate_comparison() -> None:
    # Only a likhit candidate can carry a mark; every rival comes from pdfminer
    # or the OCR converter and never marks. Charging the mark therefore taxed
    # the candidate that labelled its unmappable glyphs while the rival, which
    # emits the same glyphs disguised as ASCII, paid nothing.
    body = "कार्यालयको लेखापरीक्षण प्रतिवेदन तयार भयो।\n" * 20
    marked = body + _mark("अनुदान") * 30
    # Identical shape, with a non-Devanagari, non-Latin filler in place of the
    # marks so every other term matches.
    unmarked = body + ("·" * len("अनुदान")) * 30

    assert _markdown_quality_score(marked) == _markdown_quality_score(unmarked)


def test_replacement_characters_still_lower_the_score() -> None:
    # U+FFFD is charged by the same term and must stay charged: any candidate
    # can emit it, so unlike a mark it does discriminate.
    body = "कार्यालयको लेखापरीक्षण प्रतिवेदन तयार भयो।\n" * 20
    damaged = body + "�" * 100
    filler = body + "·" * 100

    assert _markdown_quality_score(damaged) < _markdown_quality_score(filler)
