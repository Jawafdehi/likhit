"""Tests for VOL-218's mixed letter+digit margin gate on the legacy map chooser.

**What the gate is for.** The six legacy maps fall into two families that swap the
number rows: ``Preeti``/``Kantipur``/``Sagarmatha``/``Spins`` put the Devanagari
digits on the SHIFTED row ``!@#$%^&*()`` and read ``0123456789`` as consonants, while
``PCS NEPALI``/``FONTASY_HIMALI_TT`` do the reverse. Choosing the wrong family
therefore does not *garble* a span, it TRANSPOSES letters and digits -- and on a
document that types money on one row and place names on the other, a single wrong
choice produces both directions at once.

That is what happened to ``3719__...Humla Sarkegad`` on v13 -> v14. Its ``Felix
Titling`` face flipped ``PCS NEPALI`` -> ``Preeti``, and in one table 49
unshifted-row keystrokes became consonants (``?= 10875.00`` -> ``ru. ...``, the money
column) while 23 shifted-row keystrokes became digits (``uf]&L`` -> ``go-7-i`` for
``gothi``, a place name). Measured in ``oag-corpus/runs/vol218/`` and recorded in
``FINDING-16``/``FINDING-19``.

**Why nothing else catches it.** A wrong-family reading scores ``penalty 0`` and a
high ``ratio``, because every keystroke still lands on Devanagari. On this face **five
of the six** maps tie on ``hits``, ``penalty``, ``stranded`` AND ``attested``
(3 / 0 / 0 / 14 each) -- ⚠️ not all six, as this said: ``Spins`` scores ``stranded`` 8
and ``attested`` 12, so it is separated well above ``ratio``. Among the five the decision
fell through to ``ratio``, where ``Preeti`` won by **0.000391** (re-derived:
0.000391715) -- inside the band the ranking's own docstring calls unusable. The document-level attested screen saw only
``net_attested_delta -2``, because seven gained ``ru`` nearly cancelled the loss.

**Why a MARGIN and not just another axis.** The bare term was priced corpus-wide
first: below ``attested`` it costs nothing but leaves ``4834...kharpunath`` damaged;
above ``attested`` it repairs all four damaged documents but takes ``attested -5`` and
makes four flips that are not repairs, because it speaks on spans where its advantage
is a single token. Gated on a margin it makes 6 flips at M=5 -- a strict subset of the
ungated 11 -- keeps all four repairs, and costs ``attested -2``, all of which is one
document's own repair.

**The gate is OPT-IN and OFF by default**, because which margin ships is an open board
decision. The first test below is the one that matters most: with the gate off the
chooser must be indistinguishable from the shipped one.
"""

import re

import pytest

from likhit.errors import ExtractionError, ValidationError
from likhit.extractors import font_based as fb


# The real ``Felix Titling`` aggregate from
# ``3719__1613986243Humla Sarkegad Gaupalika207475.pdf`` -- 30 spans on page 21,
# concatenated with no separator, which is how ``detect_content_legacy_fonts`` builds
# the decision unit. Legacy keystrokes are ASCII, so this is the literal byte content
# of the PDF's text; the one non-ASCII character is escaped.
AGGREGATE_3719 = (
    ";fd'bflos eag of]hgf pkef]Qmf ;ldltsf] lan e'QmfgL ubf{ s/ s\u00a7f ug'{ kg]{"
    " /sd ?= 10875.00 s\u00a7f gePsf] c;'n ul/ bflvnf ug]{' kg]{   uf]&L b]lv /f]l"
    "*sf]^ ;Dd #f]*]^f] af^f] of]hgf pkef]Qmf ;ldltsf] lan e'QmfgL ubf{ s/ sf"
    " ug'{ kg]{ /sd ?= 19305.00 sf gePsf] /sd c;'n ul/ bflvnf ug]{' kg a/fO{ "
    "b]lv /ftf *f*f ;Dd #f]*]^f] af^f] of]hgf pkef]Qmf ;ldltsf] lan e'QmfgL u"
    "bf{ s/ sf ug'{ kg]{ /sd ?= 14888.00 sf gePsf] /sd c;'n ul/ bflvnf ug]{' "
    "kg enfbL b]lv uf]&L ;Dd u|fld)f ;*s of]hgf pkef]Qmf ;ldltsf] lan e'QmfgL"
    " ubf{ s/ sf ug'{ kg]{ /sd ?= 10858.00 sf gePsf] /sd c;'n ul/ bflvnf ug]{"
    "' kg]{ ;s]{uf( b]lv *f*f;fof;Dd u|fld)f ;*s of]hgf pkef]Qmf ;ldltsf] lan"
    " e'QmfgL ubf{ s/ s\u00a7f ug'{ kg]{ /sd ?= 12921.00 s\u00a7f gePsf] /sd c;'n ul/ b"
    "flvnf ug]{' kg]{ l/kuf( hlaw't afw pkef]Qmf ;ldltsf] lan e'QmfgL ubf{ s/"
    " sf ug'{ kg]{ /sd ?= 15318.00 sf gePsf] /sd c;'n ul/ bflvnf ug]{' kg]{ c"
    "+z'adf{ cfwf/e't laWofno eag lddf{)f of]hgf pkef]Qmf ;ldltsf] lan e'Qmfg"
    "L ubf{ s/ sf ug'{ kg]{ /sd ?= 13786.00 sf gePsf] /sd c;'n ul/ bflvnf ug]"
    "{' kg]{ "
)


