"""The reconstruction may not re-identify an authored repha as a bare vowel sign.

`fix_kalimati_cmap` rewrites every font in a document it runs on, and where an
embedded program has no cmap entry for a glyph, `_infer_mark_variants` guesses the
nearest of five dependent vowel signs from glyph metrics. That guess used to overwrite
the PDF's own `/ToUnicode` value even when the authored value was a repha, which on
OAG documents 5471/5487/5492/5493 destroyed correctly spelled words -- `आर्थिक` 49 -> 2
and 62 -> 5 -- while the repair's matra gain on the same documents came from a
different class of rewrite entirely.

Every repha here is written as explicit code-point escapes rather than computed from
``kalimati._RA + kalimati._VIRAMA``. A computed reference would move with the module:
rebind ``_RA`` and both the guard and its test change together, so the test would pass
in a state where the guard no longer recognises a real repha.
"""

from __future__ import annotations

import pytest

from likhit.extractors import kalimati

REPHA = "र्"  # र + virama
IKAR = "ि"  # ि
UKAR = "ु"  # ु
EKAR = "े"  # े
YA = "य"  # य
SHA = "ष"  # ष
KA = "क"  # क


@pytest.mark.parametrize(
    ("pdf_value", "correct_value", "declines"),
    [
        # The measured harm: a metric guess re-labels the repha glyph a vowel sign.
        (REPHA, IKAR, True),
        (REPHA, UKAR, True),
        (REPHA, EKAR, True),
        # Two vowel signs is still only vowel signs.
        (REPHA, IKAR + EKAR, True),
        # An authored repha inside a longer cluster value counts just the same.
        (KA + REPHA, IKAR, True),
        # The repair's whole measured gain: an authored repha that the embedded
        # program says is a consonant, because the authored table is shifted.
        (REPHA, YA, False),
        (REPHA, SHA, False),
        (REPHA, KA + "्", False),
        # The reconstruction agrees there is a repha, so nothing is being discarded.
        (REPHA, REPHA, False),
        (REPHA, KA + REPHA, False),
        # No authored repha to protect.
        (IKAR, EKAR, False),
        (KA, IKAR, False),
        # An empty correction value is not a vowel-sign claim.
        (REPHA, "", False),
        # A reordering marker is not a bare vowel sign: the Kokila displacement pair
        # resolves `_PUA_KOKILA_IKAR` back to a repha, so it must pass through.
        (REPHA, kalimati._PUA_KOKILA_IKAR, False),
        (REPHA, kalimati._PUA_IKAR, False),
    ],
)
def test_only_a_bare_vowel_sign_claim_over_an_authored_repha_is_declined(
    pdf_value: str,
    correct_value: str,
    declines: bool,
) -> None:
    assert (
        kalimati._rewrite_discards_authored_repha(pdf_value, correct_value) is declines
    )


def test_declining_drops_only_the_offending_entry() -> None:
    pdf_map = {10: REPHA, 11: REPHA, 12: IKAR, 13: KA}
    correction_map = {10: IKAR, 11: YA, 12: EKAR, 13: SHA, 99: IKAR}

    kept = kalimati._decline_authored_repha_rewrites(pdf_map, correction_map)

    assert kept == {11: YA, 12: EKAR, 13: SHA, 99: IKAR}


def test_a_gid_the_pdf_never_authored_is_always_filled() -> None:
    """A fill cannot discard anything, so the guard must not touch one."""

    kept = kalimati._decline_authored_repha_rewrites({}, {7: IKAR, 8: UKAR})

    assert kept == {7: IKAR, 8: UKAR}


def test_a_map_with_nothing_to_decline_is_returned_unchanged() -> None:
    correction_map = {1: YA, 2: KA, 3: IKAR}

    kept = kalimati._decline_authored_repha_rewrites({1: REPHA, 3: KA}, correction_map)

    assert kept is correction_map


