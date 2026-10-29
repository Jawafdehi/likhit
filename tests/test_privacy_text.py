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


def test_strict_removes_every_personal_detail_it_can():
    result = redact_text(WIDE_SAMPLE, mode=RedactionMode.STRICT)
    for value in ("०१२३४५६७८९०१", "९८५१२३४५६७"):
        assert value not in result.text
    # ⚠️ Asserted against the label, not the bare digits: `४५` is also a substring of the
    # PAN, which `strict` deliberately keeps, so `"४५" not in text` would fail for a
    # reason that has nothing to do with the age.
    assert "उमेर ४५" not in result.text
    assert "[REDACTED:AGE]" in result.text
    assert "उमेर" in result.text, "the age label is kept; only the number goes"
    assert "खाता नं." in result.text


def test_no_tier_removes_a_pan():
    """🛑 Pinned as a promise, not an omission -- the one identifier `strict` leaves.

    A PAN identifies a *business* and is public by design: printed on invoices, filed with
    the registrar, searchable. It is frequently the only handle tying a procurement finding
    to a registered contractor, so removing it protects nobody and costs the reader the
    check. The line the tiers draw is between data about a person and data about a company.
    """

    for mode in RedactionMode:
        result = redact_text(WIDE_SAMPLE, mode=mode)
        assert "३०१२४५६७८" in result.text, f"{mode} removed a PAN"
        assert "[REDACTED:PAN-NO]" not in result.text

    # Still *disclosed* -- a corpus report must say how many are present.
    assert scan_text(PAN)[0]["pan"] == 1


def test_contact_does_not_reach_bank_or_age():
    """The tiers must be nested, not overlapping -- `contact` is below `strict`."""

    result = redact_text(WIDE_SAMPLE, mode=RedactionMode.CONTACT)
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
    # Guard 2, date floor: three digits is not a year.
    #
    # ⚠️ This case used to read `Date of Birth: 1979` and assert the year SURVIVED, on the
    # reasoning that four digits is a year rather than a date of birth. That reading was
    # reversed on 28 October 2026 and the year is now removed -- see
    # `test_privacy_label_gap.py`, where the measurement is: a labelled birth year was 100
    # of the 102 date-of-birth values the platform's gate found still sitting in text this
    # package had already redacted. The scanner counts one, so the remover has to take it
    # or the two disagree forever.
    assert "197" in redact_text("Date of Birth: 197", mode=RedactionMode.STRICT).text
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


def test_a_labelled_phone_value_stops_at_the_number():
    """🛑 The rule added to prevent a disclosure nearly caused the opposite one.

    A value window letting `[\\s-]` separate any two digits runs out of the number and into
    the next figure on the line: `फोन नं.: ९८४१२३४५६७ २०७५` matched fourteen digits as one
    "phone number" and deleted the year. That is this project's 11,215-clobbered-values
    history reproduced exactly.

    ⚠️ A Nepali mobile hides it -- `MOBILE` matches first and the overlap guard drops the
    over-captured span -- so every case here uses a number `MOBILE` cannot see, which is
    precisely the set this rule exists for.
    """

    for text, kept in (
        ("Phone: 1234567 2075", "2075"),
        ("फोन नं.: ५२६२२४७ २०७५", "२०७५"),
        ("Tel: 01-4123456 2075", "2075"),
    ):
        result = redact_text(text, mode=RedactionMode.CONTACT)
        assert kept in result.text, f"{text!r} swallowed the figure after the number"
        assert result.text.count("[REDACTED:PHONE-NO]") == 1

    # Both figures after the number survive, not just the first.
    assert redact_text("Phone: 1234567 2075 2076", mode=RedactionMode.CONTACT).text == (
        "Phone: [REDACTED:PHONE-NO] 2075 2076"
    )
    # Two short runs under a label are not a phone number at all.
    assert (
        "2075 2076" in redact_text("Phone: 2075 2076", mode=RedactionMode.CONTACT).text
    )


def test_the_shapes_the_labelled_phone_must_still_reach():
    """The constraint above must not cost the numbers the rule was added for."""

    for text in (
        "Mobile Number: 919971766006",  # the Indian number from page 1
        "Phone: +91 9971766006",  # country code separated by a space
        "Tel: 9851-234567",  # hyphenated
        "फोन नं.: ५२६२२४७",  # a bare Kathmandu landline
    ):
        result = redact_text(text, mode=RedactionMode.CONTACT)
        assert result.values_removed == 1, text
        assert not any(ch.isdigit() for ch in result.text.replace("REDACTED", "")), text


