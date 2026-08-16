"""VOL-705: a rakar the PDF's own CMap inverted must be corrected, not preserved.

A rakar -- the below-form ra, the stroke under `त` in `त्र` -- orders virama then
ra. The reverse order is a repha bound to the base consonant and reads as a
different word: `मन्तर्ालय` for `मन्त्रालय` (*ministry*), `पर्ति` for `प्रति`.

`kalimati._patch_single_cmap` compares the PDF's `/ToUnicode` value against the
value derived from the embedded font program and skips any difference
`_is_ra_virama_swap` calls a transposition. That predicate used to answer True in
both directions, so the inverted-in-the-PDF case was skipped too and the wrong
order survived into the Markdown.

Measured over the 13 CIAA annual reports (run 384bcc86, artifacts under
`ciaa-transcript-quality/_audit/runs/vol705-384bcc86/`): the direction fixed here
fires 10 times in the 33rd report, over 8 distinct GIDs (क्र ट्र त्र द्र प्र भ्र
श्र ह्र) across two embedded Kalimati subsets (xref 2469 and 2490), and once in the
32nd -- leaving 169 structurally invalid `[consonant] र ् [matra]` sequences in the
published 33rd. The opposite direction fires on none, in any report.

The 11 tests asserting the fixed direction fail on the pre-fix code; the 4
retained-direction controls -- `test_swap_predicate_still_defends_a_correct_rakar`,
`test_a_correct_pdf_rakar_survives_the_patcher`,
`test_a_genuine_repha_still_becomes_a_reordering_marker` and
`test_a_correct_rakar_is_not_a_meaningful_difference` -- pass both ways, by design.
Restoring the deleted branch gives exactly `11 failed, 4 passed`. A blanket "every
test here fails" would be the weaker claim as well as the false one: the four that
hold in both arms are what make this a directional bite rather than an assertion
that the predicate was simply switched off.
"""

from __future__ import annotations

import pytest

from likhit.extractors import kalimati

_RAKAR = kalimati._VIRAMA + kalimati._RA  # ्र -- correct below-form ra
_REPHA = kalimati._RA + kalimati._VIRAMA  # र् -- a repha, the inverted order

# The eight rakar clusters the CIAA 33rd report's embedded Kalimati subsets
# carry inverted, keyed by the GID that carries them. `pdf` is what the PDF's
# own /ToUnicode says; `font` is what the font program's GSUB says.
_MEASURED_GIDS = {
    272: ("कर्", "क्र"),
    282: ("टर्", "ट्र"),
    287: ("तर्", "त्र"),
    289: ("दर्", "द्र"),
    292: ("पर्", "प्र"),
    295: ("भर्", "भ्र"),
    302: ("शर्", "श्र"),
    305: ("हर्", "ह्र"),
}


class _FakeDoc:
    """Just enough of `fitz.Document` for `_patch_single_cmap`.

    The CMap round-trip is real: the stream handed in is built by
    `_build_cmap_stream` and the stream written back out is read with
    `_parse_tounicode_cmap`, so a test cannot pass by agreeing with itself about
    an intermediate dict.
    """

    def __init__(self, mapping: dict[int, str]) -> None:
        self._stream = kalimati._build_cmap_stream(mapping)
        self.written: bytes | None = None

    def xref_stream(self, _xref: int) -> bytes:
        return self._stream

    def update_stream(self, _xref: int, data: bytes) -> None:
        self.written = data


def _patched(pdf_map: dict[int, str], correction: dict[int, str]) -> dict[int, str]:
    doc = _FakeDoc(pdf_map)
    kalimati._patch_single_cmap(doc, 1, correction)  # type: ignore[arg-type]
    assert doc.written is not None, "_patch_single_cmap wrote no CMap at all"
    return kalimati._parse_tounicode_cmap(doc.written)


# ---------------------------------------------------------------------------
# The predicate, both directions
# ---------------------------------------------------------------------------


