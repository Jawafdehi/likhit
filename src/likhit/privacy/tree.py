"""Redacting a directory of transcripts, under four rules that each exist because they
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

🛑 **4. Rescan every document redacted, and refuse on residue.** A selected cell that survives
its own redaction means the pass disagrees with itself. This caught a real defect: removing
one of two ambiguous values in a row made the survivor look unique on a second pass, so a
rerun found a target in output the pass had just written -- one document, ``11862``, one cell.
The guard that fixed it lives in :mod:`likhit.privacy.redact_tables`; **this is the detector
that found it**, and it works at corpus grain where a unit test cannot. It did not come across
in the first version of this module, so the fix was here without the check that produced it.

The redaction rules themselves are in :mod:`likhit.privacy.redact` (a label and value in one
span) and :mod:`likhit.privacy.redact_tables` (a value in a cell away from its label). This
module only walks, rescans and journals.
"""

from __future__ import annotations

import shutil
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .redact import redact as redact_inline
from .redact_tables import redact_table_text
from .redact_tables import scan as scan_table_targets


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
    #: Documents this pass could not process, with the error. A sweep over a corpus should
    #: report a bad document rather than lose the other six thousand results -- and a document
    #: that cannot be read or redacted is itself a finding about the tree.
    failed_documents: list[dict] = field(default_factory=list)

    @property
    def net_char_delta(self) -> int:
        return self.chars_after - self.chars_before

    def as_dict(self) -> dict:
        return {
            "documents_in_tree": self.documents_in_tree,
            "documents_changed": self.documents_changed,
            "documents_failed": len(self.failed_documents),
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
            "failed_documents": self.failed_documents,
            "span_shapes": self.span_shapes,
            "no_matched_digits_recorded": True,
        }


def _redact_one(text: str, *, tables: bool) -> tuple[str, list[dict], Counter]:
    """One document's redaction: ``(redacted, span shapes, counters)``.

    The two passes report their spans differently -- the inline one appends dicts to a
    journal, the table one returns typed targets -- so this is where that is flattened, and
    the flattening is the only reason the walker below need not care which pass it drives.
    """

    if tables:
        redacted, targets, stats = redact_table_text(text)
        shapes = [
            {
                "line": target.ref.line_index + 1,
                "cell": target.ref.column_index,
                "classification": target.classification,
                "value_digit_count": target.shape.digit_count,
                "value_had_separators": target.shape.had_separators,
            }
            for target in targets
        ]
        return redacted, shapes, stats

    journal: list[dict] = []
    stats: Counter = Counter()
    redacted = redact_inline(text, journal, stats)
    return redacted, journal, stats


def _residual_targets(text: str, *, tables: bool) -> int:
    """How many selected cells survive their own redaction. Rule 4's measurement.

    Only the table pass has a rescan worth running: its selection depends on what else is in
    the row, so removing one value can change what a second pass sees. The inline pass
    replaces the value inside the match it just made, and its placeholder carries no digits,
    so it cannot satisfy a label-plus-digits pattern and a rescan there is structurally zero.
    """

    if not tables:
        return 0
    residual, _ = scan_table_targets(text)
    return len(residual)


def redact_tree(
    source: Path,
    destination: Path | None,
    *,
    tables: bool = False,
    dry_run: bool = False,
) -> RedactionReport:
    """Redact every ``*.md`` under ``source`` into ``destination``.

    ``destination`` must not exist, and must not be ``source``. ``dry_run`` measures without
    writing, and is the only mode in which ``destination`` may be ``None``.

    ``tables=True`` runs the table pass instead of the inline one.

    ⚠️ They are separate passes because they answer different questions -- a label and value in
    one span versus a value in a cell away from its label -- **not** because one reads the
    other's output. An earlier version of this docstring said the table pass treats a row the
    inline pass touched as spent, and used that as the justification for this API's shape. It
    is false: the row-spent guard reads only ``TABLE_*`` markers, and
    ``test_inline_placeholder_does_not_hide_a_separate_table_value`` asserts the opposite of
    the claim. One cell can never be both passes' target anyway -- a table candidate must be
    digits-and-separators only, and an inline target must contain its label -- so neither pass
    can shrink the other's candidate set. Order is the caller's choice and carries no
    correctness requirement. Run **both** to cover identifiers held inline and in table cells.

    A document that cannot be read or redacted is recorded in ``failed_documents`` and
    skipped; it is **not** copied through, because emitting the unredacted bytes of a file
    whose redaction crashed is the one outcome worse than stopping.
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
        relative = str(path.relative_to(source))

        # 🛑 Per-document isolation, for the reason `quality.tree.audit_document` gives: one
        # bad file in a corpus sweep must not lose the rest. It matters MORE here, because
        # this walker WRITES -- an exception partway through leaves a half-populated
        # destination, and `destination.exists()` then refuses the retry, so an operator has to
        # delete a tree that already holds real redacted output. The two walkers took opposite
        # positions on the same question and the one handling PII was the fragile one.
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            normalised = unicodedata.normalize("NFC", raw)
            redacted, shapes, stats = _redact_one(normalised, tables=tables)
            spans = len(shapes)
            # 🛑 Rule 4, and before anything is written.
            if spans and _residual_targets(redacted, tables=tables):
                raise ValueError(
                    "selected cells remain after redaction; the pass disagrees with itself "
                    "about this document"
                )
        except (OSError, AssertionError, ValueError) as exc:
            # `apply_targets` and `scan` assert on "target value changed", "target column
            # vanished" and "candidate shape disagrees". Those are findings about the document,
            # not reasons to abandon the sweep.
            report.failed_documents.append(
                {"path": relative, "error": f"{type(exc).__name__}: {exc}"}
            )
            report.counters["documents_failed"] += 1
            continue

        report.counters.update(stats)
        if normalised != raw:
            report.documents_nfc_differs_from_disk += 1

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
                    "path": relative,
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
