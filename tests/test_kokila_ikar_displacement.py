"""The measured Kokila displacement is contextual and GID-scoped."""

from __future__ import annotations

import pytest

from likhit.extractors import kalimati


def test_the_measured_ra_virama_ikar_pair_is_correlated_evidence() -> None:
    pdf_map = {83: "ि", 108: "र्"}
    # GID 999 is intentionally absent from the PDF map. The two-GID exception
    # proves only 83/108 and must not install this unrelated derived entry.
    font_map = {83: "त", 108: "ि", 999: "क"}

    assert kalimati._meaningful_cmap_diff_count(pdf_map, font_map) == 2
    assert kalimati._has_ra_virama_ikar_displacement_pair(
        "Kokila-BoldItalic",
        pdf_map,
        font_map,
    )


def test_pair_corrections_keep_context_markers_and_corroborated_half_sa() -> None:
    corrections = kalimati._kokila_displacement_corrections(
        "Kokila-Bold",
        {83: "ि", 108: "र्", 214: "थ"},
        {83: "त", 108: "ि", 214: "स्", 999: "क"},
        half_sa_corroborated_elsewhere=False,
        half_tha_proven_for_target=False,
    )

    assert corrections == {
        83: kalimati._PUA_KOKILA_TA,
        108: kalimati._PUA_KOKILA_IKAR,
        214: kalimati._PUA_KOKILA_HALF_SA,
    }


def test_half_sa_requires_own_or_same_document_corroboration() -> None:
    pdf_map = {83: "ि", 108: "र्", 214: "थ"}
    font_map = {83: "त", 108: "ि"}

    uncorroborated = kalimati._kokila_displacement_corrections(
        "Kokila",
        pdf_map,
        font_map,
        half_sa_corroborated_elsewhere=False,
        half_tha_proven_for_target=False,
    )
    corroborated = kalimati._kokila_displacement_corrections(
        "Kokila",
        pdf_map,
        font_map,
        half_sa_corroborated_elsewhere=True,
        half_tha_proven_for_target=False,
    )

    assert 214 not in uncorroborated
    assert corroborated[214] == kalimati._PUA_KOKILA_HALF_SA


def test_half_tha_requires_exact_target_shape_and_outline_proof() -> None:
    pdf_map = {83: "ि", 94: "र्", 108: "र्", 195: "्"}
    font_map = {83: "त", 94: "य", 108: "ि"}

    uncorroborated = kalimati._kokila_displacement_corrections(
        "Kokila",
        pdf_map,
        font_map,
        half_sa_corroborated_elsewhere=False,
        half_tha_proven_for_target=False,
    )
    corroborated = kalimati._kokila_displacement_corrections(
        "Kokila",
        pdf_map,
        font_map,
        half_sa_corroborated_elsewhere=False,
        half_tha_proven_for_target=True,
    )

    assert 195 not in uncorroborated
    assert corroborated[195] == kalimati._PUA_KOKILA_HALF_THA


@pytest.mark.parametrize("meaningful_diffs", [3, 8])
def test_generic_full_map_keeps_ordinary_pair_meanings_and_adds_contextual_gids(
    meaningful_diffs: int,
) -> None:
    full_map = {83: "त", 108: "ि", 300: "क", 301: "ख"}
    displacement = {
        83: kalimati._PUA_KOKILA_TA,
        108: kalimati._PUA_KOKILA_IKAR,
        214: kalimati._PUA_KOKILA_HALF_SA,
        195: kalimati._PUA_KOKILA_HALF_THA,
    }

    scoped = kalimati._scope_kokila_displacement_corrections(
        full_map,
        displacement,
        meaningful_diffs=meaningful_diffs,
    )

    assert scoped == {
        **full_map,
        214: kalimati._PUA_KOKILA_HALF_SA,
        195: kalimati._PUA_KOKILA_HALF_THA,
    }


def test_contextual_markers_repair_status_but_preserve_authored_ra_virama() -> None:
    ikar = kalimati._PUA_KOKILA_IKAR
    ta = kalimati._PUA_KOKILA_TA
    half_sa = kalimati._PUA_KOKILA_HALF_SA

    assert kalimati.reorder_devanagari(f"{ikar}स्थ{ikar}{ta}") == "स्थिति"
    assert kalimati.reorder_devanagari(f"{ikar}{half_sa}थ{ikar}{ta}") == "स्थिति"
    assert kalimati.reorder_devanagari(f"आ{ikar}थिक") == "आर्थिक"
    assert kalimati.reorder_devanagari(f"{ta}ह :") == "तह :"
    assert kalimati.reorder_devanagari(f"अ{ta}") == "अि"


