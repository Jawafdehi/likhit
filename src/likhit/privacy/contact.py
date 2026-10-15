"""Contact details as removable spans: mobile, landline, email.

🛑 **The patterns are imported from :mod:`likhit.privacy.signals`, not restated here.** That
module already owns them, and it is calibrated: ``MOBILE`` anchors on the 97/98 opening and
digit boundaries on both sides, ``LANDLINE`` on the `0N-NNNNNNN` shape. Writing a second,
slightly different mobile pattern next to the first is exactly the failure
``tests/test_no_duplicated_definitions.py`` exists to refuse, and the one that would make the
scanner and the redactor disagree about what is present.

The difference between this module and :mod:`likhit.privacy.signals` is not the patterns, it
is the question: ``scan_text`` answers *how much is here* and returns counts with no matched
text; this answers *what exactly would be removed* and returns spans. Keeping both on one set
of patterns is what makes "the scanner found 39 mobiles, the redactor removed 39 mobiles" a
checkable statement rather than two independent guesses.

⚠️ **This is a scope expansion and it is gated.** Nothing here runs unless a caller asks for
:data:`~likhit.privacy.modes.RedactionMode.CONTACT`. See that module for why.

The placeholders these emit were already registered in :mod:`likhit.privacy.placeholders`
before any module in this package emitted them -- they were written by the OAG release's
out-of-tree contact pass, 287 occurrences across 104 of its 6,234 transcripts. Spelled exactly
as that corpus writes them: the phone marker ends ``-NO`` and the email marker does not.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from .placeholders import EMAIL as EMAIL_PLACEHOLDER
from .placeholders import PHONE as PHONE_PLACEHOLDER
from ..devanagari import ANY_DIGITS
from .signals import EMAIL, LANDLINE, MOBILE


@dataclass(frozen=True)
class ContactTarget:
    """One contact value, located in the text it was found in.

    Shaped to match :class:`likhit.privacy.redact.InlineTarget` -- ``start``/``end``/``kind``
    and a ``placeholder`` -- so a caller driving both can treat them as one sequence. The
    identifier targets keep their label and replace only the value; a contact value **is** the
    whole match, because there is no label to keep.
    """

    start: int
    end: int
    kind: str
    digit_count: int
    had_separators: bool

    @property
    def placeholder(self) -> str:
        return EMAIL_PLACEHOLDER if self.kind == "email" else PHONE_PLACEHOLDER


#: ``(kind, pattern)`` in the order the pass applies them. Landline before mobile would not
#: change the result -- the two shapes cannot both match the same span, since a landline
#: requires a literal `-` where a mobile admits only a space -- but the order is fixed so the
#: journal's row order is stable across runs.
CONTACT_PATTERNS = (
    ("mobile", MOBILE),
    ("landline", LANDLINE),
    ("email", EMAIL),
)

#: Built from the shared class rather than spelled out: this corpus mixes Devanagari and
#: ASCII digits inside one value, and a second local definition of "a digit" is how the
#: shape a journal records drifts from the shape the pattern matched.
_CONTACT_DIGIT = re.compile(f"[{ANY_DIGITS}]")
_CONTACT_SEPARATOR = re.compile(r"[-\s.]")


def iter_contact_targets(
    text: str, stats: Counter | None = None
) -> Iterator[ContactTarget]:
    """Every contact value in ``text``, as a span.

    🛑 **Overlapping matches are dropped rather than merged**, and the first one wins. A span
    that overlaps one already yielded means two patterns read the same digits as different
    things, and redacting both would apply one replacement inside another's span -- which
    produces a corrupted placeholder rather than a redaction. Measured on the 13 CIAA
    transcripts: this drops nothing, so it is a guard against a shape that does not occur
    rather than a behaviour anyone depends on.

    ⚠️ The overlap check is quadratic in the number of targets, and that was measured rather
    than assumed before being left alone: **4,000 contact values in one document take 0.41 s**,
    against 0.03 s for 1,000. The whole charge-sheet corpus holds 3,773 phone numbers *across
    every document*, so a single document never approaches the figure where this matters. If
    one ever does, the fix is an interval check against the last accepted span rather than
    all of them -- noted here so the next reader measures instead of guessing.
    """

    counters: Counter = Counter() if stats is None else stats
    taken: list[tuple[int, int]] = []
    for kind, pattern in CONTACT_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(
                start < seen_end and seen_start < end for seen_start, seen_end in taken
            ):
                counters[f"{kind}_refused_overlap"] += 1
                continue
            taken.append((start, end))
            value = match.group(0)
            # `_targeted`, not `_redacted`: this function selects, it does not remove. The
            # distinction is not pedantry -- `redact`'s `{kind}_redacted` counts replacements
            # actually written, and a selector that incremented the same key would inflate
            # the one number a reader uses to check that what was found was what was removed.
            counters[f"{kind}_targeted"] += 1
            yield ContactTarget(
                start=start,
                end=end,
                kind=kind,
                digit_count=len(_CONTACT_DIGIT.findall(value)),
                had_separators=bool(_CONTACT_SEPARATOR.search(value)),
            )