@pytest.mark.parametrize("count", [1, 2, 3, 4, 6])
def test_a_passport_list_has_no_length_cap(count):
    """🛑 One capture group per list position has a cap, and overflowing it is SILENT.

    With `val`/`val2`/`val3` a fourth passport under the same label was never captured, so
    no counter, journal row or verification pass could see it -- the page carried three
    visible redactions beside one live identifier, which is this module's worst failure
    mode. The span is matched as a list and split, so there is no edge to fall off.
    """

    numbers = [f"Z{4362930 + index}" for index in range(count)]
    text = "Passport No. " + " र ".join(f"{number}/India" for number in numbers)

    result = redact_text(text, mode=RedactionMode.STRICT)

    assert result.values_removed == count
    for number in numbers:
        assert number not in result.text
    # The issuing country is research data and survives each one.
    assert result.text.count("/India") == count


def test_a_label_binds_across_at_most_one_line_break():
    """🛑 A bare `\\s*` gap binds a label to a number any distance away through whitespace.

    In text extracted from a PDF that reaches across blank lines into the next block, and
    `फोन नं.:\\n\\n\\n२०७५०१२३` redacted a *date* as a phone number. One break has to be
    allowed, because the real case needs it: the CIAA masthead puts the number on the
    following line.
    """

    assert (
        "२०७५०१२३"
        in redact_text("फोन नं.:\n\n\n२०७५०१२३", mode=RedactionMode.CONTACT).text
    )
    assert redact_text("फोन नं.:    \n५२६२२४७", mode=RedactionMode.CONTACT).changed


def test_blanking_a_placeholder_does_not_move_the_text_around_it():
    """⚠️ Every labelled scanner pattern is distance-bounded, so collapsing a 25-character
    marker to one space pulls what follows 24 characters closer and can invent a hit.

    Measured: it put `date_of_birth` at 11 over the CIAA transcripts where the unredacted
    text supports 10.
    """

    from likhit.privacy.placeholders import CITIZENSHIP

    marker_len = len(CITIZENSHIP)
    # A digit run just outside the citizenship scanner's 40-character window stays outside.
    filler = "क" * (41 - marker_len)
    text = f"नागरिकता {CITIZENSHIP}{filler} २०७५"
    assert scan_text(text)[0]["citizenship"] == 0


def test_a_label_glued_to_a_longer_word_is_not_a_label():
    """🛑 Two scripts, two guards -- and `\\b` silently does nothing for one of them.

    Python's `\\w` follows `str.isalnum()`, and a Devanagari vowel sign is category `Mn`, so
    `\\b` *succeeds* between `को` and `सम्पर्क`. `कोसम्पर्क १२३४५६७` kept matching with a word
    boundary in place; only the script-range lookbehind holds there.
    """

    for text in (
        "Himobile 1234567",
        "Nepal Telecom 1234567",
        "Parcel 1234567",
        "कोसम्पर्क १२३४५६७",
    ):
        assert not redact_text(text, mode=RedactionMode.CONTACT).changed, text

    # The same labels, standing as words, still work in both scripts.
    for text in ("Mobile 1234567", "सम्पर्क १२३४५६७", "को सम्पर्क ९८५१२३४५६७"):
        assert redact_text(text, mode=RedactionMode.CONTACT).changed, text


def test_no_contact_pattern_matches_inside_a_placeholder():
    """🛑 The contact pass runs LAST, on text the inline and table passes have already
    rewritten, so it reads their placeholders.

    `phone_labelled`'s label alternation genuinely occurs inside `[REDACTED:PHONE-NO]`; what
    stops it is that a value must open with a digit and the next character is `-`. That is a
    property of two patterns agreeing, not a guarantee, so it is pinned: a contact target
    landing inside a placeholder would splice a replacement into another one and produce a
    corrupted marker rather than a redaction.
    """

    from likhit.privacy.contact import iter_contact_targets
    from likhit.privacy.placeholders import ALL

    for marker in ALL:
        for tail in ("", " ९८४१२३४५६७", "1234567", " 1234567"):
            text = marker + tail
            inside = [t for t in iter_contact_targets(text) if t.start < len(marker)]
            assert not inside, f"{marker!r} + {tail!r} matched at {inside}"


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
