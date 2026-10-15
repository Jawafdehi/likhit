"""One call that redacts a string to a mode: :func:`redact_text`.

🛑 **Why this exists rather than callers driving the passes themselves.** Removing everything
from one transcript currently means calling :func:`~likhit.privacy.redact.redact_inline_text`
and :func:`~likhit.privacy.redact_tables.redact_table_text` and knowing that neither sees the
other's output, and that omitting the table pass leaves table-held identifiers in place. Every
caller that learns that is a caller that can get it wrong, and the README has to keep saying
"`--tables` is not optional in practice". A tier is the contract; which passes implement it is
this package's problem.

The passes run inline → tables → contact. Order is **not** a correctness requirement between
the first two -- they cannot shrink each other's candidate set, and
:mod:`likhit.privacy.tree` documents the measurement -- but contact runs last on purpose: it
matches bare digit runs rather than a label, so running it against text the first two have
already reduced means it can only ever see what they left. The placeholders they write carry
no digits and no ``@``, so none of the contact patterns can match one.

⚠️ **Known gap: the table pass covers only citizenship and date of birth.**
:func:`likhit.privacy.redact_tables.scan` decides a cell's kind from
``_label_kinds``, which knows those two and nothing else, so a PAN, bank account or age held
in a table *cell away from its label* is not removed even at
:data:`~likhit.privacy.modes.RedactionMode.STRICT`. The inline pass still catches every one
of them that sits beside its own label. Measured over the 13 CIAA transcripts: **zero**
occurrences of the uncovered shape, which is why this is recorded as a limit rather than
closed with machinery nothing exercises -- extending the table pass means extending its
per-row disambiguation and row-spent guards, whose behaviour is calibrated.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .contact import iter_contact_targets
from .modes import DEFAULT_MODE, RedactionMode
from .redact import redact as _redact_inline
from .redact_tables import redact_table_text


@dataclass
class RedactedText:
    """The result of one document's redaction."""

    text: str
    mode: RedactionMode
    #: One row per removed value: kind and shape, never the value itself. Same rule as
    #: :mod:`likhit.privacy.tree` -- a record of what was removed has to be publishable
    #: beside the thing it was removed from.
    journal: list[dict] = field(default_factory=list)
    counters: Counter = field(default_factory=Counter)

    @property
    def values_removed(self) -> int:
        return len(self.journal)

    @property
    def changed(self) -> bool:
        return bool(self.journal)


def _apply_contact(text: str, journal: list[dict], stats: Counter) -> str:
    """Replace every contact value with its placeholder.

    🛑 **Applied right-to-left.** Each replacement changes the length of the string, so
    rewriting left-to-right invalidates every offset after the first one -- a value would be
    cut at the wrong place and the placeholder spliced into the middle of the next one.
    Working backwards means an offset is only ever used before anything after it has moved.
    """

    targets = sorted(iter_contact_targets(text, stats), key=lambda t: t.start)
    for target in reversed(targets):
        text = text[: target.start] + target.placeholder + text[target.end :]
    journal.extend(
        {
            "kind": target.kind,
            "label_kept": False,
            "value_chars_removed": target.end - target.start,
            "value_digit_count": target.digit_count,
            "value_had_separators": target.had_separators,
        }
        for target in targets
    )
    return text


def redact_text(text: str, *, mode: RedactionMode = DEFAULT_MODE) -> RedactedText:
    """Redact ``text`` to ``mode``, running whichever passes that tier needs.

    ``mode=none`` returns the input unchanged, and that is a legitimate answer rather than an
    error -- unlike :func:`likhit.privacy.pdf.redact_pdf`, which refuses it, because producing
    a *file* named as a redacted derivative that is byte-identical to its source is a
    different and worse thing than a function returning its argument.
    """

    if mode is RedactionMode.NONE:
        return RedactedText(text=text, mode=mode)

    journal: list[dict] = []
    stats: Counter = Counter()

    redacted = _redact_inline(text, journal, stats, mode)

    redacted, table_targets, table_stats = redact_table_text(redacted)
    stats.update(table_stats)
    journal.extend(
        {
            "kind": target.classification,
            "label_kept": False,
            "value_chars_removed": None,
            "value_digit_count": target.shape.digit_count,
            "value_had_separators": target.shape.had_separators,
        }
        for target in table_targets
    )

    if mode.removes_contact:
        redacted = _apply_contact(redacted, journal, stats)

    return RedactedText(text=redacted, mode=mode, journal=journal, counters=stats)
