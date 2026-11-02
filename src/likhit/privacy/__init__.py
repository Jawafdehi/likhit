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

from collections.abc import Mapping
from types import MappingProxyType

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
#: ⚠️ **Read-only.** This feeds a publishing gate -- a consumer asks it which classes its
#: tier should have zeroed before deciding whether text is safe to publish. A plain dict
#: exported from a package is mutable by anything that imports it, and a single stray
#: ``REMOVAL_TIERS[k] = ...`` anywhere in a process would silently change what every later
#: gate checks, with no diff to find it in.
def _build_removal_tiers() -> Mapping[str, RedactionMode]:
    """Collect every removable kind from the rules that define them.

    🛑 **A function rather than a module-level loop.** The loop form leaks its control
    variables into the package namespace and needs a trailing ``del`` to clean them up --
    which raises ``NameError`` *at import time* if either collection is ever empty, because
    the names the ``del`` lists were never bound. An import-time crash guarding against
    nothing is a worse failure than the tidiness it buys.
    """

    tiers: dict[str, RedactionMode] = {}

    def offer(kind: str, minimum: RedactionMode) -> None:
        # 🛑 **Narrowest wins, and it has to be applied to BOTH sources.** A kind can carry
        # several rules -- `date_of_birth` has four -- and nothing stops one appearing in
        # the inline rules *and* in the contact pass. The question this map answers is "from
        # which tier onwards is any of this kind removed?", so the lowest rank is the only
        # correct answer.
        #
        # ⚠️ An earlier form used `setdefault` for the contact half, which keeps whatever
        # the inline half wrote. The two sets do not overlap today, so it was latent -- but
        # it failed in the unsafe direction: an inline rule for a contact kind at a wider
        # tier would make this map claim the class is not removed at `CONTACT` when the
        # contact pass removes it there, and a gate would then skip checking its residue.
        seen = tiers.get(kind)
        if seen is None or minimum.rank < seen.rank:
            tiers[kind] = minimum

    for kind, _pattern, _marker, minimum in redact.INLINE_RULES:
        offer(kind, minimum)
    for kind, _pattern in contact.CONTACT_PATTERNS:
        # The contact pass is gated as a whole by `mode.removes_contact`, so every kind it
        # yields enters at `CONTACT`. Read off the pass rather than listed, so a fifth
        # contact pattern is covered the day it is added.
        offer(kind, RedactionMode.CONTACT)
    return MappingProxyType(tiers)


REMOVAL_TIERS: Mapping[str, RedactionMode] = _build_removal_tiers()

__all__ = [
    "DEFAULT_MODE",
    "PLACEHOLDERS",
    "REMOVAL_TIERS",
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
