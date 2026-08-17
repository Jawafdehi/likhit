"""Tests for the THIRD Latin-side veto: the document-scope acronym axis (VOL-180).

The two shipped vetoes (`27d74f0`'s :func:`_reads_as_latin_text`, `5084fb8`'s
:func:`_reads_as_latin_words`) both judge a run on its **own** text. What they cannot
reach is a run that is *nothing but a bare acronym* — v13 renders `QOC` (3 chars,
twice) and `ECOD ` (5 chars) as Devanagari that spells nothing, precisely because
there is no surrounding context in any of the three to be judged on. The evidence has
to come from outside the run, at document scope.

**Why the shape alone cannot be the rule, which is what these tests mostly guard.**
Corpus-wide over the 469,357-run label set, a short all-caps ASCII token that both
shipped vetoes miss occurs in **7,864** remapped runs under the *loose* tokenizer a
first pass used (`considered` in `runs/vol180/strict-calibration-635286f0.json`) and
in **386** under the strict one that shipped (`shape_ok` in the same record;
independently re-derived in `runs/vol126r/RESULT-01-instrument-proof-6788a030.md`).
Those two numbers belong to two different tokenizers and must not be swapped: 7,864 is
41x the whole of `27d74f0`, 386 is **2.0x**. So the tokenizer narrowings below —
whitespace delimitation and the two-uppercase-letter floor — do ~95% of the
narrowing, and the document-scope survivor condition does the last 386 -> 25. Reading
the 41x as the strict shape's promiscuity over-credits the survivor axis, which is
exactly the mistake that hides how load-bearing these tokenizer tests are.

**The keystroke fragments below are the calibration's real failure mode, not
decoration.** A first pass tokenized on a punctuation class and produced 37 fires of
which **21 were spurious** — every one a Preeti keystroke fragment cut out of the
middle of a keystroke word, because the tokenizer split on legacy symbols: `6L` out of
`w/f}6L` (धरौटी), `G6L` out of `Uof/]G6L` (ग्यारेन्टी), `OG` out of `OG;]kmnfOl6;`
(इन्सेफलाइटिस). That is 43% precision on a reading, not the 92% the automatic
`false_positive` flag reported — a *short* keystroke run has fewer than two dictionary
words in its decode, so "≥ 2 dictionary words" is a sound definition of *provably*
Nepali and a weak detector of *actually* Nepali.
"""

from __future__ import annotations

import pytest

from likhit.extractors import font_based
from likhit.extractors.font_based import (
    _ACRONYM_EDGE,
    _ACRONYM_FORBIDDEN,
    LegacyMapChoice,
    _acronym_shaped,
    _acronym_tokens,
    _content_legacy_veto_flags,
    _decodes_as_legacy_devanagari,
    _is_wellformed_devanagari,
    _reads_as_latin_text,
    _reads_as_latin_words,
    count_marked_cids,
    detect_latin_acronym_survivors,
    unmark_cids,
)
from likhit.extractors.legacy_maps import get_converter_for_map

SPINS = get_converter_for_map("Spins")
SPINS_CHOICE = {"Spins": LegacyMapChoice(map_key="Spins", validity=None)}


def _span(font: str, text: str) -> dict[str, str]:
    return {"font": font, "text": text}


# Genuine acronyms, verbatim from the 16 fires that were read individually
# (runs/vol180/strict-calibration-635286f0.json). `ECOD` and `QOC` are VOL-126's own
# targets — the residue this axis exists to reach.
GENUINE_ACRONYMS = ["MIS", "IEE", "DPR", "PLGSP", "ECOD", "QOC"]

# The spurious class, verbatim from the loose pass's 21 fires. NONE of these may
# yield a qualifying token: they are keystroke words, and a token cut out of one is an
# artefact of the tokenizer rather than an acronym.
KEYSTROKE_WORDS = [
    "w/f}6L",  # धरौटी      -- loose tokenizer gave `6L`
    "Uof/]G6L",  # ग्यारेन्टी  -- gave `G6L`
    ";]G6/",  # सेन्टर      -- gave `G6`
    "8]en]kd]G6",  # डेभलेपमेन्ट -- gave `G6`
    "OG;]kmnfOl6;",  # इन्सेफलाइटिस -- gave `OG`
    "x'G5,",  # हुन्छ       -- gave `G5`
    "8f6f PG6«L",  # डाटा एन्ट्री -- gave `PG6`
]


def test_genuine_acronyms_qualify() -> None:
    for token in GENUINE_ACRONYMS:
        assert _acronym_tokens(token) == frozenset({token}), token
    # ...and are found inside real English context, which is where `QOC`'s own
    # survivor evidence lives (`Quality Of Care, QOC` on page 231).
    assert _acronym_tokens("Quality Of Care, QOC") == frozenset({"QOC"})


