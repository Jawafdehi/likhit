"""Fail-closed coverage for the measured Kokila GID-195 provenance."""

from __future__ import annotations

from collections.abc import MutableMapping

import pytest

from likhit.extractors import kalimati


_TARGET_PDF_MAP = {
    83: "ि",
    94: "र्",
    108: "र्",
    195: "्",
}
_TARGET_FONT_MAP = {
    83: "त",
    94: "य",
    108: "ि",
}


def _target_corrections(
    font_name: str,
    pdf_map: dict[int, str],
    font_map: dict[int, str],
    *,
    target_outline_proven: bool,
) -> dict[int, str]:
    return kalimati._kokila_displacement_corrections(
        font_name,
        pdf_map,
        font_map,
        half_sa_corroborated_elsewhere=False,
        half_tha_proven_for_target=target_outline_proven,
    )


def test_exact_target_fingerprint_marks_gid_195_only_with_proven_outline() -> None:
    corrections = _target_corrections(
        "Kokila",
        dict(_TARGET_PDF_MAP),
        dict(_TARGET_FONT_MAP),
        target_outline_proven=True,
    )

    assert corrections[195] == kalimati._PUA_KOKILA_HALF_THA


@pytest.mark.parametrize("variant", ["changed", "absent"])
@pytest.mark.parametrize(
    ("component", "mapping_name", "gid", "changed_value"),
    [
        pytest.param("font name", "font_name", None, "Kalimati", id="font-name"),
        pytest.param("PDF GID 83", "pdf_map", 83, "ी", id="pdf-gid-83"),
        pytest.param("font GID 83", "font_map", 83, "क", id="font-gid-83"),
        pytest.param("PDF GID 108", "pdf_map", 108, "ि", id="pdf-gid-108"),
        pytest.param("font GID 108", "font_map", 108, "क", id="font-gid-108"),
        pytest.param("PDF GID 195", "pdf_map", 195, "थ", id="pdf-gid-195"),
        pytest.param("PDF GID 94", "pdf_map", 94, "य", id="pdf-gid-94"),
        pytest.param("font GID 94", "font_map", 94, "र", id="font-gid-94"),
    ],
)
def test_each_target_fingerprint_component_fails_closed(
    component: str,
    mapping_name: str,
    gid: int | None,
    changed_value: str,
    variant: str,
) -> None:
    font_name = "Kokila"
    pdf_map = dict(_TARGET_PDF_MAP)
    font_map = dict(_TARGET_FONT_MAP)

    if mapping_name == "font_name":
        font_name = changed_value if variant == "changed" else ""
    else:
        mapping: MutableMapping[int, str] = (
            pdf_map if mapping_name == "pdf_map" else font_map
        )
        assert gid is not None
        if variant == "changed":
            mapping[gid] = changed_value
        else:
            mapping.pop(gid)

    corrections = _target_corrections(
        font_name,
        pdf_map,
        font_map,
        target_outline_proven=True,
    )

    authored_gid_195 = pdf_map.get(195)
    assert corrections.get(195, authored_gid_195) == authored_gid_195, component
    assert corrections.get(195) != kalimati._PUA_KOKILA_HALF_THA


def test_gid_195_stays_authored_without_proven_outline() -> None:
    pdf_map = dict(_TARGET_PDF_MAP)
    corrections = _target_corrections(
        "Kokila",
        pdf_map,
        dict(_TARGET_FONT_MAP),
        target_outline_proven=False,
    )

    assert corrections.get(195, pdf_map[195]) == pdf_map[195]
    assert corrections.get(195) != kalimati._PUA_KOKILA_HALF_THA


class _IdentityFontDoc:
    """Two Identity-H Kokila faces backed by distinct font programs."""

    _objects = {
        1: "<< /Encoding /Identity-H /DescendantFonts [11 0 R] >>",
        2: "<< /Encoding /Identity-H /DescendantFonts [12 0 R] >>",
        11: (
            "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity /FontDescriptor 21 0 R >>"
        ),
        12: (
            "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity /FontDescriptor 22 0 R >>"
        ),
        21: "<< /FontFile2 31 0 R >>",
        22: "<< /FontFile2 32 0 R >>",
    }

    def xref_object(self, xref: int, compressed: bool = False) -> str:
        assert not compressed
        return self._objects[xref]


