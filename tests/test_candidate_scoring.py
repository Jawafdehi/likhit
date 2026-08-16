"""Cross-candidate comparison semantics of `_markdown_quality_score`.

The score has exactly one production caller: the `max()` that picks which
extraction ships. So a term only earns its place if it discriminates *between*
candidates. These tests pin the two terms that did not, and guard the two ways
narrowing a term can go wrong -- letting the damage it exists for through, and
refunding the penalty to whoever pads the denominator.
"""

import io
from types import SimpleNamespace

from markitdown import DocumentConverterResult
import pytest

import likhit.converters.nepali_pdf as nepali_pdf_module
from likhit.converters.nepali_pdf import NepaliPdfConverter, _markdown_quality_score
from likhit.extractors.font_based import _CID_MARK_BASE

CELLS = ("काठमाडौँ", "महानगरपालिका", "लेखापरीक्षण", "प्रतिवेदन")
ROWS = 40


def _table(blank_cells: int) -> str:
    """The same cell text, rendered with `blank_cells` explicit empty cells."""
    row = "| " + " | ".join(CELLS) + " |" + " |" * blank_cells
    return "\n".join([row] * ROWS)


def _per_character_table(blank_cells: int) -> str:
    """The same characters, but every syllable split into its own cell.

    This is the `म ह ा ल े ख ा` damage `single_token_excess` exists to catch,
    rendered as a table so it can be padded with empty columns.
    """
    cells = [character for cell in CELLS for character in cell]
    row = "| " + " | ".join(cells) + " |" + " |" * blank_cells
    return "\n".join([row] * ROWS)


def _mark(text: str) -> str:
    """Mark every character the way get_cid_marked_page_dict does."""
    return "".join(chr(_CID_MARK_BASE + ord(char)) for char in text)


def _padding_crossover(limit: int = 2000) -> int | None:
    """Pad columns at which per-character garble first outscores intact words.

    Read from the shipping scorer rather than from a restatement of one of its
    terms: a helper that recomputes the term cannot notice the term changing,
    which is precisely the regression this file exists to catch.
    """
    intact = _markdown_quality_score(_table(blank_cells=0))
    return next(
        (
            pad
            for pad in range(limit + 1)
            if _markdown_quality_score(_per_character_table(pad)) > intact
        ),
        None,
    )


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
    # Direct form of the same defect: pipes here are 69.2% of all tokens (360 of
    # 520 -- the row is 9 pipes of 13 tokens), well past the 35% single-token
    # ceiling, yet they are structure rather than the per-character garble
    # ("म ह ा ल े") the ceiling exists to catch.
    piped = _table(blank_cells=4)
    tokens = piped.split()
    assert sum(1 for token in tokens if token == "|") / len(tokens) > 0.35

    # Same words and rows without any table syntax at all. If pipes still
    # counted as single-character tokens, the piped form would be charged for
    # them and fall behind.
    prose = "\n".join([" ".join(CELLS)] * ROWS)
    assert _markdown_quality_score(piped) >= _markdown_quality_score(prose)


def test_per_character_garble_is_still_penalised() -> None:
    """The narrowed penalty must still fire for what it exists to catch.

    Excusing pipes is only safe if the term still bites on real per-character
    splitting, and nothing else in the suite asserted that -- zeroing the penalty
    outright left every test green.

    The two candidates are built so `single_token_excess` is the *only* term that
    can differ: identical character content (899), identical Devanagari (600),
    identical token count (300), zero whitespace excess, zero matra damage and no
    pipes on either side. Comparing per-character garble against ordinary prose
    would not do -- it passes on `whitespace_excess` alone, so it stays green with
    this penalty removed.
    """
    even_tokens = " ".join(["कक"] * 300)
    half_single = " ".join(["क", "ककक"] * 150)

    even_score = _markdown_quality_score(even_tokens)
    split_score = _markdown_quality_score(half_single)

    # 50% single-character tokens against a 35% ceiling: 150 - 105 = 45 excess,
    # at _EXCESS_SINGLE_TOKEN_PENALTY = 6.
    assert split_score == even_score - 45 * 6


