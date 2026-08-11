"""Tests for the Kalimati outline reference table.

The reference font is not vendored, so the test that re-derives
:data:`likhit.extractors.kalimati_reference.OUTLINE_TO_UNICODE` from it is skipped
unless ``LIKHIT_KALIMATI_REFERENCE_TTF`` points at a copy. Everything else runs
unconditionally: the shipped table's own invariants, the lookup's behaviour
against synthetic fonts, and the ``GSUB`` swap the table depends on.
"""

from __future__ import annotations

import os
import re
import types
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from likhit.extractors import kalimati, kalimati_reference, lohit

_CONSONANT = "[क-ह]"
# A rakar is the below-form ra: virama then ra. A consonant followed directly by
# ra-then-virama -- with or without a nukta bound to it -- is that same glyph with
# its two components the wrong way round, which is the bug the GSUB swap fixes.
_INVERTED_RAKAR = re.compile(f"{_CONSONANT}़?र्")


def _build_font(
    *,
    units_per_em: int = kalimati_reference.REFERENCE_UNITS_PER_EM,
    glyph_count: int = 24,
    character_map: dict[int, str] | None = None,
    blank_gids: frozenset[int] = frozenset(),
) -> TTFont:
    """A minimal TrueType font, one distinct triangle per glyph.

    Distinct outlines mean no two glyphs hash alike, so a test can pin the table
    to this font's own digests and know which glyph each lookup found.
    Compiled and read back so ``maxp.numGlyphs`` is populated.
    """

    glyph_names = [".notdef"] + [f"g{index}" for index in range(1, glyph_count)]
    builder = FontBuilder(units_per_em, isTTF=True)
    builder.setupGlyphOrder(glyph_names)
    builder.setupCharacterMap(character_map or {})
    glyphs = {}
    for index, name in enumerate(glyph_names):
        pen = TTGlyphPen(None)
        if index not in blank_gids:
            origin = index * 11
            pen.moveTo((origin, 0))
            pen.lineTo((origin + 40, 0))
            pen.lineTo((origin, 60))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyph_names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Kalimati", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    compiled = BytesIO()
    builder.font.save(compiled)
    compiled.seek(0)
    return TTFont(compiled, lazy=False)


def _digests_for(font: TTFont, gids: dict[int, str]) -> dict[str, str]:
    """``{digest: value}`` for ``{gid: value}``, as the shipped table is shaped."""

    glyph_order = font.getGlyphOrder()
    table = {}
    for gid, value in gids.items():
        digest = kalimati_reference.outline_digest(font, glyph_order[gid])
        assert digest is not None, f"gid {gid} has no outline to key on"
        table[digest] = value
    return table


# --------------------------------------------------------------------------
# The shipped table
# --------------------------------------------------------------------------


def test_table_is_keyed_on_truncated_outline_digests() -> None:
    assert kalimati_reference.OUTLINE_TO_UNICODE
    for digest in kalimati_reference.OUTLINE_TO_UNICODE:
        assert re.fullmatch(r"[0-9a-f]{16}", digest), digest


@pytest.mark.parametrize(
    "expected",
    [
        "क",  # क -- a plain consonant
        "ि",  # ि -- the i-matra
        "०",  # ० -- Devanagari zero
        "र्",  # र् -- the repha
        "्र",  # ्र -- the rakar
        "क्ष",  # क्ष
        "प्र",  # प्र
        "त्र",  # त्र
        "म्",  # म् -- a half-form
        "स्",  # स्
    ],
)
def test_table_carries_the_load_bearing_clusters(expected: str) -> None:
    """The classes GSUB encodes are what the stripped subsets are missing."""

    assert expected in set(kalimati_reference.OUTLINE_TO_UNICODE.values())


def test_table_values_are_devanagari_only() -> None:
    """A Latin value would mean the derivation was seeded from a legacy cmap.

    Several fonts in this corpus map ASCII to Devanagari glyphs, so an
    unrestricted seed records a Devanagari outline as ``3`` or ``k``.
    """

    for digest, value in kalimati_reference.OUTLINE_TO_UNICODE.items():
        assert value, digest
        for char in value:
            assert 0x0900 <= ord(char) <= 0x097F, (digest, value, hex(ord(char)))


