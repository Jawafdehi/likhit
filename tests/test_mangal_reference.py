"""Tests for the Mangal outline reference table.

The corpus PDFs the table was derived from are not vendored, so nothing here
re-derives it. What it does test is the shipped table's own invariants -- the ones
a wrong entry would break -- the lookup's behaviour against synthetic fonts, and
the two things that make ``kalimati._reference_correction_map`` safe: the two
reference tables share no outline, and the in-line half-form of ra is exempt from
the repha reordering marker.
"""

from __future__ import annotations

import re
from io import BytesIO

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from likhit.extractors import kalimati, kalimati_reference, mangal_reference

_MODULE_SOURCE = (
    __import__("pathlib").Path(mangal_reference.__file__).read_text(encoding="utf-8")
)
_ENTRY_RE = re.compile(
    r'^    "(?P<digest>[0-9a-f]{16})": "(?P<escapes>(?:\\u[0-9a-f]{4})+)",'
    r"  # gid (?P<gid>\d+|\?) \((?P<sources>[^)]+)\) -> \S+ .*?"
    r"\[progs (?P<progs>\d+), docs (?P<docs>\d+); (?P<signals>[^\]]+)\]$",
    re.MULTILINE,
)
_CONSONANT = "[क-ह]"
# A rakar is the below-form ra: virama then ra. A consonant followed directly by
# ra-then-virama -- with or without a nukta bound to it -- is that same glyph with
# its components the wrong way round, which is what the GSUB swap fixes and what
# `reorder_devanagari` would then turn into a different word.
_INVERTED_RAKAR = re.compile(f"{_CONSONANT}़?र्")


def _build_font(
    *,
    units_per_em: int = kalimati_reference.REFERENCE_UNITS_PER_EM,
    glyph_count: int = 24,
    blank_gids: frozenset[int] = frozenset(),
) -> TTFont:
    """A minimal TrueType font, one distinct triangle per glyph.

    Distinct outlines mean no two glyphs hash alike, so a test can pin a table to
    this font's own digests and know which glyph each lookup found. Compiled and
    read back so ``maxp.numGlyphs`` is populated.
    """

    glyph_names = [".notdef"] + [f"g{index}" for index in range(1, glyph_count)]
    builder = FontBuilder(units_per_em, isTTF=True)
    builder.setupGlyphOrder(glyph_names)
    builder.setupCharacterMap({})
    glyphs = {}
    for index, name in enumerate(glyph_names):
        pen = TTGlyphPen(None)
        if index not in blank_gids:
            origin = index * 13
            pen.moveTo((origin, 0))
            pen.lineTo((origin + 37, 0))
            pen.lineTo((origin, 61))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyph_names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Mangal", "styleName": "Regular"})
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
        digest = mangal_reference.outline_digest(font, glyph_order[gid])
        assert digest is not None, f"gid {gid} has no outline to key on"
        table[digest] = value
    return table


# --------------------------------------------------------------------------
# The shipped table
# --------------------------------------------------------------------------


def test_table_is_keyed_on_truncated_outline_digests() -> None:
    assert mangal_reference.OUTLINE_TO_UNICODE
    for digest in mangal_reference.OUTLINE_TO_UNICODE:
        assert re.fullmatch(r"[0-9a-f]{16}", digest), digest


def test_table_values_are_devanagari_only() -> None:
    """A Latin value would mean the derivation was seeded from an unrestricted cmap.

    Mangal carries Latin glyphs, and several fonts in this corpus map ASCII to
    Devanagari glyphs, so an unrestricted seed records a Devanagari outline as
    ``3`` or ``k``. The seed is restricted to the Devanagari block; this is that
    restriction, asserted on the artifact rather than trusted in the generator.
    """

    for digest, value in mangal_reference.OUTLINE_TO_UNICODE.items():
        assert value, digest
        for char in value:
            assert 0x0900 <= ord(char) <= 0x097F, (digest, value, hex(ord(char)))


