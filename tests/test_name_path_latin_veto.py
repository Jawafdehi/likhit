"""Tests for the Latin-side veto on the **name** path remap (VOL-614).

Guard 2 (:func:`_reads_as_latin_words`) has been consulted by the content path since
`5084fb8`. The name path -- ``strategy == "legacy_remap"`` -> ``get_converter`` --
consulted **nothing**, and the two paths are disjoint by construction:
:func:`detect_content_legacy_fonts` returns early for any font the name classifier
routes, so a name-matched font never reached a Latin guard at all. VOL-614 closes
that.

**Why the guard is needed on a path where the font names its own map.** Candidacy is
still decided per *font*, not per span. A document that embeds a Preeti-named face and
sets one line of real English in it hands this path a genuine-Latin span that is
indistinguishable from a keystroke span -- the exact situation `:2307` exists for. The
gap also matters prospectively: VOL-614's second change widens name-path candidacy to
the embedded ``name`` table, which moves fonts *off* the three-guard content path and
*onto* this one, so the veto had to land with or before that widening.

**🛑 The test set is not VOL-610's 103.** VOL-610 named "the 103" as the acceptance set
for a font-capability guard that was then rejected -- its measured precision on that
population is 1/103. A correct implementation suppresses **one** of the 103 and leaves
the other 102 decoding, so scoring this against the old set reads correct as failed.
Both halves are pinned below, verbatim from the corpus.

The one-sidedness of the certifier is what makes it safe to add on a decoding path: it
certifies Latin and never certifies keystrokes, so a span it declines decodes exactly
as it did before the guard existed. "Declined" is therefore not a positive verdict that
a span is Nepali -- see ``runs/vol614-8f923220/`` for the three-bucket accounting.

**VOL-634: the name path now requires BOTH certifiers, and that moved the acceptance
case to the other path.** ``_reads_as_latin_words`` alone fires on 45 spans corpus-wide
of which only 21 are English; the conjunction with :func:`_reads_as_latin_text` fires on
16, all 16 English (`runs/vol614-8f923220/CONJUNCTION-8f923220.txt`). ``CERTIFIED_LATIN``
below is one of the 29 that stop being suppressed **on this path** -- and it never needed
this path's protection, because it is a *content*-path span: its PDF resource name is
``LiberationSerif`` and its Preeti map is content-inferred, so in production it is
suppressed by guard 2 on the content-legacy branch, which VOL-634 left byte-identical.
So the acceptance line is asserted where the span actually lives
(:func:`test_the_certified_span_is_suppressed_on_the_content_path_where_it_lives`), and a
name-path certified case drawn from the 16 the conjunction still suppresses is asserted
alongside it (``NAME_PATH_CERTIFIED``). Both, not one moved.
"""

from __future__ import annotations

import pytest

from likhit.extractors.font_based import (
    _LATIN_VETO_MIN_CHARS,
    FontBasedStrategy,
    LegacyMapChoice,
    _reads_as_latin_text,
    _reads_as_latin_words,
)
from likhit.extractors.legacy_maps import get_converter

# The single span of VOL-610's 103 that likhit's own certifier calls genuine Latin,
# verbatim from runs/vol610-c9cb1fbb/VETO-SPLIT-c9cb1fbb.json (document 4940 p34,
# resource font LiberationSerif, content map Preeti, share 0.33, hit ['and']).
# Under the Preeti map it becomes `च्ष्नष्म ब्उचयल गरकज्ञद्द।ज्ञछ८ज्ञ।ढ८।छ बलम` -- genuine
# destruction, and the only span in the set for which "a decode would fabricate" holds.
CERTIFIED_LATIN = "Rigid Apron u/s12.15*1.9*.5 and"

# Spans from the same 103 that MUST keep decoding. Every one is raw ASCII on a
# Latin-named face, so a font-capability predicate would have suppressed all of them;
# the word-identity veto declines all of them and they decode into connected Nepali.
# Verbatim from VETO-SPLIT-c9cb1fbb.txt, with the decode each must still produce.
MUST_STILL_DECODE = [
    (
        "kmfNu'g dlxgfsf] sd{rf/L tna e'StfgL ubf{ xfhL/L k|df0fLt g/flvPsf",
        "फाल्गुन महिनाको कर्मचारी तलब भुक्तानी गर्दा हाजीरी प्रमाणीत नराखिएका",
    ),
    (
        "j8f sfof{no leqsf] emfl8km8flg, lkmN8 ;/;kmfO tyf dd{t",
        "वडा कार्यालय भित्रको झाडिफडानि, फिल्ड सरसफाइ तथा मर्मत",
    ),
    (
        "l;=g+ sfo{qmd÷of]hgf s'n sfo{qmd ;+Vof /sd",
        "सि.नं कार्यक्रम/योजना कुल कार्यक्रम संख्या रकम",
    ),
    (
        "hu leQf kvf{ndf l;d]G6 s+lqm6 ug]{ sfd",
        "जग भित्ता पर्खालमा सिमेन्ट कंक्रिट गर्ने काम",
    ),
]

