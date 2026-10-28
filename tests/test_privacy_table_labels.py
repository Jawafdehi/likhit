"""The table pass was Devanagari-only, and half the corpus labels its tables in English.

🛑 **This is a reach defect, not a coverage gap.** ``redact_tables`` handles the layout
the inline rules cannot — a value in one cell, its label in another — but its two label
patterns listed only Devanagari forms. A charge sheet that reproduces an ePassport
application form tabulates ``Citizenship No.`` beside ``Date of Birth`` in English, and
those tables were invisible: no label, no candidate, no removal, while the scanner went on
reporting the values. Measured over the 67 documents the platform's gate refused on
27 October 2026, with the inline fixes already applied: citizenship values left 18 -> 5,
dates of birth 25 -> 23, documents still refusing 31 -> 29.

The labels stay **narrower than the inline pass** on purpose — a header governs every cell
in its column, so the English citizenship form requires a qualifier (``No.``, ``Number``,
``ID``) where the inline rule is happy with the bare word.
"""

from __future__ import annotations

import pytest

from likhit.privacy import placeholders
from likhit.privacy.redact_tables import redact_table_text


def _targets(markdown: str) -> tuple[str, int]:
    out, targets, _stats = redact_table_text(markdown)
    return out, len(targets)


@pytest.mark.parametrize(
    "label",
    ["Date of Birth", "date of birth", "Birth Date", "D.O.B.", "जन्म मिति"],
)
def test_a_date_of_birth_column_is_found_in_either_script(label: str) -> None:
    out, found = _targets(f"| {label} | 2016-06-19 |\n| Name | Ram |\n")

    assert found == 1
    assert placeholders.TABLE_DATE_OF_BIRTH in out
    assert "2016-06-19" not in out


@pytest.mark.parametrize(
    "label", ["Citizenship No.", "Citizenship Number", "Citizenship ID", "ना.प्र.नं."]
)
def test_a_citizenship_column_is_found_in_either_script(label: str) -> None:
    out, found = _targets(f"| {label} | 12-01-75-04567 |\n| Name | Ram |\n")

    assert found == 1
    assert placeholders.TABLE_CITIZENSHIP in out


def test_a_bare_citizenship_header_is_not_a_label() -> None:
    """A header governs its whole column, so the English form needs a qualifier."""

    out, found = _targets("| Citizenship | 12-01-75-04567 |\n| Name | Ram |\n")

    assert found == 0
    assert "12-01-75-04567" in out


def test_an_era_marker_inside_the_value_cell_does_not_hide_the_date() -> None:
    """`2016-06-19 BS` — two letters that made the whole cell undescribable."""

    out, found = _targets("| Date of Birth | 2016-06-19 BS |\n| Name | Ram |\n")

    assert found == 1
    assert "2016-06-19" not in out


def test_a_short_number_under_a_citizenship_header_is_still_refused() -> None:
    """The measured floor of 7 digits stands — those spans are service counts."""

    out, found = _targets("| Citizenship No. | 35152 |\n| Name | Ram |\n")

    assert found == 0
    assert "35152" in out


def test_a_year_beside_a_real_date_does_not_make_the_row_ambiguous() -> None:
    """🛑 The regression that took the bare-year cell back out.

    Admitting a four-digit year as a candidate makes it COMPETE with the real
    date in the same row: this row went from one candidate (redacted) to two
    non-adjacent ones (refused), so a change meant to remove more dates of birth
    kept one that was already being removed.
    """

    row = (
        "| विवरण | मान | अन्य | कैफियत |\n| --- | --- | --- | --- |\n"
        "| जन्म मिति | राम बहादुर | २०३२/०४/१२ | २०७८ |\n"
    )

    out, found = _targets(row)

    assert found == 1
    assert "२०३२/०४/१२" not in out
    assert "२०७८" in out, "the remarks cell is not a date of birth"
