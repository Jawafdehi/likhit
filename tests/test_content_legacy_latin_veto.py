"""Tests for the Latin-side veto on the content-legacy remap (VOL-138).

The remap these guard is decided per **font**, on that font's aggregate text across
the whole document (:func:`detect_content_legacy_fonts`). That is the right unit for
deciding whether a face is a mislabeled legacy 8-bit font, and it is deliberately
left alone here. What it cannot do is notice that *some* spans of that face are
genuine Latin: OAG's 2077 performance audit report renders the real acronym ``QOC``
as ``त्तइऋ`` and loses 1,362 characters of an English appendix, because both sit in
the same ``Spins`` resource as 3,593 characters of real Preeti keystrokes (VOL-126).

**Why this needs its own tests rather than a purity assertion.** The corruption is
invisible to every existing check by construction. A character map turns ASCII
letters into Devanagari letters whatever the input language was, so remapped English
scores ``penalty 0`` and ``ratio 1.0`` — identical to correctly-decoded Nepali. It
raises Devanagari and lowers Latin, which is the direction four of v11's legitimate
fixes also move, so no delta class can see it either. The only evidence is in the
raw ASCII, before the substitution, which is what :func:`_reads_as_latin_text`
reads.

**Both directions are guarded, and the second one is the one that bites.** A veto
that is too eager is not a safe failure: it abandons real Nepali as visible ASCII
garbage. The first version of this axis — letter-share only, as originally proposed
— fired on **13,429** of the corpus's 469,357 remapped runs to protect 273, because
symbol-free Preeti keystrokes are all-ASCII-letter strings and a letter-share axis
is structurally blind to them. So the keystroke cases below are not decoration; they
are the calibration's actual failure mode, taken verbatim from the corpus sweep.
"""

from __future__ import annotations

import pytest

from likhit.extractors.font_based import (
    LegacyMapChoice,
    _content_legacy_veto_flags,
    _reads_as_latin_text,
    _reads_as_latin_words,
    detect_content_legacy_fonts,
)
from likhit.extractors.legacy_maps import get_converter_for_map

SPINS = get_converter_for_map("Spins")

# VOL-163: `_content_legacy_veto_flags` takes `dict[str, LegacyMapChoice]` since
# VOL-156's `aa4caff` widened the content-legacy map. These cases were written
# against the earlier `dict[str, str]`. Only `map_key` is read on this path, and an
# empty `ambiguous` is what a tie-free choice carries.
SPINS_CHOICE = {"Spins": LegacyMapChoice(map_key="Spins", validity=None)}


def _veto(text: str) -> bool:
    return _reads_as_latin_text(text, SPINS(text))


# Genuine Latin, verbatim from the corpus-wide sweep of all 6,236 OAG documents
# (runs/vol138/adjudication.json). Every one of these is currently rewritten into
# Devanagari that spells nothing.
GENUINE_LATIN = [
    "Random rubble stone masonry work with 1:4 ",
    "improving patient safety should lead the implementation process. ",
    "Foundation Structure ",
    "(prophylactic antibiotics) ",
    "Bio engineering work",
    # A personal name: no dictionary word in it at all, which is why a word-list
    # veto would have to miss it and a structural one does not.
    "Kaisang Dindup Tamang",
]

# Genuine Latin the veto knowingly does NOT save, with the condition that stops it.
# These are pinned so the misses are a recorded decision rather than a surprise:
# 88 of the 273 labelled-Latin runs in the corpus are in this class, most of them
# bill-of-quantities lines dense in numerals and symbols. Reaching them means
# loosening a threshold, which costs Nepali faster than it recovers Latin -- and an
# undecoded BOQ line stays legible where wrong Devanagari does not.
KNOWN_MISSES = [
    ("Supplying, mixing , placing, compacting & curing ", "vowel share 0.29"),
    ('1/2"GI Nipple 9" Long ', "letter share, numerals and quotes"),
    ("40-4kg/sqcm series iii(280mm)", "letter share 0.63"),
    # ``engineering work `` used to sit here, with the reason "15 non-space
    # characters, one below the floor". The floor is 13 as of VOL-146 and it is now
    # saved, so the entry moved into ``FLOOR_13_ADMITTED`` below rather than being
    # deleted -- its length is the reason it was ever a miss, and that is exactly what
    # this change moves. This pin is the whole reason the suite bit the constant edit
    # with no new test written: at 13 it was the single failure in 685.
]

