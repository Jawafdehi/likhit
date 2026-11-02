"""Personal-data detection and redaction for Nepali transcripts.

Installed with ``likhit[privacy]``. See that extra's note in ``pyproject.toml`` for what it
does and does not gate.

Three surfaces, and the split between them is the point:

:func:`scan_text`
    What personal data is *present*. Reports counts and shapes, **never a match**, so its
    output is publishable next to the corpus it describes.
:func:`redact_inline_text` / :func:`redact_table_text`
    What is *removed*, which is deliberately narrower than what is found. The inline pass
    handles a label and value in one span; the table pass handles a value in a cell away
    from its label.

    ⚠️ The two passes are **independent**: the table pass's row-spent guard reads its own
    `TABLE_*` markers, not the inline pass's, and
    `test_inline_placeholder_does_not_hide_a_separate_table_value` pins that. Running inline
    first is conventional, not required. An earlier version of this docstring claimed the
    coupling existed -- measured, a row already carrying `[REDACTED:CITIZENSHIP-NO]` yields 1
    table target and 0 refusals, while one carrying `[REDACTED:TABLE-CITIZENSHIP-NO]` yields 0
    targets and a refusal.
:mod:`likhit.privacy.placeholders`
    The marker vocabulary, which :mod:`likhit.quality` strips before measuring anything.
    Registering a marker in one place is what stops a redaction from being scored as a
    decode defect.

🛑 **The detectors and redactors write nothing.** The tree-level discipline -- never redact
in place, rewrite only what the journal names, normalise for matching only -- lives in
:mod:`likhit.privacy.tree`, separate on purpose; each of those three rules exists because it
was once broken, and each is documented there with what it cost.
"""

from __future__ import annotations

from . import contact, placeholders, redact
from .contact import ContactTarget, iter_contact_targets
from .modes import DEFAULT_MODE, RedactionMode
from .pdf import PageClass, PdfRedactionReport, classify_text_layer, redact_pdf
from .placeholders import (
    ALL as PLACEHOLDERS,
)
from .placeholders import (
    contains_placeholder,
    strip_placeholders,
)
from .redact import InlineTarget, iter_inline_targets, redact_inline_text
from .redact_tables import redact_table_text
from .signals import scan_text
from .text import RedactedText, redact_text
from .tree import RedactionReport, redact_tree

#: Every kind this package can remove, mapped to the **narrowest tier that removes it**.
#:
#: 🛑 **Derived from the rules, never restated.** A consumer that needs to ask "does my
#: configured tier zero this class out?" -- which is what a *gate* in front of a publishing
#: write path does -- otherwise keeps its own hand-written copy of the rule-to-tier map, and
#: that copy has no way to notice when a rule moves between tiers here. It does not fail
#: loudly either: the stale copy simply stops gating a class that is still being removed, or
#: starts gating one that is not, and the write path's residue check quietly measures the
#: wrong set. Exporting the map is what makes the two unable to disagree, for the same reason
#: :mod:`likhit.privacy.contact` imports its patterns from :mod:`likhit.privacy.signals`
#: rather than writing them out a second time.
#:
#: ⚠️ **`pan` is absent, and that is the contract.** A PAN is detected and disclosed by
#: ``signals.LABELLED["pan"]`` but removed by no tier, so it has no entry here. A caller
#: computing "classes my tier should have zeroed" from this map therefore excludes it
#: automatically, instead of needing its own exception list.
REMOVAL_TIERS: dict[str, RedactionMode] = {}
for _kind, _pattern, _marker, _minimum in redact.INLINE_RULES:
    # A kind can carry several rules -- `date_of_birth` has four -- and they could in
    # principle sit at different tiers. The narrowest wins: the question this map answers is
    # "from which tier onwards is any of this kind removed?".
    _seen = REMOVAL_TIERS.get(_kind)
    if _seen is None or _minimum.rank < _seen.rank:
        REMOVAL_TIERS[_kind] = _minimum
for _kind, _pattern in contact.CONTACT_PATTERNS:
    # The contact pass is gated as a whole by `mode.removes_contact`, so every kind it
    # yields enters at `CONTACT`. Read off the pass rather than listed, so a fifth contact
    # pattern is covered the day it is added.
    REMOVAL_TIERS.setdefault(_kind, RedactionMode.CONTACT)
del _kind, _pattern, _marker, _minimum, _seen

__all__ = [
    "REMOVAL_TIERS",
    "DEFAULT_MODE",
    "PLACEHOLDERS",
    "ContactTarget",
    "InlineTarget",
    "PageClass",
    "PdfRedactionReport",
    "RedactedText",
    "RedactionMode",
    "RedactionReport",
    "classify_text_layer",
    "contains_placeholder",
    "iter_contact_targets",
    "iter_inline_targets",
    "placeholders",
    "redact_inline_text",
    "redact_pdf",
    "redact_table_text",
    "redact_text",
    "redact_tree",
    "scan_text",
    "strip_placeholders",
]
