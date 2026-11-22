"""`LABELLED["date_of_birth"]` must read the same labels the remover does.

The scanner's count is what JawafdehiAPI's residue gate consumes
(`materials.redaction.residual_classes`): any date of birth it still finds after a
strict pass withholds the whole transcript. Its label had drifted wider than the
remover's in two places, and both stranded documents:

* bare `जन्मदर्ता` / `जन्म दर्ता` (birth *registration*). A municipality's audit
  report tabulates its civil-registration services -- "births registered: 1234" --
  and the scanner read every such count as a date of birth no rule removes.
  61 of the Auditor General's 2081/82 local-level reports lost their transcript
  this way.
* `d.o.b` with every dot optional and no right boundary, so the start of any
  word spelled `dob...` (a road name, a contractor) counted, followed by an amount.

All values below are synthetic.
"""

from __future__ import annotations

import pytest

from likhit.privacy import RedactionMode, redact_text, scan_text


@pytest.mark.parametrize(
    "text",
    [
        # Table row from a service-delivery summary.
        "| विवरण | जम्मा संख्या |\n| जन्मदर्ता | १२३४ |\n| मृत्युदर्ता | ५६७ |",
        "| जन्म दर्ता | १२३४ | २३४५ |",
        # The same statistic in running prose.
        "यस वर्ष पालिकाबाट जन्म दर्ता १२३४ वटा, मृत्यु दर्ता ५६७ वटा भएको छ।",
        "विवरण जम्मा जन्मदर्ता 1234 मृत्युदर्ता 567 विवाह दर्ता 89",
        "यस वर्ष १२३४ जन्मदर्ता र मृत्युदर्ता, २३४५ वटा नाता प्रमाणित",
        # Words that merely start with "dob", followed by an amount.
        "| सडक मर्मत | Dobali Construction | 123456.78 |",
        "DOBATO DEKHI KHOLA 1.2 1234",
        "DOBSPI12/13-45678",
    ],
)
def test_a_count_or_a_word_is_not_a_date_of_birth(text: str) -> None:
    assert scan_text(text)[0]["date_of_birth"] == 0


@pytest.mark.parametrize(
    "text",
    [
        "जन्म मिति २०४०-०१-०१",
        "जन्ममिति: २०४०/०१/०१",
        "जन्म दर्ता मिति २०४०।०१।०१",
        "जन्मदर्ता नं. १२३४५",
        "जन्म दर्ता प्रमाणपत्र नं. १२३४५",
        "Date of Birth: 1983-04-14",
        "D.O.B. 1983-04-14",
        "DOB: 1983",
        "dob 14/04/1983",
    ],
)
def test_a_labelled_date_of_birth_is_still_counted(text: str) -> None:
    assert scan_text(text)[0]["date_of_birth"] >= 1


@pytest.mark.parametrize(
    "text",
    [
        "| जन्मदर्ता | १२३४ |",
        "जन्म दर्ता १२३४ वटा",
        "| Dobali Construction | 123456.78 |",
    ],
)
def test_a_strict_pass_leaves_no_date_of_birth_residue_on_a_count(text: str) -> None:
    """The property the API gate checks: scan after redact finds nothing."""
    redacted = redact_text(text, mode=RedactionMode.STRICT).text

    assert redacted == text, "a count is not personal data; nothing should be removed"
    assert scan_text(redacted)[0]["date_of_birth"] == 0