def test_no_keystroke_word_yields_a_qualifying_token() -> None:
    """The 21 spurious fires, and the defect that produced them.

    Whitespace delimitation plus `_ACRONYM_FORBIDDEN` is what kills these. The loose
    tokenizer `[A-Za-z0-9/&().,:;+\\-]+` split them at legacy symbols and handed back
    the fragment as though it were a word.
    """

    for word in KEYSTROKE_WORDS:
        assert _acronym_tokens(word) == frozenset(), word


def test_note_1_two_uppercase_LETTERS_not_letters_or_digits() -> None:
    """`36L` is घटी: a real whitespace-delimited all-caps ASCII keystroke word.

    It is the one spurious fire that is NOT a tokenizer artefact, and it is why the
    rule needs `>= 2 uppercase letters` rather than the issue's sketch of "2-5
    characters, all of them uppercase ASCII letters **or digits**". Under the sketch
    `36L` qualifies on one letter.
    """

    assert _acronym_tokens("36L") == frozenset()
    assert _acronym_tokens("3A") == frozenset()
    assert _acronym_tokens("12") == frozenset()
    # Two letters is enough, with or without digits alongside.
    assert _acronym_tokens("ID2") == frozenset({"ID2"})
    assert _acronym_tokens("AB") == frozenset({"AB"})


def test_note_2_an_edge_strip_can_never_expose_a_forbidden_character() -> None:
    """The module asserts the two sets are disjoint at import; this pins that."""

    assert not (_ACRONYM_FORBIDDEN & frozenset(_ACRONYM_EDGE))
    assert _acronym_tokens(";]G6/") == frozenset()
    assert _acronym_tokens(";]G6/.") == frozenset()
    # Ordinary English edge punctuation IS stripped, which is the point of the class.
    assert _acronym_tokens("(QOC),") == frozenset({"QOC"})
    assert _acronym_tokens('"MIS";') == frozenset({"MIS"})


def test_the_document_scope_vocabulary_is_built_from_unrewritten_text_only() -> None:
    """🛑 The document-scope half of this axis had NO test at all.

    Every other test in this file drives `_acronym_tokens` or
    `_content_legacy_veto_flags` with a hand-supplied survivor set. The function that
    decides what COUNTS as evidence -- the survivor-free veto call, the `rewritten`
    computation, the page skip -- was never called by a test and its output was never
    asserted: `grep -rn 'detect_latin_acronym_survivors|acronym_survivors' tests/`
    returned nothing.

    That is the gap a whole channel of finding 87-1 went through: text rewritten by the
    NAME-based `legacy_remap` path counted as "text the remap leaves alone", so its
    keystrokes attested acronyms.

    Four arms, one per class the docstring names. The fixture's vocabulary is NON-empty
    (`{"QOC"}`, from an English appendix the structural veto declines), which is what
    makes the other three falsifiable instead of vacuously true.
    """

    import fitz

    from likhit.extractors.font_based import (
        detect_content_legacy_fonts,
        detect_latin_acronym_survivors,
    )
    from tests.synthetic_pdfs import build_acronym_survivor_pdf

    doc = fitz.open(stream=build_acronym_survivor_pdf(), filetype="pdf")
    try:
        maps = detect_content_legacy_fonts(doc)
        assert maps, "fixture must carry a content-legacy candidate font"

        # (a) no candidate map at all -> no vocabulary, and no text-dict pass paid.
        assert detect_latin_acronym_survivors(doc, None) == frozenset()
        assert detect_latin_acronym_survivors(doc, {}) == frozenset()

        # (b) the run the structural veto DECLINES is the evidence -- and it is real
        # evidence, not an empty set, so (c) and (d) below have something to remove.
        assert detect_latin_acronym_survivors(doc, maps) == frozenset({"QOC"})

        # (c) a candidate-font run that IS remapped contributes NOTHING. Page 1 carries
        # the bare token `MIS`, which qualifies on shape; if the vocabulary ever read
        # rewritten text it would appear here. This is 87-1's channel.
        assert "MIS" not in detect_latin_acronym_survivors(doc, maps)
        assert _acronym_tokens("MIS") == frozenset({"MIS"}), "the token does qualify"

        # (d) a skipped page contributes nothing -- and page 2 is where the evidence is,
        # so skipping it alone must empty the vocabulary. That pins the skip to THIS
        # pass rather than to the run pass.
        assert detect_latin_acronym_survivors(doc, maps, frozenset({2})) == frozenset()
        every_page = frozenset(range(1, doc.page_count + 1))
        assert detect_latin_acronym_survivors(doc, maps, every_page) == frozenset()
    finally:
        doc.close()