# The second Rigid Apron span on that same page. It is the same English phrase minus
# the word `and`, which puts its function-word share at 0 -- so the veto declines it
# and it still decodes. Pinned deliberately: it is the guard's known residue, and it
# shows the unit of decision is the span, not the page or the font.
KNOWN_RESIDUE_SAME_PAGE = "Rigid Apron u/s12.15*1.5*1"

# VOL-634. The name-path certified case: one of the 16 spans the CONJUNCTION still
# suppresses, verbatim from runs/vol614-8f923220/CONJUNCTION-8f923220.json (document
# 11145 p3, resource font `Preeti`, so this one really is name-routed). It is OAG's
# own English motto set in the Preeti face -- both certifiers agree it is Latin
# (`_reads_as_latin_words` share 2/5, `_reads_as_latin_text` 22 non-space characters at
# alpha ratio 1.0), and the Preeti map would rewrite it into Devanagari spelling
# nothing. This is the assertion that replaces `CERTIFIED_LATIN`'s on THIS path.
NAME_PATH_CERTIFIED = "Serving the Nation and the "
NAME_PATH_CERTIFIED_DESTROYED = "क्भचखष्लन तजभ ल्बतष्यल बलम तजभ "

# VOL-634. What the conjunction costs on this path, pinned so widening it has to argue
# with a test rather than a comment. These 5 span records (4 distinct strings; `For
# supply ` occurs twice on 6097 p47) are genuine English that `_reads_as_latin_words`
# was protecting and `_reads_as_latin_text` declines, so they now decode. Every one is
# short, and all four fail on the SAME condition -- `_LATIN_VETO_MIN_CHARS`, the
# 16-non-space floor -- so they inherit `KNOWN_MISSES` from
# `test_content_legacy_latin_veto.py` rather than forming a new class of damage. That
# file already pins the identical case: `("engineering work ", "15 non-space
# characters, one below the floor")`. Note the floor is what declines them, NOT the
# `alpha_ratio` that declines `CERTIFIED_LATIN`; the floor short-circuits first, so
# their letter share is never even computed. Total cost 57 Devanagari characters
# across the 5 records (3 + 9 + 9 + 11 + 25).
# 🛑 v17: `AND BASE COURSE ` (13 non-space) MOVED OUT of this list, because the v17
# composition carries VOL-146's floor `16 -> 13` (VOL-361's lane, and the charter's
# named carry-set). At floor 13 that fragment clears the floor and is RECOVERED, which
# is precisely the effect VOL-146 was validated for -- mutation bite-proof in both
# directions plus a directional pairwise gate exiting 0/0/1.
#
# The docstring below said "lowering the floor has to argue with this test", and that is
# what happened: the test fired on the composition and this is the argument. VOL-629's
# "out of scope" applied to VOL-630's scope, not to VOL-146's separately-validated
# change. The coverage is not deleted -- it moves to
# `test_the_recovered_upper_fragment_clears_the_shipped_floor` below, which fails if the
# floor is ever raised back to 16. So the pin still bites, in the other direction.
LOST_ENGLISH_FRAGMENTS = [
    ("the ", 3),
    ("For supply ", 9),
    ("Not for sale ", 10),
]

#: The fragment floor 13 recovers. Kept as data so the two tests cannot drift apart.
RECOVERED_BY_FLOOR_13 = ("AND BASE COURSE ", 13)

# VOL-634. The one genuinely mixed-script span in the 45: Nepali keystrokes and English
# words in a single span, from 3015 p23. No span-level verdict can be right about it --
# suppressing it abandons the Nepali, decoding it destroys the English -- and the general
# fix is span-internal segmentation, which is unscoped. Decided on VOL-629: this is
# recorded as known mixed-script residue, not filed as a defect. It now decodes, which
# recovers the Nepali (`उपभोक्ता समितिहरुलाई`) and garbles the English (`management`,
# `insurance`, `tools and plant`).
MIXED_SCRIPT_RESIDUE = (
    "pkef]Qmf ;ldltx?nfO{ wuc management / p/s insurance tools and plant "
)

