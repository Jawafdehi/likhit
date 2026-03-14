from __future__ import annotations

from pathlib import Path

from likhit.cli import main
from likhit.core import extract, derive_output_name


ROOT = Path(__file__).resolve().parents[1]


def _sample_path(*candidates: str) -> Path:
    for candidate in candidates:
        path = ROOT / "samples" / candidate
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Missing sample PDF in {ROOT / 'samples'}. Tried: {', '.join(candidates)}"
    )


PRESS_RELEASE = _sample_path("pressrelease.pdf")
PRESS_RELEASE_ALT = _sample_path("Press Release.pdf", "Press_Release.pdf")
KANUN_PATRIKA = _sample_path("kanunpatrika.pdf")


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


def test_cli_extract_kanun_patrika_auto_names_output(tmp_path: Path) -> None:
    exit_code = main(
        [
            "extract",
            str(KANUN_PATRIKA),
            "--type",
            "kanun-patrika",
            "--out-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "kanunpatrika.md").exists()


def test_derive_output_name_uses_kanun_patrika_prefix_with_publication_date() -> None:
    result = extract(str(KANUN_PATRIKA), "kanun-patrika")
    result.publication_date = "2082-01-14"

    output_name = derive_output_name(result, "any-source-name.pdf", existing=set())

    assert output_name == "kanunpatrika-2082-01-14.md"
