"""The measured GID 566 needs its authored consonant only after ``र्``."""

from __future__ import annotations

from likhit.extractors import kalimati


class _FakeDoc:
    def __init__(self, mapping: dict[int, str]) -> None:
        self._stream = kalimati._build_cmap_stream(mapping)
        self.written: bytes | None = None

    def xref_stream(self, _xref: int) -> bytes:
        return self._stream

    def update_stream(self, _xref: int, data: bytes) -> None:
        self.written = data


def _patched(
    pdf_map: dict[int, str],
    correction: dict[int, str],
    *,
    font_name: str = "Kalimati",
    allow_gid_exceptions: bool = True,
) -> dict[int, str]:
    doc = _FakeDoc(pdf_map)
    kalimati._patch_single_cmap(  # type: ignore[arg-type]
        doc,
        1,
        correction,
        font_name=font_name,
        allow_gid_exceptions=allow_gid_exceptions,
    )
    assert doc.written is not None
    return kalimati._parse_tounicode_cmap(doc.written)


def test_gid_566_keeps_provenance_until_text_context_is_known() -> None:
    patched = _patched({566: "ने"}, {566: "े"})

    assert patched[566] == kalimati._PUA_CONTEXTUAL_NE
    assert kalimati.reorder_devanagari(f"गर्{patched[566]}") == "गर्ने"
    assert kalimati.reorder_devanagari(f"द{patched[566]}") == "दे"
    assert kalimati._meaningful_cmap_diff_count({566: "ने"}, {566: "े"}) == 1


def test_contextual_ne_preserves_an_authored_word_boundary() -> None:
    marker = _patched({566: "ने"}, {566: "े"})[566]

    reordered = kalimati.reorder_devanagari(f"गर् {marker}")

    assert reordered == "गर् ने"


def test_contextual_ne_preserves_authored_ne_at_hard_boundaries() -> None:
    marker = _patched({566: "ने"}, {566: "े"})[566]

    for prefix in ("", "(", "|", "।", "A", "१", "अ", "दे"):
        assert kalimati.reorder_devanagari(prefix + marker) == prefix + "ने"


def test_contextual_ne_accepts_both_nukta_consonant_encodings() -> None:
    marker = _patched({566: "ने"}, {566: "े"})[566]

    assert kalimati.reorder_devanagari(f"क़{marker}") == "क़े"
    assert kalimati.reorder_devanagari(f"क़{marker}") == "क़े"
    assert kalimati.reorder_devanagari(f"क़र्{marker}") == "क़र्ने"
    assert kalimati.reorder_devanagari(f"क़र्{marker}") == "क़र्ने"


def test_gid_566_is_not_contextual_on_an_unrelated_font_face() -> None:
    patched = _patched(
        {566: "ने"},
        {566: "े"},
        font_name="Kokila",
    )

    assert patched[566] == "े"


def test_gid_566_is_not_contextual_without_identity_gid_evidence() -> None:
    patched = _patched(
        {566: "ने"},
        {566: "े"},
        allow_gid_exceptions=False,
    )

    assert patched[566] == "े"


def test_same_mapping_shape_on_an_unproven_gid_stays_a_bare_matra() -> None:
    patched = _patched({565: "ने"}, {565: "े"})

    assert patched[565] == "े"


def test_other_consonant_deleting_corrections_are_not_suppressed() -> None:
    patched = _patched(
        {204: "ङ्ख", 226: "र्र", 682: "र्छ्य"},
        {204: "ङ्", 226: "र्", 682: "छ्य"},
    )

    assert patched == {204: "ङ्", 226: kalimati._PUA_REPH, 682: "छ्य"}
    assert kalimati.reorder_devanagari(f"क{patched[226]}") == "र्क"
