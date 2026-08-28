"""Auditing a directory of transcripts, and refusing to call an empty one clean.

:func:`likhit.quality.audit_text` is the measurement. This is the part that finds documents,
spreads the work, and -- most importantly -- declines to report a verdict it has not earned.

🛑 **The guard in :func:`assess_tree` is the reason this module is not a one-line loop.**
``Path.rglob`` returns an empty iterator and raises nothing for a missing path, a plain file,
a dangling symlink and an unreadable directory alike. Without an explicit check, auditing a
path that does not exist writes a report of zero records and exits successfully -- and a
release band read off an audit of zero documents is not a clean band, it is no measurement at
all. That has happened on this corpus.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .axes import audit_text

#: Signature of the optional metadata hook. It receives the transcript's path and returns
#: whatever a caller wants joined onto that document's row.
Enricher = Callable[[Path], dict]


def assess_tree(
    root: Path,
    transcripts: int,
    *,
    min_transcripts: int | None = None,
) -> tuple[bool, str]:
    """Is an audit of this tree evaluable at all? Returns ``(ok, why)``.

    Three distinct ways this used to read as a pass, each of which has to be named
    separately because the symptom is identical -- a report with no records and exit 0:

    * **``root`` is not a directory.** Missing, a plain file, a dangling symlink, or
      unreadable: ``rglob`` treats them all as "no matches".
    * **Vacuous.** The tree exists and holds no ``*.md`` -- a conversion that created its
      output directory and then died, which a plain ``[ -d "$TREE" ]`` cannot tell from a
      finished tree.
    * **Below the caller's floor.** Nothing intrinsic to a tree separates a 2-document
      fixture from a 6,000-document corpus, so only the caller can state the expected scale.
    """

    if not root.is_dir():
        kind = "is not a directory" if root.exists() else "does not exist"
        return False, (
            f"{root} {kind}; no transcript was audited. rglob() on a missing path yields "
            "nothing and raises nothing, so this would otherwise have written a 0-record "
            "report and exited 0"
        )
    if transcripts == 0:
        return False, (
            f"{root} exists and contains no *.md file; a verdict band read off an audit of "
            "zero documents is not a clean band, it is no measurement at all"
        )
    if min_transcripts is not None and transcripts < min_transcripts:
        return False, (
            f"{root} holds {transcripts:,} transcripts, below the stated floor of "
            f"{min_transcripts:,}: this is not the tree the caller meant, and a band taken "
            "from it is not a band of the corpus"
        )
    return True, "ok"


def find_transcripts(root: Path) -> list[Path]:
    """Every ``*.md`` under ``root``, sorted so a run is reproducible."""

    return sorted(root.rglob("*.md"))


def audit_document(
    path: Path,
    *,
    enrich: Enricher | None = None,
    confirmed_merges: set | None = None,
    repha_extended: bool = False,
    page_refusal: bool = False,
) -> dict:
    """Audit one transcript on disk.

    A read failure is reported as a ``garbled`` verdict with the error, not raised: one
    unreadable file in a corpus sweep should not lose the other six thousand results, and a
    file that cannot be read is a real finding about the tree.
    """

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "md": str(path),
            "verdict": "garbled",
            "failing": ["read"],
            "checks": {"read": {"verdict": "garbled", "error": str(exc)}},
        }

    row = audit_text(
        text,
        confirmed_merges=confirmed_merges,
        repha_extended=repha_extended,
        page_refusal=page_refusal,
    )
    row["md"] = str(path)
    if enrich is not None:
        row["meta"] = enrich(path)
    return row


def audit_tree(
    root: Path,
    *,
    enrich: Enricher | None = None,
    workers: int = 1,
    min_transcripts: int | None = None,
    limit: int | None = None,
    confirmed_merges: set | None = None,
    repha_extended: bool = False,
    page_refusal: bool = False,
) -> list[dict]:
    """Audit every transcript under ``root``. Raises if the tree is not evaluable.

    ``limit`` is applied BEFORE :func:`assess_tree`, deliberately: a truncated audit is a
    real audit of a stated subset, and it should only trip ``min_transcripts`` if the caller
    asked for both at once.

    ``enrich`` runs in the parent process, not the workers, so a caller can hand it a
    closure over anything -- a database handle, a sidecar reader -- without it needing to be
    picklable.
    """

    paths: Iterable[Path] = find_transcripts(root)
    if limit is not None:
        paths = list(paths)[:limit]
    paths = list(paths)

    ok, why = assess_tree(root, len(paths), min_transcripts=min_transcripts)
    if not ok:
        raise ValueError(why)

    options = {
        "confirmed_merges": confirmed_merges,
        "repha_extended": repha_extended,
        "page_refusal": page_refusal,
    }
    if workers <= 1:
        rows = [audit_document(p, **options) for p in paths]
    else:
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(audit_document, p, **options): p for p in paths}
            for future in as_completed(futures):
                rows.append(future.result())
        # `as_completed` yields in finish order, which would make two runs over the same
        # tree produce differently ordered reports. Sorted so a report is comparable.
        rows.sort(key=lambda row: row["md"])

    if enrich is not None:
        for row in rows:
            row["meta"] = enrich(Path(row["md"]))
    return rows
