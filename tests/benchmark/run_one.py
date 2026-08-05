"""Convert a single file through likhit in an isolated subprocess.

Runs as its own process so that hangs, segfaults, and OOM in native code
(PyMuPDF, fonttools) are observable as benchmark results instead of taking the
whole run down. Emits one JSON object on stdout.

Two details here are load-bearing and were both found the hard way:

* ``RLIMIT_DATA``, not ``RLIMIT_AS``. PyMuPDF reserves several GB of *virtual*
  address space at import, so an address-space cap strangles startup and every
  conversion looks like a hang.
* MuPDF's C layer writes diagnostics straight to fd 1, bypassing ``sys.stdout``.
  Left alone it corrupts the JSON record (one corpus PDF emits 192 lines of
  ``format error: No common ancestor in structure tree``). fd 1 is pointed at
  fd 2 for the duration of the conversion and the result is written to a private
  dup.

Memory limiting uses ``resource`` when the platform provides it. RSS reporting
is normalized for Linux and macOS; platforms without ``resource`` report no RSS.

Usage::

    python -m tests.benchmark.run_one <path> [pages]
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module
    resource = None  # type: ignore[assignment]

DEFAULT_MEM_CAP_BYTES = 8 * 1024**3


def _parse_memory_cap(value: str | None) -> int:
    """Return a positive byte cap, falling back for invalid environment values."""

    try:
        gigabytes = int(value) if value is not None else 8
    except ValueError:
        return DEFAULT_MEM_CAP_BYTES
    return gigabytes * 1024**3 if gigabytes > 0 else DEFAULT_MEM_CAP_BYTES


MEM_CAP_BYTES = _parse_memory_cap(os.getenv("LIKHIT_MEM_CAP_GB"))


def _apply_memory_cap(
    resource_module: Any = resource, cap_bytes: int = MEM_CAP_BYTES
) -> None:
    """Apply the data cap when supported without preventing result emission."""

    if resource_module is None:
        return
    try:
        _soft, hard = resource_module.getrlimit(resource_module.RLIMIT_DATA)
        cap = (
            cap_bytes if hard == resource_module.RLIM_INFINITY else min(cap_bytes, hard)
        )
        resource_module.setrlimit(resource_module.RLIMIT_DATA, (cap, hard))
    except (OSError, ValueError) as exc:
        print(f"memory cap not applied: {exc}", file=sys.stderr)


def _write_all(
    file_descriptor: int,
    data: bytes,
    *,
    write: Callable[[int, memoryview], int] = os.write,
) -> None:
    """Write a complete JSON record even when the OS accepts a short write."""

    remaining = memoryview(data)
    while remaining:
        written = write(file_descriptor, remaining)
        if written <= 0:
            raise OSError("stdout write made no progress")
        remaining = remaining[written:]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_one.py <path> [pages]", file=sys.stderr)
        return 2

    path = sys.argv[1]
    pages = sys.argv[2] if len(sys.argv) > 2 else None

    real_stdout = os.dup(1)
    os.dup2(2, 1)

    started = time.monotonic()
    result: dict[str, object] = {"path": path, "pages": pages}
    try:
        _apply_memory_cap()

        from markitdown import MarkItDown

        markitdown = MarkItDown(enable_plugins=True)
        kwargs = {"pages": pages} if pages else {}
        converted = markitdown.convert(path, **kwargs)
        result["status"] = "ok"
        result["text"] = converted.text_content or ""
    except BaseException as exc:  # noqa: BLE001 - catching everything is the point
        result["status"] = "error"
        result["exc_type"] = type(exc).__name__
        # MemoryError stringifies to "", so record a placeholder rather than an
        # empty message that reads like a truncated sentence.
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
    _write_all(real_stdout, json.dumps(result).encode())
    os.close(real_stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