def test_the_forbidden_set_is_subsumed_by_the_shape_test() -> None:
    """§8 states `_ACRONYM_FORBIDDEN` as an independent condition. It is not.

    Every character in it is already excluded by "ASCII uppercase or digit", so the
    membership test cannot currently reject a token the shape test accepts — deleting
    it changes no outcome, which a mutation run confirmed. It is kept because it is
    the spec's wording and becomes load-bearing if the shape test is ever relaxed to
    admit lowercase or symbols; this test is what makes that relaxation visible
    instead of silent.
    """

    # 🛑 Asserted against the PRODUCTION predicate, `_acronym_shaped`, not a literal copy
    # of it. The earlier form restated `("A" <= c <= "Z") or ("0" <= c <= "9")` here, so
    # it was a property of two constants and relaxing the real shape test left it -- and
    # the whole suite -- green, i.e. it could not make visible the one relaxation its
    # docstring exists for.
    #
    # ⚠️ Routing it through `_acronym_tokens` instead does NOT work, and that is worth
    # recording because it is the obvious fix: the forbidden-set membership check runs
    # BEFORE the shape test, so `_acronym_tokens("A" + c + "B")` is empty either way and
    # a relaxed shape test is swallowed. Measured.
    #
    # ⚠️ And the relaxation that matters is one admitting SYMBOLS, not lowercase.
    # Measured: `_acronym_shaped = char.isalnum()` admits lowercase and this test stays
    # green -- correctly, because no forbidden character is a lowercase letter, so the
    # forbidden set does not become load-bearing. `char.isspace()`-style relaxation does
    # admit them and fails here.
    for char in _ACRONYM_FORBIDDEN:
        assert not _acronym_shaped(char), char
        # ...and the token is excluded end to end, by one check or the other.
        assert _acronym_tokens(f"A{char}B") == frozenset(), char
    # The same token without a forbidden character qualifies, so neither half is vacuous.
    assert _acronym_tokens("AB") == frozenset({"AB"})
    assert all(_acronym_shaped(char) for char in "AB09")


def test_whitespace_delimitation_is_what_excludes_three_fragment_shapes() -> None:
    """The rule that is actually load-bearing against the spurious class.

    `G6L`, `OG` and `PG6` PASS every shape condition — 2-5 chars, all uppercase ASCII
    or digits, two uppercase letters. Nothing about their shape disqualifies them.
    They are excluded only because they are *parts* of a whitespace-delimited
    keystroke word, which is why the tokenizer splits on whitespace and nothing else.
    """

    for fragment in ("G6L", "OG", "PG6"):
        assert _acronym_tokens(fragment) == frozenset({fragment}), (
            f"{fragment} is shape-legal; only whitespace delimitation excludes it"
        )
    # In the words they were cut out of, they are unreachable.
    assert _acronym_tokens("Uof/]G6L") == frozenset()
    assert _acronym_tokens("OG;]kmnfOl6;") == frozenset()
    assert _acronym_tokens("8f6f PG6«L") == frozenset()
    # The other four are excluded by note 1's two-letter floor instead.
    for fragment in ("6L", "G6", "G5", "36L"):
        assert _acronym_tokens(fragment) == frozenset(), fragment


def test_length_bounds() -> None:
    assert _acronym_tokens("A") == frozenset()
    assert _acronym_tokens("ABCDEF") == frozenset()
    assert _acronym_tokens("ABCDE") == frozenset({"ABCDE"})


def test_the_axis_vetoes_a_bare_acronym_run_when_the_document_attests_it() -> None:
    """The whole point: a 3-character run no other axis can judge.

    `QOC` alone is far below `_reads_as_latin_text`'s 16-character floor and holds no
    dictionary word, so both shipped vetoes decline it and v13 remaps it into
    Devanagari that spells nothing.
    """

    spans = [_span("Spins", "QOC")]
    assert _reads_as_latin_text("QOC", SPINS("QOC")) is False, "axis 1 declines it"
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == [False]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE, frozenset({"QOC"})) == [True]


def test_without_survivor_evidence_the_axis_declines() -> None:
    """The shape is only the candidate generator — 7,864 runs carry it."""

    spans = [_span("Spins", "QOC")]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE, frozenset({"MIS"})) == [
        False
    ]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE, frozenset()) == [False]


def test_an_empty_survivor_set_is_exactly_the_pre_VOL180_behaviour() -> None:
    """The regression guard that matters for the generation build.

    Every caller that does not pass a survivor set must get byte-identical decisions
    to the two-axis veto. If this ever diverges, a tree built on this branch would
    differ from v13 for reasons unrelated to the acronym axis.
    """

    runs = [
        "QOC",
        "ECOD ",
        "Random rubble stone masonry work with 1:4",
        "w/f}6L",
        ";]G6/ 8]en]kd]G6",
        "MIS",
        "",
        "   ",
    ]
    # 🛑 The reference is the TWO-AXIS decision, recomputed here, not the same function
    # called twice. `acronym_survivors` defaults to `frozenset()`, so
    # `f(spans, CH, frozenset())` and `f(spans, CH)` are literally the same call: the
    # earlier form of this test pinned "the default is frozenset()" and held no
    # reference to two-axis behaviour at all, which is the one property it names.
    decode = get_converter_for_map("Spins")
    for text in runs:
        spans = [_span("Spins", text)]
        two_axis = [
            bool(text.strip())
            and (
                _reads_as_latin_text(text, decode(text)) or _reads_as_latin_words(text)
            )
        ]
        assert _content_legacy_veto_flags(spans, SPINS_CHOICE) == two_axis, text
        # ...and the default really is the survivor-free set, which is the OTHER half.
        assert _content_legacy_veto_flags(
            spans, SPINS_CHOICE, frozenset()
        ) == _content_legacy_veto_flags(spans, SPINS_CHOICE), text