def test_aggregate_fixture_is_the_measured_unit():
    """Guard the fixture itself: 1,016 characters over 30 spans (FINDING 16)."""

    assert len(AGGREGATE_3719) == 1016


class TestMixedLetterDigitCount:
    """The measure. It is comparative between readings of ONE span, never absolute."""

    def test_counts_the_damage_forms(self):
        # go-7-i for gothi, and graami-0-aa for graamin: the transposed readings.
        assert fb._mixed_letter_digit_count("\u0917\u094b\u096d\u0940") == 1
        assert (
            fb._mixed_letter_digit_count(
                "\u0917\u094d\u0930\u093e\u092e\u093f\u0966\u093e"
            )
            == 1
        )

    def test_does_not_count_the_correct_readings(self):
        # gothi and graamin as they should read: letters only.
        assert fb._mixed_letter_digit_count("\u0917\u094b\u0920\u0940") == 0
        assert (
            fb._mixed_letter_digit_count("\u0917\u094d\u0930\u093e\u092e\u093f\u0923")
            == 0
        )

    def test_the_letter_class_is_CONSONANTS_ONLY_so_an_ordinal_scores_zero(self):
        """``10au.`` -- a legitimate Nepali ordinal -- does NOT enter this count.

        Pinned because it is surprising and the natural assumption is the opposite.
        The letter class is U+0915-U+0939 plus the nukta consonants U+0958-U+095F:
        **consonants only**. The independent vowel ``au`` is U+0914 and the anusvara
        is U+0902, and neither is in the letter class OR the mark class
        (U+093A-U+094F, U+0951-U+0957, U+0962-U+0963), so they break the token and
        ``10`` is left as digits with no letter beside them.

        These classes are character-for-character the ones the corpus-wide arm sweep
        used (``runs/vol218/sweep_margin_gate_corpus_0aa6842c.py``). That is the
        requirement, not an accident: the M=5 result this gate ships was measured with
        exactly this measure, so widening the class here would silently invalidate
        every flip, repair and ``attested`` figure on the issue.

        Note this cuts the safe way -- a narrower letter class can only *miss* mixed
        tokens, and the term is a comparative one, so a form both candidates produce
        cancels regardless.
        """

        assert fb._mixed_letter_digit_count("\u0967\u0966\u0914\u0902") == 0
        # A consonant beside a digit is what the class is for, and it does count.
        assert fb._mixed_letter_digit_count("\u0967\u0966\u0915") == 1

    def test_ignores_ascii_digits(self):
        """ASCII 7 beside a Devanagari letter is undecoded keystroke, not this class."""

        assert fb._mixed_letter_digit_count("\u0917\u094b7\u0940") == 0

    def test_character_classes_are_asserted_not_trusted(self):
        """A decomposed class literal compiles and silently reclassifies marks."""

        fb._assert_mixed_classes_hold()

    def test_the_gate_path_actually_calls_the_class_assertion(self, monkeypatch):
        """🛑 Nothing pinned that `choose_legacy_map_detailed` calls the guard.

        The test above calls `_assert_mixed_classes_hold()` directly, so deleting the
        CALL from the gate path left 388 tests passing -- and it is not a no-op mutant:
        with a class broken the `ExtractionError` really does fire from the gate path, so
        removing the call converts a loud refusal into silent ranking on wrong classes.
        """

        monkeypatch.setattr(fb, "_DEVA_HAS_DIGIT", re.compile("(?!x)x"))

        with pytest.raises(ExtractionError):
            fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5)

        # ...and the ungated call must NOT raise: the guard belongs to the gate path.
        assert fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)

    def test_the_env_var_name_is_the_literal_a_build_driver_exports(self, monkeypatch):
        """Every other test reads `fb._MIXED_MARGIN_ENV_VAR`, so renaming the constant
        renamed the variable everywhere at once and the suite stayed green. The literal
        string is what a PR body tells an operator to set and what a build driver
        exports, so it is pinned as a literal exactly once.
        """

        assert fb._MIXED_MARGIN_ENV_VAR == "LIKHIT_LEGACY_MAP_MIXED_MARGIN"
        monkeypatch.setenv("LIKHIT_LEGACY_MAP_MIXED_MARGIN", "5")
        assert fb._mixed_margin_setting() == 5
        assert fb.choose_legacy_map_detailed(AGGREGATE_3719).map_key == "PCS NEPALI"


