"""Redacting a directory of transcripts, under three rules that each exist because they
were broken once.

🛑 **1. Never in place.** Reads a measured tree, writes a staging copy. The measured tree is
what the gates and the audit describe; redacting it invalidates every figure in the run
record that names it.

🛑 **2. Rewrite only what the journal names.** A document with no span is emitted as the bytes
that were read, not as the normalised text. Normalisation happens for *matching* only,
because the label patterns need composed Devanagari. Emitting the normalised form for every
document rewrote **308 PII-free documents** while the journal reported zero changes for them
-- and the counter could not see it, because ``chars_before`` was measured after normalising,
so the residual cancelled inside the net delta.

🛑 **3. The journal records shapes, never matched digits.** A record of what was removed must
be publishable beside the corpus it describes. Lengths, digit counts and separator presence
are enough to measure precision; the value itself is the thing being removed.

The redaction rules themselves are in :mod:`likhit.privacy.redact` (a label and value in one
span) and :mod:`likhit.privacy.redact_tables` (a value in a cell away from its label). This
module only walks and journals.
"""

from __future__ import annotations

import shutil
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .redact import redact as redact_inline
from .redact_tables import redact_table_text


@dataclass
class RedactionReport:
    """What a pass did, in the shape a journal is written from."""

    documents_in_tree: int = 0
    documents_changed: int = 0
    documents_nfc_differs_from_disk: int = 0
    spans_redacted: int = 0
    chars_before: int = 0
    chars_after: int = 0
    counters: Counter = field(default_factory=Counter)
    changed_documents: list[dict] = field(default_factory=list)
    span_shapes: list[dict] = field(default_factory=list)

    @property
    def net_char_delta(self) -> int:
        return self.chars_after - self.chars_before

    def as_dict(self) -> dict:
        return {
            "documents_in_tree": self.documents_in_tree,
            "documents_changed": self.documents_changed,
            "documents_nfc_differs_from_disk": self.documents_nfc_differs_from_disk,
            "spans_redacted": self.spans_redacted,
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
            "net_char_delta": self.net_char_delta,
            "counters": dict(sorted(self.counters.items())),
            "guard_refusals": {
                key: value
                for key, value in sorted(self.counters.items())
                if "refused" in key
            },
            "changed_documents": self.changed_documents,
            "span_shapes": self.span_shapes,
            "no_matched_digits_recorded": True,
        }


def redact_tree(
    source: Path,
    destination: Path | None,
    *,
    tables: bool = False,
    dry_run: bool = False,
) -> RedactionReport:
    """Redact every ``*.md`` under ``source`` into ``destination``.

    ``destination`` must not exist: refusing to overwrite is rule 1 in practice, since the
    obvious mistake is pointing the output at the input. ``dry_run`` measures without
    writing, and is the only mode in which ``destination`` may be ``None``.

    ``tables=True`` runs the table pass instead of the inline one. They are separate passes
    rather than one because the table pass reads the inline pass's placeholders and treats an
    already-redacted row as spent -- so the order matters and is the caller's to choose.
    """

    if not source.is_dir():
        raise ValueError(f"no such input tree: {source}")
    if not dry_run:
        if destination is None:
            raise ValueError("a destination is required unless dry_run is set")
        # Ordered so the in-place case gets its own message. Checking `exists()` first
        # reported "refusing to overwrite <the input tree>", which is true but tells the
        # caller nothing about what they actually did wrong.
        if destination.resolve() == source.resolve():
            raise ValueError(
                "refusing to redact in place: the measured tree is what the audit and the "
                "run record describe, and redacting it invalidates every figure that names it"
            )
        if destination.exists():
            raise ValueError(f"refusing to overwrite {destination}")

    report = RedactionReport()
    documents = sorted(source.rglob("*.md"))
    report.documents_in_tree = len(documents)

    for path in documents:
        raw = path.read_text(encoding="utf-8", errors="replace")
        normalised = unicodedata.normalize("NFC", raw)
        if normalised != raw:
            report.documents_nfc_differs_from_disk += 1

        if tables:
            redacted, targets, stats = redact_table_text(normalised)
            spans = len(targets)
            shapes = [
                {
                    "line": t.ref.line_index + 1,
                    "cell": t.ref.column_index,
                    "classification": t.classification,
                    "value_digit_count": t.shape.digit_count,
                    "value_had_separators": t.shape.had_separators,
                }
                for t in targets
            ]
        else:
            journal: list[dict] = []
            stats = Counter()
            redacted = redact_inline(normalised, journal, stats)
            spans = len(journal)
            shapes = journal
        report.counters.update(stats)

        # 🛑 Rule 2. Zero spans -> emit the bytes that were READ. Emitting `redacted` here
        # would rewrite documents the journal does not name, because NFC alone changes
        # documents this pass has nothing to say about.
        emitted = redacted if spans else raw

        # Baseline is `raw`, not the normalised text: measuring against the normalised form
        # folds the NFC residual into chars_before, where it cancels inside the net delta and
        # no counter can see it.
        report.chars_before += len(raw)
        report.chars_after += len(emitted)

        if spans:
            report.documents_changed += 1
            report.spans_redacted += spans
            report.changed_documents.append(
                {
                    "path": str(path.relative_to(source)),
                    "spans": spans,
                    "nfc_normalized": normalised != raw,
                    "char_delta": len(emitted) - len(raw),
                }
            )
            report.span_shapes.extend(shapes)

        if not dry_run:
            assert destination is not None
            out = destination / path.relative_to(source)
            out.parent.mkdir(parents=True, exist_ok=True)
            if spans:
                out.write_text(emitted, encoding="utf-8")
            else:
                # copy2, not write_text: an untouched document should be byte-identical and
                # keep its mtime, so a diff of the two trees names only what was redacted.
                shutil.copy2(path, out)

    if not dry_run:
        assert destination is not None
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix != ".md":
                out = destination / path.relative_to(source)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, out)

    return report
