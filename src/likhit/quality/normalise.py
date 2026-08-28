"""What a transcript has to have removed from it before any axis measures it.

🛑 **This module exists because two functions called ``strip_page_anchors``, with the same
signature, returned different things.** ``likhit.renderers.markdown.strip_page_anchors``
removes the anchors. The audit's copy removed them *and collapsed the blank runs they left
behind*. Both behaviours are defensible for their own caller; two definitions of one name is
not. The resolution keeps the renderer's public function untouched -- consumers on PyPI
depend on it -- and gives the audit's requirement its own name here.

⚠️ **The reason the audit's copy gave for collapsing was wrong, and it is worth recording
rather than repeating.** Its docstring said the ``spacing`` axis "reads whitespace ratios, so
leaving ``\\n\\n\\n\\n`` where an anchor stood would trade one artifact for another".
:func:`likhit.quality.axes.check_spacing` reads a token-length distribution over ``\\S+``
tokens, and a blank run contributes no tokens. Measured on 624 documents, 622 of which leave
a blank run when their anchors go: **collapsing changes zero document verdicts and zero axis
verdicts.** The collapse is kept anyway -- it is what the published corpus was measured with,
and making the normalised text canonical is worth a no-op -- but it is **inert on the current
axes**, not load-bearing, and a future reader should not infer a calibration that is not
there.

What gets removed, and what is actually known about each:

``page anchors``
    ``<!-- likhit:page N -->`` is inserted by this package. Its Latin text scales with page
    count, so scoring it inflates the Latin ratio that ``legacy_ascii`` keys on and
    manufactures regressions against transcripts produced before anchors existed. This one
    matters.
``the blank runs anchors leave``
    Inert on the current axes -- see above. Kept for canonicalisation, not for a verdict.
``redaction placeholders``
    ``[REDACTED:...]`` is Latin text in square brackets, the exact shape ``legacy_ascii``
    treats as legacy-encoded Nepali. **This one matters and is measured**: on a synthetic
    document it took the verdict from ``clean`` to ``garbled``
    (``legacy_frac_of_doc`` 0.0000 -> 0.1905), and across the corpus's 500
    redaction-affected documents it moved one from ``clean`` to ``suspect`` before this
    stripping existed and none after. See :mod:`likhit.privacy.placeholders`.
"""

from __future__ import annotations

import re
from typing import Final

from ..privacy.placeholders import strip_placeholders
from ..renderers.markdown import PAGE_ANCHOR_PATTERN, strip_page_anchors

#: Three or more newlines. Collapsed to a paragraph break rather than removed, so the
#: document keeps its block structure.
_BLANK_RUN_PATTERN: Final = re.compile(r"\n{3,}")


def normalise_for_audit(text: str) -> str:
    """``text`` with this package's own markers removed, ready to be measured.

    Order matters: placeholders go before the blank-run collapse, because a placeholder
    occupying a line of its own leaves a blank run when it goes.
    """

    text = strip_placeholders(text)
    text = strip_page_anchors(text)
    return _BLANK_RUN_PATTERN.sub("\n\n", text).strip()


def split_pages(text: str) -> tuple[str, dict[int, str]]:
    """Preamble before the first anchor, then each anchor's body by page number.

    Anchor lines themselves are not part of any body, so re-joining reproduces the
    document exactly -- ``test_splitting_pages_is_lossless`` holds that.

    Relocated here from the corpus OCR-merge module, where it was 15 self-contained lines
    that nothing else in this package needed. Importing it from there dragged in the whole
    transcription pipeline -- 4,871 lines across seven modules, including a network client --
    because that module imports the pipeline at module scope. Moving the function cut the
    chain; nothing else came with it.
    """

    matches = list(PAGE_ANCHOR_PATTERN.finditer(text))
    if not matches:
        return text, {}
    preamble = text[: matches[0].start()]
    bodies: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        bodies[int(match.group(1))] = text[match.end() : end]
    return preamble, bodies
