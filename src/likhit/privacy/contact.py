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

⚠️ **Known limit: a label covering a list takes only the first number.** The CIAA annual
reports print their masthead as `फोन नं.: ५२६२१८०` and then `५२६२२४७` on the next line, under
the one label. ``phone_labelled`` removes the first and nothing reaches the second.
:mod:`likhit.privacy.redact` solves the same shape for passports with an explicit conjunction
(`र`, `and`, `,`), and that is deliberately **not** copied here: a phone continuation would
have to join across a bare line break, which in these documents is also what separates a
label from the column of *figures* under it. A rule that walked it would eat an audit table.

Measured over the 13 CIAA transcripts this is **2 occurrences, both of them the Commission's
own switchboard** -- institutional contact details printed in a masthead, which are closer to
provenance than to personal data. Recorded as a limit rather than closed with a pattern whose
failure mode is worse than the gap.

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
from .signals import EMAIL, LANDLINE, MOBILE, PHONE_LABELLED


@dataclass(frozen=True)
class ContactTarget:
    """One contact value, located in the text it was found in.

    Shaped to match :class:`likhit.privacy.redact.InlineTarget` -- ``start``/``end``/``kind``
    and a ``placeholder`` -- so a caller driving both can treat them as one sequence.

    ``start``/``end`` are the offsets of the **value**, as they are there. For the three
    shape-anchored patterns the value is the whole match, because there is no label to keep;
    for ``phone_labelled`` it is the ``val`` group, and the label it was found by is kept.
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
#:
#: 🛑 **``phone_labelled`` is applied last, and that position is deliberate.** It is the only
#: pattern here anchored on a label rather than on the number's own shape, so it overlaps the
#: three above wherever a Nepali number is *also* labelled -- `सम्पर्क ९८५१२३४५६७` matches
#: both it and :data:`~likhit.privacy.signals.MOBILE`. Running it last means the calibrated
#: shape patterns win those spans and the overlap guard below drops the duplicate, so this
#: rule only ever picks up what they could not see: a foreign number in an international
#: format, which has no 97/98 opening. Measured on ``samples/aarop-patra.pdf``: it adds
#: exactly the one Indian mobile on page 1 and changes nothing else.
CONTACT_PATTERNS = (
    ("mobile", MOBILE),
    ("landline", LANDLINE),
    ("email", EMAIL),
    ("phone_labelled", PHONE_LABELLED),
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
            # 🛑 The span is the `val` group where a pattern has one, never the whole match.
            # A label-anchored pattern matches `Mobile Number: 919971766006` entire; removing
            # that span would delete the label too, and this package keeps labels -- a
            # transcript that says a phone number was removed is evidence, one with the field
            # name gone is just a hole. Patterns without the group are the number itself.
            start, end = (
                match.span("val") if "val" in pattern.groupindex else match.span()
            )
            if any(
                start < seen_end and seen_start < end for seen_start, seen_end in taken
            ):
                counters[f"{kind}_refused_overlap"] += 1
                continue
            taken.append((start, end))
            value = text[start:end]
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
