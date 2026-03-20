"""CLI entry point for conversion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from likhit.core import convert_many, derive_convert_output_name
from likhit.errors import LikhitError, ValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="likhit",
        description="Convert born-digital PDFs to editable Markdown.",
    )
    subparsers = parser.add_subparsers(dest="command")

    convert_parser = subparsers.add_parser(
        "convert",
        help="Convert PDFs to editable Markdown",
    )
    convert_parser.add_argument("inputs", nargs="+", help="One or more input PDF files")
    convert_parser.add_argument("--out", help="Output path for a single input file")
    convert_parser.add_argument(
        "--out-dir",
        help="Directory for generated Markdown files; defaults to current directory",
    )
    return parser


def _write_convert_outputs(args: argparse.Namespace) -> int:
    if len(args.inputs) > 1 and args.out:
        raise ValidationError("--out can only be used with a single input file")

    for input_path in args.inputs:
        path = Path(input_path)
        if path.suffix.lower() != ".pdf":
            raise ValidationError(
                "Unsupported input format for convert. Only born-digital PDF files are supported."
            )

    output_dir = Path(args.out_dir or ".")
    output_dir.mkdir(parents=True, exist_ok=True)
    results = convert_many(args.inputs)

    existing_names: set[str] = {
        path.name for path in output_dir.iterdir() if path.is_file()
    }
    for source_path, markdown in results:
        if args.out:
            destination = Path(args.out)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValidationError(f"Output file already exists: {destination}")
        else:
            destination = output_dir / derive_convert_output_name(
                source_path,
                existing_names,
            )
        destination.write_text(markdown, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1

    try:
        if args.command == "convert":
            return _write_convert_outputs(args)
        parser.print_help()
        return 1
    except LikhitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
