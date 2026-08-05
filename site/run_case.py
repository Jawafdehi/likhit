# SPDX-License-Identifier: Hippocratic-3.0
"""Run one benchmark conversion in an isolated process and emit JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module
    resource = None  # type: ignore[assignment]

MEM_CAP_BYTES = int(os.getenv("LIKHIT_MEM_CAP_GB", "8")) * 1024**3


def _write_all(file_descriptor: int, data: bytes) -> None:
    """Write a complete result even when the OS accepts a short write."""

    remaining = memoryview(data)
    while remaining:
        written = os.write(file_descriptor, remaining)
        if written <= 0:
            raise OSError("stdout write made no progress")
        remaining = remaining[written:]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--plugins", choices=("enabled", "disabled"), required=True)
    parser.add_argument("--pages")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    real_stdout = os.dup(1)
    os.dup2(2, 1)

    started = time.monotonic()
    result: dict[str, object] = {
        "path": args.path,
        "pages": args.pages,
        "plugins": args.plugins == "enabled",
    }
    try:
        if resource is not None:
            resource.setrlimit(resource.RLIMIT_DATA, (MEM_CAP_BYTES, MEM_CAP_BYTES))

        from markitdown import MarkItDown

        converter = MarkItDown(enable_plugins=args.plugins == "enabled")
        kwargs = {"pages": args.pages} if args.pages else {}
        converted = converter.convert(args.path, **kwargs)
        result["status"] = "ok"
        result["text"] = converted.text_content or ""
    except BaseException as exc:  # noqa: BLE001 - process failures are benchmark data
        result["status"] = "error"
        result["exc_type"] = type(exc).__name__
        result["exc_msg"] = str(exc)[:2000] or "<no message>"
        result["traceback"] = traceback.format_exc()[-4000:]

    result["wall_s"] = round(time.monotonic() - started, 3)
    if resource is not None:
        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024**2 if sys.platform == "darwin" else 1024
        result["max_rss_mb"] = round(max_rss / divisor, 1)
    else:
        result["max_rss_mb"] = None

    sys.stdout.flush()
    os.dup2(real_stdout, 1)
    _write_all(real_stdout, json.dumps(result, ensure_ascii=False).encode())
    os.close(real_stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
