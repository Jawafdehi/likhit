"""Identity-card numbers court orders cite that ``strict`` kept: four shapes, one corpus.

* a foreign national's citizen registration with their embassy -- a Special Court
  money-laundering order: ``<name> (भारतीय नागरिक रजिष्ट्रेसन नं. ####/####, भारतीय
  राजदूतावास काठमाडौँ, नेपाल)``;
* a driving licence (``सवारी चालक अनुमति पत्र नं. ##-######``);
* a Nepali voter card written as digits (``मतदाता परिचय पत्र नं. ########``) -- the voter
  rule only knew the Indian EPIC shape;
* any other person's identity card (``परिचय पत्र नं.``: social security, freed Haliya,
  disability, provident fund, police).

Each was in the text after ``strict`` AND unreported by the scanner, so the publishing
gate's residue check passed it. **Every value and name here is synthetic.**

The negatives are public numbers the same words introduce -- land-deed, company and
exam-candidate registrations, case/file/dispatch numbers, a teaching licence -- and must
come through untouched and uncounted.
"""

from __future__ import annotations

import random

import fitz
import pytest

from likhit.privacy import (
    REMOVAL_TIERS,
    RedactionMode,
    iter_inline_targets,
    placeholders,
    redact_pdf,
    redact_text,
    scan_text,
)

_NEW_KINDS = ("foreign_registration", "driving_licence", "identity_card", "voter_id")


def _strict(text: str) -> tuple[str, list[str]]:
    result = redact_text(text, mode=RedactionMode.STRICT)
    return result.text, [row["kind"] for row in result.journal]


# (span, value that must go, kind removed)
_POSITIVES = (
    # --- foreign national's citizen registration (the reported leak's shape) -------------
    (
        "वर्ष ४५ को रमेश अग्रवाल (भारतीय नागरिक रजिष्ट्रेसन नं.  १२३४/५६७८,  भारतीय\n"
        "राजदूतावास काठमाडौँ, नेपाल)",
        "१२३४/५६७८",
        "foreign_registration",
    ),
    # spelling variants: स्/ष्, श/स, नम्बर, a colon, ASCII digits, a hyphen
    (
        "भारतिय नागरिक रजिस्ट्रेशन नम्बर: 2345-6789 भएको",
        "2345-6789",
        "foreign_registration",
    ),
    ("भारतीय नागरिक रजिस्ट्रेसन नं ४५६७/८९०१ भएका", "४५६७/८९०१", "foreign_registration"),
    ("भारतीय नागरिक रजिष्ट्रेशन नं.९८७६/५४३२)", "९८७६/५४३२", "foreign_registration"),
    # legacy-font conversion types the e-matra before the virama
    ("नागरिक रजिस्टे्रशन नं. ३४५६/७८९० भएको", "३४५६/७८९०", "foreign_registration"),
    # the value wrapped onto the next line
    (
        "भारतीय नागरिक रजिष्ट्रेसन नं.\n१२३४/५६७८, भारतीय",
        "१२३४/५६७८",
        "foreign_registration",
    ),
    (
        "Indian Citizen Registration No. 1234/5678 of",
        "1234/5678",
        "foreign_registration",
    ),
    # --- driving licence ---------------------------------------------------------------------
    ("सवारी चालक अनुमति पत्र नं. ०३-१२३४५६ भएका", "०३-१२३४५६", "driving_licence"),
    # glued words and an en dash
    ("सवारीचालक अनुमतिपत्र नं. ०३–६५४३२१ भएका", "०३–६५४३२१", "driving_licence"),
    # smart-card shape, behind the licence classes
    (
        "A र B वर्गको सवारी चालक अनुमतिपत्र नम्बर ०१-०६-१२३४५६७८ प्राप्त",
        "०१-०६-१२३४५६७८",
        "driving_licence",
    ),
    ("सवारी चालक लाइसेन्स नं. ०५-०४५६७८ भएका", "०५-०४५६७८", "driving_licence"),
    ("Driving Licence No. 01-06-12345678 issued", "01-06-12345678", "driving_licence"),
    # --- Nepali voter card as digits -------------------------------------------------------
    ("मतदाता परिचय पत्र नं. १२३४५६७८ भएका", "१२३४५६७८", "voter_id"),
    (
        "ना.प्र.नं. ४५६७८(रुपन्देही), मतदाता परिचय पत्र नं. ८७६५४३२१ भएका",
        "८७६५४३२१",
        "voter_id",
    ),
    ("क्रम संख्या ३४५ मतदाता परिचयपत्र नं २३४५६७८९ मा फोटो", "२३४५६७८९", "voter_id"),
    # --- any other person's identity card ---------------------------------------------------
    (
        "सामाजिक सुरक्षा लाभग्राही परिचय पत्र नं. १२३४५६७८९०१२३ भएकी",
        "१२३४५६७८९०१२३",
        "identity_card",
    ),
    (
        "सामाजिक सुरक्षा भत्ता परिचय पत्र (परिचय पत्र नंम्बर:१२३४५६७८९०१२३) लिए",
        "१२३४५६७८९०१२३",
        "identity_card",
    ),
    ("मुक्त हलिया परिचयपत्र नं. ४५६७८९ भएका", "४५६७८९", "identity_card"),
    ("म सीता रावत (परिचय पत्र नं. १२३४/५६७) को बुहारी", "१२३४/५६७", "identity_card"),
    ("नेपाल प्रहरीको परिचय पत्र नं.१२३४५६७८ भएका", "१२३४५६७८", "identity_card"),
    ("कर्मचारी संचय कोषको परिचय पत्र नं.१२३४५६ का संचयकर्ता", "१२३४५६", "identity_card"),
    # a danda used as the group separator
    ("एउटै अवधिमा परिचयपत्र नं.१२३४।५६७ बाट दोहोरो भत्ता", "१२३४।५६७", "identity_card"),
    # three-digit disability-card serial
    ("वडा नं. ४ बस्ने अपाङ्गता परिचयपत्र नं. ४५६ भएको", "४५६", "identity_card"),
    # a short serial closing a bracket is a value, not a list number
    ("अपाङ्गता परिचय पत्र (परिचय पत्र नं. ४५६) भएको", "४५६", "identity_card"),
    # an excluded word's own name with `को` is not the national ID label
    ("राष्ट्रियको परिचयपत्र नं. १२३४५६७८ भएको", "१२३४५६७८", "identity_card"),
    # an Indian national's embassy-issued card
    (
        "भारतीय दुतावासबाट जारी भएको परिचय पत्र नं. ४५६७/८९ भएको",
        "४५६७/८९",
        "identity_card",
    ),
)


