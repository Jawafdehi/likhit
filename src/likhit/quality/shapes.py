"""What one numeric cell can look like, and what only a run of cells can.

:mod:`likhit.quality.axes` and the corpus's numeric-merge oracle both have to answer "is
this token one number, or several that ran together?" -- the audit from the text alone, the
oracle to report whether the text carried enough signal. Keeping the answer in one place
stops the two instruments from disagreeing about the same token.

`plausible_single_number` holds the shapes the corpus's numeric-boundary verifier
accepts, and its test pins the two to the same answers so they cannot drift. That
tool uses them to decide where geometry may *cut*, which is why they stop at two
decimal places -- a looser decimal would make one side of a real boundary look valid
and suppress the split. Judging whether a token is one *cell* is the opposite
question and needs `plausible_single_value`, which adds arbitrary precision.

No PyMuPDF here on purpose -- the axes read Markdown and nothing else, which is what
makes this module usable without any of the extraction machinery.
"""

from __future__ import annotations

import re

NUMERAL_TRANSLATION = str.maketrans("०१२३४५६७८९।", "0123456789.")
NON_DIGIT = re.compile(r"[^0-9०-९]")

#: A bare integer, or one with up to two decimal places. Also every identifier:
#: an account number is a long unpunctuated digit string.
PLAIN_AMOUNT = re.compile(r"[0-9०-९]+(?:\.[0-9०-९]{1,2})?")
#: Indian grouping, as every OAG money column uses: 1,23,45,678.90
INDIAN_AMOUNT = re.compile(
    r"[0-9०-९]{1,3}(?:,[0-9०-९]{2})*,[0-9०-९]{3}(?:\.[0-9०-९]{1,2})?"
)
#: Western grouping, which the English-language annexes use: 12,345,678.90
WESTERN_AMOUNT = re.compile(r"[0-9०-९]{1,3}(?:,[0-9०-९]{3})+(?:\.[0-9०-९]{1,2})?")
#: A numbered list item: "12."
SERIAL_NUMBER = re.compile(r"[0-9०-९]{1,4}\.")
#: A clause or schedule reference: "3.5.1", "10.2.14.7"
DOTTED_REFERENCE = re.compile(r"[0-9०-९]{1,4}(?:\.[0-9०-९]{1,4}){2,}")

#: The shapes `verify_numeric_boundaries` accepts. Two decimal places is the limit
#: there because they gate where geometry may *cut*: a looser decimal would let one
#: side of a real boundary look valid and suppress the split.
MONEY_SHAPES = (
    PLAIN_AMOUNT,
    INDIAN_AMOUNT,
    WESTERN_AMOUNT,
    SERIAL_NUMBER,
    DOTTED_REFERENCE,
)

#: A decimal of any precision. Judging a *cell* needs this and cutting one must
#: not have it: `1994010129149.00000` comes straight out of an accounting export
#: and `3.955` is a ratio, but the money shapes stop at two places, so both read as
#: invalid and then decompose -- 203 of the 303 remaining false positives.
PRECISE_DECIMAL = re.compile(r"[0-9०-९]+\.[0-9०-९]+")

_VALUE_SHAPES = (*MONEY_SHAPES, PRECISE_DECIMAL)


def canonical_numerals(text: str) -> str:
    return text.translate(NUMERAL_TRANSLATION)


def digit_count(token: str) -> int:
    """Digits in `token`, ignoring separators and Devanagari/ASCII difference."""
    return len(NON_DIGIT.sub("", token))


def trim_punctuation(token: str) -> str:
    """Drop trailing separators the token regex swallowed from the sentence.

    `likhit.quality.axes.NUM_TOKEN_RE` reads `[0-9०-९][0-9०-९,.�]*`, so an amount at the
    end of a sentence arrives as `3710792.` with the full stop attached. Left in
    place that reads as a merge -- `3710792.` is not a valid single number, but it
    decomposes into `371` and the serial `0792.`, which is how a single figure
    became a reported merged cell. Measured on a 30-document sample, this one step
    removes 791 of 1,042 flags in the under-15-digit population.
    """
    return token.rstrip(",.")


def plausible_single_number(token: str) -> bool:
    """Could `token` be one amount, as `verify_numeric_boundaries` judges it?

    Note what this deliberately accepts: a digit string of any length. A Nepali
    bank account number runs to 15, 16 or 20 digits and is a perfectly good single
    cell, which is why counting digits cannot find merged cells.
    """
    canonical = canonical_numerals(token)
    return any(shape.fullmatch(canonical) for shape in MONEY_SHAPES)


def plausible_single_value(token: str) -> bool:
    """Could `token` be one cell, amount or not?

    Broader than `plausible_single_number` by exactly one shape, the
    arbitrary-precision decimal. A cell holding `.00000` or a three-place ratio is
    one cell; it is only as a *cut* candidate that such a token has to be refused.
    """
    canonical = canonical_numerals(token)
    return any(shape.fullmatch(canonical) for shape in _VALUE_SHAPES)


def merge_shaped(token: str) -> bool:
    """Does `token` carry text-side evidence that several cells ran together?

    True when the token is not a valid single number but does break cleanly into
    ones -- `9,82,84,2881,98,11,800` splits into two Indian amounts, and
    `534,000.00534,000.00` into two Western ones. Requiring a valid decomposition
    is what separates a merge from mere corruption: a malformed amount is damage of
    a different kind and `fffd_in_number` or the Devanagari checks own it.

    A token carrying U+FFFD needs no special case. Every accepted shape is digits,
    commas and dots, so any part holding the replacement character is implausible,
    and every part has to be plausible for the token to decompose -- so a damaged
    amount can never also be counted as a merge.
    """
    trimmed = trim_punctuation(token)
    if plausible_single_value(trimmed):
        return False
    return decomposes_into_amounts(canonical_numerals(trimmed))


def decomposes_into_amounts(canonical: str) -> bool:
    """Can `canonical` be cut into two or more plausible single numbers?

    Cuts are only tried between two digits, matching the geometry oracles: a comma
    or a decimal point belongs inside one amount, so a cell boundary never falls
    there. That restriction is what keeps a malformed amount like `1,2` out -- its
    only candidate boundaries sit against the comma, so it does not decompose and
    is corruption rather than a merge.
    """
    length = len(canonical)
    cuts = [
        index
        for index in range(1, length)
        if canonical[index - 1].isdigit() and canonical[index].isdigit()
    ]
    if not cuts:
        return False

    splittable: dict[int, bool] = {length: True}

    def suffix_splittable(start: int) -> bool:
        """Does `canonical[start:]` partition into plausible numbers?"""
        if start in splittable:
            return splittable[start]
        splittable[start] = False  # cycles are impossible, but be explicit
        for end in [*cuts, length]:
            if end <= start:
                continue
            if plausible_single_value(canonical[start:end]) and suffix_splittable(end):
                splittable[start] = True
                break
        return splittable[start]

    return any(
        plausible_single_value(canonical[:cut]) and suffix_splittable(cut)
        for cut in cuts
    )