# VOL-634. Spans that discriminate the CONJUNCTION from `_reads_as_latin_text` alone:
# each has `_reads_as_latin_words` False and `_reads_as_latin_text` True. Verbatim from
# `GENUINE_LATIN` in test_content_legacy_latin_veto.py (runs/vol138/adjudication.json) --
# genuine English carrying no three-letter function word, which is precisely the word
# test's documented blind spot and the structural test's strength (VOL-146, VOL-163).
# Needed because no fixture drawn from the 45-span population can discriminate: that
# population is defined by the word test firing. See
# `test_the_word_test_is_load_bearing_in_the_conjunction`.
WORD_TEST_IS_LOAD_BEARING = [
    "Foundation Structure ",
    "(prophylactic antibiotics) ",
    "Bio engineering work",
    "Kaisang Dindup Tamang",
]


def _decode_preeti(text: str) -> str:
    converter = get_converter("Preeti")
    assert converter is not None
    return converter(text)


def _convert(text: str, font_name: str = "Preeti") -> str:
    """Run one span through the real conversion path as `legacy_remap`."""

    return FontBasedStrategy()._convert_span_text(
        text,
        font_name,
        {font_name: "legacy_remap"},
        needs_reorder=False,
    )


def _convert_content_path(text: str, font_name: str = "LiberationSerif") -> str:
    """Run one span through the real conversion path as a CONTENT-legacy candidate.

    This is the path `CERTIFIED_LATIN` actually reaches in production: the map is
    content-inferred and keyed by the full resource name, and the strategy for a
    Latin-named face is ``correct``, not ``legacy_remap``. `skip_content_legacy` is
    left False so the decision under test is guard 2's, not guard 1's run-grain flag.
    """

    return FontBasedStrategy()._convert_span_text(
        text,
        font_name,
        {},
        needs_reorder=False,
        content_legacy_maps={
            font_name: LegacyMapChoice(map_key="Preeti", validity=None)
        },
        skip_content_legacy=False,
    )


def test_the_certified_span_is_suppressed_on_the_content_path_where_it_lives() -> None:
    """VOL-613 acceptance line 1, re-vehicled onto the path the span is really on.

    VOL-634 narrowed the NAME path to a conjunction, and `CERTIFIED_LATIN` fails the
    second certifier. It was never a name-path span: `4940` p34's resource font is
    ``LiberationSerif`` and its Preeti map is content-inferred, so in production the
    span is suppressed by guard 2 on the content-legacy branch -- which VOL-634 left
    byte-identical. The acceptance line therefore still holds, on this path.
    """

    assert _reads_as_latin_words(CERTIFIED_LATIN) is True
    assert _convert_content_path(CERTIFIED_LATIN) == CERTIFIED_LATIN
    # And pin what the guard is preventing, so a regression is legible as damage
    # rather than as a changed string.
    destroyed = _decode_preeti(CERTIFIED_LATIN)
    assert destroyed != CERTIFIED_LATIN
    assert "च्ष्नष्म" in destroyed


def test_the_certified_span_is_no_longer_suppressed_on_the_name_path() -> None:
    """The other half of the same fact, asserted rather than left implicit.

    Pinned so the re-vehicling above cannot be mistaken for "nothing changed": on the
    name path this span now decodes. That is the accepted cost of the conjunction, and
    it costs the corpus nothing because no name-routed font sets this span.
    """

    assert _convert(CERTIFIED_LATIN) != CERTIFIED_LATIN
    assert _convert(CERTIFIED_LATIN) == _decode_preeti(CERTIFIED_LATIN)