@pytest.mark.parametrize(
    "expected",
    [
        "क",  # a plain consonant
        "ि",  # the pre-base i-matra, the mis-mapped class that sets matra_damage
        "०",  # Devanagari zero
        "र्",  # the repha, the mis-mapped class that sets repha_loss
        "्र",  # the rakar
        "क्ष",
        "प्र",
        "त्र",
        "ज्ञ",
        "ष्ट्र",
        "म्",  # a half-form
        "स्",
    ],
)
def test_table_carries_the_load_bearing_clusters(expected: str) -> None:
    """The classes GSUB encodes are exactly what the stripped subsets are missing."""

    assert expected in set(mangal_reference.OUTLINE_TO_UNICODE.values())


def test_table_has_no_inverted_rakar() -> None:
    """No value orders a below-form ra as ra-then-virama.

    ``_analyze_gsub`` gives every ``rphf`` output the repha value, and a rakar
    reached that way would be handed out as a repha:
    ``kalimati.reorder_devanagari`` moves a repha to the front of its cluster, so
    ``प्र`` would come back as ``र्प`` -- not a hole but a different word.
    """

    offenders = {
        digest: value
        for digest, value in mangal_reference.OUTLINE_TO_UNICODE.items()
        if _INVERTED_RAKAR.search(value)
    }
    assert not offenders, offenders


def test_table_keeps_trailing_half_forms_intact() -> None:
    """``श्र्`` is a half-form awaiting its next consonant, not a misordered rakar.

    Guards the swap against over-reaching: these must survive untouched.
    """

    values = set(mangal_reference.OUTLINE_TO_UNICODE.values())
    assert "श्र्" in values
    assert "त्र्" in values


def test_the_two_reference_tables_share_no_outline() -> None:
    """Kalimati's table and Mangal's must be disjoint, keyed on the drawing.

    ``kalimati._reference_correction_map`` reads both and lets Kalimati's answer
    win a collision. That precedence is a formality only while the key sets are
    disjoint, which is what makes "the drawing decides which family answers"
    true. If a future entry collided, this failing is how it would be noticed
    rather than the collision being resolved silently in the caller.
    """

    shared = set(mangal_reference.OUTLINE_TO_UNICODE) & set(
        kalimati_reference.OUTLINE_TO_UNICODE
    )
    assert not shared, shared


def test_shipped_and_uncorroborated_are_disjoint() -> None:
    """Nothing may be both shipped and withheld."""

    shared = set(mangal_reference.OUTLINE_TO_UNICODE) & set(
        mangal_reference.UNCORROBORATED_OUTLINE_TO_UNICODE
    )
    assert not shared, shared