# Real Preeti keystrokes, also verbatim from that sweep, and all of them
# symbol-free — the population the letter-share axis could not see. These must keep
# decoding.
GENUINE_KEYSTROKES = [
    "ljBfno ejg ",
    ":yfgLo tx ",
    "cfGtl/s lgoGq0f Joj:yf",
    "oftfoft Joj:yf ljefu ",
    "dxfn]vfk/LIfssf] ;GtfpGgf}_ jflif+s k|ltj]bg;",
    "gu/kflnsf rfn' ",
    # Both spellings of the same ministry name, letters-only and above the length
    # floor, so only the vowel share keeps them decoding.
    "dlxnf tyf jfnjflnsf ",
    "dlxnf tyf afnaflnsf ",
]


@pytest.mark.parametrize("text", GENUINE_LATIN)
def test_genuine_latin_runs_are_vetoed(text: str) -> None:
    assert _veto(text) is True


@pytest.mark.parametrize("text", GENUINE_KEYSTROKES)
def test_genuine_keystroke_runs_are_not_vetoed(text: str) -> None:
    assert _veto(text) is False


@pytest.mark.parametrize(("text", "reason"), KNOWN_MISSES)
def test_known_misses_stay_missed(text: str, reason: str) -> None:
    """Pins the residue, so widening a threshold has to argue with a test."""

    assert _veto(text) is False, reason


def test_whitespace_padding_cannot_clear_the_length_floor() -> None:
    """The length floor counts non-space characters, and this is why.

    ``alpha_ratio`` is computed over non-space characters, so a run of padding
    followed by three keystrokes reads as 100% letters. With a *raw-length* floor of
    12, 23 such runs in the corpus — about 75 spaces then ``gfo`` — cleared it and
    were vetoed. ``gfo`` is Nepali (``नयो``); abandoning it is the failure this veto
    must not have.
    """

    padded = " " * 74 + "gfo"
    assert len(padded) > 16  # clears a raw-length floor comfortably
    assert _veto(padded) is False


def test_vowel_ratio_is_what_separates_the_populations() -> None:
    """A matched pair from the corpus, separated by vowel share and nothing else.

    Both runs are pure ASCII letters and spaces, both above the length floor, so both
    score ``alpha_ratio`` 1.0 and carry no legacy symbol, no medial capital and no
    dictionary hit. **Every condition except vowel share gives the same answer on
    both.** The letter-share axis proposed before calibration cannot tell them apart
    at all, which is why it fired on 13,429 runs.

    ``dlxnf tyf jfnjflnsf`` decodes to ``महिला तथा वालवालिका`` — "women and
    children", a ministry name that appears throughout the corpus. (The corpus also
    carries the ``afnaflnsf`` spelling of the same phrase, which gives ``ब`` where
    this one gives ``व``; both are real and both must keep decoding.)
    """

    latin = " calculation sheet"
    keystroke = "dlxnf tyf jfnjflnsf "
    for text in (latin, keystroke):
        non_space = [char for char in text if not char.isspace()]
        assert len(non_space) >= 16
        assert all(char.isascii() and char.isalpha() for char in non_space)
    assert _veto(latin) is True
    assert _veto(keystroke) is False
    assert "वालवालिका" in SPINS(keystroke)


def test_a_nepali_word_in_the_decode_blocks_the_veto() -> None:
    """The conjunction's only Nepali-lexical condition, exercised on its own.

    ``cbfnt`` decodes to ``अदालत``. Padded with vowel-rich ASCII letters the run
    clears every character-class condition — 16 non-space characters, letter share
    1.0, vowel share 0.44, no legacy symbol, no medial capital — so the dictionary
    hit is the only thing that can decide it, and it does.
    """

    text = "cbfnt audio eagles"
    decoded = SPINS(text)
    assert "अदालत" in decoded
    non_space = [char for char in text if not char.isspace()]
    assert len(non_space) >= 16
    assert all(char.isalpha() for char in non_space)
    assert _reads_as_latin_text(text, decoded) is False
    # Same text, decode swapped for one carrying no Nepali word: now it is vetoed,
    # which pins the dictionary hit as the deciding condition rather than assuming it.
    assert _reads_as_latin_text(text, "no nepali word here") is True


