"""Devanagari patterns shared by more than one module in this package.

🛑 **Why the matra-damage shapes need a single home.** Two instruments here count the same
three malformations. ``converters.nepali_pdf._markdown_quality_score`` counts them to decide
which candidate transcript **ships**; a separate audit counts them to grade what shipped. Those
two lived in different repositories and drifted -- the audit's orphan lookbehind omitted
U+093C, so a matra following a *decomposed* nukta consonant read as orphaned, while this
package's copy had the fix. A grader that disagrees with the chooser about what damage is will
accept a transcript it then condemns.

⚠️ **The corpus effect of that divergence, measured rather than asserted:** 20 false positives
across 9 of 6,234 documents, out of 483,095 orphan counts, and **zero verdict moves**. The
shape appears in 158 documents (2.53%); the common forms are ``बढ़ाउने`` and ``गढ़ी`` in place
names. The audit's literal class already spanned the *precomposed* letters, so the fix bites on
the decomposed residue only. Real and correct, and small -- all three are worth saying.

**On literals versus escapes, which is not a style question here.** A Devanagari class written
as a range over decomposable characters is a live hazard, twice over:

* the U+0958-U+095F range (``क़`` … ``य़``) is base+nukta pairs, so a decomposed copy of the
  source raises ``re.error`` and **the module stops importing**;
* the U+0929/U+0931/U+0934 set silently *widens* to bare ``न``/``र``/``ळ``, which is worse,
  because nothing fails.

``tests/test_regex_normalization_stability.py`` scans every pattern in ``src/`` for both.
"""

from __future__ import annotations

import re

DOUBLED_MATRA_PATTERN = re.compile(r"[ा-ौ]{2,}")
# U+093C NUKTA belongs in the lookbehind because NFC *decomposes* the precomposed nukta
# consonants (क़ becomes क + U+093C), so canonical Nepali writes the decomposed form.
# Without it the matra in क़ानून reads as orphaned and clean text gets penalised.
#
# The class is written as code-point escapes, NOT as literal Devanagari, and that is
# load-bearing rather than a style choice. U+0958-U+095F are Unicode composition
# exclusions: every normalization form (NFC, NFD, NFKC, NFKD) replaces each with a
# two-code-point <base, U+093C NUKTA> sequence. Written literally, the range
# "\u0915\u093c-\u092f\u093c" leaves the class ending on the DESCENDING range U+093C-U+092F, and
# re.compile raises "bad character range". This module then fails to IMPORT -- not to
# match. Verified on the literal form: compiles as shipped, stops compiling under all
# four normalization forms.
ORPHAN_MATRA_PATTERN = re.compile(
    r"(?<![\u0915-\u0939\u0958-\u095f\u094d\u093c])[\u093e-\u094c]"
)
VIRAMA_MATRA_PATTERN = re.compile(r"्[ा-ौ]")

# --- digit classes ---------------------------------------------------------------------- #
#
# Used by `likhit.privacy`'s identifier and figure patterns. They live here rather than in
# that subpackage because both redaction passes and the PII scanner wanted the same two
# fragments, and each had defined its own copy -- along with a dead `XLAT` translation table
# duplicated in two more modules and used by neither.
#
# ⚠️ These stay LITERAL, unlike `ORPHAN_MATRA_PATTERN` above, and that difference is
# checked rather than remembered: U+0966-U+096F (the Devanagari digits) and U+0964 (the
# danda) have no canonical decomposition, so the hazard described above cannot reach them.
# `test_the_shared_classes_are_atomic` asserts it.

#: The Devanagari digits, as a character-class range fragment.
DEVANAGARI_DIGITS = "०-९"

#: Devanagari and ASCII digits together. This corpus mixes them inside a single amounts
#: column, so an identifier or figure pattern that admits only one script misses real spans.
ANY_DIGITS = f"0-9{DEVANAGARI_DIGITS}"
