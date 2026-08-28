"""Optional regression coverage for OAG document 11113.

The Performance Audit Report 2074 is set in Preeti and merely *declares* a
Kalimati face, which draws one glyph of the 433,222 in the file. When
`fix_kalimati_cmap` refused any document holding an unrepairable named
Kalimati/Lohit face, that single glyph withheld the whole report -- 351,643
correctly decoded Devanagari characters, in a transcript that measured cleaner
than the corpus median. The refusal now requires the face to draw a material
share of the document, so this report converts again.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

import pytest

from likhit.converters.nepali_pdf import _convert_with_likhit

_FIXTURE_ENV = "LIKHIT_OAG_11113_PDF"
_DEVANAGARI = re.compile("[ऀ-ॿ]")
#: A dependent vowel sign or virama immediately after whitespace cannot begin a
#: Nepali word, so its rate is a cheap read on whether a CMap was mis-mapped.
_WORD_INITIAL_VOWEL_SIGN = re.compile(r"(?:^|[\s।॥])[ा-्]", re.MULTILINE)


def _fixture_path() -> Path:
    configured = os.environ.get(_FIXTURE_ENV)
    if not configured:
        pytest.skip(f"set {_FIXTURE_ENV} to the public OAG 11113 PDF")
    path = Path(configured)
    if not path.is_file():
        pytest.skip(f"{_FIXTURE_ENV} does not name a file: {path}")
    return path


def test_oag_11113_is_not_withheld_over_one_incidental_kalimati_glyph() -> None:
    result, needs_ocr_pages = _convert_with_likhit(_fixture_path().read_bytes())
    markdown = result.markdown

    assert needs_ocr_pages == []
    assert "\x00" not in markdown
    assert "�" not in markdown
    assert re.search(r"\(cid:\d+\)", markdown, re.IGNORECASE) is None
    assert "महालेखापरीक्षक" in markdown

    # v17 shipped 351,643 Devanagari characters for this document. Later repairs
    # may raise that; they must never drop it back towards the withheld zero.
    devanagari = len(_DEVANAGARI.findall(markdown))
    assert devanagari >= 351_643, devanagari

    # The transcript this refusal discarded was cleaner than the corpus median
    # (0.06 vs 0.13 word-initial vowel signs per 10,000 Devanagari characters),
    # which is what made withholding it a pure loss. Hold that line.
    per_10k = len(_WORD_INITIAL_VOWEL_SIGN.findall(markdown)) / devanagari * 10_000
    assert per_10k < 0.5, per_10k