def _span(font: str, text: str) -> dict[str, str]:
    return {"font": font, "text": text}


def test_the_veto_decides_a_whole_same_font_run_not_one_span() -> None:
    """The unit is the maximal consecutive same-font run within a line.

    PyMuPDF splits spans at a font change, so a producer's contiguous piece of one
    face arrives as several spans. Judged individually none of these three clears the
    length floor; judged as the run they are, the sentence is plainly English. This is
    also the unit the thresholds were calibrated on.
    """

    spans = [
        _span("Spins", "Random rubble "),
        _span("Spins", "stone masonry "),
        _span("Spins", "work with 1:4 "),
    ]
    for span in spans:
        assert _veto(span["text"]) is False, "each span alone is below the floor"
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == [True, True, True]


def test_a_keystroke_run_split_across_spans_still_decodes() -> None:
    """The negative direction of the run-level veto.

    ⚠️ **Correcting this PR's bite proof, which is measured wrong in its commit
    message.** That message says stubbing `_reads_as_latin_text` to `return False`
    "fails 10 cases in this file including all three edited here". Re-measured on this
    head: **11** fail under the `False` stub, and of the four call sites the commit
    edited only **two** are among them -- this test and its sibling assert the NEGATIVE
    direction, so they bite under `return True` instead, where 20 of the 31 cases fail.
    A one-directional stub cannot exercise a two-directional predicate, and quoting one
    count for both reads as though it did.
    """

    spans = [
        _span("Spins", "cfGtl/s "),
        _span("Spins", "lgoGq0f "),
        _span("Spins", "Joj:yf"),
    ]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == [
        False,
        False,
        False,
    ]


def test_a_font_change_ends_the_run() -> None:
    """An interleaved companion face must not be absorbed into its neighbour's run.

    The digit companions (``Spins_EXT``, ``TT33At00``) are the reason candidacy is
    decided on content at all. They are not content-legacy candidates, so they are
    never flagged, and they break the run rather than extending it.
    """

    spans = [
        _span("Spins", "Random rubble "),
        _span("Spins_EXT", "179"),
        _span("Spins", "stone masonry work "),
    ]
    flags = _content_legacy_veto_flags(spans, SPINS_CHOICE)
    assert flags[1] is False, "a non-candidate font is never vetoed"
    # Neither Spins piece reaches the floor on its own now that the companion splits
    # them, so the veto abstains -- the safe direction, since abstaining decodes.
    assert flags == [False, False, True]


def test_spans_of_a_font_that_is_not_a_candidate_are_never_flagged() -> None:
    spans = [_span("Times New Roman", "Quality Of Care and more text here")]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == [False]
    assert _content_legacy_veto_flags(spans, None) == [False]
    assert _content_legacy_veto_flags([], SPINS_CHOICE) == []


def test_font_candidacy_is_untouched_by_the_veto() -> None:
    """The veto must not change which fonts are detected as legacy faces.

    Candidacy is decided on the font aggregate by axes VOL-77 and VOL-89 hardened;
    this change is meant to be invisible to it. A document whose ``Spins`` aggregate
    is mostly keystrokes with an English appendix must still have ``Spins`` detected,
    or the appendix would be "saved" by the accident of the font dropping out.
    """

    # Four dictionary words' worth of keystrokes, so the aggregate clears the gate's
    # `hits >= 2` on its own evidence rather than on a stubbed validity.
    keystrokes = "cbfnt sf/afxL a/fdb bfo/ dlxnf tyf jfnjflnsf " * 3
    english = "Random rubble stone masonry work with 1:4 "

    class FakePage:
        pass

    page_dict = {
        "blocks": [
            {
                "lines": [
                    {"spans": [_span("Spins", keystrokes)]},
                    {"spans": [_span("Spins", english)]},
                ]
            }
        ]
    }

    class FakeDoc:
        page_count = 1

        def __getitem__(self, _index: int) -> FakePage:
            return FakePage()

    import likhit.extractors.font_based as font_based_module

    original = font_based_module.get_cid_marked_page_dict
    font_based_module.get_cid_marked_page_dict = lambda _page: page_dict
    try:
        detected = detect_content_legacy_fonts(FakeDoc())
    finally:
        font_based_module.get_cid_marked_page_dict = original
    # The font is detected, which is the whole claim. The map *name* is deliberately
    # not pinned: Preeti, Kantipur and Sagarmatha decode this text identically, and
    # `choose_legacy_map` documents that for such spans the returned name is not a
    # stable identification of the face. Asserting one here would pin an accident.
    assert set(detected) == {"Spins"}


