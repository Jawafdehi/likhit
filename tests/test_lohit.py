"""Tests for Lohit-Devanagari ToUnicode recovery.

The reference font is not vendored, so checks that re-derive
:data:`likhit.extractors.lohit.GID_TO_UNICODE` live in
``tests/manual/test_lohit_reference.py``. Everything here runs unconditionally:
the shipped table's load-bearing entries, visual-order marker rules, and identity
guard against synthetic fonts.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from likhit.extractors import kalimati, lohit
from likhit.extractors.font_classifier import classify_font

_NAME_BUILD = 3
_NAME_VERSION = 5


def _build_font(
    *,
    build: str = lohit.EXPECTED_BUILD,
    version: str = lohit.EXPECTED_VERSION,
    units_per_em: int = lohit.EXPECTED_UNITS_PER_EM,
    glyph_count: int = lohit.UPSTREAM_GLYPH_COUNT,
    outline_offset: int = 0,
) -> TTFont:
    """A minimal TrueType font that presents as the Lohit build under test.

    Every glyph gets a differently-placed triangle, so two glyphs never hash
    alike and ``outline_offset`` reliably changes every outline -- which is what
    lets a test stand in for "the same glyph order, different font".

    Compiled to bytes and read back, because ``maxp.numGlyphs`` is only computed
    on compile and the guard reads it -- an uncompiled builder font reports zero.
    """

    glyph_names = [".notdef"] + [f"g{index}" for index in range(1, glyph_count)]
    builder = FontBuilder(units_per_em, isTTF=True)
    builder.setupGlyphOrder(glyph_names)
    # Deliberately empty, mirroring the subsets in the corpus: the whole point of
    # the reference table is that the font's own cmap is gone.
    builder.setupCharacterMap({})
    glyphs = {}
    for index, name in enumerate(glyph_names):
        pen = TTGlyphPen(None)
        origin = (index + outline_offset) * 7
        pen.moveTo((origin, 0))
        pen.lineTo((origin + 40, 0))
        pen.lineTo((origin, 60))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyph_names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Lohit Devanagari", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    font = builder.font
    for name_id, value in ((_NAME_BUILD, build), (_NAME_VERSION, version)):
        font["name"].setName(value, name_id, 3, 1, 0x409)
    compiled = BytesIO()
    font.save(compiled)
    compiled.seek(0)
    return TTFont(compiled, lazy=False)


def _anchors_for(font: TTFont) -> dict[int, str]:
    """The digests ``font`` actually has, as :data:`lohit._ANCHOR_OUTLINES` would."""

    glyph_order = font.getGlyphOrder()
    return {
        gid: digest
        for gid in lohit._ANCHOR_OUTLINES
        if gid < len(glyph_order)
        and (digest := lohit._outline_digest(font, glyph_order[gid])) is not None
    }


@pytest.fixture
def known_font(monkeypatch: pytest.MonkeyPatch) -> TTFont:
    """A synthetic font the guard accepts, by pinning the anchors to its own."""

    font = _build_font()
    monkeypatch.setattr(lohit, "_ANCHOR_OUTLINES", _anchors_for(font))
    return font


# --------------------------------------------------------------------------
# The shipped table
# --------------------------------------------------------------------------


def test_reordering_markers_match_kalimati() -> None:
    """The redeclared markers must stay identical to the ones that consume them."""

    assert lohit._PUA_REPH == kalimati._PUA_REPH
    assert lohit._PUA_IKAR == kalimati._PUA_IKAR
    assert lohit._IKAR == kalimati._IKAR
    assert lohit._REPHA == kalimati._RA + kalimati._VIRAMA


@pytest.mark.parametrize(
    ("cid", "expected"),
    [
        (71, "क"),  # क -- a plain consonant
        (113, "ि"),  # ि -- the i-matra
        (152, "०"),  # ० -- Devanagari zero
        (224, "र्"),  # र् -- the repha
        (227, "्र"),  # ्र -- the rakar, corrected from the derivation
        (231, "क्ष"),  # क्ष
        (228, "श्र"),  # श्र
        (301, "क्र"),  # क्र
        (306, "त्र"),  # त्र
        (308, "प्र"),  # प्र
        # A rakar behind a precomposed nukta letter. Derived wrongly as `फ़र्`
        # until the ra-virama swap learned to look past the nukta.
        (229, "\u095e\u094d\u0930"),  # फ़्र -- precomposed U+095E, not फ + nukta
        (276, "त्र्"),  # त्र् -- a half-form, not a repha
    ],
)
def test_table_decodes_load_bearing_glyphs(cid: int, expected: str) -> None:
    assert lohit.GID_TO_UNICODE[cid] == expected


def test_below_form_ra_corrections_are_applied_to_the_table() -> None:
    """Every recorded correction is the value the table actually ships."""

    assert lohit.BELOW_FORM_RA_CORRECTIONS
    for cid, (derived, corrected) in lohit.BELOW_FORM_RA_CORRECTIONS.items():
        assert lohit.GID_TO_UNICODE[cid] == corrected
        assert derived != corrected
        # A rakar orders virama-then-ra; the derivation had it the other way.
        assert derived.startswith(lohit._REPHA)
        assert corrected.startswith(kalimati._VIRAMA + kalimati._RA)


def test_table_covers_the_upstream_glyph_range() -> None:
    assert lohit.GID_TO_UNICODE
    assert max(lohit.GID_TO_UNICODE) < lohit.UPSTREAM_GLYPH_COUNT


# --------------------------------------------------------------------------
# Visual-order marks
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # The i-matra is drawn before its consonant, so it must be moved.
        ("ि", lohit._PUA_IKAR),
        # A repha opens the cluster it is drawn over.
        ("र्", lohit._PUA_REPH),
        ("र्ं", lohit._PUA_REPH + "ं"),
        # A matra carrying a repha keeps the matra and moves only the repha.
        ("ेर्", "े" + lohit._PUA_REPH),
        ("ौर्", "ौ" + lohit._PUA_REPH),
        # A trailing ra+virama after a consonant is a half-form, not a repha.
        ("त्र्", "त्र्"),
        ("श्र्", "श्र्"),
        # A rakar is already in logical order and stays put.
        ("्र", "्र"),
        ("क्र", "क्र"),
        # Ordinary letters are untouched.
        ("क", "क"),
        ("क्ष", "क्ष"),
    ],
)
def test_with_reordering_markers(value: str, expected: str) -> None:
    assert lohit.with_reordering_markers(value) == expected


def test_correction_map_marks_every_bare_i_matra_and_repha(known_font: TTFont) -> None:
    """No plain i-matra or repha survives into the map handed to the repair.

    Left as plain characters they would extract in visual order --
    ``प्रादेिशक`` for ``प्रादेशिक`` -- because reorder_devanagari keys off the
    markers, not off the characters.
    """

    correction_map = lohit.lohit_correction_map(known_font)
    assert correction_map
    for cid, value in correction_map.items():
        plain = lohit.GID_TO_UNICODE[cid]
        if plain == lohit._IKAR:
            assert value == lohit._PUA_IKAR, cid
        if plain == lohit._REPHA:
            assert value == lohit._PUA_REPH, cid


def test_correction_map_is_truncated_to_the_subset(monkeypatch) -> None:
    """CIDs a subset cannot emit are dropped rather than padding the CMap."""

    font = _build_font(glyph_count=120)
    monkeypatch.setattr(lohit, "_ANCHOR_OUTLINES", _anchors_for(font))
    correction_map = lohit.lohit_correction_map(font)
    assert correction_map
    assert max(correction_map) < 120
    # ...and the entries below the cut are still all there.
    assert set(correction_map) == {cid for cid in lohit.GID_TO_UNICODE if cid < 120}


# --------------------------------------------------------------------------
# The identity guard
# --------------------------------------------------------------------------


def test_guard_accepts_the_known_build(known_font: TTFont) -> None:
    assert lohit.is_known_lohit_subset(known_font) is True
    assert lohit.lohit_correction_map(known_font)


def test_guard_rejects_a_shifted_glyph_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The anchor digests, not the name records, are what prove glyph order.

    A font can claim to be this build and still have had its glyphs reordered by
    a subsetter; applying the table then emits confident nonsense.
    """

    font = _build_font()
    shifted = _build_font(outline_offset=1)
    monkeypatch.setattr(lohit, "_ANCHOR_OUTLINES", _anchors_for(font))
    assert lohit.is_known_lohit_subset(font) is True
    assert lohit.is_known_lohit_subset(shifted) is False
    assert lohit.lohit_correction_map(shifted) == {}


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            {"build": "FontForge 2.0 : Lohit Devanagari : 17-9-2013"}, id="2.95.x build"
        ),
        pytest.param({"version": "Version 2.95.4"}, id="later version"),
        pytest.param({"units_per_em": 2048}, id="rescaled"),
        pytest.param(
            {"glyph_count": lohit.UPSTREAM_GLYPH_COUNT + 1},
            id="more glyphs than upstream",
        ),
    ],
)
def test_guard_rejects_other_builds(
    kwargs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = _build_font()
    monkeypatch.setattr(lohit, "_ANCHOR_OUTLINES", _anchors_for(reference))
    font = _build_font(**kwargs)  # type: ignore[arg-type]
    assert lohit.is_known_lohit_subset(font) is False
    assert lohit.lohit_correction_map(font) == {}


def test_guard_requires_at_least_one_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A font carrying none of the anchors cannot have its order proven."""

    font = _build_font()
    monkeypatch.setattr(
        lohit, "_ANCHOR_OUTLINES", {lohit.UPSTREAM_GLYPH_COUNT - 1: "0" * 16}
    )
    assert lohit.is_known_lohit_subset(font) is False


def test_guard_ignores_anchors_the_subset_blanked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subset drops the outlines it does not use; that is not a mismatch."""

    font = _build_font()
    anchors = _anchors_for(font)
    assert len(anchors) >= 2
    blanked, kept = sorted(anchors)[0], sorted(anchors)[1:]
    glyph_order = font.getGlyphOrder()
    font["glyf"][glyph_order[blanked]].numberOfContours = 0
    font["glyf"][glyph_order[blanked]].removeHinting()
    monkeypatch.setattr(lohit, "_ANCHOR_OUTLINES", anchors)
    assert lohit._outline_digest(font, glyph_order[blanked]) is None
    assert kept
    assert lohit.is_known_lohit_subset(font) is True


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_lohit_is_classified_as_a_broken_cmap_font() -> None:
    """Without this the repair pass never runs for a Lohit-only document."""

    assert classify_font("Lohit-Devanagari", "Type0") == "broken_cmap"
    assert classify_font("ABCDEF+Lohit-Devanagari", "Type0") == "broken_cmap"


def _pdf_embedding(font: TTFont, tmp_path: Path) -> fitz.Document:
    """A PDF that embeds ``font`` as an ``/Identity-H`` CIDFontType2."""

    font_path = tmp_path / "subset.ttf"
    font.save(font_path)
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_font(fontname="Lsub", fontfile=str(font_path))
        page.insert_text((72, 72), "AB", fontname="Lsub")
        raw = doc.tobytes()
    finally:
        doc.close()
    return fitz.open(stream=raw, filetype="pdf")


def test_correction_map_falls_back_to_the_reference_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """kalimati's builder must consult the table when the font's cmap is empty.

    This is the seam the whole feature hangs on. The subsets carry no cmap, so
    the reconstruction has nothing to read and, without this fallback,
    ``_get_font_correction_map`` returns nothing and no ToUnicode is rewritten.
    """

    font = _build_font()
    monkeypatch.setattr(lohit, "_ANCHOR_OUTLINES", _anchors_for(font))
    doc = _pdf_embedding(font, tmp_path)
    try:
        xref = doc[0].get_fonts(full=True)[0][0]
        embedded = TTFont(BytesIO(doc.extract_font(xref)[3]), lazy=False)
        assert not kalimati._safe_get_best_cmap(embedded), (
            "the fixture must reproduce the corpus condition: no usable cmap"
        )
        result = kalimati._get_font_correction_map(doc, xref)
    finally:
        doc.close()

    assert result
    assert result[231] == "क्ष"
    assert result[113] == lohit._PUA_IKAR
    assert result[224] == lohit._PUA_REPH


def test_correction_map_stays_empty_for_an_unrecognised_font(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fallback must not fire for a cmap-less font we have no table for."""

    reference = _build_font()
    monkeypatch.setattr(lohit, "_ANCHOR_OUTLINES", _anchors_for(reference))
    doc = _pdf_embedding(_build_font(version="Version 2.95.4"), tmp_path)
    try:
        xref = doc[0].get_fonts(full=True)[0][0]
        assert kalimati._get_font_correction_map(doc, xref) == {}
    finally:
        doc.close()


def test_gsub_variant_additions_are_what_the_table_ships() -> None:
    """Each addition carries its source's value, and the table agrees."""

    assert lohit.GSUB_VARIANT_ADDITIONS
    for cid, (source, value) in lohit.GSUB_VARIANT_ADDITIONS.items():
        assert lohit.GID_TO_UNICODE[cid] == value
        # A positional variant is the same text as the glyph it substitutes for;
        # anything else does not belong in this dict.
        assert lohit.GID_TO_UNICODE[source] == value


def test_a_variant_addition_reorders_exactly_like_its_source() -> None:
    """The value is handed out through the same marker rules, not around them.

    This is the property that matters, and it is the only one asserted per
    entry. An earlier version also required the transform to be a *change*,
    which held for CID 292 only because its value carries a repha -- the
    transform is a no-op on CID 291's ``ीं`` and on CID 293's ``ीर्ं``, so that
    assertion over-fitted to one entry and would have blocked its two
    legitimate siblings.
    """

    for cid, (source, _value) in lohit.GSUB_VARIANT_ADDITIONS.items():
        assert lohit.with_reordering_markers(
            lohit.GID_TO_UNICODE[cid]
        ) == lohit.with_reordering_markers(lohit.GID_TO_UNICODE[source])


def test_the_repha_carrying_variant_does_reorder() -> None:
    """Kept from the per-entry check above, as a claim about CID 292 alone.

    CID 292 is the entry the corpus actually needs, so its repha must still be
    moved to the front of the cluster. Stated for that CID rather than for every
    addition, which is what made the general form wrong.
    """

    value = lohit.GID_TO_UNICODE[292]

    assert lohit.with_reordering_markers(value) != value
    assert lohit.with_reordering_markers(value) == "ी" + lohit._PUA_REPH
