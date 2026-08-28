"""Broken-CMap candidacy read from the embedded ``name`` table.

:func:`classify_font` can only see the PDF *resource* name, and a producer is free to
make that name say nothing about the font: this corpus embeds Kalimati under
``CIDFont+F2``. The family match is therefore repeated against the embedded ``name``
table, exactly as :mod:`likhit.extractors.font_classifier` already does for the legacy
registry in :func:`resolve_embedded_legacy_maps`.

**Why this is load-bearing rather than cosmetic.**
:func:`~likhit.nepali_pdf_repair.extract_repaired_text_blocks` gates the entire CMap
repair on *some* font classifying ``broken_cmap``. A document whose Kalimati face is
anonymous therefore never reached ``fix_kalimati_cmap`` at all -- not a weaker repair,
no repair. Measured over the 97 documents the published v1.3 audit fails on
``repha_loss``: 7 trip the resource-name gate and **71 embed a Kalimati face under a
name that does not say so**. On six of those, resolving the embedded name takes
``repha_corrupt`` from 6-16 to **0** and repha purity to **1.000**, and recovers every
canonical OAG word from zero occurrences -- ``बेरुजु`` 0 -> 17/21/18, ``कार्यालय``
0 -> 38/40/42.

The fixtures build genuine TrueType programs rather than faking ``fontTools``, because
what is under test is a property of a real ``name`` table.
"""

from __future__ import annotations

import io

from likhit.extractors.font_classifier import (
    classify_font,
    resolve_embedded_broken_cmap,
    scan_pdf_fonts,
    scan_pdf_fonts_by_page,
)


def _ttf(family: str, ps_name: str | None = None) -> bytes:
    """A minimal but genuine TrueType program carrying the given name records."""

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({0x41: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 100))
    pen.lineTo((100, 100))
    pen.closePath()
    glyph = pen.glyph()
    builder.setupGlyf({".notdef": glyph, "A": glyph})
    builder.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "psName": ps_name or family.replace(" ", ""),
        }
    )
    builder.setupOS2()
    builder.setupPost()
    buf = io.BytesIO()
    builder.save(buf)
    return buf.getvalue()


class _Page:
    def __init__(self, fonts: list[tuple]) -> None:
        self._fonts = fonts

    def get_fonts(self, full: bool = False) -> list[tuple]:
        del full
        return self._fonts


class _Doc:
    """Enough of a ``fitz.Document`` for the resolver: pages, fonts, extract_font."""

    def __init__(self, pages: list[list[tuple]], programs: dict[int, bytes]) -> None:
        self._pages = [_Page(f) for f in pages]
        self._programs = programs
        self.page_count = len(self._pages)
        self.extract_calls: list[int] = []

    def __getitem__(self, index: int) -> _Page:
        return self._pages[index]

    def extract_font(self, xref: int, named: bool = False) -> dict:
        del named
        self.extract_calls.append(xref)
        return {"content": self._programs.get(xref)}


def _font_info(xref: int, base_font: str, ext: str = "ttf") -> tuple:
    # (xref, ext, type, basefont, refname, encoding) -- page.get_fonts(full=True)
    return (xref, ext, "TrueType", base_font, f"F{xref}", "")


# --------------------------------------------------------------------------- #
# the resolver
# --------------------------------------------------------------------------- #


def test_anonymous_resource_name_hiding_kalimati_is_resolved() -> None:
    """The corpus case: the resource name says nothing, the program says Kalimati."""

    doc = _Doc([[_font_info(11, "CIDFont+F2")]], {11: _ttf("Kalimati")})

    # The premise, asserted so this cannot pass because the resource name leaked the
    # family after all.
    assert classify_font("CIDFont+F2", "TrueType") == "correct"

    assert resolve_embedded_broken_cmap(doc) == {"F2": "kalimati"}


def test_the_repair_gate_sees_the_resolved_font() -> None:
    """What `extract_repaired_text_blocks` actually reads must say broken_cmap.

    The resolver returning the right binding is not the claim that matters -- the
    claim is that `scan_pdf_fonts`, whose values that gate scans, reports it. This is
    the assertion that would have caught the defect.
    """

    doc = _Doc([[_font_info(11, "CIDFont+F2")]], {11: _ttf("Kalimati")})

    assert scan_pdf_fonts(doc) == {"F2": "broken_cmap"}
    assert scan_pdf_fonts_by_page(doc) == {1: {"F2": "broken_cmap"}}


def test_lohit_is_resolved_too_not_just_kalimati() -> None:
    """Both members of the family set, so the rule is not one hard-coded name."""

    doc = _Doc([[_font_info(12, "CIDFont+F7")]], {12: _ttf("Lohit Devanagari")})

    assert resolve_embedded_broken_cmap(doc) == {"F7": "lohit"}


