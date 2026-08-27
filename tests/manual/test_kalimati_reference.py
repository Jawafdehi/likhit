"""Manual provenance checks against the unvendored Kalimati reference font."""

import os
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from likhit.extractors import kalimati, kalimati_reference


@pytest.fixture(scope="module")
def reference_font() -> TTFont:
    raw = os.environ.get("LIKHIT_KALIMATI_REFERENCE_TTF")
    assert raw, (
        "set LIKHIT_KALIMATI_REFERENCE_TTF to the reference Kalimati TTF "
        "before running this module"
    )
    path = Path(raw)
    assert path.is_file(), f"LIKHIT_KALIMATI_REFERENCE_TTF is not a file: {path}"
    return TTFont(path, lazy=False)


def test_table_re_derives_from_the_reference_font(reference_font: TTFont) -> None:
    """The shipped table is exactly what the derivation produces."""

    glyph_order = reference_font.getGlyphOrder()
    best_cmap = kalimati._safe_get_best_cmap(reference_font)
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
        kalimati._infer_mark_variants(
            reference_font,
            glyph_order,
            gid_to_correct,
        )
    )
    derived = kalimati._analyze_gsub(
        reference_font,
        glyph_order,
        gid_to_correct,
    )
    full = dict(derived)
    full.update(gid_to_correct)

    glyf = reference_font["glyf"]

    def is_below_form(glyph_name: str) -> bool:
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
        digest = kalimati_reference.outline_digest(
            reference_font,
            glyph_order[gid],
        )
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


def test_reference_font_matches_the_declared_identity(
    reference_font: TTFont,
) -> None:
    assert (
        reference_font["head"].unitsPerEm == kalimati_reference.REFERENCE_UNITS_PER_EM
    )
    assert reference_font["maxp"].numGlyphs == kalimati_reference.REFERENCE_GLYPH_COUNT
    assert "GSUB" in reference_font, "the reference is only useful because it kept GSUB"
