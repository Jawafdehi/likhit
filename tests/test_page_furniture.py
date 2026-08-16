"""Page furniture: what the renderer may delete, and what it may not.

A running header or footer merged into the same paragraph block as the page body
used to condemn the whole block. `_looks_like_page_furniture` is a substring test
("वार्षिकप्रतिवेदन" anywhere in the whitespace-stripped text), both renderers
asked it about the whole block, and a `TableBlock` neighbour armed the drop — so a
page whose header the layout pass folded into its body lost every character.

Measured on all 13 CIAA annual reports, on untouched `main`, at BLOCK grain: the
rule drops 877 paragraph blocks — 793 pure furniture, correctly dropped, and 84
carrying real body text, losing 86,812 characters. Every one has a healthy PDF text
layer.

🛑 Say which grain a figure is. An earlier pass counted PAGES LEFT EMPTY and found
9; a page that loses one block but keeps others is invisible to that count, which is
why the block-grain figure is 84. A third instrument counted only Devanagari
characters and got 63,640 against the 86,812 here. All three are right about
different things, and reconciling them is not possible without naming them.

A LENGTH BOUND cannot fix this, which is the part worth remembering because the
converter's own comment used to say it could: the smallest wrongly-dropped block is
82 characters and the largest correctly-dropped one is 137, so the ranges overlap.

The corpus-scale probe lives outside this repo, in the landing-plan tools; these
pins hold the rule itself.
"""

from __future__ import annotations

from likhit.converters.nepali_pdf import _render_markdown_from_blocks
from likhit.models import ParagraphBlock, Table, TableBlock, TableCell, TableRegion
from likhit.renderers.markdown import (
    _looks_like_page_furniture,
    strip_page_furniture_lines,
)

#: The running header of the CIAA annual reports, i.e. the text that made the
#: substring test fire on a whole page.
HEADER = "परिच्छेद-६, तामेली तथा मुल्तबी २४५ वार्षिक प्रतिवेदन, २०८१/८२"
BODY = "आयोगमा परेका उजुरीहरूको संख्या बढेको छ र अनुसन्धान जारी छ।"


def _table(page_number: int) -> TableBlock:
    return TableBlock(
        Table(
            row_count=2,
            col_count=2,
            cells=[
                TableCell(row=0, col=0, text="क"),
                TableCell(row=0, col=1, text="ख"),
                TableCell(row=1, col=0, text="१"),
                TableCell(row=1, col=1, text="२"),
            ],
            regions=[
                TableRegion(
                    page_number=page_number, x0=0, y0=0, x1=100, y1=50, page_height=800
                )
            ],
        )
    )


def test_the_header_alone_is_still_furniture() -> None:
    assert _looks_like_page_furniture(HEADER)


def test_a_page_of_body_carrying_the_header_is_furniture_to_the_predicate() -> None:
    """The defect in one line: the predicate cannot tell a header from a page
    that merely contains one, which is why the caller must not act on it alone."""
    assert _looks_like_page_furniture(f"{HEADER}\n{BODY}")


def test_stripping_keeps_the_body_and_drops_the_header() -> None:
    assert strip_page_furniture_lines(f"{HEADER}\n{BODY}") == BODY


def test_stripping_an_all_furniture_block_leaves_nothing() -> None:
    """The behaviour the rule was written for has to survive the fix."""
    assert strip_page_furniture_lines(f"{HEADER}\n२४५").strip() == ""


#: `HEADER` with the line break the layout puts in when the column is narrow. The
#: token the predicate matches -- "वार्षिकप्रतिवेदन" -- straddles the break, so it is
#: present in the whitespace-compacted BLOCK and in neither LINE.
WRAPPED_HEADER = "परिच्छेद-६, तामेली तथा मुल्तबी २४५ वार्षिक\nप्रतिवेदन, २०८१/८२"


def test_a_wrapped_header_matches_at_block_grain_and_at_no_line_grain() -> None:
    """The premise of the next three tests, asserted rather than assumed.

    If this ever stops holding -- because the predicate stops compacting whitespace,
    say -- the wrapped-header run scan is dead code and these tests pass vacuously.
    """

    assert _looks_like_page_furniture(WRAPPED_HEADER)
    assert not any(
        _looks_like_page_furniture(line) for line in WRAPPED_HEADER.splitlines()
    )


def test_a_wrapped_header_standing_alone_still_renders_as_nothing() -> None:
    """The guarantee the docstring makes, for the shape that breaks line grain.

    Found in review. Testing single lines only, this block went from discarded whole
    to rendered in full -- the exact opposite of the rule's purpose -- and the suite
    could not see it because its header fixture was a single line.
    """

    assert strip_page_furniture_lines(WRAPPED_HEADER).strip() == ""


