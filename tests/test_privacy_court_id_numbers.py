"""Identity numbers a Kathmandu District Court charge sheet and two verdicts kept at ``strict``.

Found on JAW-73 (a charge sheet) and JAW-74 (two verdicts): every shape below was still in
the text after ``strict`` AND unreported by the scanner, so the publishing gate's residue
check passed text that carried them. Each test states the shape it was found in; **every
value here is synthetic** -- the documents themselves name private individuals.

Each positive case is checked twice: the remover takes the value, and the scanner (the
residue check) reports it before redaction and reports nothing after. The negatives are the
numbers these documents are read for -- cheque numbers, amounts, case numbers, dates -- and
must come through untouched.
"""

from __future__ import annotations

import pytest

from likhit.privacy import (
    RedactionMode,
    iter_contact_targets,
    iter_inline_targets,
    placeholders,
    redact_text,
    scan_text,
)


def _strict(text: str) -> tuple[str, list[str]]:
    result = redact_text(text, mode=RedactionMode.STRICT)
    return result.text, [row["kind"] for row in result.journal]


def _detected(text: str, kind: str) -> int:
    return scan_text(text)[0][kind]


# (span, value that must go, kind removed, placeholder)
_POSITIVES = (
    # --- citizenship: the charge-sheet font puts a stray repha in the label --------------
    ("सीता शर्मा (ना.प्रर्.नं.१२/३४५६७) ........", "१२/३४५६७", "citizenship"),
    ("राम श्रेष्ठ (ना.प्रर्.नं.४५६)...........", "४५६", "citizenship"),
    ("कृष्ण साह (ना.प्रर्.नं.७८९/१२३).........", "७८९/१२३", "citizenship"),
    ("हरि तिवारी (ना.प्रर्.नं.२७०१-०४५-०१२३४) ....", "२७०१-०४५-०१२३४", "citizenship"),
    ("गोपाल शाही (ना.प्रर्.न.९८७६)......", "९८७६", "citizenship"),
    # the repha moved in front of the marker
    ("सीता थापा (ना.प्र.र्नं. ५४३२)", "५४३२", "citizenship"),
    # clean label, three-digit serial -- missed even without any font artefact
    ("राम कार्की (ना.प्र.नं.१९७) र", "१९७", "citizenship"),
    # no anusvara on the marker (the verdicts)
    ("हरि राईको नाममा रहेको  ना.प्र.न.१२३४/५६७८  भएको", "१२३४/५६७८", "citizenship"),
    ("श्याम गिरी (ना.प्र.न.१२३४५६-७८९) आगे", "१२३४५६-७८९", "citizenship"),
    # a two-column table wrapped the label onto one row and the value onto the next
    ("सीता शर्मा (ना.प्रर्.नं. |  |\n| १२/३४५६७) ......", "१२/३४५६७", "citizenship"),
    ("दीपक प्रधान (ना.प्रर्.नं. |  |\n|  | ५६७८९)......", "५६७८९", "citizenship"),
    # no label at all: the sentence after the number says it is a citizenship certificate
    (
        "कार्यालय पर्सावाट  जारी  भएको  १२३४५६/७८९\n      नम्वरको राम झाको नाममा जारी"
        " भएको नागरिकताको प्रतिलिपि",
        "१२३४५६/७८९",
        "citizenship",
    ),
    # --- Indian voter ID (EPIC) and Aadhaar -----------------------------------------------
    (
        "विकास सिंह\n(भारतिय निवार्चन आयोग कार्ड नं. ABC1234567)....",
        "ABC1234567",
        "voter_id",
    ),
    ("Voter ID No.: XYZ7654321 issued", "XYZ7654321", "voter_id"),
    # the same label as the PDF's own text layer carries it (what `redact_pdf` reads)
    ("(भारमतय मर्नवािार्न आयोग काडा र्नं. ABC1234567)....", "ABC1234567", "voter_id"),
    (
        "अमित शर्मा (भारतिय\nआधार कार्ड नं. 1234 5678 9012)....",
        "1234 5678 9012",
        "aadhaar",
    ),
    (
        "राहुल पाण्डेय (भारमतय आधार काडा र्नं. 1234 56789012)....",
        "1234 56789012",
        "aadhaar",
    ),
    ("Aadhaar No. 2345-6789-0123", "2345-6789-0123", "aadhaar"),
    # --- refugee identity card ------------------------------------------------------------
    ("भुटानी शरणार्थी परिचय पत्र द.नं. १२३-४५६७८९०१ भएको", "१२३-४५६७८९०१", "refugee_id"),
    (
        "|        |राम राई  |हरि राई   |-              |शरणार्थी परिचय पत्र  |\n"
        "|        |         |          |(२)            |द.नं. ४५६-१२३४५६७८  |",
        "४५६-१२३४५६७८",
        "refugee_id",
    ),
    # --- IMEI -------------------------------------------------------------------------------
    ("दायाँ खल्तीमा   IMEI\n      No.356789012345678 भएको", "356789012345678", "imei"),
    ("मोडलको IMEI No.(Slot-1):-351234567890123 र", "351234567890123", "imei"),
    # --- bank accounts ------------------------------------------------------------------------
    (
        "प्रभु बैकको खाता न.12345678901234567890  को  चेक",
        "12345678901234567890",
        "bank_account",
    ),
    (
        "कुमारी बैंक लि.को 1234567890123456 न.को खातामा पटक-पटक",
        "1234567890123456",
        "bank_account",
    ),
    ("नेपाल ईनभेष्टमेन्ट बैक\n लि.01234567890123 तथा", "01234567890123", "bank_account"),
    (
        "बिकास बैकमा रहेको बैंक खातामा\n 12345678901234567890 मा मोबाईल बैकिङ",
        "12345678901234567890",
        "bank_account",
    ),
    (
        "गीता लिम्बुको\n 9876543210123456 नं. मा ट्रान्सफर गरी",
        "9876543210123456",
        "bank_account",
    ),
    # --- mobile numbers that MOBILE's shape refused -------------------------------------------
    # a contact name ending in a digit, glued to the number (WhatsApp exports)
    ("Whatsapp Account मा HARI KC29841234567 सँग", "9841234567", "mobile"),
    ("Ram बाट  HARIKC29841234567  न.मा  पटक", "9841234567", "mobile"),
    # one digit too many, identified by the word after it
    ("घर  हुने\n      98412345678 न.को मोवाईल प्रयोग गर्ने", "98412345678", "mobile"),
)