def test_alpha_ratio_is_the_condition_that_declines_the_certified_span() -> None:
    """VOL-634 criterion 3, pinned: name the condition, not just the outcome.

    Measured by ablation in runs/vol634-ce05aedd/GUARD1-ABLATION-ce05aedd.txt --
    `alpha_ratio` is the UNIQUE decliner, and every other condition passes. Asserting
    the conditions individually rather than only `_reads_as_latin_text(...) is False`
    means a future threshold move that declines the span for a *different* reason
    fails here instead of reading as unchanged.
    """

    non_space = [c for c in CERTIFIED_LATIN if not c.isspace()]
    letters = [c for c in non_space if c.isascii() and c.isalpha()]
    vowels = [c for c in letters if c in "aeiouAEIOU"]

    assert len(non_space) == 28  # clears the 16 floor
    assert len(letters) == 15
    assert len(letters) / len(non_space) == pytest.approx(0.5357, abs=5e-5)  # < 0.88
    assert len(vowels) / len(letters) == pytest.approx(0.4)  # clears 0.30
    assert not any(c in "][{}|~^@+_=" for c in CERTIFIED_LATIN)
    decoded = _decode_preeti(CERTIFIED_LATIN)
    assert _reads_as_latin_text(CERTIFIED_LATIN, decoded) is False


def test_the_name_path_certified_span_is_suppressed() -> None:
    """The name-path half of acceptance line 1: one of the 16 the conjunction keeps.

    `NAME_PATH_CERTIFIED` is genuinely name-routed -- its resource font IS `Preeti` --
    so unlike `CERTIFIED_LATIN` this span has no content path to fall back on. Both
    certifiers must agree, because it is the conjunction that is under test.
    """

    decoded = _decode_preeti(NAME_PATH_CERTIFIED)
    assert _reads_as_latin_words(NAME_PATH_CERTIFIED) is True
    assert _reads_as_latin_text(NAME_PATH_CERTIFIED, decoded) is True
    assert _convert(NAME_PATH_CERTIFIED) == NAME_PATH_CERTIFIED
    # Pin the damage the suppression prevents, same as above.
    assert decoded == NAME_PATH_CERTIFIED_DESTROYED


def test_the_recovered_upper_fragment_clears_the_shipped_floor() -> None:
    """`AND BASE COURSE ` is RECOVERED by VOL-146's floor 13, and must stay recovered.

    This is the other half of the pin that `LOST_ENGLISH_FRAGMENTS` used to carry. At the
    retired floor of 16 this 13-non-space fragment was declined and decoded to garbage
    (`\u092c\u094d\u0932\u094d\u092e\u094d ...`); at the shipped floor of 13 it clears and the English survives.

    It bites in the direction the composition needs: raise the floor back to 16 and this
    fails, which is what stops a later change from silently un-doing VOL-146 (`done`,
    mutation bite-proof both ways, directional pairwise gate 0/0/1) while every other
    test still passes. VOL-361 is the carry lane; the v17 charter names it.
    """

    raw, non_space = RECOVERED_BY_FLOOR_13
    assert len([c for c in raw if not c.isspace()]) == non_space
    assert non_space >= _LATIN_VETO_MIN_CHARS, (
        "the fragment no longer clears the shipped floor -- VOL-146's 16->13 may have "
        "been reverted"
    )
    assert _reads_as_latin_words(raw) is True
    assert _reads_as_latin_text(raw, _decode_preeti(raw)) is True


@pytest.mark.parametrize(("raw", "non_space"), LOST_ENGLISH_FRAGMENTS)
def test_the_lost_english_fragments_inherit_the_known_misses_class(
    raw: str, non_space: int
) -> None:
    """VOL-634's accepted cost, pinned as residue rather than left as a comment.

    Each of these is genuine English that the word veto alone was protecting. They are
    declined by `_LATIN_VETO_MIN_CHARS` -- the same condition, for the same reason, as
    the content path's pinned `KNOWN_MISSES` entry `engineering work `. Asserting the
    floor is what declines them (and not the letter share) means lowering the floor has
    to argue with this test, which is the point: VOL-629 ruled that out of scope.
    """

    assert len([c for c in raw if not c.isspace()]) == non_space
    # Read the SHIPPED floor, not a literal. A literal 16 pinned a floor v17 retires,
    # and the failure it produced looked like a regression rather than a carried fix.
    assert non_space < _LATIN_VETO_MIN_CHARS
    assert _reads_as_latin_words(raw) is True  # the word veto DID protect it
    assert _reads_as_latin_text(raw, _decode_preeti(raw)) is False
    assert _convert(raw) != raw  # ... and it now decodes on the name path


