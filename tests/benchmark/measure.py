"""Measure likhit's conversion of a corpus, and diff two measurements.

Two commands:

``run``
    Convert every file in one or more directories, each in an isolated
    subprocess, and write one JSON record per file.

``diff``
    Compare two runs and classify every file as ``same`` / ``better`` /
    ``worse`` / ``newly-failing`` / ``newly-passing``. Exits non-zero if any file
    regressed, so it can gate CI.

The point of ``diff`` is that likhit's own quality signals cannot tell repaired
Devanagari from mojibake (the mojibake sometimes scores *higher*), so "the tests
pass" is not evidence that a change did not make output worse on real documents.
Quality is judged by malformed-Devanagari counts, not by character or code-point
totals.

Usage::

    python -m tests.benchmark.measure run samples --out before.jsonl
    python -m tests.benchmark.measure diff before.jsonl after.jsonl --report r.md
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import subprocess
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tests.devanagari_quality import devanagari_quality

CONVERTIBLE_SUFFIXES = frozenset({".pdf", ".docx", ".doc"})
DEVANAGARI_RANGE = (0x0900, 0x097F)
PRIVATE_USE_RANGE = (0xE000, 0xF8FF)
# Per-file wall-clock ceiling. The slowest real corpus document takes ~90 s, and
# a crafted GSUB chain can run for minutes, so this bounds the pathological case
# without truncating legitimate work.
DEFAULT_TIMEOUT_S = 300


def _file_id(path: pathlib.Path) -> str:
    """Return a stable, collision-free identifier for a corpus input."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(pathlib.Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _text_signals(text: str) -> dict[str, object]:
    """Quality and damage signals for one conversion's markdown."""

    letters = [char for char in text if char.isalpha()]
    devanagari = sum(
        1 for char in letters if DEVANAGARI_RANGE[0] <= ord(char) <= DEVANAGARI_RANGE[1]
    )
    latin = sum(1 for char in letters if ord(char) < 0x250)
    quality = devanagari_quality(text)
    return {
        "chars": len(text),
        "letters": len(letters),
        "devanagari": devanagari,
        "deva_frac": round(devanagari / len(letters), 4) if letters else None,
        "latin_frac": round(latin / len(letters), 4) if letters else None,
        "replacement": text.count("�"),
        "pua": sum(
            1
            for char in text
            if PRIVATE_USE_RANGE[0] <= ord(char) <= PRIVATE_USE_RANGE[1]
        ),
        "control": sum(
            1
            for char in text
            if unicodedata.category(char) == "Cc" and char not in "\n\r\t"
        ),
        "cid_garbage": text.count("(cid:"),
        "malformed": quality["malformed"],
        "stranded": quality["stranded"],
        "stranded_matras": quality["stranded_matras"],
        "doubled_matras": quality["doubled"],
        "matras": quality["matras"],
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def _convert_one(
    path: pathlib.Path, *, pages: str | None, timeout_s: int
) -> dict[str, object]:
    command = [sys.executable, "-m", "tests.benchmark.run_one", str(path)]
    if pages:
        command.append(pages)

    record: dict[str, object] = {"file": _file_id(path), "pages": pages}
    try:
        record["bytes"] = path.stat().st_size
        record["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        record["status"] = "unreadable_source"
        record["exc_type"] = type(exc).__name__
        record["exc_msg"] = str(exc)[:2000]
        return record

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout_s,
            cwd=pathlib.Path(__file__).resolve().parents[2],
            check=False,
        )
    except subprocess.TimeoutExpired:
        # A hang is a result, not a crash of the run. Recording it is the whole
        # reason each file gets its own process.
        record["status"] = "timeout"
        record["wall_s"] = timeout_s
        return record

    if completed.returncode != 0 and not completed.stdout:
        # Negative return codes are POSIX signals; positive values are ordinary
        # worker failures that happened before a record could be emitted.
        record["status"] = "killed" if completed.returncode < 0 else "worker_error"
        record["returncode"] = completed.returncode
        record["stderr_tail"] = completed.stderr.decode(errors="replace")[-2000:]
        return record

    try:
        payload = json.loads(completed.stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        record["status"] = "bad_output"
        record["stdout_head"] = completed.stdout.decode(errors="replace")[:2000]
        return record

    record["status"] = payload.get("status") or "bad_output"
    record["wall_s"] = payload.get("wall_s")
    record["max_rss_mb"] = payload.get("max_rss_mb")
    if payload.get("status") == "ok":
        record.update(_text_signals(payload.get("text") or ""))
    else:
        record["exc_type"] = payload.get("exc_type")
        record["exc_msg"] = payload.get("exc_msg")
    # Native MuPDF diagnostics are a signal in their own right: likhit neither
    # suppresses nor surfaces them, and one corpus PDF emits 192 lines.
    stderr = completed.stderr.decode(errors="replace")
    if stderr.strip():
        record["native_stderr_lines"] = len(stderr.strip().splitlines())
    return record


def _iter_files(roots: list[str]) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for root in roots:
        path = pathlib.Path(root)
        if path.is_file():
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(path)
            continue
        for candidate in sorted(path.rglob("*")):
            if (
                not candidate.is_file()
                or candidate.suffix.lower() not in CONVERTIBLE_SUFFIXES
            ):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(candidate)
    return files


def command_run(args: argparse.Namespace) -> int:
    files = _iter_files(args.corpus)
    if not files:
        print(f"no convertible files under {args.corpus}", file=sys.stderr)
        return 1

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _convert_one, path, pages=args.pages, timeout_s=args.timeout
            ): path
            for path in files
        }
        for done in concurrent.futures.as_completed(futures):
            record = done.result()
            records.append(record)
            wall_s = record.get("wall_s")
            wall_display = "?" if wall_s is None else str(wall_s)
            print(
                f"  {record['status']:<11} {wall_display:>8}s  "
                f"{record['file'][:58]}",
                file=sys.stderr,
            )

    records.sort(key=lambda item: str(item["file"]))
    with out_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    ok = sum(1 for record in records if record["status"] == "ok")
    print(f"\n{ok}/{len(records)} converted -> {out_path}", file=sys.stderr)
    return 0


