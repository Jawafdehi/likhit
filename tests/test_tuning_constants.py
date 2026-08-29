"""Every module-level numeric constant in ``src/``, pinned to its measured value.

MEASURED, NOT ASSUMED. Each of the 23 constants below was perturbed in place and the
whole suite run against the mutant. **14 of the 23 survived** -- nothing in the suite
asserted anything that depended on the value, so a reviewer could change any of them
and see green. Among the survivors were the CID marking base, the threshold that routes
a page to paid OCR, and the table-cell edge tolerance.

That figure is the measurement against the suite WITHOUT this file. Re-run against this
file, all 23 are caught and 0 survive. Both numbers are needed: the first says why the
file exists, the second says it works. The sweep is `tools/constant_sweep.sh` in the run
record; the result is `inventory/constant-sweep.tsv`.

🛑 THE FIRST VERSION OF THIS FILE SHIPPED VACUOUS, and how is worth more than the fix.
`test_constant_holds_its_pinned_value` was committed with `expected` rebound to the live
module attribute -- a mutation marker, left in by the very sweep that was demonstrating
the vacuity. Three things had to line up, and all three are avoidable:

  1. The mutation was reverted with `git checkout -- tests/<this file>` while the file
     was still UNTRACKED. That command fails on an untracked path. It printed an error;
     the error was read as benign, because "the file is untracked" sounded like an
     explanation rather than the reason the restore did not happen.
  2. The restore was then confirmed by re-running the suite and seeing green. Green
     after a restore proves nothing when the mutation is one that makes tests PASS --
     which is exactly the class of mutation a vacuity demonstration uses.
  3. `git add -A tests` committed the mutant.

So: restore by byte comparison against a pristine copy, never by re-running the suite,
and never with `git checkout` on a path that may be untracked.
`test_no_pin_is_derived_from_the_thing_it_pins` now closes the specific hole -- the pin
could not detect its own vacuity, so the property is asserted over its source instead.

WHY A BARE PIN IS THE RIGHT SHAPE HERE, given that a pin asserts nothing about
behaviour: the alternative is a behavioural test per constant, and for a geometry
threshold that means inventing a fixture whose only purpose is to sit either side of a
number -- which pins the fixture, not the threshold. A pin plus a stated derivation
makes the value a decision with an owner. Three constants whose consequence is severe
enough to earn a behavioural test as well get one at the bottom of this file.

TWO THINGS THIS FILE IS CAREFUL ABOUT.

* **The registry is checked against an AST scan of the source**, so a constant added
  later must be registered. Pinning today's 23 would close 23 instances and leave the
  class open.
* **The scan sees NAMED module-level constants only**, which left a real hole: six
  weights in `_markdown_quality_score` -- the function that decides which candidate
  transcript ships -- were inline literals in the same expression as two that were
  named here. Worse, the two named ones stated their derivations against one of the
  unpinned literals ("half the U+FFFD/NUL rate of 12"), so pinned values were anchored
  to a free one. Measured: changing that rate from 12 to 1 left the whole suite green
  at 875 passed. All six are now named and registered, and
  `test_the_candidate_score_carries_no_unnamed_weight` closes the class for that
  function. Repo-wide it stays open by choice: src/ holds 432 distinct non-trivial
  numeric literals over 640 occurrences, nearly all structural.

  🛑 That pair is instrument-dependent, so read it with its definition attached:
  ``ast.walk`` over every ``src/**/*.py``, counting ``ast.Constant`` of int or float
  (``bool`` excluded, since it is an int subclass), with 0, 1 and 2 treated as
  trivial. Change any of those choices and the number moves -- excluding only 0 and 1
  gives 433 over 743. This docstring said 431 over 635 until review re-derived it and
  got 432 over 640; neither figure reproduced under any reading of the old wording,
  because the wording named no instrument. The lesson is the missing definition, not
  the off-by-one.
* **Every expected value is a literal.** A test that reads the constant to build its
  own expectation holds at any value -- which is exactly how these came to be
  unpinned. `tests/test_candidate_scoring.py` is the live example: its `_mark()` helper
  builds fixtures with `chr(_CID_MARK_BASE + ord(char))`, so moving the base moves the
  fixture with it. That file even states the principle in `_padding_crossover`'s
  docstring -- "a helper that recomputes the term cannot notice the term changing" --
  and applies it to the scorer while the helper nine lines above breaks it.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pymupdf as fitz
import pytest

from likhit.converters.nepali_pdf import _markdown_quality_score
from likhit.extractors import font_based as font_based_module
from likhit.extractors.font_classifier import (
    SCANNED_DECOY_TEXT,
    _DECOY_MAX_DEVANAGARI,
    classify_ocr_page,
)
from likhit.handlers import structure_detection as structure_detection_module
from likhit.handlers import two_column_layout as two_column_layout_module
from tests.synthetic_pdfs import build_scanned_decoy_pdf

_SRC = Path(__file__).resolve().parent.parent / "src"

# (module path relative to src/, name) -> (value, where the value comes from).
#
# The rationale column is the point of the table. A number with no derivation is a
# number nobody can safely change; a number with one can be argued about.
_PINNED: dict[tuple[str, str], tuple[float, str]] = {
    # -- markdown quality score ------------------------------------------------ #
    (
        "likhit/converters/nepali_pdf.py",
        "_MAX_REASONABLE_WHITESPACE_RATIO",
    ): (0.35, "above this share of whitespace a candidate is padding, not layout"),
    (
        "likhit/converters/nepali_pdf.py",
        "_MAX_REASONABLE_SINGLE_TOKEN_RATIO",
    ): (0.35, "above this share of one-token lines the candidate is shredded"),
    (
        "likhit/converters/nepali_pdf.py",
        "_EXCESS_SINGLE_TOKEN_PENALTY",
    ): (
        6,
        "per excess single-token line; half _UNDECODED_GLYPH_PENALTY. Stated "
        "against the CONSTANT, not against the literal 12 -- when this derivation "
        "said 'the U+FFFD/NUL rate of 12' that rate was an inline literal nothing "
        "pinned, so this pin was anchored to a free number",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_MATRA_DAMAGE_PENALTY",
    ): (
        8,
        "per matra-damage unit. Between _EXCESS_SINGLE_TOKEN_PENALTY and "
        "_UNDECODED_GLYPH_PENALTY: a damaged matra is worse than a bad line break "
        "and better than a glyph that did not decode at all. The ordering is "
        "asserted below, so the derivation is checked rather than merely stated",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_OCR_DEFAULT_DPI",
    ): (
        300,
        "initial OCR resolution. Five of seven bundled sample first pages fit the "
        "5 MiB encoded limit at 300 DPI; oversized renders shrink reactively, and "
        "LIKHIT_OCR_DPI lets a provider impose a lower service-specific ceiling",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_OCR_MAX_RENDER_PIXELS",
    ): (
        40_000_000,
        "allocation guard for pathological media boxes. Forty million pixels "
        "bounds an RGB pixmap near 120 MiB (RGBA near 160 MiB) while leaving every "
        "bundled A3-or-smaller page untouched at 300 DPI",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_OCR_MAX_RENDER_ATTEMPTS",
    ): (
        4,
        "one initial render plus three reductions bounds render work while allowing "
        "a highly incompressible page to fall to 42.2% of the initial dimensions",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_OCR_RENDER_SCALE_STEP",
    ): (
        0.75,
        "each oversized render loses one quarter of each dimension, nearly halving "
        "the pixel count without an abrupt one-step loss of OCR legibility",
    ),
    # The rest of _markdown_quality_score's weights. These were inline literals in
    # the same arithmetic expression as the two above, so this guard covered half of
    # one function's tuning surface. Measured before naming them: changing the
    # U+FFFD/NUL rate from 12 to 1 left the whole suite green at 875 passed -- the
    # full baseline -- while the two derivations above cited that very number.
    (
        "likhit/converters/nepali_pdf.py",
        "_DEVANAGARI_CHAR_CREDIT",
    ): (
        3,
        "credit per Devanagari character; with the token count it is one of only two "
        "positive terms, because Devanagari volume is the primary evidence that a "
        "Nepali candidate decoded at all. Absolute value unrecorded when introduced",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_SUSPICIOUS_TOKEN_PENALTY",
    ): (
        8,
        "per token carrying legacy-garble punctuation or letter/digit mixing; equal "
        "to _MATRA_DAMAGE_PENALTY. Absolute value unrecorded when introduced",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_VOWEL_POOR_TOKEN_PENALTY",
    ): (
        3,
        "per vowel-poor Latin token; equal to _DEVANAGARI_CHAR_CREDIT, so one such "
        "token cancels one Devanagari character. Absolute value unrecorded",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_PIPE_HEAVY_LINE_PENALTY",
    ): (
        4,
        "per pipe-heavy line; between _VOWEL_POOR_TOKEN_PENALTY and "
        "_EXCESS_SINGLE_TOKEN_PENALTY. Absolute value unrecorded",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_CID_GARBAGE_PENALTY",
    ): (
        12,
        "per '(cid:N)' run; equal to _UNDECODED_GLYPH_PENALTY, because a cid literal "
        "IS a glyph that did not decode -- it is the same damage spelled differently",
    ),
    (
        "likhit/converters/nepali_pdf.py",
        "_UNDECODED_GLYPH_PENALTY",
    ): (
        12,
        "per U+FFFD or NUL. The heaviest weight in the score and the anchor the "
        "others are stated against: a glyph that did not decode is the worst thing a "
        "candidate can ship. See the NUL comment at the call site for why a NUL must "
        "not be cheaper than a U+FFFD",
    ),
    # -- CID marking ---------------------------------------------------------- #
    (
        "likhit/extractors/font_based.py",
        "_CID_MARK_BASE",
    ): (
        0xF0000,
        "start of Supplementary Private Use Area A (plane 15), so marked CIDs stay "
        "distinct AND inside the private-use range _private_use_count counts",
    ),
    (
        "likhit/extractors/font_based.py",
        "_MAX_MARKABLE_CID",
    ): (
        0xFFFD,
        "largest CID that fits: _CID_MARK_BASE + this is 0xFFFFD, the top of the "
        "range _MARKED_CID_PATTERN matches. See the invariant test below",
    ),
    (
        "likhit/extractors/font_based.py",
        "_DUPLICATE_CONSONANT_WEIGHT",
    ): (
        3,
        "per unexplained doubled consonant in _text_quality_penalty. The lightest term "
        "there, which is deliberate: even narrowed by morphology the signal keeps ~1 in "
        "5 false positives, so it is priced below the ikar and invalid-sign terms it sits "
        "beside. Naming it is the same move as the converter's candidate-score weights -- "
        "an inline weight is invisible to this registry",
    ),
    # -- Latin cid uniform-offset recovery -------------------------------------- #
    #
    # A Latin subset whose glyph ids sit a uniform offset from ASCII can be read back
    # exactly. These three decide when the offset is believed, and the transform FAILS
    # CLOSED -- with no lexicon available it recovers nothing rather than guessing, which
    # is why an absent dictionary makes the feature inert instead of wrong.
    (
        "likhit/extractors/font_based.py",
        "_CID_RECOVERY_MIN_TOKEN",
    ): (
        3,
        "shortest token that counts as evidence of an offset; two letters match the "
        "lexicon too readily to distinguish a real offset from a coincidence",
    ),
    (
        "likhit/extractors/font_based.py",
        "_CID_RECOVERY_MIN_HITS",
    ): (
        2,
        "one lexicon hit under a candidate offset is a coincidence, as with the "
        "content-legacy dictionary's own floor",
    ),
    (
        "likhit/extractors/font_based.py",
        "_CID_RECOVERY_MIN_COV_ONE_HIT",
    ): (
        0.5,
        "coverage required when there is only a single hit: half the run's tokens must "
        "resolve, so a lone long word cannot carry the offset on its own",
    ),
    # -- the document-scope acronym veto ---------------------------------------- #
    #
    # The third Latin-side axis: an all-upper run repeated across a document is an
    # acronym, not keystrokes. These three bound what counts as one.
    (
        "likhit/extractors/font_based.py",
        "_ACRONYM_MIN_LEN",
    ): (
        2,
        "a single upper-case letter is an initial or a list label, not an acronym",
    ),
    (
        "likhit/extractors/font_based.py",
        "_ACRONYM_MAX_LEN",
    ): (
        5,
        "above this an all-upper run is more likely a keystroke sequence than an "
        "abbreviation; the acronyms this corpus carries are 2-5 letters. Written as 6 "
        "from memory first and caught by this pin -- which is what the file is for",
    ),
    (
        "likhit/extractors/font_based.py",
        "_ACRONYM_MIN_UPPER",
    ): (
        2,
        "at least two of the run's characters must be upper-case, so a capitalised "
        "ordinary word does not qualify",
    ),
    (
        "likhit/extractors/font_based.py",
        "_MIXED_MARGIN_FLOOR",
    ): (
        1,
        "smallest legal mixed-margin, for the ENV var and the keyword alike. Not a "
        "calibrated threshold: it is the boundary below which the gate stops being a "
        "gate -- at 0 the pass-1 winner is itself eligible, so nothing is promoted and "
        "the only effect is to break pass-1 ties, which drops the VOL-156 ambiguity "
        "mask. Registered in the same commit as the constant, per finding 86-5",
    ),
    (
        "likhit/extractors/font_based.py",
        "_MIXED_ELIGIBLE_INDEX",
    ): (
        4,
        "where the ELIGIBLE indicator is spliced into _map_ranking_key's tuple: below "
        "the money-figure axis, above attested. NOT a tuning value -- it is a structural "
        "index, and it is named because the gated key DERIVES from the ungated one "
        "instead of restating its axes. The restated copy diverged once already (it "
        "kept charging an ikar+nasal site the ungated key forgives), so a bare 3 in "
        "that slice is what a later axis insertion would move silently -- and it did: "
        "this was 3 until the figures axis was carried over from the corpus line and "
        "took index 3, which is why the two changes are one unit",
    ),
    # -- quality audit: axes ---------------------------------------------------- #
    #
    # Every derivation below is the one recorded at the constant's definition when this
    # tooling lived in an untracked corpus directory. Registering them here is what this
    # guard is for: an unpinned number is one nobody can safely change.
    (
        "likhit/quality/axes.py",
        "MERGE_MIN_DIGITS",
    ): (
        15,
        "digits a token needs before `numeric_damage` will consider it a merged cell. "
        "NOT evidence by itself -- the rule alone ran at precision 0.142 over the "
        "14,608 flagged runs the geometry oracle checked, 12,540 of which were single "
        "cells. It bounds the candidates to the population that oracle has measured. "
        "The denominator is 14,608, not the 14,891 an earlier revision of this pin and "
        "the ported test docstring both carried: the shared precision figure settles "
        "it, since (14608-12540)/14608 = 0.142 and (14891-12540)/14891 = 0.158",
    ),
    (
        "likhit/quality/axes.py",
        "REPHA_CORRUPT_FLOOR",
    ): (
        12,
        "the per-document COUNT of whole-token repha corruptions at or above which a "
        "document is at least `suspect` whatever its purity says -- `bad >= FLOOR` "
        "where `bad` is a count, and the test fixture is that many REPETITIONS. Not a "
        "character length, which an earlier revision of this pin said: that is the "
        "exact error class the constant is a monument to, since VOL-168 proposed 20 "
        "from a percentile computed on a different quantity than the field it named "
        "and a literal implementation ran ~8.6x weaker than intended. 12 = one above "
        "the 99th percentile (11) of v13's floor-decidable population, the 5,701 "
        "documents already clean on the other seven checks and above the 0.75 purity "
        "cut. The per-form precision evidence is a separate argument, for not sitting "
        "HIGHER than p99",
    ),
    # -- quality audit: page refusal (opt-in axis) ------------------------------- #
    (
        "likhit/quality/page_refusal.py",
        "PLACEHOLDER_CELL_SHARE_FLOOR",
    ): (
        0.25,
        "share of a page's table DATA cells that must be placeholders before the page "
        "is a refusal. A measured GAP, not a tuned value: 0.4444 for the one refusal "
        "page against 0.0000 for every other staged page carrying a table",
    ),
    (
        "likhit/quality/page_refusal.py",
        "MIN_DATA_CELLS",
    ): (
        4,
        "below this many data cells the share is not a measurement at all -- a 2-cell "
        "table with one placeholder scores 0.5 and means nothing",
    ),
    (
        "likhit/quality/page_refusal.py",
        "PLACEHOLDER_PROSE_PER_KCHAR",
    ): (
        5.0,
        "for a page with no table: placeholder occurrences per 1,000 characters. "
        "`11356` p5 is at 17.75; the highest non-refusal staged page is 0.304, so this "
        "sits inside an empty gap",
    ),
    (
        "likhit/quality/page_refusal.py",
        "MIN_PROSE_PLACEHOLDERS",
    ): (
        3,
        "the absolute floor beside PLACEHOLDER_PROSE_PER_KCHAR. A rate alone would let "
        "a very short page qualify on one hit",
    ),
    (
        "likhit/quality/page_refusal.py",
        "PLACEHOLDER_CELL_DOMINANCE",
    ): (
        0.5,
        "how much of a cell the bracketed placeholder must occupy. Separates the two "
        "populations with NO overlap: all four placeholder cells of `11356` p5 score "
        "1.000 because the cell IS the placeholder, and all 15 false-positive cells "
        "score 0.000 with no bracket at any offset. The midpoint of an empty gap",
    ),
    # -- ranking forgiveness ---------------------------------------------------- #
    #
    # Each term forgives ONE occurrence before the tell counts, because each fires at a
    # low rate on correct text. Registered in the SAME commit as the constant it pins:
    # `test_every_module_level_numeric_constant_is_pinned` compares an AST scan of src/
    # against this dict, so an unregistered constant is both an assertion failure and a
    # KeyError in a parametrized sibling -- i.e. a commit that adds the constant without
    # its entry is red on its own, and a bisect landing there sees failures unrelated to
    # what it is bisecting. Measured: 2 failures for the doublet commit and 3 for the
    # bracket commit when both entries arrived in a later third commit.
    (
        "likhit/extractors/font_based.py",
        "_RANKING_DOUBLET_FORGIVENESS",
    ): (
        1,
        "one unexplained doublet is inside the residual false-positive rate the "
        "morphology narrowing leaves behind; two is evidence",
    ),
    (
        "likhit/extractors/font_based.py",
        "_RANKING_STRANDED_FORGIVENESS",
    ): (
        1,
        "one stranded bracket can be an ordinary parenthetical. NOTE the tell now counts "
        "overlapping occurrences, so two adjacent Nepali list labels score 2 rather than "
        "1 -- which is exactly the case this forgiveness must not swallow, and the reason "
        "the count was fixed before this value was pinned. It was previously pinned only "
        "to an INTERVAL, admitting both 1 and 2; a registry entry pins exactly, which is "
        "the point of this file",
    ),
    # -- the Latin veto on the content-legacy remap ---------------------------- #
    #
    # These four gate whether a span that merely SHARES a legacy face is left as English
    # instead of being remapped into well-formed Devanagari that spells nothing. Getting
    # them wrong is silent in both directions: too loose and real Nepali survives
    # undecoded, too tight and English becomes plausible-looking gibberish with no U+FFFD
    # for any gate to notice.
    (
        "likhit/extractors/font_based.py",
        "_LATIN_VETO_MIN_CHARS",
    ): (
        13,
        "absolute floor on the run, so a two-word fragment cannot veto a document's "
        "decode on volume alone. VOL-146 moved it from 16, which is the ONE line of "
        "behaviour in that change; 13 is the lowest value that still admits every run "
        "the read census cleared, and 12 would abandon real Nepali -- both edges are "
        "pinned in tests/test_content_legacy_latin_veto.py",
    ),
    (
        "likhit/extractors/font_based.py",
        "_LATIN_VETO_MIN_CHARS_UPPER",
    ): (
        3,
        "VOL-321: the all-upper relaxation of the floor above, and it relaxes ONLY "
        "that floor and ONLY for runs whose letters are all upper case. 3 because an "
        "acronym is 2-4 characters and 16 put the whole class out of both Latin "
        "vetoes' reach -- the word veto declines it too, at function-word share 0.0 "
        "against its 0.1 floor. Its own output must not become acronym-axis evidence: "
        "see `relax_all_upper` (VOL-363)",
    ),
    # -- public constants, invisible to this guard until the scan was widened ---- #
    (
        "likhit/extractors/docx_based.py",
        "SOFFICE_TIMEOUT_SECONDS",
    ): (
        120,
        "a bound, not a calibration: LibreOffice HANGS rather than failing on some "
        "malformed .doc inputs and this runs inside a queue consumer, so the "
        "conversion has to be bounded. Raising it makes a hung consumer wait longer; "
        "lowering it fails slow-but-valid documents",
    ),
    (
        "likhit/extractors/kalimati_reference.py",
        "REFERENCE_UNITS_PER_EM",
    ): (
        2048,
        "a FACT about the reference font, not a tunable. Outline digests are computed "
        "from raw font-unit coordinates, so they are comparable only at this em square "
        "-- changing the number does not rescale anything, it invalidates every digest",
    ),
    (
        "likhit/extractors/kalimati_reference.py",
        "REFERENCE_GLYPH_COUNT",
    ): (
        696,
        "provenance only, and that IS the point: nothing is keyed on glyph count, so a "
        "subset reporting fewer is not an error. Recorded so a re-vendored reference "
        "font is noticed",
    ),
    (
        "likhit/extractors/lohit.py",
        "EXPECTED_UNITS_PER_EM",
    ): (
        1024,
        "as REFERENCE_UNITS_PER_EM above, for the Lohit table: a property of the build "
        "the table was derived from, fingerprinted alongside name IDs 3 and 5",
    ),
    (
        "likhit/extractors/lohit.py",
        "UPSTREAM_GLYPH_COUNT",
    ): (
        407,
        "the upstream font's glyph count. A subset truncates the tail but never "
        "reorders, so a real font may report FEWER and never more -- the asymmetry is "
        "the check, not the number",
    ),
    (
        "likhit/extractors/pua_maps.py",
        "SYMBOL_PUA_LIFT",
    ): (
        0xF000,
        "a FORMAT CONSTANT, not a threshold: the offset a symbol-font ToUnicode CMap "
        "conventionally applies to a single-byte code, and the same offset a legacy "
        "Devanagari font's symbol-style (3,0) cmap applies. There is no other value it "
        "could take",
    ),
    (
        "likhit/extractors/font_based.py",
        "SPAN_GAP_THRESHOLD",
    ): (
        0.75,
        "ROLE ONLY, and it says so: a horizontal gap in PDF points above which two "
        "adjacent spans are joined with a space rather than concatenated. It arrived "
        "undocumented and no sweep chose it; 0.75pt is well under a character width at "
        "every body size in these corpora, so it fires on a real gap and not on "
        "kerning. A run that wants to MOVE it owes a calibration this entry does not "
        "have",
    ),
    (
        "likhit/extractors/latin_structure.py",
        "STRUCTURE_FLOOR",
    ): (
        25,
        "VOL-446: length floor for the ungated arm of the vowel-structure axis",
    ),
    (
        "likhit/extractors/latin_structure.py",
        "GATED_FLOOR",
    ): (
        12,
        "VOL-446: the lower floor, reachable only when VOCABULARY_SHARE of the run's "
        "tokens are in the gate vocabulary -- the pair is the rule, so neither number "
        "means anything alone",
    ),
    (
        "likhit/extractors/latin_structure.py",
        "VOCABULARY_SHARE",
    ): (
        0.60,
        "VOL-446: the share of length>=TOKEN_MIN_LENGTH [A-Za-z]+ tokens that must be "
        "in the gate vocabulary before GATED_FLOOR applies",
    ),
    (
        "likhit/extractors/latin_structure.py",
        "TOKEN_MIN_LENGTH",
    ): (
        3,
        "VOL-446: shorter tokens are excluded from the vocabulary share, because a "
        "one- or two-letter token carries no word shape to measure",
    ),
    (
        "likhit/extractors/latin_structure.py",
        "MIN_TOKENS",
    ): (
        3,
        "VOL-446: fewer tokens than this is not a phrase, and the axis measures word "
        "SHAPE across a phrase rather than letter density in one token",
    ),
    (
        "likhit/extractors/latin_structure.py",
        "MIN_ALPHA_RATIO",
    ): (
        0.6,
        "VOL-446: deliberately far below `font_based._LATIN_VETO_MIN_ALPHA_RATIO` "
        "(0.88) -- reaching English prose that carries punctuation and digits is the "
        "whole reason this axis exists",
    ),
    (
        "likhit/extractors/latin_structure.py",
        "MIN_VOWEL_TOKEN_SHARE",
    ): (
        0.9,
        "VOL-446: share of tokens that must contain an ASCII vowel. High on purpose: a "
        "legacy keystroke run is consonant-dense, so this is the axis's main "
        "discriminator",
    ),
    (
        "likhit/extractors/font_based.py",
        "_LATIN_VETO_MIN_ALPHA_RATIO",
    ): (
        0.88,
        "share of the run that must be alphabetic before it reads as prose rather than "
        "as keystrokes with punctuation in them",
    ),
    (
        "likhit/extractors/font_based.py",
        "_LATIN_VETO_MIN_VOWEL_RATIO",
    ): (
        0.3,
        "vowel share: legacy keystroke text is consonant-heavy because the layout puts "
        "consonants on the home row, so a genuine English run has far more vowels",
    ),
    (
        "likhit/extractors/font_based.py",
        "_LATIN_VETO_MIN_SHARE",
    ): (
        0.1,
        # 🛑 This derivation used to read "share of the FONT's runs that must read as "
        # "Latin before the veto applies to that font at all -- run-level evidence, "
        # "aggregated per font per document". There is no per-font aggregation anywhere:
        # a single Latin-reading span vetoes on its own evidence, so a maintainer who
        # believed in that safety net would be changing something else entirely.
        "share of ONE span's multi-letter tokens that must be _LATIN_VETO_WORDS "
        "function words -- a share rather than a count so accidental collisions in a "
        "long keystroke run dilute; calibrated over 469,357 same-font runs but applied "
        "per span, and the exposure of that unit mismatch is measured in "
        "_reads_as_latin_words' docstring",
    ),
    # -- content-based legacy detection --------------------------------------- #
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MIN_HITS",
    ): (2, "one dictionary hit is a coincidence"),
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MAX_PENALTY_PER_DEVA",
    ): (0.05, "garble budget per Devanagari character of the decoded candidate"),
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MIN_DEVA_RATIO",
    ): (0.6, "a real legacy decode is mostly Devanagari"),
    (
        "likhit/extractors/font_based.py",
        "_CONTENT_LEGACY_MIN_DEVA",
    ): (8, "absolute floor, so a two-word span cannot clear the ratio on volume"),
    (
        "likhit/extractors/font_based.py",
        "_NAME_LEGACY_MIN_DIGIT_SHARE",
    ): (
        0.25,
        "minimum ASCII-digit share for the name-path digit disjunct. A corpus sweep "
        "at a2fcf84 showed the initial 0.5 rejected 180 no-letter units carrying "
        "50,887 correctly decoded Devanagari characters; 0.25 retains 99.7% of the "
        "available Devanagari mass while requiring a quarter of the aggregate to be "
        "digits",
    ),
    (
        "likhit/extractors/font_based.py",
        "_RANKING_GARBLE_FORGIVENESS",
    ): (
        6,
        "garble points forgiven when RANKING two decodes of one span, never by the gate. "
        "A small margin can be evidence about the SOURCE, which every candidate decodes "
        "alike, so it must not veto `stranded` and `attested`, which are evidence about "
        "the MAP. Replaces the site-specific _RANKING_IKAR_NASAL_FORGIVENESS and subsumes "
        "it exactly at this value (= _IKAR_NASAL_WEIGHT); measured plateau 6/12/24/48, and "
        "6 is the lower bound because 3843 and 5143 both carry a margin of exactly 6",
    ),
    (
        "likhit/extractors/font_based.py",
        "_IKAR_NASAL_WEIGHT",
    ): (
        6,
        "what one ikar+nasal site costs in _text_quality_penalty. Named so "
        "_map_ranking_key can subtract exactly it; the two must not drift apart",
    ),
    # -- scanned / decoy page classification ---------------------------------- #
    (
        "likhit/extractors/font_classifier.py",
        "_SCANNED_IMAGE_COVERAGE",
    ): (0.85, "share of the page an image must cover before OCR is considered"),
    (
        "likhit/extractors/font_classifier.py",
        "_DECOY_MAX_DEVANAGARI",
    ): (
        10,
        "at or above this many Devanagari characters the text layer is real, not a "
        "decoy. This is the gate on PAID OCR -- see the behavioural test below",
    ),
    # -- lohit cmap recovery -------------------------------------------------- #
    (
        "likhit/extractors/lohit.py",
        "_MIN_ANCHOR_MATCHES",
    ): (1, "one anchor glyph is enough to accept a recovered cmap"),
    # -- numeric boundary repair ---------------------------------------------- #
    (
        "likhit/extractors/numeric_boundaries.py",
        "_ADVANCE_OUTLIER_EM",
    ): (0.10, "advance-width excess, in em, that marks an erased separator"),
    (
        "likhit/extractors/numeric_boundaries.py",
        "_BBOX_GAP_OUTLIER_EM",
    ): (
        0.20,
        "bbox gap between adjacent glyphs, in em. Twice the advance threshold "
        "because a bbox is a rendered extent and an advance is a font metric, so "
        "the bbox measure carries the glyph's own side bearings as noise",
    ),
    (
        "likhit/extractors/numeric_boundaries.py",
        "_MIN_RULE_HEIGHT",
    ): (4.0, "points; below this a vector is a glyph stroke, not a cell rule"),
    (
        "likhit/extractors/numeric_boundaries.py",
        "_MAX_PARTITION_SEGMENTS",
    ): (12, "combinatorial bound on rule partitions per numeric run"),
    # -- table extraction ----------------------------------------------------- #
    (
        "likhit/extractors/tables.py",
        "_EDGE_TOLERANCE",
    ): (
        1.5,
        "points of slack on the fragment-centre-in-cell test and on edge "
        "clustering. Widening it pulls a neighbouring fragment into a cell, which "
        "reclassifies the row downstream -- see test_extractor_renderer_seam.py",
    ),
    (
        "likhit/extractors/tables.py",
        "_OVERPRINT_TOLERANCE",
    ): (
        1.5,
        "points of slack when deciding two equal glyph runs share a drawing ORIGIN, "
        "i.e. are one overprint. Numerically equal to _EDGE_TOLERANCE and separate on "
        "purpose: while they were one constant, tuning cell-grid snapping would have "
        "moved glyph dedupe too. Both bounds are measured. Below it: a double-strike "
        "bold repeats its origin to within 0.24pt (11102__m6t-Annual Report 2067). "
        "Above it: two distinct CIAA list items sit 3.723pt apart at the same x "
        "(2077-78 p256), and suppressing those deletes a real bullet",
    ),
    (
        "likhit/extractors/tables.py",
        "_REGION_TOLERANCE",
    ): (
        3.0,
        "points of slack when asking whether one detected table's REGION encloses "
        "another's (VOL-744's container-table rule). ROLE ONLY -- INERT ON THIS "
        "CORPUS AT ANY VALUE UNDER ~24pt, and said so rather than dressed up as a "
        "calibration. Measured over 880 multi-table pairs on 500 pages "
        "(runs/vol744-fix-d22f13e13bfe9d0d/REGION-SLACK-*.json): the containment "
        "margin separates by TENS of points, never fractions -- contained pairs sit "
        "at +24.0 and the 440 non-contained pairs reach no closer than -36.558. So "
        "no pair on this corpus falls within 3pt of the boundary and the value is "
        "not doing work here; it is kept nonzero for a foundry whose ruled border "
        "strokes put the two grids a hair apart, which is what a zero would refuse",
    ),
    # -- page furniture ------------------------------------------------------- #
    (
        "likhit/renderers/markdown.py",
        "_MAX_WRAPPED_HEADER_LINES",
    ): (
        3,
        "text-carrying lines a wrapped running header is sought across. A bound on "
        "cost and blast radius, NOT a separating threshold -- a length cap was "
        "refuted for this rule on measurement, and this one cannot delete prose "
        "because body text between the two halves of the token stops the token "
        "forming at all. 3 covers a header that wraps twice; above it the scan is an "
        "unbounded O(n^2) pass over every block mentioning the header. Pinned at 2 "
        "the suite reddens, so the third line is load-bearing",
    ),
    (
        "likhit/renderers/markdown.py",
        "FURNITURE_MAX_COMPACT_CHARS",
    ): (
        80,
        "maximum compacted length a phrase-matching line or wrapped run may have "
        "before it is preserved as body text. The measured fixtures separate at "
        "line grain: the longest real running-header/footer line is 54 characters "
        "and the body witness that mentions the same phrase is 100. At block grain "
        "those classes overlap, so this bound must remain confined to lines and "
        "wrapped runs",
    ),
    # -- layout handlers ------------------------------------------------------ #
    (
        "likhit/handlers/structure_detection.py",
        "_HEADER_Y_MAX",
    ): (80.0, "points from the top within which a fragment is a running head"),
    (
        "likhit/handlers/structure_detection.py",
        "_COLUMN_GUTTER",
    ): (20.0, "minimum horizontal gap, in points, that separates two columns"),
    (
        "likhit/handlers/two_column_layout.py",
        "_HEADER_Y_MAX",
    ): (80.0, "must equal structure_detection's -- see the agreement test below"),
    (
        "likhit/handlers/two_column_layout.py",
        "_COLUMN_GUTTER",
    ): (20.0, "must equal structure_detection's -- see the agreement test below"),
    (
        "likhit/handlers/two_column_layout.py",
        "_LAYOUT_BLOCK_GAP_MIN",
    ): (18.0, "vertical points between fragments that start a new block"),
    # -- legacy map word cache ------------------------------------------------ #
    (
        "likhit/extractors/legacy_maps.py",
        "_WORD_CACHE_SIZE",
    ): (
        65536,
        "words memoized per map. Sized to be unreachable rather than tuned: every "
        "span of the 128-page law-report sample holds 7,899 distinct words, and five "
        "warm caches -- one per map, which is what choose_legacy_map fills when it "
        "scores a span against every candidate -- measured 39,495 entries and ~1.8 "
        "MiB. A bound only needs to stop an unbounded corpus run, so anything well "
        "above the per-document count does the same work",
    ),
    (
        "likhit/extractors/digit_companion.py",
        "_RENDER_PT",
    ): (
        48,
        "point size a probe glyph is drawn at. Large enough that the ink box exceeds "
        "the 16x16 sampling grid for every digit including `1`, which is the narrowest; "
        "a signature is discarded as unreadable when the box is smaller than the grid",
    ),
    (
        "likhit/extractors/digit_companion.py",
        "_ZOOM",
    ): (2, "render zoom, so a 48pt glyph gives ~96px of ink to sample from"),
    (
        "likhit/extractors/digit_companion.py",
        "_SIG_SIZE",
    ): (
        16,
        "signature grid edge, giving 256 cells. Coarse on purpose: the comparison has "
        "to hold across foundries and stroke weights, and a finer grid makes the "
        "same shape in two typefaces read as different",
    ),
    (
        "likhit/extractors/digit_companion.py",
        "_FAMILY_MATCH_MAX",
    ): (
        25,
        "Hamming distance, of 256 cells, below which two signatures are the same shape. "
        "Read off a measured separation rather than chosen: over 968 distinct faces from "
        "60 corpus PDFs (TPFP-51d3f79c20e2107f.json), the 22 faces that draw Devanagari "
        "digits sit at median 0 / p90 15 and the 946 that do not at median 80 / p90 133. "
        "The separation, not the number, is the evidence -- but NOT 'with room on both "
        "sides', which this entry claimed until review: that spread is dominated by "
        "genuine Latin faces, while 27 unrouted partially-matching faces carry glyph "
        "distances of 0 and 3, and the one corpus near-miss sits at 28. Thin on the near "
        "side; re-measure before widening",
    ),
    (
        "likhit/extractors/digit_companion.py",
        "_ROW_MATCH_MIN",
    ): (
        7,
        "how many of the ten plain-row glyphs must match, and also the minimum readable "
        "for a verdict at all; below this the instrument returns None (abstains) rather "
        "than False. A DEFENSIVE margin for subsets that omit glyphs, with zero measured "
        "effect: this entry used to say readable companion faces carry 7-10 of the row, "
        "but re-derived from the artifacts the only faces that would fire at 7-9 are "
        "three Fontasy Himali faces, all routed_by_name and so excluded by condition 1 -- "
        "while all 14 firing companions in the acceptance sweep, and all 131 firing over "
        "the full corpus, sit at 10 of 10. Raising it back to 10 changes nothing measured",
    ),
    (
        "likhit/extractors/digit_companion.py",
        "_MAX_ALPHA_SHARE",
    ): (
        0.05,
        "maximum ASCII-alphabetic share for a face to be digit-dominant. The measured "
        "gap is nearly three orders of magnitude wide (VOL-317, whole corpus): the two "
        "companion faces sit at 0.42% and 0.85%, the prose face of the same family at "
        "54.7%. 5% is deliberately far above the companions and far below the prose "
        "face, so it is not a boundary anyone discovered",
    ),
    (
        "likhit/extractors/digit_companion.py",
        "_MIN_DIGIT_SHARE",
    ): (
        0.5,
        "minimum digit share of non-space characters. Secondary to the alpha share, "
        "which is what actually separates the classes; this only rejects a face that is "
        "neither words nor figures (rules, separators, punctuation)",
    ),
    (
        "likhit/extractors/digit_companion.py",
        "_MIN_CHARS",
    ): (
        40,
        "characters before the content test is trusted at all. A three-character font "
        "is not evidence of anything, and the two shares above are ratios that a tiny "
        "denominator makes meaningless",
    ),
    # -- Kokila / Kalimati faces whose embedded CMap contradicts what they draw --- #
    #
    # Every GID here is a specific glyph in a specific measured face, so the derivation
    # names the document that proves it. They are identities, not thresholds: there is no
    # range to widen, and changing one means the glyph was misidentified.
    (
        "likhit/extractors/kalimati.py",
        "_CONTEXTUAL_NE_GID",
    ): (
        566,
        "the measured Kalimati glyph whose authored CMap says ने while the embedded "
        "font map says bare e-matra; only its र् context proves the consonant",
    ),
    (
        "likhit/extractors/kalimati.py",
        "_KOKILA_HALF_SA_GID",
    ): (
        214,
        "the measured Kokila half-sa glyph: 13 affected identity-mapped faces say थ, "
        "corroborated as स् by their own font map or another Kokila face in the PDF",
    ),
    (
        "likhit/extractors/kalimati.py",
        "_KOKILA_HALF_THA_GID",
    ): (
        195,
        "the measured Kokila half-tha glyph: PDF 5604 authors bare virama while "
        "the target program's exact GID-195 outline digest proves the measured half-tha",
    ),
    (
        "likhit/extractors/kalimati.py",
        "_KOKILA_YA_GID",
    ): (
        94,
        "the measured following-ya glyph: PDF 5604 authors र् while the embedded "
        "Kokila map proves य, completing the GID-195 fingerprint",
    ),
    (
        "likhit/extractors/kalimati.py",
        "_INCIDENTAL_FACE_GLYPH_SHARE",
    ): (
        0.005,
        "the share of drawn glyphs below which an unrepairable named Kalimati/Lohit "
        "face is incidental and refusing the document costs more than it protects. "
        "Measured over the 18 OAG documents the refusal withholds, the two "
        "populations are four orders of magnitude apart: document 11113 -- set in "
        "Preeti, declaring a Kalimati face that draws ONE glyph of 433,222 -- sits "
        "at 0.0002%, and the next-smallest genuine offender at 10.04%. This floor "
        "is ~20x clear of each, so it is not fitted to either",
    ),
}


def _iter_module_level_constants() -> list[tuple[str, str, float]]:
    """Every ``_NAME = <number>`` assigned at module level in ``src/``.

    Module level only: a constant inside a function or class is local to its caller
    and is not the review hazard this file is about.
    """

    found: list[tuple[str, str, float]] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if not target.id.isupper() and not target.id.lstrip("_").isupper():
                continue
            # 🛑 A PUBLIC constant is scanned too, and it did not used to be. The
            # filter here was `target.id.startswith("_")`, so every SCREAMING_CASE
            # name without a leading underscore was invisible to this guard --
            # measured, 14 of them across src/, and 7 arrived in one change
            # (VOL-446's `latin_structure`) whose whole content is calibration.
            # A tuning constant does not become safe to move by being importable;
            # if anything the opposite, because a public name may have callers
            # outside this repo. Nothing about the hazard depends on the
            # underscore, so nothing about the scan does either.
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(
                value.value, (int, float)
            ):
                if isinstance(value.value, bool):
                    continue
                found.append((rel, target.id, value.value))
    return found


_FOUND = _iter_module_level_constants()


def test_every_module_level_numeric_constant_is_pinned():
    """A constant added later must be registered, or this class reopens.

    Pinning today's set would close 23 instances and leave the class open. The failure
    message is the review prompt.
    """

    found = {(rel, name) for rel, name, _value in _FOUND}
    assert found == set(_PINNED), (
        "a module-level numeric constant in src/ was added, removed or renamed.\n"
        f"  unpinned: {sorted(found - set(_PINNED))}\n"
        f"  stale pins: {sorted(set(_PINNED) - found)}\n"
        "Add it to _PINNED with its value AND where the value comes from. A number "
        "with no derivation is a number nobody can safely change."
    )


@pytest.mark.parametrize(
    ("rel", "name", "value"), _FOUND, ids=lambda v: str(v).replace(".py", "")
)
def test_constant_holds_its_pinned_value(rel, name, value):
    """The pin. ``expected`` is the LITERAL from ``_PINNED`` and must stay that way.

    Both readings of the constant are asserted against it, and they are independently
    informative: ``value`` is the source literal the AST scan found, ``live`` is the
    module attribute at runtime. They diverge if a constant is conditionally reassigned
    after its definition, which the AST scan cannot see.

    Neither may become the expectation. Deriving ``expected`` from either one makes this
    ``source == source`` and the whole table stops being read -- which is not a
    hypothetical: it shipped that way, as a mutation marker left in by the sweep that
    was meant to demonstrate the vacuity. See the module docstring.
    """

    expected = _PINNED[(rel, name)][0]
    live = getattr(
        importlib.import_module(rel.removesuffix(".py").replace("/", ".")), name
    )

    assert value == expected
    assert live == expected
    assert type(value) is type(expected), (
        f"{name} changed type: pinned {expected!r}, source has {value!r}"
    )


def test_every_pin_carries_a_derivation():
    """Guards the table against becoming a bare list of numbers.

    ⚠️ This measures LENGTH, not completeness. It can only catch an empty or
    near-empty cell -- a truncated clause passes, and one did: this file shipped
    ``_BBOX_GAP_OUTLIER_EM``'s derivation ending mid-sentence at "because bboxes are",
    62 characters and green. Read the column; do not rely on this test to.

    🛑 That hole has now bitten twice more, both found by review and neither catchable here.
    ``MERGE_MIN_DIGITS`` shipped with a denominator (14,891) that contradicted its own
    module's (14,608) while quoting the same precision figure, which is what settled which
    was wrong. ``REPHA_CORRUPT_FLOOR`` described its value as a character *length* when it is
    a per-document occurrence count, and gave the wrong one of its module's two arguments.
    A rationale that is confidently wrong is longer than 20 characters, so **the only thing
    that catches this class is someone reading the column against the source.**
    """

    missing = [key for key, (_v, why) in _PINNED.items() if len(why.strip()) < 20]
    assert missing == [], missing


def test_no_pin_is_derived_from_the_thing_it_pins():
    """The pin's expectation must be a literal in ``_PINNED``, checked at the source.

    ``test_constant_holds_its_pinned_value`` cannot detect its own vacuity: if
    ``expected`` is rebound to the live value, every assertion in it still passes. So
    the property is asserted here instead, over the source of that function -- the same
    "scan the source, not the runtime" idiom the flag-word guard uses, for the same
    reason.
    """

    body = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    target = next(
        node
        for node in ast.walk(body)
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_constant_holds_its_pinned_value"
    )
    assignments = {
        node.targets[0].id: ast.unparse(node.value)
        for node in ast.walk(target)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    assert assignments.get("expected") == "_PINNED[rel, name][0]", (
        "the pin's expectation must come from the _PINNED literal, not from the "
        f"source scan or the live module. Found: {assignments.get('expected')!r}"
    )


# --------------------------------------------------------------------------- #
# The three whose consequence earns a behavioural test as well.
# --------------------------------------------------------------------------- #


def test_the_cid_mark_range_fits_exactly_inside_plane_15():
    """``_CID_MARK_BASE`` and ``_MAX_MARKABLE_CID`` are not independent choices.

    ``_MARKED_CID_PATTERN`` states the same range a third time, as a literal. The three
    agree today, and if the base moves without the other two the top of the range lands
    in plane 16: 0xF1000 + 0xFFFD is 0x100FFD, which ``_MARKED_CID_PATTERN`` does not
    match, ``strip_marked_cids`` cannot strip and ``count_marked_cids`` cannot count.
    A high CID would then be marked into a code point nothing can recover -- silently,
    because low CIDs keep working.

    Derived from the constants rather than restating them, so this is a consistency
    check and not a second copy of the pin above.
    """

    base = font_based_module._CID_MARK_BASE
    top = base + font_based_module._MAX_MARKABLE_CID
    pattern = font_based_module._MARKED_CID_PATTERN

    # Plane 15 (Supplementary Private Use Area A) is U+F0000..U+FFFFF.
    assert base == 0xF0000
    assert top <= 0xFFFFF, f"top of the mark range 0x{top:X} leaves plane 15"

    # The pattern must cover the whole range and nothing either side of it.
    assert pattern.fullmatch(chr(base))
    assert pattern.fullmatch(chr(top))
    assert not pattern.fullmatch(chr(base - 1))
    assert not pattern.fullmatch(chr(top + 1))

    # And the worst case round-trips through all three helpers.
    worst = chr(font_based_module._MAX_MARKABLE_CID)
    marked = font_based_module.mark_unmappable_cids(worst)
    assert ord(marked) == top
    assert font_based_module.count_marked_cids(marked) == 1
    assert font_based_module.strip_marked_cids(marked) == "�"
    assert font_based_module._private_use_count(marked) == 1


class _PageWithExtraDevanagari:
    """A real decoy page whose extracted text carries ``count`` extra Devanagari.

    The text layer has to be faked at ``get_text`` rather than drawn into the PDF,
    and that is a constraint rather than a shortcut: PyMuPDF ships no
    Devanagari-capable font (`helv` and `china-s` both report no glyph for ``क``,
    and there is no builtin `notos`), so drawing ``क`` produces a substituted glyph
    that extracts as nothing -- the count stays 0 at every value. Reaching for a host
    font instead would make this test pass or fail by which machine ran it.

    Everything else is the genuine article: real full-page raster, real non-embedded
    core font, real xref. Only the one input the threshold reads is substituted, so
    the branch under test is reached the way production reaches it.
    """

    def __init__(self, page: fitz.Page, count: int) -> None:
        self._page = page
        self._extra = "क" * count

    def get_text(self, *args: object, **kwargs: object) -> str:
        return self._page.get_text(*args, **kwargs) + "\n" + self._extra

    def __getattr__(self, name: str) -> object:
        return getattr(self._page, name)


class _DocWithExtraDevanagari:
    def __init__(self, doc: fitz.Document, count: int) -> None:
        self._doc = doc
        self._count = count

    def __getitem__(self, index: int) -> _PageWithExtraDevanagari:
        return _PageWithExtraDevanagari(self._doc[index], self._count)

    def __getattr__(self, name: str) -> object:
        return getattr(self._doc, name)


def _decoy_doc_with_devanagari(count: int) -> tuple[fitz.Document, object]:
    doc = fitz.open(stream=build_scanned_decoy_pdf(page_count=1), filetype="pdf")
    return doc, _DocWithExtraDevanagari(doc, count)


def test_the_decoy_devanagari_threshold_is_the_gate_on_paid_ocr():
    """``_DECOY_MAX_DEVANAGARI`` decides whether a text layer is real.

    ``classify_ocr_page`` returns ``None`` -- "this page has real text, do not OCR it"
    -- as soon as the Devanagari count reaches this value. So the constant is not a
    tuning knob on output quality; it is the boundary between a page transcribed for
    free and one sent to a metered vision model.

    Driving the function rather than restating the comparison. The previous version of
    this test asserted ``10 >= _DECOY_MAX_DEVANAGARI`` and ``not 9 >=
    _DECOY_MAX_DEVANAGARI``, which are the constant substituted into itself and hold at
    any value -- so flipping the ``>=`` in ``classify_ocr_page`` to ``>``, the exact
    off-by-one that is spend, left it green.
    """

    assert _DECOY_MAX_DEVANAGARI == 10

    below_doc, below = _decoy_doc_with_devanagari(_DECOY_MAX_DEVANAGARI - 1)
    at_doc, at = _decoy_doc_with_devanagari(_DECOY_MAX_DEVANAGARI)
    try:
        # One short of the threshold: still a decoy, so the page is sent to OCR.
        assert classify_ocr_page(below, 0) == SCANNED_DECOY_TEXT
        # At the threshold the text layer is accepted and no OCR is bought.
        assert classify_ocr_page(at, 0) is None
    finally:
        below_doc.close()
        at_doc.close()


# The same four characters in two orders. `क्रा` puts the virama before a CONSONANT
# (valid); `क्ार` puts it before a MATRA, which is one `_VIRAMA_MATRA_PATTERN` unit.
#
# Reordering rather than appending is what makes this a measurement of the term. The
# scorer rewards Devanagari characters and token count, so a damaged string built by
# adding text scores HIGHER than the clean one -- the first version of this test read
# a delta of -2 and would have been "fixed" by weakening the assertion. An identical
# character multiset holds every other term constant by construction.
_VALID_MATRA_TEXT = "क्रा"
_DAMAGED_MATRA_TEXT = "क्ार"


def test_the_matra_fixtures_differ_only_in_matra_validity():
    # Asserted, not asserted-by-comment: if a future edit breaks the multiset the rate
    # test below silently starts measuring something else.
    assert sorted(_VALID_MATRA_TEXT) == sorted(_DAMAGED_MATRA_TEXT)
    assert len(_VALID_MATRA_TEXT) == len(_DAMAGED_MATRA_TEXT)


@pytest.mark.parametrize("units", [1, 2, 3])
def test_matra_damage_is_charged_at_its_pinned_rate(units):
    """The rate, not just the number.

    A pin says the constant is 8. This says the scorer subtracts 8 **per unit**, which
    is what makes the pin mean something: the term could be dropped from the expression
    entirely, or changed to a flat charge, and a bare pin would still pass.
    """

    valid = " ".join([_VALID_MATRA_TEXT] * units)
    damaged = " ".join([_DAMAGED_MATRA_TEXT] * units)

    delta = _markdown_quality_score(valid) - _markdown_quality_score(damaged)
    assert delta == units * 8


def test_the_two_layout_modules_agree_on_the_geometry_they_share():
    """``_HEADER_Y_MAX`` and ``_COLUMN_GUTTER`` are each defined twice.

    ``structure_detection`` decides a document IS a two-column article;
    ``two_column_layout`` then splits it. If the two copies drift, a document is
    classified with one threshold and split with another, and the failure is a
    mis-split page rather than an error.

    Not merged into a shared constant here -- that is a refactor with its own review.
    Making the coupling assert itself is the cheap half.
    """

    assert (
        structure_detection_module._HEADER_Y_MAX
        == two_column_layout_module._HEADER_Y_MAX
    )
    assert (
        structure_detection_module._COLUMN_GUTTER
        == two_column_layout_module._COLUMN_GUTTER
    )


def test_the_scoring_weights_form_the_ordering_their_derivations_claim():
    """The derivations above are prose; this makes them checked.

    Three of them state a RELATIONSHIP rather than an absolute -- "half
    _UNDECODED_GLYPH_PENALTY", "between", "equal to". A relationship stated only in a
    comment goes stale the moment one side moves, and the pin on each individual value
    cannot notice: every pin would still hold at its own number while the sentence
    joining them became false.
    """

    from likhit.converters import nepali_pdf as m

    # "half _UNDECODED_GLYPH_PENALTY"
    assert m._EXCESS_SINGLE_TOKEN_PENALTY * 2 == m._UNDECODED_GLYPH_PENALTY
    # "between _EXCESS_SINGLE_TOKEN_PENALTY and _UNDECODED_GLYPH_PENALTY"
    assert (
        m._EXCESS_SINGLE_TOKEN_PENALTY
        < m._MATRA_DAMAGE_PENALTY
        < m._UNDECODED_GLYPH_PENALTY
    )
    # "equal to _MATRA_DAMAGE_PENALTY"
    assert m._SUSPICIOUS_TOKEN_PENALTY == m._MATRA_DAMAGE_PENALTY
    # "equal to _DEVANAGARI_CHAR_CREDIT, so one such token cancels one character"
    assert m._VOWEL_POOR_TOKEN_PENALTY == m._DEVANAGARI_CHAR_CREDIT
    # "between _VOWEL_POOR_TOKEN_PENALTY and _EXCESS_SINGLE_TOKEN_PENALTY"
    assert (
        m._VOWEL_POOR_TOKEN_PENALTY
        < m._PIPE_HEAVY_LINE_PENALTY
        < m._EXCESS_SINGLE_TOKEN_PENALTY
    )
    # "a cid literal IS a glyph that did not decode"
    assert m._CID_GARBAGE_PENALTY == m._UNDECODED_GLYPH_PENALTY
    # and the anchor really is the heaviest weight in the score
    assert m._UNDECODED_GLYPH_PENALTY == max(
        m._DEVANAGARI_CHAR_CREDIT,
        m._SUSPICIOUS_TOKEN_PENALTY,
        m._VOWEL_POOR_TOKEN_PENALTY,
        m._PIPE_HEAVY_LINE_PENALTY,
        m._EXCESS_SINGLE_TOKEN_PENALTY,
        m._MATRA_DAMAGE_PENALTY,
        m._CID_GARBAGE_PENALTY,
        m._UNDECODED_GLYPH_PENALTY,
    )


def test_the_candidate_score_carries_no_unnamed_weight():
    """Closes the CLASS, not the six instances that were found.

    This file's own limit, recorded in its docstring, is that it sees *named*
    module-level constants only -- so a weight written inline is invisible to it. Six
    were, in the one function that decides which transcript ships, and one of them was
    the anchor two registered derivations cited. Naming them fixes those six; this
    test is what stops a seventh being added the same way.

    Scoped to ``_markdown_quality_score`` deliberately. src/ holds 432 distinct
    non-trivial numeric literals over 640 occurrences (see the module docstring for the
    instrument -- the pair is meaningless without it), nearly all structural -- array
    indices, small counts, geometry arithmetic -- so a repo-wide version of this test
    would be noise that nobody could keep green. This function is the one whose
    literals are all weights.
    """

    import ast
    import inspect
    import textwrap

    from likhit.converters import nepali_pdf as m

    source = textwrap.dedent(inspect.getsource(m._markdown_quality_score))
    literals = sorted(
        {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        }
    )

    # 0 and 1 are structural here: an empty-input guard and a divide-by-zero floor.
    assert literals == [0, 1], (
        f"unnamed numeric weight(s) in _markdown_quality_score: "
        f"{[v for v in literals if v not in (0, 1)]}. Every weight in this function "
        f"must be a module-level constant, so the registry above covers it."
    )
