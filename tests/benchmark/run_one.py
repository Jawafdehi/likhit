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

Usage::

    python -m tests.benchmark.run_one <path> [pages]
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
import traceback

MEM_CAP_BYTES = int(os.getenv("LIKHIT_MEM_CAP_GB", "8")) * 1024**3


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_one.py <path> [pages]", file=sys.stderr)
        return 2

    path = sys.argv[1]
    pages = sys.argv[2] if len(sys.argv) > 2 else None

    resource.setrlimit(resource.RLIMIT_DATA, (MEM_CAP_BYTES, MEM_CAP_BYTES))

    real_stdout = os.dup(1)
    os.dup2(2, 1)

    started = time.monotonic()
    result: dict[str, object] = {"path": path, "pages": pages}
    try:
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
    result["max_rss_mb"] = round(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
    )

    sys.stdout.flush()
    os.dup2(real_stdout, 1)
    os.write(real_stdout, json.dumps(result).encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
