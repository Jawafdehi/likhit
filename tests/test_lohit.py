"""Tests for Lohit-Devanagari ToUnicode recovery.

The reference font is not vendored, so the test that re-derives
:data:`likhit.extractors.lohit.GID_TO_UNICODE` from it is skipped unless
``LIKHIT_LOHIT_REFERENCE_TTF`` points at a copy. Everything else -- the shipped
table's load-bearing entries, the visual-order marker rules and the identity
guard -- runs unconditionally against synthetic fonts.
"""

from __future__ import annotations

import os
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


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def _reference_font() -> TTFont | None:
    raw = os.environ.get("LIKHIT_LOHIT_REFERENCE_TTF")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    return TTFont(path, lazy=False)


@pytest.mark.skipif(
    _reference_font() is None,
    reason="set LIKHIT_LOHIT_REFERENCE_TTF to the upstream Lohit-Devanagari 2.5.3 TTF",
)
def test_table_re_derives_from_the_reference_font() -> None:
    """The shipped table is exactly the derivation plus the recorded corrections.

    Regenerating it is not a manual edit: this is the recipe.
    """

    font = _reference_font()
    assert font is not None
    glyph_order = font.getGlyphOrder()
    best_cmap = kalimati._safe_get_best_cmap(font)
    assert best_cmap, "the reference font must still have its own cmap"
    name_to_unicode = {name: codepoint for codepoint, name in best_cmap.items()}

    gid_to_correct = {
        gid: chr(name_to_unicode[name])
        for gid, name in enumerate(glyph_order)
        if name in name_to_unicode
    }
    gid_to_correct.update(
        kalimati._infer_mark_variants(font, glyph_order, gid_to_correct)
    )
    derived = kalimati._analyze_gsub(font, glyph_order, gid_to_correct)
    expected = dict(derived)
    expected.update(gid_to_correct)
    for cid, (was, now) in lohit.BELOW_FORM_RA_CORRECTIONS.items():
        assert expected[cid] == was, f"CID {cid} no longer derives as {was!r}"
        expected[cid] = now
    for cid, (source, value) in lohit.GSUB_VARIANT_ADDITIONS.items():
        assert cid not in expected, f"CID {cid} now derives on its own"
        assert expected[source] == value, f"CID {source} no longer derives as {value!r}"
        expected[cid] = value

    assert expected == lohit.GID_TO_UNICODE


@pytest.mark.skipif(
    _reference_font() is None,
    reason="set LIKHIT_LOHIT_REFERENCE_TTF to the upstream Lohit-Devanagari 2.5.3 TTF",
)
def test_reference_font_matches_the_declared_identity_and_anchors() -> None:
    font = _reference_font()
    assert font is not None
    assert lohit._name_record(font, _NAME_BUILD) == lohit.EXPECTED_BUILD
    assert lohit._name_record(font, _NAME_VERSION) == lohit.EXPECTED_VERSION
    assert font["head"].unitsPerEm == lohit.EXPECTED_UNITS_PER_EM
    assert font["maxp"].numGlyphs == lohit.UPSTREAM_GLYPH_COUNT
    glyph_order = font.getGlyphOrder()
    for gid, expected_digest in lohit._ANCHOR_OUTLINES.items():
        assert lohit._outline_digest(font, glyph_order[gid]) == expected_digest
    assert lohit.is_known_lohit_subset(font) is True


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


@pytest.mark.skipif(
    _reference_font() is None,
    reason="set LIKHIT_LOHIT_REFERENCE_TTF to the upstream Lohit-Devanagari 2.5.3 TTF",
)
def test_variant_additions_rest_on_a_single_subst_rule_in_the_font() -> None:
    """The provenance, checked against the font rather than taken on trust.

    Two halves, and the second is the load-bearing one. That *a* SingleSubst maps
    source to target only makes them related; what makes them the *same text* is
    the feature it sits under. `psts` is post-base positional substitution, so
    the pair is one glyph drawn differently. An `aalt`/`salt`/`ss01` rule would
    be a stylistic alternate, and a future release could add one of those while
    repurposing the target glyph entirely -- which the source-to-target check
    alone would wave through.

    This test is env-gated on the reference font, and CI sets no such variable,
    so it is skipped there. It does not fail the build; it fails *this* check when
    someone runs it with the font present. The two unconditional tests above check
    the table's self-consistency, which is a different and weaker property: a
    consistent mistype across the table, the addition record and the shipped
    value passes both of them.
    """

    positional_features = {"psts", "pres", "abvs", "blws", "half", "rphf", "vatu"}

    font = _reference_font()
    assert font is not None
    glyph_order = font.getGlyphOrder()
    gsub = font["GSUB"].table

    # feature tag -> the lookups it reaches, directly or as a nested lookup of a
    # contextual rule. Lookup 82 is reachable only via the second path.
    direct: dict[int, set[str]] = {}
    for record in gsub.FeatureList.FeatureRecord:
        for index in record.Feature.LookupListIndex:
            direct.setdefault(index, set()).add(record.FeatureTag)

    def _nested_indices(subtable: object) -> set[int]:
        found: set[int] = set()
        for records in _substitution_record_lists(subtable):
            for record in records:
                found.add(record.LookupListIndex)
        return found

    reaching: dict[int, set[str]] = {index: set(tags) for index, tags in direct.items()}
    for index, lookup in enumerate(gsub.LookupList.Lookup):
        for subtable in lookup.SubTable:
            for nested in _nested_indices(subtable):
                reaching.setdefault(nested, set()).update(direct.get(index, set()))

    substitutions: dict[str, set[tuple[str, int]]] = {}
    for index, lookup in enumerate(gsub.LookupList.Lookup):
        for subtable in lookup.SubTable:
            if subtable.__class__.__name__ != "SingleSubst":
                continue
            for source_name, target_name in subtable.mapping.items():
                substitutions.setdefault(target_name, set()).add((source_name, index))

    for cid, (source, _value) in lohit.GSUB_VARIANT_ADDITIONS.items():
        target_name = glyph_order[cid]
        source_name = glyph_order[source]
        rules = {
            index
            for name, index in substitutions.get(target_name, set())
            if name == source_name
        }
        assert rules, (
            f"no SingleSubst produces {target_name} (CID {cid}) from CID {source}"
        )
        tags = {tag for index in rules for tag in reaching.get(index, set())}
        assert tags & positional_features, (
            f"CID {source} -> {cid} is reached only by {sorted(tags)}, none of "
            f"which means 'same text, different position'"
        )


def _substitution_record_lists(subtable: object) -> list[list[object]]:
    """Every `SubstLookupRecord` list a contextual subtable can hold.

    fontTools spells these differently per format, and lookup 82 is reached only
    through Format-3 `ChainContextSubst`, whose records hang off the subtable
    directly rather than off a rule.
    """

    lists: list[list[object]] = []
    records = getattr(subtable, "SubstLookupRecord", None)
    if records:
        lists.append(list(records))
    for container in ("ChainSubClassSet", "SubRuleSet", "ChainSubRuleSet"):
        for entry in getattr(subtable, container, None) or []:
            for attribute in ("ChainSubClassRule", "SubRule", "ChainSubRule"):
                for rule in getattr(entry, attribute, None) or []:
                    rule_records = getattr(rule, "SubstLookupRecord", None)
                    if rule_records:
                        lists.append(list(rule_records))
    return lists
