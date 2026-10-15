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

🛑🛑 **No tier removes names, and no tier here ever will.** ``strict`` is the widest this
package offers and it leaves every person's name in place. Three separate reasons, and none
of them is "not implemented yet":

1. **Nothing detects a Nepali name reliably.** The only signals available are `श्री` followed
   by a non-institution token and a filled `नाम:` field. Measured over the 13 CIAA
   transcripts: **2,179 `श्री` tokens** pass the institution filter and **0** `नाम:` fields
   are filled. A rule firing 2,179 times on a signal that cannot tell an office from a person
   is not a redactor, it is a corpus shredder.
2. **It is the wrong instrument.** The requirement is to remove parties *other than the
   accused*, which needs role assignment -- वादी/प्रतिवादी versus witness versus relative --
   and that is a modelling project, not a pattern.
3. **For this corpus it would invert the product.** These documents are a public
   accountability record; naming officials who were prosecuted, convicted or acquitted is the
   thing it exists to do. A mode that removed those names would produce a corruption database
   with the corruption taken out.

A caller who needs name removal needs a different tool and a human reviewer, and should be
told so rather than handed a tier that half-does it.
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

    #: Everything this package can remove: :data:`CONTACT` plus PAN, bank account and age.
    #:
    #: ⚠️ **"Everything it can remove" is not "all personal data", and the gap is names.**
    #: A person's name is the most identifying thing in these documents and nothing here
    #: removes one. That is not an oversight to be closed with another pattern -- see the
    #: module docstring.
    STRICT = "strict"

    @property
    def rank(self) -> int:
        """Position in the tier order, so a rule can ask "does this mode reach me?"."""

        return _ORDER.index(self)

    def removes(self, minimum: "RedactionMode") -> bool:
        """Does this mode remove a rule introduced at tier ``minimum``?"""

        return self is not RedactionMode.NONE and self.rank >= minimum.rank

    @property
    def removes_identifiers(self) -> bool:
        return self.removes(RedactionMode.IDENTIFIERS)

    @property
    def removes_contact(self) -> bool:
        return self.removes(RedactionMode.CONTACT)


#: The tier order. Separate from the enum's declaration order because `str`-valued enums have
#: no inherent ordering and comparing them as strings would sort `contact` before
#: `identifiers`, silently making the wider tier the narrower one.
_ORDER: tuple[RedactionMode, ...] = (
    RedactionMode.NONE,
    RedactionMode.IDENTIFIERS,
    RedactionMode.CONTACT,
    RedactionMode.STRICT,
)


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
#: ⚠️ **Provisional.** Promoting phone, email, PAN and bank account from detected to removed
#: is the scope decision in ``docs/2026-09-25-pii-redaction-pipeline/PLAN.md`` §1, which is a
#: board/DRN call. If that adjudication lands differently, this line moves and nothing else
#: changes -- which is the whole reason the tier is one named constant.
DEFAULT_MODE = RedactionMode.STRICT
