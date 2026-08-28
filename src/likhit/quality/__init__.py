"""Deterministic quality audit for Nepali transcripts.

Installed with ``likhit[quality]``. See that extra's note in ``pyproject.toml`` for what it
does and does not gate.

**Why several axes rather than one score.** A whole-document Devanagari *ratio* is not
sufficient: a legacy-Kalimati report recovered by this package scored ``ratio=0.977`` while
its first line was untranslated legacy bytes. Localised garble hides inside a good global
score, so a transcript is measured on independent axes and **the worst one wins**. Averaging
lets seven clean axes bury an eighth that is making a positive claim about damage -- which is
how a real defect stayed invisible on this corpus while 6,004 documents scored clean.

:func:`audit_text` is the entry point, and it is pure: text in, verdicts out. No filesystem,
no corpus schema, no sidecar. A caller with metadata joins it on afterwards.

🛑 **This package measures what the extractor produced, and the extractor is in the same
package. That is the point.** Two instruments here count the same three matra-damage shapes
-- ``converters.nepali_pdf._markdown_quality_score`` to choose which candidate transcript
ships, and :func:`likhit.quality.axes.check_matra_damage` to grade what shipped. They lived in
separate repositories and drifted, and the grader's copy was the wrong one. Both now read
:mod:`likhit.devanagari`. A grader that disagrees with the chooser about what damage is will
happily accept a transcript it then condemns.
"""

from __future__ import annotations

from .axes import RANK, audit_text
from .normalise import normalise_for_audit, split_pages
from .page_refusal import REFUSAL, classify_page, is_refusal_page
from .tree import assess_tree, audit_document, audit_tree, find_transcripts

__all__ = [
    "RANK",
    "REFUSAL",
    "assess_tree",
    "audit_document",
    "audit_text",
    "audit_tree",
    "classify_page",
    "find_transcripts",
    "is_refusal_page",
    "normalise_for_audit",
    "split_pages",
]
