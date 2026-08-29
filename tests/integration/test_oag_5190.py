"""Optional regression coverage for OAG document 5190."""

from __future__ import annotations

import os
from pathlib import Path
import re

import pytest

from likhit.converters.nepali_pdf import _convert_with_likhit
from likhit.renderers.markdown import page_anchor_numbers

_FIXTURE_ENV = "LIKHIT_OAG_5190_PDF"
_DEVANAGARI_OR_LATIN_RUN = re.compile("[\u0900-\u097fA-Za-z]+")
_DEVANAGARI = re.compile("[\u0900-\u097f]")
_LATIN = re.compile("[A-Za-z]")
_LATIN_RUN = re.compile("[A-Za-z]+")
_NUMERIC_MULTIPLIER = re.compile("^[०-९]+([Xx][०-९]+)+$")


def _fixture_path() -> Path:
    configured = os.environ.get(_FIXTURE_ENV)
    if not configured:
        pytest.skip(f"set {_FIXTURE_ENV} to the public OAG 5190 PDF")
    path = Path(configured)
    if not path.is_file():
        pytest.skip(f"{_FIXTURE_ENV} does not name a file: {path}")
    return path


def _class_a_mixed_script_count(text: str) -> int:
    count = 0
    for token in _DEVANAGARI_OR_LATIN_RUN.findall(text):
        if not (_DEVANAGARI.search(token) and _LATIN.search(token)):
            continue
        if _NUMERIC_MULTIPLIER.fullmatch(token):
            continue
        if max(len(run) for run in _LATIN_RUN.findall(token)) == 1:
            count += 1
    return count


def test_oag_5190_uses_repaired_likhit_output() -> None:
    result, needs_ocr_pages = _convert_with_likhit(_fixture_path().read_bytes())
    markdown = result.markdown

    assert needs_ocr_pages == []
    assert page_anchor_numbers(markdown) == list(range(1, 93))
    assert re.search(r"\(cid:\d+\)", markdown, re.IGNORECASE) is None
    assert "\x00" not in markdown
    assert "\ufffd" not in markdown
    assert "वार्षिक प्रतिवेदन" in markdown
    assert "उर्लाबारी नगरपालिका" in markdown

    # The shipped fallback had 3,242 Class-A occurrences. This repair measures
    # 3,163; allow later Mangal repairs to lower that ceiling, never raise it.
    assert _class_a_mixed_script_count(markdown) <= 3_163