class TestMarginSetting:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)
        assert fb._mixed_margin_setting() is None

    def test_blank_is_off(self, monkeypatch):
        monkeypatch.setenv(fb._MIXED_MARGIN_ENV_VAR, "   ")
        assert fb._mixed_margin_setting() is None

    def test_parses_an_integer(self, monkeypatch):
        monkeypatch.setenv(fb._MIXED_MARGIN_ENV_VAR, "5")
        assert fb._mixed_margin_setting() == 5

    def test_refuses_garbage_rather_than_disabling_itself(self, monkeypatch):
        """A gate that quietly turns itself off makes a build unfalsifiable."""

        monkeypatch.setenv(fb._MIXED_MARGIN_ENV_VAR, "five")
        with pytest.raises(ExtractionError):
            fb._mixed_margin_setting()

    def test_refuses_zero(self, monkeypatch):
        """M=0 would mean 'any advantage at all', which is the UNGATED arm."""

        monkeypatch.setenv(fb._MIXED_MARGIN_ENV_VAR, "0")
        with pytest.raises(ExtractionError):
            fb._mixed_margin_setting()


class TestRankingKeyIsAnIndicator:
    """The inserted term promotes the eligible SET; it does not order within it."""

    @staticmethod
    def _validity(mixed, attested):
        return {
            "hits": 3.0,
            "penalty": 0.0,
            "stranded": 0.0,
            "attested": float(attested),
            "ratio": 0.99,
            "devanagari": 100.0,
            "mixed": float(mixed),
        }

    def test_eligibility_is_a_step_not_a_gradient(self):
        key = fb._map_ranking_key_margin_gated(threshold=8.0)
        # Both are eligible at the threshold, so the term cannot separate them and
        # `attested` decides -- 2 beats 5 on mixed, but loses on attested.
        low_mixed = key(self._validity(mixed=2, attested=10))
        at_threshold = key(self._validity(mixed=8, attested=20))
        assert at_threshold > low_mixed

    def test_an_ineligible_candidate_is_demoted_below_attested(self):
        key = fb._map_ranking_key_margin_gated(threshold=8.0)
        eligible_weak = key(self._validity(mixed=8, attested=1))
        ineligible_strong = key(self._validity(mixed=9, attested=99))
        assert eligible_weak > ineligible_strong

    def test_a_negative_threshold_makes_the_term_constant(self):
        """The silent case: no candidate can be eligible, so shipped order holds."""

        key = fb._map_ranking_key_margin_gated(threshold=-3.0)
        assert key(self._validity(mixed=0, attested=5))[3] == 0.0
        assert key(self._validity(mixed=99, attested=5))[3] == 0.0

    def test_the_term_sits_above_attested_and_below_stranded(self):
        key = fb._map_ranking_key_margin_gated(threshold=0.0)
        got = key(self._validity(mixed=0, attested=7))
        assert got[2] == 0.0  # -max(stranded - forgiveness, 0)
        assert got[3] == 1.0  # the eligibility indicator
        assert got[4] == 7.0  # attested, demoted one position


