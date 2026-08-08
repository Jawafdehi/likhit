# SPDX-License-Identifier: Hippocratic-3.0
"""Render a benchmark artifact as a Markdown summary for a CI job summary.

The published numbers are otherwise only visible by deploying the site or
downloading the Pages artifact, which makes a regression easy to miss in a run
that is green overall. Writing them to `$GITHUB_STEP_SUMMARY` puts them on the
run page itself.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

# Outcomes worth a column, in the order a reader cares about them. Skipped runs
# are deliberately absent: a skipped run produces no run record, so it could only
# ever be counted at the top level, and a per-configuration column for it would
# read as a measured zero.
OUTCOME_COLUMNS = (
    ("fail", "Failed"),
    ("pass", "Passed"),
    ("known_issue", "Known"),
    ("blocked", "Blocked"),
    ("reference", "Reference"),
)


def _short(commit: str | None) -> str:
    return commit[:8] if commit else "unknown"


def _provenance(artifact: dict[str, Any]) -> list[str]:
    build = artifact["build"]
    measured = artifact.get("measured")
    if not measured:
        return [
            f"Measured by this build at `{_short(build.get('commit'))}` "
            f"with Likhit {build.get('likhit')}.",
        ]

    recorded = measured["build"]
    lines = [
        f"Replayed from a recording measured at `{_short(recorded.get('commit'))}` "
        f"on {measured['recorded_at']} with Likhit {recorded.get('likhit')}; "
        f"published from `{_short(build.get('commit'))}`.",
    ]
    if measured.get("stale"):
        lines.append(
            "> [!WARNING]\n"
            "> The recorded commit is not the commit being published. These "
            "numbers describe older behaviour — refresh `site/snapshot.json` if "
            "conversion behaviour changed."
        )
    if measured.get("missing_runs"):
        missing = measured["missing_runs"]
        lines.append(
            "> [!WARNING]\n"
            f"> {len(missing)} catalog run(s) are absent from the recording and "
            f"were skipped: {', '.join(f'`{name}`' for name in missing)}."
        )
    return lines


def _configuration_rows(artifact: dict[str, Any]) -> list[str]:
    """One row per configuration, with the outcomes belonging to it.

    A single total hides the thing most worth seeing: which OCR backend a
    regression landed in, and which configurations produced no runs at all.
    """

    tally: dict[str, dict[str, int]] = {
        name: dict.fromkeys([key for key, _ in OUTCOME_COLUMNS], 0)
        for name in artifact["configurations"]
    }
    for document in artifact["documents"]:
        for run in document["runs"]:
            counts = tally.get(run["config"])
            if counts is None:
                continue
            key = run["outcome"].replace("-", "_")
            if key in counts:
                counts[key] += 1

    # Tokens per configuration, so the spend of each OCR backend is attributable.
    # Runs that made no vision call contribute a measured zero rather than being
    # dropped, which is why this sums `calls` as well as tokens.
    spend: dict[str, dict[str, int]] = {
        name: {"calls": 0, "tokens": 0} for name in artifact["configurations"]
    }
    for document in artifact["documents"]:
        for run in document["runs"]:
            usage = run.get("ocr_usage")
            if usage and run["config"] in spend:
                spend[run["config"]]["calls"] += usage["calls"]
                spend[run["config"]]["tokens"] += usage["total_tokens"]

    rows = []
    for name, config in artifact["configurations"].items():
        counts = tally[name]
        total = sum(counts.values())
        if config.get("available"):
            note = f"{total} run(s)"
            if config.get("model"):
                note += f" · `{config['model']}`"
            calls = spend[name]["calls"]
            if calls:
                note += f" · {calls} vision call(s), {spend[name]['tokens']:,} tokens"
        else:
            note = f"not run — {config.get('unavailable_reason') or 'unavailable'}"
        cells = " | ".join(
            str(counts[key]) if config.get("available") else "—"
            for key, _ in OUTCOME_COLUMNS
        )
        rows.append(f"| {config['label']} | {cells} | {note} |")
    return rows


def render(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    integration = artifact["integration"]
    headers = " | ".join(label for _, label in OUTCOME_COLUMNS)
    dividers = " | ".join("---:" for _ in OUTCOME_COLUMNS)

    lines = [
        "## Likhit benchmark",
        "",
        *_provenance(artifact),
        "",
        f"**{summary['documents']} documents · {summary['runs']} runs · "
        f"{summary['fail']} failed · {summary['skipped']} skipped**",
        "",
        f"| Configuration | {headers} | Notes |",
        f"| --- | {dividers} | --- |",
        *_configuration_rows(artifact),
        "",
    ]

    if integration["status"] == "not-run":
        lines.append("Integration suite: not attached to this build.")
    else:
        executed = integration["tests"] - integration["skipped"]
        passed = executed - integration["failures"] - integration["errors"]
        lines.append(
            f"Integration suite: **{integration['status']}** — {passed}/{executed} "
            f"passed, {integration['skipped']} skipped."
        )

    failures = [
        (document["title"], run["label"], run.get("error") or {})
        for document in artifact["documents"]
        for run in document["runs"]
        if run["outcome"] == "fail"
    ]
    if failures:
        lines += ["", "### Failures", ""]
        for title, label, error in failures:
            detail = error.get("message") or "assertions did not hold"
            lines.append(f"- **{title}** — {label}: {detail.splitlines()[0][:200]}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    args = parser.parse_args(argv)

    if not args.artifact.is_file():
        # Called with `if: always()`, so the generator may have failed before
        # writing anything. Say so and succeed -- masking the real failure with a
        # second one helps nobody.
        print(f"No benchmark artifact at `{args.artifact}`; nothing to summarize.")
        return 0

    print(render(json.loads(args.artifact.read_text(encoding="utf-8"))), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
