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
    from its label. Run inline first -- the table pass reads the inline pass's placeholders
    and treats an already-redacted row as spent.
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

from . import placeholders
from .placeholders import (
    ALL as PLACEHOLDERS,
)
from .placeholders import (
    contains_placeholder,
    strip_placeholders,
)
from .redact import redact_inline_text
from .redact_tables import redact_table_text
from .signals import scan_text
from .tree import RedactionReport, redact_tree

__all__ = [
    "PLACEHOLDERS",
    "RedactionReport",
    "contains_placeholder",
    "placeholders",
    "redact_inline_text",
    "redact_table_text",
    "redact_tree",
    "scan_text",
    "strip_placeholders",
]
