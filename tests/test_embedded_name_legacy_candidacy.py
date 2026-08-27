"""Name-path candidacy read from the embedded ``name`` table (VOL-614 item 2).

The PDF resource name is producer-assigned metadata: OAG's reports carry Preeti,
Rukmini and Spins programs under names their subsetter invented (``CIDFont+F1``,
``CIDFont+F2``, ``Shangrila Hybrid``). The embedded font program's ``name`` table is
the font's own claim about itself, so asking the *same* registry predicate about
*that* string reaches faces the resource name cannot.

**The predicate is unchanged and that is the point.** These tests call
:func:`match_legacy_map_name`, which is ``_match_font`` -- the very function
``get_converter`` uses. Nothing about candidacy is re-implemented; only the string
being matched is better.

**🛑 Per-document binding, not per-font-name.** In this corpus ``CIDFont+F1`` binds
three different families across 16 documents. An instrument that probes one document
per font name and multiplies is wrong by construction, and that shortcut is what put
an earlier estimate of this mechanism's reach at 807 spans against a measured 534
(VOL-610). :func:`resolve_embedded_legacy_maps` therefore takes a document.

The fixtures below build real TrueType programs rather than faking ``fontTools``,
because the ordering rule under test is a property of a genuine ``name`` table.
"""

from __future__ import annotations

import io

import pytest

from likhit.extractors.font_based import (
    FontBasedStrategy,
    detect_content_legacy_fonts,
)
from likhit.extractors.font_classifier import (
    _embedded_name_candidates,
    classify_font,
    resolve_embedded_legacy_maps,
    scan_pdf_fonts_by_page,
)
from likhit.extractors.legacy_maps import get_converter_for_map, match_legacy_map_name


def _ttf(family: str, ps_name: str, typographic: str | None = None) -> bytes:
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
    names = {"familyName": family, "styleName": "Regular", "psName": ps_name}
    if typographic is not None:
        names["typographicFamily"] = typographic
    builder.setupNameTable(names)
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

    def __getitem__(self, index: int) -> _Page:
        return self._pages[index]

    def extract_font(self, xref: int, named: bool = False) -> dict:
        del named
        return {"content": self._programs.get(xref)}

    def close(self) -> None:
        return None


def _font_info(xref: int, base_font: str, ext: str = "ttf") -> tuple:
    # (xref, ext, type, basefont, refname, encoding) -- page.get_fonts(full=True)
    return (xref, ext, "TrueType", base_font, f"F{xref}", "")


# --------------------------------------------------------------------------- #
# the `name` table read
# --------------------------------------------------------------------------- #


def test_name_records_come_back_in_precedence_order_not_table_order() -> None:
    """16, 1, 6, 4 -- regardless of how the font stores its records.

    This is the corpus case that makes the ordering load-bearing rather than
    cosmetic: `2392`'s body font has familyName ``Shangrila Hybrid`` (unregistered)
    and psName ``FONTASYHIMALITTNORMAL`` (registered). A caller that stops at the
    first match must see nameID 1 *before* nameID 6, or the binding is decided by
    table layout instead of by the precedence this module documents.
    """

    program = _ttf("Shangrila Hybrid", "FONTASYHIMALITTNORMAL")
    ids = [name_id for name_id, _ in _embedded_name_candidates(program)]
    assert ids == sorted(ids, key=[16, 1, 6, 4].index)
    assert 1 in ids and 6 in ids
    # And the registry's verdict on each, which is what makes 6 the deciding record.
    by_id = dict(_embedded_name_candidates(program))
    assert match_legacy_map_name(by_id[1]) is None
    assert match_legacy_map_name(by_id[6]) == "FONTASY_HIMALI_TT"


def test_a_typographic_family_outranks_the_family_name() -> None:
    program = _ttf("Some Subset Name", "Whatever", typographic="Preeti")
    first_id, first_value = _embedded_name_candidates(program)[0]
    assert (first_id, first_value) == (16, "Preeti")


def test_unparseable_font_bytes_degrade_to_no_evidence() -> None:
    """A corrupt embed must not fail an extraction that works today."""

    assert _embedded_name_candidates(b"not a font at all") == []
    assert _embedded_name_candidates(b"") == []