def test_swap_predicate_does_not_defend_an_inverted_rakar() -> None:
    """PDF `तर्` against font `त्र` is a defect to fix, not a transposition to keep."""

    assert kalimati._is_ra_virama_swap("तर्", "त्र") is False


def test_swap_predicate_still_defends_a_correct_rakar() -> None:
    """The retained direction: PDF `त्र` against font `तर्` keeps the PDF's value.

    `_analyze_gsub` reaching a rakar through a ligature rule derives ra-then-virama
    and has to swap it back. If that ever misses for some subset, the PDF's own
    correct value is the better of the two.
    """

    assert kalimati._is_ra_virama_swap("त्र", "तर्") is True


# ---------------------------------------------------------------------------
# The patcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gid", sorted(_MEASURED_GIDS))
def test_every_measured_gid_is_corrected(gid: int) -> None:
    """Each of the eight clusters the 33rd report inverted comes out as a rakar."""

    pdf_value, font_value = _MEASURED_GIDS[gid]
    assert _patched({gid: pdf_value}, {gid: font_value})[gid] == font_value


def test_the_ministry_token_decodes_correctly() -> None:
    """`मन्तर्ालय` -> `मन्त्रालय`, the 106-occurrence token, through the real CMap.

    The glyph run is म, न्, <rakar-त्र>, ा, ल, य. Only the third glyph's mapping
    is in question; the rest are spelled out so the assertion is on a word a
    reader can check, not on a dict entry.
    """

    run = [1, 2, 287, 3, 4, 5]
    pdf_map = {1: "म", 2: "न्", 287: "तर्", 3: "ा", 4: "ल", 5: "य"}
    correction = {287: "त्र"}

    before = "".join(pdf_map[gid] for gid in run)
    assert before == "मन्तर्ालय", "the pre-fix spelling this issue reported"

    patched = _patched(pdf_map, correction)
    after = "".join(patched[gid] for gid in run)
    assert after == "मन्त्रालय"


def test_a_correct_pdf_rakar_survives_the_patcher() -> None:
    """The other direction, end to end: a PDF that already had it right is left alone."""

    patched = _patched({287: "त्र"}, {287: "तर्"})
    assert patched[287] == "त्र"


def test_a_genuine_repha_still_becomes_a_reordering_marker() -> None:
    """A bare repha is not a rakar and must keep its PUA sentinel.

    Guards the fix against over-reach: `_patch_single_cmap`'s
    `correct_value == _RA + _VIRAMA` branch hands out `_PUA_REPH` so
    `reorder_devanagari` can move it in front of its cluster. Narrowing the swap
    predicate must not divert a repha into that path or out of it.
    """

    assert _patched({9: "र"}, {9: _REPHA})[9] == kalimati._PUA_REPH


# ---------------------------------------------------------------------------
# The gate that let it ship
# ---------------------------------------------------------------------------


def test_an_inverted_rakar_counts_as_a_meaningful_cmap_difference() -> None:
    """`_meaningful_cmap_diff_count` must see it, or the font is never repaired.

    A non-Kalimati-named font needs 3 meaningful differences before
    `fix_kalimati_cmap` will patch it at all (`kalimati.py`, the
    `meaningful_diffs < 3` gate). While the swap predicate answered True in both
    directions, a font whose only damage was inverted rakars scored 0 and was
    skipped outright.
    """

    pdf_map = {gid: pair[0] for gid, pair in _MEASURED_GIDS.items()}
    correction = {gid: pair[1] for gid, pair in _MEASURED_GIDS.items()}

    assert kalimati._meaningful_cmap_diff_count(pdf_map, correction) == len(
        _MEASURED_GIDS
    )


def test_a_correct_rakar_is_not_a_meaningful_difference() -> None:
    """The retained direction stays uncounted, so it cannot trip the gate by itself."""

    assert kalimati._meaningful_cmap_diff_count({287: "त्र"}, {287: "तर्"}) == 0
