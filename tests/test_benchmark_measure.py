"""Tests for the corpus measurement harness.

The harness is what makes an extraction change falsifiable, so its classifier
needs its own tests: a differ that cannot fail would silently bless every
regression it is meant to catch.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from tests.benchmark.measure import (
    _classify,
    _convert_one,
    _file_id,
    _load,
    _text_signals,
    main,
)
from tests.benchmark.run_one import _write_all

BASE = {
    "status": "ok",
    "sha256": "aaa",
    "chars": 1000,
    "devanagari": 800,
    "deva_frac": 0.95,
    "latin_frac": 0.05,
    "malformed": 4,
    "replacement": 0,
    "cid_garbage": 0,
}


def _after(**overrides: object) -> dict[str, object]:
    record = dict(BASE)
    record["sha256"] = "bbb"  # any judged change implies the text differs
    record.update(overrides)
    return record


def test_byte_identical_output_is_same() -> None:
    assert _classify(BASE, dict(BASE))[0] == "same"


def test_more_malformed_devanagari_is_worse() -> None:
    assert _classify(BASE, _after(malformed=54))[0] == "worse"


def test_fewer_malformed_devanagari_is_better() -> None:
    assert _classify(BASE, _after(malformed=0))[0] == "better"


def test_new_replacement_chars_are_worse() -> None:
    assert _classify(BASE, _after(replacement=12))[0] == "worse"


def test_new_cid_garbage_is_worse() -> None:
    assert _classify(BASE, _after(cid_garbage=7))[0] == "worse"


def test_script_collapse_is_worse_even_when_malformed_is_zero() -> None:
    """Latin mojibake scores malformed=0, so that signal alone is blind to it.

    A conversion that turns correct Nepali into legacy-keystroke Latin has no
    Devanagari left to be malformed. Measured on one sample: 13,527 chars,
    deva_frac 0.0, malformed 0. Only the Devanagari share catches it.
    """

    collapsed = _after(deva_frac=0.0, latin_frac=1.0, devanagari=0, malformed=0)
    verdict, reason = _classify(BASE, collapsed)
    assert verdict == "worse"
    assert "share" in reason


def test_silent_content_loss_is_worse() -> None:
    """Dropping content without introducing damage must still fail.

    This is the shape of the page-furniture bug: whole paragraphs vanish while
    everything that remains is perfectly well-formed.
    """

    verdict, reason = _classify(BASE, _after(devanagari=400))
    assert verdict == "worse"
    assert "lost" in reason


def test_newly_failing_is_worse_than_worse() -> None:
    assert _classify(BASE, {"status": "timeout"})[0] == "newly-failing"


def test_newly_passing_is_reported() -> None:
    assert _classify({"status": "error"}, dict(BASE))[0] == "newly-passing"


def test_failing_in_both_runs_is_same() -> None:
    assert _classify({"status": "error"}, {"status": "timeout"})[0] == "same"


def test_more_devanagari_with_no_new_damage_is_better() -> None:
    assert _classify(BASE, _after(devanagari=1300))[0] == "better"


@pytest.mark.parametrize(
    "verdict_case,expected_exit",
    [("identical", 0), ("worse", 1), ("better", 0)],
)
def test_diff_exit_code_gates_on_regressions(
    tmp_path: pathlib.Path, verdict_case: str, expected_exit: int
) -> None:
    """The differ must exit non-zero on regression so it can gate CI."""

    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(json.dumps({"file": "x.pdf", **BASE}) + "\n", encoding="utf-8")

    if verdict_case == "identical":
        payload = {"file": "x.pdf", **BASE}
    elif verdict_case == "worse":
        payload = {"file": "x.pdf", **_after(malformed=99)}
    else:
        payload = {"file": "x.pdf", **_after(malformed=0)}
    after.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert main(["diff", str(before), str(after)]) == expected_exit


def test_allowlisted_regression_does_not_fail_the_run(tmp_path: pathlib.Path) -> None:
    """Intentional output changes need an escape hatch, named explicitly."""

    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(json.dumps({"file": "x.pdf", **BASE}) + "\n", encoding="utf-8")
    after.write_text(
        json.dumps({"file": "x.pdf", **_after(malformed=99)}) + "\n", encoding="utf-8"
    )

    assert main(["diff", str(before), str(after)]) == 1
    assert main(["diff", str(before), str(after), "--allow", "x.pdf"]) == 0


def test_missing_file_gates_the_run(tmp_path: pathlib.Path) -> None:
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(
        json.dumps({"file": "x.pdf", **BASE})
        + "\n"
        + json.dumps({"file": "y.pdf", **BASE})
        + "\n",
        encoding="utf-8",
    )
    after.write_text(
        json.dumps({"file": "x.pdf", **BASE}) + "\n",
        encoding="utf-8",
    )

    assert main(["diff", str(before), str(after)]) == 1
    assert main(["diff", str(before), str(after), "--allow", "y.pdf"]) == 0


def test_text_signals_report_devanagari_and_damage() -> None:
    signals = _text_signals("अख्तियार दुरुपयोग")
    assert signals["devanagari"] > 0
    assert signals["deva_frac"] == 1.0
    assert signals["malformed"] == 0
    assert signals["replacement"] == 0

    damaged = _text_signals("पृ�भूिम (cid:2)")
    assert damaged["replacement"] == 1
    assert damaged["cid_garbage"] == 1


def test_text_signals_are_stable_for_identical_text() -> None:
    """sha256 lets a performance change assert byte-identical output."""

    assert _text_signals("क")["sha256"] == _text_signals("क")["sha256"]
    assert _text_signals("क")["sha256"] != _text_signals("ख")["sha256"]


def test_file_ids_distinguish_duplicate_basenames(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first" / "report.pdf"
    second = tmp_path / "second" / "report.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.touch()
    second.touch()
    monkeypatch.chdir(tmp_path)

    assert _file_id(first) == "first/report.pdf"
    assert _file_id(second) == "second/report.pdf"


def test_load_rejects_duplicate_record_ids(tmp_path: pathlib.Path) -> None:
    measurements = tmp_path / "measurements.jsonl"
    record = json.dumps({"file": "same/report.pdf", **BASE})
    measurements.write_text(f"{record}\n{record}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate record key"):
        _load(str(measurements))


def test_progress_handles_missing_wall_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "sample.pdf"
    source.touch()
    output = tmp_path / "measurements.jsonl"

    def fake_convert(
        path: pathlib.Path, *, pages: str | None, timeout_s: int
    ) -> dict[str, object]:
        del pages, timeout_s
        return {"file": _file_id(path), "status": "bad_output", "wall_s": None}

    monkeypatch.setattr("tests.benchmark.measure._convert_one", fake_convert)

    assert (
        main(
            [
                "run",
                str(source),
                "--out",
                str(output),
                "--workers",
                "1",
            ]
        )
        == 0
    )
    assert "?s" in capsys.readouterr().err


def test_positive_worker_exit_is_not_reported_as_signal_kill(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample.pdf"
    source.touch()
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=2,
        stdout=b"",
        stderr=b"worker setup failed",
    )
    monkeypatch.setattr(
        "tests.benchmark.measure.subprocess.run", lambda *a, **k: completed
    )

    record = _convert_one(source, pages=None, timeout_s=1)

    assert record["status"] == "worker_error"
    assert record["returncode"] == 2


def test_write_all_retries_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    written = bytearray()

    def short_write(_file_descriptor: int, data: bytes | memoryview) -> int:
        chunk = bytes(data[:3])
        written.extend(chunk)
        return len(chunk)

    monkeypatch.setattr("tests.benchmark.run_one.os.write", short_write)

    _write_all(1, b"complete record")

    assert written == b"complete record"


def test_run_one_reports_import_failures_as_json(tmp_path: pathlib.Path) -> None:
    """Dependency failures must remain distinguishable from native process kills."""

    (tmp_path / "markitdown.py").write_text(
        'raise RuntimeError("synthetic import failure")\n', encoding="utf-8"
    )
    runner = pathlib.Path(__file__).parent / "benchmark" / "run_one.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(tmp_path), env.get("PYTHONPATH")) if part
    )

    completed = subprocess.run(
        [sys.executable, str(runner), "unused.pdf"],
        capture_output=True,
        check=True,
        env=env,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "error"
    assert payload["exc_type"] == "RuntimeError"
    assert payload["exc_msg"] == "synthetic import failure"
