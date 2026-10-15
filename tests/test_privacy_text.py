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
PAN = "स्थायी लेखा नम्बर ३०१२४५६७८"
BANK = "खाता नं. ०१२३४५६७८९०१"
AGE = "निजको उमेर ४५ वर्ष"
SAMPLE = "\n".join([CITIZENSHIP, DOB, MOBILE, EMAIL])
WIDE_SAMPLE = "\n".join([CITIZENSHIP, DOB, MOBILE, EMAIL, PAN, BANK, AGE])


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


def test_strict_removes_everything_the_package_can_remove():
    result = redact_text(WIDE_SAMPLE, mode=RedactionMode.STRICT)
    for value in ("३०१२४५६७८", "०१२३४५६७८९०१", "४५", "९८५१२३४५६७"):
        assert value not in result.text
    assert "उमेर" in result.text, "the age label is kept; only the number goes"
    assert "खाता नं." in result.text


def test_contact_does_not_reach_pan_bank_or_age():
    """The tiers must be nested, not overlapping -- `contact` is below `strict`."""

    result = redact_text(WIDE_SAMPLE, mode=RedactionMode.CONTACT)
    assert "३०१२४५६७८" in result.text
    assert "०१२३४५६७८९०१" in result.text
    assert "उमेर ४५" in result.text


def test_an_alphanumeric_bank_account_is_removed():
    """🛑 `खाता नं. MM##########` is real, and the digits-only value guard missed it.

    A Latin branch/product prefix is a bank's convention, so missing it is systematic across
    every account that bank issues rather than a one-off.
    """

    result = redact_text("खाता नं. MM१२३४५६७८९०", mode=RedactionMode.STRICT)
    assert "MM" not in result.text
    assert "१२३४५६७८९०" not in result.text


def test_age_anchors_on_umer_and_not_on_varsha():
    """🛑 The measurement behind the age rule, as behaviour.

    A `वर्ष`-adjacent rule matches 413 spans over the CIAA corpus, mostly durations: a
    contract term, a deadline, years of service. Removing those deletes the financial
    evidence the corpus exists for.
    """

    durations = [
        "# वर्षको लागि रु ५,००,००० भुक्तानी",
        "प्रमाणपत्र जारी भएको ३ वर्षभित्र",
        "सेवा प्रवेश गरेको १२ वर्षमा",
    ]
    for line in durations:
        result = redact_text(line, mode=RedactionMode.STRICT)
        assert "[REDACTED:AGE]" not in result.text, f"clobbered a duration: {line}"

    # Both word orders of a real age ARE removed.
    for line in ("निजको उमेर ४५ वर्ष", "४५ वर्षको उमेरका व्यक्ति"):
        assert "[REDACTED:AGE]" in redact_text(line, mode=RedactionMode.STRICT).text


def test_no_mode_removes_a_name():
    """🛑 Pinned as a promise, not an omission.

    `strict` is the widest tier and leaves every name in place. Nothing detects a Nepali name
    reliably -- `श्री` fires 2,179 times across the CIAA corpus on a signal that cannot tell
    an office from a person -- and for an accountability corpus, removing the names of
    prosecuted officials would invert the product.
    """

    named = "श्री राम बहादुर थापा र श्री सीता देवी पौडेल"
    for mode in RedactionMode:
        assert "राम बहादुर थापा" in redact_text(named, mode=mode).text
        assert "सीता देवी पौडेल" in redact_text(named, mode=mode).text


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


def test_save_cli_writes_nothing_if_redaction_raises(tmp_path, monkeypatch):
    """🛑 Fail-closed, which is what makes redact-by-default more than a label.

    Falling through to write the unredacted text a failed pass was asked to clean is the one
    outcome worse than not converting the document at all. Asserted here rather than left to
    `save_cli`'s docstring, because it is a property of the control flow and a later
    `try/except` around the conversion loop would silently remove it.
    """

    import fitz

    from likhit import save_cli

    # A one-page PDF, not `samples/aarop-patra.pdf`: converting that one takes ~20 s, and
    # this test is about control flow after conversion, not about conversion.
    source = tmp_path / "tiny.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "hello")
    doc.save(str(source))
    doc.close()

    def boom(text, *, mode):
        raise RuntimeError("simulated redaction failure")

    monkeypatch.setattr(save_cli, "redact_text", boom)
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="simulated redaction failure"):
        save_cli.main([str(source), "--out-dir", str(out)])
    assert not out.exists() or not list(out.iterdir())


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