# --------------------------------------------------------------- CID-marked input


def _mark(text: str) -> str:
    """The same transform `mark_unmappable_cids` applies, spelled out.

    Deliberately NOT built by calling the production marker: a helper that reuses the
    thing under test moves with it. `_CID_MARK_BASE` is imported so the offset stays
    pinned to the one the extractor actually uses.
    """

    from likhit.extractors.font_based import _CID_MARK_BASE

    return "".join(chr(_CID_MARK_BASE + ord(char)) for char in text)


def test_marked_genuine_latin_is_still_vetoed() -> None:
    """Raised in review. `spans` come from `get_cid_marked_page_dict`, so a run whose
    glyphs failed to decode arrives CID-MARKED -- and every ASCII test in this veto
    then sees plane-15 codepoints instead of letters.

    Measured before the fix: `True` plain, `False` marked, on the same sentence.

    `_reads_as_latin_words` already unmarked, and its comment claims the principle --
    "done here rather than at the call site so every caller inherits it". This sibling
    predicate did not follow it, so the claim was half true.

    ⚠️ This is the PREDICATE arm and it has no output consequence on its own: for a
    FULLY marked run the veto's verdict changes no bytes, because every plane-15 code
    point passes the converter untouched, so remap and no-remap are byte-identical (and
    the marks are exactly what `count_marked_cids` notices). The reachable damaging
    shape is a PARTIALLY marked run, which
    :func:`test_a_partially_marked_latin_span_is_remapped_without_the_veto` pins on the
    output rather than on the predicate.
    """

    english = "Random rubble stone masonry work with cement mortar"
    assert _veto(english) is True, "control: this must read as Latin unmarked"
    assert _veto(_mark(english)) is True
    # The no-output-consequence claim above, asserted rather than described.
    assert SPINS(_mark(english)) == _mark(english)


def test_a_partially_marked_latin_span_is_remapped_without_the_veto() -> None:
    """🛑 The arm with an actual output consequence, which nothing covered.

    A run where only SOME glyphs failed to decode arrives part marked, part plain
    ASCII. The plain half is what a converter will rewrite, so this is the shape where
    "genuine Latin becomes Devanagari that spells nothing" is literally true -- and
    where the veto's verdict changes the emitted bytes.

    Asserted on the decode, not on the predicate, so it cannot pass vacuously the way
    the fully-marked arm can.
    """

    english = "Random rubble stone masonry work with cement mortar"
    # Mark only the first word; the rest stays ASCII and is therefore convertible.
    head, _, tail = english.partition(" ")
    partial = _mark(head) + " " + tail

    # Without the veto this span WOULD be rewritten: the unmarked half converts.
    rewritten = SPINS(partial)
    assert rewritten != partial, "fixture must have a convertible half"
    assert any("ऀ" <= char <= "ॿ" for char in rewritten), (
        "the unmarked half must produce Devanagari, or this fixture proves nothing"
    )

    # ...and the veto sees it as Latin, so the caller declines the remap.
    assert _veto(partial) is True


def test_the_dictionary_axis_survives_marking() -> None:
    """The half a `text = unmark_cids(text)` inside the predicate does NOT fix.

    `decoded` is derived from the same run by the caller. A converter passes a marked
    codepoint through untouched, so decoding the MARKED form yields no Devanagari at
    all -- the dictionary axis finds no word and never suppresses the veto. Pinned
    through the caller, because that is where the decode happens.

    ⚠️ This half fails **CLOSED**, not open, and the docstring said open. The veto
    firing on a genuine Nepali run blocks a correct remap; it does not corrupt English.
    The sibling ASCII axes are the ones that fail open. Both needed the fix; only the
    sibling was the dangerous direction.
    """

    marked = _mark("cbfnt audio eagles")
    plain_decode = SPINS("cbfnt audio eagles")
    assert "अदालत" in plain_decode, "fixture must carry a dictionary word"
    assert SPINS(marked) == marked, (
        "if a converter ever starts mapping marked codepoints, this test's premise is "
        "gone and the failure mode it guards is different"
    )

    spans = [_span("Spins", marked)]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == [False], (
        "a marked run that decodes to a Nepali word must not be vetoed as Latin"
    )


