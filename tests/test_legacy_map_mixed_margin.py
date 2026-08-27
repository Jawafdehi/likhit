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

# Distinct per axis, so no assertion in this file can pass because two axes happen to
# hold the same number. Run `9c7a9a3b`'s first attempt at this measurement used a span
# that scored 0.0 on every axis and therefore reported a missing axis as PRESENT.
_SENTINELS = {
    "hits": 11.0,
    "penalty": 13.0,
    "stranded": 0.0,  # 0 so the forgiveness clamp cannot alias another axis
    "figures": 19.0,
    "attested": 23.0,
    "ratio": 29.0,
    "devanagari": 31.0,
    "mixed": 0.0,
}


def _derive_eligible_index() -> int:
    """Where the indicator sits, DERIVED from the two keys rather than hardcoded.

    Adding an axis above the indicator shifts its position. A hardcoded index would
    then silently move every positional assertion in this file onto the wrong axis --
    which is exactly the class of failure `9c7a9a3b` measured in the production code,
    and exactly what carrying the `figures` axis over from the corpus line did here:
    the indicator moved from index 3 to index 4.
    """

    shipped = fb._map_ranking_key(_SENTINELS)
    gated = fb._map_ranking_key_margin_gated(threshold=-1.0)(_SENTINELS)
    assert len(gated) == len(shipped) + 1, (shipped, gated)
    positions = [
        index
        for index in range(len(gated))
        if gated[:index] + gated[index + 1 :] == shipped
    ]
    assert len(positions) == 1, f"indicator position is not unique: {positions}"
    return positions[0]


#: Derived, and asserted to agree with the production constant below, so the two cannot
#: drift apart silently.
_ELIGIBLE_INDEX = _derive_eligible_index()