@pytest.mark.parametrize(("span", "value", "kind"), _POSITIVES)
def test_the_value_is_removed_and_the_label_kept(
    span: str, value: str, kind: str
) -> None:
    redacted, kinds = _strict(span)

    assert value not in redacted
    assert kind in kinds


@pytest.mark.parametrize(("span", "value", "kind"), _POSITIVES)
def test_the_residue_check_sees_it_before_and_not_after(
    span: str, value: str, kind: str
) -> None:
    # The scanner is what the platform's publishing gate asks; a value it cannot see is a
    # value the gate lets through.
    assert _detected(span, kind) >= 1
    redacted, _ = _strict(span)
    residue, _ = scan_text(redacted)
    assert residue[kind] == 0


@pytest.mark.parametrize(("span", "value", "kind"), _POSITIVES)
def test_redaction_is_idempotent(span: str, value: str, kind: str) -> None:
    once, _ = _strict(span)
    twice, kinds = _strict(once)
    assert twice == once
    assert kinds == []


@pytest.mark.parametrize(("span", "value", "kind"), _POSITIVES)
def test_span_targets_agree_with_the_text_pass(
    span: str, value: str, kind: str
) -> None:
    """The PDF redactor works from spans, not from the rewritten text."""

    spans = [
        span[t.start : t.end]
        for t in (
            *iter_inline_targets(span, mode=RedactionMode.STRICT),
            *iter_contact_targets(span),
        )
    ]
    assert value in spans