def test_the_axis_runs_second_and_cannot_override_axis_1() -> None:
    """§8 requires a SECOND pass, decided only on runs axis 1 has declined.

    Both are one-sided (each only ever declines to remap), so ordering cannot change a
    veto into a remap — but it can change *which* axis is credited, and `QOC`'s own
    survivor evidence is created by axis 1 firing on `Quality Of Care, QOC`. A run
    axis 1 already vetoes must stay vetoed whatever the survivor set says.
    """

    english = "Random rubble stone masonry work with 1:4"
    spans = [_span("Spins", english)]
    assert _reads_as_latin_text(english, SPINS(english)) is True
    for survivors in (frozenset(), frozenset({"MIS"}), frozenset({"QOC"})):
        assert _content_legacy_veto_flags(spans, SPINS_CHOICE, survivors) == [True]


def test_the_veto_decides_a_whole_same_font_run_not_one_span() -> None:
    """Same run unit as the other two axes: a vetoed run is kept whole."""

    spans = [_span("Spins", "EC"), _span("Spins", "OD"), _span("Spins", " ")]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE, frozenset({"ECOD"})) == [
        True,
        True,
        True,
    ]


def test_a_non_candidate_font_is_never_flagged() -> None:
    spans = [_span("Helvetica", "QOC")]
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE, frozenset({"QOC"})) == [
        False
    ]


# --------------------------------------------------------------- CID-marked input


def _mark(text: str) -> str:
    """The transform `mark_unmappable_cids` applies, spelled out rather than reused.

    A fixture built by calling the production marker moves with it; `_CID_MARK_BASE` is
    imported so the offset stays pinned to the one the extractor uses.
    """

    from likhit.extractors.font_based import _CID_MARK_BASE

    return "".join(chr(_CID_MARK_BASE + ord(char)) for char in text)


def test_marked_acronyms_still_qualify() -> None:
    """Raised in review. Both callers read spans from `get_cid_marked_page_dict`, so a
    run whose glyphs failed to decode arrives CID-MARKED -- and a marked codepoint sits
    in plane 15, so it fails the `"A" <= char <= "Z"` and digit tests and NO token
    qualifies.

    Measured before the fix: `{OAG, CIAA}` plain, `{}` marked.

    The consequence is not a missing token, it is a silently empty survivor vocabulary,
    which disables this whole third axis for marked spans -- in the direction that
    vetoes LESS, so the result is more genuine Latin remapped into Devanagari.
    """

    assert _acronym_tokens("Quality Of Care, QOC") == frozenset({"QOC"})
    assert _acronym_tokens(_mark("Quality Of Care, QOC")) == frozenset({"QOC"})
    assert _acronym_tokens(_mark("The OAG and CIAA reports")) == frozenset(
        {"OAG", "CIAA"}
    )


def test_marking_does_not_manufacture_survivors() -> None:
    """The control: unmarking must not turn keystrokes into acronym evidence.

    Every one of the 21 measured spurious fires, marked.

    ⚠️ **This arm cannot fail while its plain-text sibling passes**, and that is stated
    rather than left as an implied guarantee: `unmark_cids(mark(x)) == x` for every ASCII
    input, so `_acronym_tokens(_mark(t))` and `_acronym_tokens(t)` are equal by
    construction. No control built from MARKED COPIES of ASCII keystrokes can rule out a
    widening. The arm below is the one that can -- it uses the input class where
    unmarking really does invent text.
    """

    for token in KEYSTROKE_WORDS:
        assert _acronym_tokens(_mark(token)) == frozenset(), token
        # The identity that makes the line above redundant, asserted so the redundancy
        # is a recorded fact rather than a thing to rediscover.
        assert unmark_cids(_mark(token)) == token


def test_arbitrary_glyph_indices_do_not_enter_the_survivor_vocabulary() -> None:
    """🛑 The control that CAN fail: raw CIDs that are not keystroke bytes.

    `get_cid_marked_page_dict` marks the raw CID of every glyph that failed to decode,
    and `_acronym_tokens` unmarks it back to `chr(cid)`. For a legacy 8-bit face the CID
    IS the keystroke byte, so that recovery is right and the run side keeps it. For a
    subset font the CIDs are arbitrary glyph indices: any 2-5 consecutive unmapped glyphs
    whose indices land in {48..57, 65..90} unmark into a qualifying token.

    So `_acronym_tokens` itself accepts them -- that is the mechanism, asserted here --
    and the fix is on the VOCABULARY side, in `detect_latin_acronym_survivors`, which now
    skips any span carrying a marked CID. Survival is only evidence of Latin if the
    surviving occurrence is itself Latin, and a glyph index is not.
    """

    # Glyph indices 77, 73, 83 happen to spell "MIS" once unmarked. Nothing about the
    # subset font makes that text; it is an accident of the index space.
    accidental = _mark("MIS")
    assert count_marked_cids(accidental) == 3
    assert _acronym_tokens(accidental) == frozenset({"MIS"})  # the mechanism


