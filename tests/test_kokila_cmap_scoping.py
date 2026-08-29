"""Per-CMap scoping for Kokila sibling evidence and font-map caching."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from likhit.extractors import kalimati


def _font(xref: int, name: str = "Kokila") -> tuple[object, ...]:
    return (xref, "ttf", "Type0", f"ABCDEF+{name}", "Identity-H")


class _FakePage:
    def __init__(self, fonts: Sequence[tuple[object, ...]]) -> None:
        self._fonts = list(fonts)

    def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
        assert full
        return self._fonts


class _FakeDoc:
    page_count = 1

    def __init__(
        self,
        fonts: Sequence[tuple[object, ...]],
        to_unicode_xrefs: Mapping[int, int],
    ) -> None:
        self._page = _FakePage(fonts)
        self._to_unicode_xrefs = dict(to_unicode_xrefs)

    def __getitem__(self, index: int) -> _FakePage:
        assert index == 0
        return self._page

    def xref_object(self, xref: int, compressed: bool = False) -> str:
        assert not compressed
        if xref in self._to_unicode_xrefs:
            descendant_xref = xref + 1000
            return (
                f"<< /Encoding /Identity-H /DescendantFonts [{descendant_xref} 0 R] "
                f"/ToUnicode {self._to_unicode_xrefs[xref]} 0 R >>"
            )
        if xref - 1000 in self._to_unicode_xrefs:
            return "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>"
        raise AssertionError(f"unexpected xref {xref}")

    def xref_is_stream(self, xref: int) -> bool:
        return xref in self._to_unicode_xrefs.values()

    def xref_stream(self, xref: int) -> bytes:
        assert self.xref_is_stream(xref)
        return str(xref).encode()

    def save(self, buffer: object) -> None:
        buffer.write(b"%PDF-1.4")  # type: ignore[attr-defined]

    def close(self) -> None:
        return None


def _run_fix(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fonts: Sequence[tuple[object, ...]],
    to_unicode_xrefs: Mapping[int, int],
    pdf_maps: Mapping[int, dict[int, str]],
    correction_map: dict[int, str],
    fontfile_xrefs: Mapping[int, int | None],
    outline_digests: Mapping[tuple[int, int], str | None] | None = None,
) -> tuple[dict[int, dict[int, str]], list[int]]:
    patched_maps: dict[int, dict[int, str]] = {}
    correction_calls: list[int] = []

    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda stream: dict(pdf_maps[int(stream.decode())]),
    )

    def get_correction_map(_doc: object, type0_xref: int) -> dict[int, str]:
        correction_calls.append(type0_xref)
        return dict(correction_map)

    monkeypatch.setattr(kalimati, "_get_font_correction_map", get_correction_map)
    monkeypatch.setattr(
        kalimati,
        "_get_fontfile_xref",
        lambda _doc, type0_xref: fontfile_xrefs[type0_xref],
    )
    monkeypatch.setattr(
        kalimati,
        "_font_program_gid_outline_digest",
        lambda _doc, fontfile_xref, gid: (outline_digests or {}).get(
            (fontfile_xref, gid)
        ),
    )
    monkeypatch.setattr(
        kalimati,
        "_collect_trace_fallback_map",
        lambda _doc, _font_name: {},
    )
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, xref, corrections, **_kwargs: patched_maps.__setitem__(
            xref,
            dict(corrections),
        ),
    )
    reopened = object()
    monkeypatch.setattr(kalimati.fitz, "open", lambda *args, **kwargs: reopened)

    repaired, needs_reorder = kalimati.fix_kalimati_cmap(
        _FakeDoc(fonts, to_unicode_xrefs)  # type: ignore[arg-type]
    )

    assert repaired is reopened
    assert needs_reorder is True
    return patched_maps, correction_calls


def test_sibling_corroboration_does_not_override_target_font_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched, _calls = _run_fix(
        monkeypatch,
        fonts=[_font(77), _font(88, "Kokila-Bold")],
        to_unicode_xrefs={77: 8453, 88: 8454},
        pdf_maps={
            8453: {83: "ि", 108: "र्", 214: "थ"},
            8454: {214: "स्"},
        },
        correction_map={83: "त", 108: "ि", 214: "क"},
        fontfile_xrefs={77: 31, 88: 31},
    )

    assert patched[8453][214] == "क"
    assert patched[8453][214] != kalimati._PUA_KOKILA_HALF_SA


@pytest.mark.parametrize(
    ("fontfile_xrefs", "expects_contextual_marker"),
    [
        pytest.param({77: 31, 88: 31}, True, id="shared-program"),
        pytest.param({77: 31, 88: 32}, False, id="different-program"),
    ],
)
def test_sibling_corroboration_requires_the_target_font_program(
    monkeypatch: pytest.MonkeyPatch,
    fontfile_xrefs: dict[int, int | None],
    expects_contextual_marker: bool,
) -> None:
    patched, _calls = _run_fix(
        monkeypatch,
        fonts=[_font(77), _font(88, "Kokila-Bold")],
        to_unicode_xrefs={77: 8453, 88: 8454},
        pdf_maps={
            8453: {83: "ि", 108: "र्", 214: "थ"},
            8454: {214: "स्"},
        },
        correction_map={83: "त", 108: "ि"},
        fontfile_xrefs=fontfile_xrefs,
    )

    assert (
        patched[8453].get(214) == kalimati._PUA_KOKILA_HALF_SA
    ) is expects_contextual_marker


@pytest.mark.parametrize(
    ("outline_digests", "expects_contextual_marker"),
    [
        pytest.param(
            {(31, 214): "same-outline", (32, 214): "same-outline"},
            True,
            id="same-outline",
        ),
        pytest.param(
            {(31, 214): "target-outline", (32, 214): "sibling-outline"},
            False,
            id="different-outline",
        ),
        pytest.param(
            {(31, 214): "target-outline"},
            False,
            id="missing-sibling-outline",
        ),
        pytest.param(
            {(32, 214): "sibling-outline"},
            False,
            id="missing-target-outline",
        ),
    ],
)
def test_half_sa_corroboration_accepts_only_identical_subset_outlines(
    monkeypatch: pytest.MonkeyPatch,
    outline_digests: dict[tuple[int, int], str | None],
    expects_contextual_marker: bool,
) -> None:
    patched, _calls = _run_fix(
        monkeypatch,
        fonts=[_font(77), _font(88)],
        to_unicode_xrefs={77: 8453, 88: 8454},
        pdf_maps={
            8453: {83: "ि", 108: "र्", 214: "थ"},
            8454: {214: "स्"},
        },
        correction_map={83: "त", 108: "ि"},
        fontfile_xrefs={77: 31, 88: 32},
        outline_digests=outline_digests,
    )

    assert (
        patched[8453].get(214) == kalimati._PUA_KOKILA_HALF_SA
    ) is expects_contextual_marker


def test_pdf_like_sibling_evidence_repairs_health_and_preserves_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_pdf_map = {
        83: "ि",
        94: "र्",
        100: "ि",
        107: "ा",
        108: "र्",
        195: "्",
        214: "थ",
    }
    patched, _calls = _run_fix(
        monkeypatch,
        fonts=[_font(77), _font(88), _font(99, "Kokila-Bold")],
        to_unicode_xrefs={77: 8453, 88: 8454, 99: 8455},
        pdf_maps={
            8453: target_pdf_map,
            8454: {214: "स्"},
            8455: {195: "थ्"},
        },
        correction_map={83: "त", 94: "य", 100: "व", 108: "ि"},
        fontfile_xrefs={77: 31, 88: 32, 99: 33},
        outline_digests={
            (31, 214): "same-regular-outline",
            (32, 214): "same-regular-outline",
            (31, 195): next(iter(kalimati._KOKILA_HALF_THA_OUTLINE_DIGESTS)),
        },
    )

    target = patched[8453]

    def decode(gids: list[int]) -> str:
        encoded = "".join(target.get(gid, target_pdf_map[gid]) for gid in gids)
        return kalimati.reorder_devanagari(encoded)

    assert decode([214, 100, 107, 214, 195, 94]) == "स्वास्थ्य"
    assert decode([83, 195, 94]) == "तथ्य"


@pytest.mark.parametrize(
    "font_order",
    [
        pytest.param([_font(77), _font(88)], id="generic-first"),
        pytest.param([_font(88), _font(77)], id="pair-first"),
    ],
)
def test_cached_font_map_is_scoped_per_cmap_in_any_resource_order(
    monkeypatch: pytest.MonkeyPatch,
    font_order: list[tuple[object, ...]],
) -> None:
    raw_font_map = {83: "त", 108: "ि", 200: "क"}
    patched, correction_calls = _run_fix(
        monkeypatch,
        fonts=font_order,
        to_unicode_xrefs={77: 8453, 88: 8454},
        pdf_maps={
            8453: {83: "क", 108: "ख", 200: "ग"},
            8454: {83: "ि", 108: "र्"},
        },
        correction_map=raw_font_map,
        fontfile_xrefs={77: 31, 88: 31},
    )

    assert patched == {
        8453: raw_font_map,
        8454: {
            83: kalimati._PUA_KOKILA_TA,
            108: kalimati._PUA_KOKILA_IKAR,
        },
    }
    assert len(correction_calls) == 1
