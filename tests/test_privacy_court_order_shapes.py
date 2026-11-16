"""Identifier shapes from the Supreme Court's Word orders that the strict tier left public.

Found while publishing redacted transcripts for ~40,000 court orders (JAW-49): every span
below was still in the text after ``strict``. The en-dash accounts were in published
transcripts, because the scanner -- which wants an unbroken run of digits -- did not report
them either; the code-and-space accounts held their documents back at the residue gate.
"""

from __future__ import annotations

import pytest

from likhit.privacy import RedactionMode, placeholders, redact_text
from likhit.privacy.redact import redact_inline_text


def _strict(text: str) -> tuple[str, list[str]]:
    """The tier the platform publishes at; bank accounts are removed there only."""
    result = redact_text(text, mode=RedactionMode.STRICT)
    return result.text, [row["kind"] for row in result.journal]


#: Word autocorrect turns a typed hyphen between digit groups into an en dash.
_EN_DASH_ACCOUNTS = (
    # supreme.072-rb-0277
    ("खाता नं. १९–००००४–० बाट भुक्तानी पाउने", "१९–००००४–०"),
    # special.93-068-0123
    ("खाता नं.५५०–०१–२७९–५० सम्बन्धमा पहिले पेश गरे", "५५०–०१–२७९–५०"),
    ("खाता नं ५१–०१–५४२–६३५०१२–०१–०५को चेक", "५१–०१–५४२–६३५०१२–०१–०५"),
    ("खाता नं. १६–२२–५२४–०२२९०४–०१–१ भएको", "१६–२२–५२४–०२२९०४–०१–१"),
)


@pytest.mark.parametrize(("span", "value"), _EN_DASH_ACCOUNTS)
def test_an_en_dash_separated_account_is_redacted(span: str, value: str) -> None:
    redacted, kinds = _strict(span)

    assert value not in redacted
    assert placeholders.BANK_ACCOUNT in redacted
    assert kinds == ["bank_account"]
    assert redacted.startswith("खाता नं")


def test_an_em_dash_is_a_separator_too() -> None:
    redacted, _ = _strict("खाता नं. ५१—०१—५४२—६३५०१२ बाट")
    assert "६३५०१२" not in redacted


def test_an_en_dash_separated_citizenship_number_is_redacted() -> None:
    redacted, journal, _ = redact_inline_text("नागरिकता नं. २७–०१–७०–१२३४५ जारी")
    assert "१२३४५" not in redacted
    assert [row["kind"] for row in journal] == ["citizenship"]


def test_an_en_dash_separated_date_of_birth_is_redacted() -> None:
    redacted, journal, _ = redact_inline_text("जन्म मिति २०३२–०४–१५ भएको")
    assert "२०३२" not in redacted
    assert [row["kind"] for row in journal] == ["date_of_birth"]


_CODE_SPACE_ACCOUNTS = (
    ("नविल बैंकको खाता नं. KL 2035931 भएको", "KL", "2035931"),
    ("खाता नं. KP 2504948 मा 15 जुलाई", "KP", "2504948"),
    ("खाता नं TD ०७३२०५ र ऐ.बैकको बचत", "TD", "०७३२०५"),
    ("खाता नं. Md २२१६५९९ चेक नं.", "Md", "२२१६५९९"),
)


@pytest.mark.parametrize(("span", "code", "value"), _CODE_SPACE_ACCOUNTS)
def test_a_code_then_space_account_is_redacted_code_and_all(
    span: str, code: str, value: str
) -> None:
    redacted, kinds = _strict(span)

    assert value not in redacted
    # The code is part of the identifier and goes with it.
    assert code not in redacted
    assert placeholders.BANK_ACCOUNT in redacted
    assert kinds == ["bank_account"]


def test_an_amount_in_rupees_after_an_account_label_is_not_an_account() -> None:
    redacted, kinds = _strict("खाता नं. Rs 500000 जम्मा")
    assert "500000" in redacted
    assert "bank_account" not in kinds


def test_a_dashed_range_that_is_too_short_is_still_left() -> None:
    # Four digits behind a citizenship label are below no floor, but a fiscal-year range is
    # not behind one at all: the dash adds no reach to text that carries no label.
    redacted, journal, _ = redact_inline_text("आर्थिक वर्ष २०७५–७६ को बजेट")
    assert redacted == "आर्थिक वर्ष २०७५–७६ को बजेट"
    assert journal == []
