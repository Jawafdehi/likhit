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


#: Page 1 of `samples/aarop-patra.pdf`, the line that carried an Indian national's details in
#: Latin script inside Nepali prose. Every English-labelled value here survived `strict`
#: before the label alternations carried their English forms.
BILINGUAL_LINE = (
    "सम्पर्क नं.९८५१३३९९९९ का भारतीय नागरीक Deepak Anand "
    "(Passport No. Z4362932/India र K9838300/India, "
    "Date of Birth: 27 August 1979)  Mobile Number: 919971766006"
)


def test_english_labelled_values_are_removed_at_strict():
    """🛑 The regression this exists to prevent is a *correlated* miss.

    Nepali-only labels do not fail at random -- they fail on exactly the people whose details
    were written in English, which in this corpus means foreign nationals. On this real line
    `strict` removed 1 of 5 values and reported success.
    """

    result = redact_text(BILINGUAL_LINE, mode=RedactionMode.STRICT)

    for value in ("Z4362932", "K9838300", "27 August 1979", "919971766006"):
        assert value not in result.text, f"{value} survived the widest tier"
    assert "९८५१३३९९९९" not in result.text, "the Nepali-labelled mobile still goes"
    # The labels themselves are evidence and are kept -- a transcript saying a passport
    # number was removed is a record; one with the field name gone is a hole.
    for label in ("Passport No.", "Date of Birth:", "Mobile Number:"):
        assert label in result.text
    # The issuing country is research data, not an identifier.
    assert result.text.count("/India") == 2


def test_both_passports_go_when_one_label_covers_two():
    """🛑 The second value in a list is the miss that looks most like a success.

    `Passport No. A र B` labels only the first. Taking it and leaving the second puts a
    visible redaction on the page beside the identifier it did not remove.
    """

    result = redact_text(BILINGUAL_LINE, mode=RedactionMode.STRICT)
    assert result.text.count("[REDACTED:PASSPORT-NO]") == 2


def test_the_passport_connector_is_not_a_label_on_its_own():
    """⚠️ `र` is simply "and". It must never reach a passport-shaped token by itself, or
    every cheque and voucher reference on the page becomes a redaction target."""

    unlabelled = "बिल नं. मा उल्लेख भएको र AB1234567 बमोजिम"
    assert "AB1234567" in redact_text(unlabelled, mode=RedactionMode.STRICT).text


def test_english_labels_do_not_widen_what_counts_as_a_value():
    """English widens the *label* alternation only; the three guards are untouched.

    Each case below is refused by a guard, reached through an English label -- so the guard
    is doing the work, not the label.
    """

    # Guard 2, age floor: a single digit beside `उमेर`/`Age` is a count in a welfare table
    # far more often than a person being identified.
    assert "7" in redact_text("Age 7", mode=RedactionMode.STRICT).text
    # Guard 2, date floor: four digits is a year, not a date of birth.
    assert "1979" in redact_text("Date of Birth: 1979", mode=RedactionMode.STRICT).text
    # Guard 2, passport digit bound: too short to be a passport number.
    assert "Z412" in redact_text("Passport No. Z412", mode=RedactionMode.STRICT).text
    # Guard 3, amount cue: the window ran into a money column.
    assert "रु" in redact_text("Age रु ४५", mode=RedactionMode.STRICT).text


def test_passport_is_outside_the_adjudicated_identifiers_scope():
    """🛑 `identifiers` means "the scope I was authorised for". A caller who picks it must
    not silently get a third class of removal -- see `redact.INLINE_RULES`."""

    line = "Passport No. Z4362932"
    assert "Z4362832" not in redact_text(line, mode=RedactionMode.STRICT).text
    assert "Z4362932" in redact_text(line, mode=RedactionMode.IDENTIFIERS).text
    assert "Z4362932" in redact_text(line, mode=RedactionMode.CONTACT).text


def test_an_international_phone_is_removed_at_contact():
    """`signals.MOBILE` anchors on a Nepali 97/98 opening, so a foreign number in an
    international format is invisible to it. The label is what finds this one."""

    line = "Mobile Number: 919971766006"
    assert "919971766006" not in redact_text(line, mode=RedactionMode.CONTACT).text
    assert "919971766006" in redact_text(line, mode=RedactionMode.IDENTIFIERS).text
    assert "Mobile Number:" in redact_text(line, mode=RedactionMode.CONTACT).text


def test_a_labelled_phone_does_not_double_count_a_nepali_mobile():
    """⚠️ `सम्पर्क ९८५१२३४५६७` matches the labelled rule AND `MOBILE` over the same digits.

    The shape pattern wins and the overlap guard drops the duplicate, so one value is one
    removal -- the journal is how this pass's precision is measured.
    """

    result = redact_text("सम्पर्क ९८५१२३४५६७", mode=RedactionMode.CONTACT)
    assert result.values_removed == 1
    assert result.text.count("[REDACTED:PHONE-NO]") == 1


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


def test_help_advertises_the_values_the_parser_accepts():
    """🛑 `--help` listed `{RedactionMode.NONE,...}` -- which are exactly the strings the
    parser REJECTS -- while never showing the ones that work.

    A `str`-valued `Enum` inherits `Enum.__str__`, and argparse renders both its choices list
    and its "invalid choice" error through `str()`.
    """

    help_text = build_parser().format_help()
    for mode in RedactionMode:
        assert f"RedactionMode.{mode.name}" not in help_text
    assert "none" in help_text and "identifiers" in help_text

    # Every advertised value must actually parse.
    for mode in RedactionMode:
        assert (
            build_parser().parse_args(["in.pdf", "--redact", str(mode)]).redact is mode
        )


def test_the_save_cli_help_describes_the_actual_default():
    """The help text named `identifiers`' scope while the default was `strict`, so it
    understated what the command removes -- the one direction a privacy default must not be
    wrong in."""

    help_text = build_parser().format_help()
    assert str(DEFAULT_MODE) in help_text
    assert "passport" in help_text


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