def test_a_wrapped_header_does_not_take_the_body_with_it() -> None:
    """Why the repair is a shortest-run scan and not "drop the whole block".

    Dropping the block whenever no single line matched would delete this body text,
    which is the defect the fix exists to close -- so the cheap repair re-opens it.
    """

    assert strip_page_furniture_lines(f"{WRAPPED_HEADER}\n{BODY}") == BODY


def test_a_wrapped_header_is_found_across_blank_lines() -> None:
    """A blank line inside the block must not consume the run budget.

    Two blanks and three fragments, so counting blanks would put the second half of
    the token outside `_MAX_WRAPPED_HEADER_LINES` and leave the header rendered. A
    single blank line does NOT discriminate -- it still fits in the budget -- which
    is how the first version of this test passed against both arms.
    """

    assert strip_page_furniture_lines("वार्षिक\n\n\nप्रति\nवेदन").strip() == ""


def test_the_run_scan_is_confined_to_the_header_clause() -> None:
    """The scan is gated on the header token, so the other two clauses are untouched.

    A block flagged as a chapter heading or a bare page number has no wrap to find,
    and paying for the scan there would be the only way this change could alter what
    those clauses do. Written after a first draft of the test above asserted on a
    block that the WHOLE-BLOCK predicate does not even flag -- outside this helper's
    contract, and so a claim about behaviour no caller can reach.
    """

    chapter = "६ परिच्छेद\n" + BODY
    assert _looks_like_page_furniture(chapter)
    assert strip_page_furniture_lines(chapter) == BODY


def test_the_run_scan_stops_at_its_bound() -> None:
    """The limit, pinned as behaviour so it is a decision and not an accident.

    Four fragments exceed `_MAX_WRAPPED_HEADER_LINES`, so this header is left
    rendered. Body text can never be caught by widening the bound -- prose between
    the halves stops the token forming at all -- so the bound costs only detection,
    and the failure mode it chooses is a visible header rather than deleted text.
    """

    assert _looks_like_page_furniture("वार्षि\nक\nप्रति\nवेदन")
    assert strip_page_furniture_lines("वार्षि\nक\nप्रति\nवेदन").strip() != ""


def test_the_scan_never_joins_across_body_text() -> None:
    """Why widening the bound could not delete prose: the token stops forming."""

    scattered = "\n".join(["वार्षिक", BODY, BODY, "प्रतिवेदन"])
    assert not _looks_like_page_furniture(scattered)
    assert strip_page_furniture_lines(scattered).count(BODY) == 2


def test_a_table_adjacent_page_of_body_survives_rendering() -> None:
    """The regression test proper: page 2's text must reach the output even though
    its block carries the running header and sits next to a table."""
    blocks = [
        _table(1),
        ParagraphBlock(text=f"{HEADER}\n{BODY}", page_number=2),
        _table(3),
    ]
    rendered = _render_markdown_from_blocks(blocks, page_numbers=[1, 2, 3])
    assert BODY in rendered
    assert HEADER not in rendered


def test_a_table_adjacent_furniture_only_block_is_still_dropped() -> None:
    blocks = [
        _table(1),
        ParagraphBlock(text=HEADER, page_number=2),
        _table(3),
    ]
    rendered = _render_markdown_from_blocks(blocks, page_numbers=[1, 2, 3])
    assert HEADER not in rendered


def test_a_block_with_no_table_neighbour_is_untouched() -> None:
    """Adjacency still arms the rule; nothing else changed about when it applies."""
    blocks = [
        ParagraphBlock(text=f"{HEADER}\n{BODY}", page_number=1),
        ParagraphBlock(text=BODY, page_number=2),
    ]
    rendered = _render_markdown_from_blocks(blocks, page_numbers=[1, 2])
    assert HEADER in rendered


def test_stripping_is_not_applied_to_a_block_the_predicate_rejects() -> None:
    """`strip_page_furniture_lines` is more eager per line than per block — the
    bare-short-number clause would eat a page number sitting inside a longer
    block. Measured cost of applying it unconditionally on the CIAA corpus: 15
    characters (2069-70 -8, 2071-72 -7). So a block the whole-block predicate
    rejects must reach the output byte-for-byte."""
    text = f"{BODY}\n२४५\n{BODY}"
    assert not _looks_like_page_furniture(text)
    blocks = [_table(1), ParagraphBlock(text=text, page_number=2), _table(3)]
    rendered = _render_markdown_from_blocks(blocks, page_numbers=[1, 2, 3])
    assert "२४५" in rendered
