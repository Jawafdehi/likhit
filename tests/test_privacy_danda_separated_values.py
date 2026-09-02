"""The danda U+0964 is a field separator inside an identifier, not sentence punctuation.

🛑 **This is a privacy defect, not a coverage gap.** Nepali writes a date as
``२०२०।०४।०१`` -- danda between the digit groups. A value class listing ``- / .`` and
stopping there fails twice over, and both failures keep the value:

* the pattern's value window stops at the first danda, so the span no longer reaches the
  measured digit-count floor and :func:`~likhit.privacy.redact.redact` refuses it on length;
* :data:`~likhit.privacy.redact.VALUE_FORBIDDEN`, built as the complement of the same
  separator set, reads the danda as surrounding prose and refuses the replacement outright.

Measured over the 6,234 transcripts of the v19 tree the redactor runs against, comparing the
two classes: **+79 dates of birth in 40 documents and +27 citizenship numbers in 12
documents** kept a named private individual's identifier in public text for this reason alone.
Every date fixture below is a verbatim span from that measurement.

🛑 **But the danda is also the sentence terminator**, so admitting it is not simply a widening
-- see :func:`~likhit.privacy.redact._value_window` and the two boundary tests at the bottom of
this file, which are the other half of the change.
"""

from __future__ import annotations

import pytest

from likhit.privacy import placeholders
from likhit.privacy.redact import (
    _CIT_LEGACY_SEPARATORS,
    _DOB_LEGACY_SEPARATORS,
    VALUE_FORBIDDEN,
    redact_inline_text,
)

#: Verbatim from the published tree, with the document each was found in.
_REAL_DANDA_DATES = (
    ("जन्म मिति २०२०।०४।०१", "13100__हनुमाननगर कंकालिनी नगरपालिका, सप्तरी"),
    ("जन्म मिति २०२१।३।२", "13219__माई नगरपालिका, इलाम"),
    ("जन्म मिति २००३।४।१९", "2388__1612695753भुम्लु गाउँपालिका"),
    ("जन्म मितिः२००३।९।६", "2398__161269627814_Dispatch_Indrasarowar_Rural"),
    ("जन्म मितिः२०१३।४।५", "2398__161269627814_Dispatch_Indrasarowar_Rural"),
)


@pytest.mark.parametrize(("span", "source"), _REAL_DANDA_DATES)
def test_a_danda_separated_date_of_birth_is_redacted(span: str, source: str) -> None:
    redacted, journal, _ = redact_inline_text(span)

    assert placeholders.DATE_OF_BIRTH in redacted, f"still public, from {source}"
    assert [row["kind"] for row in journal] == ["date_of_birth"]
    # The label survives; only the value goes. That is the whole design of this pass.
    assert redacted.startswith("जन्म मिति")


def test_a_danda_separated_citizenship_number_is_redacted() -> None:
    """The 27-value half of the measurement."""

    redacted, journal, _ = redact_inline_text("नागरिकता नं. १२।३४।५६७८९")

    assert placeholders.CITIZENSHIP in redacted
    assert [row["kind"] for row in journal] == ["citizenship"]


def test_the_journal_records_a_danda_separated_value_as_having_separators() -> None:
    """🛑 The shape field must agree with the pattern that produced the span.

    ``value_had_separators`` was a hardcoded ``[-/.\\s]``, so admitting the danda made the set
    a *third* thing written down separately -- and a danda-separated date, the whole point of
    this change, was journalled ``False``. This pass's precision is measured from its journal,
    and this is the field that says whether the span looked like a grouped identifier or a bare
    digit run, so getting it wrong misreports exactly the class the change adds.

    The control is the second assertion: a value with no separator at all must still read
    ``False``, or this test would pass against a field hardcoded to ``True``.
    """

    _redacted, journal, _stats = redact_inline_text("जन्म मिति २०२०।०४।०१")
    assert journal[0]["value_had_separators"] is True, journal

    _redacted, journal, _stats = redact_inline_text("जन्म मिति २०२००४०१")
    assert journal[0]["value_had_separators"] is False, journal


def test_the_separator_set_and_the_guard_cannot_disagree() -> None:
    """Every separator a value class admits must survive the guard.

    🛑 The bug was precisely a disagreement between the two: the guard is the *complement*
    of the separator set, so a separator present in one and absent from the other refuses
    every value containing it. Asserting the relationship rather than a list of characters
    is what stops a future separator reintroducing it.

    The second half asserts the containment the guard is built on. ``VALUE_FORBIDDEN`` is the
    complement of the *date* set alone, which is only sound while the citizenship set is a
    subset of it; widening citizenship past dates would refuse every citizenship value
    carrying the new character, and nothing else in this file would notice.
    """

    for separator in ("-", "/", ".", " ", "।"):
        value = f"२०७०{separator}०१{separator}०२"
        assert not VALUE_FORBIDDEN.search(value), (
            f"{separator!r} is admitted by a value pattern but refused by the guard"
        )

    extra = set(_CIT_LEGACY_SEPARATORS) - set(_DOB_LEGACY_SEPARATORS)
    assert not extra, (
        f"the citizenship class admits {sorted(extra)}, which VALUE_FORBIDDEN -- built from "
        f"the date class -- would refuse in every citizenship value"
    )


