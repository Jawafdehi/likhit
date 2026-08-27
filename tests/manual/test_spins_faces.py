"""Manual checks against VOL-317's extracted Spins font programs."""

import os
from pathlib import Path

import pytest

from likhit.extractors.digit_companion import glyphs_draw_devanagari_digits

FACES_ENV = "LIKHIT_SPINS_FACES_DIR"


def _required_faces_dir() -> Path:
    raw = os.environ.get(FACES_ENV)
    assert raw, (
        f"set {FACES_ENV} to VOL-317's fonts-c8a8e41c directory before "
        "running tests/manual/test_spins_faces.py"
    )
    path = Path(raw)
    assert path.is_dir(), f"{FACES_ENV} is not a directory: {path}"
    return path


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("3653-Spins_EXT.ttf", True),
        ("3097-SpinsEXT.ttf", True),
        ("3653-Spins.ttf", False),
        ("11102-Spins_EXT.ttf", None),
    ],
)
def test_the_extracted_faces_decide_as_read(
    filename: str, expected: bool | None
) -> None:
    faces = _required_faces_dir()
    path = faces / filename
    assert path.is_file(), f"{filename} not present in {faces}"
    assert glyphs_draw_devanagari_digits(path.read_bytes()) is expected