def test_only_the_pipe_is_excused_not_punctuation_generally() -> None:
    """The exclusion is one exact token, not a class.

    Widening it to other single-character separators would excuse real garble:
    `-` and `:` are not table structure in likhit's output (its separator row is
    `---`, three characters, so it was never a single-character token). Without
    this, an exclusion that over-reached to `{"|", "-", ":"}` passed every test.
    """
    stem = ["ककक"] * 150
    piped = " ".join(token for pair in zip(["|"] * 150, stem) for token in pair)
    dashed = " ".join(token for pair in zip(["-"] * 150, stem) for token in pair)
    coloned = " ".join(token for pair in zip([":"] * 150, stem) for token in pair)

    # Same length, same Devanagari, same token count in all three.
    assert _markdown_quality_score(dashed) < _markdown_quality_score(piped)
    assert _markdown_quality_score(coloned) < _markdown_quality_score(piped)
    # And they are charged identically -- nothing about `-` or `:` is special,
    # they are simply not excused.
    assert _markdown_quality_score(dashed) == _markdown_quality_score(coloned)


def test_padding_empty_columns_cannot_refund_the_garble_penalty() -> None:
    """Pipes must leave the ratio's denominator, not just its numerator.

    Excluding `|` from the count alone leaves it in the population that sets the
    allowance, so each added pipe raised the tolerated number of lone characters
    and refunded 2.1 points of penalty. A candidate that split every syllable into
    its own cell could then outscore one carrying the same characters as whole
    words purely by padding empty columns -- 3801 against 3750 at 60 pad columns,
    on byte-identical Devanagari.

    Padding can still win eventually, and that is deliberately not asserted away:
    `+len(tokens)` credits +1 per pipe with no bound, which is F05's half of the
    problem and what `test_pipe_heavy_output_is_still_penalised` xfails on. What
    the fix buys is that the *penalty* stops shrinking, which moves the crossover
    from 70 pad columns to 292 -- so the crossover itself is the measurement, and
    it collapses if the refund returns.
    """
    intact = _markdown_quality_score(_table(blank_cells=0))

    # 120 columns is past where the refund let garble win (70) and well short of
    # where the unbounded token credit does (292).
    assert _markdown_quality_score(_per_character_table(120)) < intact

    crossover = _padding_crossover()
    assert crossover is not None, "the token credit should still win eventually"
    assert crossover > 240, (
        f"garble outscored intact words at only {crossover} pad columns; the "
        f"single-token penalty is being refunded by padding again"
    )


@pytest.mark.xfail(
    reason=(
        "Nothing charges a candidate per column. pipe_heavy_lines is flat per "
        "line while +len(tokens) credits +1 per pipe, so widening a table is "
        "pure gain (measured here: 6,516 against 5,000). Excusing pipes from "
        "the single-token ceiling removed the only term that happened to scale "
        "with column count, taking the net from -2.9 to exactly +1.0 per pipe "
        "-- an accidental brake that charged likhit's real blank cells and "
        "markitdown's invented separator rows alike. Now that pipes are out of "
        "both sides of that ratio the remaining +1 is the token credit alone, "
        "which is research fix-plan F05; this passes once F05 stops counting "
        "table syntax as content."
    ),
    strict=True,
)
def test_pipe_heavy_output_is_still_penalised() -> None:
    # The table signal ought to survive: excusing pipes from the single-token
    # ceiling should not leave runaway column counts uncharged.
    reasonable = _table(blank_cells=0)
    runaway = _table(blank_cells=40)

    assert _markdown_quality_score(runaway) < _markdown_quality_score(reasonable)


def test_marked_cids_are_not_charged_as_damage() -> None:
    # Only a likhit candidate can carry a mark; every rival comes from pdfminer
    # or the OCR converter and never marks. Charging the mark therefore taxed
    # the candidate that labelled its unmappable glyphs while the rival, which
    # emits the same glyphs disguised as ASCII, paid a smaller penalty.
    body = "कार्यालयको लेखापरीक्षण प्रतिवेदन तयार भयो।\n" * 20
    # Separate tokens: `_mark(word) * 30` glues into ONE 180-character token,
    # which measures something else entirely.
    marked = body + " " + " ".join([_mark("अनुदान")] * 30)
    # Identical shape, with a non-Devanagari, non-Latin filler in place of the
    # marks so every other term matches.
    unmarked = body + " " + " ".join(["·" * len("अनुदान")] * 30)

    assert _markdown_quality_score(marked) == _markdown_quality_score(unmarked)