@pytest.mark.parametrize("raw", WORD_TEST_IS_LOAD_BEARING)
def test_the_word_test_is_load_bearing_in_the_conjunction(raw: str) -> None:
    """Both terms are required, and this is the only test that can see it.

    Added because a mutation arm proved the suite could not: replacing the whole
    conjunction with `_reads_as_latin_text(text, decoded)` alone left all 42 tests green
    (`runs/vol634-ce05aedd/MUTATION-ce05aedd.txt`, arm `text_only`). Every other fixture
    in this file is drawn from the 45-span population, which is *defined* by
    `_reads_as_latin_words` being True -- so within it the structural test alone and the
    conjunction are indistinguishable, and no fixture from it can discriminate.

    These four spans do: each has ``_reads_as_latin_words`` False and
    ``_reads_as_latin_text`` True, so the conjunction declines and the span decodes,
    where the structural test alone would suppress it.

    🛑 What this test pins is the conjunction's STRUCTURE, not a desirable outcome. All
    four are genuine English -- verbatim from ``GENUINE_LATIN`` in
    `test_content_legacy_latin_veto.py` (runs/vol138/adjudication.json) -- and on a
    name-routed font this path destroys them. That is unchanged from the landed veto
    (``_reads_as_latin_words`` is False for them, so option A destroyed them too), so it
    is not a cost VOL-634 introduces. It *is* a real unaddressed exposure, and it is the
    one `_reads_as_latin_text`-alone would fix -- which is exactly why VOL-629 held that
    option back pending a corpus sweep of this path's population. If that sweep happens
    and the answer is "drop the word term", this test is the one to delete, deliberately.
    """

    decoded = _decode_preeti(raw)
    assert _reads_as_latin_words(raw) is False
    assert _reads_as_latin_text(raw, decoded) is True
    assert _convert(raw) == decoded  # the conjunction declines; the span decodes


def test_the_mixed_script_span_is_the_known_residue() -> None:
    """The 1 genuinely-mixed span of the 45, recorded as residue (VOL-629, decided).

    One span carrying Nepali keystrokes and English words together. No span-level
    verdict can be right: suppressing abandons the Nepali, decoding garbles the
    English. Decoding is the chosen side because the Nepali is the larger part. The
    general fix is span-internal segmentation and it is unscoped -- deliberately no
    separate issue.
    """

    decoded = _convert(MIXED_SCRIPT_RESIDUE)
    assert decoded != MIXED_SCRIPT_RESIDUE
    # The Nepali is recovered ...
    assert "उपभोक्ता समितिहरुलाई" in decoded
    # ... and the English in the same span is garbled. Both, so the trade-off is
    # legible to whoever reads this next rather than being implied by one assertion.
    assert "management" not in decoded
    assert "insurance" not in decoded


@pytest.mark.parametrize(("raw", "expected"), MUST_STILL_DECODE)
def test_the_other_spans_are_unaffected(raw: str, expected: str) -> None:
    """Acceptance line 2: the remaining 102 must be untouched by the guard.

    Asserted against the decoded Nepali rather than merely "!= raw", so a guard that
    suppressed the decode *and* a converter that broke it are separate failures.
    """

    assert _reads_as_latin_words(raw) is False
    assert _convert(raw) == expected


def test_the_residue_on_the_certified_page_still_decodes() -> None:
    """Pins the guard's grain: per span, not per page or per font."""

    assert _reads_as_latin_words(KNOWN_RESIDUE_SAME_PAGE) is False
    assert _convert(KNOWN_RESIDUE_SAME_PAGE) != KNOWN_RESIDUE_SAME_PAGE


def test_a_font_with_no_converter_is_unchanged() -> None:
    """The `converter is None` arm is untouched: no veto, no decode, raw text out."""

    assert _convert(CERTIFIED_LATIN, font_name="NotALegacyFace") == CERTIFIED_LATIN
    assert _convert("cbfnt sf/afxL", font_name="NotALegacyFace") == "cbfnt sf/afxL"


def test_the_guard_is_reached_only_on_the_legacy_remap_branch() -> None:
    """A `correct` font must not consult the veto -- it has no converter to decline.

    This is what stops the guard leaking into the strategy branches below it: a
    Latin-certified span on a `correct` font returns raw because that is what the
    branch already did, not because the veto fired.
    """

    extractor = FontBasedStrategy()
    for strategy in ("correct", "broken_cmap"):
        assert (
            extractor._convert_span_text(
                CERTIFIED_LATIN,
                "Preeti",
                {"Preeti": strategy},
                needs_reorder=False,
            )
            == CERTIFIED_LATIN
        )
