"""A four-digit run beside `नागरिकता` is a year, and counting it refused real documents.

The scanner's output is the disclosure published beside the corpus AND the gate the
platform's writers are held to, so a false positive here is not cosmetic: it refuses a
document over data that is not in it. Over 106 control documents every four-digit run this
signal reached across prose was a date or a statute year.

⚠️ This is the one narrowing in `signals` that hides nothing — four digits is below
`redact.DIGIT_BOUNDS`'s floor of 7 for the class, so no remover was ever going to act on
one.
"""

from __future__ import annotations

import pytest

from likhit.privacy.signals import scan_text


@pytest.mark.parametrize(
    "text",
    [
        "नागरिकता बदर भएको भनी पठाएको पत्र मैले २०६७।७।२३ मा",
        "नेपाल नागरिकता ऐन, २०२० बमोजिम",
        "नागरिकतासम्बन्धी दर्ता किताब २०३२ मा",
    ],
)
def test_a_year_near_the_label_is_not_a_citizenship_signal(text: str) -> None:
    assert scan_text(text)[0]["citizenship"] == 0


@pytest.mark.parametrize(
    "text",
    [
        "नागरिकता नं. ४५२५६ रहेको",  # five digits — short, but a real number
        "नागरिकता प्रमाणपत्र नं. १२-०१-७५-०४५६७ को",
        "Citizenship No. 3515212 issued",
    ],
)
def test_a_real_citizenship_number_is_still_counted(text: str) -> None:
    assert scan_text(text)[0]["citizenship"] >= 1
