"""The detector reached further than the removers, and the difference was kept data.

🛑 **This is the defect the platform's redaction gate found, not a coverage gap.**
``signals.LABELLED`` matches a label, then ``[^\\n]{0,30}?``, then the digits. The removal
rules matched a label, an optional colon, whitespace, and nothing else. Every value sitting
behind a word, a comma, a bracket or a column of spaces was therefore **counted by the
scanner and left in place by the redactor** -- so text this package reported as redacted
still carried the value, and a caller that re-scans its own output (which is what
JawafdehiAPI's writers now do) refuses the document.

Measured over the 67 live charge sheets that refused on 27 October 2026, against the same
corpus before this change:

===========================  ========  =======
metric                       before    after
===========================  ========  =======
documents still refusing          67       31
bank account values left         136       40
citizenship values left           21       18
date of birth values left        102       25
6+ digit runs left in text    14,002   13,838
===========================  ========  =======

And over 106 control documents carrying the same labels -- the over-removal check, since a
wider reach can take the wrong number: **34 spans newly removed, every one an identifier,
and none of the spans the old build removed lost.**

Each fixture below is a shape from that corpus with its digits replaced. The shapes are the
measurement; the digits are nobody's.
"""

from __future__ import annotations

import pytest

from likhit.privacy import RedactionMode, placeholders, redact_text


def _strict(text: str) -> str:
    return redact_text(text, mode=RedactionMode.STRICT).text


# --- the gap reaches one word, never two ------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "खाता नं. क्रमशः १११२२२३३३४४४ र",  # "respectively"
        "Account Number भनी ११११२२२२३३३३४४४४ मा",  # "stating"
        "खाता नं, ११२२३३४४५५६६७७ मा रहेको",  # a comma for a colon
        "खाता नम्बर (११२२३३४४५५६) बाट",  # brackets
    ],
)
def test_one_word_or_punctuation_between_label_and_value_is_crossed(text: str) -> None:
    assert placeholders.BANK_ACCOUNT in _strict(text)


@pytest.mark.parametrize(
    ("text", "must_survive"),
    [
        # Two words is a sentence, and the number at the end of one belongs to it.
        ("नागरिकताको प्रमाणपत्र दिइएको भनी २०३६।१।१२ मा", "२०३६।१।१२"),
        ("नागरिकतासम्बन्धी दर्ता किताब २०३२/०३३ मा", "२०३२/०३३"),
        # A cue naming another field, however short the gap.
        ("नागरिकता बदर भएको मिति २०७८/०६/२२ गते", "२०७८/०६/२२"),
        ("खाता नम्बरमा रहेको रकम ५,००,००० को", "५,००,०००"),
        ("नागरिकता सिफारिस २५ जना", "२५"),
        # A law's year is a citation. Redacting it damages what the document is read for.
        ("नेपाल नागरिकता ऐन, २०२० / २०६३ बमोजिम", "२०२० / २०६३"),
    ],
)
def test_prose_and_other_fields_are_not_crossed(text: str, must_survive: str) -> None:
    assert must_survive in _strict(text)


# --- line breaks -------------------------------------------------------------


def test_a_value_wrapped_onto_the_next_line_is_still_redacted() -> None:
    """The corpus wraps immediately after the label often enough to matter.

    Excluding the line break from the gap cost 306 bank accounts that the *narrow* gap had
    been removing -- 599 -> 440 in one document -- which is the measurement that put it
    back.
    """

    assert placeholders.BANK_ACCOUNT in _strict("खाता नं.\n1234567890123456 मा")


def test_words_on_the_far_side_of_a_line_break_are_not_crossed() -> None:
    """A label whose own value is gone must not reach down to the next field's number.

    This is the failure that the PDF pass caught first: `नागरिकता नं.` on one line, a mobile
    number on the next, and a gap that read on through took the mobile as a citizenship
    number -- a redaction pass eating an unrelated identifier.

    ⚠️ At ``identifiers``, because ``strict`` removes a mobile on its own merits and the
    question here is whether the *citizenship* rule reaches it.
    """

    redacted = redact_text(
        "नागरिकता नं.\nसम्पर्क ९८५१२३४५६७", mode=RedactionMode.IDENTIFIERS
    ).text

    assert "९८५१२३४५६७" in redacted
    assert placeholders.CITIZENSHIP not in redacted


