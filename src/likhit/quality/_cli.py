"""``likhit-audit`` -- run the quality axes over a tree and write a JSON report.

Kept thin on purpose: everything decidable lives in :mod:`likhit.quality.tree` and
:mod:`likhit.quality.axes`, so the behaviour a caller gets from the command is the behaviour
they get from the library.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .axes import RANK
from .tree import audit_tree


def build_audit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="likhit-audit",
        description="Score Nepali transcripts on independent quality axes.",
    )
    parser.add_argument("markdown", type=Path, help="directory of *.md transcripts")
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, help="audit only the first N transcripts")
    parser.add_argument(
        "--min-transcripts",
        type=int,
        help=(
            "refuse to report if the tree holds fewer than this. There is nothing "
            "intrinsic to a tree that separates a small fixture from a whole corpus, so "
            "only the caller can state the expected scale"
        ),
    )
    parser.add_argument(
        "--verdict",
        choices=sorted(RANK),
        help="list only documents with this verdict",
    )
    parser.add_argument(
        "--repha-extended",
        action="store_true",
        help="opt into the wider repha probe",
    )
    parser.add_argument(
        "--page-refusal",
        action="store_true",
        help="opt into the per-page refusal axis (off by default: turning a gate on is a "
        "generation decision, not a tool's)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_audit_parser().parse_args(argv)

    try:
        rows = audit_tree(
            args.markdown,
            workers=args.workers,
            limit=args.limit,
            min_transcripts=args.min_transcripts,
            repha_extended=args.repha_extended,
            page_refusal=args.page_refusal,
        )
    except ValueError as exc:
        # The tree was not evaluable. Exit non-zero: the whole point of that guard is that
        # a 0-record report must not look like a clean one.
        print(f"likhit-audit: {exc}", file=sys.stderr)
        return 2

    bands = Counter(row["verdict"] for row in rows)
    failing = Counter(axis for row in rows for axis in row.get("failing", ()))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "markdown_root": str(args.markdown),
                    "documents": len(rows),
                    "bands": dict(bands),
                    "failing_axes": dict(failing),
                    "files": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"audited {len(rows):,} transcripts from {args.markdown}")
    for band in sorted(bands, key=lambda b: RANK[b]):
        print(f"  {band:8} {bands[band]:,}")
    if failing:
        print("failing axes, by document count:")
        for axis, count in failing.most_common():
            print(f"  {axis:16} {count:,}")
    if args.verdict:
        for row in rows:
            if row["verdict"] == args.verdict:
                print(f"  {row['verdict']:8} {row['md']}")
    if args.out:
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