def test_the_span_level_veto_returns_a_short_english_span_unchanged() -> None:
    """🛑 The span-level veto's only production effect, which nothing covered.

    `_convert_span_text` carries `if _reads_as_latin_words(text): return text` for a
    span of a content-legacy CANDIDATE font. Measured before this test: deleting that
    branch left the ENTIRE suite green. The PR's own end-to-end fixture cannot see it,
    because its English lines are long enough for the run-level veto in
    `_content_legacy_veto_flags` to catch them first -- so the span-level branch was
    shadowed by its own sibling.

    This fixture is deliberately BELOW `_LATIN_VETO_MIN_CHARS` non-space characters, so
    the run-level veto declines it and only the span-level branch can save it.
    """

    from likhit.extractors.font_based import FontBasedStrategy, _LATIN_VETO_MIN_CHARS

    short_english = "and the report"
    assert len([c for c in short_english if not c.isspace()]) < _LATIN_VETO_MIN_CHARS
    # The run-level veto declines it -- this is what makes the span-level branch the
    # only thing standing between this span and a remap.
    assert _veto(short_english) is False
    assert _reads_as_latin_words(short_english) is True

    # ...and it WOULD be rewritten: the decode is real Devanagari.
    rewritten = SPINS(short_english)
    assert any("\u0900" <= char <= "\u097f" for char in rewritten)

    kept = FontBasedStrategy()._convert_span_text(
        short_english,
        "Arial",
        {"Arial": "correct"},
        needs_reorder=False,
        # ⚠️ `LegacyMapChoice`, not a bare map key: `b236646` widened this map's values
        # earlier in this branch, and a fixture written against the parent's `str` hands
        # a string to `.map_key`. Same widening as `8d88c7d` applied to 27d74f0's cases.
        content_legacy_maps={"Arial": LegacyMapChoice("Spins", None)},
    )

    assert kept == short_english, (
        "a short genuine-English span of a candidate font must ship unchanged; "
        f"got {kept!r}"
    )


def test_marked_keystrokes_are_still_not_vetoed() -> None:
    """The control for both tests above: unmarking must not make the veto over-fire."""

    keystrokes = "cfGtl/s lgoGq0f Joj:yf"
    assert _veto(keystrokes) is False
    assert _veto(_mark(keystrokes)) is False


# -------------------------------------------------- the veto's unguarded decode


def test_an_extraction_error_from_the_veto_is_not_swallowed(monkeypatch) -> None:
    """`ExtractionError` must propagate out of the veto, not be caught per run.

    Review proposed wrapping the veto's decode in `except Exception`. Declined, and this
    pins the decisive reason so the suggestion cannot be applied in that form later:
    `choose_legacy_map_detailed` RE-RAISES `ExtractionError` on purpose -- "a
    missing/broken npttf2utf is a real config error -- surface it rather than silently
    disabling Part B". A blanket catch here would do what that comment forbids, one run
    at a time, so a broken install would look like a corpus with no Latin in it.

    Asserted through `_content_legacy_veto_flags` rather than on the converter, because
    the claim is about the CALL SITE's error handling, not the converter's.
    """

    from likhit.errors import ExtractionError
    from likhit.extractors import font_based as font_based_module

    def broken(map_key: str):
        def convert(text: str) -> str:
            raise ExtractionError("npttf2utf is missing")

        return convert

    monkeypatch.setattr(font_based_module, "get_converter_for_map", broken)
    with pytest.raises(ExtractionError):
        _content_legacy_veto_flags(
            [_span("Spins", "Random rubble stone masonry")], SPINS_CHOICE
        )


