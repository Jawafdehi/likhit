"""Forward-port coverage for the name-routed legacy-font pipeline."""

from __future__ import annotations

from io import BytesIO
import inspect

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
import pytest

from likhit.extractors import font_based
from likhit.extractors.font_based import (
    FontBasedStrategy,
    LegacyMapChoice,
    _ascii_bracketed_run_exemptions,
    _span_legacy_map_key,
    detect_content_legacy_fonts,
    detect_latin_acronym_survivors,
    detect_name_legacy_candidates,
)
from likhit.extractors.font_classifier import (
    _embedded_name_candidates,
    resolve_embedded_legacy_maps,
    scan_pdf_fonts_by_page,
)
from likhit.nepali_pdf_repair import _convert_span_text as convert_repair_span


def _ttf(family: str, postscript_name: str) -> bytes:
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
            "psName": postscript_name,
        }
    )
    builder.setupOS2()
    builder.setupPost()
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


class _Page:
    def __init__(
        self,
        fonts: list[tuple] | None = None,
        spans: list[tuple[str, str]] | None = None,
    ) -> None:
        self.fonts = fonts or []
        self.spans = spans or []

    def get_fonts(self, full: bool = False) -> list[tuple]:
        del full
        return self.fonts


class _Doc:
    def __init__(
        self,
        pages: list[_Page],
        programs: dict[int, bytes] | None = None,
    ) -> None:
        self.pages = pages
        self.programs = programs or {}
        self.page_count = len(pages)

    def __getitem__(self, index: int) -> _Page:
        return self.pages[index]

    def extract_font(self, xref: int, named: bool = False) -> dict[str, bytes | None]:
        del named
        return {"content": self.programs.get(xref)}


def _font_info(xref: int, resource_name: str, ext: str = "ttf") -> tuple:
    return (xref, ext, "TrueType", resource_name, f"F{xref}", "")


def _page_dict(page: _Page) -> dict:
    return {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {"font": font_name, "text": text}
                            for font_name, text in page.spans
                        ]
                    }
                ]
            }
        ]
    }


def test_embedded_names_use_declared_precedence_and_route_the_resource() -> None:
    program = _ttf("Shangrila Hybrid", "FONTASYHIMALITTNORMAL")
    candidates = _embedded_name_candidates(program)
    ids = [name_id for name_id, _value in candidates]
    assert ids == sorted(ids, key=(16, 1, 6, 4).index)

    doc = _Doc(
        [_Page([_font_info(7, "ABCEEE+Shangrila Hybrid")])],
        {7: program},
    )
    binding = resolve_embedded_legacy_maps(doc)
    assert binding == {"Shangrila Hybrid": "FONTASY_HIMALI_TT"}
    assert scan_pdf_fonts_by_page(doc, binding) == {
        1: {"Shangrila Hybrid": "legacy_remap"}
    }


def test_embedded_binding_is_scoped_to_the_document() -> None:
    preeti = _Doc(
        [_Page([_font_info(1, "CIDFont+F1")])],
        {1: _ttf("Preeti", "Preeti")},
    )
    himali = _Doc(
        [_Page([_font_info(1, "CIDFont+F1")])],
        {1: _ttf("Fontasy Himali", "FontasyHimali")},
    )
    assert resolve_embedded_legacy_maps(preeti) == {"F1": "Preeti"}
    assert resolve_embedded_legacy_maps(himali) == {"F1": "FONTASY_HIMALI_TT"}


def test_unparseable_embedded_font_is_not_routing_evidence() -> None:
    assert _embedded_name_candidates(b"not a font") == []


PREETI_PROSE = "clVtof/ b'?kof]u cg';Gwfg cfof]u " * 4


def test_name_candidacy_uses_the_aggregate_and_honours_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(font_based, "get_cid_marked_page_dict", _page_dict)
    doc = _Doc(
        [
            _Page(spans=[("Preeti", PREETI_PROSE)]),
            _Page(spans=[("PreetiExt", "2070 179 23.2 25,70,266/- 100")]),
        ]
    )

    confirmed = detect_name_legacy_candidates(doc)
    assert "Preeti" in confirmed
    assert "PreetiExt" not in confirmed
    assert detect_name_legacy_candidates(doc, frozenset({1})) == frozenset()


def test_embedded_name_needs_no_second_map_corroboration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VOL-675's final no-term ruling retains a clean Himali digit face."""

    monkeypatch.setattr(font_based, "get_cid_marked_page_dict", _page_dict)
    doc = _Doc([_Page(spans=[("CIDFont+F2", "142445264")])])
    assert detect_name_legacy_candidates(
        doc,
        embedded_legacy_maps={"F2": "FONTASY_HIMALI_TT"},
    ) == frozenset({"CIDFont+F2"})


def test_embedded_routing_excludes_content_candidacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(font_based, "get_cid_marked_page_dict", _page_dict)
    doc = _Doc([_Page(spans=[("CIDFont+F2", PREETI_PROSE)])])
    assert set(detect_content_legacy_fonts(doc)) == {"CIDFont+F2"}
    assert (
        detect_content_legacy_fonts(
            doc,
            embedded_legacy_maps={"F2": "Preeti"},
        )
        == {}
    )