def test_an_exempt_gid_keeps_its_reconstruction() -> None:
    """Only a separately corroborated GID may override the guard."""

    pdf_map = {83: IKAR, 108: REPHA, 300: REPHA}
    correction_map = {83: "त", 108: IKAR, 300: IKAR}

    guarded = kalimati._decline_authored_repha_rewrites(pdf_map, correction_map)
    exempted = kalimati._decline_authored_repha_rewrites(
        pdf_map,
        correction_map,
        exempt=kalimati._KOKILA_DISPLACEMENT_GIDS,
    )

    # Both 108 and 300 carry the declined shape, so both go.
    assert guarded == {83: "त"}
    # The exemption restores GID 108 and nothing else: 300 stays declined.
    assert exempted == {83: "त", 108: IKAR}


def test_a_declined_gid_keeps_the_authored_value_in_the_patched_cmap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the entry must not drop the GID from the CMap that gets written.

    `_patch_single_cmap`'s fill loop only writes GIDs the PDF has no entry for, so a
    declined GID has to survive at its authored value. If it were removed from the
    written map instead, the glyph would decode to nothing at all -- strictly worse
    than either candidate.
    """

    written: list[dict[int, str]] = []

    class FakeDoc:
        def xref_stream(self, xref: int) -> bytes:
            assert xref == 10
            return b"unused"

        def update_stream(self, xref: int, stream: bytes) -> None:
            assert xref == 10

    pdf_map = {10: REPHA, 11: REPHA, 12: KA}
    monkeypatch.setattr(kalimati, "_parse_tounicode_cmap", lambda _s: dict(pdf_map))
    monkeypatch.setattr(
        kalimati,
        "_build_cmap_stream",
        lambda mapping: written.append(dict(mapping)) or b"patched",
    )

    kalimati._patch_single_cmap(  # type: ignore[arg-type]
        FakeDoc(),
        10,
        kalimati._decline_authored_repha_rewrites(pdf_map, {10: IKAR, 11: YA, 12: SHA}),
    )

    assert written == [{10: REPHA, 11: YA, 12: SHA}]


def test_the_declined_disagreement_still_counts_as_a_meaningful_diff() -> None:
    """The floor and the Kokila fingerprint both read the unfiltered reconstruction.

    `_meaningful_cmap_diff_count` answers "does the embedded program disagree with the
    authored table", which stays true of a disagreement the guard declines to act on --
    and `_has_ra_virama_ikar_displacement_pair` is built on exactly one such
    disagreement (GID 108, authored `र्`, program `ि`). Filtering the count would
    silently close the repair on those documents.
    """

    assert kalimati._meaningful_cmap_diff_count({10: REPHA}, {10: IKAR}) == 1
    assert (
        kalimati._meaningful_cmap_diff_count(
            {83: IKAR, 108: REPHA}, {83: "त", 108: IKAR}
        )
        == 2
    )


def _kokila_doc() -> object:
    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [(77, "ttf", "Type0", "ABCDEF+Kokila-BoldItalic", "Identity-H")]

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

    return FakeDoc()


def test_a_proven_pair_survives_while_an_unrelated_repha_claim_is_declined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole-map branch of the Kokila displacement, guarded.

    With three meaningful diffs and no corroborated 214/195,
    `_scope_kokila_displacement_corrections` hands the *full* embedded map through, so
    GID 108's authored `र्` is rewritten to a bare `ि` on purpose -- that pair is
    corroborated per face. GID 300 carries the identical shape with no corroboration
    behind it and must be declined.
    """

    captured: list[dict[int, str]] = []
    pdf_map = {83: IKAR, 108: REPHA, 300: REPHA}
    font_map = {83: "त", 108: IKAR, 300: IKAR}

    monkeypatch.setattr(kalimati, "_parse_tounicode_cmap", lambda _s: dict(pdf_map))
    monkeypatch.setattr(
        kalimati, "_get_font_correction_map", lambda _doc, _xref: dict(font_map)
    )
    monkeypatch.setattr(kalimati, "_collect_trace_fallback_map", lambda _doc, _n: {})
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: None)
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, _xref, correction, *, font_name, allow_gid_exceptions: (
            captured.append(dict(correction))
        ),
    )
    monkeypatch.setattr(kalimati.fitz, "open", lambda *a, **k: object())

    kalimati.fix_kalimati_cmap(_kokila_doc())  # type: ignore[arg-type]

    assert captured == [{83: "त", 108: IKAR}]