def test_an_unrelated_embedded_family_stays_correct() -> None:
    """A real embedded name that is not one of these families is not a hit.

    Without this the resolver could return every embedded font it can read and the
    tests above would still pass.
    """

    doc = _Doc([[_font_info(13, "CIDFont+F3")]], {13: _ttf("Cambria")})

    assert resolve_embedded_broken_cmap(doc) == {}
    assert scan_pdf_fonts(doc) == {"F3": "correct"}


# --------------------------------------------------------------------------- #
# what must NOT change
# --------------------------------------------------------------------------- #


def test_a_legacy_font_is_not_downgraded_by_its_embedded_name() -> None:
    """`legacy_remap` outranks `broken_cmap` and must survive the new binding.

    A Preeti resource whose subsetted program happens to carry a Kalimati name record
    decodes by keystroke map, not by CMap repair. Routing it to `broken_cmap` would
    silently replace a working decode path with the wrong one.
    """

    doc = _Doc([[_font_info(14, "ABCDEF+Preeti")]], {14: _ttf("Kalimati")})

    assert classify_font("ABCDEF+Preeti", "TrueType") == "legacy_remap"
    assert resolve_embedded_broken_cmap(doc) == {}
    assert scan_pdf_fonts(doc) == {"Preeti": "legacy_remap"}
    assert scan_pdf_fonts_by_page(doc) == {1: {"Preeti": "legacy_remap"}}


def test_a_font_whose_resource_name_already_says_kalimati_is_not_probed() -> None:
    """It is already `broken_cmap`; extracting its program buys nothing."""

    doc = _Doc([[_font_info(15, "ABCDEF+Kalimati")]], {15: _ttf("Kalimati")})

    assert resolve_embedded_broken_cmap(doc) == {}
    assert doc.extract_calls == []
    assert scan_pdf_fonts(doc) == {"Kalimati": "broken_cmap"}


def test_a_bare_core_font_is_not_probed() -> None:
    """No embedded program means no name table to ask, and no extract_font call."""

    doc = _Doc([[_font_info(16, "Helvetica", ext="n/a")]], {})

    assert resolve_embedded_broken_cmap(doc) == {}
    assert doc.extract_calls == []


def test_an_unreadable_program_is_no_evidence_rather_than_an_error() -> None:
    """A broken embed must leave the font `correct`, not raise out of the scan."""

    doc = _Doc([[_font_info(17, "CIDFont+F9")]], {17: b"not a font at all"})

    assert resolve_embedded_broken_cmap(doc) == {}
    assert scan_pdf_fonts(doc) == {"F9": "correct"}


# --------------------------------------------------------------------------- #
# cost and scope
# --------------------------------------------------------------------------- #


def test_a_font_that_never_matches_is_still_extracted_only_once() -> None:
    """The probe is per xref, not per page-font occurrence.

    A 200-page report referencing one font on every page must pay for one
    `extract_font`, not 200. The xref cache is what makes resolving inside
    `scan_pdf_fonts` affordable enough to be the default.

    ⚠️ The font here deliberately does NOT match. An earlier version of this test
    used a Kalimati program and was **vacuous**: a matching font enters `resolved`
    on the first page and every later page short-circuits on the `base in resolved`
    check, so `extract_font` is never reached a second time whether the cache exists
    or not -- deleting the cache left the test green. Only a font that never enters
    `resolved` re-reaches the probe, which is exactly the case the cache exists for.
    """

    pages = [[_font_info(13, "CIDFont+F3")] for _ in range(25)]
    doc = _Doc(pages, {13: _ttf("Cambria")})

    assert resolve_embedded_broken_cmap(doc) == {}
    assert doc.extract_calls == [13]


def test_a_matching_font_is_extracted_once_by_the_resolved_short_circuit() -> None:
    """The other half of the cost claim, and it is a different mechanism.

    Held by `base in resolved` rather than by the xref cache. Asserted separately so
    that neither guard can be removed while the cost claim still looks covered.
    """

    pages = [[_font_info(11, "CIDFont+F2")] for _ in range(25)]
    doc = _Doc(pages, {11: _ttf("Kalimati")})

    assert resolve_embedded_broken_cmap(doc) == {"F2": "kalimati"}
    assert doc.extract_calls == [11]


def test_a_precomputed_binding_is_used_and_suppresses_the_probe() -> None:
    """Callers that already resolved it must not pay twice."""

    doc = _Doc([[_font_info(11, "CIDFont+F2")]], {11: _ttf("Kalimati")})

    assert scan_pdf_fonts(doc, embedded_broken_cmap={}) == {"F2": "correct"}
    assert doc.extract_calls == []