def test_table_has_no_inverted_rakar() -> None:
    """No value orders a below-form ra as ra-then-virama.

    The derivation produces that order for a rakar reached through a ligature
    rule; ``_analyze_gsub`` swaps it back, including across a nukta.

    The nukta handling is two independent halves, and this table depends on both.
    Re-derived from the reference font by reverting each and diffing: the nukta
    *skip* inside the swap accounts for 40 entries, the precomposed-nukta range in
    ``_is_rakar_base`` for 16, and the two together for 56 -- they are disjoint,
    overlap 0. A regression in either is silent without this test.
    """

    offenders = {
        digest: value
        for digest, value in kalimati_reference.OUTLINE_TO_UNICODE.items()
        if _INVERTED_RAKAR.search(value)
    }
    assert not offenders, offenders


def test_below_form_ra_corrections_are_applied_to_the_table() -> None:
    """Every recorded correction is the value the table actually ships.

    A rakar handed out as a repha is not a hole but a different word:
    ``reorder_devanagari`` moves a repha to the front of its cluster, so ``प्र``
    would come back as ``र्प``.
    """

    assert kalimati_reference.BELOW_FORM_RA_CORRECTIONS
    for digest, (
        derived,
        corrected,
    ) in kalimati_reference.BELOW_FORM_RA_CORRECTIONS.items():
        assert kalimati_reference.OUTLINE_TO_UNICODE[digest] == corrected
        assert derived != corrected
        # A repha orders ra-then-virama; a rakar the other way round.
        assert derived == kalimati._RA + kalimati._VIRAMA
        assert corrected == kalimati._VIRAMA + kalimati._RA


def test_table_keeps_trailing_half_forms_intact() -> None:
    """``श्र्`` is a half-form awaiting its next consonant, not a misordered rakar.

    Guards the swap against over-reaching: these must survive untouched.
    """

    values = set(kalimati_reference.OUTLINE_TO_UNICODE.values())
    assert "श्र्" in values  # श्र्
    assert "त्र्" in values  # त्र्


# --------------------------------------------------------------------------
# Looking a font up
# --------------------------------------------------------------------------


def test_outline_digest_matches_lohit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two digest helpers must not drift; the table's keys assume they agree."""

    font = _build_font()
    glyph_order = font.getGlyphOrder()
    compared = 0
    for glyph_name in glyph_order:
        mine = kalimati_reference.outline_digest(font, glyph_name)
        theirs = lohit._outline_digest(font, glyph_name)
        assert mine == theirs, glyph_name
        compared += mine is not None
    assert compared > 1, "the fixture must carry outlines for this to prove anything"


def test_reference_map_recovers_glyphs_by_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font = _build_font()
    monkeypatch.setattr(
        kalimati_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {3: "क्ष", 5: "प्र"}),
    )

    assert kalimati_reference.kalimati_reference_map(font) == {
        3: "क्ष",
        5: "प्र",
    }


def test_reference_map_skips_gids_the_caller_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The font's own cmap beats a reference, so the caller's answers win."""

    font = _build_font()
    monkeypatch.setattr(
        kalimati_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {3: "क्ष", 5: "प्र"}),
    )

    assert kalimati_reference.kalimati_reference_map(font, skip={3}) == {5: "प्र"}


def test_reference_map_is_empty_for_an_unrecognised_font() -> None:
    """Synthetic triangles are not Kalimati drawings, so nothing is claimed."""

    assert kalimati_reference.kalimati_reference_map(_build_font()) == {}


def test_reference_map_ignores_another_em_square(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A font drawn on a different em square is refused before it is hashed.

    Keyed on the rescaled font's *own* digests, so the em square is the only
    thing standing between it and a match -- otherwise this would pass whether
    the check existed or not.
    """

    rescaled = _build_font(units_per_em=1000)
    monkeypatch.setattr(
        kalimati_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(rescaled, {3: "क्ष"}),
    )

    assert kalimati_reference.kalimati_reference_map(rescaled) == {}


def test_reference_map_ignores_glyphs_the_subsetter_blanked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blanked glyph has no outline, so it is absence of evidence, not a match."""

    font = _build_font()
    table = _digests_for(font, {3: "क्ष"})
    monkeypatch.setattr(kalimati_reference, "OUTLINE_TO_UNICODE", table)

    blanked = _build_font(blank_gids=frozenset({3}))
    assert kalimati_reference.kalimati_reference_map(blanked) == {}


def test_reference_map_survives_a_font_without_glyph_outlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``glyf`` table means no digests, not an exception."""

    font = _build_font()
    monkeypatch.setattr(
        kalimati_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "क"})
    )
    del font["glyf"]
    assert kalimati_reference.kalimati_reference_map(font) == {}


