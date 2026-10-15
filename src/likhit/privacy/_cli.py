"""``likhit-redact`` -- scan or redact a tree of transcripts, and journal what happened.

Two subcommands, because they answer different questions and only one of them writes:

``scan``
    What personal data is present. Reports counts and shapes, never a match, so the output
    is publishable beside the corpus it describes.
``redact``
    Remove the narrow set that has been adjudicated as unambiguously personal, into a
    **staging copy**. Never in place -- see :mod:`likhit.privacy.tree`.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from .modes import DEFAULT_MODE, RedactionMode
from .pdf import redact_pdf
from .signals import scan_text
from .tree import redact_tree


def build_redact_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="likhit-redact",
        description="Detect and remove personal data from Nepali transcripts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser(
        "scan", help="report personal-data signals; writes no transcript"
    )
    scan.add_argument("markdown", type=Path)
    scan.add_argument("--out", type=Path, help="write the JSON report here")

    redact = sub.add_parser("redact", help="write a redacted staging copy of a tree")
    redact.add_argument("markdown", type=Path)
    redact.add_argument(
        "--out-tree",
        type=Path,
        help="staging copy to write; refused if it exists. Omit only with --dry-run",
    )
    redact.add_argument("--journal", type=Path, required=True)
    redact.add_argument("--dry-run", action="store_true")
    redact.add_argument(
        "--tables",
        action="store_true",
        help="run the table pass -- a value in a cell away from its label -- instead of "
        "the inline pass. The two are independent and either order works; run both to "
        "cover identifiers held inline AND in table cells",
    )

    pdf = sub.add_parser(
        "pdf", help="write a redacted copy of ONE PDF; refuses if it cannot read a page"
    )
    pdf.add_argument("pdf", type=Path)
    pdf.add_argument(
        "--out",
        type=Path,
        help="redacted PDF to write; refused if it exists. Omit only with --dry-run",
    )
    pdf.add_argument("--journal", type=Path, required=True)
    pdf.add_argument("--dry-run", action="store_true")
    pdf.add_argument(
        "--mode",
        type=RedactionMode,
        # `none` is excluded here, not forgotten: `redact_pdf` refuses it, because writing a
        # file named as a redacted derivative that is byte-identical to its source is a
        # different and worse thing than a function returning its argument.
        choices=[
            RedactionMode.IDENTIFIERS,
            RedactionMode.CONTACT,
            RedactionMode.STRICT,
        ],
        default=DEFAULT_MODE,
        help="removal tier. 'identifiers' is the formally adjudicated scope (citizenship "
        "numbers, dates of birth); 'contact' adds mobile, landline and email; 'strict' "
        "(default) adds PAN, bank account and age. No tier removes names -- see "
        "likhit.privacy.modes for why that is a decision rather than a gap",
    )
    return parser


def _scan(args: argparse.Namespace) -> int:
    if not args.markdown.is_dir():
        print(f"likhit-redact: no such tree: {args.markdown}", file=sys.stderr)
        return 2
    documents = sorted(args.markdown.rglob("*.md"))
    if not documents:
        print(
            f"likhit-redact: {args.markdown} holds no *.md; a scan of zero documents is "
            "not evidence of no personal data",
            file=sys.stderr,
        )
        return 2

    high_precision: Counter = Counter()
    name_shaped: Counter = Counter()
    with_any = 0
    for path in documents:
        text = unicodedata.normalize(
            "NFC", path.read_text(encoding="utf-8", errors="replace")
        )
        hp, ns = scan_text(text)
        high_precision.update(hp)
        name_shaped.update(ns)
        if sum(hp.values()):
            with_any += 1

    report = {
        "markdown_root": str(args.markdown),
        "documents_scanned": len(documents),
        "documents_with_any_high_precision_hit": with_any,
        "high_precision_occurrences": dict(sorted(high_precision.items())),
        "name_shaped_occurrences": dict(sorted(name_shaped.items())),
        "no_matched_text_emitted": True,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"scanned {len(documents):,} documents from {args.markdown}")
    print(f"documents with >=1 high-precision hit: {with_any:,}")
    for key, value in sorted(high_precision.items(), key=lambda kv: -kv[1]):
        if value:
            print(f"  {key:16} {value:,}")
    if args.out:
        print(f"-> {args.out}")
    return 0


def _redact(args: argparse.Namespace) -> int:
    if args.journal.exists():
        print(f"likhit-redact: refusing to overwrite {args.journal}", file=sys.stderr)
        return 2
    try:
        report = redact_tree(
            args.markdown,
            args.out_tree,
            tables=args.tables,
            dry_run=args.dry_run,
        )
    except (ValueError, AssertionError) as exc:
        # `AssertionError` as well as `ValueError`: `redact_tables.apply_targets` and `scan`
        # assert on "target value changed", "target column vanished" and "candidate shape
        # disagrees". Catching only `ValueError` let those surface as a traceback instead of
        # this command's exit-2 path. Per-document failures no longer reach here at all --
        # `redact_tree` records them in `failed_documents` -- so what does reach here is a
        # refusal about the run as a whole, which is exactly what should stop it.
        print(f"likhit-redact: {exc}", file=sys.stderr)
        return 2

    payload = report.as_dict()
    payload |= {
        "in_tree": str(args.markdown),
        "out_tree": None if args.dry_run else str(args.out_tree),
        "dry_run": bool(args.dry_run),
        "pass": "tables" if args.tables else "inline",
    }
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    args.journal.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"documents: {report.documents_in_tree:,}  "
        f"changed: {report.documents_changed:,}  spans: {report.spans_redacted:,}  "
        f"failed: {len(report.failed_documents):,}"
    )
    for failure in report.failed_documents[:10]:
        print(f"  FAILED {failure['path']}: {failure['error']}", file=sys.stderr)
    print(
        f"chars {report.chars_before:,} -> {report.chars_after:,} "
        f"(delta {report.net_char_delta:,})"
    )
    print(f"-> {args.journal}")

    # 🛑 Exit 1 when any document failed, by the same argument `quality.tree.assess_tree`
    # makes: a partial result that exits 0 is indistinguishable, to any caller, from a
    # complete one. That matters more for redaction than for auditing -- a CI step reading
    # success here would conclude a tree had been de-identified when some of it was skipped.
    # 2 is a refusal to start, 1 is "ran and something did not complete", 0 is all clear.
    # The journal names every failure either way.
    return 1 if report.failed_documents else 0


def _pdf(args: argparse.Namespace) -> int:
    if args.journal.exists():
        print(f"likhit-redact: refusing to overwrite {args.journal}", file=sys.stderr)
        return 2
    try:
        report = redact_pdf(args.pdf, args.out, mode=args.mode, dry_run=args.dry_run)
    except ValueError as exc:
        # Exit 2, the same refusal code `redact` uses: this is "did not run", not "ran and
        # something was skipped". Every raise in `likhit.privacy.pdf` happens before anything
        # is written, so a refusal here never leaves a partial artifact behind.
        print(f"likhit-redact: {exc}", file=sys.stderr)
        return 2

    payload = report.as_dict()
    payload |= {
        "source": str(args.pdf),
        "out": None if args.dry_run else str(args.out),
        "dry_run": bool(args.dry_run),
        "mode": args.mode.value,
    }
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    args.journal.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"pages: {report.pages:,}  changed: {report.pages_changed:,}  "
        f"values removed: {report.values_removed:,}  "
        f"rectangles: {report.rects_applied:,}  mode: {args.mode.value}"
    )
    for page_class, count in sorted(report.pages_by_class.items()):
        print(f"  {page_class:10} {count:,}")
    print(f"-> {args.journal}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_redact_parser().parse_args(argv)
    if args.command == "scan":
        return _scan(args)
    return _pdf(args) if args.command == "pdf" else _redact(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