def test_the_derived_indicator_index_is_the_production_constant() -> None:
    """The derivation and `_MIXED_ELIGIBLE_INDEX` must agree.

    Either alone is a single point of failure: the constant can be edited without moving
    the splice, and the derivation can only see the shape the splice produces. Asserting
    them equal is what makes the rest of this file's positional assertions trustworthy.
    """

    assert _ELIGIBLE_INDEX == fb._MIXED_ELIGIBLE_INDEX


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
    def _validity(mixed, attested, penalty=0.0, ikar_nasal=0.0, figures=0.0):
        return {
            "hits": 3.0,
            "penalty": float(penalty),
            "stranded": 0.0,
            # Defaults to 0 so these fixtures tie on it and reach the axis they assert.
            # It sits immediately ABOVE the spliced indicator, so a nonzero default here
            # would mask the very ordering `_MIXED_ELIGIBLE_INDEX` exists to pin.
            "figures": float(figures),
            "attested": float(attested),
            "ratio": 0.99,
            "devanagari": 100.0,
            "mixed": float(mixed),
            # Required, not optional: the gated key derives from `_map_ranking_key`,
            # which reads this strictly. A dict built without it is a bug.
            "ikar_nasal": float(ikar_nasal),
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
        assert key(self._validity(mixed=0, attested=5))[_ELIGIBLE_INDEX] == 0.0
        assert key(self._validity(mixed=99, attested=5))[_ELIGIBLE_INDEX] == 0.0

    def test_the_term_sits_below_figures_and_above_attested(self):
        """The position, stated against the axes either side of it.

        ⚠️ Below `figures`, not below `stranded`: carrying the corpus line's money-figure
        axis inserted a term at index 3 and pushed the indicator to 4. Left above
        `figures`, enabling the gate would OUTRANK the figure axis rather than refine the
        pass beneath it -- run `9c7a9a3b` measured that exact composition reverting all
        six figures repairs, `3719` among them.
        """

        key = fb._map_ranking_key_margin_gated(threshold=0.0)
        got = key(self._validity(mixed=0, attested=7, figures=4))
        assert got[_ELIGIBLE_INDEX - 1] == 4.0  # figures, immediately above
        assert got[_ELIGIBLE_INDEX] == 1.0  # the eligibility indicator
        assert got[_ELIGIBLE_INDEX + 1] == 7.0  # attested, immediately below

    def test_the_gated_key_is_the_ungated_key_with_one_term_spliced_in(self):
        """🛑 The anti-drift pin, and it is not hypothetical.

        This function used to RESTATE `_map_ranking_key`'s axes instead of deriving from
        them, and the copy diverged the moment one of them gained a term: the ungated key
        began forgiving a single ikar+nasal site and the gated copy kept charging it, so
        the gate ranked on a *different garble measure* than the pass it refines.

        Removing the ELIGIBLE term must leave exactly the ungated key -- for every shape,
        including ones where the forgiven term actually bites, which is what the copy got
        wrong.
        """

        key = fb._map_ranking_key_margin_gated(threshold=8.0)
        i = fb._MIXED_ELIGIBLE_INDEX
        for mixed, attested, penalty, nasal in [
            (0, 5, 0, 0),
            (9, 5, 0, 0),
            (2, 20, 6, 1),  # the forgiveness bites: 6 charged, 6 forgiven
            (2, 20, 12, 2),  # bounded: only one site is forgiven
            (99, 1, 30, 5),
        ]:
            validity = self._validity(
                mixed=mixed, attested=attested, penalty=penalty, ikar_nasal=nasal
            )
            gated = key(validity)
            ungated = fb._map_ranking_key(validity)
            assert gated[:i] + gated[i + 1 :] == ungated, (
                f"gated key diverged from the ungated one at "
                f"penalty={penalty} ikar_nasal={nasal}"
            )
            assert len(gated) == len(ungated) + 1

    def test_the_gate_inherits_the_ikar_nasal_forgiveness(self):
        """The specific divergence, stated as its own case.

        `3229__1613898700sidingwa gapa.pdf`, font `Spins`: one structurally impossible
        ikar+nasal site, in a region malformed under every candidate map. Forgiven, Spins
        is level with `Kantipur` on the garble axis and the stranded tell decides it
        correctly. Charged -- which is what the hand-copied gated key did -- Spins sits at
        -6 and Kantipur wins outright, mis-decoding the whole font unit.
        """

        key = fb._map_ranking_key_margin_gated(threshold=8.0)
        spins = self._validity(mixed=0, attested=5, penalty=6, ikar_nasal=1)
        kantipur = self._validity(mixed=0, attested=5, penalty=0, ikar_nasal=0)

        assert key(spins)[1] == key(kantipur)[1] == 0.0
        # ...and the raw penalty really did differ, so this is not a vacuous comparison.
        assert spins["penalty"] != kantipur["penalty"]


class TestGateOffIsShipped:
    """The default must be indistinguishable from the chooser without this change.

    🛑 **Stated against a COMPUTED reference, not the literal ``"Preeti"``.** Which map
    the shipped chooser picks for `3719` is a property of the tree's other axes, and the
    property under test is not: with the money-figure axis carried over from the corpus
    line, pass 1 already repairs this document to ``PCS NEPALI``. Hardcoding the old
    winner made four of these tests assert the absence of that axis.
    """

    @staticmethod
    def _shipped_choice():
        """The chooser without this change: the shipped key, no mixed term at all."""

        return fb._choose_legacy_map_ranked(
            AGGREGATE_3719, fb._map_ranking_key, mixed_threshold=None
        )

    def test_env_unset_leaves_3719_on_the_shipped_map(self, monkeypatch):
        monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719)
        assert choice.map_key == self._shipped_choice().map_key

    def test_explicit_none_leaves_3719_on_the_shipped_map(self):
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        assert choice.map_key == self._shipped_choice().map_key

    def test_an_explicit_none_overrides_a_set_environment(self, monkeypatch):
        """A caller that means OFF must not be overridden by an env var."""

        monkeypatch.setenv(fb._MIXED_MARGIN_ENV_VAR, "5")
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        assert choice.map_key == self._shipped_choice().map_key

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

        # The state-distinguishing assertion, MOVED OFF 3719 and onto a fixture where
        # the two states still differ. It used to read `Preeti` OFF / `PCS NEPALI` ON,
        # and on a tree carrying the money-figure axis both are `PCS NEPALI` -- pass 1
        # repairs the document, so 3719 can no longer tell the states apart. Keeping the
        # old pair here would have looked like coverage and asserted nothing.
        # `GATED_AGGREGATE` at M=1 is a measured winner change on this tree.
        shipped = fb._choose_legacy_map_ranked(
            GATED_AGGREGATE, fb._map_ranking_key, mixed_threshold=None
        )
        gated = fb.choose_legacy_map_detailed(GATED_AGGREGATE, mixed_margin=1)
        assert shipped.map_key is not None and gated.map_key is not None
        assert gated.map_key != shipped.map_key