def test_literal_th_status_signature_repairs_only_when_complete() -> None:
    ikar = kalimati._PUA_KOKILA_IKAR
    ta = kalimati._PUA_KOKILA_TA
    components = [ikar, "थ", "थ", ikar, ta]
    signature = "".join(components)

    assert kalimati.reorder_devanagari(signature) == "स्थिति"
    for index in range(len(components)):
        incomplete = "".join(components[:index] + components[index + 1 :])
        assert kalimati.reorder_devanagari(incomplete) != "स्थिति"


def test_deferred_context_resolves_every_kokila_marker_but_not_contextual_ne() -> None:
    contextual_ne = kalimati._PUA_CONTEXTUAL_NE
    ikar = kalimati._PUA_KOKILA_IKAR
    ta = kalimati._PUA_KOKILA_TA
    half_sa = kalimati._PUA_KOKILA_HALF_SA
    half_tha = kalimati._PUA_KOKILA_HALF_THA

    reordered = kalimati.reorder_devanagari(
        f"{contextual_ne} आ{ikar}थिक {ta}ह {half_sa}थानीय त{half_tha}य",
        resolve_contextual=False,
    )

    assert reordered == f"{contextual_ne} आर्थिक तह स्थानीय तथ्य"
    assert not any(marker in reordered for marker in (ikar, ta, half_sa, half_tha))


def test_half_sa_marker_requires_a_proven_following_cluster() -> None:
    half_sa = kalimati._PUA_KOKILA_HALF_SA
    half_tha = kalimati._PUA_KOKILA_HALF_THA

    assert kalimati.reorder_devanagari(f"{half_sa}थानीय") == "स्थानीय"
    assert kalimati.reorder_devanagari(f"स्वा{half_sa}{half_tha}य") == "स्वास्थ्य"
    assert kalimati.reorder_devanagari(f"स्वा{half_sa}्य") == "स्वाथ्य"
    assert kalimati.reorder_devanagari(f"{half_sa}ापा") == "थापा"
    assert kalimati.reorder_devanagari(half_sa) == "थ"


def test_half_tha_marker_repairs_fact_but_preserves_unproven_virama() -> None:
    marker = kalimati._PUA_KOKILA_HALF_THA

    assert kalimati.reorder_devanagari(f"त{marker}य") == "तथ्य"
    assert kalimati.reorder_devanagari(f"{marker}क") == "्क"
    assert kalimati.reorder_devanagari(marker) == "्"


def test_half_sa_resolves_before_generic_ikar_reordering() -> None:
    half_sa = kalimati._PUA_KOKILA_HALF_SA
    ikar = kalimati._PUA_IKAR

    assert (
        kalimati.reorder_devanagari(
            f"{ikar}{half_sa}थ{ikar}त",
            resolve_contextual=False,
        )
        == "स्थिति"
    )


def test_half_tha_resolves_before_generic_ikar_reordering() -> None:
    half_tha = kalimati._PUA_KOKILA_HALF_THA
    ikar = kalimati._PUA_IKAR

    assert (
        kalimati.reorder_devanagari(
            f"{ikar}{half_tha}य",
            resolve_contextual=False,
        )
        == "थ्यि"
    )


@pytest.mark.parametrize(
    ("encoding", "cid_to_gid", "expected"),
    [
        ("/Identity-H", "/CIDToGIDMap /Identity", True),
        ("/Identity-H", "", True),
        ("/Identity-V", "/CIDToGIDMap /Identity", False),
        ("/Identity-H", "/CIDToGIDMap 99 0 R", False),
    ],
)
def test_gid_exceptions_require_identity_character_and_glyph_mappings(
    encoding: str,
    cid_to_gid: str,
    expected: bool,
) -> None:
    class FakeDoc:
        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                1: (
                    f"<< /Encoding {encoding} /DescendantFonts [2 0 R] "
                    "/ToUnicode 10 0 R >>"
                ),
                2: f"<< /Subtype /CIDFontType2 {cid_to_gid} >>",
            }[xref]

    assert kalimati._type0_uses_identity_gid_mapping(FakeDoc(), 1) is expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("font_name", "expected"),
    [
        ("Kokila", True),
        ("Kokila-Bold", True),
        ("NotKokila", False),
        ("KokilaExtra", False),
    ],
)
def test_face_specific_gid_scope_requires_a_font_family_boundary(
    font_name: str,
    expected: bool,
) -> None:
    class FakeDoc:
        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                1: "<< /Encoding /Identity-H /DescendantFonts [2 0 R] >>",
                2: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
            }[xref]

    assert (
        kalimati._stream_allows_face_specific_gids(  # type: ignore[arg-type]
            FakeDoc(),
            {1},
            {1: font_name},
            "kokila",
        )
        is expected
    )