# --------------------------------------------------------------------------- #
# candidacy resolution
# --------------------------------------------------------------------------- #


def test_an_unregistered_resource_name_is_reached_through_its_embedded_name() -> None:
    doc = _Doc(
        [[_font_info(7, "CIDFont+F2")]],
        {7: _ttf("Preeti", "Preeti-Regular")},
    )
    assert classify_font("CIDFont+F2", "") == "correct"
    assert resolve_embedded_legacy_maps(doc) == {"F2": "Preeti"}


def test_the_shangrila_case_binds_on_the_postscript_name() -> None:
    """The real `2392` binding, and the digit row is what makes it checkable.

    Preeti and FONTASY_HIMALI_TT agree on every letter slot and differ by having
    the two number rows exchanged, so prose cannot tell them apart -- only numerals
    can. This document's spans are 38 numeral runs and 10 prose runs; under Himali
    the numerals become Devanagari DIGITS, which is right, and under Preeti they
    would become consonants. Asserting the digit row is asserting the discriminator
    rather than restating the binding.
    """

    doc = _Doc(
        [[_font_info(28, "ABCEEE+Shangrila Hybrid")]],
        {28: _ttf("Shangrila Hybrid", "FONTASYHIMALITTNORMAL")},
    )
    assert resolve_embedded_legacy_maps(doc) == {
        "Shangrila Hybrid": "FONTASY_HIMALI_TT"
    }
    himali = get_converter_for_map("FONTASY_HIMALI_TT")
    preeti = get_converter_for_map("Preeti")
    assert himali("142445264") == "१४२४४५२६४"
    assert preeti("142445264") != himali("142445264")


def test_a_resource_name_that_already_matches_is_short_circuited() -> None:
    """No font parse, and no entry: the name path already owns this font.

    ``extract_font`` raises here, so the test fails if the resolver reaches for the
    program at all. That is what pins the short-circuit as behaviour rather than as
    an optimisation comment.
    """

    class Exploding(_Doc):
        def extract_font(self, xref: int, named: bool = False) -> dict:
            raise AssertionError("must not parse an already-routed font")

    doc = Exploding([[_font_info(3, "ABCDEF+Preeti")]], {})
    assert classify_font("ABCDEF+Preeti", "") == "legacy_remap"
    assert resolve_embedded_legacy_maps(doc) == {}


def test_a_non_embedded_font_has_no_self_claim_to_read() -> None:
    """A bare core font is the decoy-layer signature; scan_ocr_pages owns it."""

    for ext in ("n/a", ""):
        doc = _Doc(
            [[_font_info(4, "Helvetica", ext=ext)]], {4: _ttf("Preeti", "Preeti")}
        )
        assert resolve_embedded_legacy_maps(doc) == {}


def test_the_binding_is_per_document_not_per_font_name() -> None:
    """The same resource name, two documents, two different programs.

    This is the failure mode that produced the refuted 807: one probe document per
    font name, multiplied across every span sharing the name.
    """

    preeti_doc = _Doc([[_font_info(1, "CIDFont+F1")]], {1: _ttf("Preeti", "Preeti")})
    himali_doc = _Doc(
        [[_font_info(1, "CIDFont+F1")]],
        {1: _ttf("Fontasy Himali", "FontasyHimali")},
    )
    assert resolve_embedded_legacy_maps(preeti_doc) == {"F1": "Preeti"}
    assert resolve_embedded_legacy_maps(himali_doc) == {"F1": "FONTASY_HIMALI_TT"}


def test_an_embedded_name_that_matches_nothing_yields_nothing() -> None:
    doc = _Doc(
        [[_font_info(9, "CIDFont+F9")]],
        {9: _ttf("Liberation Serif", "LiberationSerif")},
    )
    assert resolve_embedded_legacy_maps(doc) == {}


# --------------------------------------------------------------------------- #
# the wiring: strategy, disjointness, and the decode
# --------------------------------------------------------------------------- #