class TestGateOnRepairs3719:
    """3719 must read ``PCS NEPALI`` when the gate is on -- however it gets there.

    ⚠️ **On a tree carrying the money-figure axis these pass without the gate doing any
    of the work.** Pass 1 already decides ``PCS NEPALI``, so the threshold is `0 - M` and
    the gate is silent. That is not a weaker result -- the document is repaired either
    way -- but it means these tests do not, on such a tree, demonstrate the gate.
    `TestTheGatedKeyCannotDriftFromTheShipped` is what protects them there, and
    `test_off_computes_no_mixed_term_at_all` carries the state-distinguishing assertion
    on a fixture that still distinguishes.

    Two of these asserted the intermediate quantity `13` and the literal ``"Preeti"``;
    each is a property of the pre-axis pass-1 winner, not of the gate.
    """

    @staticmethod
    def _mixed_of(choice):
        return fb._mixed_letter_digit_count(
            fb.get_converter_for_map(choice.map_key)(AGGREGATE_3719)
        )

    def test_margin_five_restores_pcs_nepali(self):
        choice = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5)
        assert choice.map_key == "PCS NEPALI"

    def test_the_repair_removes_every_mixed_token(self):
        shipped = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        gated = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5)
        # The invariant is directional, and it is the whole point of the issue: the
        # gated reading carries NO transposed letter/digit tokens, and can never carry
        # more than the shipped one.
        assert self._mixed_of(gated) == 0
        assert self._mixed_of(gated) <= self._mixed_of(shipped)

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
        """A margin no advantage can clear must leave the shipped decision alone.

        Stated as a comparison rather than as the literal ``"Preeti"``: which map that
        is depends on the tree's other axes, and the property does not.
        """

        shipped = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        assert (
            fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=99).map_key
            == shipped.map_key
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
        """Positive control for the counter above: on 3719 a second pass really runs.

        The threshold is DERIVED, not the literal `8.0`. It is
        `mixed(pass-1 winner) - margin`, so it is a property of who pass 1 picked: `8.0`
        when that was `Preeti` at mixed 13, and `-5.0` on a tree whose money-figure axis
        already repairs the document to `PCS NEPALI` at mixed 0. What this test is for is
        that pass 2 runs at all when pass 1 decides, which both cases show.
        """

        # Derived BEFORE the counter is installed: `_count_passes` patches
        # `_choose_legacy_map_ranked`, so deriving the threshold afterwards would append a
        # third pass of the test's own making and the assertion would chase its own tail.
        shipped = fb._choose_legacy_map_ranked(
            AGGREGATE_3719, fb._map_ranking_key, mixed_threshold=None
        )
        expected = float(
            fb._mixed_letter_digit_count(
                fb.get_converter_for_map(shipped.map_key)(AGGREGATE_3719)
            )
            - 5
        )

        passes = self._count_passes(monkeypatch)
        assert (
            fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5).map_key
            == "PCS NEPALI"
        )
        assert passes == [None, expected], f"expected [None, {expected}], got {passes}"


# ------------------------------------------------- decided -> abstain, the other way

#: Two real CIAA spans on which the gate, as first written, turned a DECIDED span into
#: an abstention -- i.e. dropped text that ships today. Found by review, then located by
#: sweeping the first 3 reports: 21,376 distinct spans, 2,015 decided by pass 1, 275
#: winners changed by the gate, and these 2 abstained.
#:
#: 🛑 Synthetic fixtures cannot reach this. 60,000 generated keystroke strings all
#: abstained in PASS 1, so the gate never ran on any of them. That is why these are
#: verbatim corpus spans and not constructed ones.
#: 🛑 **This span no longer serves as the witness, and the test below says why rather
#: than being deleted.** Under VOL-226's garble floor the span abstains in PASS 1, so the
#: test's own precondition assert fires -- correctly, since a fixture that abstains before
#: the gate runs proves nothing about the gate. Kept because the mechanism it found is
#: real and the search that found it was expensive.
#:
#: ⚠️ A span is also the wrong UNIT to re-pin it with. `detect_content_legacy_fonts` joins
#: a font's spans and calls the chooser once, so production decides per AGGREGATE, and
#: spans are too short to clear the accept gate: measured, 32,307 candidate spans over 150
#: documents decide **0** (`tools/span_choice_sweep.py`). At the aggregate level the
#: invariant holds corpus-wide -- 1,389 decided (document, font) aggregates over all 6,236
#: PDFs, **0** of which the gate turns into an abstention at any of M=1..5
#: (`tools/aggregate_gate_witness.py`).
HISTORICAL_DECIDED_THEN_ABSTAINED = (
    1,
    "/sddWo] ?=%,!!,**,^&).() cfkm\"nfO{ u}/sfg'gL ¿kdf nfe k'¥ofpg] / g]kfn",
)

