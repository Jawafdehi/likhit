"""Small CLI for saving MarkItDown plugin output to Markdown files.

🛑 **Redaction is on by default here, and producing unredacted text is the thing you have to
ask for.** That is a deliberate reversal of the usual library instinct, and it is a
behavioural change for anyone who used this command before: ``likhit-save report.pdf`` now
writes a transcript redacted to :data:`~likhit.privacy.modes.DEFAULT_MODE`. The reasoning is
that this command's output is a *file*, and a file outlives the invocation that made it --
nobody reading ``report.md`` six months later can tell whether redaction was considered. The
safe default is the one where forgetting produces the harmless outcome.

``--redact none`` restores the old behaviour exactly. It is a legitimate thing to want -- the
retained original, internal casework and the research corpus all need it -- so it is one flag
and not a fight, but it is logged at WARNING, because "this artifact is unredacted" is a fact
the run record should carry.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from markitdown import MarkItDown

from likhit.privacy.modes import DEFAULT_MODE, RedactionMode
from likhit.privacy.text import redact_text


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
    parser.add_argument(
        "--pages",
        help="Optional 1-based page selection for PDFs, like '5' or '2-4'.",
    )
    parser.add_argument(
        "--redact",
        type=RedactionMode,
        choices=list(RedactionMode),
        default=DEFAULT_MODE,
        help=(
            "Removal tier applied to the converted text before it is written, widest "
            f"last: 'identifiers' (citizenship numbers, dates of birth), 'contact' (adds "
            f"mobile, landline, email), '{DEFAULT_MODE}' (default; adds bank "
            "account, age, passport). Labels are matched in Nepali and English. "
            "'none' writes the transcript unredacted -- say so deliberately."
        ),
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

    if args.redact is RedactionMode.NONE:
        logging.warning(
            "--redact none: transcripts will be written UNREDACTED. Personal data in the "
            "source is carried through verbatim."
        )

    for source_path in args.inputs:
        convert_kwargs: dict[str, str] = {}
        if args.pages:
            convert_kwargs["pages"] = args.pages
        result = md.convert(source_path, **convert_kwargs)
        markdown = result.markdown or result.text_content

        # 🛑 Fail closed. A redaction that raised must not fall through to writing the
        # unredacted text it was asked to clean -- that is the one outcome worse than not
        # converting the document at all. The exception propagates; no file is written.
        redacted = redact_text(markdown, mode=args.redact)
        markdown = redacted.text
        if redacted.changed:
            logging.info(
                "redacted %d value(s) from %s (mode=%s)",
                redacted.values_removed,
                source_path,
                args.redact.value,
            )

        if args.out:
            destination = Path(args.out)
            destination.parent.mkdir(parents=True, exist_ok=True)
        else:
            destination = output_dir / _derive_output_name(source_path, existing_names)

        destination.write_text(markdown, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
