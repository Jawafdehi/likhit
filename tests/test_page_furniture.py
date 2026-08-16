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