#: A real corpus aggregate that pass 1 DECIDES and the gate genuinely moves at M=1
#: (`Preeti` -> `PCS NEPALI`), so the fallback below is exercised on a span the gate can
#: actually reach. `3850__...मोहन्याल गाउँपालिका`, font `Courier New,Bold`, 117 characters.
#: Its `ambiguous` set is non-empty (`(` and `?`), which is what makes the identity
#: assertion bite rather than merely checking for non-`None`.
GATED_AGGREGATE = (
    " uf]=ef}=g=ldlt pkef]Qmf ;ldlt ;Demf}tf /sd -?=_ k]ZsL lbg'kg]{ -?=_ k]ZsL "
    "lbPsf] -?=_ a(L k]ZsL -?=_ cfg'kflts sL M "
)

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


def test_the_gate_never_turns_a_decided_span_into_an_abstention(monkeypatch) -> None:
    """The gate's stated invariant, in the direction it was not guarded in.

    Its docstring always claimed the gate "can only ever move a span from one map to
    another". `abstain -> decided` was foreclosed by construction; `decided -> abstain`
    was not, and two measured spans did it, so pass 2 now falls back to pass 1's answer.

    🛑 **This is now pinned by forcing pass 2 to abstain, not by a corpus fixture that
    happens to make it abstain.** The original witness -- `HISTORICAL_DECIDED_THEN_
    ABSTAINED` above -- stopped abstaining in pass 2 and started abstaining in pass *1*
    when VOL-226's garble floor changed the shipped ranking, which left the test proving
    nothing but its own precondition. A fallback branch should not depend on a ranking
    change to stay covered: patched, this fails the moment the `else shipped` on
    `choose_legacy_map_detailed`'s last line becomes `else <abstention>`.
    """

    monkeypatch.delenv(fb._MIXED_MARGIN_ENV_VAR, raising=False)
    text = GATED_AGGREGATE

    shipped = fb._choose_legacy_map_ranked(
        text, fb._map_ranking_key, mixed_threshold=None
    )
    assert shipped.map_key is not None, (
        "fixture must be DECIDED without the gate, or this test proves nothing"
    )
    assert shipped.ambiguous, (
        "and it must carry a tie mask, or the identity assert is weak"
    )

    # The fixture really does reach pass 2 in production: the gate MOVES it. Asserted
    # before the patch, so the patched run below cannot be exercising a path the gate
    # never takes on this span.
    assert (
        fb.choose_legacy_map_detailed(text, mixed_margin=1).map_key != shipped.map_key
    )

    real = fb._choose_legacy_map_ranked
    seen: list[float | None] = []

    def abstaining_second_pass(span, key, *, mixed_threshold):
        seen.append(mixed_threshold)
        if mixed_threshold is None:  # pass 1, the shipped ranking -- untouched
            return real(span, key, mixed_threshold=None)
        return fb.LegacyMapChoice(map_key=None, validity=None)

    monkeypatch.setattr(fb, "_choose_legacy_map_ranked", abstaining_second_pass)
    gated = fb.choose_legacy_map_detailed(text, mixed_margin=1)

    assert len(seen) == 2 and seen[0] is None and seen[1] is not None, (
        "both passes must have run, or the fallback was not the thing under test"
    )
    assert gated.map_key is not None, "the gate dropped a span that ships today"
    # Identical, not merely non-None: the fallback must carry the shipped choice's
    # `ambiguous` set too, or masked code points would be decoded as though the tie had
    # been settled.
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

    # 🛑 `GATED_AGGREGATE` at M=1, not `AGGREGATE_3719` at M=5. 3719 was the measured
    # winner-change case before the money-figure axis was carried over from the corpus
    # line; that axis repairs it in PASS 1, so the gate is correctly silent there and
    # this control would fail for the right reason while proving nothing. Re-pointed at a
    # fixture on which the gate still changes a winner on this tree -- measured, `Preeti`
    # -> `PCS NEPALI` -- so the control keeps its teeth instead of being relaxed away.
    text = GATED_AGGREGATE
    shipped = fb._choose_legacy_map_ranked(
        text, fb._map_ranking_key, mixed_threshold=None
    )
    gated = fb.choose_legacy_map_detailed(text, mixed_margin=1)
    assert shipped.map_key is not None and gated.map_key is not None
    assert gated.map_key != shipped.map_key, (
        "this fixture is the measured winner-change case; if the gate no longer moves "
        "it, the fallback has made the gate inert"
    )