def test_uncorroborated_entries_are_never_looked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The residue is a record, not a fallback.

    Keyed on the fixture's own digests so the withheld entry WOULD match if the
    lookup consulted it -- otherwise this would pass whether it did or not.
    """

    font = _build_font()
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "क"})
    )
    monkeypatch.setattr(
        mangal_reference,
        "UNCORROBORATED_OUTLINE_TO_UNICODE",
        _digests_for(font, {5: "ख"}),
    )

    assert mangal_reference.mangal_reference_map(font) == {3: "क"}


def test_resolved_conflicts_all_ship() -> None:
    """A recorded resolution must name an outline the table actually carries."""

    assert mangal_reference.RESOLVED_CONFLICTS
    for digest, rule in mangal_reference.RESOLVED_CONFLICTS.items():
        assert digest in mangal_reference.OUTLINE_TO_UNICODE, digest
        assert rule in {"R1-nfc-cmap", "R2-uniqueness", "R3-below-baseline"}, rule


def test_the_below_form_ra_resolved_to_virama_then_ra() -> None:
    """The one outline the subsets disagree about is the rakar, valued ``्र``.

    Named explicitly because getting it the other way round is the single
    highest-cost error the table can make -- see
    :func:`test_table_has_no_inverted_rakar` for what it would do.
    """

    rakar = [
        digest
        for digest, rule in mangal_reference.RESOLVED_CONFLICTS.items()
        if mangal_reference.OUTLINE_TO_UNICODE[digest] == "्र"
    ]
    assert rakar, mangal_reference.RESOLVED_CONFLICTS


# --------------------------------------------------------------------------
# Every entry carries its provenance
# --------------------------------------------------------------------------


def test_every_shipped_entry_records_its_provenance() -> None:
    """The comment on each line is load-bearing, not decoration.

    The corroboration bar is only auditable if the evidence is written down next
    to the entry it justifies, so this pins the shape and the counts. It also
    catches a hand-added entry, which would carry no provenance comment at all.
    """

    parsed = {
        match.group("digest"): match for match in _ENTRY_RE.finditer(_MODULE_SOURCE)
    }
    shipped = set(mangal_reference.OUTLINE_TO_UNICODE)
    missing = shipped - set(parsed)
    assert not missing, sorted(missing)[:10]
    for digest in shipped:
        match = parsed[digest]
        value = "".join(
            chr(int(code, 16))
            for code in re.findall(r"\\u([0-9a-f]{4})", match.group("escapes"))
        )
        assert value == mangal_reference.OUTLINE_TO_UNICODE[digest], digest
        assert int(match.group("progs")) >= 2, digest
        signals = {
            part.split()[0] for part in match.group("signals").split(", ") if part
        }
        assert signals <= {"cmap", "tounicode", "kalimati", "words"}, (digest, signals)
        assert signals, digest


def test_no_shipped_entry_rests_on_a_metric_match_alone() -> None:
    """``inferred`` is a five-candidate metric match, never sufficient by itself.

    ``kalimati._infer_mark_variants`` matches a glyph's advance, bearing, contour
    count and bounding box against five candidate marks and takes the nearest
    within a fixed distance. That is a guess. Every entry whose derivation used it
    must also have been derived from the font's ``cmap`` or its ``GSUB``.
    """

    for match in _ENTRY_RE.finditer(_MODULE_SOURCE):
        sources = set(match.group("sources").split("+"))
        if "inferred" in sources:
            assert sources & {"cmap", "gsub"}, (match.group("digest"), sources)


# --------------------------------------------------------------------------
# The in-line half-form of ra
# --------------------------------------------------------------------------


def test_in_line_ra_digests_are_valued_ra_plus_virama() -> None:
    assert mangal_reference.IN_LINE_RA_DIGESTS
    for digest in mangal_reference.IN_LINE_RA_DIGESTS:
        assert mangal_reference.OUTLINE_TO_UNICODE[digest] == "र्", digest


def test_in_line_ra_cids_reports_only_the_exempt_outlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font = _build_font()
    monkeypatch.setattr(
        mangal_reference,
        "IN_LINE_RA_DIGESTS",
        frozenset(_digests_for(font, {7: "र्"})),
    )

    assert mangal_reference.mangal_in_line_ra_cids(font, {5, 7, 9}) == {7}
    assert mangal_reference.mangal_in_line_ra_cids(font, {5, 9}) == set()


def test_the_in_line_ra_is_exempt_from_the_repha_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repha gets the reordering marker; the in-line half-form must not.

    Both decode to ``ra + virama``, so the value cannot tell them apart --
    geometry does, and this is the consequence. Marking the in-line form moves it
    to the front of its cluster and turns ``प्र`` into ``र्प``.
    """

    font = _build_font()
    monkeypatch.setattr(
        mangal_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {3: "र्", 7: "र्"}),
    )
    monkeypatch.setattr(
        mangal_reference,
        "IN_LINE_RA_DIGESTS",
        frozenset(_digests_for(font, {7: "र्"})),
    )

    assert kalimati._mangal_reference_map(font) == {
        3: kalimati._PUA_REPH,
        7: "र्",
    }


# --------------------------------------------------------------------------
# Looking a font up
# --------------------------------------------------------------------------


def test_outline_digest_is_the_one_the_keys_were_computed_with() -> None:
    """The lookup must hash exactly as the table's keys were hashed.

    ``mangal_reference`` imports the helper from ``kalimati_reference`` rather
    than duplicating it, because a silent divergence would invalidate every
    lookup here. Same for the em-square precondition, which is a property of the
    digest rather than of either table. This pins both imports, so replacing
    either with a copy fails -- and it is also why the em square is stated once,
    in ``kalimati_reference.REFERENCE_UNITS_PER_EM``.
    """

    assert mangal_reference.outline_digest is kalimati_reference.outline_digest
    assert (
        mangal_reference._has_reference_units_per_em
        is kalimati_reference._has_reference_units_per_em
    )
    assert not hasattr(mangal_reference, "REFERENCE_UNITS_PER_EM")


def test_reference_map_recovers_glyphs_by_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font = _build_font()
    monkeypatch.setattr(
        mangal_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {3: "क्ष", 5: "प्र"}),
    )

    assert mangal_reference.mangal_reference_map(font) == {3: "क्ष", 5: "प्र"}


