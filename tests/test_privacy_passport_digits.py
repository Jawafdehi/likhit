"""All-digit passport numbers, and the sibling ID labels that had the same blind spot.

Found on JAW-79 (a Patan High Court charge sheet, ``ag/115641``): four passport numbers
written after ``राहदानी नं.`` in Devanagari digits stayed in the text at ``strict`` AND
were not reported by the scanner, so the publishing gate's residue check passed them. The
passport rule only knew the Latin-prefixed shape (``PA1234567``); a Nepali passport number
written with no prefix -- in either digit script -- matched nothing.

Each positive is checked four ways, as in ``test_privacy_court_id_numbers``: removed with
the label kept, seen by the residue check before and not after, idempotent, and reached by
the span pass the PDF redactor uses. **Every value here is synthetic.**
"""

from __future__ import annotations

import pytest

from likhit.privacy import (
    RedactionMode,
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


# (span, values that must all go, kind)
_POSITIVES = (
    # --- passport: digits only, both scripts --------------------------------------------
    ("प्रतिवादी राम थापा (राहदानी नं. ०८१२३४५६) ले", ("०८१२३४५६",), "passport"),
    ("प्रतिवादी राम थापा (राहदानी नं. 08123456) ले", ("08123456",), "passport"),
    ("Ram Thapa, Passport No. 08123456, resident of", ("08123456",), "passport"),
    ("Passport No.: 08123456", ("08123456",), "passport"),
    ("passport number 1234567 issued", ("1234567",), "passport"),
    # the other label spellings
    ("राम थापा (राहदानी नम्बर ०८१२३४५६) ले", ("०८१२३४५६",), "passport"),
    ("राम थापा (पासपोर्ट नं. ०८१२३४५६) ले", ("०८१२३४५६",), "passport"),
    ("राम थापाको राहदानी नं.०८१२३४५६ भएको", ("०८१२३४५६",), "passport"),
    ("राम थापाको राहदानी नं ०८१२३४५६ भएको", ("०८१२३४५६",), "passport"),
    ("राम थापाको पासपोर्ट नम्बर: ०८१२३४५६ भएको", ("०८१२३४५६",), "passport"),
    # a list under one label: every number goes, not just the first
    ("राहदानी नं. ०८१२३४५६, ११२२३३४४ भएका", ("०८१२३४५६", "११२२३३४४"), "passport"),
    ("राहदानी नं. ०८१२३४५६ र ११२२३३४४ भएका", ("०८१२३४५६", "११२२३३४४"), "passport"),
    (
        "क्रमशः राहदानी नं. ०८१२३४५६, ११२२३३४४, ०९८७६५४३ र ०७६५४३२१ भएका",
        ("०८१२३४५६", "११२२३३४४", "०९८७६५४३", "०७६५४३२१"),
        "passport",
    ),
    ("Passport No. 08123456 and 11223344", ("08123456", "11223344"), "passport"),
    # mixed with the Latin-prefixed shape
    ("Passport No. PA1234567 र ०८१२३४५६", ("PA1234567", "०८१२३४५६"), "passport"),
    # the value on the line after its label
    ("राम थापा, राहदानी नं.\n०८१२३४५६ भएको", ("०८१२३४५६",), "passport"),
    ("Passport No.\n  08123456", ("08123456",), "passport"),
    # the label as a Word-made PDF's glyph layer carries it (stray repha before the marker)
    ("राम थापा (पासपोटा र्नं. ०८१२३४५६)", ("०८१२३४५६",), "passport"),
    ("राम थापा (राहदानी र्नं. ०८१२३४५६)", ("०८१२३४५६",), "passport"),
    # the JAW-79 PDF's own glyph layer: `राहदानी नं.` comes out as `रािदार्नी र्नं.`
    (
        "(र्ना.प्र.र्नं. ४५६, कैलाली, रािदार्नी र्नं. 08123456, 11223344, िोिाईल",
        ("08123456", "11223344"),
        "passport",
    ),
    ("रािदार्नी र्नं. \n08123456, िोिाईल", ("08123456",), "passport"),
    # --- citizenship: `ना.प्र.पत्र नं.` (the प्रमाणपत्र spelled out after the abbreviation)
    ("सीता शर्मा (ना.प्र.पत्र नं. १२३४५/६७८) ले", ("१२३४५/६७८",), "citizenship"),
    ("सीता शर्मा (ना.प्र.प.नं. ४५६) ले", ("४५६",), "citizenship"),
    # --- national identity card -----------------------------------------------------------
    ("राम थापा (राष्ट्रिय परिचयपत्र नं. १२३-४५६-७८९०) ले", ("१२३-४५६-७८९०",), "national_id"),
    ("राम थापा (राष्ट्रिय परिचय पत्र नम्बर १२३४५६७८९०) ले", ("१२३४५६७८९०",), "national_id"),
    ("National ID No. 1234567890 of", ("1234567890",), "national_id"),
    ("NID No.: 123-456-7890", ("123-456-7890",), "national_id"),
    # --- bank accounts: a list under one label --------------------------------------------
    (
        "खाता नं. ०१२३४५६७८९०१, ९८७६५४३२१०९८ मा जम्मा",
        ("०१२३४५६७८९०१", "९८७६५४३२१०९८"),
        "bank_account",
    ),
    (
        "खाता नं. ०१२३४५६७८९०१ र ९८७६५४३२१०९८ मा जम्मा",
        ("०१२३४५६७८९०१", "९८७६५४३२१०९८"),
        "bank_account",
    ),
)


@pytest.mark.parametrize(("span", "values", "kind"), _POSITIVES)
def test_every_value_is_removed_and_the_label_kept(
    span: str, values: tuple[str, ...], kind: str
) -> None:
    redacted, kinds = _strict(span)

    for value in values:
        assert value not in redacted
    assert kinds.count(kind) == len(values)


@pytest.mark.parametrize(("span", "values", "kind"), _POSITIVES)
def test_the_residue_check_sees_it_before_and_not_after(
    span: str, values: tuple[str, ...], kind: str
) -> None:
    assert _detected(span, kind) >= 1
    redacted, _ = _strict(span)
    residue, _ = scan_text(redacted)
    assert residue[kind] == 0


@pytest.mark.parametrize(("span", "values", "kind"), _POSITIVES)
def test_redaction_is_idempotent(span: str, values: tuple[str, ...], kind: str) -> None:
    once, _ = _strict(span)
    twice, kinds = _strict(once)
    assert twice == once
    assert kinds == []


@pytest.mark.parametrize(("span", "values", "kind"), _POSITIVES)
def test_span_targets_agree_with_the_text_pass(
    span: str, values: tuple[str, ...], kind: str
) -> None:
    """The PDF redactor works from spans, not from the rewritten text."""

    spans = [
        span[t.start : t.end]
        for t in iter_inline_targets(span, mode=RedactionMode.STRICT)
    ]
    for value in values:
        assert value in spans


def test_the_residue_check_counts_every_passport_in_a_list() -> None:
    """One label, four numbers: four values left, not one."""

    span = "राहदानी नं. ०८१२३४५६, ११२२३३४४, ०९८७६५४३ र ०७६५४३२१"
    assert _detected(span, "passport") == 4


def test_the_passport_label_survives() -> None:
    redacted, _ = _strict("राम थापा (राहदानी नं. ०८१२३४५६) ले")
    assert redacted == f"राम थापा (राहदानी नं. {placeholders.PASSPORT}) ले"


def test_a_second_unremoved_passport_is_still_reported() -> None:
    """A redacted value must not hide a live one under its own label further on."""

    span = f"राहदानी नं. {placeholders.PASSPORT}, पासपोर्ट नं. ०८१२३४५६"
    assert _detected(span, "passport") == 1


# The identifier next to a number the document is read for: the identifier goes, the
# neighbour stays exactly as written.
_NEIGHBOURS = (
    (
        "राहदानी नं. ०८१२३४५६ जारी मिति २०७८/०१/०२ गते",
        f"राहदानी नं. {placeholders.PASSPORT} जारी मिति २०७८/०१/०२ गते",
    ),
    (
        "Passport No. 08123456 dated 2021/01/02",
        f"Passport No. {placeholders.PASSPORT} dated 2021/01/02",
    ),
    (
        "राहदानी नं. ०८१२३४५६ र रु. १२,३४,५६७ बुझाएको",
        f"राहदानी नं. {placeholders.PASSPORT} र रु. १२,३४,५६७ बुझाएको",
    ),
    (
        "खाता नं. ०१२३४५६७८९०१ र रु. १२,३४,५६७",
        f"खाता नं. {placeholders.BANK_ACCOUNT} र रु. १२,३४,५६७",
    ),
    (
        "राहदानी नं. ०८१२३४५६ र १२३४५६७ रुपैयाँ",
        f"राहदानी नं. {placeholders.PASSPORT} र १२३४५६७ रुपैयाँ",
    ),
    (
        "राहदानी नं. ०८१२३४५६, मुद्दा नं. ०८२-CR-००४८",
        f"राहदानी नं. {placeholders.PASSPORT}, मुद्दा नं. ०८२-CR-००४८",
    ),
)


@pytest.mark.parametrize(("span", "expected"), _NEIGHBOURS)
def test_only_the_identifier_goes(span: str, expected: str) -> None:
    redacted, _ = _strict(span)
    assert redacted == expected
    hp, _ = scan_text(redacted)
    for kind in ("passport", "citizenship", "national_id", "bank_account"):
        assert hp[kind] == 0, kind


# What must come through untouched: the numbers these documents are read for, next to the
# same labels.
_NEGATIVES = (
    # dates and statute years beside the label
    "राहदानी बनाउन मिति २०८१।०२।०३ मा निवेदन",
    "राहदानी ऐन, २०७६ को दफा ४",
    "Passport Act, 2076",
    "राहदानी विभागको च.नं. १२३४ मिति २०८०।०१।०१",
    "राहदानी नं. मिति २०७८/०१/०२",
    # the label word in its ordinary sense, then a count or an amount
    "पासपोर्ट साइज फोटो २ प्रति",
    "राहदानी नं. मा उल्लेख भएको रु. १२३४५६७",
    "राहदानी शुल्क रु. ५,०००",
    "राहदानी लिएका ११२२३३४४ जना",
    "राहदानी नं. ११२२३३४४ जना",
    # case numbers
    "मुद्दा नं. ०८२-CR-००४८ को अभियोग पत्र, राहदानी नं. उल्लेख नभएको",
    "राहदानी नं. ०८२-CR-००४८",
    # a list continuation is never money or a count
    "राहदानी नं. उल्लेख नभएको र १२३४५६७ रुपैयाँ",
    # a ten-digit mobile is not a passport (the contact pass owns it)
    "Passport No. 1234567890123",
    # the value shape with no label
    "मिसिल नं. ०८१२३४५६ को फैसला",
    "चेक नं. ०८१२३४५६ बाट",
    "बिल नं. मा उल्लेख भएको र ०८१२३४५६ बमोजिम",
    # national ID labels without a number marker, or with a count after them
    "राष्ट्रिय परिचयपत्र वितरण १२३४५६ जना",
    "राष्ट्रिय परिचयपत्र तथा पञ्जीकरण विभाग, मिति २०८०।०१।०१",
    "राष्ट्रिय परिचयपत्र नं. ११२२३३४४ जना",
    # citizenship: a count after ना.प्र.पत्र
    "ना.प्र.पत्र सिफारिस १२३४ जना",
)


@pytest.mark.parametrize("span", _NEGATIVES)
def test_non_identifiers_are_left_alone(span: str) -> None:
    redacted, kinds = _strict(span)
    assert redacted == span
    assert kinds == []


@pytest.mark.parametrize("span", _NEGATIVES)
def test_non_identifiers_are_not_counted_as_residue(span: str) -> None:
    hp, _ = scan_text(span)
    for kind in ("passport", "citizenship", "national_id", "bank_account"):
        assert hp[kind] == 0, kind
