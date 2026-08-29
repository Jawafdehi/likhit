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
``test_every_placeholder_is_registered`` fails otherwise.
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

ALL: Final = (
    CITIZENSHIP,
    DATE_OF_BIRTH,
    TABLE_CITIZENSHIP,
    TABLE_DATE_OF_BIRTH,
    TABLE_PERSONAL_VALUE,
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