def test_reference_map_skips_gids_the_caller_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The font's own cmap and GSUB beat a reference, so the caller's answers win."""

    font = _build_font()
    monkeypatch.setattr(
        mangal_reference,
        "OUTLINE_TO_UNICODE",
        _digests_for(font, {3: "क्ष", 5: "प्र"}),
    )

    assert mangal_reference.mangal_reference_map(font, skip={3}) == {5: "प्र"}


def test_reference_map_is_empty_for_an_unrecognised_font() -> None:
    """Synthetic triangles are not Mangal drawings, so nothing is claimed."""

    assert mangal_reference.mangal_reference_map(_build_font()) == {}


def test_reference_map_refuses_another_em_square(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A digest is over raw font units, so it only means anything at 2048.

    The corpus really does carry Mangal embeds at 1000 units per em, so this is a
    live gate. Keyed on the rescaled font's OWN digests, so the em square is the
    only thing standing between it and a match.
    """

    rescaled = _build_font(units_per_em=1000)
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(rescaled, {3: "क्ष"})
    )

    assert mangal_reference.mangal_reference_map(rescaled) == {}
    assert mangal_reference.mangal_in_line_ra_cids(rescaled, {3}) == set()


def test_reference_map_ignores_glyphs_the_subsetter_blanked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blanked glyph has no outline, so it is absence of evidence, not a match."""

    font = _build_font()
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "क्ष"})
    )

    blanked = _build_font(blank_gids=frozenset({3}))
    assert mangal_reference.mangal_reference_map(blanked) == {}


def test_reference_map_survives_a_font_without_glyph_outlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``glyf`` table means no digests, not an exception."""

    font = _build_font()
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "क"})
    )
    del font["glyf"]
    assert mangal_reference.mangal_reference_map(font) == {}
    assert mangal_reference.mangal_in_line_ra_cids(font, {3}) == set()


def test_reference_map_survives_a_font_without_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown geometry means unknown em square, so claim nothing."""

    font = _build_font()
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "क"})
    )
    del font["head"]
    assert mangal_reference.mangal_reference_map(font) == {}


# --------------------------------------------------------------------------
# The combined lookup in kalimati.py
# --------------------------------------------------------------------------


def test_combined_reference_map_reads_both_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document can embed a Kalimati face and a Mangal face; both get answered."""

    font = _build_font()
    monkeypatch.setattr(
        kalimati_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "क्ष"})
    )
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {5: "प्र"})
    )

    assert kalimati._reference_correction_map(font) == {3: "क्ष", 5: "प्र"}


def test_combined_reference_map_honours_the_callers_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``skip`` must reach BOTH tables, not just the one asked first."""

    font = _build_font()
    monkeypatch.setattr(
        kalimati_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "क्ष"})
    )
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {5: "प्र"})
    )

    assert kalimati._reference_correction_map(font, skip={5}) == {3: "क्ष"}
    assert kalimati._reference_correction_map(font, skip={3}) == {5: "प्र"}


def test_combined_reference_map_lets_kalimati_win_a_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the documented precedence, which the disjointness test keeps academic.

    Constructed, because the shipped tables do not collide -- the point is that
    if they ever did, the older reviewed table would answer rather than the
    result depending on dict ordering.
    """

    font = _build_font()
    collision = _digests_for(font, {3: "क्ष"})
    monkeypatch.setattr(kalimati_reference, "OUTLINE_TO_UNICODE", collision)
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "प्र"})
    )

    assert kalimati._reference_correction_map(font) == {3: "क्ष"}


def test_combined_reference_map_applies_markers_from_both_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reordering markers are what make a reference value usable downstream.

    Both halves must mark: an i-matra from either table has to become the
    reordering marker, or ``प्रादेशिक`` ships as ``प्रादेिशक``.
    """

    font = _build_font()
    monkeypatch.setattr(
        kalimati_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {3: "ि"})
    )
    monkeypatch.setattr(
        mangal_reference, "OUTLINE_TO_UNICODE", _digests_for(font, {5: "ि"})
    )

    assert kalimati._reference_correction_map(font) == {
        3: kalimati._PUA_IKAR,
        5: kalimati._PUA_IKAR,
    }
