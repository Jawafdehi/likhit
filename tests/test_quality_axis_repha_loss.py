"""VOL-168: `repha_loss` must see the corruption family that actually dominates.

Three defects, all pushing the verdict the same way — toward `clean`:

1. `REPHA_PAIRS` probes the `य`-substituting form. For 9 of its 11 pairs the
   corpus's dominant form substitutes `ि` instead, so the shipped probe observes
   6,635 whole-token corruptions against 53,915 it cannot see — 11.0% of the
   phenomenon it is named for.
2. `purity` is a whole-document ratio with no absolute floor, so a report can
   carry hundreds of destroyed words and still clear 0.75 on the strength of its
   undamaged pages. 102 of the 299 v12 documents holding >= 5 whole-token repha
   corruptions shipped clean on all eight checks.
3. `कार्य` is a prefix substring of `कार्यालय` and the check counts per form
   with `str.count`, so every `कार्यालय` was counted twice on the canonical
   side — a 1.99x inflation of `good`, and `purity = good / (good + bad)`.

Ground truth: `runs/vol168/FINDING-01-repha-forms-87393de3.md` (defects 1 and 3,
all 6,223 v12 transcripts, no sampling) and `FINDING-02-floor-and-bands-*.md`
(defect 2, the control percentiles and the floor table).

The fix is opt-in. `extended=False` must stay byte-identical to the shipped
instrument, because v12 and v13 published verdict counts computed with it.
"""

import pytest

from likhit.quality.axes import (
    REPHA_CORRUPT_FLOOR,
    REPHA_PAIRS,
    REPHA_PAIRS_EXTENDED,
    REPHA_PAIRS_I_FORM,
    _count_longest,
    check_repha_loss,
)


#: The shipped scoring formula, transcribed from `audit_quality.py.BEFORE-vol168`
#: so the default path is checked against the old arithmetic rather than against
#: itself. If this and `check_repha_loss(text)` ever disagree, the opt-in
#: guarantee is broken.
def shipped_repha_loss(text):
    good = sum(text.count(g) for g, _ in REPHA_PAIRS)
    bad = sum(text.count(b) for _, b in REPHA_PAIRS)
    if good + bad < 10:
        return "clean", {"probe_hits": good + bad}
    purity = good / (good + bad) if (good + bad) else 0.0
    ev = {"canonical": good, "repha_corrupt": bad, "purity": round(purity, 3)}
    if purity < 0.35:
        return "garbled", ev
    if purity < 0.75:
        return "suspect", ev
    return "clean", ev


#: A clean report: canonical words, no corruption.
CLEAN = " ".join(["कार्यालय", "निर्माण", "आर्थिक", "वर्ष", "खर्च"] * 8)

#: Document 5430's shape — `खप्तड छेडेदह गाउँपालिका, २०७८।७९`, which ships
#: `purity=1.0`, `repha_corrupt=0`, verdict `clean` on all eight checks while
#: carrying 157 `ि`-form corrupt tokens. The shipped probe does not "nearly
#: catch" it; it sees nothing at all in it.
I_FORM_ONLY = " ".join(["कार्यालय"] * 40 + ["कायािलय"] * 40)

#: The same corruption in the form the shipped probe does look for.
Y_FORM_ONLY = " ".join(["कार्यालय"] * 40 + ["कायायलय"] * 40)


def test_default_is_byte_identical_to_the_shipped_instrument():
    """The opt-in guarantee, on every fixture in this file.

    VOL-523 narrowed this from whole-payload equality to *verdict* equality plus
    payload **superset**, and only on the low-signal branch. The reason the
    original form had to give: it pinned `{"probe_hits": n}` as the entire
    payload of that branch, which is precisely the field collapse that made
    "all repha destroyed" indistinguishable from "all repha well-formed". Every
    key the shipped instrument emitted still exists and still carries the same
    value -- so nothing a consumer could read has changed -- and the verdict,
    which is what v12's and v13's published counts are counts *of*, is asserted
    equal unconditionally.
    """
    for name, text in [
        ("clean", CLEAN),
        ("i_form", I_FORM_ONLY),
        ("y_form", Y_FORM_ONLY),
        ("empty", ""),
        ("short", "कार्यालय कायािलय"),
    ]:
        got_verdict, got_ev = check_repha_loss(text)
        want_verdict, want_ev = shipped_repha_loss(text)
        assert got_verdict == want_verdict, name
        # every shipped key survives, with its shipped value
        assert want_ev.items() <= got_ev.items(), name