def test_an_undecided_choice_falls_through_to_the_strategy_branches() -> None:
    """🛑 `LegacyMapChoice(map_key=None)` is not None, and the guard tested the wrapper.

    The parent branch guarded on the map KEY. When the value became a `LegacyMapChoice`,
    `_convert_span_text` was widened to `if content_choice is not None`, so an UNDECIDED
    choice -- the abstention a surviving tie or a failed gate produces -- entered the
    content branch, `decode_with_legacy_map` returned the text unchanged, and the
    `legacy_remap`, `is_symbol_pua_font` and reorder branches below were skipped for
    that span. The sibling helper added in the same commit kept both halves.

    `Preeti` is used as the span font because it classifies `legacy_remap`, so its own
    branch is what must still run. If the wrapper guard is restored, this span comes
    back as raw keystrokes instead.
    """

    from likhit.extractors.font_based import FontBasedStrategy, LegacyMapChoice

    keystrokes = "g]kfn ;/sf/"
    undecided = LegacyMapChoice(None, None)

    out = FontBasedStrategy()._convert_span_text(
        keystrokes,
        "Preeti",
        {"Preeti": "legacy_remap"},
        needs_reorder=False,
        content_legacy_maps={"Preeti": undecided},
    )

    assert out == "नेपाल सरकार", out


def test_the_veto_decode_has_no_demonstrated_failing_input() -> None:
    """The other half of declining that suggestion: nothing reachable raises.

    Probed every candidate map against the adversarial inputs a malformed run could
    plausibly carry. Kept as a test rather than a run note so a future map or converter
    that DOES raise on one of these shows up here -- which is the point at which the
    guard becomes worth adding, in the ExtractionError-preserving form.
    """

    from likhit.extractors.legacy_maps import ALL_MAP_KEYS, get_converter_for_map

    inputs = [
        "",
        "\x00",
        "\x00abc",
        "�",
        "a" * 2000,
        " ",
        "",
        chr(0xF0000 + 65),
        "\U0001f600",
        "a\nb\tc\rd",
        "\\",
        "%",
        "​",
        "́" * 50,
        "".join(chr(index) for index in range(1, 256)),
        "नेपाली",
    ]
    for map_key in ALL_MAP_KEYS:
        convert = get_converter_for_map(map_key)
        for text in inputs:
            convert(text)  # must not raise


# ---------------------------------------------------------------------------
# The all-upper length floor (VOL-188 -> VOL-319 -> VOL-321).
#
# `_LATIN_VETO_MIN_CHARS` was 16 when this was calibrated and is 13 since VOL-146,
# which changes nothing here: an acronym is 2-4 characters, so it is below either
# figure and neither veto could reach the class. The word veto declines it too, at
# function-word share 0.0 against its 0.1 floor.
# `_LATIN_VETO_MIN_CHARS_UPPER` = 3 relaxes ONLY that floor and ONLY for runs whose
# ASCII letters are all upper case.
#
# The token lists are the complete read census of the population in all 6,236 OAG
# documents (runs/vol319/, blind read, controls 15/15) -- not a sample and not a
# selection. That is what makes the two boundaries below assertable as exhaustive:
# the whole false-positive set is length 2 and the whole noise class is length 1.
# ---------------------------------------------------------------------------

# All 12 distinct tokens of the 34 admitted runs, verbatim from the census.
CENSUS_ACRONYMS = [
    "MIS",
    "PAN",
    "ICT",
    "QOC",
    "TOR",
    "TOD",
    "UPS",
    "ECOD",
    "IEE",
    "OJT",
    "TEE",
    "WAN",
]

# The complete false-positive set: both tokens are two characters and both are
# Preeti fragments, so the floor of 3 excludes exactly them. `OF` is the tail of
# `8«OF` = ड्रइङ ("drawing") -- the `8«` sits on another font, so the same-font run
# is the bare `OF`, which is why an English preposition appears to stand alone.
# `OG` -> इन् is a correct decode (इन्फ्लुएन्जा, इन्टरनेशनल).
CENSUS_FALSE_POSITIVES = ["OF", "OG"]

# The complete length-1 noise class: 136 of the 177 all-upper runs in the corpus.
CENSUS_NOISE = ["O", "A", "E", "I"]


@pytest.mark.parametrize("text", CENSUS_ACRONYMS)
def test_all_upper_acronyms_are_vetoed(text: str) -> None:
    """The 34 runs the floor destroyed, read as genuine Latin 34/34."""

    assert len(text) >= 3
    assert _veto(text) is True


