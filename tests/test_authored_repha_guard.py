"""A GUESSED vowel sign may not overwrite an authored repha; an exact one may.

`fix_kalimati_cmap` rewrites every font in a document it runs on, and where an embedded
program has no cmap entry for a glyph, `_infer_mark_variants` guesses the nearest of
five dependent vowel signs from glyph *metrics*. That guess used to overwrite the PDF's
own `/ToUnicode` value even when the authored value was a repha, which on OAG documents
5471/5487/5492/5493 destroyed correctly spelled words -- `आर्थिक` 49/52/56/62 -> 2/2/2/5.

All three conditions are load-bearing, and each was added because dropping it regressed
a measured population:

  * the CATEGORY test -- a bare vowel sign, never a consonant-bearing value. On the 64
    no-gate Kokila documents the consonant class is 111 GIDs / 13,354 drawn glyphs and
    carries the repair's whole gain; the vowel-sign class is 12 GIDs / 1,106.
  * the PROVENANCE test -- a metric guess, never an exact reading of the embedded
    program. Declining exact readings as well cost 68 canonical repha words on the
    55-document gated sample and moved document 4070 from clean to suspect.
  * the FACE test -- an unrouted face only. Provenance alone still declined on Kalimati
    GID 466/467, 7,180 drawn glyphs over 15 gated documents, where the guess is right
    and the authored value wrong: document 5403 lost `आर्थिक` 51 -> 6 and `निर्माण`
    66 -> 3. Same failure mode this guard exists to prevent, mirrored.

⚠️ No threshold separates these populations and none is used. Their well-formedness
rates overlap completely -- the authored repha lands on a consonant 0.399 of the time on
5471, where declining is right, and 1.000 of the time on 4070, where it is wrong.

Every repha here is an explicit code-point literal rather than `_RA + _VIRAMA`. A
computed reference would move with the module: rebind `_RA` and both guard and test
change together, so the suite would stay green in a state where the guard no longer
recognises a real repha.
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


def reconstruction(
    values: dict[int, str], guessed: set[int]
) -> kalimati._Reconstruction:
    """A correction map that remembers which of its values were metric guesses."""

    built = kalimati._Reconstruction(values)
    built.metric_guessed = frozenset(guessed)
    return built


@pytest.mark.parametrize(
    ("pdf_value", "correct_value", "matches"),
    [
        # The measured harm: a claim that the repha glyph is really a vowel sign.
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
def test_only_a_bare_vowel_sign_claim_over_an_authored_repha_matches(
    pdf_value: str,
    correct_value: str,
    matches: bool,
) -> None:
    assert (
        kalimati._rewrite_discards_authored_repha(pdf_value, correct_value) is matches
    )


def test_an_exact_vowel_sign_reading_is_believed_and_a_guessed_one_is_not() -> None:
    """The condition that separates the two measured populations.

    The category test cannot tell an outline-digest match from a metric guess, and on
    the gated Kalimati population every vowel-sign claim over an authored repha is an
    exact one. Declining by category alone regressed that population.
    """

    pdf_map = {10: REPHA, 11: REPHA}
    correction_map = {10: IKAR, 11: IKAR}

    exact_only = kalimati._decline_authored_repha_rewrites(
        pdf_map, correction_map, font_name="Mangal", guessed=frozenset()
    )
    ten_guessed = kalimati._decline_authored_repha_rewrites(
        pdf_map, correction_map, font_name="Mangal", guessed={10}
    )
    both_guessed = kalimati._decline_authored_repha_rewrites(
        pdf_map, correction_map, font_name="Mangal", guessed={10, 11}
    )

    assert exact_only == {10: IKAR, 11: IKAR}
    assert ten_guessed == {11: IKAR}
    assert both_guessed == {}


def test_declining_drops_only_the_offending_entry() -> None:
    pdf_map = {10: REPHA, 11: REPHA, 12: IKAR, 13: KA}
    correction_map = {10: IKAR, 11: YA, 12: EKAR, 13: SHA, 99: IKAR}

    kept = kalimati._decline_authored_repha_rewrites(
        pdf_map, correction_map, font_name="Mangal", guessed=set(correction_map)
    )

    assert kept == {11: YA, 12: EKAR, 13: SHA, 99: IKAR}


def test_a_gid_the_pdf_never_authored_is_always_filled() -> None:
    """A fill cannot discard anything, so the guard must not touch one."""

    kept = kalimati._decline_authored_repha_rewrites(
        {}, {7: IKAR, 8: UKAR}, font_name="Mangal", guessed={7, 8}
    )

    assert kept == {7: IKAR, 8: UKAR}


def test_a_map_with_nothing_to_decline_is_returned_unchanged() -> None:
    correction_map = {1: YA, 2: KA, 3: IKAR}

    kept = kalimati._decline_authored_repha_rewrites(
        {1: REPHA, 3: KA},
        correction_map,
        font_name="Mangal",
        guessed=set(correction_map),
    )

    assert kept is correction_map


def test_an_exempt_gid_keeps_its_reconstruction() -> None:
    """Only a separately corroborated GID may override the guard."""

    pdf_map = {83: IKAR, 108: REPHA, 300: REPHA}
    correction_map = {83: "त", 108: IKAR, 300: IKAR}
    guessed = set(correction_map)

    guarded = kalimati._decline_authored_repha_rewrites(
        pdf_map, correction_map, font_name="Mangal", guessed=guessed
    )
    exempted = kalimati._decline_authored_repha_rewrites(
        pdf_map,
        correction_map,
        font_name="Mangal",
        guessed=guessed,
        exempt=kalimati._KOKILA_DISPLACEMENT_GIDS,
    )

    # Both 108 and 300 carry the declined shape, so both go.
    assert guarded == {83: "त"}
    # The exemption restores GID 108 and nothing else: 300 stays declined.
    assert exempted == {83: "त", 108: IKAR}


def test_a_plain_mapping_reports_no_guesses() -> None:
    """The compatibility contract that keeps a dozen existing stubs working.

    `_get_font_correction_map` is monkeypatched with a plain-dict lambda in fourteen
    places across six test files. Provenance rides on the mapping, so a plain dict has
    to read as "nothing was guessed" -- the guard then declines nothing, which is the
    behaviour those tests were written against.
    """

    assert kalimati._reconstruction_guesses({1: IKAR}) == frozenset()
    assert kalimati._reconstruction_guesses(reconstruction({1: IKAR}, {1})) == {1}


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
        kalimati._decline_authored_repha_rewrites(
            pdf_map,
            {10: IKAR, 11: YA, 12: SHA},
            font_name="Mangal",
            guessed={10, 11, 12},
        ),
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


def _run_fix(
    monkeypatch: pytest.MonkeyPatch,
    doc: object,
    pdf_map: dict[int, str],
    font_map: kalimati._Reconstruction,
) -> list[dict[int, str]]:
    captured: list[dict[int, str]] = []
    monkeypatch.setattr(kalimati, "_parse_tounicode_cmap", lambda _s: dict(pdf_map))
    monkeypatch.setattr(kalimati, "_get_font_correction_map", lambda _d, _x: font_map)
    monkeypatch.setattr(kalimati, "_collect_trace_fallback_map", lambda _d, _n: {})
    monkeypatch.setattr(kalimati, "_get_fontfile_xref", lambda _d, _x: None)
    monkeypatch.setattr(
        kalimati,
        "_patch_single_cmap",
        lambda _doc, _xref, correction, *, font_name, allow_gid_exceptions: (
            captured.append(dict(correction))
        ),
    )
    monkeypatch.setattr(kalimati.fitz, "open", lambda *a, **k: object())
    kalimati.fix_kalimati_cmap(doc)  # type: ignore[arg-type]
    return captured


def test_a_proven_pair_survives_while_an_unrelated_guess_is_declined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole-map branch of the Kokila displacement, guarded.

    With three meaningful diffs and no corroborated 214/195,
    `_scope_kokila_displacement_corrections` hands the *full* embedded map through, so
    GID 108's authored `र्` is rewritten to a bare `ि` on purpose -- that pair is
    corroborated per face. GID 300 carries the identical shape from a metric guess with
    no corroboration behind it and must be declined; GID 301 carries it from an exact
    reading and must not.
    """

    captured = _run_fix(
        monkeypatch,
        _kokila_doc(),
        {83: IKAR, 108: REPHA, 300: REPHA, 301: REPHA},
        reconstruction({83: "त", 108: IKAR, 300: IKAR, 301: IKAR}, {108, 300}),
    )

    assert captured == [{83: "त", 108: IKAR, 301: IKAR}]


