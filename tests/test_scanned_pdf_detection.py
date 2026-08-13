"""Tests for scanned-PDF (Part A) and content-based legacy-font (Part B) detection.

These exercise the extraction fixes for Nepal Police CIB press releases: a scanned
raster carrying a non-embedded core-font "decoy" text layer must be routed to OCR
(never emitted as garbage), while a genuinely mislabeled legacy font must still be
rescued. Synthetic, PII-free PDFs stand in for the git-ignored CIB originals; the
real ones are covered in ``tests/integration/test_cib_pdfs.py`` when present.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import fitz
import pytest

from likhit.errors import ScannedPdfError
from likhit.extractors.font_based import (
    FontBasedStrategy,
    _choose_fragment_text,
    _has_severe_noise,
    _is_probably_legacy_ascii,
    _map_ranking_key,
    _nepali_validity,
    _passes_content_legacy_gate,
    _text_quality_penalty,
    choose_legacy_map,
    detect_content_legacy_fonts,
)
from likhit.extractors.font_classifier import (
    IMAGE_ONLY,
    SCANNED_DECOY_TEXT,
    _is_non_embedded_core_font,
    classify_ocr_page,
    is_core_font_name,
    scan_ocr_pages,
)
from likhit.extractors.legacy_maps import ALL_MAP_KEYS, get_converter_for_map
from tests.synthetic_pdfs import (
    build_legacy_then_english_pdf,
    build_mislabeled_preeti_pdf,
    build_mixed_scan_and_text_pdf,
    build_pure_scan_pdf,
    build_scanned_decoy_pdf,
    build_subset_named_english_pdf,
    build_subset_named_preeti_pdf,
    build_subset_named_spins_pdf,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "samples"


def _has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def _write_pdf(tmp_path: Path, raw: bytes, name: str = "synthetic.pdf") -> str:
    path = tmp_path / name
    path.write_bytes(raw)
    return str(path)


# --- Part A: scanned-raster / decoy-layer detection ---------------------------


def test_scanned_decoy_pdf_raises_scanned_error(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_scanned_decoy_pdf(page_count=2))

    with pytest.raises(ScannedPdfError) as exc_info:
        FontBasedStrategy().extract_text(path)

    assert exc_info.value.needs_ocr_pages == [1, 2]


def test_pure_scan_pdf_raises_scanned_error(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_pure_scan_pdf())

    with pytest.raises(ScannedPdfError) as exc_info:
        FontBasedStrategy().extract_text(path)

    assert exc_info.value.needs_ocr_pages == [1]


def test_scanned_decoy_never_emits_decoy_text(tmp_path: Path) -> None:
    # The decoy keystrokes must never leak into extracted text under any path.
    path = _write_pdf(tmp_path, build_scanned_decoy_pdf(page_count=1))
    try:
        result = FontBasedStrategy().extract_text(path)
    except ScannedPdfError:
        return
    assert "qt+:" not in result.raw_text
    assert "$TTDtit" not in result.raw_text


def test_mixed_document_keeps_real_page_and_flags_scanned_page(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_mixed_scan_and_text_pdf())

    result = FontBasedStrategy().extract_text(path)

    # Page 1 (decoy) is flagged for OCR and suppressed; page 2 survives.
    assert result.needs_ocr_pages == [1]
    assert "ordinary born-digital paragraph" in result.raw_text
    assert "qt+:" not in result.raw_text


def test_classify_ocr_page_labels_synthetic_pages(tmp_path: Path) -> None:
    decoy = fitz.open(stream=build_scanned_decoy_pdf(page_count=1), filetype="pdf")
    scan = fitz.open(stream=build_pure_scan_pdf(), filetype="pdf")
    text = fitz.open(stream=build_mislabeled_preeti_pdf(), filetype="pdf")
    try:
        assert classify_ocr_page(decoy, 0) == SCANNED_DECOY_TEXT
        assert classify_ocr_page(scan, 0) == IMAGE_ONLY
        # A born-digital page (no full-page raster) is never an OCR page.
        assert classify_ocr_page(text, 0) is None
    finally:
        decoy.close()
        scan.close()
        text.close()


def test_is_non_embedded_core_font_matches_synthetic_helvetica() -> None:
    doc = fitz.open(stream=build_scanned_decoy_pdf(page_count=1), filetype="pdf")
    try:
        fonts = doc[0].get_fonts(full=True)
        assert fonts, "expected a decoy font on the page"
        assert all(_is_non_embedded_core_font(doc, font) for font in fonts)
    finally:
        doc.close()


def test_is_core_font_name_recognizes_standard_families() -> None:
    assert is_core_font_name("Helvetica")
    assert is_core_font_name("ABCDEF+Arial-BoldMT")
    assert is_core_font_name("Times New Roman,Bold")
    assert not is_core_font_name("ABCDEE+Kalimati")
    assert not is_core_font_name("BOFDOE+Preeti")


# --- Part A must NOT misfire on clean / legacy born-digital samples -----------


@pytest.mark.parametrize(
    "sample_name",
    ["pressrelease.pdf", "Press Release.pdf", "kanunpatrika.pdf"],
)
def test_clean_and_legacy_samples_are_not_flagged_for_ocr(sample_name: str) -> None:
    sample_path = SAMPLES_DIR / sample_name
    if not sample_path.exists():
        pytest.skip(f"sample missing: {sample_name}")

    result = FontBasedStrategy().extract_text(str(sample_path))

    assert result.needs_ocr_pages == []
    assert result.raw_text.strip()


def test_scan_ocr_pages_empty_for_born_digital_sample() -> None:
    sample_path = SAMPLES_DIR / "kanunpatrika.pdf"
    if not sample_path.exists():
        pytest.skip("sample missing: kanunpatrika.pdf")
    doc = fitz.open(str(sample_path))
    try:
        # Note: kanunpatrika is deva=0 legacy AND has non-embedded core fonts,
        # yet its zero image coverage keeps it off the OCR path.
        assert scan_ocr_pages(doc) == {}
    finally:
        doc.close()


# --- Part B: content-based legacy-font detection ------------------------------


def test_choose_legacy_map_accepts_real_preeti() -> None:
    # Real Preeti keystrokes decoding to several dictionary words.
    keystrokes = "g]kfn ;/sf/ cbfnt cg';Gwfg k|ltjfbL e|i6frf/"
    map_key, validity = choose_legacy_map(keystrokes)

    assert map_key == "Preeti"
    assert validity is not None and validity["hits"] >= 2
    assert get_converter_for_map(map_key)(keystrokes).startswith("नेपाल सरकार")


def test_choose_legacy_map_declines_english() -> None:
    map_key, _validity = choose_legacy_map(
        "The quick brown fox jumps over the lazy dog several times over"
    )
    assert map_key is None


def test_nepali_validity_flags_garble_low() -> None:
    # A wrong-map read produces Devanagari code points but no real words.
    garble = "मगचमर्तटर्चमाट म२िष्न्चित्र।८भस्भ्चंष,ष्।क्ष्िँक्ष"
    validity = _nepali_validity(garble)
    assert validity["hits"] == 0
    assert validity["ratio"] > 0.8  # high ratio is a mirage; hits is what matters


def test_is_probably_legacy_ascii() -> None:
    assert _is_probably_legacy_ascii("g]kfn ;/sf/ cbfnt cg';Gwfg")
    assert not _is_probably_legacy_ascii("नेपाल सरकार")  # already Devanagari
    assert not _is_probably_legacy_ascii("   ")


def test_detect_content_legacy_fonts_on_mislabeled_preeti() -> None:
    doc = fitz.open(stream=build_mislabeled_preeti_pdf(), filetype="pdf")
    try:
        assert detect_content_legacy_fonts(doc) == {"Helvetica": "Preeti"}
    finally:
        doc.close()


def test_detect_content_legacy_fonts_on_subset_named_font() -> None:
    # The OAG annual-report shape: the font name is subsetter noise ("TT339t00"),
    # so neither the standard-14 core list nor the legacy-name registry sees it.
    # Only the bytes say Preeti, and that has to be enough.
    doc = fitz.open(stream=build_subset_named_preeti_pdf(), filetype="pdf")
    try:
        assert not is_core_font_name("TT339t00")
        assert detect_content_legacy_fonts(doc) == {"TT339t00": "Preeti"}
    finally:
        doc.close()


def test_detect_content_legacy_fonts_declines_subset_named_english() -> None:
    # The converse: an unrecognisable font name is not evidence. English under
    # "TT339t00" must be left alone, or the widened candidate set would remap
    # every Latin font whose name we do not recognise.
    doc = fitz.open(stream=build_subset_named_english_pdf(), filetype="pdf")
    try:
        assert detect_content_legacy_fonts(doc) == {}
    finally:
        doc.close()


def test_subset_named_preeti_pdf_extracts_as_nepali(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_subset_named_preeti_pdf())

    result = FontBasedStrategy().extract_text(path)

    assert "नेपाल सरकार" in result.raw_text
    assert "प्रतिवादी" in result.raw_text
    assert "g]kfn" not in result.raw_text


def test_subset_named_english_pdf_survives_extraction(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_subset_named_english_pdf())

    result = FontBasedStrategy().extract_text(path)

    assert "English catalogue reference" in result.raw_text
    assert not _has_devanagari(result.raw_text)


def test_detect_content_legacy_fonts_picks_spins_over_preeti() -> None:
    # Detecting "this is legacy" is only half the job: the 2067-2072 annual
    # reports are the Spins layout, and the Preeti map reads their bytes as
    # well-formed Devanagari spelling the WRONG words. Pin the choice, not just
    # the detection.
    doc = fitz.open(stream=build_subset_named_spins_pdf(), filetype="pdf")
    try:
        assert detect_content_legacy_fonts(doc) == {"TT339t00": "Spins"}
    finally:
        doc.close()


def test_subset_named_spins_pdf_recovers_repha(tmp_path: Path) -> None:
    # The six codes Spins rotates put the repha (र्) where Preeti has the
    # anusvara (ं), so every repha-bearing word is where the two maps visibly
    # disagree. Assert both directions: the right spellings present AND the
    # Preeti misreadings absent, since a purity axis passes either one.
    path = _write_pdf(tmp_path, build_subset_named_spins_pdf())

    result = FontBasedStrategy().extract_text(path)

    for correct in ("अर्थ", "कार्यालय", "निर्णय", "वार्षिक"):
        assert correct in result.raw_text
    for preeti_misread in ("अथं", "कायांलय", "निणंय", "वाषिंक"):
        assert preeti_misread not in result.raw_text


def test_spins_does_not_steal_genuine_preeti() -> None:
    # The converse guard on widening ALL_MAP_KEYS: real Preeti keystrokes must
    # still choose Preeti. Reading them as Spins corrupts the other direction
    # (काठमाडौं -> काठमार्डौ, दर्ता -> दता)), so a Spins win here would be a
    # regression on every document the name registry already handled.
    doc = fitz.open(stream=build_mislabeled_preeti_pdf(), filetype="pdf")
    try:
        assert detect_content_legacy_fonts(doc) == {"Helvetica": "Preeti"}
    finally:
        doc.close()

    # Genuine Preeti keystrokes: here the anusvara is "+" and the repha is "{".
    # Spins is the same layout with those two rolled on by one key, so the SAME
    # bytes read as Spins corrupt exactly the words Spins would otherwise fix.
    preeti_bytes = "g]kfn ;/sf/ cbfnt cg';Gwfg k|ltjfbL sf7df8f}+ lhNnf btf{ lg0f{o"
    assert choose_legacy_map(preeti_bytes)[0] == "Preeti"

    preeti_read = get_converter_for_map("Preeti")(preeti_bytes)
    spins_read = get_converter_for_map("Spins")(preeti_bytes)
    assert "काठमाडौं" in preeti_read and "दर्ता" in preeti_read
    assert "काठमाडौं" not in spins_read and "दर्ता" not in spins_read
    # Both reads are pure Devanagari at zero penalty, so purity cannot separate
    # them: the dictionary evidence is the whole of the margin, and it is small.
    assert _nepali_validity(spins_read)["penalty_per_deva"] == 0.0
    assert _nepali_validity(spins_read)["hits"] < _nepali_validity(preeti_read)["hits"]


# --- Part B, VOL-77: what may and may not decide a tie -------------------------
#
# The three cases below are a partition of "the dictionary and the penalty are
# level". Each is a miniature of a shape measured on the OAG corpus, and each
# behaved differently before the fix -- all three chose Preeti, because Preeti is
# ALL_MAP_KEYS[0] and the loop kept the first strict maximum.

_TIE_PREFIX = "g]kfn ;/sf/ cbfnt"  # नेपाल सरकार अदालत -- read the same by every map


def test_devanagari_ratio_breaks_a_hits_and_penalty_tie() -> None:
    # The Ghiring shape (`3585__...Ghiring Gaunpalika`, font "Spins", 303 chars):
    # every map ties at hits=3, penalty=0.0, and only the Devanagari ratio
    # separates them, because the keystrokes ";_Vof" land entirely inside
    # Devanagari under Spins and leave a literal ")" under every other map.
    keystrokes = f"{_TIE_PREFIX} ;_Vof"
    per_map = {
        candidate: _nepali_validity(get_converter_for_map(candidate)(keystrokes))
        for candidate in ALL_MAP_KEYS
    }
    assert len({validity["hits"] for validity in per_map.values()}) == 1
    assert {validity["penalty_per_deva"] for validity in per_map.values()} == {0.0}
    assert per_map["Spins"]["ratio"] > per_map["Preeti"]["ratio"]

    assert choose_legacy_map(keystrokes)[0] == "Spins"
    # The point of the fix, stated as text rather than as a ranking: the word is
    # संख्या ("number"), and Preeti spells it स)ख्या -- well-formed Devanagari, the
    # wrong word, invisible to every purity axis and to a reader.
    assert get_converter_for_map("Spins")(keystrokes).endswith("संख्या")
    assert get_converter_for_map("Preeti")(keystrokes).endswith("स)ख्या")


def test_the_ratio_axis_outranks_the_devanagari_count_and_that_order_decides() -> None:
    """🛑 The fixture above does not isolate the axis it is named for.

    On it Spins wins on `ratio` AND on `devanagari`, so either tie-break alone suffices
    and zeroing either one leaves the full suite green -- measured, 1088 passed both ways.
    But on the real Ghiring document the two axes DISAGREE:

        Spins       key=(3, -0.0, 1.00000, 245)   <- wins on ratio
        PCS NEPALI  key=(3, -0.0, 0.98795, 246)   <- wins on devanagari

    so `devanagari` alone would reinstate exactly the VOL-77 defect this change removes.
    The ORDER of the two tie-breaks is load-bearing on the corpus and nothing tested it.

    This fixture reproduces that shape: appending `))` gives the wrong maps two more
    Devanagari code points than the right one. The mechanism is worth naming, because it
    is why a Devanagari COUNT is the weaker axis -- a wrong map can manufacture more
    Devanagari than the right one. `))` is `००` under Spins (2 code points) and `ण्ण्`
    under PCS NEPALI (4), so PCS NEPALI scores a HIGHER count out of pure garbage while
    still leaving the `)` residue that costs it the ratio.
    """

    keystrokes = f"{_TIE_PREFIX} ;_Vof ))"
    per_map = {
        candidate: _nepali_validity(get_converter_for_map(candidate)(keystrokes))
        for candidate in ALL_MAP_KEYS
    }
    spins, pcs = per_map["Spins"], per_map["PCS NEPALI"]

    # The primary axes still tie, so the tie-breaks are what decide.
    assert spins["hits"] == pcs["hits"]
    assert spins["penalty_per_deva"] == pcs["penalty_per_deva"] == 0.0
    # ...and the two tie-breaks point in OPPOSITE directions. This is the whole point.
    assert spins["ratio"] > pcs["ratio"]
    assert spins["devanagari"] < pcs["devanagari"]

    # ratio is ranked first, so the right map wins.
    assert choose_legacy_map(keystrokes)[0] == "Spins"

    # Stated as text: Spins reads the word, the count-winner does not.
    assert get_converter_for_map("Spins")(keystrokes) == ("नेपाल सरकार अदालत संख्या ००")
    assert get_converter_for_map("PCS NEPALI")(keystrokes) == (
        "नेपाल सरकार अदालत स)ख्या ण्ण्"
    )

    # And the order is asserted structurally, so a reordering of the tuple is caught even
    # if no corpus-shaped fixture happens to cover the pair that reorders.
    #
    # 🛑 Deliberately NOT by reconstructing the whole tuple: later changes in this stack
    # ADD axes to `_map_ranking_key`, so pinning its arity here makes this test fail
    # upstack for a reason that has nothing to do with what it is testing. Compare two
    # validities that differ ONLY in these two axes instead -- that states "ratio
    # outranks devanagari" and stays true however many axes sit above them.
    worse_ratio_higher_count = {
        **spins,
        "ratio": spins["ratio"] - 0.01,
        "devanagari": spins["devanagari"] + 10,
    }
    assert _map_ranking_key(spins) > _map_ranking_key(worse_ratio_higher_count)


def test_choose_legacy_map_abstains_when_every_axis_ties() -> None:
    # "X" is ह् under Preeti and हृ under Kantipur: both pure Devanagari, both two
    # code points, so hits, penalty, ratio and Devanagari count are all identical
    # and the two readings still differ. Nothing but tuple position could pick a
    # winner, so there is no winner to pick.
    keystrokes = f"{_TIE_PREFIX} X"
    readings = {
        get_converter_for_map(candidate)(keystrokes) for candidate in ALL_MAP_KEYS
    }
    assert len(readings) > 1

    map_key, best = choose_legacy_map(keystrokes)
    assert map_key is None
    # Abstention is NOT the gate declining: the best candidate clears it. The
    # keystrokes stay visibly undecoded, which is recoverable; a confident wrong
    # word is not.
    assert best is not None and _passes_content_legacy_gate(best)


def test_identical_readings_are_not_an_ambiguity() -> None:
    # The converse, and the reason abstention compares text rather than counting
    # tied candidates: all six maps tie on every axis here too, but they all
    # decode to the SAME string. Abstaining would throw away a correct decode over
    # a distinction without a difference.
    keystrokes = _TIE_PREFIX
    readings = {
        get_converter_for_map(candidate)(keystrokes) for candidate in ALL_MAP_KEYS
    }
    assert readings == {"नेपाल सरकार अदालत"}

    assert choose_legacy_map(keystrokes)[0] == "Preeti"


def test_all_map_keys_order_does_not_decide_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The invariant behind all three cases above, asserted against the tuple
    # itself rather than through a fixture: reversing the walk order must not
    # change what any document ends up SAYING. If it does, order is evidence again.
    #
    # The invariant is on the text, not on the map key, and the third case is why.
    # When tied candidates decode identically, which of their names is returned is
    # still positional -- reversing turns "Preeti" into "Spins" there. That is
    # harmless by construction (the readings are equal, so the transcript cannot
    # differ) but it does mean the recorded map name is not a stable label for
    # such spans. Asserting on the key would either fail on a harmless difference
    # or force an arbitrary tie-break back into the chooser.
    from likhit.extractors import font_based as font_based_module

    def decode(keystrokes: str) -> str | None:
        map_key, _validity = choose_legacy_map(keystrokes)
        return get_converter_for_map(map_key)(keystrokes) if map_key else None

    cases = [f"{_TIE_PREFIX} ;_Vof", f"{_TIE_PREFIX} X", _TIE_PREFIX]
    before_text = [decode(case) for case in cases]
    assert before_text == [
        "नेपाल सरकार अदालत संख्या",  # ratio decided it
        None,  # abstained
        "नेपाल सरकार अदालत",  # every map agrees
    ]

    # Reversing must actually move Preeti off the head, or this proves nothing.
    reversed_keys = tuple(reversed(ALL_MAP_KEYS))
    assert ALL_MAP_KEYS[0] == "Preeti" and reversed_keys[0] != "Preeti"

    monkeypatch.setattr(font_based_module, "ALL_MAP_KEYS", reversed_keys)
    assert [decode(case) for case in cases] == before_text
    # The evidence-decided cases pin the key too: only the identical-text one is
    # allowed to relabel.
    assert choose_legacy_map(cases[0])[0] == "Spins"
    assert choose_legacy_map(cases[1])[0] is None


# --- Part B, VOL-89: which form of the garble measure may decide ---------------
#
# VOL-77 stopped ALL_MAP_KEYS order from deciding a tie. VOL-89 is the residual it
# does not reach: two candidates that are NOT tied under the old key, separated
# only because the garble count was divided by two different Devanagari counts.
#
# Both fixtures carry the numbers measured on the OAG corpus rather than a
# synthetic span, because the shape needs a map that produces *fewer* Devanagari
# characters and a *higher* Devanagari ratio than its rival, which hand-built
# keystrokes do not reproduce at a penalty the gate still admits. Provenance:
# `runs/vol89/evidence-stride14.json`, re-derived in `FINDING-03-root-cause.md`.


def _validity(
    hits: int, penalty: int, devanagari: int, ratio: float, stranded: int = 0
) -> dict[str, float]:
    """A validity dict as `_nepali_validity` would return it, for ranking tests."""

    return {
        "hits": hits,
        "penalty": penalty,
        "penalty_per_deva": penalty / devanagari if devanagari else float("inf"),
        "devanagari": devanagari,
        "ratio": ratio,
        "stranded": stranded,
    }


def test_equal_garble_counts_do_not_decide_however_they_normalise() -> None:
    # `3222__...faktalung ga.pa`, font "Spins", 757 characters. All six maps score
    # an identical raw penalty of 18; PCS NEPALI won only because 18/576 is less
    # than 18/562. That is a denominator difference, not a garble difference, and
    # it outranked a 1.1-point Devanagari-ratio difference that is real.
    pcs = _validity(hits=5, penalty=18, devanagari=576, ratio=0.966443)
    spins = _validity(hits=5, penalty=18, devanagari=562, ratio=0.977391)
    assert pcs["penalty_per_deva"] < spins["penalty_per_deva"]  # the phantom margin

    # The garble axis must see these as level, so `ratio` is reached and decides.
    assert _map_ranking_key(pcs)[:2] == _map_ranking_key(spins)[:2]
    assert _map_ranking_key(spins) > _map_ranking_key(pcs)

    # 🛑 The OTHER half of this change's thesis, and the half the ranking
    # assertions above cannot reach: the GATE must keep reading the RATE. This
    # fixture is the one that separates them, because 18 is far over the 0.05
    # ceiling read as a count and comfortably under it read as a rate
    # (18/576 = 0.031, 18/562 = 0.032). So a "unification" of the two measures in
    # the gate's direction -- the same conflation this commit removes from the
    # ranking, applied the other way round -- fails here instead of shipping every
    # legacy font that carries a single duplicate-consonant charge as raw ASCII.
    assert _passes_content_legacy_gate(pcs)
    assert _passes_content_legacy_gate(spins)


def test_a_real_difference_in_garble_still_outranks_the_ratio() -> None:
    # The control, and the reason `ratio` is NOT promoted above the garble axis:
    # `4487__...बसबरिया गाउँपालिका`, font "Spins", 2,156 characters. Spins reads a
    # higher Devanagari ratio there and is still the wrong map -- 48 penalty points
    # against PCS NEPALI's zero, and it leaves a stranded ")" inside दनवा)टोल.
    # A ratio-first key would pick Spins here, then lose the span entirely when it
    # failed the gate.
    pcs = _validity(hits=2, penalty=0, devanagari=658, ratio=0.679752)
    spins = _validity(hits=2, penalty=48, devanagari=655, ratio=0.688025)
    assert spins["ratio"] > pcs["ratio"]

    assert _map_ranking_key(pcs) > _map_ranking_key(spins)
    # And Spins could not have been used anyway: normalised, its garble is over
    # the gate's ceiling. The ranking and the gate are separate judgements.
    assert not _passes_content_legacy_gate(spins)
    assert _passes_content_legacy_gate(pcs)


def test_nepali_validity_reports_both_forms_of_the_garble_measure() -> None:
    # The ranking compares candidates on one span, so it uses the raw count; the
    # gate compares one span against an absolute ceiling, so it needs the rate.
    # Both must be present, and the rate must remain the quotient of the count.
    garble = "���" + "नेपाल"
    validity = _nepali_validity(garble)
    assert validity["penalty"] == pytest.approx(
        validity["penalty_per_deva"] * validity["devanagari"]
    )
    assert isinstance(validity["penalty"], int)


# --- Part B, VOL-131: the garble measure must not charge correct Nepali ---------
#
# VOL-89 fixed which *form* of the penalty may decide. VOL-131 is the residual that
# reaches: two of the patterns summed into the penalty fire on ordinary Nepali, so
# the correct map is charged and a wrong one wins on a margin that is not evidence.
#
# Measured on all 6,223 published v11 transcripts, whose text is accepted output:
# `([क-ह])\1` matched 1,087,029 times in 6,186 of them (17.9% of all penalty
# charged), and the ikar lookahead matched a nasal or visarga mark 95,153 times.
# Both figures and the per-word evidence are in `oag-corpus/runs/vol131/`.


def test_ordinary_nepali_morphology_is_not_charged_as_garble() -> None:
    # A bare doubled consonant is Nepali morphology -- a stem ending in a consonant
    # followed by a suffix beginning with the same one -- not a mis-map artifact.
    # The most frequent instance in this corpus is the name of the body that
    # published it. `अध्ययन` is the word that charged all six candidate maps 3
    # points on `3544__...Thasang Ga. Pa.`.
    #
    # ⚠️ The doublet pattern is NARROWED, not removed -- an earlier form of this
    # comment said the opposite, arguing that garble like `वडडा`/`द्दद्दण्` is
    # indistinguishable from these by adjacency. It is not, under the shipped rule:
    # measured, the narrowed rule charges वडडा and द्दद्दण् 1 each while excusing
    # महालेखापरीक्षकको, अध्ययन and क्रममा, so the morpheme lists separate them by
    # more than adjacency. `_DUPLICATE_CONSONANT_PATTERN` is still defined and still
    # fires, inside `_duplicate_consonant_count`.
    for word in (
        "महालेखापरीक्षकको",  # "of the Office of the Auditor General"
        "कार्यालय",
        "अध्ययन",  # "study"
        "क्रममा",  # "in the course of"
        "सुनिश्चितता",  # "assurance"
        "मितव्ययिता",  # "economy"
        "त्यससँग",  # "with that"
    ):
        assert _text_quality_penalty(word) == 0, word


def test_ikar_before_a_nasal_or_visarga_mark_is_not_charged() -> None:
    # Two vowel signs in a row cannot be typed, so the ikar lookahead is a real
    # signal for those. A vowel sign followed by anusvara, candrabindu or visarga is
    # spelling, and these are among the commonest words in the corpus.
    for word in (
        "सिंह",  # a surname, 8,139 occurrences
        "सिंचाई",  # "irrigation"
        "दिँदा",  # "while giving"
        "हिंसा",  # "violence"
        "नदेखिंदा",  # "not being seen" -- the word that cost 2366 its span
        "निःशुल्क",  # "free of charge"
        "मितिः",  # "date:"
    ):
        assert _text_quality_penalty(word) == 0, word


def test_the_narrowed_ikar_still_charges_two_vowel_signs_in_a_row() -> None:
    # The control on the narrowing: the 101,628 matches that were doing real work
    # must survive it. Each of these is one ikar followed by another vowel sign.
    for word in ("वििरण", "आथििक", "सिालन", "पििकरण"):
        assert _text_quality_penalty(word) == 6, word


def test_an_ikar_nasal_that_is_structurally_impossible_is_still_charged() -> None:
    """🛑 The 1.7% the nasal exemption gives up, and why it needs its own term.

    A Devanagari vowel sign is only well formed after a consonant or a
    virama-terminated cluster. In these words the ikar sits after an INDEPENDENT
    VOWEL or after another matra and is then followed by a nasal or visarga -- not
    spelling under any orthography, and the same mis-map the narrowed class was
    written for. Measured over the 6,223 v11 transcripts: 1,550 sites in 240
    documents, against the 95,153 correct nasal sequences the exemption keeps.

    The pair below is the discriminator. `सिं` and `एिं` differ only in what precedes
    the ikar, so any rule that charges the second must read the LOOKBEHIND -- widening
    the narrowed class's lookahead cannot separate them, which is why this is a second
    pattern and not an edit to the first.
    """

    # Impossible: ikar after an independent vowel or after another matra.
    assert _text_quality_penalty("एिं") == 6  # एवं "and", 467 corpus occurrences
    assert _text_quality_penalty("पुिःत") == 6  # पुस्त, 88
    assert _text_quality_penalty("बैिंक") == 6  # बैंक "bank"
    # Correct: same nasal, but the ikar sits on a consonant.
    assert _text_quality_penalty("सिंह") == 0
    assert _text_quality_penalty("निःशुल्क") == 0
    # ...and on a virama-terminated cluster, the other well-formed position.
    assert _text_quality_penalty("क्रिं") == 0

    # 🛑 The nukta, which is why the lookbehind carries U+093C and not the precomposed
    # U+0958-095F range. Those eight letters are Unicode composition EXCLUSIONS, so NFC
    # rewrites them to base+U+093C and a pattern containing one fails
    # `test_every_non_ascii_regex_source_is_normalization_stable` however it is spelled.
    # The decomposed form is the one NFC produces and it must be excused:
    assert _text_quality_penalty("\u0915\u093c\u093f\u0902") == 0
    # The precomposed spelling IS reachable (829 occurrences in 311 v11 documents, and
    # every map passes 0x958-0x95f through unchanged) but precomposed-then-ikar-then-nasal
    # has zero occurrences in all 6,223, so this residue is a priced, recorded miss --
    # charged, not excused -- rather than a range that breaks the module's import.
    assert _text_quality_penalty("\u0958\u093f\u0902") == 6


def test_the_impossible_ikar_nasal_reaches_the_variant_merge_not_only_the_ranking() -> (
    None
):
    """The ikar patterns have a SECOND consumer, and it is on the shipped path.

    `_has_severe_noise` gates the token-wise merge in `_choose_fragment_text`, which
    runs from `_merge_fragment_variants` over the raw-vs-cmap-repaired pair. A pattern
    narrowing therefore prices two things: what the garble measure charges, and whether
    a repair is attempted at all.

    Both variants here carry one impossible ikar+anusvara, in a DIFFERENT token, and no
    other garble signal. Measured with the term absent: neither side reports severe
    noise, the merge is skipped, and the fragment ships as `एवं बैिंक` with the garble
    intact. With it, both are noisy and each token comes from the side that scores lower.
    """

    original = "एिं बैंक"
    repaired = "एवं बैिंक"

    assert _has_severe_noise(original)
    assert _has_severe_noise(repaired)
    assert _choose_fragment_text(original, repaired) == "एवं बैंक"

    # The other direction, which stays as the narrowing intended: a line whose only
    # marker is a CORRECT ikar+nasal is not noisy, so no merge is attempted.
    assert not _has_severe_noise("सिंह निःशुल्क")


def test_a_false_positive_no_longer_decides_a_real_legacy_span() -> None:
    # `2366__...Dolakha Tamakoshi ga.pa`, font "Spins", 951 characters. Every rival
    # map scored 0 and Spins scored 12 -- all of it two ikar hits on `नदेखिंदा`. So
    # `PCS NEPALI` won the span and rendered `;_Vof` as `स)ख्या` where the correct
    # Spins read is `संख्या`. Deriving the penalty from the word rather than pinning
    # the number keeps this test coupled to the pattern it is about.
    spurious = _text_quality_penalty("नदेखिंदा") * 2
    assert spurious == 0

    spins = _validity(hits=5, penalty=spurious, devanagari=808, ratio=0.997531)
    pcs = _validity(hits=5, penalty=0, devanagari=788, ratio=0.982544)
    # Level on the garble axis now, so `ratio` is reached -- and the margin it decides
    # on is 0.0150, two orders of magnitude above the 0.000132 it was deciding on
    # before. Both maps still clear the gate, so nothing abstains.
    assert _map_ranking_key(spins)[:2] == _map_ranking_key(pcs)[:2]
    assert _map_ranking_key(spins) > _map_ranking_key(pcs)
    assert _passes_content_legacy_gate(spins)


def test_a_stranded_bracket_decides_before_the_ratio_does() -> None:
    # `2573__...चामुण्डा विन्द्रासैनि`, font "Spins", 446 characters. Every map scores
    # hits=3 and (after VOL-131) penalty 0, so the garble axis ties and the decision
    # falls through. `ratio` separates Kantipur from Spins by 0.000016 -- 0.992974
    # against 0.990719 -- and gets it wrong. The wrong-map tell is not close: the
    # rivals leave three `स)ख्या` behind and Spins leaves none.
    kantipur = _validity(hits=3, penalty=0, devanagari=424, ratio=0.992974, stranded=3)
    spins = _validity(hits=3, penalty=0, devanagari=427, ratio=0.990719, stranded=0)
    assert kantipur["ratio"] > spins["ratio"]  # ratio prefers the wrong map
    assert abs(kantipur["ratio"] - spins["ratio"]) < 0.005  # at its noise floor

    assert _map_ranking_key(spins) > _map_ranking_key(kantipur)
    # And it must decide ABOVE ratio, not below it: below, the 0.000016 still wins.
    assert _map_ranking_key(spins)[:2] == _map_ranking_key(kantipur)[:2]
    assert _map_ranking_key(spins)[2] > _map_ranking_key(kantipur)[2]


def test_a_real_garble_difference_still_outranks_the_stranded_count() -> None:
    # The control that keeps VOL-89's counter-case working. On
    # `4487__...बसबरिया गाउँपालिका` the wrong map (Spins) leaves one stranded bracket
    # in `दनवा)टोल` AND carries 48 penalty points; the right map carries zero of both.
    # Here the two signals agree, so it proves nothing on its own -- the point is the
    # hypothetical where they disagree: a candidate with less stranding must not win
    # on that alone while carrying more garble.
    pcs = _validity(hits=2, penalty=0, devanagari=658, ratio=0.679752, stranded=4)
    spins = _validity(hits=2, penalty=48, devanagari=655, ratio=0.688025, stranded=0)
    assert spins["stranded"] < pcs["stranded"]
    assert _map_ranking_key(pcs) > _map_ranking_key(spins)


def test_stranded_count_excludes_devanagari_digits() -> None:
    # `दफा ३५(२)` -- "section 35(2)" -- is ordinary legal citation in these reports,
    # and the Devanagari digits U+0966-U+096F sit inside the Devanagari block, so a
    # `[ऀ-ॿ]` class would charge the correct map for reading a section number.
    # `runs/vol89/adjudicate_font.py` has exactly that defect and reports Spins with
    # 2 and 3 "stranded" hits on the two documents where it in fact has none.
    assert _nepali_validity("३५(२")["stranded"] == 0
    assert _nepali_validity("दफा ३५(२) बमोजिम")["stranded"] == 0
    assert _nepali_validity("स)ख्या")["stranded"] == 1
    # Nepali list labels are NOT excluded -- they cannot be, they are structurally
    # identical to the tell. That is precisely why this count stays out of
    # `_text_quality_penalty`, which is an absolute measure, and is used only to
    # compare two decodes of one span, where a shared label is shared by both.
    assert _nepali_validity("क)वित्तीय")["stranded"] == 1
    assert _text_quality_penalty("क)वित्तीय") == 0


def test_detect_content_legacy_fonts_ignores_english() -> None:
    doc = fitz.open(stream=build_mixed_scan_and_text_pdf(), filetype="pdf")
    try:
        ocr_pages = scan_ocr_pages(doc)
        # Page 2 is plain English Helvetica; it must NOT be mapped as legacy.
        assert detect_content_legacy_fonts(doc, frozenset(ocr_pages)) == {}
    finally:
        doc.close()


def test_content_legacy_detection_is_scoped_to_requested_pages(tmp_path: Path) -> None:
    # Page 1 is mislabeled-Preeti Helvetica, page 2 is English Helvetica (same
    # base name). Extracting only page 2 must not let page 1's Preeti flip the
    # content-map gate and remap page 2's English into Devanagari garbage.
    path = _write_pdf(tmp_path, build_legacy_then_english_pdf())

    result = FontBasedStrategy().extract_text(path, pages="2")

    assert "English catalogue reference" in result.raw_text
    assert not _has_devanagari(result.raw_text)


def test_mislabeled_preeti_pdf_extracts_as_nepali(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path, build_mislabeled_preeti_pdf())

    result = FontBasedStrategy().extract_text(path)

    assert result.needs_ocr_pages == []
    assert "नेपाल सरकार" in result.raw_text
    assert "प्रतिवादी" in result.raw_text
    # The raw keystrokes must be gone.
    assert "g]kfn" not in result.raw_text


# --- npttf2utf SyntaxWarning suppression --------------------------------------


def test_npttf2utf_syntaxwarning_is_suppressed(tmp_path: Path) -> None:
    """Building the mapper must not surface npttf2utf's invalid-escape warning.

    Forces a fresh compile of the bundled preetimapper under a strict
    ``error::SyntaxWarning`` filter; our import-site suppression must keep it
    from becoming fatal.

    The freshness is bought with ``-X pycache_prefix`` pointed at an empty
    directory, **not** by deleting ``preetimapper*.pyc`` out of site-packages,
    and that distinction is load-bearing rather than stylistic. Cached bytecode
    is not recompiled, so the warning never fires and this test passes *even with
    the suppression removed altogether* -- measured. Deleting the shared .pyc made
    that a race the moment the suite gained ``-n auto``: any sibling worker
    importing ``npttf2utf.base.preetimapper`` between the unlink and this
    subprocess's import restores the file and the assertions below go vacuous. A
    private cache directory cannot be repopulated by anyone else, needs no
    writable site-packages, and leaves other workers' bytecode alone.
    """

    script = textwrap.dedent(
        """
        import warnings
        from likhit.extractors.legacy_maps import _get_mapper
        warnings.simplefilter("error", SyntaxWarning)
        out = _get_mapper().map_to_unicode("g]kfn ;/sf/", "Preeti")
        assert out == "नेपाल सरकार", out
        print("SUPPRESSION-OK")
        """
    )
    pycache = tmp_path / "pycache"
    completed = subprocess.run(
        [sys.executable, "-X", f"pycache_prefix={pycache}", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(ROOT),
    )
    assert "SUPPRESSION-OK" in completed.stdout, completed.stderr
    # Compiled fresh, so an unsuppressed warning would surface here. Under
    # `error::SyntaxWarning` it arrives as a SyntaxError naming the escape.
    assert "invalid escape sequence" not in completed.stderr