# --------------------------------------------------------------------------
# The GSUB swap the table depends on
# --------------------------------------------------------------------------


def _gsub_font(ligature_first: str) -> types.SimpleNamespace:
    """A GSUB table carrying just enough for ``_analyze_gsub``.

    Three rules, mirroring how the reference font builds a nukta'd rakar: a
    ``nukt`` single substitution, an ``rphf`` one that gives the ra glyph its
    ``र्`` value, and a ligature joining ``ligature_first`` to the ra glyph.
    """

    def lookup(lookup_type, subtable):
        return types.SimpleNamespace(LookupType=lookup_type, SubTable=[subtable])

    lookups = [
        lookup(1, types.SimpleNamespace(mapping={"cons": "nukta_form"})),
        lookup(1, types.SimpleNamespace(mapping={"ra": "rakar"})),
        lookup(
            4,
            types.SimpleNamespace(
                ligatures={
                    ligature_first: [
                        types.SimpleNamespace(LigGlyph="lig", Component=["rakar"])
                    ]
                }
            ),
        ),
    ]
    features = [
        types.SimpleNamespace(
            FeatureTag=tag, Feature=types.SimpleNamespace(LookupListIndex=[index])
        )
        for index, tag in enumerate(("nukt", "rphf", "akhn"))
    ]
    return types.SimpleNamespace(
        table=types.SimpleNamespace(
            FeatureList=types.SimpleNamespace(FeatureRecord=features),
            LookupList=types.SimpleNamespace(Lookup=lookups),
        )
    )


class _FakeGsubFont:
    """Minimal mapping protocol so ``_analyze_gsub`` can read one GSUB table."""

    def __init__(self, gsub: object) -> None:
        self._gsub = gsub

    def __contains__(self, key: str) -> bool:
        return key == "GSUB"

    def __getitem__(self, key: str) -> object:
        if key != "GSUB":
            raise KeyError(key)
        return self._gsub


@pytest.mark.parametrize(
    ("ligature_first", "base_value", "expected"),
    [
        # छ + rakar -> छ्र. The swap already handled the plain case.
        ("cons", "छ", "छ्र"),
        # छ़ + rakar -> छ़्र. A nukta binds to the consonant in front of it, so it
        # sits between the base and its below-form ra without separating them.
        ("nukta_form", "छ", "छ़्र"),
        # ढ़ + rakar -> ढ़्र, with the base written as the single precomposed
        # letter U+095D rather than ढ + U+093C. This is the case _is_rakar_base's
        # U+0958-U+095F range exists for, and it is a different code path from the
        # combining-nukta case above -- an earlier version of this parameter spelled
        # it decomposed, so it re-tested the previous row and left the precomposed
        # range covered by nothing but the env-gated re-derivation test.
        ("cons", "ढ़", "ढ़्र"),
    ],
)
def test_analyze_gsub_orders_a_rakar_after_its_base(
    ligature_first: str, base_value: str, expected: str
) -> None:
    glyph_order = [".notdef", "cons", "ra", "nukta_form", "rakar", "lig"]
    font = _FakeGsubFont(_gsub_font(ligature_first))

    derived = kalimati._analyze_gsub(font, glyph_order, {1: base_value, 2: "र"})

    assert derived[glyph_order.index("lig")] == expected


def test_analyze_gsub_returns_nothing_without_a_gsub_table() -> None:
    class _NoGsub:
        def __contains__(self, key: str) -> bool:
            return False

    assert kalimati._analyze_gsub(_NoGsub(), [".notdef"], {}) == {}


# --------------------------------------------------------------------------
# Wiring into the correction map
# --------------------------------------------------------------------------


def _pdf_embedding(font_bytes: bytes) -> bytes:
    """A one-page PDF that embeds ``font_bytes`` as an /Identity-H Type0 font."""

    doc = fitz.open()
    try:
        page = doc.new_page()
        path = Path(fitz.__file__).parent / "_embedded_kalimati_test.ttf"
        path.write_bytes(font_bytes)
        try:
            page.insert_font(fontname="KaliTest", fontfile=str(path))
            page.insert_text((72, 72), "AB", fontname="KaliTest")
        finally:
            path.unlink(missing_ok=True)
        out = BytesIO()
        doc.save(out)
        return out.getvalue()
    finally:
        doc.close()


