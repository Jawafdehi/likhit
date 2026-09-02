"""The one place the redaction placeholder vocabulary is written down.

🛑 **Why this module exists, and it is not tidiness.** The two redaction passes each
defined a module-level ``CITIZENSHIP_PLACEHOLDER`` with a *different value* --
``[REDACTED:CITIZENSHIP-NO]`` inline, ``[REDACTED:TABLE-CITIZENSHIP-NO]`` in tables. Same
name, two modules, two values, and nothing asserted they were distinct on purpose. That is
the exact shape ``tests/test_no_duplicated_definitions.py`` refuses.

🛑🛑 **The second reason is a measured defect.** A placeholder is Latin text wrapped in
square brackets, and :mod:`likhit.quality`'s ``legacy_ascii`` axis counts bracket-bearing
Latin runs as legacy-encoded Nepali leaking through. ``LEGACY_PUNCT`` contains ``[`` and
``]``, so ``[REDACTED:CITIZENSHIP-NO]`` reads as **two** legacy runs -- ``[REDACTED`` and
``NO]``. Measured on a synthetic document before this module existed:

    before redaction: verdict=clean    legacy_frac_of_doc=0.0000  legacy_runs=0
    after  redaction: verdict=garbled  legacy_frac_of_doc=0.1905  legacy_runs=12

So redacting a document made the quality instrument call it garbled, purely because the
two tools could not see each other. On the real corpus the effect was one document moving
``clean`` -> ``suspect``, small only because placeholder density is low -- it is a
systematic bias that grows with redaction scope, and scope is expected to grow.

:data:`PLACEHOLDER_PATTERN` is what :mod:`likhit.quality.normalise` strips before measuring
anything, for the same reason it strips code fences and fiscal-year spans: the marker is
structure this project inserted, not evidence about the decode.

**Adding a placeholder means adding it here**, not in the pass that emits it.
``test_every_placeholder_a_module_emits_is_registered`` fails otherwise -- but be precise
about what that guard can see, because **three** markers got past it and into a published
corpus. It scans this repository's sources, so a redaction pass living *outside* the package
is structurally invisible to it, and the OAG release has such a pass. The vocabulary this
module owns is therefore the **project's**, not this package's, which is why :data:`ALL`
registers markers that nothing here emits.
"""

from __future__ import annotations

import re
from typing import Final

#: Inline pass -- label and value in one text span (:mod:`likhit.privacy.redact`).
CITIZENSHIP: Final = "[REDACTED:CITIZENSHIP-NO]"
DATE_OF_BIRTH: Final = "[REDACTED:DATE-OF-BIRTH]"

#: Table pass -- value stored in a cell away from its label
#: (:mod:`likhit.privacy.redact_tables`). Distinct from the inline forms on purpose: a
#: header can govern thousands of cells, so which mechanism removed a value is worth
#: keeping in the output rather than only in the journal.
TABLE_CITIZENSHIP: Final = "[REDACTED:TABLE-CITIZENSHIP-NO]"
TABLE_DATE_OF_BIRTH: Final = "[REDACTED:TABLE-DATE-OF-BIRTH]"

#: Written when a cell's label context admits more than one kind, so the record cannot
#: honestly name which. Never emitted by the inline pass, which always knows its label.
TABLE_PERSONAL_VALUE: Final = "[REDACTED:TABLE-PERSONAL-VALUE]"

#: Contact details, emitted by the OAG release's contact pass and by **no module in this
#: package**. Registered anyway, and that is the point of the module rather than an oversight.
#:
#: 🛑 **These three are in a published corpus**: 287 occurrences over 104 of its 6,234
#: transcripts (178 :data:`PHONE`, 80 :data:`TABLE_PHONE`, 29 :data:`EMAIL`).
#: ``test_every_placeholder_a_module_emits_is_registered`` scans this repository for the
#: literal, so it is structurally blind to a consumer that redacts with its own pass and then
#: hands the result to :mod:`likhit.quality` -- which is exactly what that release does. Until
#: they were registered, :func:`strip_placeholders` left them in the text
#: :mod:`likhit.quality` measures, and each scored as **two** ``legacy_ascii`` runs --
#: ``[REDACTED`` and ``NO]`` -- so the audit read this project's own markers as evidence about
#: a decode. That is the defect this module's docstring records, reappearing through the one
#: hole its guard cannot cover.
#:
#: Re-auditing those 104 documents with and without the registration, the report loses:
#:
#: * ``legacy_ascii``: -574 ``legacy_runs``, -3,531 ``legacy_run_chars``, -4,647 latin chars
#: * ``spacing``: -187 ``tokens``
#: * ``structure``: -188 ``words``
#:
#: **20 of the 104 had at least half of their reported ``legacy_runs`` manufactured by these
#: markers, and one had all of it.** ⚠️ **No verdict moved, on any axis** -- the density is too
#: low to cross a threshold on this corpus, which is a reason to fix it while it is still free
#: rather than an argument that it does not matter. The bias is one-directional and grows with
#: redaction scope. It also reaches two axes the module docstring above does not mention: a
#: marker is one ``\S+`` token, so it inflates ``structure``'s word count, and ``structure``
#: refuses a document *below* 100 words.
#:
#: ⚠️ **Spelled exactly as the corpus writes them, which is not how they read.** The phone
#: markers end ``-NO`` (an abbreviated "number", matching :data:`CITIZENSHIP`) and the email
#: marker does not. A draft of this change registered ``[REDACTED:PHONE]``; that string occurs
#: **0** times in the corpus, so it would have stripped nothing and left 258 of the 287
#: occurrences in place while looking correct. Verify against the bytes, not the name.
#:
#: ⚠️ **Do not delete these as unused.** Nothing in this package imports them, so a reference
#: search finds only the registration itself, and
#: ``test_release_markers_stay_registered_without_an_emitter`` is all that stands between them
#: and a tidy-up.
PHONE: Final = "[REDACTED:PHONE-NO]"
TABLE_PHONE: Final = "[REDACTED:TABLE-PHONE-NO]"
EMAIL: Final = "[REDACTED:EMAIL]"

ALL: Final = (
    CITIZENSHIP,
    DATE_OF_BIRTH,
    TABLE_CITIZENSHIP,
    TABLE_DATE_OF_BIRTH,
    TABLE_PERSONAL_VALUE,
    PHONE,
    TABLE_PHONE,
    EMAIL,
)

#: Matches any registered placeholder.
#:
#: ⚠️ Built by alternation over :data:`ALL` rather than as a general
#: ``\[REDACTED:[A-Z-]+\]`` shape. A general pattern would also swallow a literal
#: ``[REDACTED:...]`` that arrived in a source document -- which would be a real decode
#: artifact worth reporting -- and would silently accept a typo'd placeholder that no pass
#: actually writes. Longest-first so ``TABLE-CITIZENSHIP-NO`` cannot be partly matched by a
#: shorter alternative.
PLACEHOLDER_PATTERN: Final = re.compile(
    "|".join(re.escape(marker) for marker in sorted(ALL, key=len, reverse=True))
)


def strip_placeholders(text: str, replacement: str = " ") -> str:
    """Remove every registered placeholder from ``text``.

    Replaced with a space rather than the empty string: a placeholder sits between a label
    and whatever follows it, and joining those together would manufacture the very
    run-together token that the ``spacing`` and ``legacy_ascii`` axes look for.
    """

    return PLACEHOLDER_PATTERN.sub(replacement, text)


def contains_placeholder(text: str) -> bool:
    """Has ``text`` already been through a redaction pass?"""

    return PLACEHOLDER_PATTERN.search(text) is not None