class TestTheGatedKeyCannotDriftFromTheShipped:
    """The regression run `9c7a9a3b` measured, pinned as a RELATIONSHIP between the
    two keys rather than as either key's contents -- so it keeps biting as axes are
    added.

    History, because it is the reason these tests exist. The gate's pass-2 key was
    written as a hand copy of the shipped tuple when that tuple had six elements.
    Inserting the money-figure axis at index 3 then collided with the copy's ``ELIGIBLE``
    at that index -- so pass 2 OVERWROTE the figures slot, and because
    :func:`choose_legacy_map_detailed` returns pass 2 on every deciding unit, enabling the
    gate deleted the figures axis corpus-wide. Cherry-picking the two changes together
    auto-merges CLEAN, so nothing warned. Measured footprint: all six figures repairs
    reverted, including `3719`, the document the gate exists to repair.

    Upstream has since removed the hand copy -- :func:`_map_ranking_key_margin_gated`
    splices into the ungated key instead of restating it -- which kills that specific
    mechanism. These tests stay because the *index* is still a separate constant from the
    axis order it has to track, and this is what fails if the two drift again.
    """

    @staticmethod
    def _validity(**overrides):
        return {**_SENTINELS, **overrides}

    def test_the_gated_key_is_the_shipped_key_plus_exactly_one_element(self):
        """Delete the indicator and what is left must be the shipped tuple EXACTLY.

        This is the drift detector: it fails the moment an axis exists in the shipped
        key and not in the gated one, whatever that axis is and wherever it sits.
        """

        for mixed, threshold in ((0.0, 5.0), (9.0, -1.0), (3.0, 3.0), (7.0, 0.0)):
            validity = self._validity(mixed=mixed)
            shipped = fb._map_ranking_key(validity)
            gated = fb._map_ranking_key_margin_gated(threshold=threshold)(validity)
            stripped = gated[:_ELIGIBLE_INDEX] + gated[_ELIGIBLE_INDEX + 1 :]
            assert stripped == shipped, f"mixed={mixed} threshold={threshold}"

    def test_every_shipped_axis_survives_into_the_gated_key(self):
        """Axis by axis, by VALUE, so a vanished axis cannot hide behind a zero."""

        gated = fb._map_ranking_key_margin_gated(threshold=-1.0)(dict(_SENTINELS))
        for axis, value in _SENTINELS.items():
            if axis in {"mixed", "stranded", "penalty"}:
                continue  # `mixed` is not an axis; `stranded`/`penalty` enter signed
            assert value in gated, f"{axis} ({value}) is missing from the gated key"

    def test_the_silent_case_leaves_the_shipped_decision_alone_end_to_end(self):
        """The invariant the docstring claimed and the joint tip violated.

        On a tree carrying ``figures``, `3719`'s span is already repaired by that axis,
        so pass 1 wins with mixed 0 and the threshold `0 - 5` makes every candidate
        ineligible -- the silent case. The gate must then return pass 1's decision. On
        the pre-fix joint tip it returned ``Preeti``, re-damaging the document.
        """

        shipped = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=None)
        silent = fb.choose_legacy_map_detailed(AGGREGATE_3719, mixed_margin=5)
        assert silent.map_key == shipped.map_key

    def test_a_constant_indicator_cannot_reorder_candidates(self):
        """Why the silent case holds by construction and not by inspection."""

        key = fb._map_ranking_key_margin_gated(threshold=-1.0)
        weak = self._validity(figures=1.0, attested=1.0)
        strong = self._validity(figures=9.0, attested=1.0)
        assert key(weak)[_ELIGIBLE_INDEX] == key(strong)[_ELIGIBLE_INDEX] == 0.0
        assert (key(strong) > key(weak)) == (
            fb._map_ranking_key(strong) > fb._map_ranking_key(weak)
        )
