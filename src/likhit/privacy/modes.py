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
    """The removal tiers, in increasing order of what they take out.

    ⚠️ ``__str__`` returns the bare value, and that is load-bearing rather than cosmetic.
    ``argparse`` renders both its ``choices`` list and its "invalid choice" error through
    ``str()``, and a ``str``-valued ``Enum`` inherits ``Enum.__str__`` -- so ``--help``
    advertised ``{RedactionMode.NONE,RedactionMode.IDENTIFIERS,...}``, which are exactly the
    strings the parser **rejects**. The values that do work were never shown.
    """

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

    #: :data:`CONTACT` plus the numbers on a person's accounts and papers -- bank account
    #: and passport -- but **not age**. Every identifier this package removes, and no
    #: attribute.
    #:
    #: 🛑 **This tier exists because `age` is the one rule whose precision is a property of
    #: the corpus rather than of the pattern.** It carries its own measured number, as
    #: :mod:`likhit.privacy.modes` requires of every tier:
    #:
    #: - **CIAA annual reports, 3,854 pages: 28 `age` removals, 28 of them wrong.** Twelve
    #:   are the statutory tenure age of CIAA commissioners -- a constitutional provision,
    #:   not a person's age. The rest are the age limits whose breach **is** the finding:
    #:   `[AGE] वर्ष उमेर नाघेका व्यक्तिलाई … नियुक्त`, `[AGE] वर्ष उमेर हदका कारण … अवकाश`.
    #:   Removing them takes the substance out of an accountability record.
    #: - **Auditor General shelf: 76 `age` removals, effectively all statutory retirement
    #:   thresholds** (`कर्मचारीको उमेर ५८ वर्ष पुरा`).
    #:
    #: On the same CIAA pages `bank_account` removes 49, and reading the possessive in front
    #: of each shows they are substantially real: the accounts of named spouses, a mother, a
    #: brother and daughters caught in asset-seizure findings -- people who are frequently
    #: not the accused. Dropping to :data:`CONTACT` to escape `age` would publish all of
    #: those. So the two cannot be traded against each other, and the tier that separates
    #: them is the only configuration that fits this corpus.
    #:
    #: ⚠️ **Not a licence to skip `age` generally.** On a charge sheet a defendant's age is
    #: ordinary personal data and :data:`STRICT` remains right there. This tier is for a
    #: corpus whose ages are statutory, which is a judgement about documents, not about the
    #: rule.
    ACCOUNTS = "accounts"

    #: Everything this package can remove: :data:`ACCOUNTS` plus age.
    #:
    #: ⚠️ **Not PAN.** A PAN is a business registration number, public by design; it is
    #: detected and disclosed, never removed. See :data:`likhit.privacy.redact.INLINE_RULES`.
    #:
    #: ⚠️ **"Everything it can remove" is not "all personal data", and the gap is names.**
    #: A person's name is the most identifying thing in these documents and nothing here
    #: removes one. That is not an oversight to be closed with another pattern -- see the
    #: module docstring.
    STRICT = "strict"

    def __str__(self) -> str:
        return self.value

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

    @property
    def removes_accounts(self) -> bool:
        return self.removes(RedactionMode.ACCOUNTS)


#: The tier order. Separate from the enum's declaration order because `str`-valued enums have
#: no inherent ordering and comparing them as strings would sort `contact` before
#: `identifiers`, silently making the wider tier the narrower one.
_ORDER: tuple[RedactionMode, ...] = (
    RedactionMode.NONE,
    RedactionMode.IDENTIFIERS,
    RedactionMode.CONTACT,
    RedactionMode.ACCOUNTS,
    RedactionMode.STRICT,
)


#: What a caller gets if they do not choose. Stated as a name rather than left implicit at
#: each call site, because "what happens when nobody decided" is the question an incident asks.
#:
#: 🛑 **This is :data:`~RedactionMode.STRICT`, which is wider than the formally adjudicated
#: scope, and that is deliberate.** `IDENTIFIERS` alone removes **0.3% of the personal data
#: detected in the charge-sheet corpus** -- 15 of 4,685 -- because that corpus is phone
#: numbers (3,773) and bank accounts (804), not citizenship numbers. On
#: ``samples/aarop-patra.pdf`` it removes 2 values and leaves all 5 mobile numbers. A
#: redact-by-default whose default leaves the largest category is a label rather than a
#: safeguard, so the default errs toward removing too much -- accepting the
#: bank-account-as-evidence cost recorded on
#: :data:`likhit.privacy.placeholders.BANK_ACCOUNT`.
#:
#: 🛑 **The line that is NOT drawn here is PAN**, and it is the one case where "remove
#: everything detectable" was the wrong instinct. A PAN identifies a *business*, is public by
#: design, and is frequently the only handle a reader has to tie a procurement finding to a
#: registered contractor. Removing it protects nobody and costs the reader the check. So the
#: default is "remove the personal details" rather than "remove every identifier", and the
#: distinction is between data about a *person* and data about a *company*.
DEFAULT_MODE = RedactionMode.STRICT