class TestGateOffIsShipped:
    """The default must be indistinguishable from the chooser without this change."""

    def test_env_unset_leaves_3719_on_the_shipped_map(self, monkeypatch):
        monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719)
        assert choice.map_key == "Preeti"

    def test_explicit_none_leaves_3719_on_the_shipped_map(self):
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        assert choice.map_key == "Preeti"

    def test_an_explicit_none_overrides_a_set_environment(self, monkeypatch):
        """A caller that means OFF must not be overridden by an env var."""

        monkeypatch.setenv(fb._MIXED_MARGIN_ENV_VAR, "5")
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        assert choice.map_key == "Preeti"

    def test_off_computes_no_mixed_term_at_all(self, monkeypatch):
        """⚠️ This assertion holds with the gate ON too, and that is now stated.

        On this fixture the gated pass wins through the TIE path, and that path rebuilds
        `best = _nepali_validity(readings.pop())` from the masked reading, which drops
        the "mixed" key -- so `"mixed" not in validity` is true at M=5 and at M=1 as
        well. Confirmed by the mutation that defaults the gate ON at 5: three tests
        failed and this was not among them.

        Kept because the OFF path is worth asserting somewhere, and paired with the
        assertion below that does distinguish the two states.
        """

        monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719)
        assert "mixed" not in (choice.validity or {})
        # The state-distinguishing assertion: OFF leaves the shipped winner in place.
        assert choice.map_key == "Preeti"
        assert (
            fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5).map_key
            == "PCS NEPALI"
        )


class TestGateOnRepairs3719:
    def test_margin_five_restores_pcs_nepali(self):
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5)
        assert choice.map_key == "PCS NEPALI"

    def test_the_repair_removes_every_mixed_token(self):
        shipped = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        gated = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5)
        shipped_mixed = fb._mixed_letter_digit_count(
            fb.get_converter_for_map(shipped.map_key)(AGGREGATE_3719)
        )
        gated_mixed = fb._mixed_letter_digit_count(
            fb.get_converter_for_map(gated.map_key)(AGGREGATE_3719)
        )
        assert (shipped_mixed, gated_mixed) == (13, 0)

    def test_the_environment_variable_is_an_equivalent_route(self, monkeypatch):
        monkeypatch.setenv(fb._MIXED_MARGIN_ENV_VAR, "5")
        assert fb.choose_legacy_map_detailed(AGGREGATE_3719).map_key == "PCS NEPALI"

    def test_the_repaired_reading_spells_the_place_names(self):
        """The point of the exercise: real words, not digits."""

        gated = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5)
        reading = fb.decode_with_legacy_map(AGGREGATE_3719, gated)
        assert "\u0917\u094b\u0920\u0940" in reading  # gothi
        assert "\u0917\u094b\u096d\u0940" not in reading  # go-7-i

    def test_a_margin_wider_than_the_advantage_stays_silent(self):
        """M must exceed the advantage to bite; 13 -> 0 is an advantage of 13."""

        assert (
            fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=99).map_key
            == "Preeti"
        )


