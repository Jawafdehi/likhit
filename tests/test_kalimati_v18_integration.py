"""Integration coverage for the three v18 Kalimati repair paths."""

from __future__ import annotations

import pytest

from likhit.extractors import kalimati


@pytest.mark.parametrize(
    "encoding",
    [
        "StandardEncoding",
        "MacRomanEncoding",
        "MacExpertEncoding",
        "WinAnsiEncoding",
    ],
)
def test_named_simple_font_with_standard_encoding_is_already_usable(
    monkeypatch: pytest.MonkeyPatch,
    encoding: str,
) -> None:
    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [(11, "ttf", "TrueType", "ABCDEF+Kalimati", encoding)]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            assert xref == 11
            return f"<< /Encoding /{encoding} /ToUnicode 12 0 R >>"

    source = FakeDoc()
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda *_args: pytest.fail("standard encoding must not enter repair"),
    )

    assert kalimati.fix_kalimati_cmap(source) == (source, False)  # type: ignore[arg-type]


@pytest.mark.parametrize("generic_first", [True, False])
def test_mixed_generic_and_named_shared_cmap_fails_in_any_resource_order(
    monkeypatch: pytest.MonkeyPatch,
    generic_first: bool,
) -> None:
    generic = (11, "ttf", "Type0", "CIDFont+F1", "Identity-H")
    named = (21, "ttf", "Type0", "ABCDEF+Kalimati", "Identity-H")
    fonts: list[tuple[object, ...]] = (
        [generic, named] if generic_first else [named, generic]
    )
    patched: list[object] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return fonts

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                11: (
                    "<< /Encoding /Identity-H /DescendantFonts "
                    "[ << /Subtype /CIDFontType2 /CIDToGIDMap /Identity >> ] "
                    "/ToUnicode 12 0 R >>"
                ),
                21: (
                    "<< /Encoding /Identity-H /DescendantFonts [22 0 R] "
                    "/ToUnicode 12 0 R >>"
                ),
                22: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
            }[xref]

        def xref_is_stream(self, xref: int) -> bool:
            return xref == 12

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda _stream: {1: "क"},
    )
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda _doc, _xref: {1: "क", 2: "ख"},
    )
    monkeypatch.setattr(
        kalimati,
        "_collect_trace_fallback_map",
        lambda _doc, _font_name: {},
    )
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: 31)
    monkeypatch.setattr(
        kalimati,
        "_patch_missing_cmap_entries",
        lambda *args, **kwargs: patched.append((args, kwargs)),
    )
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda *args, **kwargs: patched.append((args, kwargs)),
    )

    with pytest.raises(kalimati.ExtractionError, match="Kalimati"):
        kalimati.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert patched == []


def test_single_difference_lohit_map_keeps_the_named_font_exemption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched: list[tuple[int, dict[int, str]]] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [
                (
                    11,
                    "ttf",
                    "Type0",
                    "ABCDEF+Lohit-Devanagari",
                    "Identity-H",
                )
            ]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            assert xref == 11
            return "<< /ToUnicode 12 0 R >>"

        def xref_is_stream(self, xref: int) -> bool:
            return xref == 12

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

        def save(self, buffer: object) -> None:
            buffer.write(b"%PDF-1.4")  # type: ignore[attr-defined]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda _stream: {1: "क"},
    )
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda _doc, _xref: {1: "ख"},
    )
    monkeypatch.setattr(
        kalimati,
        "_collect_trace_fallback_map",
        lambda _doc, _font_name: {},
    )
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: None)
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, xref, corrections, **_kwargs: patched.append(
            (xref, dict(corrections))
        ),
    )
    reopened = object()
    monkeypatch.setattr(kalimati.fitz, "open", lambda *args, **kwargs: reopened)

    repaired, needs_reorder = kalimati.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert repaired is reopened
    assert needs_reorder is True
    assert patched == [(12, {1: "ख"})]


