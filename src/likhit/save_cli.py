"""Small CLI for saving MarkItDown plugin output to Markdown files."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from markitdown import MarkItDown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="likhit-save",
        description="Convert documents to Markdown files using MarkItDown with the likhit plugin enabled.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="One or more input files (.pdf, .docx, .doc).",
    )
    parser.add_argument(
        "--out",
        help="Output file path for a single input.",
    )
    parser.add_argument(
        "--out-dir",
        help="Directory for generated Markdown files. Defaults to the current directory.",
    )
    return parser


def _derive_output_name(source_path: str, existing: set[str]) -> str:
    base_name = Path(source_path).stem or "document"
    candidate = f"{base_name}.md"
    counter = 2
    while candidate in existing:
        candidate = f"{base_name}-{counter}.md"
        counter += 1
    existing.add(candidate)
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[likhit] %(message)s")

    if len(args.inputs) > 1 and args.out:
        parser.error("--out can only be used with a single input file")

    output_dir = Path(args.out_dir or ".")
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_names = {path.name for path in output_dir.iterdir() if path.is_file()}

    md = MarkItDown(enable_plugins=True)

    for source_path in args.inputs:
        result = md.convert(source_path)
        markdown = result.markdown or result.text_content

        if args.out:
            destination = Path(args.out)
            destination.parent.mkdir(parents=True, exist_ok=True)
        else:
            destination = output_dir / _derive_output_name(source_path, existing_names)

        destination.write_text(markdown, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