@pytest.mark.parametrize(
    ("word_veto", "text_veto", "preserved"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_name_path_decodes_once_and_requires_both_latin_certifiers(
    monkeypatch: pytest.MonkeyPatch,
    word_veto: bool,
    text_veto: bool,
    preserved: bool,
) -> None:
    calls: list[str] = []

    def converter(text: str) -> str:
        calls.append(text)
        return "decoded"

    monkeypatch.setattr(
        font_based,
        "_name_legacy_converter",
        lambda _font, _embedded: converter,
    )
    monkeypatch.setattr(font_based, "_reads_as_latin_words", lambda _text: word_veto)
    monkeypatch.setattr(
        font_based,
        "_reads_as_latin_text",
        lambda _raw, _decoded: text_veto,
    )

    output = FontBasedStrategy()._convert_span_text(
        "raw span",
        "Preeti",
        {"Preeti": "legacy_remap"},
        needs_reorder=False,
        name_legacy_confirmed=frozenset({"Preeti"}),
    )
    assert output == ("raw span" if preserved else "decoded")
    assert calls == ["raw span"]


def test_exemption_slices_share_the_whole_name_path_latin_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "Serving the Nation and the (12)"
    marker_start = raw.index("(")
    seen: list[str] = []

    def converter(text: str) -> str:
        return f"<{text}>"

    monkeypatch.setattr(
        font_based,
        "_name_legacy_converter",
        lambda _font, _embedded: converter,
    )
    monkeypatch.setattr(
        font_based,
        "_reads_as_latin_words",
        lambda text: seen.append(text) is None,
    )
    monkeypatch.setattr(font_based, "_reads_as_latin_text", lambda _raw, _decoded: True)

    output = FontBasedStrategy()._convert_span_text(
        raw,
        "CIDFont+F2",
        {"F2": "legacy_remap"},
        needs_reorder=False,
        embedded_legacy_maps={"F2": "Preeti"},
        name_legacy_confirmed=frozenset({"CIDFont+F2"}),
        exempt_slices=((marker_start, len(raw)),),
    )
    assert output == "Serving the Nation and the (१२)"
    assert seen == [raw]


def test_embedded_name_path_uses_the_output_converter() -> None:
    output = FontBasedStrategy()._convert_span_text(
        "(1)",
        "CIDFont+F2",
        {"F2": "legacy_remap"},
        needs_reorder=False,
        embedded_legacy_maps={"F2": "FONTASY_HIMALI_TT"},
        name_legacy_confirmed=frozenset({"CIDFont+F2"}),
    )
    assert output == "(१)"


def test_marker_routing_uses_embedded_identity_and_name_confirmation() -> None:
    strategies = {"F2": "legacy_remap"}
    embedded = {"F2": "Preeti"}
    confirmed = frozenset({"CIDFont+F2"})
    assert (
        _span_legacy_map_key(
            "(12)",
            "CIDFont+F2",
            strategies,
            None,
            False,
            embedded,
            confirmed,
        )
        == "Preeti"
    )
    assert (
        _span_legacy_map_key(
            "(12)",
            "CIDFont+F2",
            strategies,
            None,
            False,
            embedded,
            frozenset(),
        )
        is None
    )
    spans = [{"font": "CIDFont+F2", "text": "(12)"}]
    assert _ascii_bracketed_run_exemptions(
        spans,
        None,
        [False],
        strategies,
        embedded,
        confirmed,
    ) == [((0, 4),)]


def test_marker_routing_respects_the_name_path_latin_veto() -> None:
    text = "Serving the Nation and the annual report (12)"
    assert _ascii_bracketed_run_exemptions(
        [{"font": "Preeti", "text": text}],
        None,
        [False],
        {"Preeti": "legacy_remap"},
        name_legacy_confirmed=frozenset({"Preeti"}),
    ) == [()]


def test_embedded_routed_text_cannot_attest_a_content_path_acronym(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(font_based, "get_cid_marked_page_dict", _page_dict)
    monkeypatch.setattr(
        font_based,
        "_decodes_as_legacy_devanagari",
        lambda _token, _maps: False,
    )
    doc = _Doc([_Page(spans=[("CIDFont+F2", "MIS")])])
    content_maps = {"OtherFont": LegacyMapChoice("Preeti", None)}

    assert detect_latin_acronym_survivors(doc, content_maps) == frozenset({"MIS"})
    assert (
        detect_latin_acronym_survivors(
            doc,
            content_maps,
            embedded_legacy_maps={"F2": "Preeti"},
        )
        == frozenset()
    )


def test_repair_path_unmarks_winansi_before_legacy_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from likhit import nepali_pdf_repair

    seen: list[str] = []

    def converter(text: str) -> str:
        seen.append(text)
        return text

    monkeypatch.setattr(nepali_pdf_repair, "get_converter", lambda _font: converter)
    marked = chr(0xF0000 + 0x88) + chr(0xF0000 + 0xAB)
    assert (
        convert_repair_span(
            marked,
            "Preeti",
            {"Preeti": "legacy_remap"},
            needs_reorder=False,
        )
        == "ˆ«"
    )
    assert seen == ["ˆ«"]


def test_all_production_passes_forward_the_name_routing_context() -> None:
    source = inspect.getsource(FontBasedStrategy._extract_raw_document)
    assert source.count("embedded_legacy_maps=embedded_legacy_maps") == 3
    assert source.count("name_legacy_confirmed=name_legacy_confirmed") == 3

    inner = inspect.getsource(FontBasedStrategy._extract_from_document)
    assert inner.count("embedded_legacy_maps=embedded_legacy_maps") == 1
    assert inner.count("name_legacy_confirmed=name_legacy_confirmed") == 1