def test_marking_never_beats_decoding_the_same_glyphs() -> None:
    """The safety property that makes an uncharged mark acceptable.

    Not charging a mark is only sound because a marked glyph still forfeits the
    +3 Devanagari credit it would have earned decoded. Against *absence* a
    marked token is worth +1 like any token, so this is not neutrality -- it is
    that the candidate which decodes always outranks the candidate which labels.
    """
    words = ["अनुदान", "बेरुजु", "रकम"] * 10

    decoded = " ".join(words)
    marked = " ".join(_mark(word) for word in words)

    assert _markdown_quality_score(decoded) > _markdown_quality_score(marked)


def test_replacement_characters_still_lower_the_score() -> None:
    # U+FFFD is charged by the same term and must stay charged: any candidate
    # can emit it, so unlike a mark it does discriminate.
    body = "कार्यालयको लेखापरीक्षण प्रतिवेदन तयार भयो।\n" * 20
    damaged = body + "�" * 100
    filler = body + "·" * 100

    assert _markdown_quality_score(damaged) < _markdown_quality_score(filler)


def test_nul_sentinels_still_lower_the_score() -> None:
    # pdfminer's sentinel for a glyph it cannot decode is U+0000, where likhit
    # emits U+FFFD or a mark. It is exactly as discriminating as U+FFFD -- any
    # candidate can emit it -- so it has to be charged the same.
    body = "कार्यालयको लेखापरीक्षण प्रतिवेदन तयार भयो।\n" * 20
    damaged = body + "\x00" * 100
    filler = body + "·" * 100

    assert _markdown_quality_score(damaged) < _markdown_quality_score(filler)


def test_a_nul_cannot_outrank_the_same_damage_declared_as_u_fffd() -> None:
    """The inversion this pairing exists to prevent.

    Both candidates lost the same glyphs; they differ only in which sentinel
    marks the loss. Charging U+FFFD but not U+0000 made the *quieter* sentinel
    win, which is the same "hidden damage outranks declared damage" ordering the
    marked-CID comment above says is backwards. A NUL is strictly worse to ship
    than a U+FFFD: GNU grep, `sort` and `comm` classify a NUL-bearing file as
    binary, drop every match and still exit 0.
    """
    body = "कार्यालयको लेखापरीक्षण प्रतिवेदन तयार भयो।\n" * 20
    declared = body + "�" * 100
    hidden = body + "\x00" * 100

    assert _markdown_quality_score(hidden) <= _markdown_quality_score(declared)


def test_the_production_comparison_picks_the_marked_candidate_over_the_disguise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the one caller, not the scorer in isolation.

    Every other test here calls `_markdown_quality_score` directly, so none of
    them would notice if the safety tuple, `requires_geometry_aware_candidate` or
    the tie-break shadowed this change. The `max()` key is `(safe, score)`, and
    what ships is what matters.
    """
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_collect_numeric_boundary_repairs",
        lambda _raw: [],
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _stream: {"Kalimati": "broken_cmap"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "pdf_likely_needs_ocr",
        lambda _raw: False,
    )
    # The rival carries the same damaged word disguised as ASCII; likhit labels
    # it. Same words otherwise, so only the two terms under test differ.
    disguised = "कार्यालयको लेखापरीक्षण ूारिWभक प्रतिवेदन\n" * 20
    labelled = ("कार्यालयको लेखापरीक्षण " + _mark("ूारिभक") + " प्रतिवेदन\n") * 20
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda _raw, _info: DocumentConverterResult(markdown=disguised),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda _raw: (DocumentConverterResult(markdown=labelled), [1], None),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_ocr_pdf_converter",
        lambda _raw, _info, **_kwargs: None,
    )

    result = NepaliPdfConverter().convert(
        io.BytesIO(b"%PDF-1.4 placeholder"),
        SimpleNamespace(extension=".pdf", mimetype="application/pdf"),
    )

    assert result.markdown == labelled