def _load(path: str) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        file_id = str(record["file"])
        if file_id in records:
            raise ValueError(f"duplicate record key {file_id!r} in {path}")
        records[file_id] = record
    return records


def _classify(before: dict[str, object], after: dict[str, object]) -> tuple[str, str]:
    """Classify one file's change as (verdict, reason)."""

    if before.get("status") == "ok" and after.get("status") != "ok":
        return "newly-failing", f"{before['status']} -> {after.get('status')}"
    if before.get("status") != "ok" and after.get("status") == "ok":
        return "newly-passing", f"{before.get('status')} -> ok"
    if before.get("status") != "ok" and after.get("status") != "ok":
        return "same", "failing in both"

    if before.get("sha256") == after.get("sha256"):
        return "same", "byte-identical"

    # Output changed, so judge it. Malformed-Devanagari count is the primary
    # signal: character and code-point totals cannot separate repaired text from
    # mojibake, because corruption reorders glyphs rather than deleting them.
    malformed_delta = int(after.get("malformed", 0)) - int(before.get("malformed", 0))
    deva_delta = int(after.get("devanagari", 0)) - int(before.get("devanagari", 0))
    damage_delta = (
        int(after.get("replacement", 0))
        + int(after.get("cid_garbage", 0))
        - int(before.get("replacement", 0))
        - int(before.get("cid_garbage", 0))
    )

    # The malformed count is blind to LATIN mojibake: text with no Devanagari at
    # all scores 0, so a change that turned correct Nepali into legacy-keystroke
    # Latin would look clean by that measure. A collapse in the Devanagari share
    # of letters is the signal that catches it.
    before_frac = before.get("deva_frac")
    after_frac = after.get("deva_frac")
    script_collapse = (
        before_frac is not None
        and after_frac is not None
        and float(before_frac) >= 0.3
        and float(after_frac) < float(before_frac) * 0.5
    )
    content_loss = (
        int(before.get("devanagari", 0)) > 100
        and int(after.get("devanagari", 0)) < int(before.get("devanagari", 0)) * 0.9
    )

    if script_collapse:
        return "worse", f"devanagari share {before_frac} -> {after_frac}"
    if content_loss:
        return "worse", f"devanagari {deva_delta:+d} (>10% of content lost)"
    if malformed_delta > 0 or damage_delta > 0:
        return "worse", f"malformed {malformed_delta:+d}, damage {damage_delta:+d}"
    if malformed_delta < 0 or deva_delta > 0:
        return "better", f"malformed {malformed_delta:+d}, devanagari {deva_delta:+d}"
    return "changed", f"devanagari {deva_delta:+d}, text differs"