def test_a_resolved_font_is_routed_legacy_remap() -> None:
    doc = _Doc([[_font_info(2, "CIDFont+F2")]], {2: _ttf("Preeti", "Preeti")})
    embedded = resolve_embedded_legacy_maps(doc)
    assert scan_pdf_fonts_by_page(doc) == {1: {"F2": "correct"}}
    assert scan_pdf_fonts_by_page(doc, embedded) == {1: {"F2": "legacy_remap"}}


def test_without_the_binding_extraction_is_unchanged() -> None:
    """Leaving the argument out must reproduce pre-VOL-614 routing exactly."""

    doc = _Doc([[_font_info(2, "CIDFont+F2")]], {2: _ttf("Preeti", "Preeti")})
    assert scan_pdf_fonts_by_page(doc, None) == {1: {"F2": "correct"}}
    assert scan_pdf_fonts_by_page(doc, {}) == {1: {"F2": "correct"}}


def test_an_embedded_routed_font_decodes_with_the_embedded_map() -> None:
    """`get_converter` returns None for these names -- the map key carries it."""

    from likhit.extractors.legacy_maps import get_converter

    assert get_converter("CIDFont+F2") is None
    text = "dxfn]vfk/LIfssf] sfof{no"
    got = FontBasedStrategy()._convert_span_text(
        text,
        "CIDFont+F2",
        {"F2": "legacy_remap"},
        needs_reorder=False,
        embedded_legacy_maps={"F2": "Preeti"},
    )
    assert got == "महालेखापरीक्षकको कार्यालय"


def test_the_latin_veto_also_guards_the_embedded_converter() -> None:
    """Item 1's guard must cover the path item 2 opens, not only the old one.

    This is why the ordering in VOL-614 is load-bearing: the widening feeds spans
    into exactly the branch the veto protects.

    VOL-634 changed the FIXTURE, not the intent. The span was
    `Rigid Apron u/s12.15*1.9*.5 and`, which the name path no longer suppresses now that
    the veto is a conjunction with `_reads_as_latin_text` -- see
    `test_name_path_latin_veto.py`, where that span's acceptance line is re-vehicled onto
    the content path it actually lives on. This test is about the embedded-converter ARM
    being guarded at all, so it needs any span the guard suppresses; it is not about that
    span. `Serving the Nation and the ` is one of the 16 the conjunction still suppresses,
    and it is genuinely name-routed (11145 p3, resource font `Preeti`).

    🛑 The choice of span is load-bearing for a reason worth stating: it must be one the
    conjunction suppresses, i.e. certified by BOTH predicates. A span certified by only
    the word test would pass here even if this arm consulted nothing, because the arm's
    fallthrough also returns raw text -- so it would be a vacuous green.
    """

    latin = "Serving the Nation and the "
    got = FontBasedStrategy()._convert_span_text(
        latin,
        "CIDFont+F2",
        {"F2": "legacy_remap"},
        needs_reorder=False,
        embedded_legacy_maps={"F2": "Preeti"},
    )
    assert got == latin
    # Pin that the arm really did have a converter to decline, so a regression that
    # silently loses the embedded binding fails here rather than reading as a pass.
    unguarded = FontBasedStrategy()._convert_span_text(
        "cbfnt sf/afxL a/fdb",
        "CIDFont+F2",
        {"F2": "legacy_remap"},
        needs_reorder=False,
        embedded_legacy_maps={"F2": "Preeti"},
    )
    assert unguarded == "अदालत कारबाही बरामद"


def test_an_embedded_routed_font_is_excluded_from_content_candidacy() -> None:
    """Exactly one path must own a font, or the widening measures as reaching nothing.

    Without the exclusion the content branch in `_convert_span_text` returns first
    and shadows the name path. The aggregate below is real Preeti keystrokes, so it
    WOULD be a content candidate -- which is what makes the assertion meaningful.
    """

    keystrokes = "cbfnt sf/afxL a/fdb bfo/ dlxnf tyf jfnjflnsf " * 3
    page_dict = {
        "blocks": [{"lines": [{"spans": [{"font": "CIDFont+F2", "text": keystrokes}]}]}]
    }

    class Doc:
        page_count = 1

        def __getitem__(self, _index: int) -> object:
            return object()

    import likhit.extractors.font_based as font_based_module

    original = font_based_module.get_cid_marked_page_dict
    font_based_module.get_cid_marked_page_dict = lambda _page: page_dict
    try:
        without = detect_content_legacy_fonts(Doc())  # type: ignore[arg-type]
        with_binding = detect_content_legacy_fonts(
            Doc(),  # type: ignore[arg-type]
            frozenset(),
            {"F2": "Preeti"},
        )
    finally:
        font_based_module.get_cid_marked_page_dict = original

    assert set(without) == {"CIDFont+F2"}, "the font is a content candidate today"
    assert with_binding == {}, "and must stop being one once the name path owns it"