@pytest.mark.parametrize(
    ("font_name", "expected"),
    [
        ("Kalimati", True),
        ("Kalimati-Bold", True),
        ("Lohit-Devanagari", True),
        ("NotKalimati", False),
        ("KalimatiExtra", False),
        ("LohitExtra", False),
    ],
)
def test_named_repair_font_requires_a_font_family_boundary(
    font_name: str,
    expected: bool,
) -> None:
    assert kalimati._is_named_repair_font(font_name) is expected


@pytest.mark.parametrize(
    ("font_name", "expected"),
    [
        ("Kokila-Bold", True),
        ("NotKokila", False),
        ("KokilaExtra", False),
    ],
)
def test_kokila_displacement_pair_requires_a_font_family_boundary(
    font_name: str,
    expected: bool,
) -> None:
    assert (
        kalimati._has_ra_virama_ikar_displacement_pair(
            font_name,
            {83: "ि", 108: "र्"},
            {83: "त", 108: "ि"},
        )
        is expected
    )


@pytest.mark.parametrize(
    ("font_name", "expected_value"),
    [
        ("Kalimati-Bold", kalimati._PUA_CONTEXTUAL_NE),
        ("NotKalimati", "े"),
        ("KalimatiExtra", "े"),
    ],
)
def test_contextual_ne_patch_requires_a_font_family_boundary(
    monkeypatch: pytest.MonkeyPatch,
    font_name: str,
    expected_value: str,
) -> None:
    updated: list[dict[int, str]] = []

    class FakeDoc:
        def xref_stream(self, xref: int) -> bytes:
            assert xref == 10
            return b"unused"

        def update_stream(self, xref: int, stream: bytes) -> None:
            assert xref == 10
            assert stream == b"patched"

    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda _stream: {kalimati._CONTEXTUAL_NE_GID: "ने"},
    )
    monkeypatch.setattr(
        kalimati,
        "_build_cmap_stream",
        lambda mapping: updated.append(dict(mapping)) or b"patched",
    )

    kalimati._patch_single_cmap(  # type: ignore[arg-type]
        FakeDoc(),
        10,
        {kalimati._CONTEXTUAL_NE_GID: "े"},
        font_name=font_name,
        allow_gid_exceptions=True,
    )

    assert updated == [{kalimati._CONTEXTUAL_NE_GID: expected_value}]


def test_mixed_face_shared_cmap_cannot_activate_pair_in_either_owner_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched: list[object] = []

    class FakePage:
        def __init__(self, owners: list[tuple[object, ...]]) -> None:
            self._owners = owners

        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return self._owners

    class FakeDoc:
        page_count = 1

        def __init__(self, owners: list[tuple[object, ...]]) -> None:
            self._page = FakePage(owners)

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return self._page

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
                    "/ToUnicode 8453 0 R >>"
                ),
                89: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
            }[xref]

        def xref_is_stream(self, xref: int) -> bool:
            return xref == 8453

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 8453
            return b"unused"

    kokila = (77, "ttf", "Type0", "ABCDEF+Kokila", "F1", "Identity-H")
    arial = (88, "ttf", "Type0", "ABCDEF+Arial", "F2", "Identity-H")
    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda _stream: {83: "ि", 108: "र्"},
    )
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda _doc, _xref: {83: "त", 108: "ि"},
    )
    monkeypatch.setattr(
        kalimati,
        "_collect_trace_fallback_map",
        lambda _doc, _font_name: {},
    )
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, xref: xref)
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda *args, **kwargs: patched.append((args, kwargs)),
    )

    for owners in ([kokila, arial], [arial, kokila]):
        with pytest.raises(kalimati.ExtractionError):
            kalimati.fix_kalimati_cmap(FakeDoc(owners))  # type: ignore[arg-type]

    assert patched == []


def test_same_face_shared_cmap_is_safe_only_with_one_font_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDoc:
        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                1: "<< /Encoding /Identity-H /DescendantFonts [11 0 R] >>",
                2: "<< /Encoding /Identity-H /DescendantFonts [12 0 R] >>",
                11: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
                12: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
            }[xref]

    names = {1: "Kalimati", 2: "Kalimati"}
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: 99)
    assert kalimati._stream_allows_face_specific_gids(  # type: ignore[arg-type]
        FakeDoc(),
        {1, 2},
        names,
        "kalimati",
    )

    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, xref: xref)
    assert not kalimati._stream_allows_face_specific_gids(  # type: ignore[arg-type]
        FakeDoc(),
        {1, 2},
        names,
        "kalimati",
    )


