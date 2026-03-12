from __future__ import annotations

from pathlib import Path

from likhit.cli import main
from likhit.core import extract, derive_output_name


ROOT = Path(__file__).resolve().parents[1]
PRESS_RELEASE = ROOT / "samples" / "pressrelease.pdf"
PRESS_RELEASE_ALT = ROOT / "samples" / "Press Release.pdf"


def test_cli_extract_writes_multiple_outputs(tmp_path: Path) -> None:
    exit_code = main(
        [
            "extract",
            str(PRESS_RELEASE),
            str(PRESS_RELEASE_ALT),
            "--type",
            "ciaa-press-release",
            "--out-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "pressrelease-2081-10-24.md").exists()
    assert (tmp_path / "pressrelease-2082-01-14.md").exists()


def test_cli_extract_single_file_with_out(tmp_path: Path) -> None:
    output_path = tmp_path / "single.md"

    exit_code = main(
        [
            "extract",
            str(PRESS_RELEASE),
            "--type",
            "ciaa-press-release",
            "--out",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    assert "आरोपपत्र दायर गररएको" in output_path.read_text(encoding="utf-8")


def test_cli_extract_avoids_existing_auto_named_output(tmp_path: Path) -> None:
    result = extract(str(PRESS_RELEASE), "ciaa-press-release")
    existing = derive_output_name(result, str(PRESS_RELEASE), existing=set())
    (tmp_path / existing).write_text("existing", encoding="utf-8")

    exit_code = main(
        [
            "extract",
            str(PRESS_RELEASE),
            "--type",
            "ciaa-press-release",
            "--out-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    generated = sorted(path.name for path in tmp_path.glob("*.md"))
    assert len(generated) == 2
    assert existing in generated
    assert any(name != existing for name in generated)