def test_correction_map_fills_conjuncts_a_stripped_subset_cannot_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: cmap survives, GSUB does not, so conjuncts come from here.

    The font's own cmap answers for the glyph it covers; the reference answers for
    the one it does not, and neither overrides the other.
    """

    font = _build_font(character_map={0x0915: "g3"})
    compiled = BytesIO()
    font.save(compiled)
    reference = _digests_for(font, {5: "क्ष"})
    monkeypatch.setattr(kalimati_reference, "OUTLINE_TO_UNICODE", reference)

    doc = fitz.open(stream=_pdf_embedding(compiled.getvalue()), filetype="pdf")
    try:
        type0_xrefs = [
            info[0] for info in doc[0].get_fonts(full=True) if info[2] == "Type0"
        ]
        assert type0_xrefs, "the fixture must embed a Type0 font"
        merged: dict[int, str] = {}
        for xref in type0_xrefs:
            merged.update(kalimati._get_font_correction_map(doc, xref))
    finally:
        doc.close()

    assert merged.get(3) == "क", "the font's own cmap must still answer"
    assert merged.get(5) == "क्ष", "the reference must fill the rest"


def test_the_fonts_own_cmap_wins_when_the_reference_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Precedence, tested where it can actually be lost.

    The test above has the reference speak only about a glyph the cmap does not
    cover, so there is no conflict in it to lose. This one makes the reference
    claim a glyph the font's own cmap already answers, and asserts the cmap wins.

    Why it needs to be behavioural: the ordering is doubly guarded -- by
    ``skip=set(from_cmap) | set(derived)`` and again by the ``gid not in
    from_cmap`` test on the fill -- so no single-point mutation bites. Removing
    the ``skip`` *and* hoisting ``full_map.update(from_cmap)`` above the fill
    left the whole suite green before this existed. The stake is the difference
    between "no repair" and confident wrong Unicode, which this module exists to
    avoid.
    """

    font = _build_font(character_map={0x0915: "g3"})
    compiled = BytesIO()
    font.save(compiled)
    # The reference claims gid 3 is क्ष. The font's own cmap says क. The cmap is
    # first-hand evidence about *this* font and must not be overridden.
    monkeypatch.setattr(
        kalimati_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {3: "क्ष", 5: "प्र"}),
    )

    doc = fitz.open(stream=_pdf_embedding(compiled.getvalue()), filetype="pdf")
    try:
        merged: dict[int, str] = {}
        for info in doc[0].get_fonts(full=True):
            if info[2] == "Type0":
                merged.update(kalimati._get_font_correction_map(doc, info[0]))
    finally:
        doc.close()

    assert merged.get(3) == "क", (
        "the reference overrode the font's own cmap; that emits confident wrong "
        "Unicode rather than leaving a glyph unrepaired"
    )
    # ...and the reference is still consulted for glyphs the cmap is silent about,
    # so this is precedence rather than the reference being ignored outright.
    assert merged.get(5) == "प्र"


def test_the_in_line_half_form_of_ra_is_not_marked_as_a_repha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-line ``र्`` must not be reordered to the front of its cluster.

    A repha and the in-line half-form of ra decode to the same ``ra + virama``
    string, so ``with_reordering_markers`` -- which keys on the value -- would
    give both the repha marker, and ``reorder_devanagari`` would then move the
    half-form to the front: ``प्र`` becomes ``र्प``, a different word rather than a
    hole. The two are told apart by outline, via ``IN_LINE_RA_DIGESTS``.
    """

    font = _build_font()
    glyph_order = font.getGlyphOrder()
    repha_digest = kalimati_reference.outline_digest(font, glyph_order[3])
    in_line_digest = kalimati_reference.outline_digest(font, glyph_order[5])
    assert repha_digest and in_line_digest and repha_digest != in_line_digest

    monkeypatch.setattr(
        kalimati_reference,
        "OUTLINE_TO_UNICODE",
        {repha_digest: "र्", in_line_digest: "र्"},
    )
    monkeypatch.setattr(
        kalimati_reference,
        "IN_LINE_RA_DIGESTS",
        frozenset({in_line_digest}),
    )

    mapped = kalimati._kalimati_reference_map(font)

    assert mapped[3] == lohit._PUA_REPH, "a real repha must still be marked"
    assert mapped[5] == "र्", "the in-line half-form must be left in logical order"


def test_correction_map_prefers_an_outline_match_over_an_inferred_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact outline beats ``_infer_mark_variants``' nearest-metric guess."""

    font = _build_font(character_map={0x0915: "g3"})
    compiled = BytesIO()
    font.save(compiled)
    monkeypatch.setattr(
        kalimati_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {5: "ी"}),  # ी
    )
    # Stand in for the metric matcher claiming the same glyph, wrongly.
    monkeypatch.setattr(
        kalimati,
        "_infer_mark_variants",
        lambda font, glyph_order, gid_to_correct: {5: "ि"},  # ि
    )

    doc = fitz.open(stream=_pdf_embedding(compiled.getvalue()), filetype="pdf")
    try:
        merged: dict[int, str] = {}
        for info in doc[0].get_fonts(full=True):
            if info[2] == "Type0":
                merged.update(kalimati._get_font_correction_map(doc, info[0]))
    finally:
        doc.close()

    assert merged.get(5) == "ी"