def test_the_label_and_names_survive() -> None:
    redacted, _ = _strict("राम श्रेष्ठ (ना.प्रर्.नं.४५६)")
    assert redacted == f"राम श्रेष्ठ (ना.प्रर्.नं.{placeholders.CITIZENSHIP})"


def test_both_imeis_of_a_dual_sim_phone_go() -> None:
    span = "IME नम्बर 351234567890123/ 351234567890124 भएको"
    redacted, kinds = _strict(span)
    assert "3512345678901" not in redacted
    assert kinds.count("imei") == 2
    assert redacted.count(placeholders.IMEI) == 2


def test_an_account_reached_by_two_rules_is_one_value() -> None:
    """Bank name before it AND `न.को खाता` after it: one account, counted and targeted once."""

    span = "कुमारी बैंक लि.को 1234567890123456 न.को खातामा पटक"
    assert _detected(span, "bank_account") == 1
    targets = list(iter_inline_targets(span, mode=RedactionMode.STRICT))
    assert len(targets) == 1
    _, kinds = _strict(span)
    assert kinds == ["bank_account"]


def test_the_name_digit_is_kept_with_the_name() -> None:
    redacted, _ = _strict("HARI KC29841234567 सँग")
    assert redacted == f"HARI KC2{placeholders.PHONE} सँग"


# What must come through untouched: the numbers these documents are read for.
_NEGATIVES = (
    # cheque numbers, amounts, case numbers and dates
    "SA को चेक नं.0012345678 बाट मिति २०८०/०१/०२ गते  रु.५,००,०००/-",
    "ग्लोबल आईएमई बैंकको चेक नं. ००१२३४५६७८ बाट रु.१०,००,०००।–",
    "Global IME Bank को चेक नं. 001234567890123",
    "नबिल बैंकमा रु.२५,००,०००।– जम्मा",
    "बैंक लि. 123456789 रुपैयाँ जम्मा गरेको",
    # the integer part of a decimal or grouped amount after a bank name
    "बैंक लि. 123456789.00 रुपैयाँ जम्मा गरेको",
    "बैंकमा 1234567890,50 जम्मा",
    "मुद्दा नं. ०७९-C1-२२७१ को फैसला मिति २०८०।०५।१२",
    "पत्र द.नं. ४५६-२०७९ च.नं. १२३४",
    "प्रतिवादीहरु ९८४१२३४५६७८ रकम",
    # `आधार` alone is "basis"
    "के कुन आधारमा १२३४ ५६७८ ९०१२ रकम माग गरेको",
    # a bare EPIC shape without its label
    "फाइल नं. ABC1234567 को",
    # the spelled-out label keeps its four-digit floor
    "नागरिकता नं. १२३ जना",
    # `जारी भएको … नम्बरको` without the citizenship close
    "कार्यालयबाट जारी भएको १२३४/५६७ नम्बरको पत्र बमोजिम",
    # a 15-digit run with no IMEI label
    "रकम 351234567890123 मा",
)


@pytest.mark.parametrize("span", _NEGATIVES)
def test_non_identifiers_are_left_alone(span: str) -> None:
    redacted, kinds = _strict(span)
    assert redacted == span
    assert kinds == []


@pytest.mark.parametrize("span", _NEGATIVES)
def test_non_identifiers_are_not_counted_as_residue(span: str) -> None:
    hp, _ = scan_text(span)
    for kind in ("citizenship", "voter_id", "aadhaar", "refugee_id", "imei", "mobile"):
        assert hp[kind] == 0, kind


def test_the_new_kinds_are_strict_only() -> None:
    span = "ABC निवार्चन आयोग कार्ड नं. ABC1234567, IMEI No.351234567890123"
    redacted = redact_text(span, mode=RedactionMode.IDENTIFIERS).text
    assert redacted == span