@pytest.mark.parametrize(("span", "value", "kind"), _POSITIVES)
def test_the_value_is_removed_and_the_label_kept(
    span: str, value: str, kind: str
) -> None:
    redacted, kinds = _strict(span)
    assert value not in redacted
    assert kinds.count(kind) == 1
    # The label's own words (the text right before the value) are kept.
    label = span[: span.index(value)].rstrip(" :(\n")[-8:]
    assert label in redacted


@pytest.mark.parametrize(("span", "value", "kind"), _POSITIVES)
def test_the_residue_check_sees_it_before_and_not_after(
    span: str, value: str, kind: str
) -> None:
    before, _ = scan_text(span)
    assert before[kind] == 1
    redacted, _ = _strict(span)
    after, _ = scan_text(redacted)
    assert after[kind] == 0


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
    """The PDF redactor works from spans on the original text, not the rewritten text."""

    targets = [
        (span[t.start : t.end], t.kind)
        for t in iter_inline_targets(span, mode=RedactionMode.STRICT)
    ]
    assert (value, kind) in targets


def test_the_reported_leak_reads_back_with_its_placeholder() -> None:
    redacted, _ = _strict("रमेश अग्रवाल (भारतीय नागरिक रजिष्ट्रेसन नं. १२३४/५६७८, भारतीय")
    assert redacted == (
        "रमेश अग्रवाल (भारतीय नागरिक रजिष्ट्रेसन नं. "
        f"{placeholders.FOREIGN_REGISTRATION}, भारतीय"
    )


