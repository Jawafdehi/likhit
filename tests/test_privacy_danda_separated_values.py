"""The danda U+0964 is a field separator inside an identifier, not sentence punctuation.

🛑 **This is a privacy defect, not a coverage gap.** Nepali writes a date as
``२०२०।०४।०१`` -- danda between the digit groups. A value class listing ``- / .`` and
stopping there fails twice over, and both failures keep the value:

* the pattern's value window stops at the first danda, so the span no longer reaches the
  measured digit-count floor and :func:`~likhit.privacy.redact.redact` refuses it on length;
* :data:`~likhit.privacy.redact.VALUE_FORBIDDEN`, built as the complement of the same
  separator set, reads the danda as surrounding prose and refuses the replacement outright.

Measured over the 6,235 transcripts of the published corpus, comparing the two classes:
**92 dates of birth in 40 documents and 27 citizenship numbers in 11 documents** -- 49
documents -- kept a named private individual's identifier in public text for this reason
alone. Every date fixture below is a verbatim span from that measurement.
"""

from __future__ import annotations

import pytest

from likhit.privacy import placeholders
from likhit.privacy.redact import VALUE_FORBIDDEN, redact_inline_text

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


def test_the_separator_set_and_the_guard_cannot_disagree() -> None:
    """Every separator a value class admits must survive the guard.

    🛑 The bug was precisely a disagreement between the two: the guard is the *complement*
    of the separator set, so a separator present in one and absent from the other refuses
    every value containing it. Asserting the relationship rather than a list of characters
    is what stops a future separator reintroducing it.
    """

    for separator in ("-", "/", ".", " ", "।"):
        value = f"२०७०{separator}०१{separator}०२"
        assert not VALUE_FORBIDDEN.search(value), (
            f"{separator!r} is admitted by a value pattern but refused by the guard"
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
    """The danda's other job, and why admitting it does not swallow a sentence.

    ``छ।`` ends a sentence. The value pattern requires a digit on both sides of the run, so
    a trailing danda cannot extend a match past the number it follows -- checked rather than
    assumed, because this is the one way the widening could have over-reached.
    """

    redacted, _journal, _stats = redact_inline_text("जन्म मिति २०५०/०१/०२ रहेको छ।")

    assert redacted.endswith("रहेको छ।")
    assert placeholders.DATE_OF_BIRTH in redacted