@pytest.mark.parametrize(
    ("font_name", "raises"),
    [
        ("Kokila", True),
        ("Kokila-Bold", True),
        ("NotKokila", False),
        ("KokilaExtra", False),
    ],
)
def test_no_patch_failure_requires_exact_kokila_ownership(
    monkeypatch: pytest.MonkeyPatch,
    font_name: str,
    raises: bool,
) -> None:
    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [(11, "ttf", "Type0", f"ABCDEF+{font_name}", "Identity-H")]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            assert xref == 11
            return "<< /ToUnicode 12 0 R >>"

        def xref_is_stream(self, xref: int) -> bool:
            return xref == 12

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 12
            return b"unused"

    source = FakeDoc()
    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda _stream: {1: "क"},
    )
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda _doc, _xref: {},
    )
    monkeypatch.setattr(
        kalimati,
        "_collect_trace_fallback_map",
        lambda _doc, _font_name: {},
    )
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: None)

    if raises:
        with pytest.raises(kalimati.ExtractionError):
            kalimati.fix_kalimati_cmap(source)  # type: ignore[arg-type]
    else:
        assert kalimati.fix_kalimati_cmap(source) == (source, False)  # type: ignore[arg-type]


def test_generic_simple_and_contextual_repairs_coexist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_patches: dict[int, dict[int, str]] = {}
    cmap_patches: dict[int, tuple[dict[int, str], dict[str, object]]] = {}

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [
                (11, "ttf", "Type0", "CIDFont+F1", "Identity-H"),
                (21, "ttf", "Type0", "ABCDEF+Kokila", "Identity-H"),
                (31, "ttf", "TrueType", "ABCDEF+Kalimati", "BuiltIn"),
            ]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                11: (
                    "<< /Encoding /Identity-H /DescendantFonts "
                    "[ << /CIDToGIDMap /Identity >> ] /ToUnicode 12 0 R >>"
                ),
                21: (
                    "<< /Encoding /Identity-H /DescendantFonts [22 0 R] "
                    "/ToUnicode 23 0 R >>"
                ),
                22: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
                31: "<< /ToUnicode 32 0 R >>",
            }[xref]

        def xref_is_stream(self, xref: int) -> bool:
            return xref in {12, 23, 32}

        def xref_stream(self, xref: int) -> bytes:
            assert self.xref_is_stream(xref)
            return str(xref).encode()

        def save(self, buffer: object) -> None:
            buffer.write(b"%PDF-1.4")  # type: ignore[attr-defined]

        def close(self) -> None:
            return None

    pdf_maps = {
        12: {1: "क"},
        23: {83: "ि", 108: "र्"},
        32: {65: "क", 66: "ख", 67: "ग", 68: "broken"},
    }
    font_maps = {
        11: {1: "क", 2: "ख"},
        21: {83: "त", 108: "ि"},
        31: {7: "क", 8: "ख", 9: "ग", 10: "क्ष"},
    }
    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda stream: dict(pdf_maps[int(stream.decode())]),
    )
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda _doc, xref: dict(font_maps[xref]),
    )
    monkeypatch.setattr(
        kalimati,
        "_get_simple_font_correction_map",
        lambda _doc, _xref, correction: {
            65: correction[7],
            66: correction[8],
            67: correction[9],
            68: correction[10],
        },
    )
    monkeypatch.setattr(
        kalimati,
        "_collect_trace_fallback_map",
        lambda _doc, _font_name: {},
    )
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: None)
    monkeypatch.setattr(
        kalimati,
        "_patch_missing_cmap_entries",
        lambda _doc, xref, _pdf_map, missing: missing_patches.__setitem__(
            xref,
            dict(missing),
        ),
    )
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, xref, corrections, **kwargs: cmap_patches.__setitem__(
            xref,
            (dict(corrections), dict(kwargs)),
        ),
    )
    reopened = object()
    monkeypatch.setattr(kalimati.fitz, "open", lambda *args, **kwargs: reopened)

    repaired, needs_reorder = kalimati.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert repaired is reopened
    assert needs_reorder is True
    assert missing_patches == {12: {2: "ख"}}
    assert cmap_patches[32] == (
        {65: "क", 66: "ख", 67: "ग", 68: "क्ष"},
        {},
    )
    assert cmap_patches[23] == (
        {
            83: kalimati._PUA_KOKILA_TA,
            108: kalimati._PUA_KOKILA_IKAR,
        },
        {
            "font_name": "Kokila",
            "allow_gid_exceptions": False,
        },
    )