def test_a_marked_span_contributes_nothing_to_the_survivor_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half that matters: the exclusion, asserted through the vocabulary pass.

    A marked-CID PDF cannot be built from PyMuPDF's core fonts, so the page dict is
    supplied at the seam the function reads it through -- `get_cid_marked_page_dict` --
    rather than by constructing one. Two spans, identical text, one marked: only the
    plain one may become evidence.
    """

    from likhit.extractors import font_based as font_based_module

    plain = "QOC"
    marked = _mark("MIS")

    def fake_page_dict(_page):
        return {
            "blocks": [
                {
                    "lines": [
                        {"spans": [{"font": "Helvetica", "text": plain}]},
                        {"spans": [{"font": "Helvetica", "text": marked}]},
                    ]
                }
            ]
        }

    monkeypatch.setattr(font_based_module, "get_cid_marked_page_dict", fake_page_dict)

    import fitz

    doc = fitz.open()
    try:
        doc.new_page()
        survivors = font_based_module.detect_latin_acronym_survivors(
            doc, {"Spins": LegacyMapChoice("Spins", None)}
        )
    finally:
        doc.close()

    assert survivors == frozenset({plain}), survivors
    assert "MIS" not in survivors, "a glyph index is not evidence of Latin"


# ---------------------------------------------------------------------------
# VOL-212: SURVIVOR PURITY. Latin *shape* is not evidence of Latin.
#
# The 15 tests above guard the shape test and the veto's scope -- 6 of them through
# `_content_legacy_veto_flags` -- and NONE of them reaches
# `detect_latin_acronym_survivors` (0 references above this line, re-derived). All of
# them hand the veto a survivor set built by hand, and the defect is in how that set is
# BUILT. ⚠️ This said "13 tests" and neither half of that was right; the load-bearing
# clause is the reach, not the count.
# ---------------------------------------------------------------------------

# Verbatim from `runs/vol197/fire-sweep-caps-token-f7071d15.json`, fire 26:
# `pdfs/report_annual-report/11129__n30-Annual Report 2071.pdf`, page 265, font Spins.
VOL212_RUN = (
    "ljj/0fx? oyfy+ b]lvg] cj:yf 5}g . ;fy}, nul;6df ljdfgsf] "
    "rf]s Og 6fOd, rf]s ckm 6fOd, PG6L "
)
# That document's ENTIRE survivor vocabulary under the shipped axis. No English at
# all -- three Preeti keystroke fragments, which is the defect in one line.
VOL212_IMPURE_VOCABULARY = {"OG6": "इन्ट", "P06L": "एण्टी", "PG6L": "एन्टी"}
# The 8 distinct tokens carrying the 25 genuine fires of the same sweep, across the
# other 10 firing documents. `runs/vol212/FINDING-discriminator-a3f21c8e.md`.
VOL212_GENUINE = {
    "MIS": ":क्ष्क्",
    "NS": "ल्क्",
    "GI": "न्क्ष्",
    "QOC": "त्तइऋ",
    "DPR": "म्एच्",
    "ECOD": "भ्ऋइम्",
    "HDPE": "ज्म्एभ्",
    "IEE": "क्ष्भ्भ्",
}


def test_the_impure_vocabulary_is_latin_SHAPED_which_is_why_note_3_missed_it() -> None:
    """`PG6L` passes every condition VOL-180 §8 imposes. That is the whole bug."""

    for token in VOL212_IMPURE_VOCABULARY:
        assert _acronym_tokens(token) == frozenset({token}), token
        assert sum(1 for char in token if "A" <= char <= "Z") >= 2, token


def test_11129_whole_survivor_vocabulary_is_impure() -> None:
    """Acceptance #1: `PG6L`, `P06L` and `OG6` must not qualify as survivors."""

    for token, decoded in VOL212_IMPURE_VOCABULARY.items():
        assert SPINS(token) == decoded, token
        assert _decodes_as_legacy_devanagari(token, SPINS_CHOICE), token


def test_11129_run_decodes_once_the_vocabulary_is_purified() -> None:
    """Acceptance #1, at the veto: fire 26 disappears and the Nepali comes back.

    The assertion that matters is the last one — published v12 and v13 both hold
    `चोक इन टाइम` for this run and none of the raw keystrokes, so the shipped axis was
    replacing fluent Nepali with ASCII.
    """

    spans = [_span("Spins", VOL212_RUN)]
    impure = frozenset(VOL212_IMPURE_VOCABULARY)
    assert _reads_as_latin_text(VOL212_RUN, SPINS(VOL212_RUN)) is False, (
        "axis 1 declines"
    )
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE, impure) == [True], "fire 26"

    purified = frozenset(
        token
        for token in impure
        if not _decodes_as_legacy_devanagari(token, SPINS_CHOICE)
    )
    assert purified == frozenset(), "nothing in this document attests English"
    assert _content_legacy_veto_flags(spans, SPINS_CHOICE, purified) == [False]
    assert "चोक इन टाइम" in SPINS(VOL212_RUN)


