"""The shared DIGIT classes, and why their literal spelling is safe.

Sibling of ``font_based``'s ``test_the_figure_classes_are_atomic``, which makes the same
argument about the same characters for the money-figure axis, and of
``test_devanagari_shared_patterns.py``, which covers the matra patterns in the same module.
All three exist because this project has twice been bitten by a Devanagari character class
that decomposed: once loudly (``re.error: bad character range``, module stops importing) and
once silently (a class widening to admit bare consonants).

The split is deliberate: these two fragments are atomic and stay literal, while the orphan
matra class next to them must use escapes. Asserting the difference is what stops a future
edit from applying the wrong rule to the wrong one.
"""

from __future__ import annotations

import re
import unicodedata

from likhit.devanagari import ANY_DIGITS, DEVANAGARI_DIGITS


def test_the_shared_classes_are_atomic() -> None:
    """No character here has a canonical decomposition, so the literal form is safe.

    This is the assertion the module docstring points at. A future edit reaching for a
    decomposable character -- U+0958-U+095F, or U+0929/U+0931/U+0934 -- fails here instead
    of either breaking the import or quietly widening the class.
    """

    for spelling in (DEVANAGARI_DIGITS, ANY_DIGITS):
        for char in spelling:
            if char == "-":  # the range operator, not a member
                continue
            assert not unicodedata.decomposition(char), (
                f"{char!r} (U+{ord(char):04X}) decomposes, so this class is "
                f"normalisation-fragile and must be written with escapes"
            )
            assert unicodedata.normalize("NFD", char) == char


def test_the_digit_classes_match_exactly_the_digits() -> None:
    """The silent failure mode: a class that has widened to admit letters."""

    deva = re.compile(f"[{DEVANAGARI_DIGITS}]")
    both = re.compile(f"[{ANY_DIGITS}]")
    probes = {chr(code) for code in range(0x0900, 0x0980)} | set("0123456789abcXYZ-/.,")

    assert {c for c in probes if deva.match(c)} == set("०१२३४५६७८९")
    assert {c for c in probes if both.match(c)} == set("०१२३४५६७८९0123456789")


def test_both_scripts_are_admitted_together() -> None:
    """An amounts column in this corpus mixes the two, so one script alone misses spans."""

    assert re.compile(f"[{ANY_DIGITS}]+").findall("१२३ and 456") == ["१२३", "456"]
