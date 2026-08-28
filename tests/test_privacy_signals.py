"""The PII scanner: what it finds, and the disclosure property it must not lose."""

from __future__ import annotations

from likhit.privacy import scan_text
from likhit.privacy.signals import INSTITUTION


def test_scan_returns_counts_only_and_never_a_match() -> None:
    """🛑 The load-bearing property, asserted structurally rather than by inspection.

    A report on the personal data a corpus contains has to be publishable *beside* that
    corpus; one that quotes its matches is itself a disclosure. Checking that the matched
    strings are absent from the output would pass today and keep passing if someone added a
    ``"samples"`` field -- because the assertion would still only look at the values it knew
    about. So this asserts the *shape*: every value is an integer count, full stop.
    """

    secret_email = "unique-local-part-9f3a@example.org"
    secret_mobile = "९८४१२३४५६७"
    high_precision, name_shaped = scan_text(
        f"सम्पर्क {secret_email} फोन {secret_mobile} नागरिकता नं. १२-३४-५६७८९"
    )

    assert high_precision["email"] == 1
    assert high_precision["mobile"] == 1
    assert high_precision["citizenship"] == 1

    for counter in (high_precision, name_shaped):
        for key, value in counter.items():
            assert isinstance(value, int), (
                f"{key!r} carries {value!r}, which is not a count -- if this counter has "
                f"grown a field that can hold matched text, the scan is no longer safe to "
                f"publish alongside the corpus"
            )
            assert isinstance(key, str) and key.isascii(), key


def test_the_honorific_is_not_counted_as_personal_before_an_institution() -> None:
    """The trap the module exists to avoid.

    ``श्री`` is applied to offices as readily as to people in Nepali government prose, so a
    bare count of it is not a PII signal. Both spans below carry the honorific; only one of
    them is about a person.
    """

    _, office = scan_text("श्री कार्यालयको निर्णय अनुसार")
    _, person = scan_text("श्री रामबहादुर थापाको निवेदन")

    assert office["shri_total"] == 1
    assert office["shri_not_institution"] == 0, (
        "an office was counted as a person; the INSTITUTION exclusion is not firing"
    )
    assert person["shri_total"] == 1
    assert person["shri_not_institution"] == 1


def test_the_institution_list_is_a_real_population() -> None:
    """Guard the exclusion list itself: an empty one would make the test above vacuous."""

    assert len(INSTITUTION) >= 30
    assert len(set(INSTITUTION)) == len(INSTITUTION), "duplicated institution words"
    assert all(word and not word.isascii() for word in INSTITUTION)


def test_a_bare_digit_run_is_not_a_labelled_identifier() -> None:
    """The label is what makes adjacent digits personal -- an amounts column is not PII."""

    high_precision, _ = scan_text("| रकम | १२३४५६७८९ |\n| जम्मा | ९८७६५४३२१ |")

    assert high_precision["citizenship"] == 0
    assert high_precision["pan"] == 0
    assert high_precision["bank_account"] == 0
    assert high_precision["date_of_birth"] == 0


def test_scanning_is_independent_of_prior_redaction() -> None:
    """A redacted document must not read as though it still carries the identifier."""

    high_precision, _ = scan_text("नागरिकता नं. [REDACTED:CITIZENSHIP-NO] हो")

    assert high_precision["citizenship"] == 0, (
        "the placeholder is being counted as a citizenship number, which would make the "
        "scan report personal data that has already been removed"
    )
