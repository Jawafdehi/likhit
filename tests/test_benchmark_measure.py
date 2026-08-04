"""Tests for the corpus measurement harness.

The harness is what makes an extraction change falsifiable, so its classifier
needs its own tests: a differ that cannot fail would silently bless every
regression it is meant to catch.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.benchmark.measure import _classify, _text_signals, main

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
