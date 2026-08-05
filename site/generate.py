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


def _download_public_source(
    spec: dict[str, Any],
    source_cache: pathlib.Path | None,
    *,
    attempts: int = 3,
    open_url: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    cached = _cached_public_source(spec, source_cache)
    if cached is not None:
        data = cached.read_bytes()
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
                    data = response.read()
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
    path.write_text(value, encoding="utf-8")
    return path.as_posix(), _sha256(value.encode())


def _generate_run(
    document: dict[str, Any],
    source: pathlib.Path,
    run: dict[str, Any],
    configurations: dict[str, Any],
    output: pathlib.Path,
    timeout_s: int,
    run_case: RunCase,
) -> dict[str, Any]:
    config = configurations[run["config"]]
    payload, diagnostics = run_case(source, run, config, timeout_s)
    text = str(payload.get("text") or "")
    metrics = _text_signals(text) if payload.get("status") == "ok" else {}
    checks = [
        _evaluate_check(check, text, diagnostics, metrics)
        for check in run.get("checks", [])
    ]

    if payload.get("status") != "ok":
        outcome = "fail"
    elif run["expectation"] == "reference":
        outcome = "reference"
    elif run["expectation"] == "blocked":
        outcome = "blocked" if all(item["passed"] for item in checks) else "fail"
    elif run["expectation"] == "known_issue":
        outcome = "pass" if all(item["passed"] for item in checks) else "known-issue"
    else:
        outcome = "pass" if all(item["passed"] for item in checks) else "fail"

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
    }


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


def _prepare_output(output: pathlib.Path) -> None:
    resolved = output.resolve()
    repository_root = ROOT.resolve()
    allowed_repository_output = (repository_root / "_site").resolve()
    if (
        resolved == pathlib.Path(resolved.anchor)
        or resolved == repository_root
        or (
            repository_root in resolved.parents
            and resolved != allowed_repository_output
        )
    ):
        raise ValueError("refusing to replace repository content")
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


def generate(
    output: pathlib.Path,
    *,
    junit: pathlib.Path | None = None,
    source_cache: pathlib.Path | None = None,
    include_public: bool = True,
    timeout_s: int = 300,
    commit: str | None = None,
    ref: str | None = None,
    run_case: RunCase = _default_run_case,
) -> dict[str, Any]:
    catalog = _load_catalog()
    _prepare_output(output)

    documents: list[dict[str, Any]] = []
    for spec in catalog["documents"]:
        if spec["origin"] == "public-institutional" and not include_public:
            continue
        source = _materialize_source(spec, output / "documents", source_cache)
        data = source.read_bytes()
        if (
            spec.get("sha256")
            and not spec.get("publish_pages")
            and _sha256(data) != spec["sha256"]
        ):
            raise ValueError(f"{spec['id']} did not retain its pinned checksum")
        thumbnail, pages = _render_thumbnail(source, output / "previews")
        runs = [
            _generate_run(
                spec,
                source,
                run,
                catalog["configurations"],
                output,
                timeout_s,
                run_case,
            )
            for run in spec["runs"]
        ]
        documents.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "summary": spec["summary"],
                "kind": spec["kind"],
                "publisher": spec["publisher"],
                "origin": spec["origin"],
                "privacy": spec["privacy"],
                "screening": spec.get("screening"),
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

    outcomes = [run["outcome"] for document in documents for run in document["runs"]]
    artifact = {
        "schema_version": catalog["schema_version"],
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "build": _build_metadata(commit, ref),
        "integration": _parse_junit(junit),
        "summary": {
            "documents": len(documents),
            "runs": len(outcomes),
            "pass": outcomes.count("pass"),
            "fail": outcomes.count("fail"),
            "known_issue": outcomes.count("known-issue"),
            "blocked": outcomes.count("blocked"),
            "reference": outcomes.count("reference"),
        },
        "configurations": catalog["configurations"],
        "documents": documents,
    }
    (output / "data" / "results.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return artifact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "_site")
    parser.add_argument("--junit", type=pathlib.Path)
    parser.add_argument("--source-cache", type=pathlib.Path)
    parser.add_argument("--skip-public", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--commit")
    parser.add_argument("--ref")
    parser.add_argument("--fail-on-regression", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = generate(
        args.output,
        junit=args.junit,
        source_cache=args.source_cache,
        include_public=not args.skip_public,
        timeout_s=args.timeout,
        commit=args.commit,
        ref=args.ref,
    )
    print(
        f"generated {artifact['summary']['documents']} documents and "
        f"{artifact['summary']['runs']} runs in {args.output}"
    )
    return int(args.fail_on_regression and artifact["summary"]["fail"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