def test_the_purity_filter_is_wired_into_the_vocabulary_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🛑 This change's ONLY production edit, and nothing exercised it.

    Every other VOL-212 test calls `_is_wellformed_devanagari`,
    `_decodes_as_legacy_devanagari` or `_content_legacy_veto_flags` directly and hands
    the veto a survivor set built BY HAND -- including the purified-set assertion above,
    which recomputes the filter in the test rather than reading the one that ships. So
    the comprehension inside `detect_latin_acronym_survivors` could be deleted with the
    whole suite green, and the new test block's own comment says the defect "is in how
    `detect_latin_acronym_survivors` BUILDS that set".

    Two spans, both survivors of the remap: a `Preeti` span carrying `PG6L` -- the
    keystroke spelling of एन्टी, which has Latin SHAPE and is not English -- and a
    `Times New Roman` span carrying a genuine acronym. Only the second may become
    evidence.
    """

    from likhit.extractors import font_based as font_based_module

    def fake_page_dict(_page):
        return {
            "blocks": [
                {
                    "lines": [
                        {"spans": [{"font": "Preeti", "text": "PG6L"}]},
                        {"spans": [{"font": "Times New Roman", "text": "MIS"}]},
                    ]
                }
            ]
        }

    monkeypatch.setattr(font_based_module, "get_cid_marked_page_dict", fake_page_dict)

    import fitz

    doc = fitz.open()
    try:
        doc.new_page()
        survivors = font_based_module.detect_latin_acronym_survivors(doc, SPINS_CHOICE)
    finally:
        doc.close()

    # Both tokens qualify on SHAPE, so shape is not what separates them here.
    assert _acronym_tokens("PG6L") == frozenset({"PG6L"})
    assert _acronym_tokens("MIS") == frozenset({"MIS"})
    # ...and only the one that does not decode as Nepali becomes evidence.
    assert survivors == frozenset({"MIS"}), survivors
    assert _decodes_as_legacy_devanagari("PG6L", SPINS_CHOICE)
    assert not _decodes_as_legacy_devanagari("MIS", SPINS_CHOICE)


def test_the_25_genuine_recoveries_survive_purification() -> None:
    """Acceptance #2, at token level: a narrowing that kills these is not a fix."""

    for token, decoded in VOL212_GENUINE.items():
        assert SPINS(token) == decoded, token
        assert not _decodes_as_legacy_devanagari(token, SPINS_CHOICE), token


def test_C4_alone_carries_QOC_so_it_cannot_be_dropped() -> None:
    """Which of the six conditions is load-bearing, measured not assumed.

    C3 (halant-final) carries 7 of the 8 genuine tokens. `QOC` is the exception: its
    decode `त्तइऋ` ends in a vowel letter, so only C4 (a non-initial independent vowel,
    which Nepali writes as a matra) keeps it. Drop C4 and `QOC`'s 2 fires die.
    """

    assert not SPINS("QOC").endswith("्"), "C3 does not fire on QOC"
    assert not _is_wellformed_devanagari(SPINS("QOC")), "C4 does"
    for token in ("MIS", "NS", "GI", "DPR", "ECOD", "HDPE", "IEE"):
        assert SPINS(token).endswith("्"), f"C3 carries {token}"


def test_the_predicate_accepts_real_nepali_or_the_filter_is_inert() -> None:
    """The other side of the one-sidedness: it must not call everything malformed."""

    for word in ("नेपाल", "सरकार", "कार्यालय", "काठमाडौं", "विरुद्ध", "मिति", "डॉलर"):
        assert _is_wellformed_devanagari(word), word


def test_each_of_the_six_conditions_rejects_its_own_shape() -> None:
    assert not _is_wellformed_devanagari(":क्ष्क"), "C1 non-Devanagari"
    assert not _is_wellformed_devanagari("ँब्त्त"), "C2 initial combining mark"
    assert not _is_wellformed_devanagari("ल्क्"), "C3 halant-final"
    assert not _is_wellformed_devanagari("त्तइऋ"), "C4 non-initial independent vowel"
    assert not _is_wellformed_devanagari("त्ी"), "C5 vowel sign after halant"
    assert not _is_wellformed_devanagari("ध्ब्ीी"), "C6 two vowel signs in a row"
    assert not _is_wellformed_devanagari(""), "empty is not a word"
    assert not _is_wellformed_devanagari("MIS"), (
        "an untouched ASCII token is not Nepali"
    )


