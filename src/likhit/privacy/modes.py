"""What a caller is asking for when they ask for redaction.

🛑 **A mode names a policy tier, never a pass.** ``likhit-redact redact --tables`` names an
implementation detail, which is why removing everything from a tree takes two invocations
chained through a staging directory -- and why a caller has to know that the inline and table
passes exist at all. Tiers are the contract; which passes run is this package's problem.

🛑🛑 **Ordered tiers, not a set of flags.** Precision here is measured per removal rule against
a real corpus, and this project's redaction history is 572 real hits against **11,215
non-secret values clobbered**. A flag set lets every caller invent a combination nobody
measured; four named tiers are four configurations that can each carry a number.

⚠️ :data:`RedactionMode.CONTACT` is **a scope change and is deliberately not the default.**
Promoting phone and email from detected to removed is adjudicated work -- see
``docs/2026-09-25-pii-redaction-pipeline/PLAN.md`` §1 in the meta-repo -- so it is available to
a caller who has made that decision and never applied to one who has not. :data:`IDENTIFIERS`
is exactly today's behaviour.
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
    #: personal and of no research value. Today's scope, and the default everywhere.
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
DEFAULT_MODE = RedactionMode.IDENTIFIERS