def test_redacting_twice_removes_nothing_the_second_time() -> None:
    """Re-running the sweep is normal, so redaction has to be idempotent.

    A placeholder is 26 characters with no digits in it -- comfortably inside the gap -- so
    a gap that could read across one would let a spent label take whatever number came
    next.
    """

    once = _strict("खाता नं. ११११२२२२३३३ र चेक नं. ९९८८७७६")
    assert once == _strict(once)
    assert "९९८८७७६" in once


# --- bank accounts are not always digits -------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "खाता नं. 012ABC345678901 मा",  # the code sits inside the value
        "खाता नं. XY-1111-222222-3333 मा",  # code, then a separator
        "खाता नं. १११२२२३.३३४४४५५ बाट",  # one account written with a dot
        "खाता नं. ११२,३३४४५५६६७ मा",  # …and with a comma
        "खाता नं.१११२२२३३३४४४५५५६६६७७७ मा",  # 21 digits, over the old ceiling
    ],
)
def test_a_bank_account_carrying_more_than_digits_is_redacted(text: str) -> None:
    redacted = _strict(text)
    assert placeholders.BANK_ACCOUNT in redacted
    for run in ("012ABC", "222222", "३३४४४५५", "३३४४५५६६७", "४४४५५५६६६७७७"):
        assert run not in redacted


def test_the_latin_code_goes_with_the_account_not_the_gap() -> None:
    """`(ABCDEF1234567)` is one value in brackets, not a word and then a number."""

    assert _strict("खाता नं. (ABCDEF1234567) बाट") == (
        f"खाता नं. ({placeholders.BANK_ACCOUNT}) बाट"
    )


def test_an_amount_is_not_an_account_however_it_is_written() -> None:
    assert "९,९९,९९९" in _strict("खाता नं. को रकम रु.९,९९,९९९/- जम्मा")


# --- citizenship -------------------------------------------------------------


def test_the_certificate_abbreviation_is_a_label_not_prose() -> None:
    """`ना.प्र.प.नं.` -- the `प` of प्रमाणपत्र, written as its own abbreviated field."""

    assert placeholders.CITIZENSHIP in _strict("ना.प्र.प.नं.१११।२२३३४ जारी भएको")


# --- a birth year is a date of birth -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "जन्म मिति २००७ साल हो",  # the year alone, which is 100 of 102 values left
        "जन्म मिति २०११।२ साल रहेको",  # year and month, too short for the window rules
        "जन्म मिति 2010/7 को",
        "जन्म मिति वि.स. २०१७ देखिन्छ",  # the era marker belongs to the label
        "जन्म मिति ई.सं. २००४।९।३ हो",
        "Date of Birth: 1979",
    ],
)
def test_a_birth_year_is_removed(text: str) -> None:
    assert placeholders.DATE_OF_BIRTH in _strict(text)


@pytest.mark.parametrize(
    ("text", "must_survive"),
    [
        # A range bound. There is no person in the sentence to identify.
        ("जन्म मिति २०७० सम्मको विवरण छ।", "२०७०"),
        ("जन्म मिति २०६० देखि २०७० सम्म", "२०६०"),
        # The sentence-terminating danda, and a staff count behind it.
        ("जन्म मिति २०७७। ३ जना कर्मचारी छन्।", "३ जना"),
        # No birth label: an ordinary document date.
        ("मिति २०७७।०४।०१ मा दर्ता", "२०७७।०४।०१"),
        # Below the floor: three digits is not a year.
        ("Date of Birth: 197", "197"),
    ],
)
def test_a_year_that_is_not_a_birth_year_survives(text: str, must_survive: str) -> None:
    assert must_survive in _strict(text)


def test_a_full_date_is_taken_whole_not_by_its_first_field() -> None:
    """The year rule must never get at a date the window rules describe."""

    assert _strict("जन्म मिति २०२०।०४।०१ हो") == (
        f"जन्म मिति {placeholders.DATE_OF_BIRTH} हो"
    )


@pytest.mark.parametrize(
    ("text", "removed"),
    [
        ("जन्म मिति २००७ साल", True),
        ("Date of Birth: 1979", True),
        # Four digits that are not a year. Length stopped being the test here, and
        # the table pass carries the same bound for the same reason.
        ("जन्म मिति १२३४ हो", False),
        ("Date of Birth: 9999", False),
    ],
)
def test_four_digits_are_a_year_only_when_they_read_as_one(
    text: str, removed: bool
) -> None:
    out = _strict(text)

    assert (placeholders.DATE_OF_BIRTH in out) is removed
