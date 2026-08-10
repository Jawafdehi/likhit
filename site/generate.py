# SPDX-License-Identifier: Hippocratic-3.0
"""Generate the static Likhit benchmark application and its data artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from typing import Any

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE_DIR = pathlib.Path(__file__).resolve().parent
STATIC_DIR = SITE_DIR / "static"
RUNNER = SITE_DIR / "run_case.py"
DEFAULT_MAX_PUBLIC_SOURCE_BYTES = 100 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
OUTPUT_MARKER = ".likhit-benchmark-output"
OUTPUT_MARKER_CONTENT = "likhit-benchmark-output-v1\n"
# Bump when a snapshot field changes meaning. _load_snapshot refuses a version it
# does not understand rather than silently replaying half a record.
SNAPSHOT_VERSION = 1

sys.path.insert(0, str(ROOT))

from tests.benchmark.measure import _text_signals  # noqa: E402
from tests.synthetic_pdfs import (  # noqa: E402
    build_legacy_then_english_pdf,
    build_mislabeled_preeti_pdf,
    build_mixed_scan_and_text_pdf,
    build_pure_scan_pdf,
    build_scanned_decoy_pdf,
)

RunCase = Callable[
    [pathlib.Path, dict[str, Any], dict[str, Any], int],
    tuple[dict[str, Any], str],
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_born_digital_table_pdf() -> bytes:
    document = fitz.open()
    try:
        page = document.new_page(width=612, height=792)
        page.insert_text((54, 62), "Procurement Plan", fontsize=18, fontname="helv")
        page.insert_text(
            (54, 88),
            "Quarterly procurement review",
            fontsize=10,
            fontname="helv",
        )

        columns = (54, 270, 430, 558)
        top = 126
        row_height = 38
        rows = (
            ("Item", "Owner", "Status"),
            ("Document scanner", "Operations", "Approved"),
            ("OCR validation", "Research", "In review"),
            ("Archive export", "Records", "Scheduled"),
        )
        for row_index in range(len(rows) + 1):
            y = top + row_index * row_height
            page.draw_line((columns[0], y), (columns[-1], y), color=(0.2, 0.2, 0.2))
        for x in columns:
            page.draw_line(
                (x, top),
                (x, top + len(rows) * row_height),
                color=(0.2, 0.2, 0.2),
            )
        for row_index, row in enumerate(rows):
            y = top + row_index * row_height + 24
            for column_index, value in enumerate(row):
                page.insert_text(
                    (columns[column_index] + 8, y),
                    value,
                    fontsize=9,
                    fontname="helv",
                )

        page.insert_text(
            (54, top + len(rows) * row_height + 34),
            "All amounts are synthetic and contain no personal data.",
            fontsize=10,
            fontname="helv",
        )
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def _build_unicode_docx() -> bytes:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>\u0928\u0947\u092a\u093e\u0932 \u0938\u0930\u0915\u093e\u0930</w:t></w:r></w:p>
    <w:p><w:r><w:t>\u0921\u093f\u091c\u093f\u091f\u0932 \u0905\u092d\u093f\u0932\u0947\u0916 \u092a\u0930\u0940\u0915\u094d\u0937\u0923 \u092a\u094d\u0930\u0924\u093f\u0935\u0947\u0926\u0928</w:t></w:r></w:p>
    <w:p><w:r><w:t>Quarterly programme review for document interoperability.</w:t></w:r></w:p>
    <w:tbl>
      <w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
      <w:tblGrid><w:gridCol w:w="3600"/><w:gridCol w:w="3600"/></w:tblGrid>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Format</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Status</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>DOCX</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Verified</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>
"""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    return stream.getvalue()


SYNTHETIC_BUILDERS: dict[str, Callable[[], bytes]] = {
    "scanned_decoy": build_scanned_decoy_pdf,
    "pure_scan": build_pure_scan_pdf,
    "mislabeled_preeti": build_mislabeled_preeti_pdf,
    "legacy_then_english": build_legacy_then_english_pdf,
    "mixed_scan_text": build_mixed_scan_and_text_pdf,
    "born_digital_table": _build_born_digital_table_pdf,
    "unicode_docx": _build_unicode_docx,
}


def _load_catalog() -> dict[str, Any]:
    return json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))


def _cached_public_source(
    spec: dict[str, Any], source_cache: pathlib.Path | None
) -> pathlib.Path | None:
    if source_cache is None or not source_cache.exists():
        return None
    direct = source_cache / f"{spec['id']}.{spec['kind']}"
    if direct.is_file():
        return direct
    matches = sorted(source_cache.glob(f"{spec['sha256'][:10]}*"))
    return next((path for path in matches if path.is_file()), None)