class TestGateCannotManufactureADecision:
    """Where the shipped chooser abstains the gate must stay silent.

    This is the property that bounds the arm's blast radius: it can move a span from
    one map to another, but it can never bring a span the accept gate rejected into
    the transcript. Asserted on a span that really does abstain, not on the code.
    """

    ABSTAINING = "\u0917 \u0916"  # two Devanagari letters: no legacy keystrokes at all

    def test_shipped_abstains_here(self):
        assert (
            fb.choose_legacy_map_detailed(self.ABSTAINING, mixed_margin=None).map_key
            is None
        )

    def test_the_gate_also_abstains_here(self):
        assert (
            fb.choose_legacy_map_detailed(self.ABSTAINING, mixed_margin=5).map_key
            is None
        )

    def test_an_empty_span_abstains_both_ways(self):
        assert fb.choose_legacy_map_detailed("", mixed_margin=None).map_key is None
        assert fb.choose_legacy_map_detailed("", mixed_margin=5).map_key is None

    @staticmethod
    def _count_passes(monkeypatch):
        """Record the ``mixed_threshold`` of every ranking pass, in order."""

        passes: list = []
        original = fb._choose_legacy_map_ranked

        def counting(text, ranking_key, mixed_threshold):
            passes.append(mixed_threshold)
            return original(text, ranking_key, mixed_threshold)

        monkeypatch.setattr(fb, "_choose_legacy_map_ranked", counting)
        return passes

    def test_never_asks_for_a_converter_for_a_non_decision(self, monkeypatch):
        """The guard is asserted directly, because its absence is otherwise INVISIBLE.

        Dropping ``shipped.map_key is None`` from the guard changes neither the result
        nor the number of ranking passes: the threshold lookup raises on a ``None`` map
        key and the surrounding ``except`` returns the shipped choice, *before* pass 2
        is reached. A mutation run confirmed it -- the mutant survived both an
        outcome-only test and a pass-counting one, because it is a semantically
        equivalent mutant.

        What does separate the two is whether the code ASKS for a converter it has no
        business asking for. Enforcing an invariant by relying on a downstream
        exception is the shape that breaks silently the day that lookup is made
        tolerant of ``None``, so the invariant is pinned here instead:
        a span with no decision never reaches the converter lookup.
        """

        seen: list = []
        original = fb.get_converter_for_map

        def recording(map_key):
            seen.append(map_key)
            return original(map_key)

        monkeypatch.setattr(fb, "get_converter_for_map", recording)
        assert (
            fb.choose_legacy_map_detailed(self.ABSTAINING, mixed_margin=5).map_key
            is None
        )
        assert None not in seen, "asked for a converter for a None map key"

    def test_no_second_pass_at_all_when_the_shipped_chooser_abstains(self, monkeypatch):
        """Outcome-level companion to the above: exactly one ranking pass runs."""

        passes = self._count_passes(monkeypatch)
        assert (
            fb.choose_legacy_map_detailed(self.ABSTAINING, mixed_margin=5).map_key
            is None
        )
        assert passes == [None], f"expected one shipped pass only, got {passes}"

    def test_two_passes_when_the_shipped_chooser_decides(self, monkeypatch):
        """Positive control for the counter above: on 3719 the gate really does re-rank."""

        passes = self._count_passes(monkeypatch)
        assert (
            fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5).map_key
            == "PCS NEPALI"
        )
        # Pass 1 shipped (None), pass 2 gated at mixed(winner) - margin = 13 - 5.
        assert passes == [None, 8.0], f"expected [None, 8.0], got {passes}"


# ------------------------------------------------- decided -> abstain, the other way

#: Two real CIAA spans on which the gate, as first written, turned a DECIDED span into
#: an abstention -- i.e. dropped text that ships today. Found by review, then located by
#: sweeping the first 3 reports: 21,376 distinct spans, 2,015 decided by pass 1, 275
#: winners changed by the gate, and these 2 abstained.
#:
#: 🛑 Synthetic fixtures cannot reach this. 60,000 generated keystroke strings all
#: abstained in PASS 1, so the gate never ran on any of them. That is why these are
#: verbatim corpus spans and not constructed ones.
DECIDED_THEN_ABSTAINED = [
    # pass 1 picks Spins and it passes the accept gate; pass 2 promotes Preeti, which
    # FAILS it. An unacceptable candidate is not a better one. Reachable at M=1 only:
    # measured, M=2 and above leave pass 2 electing Spins again.
    (1, "/sddWo] ?=%,!!,**,^&).() cfkm\"nfO{ u}/sfg'gL ¿kdf nfe k'¥ofpg] / g]kfn"),
]