@pytest.mark.parametrize(
    ("span", "kind"),
    [
        ("राष्ट्रिय परिचयपत्र नं. १२३४५६७८९० ले", "national_id"),
        ("राष्ट्रियपरिचयपत्र नं. १२३४५६७८९० ले", "national_id"),
        ("शरणार्थी परिचय पत्र नं. १२३-४५६७८९०१ भएको", "refugee_id"),
        ("मतदाता परिचय पत्र नं. १२३४५६७८ भएका", "voter_id"),
    ],
)
def test_a_card_with_its_own_class_is_not_also_an_identity_card(
    span: str, kind: str
) -> None:
    """One value, one kind: the generic card label steps aside for the specific ones."""

    counts, _ = scan_text(span)
    assert counts[kind] == 1
    assert counts["identity_card"] == 0
    _, kinds = _strict(span)
    assert kinds == [kind]


# What must come through untouched: public registrations, file numbers and counts.
_NEGATIVES = (
    # case, file, dispatch and contract numbers
    "दर्ता नं. १२३४ मिति २०८०/०१/०२",
    "पत्र दर्ता नं. ४५६७ च.नं. १२३४",
    "मुद्दा नं. ०८०-CR-०१३५ को फैसला",
    "Contract ID No. NWSC/2079/12345",
    "च.नं. १२३४",
    # company and firm registrations are public business identifiers
    "कम्पनी रजिष्ट्रेसन नं. १२३४५६ भएको",
    "कम्पनी रजिष्ट्रेशन नम्बर १२३४५/०७५/७६",
    "कम्पनी दर्ता नं. १२३४५/०७५/७६",
    "PCL इन्जिनियरिङ्गको रजिष्ट्रेशन नं. १२३४५ रहेको",
    # land-deed registrations
    "मालपोत कार्यालय चाबहिलबाट रजिष्ट्रेशन नं. ४५६७ मिति २०७८/०२/०३ मा पास",
    "रजिष्ट्रेशन नं. ४५६७'क' को लिखत",
    "रजिष्ट्रेशन नं. ४५६७क को लिखत",
    # exam-candidate registration beside roll/symbol numbers and marks: decided to stay
    # visible
    "रजिष्ट्रेशन नं. १२३४५६७ रोल नं. ७६५४३२१ सिम्वोल नं. ०१२३४५६७ प्राप्ताङ्क ३२०",
    "| सिम्वोल नं. | रजिष्ट्रेसन नं. | प्राप्ताङ्क |\n| ०१२३४५६७ | १२३४५६७ | ३२० |",
    # a taxpayer identity number is a PAN: detected, never removed, by policy
    "करदाता परिचय पत्र नं. ६०१२३४५६७ भएको फर्म",
    "करदाता परिचयपत्र नं.६०१२३४५६७",
    "करदाता  परिचय पत्र नं. ६०१२३४५६७",
    "करदाताको परिचय पत्र नं. ६०१२३४५६७",
    "करदाता\nपरिचय पत्र नं. ६०१२३४५६७",
    "करदाता परिचय पत्र नं. ऐन २०५८ आदि",
    # a blank form's numbered field list: the digit after the label is the next field's
    # list number, not a card
    "१. कागजातको किसिम २. परिचय पत्र नं. ३. जारी गर्ने कार्यालय र स्थान",
    "१. कागजातको किसिंम २. परिचयपत्र नं. ३. जारी गर्ने कार्यालय र स्थान | ४. जारी मिति",
    "११. कागजातको किसिम १२. परिचय पत्र नं. १३. जारी गर्ने कार्यालय",
    "१०१. कागजातको किसिम १०२. परिचय पत्र नं. १०३. जारी गर्ने कार्यालय",
    "२. परिचय पत्र नं.\n३. जारी गर्ने कार्यालय",
    # a teaching licence is a professional registration
    "अध्यापन अनुमति पत्र नं. १२३४५६ भएका",
    "अध्यापन अनुमतिपत्र नं. १२३४५६ भएका",
    "निर्माण अनुमति पत्र नं. ४५६७/०७९ बमोजिम",
    # the label words in their ordinary senses, and counts
    "भारतीय नागरिक ५ जना पक्राउ",
    "भारतीय नागरिक नं. १२३४ भन्ने",
    "परिचयपत्र वितरण ५०० जना",
    "मतदाता परिचयपत्र वितरण २०० जना",
    "परिचय पत्र नं. ४५६७ जना",
    "आफ्नो परिचय दिँदै २०७८ सालमा",
    # a statute, a date and a case number behind a card label refuse, as do short runs
    "परिचय पत्र नं. ४५",
    "परिचयपत्र नियमावली, २०७५",
    "मतदाता परिचयपत्र नं. १२३४ जारी",
    "सवारी चालक अनुमति पत्र नं. १२३४ जना",
)


