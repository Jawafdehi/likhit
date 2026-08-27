"""Manual subprocess check for the adversarial GSUB fixture."""

import os
from pathlib import Path
import resource
import subprocess
import sys


def _limit_address_space() -> None:
    limit = 768 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def test_crafted_gsub_stays_within_the_subprocess_memory_limit() -> None:
    raw = os.getenv("LIKHIT_GSUB_OOM_PDF")
    assert raw, (
        "set LIKHIT_GSUB_OOM_PDF to the adversarial PDF before running "
        "tests/manual/test_resource_exhaustion.py"
    )
    fixture = Path(raw)
    assert fixture.is_file(), f"LIKHIT_GSUB_OOM_PDF is not a file: {fixture}"

    script = (
        "from markitdown import MarkItDown; "
        f"MarkItDown(enable_plugins=True).convert({str(fixture)!r})"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        preexec_fn=_limit_address_space,
    )

    assert completed.returncode >= 0, (
        f"conversion terminated by signal {-completed.returncode}: "
        f"{completed.stderr[-1000:]}"
    )
