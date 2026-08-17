"""Tests for the THIRD Latin-side veto: the document-scope acronym axis (VOL-180).

The two shipped vetoes (`27d74f0`'s :func:`_reads_as_latin_text`, `5084fb8`'s
:func:`_reads_as_latin_words`) both judge a run on its **own** text. What they cannot
reach is a run that is *nothing but a bare acronym* — v13 renders `QOC` (3 chars,
twice) and `ECOD ` (5 chars) as Devanagari that spells nothing, precisely because
there is no surrounding context in any of the three to be judged on. The evidence has
to come from outside the run, at document scope.

**Why the shape alone cannot be the rule, which is what these tests mostly guard.**
Corpus-wide (`runs/vol180/strict-calibration-635286f0.json`) 7,864 remapped runs hold
a short all-caps ASCII token that both shipped vetoes miss — 41x the whole of
`27d74f0`. Vetoing on that shape would be a licence to stop decoding wherever two
capitals appear. It is only the candidate generator; the document-scope survivor
condition is what cuts 7,864 to **16 fires, 16/16 genuine English, 0 Nepali touched**.

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

from likhit.extractors.font_based import (
    _ACRONYM_EDGE,
    _ACRONYM_FORBIDDEN,
    LegacyMapChoice,
    _acronym_tokens,
    _content_legacy_veto_flags,
    _reads_as_latin_text,
    _acronym_shaped,
    _reads_as_latin_words,
    count_marked_cids,
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