@pytest.mark.parametrize("span", _NEGATIVES)
def test_public_numbers_are_left_alone(span: str) -> None:
    redacted, kinds = _strict(span)
    assert redacted == span
    assert kinds == []


@pytest.mark.parametrize("span", _NEGATIVES)
def test_public_numbers_are_not_counted_as_residue(span: str) -> None:
    counts, _ = scan_text(span)
    for kind in (*_NEW_KINDS, "citizenship", "national_id", "refugee_id"):
        assert counts[kind] == 0, kind


# A seeded sweep of synthetic values: the label decides, never the digits.
_RNG = random.Random(20261126)
_DIGITS = "०१२३४५६७८९"


def _synthetic(n: int) -> str:
    return "".join(_RNG.choice(_DIGITS) for _ in range(n))


_SWEEP = [f"{_synthetic(4)}/{_synthetic(4)}" for _ in range(25)]


@pytest.mark.parametrize("value", _SWEEP)
def test_any_registration_value_goes_behind_the_citizen_label(value: str) -> None:
    redacted, kinds = _strict(f"हरि प्रसाद (भारतीय नागरिक रजिष्ट्रेसन नं. {value}, भारतीय")
    assert value not in redacted
    assert kinds == ["foreign_registration"]


@pytest.mark.parametrize("value", _SWEEP)
@pytest.mark.parametrize(
    "label", ["दर्ता नं.", "कम्पनी रजिष्ट्रेसन नं.", "कम्पनी दर्ता नं.", "रजिष्ट्रेशन नं."]
)
def test_the_same_value_stays_behind_a_public_registration_label(
    label: str, value: str
) -> None:
    span = f"{label} {value} बमोजिम"
    redacted, kinds = _strict(span)
    assert redacted == span
    assert kinds == []


def test_the_new_kinds_are_strict_only() -> None:
    span = (
        "भारतीय नागरिक रजिष्ट्रेसन नं. १२३४/५६७८, सवारी चालक अनुमति पत्र नं. ०३-१२३४५६, "
        "परिचय पत्र नं. १२३४५६, मतदाता परिचय पत्र नं. १२३४५६७८"
    )
    assert redact_text(span, mode=RedactionMode.IDENTIFIERS).text == span
    # `contact` takes a `##-######` licence as a landline (its shape is one); the new
    # classes themselves are removed only at `strict`.
    contact = redact_text(span, mode=RedactionMode.CONTACT).text
    for marker in (
        placeholders.FOREIGN_REGISTRATION,
        placeholders.DRIVING_LICENCE,
        placeholders.IDENTITY_CARD,
        placeholders.VOTER_ID,
    ):
        assert marker not in contact
    for kind in ("foreign_registration", "driving_licence", "identity_card"):
        assert REMOVAL_TIERS[kind] is RedactionMode.STRICT


# --- the PDF path: the same rules black out the glyphs ------------------------------------
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
)


def _devanagari_font() -> str:
    import os

    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    pytest.skip("no Devanagari system font available to build a synthetic PDF")


def test_redact_pdf_removes_the_new_classes_from_the_glyph_layer(tmp_path) -> None:
    lines = [
        "रमेश अग्रवाल (भारतीय नागरिक रजिष्ट्रेसन नं. १२३४/५६७८, भारतीय",
        "सवारी चालक अनुमति पत्र नं. ०३-१२३४५६ भएका",
        "मुक्त हलिया परिचयपत्र नं. ४५६७८९ भएका",
        "मतदाता परिचय पत्र नं. १२३४५६७८ भएका",
        "कम्पनी रजिष्ट्रेसन नं. ९८७६५४ भएको",
    ]
    source, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="dev", fontfile=_devanagari_font())
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + 20 * i), line, fontname="dev", fontsize=12)
    doc.save(source)
    doc.close()

    report = redact_pdf(source, out, mode=RedactionMode.STRICT)
    assert report.values_removed == 4

    with fitz.open(out) as redacted:
        after = redacted[0].get_text()
    for value in ("१२३४/५६७८", "०३-१२३४५६", "४५६७८९", "१२३४५६७८"):
        assert value not in after
    assert "रजिष्ट्रेसन" in after, "the label is kept"
    assert "९८७६५४" in after, "a company registration is public"