@pytest.mark.parametrize("binding", [None, {}])
def test_content_candidacy_is_untouched_when_nothing_was_resolved(binding) -> None:
    keystrokes = "cbfnt sf/afxL a/fdb bfo/ dlxnf tyf jfnjflnsf " * 3
    page_dict = {
        "blocks": [{"lines": [{"spans": [{"font": "CIDFont+F2", "text": keystrokes}]}]}]
    }

    class Doc:
        page_count = 1

        def __getitem__(self, _index: int) -> object:
            return object()

    import likhit.extractors.font_based as font_based_module

    original = font_based_module.get_cid_marked_page_dict
    font_based_module.get_cid_marked_page_dict = lambda _page: page_dict
    try:
        detected = detect_content_legacy_fonts(
            Doc(),  # type: ignore[arg-type]
            frozenset(),
            binding,
        )
    finally:
        font_based_module.get_cid_marked_page_dict = original
    assert set(detected) == {"CIDFont+F2"}


def test_the_binding_reaches_the_span_conversion_end_to_end(monkeypatch) -> None:
    """🛑 The regression test for the defect the paired control caught.

    Unit tests passed `embedded_legacy_maps` to `_convert_span_text` by hand, and the
    broken-cmap doubles asserted it reached `scan_pdf_fonts_by_page` and
    `detect_content_legacy_fonts`. Nothing exercised the chain
    `_extract_raw_document` -> `_extract_from_document` -> `_convert_span_text`, and
    all three `_extract_from_document` call sites were in fact NOT passing it. The
    binding defaulted to `None`, so an embedded-routed font found no converter on the
    name path -- while having been excluded from content candidacy by the same
    change. It therefore decoded NOWHERE.

    Measured cost of that hole before the fix, over the 285 documents this change can
    touch: 121 of them changed, every one `DEVANAGARI_REVERTED_TO_ASCII`, for
    -5,495,328 Devanagari characters (`runs/vol614-8f923220/PAIRED-*.json`). A guard
    that silently stops decoding is exactly the failure this corpus cannot see from a
    purity ratio, which is why the assertion below is on decoded TEXT.
    """

    import likhit.extractors.font_based as font_based_module

    keystrokes = "dxfn]vfk/LIfssf] sfof{no"
    page_dict = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {
                                "font": "CIDFont+F2",
                                "text": keystrokes,
                                "bbox": (0.0, 0.0, 10.0, 10.0),
                            }
                        ]
                    }
                ]
            }
        ]
    }

    doc = _Doc([[_font_info(2, "CIDFont+F2")]], {2: _ttf("Preeti", "Preeti")})
    # `resolve_embedded_legacy_maps` must see a real binding for this to mean anything.
    assert resolve_embedded_legacy_maps(doc) == {"F2": "Preeti"}

    monkeypatch.setattr(font_based_module.fitz, "open", lambda _path: doc)
    monkeypatch.setattr(
        font_based_module, "get_cid_marked_page_dict", lambda _page: page_dict
    )
    monkeypatch.setattr(font_based_module, "scan_ocr_pages", lambda _doc: {})
    monkeypatch.setattr(
        font_based_module, "collect_page_repairs_by_line", lambda _page, page_number: {}
    )
    monkeypatch.setattr(
        font_based_module, "detect_page_tables", lambda _page, _frags, _idx: []
    )

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir="/var/tmp") as fh:
        fh.write(b"%PDF-1.4")
        path = fh.name
    result = FontBasedStrategy().extract_text(path)
    assert "महालेखापरीक्षकको कार्यालय" in result.raw_text, result.raw_text
    assert keystrokes not in result.raw_text
