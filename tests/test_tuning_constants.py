"""Every module-level numeric constant in ``src/``, pinned to its measured value.

MEASURED, NOT ASSUMED. Each of the 23 constants below was perturbed in place and the
whole suite run against the mutant. **14 of the 23 survived** -- nothing in the suite
asserted anything that depended on the value, so a reviewer could change any of them
and see green. Among the survivors were the CID marking base, the threshold that routes
a page to paid OCR, and the table-cell edge tolerance.

That figure is the measurement against the suite WITHOUT this file. Re-run against this
file, all 23 are caught and 0 survive. Both numbers are needed: the first says why the
file exists, the second says it works. The sweep is `tools/constant_sweep.sh` in the run
record; the result is `inventory/constant-sweep.tsv`.

🛑 THE FIRST VERSION OF THIS FILE SHIPPED VACUOUS, and how is worth more than the fix.
`test_constant_holds_its_pinned_value` was committed with `expected` rebound to the live
module attribute -- a mutation marker, left in by the very sweep that was demonstrating
the vacuity. Three things had to line up, and all three are avoidable:

  1. The mutation was reverted with `git checkout -- tests/<this file>` while the file
     was still UNTRACKED. That command fails on an untracked path. It printed an error;
     the error was read as benign, because "the file is untracked" sounded like an
     explanation rather than the reason the restore did not happen.
  2. The restore was then confirmed by re-running the suite and seeing green. Green
     after a restore proves nothing when the mutation is one that makes tests PASS --
     which is exactly the class of mutation a vacuity demonstration uses.
  3. `git add -A tests` committed the mutant.

So: restore by byte comparison against a pristine copy, never by re-running the suite,
and never with `git checkout` on a path that may be untracked.
`test_no_pin_is_derived_from_the_thing_it_pins` now closes the specific hole -- the pin
could not detect its own vacuity, so the property is asserted over its source instead.

WHY A BARE PIN IS THE RIGHT SHAPE HERE, given that a pin asserts nothing about
behaviour: the alternative is a behavioural test per constant, and for a geometry
threshold that means inventing a fixture whose only purpose is to sit either side of a
number -- which pins the fixture, not the threshold. A pin plus a stated derivation
makes the value a decision with an owner. Three constants whose consequence is severe
enough to earn a behavioural test as well get one at the bottom of this file.

TWO THINGS THIS FILE IS CAREFUL ABOUT.

* **The registry is checked against an AST scan of the source**, so a constant added
  later must be registered. Pinning today's 23 would close 23 instances and leave the
  class open.
* **The scan sees NAMED module-level constants only**, which left a real hole: six
  weights in `_markdown_quality_score` -- the function that decides which candidate
  transcript ships -- were inline literals in the same expression as two that were
  named here. Worse, the two named ones stated their derivations against one of the
  unpinned literals ("half the U+FFFD/NUL rate of 12"), so pinned values were anchored
  to a free one. Measured: changing that rate from 12 to 1 left the whole suite green
  at 875 passed. All six are now named and registered, and
  `test_the_candidate_score_carries_no_unnamed_weight` closes the class for that
  function. Repo-wide it stays open by choice: src/ holds 432 distinct non-trivial
  numeric literals over 640 occurrences, nearly all structural.

  🛑 That pair is instrument-dependent, so read it with its definition attached:
  ``ast.walk`` over every ``src/**/*.py``, counting ``ast.Constant`` of int or float
  (``bool`` excluded, since it is an int subclass), with 0, 1 and 2 treated as
  trivial. Change any of those choices and the number moves -- excluding only 0 and 1
  gives 433 over 743. This docstring said 431 over 635 until review re-derived it and
  got 432 over 640; neither figure reproduced under any reading of the old wording,
  because the wording named no instrument. The lesson is the missing definition, not
  the off-by-one.
* **Every expected value is a literal.** A test that reads the constant to build its
  own expectation holds at any value -- which is exactly how these came to be
  unpinned. `tests/test_candidate_scoring.py` is the live example: its `_mark()` helper
  builds fixtures with `chr(_CID_MARK_BASE + ord(char))`, so moving the base moves the
  fixture with it. That file even states the principle in `_padding_crossover`'s
  docstring -- "a helper that recomputes the term cannot notice the term changing" --
  and applies it to the scorer while the helper nine lines above breaks it.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pymupdf as fitz
import pytest

from likhit.converters.nepali_pdf import _markdown_quality_score
from likhit.extractors import font_based as font_based_module
from likhit.extractors.font_classifier import (
    SCANNED_DECOY_TEXT,
    _DECOY_MAX_DEVANAGARI,
    classify_ocr_page,
)
from likhit.handlers import structure_detection as structure_detection_module
from likhit.handlers import two_column_layout as two_column_layout_module
from tests.synthetic_pdfs import build_scanned_decoy_pdf

_SRC = Path(__file__).resolve().parent.parent / "src"

# (module path relative to src/, name) -> (value, where the value comes from).
#
# The rationale column is the point of the table. A number with no derivation is a
# number nobody can safely change; a number with one can be argued about.
_PINNED: dict[tuple[str, str], tuple[float, str]] = {
    # -- markdown quality score ------------------------------------------------ #
    (
        "likhit/converters/nepali_pdf.py",
        "_MAX_REASONABLE_WHITESPACE_RATIO",
    ): (0.35, "above this share of whitespace a candidate is padding, not layout"),
    (
        "likhit/converters/nepali_pdf.py",
        "_MAX_REASONABLE_SINGLE_TOKEN_RATIO",
    ): (0.35, "above this share of one-token lines the candidate is shredded"),
    (
        "likhit/converters/nepali_pdf.py",
        "_EXCESS_SINGLE_TOKEN_PENALTY",
    ): (
        6,
        "per excess single-token line; half _UNDECODED_GLYPH_PENALTY. Stated "
        "against the CONSTANT, not against the literal 12 -- when this derivation "
        "said 'the U+FFFD/NUL rate of 12' that rate was an inline literal nothing "
        "pinned, so this pin was anchored to a free number",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_MATRA_DAMAGE_PENALTY",
    ): (
        8,
        "per matra-damage unit. Between _EXCESS_SINGLE_TOKEN_PENALTY and "
        "_UNDECODED_GLYPH_PENALTY: a damaged matra is worse than a bad line break "
        "and better than a glyph that did not decode at all. The ordering is "
        "asserted below, so the derivation is checked rather than merely stated",
    ),
    # The rest of _markdown_quality_score's weights. These were inline literals in
    # the same arithmetic expression as the two above, so this guard covered half of
    # one function's tuning surface. Measured before naming them: changing the
    # U+FFFD/NUL rate from 12 to 1 left the whole suite green at 875 passed -- the
    # full baseline -- while the two derivations above cited that very number.
    (
        "likhit/converters/nepali_pdf.py",
        "_DEVANAGARI_CHAR_CREDIT",
    ): (
        3,
        "credit per Devanagari character; with the token count it is one of only two "
        "positive terms, because Devanagari volume is the primary evidence that a "
        "Nepali candidate decoded at all. Absolute value unrecorded when introduced",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_SUSPICIOUS_TOKEN_PENALTY",
    ): (
        8,
        "per token carrying legacy-garble punctuation or letter/digit mixing; equal "
        "to _MATRA_DAMAGE_PENALTY. Absolute value unrecorded when introduced",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_VOWEL_POOR_TOKEN_PENALTY",
    ): (
        3,
        "per vowel-poor Latin token; equal to _DEVANAGARI_CHAR_CREDIT, so one such "
        "token cancels one Devanagari character. Absolute value unrecorded",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_PIPE_HEAVY_LINE_PENALTY",
    ): (
        4,
        "per pipe-heavy line; between _VOWEL_POOR_TOKEN_PENALTY and "
        "_EXCESS_SINGLE_TOKEN_PENALTY. Absolute value unrecorded",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_CID_GARBAGE_PENALTY",
    ): (
        12,
        "per '(cid:N)' run; equal to _UNDECODED_GLYPH_PENALTY, because a cid literal "
        "IS a glyph that did not decode -- it is the same damage spelled differently",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_UNDECODED_GLYPH_PENALTY",
    ): (
        12,
        "per U+FFFD or NUL. The heaviest weight in the score and the anchor the "
        "others are stated against: a glyph that did not decode is the worst thing a "
        "candidate can ship. See the NUL comment at the call site for why a NUL must "
        "not be cheaper than a U+FFFD",
    ),
    # -- CID marking ---------------------------------------------------------- #
    (
        "likhit/extractors/font_based.py",
        "_CID_MARK_BASE",
    ): (
        0xF0000,
        "start of Supplementary Private Use Area A (plane 15), so marked CIDs stay "
        "distinct AND inside the private-use range _private_use_count counts",
    ),
    (
        "likhit/extractors/font_based.py",
        "_MAX_MARKABLE_CID",
    ): (
        0xFFFD,
        "largest CID that fits: _CID_MARK_BASE + this is 0xFFFFD, the top of the "
        "range _MARKED_CID_PATTERN matches. See the invariant test below",
    ),
    # -- content-based legacy detection --------------------------------------- #
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MIN_HITS",
    ): (2, "one dictionary hit is a coincidence"),
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MAX_PENALTY_PER_DEVA",
    ): (0.05, "garble budget per Devanagari character of the decoded candidate"),
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MIN_DEVA_RATIO",
    ): (0.6, "a real legacy decode is mostly Devanagari"),
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MIN_DEVA",
    ): (8, "absolute floor, so a two-word span cannot clear the ratio on volume"),
    # -- scanned / decoy page classification ---------------------------------- #
    (
        "likhit/extractors/font_classifier.py",
        "_SCANNED_IMAGE_COVERAGE",
    ): (0.85, "share of the page an image must cover before OCR is considered"),
    (
        "likhit/extractors/font_classifier.py",
        "_DECOY_MAX_DEVANAGARI",
    ): (
        10,
        "at or above this many Devanagari characters the text layer is real, not a "
        "decoy. This is the gate on PAID OCR -- see the behavioural test below",
    ),
    # -- lohit cmap recovery -------------------------------------------------- #
    (
        "likhit/extractors/lohit.py",
        "_MIN_ANCHOR_MATCHES",
    ): (1, "one anchor glyph is enough to accept a recovered cmap"),
    # -- numeric boundary repair ---------------------------------------------- #
    (
        "likhit/extractors/numeric_boundaries.py",
        "_ADVANCE_OUTLIER_EM",
    ): (0.10, "advance-width excess, in em, that marks an erased separator"),
    (
        "likhit/extractors/numeric_boundaries.py",
        "_BBOX_GAP_OUTLIER_EM",
    ): (
        0.20,
        "bbox gap between adjacent glyphs, in em. Twice the advance threshold "
        "because a bbox is a rendered extent and an advance is a font metric, so "
        "the bbox measure carries the glyph's own side bearings as noise",
    ),
    (
        "likhit/extractors/numeric_boundaries.py",
        "_MIN_RULE_HEIGHT",
    ): (4.0, "points; below this a vector is a glyph stroke, not a cell rule"),
    (
        "likhit/extractors/numeric_boundaries.py",
        "_MAX_PARTITION_SEGMENTS",
    ): (12, "combinatorial bound on rule partitions per numeric run"),
    # -- table extraction ----------------------------------------------------- #
    (
        "likhit/extractors/tables.py",
        "_EDGE_TOLERANCE",
    ): (
        1.5,
        "points of slack on the fragment-centre-in-cell test and on edge "
        "clustering. Widening it pulls a neighbouring fragment into a cell, which "
        "reclassifies the row downstream -- see test_extractor_renderer_seam.py",
    ),
    # -- layout handlers ------------------------------------------------------ #
    (
        "likhit/handlers/structure_detection.py",
        "_HEADER_Y_MAX",
    ): (80.0, "points from the top within which a fragment is a running head"),
    (
        "likhit/handlers/structure_detection.py",
        "_COLUMN_GUTTER",
    ): (20.0, "minimum horizontal gap, in points, that separates two columns"),
    (
        "likhit/handlers/two_column_layout.py",
        "_HEADER_Y_MAX",
    ): (80.0, "must equal structure_detection's -- see the agreement test below"),
    (
        "likhit/handlers/two_column_layout.py",
        "_COLUMN_GUTTER",
    ): (20.0, "must equal structure_detection's -- see the agreement test below"),
    (
        "likhit/handlers/two_column_layout.py",
        "_LAYOUT_BLOCK_GAP_MIN",
    ): (18.0, "vertical points between fragments that start a new block"),
    # -- legacy map word cache ------------------------------------------------ #
    (
        "likhit/extractors/legacy_maps.py",
        "_WORD_CACHE_SIZE",
    ): (
        65536,
        "words memoized per map. Sized to be unreachable rather than tuned: every "
        "span of the 128-page law-report sample holds 7,899 distinct words, and five "
        "warm caches -- one per map, which is what choose_legacy_map fills when it "
        "scores a span against every candidate -- measured 39,495 entries and ~1.8 "
        "MiB. A bound only needs to stop an unbounded corpus run, so anything well "
        "above the per-document count does the same work",
    ),
}


def _iter_module_level_constants() -> list[tuple[str, str, float]]:
    """Every ``_NAME = <number>`` assigned at module level in ``src/``.

    Module level only: a constant inside a function or class is local to its caller
    and is not the review hazard this file is about.
    """

    found: list[tuple[str, str, float]] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not target.id.startswith("_"):
                continue
            if not target.id.isupper() and not target.id.lstrip("_").isupper():
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(
                value.value, (int, float)
            ):
                if isinstance(value.value, bool):
                    continue
                found.append((rel, target.id, value.value))
    return found


_FOUND = _iter_module_level_constants()


def test_every_module_level_numeric_constant_is_pinned():
    """A constant added later must be registered, or this class reopens.

    Pinning today's set would close 23 instances and leave the class open. The failure
    message is the review prompt.
    """

    found = {(rel, name) for rel, name, _value in _FOUND}
    assert found == set(_PINNED), (
        "a module-level numeric constant in src/ was added, removed or renamed.\n"
        f"  unpinned: {sorted(found - set(_PINNED))}\n"
        f"  stale pins: {sorted(set(_PINNED) - found)}\n"
        "Add it to _PINNED with its value AND where the value comes from. A number "
        "with no derivation is a number nobody can safely change."
    )


@pytest.mark.parametrize(
    ("rel", "name", "value"), _FOUND, ids=lambda v: str(v).replace(".py", "")
)
def test_constant_holds_its_pinned_value(rel, name, value):
    """The pin. ``expected`` is the LITERAL from ``_PINNED`` and must stay that way.

    Both readings of the constant are asserted against it, and they are independently
    informative: ``value`` is the source literal the AST scan found, ``live`` is the
    module attribute at runtime. They diverge if a constant is conditionally reassigned
    after its definition, which the AST scan cannot see.

    Neither may become the expectation. Deriving ``expected`` from either one makes this
    ``source == source`` and the whole table stops being read -- which is not a
    hypothetical: it shipped that way, as a mutation marker left in by the sweep that
    was meant to demonstrate the vacuity. See the module docstring.
    """

    expected = _PINNED[(rel, name)][0]
    live = getattr(
        importlib.import_module(rel.removesuffix(".py").replace("/", ".")), name
    )

    assert value == expected
    assert live == expected
    assert type(value) is type(expected), (
        f"{name} changed type: pinned {expected!r}, source has {value!r}"
    )


def test_every_pin_carries_a_derivation():
    """Guards the table against becoming a bare list of numbers.

    ⚠️ This measures LENGTH, not completeness. It can only catch an empty or
    near-empty cell -- a truncated clause passes, and one did: this file shipped
    ``_BBOX_GAP_OUTLIER_EM``'s derivation ending mid-sentence at "because bboxes are",
    62 characters and green. Read the column; do not rely on this test to.
    """

    missing = [key for key, (_v, why) in _PINNED.items() if len(why.strip()) < 20]
    assert missing == [], missing


def test_no_pin_is_derived_from_the_thing_it_pins():
    """The pin's expectation must be a literal in ``_PINNED``, checked at the source.

    ``test_constant_holds_its_pinned_value`` cannot detect its own vacuity: if
    ``expected`` is rebound to the live value, every assertion in it still passes. So
    the property is asserted here instead, over the source of that function -- the same
    "scan the source, not the runtime" idiom the flag-word guard uses, for the same
    reason.
    """

    body = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    target = next(
        node
        for node in ast.walk(body)
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_constant_holds_its_pinned_value"
    )
    assignments = {
        node.targets[0].id: ast.unparse(node.value)
        for node in ast.walk(target)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    assert assignments.get("expected") == "_PINNED[rel, name][0]", (
        "the pin's expectation must come from the _PINNED literal, not from the "
        f"source scan or the live module. Found: {assignments.get('expected')!r}"
    )


# --------------------------------------------------------------------------- #
# The three whose consequence earns a behavioural test as well.
# --------------------------------------------------------------------------- #


def test_the_cid_mark_range_fits_exactly_inside_plane_15():
    """``_CID_MARK_BASE`` and ``_MAX_MARKABLE_CID`` are not independent choices.

    ``_MARKED_CID_PATTERN`` states the same range a third time, as a literal. The three
    agree today, and if the base moves without the other two the top of the range lands
    in plane 16: 0xF1000 + 0xFFFD is 0x100FFD, which ``_MARKED_CID_PATTERN`` does not
    match, ``strip_marked_cids`` cannot strip and ``count_marked_cids`` cannot count.
    A high CID would then be marked into a code point nothing can recover -- silently,
    because low CIDs keep working.

    Derived from the constants rather than restating them, so this is a consistency
    check and not a second copy of the pin above.
    """

    base = font_based_module._CID_MARK_BASE
    top = base + font_based_module._MAX_MARKABLE_CID
    pattern = font_based_module._MARKED_CID_PATTERN

    # Plane 15 (Supplementary Private Use Area A) is U+F0000..U+FFFFF.
    assert base == 0xF0000
    assert top <= 0xFFFFF, f"top of the mark range 0x{top:X} leaves plane 15"

    # The pattern must cover the whole range and nothing either side of it.
    assert pattern.fullmatch(chr(base))
    assert pattern.fullmatch(chr(top))
    assert not pattern.fullmatch(chr(base - 1))
    assert not pattern.fullmatch(chr(top + 1))

    # And the worst case round-trips through all three helpers.
    worst = chr(font_based_module._MAX_MARKABLE_CID)
    marked = font_based_module.mark_unmappable_cids(worst)
    assert ord(marked) == top
    assert font_based_module.count_marked_cids(marked) == 1
    assert font_based_module.strip_marked_cids(marked) == "�"
    assert font_based_module._private_use_count(marked) == 1


class _PageWithExtraDevanagari:
    """A real decoy page whose extracted text carries ``count`` extra Devanagari.

    The text layer has to be faked at ``get_text`` rather than drawn into the PDF,
    and that is a constraint rather than a shortcut: PyMuPDF ships no
    Devanagari-capable font (`helv` and `china-s` both report no glyph for ``क``,
    and there is no builtin `notos`), so drawing ``क`` produces a substituted glyph
    that extracts as nothing -- the count stays 0 at every value. Reaching for a host
    font instead would make this test pass or fail by which machine ran it.

    Everything else is the genuine article: real full-page raster, real non-embedded
    core font, real xref. Only the one input the threshold reads is substituted, so
    the branch under test is reached the way production reaches it.
    """

    def __init__(self, page: fitz.Page, count: int) -> None:
        self._page = page
        self._extra = "क" * count

    def get_text(self, *args: object, **kwargs: object) -> str:
        return self._page.get_text(*args, **kwargs) + "\n" + self._extra

    def __getattr__(self, name: str) -> object:
        return getattr(self._page, name)


class _DocWithExtraDevanagari:
    def __init__(self, doc: fitz.Document, count: int) -> None:
        self._doc = doc
        self._count = count

    def __getitem__(self, index: int) -> _PageWithExtraDevanagari:
        return _PageWithExtraDevanagari(self._doc[index], self._count)

    def __getattr__(self, name: str) -> object:
        return getattr(self._doc, name)


def _decoy_doc_with_devanagari(count: int) -> tuple[fitz.Document, object]:
    doc = fitz.open(stream=build_scanned_decoy_pdf(page_count=1), filetype="pdf")
    return doc, _DocWithExtraDevanagari(doc, count)


def test_the_decoy_devanagari_threshold_is_the_gate_on_paid_ocr():
    """``_DECOY_MAX_DEVANAGARI`` decides whether a text layer is real.

    ``classify_ocr_page`` returns ``None`` -- "this page has real text, do not OCR it"
    -- as soon as the Devanagari count reaches this value. So the constant is not a
    tuning knob on output quality; it is the boundary between a page transcribed for
    free and one sent to a metered vision model.

    Driving the function rather than restating the comparison. The previous version of
    this test asserted ``10 >= _DECOY_MAX_DEVANAGARI`` and ``not 9 >=
    _DECOY_MAX_DEVANAGARI``, which are the constant substituted into itself and hold at
    any value -- so flipping the ``>=`` in ``classify_ocr_page`` to ``>``, the exact
    off-by-one that is spend, left it green.
    """

    assert _DECOY_MAX_DEVANAGARI == 10

    below_doc, below = _decoy_doc_with_devanagari(_DECOY_MAX_DEVANAGARI - 1)
    at_doc, at = _decoy_doc_with_devanagari(_DECOY_MAX_DEVANAGARI)
    try:
        # One short of the threshold: still a decoy, so the page is sent to OCR.
        assert classify_ocr_page(below, 0) == SCANNED_DECOY_TEXT
        # At the threshold the text layer is accepted and no OCR is bought.
        assert classify_ocr_page(at, 0) is None
    finally:
        below_doc.close()
        at_doc.close()


# The same four characters in two orders. `क्रा` puts the virama before a CONSONANT
# (valid); `क्ार` puts it before a MATRA, which is one `_VIRAMA_MATRA_PATTERN` unit.
#
# Reordering rather than appending is what makes this a measurement of the term. The
# scorer rewards Devanagari characters and token count, so a damaged string built by
# adding text scores HIGHER than the clean one -- the first version of this test read
# a delta of -2 and would have been "fixed" by weakening the assertion. An identical
# character multiset holds every other term constant by construction.
_VALID_MATRA_TEXT = "क्रा"
_DAMAGED_MATRA_TEXT = "क्ार"


def test_the_matra_fixtures_differ_only_in_matra_validity():
    # Asserted, not asserted-by-comment: if a future edit breaks the multiset the rate
    # test below silently starts measuring something else.
    assert sorted(_VALID_MATRA_TEXT) == sorted(_DAMAGED_MATRA_TEXT)
    assert len(_VALID_MATRA_TEXT) == len(_DAMAGED_MATRA_TEXT)


@pytest.mark.parametrize("units", [1, 2, 3])
def test_matra_damage_is_charged_at_its_pinned_rate(units):
    """The rate, not just the number.

    A pin says the constant is 8. This says the scorer subtracts 8 **per unit**, which
    is what makes the pin mean something: the term could be dropped from the expression
    entirely, or changed to a flat charge, and a bare pin would still pass.
    """

    valid = " ".join([_VALID_MATRA_TEXT] * units)
    damaged = " ".join([_DAMAGED_MATRA_TEXT] * units)

    delta = _markdown_quality_score(valid) - _markdown_quality_score(damaged)
    assert delta == units * 8


def test_the_two_layout_modules_agree_on_the_geometry_they_share():
    """``_HEADER_Y_MAX`` and ``_COLUMN_GUTTER`` are each defined twice.

    ``structure_detection`` decides a document IS a two-column article;
    ``two_column_layout`` then splits it. If the two copies drift, a document is
    classified with one threshold and split with another, and the failure is a
    mis-split page rather than an error.

    Not merged into a shared constant here -- that is a refactor with its own review.
    Making the coupling assert itself is the cheap half.
    """

    assert (
        structure_detection_module._HEADER_Y_MAX
        == two_column_layout_module._HEADER_Y_MAX
    )
    assert (
        structure_detection_module._COLUMN_GUTTER
        == two_column_layout_module._COLUMN_GUTTER
    )


def test_the_scoring_weights_form_the_ordering_their_derivations_claim():
    """The derivations above are prose; this makes them checked.

    Three of them state a RELATIONSHIP rather than an absolute -- "half
    _UNDECODED_GLYPH_PENALTY", "between", "equal to". A relationship stated only in a
    comment goes stale the moment one side moves, and the pin on each individual value
    cannot notice: every pin would still hold at its own number while the sentence
    joining them became false.
    """

    from likhit.converters import nepali_pdf as m

    # "half _UNDECODED_GLYPH_PENALTY"
    assert m._EXCESS_SINGLE_TOKEN_PENALTY * 2 == m._UNDECODED_GLYPH_PENALTY
    # "between _EXCESS_SINGLE_TOKEN_PENALTY and _UNDECODED_GLYPH_PENALTY"
    assert (
        m._EXCESS_SINGLE_TOKEN_PENALTY
        < m._MATRA_DAMAGE_PENALTY
        < m._UNDECODED_GLYPH_PENALTY
    )
    # "equal to _MATRA_DAMAGE_PENALTY"
    assert m._SUSPICIOUS_TOKEN_PENALTY == m._MATRA_DAMAGE_PENALTY
    # "equal to _DEVANAGARI_CHAR_CREDIT, so one such token cancels one character"
    assert m._VOWEL_POOR_TOKEN_PENALTY == m._DEVANAGARI_CHAR_CREDIT
    # "between _VOWEL_POOR_TOKEN_PENALTY and _EXCESS_SINGLE_TOKEN_PENALTY"
    assert (
        m._VOWEL_POOR_TOKEN_PENALTY
        < m._PIPE_HEAVY_LINE_PENALTY
        < m._EXCESS_SINGLE_TOKEN_PENALTY
    )
    # "a cid literal IS a glyph that did not decode"
    assert m._CID_GARBAGE_PENALTY == m._UNDECODED_GLYPH_PENALTY
    # and the anchor really is the heaviest weight in the score
    assert m._UNDECODED_GLYPH_PENALTY == max(
        m._DEVANAGARI_CHAR_CREDIT,
        m._SUSPICIOUS_TOKEN_PENALTY,
        m._VOWEL_POOR_TOKEN_PENALTY,
        m._PIPE_HEAVY_LINE_PENALTY,
        m._EXCESS_SINGLE_TOKEN_PENALTY,
        m._MATRA_DAMAGE_PENALTY,
        m._CID_GARBAGE_PENALTY,
        m._UNDECODED_GLYPH_PENALTY,
    )


def test_the_candidate_score_carries_no_unnamed_weight():
    """Closes the CLASS, not the six instances that were found.

    This file's own limit, recorded in its docstring, is that it sees *named*
    module-level constants only -- so a weight written inline is invisible to it. Six
    were, in the one function that decides which transcript ships, and one of them was
    the anchor two registered derivations cited. Naming them fixes those six; this
    test is what stops a seventh being added the same way.

    Scoped to ``_markdown_quality_score`` deliberately. src/ holds 432 distinct
    non-trivial numeric literals over 640 occurrences (see the module docstring for the
    instrument -- the pair is meaningless without it), nearly all structural -- array
    indices, small counts, geometry arithmetic -- so a repo-wide version of this test
    would be noise that nobody could keep green. This function is the one whose
    literals are all weights.
    """

    import ast
    import inspect
    import textwrap

    from likhit.converters import nepali_pdf as m

    source = textwrap.dedent(inspect.getsource(m._markdown_quality_score))
    literals = sorted(
        {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        }
    )

    # 0 and 1 are structural here: an empty-input guard and a divide-by-zero floor.
    assert literals == [0, 1], (
        f"unnamed numeric weight(s) in _markdown_quality_score: "
        f"{[v for v in literals if v not in (0, 1)]}. Every weight in this function "
        f"must be a module-level constant, so the registry above covers it."
    )
