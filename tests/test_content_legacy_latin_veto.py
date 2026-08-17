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
    ("engineering work ", "15 non-space characters, one below the floor"),
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
