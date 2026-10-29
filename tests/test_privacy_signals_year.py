"""Direct coverage for `LABELLED["citizenship"]`, which had none.

The narrowing-specific tests went with the narrowing (see the 🛑 note on the
pattern in `likhit/privacy/signals.py` for why three attempts were reverted).
These three fixtures are the ones that hold against the pattern as measured, and
without them the signal — including its English alternative and its
``[^\\n]{0,40}?`` reach — has no test anywhere.
"""

from __future__ import annotations

import pytest

from likhit.privacy.signals import scan_text


@pytest.mark.parametrize(
    "text",
    [
        # Ten digits across its separators, longest contiguous run four.
        "नागरिकता नं. ११-२२-३३-४४४४ रहेको",
        # Five digits: real, and below the remover's floor of 7 — the value that
        # makes the two masters irreconcilable.
        "नागरिकता नं. ४५२५६ रहेको",
        # The English alternative.
        "Citizenship No. 3515212 issued",
    ],
)
def test_a_citizenship_number_is_counted(text: str) -> None:
    assert scan_text(text)[0]["citizenship"] >= 1


def test_the_scanner_never_emits_a_value() -> None:
    """Its output is published beside the corpus, so it carries counts only."""

    high, _name_shaped = scan_text("नागरिकता नं. ११-२२-३३-४४४४ रहेको")

    assert all(isinstance(v, int) for v in high.values())