@pytest.mark.parametrize("text", CENSUS_FALSE_POSITIVES)
def test_two_character_all_upper_runs_keep_decoding(text: str) -> None:
    """The calibrated boundary, from below. These are the ONLY false positives.

    Dropping the floor to 2 admits these 7 occurrences and takes precision from
    1.000 to 0.829, which is the whole reason the constant is 3 and not 2.
    """

    assert len(text) == 2
    assert _veto(text) is False


@pytest.mark.parametrize("text", CENSUS_NOISE)
def test_single_character_all_upper_runs_keep_decoding(text: str) -> None:
    """The noise class -- page labels, not acronyms -- stays out at any floor >= 2."""

    assert _veto(text) is False


def test_the_all_upper_conjunction_is_load_bearing() -> None:
    """A minimal pair: same characters, same length, differing only in case.

    Without the conjunction the constant would be a bare floor of 3, which admits
    2,306 runs in 919 documents to reach the same 34 -- and 98 of those admissions
    are contradicted by the run's own decode appearing as genuine Nepali elsewhere
    in the same document. The case test is what keeps the blast radius at 16
    documents, so it is pinned here rather than left to the constant.
    """

    assert _veto("MIS") is True
    for variant in ("Mis", "mis", "MiS"):
        assert _veto(variant) is False, variant


@pytest.mark.parametrize(
    ("text", "gate"),
    [
        ("XYZ", "vowel share 0/3"),
        ("BCD", "vowel share 0/3"),
        ("MIS]", "legacy keystroke symbol"),
        ("MIS+", "legacy keystroke symbol"),
    ],
)
def test_every_other_gate_still_applies_to_an_all_upper_run(
    text: str, gate: str
) -> None:
    """The relaxation is of the floor alone; the conjunction is otherwise intact."""

    letters = [char for char in text if char.isascii() and char.isalpha()]
    assert "".join(letters).isupper()
    assert len([c for c in text if not c.isspace()]) >= 3
    assert _veto(text) is False, gate


def test_an_empty_run_cannot_reach_the_ratio() -> None:
    """`letters` is hoisted above the floor test, so this is the guard that matters.

    An empty run has no letters, so the floor stays at the mixed-case one -- whatever
    its value -- and the function returns False there, which is what stops
    `alpha_ratio` dividing by zero. A hoist that also moved the floor would turn this
    into a ZeroDivisionError.
    """

    assert _reads_as_latin_text("", "") is False
    assert _reads_as_latin_text("   ", "") is False


def test_an_all_upper_acronym_run_is_kept_whole_across_spans() -> None:
    """The decision unit is the same-font run, and the floor now sees it as one.

    `Q`, `O`, `C` split across three spans is one run of 3 characters, not three
    runs of 1 -- so it clears the upper floor, where per-span evaluation would leave
    every piece in the length-1 noise class and destroy the acronym.
    """

    spans = [
        {"font": "Spins", "text": "Q"},
        {"font": "Spins", "text": "O"},
        {"font": "Spins", "text": "C"},
    ]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == [True, True, True]


def test_a_two_character_fragment_split_across_spans_still_decodes() -> None:
    """`OF` as two spans is still a 2-character run, so it stays below the floor."""

    spans = [
        {"font": "Spins", "text": "O"},
        {"font": "Spins", "text": "F"},
    ]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == [False, False]


# ---------------------------------------------------------------------------
# The MIXED-CASE length floor: 13, not 16 and not 12 (VOL-146 -> VOL-319 -> VOL-146).
#
# `_LATIN_VETO_MIN_CHARS_UPPER` above relaxes the floor for all-upper runs only. This
# section is the other floor -- the one every mixed-case run is judged by -- and it
# moved 16 -> 13.
#
# Why 13 is the boundary and not a preference. The population is every run the floor
# ALONE rejected at length >= 10 in all 6,236 OAG documents: 91 runs, each read blind
# and given a verdict, 41 keystrokes and 50 genuine Latin. Re-derived at the build tip
# through this module's own `_reads_as_latin_text` -- not a re-implementation of it --
# `runs/vol146-floor13-7c712e1c/floor-admission-7c712e1c.json`:
#
#   floor   admitted   LATIN   KEYSTROKE   precision
#      16          0       0           0        n/a
#      15          4       4           0      1.000
#      14          9       9           0      1.000
#      13         14      14           0      1.000
#      12         24      19           5      0.792
#      11         33      24           9      0.727
#      10         91      50          41      0.549
#
# So 13 is the LAST floor with nothing but Latin in it, and 12 is the first that
# abandons real Nepali. The two lists below are that boundary, verbatim, and the
# keystroke list is what makes a further step down fail rather than merely look worse.
#
# The step also has a transcript-level cost, measured pairwise on built trees at the
# same tip and not inferred from these flags: 14 runs / 11 documents / 245 characters
# recovered, 0 documents regressed. `runs/vol146-floor13-7c712e1c/`.