def _source_size_limit(spec: dict[str, Any]) -> int:
    try:
        max_bytes = int(spec.get("max_bytes", DEFAULT_MAX_PUBLIC_SOURCE_BYTES))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{spec['id']} has an invalid max_bytes value") from exc
    if max_bytes < 1:
        raise ValueError(f"{spec['id']} max_bytes must be positive")
    return max_bytes


def _read_bounded_response(
    response: Any,
    *,
    source_id: str,
    max_bytes: int,
) -> bytes:
    headers = getattr(response, "headers", None)
    content_length = headers.get("Content-Length") if headers is not None else None
    try:
        declared_bytes = int(content_length) if content_length is not None else None
    except (TypeError, ValueError):
        declared_bytes = None
    if declared_bytes is not None and declared_bytes > max_bytes:
        raise ValueError(f"{source_id} download exceeds {max_bytes} bytes")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(DOWNLOAD_CHUNK_BYTES, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"{source_id} download exceeds {max_bytes} bytes")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _download_public_source(
    spec: dict[str, Any],
    source_cache: pathlib.Path | None,
    *,
    attempts: int = 3,
    open_url: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    max_bytes = _source_size_limit(spec)
    cached = _cached_public_source(spec, source_cache)
    if cached is not None:
        if cached.stat().st_size > max_bytes:
            raise ValueError(f"{spec['id']} cached source exceeds {max_bytes} bytes")
        data = cached.read_bytes()
        if len(data) > max_bytes:
            raise ValueError(f"{spec['id']} cached source exceeds {max_bytes} bytes")
    else:
        if attempts < 1:
            raise ValueError("download attempts must be positive")
        request = urllib.request.Request(
            spec["url"],
            headers={"User-Agent": "likhit-benchmark/1.0"},
        )
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with open_url(request, timeout=120) as response:
                    data = _read_bounded_response(
                        response,
                        source_id=spec["id"],
                        max_bytes=max_bytes,
                    )
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    sleep(float(2**attempt))
        else:
            raise RuntimeError(
                f"{spec['id']} download failed from {spec['url']} "
                f"after {attempts} attempts"
            ) from last_error

    digest = _sha256(data)
    if digest != spec["sha256"]:
        raise ValueError(
            f"{spec['id']} checksum mismatch: expected {spec['sha256']}, got {digest}"
        )
    return data


def _selected_page_indexes(selection: str, page_count: int) -> list[int]:
    indexes: list[int] = []
    for part in selection.split(","):
        bounds = part.strip().split("-", maxsplit=1)
        start = int(bounds[0])
        end = int(bounds[-1])
        if start < 1 or end < start or end > page_count:
            raise ValueError(
                f"invalid page selection {selection!r} for {page_count} pages"
            )
        indexes.extend(range(start - 1, end))
    return indexes


def _sanitize_pdf_excerpt(data: bytes, pages: str) -> bytes:
    with fitz.open(stream=data, filetype="pdf") as source:
        indexes = _selected_page_indexes(pages, source.page_count)
        excerpt = fitz.open()
        try:
            for index in indexes:
                excerpt.insert_pdf(source, from_page=index, to_page=index)
            excerpt.set_metadata({})
            return excerpt.tobytes(
                garbage=4,
                clean=True,
                deflate=True,
                no_new_id=True,
            )
        finally:
            excerpt.close()


def _materialize_source(
    spec: dict[str, Any],
    documents_dir: pathlib.Path,
    source_cache: pathlib.Path | None,
) -> pathlib.Path:
    target = documents_dir / f"{spec['id']}.{spec['kind']}"
    if spec["origin"] == "synthetic":
        data = SYNTHETIC_BUILDERS[spec["builder"]]()
    else:
        data = _download_public_source(spec, source_cache)
        if spec.get("publish_pages"):
            data = _sanitize_pdf_excerpt(data, spec["publish_pages"])
    target.write_bytes(data)
    return target


def _render_thumbnail(
    source: pathlib.Path, previews_dir: pathlib.Path
) -> tuple[str | None, int | None]:
    if source.suffix.lower() != ".pdf":
        return None, None
    with fitz.open(source) as document:
        pages = document.page_count
        page = document.load_page(0)
        pixmap = page.get_pixmap(dpi=72, colorspace=fitz.csRGB, alpha=False)
        output = previews_dir / f"{source.stem}.png"
        pixmap.save(output)
    return f"previews/{output.name}", pages


def _default_run_case(
    source: pathlib.Path,
    run: dict[str, Any],
    config: dict[str, Any],
    timeout_s: int,
) -> tuple[dict[str, Any], str]:
    command = [
        sys.executable,
        str(RUNNER),
        str(source),
        "--plugins",
        "enabled" if config["plugins"] else "disabled",
    ]
    if run.get("pages"):
        command.extend(["--pages", str(run["pages"])])

    environment = os.environ.copy()
    environment["LIKHIT_SENTRY_DSN"] = ""
    if config["environment"] == "no-ocr":
        for name in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "MARKITDOWN_OCR_MODEL",
        ):
            environment.pop(name, None)
    elif config["environment"] == "ocr-local":
        # Point likhit's OpenAI-compatible client at a locally served vision model
        # (Ollama, llama.cpp, vLLM). No likhit change is needed for this: it is the
        # same client, a different base URL, and no per-token charge. Any hosted
        # key is cleared so a local run cannot silently bill a hosted API.
        environment.pop("GEMINI_API_KEY", None)
        environment.pop("GEMINI_MODEL", None)
        environment["OPENAI_BASE_URL"] = os.environ.get("LIKHIT_LOCAL_OCR_BASE_URL", "")
        environment["MARKITDOWN_OCR_MODEL"] = os.environ.get(
            "LIKHIT_LOCAL_OCR_MODEL", ""
        )
        # Local servers ignore the key but the client refuses to build without one.
        environment["OPENAI_API_KEY"] = os.environ.get(
            "LIKHIT_LOCAL_OCR_API_KEY", "local"
        )

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            cwd=ROOT,
            env=environment,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        diagnostics = (exc.stderr or b"").decode(errors="replace")
        return {
            "status": "timeout",
            "text": "",
            "wall_s": float(timeout_s),
            "max_rss_mb": None,
            "exc_type": "TimeoutExpired",
            "exc_msg": f"conversion exceeded {timeout_s}s",
        }, diagnostics

    diagnostics = completed.stderr.decode(errors="replace")
    try:
        payload = json.loads(completed.stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return {
            "status": "bad-output",
            "text": "",
            "wall_s": None,
            "max_rss_mb": None,
            "exc_type": "JSONDecodeError",
            "exc_msg": completed.stdout.decode(errors="replace")[:2000],
        }, diagnostics
    return payload, diagnostics


def _evaluate_check(
    check: dict[str, Any],
    text: str,
    diagnostics: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    kind = check["kind"]
    value = check["value"]
    metric_kinds = {
        "min_chars",
        "min_devanagari",
        "max_devanagari",
        "max_replacement",
    }
    if kind in metric_kinds and not metrics:
        return {
            "label": check["label"],
            "kind": kind,
            "passed": False,
            "detail": "Metric unavailable because conversion did not succeed",
        }
    if kind == "contains":
        passed = str(value) in text
        detail = f"Required text {'found' if passed else 'missing'}"
    elif kind == "min_chars":
        actual = int(metrics.get("chars", 0))
        passed = actual >= int(value)
        detail = f"{actual:,} characters; minimum {int(value):,}"
    elif kind == "min_devanagari":
        actual = int(metrics.get("devanagari", 0))
        passed = actual >= int(value)
        detail = f"{actual:,} Devanagari letters; minimum {int(value):,}"
    elif kind == "max_devanagari":
        actual = int(metrics.get("devanagari", 0))
        passed = actual <= int(value)
        detail = f"{actual:,} Devanagari letters; maximum {int(value):,}"
    elif kind == "max_replacement":
        actual = int(metrics.get("replacement", 0))
        passed = actual <= int(value)
        detail = f"{actual:,} replacement characters; maximum {int(value):,}"
    elif kind == "diagnostic_contains":
        passed = str(value).casefold() in diagnostics.casefold()
        detail = f"Diagnostic {'found' if passed else 'missing'}"
    else:
        raise ValueError(f"unknown benchmark check: {kind}")
    return {
        "label": check["label"],
        "kind": kind,
        "passed": passed,
        "detail": detail,
    }


def _write_text_artifact(path: pathlib.Path, value: str) -> tuple[str, str]:
    data = value.encode()
    path.write_bytes(data)
    return path.as_posix(), _sha256(data)


def _read_ocr_usage(usage_url: str | None) -> dict[str, int] | None:
    """Read cumulative OCR token counters from the configured endpoint.

    The endpoint reports totals since it started, so a single run's usage is the
    difference across the run. Returns None when unreachable or malformed, which
    is treated as "usage unknown" rather than as zero usage -- reporting zero
    tokens for a run that really did call a vision model would be a lie.
    """

    if not usage_url:
        return None
    try:
        request = urllib.request.Request(
            usage_url, headers={"User-Agent": "likhit-benchmark/1.0"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    counters = {}
    for key in ("calls", "input_tokens", "output_tokens"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        counters[key] = value
    return counters


_UNAVAILABLE_REASONS = {
    "ocr-api": (
        "no hosted vision API configured (needs MARKITDOWN_OCR_MODEL plus "
        "OPENAI_API_KEY or GEMINI_API_KEY)"
    ),
    "ocr-local": (
        "no local vision model served (needs LIKHIT_LOCAL_OCR_BASE_URL and "
        "LIKHIT_LOCAL_OCR_MODEL, with that model actually served)"
    ),
}


def _ocr_backend_available(config: dict[str, Any]) -> bool:
    """Whether the OCR backend a configuration needs is actually reachable here.

    Environments differ: CI has no vision credentials, a laptop may have a hosted
    API key, and another machine may only have a local model server. A
    configuration whose backend is absent is *skipped* rather than run, because
    running it would report a likhit defect where the truth is "not configured".
    Configurations that do not use OCR are always available.
    """

    requirement = config.get("requires")
    if not requirement:
        return True
    if requirement == "ocr-api":
        return bool(
            os.environ.get("MARKITDOWN_OCR_MODEL")
            and (os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        )
    if requirement == "ocr-local":
        # A local OpenAI-compatible server (Ollama, llama.cpp, vLLM). It counts as
        # available only when it answers *and* serves the requested model, so a
        # running-but-empty server does not produce a column of failures.
        base = os.environ.get("LIKHIT_LOCAL_OCR_BASE_URL")
        model = os.environ.get("LIKHIT_LOCAL_OCR_MODEL")
        if not base or not model:
            return False
        return model in _local_ocr_models(base)
    return False


def _local_ocr_models(base_url: str) -> frozenset[str]:
    """Model ids a local OpenAI-compatible server currently serves."""

    url = base_url.rstrip("/") + "/models"
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "likhit-benchmark/1.0"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return frozenset()
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(
        str(entry.get("id"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    )


def _ocr_backend_accounting(config: dict[str, Any]) -> tuple[str | None, str | None]:
    """The usage endpoint and model id belonging to a configuration's backend.

    Both are per-backend, not global. Reading a single ambient endpoint and a
    single ambient model name would attribute one backend's tokens, and one
    backend's model id, to a run served by the other -- so a locally served model
    could be reported as having spent tokens on a hosted API.
    """

    requirement = config.get("requires")
    if requirement == "ocr-api":
        return (
            os.environ.get("LIKHIT_OCR_USAGE_URL"),
            os.environ.get("MARKITDOWN_OCR_MODEL"),
        )
    if requirement == "ocr-local":
        return (
            os.environ.get("LIKHIT_LOCAL_OCR_USAGE_URL"),
            os.environ.get("LIKHIT_LOCAL_OCR_MODEL"),
        )
    # A configuration with no OCR backend cannot spend tokens.
    return None, None


def _ocr_usage_record(
    before: dict[str, int] | None,
    after: dict[str, int] | None,
    model: str | None,
) -> dict[str, Any] | None:
    """Vision calls and tokens one run spent, plus the model that spent them.

    Tokens only, deliberately: no cost is derived. Vendor token rates are not
    published for every model in the AWS price list, so any built-in price table
    would silently produce wrong money. Tokens and calls are the measured
    quantities, and the model is recorded so they can be priced elsewhere.
    """

    if before is None or after is None:
        return None
    calls = after["calls"] - before["calls"]
    input_tokens = after["input_tokens"] - before["input_tokens"]
    output_tokens = after["output_tokens"] - before["output_tokens"]
    # A counter that went backwards means the endpoint restarted mid-build; the
    # difference is then meaningless.
    if min(calls, input_tokens, output_tokens) < 0:
        return None
    # Zero calls is a measurement, not an absence: Likhit only adds an OCR
    # candidate for pages a text layer cannot serve, so most documents spend
    # nothing even with a vision backend configured. Reporting that as null would
    # make "this run needed no OCR" indistinguishable from "nobody was counting",
    # and the dashboard would show a blank for both.

    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "model": model,
    }


_RECORDED_PAYLOAD_FIELDS = (
    "status",
    "text",
    "wall_s",
    "max_rss_mb",
    "exc_type",
    "exc_msg",
    "traceback",
)


def _snapshot_key(document_id: str, run_id: str) -> str:
    return f"{document_id}--{run_id}"


def _load_snapshot(path: pathlib.Path) -> dict[str, Any]:
    """Read a recorded benchmark, refusing a shape this generator cannot replay."""

    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise ValueError(f"{path} is not a snapshot object")
    version = snapshot.get("snapshot_version")
    if version != SNAPSHOT_VERSION:
        raise ValueError(
            f"{path} declares snapshot_version {version!r}; "
            f"this generator replays version {SNAPSHOT_VERSION}"
        )
    if not isinstance(snapshot.get("recorded_at"), str):
        raise ValueError(f"{path} is missing required field 'recorded_at'")
    for field in ("build", "configurations", "runs"):
        # Typed, not merely present: a `runs` string would pass a presence check
        # and then fail deep in the build with an AttributeError.
        if not isinstance(snapshot.get(field), dict):
            raise ValueError(f"{path} is missing required field {field!r}")
    for key, recorded in snapshot["runs"].items():
        # One level deeper, for the same reason: `_replayed_payload` calls
        # `.get` on each record, so a non-object run fails mid-build rather
        # than here, where the file being wrong is still the obvious cause.
        if not isinstance(recorded, dict):
            raise ValueError(
                f"{path} records run {key!r} as {type(recorded).__name__}, not an object"
            )
    return snapshot


def _replayed_payload(recorded: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Rebuild the (payload, diagnostics) pair a recorded run originally produced.

    The recorded diagnostics are the raw subprocess stderr, *before* the error and
    traceback lines are folded in -- so a replayed run composes its diagnostic
    artifact exactly once, byte-identically to the run that was recorded.
    """

    payload = {
        field: recorded[field]
        for field in _RECORDED_PAYLOAD_FIELDS
        if recorded.get(field) is not None
    }
    payload.setdefault("status", "bad-output")
    payload.setdefault("text", "")
    return payload, str(recorded.get("diagnostics") or "")


def _snapshot_document(
    artifact: dict[str, Any], recordings: dict[str, Any]
) -> dict[str, Any]:
    """The committed record of a full-fidelity run, for replay where one is impossible.

    The published site is built by CI, which has no vision backend and no budget
    for tens of conversions per commit. Recording a complete local run -- every
    configuration, every document -- lets CI publish those measurements instead of
    a degraded subset it could compute itself.
    """

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "recorded_at": artifact["generated_at"],
        "build": artifact["build"],
        "configurations": {
            name: {
                "label": config["label"],
                "available": config["available"],
                "model": config["model"],
            }
            for name, config in artifact["configurations"].items()
        },
        "runs": recordings,
    }


def _generate_run(
    document: dict[str, Any],
    source: pathlib.Path,
    run: dict[str, Any],
    configurations: dict[str, Any],
    output: pathlib.Path,
    timeout_s: int,
    run_case: RunCase,
    recorded: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = configurations[run["config"]]
    if recorded is None:
        # Bracket the conversion so the tokens attributed to this run are only the
        # ones it spent, and read the counter belonging to *this* run's backend.
        usage_url, usage_model = _ocr_backend_accounting(config)
        usage_before = _read_ocr_usage(usage_url)
        # A configuration may need more wall clock than the global default: a
        # vision model served locally on CPU is orders of magnitude slower than a
        # hosted API, and timing it out would report a Likhit defect where the
        # truth is "this machine is slow".
        payload, diagnostics = run_case(
            source, run, config, int(config.get("timeout_s") or timeout_s)
        )
        ocr_usage = _ocr_usage_record(
            usage_before,
            _read_ocr_usage(usage_url),
            usage_model,
        )
    else:
        # Replaying a recorded run. Nothing is converted and no backend is called,
        # so the usage figures come from the record rather than from live counters.
        payload, diagnostics = _replayed_payload(recorded)
        ocr_usage = recorded.get("ocr_usage")
    text = str(payload.get("text") or "")
    metrics = _text_signals(text) if payload.get("status") == "ok" else {}
    checks = [
        _evaluate_check(check, text, diagnostics, metrics)
        for check in run.get("checks", [])
    ]

    status_ok = payload.get("status") == "ok"
    checks_passed = all(item["passed"] for item in checks)
    if run["expectation"] == "reference":
        outcome = "reference"
    elif run["expectation"] == "blocked":
        outcome = "blocked" if checks_passed else "fail"
    elif run["expectation"] == "known_issue":
        outcome = "pass" if status_ok and checks_passed else "known-issue"
    elif not status_ok:
        outcome = "fail"
    else:
        outcome = "pass" if checks_passed else "fail"

    artifact_id = f"{document['id']}--{run['id']}"
    transcript_path = output / "transcripts" / f"{artifact_id}.md"
    diagnostics_path = output / "diagnostics" / f"{artifact_id}.log"
    diagnostic_parts = [diagnostics.strip()]
    if payload.get("exc_type"):
        diagnostic_parts.append(
            f"{payload['exc_type']}: {payload.get('exc_msg') or '<no message>'}"
        )
    if payload.get("traceback"):
        diagnostic_parts.append(str(payload["traceback"]))
    diagnostic_text = "\n\n".join(part for part in diagnostic_parts if part).strip()

    _transcript_file, transcript_sha = _write_text_artifact(transcript_path, text)
    _diagnostics_file, diagnostics_sha = _write_text_artifact(
        diagnostics_path, diagnostic_text
    )
    label = config["label"]
    if run.get("pages"):
        label = f"{label} / pages {run['pages']}"
    # Everything a later build needs to reproduce this run without performing it.
    # Captured here, where the raw payload and pre-composition stderr are still in
    # hand, rather than reconstructed from the published artifact.
    recording = {
        **{
            field: payload.get(field)
            for field in _RECORDED_PAYLOAD_FIELDS
            if field != "text"
        },
        "text": text,
        "diagnostics": diagnostics,
        "ocr_usage": ocr_usage,
    }
    return {
        "id": run["id"],
        "config": run["config"],
        "label": label,
        "outcome": outcome,
        "status": payload.get("status", "bad-output"),
        "pages": run.get("pages"),
        "wall_s": payload.get("wall_s"),
        "max_rss_mb": payload.get("max_rss_mb"),
        "metrics": metrics,
        "ocr_usage": ocr_usage,
        "checks": checks,
        "transcript": f"transcripts/{transcript_path.name}",
        "transcript_sha256": transcript_sha,
        "diagnostics": f"diagnostics/{diagnostics_path.name}",
        "diagnostics_sha256": diagnostics_sha,
        "error": (
            {
                "type": payload.get("exc_type"),
                "message": payload.get("exc_msg"),
            }
            if payload.get("exc_type")
            else None
        ),
        "excerpt": text[:500],
    }, recording


def _parse_junit(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "status": "not-run",
            "tests": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "duration_s": 0.0,
            "cases": [],
        }

    root = ET.parse(path).getroot()
    cases: list[dict[str, Any]] = []
    for case in root.findall(".//testcase"):
        if case.find("failure") is not None:
            status = "failed"
        elif case.find("error") is not None:
            status = "error"
        elif case.find("skipped") is not None:
            status = "skipped"
        else:
            status = "passed"
        cases.append(
            {
                "name": case.attrib.get("name", ""),
                "classname": case.attrib.get("classname", ""),
                "status": status,
                "duration_s": round(float(case.attrib.get("time", 0)), 3),
            }
        )

    failures = sum(case["status"] == "failed" for case in cases)
    errors = sum(case["status"] == "error" for case in cases)
    skipped = sum(case["status"] == "skipped" for case in cases)
    return {
        "status": "failed" if failures or errors else "passed",
        "tests": len(cases),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "duration_s": round(sum(case["duration_s"] for case in cases), 3),
        "cases": cases,
    }


def _git_value(*arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or None


def _build_metadata(commit: str | None, ref: str | None) -> dict[str, str | None]:
    return {
        "commit": commit or os.getenv("GITHUB_SHA") or _git_value("rev-parse", "HEAD"),
        "ref": ref
        or os.getenv("GITHUB_REF_NAME")
        or _git_value("branch", "--show-current"),
        "python": sys.version.split()[0],
        "likhit": importlib.metadata.version("likhit"),
        "markitdown": importlib.metadata.version("markitdown"),
    }


def _validate_output_target(output: pathlib.Path) -> None:
    resolved = output.resolve()
    repository_root = ROOT.resolve()
    allowed_repository_output = (repository_root / "_site").resolve()
    if (
        output.is_symlink()
        or resolved == pathlib.Path(resolved.anchor)
        or resolved == repository_root
        or resolved in repository_root.parents
        or (
            repository_root in resolved.parents
            and resolved != allowed_repository_output
        )
    ):
        raise ValueError("refusing to replace repository content")
    if output.exists() and not output.is_dir():
        raise ValueError("refusing to replace a non-directory output path")
    if output.exists() and resolved != allowed_repository_output:
        marker = output / OUTPUT_MARKER
        try:
            owned = marker.read_text(encoding="utf-8") == OUTPUT_MARKER_CONTENT
        except OSError:
            owned = False
        if not owned:
            raise ValueError("refusing to replace an unowned output directory")


def _initialize_output(output: pathlib.Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    for directory in (
        output,
        output / "data",
        output / "documents",
        output / "previews",
        output / "transcripts",
        output / "diagnostics",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    for source in STATIC_DIR.iterdir():
        if source.is_file():
            shutil.copy2(source, output / source.name)
    shutil.copy2(SITE_DIR / "schema.json", output / "data" / "schema.json")
    (output / ".nojekyll").touch()
    (output / OUTPUT_MARKER).write_text(OUTPUT_MARKER_CONTENT, encoding="utf-8")


def _prepare_output(output: pathlib.Path) -> None:
    _validate_output_target(output)
    _initialize_output(output)


def _publish_output(staging: pathlib.Path, output: pathlib.Path) -> None:
    _validate_output_target(output)
    backup: pathlib.Path | None = None
    if output.exists():
        backup = pathlib.Path(
            tempfile.mkdtemp(prefix=f".{output.name}-previous-", dir=output.parent)
        )
        backup.rmdir()
        output.rename(backup)
    try:
        staging.rename(output)
    except BaseException:
        if backup is not None and not output.exists():
            backup.rename(output)
        raise
    if backup is not None:
        shutil.rmtree(backup)


def generate(
    output: pathlib.Path,
    *,
    junit: pathlib.Path | None = None,
    source_cache: pathlib.Path | None = None,
    include_public: bool = True,
    include_synthetic: bool = True,
    timeout_s: int = 300,
    commit: str | None = None,
    ref: str | None = None,
    run_case: RunCase = _default_run_case,
    snapshot: pathlib.Path | None = None,
    write_snapshot: pathlib.Path | None = None,
) -> dict[str, Any]:
    # The CLI's mutually-exclusive group covers the command line; this covers
    # every other caller. Recording a replay would stamp the replaying build's
    # commit and a fresh `recorded_at` onto numbers it never measured, which is
    # exactly the provenance confusion `measured` exists to prevent.
    if snapshot is not None and write_snapshot is not None:
        raise ValueError(
            "snapshot and write_snapshot are mutually exclusive: recording a "
            "replay would copy the recording forward under a new timestamp "
            "without measuring anything"
        )
    catalog = _load_catalog()
    snapshot_data = _load_snapshot(snapshot) if snapshot is not None else None
    _validate_output_target(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{output.name}-staging-", dir=output.parent)
    )
    try:
        _initialize_output(staging)

        # Decide once, up front, which configurations can actually run here, so
        # the same catalog produces a coherent artifact on a machine with a vision
        # API, on one with only a local model server, and in CI with neither.
        # Replaying a snapshot inverts the question: what matters is which
        # configurations the *recording* covered, not what this machine can reach,
        # or CI would drop the very OCR columns the snapshot exists to publish.
        # The model id belongs to the configuration, not to a single run: it is
        # the same for every document, and it has to be reportable even for the
        # runs that spent nothing -- which is most of them -- and for a backend
        # with no token counter at all. Read from the environment on a live build,
        # from the recording on a replay.
        #
        # A configuration the recording predates is not the same thing as one whose
        # backend was absent when the recording was made: the first has no
        # measurement at all, the second has a reason. Both end up unavailable, so
        # track which is which -- otherwise those runs would be dropped by the
        # availability check below, ahead of the lookup that names an uncovered
        # run, and `skipped` would rise with nothing named to explain it.
        unrecorded_configurations: set[str] = set()
        if snapshot_data is None:
            availability = {
                name: _ocr_backend_available(config)
                for name, config in catalog["configurations"].items()
            }
            models = {
                name: _ocr_backend_accounting(config)[1]
                for name, config in catalog["configurations"].items()
            }
        else:
            recorded_configurations = snapshot_data["configurations"]
            availability = {
                name: bool(recorded_configurations.get(name, {}).get("available"))
                for name in catalog["configurations"]
            }
            models = {
                name: recorded_configurations.get(name, {}).get("model")
                for name in catalog["configurations"]
            }
            unrecorded_configurations = set(catalog["configurations"]) - set(
                recorded_configurations
            )
        skipped_runs = 0
        recordings: dict[str, Any] = {}
        missing_runs: list[str] = []

        documents: list[dict[str, Any]] = []
        for spec in catalog["documents"]:
            if spec["origin"] == "public-institutional" and not include_public:
                continue
            # The published benchmark carries real government documents only.
            # The synthetic fixtures stay in the catalog because they are the
            # only corpus the generator can build with no network access, which
            # is what test_generator_writes_complete_synthetic_artifact uses.
            if spec["origin"] == "synthetic" and not include_synthetic:
                continue
            source = _materialize_source(spec, staging / "documents", source_cache)
            data = source.read_bytes()
            if (
                spec.get("sha256")
                and not spec.get("publish_pages")
                and _sha256(data) != spec["sha256"]
            ):
                raise ValueError(f"{spec['id']} did not retain its pinned checksum")
            thumbnail, pages = _render_thumbnail(source, staging / "previews")
            runs: list[dict[str, Any]] = []
            for run in spec["runs"]:
                key = _snapshot_key(spec["id"], run["id"])
                if not availability[run["config"]]:
                    skipped_runs += 1
                    if run["config"] in unrecorded_configurations:
                        # Uncovered for want of a recording, not for want of a
                        # backend -- the same case as a missing run, one level up.
                        missing_runs.append(key)
                    continue
                recorded = None
                if snapshot_data is not None:
                    recorded = snapshot_data["runs"].get(key)
                    if recorded is None:
                        # The catalog grew a run the snapshot predates. Skipping is
                        # the only honest option -- there is no measurement to
                        # publish -- but it must be visible, not silent, so the
                        # artifact names it and the dashboard can say so.
                        missing_runs.append(key)
                        skipped_runs += 1
                        continue
                record, recording = _generate_run(
                    spec,
                    source,
                    run,
                    catalog["configurations"],
                    staging,
                    timeout_s,
                    run_case,
                    recorded,
                )
                runs.append(record)
                recordings[key] = recording
            if not runs:
                # Every configuration this document declares was skipped, so there
                # is nothing to show and nothing to assert. Publishing the document
                # anyway would emit an empty `runs` array -- which the schema
                # forbids -- and a row the dashboard would filter out regardless.
                continue
            documents.append(
                {
                    "id": spec["id"],
                    "title": spec["title"],
                    "summary": spec["summary"],
                    "kind": spec["kind"],
                    "publisher": spec["publisher"],
                    "origin": spec["origin"],
                    "privacy": spec["privacy"],
                    "content_note": spec.get("content_note"),
                    "tags": spec["tags"],
                    "source": {
                        "download": f"documents/{source.name}",
                        "original_url": spec.get("url"),
                        "original_sha256": spec.get("sha256"),
                        "sanitization": spec.get("sanitization"),
                        "thumbnail": thumbnail,
                        "bytes": len(data),
                        "sha256": _sha256(data),
                        "pages": pages,
                    },
                    "runs": runs,
                }
            )

        outcomes = [
            run["outcome"] for document in documents for run in document["runs"]
        ]
        build = _build_metadata(commit, ref)
        # Two provenances, deliberately separate: `build` is the commit that
        # published these pages, `measured` is the commit whose behaviour the
        # numbers actually describe. On a live build they are the same thing and
        # `measured` is null; on a replay they can differ, and collapsing them
        # would present recorded numbers as freshly measured ones.
        measured = None
        if snapshot_data is not None:
            measured = {
                "recorded_at": snapshot_data["recorded_at"],
                "build": snapshot_data["build"],
                "missing_runs": sorted(missing_runs),
                "stale": snapshot_data["build"].get("commit") != build["commit"],
            }
        artifact = {
            "schema_version": catalog["schema_version"],
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "build": build,
            "measured": measured,
            "integration": _parse_junit(junit),
            "summary": {
                "documents": len(documents),
                "runs": len(outcomes),
                "pass": outcomes.count("pass"),
                "fail": outcomes.count("fail"),
                "known_issue": outcomes.count("known-issue"),
                "blocked": outcomes.count("blocked"),
                "reference": outcomes.count("reference"),
                "skipped": skipped_runs,
            },
            "configurations": {
                name: {
                    **config,
                    "available": availability[name],
                    "model": models[name],
                    "unavailable_reason": (
                        None
                        if availability[name]
                        # Don't blame a missing backend for a gap in the recording:
                        # on a replay the backend's configuration here says nothing
                        # about a configuration the snapshot never covered.
                        else "not covered by the recording"
                        if name in unrecorded_configurations
                        else _UNAVAILABLE_REASONS.get(
                            config.get("requires"), "backend not configured"
                        )
                    ),
                }
                for name, config in catalog["configurations"].items()
            },
            "documents": documents,
        }
        (staging / "data" / "results.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _publish_output(staging, output)
        if write_snapshot is not None:
            # Only after a successful publish: a build that failed to produce a
            # site should not leave behind a record claiming it did.
            write_snapshot.parent.mkdir(parents=True, exist_ok=True)
            write_snapshot.write_text(
                json.dumps(
                    _snapshot_document(artifact, recordings),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        return artifact
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "_site")
    parser.add_argument("--junit", type=pathlib.Path)
    parser.add_argument("--source-cache", type=pathlib.Path)
    parser.add_argument("--skip-public", action="store_true")
    parser.add_argument("--skip-synthetic", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--commit")
    parser.add_argument("--ref")
    parser.add_argument("--fail-on-regression", action="store_true")
    # Reading a snapshot and writing one in the same invocation would record a
    # replay of a recording -- a copy with a fresh timestamp, and no new
    # measurement anywhere in it.
    snapshots = parser.add_mutually_exclusive_group()
    snapshots.add_argument(
        "--snapshot",
        type=pathlib.Path,
        help="publish runs recorded in this file instead of converting anything",
    )
    snapshots.add_argument(
        "--write-snapshot",
        type=pathlib.Path,
        help="record this build's runs to this file for later replay",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = generate(
        args.output,
        junit=args.junit,
        source_cache=args.source_cache,
        include_public=not args.skip_public,
        include_synthetic=not args.skip_synthetic,
        timeout_s=args.timeout,
        commit=args.commit,
        ref=args.ref,
        snapshot=args.snapshot,
        write_snapshot=args.write_snapshot,
    )
    summary = artifact["summary"]
    print(
        f"generated {summary['documents']} documents and "
        f"{summary['runs']} runs in {args.output}"
    )
    if artifact["measured"]:
        measured = artifact["measured"]
        print(
            f"replayed runs recorded at {measured['build'].get('commit')} "
            f"on {measured['recorded_at']}"
        )
        if measured["missing_runs"]:
            print(
                f"warning: {len(measured['missing_runs'])} catalog runs are absent "
                f"from the snapshot and were skipped: "
                f"{', '.join(measured['missing_runs'])}"
            )
    if args.write_snapshot is not None:
        available = sorted(
            name
            for name, config in artifact["configurations"].items()
            if config["available"]
        )
        print(
            f"recorded {summary['runs']} runs to {args.write_snapshot} "
            f"covering {len(available)} configurations: {', '.join(available)}"
        )
    return int(args.fail_on_regression and summary["fail"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