def test_purity_is_ANY_candidate_map_not_ALL() -> None:
    """`DAX` (11115's vocabulary) is the corpus case that separates the two.

    Spins reads it as `म्ब्ह्` — halant-final, so malformed — while Kantipur reads it
    as `म्ब्हृ`, a well-formed word shape. A document carrying both candidate maps must
    not use it as evidence of English: one map reading it as Nepali is enough.
    """

    spins_only = {"Spins": LegacyMapChoice(map_key="Spins", validity=None)}
    kantipur_only = {"K": LegacyMapChoice(map_key="Kantipur", validity=None)}
    both = {**spins_only, **kantipur_only}
    assert not _decodes_as_legacy_devanagari("DAX", spins_only)
    assert _decodes_as_legacy_devanagari("DAX", kantipur_only)
    assert _decodes_as_legacy_devanagari("DAX", both), "any(), not all()"


def test_a_document_with_no_candidate_map_has_no_purity_opinion() -> None:
    assert not _decodes_as_legacy_devanagari("PG6L", {})
    assert not _decodes_as_legacy_devanagari(
        "PG6L", {"Spins": LegacyMapChoice(map_key=None, validity=None)}
    )


# --- the survivor VOCABULARY, and the second remap it was blind to (VOL-247) -------
#
# 24 tests precede this block and **7** of them exercise `_content_legacy_veto_flags`,
# which takes the survivor set as a parameter. The clause that matters is the reach, and
# it is re-derived: exactly 1 reference to `detect_latin_acronym_survivors` above this
# line, added by the round-6 fix for finding 87-6 -- before that, none. That function is
# what *builds* the set, and it is where VOL-247 found the axis's one measured false
# positive over the 74 corpus documents that can fire. ⚠️ This said "the 13 tests above
# all exercise" and both halves were wrong: the 13 predates the two marked-CID tests and
# the nine VOL-212 ones, and "all" was never true.
#
# `detect_content_legacy_fonts` only ever considers fonts the name classifier calls
# "correct", so `Preeti` is never a CONTENT-legacy candidate. But
# `_convert_span_text` routes it down `strategy == "legacy_remap"` to
# `get_converter`, so it is remapped all the same. Counting its spans as "text the
# remap does not rewrite" let `PG6L` — which is `एन्टी`, "anti" — attest itself from
# two Preeti spans on `11129` p329 and license a veto over 91 characters of fluent
# Nepali. Records: `runs/vol126r/RESULT-02-fires-and-fpr-6788a030.md`.
#
# These use a synthetic page dict rather than a PDF fixture because the unit under
# test is which spans may contribute, not PyMuPDF's extraction.


class _StubPage:
    pass


class _StubDoc:
    page_count = 1

    def __getitem__(self, index: int) -> _StubPage:
        return _StubPage()


def _survivors_for(monkeypatch, spans: list[dict[str, str]]) -> frozenset[str]:
    page_dict = {"blocks": [{"lines": [{"spans": spans}]}]}
    monkeypatch.setattr(font_based, "get_cid_marked_page_dict", lambda page: page_dict)
    return detect_latin_acronym_survivors(_StubDoc(), SPINS_CHOICE)


def test_a_name_legacy_font_never_attests_a_survivor(monkeypatch) -> None:
    """The regression VOL-247 measured: `Preeti` is rewritten by the NAME path.

    `PG6L` is a keystroke sequence, not an acronym. Before this guard it attested
    itself out of Preeti text and the veto shipped 91 characters of correct Nepali as
    raw keystrokes.
    """

    # `11129` p329, verbatim. Of the two Preeti spans there holding `PG6L`, this is
    # the one that actually attests: in the other, `PG6L/]leh` is nine characters and
    # the strict tokenizer already refuses it on length. Using that one instead makes
    # this test pass whether or not the guard exists — checked by mutation.
    nepali_in_preeti = 'yk Jooef/ — PG6L :g]s e]gd ;]/dsf] nflu Ps cfk"t{s;Fu '
    assert _acronym_tokens(nepali_in_preeti) == frozenset({"PG6L"})
    assert (
        _survivors_for(monkeypatch, [_span("Preeti", nepali_in_preeti)]) == frozenset()
    )


@pytest.mark.parametrize(
    "font_name",
    [
        "Preeti",
        "BCDEEE+Preeti",
        # 🛑 The registry is a SUBSTRING matcher, and only these two shapes can tell it
        # apart from an exact-key one. `Preeti` and `BCDEEE+Preeti` both match a bare
        # registry key exactly, so a narrowing to exact-name lookup -- which would
        # re-admit both of the names below -- passed green.
        "Himalb,Bold",
        "ARAP 11",
        # And the shape that reopened the hole once: `_span_base_font` takes the tail
        # after the FIRST `+`, which discards the segment carrying the registry key.
        "XXXXXX+Preeti+Bold",
    ],
)
def test_a_name_legacy_font_is_excluded_however_its_name_is_spelled(
    monkeypatch, font_name: str
) -> None:
    """Producers emit `ABCDEF+Preeti`, style suffixes, and space-bearing family names.

    The exclusion must use the remap's OWN matcher, which is what decides whether the
    span is rewritten -- anything narrower admits a span the remap rewrites, which is the
    hole this guard exists to close.
    """

    assert (
        _survivors_for(monkeypatch, [_span(font_name, "yk Jooef/ PG6L :g]s e]gd MIS ")])
        == frozenset()
    ), font_name