def test_a_non_kokila_face_gets_no_exemption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exemption is face-scoped, so the same GID pair on Mangal is guarded."""

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            assert full
            return [(77, "ttf", "Type0", "BCDGEE+Mangal", "Identity-H")]

    doc = _kokila_doc()
    monkeypatch.setattr(type(doc), "__getitem__", lambda _s, _i: FakePage())

    captured = _run_fix(
        monkeypatch,
        doc,
        {83: IKAR, 108: REPHA, 300: REPHA, 301: KA},
        reconstruction({83: "त", 108: IKAR, 300: IKAR, 301: YA}, {108, 300, 301}),
    )

    assert captured == [{83: "त", 301: YA}]


def test_the_simple_font_path_declines_nothing_because_its_faces_are_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring is there; the face condition makes it inert, and that is correct.

    `fix_kalimati_cmap` admits a non-Type0 font only when `_is_named_repair_font` accepts
    it, and every family that predicate accepts is routed -- so on this branch
    `_face_reconstruction_is_unvalidated` is always False and nothing is ever declined.
    This is the published Kalimati/Lohit population, and it is exactly the population the
    face condition exists to leave alone: declining there destroyed `आर्थिक` 51 -> 6 and
    `निर्माण` 66 -> 3 on OAG document 5403.

    The call is still wired, so the branch is covered the moment that admission gate
    widens; this test pins that it is currently a no-op rather than silently broken.
    """

    captured: list[dict[int, str]] = []
    pdf_map = {10: REPHA, 11: REPHA, 12: KA}

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
        kalimati,
        "_get_font_correction_map",
        lambda _d, _x: reconstruction({10: IKAR, 11: YA, 12: SHA}, {10, 11, 12}),
    )
    monkeypatch.setattr(
        kalimati,
        "_get_simple_font_correction_map",
        lambda _d, _x, gid_map: gid_map,
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

    # GID 10 keeps its guessed `ि` because Kalimati is a routed face.
    assert captured == [{10: IKAR, 11: YA, 12: SHA}]


def test_simple_font_translation_re_expresses_provenance_in_character_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A simple font's map is re-keyed from GIDs to codes, and so is the guessed set.

    Without the translation the guard would look GIDs up in a code-keyed map, find
    nothing, and decline nothing -- silently off on the whole simple-font path.
    """

    class FakeCmapTable:
        cmap = {65: "gidA", 66: "gidB", 67: "gidC"}

        def isUnicode(self) -> bool:  # noqa: N802 - fontTools spelling
            return False

    class FakeCmap:
        tables = [FakeCmapTable()]

    class FakeFont:
        def getGlyphOrder(self) -> list[str]:  # noqa: N802 - fontTools spelling
            return ["gidA", "gidB", "gidC"]

        def __contains__(self, key: str) -> bool:
            return key == "cmap"

        def __getitem__(self, key: str) -> FakeCmap:
            assert key == "cmap"
            return FakeCmap()

        def close(self) -> None:
            return None

    class FakeDoc:
        def xref_stream(self, xref: int) -> bytes:
            return b"font-bytes"

    monkeypatch.setattr(
        kalimati, "_simple_font_uses_embedded_encoding", lambda _d, _x: True
    )
    monkeypatch.setattr(kalimati, "_resolve_fontfile2_xref", lambda _d, _x: 9)
    monkeypatch.setattr("fontTools.ttLib.TTFont", lambda *a, **k: FakeFont())

    translated = kalimati._get_simple_font_correction_map(  # type: ignore[arg-type]
        FakeDoc(),
        1,
        reconstruction({0: IKAR, 1: YA, 2: SHA}, {0, 2}),
    )

    assert translated == {65: IKAR, 66: YA, 67: SHA}
    # GID 0 -> code 65 and GID 2 -> code 67 were the guesses.
    assert kalimati._reconstruction_guesses(translated) == {65, 67}


@pytest.mark.parametrize(
    ("font_name", "unvalidated"),
    [
        # Faces `fix_kalimati_cmap` rewrites but was never measured against. These are
        # where every recovered word came from.
        ("Mangal", True),
        ("Mangal-Bold", True),
        ("Mangal,Bold", True),
        ("Arial Unicode MS", True),
        ("ArialUnicodeMS", True),
        ("NirmalaUI", True),
        # Routed families: the repair carries a reference table and measured GID space
        # for these, and declining their guesses destroys correct words.
        ("Kalimati", False),
        ("Kalimati-Bold", False),
        ("Lohit-Devanagari", False),
        # Family boundary, so a longer name that merely contains one is NOT routed.
        ("NotKalimati", True),
        ("KalimatiExtra", True),
        ("LohitExtra", True),
    ],
)
def test_only_an_unrouted_face_is_unvalidated(
    font_name: str, unvalidated: bool
) -> None:
    assert kalimati._face_reconstruction_is_unvalidated(font_name) is unvalidated


def test_every_routed_family_is_treated_as_validated() -> None:
    """The coupling, asserted rather than left implicit.

    The guard's scope is the COMPLEMENT of `_KNOWN_BROKEN_CMAP`, so routing a family
    turns the guard off for it. Stating it as an invariant means the day `mangal` is
    added, whoever adds it sees that documents 5471/5487/5492/5493 stop being protected.
    """

    for family in kalimati._KNOWN_BROKEN_CMAP:
        assert kalimati._face_reconstruction_is_unvalidated(family) is False


def test_a_routed_face_keeps_every_guessed_rewrite() -> None:
    """Same inputs, two faces, opposite outcomes -- the whole point of the condition."""

    pdf_map = {466: REPHA}
    correction_map = {466: IKAR}

    unrouted = kalimati._decline_authored_repha_rewrites(
        pdf_map, correction_map, font_name="Mangal", guessed={466}
    )
    routed = kalimati._decline_authored_repha_rewrites(
        pdf_map, correction_map, font_name="Kalimati", guessed={466}
    )

    assert unrouted == {}
    assert routed is correction_map


def test_provenance_follows_the_reconstruction_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real composition inside `_get_font_correction_map`, not a supplied set.

    Every other test here hands the guard a provenance set directly, so the code that
    DERIVES that set was uncovered -- and mutation showed it: turning
    `guessed -= set(from_cmap)` into `guessed |= set(from_cmap)` marks every
    cmap-sourced GID as a guess, which would make the guard decline exact readings, and
    the whole suite stayed green.

    The four sources and their precedence, each represented once:

        gid 1  the font's own cmap                        exact    -> not guessed
        gid 2  reference table, overriding an inference    exact    -> not guessed
        gid 3  inference, overriding GSUB                  GUESS    -> guessed
        gid 4  reference table alone                       exact    -> not guessed
    """

    class FakeFont:
        def getGlyphOrder(self):  # noqa: N802 - fontTools spelling
            return ["gid0", "uni0915", "glyph00002", "glyph00003", "glyph00004"]

        def close(self) -> None:
            return None

    class FakeDoc:
        def xref_stream(self, xref: int) -> bytes:
            return b"font-bytes"

    monkeypatch.setattr(kalimati, "_resolve_fontfile2_xref", lambda _d, _x: 9)
    monkeypatch.setattr("fontTools.ttLib.TTFont", lambda *a, **k: FakeFont())
    monkeypatch.setattr(kalimati, "_safe_get_best_cmap", lambda _f: {0x0915: "uni0915"})
    monkeypatch.setattr(
        kalimati, "_infer_mark_variants", lambda _f, _o, _c: {2: IKAR, 3: EKAR}
    )
    monkeypatch.setattr(kalimati, "_analyze_gsub", lambda _f, _o, _c: {3: "ख्"})
    monkeypatch.setattr(
        kalimati,
        "_kalimati_reference_map",
        lambda _f, skip=frozenset(): {2: "ी", 4: "ग"},
    )

    built = kalimati._get_font_correction_map(FakeDoc(), 1)  # type: ignore[arg-type]

    assert built == {1: "क", 2: "ी", 3: EKAR, 4: "ग"}
    assert kalimati._reconstruction_guesses(built) == {3}