def test_a_zero_canonical_count_is_visible_rather_than_collapsed():
    """VOL-523. The defect: `र्` -> `ं` produces a form that is neither the
    canonical word nor its repha-*stripped* corruption, so `good = bad = 0`, the
    `< 10` branch fires, and the payload used to become `{"probe_hits": 0}` --
    reporting nothing at all about repha while still saying `clean`.

    Measured on the real subject (VOL-508 lane A OCR transcript, run
    `2561ac89`): 7,425 `र्` destroyed, `repha_loss` payload
    `{"probe_hits": 1}`, whole-audit verdict `clean`, `failing == []`, on both
    probes.

    The verdict is still `clean` by design -- with no probe hits there is
    nothing to compare. What must not happen again is the count going missing.
    """
    destroyed = " ".join(["कार्यालय", "निर्माण", "वर्ष"] * 20).replace("र्", "ं")
    assert "र्" not in destroyed

    for extended in (False, True):
        verdict, ev = check_repha_loss(destroyed, extended=extended)
        assert verdict == "clean", extended
        # the point of the test: the zero is REPORTED, not replaced
        assert ev["canonical"] == 0, extended
        assert ev["repha_corrupt"] == 0, extended
        assert ev["undetermined"] is True, extended
        # and it is distinguishable from a measured clean, which has a purity
        # and no `undetermined` marker
        assert "purity" not in ev, extended
        measured_verdict, measured_ev = check_repha_loss(CLEAN, extended=extended)
        assert measured_verdict == "clean", extended
        assert "undetermined" not in measured_ev, extended
        assert measured_ev["purity"] == 1.0, extended


def test_undetermined_marks_the_band_that_did_no_comparison():
    """The marker must track the branch, not the verdict: a document with >= 10
    probe hits is measured and must never carry `undetermined`, however clean."""
    assert check_repha_loss(CLEAN)[1].get("undetermined") is None
    assert check_repha_loss(I_FORM_ONLY, extended=True)[1].get("undetermined") is None
    # one hit short of the cut is undetermined; at the cut it is measured
    nine = " ".join(["कार्य"] * 9)
    ten = " ".join(["कार्य"] * 10)
    assert check_repha_loss(nine)[1]["undetermined"] is True
    assert check_repha_loss(ten)[1].get("undetermined") is None


def test_shipped_probe_is_blind_to_the_dominant_form():
    """Defect 1, stated as the failure it is: a document that is 50% destroyed
    reads perfect, and its `repha_corrupt` is 0 rather than merely low."""
    verdict, ev = check_repha_loss(I_FORM_ONLY)
    assert verdict == "clean"
    assert ev["repha_corrupt"] == 0
    assert ev["purity"] == 1.0


def test_extended_probe_catches_the_dominant_form():
    """Half the words destroyed reads `purity=0.5` — `suspect`, since the
    `garbled` cut is 0.35. The point of the test is the 1.0 -> 0.5 move."""
    verdict, ev = check_repha_loss(I_FORM_ONLY, extended=True)
    assert verdict == "suspect"
    assert ev["repha_corrupt"] == 40
    assert ev["purity"] == 0.5
    assert ev["probe"] == "extended"