#: Every run the 16 -> 13 step admits, with its non-space length. All 14 carry the
#: blind-read verdict LATIN; there is no admitted run at 13 that a reader called
#: keystrokes, which is the claim `precision 1.000` above makes.
FLOOR_13_ADMITTED = [
    ("Cost Valuation ", 13),
    ("specification ", 13),
    ("Rate Analaysis ", 13),
    ("Dipendra Rawal", 13),
    ("Non filer since ", 13),
    ("Debilal Sapkota", 14),
    ("Implementation ", 14),
    ("non-hierarchal ", 14),
    ("Ramesh Pun Magar", 14),
    ("LS Solar Asia Pvt ", 14),
    ("engineering work ", 15),
    ("Govinda Raj Bista", 15),
    ("Pasang lamu sadak", 15),
    ("Error! Reference ", 15),
]

#: The three distinct strings behind the five runs a floor of 12 would admit, with the
#: Nepali they decode to. Every one is real Nepali set in a mislabeled legacy face, so
#: admitting them means publishing the ASCII keystrokes instead of the words -- the
#: failure this veto exists to prevent, and the reason the floor stops at 13.
FLOOR_12_WOULD_ABANDON = [
    ("lakb Aoa:yfkg ", "बिपद"),
    ("Zofd afa' ofba ", "श्याम"),
    ("Zofdafa' ofba ", "श्यामबाबु"),
]


@pytest.mark.parametrize(("text", "nonspace"), FLOOR_13_ADMITTED)
def test_the_floor_admits_every_run_the_read_census_cleared(
    text: str, nonspace: int
) -> None:
    """Fails at 16 and passes at 13 -- the bite test for the constant itself.

    The length is asserted alongside the verdict so a future edit to either the
    string or the floor cannot leave this passing for a different reason than the one
    it was written for.
    """

    assert sum(1 for char in text if not char.isspace()) == nonspace
    assert 13 <= nonspace < 16, "outside the band this step can reach"
    assert _veto(text) is True


@pytest.mark.parametrize(("text", "word"), FLOOR_12_WOULD_ABANDON)
def test_a_further_step_to_12_would_abandon_real_nepali(text: str, word: str) -> None:
    """The assertion that must fail if the floor is lowered again.

    This is the mutation guard: set `_LATIN_VETO_MIN_CHARS` to 12 and every case here
    goes red, because each run then clears the floor and every other condition of the
    conjunction already passes it -- letter share >= 0.88, vowel share >= 0.30, no
    legacy keystroke symbol, no medial capital, and no dictionary word in the Spins
    decode. The floor is the only thing standing between these runs and being
    published as ASCII garbage.
    """

    assert sum(1 for char in text if not char.isspace()) == 12
    assert word in SPINS(text), "the decode is real Nepali, so the run must decode"
    assert _veto(text) is False


def test_the_floor_boundary_is_13_exactly() -> None:
    """13 is admitted, 12 is not, on one string whose length is the only variable.

    Both halves matter. A test that only pins the admitted side passes at any floor
    of 13 or lower and so cannot notice a step down; a test that only pins the
    rejected side passes at any floor of 13 or higher and cannot notice this change
    at all.
    """

    thirteen = "Cost Valuation "
    twelve = "Cost Valuatio "  # the same string, one letter shorter
    assert sum(1 for char in thirteen if not char.isspace()) == 13
    assert sum(1 for char in twelve if not char.isspace()) == 12
    assert _veto(thirteen) is True
    assert _veto(twelve) is False
