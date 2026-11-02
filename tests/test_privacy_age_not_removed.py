"""`age` is removed by no tier, and `REMOVAL_TIERS` is what consumers gate on.

An age is an attribute rather than an identifier, and in an accountability corpus it is
overwhelmingly the *subject* of a finding rather than an incidental detail about a person.
Counted against every corpus available it was wrong in essentially every instance and
right in none -- 36 removals on the CIAA annual reports, 76 on the Auditor General shelf,
34 on the charge sheets, dominated by statutory thresholds and the age bars whose breach
was the alleged offence. See `likhit.privacy.redact.INLINE_RULES` for the breakdown.
"""

from __future__ import annotations

import pytest

from likhit.privacy import (
    REMOVAL_TIERS,
    RedactionMode,
    placeholders,
    redact_text,
    strip_placeholders,
)
from likhit.privacy.contact import CONTACT_PATTERNS
from likhit.privacy.redact import INLINE_RULES

#: Quoted from the corpora, one per failure mode the measurement found.
STATUTORY = "सदस्यको उमेर ६५ वर्ष पूरा भएमा स्वतः अवकाश हुनेछ"
THE_FINDING = "शिक्षा सम्बन्धी कानुन विपरीत ४५ वर्ष उमेर भैसकेका डम्मरबहादुर देवकोटालाई नियुक्त"
DEPOSITION = "मेरो उमेर ३८ वर्ष, पेसा व्यापार र ठेगाना जिल्ला ललितपुर हो"

BANK = "निजको नाममा रहेको नेपाल बैंकको बचत खाता नं. १२३४५६७८९०१२ मा"
CITIZENSHIP = "निजको ना.प्र.नं. ९८५४/०५२ को नागरिकता"


@pytest.mark.parametrize("mode", list(RedactionMode))
@pytest.mark.parametrize("text", [STATUTORY, THE_FINDING, DEPOSITION])
def test_no_tier_removes_an_age(mode: RedactionMode, text: str) -> None:
    """🛑 Asserted across **every** tier, including the widest.

    Pinning only `strict` would let a later edit reintroduce the rule at a narrower tier
    and still pass, which is the same shape as the bug being fixed: a rule nobody measured
    sitting inside a tier everybody uses.
    """

    assert redact_text(text, mode=mode).text == text


def test_the_tiers_still_remove_what_they_are_for() -> None:
    """⚠️ No-regression. Dropping a rule must not be indistinguishable from dropping all."""

    assert (
        "[REDACTED:CITIZENSHIP-NO]"
        in redact_text(CITIZENSHIP, mode=RedactionMode.IDENTIFIERS).text
    )
    assert (
        "[REDACTED:BANK-ACCOUNT-NO]"
        in redact_text(BANK, mode=RedactionMode.STRICT).text
    )


def test_age_is_absent_from_the_removal_map() -> None:
    """So a consumer's "classes my tier should have zeroed" never contains it."""

    assert "age" not in REMOVAL_TIERS
    for mode in RedactionMode:
        assert "age" not in {k for k, v in REMOVAL_TIERS.items() if mode.removes(v)}


def test_the_age_placeholder_stays_registered() -> None:
    """🛑 Text redacted by an earlier pass must still be recognised.

    The marker is in the published corpus already. Unregistering it would make
    `strip_placeholders` leave `[REDACTED:AGE]` in text handed to the quality scorer, which
    would then score the marker as a decode defect -- the exact failure
    `likhit.privacy.placeholders` exists to prevent, and why `PAN` stays registered too.
    """

    assert placeholders.AGE in placeholders.ALL
    # The claim is that the marker is recognised and taken out, not how the gap is spaced.
    stripped = strip_placeholders("निज [REDACTED:AGE] वर्षको")
    assert placeholders.AGE not in stripped
    assert "निज" in stripped and "वर्षको" in stripped


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (RedactionMode.NONE, set()),
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
            },
        ),
    ],
)
def test_removal_tiers_answers_what_each_mode_removes(
    mode: RedactionMode, expected: set[str]
) -> None:
    """The question a publishing gate asks, pinned per tier."""

    assert {k for k, v in REMOVAL_TIERS.items() if mode.removes(v)} == expected


def test_removal_tiers_is_derived_from_the_rules_not_restated() -> None:
    """🛑 Catches the map DRIFTING from the rules, which is the failure that bites.

    ⚠️ It cannot catch a hand-copy that currently happens to agree -- measured: replacing
    the derivation with a correct literal dict leaves this green. "Copied but still
    correct" is indistinguishable from "derived" at a single point in time. The derivation
    is the mechanism; this is the net that catches the copy the moment a rule moves.
    """

    for kind, _pattern, _marker, minimum in INLINE_RULES:
        assert kind in REMOVAL_TIERS
        assert REMOVAL_TIERS[kind].rank <= minimum.rank

    for kind, _pattern in CONTACT_PATTERNS:
        assert REMOVAL_TIERS[kind] is RedactionMode.CONTACT


def test_pan_is_absent_too() -> None:
    """A PAN identifies a business, is public by design, and no tier removes it."""

    assert "pan" not in REMOVAL_TIERS
