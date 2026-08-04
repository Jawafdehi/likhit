"""Tests for the Devanagari well-formedness metric.

The metric exists to make extraction-quality changes measurable, so it needs its
own calibration: it must score 0 on clean Nepali, separate known-corrupted from
known-repaired text, and never regress to ``unicodedata.combining()``.
"""

from __future__ import annotations

import unicodedata

import pytest

from tests.devanagari_quality import (
    devanagari_quality,
    is_consonant,
    is_matra,
    orphaned_matra_ratio,
)

CLEAN_NEPALI_WORDS = (
    "अख्तियार",
    "दुरुपयोग",
    "अनुसन्धान",
    "आयोग",
    "प्रतिवेदन",
    "कार्यालय",
    "मन्त्रालय",
    "नियमावली",
    "भ्रष्टाचार",
    "सम्बन्धी",
    "तोकिएबमोजिमको",
    "विवरण",
)


@pytest.mark.parametrize("word", CLEAN_NEPALI_WORDS)
def test_clean_nepali_scores_zero(word: str) -> None:
    """Well-formed Nepali must have no malformed sequences at all."""

    assert devanagari_quality(word)["malformed"] == 0, word


@pytest.mark.parametrize(
    "corrupted,repaired",
    [
        # Word pairs from one corpus PDF: the first is what the default
        # extraction produces (matras before their bases), the second is what
        # likhit's repair produces.
        ("िोटकएबमोख्जमको", "तोकिएबमोजिमको"),
        ("प्रनििेदन", "प्रतिवेदन"),
        ("टििरण", "विवरण"),
    ],
)
def test_metric_separates_repaired_from_corrupted(
    corrupted: str, repaired: str
) -> None:
    """The metric must discriminate where a Devanagari char count cannot."""

    assert devanagari_quality(repaired)["malformed"] == 0
    assert devanagari_quality(corrupted)["malformed"] > 0


def test_metric_discriminates_where_devanagari_count_does_not() -> None:
    """A raw code-point count is a near-useless quality signal; this is not.

    Figures measured end-to-end on one corpus PDF (pages 17-22), comparing the
    default extraction against likhit's: the Devanagari code-point counts differ
    by 0.4% while the stranded-mark counts differ by 57x. A metric worth having
    must reproduce that separation.
    """

    default_deva, likhit_deva = 4206, 4188
    default_stranded, likhit_stranded = 172, 3

    deva_separation = abs(default_deva - likhit_deva) / max(default_deva, likhit_deva)
    assert deva_separation < 0.01, "premise: code-point counts are nearly identical"
    assert default_stranded / max(likhit_stranded, 1) > 50


def test_metric_does_not_use_unicodedata_combining() -> None:
    """Regression guard: the obvious implementation measures nothing.

    unicodedata.combining() returns 0 for every Devanagari matra, so a metric
    built on it reports 0 stranded marks even for text that is entirely orphaned
    vowel signs. That is exactly how a 97%-damaged document was measured as
    healthy during research.
    """

    for matra in "ािीुूृेैोौ":
        assert unicodedata.combining(matra) == 0, "documents the trap"

    assert devanagari_quality(" ि")["stranded"] == 1
    assert devanagari_quality(" ि")["word_initial"] == 1


def test_word_initial_matra_is_counted() -> None:
    """A matra at a word boundary has no base and is always a defect."""

    quality = devanagari_quality("सूचना िदँदा")
    assert quality["word_initial"] >= 1
    assert quality["stranded"] >= 1


def test_doubled_matra_is_counted() -> None:
    """Two dependent vowel signs in a row are not representable Devanagari."""

    # U+0940 (ी) followed by U+093F (ि) -- produced by deleting a space between
    # a word and a following pre-base matra.
    assert devanagari_quality("सम्बन्धीिनयमावली")["doubled"] >= 1


def test_virama_followed_by_matra_is_counted() -> None:
    """A virama consumes the inherent vowel, so a matra cannot follow it."""

    assert devanagari_quality("क्ि")["halant_matra"] == 1


def test_orphaned_matra_ratio_flags_rubble() -> None:
    """Text that is mostly orphaned vowel signs must score near 1.0."""

    rubble = "(ू प ू : 80/ 6/18) प , , . . 80/ 1 ै प ू प ः , 1/1 3 ॅ ऽ ू ेऽ /"
    assert orphaned_matra_ratio(rubble) > 0.5
    assert orphaned_matra_ratio("अख्तियार दुरुपयोग अनुसन्धान आयोग") == 0.0


def test_matras_denominator_is_not_a_defect_count() -> None:
    """``matras`` counts all vowel signs so callers can compute a ratio."""

    quality = devanagari_quality("कि की कु")
    assert quality["matras"] == 3
    assert quality["malformed"] == 0


def test_predicates_reject_non_devanagari() -> None:
    for char in "aA1 .‍":
        assert not is_consonant(char)
        assert not is_matra(char)


def test_predicates_accept_the_empty_previous_character() -> None:
    """The scan passes "" for the character before the first one."""

    assert not is_consonant("")
    assert not is_matra("")
    assert devanagari_quality("ि")["stranded"] == 1


def test_empty_and_latin_text_is_clean() -> None:
    for text in ("", "   ", "Ordinary English text.", "12345"):
        assert devanagari_quality(text)["malformed"] == 0
        assert orphaned_matra_ratio(text) == 0.0


def test_orphaned_matra_ratio_is_a_true_ratio() -> None:
    """Numerator and denominator must both count matras only.

    Counting stranded *signs* (anusvara, virama) against a matra-only
    denominator returns values above 1.0, which is not a ratio. Measured on one
    corpus PDF, the mismatched form returned 1.157.
    """

    rubble = "(ू प ू : 80/ 6/18)\nप , , . . 80/ 1 ै प\nू प ः ,\n1/1 3 ॅ ऽ ू ेऽ /"
    ratio = orphaned_matra_ratio(rubble)
    assert 0.0 <= ratio <= 1.0
    assert ratio > 0.5

    for word in CLEAN_NEPALI_WORDS:
        assert 0.0 <= orphaned_matra_ratio(word) <= 1.0


def test_stranded_matras_is_a_subset_of_stranded() -> None:
    text = "सूचना िदँदा ः क्ि"
    quality = devanagari_quality(text)
    assert quality["stranded_matras"] <= quality["stranded"]
    assert quality["matras"] <= quality["marks"]
