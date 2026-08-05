"""Devanagari well-formedness signals for regression measurement.

likhit's existing quality signals cannot distinguish repaired Devanagari from
mojibake, because legacy-font corruption *reorders and strands* glyphs rather
than deleting them. A raw Devanagari code-point count is therefore nearly
identical for both (measured on one corpus PDF: 12,618 corrupted vs 12,564
repaired -- a 0.4% difference across a 57x quality gap).

What separates them is whether each combining mark has a valid base. These
helpers count malformed sequences so a change can be measured rather than
argued about.

IMPORTANT: ``unicodedata.combining()`` returns 0 for EVERY Devanagari matra
(they are category Mc/Mn but canonical combining class 0), so the obvious
implementation silently measures nothing. These predicates use explicit
code-point ranges instead. See ``test_devanagari_quality.py`` for a regression
guard on exactly that trap.
"""

from __future__ import annotations

# Consonants: KA..HA, nukta forms QA..YYA, and Sindhi extensions.
_CONSONANT_RANGES = ((0x0915, 0x0939), (0x0958, 0x095F), (0x0979, 0x097F))
# Dependent vowel signs, including short/extended and vocalic L/LL forms.
_MATRA_RANGES = (
    (0x093A, 0x093B),
    (0x093E, 0x094C),
    (0x094E, 0x094F),
    (0x0955, 0x0957),
    (0x0962, 0x0963),
)
# Candrabindu, anusvara, visarga, nukta, virama.
_SIGNS = frozenset("ँंः़्")
# Independent vowels A..AU -- a valid base for anusvara/visarga.
_INDEPENDENT_VOWEL_RANGE = (0x0905, 0x0914)


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    # Callers pass the previous character, which is "" at the start of a string,
    # so an empty argument is expected rather than a programming error.
    if not char:
        return False
    codepoint = ord(char)
    return any(low <= codepoint <= high for low, high in ranges)


def is_consonant(char: str) -> bool:
    """True for a Devanagari consonant, which can carry a matra."""

    return _in_ranges(char, _CONSONANT_RANGES)


def is_matra(char: str) -> bool:
    """True for a Devanagari dependent vowel sign."""

    return _in_ranges(char, _MATRA_RANGES)


def is_sign(char: str) -> bool:
    """True for candrabindu, anusvara, visarga, nukta or virama."""

    return bool(char) and char in _SIGNS


def is_independent_vowel(char: str) -> bool:
    """True for an independent vowel, a valid base for anusvara/visarga."""

    return _in_ranges(char, (_INDEPENDENT_VOWEL_RANGE,))


def devanagari_quality(text: str) -> dict[str, int]:
    """Count malformed Devanagari sequences in ``text``.

    Every count is a defect count: lower is better and 0 is ideal. Clean Nepali
    scores 0 on all of them.

    Returns:
        ``stranded``         -- marks (matras and signs) with no valid base to their left
        ``stranded_matras``  -- subset of ``stranded`` that are dependent vowel signs
        ``word_initial``     -- subset of ``stranded`` sitting at a word boundary
        ``doubled``          -- two dependent vowel signs in a row
        ``halant_matra``     -- a virama immediately followed by a matra
        ``matras``           -- total dependent vowel signs (a denominator, not a defect)
        ``marks``            -- total matras and signs (a denominator, not a defect)
        ``malformed``        -- ``stranded + doubled + halant_matra``
    """

    stranded = 0
    stranded_matras = 0
    word_initial = 0
    doubled = 0
    halant_matra = 0
    matras = 0
    marks = 0
    previous = ""

    for char in text:
        char_is_matra = is_matra(char)
        if char_is_matra:
            matras += 1

        if char_is_matra or is_sign(char):
            marks += 1
            has_base = bool(previous) and (
                is_consonant(previous)
                or is_matra(previous)
                or is_sign(previous)
                or is_independent_vowel(previous)
            )
            if not has_base:
                stranded += 1
                if char_is_matra:
                    stranded_matras += 1
                if not previous or previous.isspace():
                    word_initial += 1
            if char_is_matra and is_matra(previous):
                doubled += 1
            if char_is_matra and previous == "्":
                halant_matra += 1

        previous = char

    return {
        "stranded": stranded,
        "stranded_matras": stranded_matras,
        "word_initial": word_initial,
        "doubled": doubled,
        "halant_matra": halant_matra,
        "matras": matras,
        "marks": marks,
        "malformed": stranded + doubled + halant_matra,
    }


def orphaned_matra_ratio(text: str) -> float:
    """Fraction of dependent vowel signs that have no base consonant.

    A high ratio means the text is unreadable rubble even when its Devanagari
    code-point count looks healthy: one corpus PDF ships a Devanagari fraction of
    0.632 with almost every vowel sign orphaned.

    Both numerator and denominator count matras only. Counting stranded *signs*
    (anusvara, virama) against a matra-only denominator can exceed 1.0, which
    makes the result unusable as a ratio.
    """

    quality = devanagari_quality(text)
    if not quality["matras"]:
        return 0.0
    return quality["stranded_matras"] / quality["matras"]
