"""A citizenship number is introduced by a number marker. A statute year is not.

Without the marker requirement, both the scanner and the remover read any number within
40 characters of the word नागरिकता as a certificate number. Measured over the CIAA annual
reports, of 35 detections surviving redaction only 6 were real numbers: 11 were
`नागरिकता ऐन, २०६३` -- the Citizenship *Act*'s year -- and around 14 were decision or
letter dates. That miscount is not cosmetic: `materials.redaction.residual_classes`
consumes it to decide whether a document is publishable at all, and it stranded 29 of 35
reports behind the gate.
"""

from __future__ import annotations

import pytest

from likhit.privacy import RedactionMode, redact_text, scan_text
from likhit.privacy.redact import DIGIT_BOUNDS

#: Quoted from the corpus. The statutes first -- these carry no personal data at all.
STATUTES = [
    "झुट्टा विवरण पेश गरी नागरिकता प्राप्त गरेको कसुर नेपाल नागरिकता ऐन, २०६३ को हुने",
    "प्रचलित नेपाल नागरिकता ऐन २०२० र नागरिकता नियमावली २०४९ अनुसार गाउँ सरजमिन",
    "नागरिकता ऐन, २०६३ को दफा १३ मा दोहोरो नागरिकताको प्रमाणपत्र लिन नहुने",
]

#: Dates that sit near the label and belong to a decision or a letter, not a person.
NEARBY_DATES = [
    "सच्याइएको नागरिकताका आधारमा मिति २०६६।९।२८ मा लालपुर्जा उपलब्ध गराउने",
    "नेपाली नागरिकता लिए भन्ने उजुरीका सम्बन्धमा मिति २०६४।४।३० को निर्णय अनुसार",
]

#: Real certificate numbers, all marker-introduced, all short enough that the old floor
#: of 7 refused them. Each carries an issuing office, an issue date or a named person.
REAL = [
    ("नागरिकता नं.२३९५ मिति २०४४।१०।१७ को नागरिकता प्रमाणपत्रमा", "२३९५"),
    ("नेपाली नागरिकता प्रमाणपत्र नं. २९९४ (जिल्ला प्रशासन कार्यालय सुनसरीको", "२९९४"),
    ("नेपाली नागरिकता नं. १०४४९ जारी मिति २०५६।११।२७ बमोजिमको", "१०४४९"),
    ("प्राप्त गरेको नागरिकता नं.१५१०८७ (मिति २०६४।१।३०) को प्रमाणपत्र", "१५१०८७"),
    ("ना.प्र.नं. ३६९३ मिति २०४९।४।२५ को नागरिकता अभिलेख", "३६९३"),
]


@pytest.mark.parametrize("text", STATUTES)
def test_a_statute_year_is_neither_detected_nor_removed(text: str) -> None:
    """🛑 Both halves, because fixing one alone is a different bug each way.

    Removing it destroys the legal citation a finding rests on; detecting it without
    removing it strands the document behind the publishing gate for nothing.
    """

    assert scan_text(text)[0]["citizenship"] == 0
    assert redact_text(text, mode=RedactionMode.STRICT).text == text


@pytest.mark.parametrize("text", NEARBY_DATES)
def test_a_decision_date_near_the_label_is_left_alone(text: str) -> None:
    assert scan_text(text)[0]["citizenship"] == 0
    assert redact_text(text, mode=RedactionMode.STRICT).text == text


@pytest.mark.parametrize(("text", "value"), REAL)
def test_a_short_certificate_number_is_removed(text: str, value: str) -> None:
    """⚠️ These were detected and left in, which is the worst of both answers.

    The scanner reported them, so the gate refused to publish the document; the remover's
    floor of 7 meant the value stayed in the text anyway.
    """

    out = redact_text(text, mode=RedactionMode.STRICT).text
    assert value not in out
    assert "[REDACTED:CITIZENSHIP-NO]" in out
    assert scan_text(out)[0]["citizenship"] == 0


def test_the_bare_letter_marker_requires_its_dot() -> None:
    """🛑 `न\\.?` with an optional dot matched the leading न of any word.

    It was harmless while the marker was optional and the floor was 7 -- the length guard
    caught what it let through. Made mandatory, it turned `नागरिकता नियमावली २०४९` into a
    match and **deleted the statute's year**. Found by this exact case during development.
    """

    assert redact_text(
        "नागरिकता नियमावली २०४९ अनुसार", mode=RedactionMode.STRICT
    ).text == ("नागरिकता नियमावली २०४९ अनुसार")
    # The real abbreviation still works.
    assert (
        "[REDACTED:CITIZENSHIP-NO]"
        in redact_text("नागरिकता न. १२३४५ को", mode=RedactionMode.STRICT).text
    )


def test_a_count_of_people_is_still_refused() -> None:
    """The `_COUNTED_UNIT` veto, which the marker requirement now also covers."""

    text = "नागरिकता लिएका ७८९०१२३ जना"
    assert scan_text(text)[0]["citizenship"] == 0
    assert redact_text(text, mode=RedactionMode.STRICT).text == text


def test_the_floor_is_four_and_the_ceiling_still_sixteen() -> None:
    """⚠️ Pinned because the floor is only safe while the marker is mandatory.

    A 4-digit floor with an optional marker reaches `नागरिकता ऐन, २०६३`. If someone
    relaxes the label back, this constant is the other half of that mistake.
    """

    assert DIGIT_BOUNDS["citizenship"] == (4, 16)