def test_reference_values_carry_reordering_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare i-matra or repha must be handed out as a reordering marker.

    ``_patch_single_cmap``'s own marker rules are conditioned on the value the
    PDF's broken CMap supplied, which says nothing about a reference-derived one.
    """

    font = _build_font()
    monkeypatch.setattr(
        kalimati_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {3: "ि", 5: "र्", 7: "म्"}),
    )

    handed_out = kalimati._kalimati_reference_map(font)

    assert handed_out[3] == kalimati._PUA_IKAR
    assert handed_out[5] == kalimati._PUA_REPH
    # A half-form is not a visual-order mark and must not be rewritten.
    assert handed_out[7] == "म्"


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def _reference_font() -> TTFont | None:
    raw = os.environ.get("LIKHIT_KALIMATI_REFERENCE_TTF")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    return TTFont(path, lazy=False)


@pytest.mark.skipif(
    _reference_font() is None,
    reason="set LIKHIT_KALIMATI_REFERENCE_TTF to the reference Kalimati TTF",
)
def test_table_re_derives_from_the_reference_font() -> None:
    """The shipped table is exactly what the derivation produces. This is the recipe.

    Seeded from Devanagari ``cmap`` entries only, then re-keyed from CID to
    outline digest. An outline that two CIDs disagree about is dropped.
    """

    font = _reference_font()
    assert font is not None
    glyph_order = font.getGlyphOrder()
    best_cmap = kalimati._safe_get_best_cmap(font)
    devanagari = {
        codepoint: name
        for codepoint, name in best_cmap.items()
        if 0x0900 <= codepoint <= 0x097F
    }
    assert devanagari, "the reference font must still have its own cmap"

    name_to_unicode = {name: codepoint for codepoint, name in devanagari.items()}
    gid_to_correct = {
        gid: chr(name_to_unicode[name])
        for gid, name in enumerate(glyph_order)
        if name in name_to_unicode
    }
    gid_to_correct.update(
        kalimati._infer_mark_variants(font, glyph_order, gid_to_correct)
    )
    derived = kalimati._analyze_gsub(font, glyph_order, gid_to_correct)
    full = dict(derived)
    full.update(gid_to_correct)

    glyf = font["glyf"]

    def is_below_form(glyph_name: str) -> bool:
        """Drawn entirely below the baseline, so a below-form rather than a repha."""

        glyph = glyf[glyph_name]
        if glyph.numberOfContours == 0:
            return False
        glyph.recalcBounds(glyf)
        return bool(glyph.yMax <= 0)

    expected: dict[str, str] = {}
    corrections: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()
    for gid, value in sorted(full.items()):
        if gid >= len(glyph_order) or not value:
            continue
        digest = kalimati_reference.outline_digest(font, glyph_order[gid])
        if digest is None:
            continue
        if value == kalimati._RA + kalimati._VIRAMA and is_below_form(glyph_order[gid]):
            corrected = kalimati._VIRAMA + kalimati._RA
            corrections[digest] = (value, corrected)
            value = corrected
        if digest in expected and expected[digest] != value:
            ambiguous.add(digest)
            continue
        expected[digest] = value
    for digest in ambiguous:
        expected.pop(digest, None)

    assert expected == kalimati_reference.OUTLINE_TO_UNICODE
    assert corrections == kalimati_reference.BELOW_FORM_RA_CORRECTIONS


@pytest.mark.skipif(
    _reference_font() is None,
    reason="set LIKHIT_KALIMATI_REFERENCE_TTF to the reference Kalimati TTF",
)
def test_reference_font_matches_the_declared_identity() -> None:
    font = _reference_font()
    assert font is not None
    assert font["head"].unitsPerEm == kalimati_reference.REFERENCE_UNITS_PER_EM
    assert font["maxp"].numGlyphs == kalimati_reference.REFERENCE_GLYPH_COUNT
    assert "GSUB" in font, "the reference is only useful because it kept GSUB"
