"""JAW-167: two citizenship gaps found while redacting PDFs. Every fixture is synthetic.

1. The label written out as ``नागरिकता प्रशासन नं`` (ag/60206) was not a label at all,
   with or without a line break before the value.
2. A table column headed ``ना.प्र.नं.र प्राप्त गरेको जिल्ला`` (document/20260103.7d4b21f2)
   writes the issuing district into the same cell as the number, so no cell matched.
"""

import pytest

from likhit.privacy import RedactionMode, redact_text, scan_text
from likhit.privacy import redact_tables


@pytest.mark.parametrize(
    "source",
    [
        "राम थापा (नागरिकता प्रशासन नं १२३४५६/७८:काठमाडौं) ले",
        "राम थापा (नागरिकता प्रशासन नं\n१२३४५६/७८:काठमाडौं) ले",
        "राम थापा (नागरिकता प्रशासन नं. १२३४५६/७८:काठमाडौं) ले",
        "राम थापा (नागरिकता प्रमाणपत्र नं १२३४५६/७८:काठमाडौं) ले",
        "राम थापा (नागरिकता प्रमाणपत्र नं\n१२३४५६/७८:काठमाडौं) ले",
    ],
)
def test_the_spelled_out_label_is_redacted_and_counted(source):
    assert scan_text(source)[0]["citizenship"] == 1
    out = redact_text(source, mode=RedactionMode.STRICT).text
    assert "१२३४५६" not in out
    assert "[REDACTED:CITIZENSHIP-NO]" in out
    assert "काठमाडौं" in out


REGISTER = (
    "| क्र.सं. | शेयरधनीको नाम | ना.प्र.नं.र प्राप्त गरेको जिल्ला | शेयर संख्या |\n"
    "|---|---|---|---|\n"
    "| १ | राम बहादुर थापा | १२३४५/६ ७ ओखलढुंगा | ५०० |\n"
    "| २ | सीता कुमारी श्रेष्ठ | ४५६७८ ललितपुर | ३०० |\n"
    "| ३ | हरि प्रसाद | २७-०१-७०-०४५६७ | ३०० |\n"
)


def test_a_citizenship_column_with_the_district_in_the_cell_is_redacted():
    output, targets, _ = redact_tables.redact_table_text(REGISTER)
    assert len(targets) == 3
    for value in ("१२३४५", "४५६७८", "०४५६७"):
        assert value not in output
    # The share counts beside them are not identifiers.
    assert "| ५०० |" in output and "| ३०० |" in output
    assert output.count("[REDACTED:TABLE-CITIZENSHIP-NO]") == 3


def test_the_district_tail_is_letters_only():
    # A second number after the "district" is not a district: the cell is refused,
    # not partly matched.
    source = "| नाम | ना.प्र.नं.र जिल्ला |\n| राम | १२३४५ ललितपुर ६७८९० |\n"
    _, targets, _ = redact_tables.redact_table_text(source)
    assert targets == []


def test_a_district_only_cell_is_not_a_candidate():
    source = "| नाम | ना.प्र.नं.र जिल्ला |\n| राम | ललितपुर |\n| सीता | १२३ ललितपुर |\n"
    _, targets, _ = redact_tables.redact_table_text(source)
    assert targets == []


def test_the_looser_shape_is_header_only():
    # A same-row label still needs a whole-cell value: a district beside it is prose.
    source = "| ना.प्र.नं. | १२३४५ ललितपुर | शेयर |\n"
    _, targets, _ = redact_tables.redact_table_text(source)
    assert targets == []


def test_rerun_is_stable():
    once, _, _ = redact_tables.redact_table_text(REGISTER)
    twice, targets, _ = redact_tables.redact_table_text(once)
    assert twice == once
    assert targets == []


@pytest.mark.parametrize(
    "source",
    [
        "नागरिकता नं मिति २०८०/०१/०१",
        "नागरिकता नं\nमिति २०८०/०१/०१",
        "नागरिकता प्रशासन नं\nमिति २०८०/०१/०१",
    ],
)
def test_an_issue_date_after_the_marker_is_not_counted(source):
    # The remover keeps the date (another field's cue ends its gap), so counting it
    # would hand the residue gate a value no rule clears (CodeRabbit on #140).
    assert redact_text(source, mode=RedactionMode.STRICT).text == source
    assert scan_text(source)[0]["citizenship"] == 0


def test_a_header_backed_short_cell_under_a_dual_header_is_ambiguous():
    source = "| नाम | नागरिकता नं / जन्म मिति |\n| राम | १२३४५ |\n"
    output, targets, _ = redact_tables.redact_table_text(source)
    assert [t.classification for t in targets] == ["ambiguous_personal_value"]
    assert "१२३४५" not in output
