"""The `accounts` tier, and the rule-to-tier map consumers gate on.

`accounts` exists because `age` is the one rule whose precision is a property of the
corpus rather than of the pattern -- measured 28/28 wrong on the CIAA annual reports and
effectively all statutory on the Auditor General shelf. Dropping to `contact` to escape it
would publish 49 bank account numbers belonging to named spouses, a mother, a brother and
daughters caught in asset-seizure findings, so the two cannot be traded against each other.
"""

from __future__ import annotations

import pytest

from likhit.privacy import REMOVAL_TIERS, RedactionMode, redact_text
from likhit.privacy.contact import CONTACT_PATTERNS
from likhit.privacy.redact import INLINE_RULES

AGE = "निज ५८ वर्ष उमेर पूरा गरी अनिवार्य अवकाश भएको"
BANK = "निजको नाममा रहेको नेपाल बैंकको बचत खाता नं. १२३४५६७८९०१२ मा"
#: ⚠️ A passport value requires a 1-2 letter Latin prefix (``_PASSPORT_VALUE`` is
#: ``[A-Z]{1,2}[digits]{6,9}``), so a bare Devanagari digit run after `राहदानी नं.` is not
#: one. That is why this example is in the form the corpus actually carries.
PASSPORT = "Passport No. Z4362932 मा उल्लेख भएको"
CITIZENSHIP = "निजको ना.प्र.नं. ९८५४/०५२ को नागरिकता"


def test_accounts_sits_between_contact_and_strict() -> None:
    """The tier order is a rank, so a rule asks "does this mode reach me?" by position."""

    assert RedactionMode.CONTACT.rank < RedactionMode.ACCOUNTS.rank
    assert RedactionMode.ACCOUNTS.rank < RedactionMode.STRICT.rank


def test_accounts_removes_every_identifier_but_leaves_age() -> None:
    """🛑 The whole purpose of the tier, asserted on both halves at once.

    Either half alone would pass under a mode that simply did nothing.
    """

    assert (
        "[REDACTED:BANK-ACCOUNT-NO]"
        in redact_text(BANK, mode=RedactionMode.ACCOUNTS).text
    )
    assert (
        "[REDACTED:PASSPORT-NO]"
        in redact_text(PASSPORT, mode=RedactionMode.ACCOUNTS).text
    )
    assert (
        "[REDACTED:CITIZENSHIP-NO]"
        in redact_text(CITIZENSHIP, mode=RedactionMode.ACCOUNTS).text
    )

    assert redact_text(AGE, mode=RedactionMode.ACCOUNTS).text == AGE


def test_strict_still_removes_age() -> None:
    """⚠️ No-regression. `accounts` moves rules *below* `strict`; it must not empty it.

    On a charge sheet a defendant's age is ordinary personal data, so `strict` keeping age
    is what makes the new tier a choice rather than a deletion.
    """

    assert "[REDACTED:AGE]" in redact_text(AGE, mode=RedactionMode.STRICT).text


def test_contact_leaves_the_accounts_classes() -> None:
    """The gap `accounts` was introduced to close."""

    assert redact_text(BANK, mode=RedactionMode.CONTACT).text == BANK
    assert redact_text(PASSPORT, mode=RedactionMode.CONTACT).text == PASSPORT


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (RedactionMode.IDENTIFIERS, {"citizenship", "date_of_birth"}),
        (
            RedactionMode.CONTACT,
            {
                "citizenship",
                "date_of_birth",
                "mobile",
                "landline",
                "email",
                "phone_labelled",
            },
        ),
        (
            RedactionMode.ACCOUNTS,
            {
                "citizenship",
                "date_of_birth",
                "mobile",
                "landline",
                "email",
                "phone_labelled",
                "bank_account",
                "passport",
            },
        ),
        (
            RedactionMode.STRICT,
            {
                "citizenship",
                "date_of_birth",
                "mobile",
                "landline",
                "email",
                "phone_labelled",
                "bank_account",
                "passport",
                "age",
            },
        ),
    ],
)
def test_removal_tiers_answers_what_each_mode_removes(
    mode: RedactionMode, expected: set[str]
) -> None:
    """The question a publishing gate asks, pinned per tier.

    This is the assertion a downstream consumer would otherwise encode as its own
    hand-written copy of the map -- which is exactly the copy that cannot notice a rule
    moving between tiers.
    """

    assert {k for k, v in REMOVAL_TIERS.items() if mode.removes(v)} == expected


def test_removal_tiers_is_derived_from_the_rules_not_restated() -> None:
    """🛑 Catches the map DRIFTING from the rules, which is the failure that actually bites.

    ⚠️ It cannot catch a hand-copy that currently happens to agree -- measured: replacing
    the derivation with a correct literal dict leaves this green. That is not a hole to
    patch with a cleverer assertion; "copied but still correct" is indistinguishable from
    "derived" by any test at a single point in time. The *derivation* is the mechanism and
    this is the net under it: the moment a rule moves tier, a stale copy fails here.
    """

    for kind, _pattern, _marker, minimum in INLINE_RULES:
        assert kind in REMOVAL_TIERS
        assert REMOVAL_TIERS[kind].rank <= minimum.rank

    for kind, _pattern in CONTACT_PATTERNS:
        assert REMOVAL_TIERS[kind] is RedactionMode.CONTACT


def test_pan_is_absent_so_a_gate_excludes_it_without_its_own_exception_list() -> None:
    """⚠️ A PAN identifies a business, is public by design, and no tier removes it.

    A consumer computing "classes my tier should have zeroed" from this map must get a set
    that never contains `pan`, at every tier -- including the widest.
    """

    assert "pan" not in REMOVAL_TIERS
    for mode in RedactionMode:
        assert "pan" not in {k for k, v in REMOVAL_TIERS.items() if mode.removes(v)}


def test_none_removes_nothing() -> None:
    """`NONE` is a first-class answer, not an absent one -- see `modes.py`."""

    assert {
        k for k, v in REMOVAL_TIERS.items() if RedactionMode.NONE.removes(v)
    } == set()
    assert redact_text(BANK, mode=RedactionMode.NONE).text == BANK
