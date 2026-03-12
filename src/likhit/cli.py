"""CLI entry point for document extraction."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from likhit.core import derive_output_name, extract_many, render_markdown
from likhit.errors import LikhitError, ValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="likhit")
    subparsers = parser.add_subparsers(dest="command")

    extract_parser = subparsers.add_parser("extract", help="Extract document text")
    extract_parser.add_argument("inputs", nargs="+", help="One or more input PDF files")
    extract_parser.add_argument("--type", required=True, help="Document type")
    extract_parser.add_argument("--out", help="Output path for a single input file")
    extract_parser.add_argument(
        "--out-dir",
        help="Directory for generated Markdown files; defaults to current directory",
    )
    extract_parser.add_argument("--title", help="Override the extracted title")
    extract_parser.add_argument("--date", dest="publication_date", help="Override date")
    extract_parser.add_argument("--source-url", help="Attach a source URL")
    extract_parser.add_argument("--pages", help="Page range to extract, e.g. 1-3 or 5")
    return parser


def _write_outputs(args: argparse.Namespace) -> int:
    if len(args.inputs) > 1 and args.out:
        raise ValidationError("--out can only be used with a single input file")

    output_dir = Path(args.out_dir or ".")
    output_dir.mkdir(parents=True, exist_ok=True)

    results = extract_many(
        args.inputs,
        args.type,
        title=args.title,
        publication_date=args.publication_date,
        source_url=args.source_url,
        pages=args.pages,
    )

    existing_names: set[str] = {
        path.name for path in output_dir.iterdir() if path.is_file()
    }
    for source_path, result in results:
        markdown = render_markdown(result)
        if args.out:
            destination = Path(args.out)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValidationError(f"Output file already exists: {destination}")
        else:
            destination = output_dir / derive_output_name(
                result,
                source_path,
                existing_names,
            )
        destination.write_text(markdown, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "extract":
        parser.print_help()
        return 1

    try:
        return _write_outputs(args)
    except LikhitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