def test_prose_after_the_label_is_still_not_redacted() -> None:
    """The control: widening the separator set must not let a window escape into prose.

    ⚠️ **What this does *not* assert, and why.** It does not assert
    ``date_of_birth_refused_nondigit`` -- that counter reads 0 here, measured, and asserting
    1 would have been a false claim about the mechanism. Guard 1 fires only on a value the
    *pattern* matched, and once :data:`VALUE_FORBIDDEN` is the exact complement of the
    separator set, no matched value can contain a forbidden character: the pattern rejects
    ``सम्मको`` before the guard is reached. Guard 1 is now defence in depth against a future
    widening of the class, not a live filter, and the outcome -- the value stays -- is what
    is worth pinning.
    """

    assert VALUE_FORBIDDEN.search("२०७० सम्मको")

    redacted, journal, _stats = redact_inline_text("जन्म मिति २०७० सम्मको विवरण छ।")

    assert placeholders.DATE_OF_BIRTH not in redacted
    assert journal == []
    assert "सम्मको" in redacted


def test_an_amount_cue_beside_the_label_is_still_refused() -> None:
    """Guard 3's control, kept alongside guard 1's: a danda bypasses neither."""

    redacted, journal, _ = redact_inline_text("नागरिकता नं. रु ११।२२।३३४४५")

    assert placeholders.CITIZENSHIP not in redacted
    assert journal == []


def test_a_sentence_ending_danda_is_not_read_as_a_separator() -> None:
    """The danda's other job, and why admitting it does not swallow the following sentence.

    ``छ।`` ends a sentence. Requiring a digit on both sides of the *whole* window is not what
    makes this safe -- see the next test, where a digit does follow -- but it does mean a
    trailing danda with prose after it cannot extend a match.
    """

    redacted, _journal, _stats = redact_inline_text("जन्म मिति २०५०/०१/०२ रहेको छ।")

    assert redacted.endswith("रहेको छ।")
    assert placeholders.DATE_OF_BIRTH in redacted


#: ``(text, the span that must survive, whether a date is still redacted)``.
#:
#: A sentence terminated by a danda whose next sentence opens with a number. In both, the flat
#: class spans a window with a digit at each end that lands inside the 5--10 digit guard, so
#: nothing but the lookahead stops it -- but what is *left* differs, and the difference is the
#: pre-existing digit floor rather than anything this change does:
#:
#: * ``२०७७`` is a bare four-digit year, below the floor, so once the window cannot reach into
#:   the next sentence there is nothing here to redact at all -- which is also what `main`
#:   does with it, measured;
#: * ``२०५८।०९।१२`` is a full eight-digit date, so the window narrows to it and it still goes.
_SENTENCE_BOUNDARY_CASES = (
    ("जन्म मिति २०७७। ३ जना कर्मचारी छन्।", "३ जना कर्मचारी", False),
    ("जन्म मिति २०५८।०९।१२। ४५ वर्ष पुगेको।", "४५ वर्ष", True),
)


@pytest.mark.parametrize(
    ("text", "must_survive", "date_redacted"), _SENTENCE_BOUNDARY_CASES
)
def test_a_value_window_does_not_cross_a_sentence_boundary(
    text: str, must_survive: str, date_redacted: bool
) -> None:
    """🛑 The cost half of admitting the danda, and the reason for the lookahead.

    Admitted flatly, the danda is the only separator that is also the sentence terminator, so
    the window runs out of the date, past the boundary and into the next sentence's leading
    number -- taking a staff count and an age with it. **Neither guard can see this**:
    ``VALUE_FORBIDDEN`` is the complement of the same set so it permits the danda by
    construction, and both spans land inside the 5--10 digit window. They are journalled as
    ordinary date-of-birth redactions, so the "values removed" total absorbs them silently.

    The direction is safe for disclosure and destructive for the audit data this corpus exists
    to carry, which is why it is a defect rather than an acceptable trade.

    Both assertions matter. The first is the bite -- remove the lookahead from
    :func:`~likhit.privacy.redact._value_window` and the surviving text disappears from both
    cases. The second is the control, and it is why ``date_redacted`` is a parameter rather
    than a constant: on the second case the date must still be gone, so a "fix" that simply
    stopped redacting danda-separated dates fails here instead of looking like a pass.
    """

    redacted, journal, _stats = redact_inline_text(text)

    assert must_survive in redacted, (
        f"the value window crossed the sentence-ending danda and removed {must_survive!r}"
    )
    assert (placeholders.DATE_OF_BIRTH in redacted) is date_redacted, redacted
    assert [row["kind"] for row in journal] == (
        ["date_of_birth"] if date_redacted else []
    )


def test_the_lookahead_still_admits_a_separator_followed_by_a_space() -> None:
    """⚠️ The asymmetry is load-bearing: only the danda is constrained.

    Applying the lookahead to `- / .` as well reads tidier and costs a real identifier.
    Measured over the same tree, this verbatim span -- a senior citizen's citizenship number
    with a stray space after the slash, which `main` redacts today -- is the *only* span the
    wider form changes anywhere in the corpus: one loss, no gain. So this pins the freedom the
    legacy separators keep, and a future tidy-up that constrains them fails here.
    """

    redacted, journal, _stats = redact_inline_text("ना.प्र.नं.१९१०/ १५७")

    assert placeholders.CITIZENSHIP in redacted
    assert [row["kind"] for row in journal] == ["citizenship"]