def test_shared_type0_reconstruction_requires_identity_gid_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDoc:
        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                1: "<< /Encoding /Identity-H /DescendantFonts [11 0 R] >>",
                2: "<< /Encoding /Identity-H /DescendantFonts [12 0 R] >>",
                11: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
                12: "<< /Subtype /CIDFontType2 /CIDToGIDMap 99 0 R >>",
            }[xref]

    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: 31)

    assert not kalimati._shared_type0_owners_are_homogeneous(  # type: ignore[arg-type]
        FakeDoc(),
        {1, 2},
        {1: "Kalimati", 2: "Kalimati"},
    )


def test_mixed_face_shared_cmap_cannot_corroborate_half_sa() -> None:
    class FakeDoc:
        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return {
                1: "<< /Encoding /Identity-H /DescendantFonts [11 0 R] >>",
                11: "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity >>",
            }[xref]

    pdf_maps = {10: {214: "स्"}}
    names = {1: "Kokila", 2: "Arial"}

    assert not kalimati._has_corroborated_kokila_half_sa(  # type: ignore[arg-type]
        FakeDoc(),
        pdf_maps,
        {10: {1, 2}},
        names,
    )
    assert kalimati._has_corroborated_kokila_half_sa(  # type: ignore[arg-type]
        FakeDoc(),
        pdf_maps,
        {10: {1}},
        names,
    )


@pytest.mark.parametrize(
    ("font_name", "pdf_map", "font_map"),
    [
        ("Kokila", {108: "र्"}, {108: "ि"}),
        ("Kokila", {83: "ि"}, {83: "त"}),
        ("Kokila", {83: "ि", 108: "क"}, {83: "त", 108: "ि"}),
        ("Kokila", {83: "ि", 108: "र्"}, {83: "े", 108: "ि"}),
        ("Kalimati", {83: "ि", 108: "र्"}, {83: "त", 108: "ि"}),
        ("Kokila", {1: "ि", 2: "र्"}, {1: "त", 2: "ि"}),
    ],
)
def test_neither_one_leg_nor_unrelated_differences_clear_the_floor(
    font_name: str,
    pdf_map: dict[int, str],
    font_map: dict[int, str],
) -> None:
    assert not kalimati._has_ra_virama_ikar_displacement_pair(
        font_name,
        pdf_map,
        font_map,
    )


def test_fix_kalimati_cmap_repairs_the_measured_two_gid_kokila_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched_maps: list[tuple[int, dict[int, str], str, bool]] = []

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [
                (
                    77,
                    "ttf",
                    "Type0",
                    "ABCDEF+Kokila-BoldItalic",
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
            return {
                77: (
                    "<< /Encoding /Identity-H /DescendantFonts [78 0 R] "
                    "/ToUnicode 8453 0 R >>"
                ),
                78: (
                    "<< /Subtype /CIDFontType2 /CIDToGIDMap /Identity "
                    "/FontDescriptor << >> >>"
                ),
            }[xref]

        def xref_stream(self, xref: int) -> bytes:
            assert xref == 8453
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            assert xref == 8453
            return True

        def save(self, buffer: object) -> None:
            buffer.write(b"%PDF-1.4")  # type: ignore[attr-defined]

        def close(self) -> None:
            return None

    reopened_doc = object()
    pdf_map = {83: "ि", 108: "र्"}
    font_map = {83: "त", 108: "ि"}
    monkeypatch.setattr(
        kalimati,
        "_parse_tounicode_cmap",
        lambda _stream: dict(pdf_map),
    )
    monkeypatch.setattr(
        kalimati,
        "_get_font_correction_map",
        lambda _doc, _xref: dict(font_map),
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
        lambda _doc, xref, correction, *, font_name, allow_gid_exceptions: (
            patched_maps.append(
                (xref, dict(correction), font_name, allow_gid_exceptions)
            )
        ),
    )
    monkeypatch.setattr(kalimati.fitz, "open", lambda *args, **kwargs: reopened_doc)

    repaired, needs_reorder = kalimati.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert repaired is reopened_doc
    assert needs_reorder is True
    assert patched_maps == [
        (
            8453,
            {
                83: kalimati._PUA_KOKILA_TA,
                108: kalimati._PUA_KOKILA_IKAR,
            },
            "Kokila-BoldItalic",
            False,
        )
    ]