def test_half_tha_outline_proof_is_target_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _IdentityFontDoc()
    font_names = {1: "Kokila", 2: "Kokila-Bold"}
    monkeypatch.setattr(
        kalimati,
        "_font_program_gid_outline_digest",
        lambda _doc, fontfile_xref, _gid: {
            31: next(iter(kalimati._KOKILA_HALF_THA_OUTLINE_DIGESTS)),
            32: "different-outline",
        }[fontfile_xref],
    )

    assert kalimati._has_proven_kokila_half_tha_outline(  # type: ignore[arg-type]
        doc,
        {1},
        font_names,
    )
    assert not kalimati._has_proven_kokila_half_tha_outline(  # type: ignore[arg-type]
        doc,
        {2},
        font_names,
    )
    assert not kalimati._has_proven_kokila_half_tha_outline(  # type: ignore[arg-type]
        doc,
        {1, 2},
        font_names,
    )


@pytest.mark.parametrize("digest", [None, "", "different-outline"])
def test_half_tha_outline_proof_rejects_unmeasured_outlines(
    monkeypatch: pytest.MonkeyPatch,
    digest: str | None,
) -> None:
    monkeypatch.setattr(
        kalimati,
        "_font_program_gid_outline_digest",
        lambda _doc, _fontfile_xref, _gid: digest,
    )

    assert not kalimati._has_proven_kokila_half_tha_outline(  # type: ignore[arg-type]
        _IdentityFontDoc(),
        {1},
        {1: "Kokila"},
    )


def test_fix_kalimati_cmap_marks_only_the_proven_target_half_tha_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched_maps: list[tuple[int, dict[int, str]]] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [
                (77, "ttf", "Type0", "AAAAAA+Kokila", "Identity-H"),
                (88, "ttf", "Type0", "BBBBBB+Kokila-Bold", "Identity-H"),
            ]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                77: (
                    "<< /Encoding /Identity-H /DescendantFonts [78 0 R] "
                    "/ToUnicode 8453 0 R >>"
                ),
                78: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
                88: (
                    "<< /Encoding /Identity-H /DescendantFonts [89 0 R] "
                    "/ToUnicode 8454 0 R >>"
                ),
                89: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
            }[xref]

        def xref_is_stream(self, xref: int) -> bool:
            return xref in {8453, 8454}

        def xref_stream(self, xref: int) -> bytes:
            return str(xref).encode()

        def save(self, buffer: object) -> None:
            buffer.write(b"%PDF-1.4")  # type: ignore[attr-defined]

        def close(self) -> None:
            return None

    target_pdf_map = {83: "ि", 94: "र्", 108: "र्", 195: "्"}
    target_font_map = {83: "त", 94: "य", 108: "ि"}
    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda _stream: dict(target_pdf_map),
    )
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda _doc, _xref: dict(target_font_map),
    )
    monkeypatch.setattr(
        kalimati,
        "_collect_trace_fallback_map",
        lambda _doc, _font_name: {},
    )
    monkeypatch.setattr(
        kalimati,
        "_get_fontfile_xref",
        lambda _doc, xref: {77: 31, 88: 32}[xref],
    )
    monkeypatch.setattr(
        kalimati,
        "_font_program_gid_outline_digest",
        lambda _doc, fontfile_xref, _gid: {
            31: next(iter(kalimati._KOKILA_HALF_THA_OUTLINE_DIGESTS)),
            32: "different-outline",
        }[fontfile_xref],
    )
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, xref, correction, **_kwargs: patched_maps.append(
            (xref, dict(correction))
        ),
    )
    reopened = object()
    monkeypatch.setattr(kalimati.fitz, "open", lambda *args, **kwargs: reopened)

    repaired, needs_reorder = kalimati.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert repaired is reopened
    assert needs_reorder is True
    assert patched_maps == [
        (
            8453,
            {
                83: "त",
                94: "य",
                108: "ि",
                195: kalimati._PUA_KOKILA_HALF_THA,
            },
        ),
        (
            8454,
            {
                83: "त",
                94: "य",
                108: "ि",
            },
        ),
    ]