#: 🛑 The SECOND witness, and it is reachable at **M=0 only** -- a margin the entry point
#: now refuses (`_MIXED_MARGIN_FLOOR`, finding 91-1). Kept as a recorded mechanism rather
#: than as a live regression test, because at every legal margin the threshold goes
#: negative, every candidate is ineligible, pass 2 orders exactly as shipped and the
#: pathology does not arise. Re-measured at M=1..11: pass 2 elects `Preeti` with
#: `ambiguous == {'8'}` every time, i.e. identical to pass 1.
#:
#: The mechanism itself is real and subtler than the review described: the SAME map wins
#: both passes: pass 1 reaches it through the tie path, so it gates the MASKED reading
#: and accepts; pass 2 finds no tie under the gated key, so it gates the UNMASKED reading
#: and rejects.
DECIDED_THEN_ABSTAINED_AT_ZERO_MARGIN = "lhNnf lzIff sfof{no, 88]nw'/f"


@pytest.mark.parametrize(("margin", "text"), DECIDED_THEN_ABSTAINED)
def test_the_gate_never_turns_a_decided_span_into_an_abstention(
    monkeypatch, margin: float, text: str
) -> None:
    """The gate's stated invariant, in the direction it was not guarded in.

    Its docstring always claimed the gate "can only ever move a span from one map to
    another". `abstain -> decided` was foreclosed by construction; `decided -> abstain`
    was not, and these two spans did it. An abstaining second pass is not evidence
    against the first pass's accepted answer, so pass 2 now falls back to it.
    """

    monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)

    shipped = fb._choose_legacy_map_ranked(
        text, fb._map_ranking_key, mixed_threshold=None
    )
    assert shipped.map_key is not None, (
        "fixture must be DECIDED without the gate, or this test proves nothing"
    )

    gated = fb.choose_legacy_map_detailed(text, mixed_margin=margin)
    assert gated.map_key is not None, "the gate dropped a span that ships today"
    # Identical, not merely non-None: the fallback must carry the shipped choice's
    # `ambiguous` set too, or the second fixture's masked code points would be decoded
    # as though the tie had been settled.
    assert gated == shipped


def test_the_keyword_route_enforces_the_same_floor_as_the_env_route(
    monkeypatch,
) -> None:
    """🛑 `mixed_margin=0` used to go straight through, and M=0 is not a gate.

    `_mixed_margin_setting` refuses anything below 1 for the env var ("unset it to
    disable the gate"), but the guard at the entry point was `margin is None`, so a
    CALLER could pass a value a build is forbidden to use. At M=0 the pass-1 winner is
    itself eligible, so the indicator promotes nobody and the gate's only effect is to
    break pass-1 ties -- which drops the VOL-156 ambiguity mask and decodes those code
    points as if the tie had been settled.

    ⚠️ This is also the only margin at which the second `decided -> abstain` witness was
    reachable; see `DECIDED_THEN_ABSTAINED_AT_ZERO_MARGIN`. Re-measured at M=1..11, pass
    2 elects the same map with the same `ambiguous` set as pass 1, so the pathology has
    no legal-margin instance.
    """

    monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)
    text = DECIDED_THEN_ABSTAINED_AT_ZERO_MARGIN

    for illegal in (0, -1, -5):
        with pytest.raises(ValidationError):
            fb.choose_legacy_map_detailed(text, mixed_margin=illegal)

    # The floor itself is legal, and the gate runs.
    assert fb.choose_legacy_map_detailed(text, mixed_margin=1).map_key is not None
    # ...and `None` still means "disabled", not "zero".
    assert fb.choose_legacy_map_detailed(
        text, mixed_margin=None
    ) == fb._choose_legacy_map_ranked(text, fb._map_ranking_key, mixed_threshold=None)


def test_the_fallback_does_not_suppress_a_legitimate_winner_change(monkeypatch) -> None:
    """The control. A fallback that swallowed every pass-2 result would pass the test
    above while making the gate inert, which is the failure mode worth guarding: the
    gate exists to change winners, and it changes 275 of them on those 3 reports.
    """

    monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)

    text = AGGREGATE_3719
    shipped = fb._choose_legacy_map_ranked(
        text, fb._map_ranking_key, mixed_threshold=None
    )
    gated = fb.choose_legacy_map_detailed(text, mixed_margin=5)
    assert shipped.map_key is not None and gated.map_key is not None
    assert gated.map_key != shipped.map_key, (
        "this fixture is the measured winner-change case; if the gate no longer moves "
        "it, the fallback has made the gate inert"
    )
