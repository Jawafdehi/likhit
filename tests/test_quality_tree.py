"""Walking a tree, and the guard that stops a non-measurement reading as a clean one."""

from __future__ import annotations

import json
import unicodedata

import pytest

from likhit.quality import assess_tree, audit_document, audit_tree

_REPORT = (
    "नेपाल सरकार\nमहालेखापरीक्षकको कार्यालय\n\n"
    "आर्थिक वर्ष २०७४/७५ को बेरुजु रकम रु. ९,८२,८४,२८८ रहेको छ।\n"
    "जम्मा खर्च रु. ५,२३,९६,९०८ र राजस्व रु. ४,०७,००,७९२ छ।\n"
) * 10


def _tree(tmp_path, count=3):
    root = tmp_path / "markdown"
    (root / "bucket").mkdir(parents=True)
    for index in range(count):
        (root / "bucket" / f"{index}__doc.md").write_text(_REPORT, encoding="utf-8")
    return root


def test_a_missing_tree_is_refused_rather_than_audited_as_empty(tmp_path) -> None:
    """🛑 The failure this guard exists for, and it is not hypothetical.

    ``rglob`` yields nothing and raises nothing for a missing path, a plain file, a dangling
    symlink and an unreadable directory alike. Without this, auditing a typo'd path writes a
    report of zero records and exits 0 -- and a verdict band read off zero documents is not a
    clean band, it is no measurement at all.
    """

    ok, why = assess_tree(tmp_path / "nope", 0)
    assert not ok
    assert "does not exist" in why

    with pytest.raises(ValueError, match="does not exist"):
        audit_tree(tmp_path / "nope")


def test_a_plain_file_is_refused_too(tmp_path) -> None:
    """The same symptom by a different route, so it needs its own assertion."""

    path = tmp_path / "a.md"
    path.write_text(_REPORT, encoding="utf-8")

    ok, why = assess_tree(path, 0)
    assert not ok
    assert "is not a directory" in why


def test_an_existing_but_empty_tree_is_refused(tmp_path) -> None:
    """A conversion that made its output directory and then died."""

    root = tmp_path / "markdown"
    root.mkdir()

    ok, why = assess_tree(root, 0)
    assert not ok
    assert "no *.md" in why


def test_a_tree_below_the_callers_floor_is_refused(tmp_path) -> None:
    """Only the caller can state the scale; nothing intrinsic to a tree does."""

    root = _tree(tmp_path, count=2)

    assert assess_tree(root, 2)[0] is True
    assert assess_tree(root, 2, min_transcripts=100)[0] is False
    with pytest.raises(ValueError, match="below the stated floor"):
        audit_tree(root, min_transcripts=100)


def test_limit_is_applied_before_the_floor_is_checked(tmp_path) -> None:
    """A deliberately truncated audit is a real audit of a stated subset.

    It should only trip ``min_transcripts`` if the caller asked for both at once -- which is
    a contradiction worth surfacing, not a silent truncation.
    """

    root = _tree(tmp_path, count=5)

    assert len(audit_tree(root, limit=2)) == 2
    with pytest.raises(ValueError, match="below the stated floor"):
        audit_tree(root, limit=2, min_transcripts=5)


def test_an_unreadable_document_is_a_finding_not_an_exception(tmp_path) -> None:
    """One bad file must not lose the other results, and is itself worth reporting."""

    root = _tree(tmp_path, count=1)
    broken = root / "bucket" / "broken__doc.md"
    broken.mkdir()  # a directory named *.md: read_text raises OSError

    rows = audit_tree(root)

    assert len(rows) == 2
    bad = next(r for r in rows if r["md"].endswith("broken__doc.md"))
    assert bad["verdict"] == "garbled"
    assert "read" in bad["checks"]


def test_rows_are_ordered_the_same_way_with_and_without_workers(tmp_path) -> None:
    """``as_completed`` yields in finish order, which would make two runs incomparable."""

    root = _tree(tmp_path, count=6)

    serial = [row["md"] for row in audit_tree(root, workers=1)]
    parallel = [row["md"] for row in audit_tree(root, workers=3)]

    assert serial == parallel == sorted(serial)


def test_enrich_runs_in_the_parent_and_joins_onto_the_row(tmp_path) -> None:
    """The seam that keeps corpus schema out of the library.

    ``enrich`` is called in the parent process, so a caller can close over anything --
    a sidecar reader, a database handle -- without it needing to be picklable.
    """

    root = _tree(tmp_path, count=2)
    unpicklable = lambda path: {"stem": path.stem}  # noqa: E731 - the point is a closure

    rows = audit_tree(root, workers=2, enrich=unpicklable)

    assert all(row["meta"]["stem"].endswith("__doc") for row in rows)


def test_auditing_a_document_does_not_depend_on_a_sidecar(tmp_path) -> None:
    """The library must not require the corpus's ``.json`` shape to produce a verdict."""

    root = _tree(tmp_path, count=1)
    only = next((root / "bucket").glob("*.md"))

    row = audit_document(only)

    assert row["verdict"] in {"clean", "suspect", "garbled"}
    assert "meta" not in row
    assert unicodedata.normalize("NFC", only.read_text(encoding="utf-8"))


def test_the_cli_exits_non_zero_on_a_tree_it_cannot_evaluate(tmp_path, capsys) -> None:
    """The guard has to reach the exit code, or a CI step reads a non-measurement as a pass.

    This is the whole point of `assess_tree`: a report of zero records with exit 0 is
    indistinguishable, to any caller, from a clean corpus.
    """

    from likhit.quality._cli import main

    assert main([str(tmp_path / "missing")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_the_cli_reports_bands_for_a_real_tree(tmp_path, capsys) -> None:
    from likhit.quality._cli import main

    root = _tree(tmp_path, count=3)
    out = tmp_path / "report.json"

    assert main([str(root), "--out", str(out)]) == 0

    captured = capsys.readouterr().out
    assert "audited 3 transcripts" in captured
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["documents"] == 3
    assert sum(payload["bands"].values()) == 3