def command_diff(args: argparse.Namespace) -> int:
    before = _load(args.before)
    after = _load(args.after)
    allowlist = set(args.allow or [])

    verdicts: list[tuple[str, str, str]] = []
    for name in sorted(before.keys() | after.keys()):
        if name not in before:
            verdicts.append((name, "new-file", "not in baseline"))
        elif name not in after:
            verdicts.append((name, "missing", "not in new run"))
        else:
            verdict, reason = _classify(before[name], after[name])
            verdicts.append((name, verdict, reason))

    counts: dict[str, int] = {}
    for _name, verdict, _reason in verdicts:
        counts[verdict] = counts.get(verdict, 0) + 1

    lines = ["# Corpus regression report", ""]
    lines.append(f"Baseline `{args.before}` vs `{args.after}`")
    lines.append("")
    for verdict, count in sorted(counts.items()):
        lines.append(f"- **{verdict}**: {count}")
    lines.append("")

    regressions = [
        (name, reason)
        for name, verdict, reason in verdicts
        if verdict in {"worse", "newly-failing", "missing"} and name not in allowlist
    ]
    notable = [
        (name, verdict, reason)
        for name, verdict, reason in verdicts
        if verdict not in {"same"}
    ]
    if notable:
        lines.extend(["| file | verdict | detail |", "|---|---|---|"])
        for name, verdict, reason in notable:
            flag = " (allowed)" if name in allowlist else ""
            lines.append(f"| `{name}` | {verdict}{flag} | {reason} |")
        lines.append("")

    # Timing is reported but never gates: it is noisy on shared runners.
    before_wall = sum(float(r.get("wall_s") or 0) for r in before.values())
    after_wall = sum(float(r.get("wall_s") or 0) for r in after.values())
    if before_wall:
        lines.append(
            f"Total wall time {before_wall:.1f}s -> {after_wall:.1f}s "
            f"({(after_wall - before_wall) / before_wall:+.1%})"
        )

    report = "\n".join(lines) + "\n"
    if args.report:
        pathlib.Path(args.report).write_text(report, encoding="utf-8")
    print(report)

    if regressions:
        print(f"REGRESSION: {len(regressions)} file(s) got worse", file=sys.stderr)
        for name, reason in regressions:
            print(f"  {name}: {reason}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure", description="Measure and diff likhit conversion quality."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="convert a corpus and record signals")
    run.add_argument("corpus", nargs="+", help="files or directories to convert")
    run.add_argument("--out", required=True, help="output JSONL path")
    run.add_argument("--pages", help="optional page selection passed to every file")
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    run.set_defaults(func=command_run)

    diff = sub.add_parser("diff", help="compare two runs")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--report", help="write a Markdown report here")
    diff.add_argument(
        "--allow",
        nargs="*",
        help="file identifiers permitted to regress (intentional output changes)",
    )
    diff.set_defaults(func=command_diff)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
