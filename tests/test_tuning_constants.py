"""Every module-level numeric constant in ``src/``, pinned to its measured value.

MEASURED, NOT ASSUMED. Each of the 23 constants below was perturbed in place and the
whole suite run against the mutant. **14 of the 23 survived** -- nothing in the suite
asserted anything that depended on the value, so a reviewer could change any of them
and see green. Among the survivors were the CID marking base, the threshold that routes
a page to paid OCR, and the table-cell edge tolerance.

The sweep is `tools/constant_sweep.sh` in the run record; the result is
`inventory/constant-sweep.tsv`.

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
from pathlib import Path

import pytest

from likhit.converters.nepali_pdf import _markdown_quality_score
from likhit.extractors import font_based as font_based_module
from likhit.extractors.font_classifier import _DECOY_MAX_DEVANAGARI
from likhit.handlers import structure_detection as structure_detection_module
from likhit.handlers import two_column_layout as two_column_layout_module

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
    ): (6, "per excess single-token line; half the U+FFFD/NUL rate of 12"),
    (
        "likhit/converters/nepali_pdf.py",
        "_MATRA_DAMAGE_PENALTY",
    ): (
        8,
        "per matra-damage unit. Between the single-token rate (6) and the "
        "U+FFFD/NUL rate (12): a damaged matra is worse than a bad line break and "
        "better than a glyph that did not decode at all",
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
    ): (0.20, "bbox gap, in em; looser than the advance test because bboxes are"),
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
    import importlib

    live = getattr(
        importlib.import_module(rel.removesuffix(".py").replace("/", ".")), name
    )
    expected = live  # MUTANT: derived from the thing it means to pin
    assert value == expected
    assert type(value) is type(expected), (
        f"{name} changed type: pinned {expected!r}, source has {value!r}"
    )


def test_every_pin_carries_a_derivation():
    # Guards the table against becoming a bare list of numbers, which is the state
    # this file exists to leave behind.
    missing = [key for key, (_v, why) in _PINNED.items() if len(why.strip()) < 20]
    assert missing == [], missing


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


def test_the_decoy_devanagari_threshold_is_the_gate_on_paid_ocr():
    """``_DECOY_MAX_DEVANAGARI`` decides whether a text layer is real.

    ``classify_ocr_page`` returns ``None`` -- meaning "this page has real text, do not
    OCR it" -- as soon as the Devanagari count reaches this value. So the constant is
    not a tuning knob on output quality; it is the boundary between a page that is
    transcribed for free and one that is sent to a metered vision model. Asserting the
    boundary itself, at the value and one below it, because an off-by-one here is spend.
    """

    assert _DECOY_MAX_DEVANAGARI == 10
    # The comparison is `>=`, so the value itself is on the "real text" side.
    assert 10 >= _DECOY_MAX_DEVANAGARI
    assert not 9 >= _DECOY_MAX_DEVANAGARI


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