def test_a_seventeen_digit_value_is_still_refused_by_the_ceiling() -> None:
    text = "नागरिकता नं. १२३४५६७८९०१२३४५६७"
    assert redact_text(text, mode=RedactionMode.STRICT).text == text


def test_the_character_window_admits_a_four_digit_value() -> None:
    """🛑 The window floor is a CHARACTER count and binds before `DIGIT_BOUNDS`.

    `_value_window` builds "a digit, lo-hi inner characters, then a digit", so `lo=3` set
    a five-character minimum and refused `२३९५` before the digit bounds were consulted.
    Lowering only `DIGIT_BOUNDS` changed nothing -- measured, all four short numbers in
    the corpus stayed in the text. This asserts the shortest real value survives the
    window, which is the half a digit-bounds test cannot see.
    """

    out = redact_text("नागरिकता नं. २३९५", mode=RedactionMode.STRICT).text
    assert out == "नागरिकता नं. [REDACTED:CITIZENSHIP-NO]"


def test_a_detection_is_always_accompanied_by_a_removal() -> None:
    """🛑 The property the gate depends on, and it is one-directional.

    A detection WITHOUT a removal is what strands a document: the scanner reports residue,
    `materials.redaction.residual_classes` refuses to publish, and the value is in the text
    either way. That direction must never happen.

    ⚠️ The converse is deliberately allowed -- see `redact.CITIZENSHIP_LABEL_VALUE`. The
    remover's number marker is optional while the scanner's is required, so the remover
    reaches slightly further. A removal the scanner never counted costs nothing, and
    requiring symmetry here measured as pure loss: it dropped
    `नागरिकताको प्रमाणपत्रमा २०२४।३।११`, a second claimed birth date in a sentence
    comparing it against the one on an educational certificate.
    """

    for text in STATUTES + NEARBY_DATES + [t for t, _ in REAL]:
        if scan_text(text)[0]["citizenship"] > 0:
            assert (
                "[REDACTED:CITIZENSHIP-NO]"
                in redact_text(text, mode=RedactionMode.STRICT).text
            ), f"detected but not removed, which strands a document: {text}"


def test_the_remover_reaches_a_second_birth_date_the_scanner_does_not_count() -> None:
    """⚠️ The asymmetry, pinned so nobody "fixes" it back into symmetry.

    This sentence compares two claimed birth dates. The second is introduced by the
    citizenship certificate rather than by a `जन्म मिति` label, so only this rule reaches
    it -- under the wrong class name, but the value is personal data and it goes.
    """

    text = "प्रमाणपत्रमा निजको जन्म मिति २०२१।५।६ र नेपाली नागरिकताको प्रमाणपत्रमा २०२४।३।११ उल्लेख"
    out = redact_text(text, mode=RedactionMode.STRICT).text

    assert "२०२४।३।११" not in out
    assert scan_text(text)[0]["citizenship"] == 0


# ---------------------------------------------------------------------------
# A window must not bridge its own redaction.
# ---------------------------------------------------------------------------


def test_a_label_does_not_reach_past_its_redaction_to_the_next_number() -> None:
    """🛑 The dominant residue on this shelf, and the worst shape the scanner has.

    The label's value WAS removed; the window then reached 25 blanked characters past the
    placeholder to the certificate's issue date and reported that as a citizenship number.
    `residual_classes` consumes this count to decide publishability, so the document was
    held back for a value that had already gone.
    """

    out = redact_text(
        "नागरिकता नं.२३९५ मिति २०४४।१०।१७ को", mode=RedactionMode.STRICT
    ).text

    assert "[REDACTED:CITIZENSHIP-NO]" in out
    assert "२०४४।१०।१७" in out, "the issue date is not personal data and must stay"
    assert scan_text(out)[0]["citizenship"] == 0


def test_a_second_unremoved_value_is_still_detected() -> None:
    """⚠️ The guard that keeps the above from being a blindfold.

    Terminating a window at a placeholder must not hide a value that is genuinely still
    in the text -- it is found through its own label, on its own side of the placeholder.
    """

    assert (
        scan_text("नागरिकता नं. [REDACTED:CITIZENSHIP-NO] र नागरिकता नं. ९८७६५४")[0][
            "citizenship"
        ]
        == 1
    )
    assert (
        scan_text("खाता नं. [REDACTED:CITIZENSHIP-NO] र खाता नं. १२३४५६७८९०")[0][
            "bank_account"
        ]
        == 1
    )
    # Shape-anchored patterns never depended on a window at all.
    assert scan_text("सम्पर्क [REDACTED:EMAIL] ९८४१२३४५६७")[0]["mobile"] == 1


def test_the_phantom_labelled_phone_is_retired() -> None:
    """The case `scan_text`'s own docstring cites as invented by collapsing a marker.

    Same-width spaces bounded it; newlines remove it.
    """

    assert scan_text("फोन [REDACTED:EMAIL] 1234567")[0]["phone_labelled"] == 0


def test_blanking_preserves_width() -> None:
    """🛑 The property the same-width rule bought, which newlines must not give back.

    If a placeholder were collapsed, everything after it would move closer and the windows
    would measure a distance they were never calibrated against.
    """

    import re

    from likhit.privacy.placeholders import PLACEHOLDER_PATTERN

    text = "क [REDACTED:CITIZENSHIP-NO] ख"
    blanked = PLACEHOLDER_PATTERN.sub(lambda m: "\n" * len(m.group(0)), text)
    assert len(blanked) == len(text)
    assert re.sub(r"\n", "", blanked) == "क  ख"
