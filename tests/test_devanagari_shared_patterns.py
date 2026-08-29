"""The shared matra-damage patterns: what they match, and why they use escapes.

Sibling of ``tests/test_regex_normalization_stability.py``, which scans every pattern in
``src/`` for the same two hazards generically. These are the specific assertions for the
three shapes that now have one home, including the nukta case that the two instruments
used to disagree about.
"""

from __future__ import annotations

import re
import unicodedata

from likhit.devanagari import (
    DOUBLED_MATRA_PATTERN,
    ORPHAN_MATRA_PATTERN,
    VIRAMA_MATRA_PATTERN,
)

#: `क़ानून` (law) and `ज़िल्ला` (district). Written as **literals**, deliberately -- as escapes
#: they read `\u0915\u093c\u093e\u0928\u0942\u0928`, and a fixture nobody can read is a fixture
#: nobody will notice going wrong.
#:
#: ⚠️ So the spelling is NOT what protects them. The `unicodedata.normalize("NFC", word) == word`
#: assertion below is: it fails if a normalising editor or a copy through a shell has changed
#: these to the precomposed form, which would silently stop the test being about a decomposed
#: nukta consonant at all. An earlier version of this comment claimed they were "written from
#: code points", which was simply untrue of the line beneath it -- exactly the drift this
#: module exists to remove, in a file arguing that spelling is load-bearing.
_NUKTA_WORDS = (
    ("क़ानून", "kaanoon, law"),
    ("ज़िल्ला", "jilla, district"),
)


def test_a_matra_after_a_nukta_consonant_is_not_an_orphan() -> None:
    """🛑 The divergence that made this module necessary.

    NFC *decomposes* the precomposed nukta letters, so canonical Nepali writes
    ``क`` + U+093C. A lookbehind that omits U+093C reads the following matra as orphaned --
    which is what the corpus audit did while this package's copy had the fix, because they
    lived in different repositories.
    """

    for word, gloss in _NUKTA_WORDS:
        assert unicodedata.normalize("NFC", word) == word, gloss
        assert ORPHAN_MATRA_PATTERN.findall(word) == [], (
            f"{gloss}: the matra after a decomposed nukta consonant is being counted as "
            f"orphaned, which is the defect this module exists to make impossible"
        )


def test_the_patterns_still_catch_real_damage() -> None:
    """The fix must not have been a blanket suppression."""

    assert ORPHAN_MATRA_PATTERN.findall("ा")  # a bare matra
    assert ORPHAN_MATRA_PATTERN.findall(" ा")  # after a space
    assert DOUBLED_MATRA_PATTERN.findall("काि")  # two signs in a row
    assert VIRAMA_MATRA_PATTERN.findall("्ा")  # virama then a sign


def test_a_matra_after_an_ordinary_consonant_or_virama_is_fine() -> None:
    """The negative half: every character the lookbehind is supposed to admit.

    ⚠️ These five overlap with ``test_regex_normalization_stability.py``'s assertions on the
    same pattern, and the duplication is deliberate rather than accidental. That file's
    subject is the *normalisation* hazard across every pattern in ``src/``; this one's is the
    three matra shapes and what they mean. Neither is a superset -- and this is the pattern
    two instruments disagreed about, so being reachable from both files is the point. If they
    ever disagree, that is a finding.
    """

    for preceding in ("क", "ह", "क़", "्", "़"):
        assert not ORPHAN_MATRA_PATTERN.findall(preceding + "ा"), preceding


def test_the_orphan_class_is_written_as_escapes_not_literals() -> None:
    """🛑 Not a style assertion. The literal form stops the module IMPORTING.

    U+0958-U+095F are composition exclusions, so a normalised copy of the source turns
    ``क़-य़`` into ``क``+U+093C``-``य``+U+093C -- leaving the class ending on the
    **descending** range U+093C-U+092F, which ``re.compile`` rejects. The failure is at
    import time, not match time, which is why it cannot be caught downstream.
    """

    assert ORPHAN_MATRA_PATTERN.pattern.isascii(), (
        "the orphan class must be spelled with \\uXXXX escapes; written literally it "
        "stops compiling under every normalization form"
    )
    for form in ("NFC", "NFD", "NFKC", "NFKD"):
        normalised = unicodedata.normalize(form, ORPHAN_MATRA_PATTERN.pattern)
        assert normalised == ORPHAN_MATRA_PATTERN.pattern, form
        re.compile(normalised)  # must not raise


def test_the_two_literal_patterns_are_atomic_so_their_spelling_is_safe() -> None:
    """The other two stay literal, and that is checked rather than assumed.

    U+093E-U+094C (the dependent vowel signs) and U+094D (virama) have no canonical
    decomposition, so the literal spelling cannot drift. Anything reaching for a
    decomposable character in these two must switch to escapes, and this fails first.
    """

    for pattern in (DOUBLED_MATRA_PATTERN, VIRAMA_MATRA_PATTERN):
        for char in pattern.pattern:
            assert not unicodedata.decomposition(char), (
                f"{char!r} (U+{ord(char):04X}) in {pattern.pattern!r} decomposes, so this "
                f"pattern is normalisation-fragile and must use escapes"
            )
