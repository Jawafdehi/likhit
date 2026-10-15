"""`redact_text`, and the default that decides what a forgetful caller gets."""

from __future__ import annotations

import pytest

from likhit.privacy import RedactionMode, contains_placeholder, redact_text, scan_text
from likhit.privacy.modes import DEFAULT_MODE
from likhit.save_cli import build_parser

CITIZENSHIP = "नागरिकता नं. १२-३४-५६-७८९०१"
DOB = "जन्म मिति २०४५।०३।१२"
MOBILE = "सम्पर्क ९८५१२३४५६७"
EMAIL = "इमेल ram.bahadur@example.com"
SAMPLE = "\n".join([CITIZENSHIP, DOB, MOBILE, EMAIL])


def test_the_default_removes_something():
    """🛑 The whole point of the default. If this ever returns the input, the default is a lie."""

    result = redact_text(SAMPLE)
    assert result.mode is DEFAULT_MODE
    assert result.changed
    assert contains_placeholder(result.text)


def test_the_default_is_not_none():
    """Pinned separately from the test above, because `DEFAULT_MODE` is one edit away from
    silently turning every caller's redaction off while every other test still passes."""

    assert DEFAULT_MODE is not RedactionMode.NONE


def test_the_default_removes_contact_details():
    """🛑 What the default is FOR, pinned as behaviour rather than as a constant.

    `IDENTIFIERS` alone removes 0.3% of the personal data detected in the charge-sheet
    corpus, because that corpus is phone numbers rather than citizenship numbers. A default
    that leaves the largest category is a label, not a safeguard. If someone narrows
    `DEFAULT_MODE`, this is the test that should stop them -- asserting on the enum value
    would not, because narrowing it is exactly the edit that would update that assertion too.
    """

    result = redact_text(SAMPLE)
    assert "९८५१२३४५६७" not in result.text
    assert "ram.bahadur@example.com" not in result.text
    assert not sum(scan_text(result.text)[0].values())


def test_mode_none_returns_the_input_unchanged():
    result = redact_text(SAMPLE, mode=RedactionMode.NONE)
    assert result.text == SAMPLE
    assert not result.changed
    assert not contains_placeholder(result.text)


def test_identifiers_mode_removes_identifiers_and_keeps_contact():
    result = redact_text(SAMPLE, mode=RedactionMode.IDENTIFIERS)
    assert "१२-३४-५६-७८९०१" not in result.text
    assert "२०४५।०३।१२" not in result.text
    assert "नागरिकता" in result.text, "the label survives; only the value goes"
    # Promoting contact details is a scope change -- the default tier must not make it.
    assert "९८५१२३४५६७" in result.text
    assert "ram.bahadur@example.com" in result.text


def test_contact_mode_clears_the_scanner():
    result = redact_text(SAMPLE, mode=RedactionMode.CONTACT)
    assert not sum(scan_text(result.text)[0].values())
    assert "९८५१२३४५६७" not in result.text
    assert "ram.bahadur@example.com" not in result.text


def test_contact_replacements_do_not_corrupt_each_other():
    """🛑 Two contact values on one line, applied right-to-left.

    Rewriting left-to-right invalidates every offset after the first replacement, which
    splices a placeholder into the middle of the next value instead of replacing it.
    """

    # Each is exactly 10 digits, which is what `signals.MOBILE` requires -- an earlier
    # version of this test used 11-digit values, matched nothing, and asserted against an
    # unredacted string while looking like it tested the ordering.
    numbers = ("९८५१२३४५६७", "९८४१२३४५६७", "९८६१२३४५६७")
    assert all(len(number) == 10 for number in numbers)
    text = f"क {numbers[0]} ख {numbers[1]} ग {numbers[2]}"
    result = redact_text(text, mode=RedactionMode.CONTACT)
    assert result.text.count("[REDACTED:PHONE-NO]") == 3
    assert not any(char.isdigit() for char in result.text.replace("REDACTED", ""))
    for marker in ("क", "ख", "ग"):
        assert marker in result.text


def test_journal_records_shapes_and_never_a_value():
    result = redact_text(SAMPLE, mode=RedactionMode.CONTACT)
    assert result.values_removed == len(result.journal)
    blob = str(result.journal)
    for value in ("१२-३४-५६-७८९०१", "९८५१२३४५६७", "ram.bahadur@example.com"):
        assert value not in blob


@pytest.mark.parametrize("mode", list(RedactionMode))
def test_every_mode_is_reachable_from_the_save_cli(mode):
    args = build_parser().parse_args(["in.pdf", "--redact", mode.value])
    assert args.redact is mode


def test_save_cli_redacts_unless_told_otherwise():
    """The behavioural claim in `save_cli`'s docstring, pinned.

    `likhit-save report.pdf` with no flags must not be capable of writing unredacted text.
    """

    assert build_parser().parse_args(["in.pdf"]).redact is DEFAULT_MODE
    assert build_parser().parse_args(["in.pdf"]).redact is not RedactionMode.NONE
    assert (
        build_parser().parse_args(["in.pdf", "--redact", "none"]).redact
        is RedactionMode.NONE
    )