def test_the_simple_font_path_is_guarded_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named TrueType face takes a different branch and must not be left unguarded.

    `fix_kalimati_cmap` admits a non-Type0 font only when it is a named repair face, so
    this branch is the one that runs on the published Kalimati/Lohit population -- the
    largest group the guard can affect. It reaches `_patch_single_cmap` by its own call,
    not through `to_unicode_maps`, so the guard has to be wired there separately.
    """

    captured: list[dict[int, str]] = []
    pdf_map = {10: REPHA, 11: REPHA, 12: KA}
    correction_map = {10: IKAR, 11: YA, 12: SHA}

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [(77, "ttf", "TrueType", "ABCDEF+Kalimati", "WinAnsi")]

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def xref_object(self, xref: int, compressed: bool = False) -> str:
            assert not compressed
            return "<< /Subtype /TrueType /ToUnicode 8453 0 R >>"

        def xref_stream(self, xref: int) -> bytes:
            return b"unused"

        def xref_is_stream(self, xref: int) -> bool:
            return xref == 8453

        def save(self, buffer: object) -> None:
            buffer.write(b"%PDF-1.4")  # type: ignore[attr-defined]

        def close(self) -> None:
            return None

    monkeypatch.setattr(kalimati, "_parse_tounicode_cmap", lambda _s: dict(pdf_map))
    monkeypatch.setattr(
        kalimati, "_get_font_correction_map", lambda _doc, _xref: dict(correction_map)
    )
    monkeypatch.setattr(
        kalimati,
        "_get_simple_font_correction_map",
        lambda _doc, _xref, gid_map: dict(gid_map),
    )
    monkeypatch.setattr(
        kalimati, "_simple_font_correction_is_credible", lambda _pdf, _corr: True
    )
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, _xref, correction: captured.append(dict(correction)),
    )
    monkeypatch.setattr(kalimati.fitz, "open", lambda *a, **k: object())

    kalimati.fix_kalimati_cmap(FakeDoc())  # type: ignore[arg-type]

    assert captured == [{11: YA, 12: SHA}]


def test_a_non_kokila_face_gets_no_exemption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exemption is face-scoped, so the same GID pair on Mangal is guarded."""

    captured: list[dict[int, str]] = []
    pdf_map = {83: IKAR, 108: REPHA, 300: REPHA, 301: KA}
    font_map = {83: "त", 108: IKAR, 300: IKAR, 301: YA}

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [(77, "ttf", "Type0", "BCDGEE+Mangal", "Identity-H")]

    doc = _kokila_doc()
    monkeypatch.setattr(type(doc), "__getitem__", lambda _s, _i: FakePage())
    monkeypatch.setattr(kalimati, "_parse_tounicode_cmap", lambda _s: dict(pdf_map))
    monkeypatch.setattr(
        kalimati, "_get_font_correction_map", lambda _doc, _xref: dict(font_map)
    )
    monkeypatch.setattr(kalimati, "_collect_trace_fallback_map", lambda _doc, _n: {})
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _doc, _xref: None)
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, _xref, correction, *, font_name, allow_gid_exceptions: (
            captured.append(dict(correction))
        ),
    )
    monkeypatch.setattr(kalimati.fitz, "open", lambda *a, **k: object())

    kalimati.fix_kalimati_cmap(doc)  # type: ignore[arg-type]

    assert captured == [{83: "त", 301: YA}]