def test_a_genuinely_non_legacy_font_still_attests(monkeypatch) -> None:
    """The guard must not cost the evidence the axis exists to read.

    None of the 25 genuine fires VOL-247 measured depends on name-legacy evidence, so
    narrowing to genuinely-untouched fonts keeps every one of them.
    """

    assert _survivors_for(
        monkeypatch, [_span("Helvetica", "the ECOD system (QOC), MIS")]
    ) == frozenset({"ECOD", "QOC", "MIS"})


def test_a_candidate_run_vetoed_as_latin_still_attests(monkeypatch) -> None:
    """`QOC`'s evidence is *created* by axis 1 firing — the documented reason this
    pass runs after the structural veto rather than before it."""

    english = "Quality Of Care, QOC "
    assert _reads_as_latin_text(english, SPINS(english)) is True
    assert "QOC" in _survivors_for(monkeypatch, [_span("Spins", english)])


def test_a_remapped_candidate_run_does_not_attest(monkeypatch) -> None:
    """The original self-attestation guard still holds: a run that IS remapped is not
    a survivor source, whatever acronym shapes its raw keystrokes happen to contain."""

    keystrokes = "ljj/0fx? oyfy+ b]lvg] cj:yf 5}g . ;fy}, PG6L "
    assert _reads_as_latin_text(keystrokes, SPINS(keystrokes)) is False
    assert _survivors_for(monkeypatch, [_span("Spins", keystrokes)]) == frozenset()


# ------------------------------------ the guard's own coverage, and the other two paths


def test_the_name_legacy_guard_bites_on_a_token_the_purity_clause_admits(
    monkeypatch,
) -> None:
    """🛑 Without this the guard above has NO coverage, and that is a REBASE artifact.

    `test_a_name_legacy_font_never_attests_a_survivor` was mutation-checked when it was
    written — on a base where VOL-212's token-purity clause was not an ancestor. The
    rebase moved that clause five commits earlier, and `PG6L` **decodes as a Devanagari
    word**, so the purity clause now drops it whether or not the name-legacy guard runs.
    Measured: the whole suite passed with the guard deleted.

    This fixture separates the two. `MIS` is an acronym shape the purity clause
    **admits** — `_decodes_as_legacy_devanagari("MIS", …)` is False — so the name-legacy
    exclusion is the only thing that can drop it. Verified by mutation: with
    `_rewritten_outside_the_content_remap` forced to False this returns `{"MIS"}`.
    """

    preeti = "g]kfn ;/sf/ MIS 5f/]v"
    assert _acronym_tokens(preeti) == frozenset({"MIS"})
    assert _decodes_as_legacy_devanagari("MIS", SPINS_CHOICE) is False, (
        "if MIS ever starts decoding as a Nepali word, this test goes vacuous the same "
        "way the PG6L one did -- pick another admitted token, do not drop the assertion"
    )
    assert _survivors_for(monkeypatch, [_span("Preeti", preeti)]) == frozenset()


def test_a_broken_cmap_font_never_attests_a_survivor(monkeypatch) -> None:
    """`legacy_remap` is not the only other rewrite path -- `broken_cmap` is one too.

    `Kalimati` and `Lohit` are repaired from their embedded tables, so their spans are
    rewritten exactly as a name-legacy font's are. `is_legacy_font` is False for both,
    so a guard testing only that left them attesting: measured, each of these returned
    `{"MIS"}` before :func:`_rewritten_outside_the_content_remap` existed.
    """

    text = "g]kfn ;/sf/ MIS 5f/]v"
    for font in ("Kalimati", "Lohit", "BCDEEE+Kalimati"):
        assert _survivors_for(monkeypatch, [_span(font, text)]) == frozenset(), font


def test_a_symbol_pua_font_never_attests_a_survivor(monkeypatch) -> None:
    """The third rewrite path: a face whose glyphs land in the private-use area.

    `is_symbol_pua_font` decides it, again from the name alone, and again
    `is_legacy_font` is False for it -- so this was the second channel still open.
    """

    assert (
        _survivors_for(monkeypatch, [_span("Symbol", "g]kfn ;/sf/ MIS 5f/]v")])
        == frozenset()
    )


def test_the_exclusion_reads_the_base_name_and_still_lets_real_evidence_through() -> (
    None
):
    """The two directions of :func:`_rewritten_outside_the_content_remap`, directly.

    A subset prefix must not hide a rewritten face, and a genuinely untouched face must
    not be excluded -- the axis exists to read exactly those, and over-excluding costs
    the 25 genuine fires this veto is for.
    """

    for font in ("Preeti", "BCDEEE+Preeti", "Kalimati", "Lohit", "Symbol"):
        assert font_based._rewritten_outside_the_content_remap(font) is True, font
    for font in ("Helvetica", "Times New Roman", "TT339t00", "Spins"):
        assert font_based._rewritten_outside_the_content_remap(font) is False, font
