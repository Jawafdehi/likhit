"""What a caller is asking for when they ask for redaction.

🛑 **A mode names a policy tier, never a pass.** ``likhit-redact redact --tables`` names an
implementation detail, which is why removing everything from a tree takes two invocations
chained through a staging directory -- and why a caller has to know that the inline and table
passes exist at all. Tiers are the contract; which passes run is this package's problem.

🛑🛑 **Ordered tiers, not a set of flags.** Precision here is measured per removal rule against
a real corpus, and this project's redaction history is 572 real hits against **11,215
non-secret values clobbered**. A flag set lets every caller invent a combination nobody
measured; four named tiers are four configurations that can each carry a number.

⚠️ :data:`RedactionMode.IDENTIFIERS` is the scope that was formally adjudicated; it is **no
longer the default**, for the reason recorded on :data:`DEFAULT_MODE`.
"""

from __future__ import annotations

from enum import Enum


class RedactionMode(str, Enum):
    """The removal tiers, in increasing order of what they take out."""

    #: Remove nothing. Output is byte-identical to the input. First-class on purpose:
    #: internal casework, the retained original and the research corpus all need it, and a
    #: library that cannot be asked to leave text alone will be worked around rather than used.
    NONE = "none"

    #: Citizenship numbers and dates of birth -- the two classes adjudicated as unambiguously
    #: personal and of no research value. The formally adjudicated scope, and the right
    #: choice for a caller who must not exceed it.
    IDENTIFIERS = "identifiers"

    #: :data:`IDENTIFIERS` plus mobile numbers, landlines and email addresses.
    CONTACT = "contact"

    @property
    def removes_identifiers(self) -> bool:
        return self in (RedactionMode.IDENTIFIERS, RedactionMode.CONTACT)

    @property
    def removes_contact(self) -> bool:
        return self is RedactionMode.CONTACT


#: What a caller gets if they do not choose. Stated as a name rather than left implicit at
#: each call site, because "what happens when nobody decided" is the question an incident asks.
#:
#: 🛑 **This is :data:`~RedactionMode.CONTACT`, which is wider than the formally adjudicated
#: scope, and that is deliberate.** `IDENTIFIERS` alone removes **0.3% of the personal data
#: detected in the charge-sheet corpus** -- 15 of 4,685 -- because that corpus is phone
#: numbers (3,773) and bank accounts (804), not citizenship numbers. On
#: ``samples/aarop-patra.pdf`` it removes 2 values and leaves all 5 mobile numbers. A
#: redact-by-default whose default leaves the largest category is a label rather than a
#: safeguard, so the default errs toward removing too much.
#:
#: ⚠️ **Provisional.** Promoting phone and email from detected to removed is the scope
#: decision in ``docs/2026-09-25-pii-redaction-pipeline/PLAN.md`` §1, which is a board/DRN
#: call. If that adjudication lands the other way, this line moves back to `IDENTIFIERS` and
#: nothing else changes -- which is the whole reason the tier is one named constant.
DEFAULT_MODE = RedactionMode.CONTACT