def test_extended_still_catches_what_the_shipped_probe_caught():
    """Defect 1's fix must be additive — the `य` forms stay in the list, and
    both probes reach the same band on the form both can see."""
    assert check_repha_loss(Y_FORM_ONLY)[0] == "suspect"
    assert check_repha_loss(Y_FORM_ONLY, extended=True)[0] == "suspect"
    heavy = " ".join(["कार्यालय"] * 5 + ["कायायलय"] * 95)
    assert check_repha_loss(heavy)[0] == "garbled"
    assert check_repha_loss(heavy, extended=True)[0] == "garbled"


def test_karya_is_not_counted_twice_inside_karyalaya():
    """Defect 3. `कार्य` ⊂ `कार्यालय`, and the shipped per-form `str.count`
    scores one word as two canonical hits."""
    one_word = "कार्यालय"
    assert sum(one_word.count(g) for g, _ in REPHA_PAIRS) == 2
    assert _count_longest(one_word, [g for g, _ in REPHA_PAIRS_EXTENDED]) == 1


def test_inflected_canonical_forms_still_count():
    """The nesting fix must not become whole-token equality. Nepali is
    agglutinative: `कार्यालयको` is a real occurrence of `कार्यालय`, and token
    equality discards 918k canonical occurrences corpus-wide."""
    assert _count_longest("कार्यालयको", [g for g, _ in REPHA_PAIRS_EXTENDED]) == 1
    assert (
        _count_longest("कार्यालयले कार्यालयमा", [g for g, _ in REPHA_PAIRS_EXTENDED]) == 2
    )


def test_floor_fires_when_purity_alone_would_pass():
    """Defect 2: a big clean document absorbs a lot of destroyed words. Purity
    here is above the 0.75 cut, so only an absolute count can flag it."""
    text = " ".join(["कार्यालय"] * 1000 + ["कायािलय"] * REPHA_CORRUPT_FLOOR)
    verdict, ev = check_repha_loss(text, extended=True)
    assert ev["purity"] >= 0.75
    assert ev["repha_corrupt"] >= REPHA_CORRUPT_FLOOR
    assert verdict == "suspect"
    # One below the floor, same shape, must stay clean — the floor is the only
    # thing separating them.
    below = " ".join(["कार्यालय"] * 1000 + ["कायािलय"] * (REPHA_CORRUPT_FLOOR - 1))
    assert check_repha_loss(below, extended=True)[0] == "clean"


def test_floor_without_the_forms_would_be_inert_on_this_family():
    """Why the two halves cannot be landed separately. VOL-168 proposed the
    floor on `repha_corrupt`, the field the shipped check already emits; on that
    field the corruption above counts 0, so no floor of any size can fire.
    Measured corpus-wide the literal reading moves 18 documents against the 155
    its own calibration implies."""
    text = " ".join(["कार्यालय"] * 1000 + ["कायािलय"] * 500)
    assert check_repha_loss(text)[1]["repha_corrupt"] == 0
    assert check_repha_loss(text)[0] == "clean"


def test_deletion_family_pairs_have_no_i_form_entry():
    """`प्रदेश` -> `पदेश` and `वर्गीकरण` -> `वगीकरण` delete `र्` without
    substituting a glyph, so they were already probing their own dominant form.
    Adding an invented `ि`-variant for them would be a guess, not a
    measurement."""
    i_form_canonicals = {g for g, _ in REPHA_PAIRS_I_FORM}
    assert "प्रदेश" not in i_form_canonicals
    assert "वर्गीकरण" not in i_form_canonicals
    assert len(REPHA_PAIRS_I_FORM) == len(REPHA_PAIRS) - 2


@pytest.mark.parametrize("text", [CLEAN, I_FORM_ONLY, Y_FORM_ONLY, "", "कार्य"])
def test_extended_never_lowers_severity(text):
    """All three corrections bias against `clean`, so the extended probe must
    never rescue a document the shipped one flagged. A drop would mean the
    dedup had eaten real corruption."""
    rank = {"clean": 0, "suspect": 1, "garbled": 2}
    assert (
        rank[check_repha_loss(text, extended=True)[0]]
        >= rank[check_repha_loss(text)[0]]
    )
