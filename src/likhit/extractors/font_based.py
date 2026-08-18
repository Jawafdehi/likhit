"""Font-based extraction for Nepali PDFs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import os
import re
from pathlib import Path
from typing import Any

import fitz

from likhit.errors import ExtractionError, ScannedPdfError, ValidationError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment
from likhit.extractors.digit_companion import (
    detect_digit_companion_fonts,
    devanagarize_companion_digits,
)
from likhit.extractors.font_classifier import (
    SCANNED_DECOY_TEXT,
    classify_font,
    scan_ocr_pages,
    scan_pdf_fonts_by_page,
)
from likhit.extractors.kalimati import (
    fix_kalimati_cmap,
    normalize_devanagari_spacing,
    reorder_devanagari,
)
from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    get_converter,
    get_converter_for_map,
    get_output_converter_for_map,
    is_legacy_font,
)
from likhit.extractors.numeric_boundaries import (
    apply_line_numeric_boundary_repairs,
    collect_page_repairs_by_line,
)
from likhit.extractors.pua_maps import (
    KNOWN_UNMAPPABLE,
    is_symbol_pua_font,
    remap_symbol_pua,
    unlift_symbol_pua,
)
from likhit.extractors.tables import detect_page_tables, merge_continuation_tables
from likhit.models import Table

PAGE_RANGE_PATTERN = re.compile(r"^\d+(?:-\d+)?$")
SPAN_GAP_THRESHOLD = 0.75
# Zeroed ToUnicode maps otherwise collapse every unknown glyph to the same
# replacement character. Raw CIDs keep those glyphs distinct for later repair.
# This word REPLACES PyMuPDF's default rather than adding to it, and that is
# deliberate: OR-ing `TEXTFLAGS_RAWDICT` in stops all CID marking and deletes
# 1,250,148 glyphs corpus-wide. Do not "fix" it -- see
# `test_text_dict_flags_replace_the_default_and_must_not_be_made_additive`.
_TEXT_DICT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
# A raw CID is an arbitrary code point: observed values include 0x7a, an ordinary
# ASCII "z". Nothing distinguishes one from real text, so the glyphs stay
# distinct (which is the point) but every garble heuristic stops seeing them.
# Marked CIDs are offset into Supplementary Private Use Area A, which keeps them
# distinct AND inside the private-use range `_private_use_count` already counts.
# Plane 15 holds 0xFFFE code points, so any 16-bit CID fits.
_CID_MARK_BASE = 0xF0000
_MAX_MARKABLE_CID = 0xFFFD
_MARKED_CID_PATTERN = re.compile(r"[\U000F0000-\U000FFFFD]")
_PREFIX_IKAR_PATTERN = re.compile(r"(?:(?<=^)|(?<=[\s(]))ि(?=[\u0915-\u0939])")
# The lookahead is the eleven vowel *matras* only. It deliberately excludes the three
# nasal/visarga marks that used to be in this class -- anusvara U+0902, visarga
# U+0903, candrabindu U+0901 -- because an ikar followed by one of those is
# USUALLY ordinary Nepali rather than a mis-map: on the 6,223 published v11
# transcripts those three account for 95,153 matches, every sampled one of them
# correct (सिंह, सिंचाई, दिँदा, हिंसा, लिंक, निःशुल्क, मितिः), against 101,628
# matches for the matras, every sampled one of them garble (सिालन, आथििक, वििरण).
# Two vowel signs in a row cannot be typed; a vowel sign then a nasal mark is
# spelling. VOL-131: this false positive is what charged the correct `Spins` decode
# of `2366__…Dolakha Tamakoshi` 12 points for the word नदेखिंदा, losing it the span.
#
# ⚠️ "Usually", not "always" -- 98.3%, and the residue is recovered separately below.
_INVALID_IKAR_PATTERN = re.compile(r"ि(?=[ािीुूृॄेैोौ])")
# The 1.7% the class above gives up, charged back. A Devanagari vowel sign is only
# well formed after a CONSONANT or a virama-terminated cluster, so an ikar that sits
# after an independent vowel, after another matra, or at the start of a cluster and is
# then followed by a nasal/visarga is not spelling under any orthography -- it is the
# same mis-map, wearing a lookahead the narrowed class no longer covers.
#
# Measured over the same 6,223 v11 transcripts: the narrowed class fires 101,628
# times, the pre-narrowing class 196,781, and this term recovers **1,550 sites in 240
# documents** -- every one of the 95,153 correct nasal sequences still excused. The
# recovered mass is dominated by `िःथ` (196, a mis-map of स्थ), `एिं` (467 across its
# spacing variants, a mis-map of एवं "and") and `पुिःत` (88, पुस्त).
#
# 🛑 Why it is worth a second pattern rather than a wider class: 1,451 of the 1,550
# (93.6%) sit in words whose WHOLE `_text_quality_penalty` is otherwise 0, so no axis
# saw them at all, and the direction of that miss is fails-open. On a 100-character
# window of `5772__…एकडारा गाउँपालिका` the aggregate's `penalty_per_deva` goes
# 0.10588 -> 0.03529 without this term, which flips `_passes_content_legacy_gate` from
# False to True and ACCEPTS the garbled decode instead of abstaining and leaving the
# keystrokes visible; a 160-character window of `5939__…गोसाइकुण्ड गाउँपालिका` flips
# the same way, 0.08823 -> 0.04411.
#
# 🛑 **The precomposed nukta letters U+0958-095F are deliberately NOT in the lookbehind,
# and writing them with `\uXXXX` escapes does not help.** They are Unicode composition
# EXCLUSIONS, so NFC rewrites each of them to base+U+093C -- which means a pattern whose
# source contains one is not a normalization fixed point, and
# `tests/test_regex_normalization_stability.py` fails on it however it is spelled in this
# file (escapes are resolved by Python before `re` ever sees them). That guard exists
# because this class of edit has taken the whole module's import down before.
#
# U+093C, the combining nukta, is in the class instead, and it is the form that occurs:
# NFC never produces U+0958-095F, so `क़ि` is क + ़ + ि and the lookbehind sees the nukta.
# The precomposed spelling IS reachable -- 829 occurrences in 311 v11 documents, and every
# map passes 0x958-0x95f through unchanged -- but precomposed-then-ikar-then-nasal has
# ZERO occurrences in all 6,223, measured: the nukta-only class recovers exactly the same
# 1,550 sites as one carrying the full range.
_IMPOSSIBLE_IKAR_NASAL_PATTERN = re.compile(
    # (?<! consonant | nukta | virama ) ikar (?= candrabindu | anusvara | visarga )
    "(?<![\u0915-\u0939\u093c\u094d])\u093f(?=[\u0901\u0902\u0903])"
)
#: The weight the term carries in `_text_quality_penalty`. Named because
#: `_map_ranking_key` has to subtract exactly it to forgive a site, and a literal in both
#: places is a two-site edit that only an equality test would catch -- the failure mode
#: `_RANKING_DOUBLET_FORGIVENESS`'s sibling term already produced once in this stack.
_IKAR_NASAL_WEIGHT = 6
_HALANT_IKAR_PATTERN = re.compile(r"्ि")
_DUPLICATE_CONSONANT_PATTERN = re.compile(r"([क-ह])\1")
# Named because `_legacy_map_garble` subtracts this exact term back out for map
# ranking. If the penalty ever adds the narrowed count at one weight while the
# subtraction removes it at another, the ranking measure runs low or negative.
_DUPLICATE_CONSONANT_WEIGHT = 3
# How many doublet hits `_legacy_map_garble` forgives when RANKING candidate maps.
# Calibrated, not chosen: see that function. One hit is inside the residual false
# positive rate the narrowing above admits, and is the margin that lost VOL-185's
# eleven documents their correct map; hundreds of hits are real damage and must still
# decide, which is what forgiving a bounded number rather than the whole term preserves.
_RANKING_DOUBLET_FORGIVENESS = 1
# The same floor on the other weak positive tell, for the same reason and calibrated in
# the same sweep (VOL-185). VOL-131 calibrated `stranded` on counts of 3 and 6 against 0,
# so a floor of 1 is inside what that calibration never rested on -- and a lone bracket
# was deciding a map wrongly: on `2992`/`2993 parsa gaupalika`, font `Arial`, `Preeti`
# carries 0 stranded to `FONTASY_HIMALI_TT`'s 1 and is the wrong map, losing `समानीकरण`
# (df 4,397), `ऋण` (4,549), `संघिय` (3,558) and `पोषण` (2,650) while gaining only `द्ध`
# (df 52). With the bracket forgiven the two level and `attested` decides, 23 to 22, the
# right way. `oag-corpus/runs/vol185/calibrate_two_floors_5f0833fc.py` sweeps the pair.
#
# 🛑 **The corpus figure for the two floors together, which no record carried.** Measured
# against the parent over all 6,236 OAG PDFs: the emitted text changes on **24 (doc,font)
# pairs in 24 documents** -- 20 from the doublet floor, 4 from this one -- against the
# **2** documents the derivations name (`2992`/`2993`, the same report filed twice). Net
# +35,754 characters, 47 documents moved, 12 worsened, worst -5.
#
# The two bracket-floor pairs no derivation mentions are `2989__Kaudana gaupalika`/`Arial`
# and `4834__खार्पुनाथ`/`LiberationSerif-Bold`; `2989` also sits OUTSIDE the 77-document
# set the sweep was run over, so it was never adjudicated by it. Five spans the parent's
# gate REJECTED are now decoded, four of them `LiberationSerif` faces -- `4817`'s new
# `PCS NEPALI` reading carries 19 doublets in 1,734 Devanagari characters, and
# `5023__नौकुण्ड`/`LiberationSerif` is a face this programme tracks separately. **None of
# those five has been adjudicated**, and that is stated here rather than left implied.
_RANKING_STRANDED_FORGIVENESS = 1
# The FIRST measured instance of this floor, and the one that named it: a single
# ikar+nasal site (`_IMPOSSIBLE_IKAR_NASAL_PATTERN`, `_IKAR_NASAL_WEIGHT` = 6 points).
# It was a forgiveness of its own -- `_RANKING_IKAR_NASAL_FORGIVENESS = 1` -- until
# VOL-226 showed the same argument holds for any small margin, whatever pattern raised
# it, and the floor below subsumed it exactly (see `_map_ranking_key`).
#
# The term is a gate signal first: 1,451 of its 1,550 sites sit in words whose whole
# penalty is otherwise 0, and without it `_passes_content_legacy_gate` accepts garble
# (see `_IMPOSSIBLE_IKAR_NASAL_PATTERN`). But `_map_ranking_key` reuses the same raw
# `penalty` to CHOOSE
# between candidate maps, and there a single site is not evidence about the map at all:
# it can be evidence about the SOURCE, which every candidate decodes alike.
#
# `3229__1613898700sidingwa gapa.pdf`, font `Spins`, 687 raw / 592 Devanagari characters,
# is the measured instance. One source region decodes as `\u092e\u094d\u0926\u093e\u093f\u0901` under Spins and Preeti
# and as `\u092e\u094d\u0926\u093e\u093e\u093f` under Kantipur, PCS NEPALI and FONTASY_HIMALI_TT -- malformed either
# way (two vowel signs in sequence), so it says nothing about which map is right, but
# only the first ordering matches this pattern. That single site costs 6 penalty points,
# and `penalty` ranks ABOVE the stranded-bracket tell, so it decides the span before any
# axis that actually separates the maps is consulted:
#
#     map           hits  penalty  stranded   ratio   deva
#     Spins            4        6         0  0.9801    592   <- correct
#     Kantipur         4        0        12  0.9681    577
#
# Spins is right and not marginally so: it reads `\u0915\u093e\u0932\u0940\u0916\u094b\u0932\u093e`, `\u092d\u091e\u094d\u091c\u094d\u092f\u093e\u0901`, `\u0924\u093f\u092e\u094d\u092c\u0941\u0901\u092a\u094b\u0916\u0930\u0940`,
# `\u0938\u093f\u0926\u094d\u0927\u0947\u0936\u0935\u0930\u0940 \u092e\u093e.\u0935\u093f.`, `\u091c\u0928\u0924\u093e \u092a\u094d\u0930\u093e.\u0935\u093f.` where Kantipur strands `)` mid-word twelve times
# and PCS NEPALI injects digits (`\u092e\u096c\u093f\u096c\u093e\u0930` for `\u092e\u091f\u093f\u091f\u093e\u0930`). Charged, the whole font unit is
# lost: `main` elects a wrong map that then FAILS the gate, so 592 Devanagari characters
# go undecoded -- the drop-rather-than-remap hazard `_map_ranking_key` names.
#
# One, not two, and only on the ranking axis:
#   * the gate keeps every site, because that is where the fails-open evidence is;
#   * a systematic mis-map fires far more than once -- `_IMPOSSIBLE_IKAR_NASAL_PATTERN`
#     measures the recovered mass at 1,550 sites in 240 documents, ~6 per document, and
#     the two windows it measured for the gate carry 3 and 4 in 100 and 160 characters;
#   * corpus-wide the site-specific forgiveness this replaced changed exactly ONE
#     (document, font) pair of the 6,236 swept, the one above. The floor that replaced
#     it changes TWO -- that one plus `3843`/`5143`; see the calibration below.
#
# \u26a0\ufe0f Widening the lookbehind instead was measured and is WORSE than the defect: excluding
# a preceding vowel sign gives up **300 of 1,523 sites across 65 documents** (19.7%),
# because `\u093e`/`\u0941`/`\u0942` before the ikar is the second-commonest shape the term catches --
# after SPACE (629) and `\u090f` (479) it is `\u0941` (126) and `\u093e` (83). This forgiveness gives up
# one site in one document.
#
# \u26a0\ufe0f **1,550/240 and 1,523/227 are the same term on different generations, not a
# correction.** `_IMPOSSIBLE_IKAR_NASAL_PATTERN`'s own block counted v11
# (`markdown-quality-v11`, 6,223 documents);
# the 1,523 here and the 300 given up are v16 (`markdown-quality-v16`, 6,235). Re-derive
# against the generation you mean and say which one, rather than reconciling them.
#
# The third floor, on the garble axis itself, for the third time the same reason
# applies (VOL-226). `_text_quality_penalty` measures WELL-FORMEDNESS, and a wrong
# legacy map does not emit malformed Devanagari -- it emits well-formed Devanagari
# spelling the wrong word. So a small garble margin between two candidate maps is not
# evidence about which of them read the span, while it is large enough to veto the two
# axes that are (`stranded`, `attested`).
#
# 12 is one hit of the heaviest single pattern in `_text_quality_penalty` -- a
# replacement char or a PUA code point at 12 points each -- so the floor forgives at
# most ONE artifact of ANY kind and no more.
#
# Swept over all 6,236 corpus PDFs at 3/6/12/24/48/70/71
# (`oag-corpus/runs/vol226/floor-sweep-88a30e76.txt`). The floor alone has NO plateau
# -- the changed-decision set grows monotonically with it, 0 decisions at 3 and 11 at
# 71 -- which is why the raw count is restored below `attested` in
# `_map_ranking_key`. With that restoration only decisions an evidence axis actually
# separates can move, and the value stops carrying the weight: re-swept at 6/12/24/48
# (`key-sweep-88a30e76.txt`), all four produce the IDENTICAL corpus outcome -- the same
# 2 font decisions of 37,901, 0 abstentions, 0 byte differences anywhere else. That
# plateau is the calibration.
#
# **6, and the value is FORCED rather than chosen.** It is the floor's lower bound -- both
# spans this is for carry a margin of exactly 6 -- and it is what makes this floor subsume
# the ikar+nasal forgiveness it replaced *exactly*: that term subtracted
# `_IKAR_NASAL_WEIGHT` = 6 wherever it fired, so at 6 every case it decided still decides
# the same way, and the landed tests that pin it still hold. At 12 they do not:
# `test_a_second_ikar_nasal_site_still_decides` builds `penalty` 12 across two sites and
# expects 6 forgiven, which a floor of 12 forgives entirely. The plateau means 6 costs
# nothing to prefer, so the tie-breaker is behaviour already shipped.
#
# **Re-measured on `main` rather than inherited from v14**, because the axes below this one
# moved since (`attested` VOL-185, the stranded forgiveness, the ikar split). Two trees
# swept over all 6,236 corpus PDFs, `tools/dump_map_choices.py` -- the arms differ by TREE,
# not by a constant, because this change alters the tuple's SHAPE and no constant expresses
# the other arm:
#
#     changed decisions       2 of 1,389 decided (document, font) aggregates
#     3843__...Godawari       Preeti            -> Spins
#     5143__...हलेसी तुवाचुङ    FONTASY_HIMALI_TT -> Preeti
#     abstentions gained      0
#
# Both were vetoed by a 6-point margin while every axis below it favoured the map that
# wins here; on both, `ikar_nasal` is **0**, which is why the retired term did not reach
# them. `3229` is unchanged -- its margin is forgiven either way.
#
# ⚠️ **The unit matters, and a SPAN is not the unit.** `detect_content_legacy_fonts` joins
# a font's spans and calls the chooser once, so the aggregate is what production decides.
# Measured at span level, 32,307 candidate spans over 150 documents decide **0** -- spans
# are too short to clear the accept gate -- so a span-level fixture can abstain here while
# the corpus outcome is clean. One landed test's fixture does exactly that; see
# `tests/test_legacy_map_mixed_margin.py`.
_RANKING_GARBLE_FORGIVENESS = 6
# Two identical adjacent consonants are a real garble signal, but adjacency ALONE
# is mostly wrong: in Nepali a stem ending in a consonant plus a suffix beginning
# with the same one is ordinary morphology. Measured over all 6,223 documents of
# `markdown-quality-v11` (VOL-135, `oag-corpus/runs/vol135/`), the bare pattern
# fires 1,087,029 times in 99.4% of documents, and adjudicating every one of the
# 34,684 distinct doublet-bearing words against the corpus's own 1.87M-word
# vocabulary put ~4 of every 5 hits on correct Nepali.
#
# The doublet is NOT droppable, though: the same sweep found >=209,998 occurrences
# of genuine damage, dominated by legacy i-matra loss resurfacing as a doubled
# consonant (`खररद`->`खरिद`, `ववरण`->`विवरण`, `आन्तररक`->`आन्तरिक`), whose clean forms
# are attested 100,000-800,000 times each. Down-weighting the pattern would give
# that up.
#
# So charge adjacency only when it is not explained by morphology. The two lists
# below are closed and auditable, and were chosen by scoring thirteen candidate
# rules against that adjudication (`runs/vol135/score_rules.py`): this one keeps
# 91.6% of the true damage while removing 88.6% of the spurious mass. Note the
# co-signal reading that suggests itself first -- "charge only if the token already
# shows an i-matra anomaly" -- scores 9.0% recall and is not usable.
_MORPHEME_SUFFIXES = (
    "को",
    "मा",
    "ता",
    "ताको",
    "ताले",
    "तामा",
    "सँग",
    "सङ्ग",
    "हरु",
    "हरू",
    "रेट",
    "योजना",
)
# Correct Nepali whose doubled consonant is word-internal rather than at a
# morpheme boundary, so no suffix test can reach it.
_DOUBLED_CONSONANT_LEXEMES = (
    "व्यय",
    "कक्षा",
    "अध्ययन",
    "तत्काल",
    "ललितपुर",
    "ससुरा",
    "बबरमहल",
    "उत्खनन",
    "जज",
    "ननाघे",
    "तत्सम",
    "सम्ममा",
    "ससर्त",
    "छैनन्",
    "तहहरु",
    "कार्ययोजना",
)
_DEVANAGARI_WORD_PATTERN = re.compile(r"[ऀ-ॿ]+")
_SUSPICIOUS_ARTIFACT_PATTERN = re.compile(
    r"(ख्ज|अधध|धिरूद्ध|धिरुद्ध|प्रविधध|राविय|नम्िर|िडा|ितन|उज्वल|उज्जवल)"
)
# Devanagari signs that are valid Unicode but essentially never occur in real
# Nepali: short-O (U+094A) and the nukta-form consonants NNNA/RRA/LLLA
# (U+0929/0931/0934). They are produced almost exclusively by a mis-applied
# legacy-font byte map (e.g. Preeti read as WinAnsi), so they are a reliable
# signal that a fragment is garbled even when the rest looks Devanagari.
# NOTE: candra-O (U+0949 ॉ) is deliberately EXCLUDED — it appears in legitimate
# Nepali/Hindi loanwords (डॉलर "dollar", कॉल "call", डॉक्टर "doctor"), so
# flagging it would penalise clean text. The remaining signs have no such use.
# Escapes, not literals, for the same reason as _ORPHAN_MATRA_PATTERN in
# converters/nepali_pdf.py: U+0929/0931/0934 are composition exclusions that every
# normalization form decomposes to <base, U+093C NUKTA>. Written literally this
# class normalizes into a FIVE-member set that includes the bare consonants, so a
# garble detector would start firing on three of the commonest Nepali letters.
# Measured on 70 characters of clean prose: 0 hits as written, 11 after
# normalization, every one a false positive.
_INVALID_SIGN_PATTERN = re.compile(r"[\u094a\u0929\u0931\u0934]")

# Devanagari letters and combining marks, EXCLUDING the digits U+0966-U+096F and the
# two dandas U+0964/U+0965.
_DEVANAGARI_LETTER_RANGE = "\u0900-\u0963\u0970-\u097f"
# An ASCII bracket wedged between two Devanagari LETTERS is a positive tell that a
# legacy map does not fit the face. A map that fits turns every keystroke into
# Devanagari; a wrong one leaves its own literal reading behind. `Spins` reads the byte
# that Preeti and PCS NEPALI read as `)` as the anusvara `ं`, so `संख्या` ("number")
# comes out of the wrong map as `स)ख्या` (VOL-77, VOL-89, VOL-131).
#
# Digits are excluded because `दफा ३५(२)` -- "section 35(2)" -- is ordinary legal
# citation in these reports, and U+0966-U+096F sit inside the Devanagari block, so the
# obvious `[ऀ-ॿ]` class charges correct text. That is a live defect in
# `runs/vol89/adjudicate_font.py`, which counts this tell with the digits included.
#
# This count is DELIBERATELY NOT a term in `_text_quality_penalty`. On the 6,223
# published v11 transcripts it fires 33,256 times in 4,878 of them, and most of those
# are Nepali alphabetic list labels -- `क)वित्तीय`, `ख)राजस्व`, `ग)सशर्त` -- which is how
# Nepali writes `a)`, `b)`, `c)`, plus ordinary parentheticals like `फिर्ता(साँवा)`. As an
# absolute quantity it is therefore not a damage measure, and the penalty feeds an
# absolute accept ceiling. Comparison between two decodes of the same span is the only
# use here.
#
# ⚠️ 33,256 is the LOOKAHEAD form's count, the one shipped below. The consuming form this
# commit replaced fires 33,204 on the same corpus -- a 52-hit difference, and the reason
# the two figures both appear in this lineage's records. The 4,878 is right for both.
#
# 🛑 **"A shared label is shared by both" is not true of these maps, and the axis is
# narrower than it looks.** Measured by inverting every map over 0x00-0x2FFF: an output
# `)` comes from source `_` (0x5f) under Preeti / Kantipur / PCS NEPALI /
# FONTASY_HIMALI_TT / Sagarmatha, and from source `{` (0x7b) under Spins; an output `(`
# comes from `-` (0x2d, plus 0xad on three of them) under the five and from `[` (0x5b)
# under Spins. So a label is shared only WITHIN a map family, the one boundary this axis
# can ever act on is {Spins} vs {the other five}, and its sign is set by ground truth: on
# a genuine Spins face a `_` is an anusvara and the tell correctly charges the five, but
# on a genuine Preeti-family face a `_` is a real `)` and the tell charges the CORRECT map
# while sparing Spins. A single ordinary list label (`क)वित्तीय`, 1,506 corpus
# occurrences) is enough to hand Spins a 1-0 advantage on an axis that outranks `ratio`.
#
# That direction is real but it cannot ship on its own: `_RANKING_STRANDED_FORGIVENESS`
# (VOL-185, later in this same chain) forgives the first bracket, which is exactly the
# `>= 2` floor the exposure needs -- both spans this axis was calibrated on clear it at
# 3-vs-0 and 6-vs-0. A 400-document sample found no accepted-map change either way.
#
# ⚠️ The gap in the OTHER direction, recorded as a decision rather than fixed: the
# pattern requires EXACTLY ONE bracket between the two letters, so a run -- `क))ख`,
# `क)(ख`, `क()ख`, `क[]ख` -- counts 0. Measured on the same corpus: 148 occurrences in 76
# documents, and the top contexts are `न)(म` (25), `ख)(ग` (18), `ग)(घ` (8), `क)(ख` (6),
# i.e. overwhelmingly two adjacent legitimate parenthesised list labels rather than
# residue. Reading 0 there is fail-OPEN (both candidates read 0, so the axis is silenced,
# not inverted) and on this evidence it is the safer half of the trade. Widening to
# `[)(\]\[}{]+` would need those 148 adjudicated first.
#
# The trailing letter is matched by LOOKAHEAD, not consumed. `findall` scans
# non-overlapping, so consuming it made consecutive tells invisible: `क)ख)ग` -- which is
# exactly how a wrong map renders two adjacent Nepali list labels -- counted 1 instead of
# 2, because `ख` belonged to the first match. That is the shape a forgiveness floor of
# one then waves through entirely, so the undercount is worst precisely where the tell
# matters most.
_STRANDED_BRACKET_PATTERN = re.compile(
    "["
    + _DEVANAGARI_LETTER_RANGE
    + "]"
    + r"[)(\]\[}{]"
    + "(?=["
    + _DEVANAGARI_LETTER_RANGE
    + "])"
)


def parse_page_range(spec: str, total_pages: int) -> tuple[int, int]:
    """Parse a 1-based inclusive page range to 0-based bounds."""

    if not PAGE_RANGE_PATTERN.fullmatch(spec.strip()):
        raise ValidationError("Invalid page range format. Use format: '1-3' or '5'")

    if "-" in spec:
        start_text, end_text = spec.split("-", 1)
        start = int(start_text)
        end = int(end_text)
    else:
        start = end = int(spec)

    if start < 1 or end < 1 or end < start:
        raise ValidationError("Invalid page range format. Use format: '1-3' or '5'")

    if start > total_pages:
        raise ValidationError(
            f"Requested page range starts beyond document length ({total_pages} pages)"
        )

    end = min(end, total_pages)
    return start - 1, end - 1


def _iter_dict_spans(page_dict: dict) -> list[dict]:
    """Flatten a page dict to its spans in document order."""

    return [
        span
        for block in page_dict.get("blocks", [])
        if "lines" in block
        for line in block["lines"]
        for span in line["spans"]
    ]


def _char_position(char: dict) -> tuple[float, ...]:
    """Position key pairing one character across two extractions of a page.

    Rounded because the two extractions agree on glyph geometry to well within
    a hundredth of a point, but not always bit-for-bit.
    """

    return tuple(round(value, 2) for value in char["bbox"])


def _replacement_and_decoded_positions(
    page_dict: dict,
) -> tuple[set[tuple[float, ...]], set[tuple[float, ...]]]:
    """Split a raw page dict's character positions by whether they decoded."""

    replacement: set[tuple[float, ...]] = set()
    decoded: set[tuple[float, ...]] = set()
    for span in _iter_dict_spans(page_dict):
        for char in span.get("chars", ()):
            target = replacement if char["c"] == "�" else decoded
            target.add(_char_position(char))
    return replacement, decoded


def _to_dict_shape(page_dict: dict) -> dict:
    """Collapse a `rawdict` page to `dict` shape: span `text`, no `chars`.

    `dict` mode's span text is exactly the concatenation of `rawdict`'s per-glyph
    characters, so callers cannot tell which mode produced the page.
    """

    for span in _iter_dict_spans(page_dict):
        if "chars" in span:
            span["text"] = "".join(char["c"] for char in span.pop("chars"))
    return page_dict


def mark_unmappable_cids(text: str) -> str:
    """Offset every character of `text` into the marked-CID range."""

    return "".join(
        chr(_CID_MARK_BASE + ord(char)) if ord(char) <= _MAX_MARKABLE_CID else char
        for char in text
    )


def unmark_cids(text: str) -> str:
    """Undo :func:`mark_unmappable_cids`, restoring each character it offset.

    Distinct from :func:`strip_marked_cids`, which replaces a mark with a visible
    U+FFFD for reporting. This recovers the ORIGINAL character, which is what any
    predicate reading a span's content needs -- a marked glyph still carries its
    identity, it is just offset.
    """

    return "".join(
        chr(ord(char) - _CID_MARK_BASE)
        if _CID_MARK_BASE <= ord(char) <= _CID_MARK_BASE + _MAX_MARKABLE_CID
        else char
        for char in text
    )


# Latin subsets whose glyph ids sit a uniform offset from ASCII decode losslessly
# once that offset is known, so text a missing /ToUnicode would otherwise throw
# away can be read back exactly. Recovery is deliberately hemmed in on four
# independent sides, because the failure mode is not "recovers less" -- it is
# "invents English that was never on the page".
#
# 1. The font name must say Latin. This is a POSITIVE requirement, not merely the
#    absence of a legacy name, because the corpus also carries fonts whose script
#    is undetermined (`CIDFont+F1..F5`, `TT3CBt00`, `SymbolMT`); their glyph order
#    is unknown, so reading them as ASCII would fabricate text.
# 2. The font must not be one the legacy-map registry recognises. Devanagari
#    keystroke fonts hold real ASCII bytes -- Preeti read as raw CIDs *is* ASCII,
#    and ASCII of Nepali Preeti accidentally contains English words, which is the
#    same trap as the Preeti digraphs `of]`/`If]q` reading as "of"/"if". Measured:
#    without this gate the rule accepts `n]fk/LIf0fsf]k|tj]gdf pNn]vt Joxf]fsf `
#    as English on two audit bulletins.
#
#    ⚠️ **It closes that class only for fonts NAMED after a legacy layout, and this
#    comment used to read as though the class were closed outright.** `is_legacy_font`
#    matches on the NAME, so mislabeled Preeti -- this repo's own
#    `build_mislabeled_preeti_pdf`, "a born-digital page whose bare Helvetica font
#    carries Preeti keystrokes", and the `ABCDE+Helvetica` shape
#    `detect_content_legacy_fonts` names -- passes gate 2 and is caught only by gate 1
#    (`Helvetica` IS in the Latin family list, so it is not caught there either) and
#    gate 4.
#
#    What bounds it is that gate 2 is never the OPERATIVE exclusion in practice:
#    measured over 1,631 distinct corpus font names, **0** are both Latin-family-named
#    and legacy-registered, so the two name tests never disagree on a real font. The
#    demonstration case above is the identity map at k=0, where the "recovered" text is
#    the keystrokes themselves. A content-side exclusion -- declining any font the same
#    document's content-legacy pass has made a candidate -- is the right shape if this
#    is ever widened; it is not added here because gate 2 currently excludes nothing
#    that gate 1 does not.
# 3. Only two offsets are tried. A wide search is what lets a repeated boilerplate
#    span reach 99.7% per-font offset coherence and still decode to
#    `RQPONMPLKJIPOHGFEKEDEK...`; coherence across repeated content is not
#    evidence of a correct decode.
# 4. The decode must read as English against an EXTERNAL lexicon. A vocabulary
#    derived from the corpus would be scored against text produced by the same
#    extractor whose failures are being repaired.
_LATIN_CID_FONT_FAMILIES = re.compile(
    r"times|arial|calibri|garamond|cambria|courier|helvetica|book|acumin|dejavu|"
    r"verdana|tahoma|georgia|palatino|century|candara|consolas|corbel|segoe|roboto|"
    r"franklin|gill|futura|myriad|minion",
    re.I,
)
# 🛑 The family list is an unanchored SUBSTRING match, and `book` admits
# `Bookshelf Symbol 7` -- a pictorial family whose glyph order is not ASCII+k at all.
# Combined with the measured 1.5-2.1% false-recovery rate on arbitrary glyph ids, that
# is a font whose dingbats could be rewritten as English. Gate 1 is described as a
# POSITIVE Latin requirement that excludes fonts of undetermined script, and a
# pictorial family is exactly such a font, so the exclusion belongs here rather than in
# a wider word-boundary rule (`Bookman Old Style` and `Bookerly` are real Latin text
# faces and a boundary would keep them out).
#
# Same kind of evidence as the family list itself: a name list.
_SYMBOL_FONT_NAMES = re.compile(r"symbol|dingbat|wingding|webding|ornament", re.I)
# k=0 is the identity mapping and the modal case: the CID already *is* the ASCII
# code and the only defect is the missing /ToUnicode. k=29 is the standard
# glyph-order subset where glyph 3 is the space. Nothing else is tried.
_CID_RECOVERY_OFFSETS = (0, 29)
_CID_RECOVERY_WORD = re.compile(r"[A-Za-z]+")
# Tokens shorter than 3 characters are the commonest accidental dictionary hit.
_CID_RECOVERY_MIN_TOKEN = 3
# Two dictionary words settle it alone. The one-word leg needs the word to
# dominate the span, and BOTH legs are load-bearing: coverage alone rejects
# `(based on INTOSAI SAI-PMF Pilot Version, 2013)` at cov=0.425, because
# acronyms, digits and punctuation are not dictionary characters -- so a
# coverage-only rule cannot see one of this defect's founding examples.
_CID_RECOVERY_MIN_HITS = 2
_CID_RECOVERY_MIN_COV_ONE_HIT = 0.5
# Hunspell ships these; `/usr/share/dict/words` is absent on this host, which is
# what the note in the corpus' gate_latin_loss.py is actually describing.
# Override with a colon-separated LIKHIT_LATIN_LEXICON. No lexicon means no
# recovery: the transform fails closed rather than guessing.
_CID_RECOVERY_LEXICON_ENV = "LIKHIT_LATIN_LEXICON"
_CID_RECOVERY_LEXICON_PATHS = (
    "/usr/share/hunspell/en_US.dic",
    "/usr/share/hunspell/en_GB.dic",
)


@lru_cache(maxsize=1)
def _latin_cid_lexicon() -> frozenset[str]:
    """Load the external English word list once per process."""

    override = os.environ.get(_CID_RECOVERY_LEXICON_ENV)
    paths = override.split(":") if override else _CID_RECOVERY_LEXICON_PATHS
    words: set[str] = set()
    for name in paths:
        path = Path(name)
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        # ⚠️ Two filters that used to be in this loop are DEAD, and each carried a
        # comment asserting it did work. A hunspell header skip
        # (`index == 0 and line.strip().isdigit()`) is unreachable because a numeric
        # header already fails `word.isalpha()`; a `len(word) >= 2` floor is unreachable
        # because `_CID_RECOVERY_MIN_TOKEN` is 3, so a two-letter entry is never looked
        # up. Removed rather than annotated -- a dead filter with a confident comment is
        # what made the audit of these gates read as more complete than it was.
        for line in lines:
            word = line.split("/", 1)[0].strip()
            if word.isalpha():
                words.add(word.lower())
    return frozenset(words)


def _latin_cid_score(text: str) -> tuple[int, float]:
    """(dictionary hits among long-enough alpha tokens, dictionary char coverage)."""

    lexicon = _latin_cid_lexicon()
    tokens = [
        token
        for token in _CID_RECOVERY_WORD.findall(text)
        if len(token) >= _CID_RECOVERY_MIN_TOKEN
    ]
    hits = [token for token in tokens if token.lower() in lexicon]
    nonspace = sum(1 for char in text if not char.isspace())
    coverage = sum(len(token) for token in hits) / nonspace if nonspace else 0.0
    return len(hits), coverage


def is_latin_cid_font(font_name: str) -> bool:
    """True if this font's undecodable glyphs may be read as offset ASCII."""

    if not font_name or is_legacy_font(font_name):
        return False
    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    if _SYMBOL_FONT_NAMES.search(base):
        return False
    return bool(_LATIN_CID_FONT_FAMILIES.search(base))


def recover_latin_cid_text(cids: list[int], font_name: str) -> str | None:
    """Read a run of undecodable Latin glyph ids back as text, or decline.

    Returns text only when a uniform offset lands the whole run in printable
    ASCII *and* the result reads as English. Declining is the common case and is
    not a failure: the caller then marks the run as an unmappable CID exactly as
    before, so a document this cannot read is left byte-identical.

    🛑 **A recovered run is NOT only new output; it is new INPUT to two decisions
    upstream of it, and no record said so.** The recovered ASCII enters that font's
    per-document aggregate, and neither `_is_probably_legacy_ascii` nor
    `choose_legacy_map_detailed` unmarks, so the aggregate they score after this change
    is not the aggregate they scored before. Two consequences, both real:

    * **content-legacy CANDIDACY** can flip -- a font that was mostly undecodable marks
      now presents ASCII keystroke-shaped text; and
    * **every map-ranking scalar** moves, because the aggregate is longer and carries
      Latin letters.

    So this change moves corpus output on the MAP-CHOICE axis too, not only on the runs
    it recovers. The docstring's "byte-identical" promise holds only for a DECLINED run.

    ⚠️ And a recovered run then becomes eligible for the content-legacy remap itself, so
    a short recovered Latin word inside a candidate font can be rewritten into Devanagari
    that spells nothing -- all three Latin vetoes are blind to a short run by
    construction (axis 1 needs >=16 non-space characters). Measured: **0 of 331** real
    recoveries land in a candidate font, and the harm predates this change (an
    undecodable run already shipped as a visible U+FFFD gap), so it is disclosed rather
    than fixed. The fix shape, if the footprint ever grows, is to record recovery
    provenance on the span and treat a recovered run as veto-flagged unconditionally --
    not to lower a Latin veto's floor, which costs Nepali faster than it saves English.
    """

    if not cids or not is_latin_cid_font(font_name):
        return None
    if not _latin_cid_lexicon():
        return None

    low, high = min(cids), max(cids)
    accepted: list[str] = []
    for offset in _CID_RECOVERY_OFFSETS:
        # The whole run must land in printable ASCII. This range test is what
        # keeps the transform away from Devanagari glyph ids, which sit far above
        # the band at both offsets.
        if low + offset < 0x20 or high + offset > 0x7E:
            continue
        text = "".join(chr(cid + offset) for cid in cids)
        hits, coverage = _latin_cid_score(text)
        if hits >= _CID_RECOVERY_MIN_HITS or (
            hits >= 1 and coverage >= _CID_RECOVERY_MIN_COV_ONE_HIT
        ):
            accepted.append(text)

    # 🛑 **Every admissible offset is scored against the acceptance rule, and TWO
    # acceptances is a decline.** This used to keep the arg-max on `(hits, coverage)`
    # with a strict `>`, i.e. FIRST-WINS on a tie -- and `_CID_RECOVERY_OFFSETS` is
    # ordered `(0, 29)`, so k=0 won. For a run short enough that both offsets are
    # admissible the two routinely score identically at (1, 1.0), and where the true
    # encoding is k=29 -- the standard glyph-order subset this feature exists to read --
    # first-wins emits a different, confidently-wrong word. An ambiguous decode is not
    # evidence of anything, and declining costs nothing: the caller marks the run
    # exactly as before, which is the same outcome the run had without this feature.
    #
    # ⚠️ Structurally rare rather than common, and that is why it went unnoticed: k=29's
    # space glyph is cid 3, which fails k=0's `low + 0 >= 0x20` test, so any run
    # containing a space is admissible at one offset only. Measured over 331 real
    # recoveries: **0** had more than one accepted offset, so this changes no recovery in
    # the corpus and removes a wrong-decode path.
    if len(accepted) != 1:
        return None
    return accepted[0]


def strip_marked_cids(text: str, replacement: str = "�") -> str:
    """Render marked CIDs back to a visible replacement character."""

    return _MARKED_CID_PATTERN.sub(replacement, text)


def count_marked_cids(text: str) -> int:
    """Count marked CIDs in `text`.

    Kept deliberately, though nothing in `src/` calls it since marks stopped
    being charged in the candidate comparison. It is the only way to observe how
    much a page failed to decode without re-deriving the mark range, which is
    what the tests and the corpus instruments use it for.
    """

    return len(_MARKED_CID_PATTERN.findall(text))


def get_cid_marked_page_dict(page: fitz.Page) -> dict:
    """Extract a page dict whose unmappable glyphs are marked as private-use.

    A page with nothing unmappable is extracted once, exactly as before. Only a
    page that actually decodes some glyph to U+FFFD pays a second extraction, and
    the two are paired to learn *which* characters the CID flag substituted --
    the flag alone cannot tell us, because a raw CID is indistinguishable from
    real text.

    Pairing is positional over individual glyph boxes, not over spans. Spans are
    the wrong unit: dropping the CID flag regroups them, so the two extractions
    routinely disagree on span count even though every glyph still sits at the
    same coordinates. Span-level pairing therefore failed wholesale on real
    documents -- measured on the Nepali audit corpus, 38 of 40 sampled pages had
    mismatched span counts and came back with no marking at all, which is the
    exact blindness this marking exists to remove. Glyph boxes survive the
    regrouping: the same measurement pairs 98.6% of replacement characters.

    A position that decodes to real text somewhere on the page and to U+FFFD
    somewhere else cannot be attributed to either, so it keeps its raw CID rather
    than being guessed at.

    A run of unmappable glyphs in a Latin font is offered to
    `recover_latin_cid_text` first: for those subsets the glyph ids are a uniform
    offset from ASCII, so the text is present and merely unmapped. Only a run that
    decodes to English is taken; everything else is marked exactly as before.
    """

    # Bit 128 is dropped here on purpose: this pass detects unmappable glyphs BY
    # their U+FFFD, and `TEXTFLAGS_RAWDICT` already sets the CID bit, so making
    # this word additive returns zero U+FFFD and silently ends every marking below.
    plain_dict = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    replacement, decoded = _replacement_and_decoded_positions(plain_dict)
    if not replacement:
        return _to_dict_shape(plain_dict)

    unmappable = replacement - decoded
    cid_dict = page.get_text("rawdict", flags=_TEXT_DICT_FLAGS)
    for span in _iter_dict_spans(cid_dict):
        _recover_or_mark_unmappable_span(span, unmappable)
    return _to_dict_shape(cid_dict)


def _unmappable_runs(
    span: dict, unmappable: set[tuple[float, ...]]
) -> list[list[dict]]:
    """Group a span's unmappable characters into maximal consecutive runs.

    Runs, not individual glyphs, because a uniform-offset decode can only be
    judged as English over a stretch of text. A decoded glyph interrupting the
    stretch ends the run: the surviving text is real, so the two sides were set
    with different mappings and must be scored apart.
    """

    runs: list[list[dict]] = []
    current: list[dict] = []
    for char in span.get("chars", ()):
        if _char_position(char) in unmappable:
            current.append(char)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _recover_or_mark_unmappable_span(
    span: dict, unmappable: set[tuple[float, ...]]
) -> None:
    """Recover each unmappable run as offset ASCII where possible, else mark it."""

    font_name = span.get("font") or ""
    for run in _unmappable_runs(span, unmappable):
        recovered: str | None = None
        # One code point per glyph is the shape the offset arithmetic assumes; a
        # multi-character glyph is left to the marking path rather than guessed at.
        if all(len(char["c"]) == 1 for char in run):
            recovered = recover_latin_cid_text(
                [ord(char["c"]) for char in run], font_name
            )
        if recovered is not None and len(recovered) == len(run):
            for char, decoded_char in zip(run, recovered):
                char["c"] = decoded_char
        else:
            for char in run:
                char["c"] = mark_unmappable_cids(char["c"])


def normalize_press_release_paragraph(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    normalized = text
    # Any unmappable glyph opening a list item is a bullet, whatever CID it came
    # from. Enumerating CIDs does not converge: the law-report sample alone emits
    # two (0x83 and 0x7a, an ASCII "z" that no literal class would ever cover).
    #
    # VOL-704 adds two things to the class. U+E000-U+F8FF covers a symbol-font
    # bullet that reached here unmapped -- an unregistered font. U+2022/U+25AA/U+27A2
    # are the real bullet characters `pua_maps` resolves Symbol and Wingdings to, and
    # they need converting for the same reason the CIDs do.
    #
    # 🛑 A codepoint in `pua_maps.KNOWN_UNMAPPABLE` is EXCLUDED, and the exclusion is
    # a decision rather than an oversight. This comment used to name that dict as an
    # intended target of the class, while the dict's own docstring promises the
    # opposite -- that such glyphs "stay in the output and keep being counted ... a
    # glyph we cannot faithfully represent should remain visible as a gap". Both
    # could not be true. The recorded human judgement wins over the positional guess:
    # `KNOWN_UNMAPPABLE` is an explicit per-codepoint finding, this rule is a
    # heuristic, and rewriting such a glyph to "- " would assert we know what it is
    # while deleting the audit's only evidence that we do not. The cost of getting it
    # this way round is a missing list marker; the other way round it is a silently
    # destroyed damage signal, which is the failure mode this corpus keeps paying for.
    #
    # Dormant today -- `KNOWN_UNMAPPABLE` is empty since U+F093 resolved to U+1F668 --
    # so this guards the next entry, which is when it would otherwise have bitten
    # silently. Raised in review of #64.
    #
    # Position is what decides this, not identity, and the split is deliberate: a
    # LEADING bullet is document *structure*, so it becomes "- " -- real Markdown
    # list syntax that a parser sees as a list, which is the point of a corpus
    # meant to be machine-readable. An INLINE bullet is *content* and is left as
    # the literal glyph, because rewriting a mid-sentence bullet as a hyphen would
    # corrupt the sentence. Measured on the CIAA corpus: 2,227 of the 4,210 U+F0B7
    # are leading.
    # The private-use part of this class is U+F020-U+F0FF, i.e. pua_maps.SYMBOL_PUA_RANGE,
    # NOT the whole BMP private-use area. A full \ue000-\uf8ff class also matches
    # kalimati._PUA_REPH (U+F000) and _PUA_IKAR (U+F001), and this rule fires on
    # position: a sentinel that reached the start of a line followed by whitespace was
    # rewritten to "- ", destroying it and disguising the failure as a list item.
    # Verified both ways -- U+F0B7, the bullet this rule exists for and the corpus's most
    # common private-use character at 4,210 occurrences, is inside F020-F0FF; both
    # sentinels are below it. The agreement with SYMBOL_PUA_RANGE is asserted in
    # tests/test_pua_maps.py so the two cannot drift.
    leading = normalized[:1]
    if not (leading and ord(leading) in KNOWN_UNMAPPABLE):
        normalized = re.sub(
            r"^[\ufffd\u2022\u25aa\u27a2\uf020-\uf0ff\U000F0000-\U000FFFFD](?=\s)",
            "-",
            normalized,
        )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s+([।,:;])", r"\1", normalized)
    if re.fullmatch(r"प्रेस\s+विज्ञ\S*", normalized):
        return "प्रेस विज्ञप्ति"
    return normalized


def join_words_with_spacing(words: list[str]) -> str:
    """Reconstruct a line from extracted word tokens."""

    return " ".join(word.strip() for word in words if word.strip())


def join_spans_with_layout(
    spans: list[tuple[float, float, float, float, str]],
) -> str:
    """Reconstruct a line from positioned spans without forcing spaces inside words."""

    if not spans:
        return ""

    parts: list[str] = []
    previous_x1: float | None = None
    for x0, _y0, x1, _y1, text in spans:
        if not text:
            continue
        if (
            previous_x1 is not None
            and x0 - previous_x1 > SPAN_GAP_THRESHOLD
            and parts
            and not parts[-1].endswith((" ", "\t"))
            and not text.startswith((" ", "\t"))
        ):
            parts.append(" ")
        parts.append(text)
        previous_x1 = x1

    return "".join(parts)


def normalize_extracted_word(text: str) -> str:
    """Normalize a single extracted token without touching inter-word spacing."""

    normalized = reorder_devanagari(text)
    normalized = normalize_devanagari_spacing(normalized)
    return normalized.strip()


def _line_key(fragment: TextFragment) -> tuple[int, int, int]:
    return fragment.page_number, fragment.block_number, fragment.line_number


def _private_use_count(text: str) -> int:
    # Plane 15 is included because marked CIDs live there: a glyph the font could
    # not map is damage whether it arrived as a private-use code point from a
    # legacy map or as a CID we marked.
    return sum(
        1
        for char in text
        if 0xE000 <= ord(char) <= 0xF8FF
        or _CID_MARK_BASE <= ord(char) <= _CID_MARK_BASE + _MAX_MARKABLE_CID
    )


def _contains_private_use_marker(text: str) -> bool:
    return _private_use_count(text) > 0


def _duplicate_consonant_count(text: str) -> int:
    """Doubled consonants that morphology does not explain. See the pattern's note.

    Scoped to one Devanagari word at a time because both tests are about where the
    doublet sits inside a word. A doublet can never span a word boundary -- both
    halves are Devanagari, so they are always inside the same maximal run -- which
    makes this exactly the bare pattern's match set, minus the excused ones.
    """
    total = 0
    for word_match in _DEVANAGARI_WORD_PATTERN.finditer(text):
        word = word_match.group(0)
        matches = list(_DUPLICATE_CONSONANT_PATTERN.finditer(word))
        if not matches:
            continue
        # Excuse only the doublets that fall INSIDE a lexeme, not every doublet in a
        # word that happens to contain one. Devanagari compounds carry no internal
        # space, so a garbled token can hold a listed lexeme and unrelated damage at
        # once -- measured: "कक्षा" + "गग" scored 0 when the whole word was
        # excused, hiding damage that scores 1 on its own. Under-charging matters as
        # much as over-charging here, because this term helps decide which legacy map
        # wins.
        lexeme_spans = [
            (m.start(), m.end())
            for lexeme in _DOUBLED_CONSONANT_LEXEMES
            for m in re.finditer(re.escape(lexeme), word)
        ]
        for match in matches:
            if any(
                start <= match.start() and match.end() <= end
                for start, end in lexeme_spans
            ):
                continue
            # The doublet's second consonant opening a known suffix means the pair
            # straddles a morpheme boundary: `\u0915\u094d\u0930\u092e` + `\u092e\u093e`, `...\u0915` + `\u0915\u094b`. It only
            # counts as a boundary if a real stem precedes it -- a single bare
            # consonant is not a Nepali stem, which is what separates the damage
            # `\u0915\u0915\u094b` -> `\u0915\u094b` and `\u092e\u092e\u093e` -> `\u092e\u093e` from `\u092e\u0939\u093e\u0932\u0947\u0916\u093e\u092a\u0930\u0940\u0915\u094d\u0937\u0915\u0915\u094b` and `\u0915\u094d\u0930\u092e\u092e\u093e`.
            stem_len = match.start() + 1
            if stem_len >= 2 and word[match.start() + 1 :].startswith(
                _MORPHEME_SUFFIXES
            ):
                continue
            total += 1
    return total


def _text_quality_penalty(text: str) -> int:
    return (
        text.count("\ufffd") * 12
        + _private_use_count(text) * 12
        + len(_INVALID_SIGN_PATTERN.findall(text)) * 8
        + len(_PREFIX_IKAR_PATTERN.findall(text)) * 6
        + len(_INVALID_IKAR_PATTERN.findall(text)) * 6
        + len(_IMPOSSIBLE_IKAR_NASAL_PATTERN.findall(text)) * _IKAR_NASAL_WEIGHT
        + len(_HALANT_IKAR_PATTERN.findall(text)) * 4
        + _duplicate_consonant_count(text) * _DUPLICATE_CONSONANT_WEIGHT
        + len(_SUSPICIOUS_ARTIFACT_PATTERN.findall(text)) * 8
    )


def _legacy_map_garble(text: str) -> int:
    """Garble measure for RANKING candidate legacy maps -- comparative, not absolute.

    :func:`_text_quality_penalty` with at most
    :data:`_RANKING_DOUBLET_FORGIVENESS` doubled-consonant hits forgiven (VOL-185).

    A doublet is real evidence when two readings of the **same token** are compared, and
    at low counts it is noise when candidate **maps** are compared, for the reason the
    file already gives about `_STRANDED_BRACKET_PATTERN`: adjacency alone does not
    distinguish Nepali morphology from garble, so it charges readings that are correct.
    Where one or two hits move a map decision they can move it the wrong way, because
    the charge lands on whichever reading happens to spell a doublet.

    ⚠️ **Not because "a correct reading of Nepali spells more doublets than a garbled
    one".** That general claim was here and it is false in this population -- and the
    comment at the top of this file already contradicts it (>= 209,998 occurrences of
    genuine doublet damage, 91.6% of it kept by the narrowing). Measured on the narrowed
    count, which is what is actually subtracted: **0 of 536** attested correct forms
    carry an unexplained doublet, and over **1,867** (correct-map, wrong-map) pairs that
    read differently the correct reading carries more in **0** cases while the wrong
    reading carries more in **53**. So the term is usually right and the direction of
    its error is not systematic -- which is exactly why a bounded FORGIVENESS is the
    right shape and dropping the term is not.

    The real ground is narrower and is the paragraph below: on the eight spans VOL-185
    regressed, the charge landed on the CORRECT reading as a false positive of the
    narrowed count, and it was decisive there.

    That is the whole of VOL-185's regression on eight of its eleven documents. Measured
    on each of their `Spins` font aggregates: the correct `Spins` reading carries exactly
    **one** narrowed doublet hit, 3 points, and the map that misreads the span carries
    **0** -- so with `a5cfd4a` having removed the `_INVALID_IKAR_PATTERN` counterweight
    the wrong map wins the `penalty` axis by that margin, and `र्` is emitted as a
    misplaced `ं` (`वर्ष`->`वषं`, `आर्थिक`->`आथिंक`).

    **Forgiving a bounded number, not the whole term.** Dropping the term outright also
    repairs those eight, and it destroys `2649__…घोराही उपमहानगरपालिका`: on its `Hisab`
    aggregate `Preeti`/`Kantipur`/`Sagarmatha` carry **323** doublets to
    `FONTASY_HIMALI_TT`'s **3** -- **960 points** of margin (969 points of charge on the
    wrong maps against 9 on the right one), which is exactly the damage VOL-135 measured
    (>=209,998 occurrences of legacy i-matra loss resurfacing as a doublet). 865 attested
    occurrences are lost outright.

    🛑 **By the ACCEPT GATE, not by a failed tie.** This paragraph used to say "the tie
    fails to localise, the span abstains", and that is the wrong mechanism. Measured on
    the forgive-all arm (which is exactly the parent's code): 2649/`Hisab`'s tie group is
    {`Preeti`(best), `Kantipur`, `Sagarmatha`}, masking DOES localise it, and the span is
    lost because the masked reading fails the absolute ceiling --
    `penalty_per_deva` **0.05146748** against 0.05. Corpus-wide the parent produces **0**
    tie-abstentions in 37,888 candidate pairs, so "the span abstains" cannot be what
    happens. The distinction is load-bearing in the obvious way: a maintainer told the
    tie failed to localise would go and read `_ambiguous_code_points`, where nothing is
    wrong.

    So the floor is what separates the two cases, and it is calibrated rather than
    chosen (`oag-corpus/runs/vol185/calibrate_forgive_5f0833fc.py`, swept over
    N = 0, 1, 2, 3, 5, 10, 25, inf on all 77 documents whose map choice this change can
    move):

    * the correct map on the eleven carries **0 or 1** doublets, never more;
    * 2649's wrong maps carry **323-325** against the right map's 3;
    * every N from 1 to 25 gives 11/11 repaired and **0** abstentions; N=0 repairs only
      the 3 that `attested` decides, and N=inf repairs all 11 and LOSES 2649 -- to the
      gate, as above, not to an abstention.

    **1** is therefore the smallest value that works, and the two populations sit two
    orders of magnitude apart, so nothing here depends on the exact figure.

    🛑 **What the floor deliberately does NOT cover: a one-doublet MARGIN between two
    candidates.** `min(count, N)` forgives the first doublet of EACH candidate; it does
    not neutralise a difference of one between them. So wherever both candidates carry
    doublets and the margin is exactly one, the term still decides -- which is a
    large-document shape, not a corner case. Seven (doc,font) pairs do exactly that
    through the real `detect_content_legacy_fonts`, all `Preeti` -> `Sagarmatha`, with
    `attested`, `ratio` and `devanagari` all moving DOWN in every one:

        2871 · 2880 · 2998 · 3005 · 3008 · 11128/TT3CFt00 · 11334/Nagarik

    On 11334 the deciding doublet is inside the personal name `वेगेन्द्रराज शर्मा`, which
    is correct Nepali, and on 11128 inside a span-concatenation garbage token -- i.e. the
    charge that decides the map lands on non-evidence both times.

    **Accepted as a priced trade rather than fixed, and the price is small against the
    merge target.** Five of the seven (2871, 2880, 2998, 3005, 3008) are **byte-identical
    to `main`** after this change, and the OAG generation tree `8b317ac` already carries
    `N=1` and this exact body, so relative to the published corpus the flip is not a
    change at all. 11334 is the one that moves the wrong way against `main`, which picks
    a third map there (`Kantipur`, alien 84 / 10 letters against this branch's 125 / 28).

    The two fixes that suggest themselves were both rejected on measurement rather than
    taste: a comparative MARGIN BAND ("treat a difference of <= N as a tie") is
    **intransitive**, so it cannot be a sort key -- A ties B, B ties C, A beats C -- and a
    coarse-grained per-block term merely moves the boundary to a different margin.

    Note `ecc5338`'s version of this docstring cited `3544__…Thasang Ga. Pa.` charging
    all six candidates 3 points for `अध्ययन` ("study"). That is no longer true and must
    not be re-copied: `bad7fe2` (VOL-135) added `अध्ययन` to
    :data:`_DOUBLED_CONSONANT_LEXEMES`, so `_duplicate_consonant_count` scores it 0.
    The live examples are damage forms the narrowing keeps -- `खररद`, `ववरण`,
    `आन्तररक` -- each still charged 3.

    **This is used for the ranking axis ONLY, never for the accept gate.**
    `ecc5338` made a version of this subtraction and fed it to both, because
    :func:`_nepali_validity` derived `penalty` and `penalty_per_deva` from one number.
    `_passes_content_legacy_gate` compares one span against an *absolute* ceiling of
    0.05, so lowering that numerator loosens the gate, admits spans that were correctly
    rejected, and cost `3219__…रामधुनी नगरपालिका` 1,723 attested occurrences -- which is
    why VOL-163 reverted it in `677fa95`. The comparative half was never the problem;
    only the substitution's reach was.

    The counter must be the SAME one `_text_quality_penalty` adds -- the narrowed
    `_duplicate_consonant_count`, not `_DUPLICATE_CONSONANT_PATTERN.findall`. The raw
    count is >= the narrowed count on every input, so subtracting it would remove more
    than was ever added and drive this measure below the true penalty, silently.
    """

    forgiven = min(_duplicate_consonant_count(text), _RANKING_DOUBLET_FORGIVENESS)
    return _text_quality_penalty(text) - forgiven * _DUPLICATE_CONSONANT_WEIGHT


def _is_garbled_orphan(text: str) -> bool:
    """True if a fragment with no clean counterpart is clearly legacy-font garble.

    Used only to decide whether to DROP an unpaired fragment during variant
    merging, so it is deliberately conservative: it fires only when the text
    carries the unambiguous mis-map signals (replacement char, private-use
    glyphs, or invalid Devanagari signs) AND those signals are dense relative to
    the Devanagari content. Clean Nepali has zero invalid signs, so this never
    triggers on readable text.
    """
    stripped = text.strip()
    if not stripped:
        return True
    invalid = (
        stripped.count("�")
        + _private_use_count(stripped)
        + len(_INVALID_SIGN_PATTERN.findall(stripped))
    )
    if invalid == 0:
        return False
    devanagari = sum(1 for char in stripped if 0x0900 <= ord(char) <= 0x097F)
    if devanagari == 0:
        return True
    # >=2 invalid signals, or invalid signs making up a meaningful share of the
    # Devanagari characters, marks a fragment as garble rather than a stray typo.
    return invalid >= 2 or invalid / devanagari >= 0.08


def _has_severe_noise(text: str) -> bool:
    """Whether a fragment is noisy enough to be worth a token-wise variant merge.

    \ud83d\uded1 **This is the SECOND consumer of the ikar patterns, and it is not a ranking
    function.** It gates the token-wise merge in :func:`_choose_fragment_text`, which
    runs on the shipped extraction path (:func:`_merge_fragment_variants`) over the
    raw-vs-cmap-repaired document pair. So narrowing any pattern above prices two
    things at once: what the garble measure charges, and whether a repair is attempted
    at all. A narrowing that is right for ranking can silently drop a repair here.

    That is why the impossible ikar+nasal term is read here too rather than only in
    :func:`_text_quality_penalty`. Without it, two variants of one line each carrying a
    single impossible ikar+anusvara in a DIFFERENT token -- original `\u090f\u093f\u0902 \u092c\u0948\u0902\u0915`,
    repaired `\u090f\u0935\u0902 \u092c\u0948\u093f\u0902\u0915`, truth `\u090f\u0935\u0902 \u092c\u0948\u0902\u0915` -- both report no severe noise, the merge is
    skipped, and the fragment ships as `\u090f\u0935\u0902 \u092c\u0948\u093f\u0902\u0915` with the garble intact. With it,
    both sides are noisy, the merge runs, and each token is taken from the side that
    scores lower.

    For a line whose sole marker is a genuinely CORRECT ikar+nasal (`\u0938\u093f\u0902\u0939`, `\u0928\u093f\u0903\u0936\u0941\u0932\u094d\u0915`),
    skipping the merge remains the new and intended behaviour -- that is the 98.3% the
    narrowed class deliberately excuses.
    """

    return any(
        (
            "\ufffd" in text,
            _private_use_count(text) > 0,
            bool(_INVALID_SIGN_PATTERN.search(text)),
            bool(_PREFIX_IKAR_PATTERN.search(text)),
            bool(_INVALID_IKAR_PATTERN.search(text)),
            bool(_IMPOSSIBLE_IKAR_NASAL_PATTERN.search(text)),
            bool(_HALANT_IKAR_PATTERN.search(text)),
        )
    )


def _choose_token_text(original: str, repaired: str) -> str:
    if repaired == original:
        return original

    original_penalty = _text_quality_penalty(original)
    repaired_penalty = _text_quality_penalty(repaired)
    if repaired_penalty < original_penalty:
        return repaired
    if original_penalty < repaired_penalty:
        return original

    return repaired


def _merge_tokenwise(original: str, repaired: str) -> str | None:
    original_tokens = original.split()
    repaired_tokens = repaired.split()
    if len(original_tokens) != len(repaired_tokens):
        return None

    merged_tokens = [
        _choose_token_text(original_token, repaired_token)
        for original_token, repaired_token in zip(original_tokens, repaired_tokens)
    ]
    return " ".join(merged_tokens)


def _choose_fragment_text(original: str, repaired: str | None) -> str:
    if repaired is None or repaired == original:
        return original

    candidates: list[tuple[str, int, int]] = [
        (repaired, 1, len(repaired.strip())),
        (original, 2, len(original.strip())),
    ]
    merged = None
    if _has_severe_noise(original) or _has_severe_noise(repaired):
        merged = _merge_tokenwise(original, repaired)
    if merged and merged not in {original, repaired}:
        candidates.append((merged, 0, len(merged.strip())))

    best_text, _rank, _length = min(
        candidates,
        key=lambda item: (_text_quality_penalty(item[0]), item[1], -item[2]),
    )
    return best_text


def _merge_fragment_variants(
    original_fragments: list[TextFragment],
    repaired_fragments: list[TextFragment],
) -> list[TextFragment]:
    repaired_by_key = {_line_key(fragment): fragment for fragment in repaired_fragments}
    merged: list[TextFragment] = []

    for fragment in original_fragments:
        repaired = repaired_by_key.pop(_line_key(fragment), None)
        if repaired is None and _is_garbled_orphan(fragment.text):
            # An original-only fragment (no repaired counterpart to compare
            # against) that is itself severely garbled is a legacy-font
            # mis-map duplicate of text already captured by another fragment.
            # Keeping it produces the "clean line + garbled tail" artifact, so
            # drop it rather than emitting unreadable Devanagari.
            continue
        merged.append(
            replace(
                fragment,
                text=_choose_fragment_text(
                    fragment.text,
                    repaired.text if repaired is not None else None,
                ),
            )
        )

    merged.extend(
        fragment
        for fragment in repaired_by_key.values()
        if not _is_garbled_orphan(fragment.text)
    )
    return sorted(
        merged,
        key=lambda fragment: (
            fragment.page_number,
            round(fragment.y0, 2),
            fragment.x0,
            fragment.block_number,
            fragment.line_number,
        ),
    )


def _raw_document_from_fragments(
    fragments: list[TextFragment],
    tables: list[Table],
) -> RawDocument:
    paragraphs = [fragment.text for fragment in fragments if fragment.text.strip()]
    return RawDocument(
        paragraphs=paragraphs,
        raw_text="\n\n".join(paragraphs).strip(),
        fragments=fragments,
        tables=merge_continuation_tables(tables),
        # This path has no document handle, so the pages it covered can only be
        # recovered from what it produced.
        page_numbers=sorted(
            {fragment.page_number for fragment in fragments}
            | {region.page_number for table in tables for region in table.regions}
        ),
    )


# --- Part B: content-based (name-agnostic) legacy-font detection ---------------
#
# The font name alone cannot tell a legacy-font span apart when the producer
# mislabels an embedded Preeti glyf as a generic core font ("Helvetica"). We
# detect it from CONTENT: try every legacy map on the font's aggregate text and
# accept a remap only when the output validates as real Nepali. Validation is
# deliberately anchor/dictionary based, NOT Devanagari-ratio based — every
# legacy map emits Devanagari code points from any ASCII, so a high Devanagari
# ratio is a mirage (proven on the CIB decoy layer, which yields ~0.95 ratio yet
# zero real words under all five maps).

_DEVANAGARI_CHAR = re.compile(r"[ऀ-ॿ]")

# Common Nepali admin/legal words, each >= 4 Devanagari code points so they do
# not appear by chance inside garble. A genuine mislabeled-Preeti document hits
# several of these; a wrong-map read of scanned-page junk hits none.
_CONTENT_LEGACY_DICTIONARY: frozenset[str] = frozenset(
    {
        "नेपाल",
        "सरकार",
        "गरेको",
        "गरेका",
        "गरिएको",
        "भएको",
        "अनुसार",
        "अनुसन्धान",
        "कार्यालय",
        "मन्त्रालय",
        "प्रतिवादी",
        "निर्णय",
        "सम्बन्धी",
        "सम्बन्धमा",
        "अदालत",
        "मुद्दा",
        "भ्रष्टाचार",
        "प्रहरी",
        "आयोग",
        "आरोप",
        "दायर",
        "विषय",
        "जिल्ला",
        "काठमाडौं",
        "प्रदेश",
        "कारबाही",
        "बरामद",
        "रहेको",
        "फैसला",
        "विरुद्ध",
        "निजले",
        "रकम",
        "हिनामिना",
        "मिति",
    }
)

# High-frequency Nepali word-forms, used ONLY as the `attested` ranking tie-break in
# :func:`_map_ranking_key` -- never by the accept gate, never by the Latin veto. It is
# deliberately a separate set from :data:`_CONTENT_LEGACY_DICTIONARY`: that one is read
# by `_passes_content_legacy_gate` and `_reads_as_latin_text`, both calibrated against
# their own populations, and adding words to it would loosen the gate and weaken the
# veto at the same time.
#
# Derived by a RULE, not hand-picked, because a hand-list of the forms a bug was
# reported on repairs exactly those forms: Devanagari-only, >= 4 code
# points, and document frequency >= 5,000 of the 6,223 documents of published
# `markdown-quality-v12` -- v12 and not v13, because v13 is the tree under suspicion
# and must not certify its own vocabulary. 536 forms.
# Instruments: `oag-corpus/runs/vol185/derive_attested_5f0833fc.py` and
# `emit_attested_block_5f0833fc.py`.
#
# Garble cannot satisfy this. The forms VOL-185's wrong map produces sit far below the
# floor -- `आथिंक` df 108, `कायंविधि` df 128, `गनुंपनें` df 276, `कमंचारी` df 291, and
# `खचं`/`वषं` df **0** -- and the derivation asserts that, fatally, rather than assuming
# it. ⚠️ This used to say "three orders of magnitude", which the figures beside it
# contradict: against a floor of 5,000 those four are factors of 46x, 39x, 18x and 17x,
# i.e. one to two orders. Stated as the data does instead: at most df 291 against a
# floor of 5,000. `emit_attested_block_5f0833fc.py` generates this sentence, so the
# template is wrong there too and must be corrected with it.
#
# A systematically garbled form is still rare across
# documents, which is exactly what `gate_attested_nepali.py`'s df >= 20 bar is too low
# to see (VOL-175).
_ATTESTED_NEPALI_WORDS: frozenset[str] = frozenset(
    {
        "अख्तियारी",
        "अद्यावधिक",
        "अधिकार",
        "अधिकृत",
        "अध्यक्ष",
        "अनियमित",
        "अनुगमन",
        "अनुगमनका",
        "अनुदान",
        "अनुदानको",
        "अनुमान",
        "अनुरुप",
        "अनुशासन",
        "अनुसार",
        "अनुसारको",
        "अनुसूची",
        "अन्तर्गत",
        "अन्तिम",
        "अन्य",
        "अभिलेख",
        "अभिवृद्धि",
        "अवधि",
        "अवलम्बन",
        "अवस्था",
        "अवस्थामा",
        "असार",
        "असुल",
        "असुली",
        "आएको",
        "आगामी",
        "आधार",
        "आधारका",
        "आधारभुत",
        "आधारमा",
        "आधारित",
        "आन्तरिक",
        "आफ्नो",
        "आम्दानी",
        "आयको",
        "आयोजना",
        "आयोजनाको",
        "आर्थिक",
        "आवश्यक",
        "आश्वस्तता",
        "आषाढ",
        "उक्त",
        "उचित",
        "उत्तरदायित्व",
        "उद्देश्य",
        "उपभोक्ता",
        "उपयुक्त",
        "उपयोग",
        "उपलब्ध",
        "उपलव्ध",
        "उल्लेख",
        "उल्लेखित",
        "एउटै",
        "एकिन",
        "एकीकृत",
        "एण्ड",
        "ऐनको",
        "ऐनमा",
        "कट्टा",
        "कट्टी",
        "कमजोर",
        "करार",
        "करोड",
        "कर्मचारी",
        "कर्मचारीको",
        "कर्मचारीले",
        "कागजात",
        "कानुन",
        "कानून",
        "कामको",
        "काममा",
        "कायम",
        "कारण",
        "कारोबार",
        "कारोबारको",
        "कारोवारको",
        "कार्य",
        "कार्यको",
        "कार्यक्रम",
        "कार्यक्रमको",
        "कार्यक्रममा",
        "कार्यदक्षता",
        "कार्यमा",
        "कार्यरत",
        "कार्यविधि",
        "कार्यसम्पन्न",
        "कार्यसम्पादन",
        "कार्यान्वयन",
        "कार्यान्वयनमा",
        "कार्यालय",
        "कार्यालयका",
        "कार्यालयको",
        "कार्यालयबाट",
        "कार्यालयमा",
        "कार्यालयले",
        "कुनै",
        "कुरामा",
        "कृषि",
        "केही",
        "कैफियत",
        "कोषको",
        "कोषमा",
        "क्रममा",
        "क्षमता",
        "क्षेत्र",
        "क्षेत्रमा",
        "खण्डमा",
        "खरिद",
        "खर्च",
        "खर्चको",
        "खर्चमा",
        "खाता",
        "खातामा",
        "खानेपानी",
        "गएको",
        "गराई",
        "गराउँदा",
        "गराउन",
        "गराउनु",
        "गराउने",
        "गराएको",
        "गरिएका",
        "गरिएको",
        "गरिने",
        "गरेका",
        "गरेको",
        "गरेकोमा",
        "गरेकोले",
        "गरेर",
        "गर्दछ",
        "गर्दा",
        "गर्दै",
        "गर्न",
        "गर्नु",
        "गर्नुपर्दछ",
        "गाउँ",
        "गाउँपालिका",
        "गुणस्तर",
        "गुणस्तरीय",
        "चालु",
        "चित्रण",
        "चौमासिक",
        "छनौट",
        "छलफल",
        "जटिल",
        "जनसहभागिता",
        "जम्मा",
        "जवाफदेहिता",
        "जस्ता",
        "जानकारी",
        "जारी",
        "जिन्सी",
        "जिम्मेवार",
        "जिम्मेवारी",
        "जिल्ला",
        "ठेक्का",
        "ढाँचामा",
        "तयार",
        "तर्जुमा",
        "तर्फ",
        "तसर्थ",
        "तहका",
        "तहको",
        "तहमा",
        "तहले",
        "तालिम",
        "तोकिए",
        "तोकिएको",
        "तोकेको",
        "त्यसैगरी",
        "त्यस्तो",
        "दरबन्दी",
        "दरले",
        "दर्ता",
        "दाखिला",
        "दायित्व",
        "दिएको",
        "दिगो",
        "दिनुपर्दछ",
        "दिने",
        "देखि",
        "देखिएका",
        "देखिएको",
        "देखिएकोले",
        "देखिएन",
        "देखिने",
        "देखिन्छ",
        "देखियो",
        "देहाय",
        "दैनिक",
        "दोस्रो",
        "दोहोरो",
        "धरौटी",
        "धारा",
        "ध्यान",
        "नगदमा",
        "नगरी",
        "नगरेको",
        "नगरेकोले",
        "नदेखिएको",
        "नभएको",
        "नभएकोले",
        "नरहेको",
        "नराखेको",
        "नलिएको",
        "नसकेको",
        "नसारेको",
        "नहुने",
        "नागरिक",
        "नाममा",
        "निकायबाट",
        "निकायले",
        "निकासा",
        "निम्न",
        "निम्नानुसार",
        "नियन्त्रण",
        "नियम",
        "नियमको",
        "नियमानुसार",
        "नियमावली",
        "नियमावलीको",
        "नियमित",
        "नियमितता",
        "निर्णय",
        "निर्धारण",
        "निर्माण",
        "नीति",
        "नेपाल",
        "नेपालको",
        "न्यायिक",
        "न्यून",
        "पत्र",
        "पदपूर्ति",
        "पदाधिकारी",
        "परिचालन",
        "परिमाण",
        "परीक्षण",
        "परेको",
        "पर्दछ",
        "पर्याप्त",
        "पश्चात",
        "पहिचान",
        "पाइएन",
        "पाइयो",
        "पाईएन",
        "पाउने",
        "पारदर्शिता",
        "पारित",
        "पालना",
        "पालिका",
        "पालिकाको",
        "पालिकाले",
        "पूँजीगत",
        "पूर्वाधार",
        "पेश्की",
        "प्रकारका",
        "प्रकृतिका",
        "प्रकृतिको",
        "प्रक्षेपण",
        "प्रगति",
        "प्रचलित",
        "प्रणाली",
        "प्रति",
        "प्रतिवेदन",
        "प्रतिवेदनको",
        "प्रतिवेदनमा",
        "प्रतिशत",
        "प्रत्येक",
        "प्रथम",
        "प्रदान",
        "प्रदेश",
        "प्रभावकारिता",
        "प्रभावकारी",
        "प्रमाण",
        "प्रमाणित",
        "प्रमुख",
        "प्रमुखले",
        "प्रयोग",
        "प्रवाह",
        "प्रशासकीय",
        "प्रशासन",
        "प्रशासनिक",
        "प्रा",
        "प्राप्त",
        "प्राप्ति",
        "प्रारम्भिक",
        "प्राविधिक",
        "फर्छ्यौट",
        "फिर्ता",
        "बजेट",
        "बनाई",
        "बनाउन",
        "बमोजिम",
        "बमोजिमको",
        "बर्ष",
        "बाँकी",
        "बाहेक",
        "बेरुजु",
        "बेरुजू",
        "बैंक",
        "बैठक",
        "भएका",
        "भएको",
        "भएकोमा",
        "भएकोले",
        "भएपछि",
        "भएमा",
        "भत्ता",
        "भन्दा",
        "भन्ने",
        "भरपाई",
        "भित्र",
        "भित्रका",
        "भुक्तानी",
        "भुक्तानीको",
        "भौचर",
        "भौतिक",
        "भ्रमण",
        "मध्ये",
        "मर्मत",
        "महालेखा",
        "महालेखापरीक्षक",
        "महालेखापरीक्षकको",
        "महालेखापरीक्षकबाट",
        "महिना",
        "मात्र",
        "मानदण्ड",
        "मापदण्ड",
        "मार्गदर्शन",
        "मार्फत",
        "मालसामान",
        "मासिक",
        "मितव्ययिता",
        "मिति",
        "मिलान",
        "मूल्य",
        "मूल्याङ्कन",
        "मौज्दात",
        "म्याद",
        "यकिन",
        "यथार्थ",
        "यसरी",
        "यसैसाथ",
        "यस्तो",
        "योगदान",
        "योजना",
        "योजनाको",
        "योजनामा",
        "रकमको",
        "रहेका",
        "रहेको",
        "रहेकोमा",
        "राखी",
        "राखेको",
        "राख्ने",
        "राजश्व",
        "राजस्व",
        "रायमा",
        "रुपमा",
        "लक्ष्य",
        "लगती",
        "लगाउने",
        "लगायत",
        "लगायतका",
        "लगायतको",
        "लागत",
        "लागि",
        "लागु",
        "लागू",
        "लाग्ने",
        "लाभग्राही",
        "लिएको",
        "लिने",
        "लेखा",
        "लेखापरीक्षकको",
        "लेखापरीक्षण",
        "लेखापरीक्षणको",
        "लेखापरीक्षणबाट",
        "लेखापरीक्षणमा",
        "लेखेको",
        "वजेट",
        "वमोजिम",
        "वर्ष",
        "वर्षको",
        "वर्षमा",
        "वापत",
        "वार्षिक",
        "वास्तविक",
        "विकास",
        "विकासका",
        "वितरण",
        "वितरणमुखी",
        "वित्तीय",
        "विद्यालय",
        "विद्यालयको",
        "विधायिकी",
        "विनियोजन",
        "विभिन्न",
        "विवरण",
        "विवरणको",
        "विविध",
        "विशेष",
        "विश्लेषण",
        "विश्वस्त",
        "विषय",
        "विषयः",
        "विषयगत",
        "विषयमा",
        "व्यक्त",
        "व्यक्ति",
        "व्यय",
        "व्ययको",
        "व्यवसायी",
        "व्यवस्था",
        "व्यवस्थापन",
        "व्यवस्थापनमा",
        "व्यवस्थित",
        "व्यहोरा",
        "व्यहोराहरु",
        "शासन",
        "शिक्षक",
        "शिक्षा",
        "शुल्क",
        "शैक्षिक",
        "श्री",
        "संकलन",
        "संख्या",
        "संघीय",
        "संचालन",
        "संचित",
        "संरक्षण",
        "संरचना",
        "संलग्न",
        "संविधान",
        "संविधानको",
        "संस्था",
        "संस्थागत",
        "सकिएन",
        "सकिने",
        "सक्ने",
        "सञ्चालन",
        "सञ्चालित",
        "सञ्चित",
        "सदस्य",
        "समग्र",
        "समयमा",
        "समाप्त",
        "समायोजन",
        "समावेश",
        "समिति",
        "समितिको",
        "समितिबाट",
        "समितिलाई",
        "समितिले",
        "समेत",
        "समेतको",
        "सम्झौता",
        "सम्पत्ति",
        "सम्पत्तिको",
        "सम्पन्न",
        "सम्पादन",
        "सम्पूर्ण",
        "सम्बन्धमा",
        "सम्बन्धित",
        "सम्बन्धी",
        "सम्म",
        "सम्मको",
        "सम्वन्धित",
        "सरकार",
        "सरकारका",
        "सरकारको",
        "सरकारबाट",
        "सरकारले",
        "सरकारी",
        "सवारी",
        "सशर्त",
        "सहयोग",
        "सहायता",
        "सहित",
        "सहितको",
        "साथै",
        "साधन",
        "साधनको",
        "साना",
        "सामाग्री",
        "सामाजिक",
        "सामान",
        "सामानको",
        "सामान्य",
        "सामुदायिक",
        "सार्वजनिक",
        "सीमा",
        "सुझाव",
        "सुदृढ",
        "सुधार",
        "सुनिश्चित",
        "सुरक्षा",
        "सुविधा",
        "सुशासन",
        "सूचना",
        "सेवा",
        "सेवाको",
        "सोको",
        "सोझै",
        "सोधभर्ना",
        "सोही",
        "स्थानीय",
        "स्थापना",
        "स्थायी",
        "स्थिति",
        "स्पष्ट",
        "स्रेस्ता",
        "स्रोत",
        "स्वास्थ्य",
        "स्वीकृत",
        "हजार",
        "हजारमा",
        "हस्तान्तरण",
        "हामी",
        "हामीले",
        "हिसाब",
        "हुँदा",
        "हुनु",
        "हुनुपर्दछ",
        "हुने",
        "हुन्छ",
        "२०६३",
        "२०६४",
        "२०७४",
        "२०७५",
        "२०७६",
        "२०७७",
    }
)

# Token boundaries for `attested`. Counting DISTINCT tokens by set intersection, not
# substrings: a substring test lets one long garbled token satisfy several short list
# entries, and it costs O(list x text) per candidate map on every font of every
# document, where this pass runs the list six times per font.
_ATTESTED_TOKEN_PATTERN = re.compile(r"[^\s|*#>`~\[\]()!;:,.\-_/\\'\"=+]+")


def _attested_word_count(text: str) -> int:
    """How many distinct :data:`_ATTESTED_NEPALI_WORDS` forms ``text`` contains."""

    tokens = {t.strip("।॥") for t in _ATTESTED_TOKEN_PATTERN.findall(text)}
    return len(tokens & _ATTESTED_NEPALI_WORDS)


# Accept gate thresholds. Calibrated so hand-built real Preeti keystrokes pass
# (hits >= 2, penalty-per-Devanagari ~0.0) while CIB decoy text fails under all
# five maps (hits == 0, penalty-per-Devanagari 0.09-0.17).
_CONTENT_LEGACY_MIN_HITS = 2
_CONTENT_LEGACY_MAX_PENALTY_PER_DEVA = 0.05
_CONTENT_LEGACY_MIN_DEVA_RATIO = 0.6
_CONTENT_LEGACY_MIN_DEVA = 8

# Latin-side veto on the content-legacy remap (VOL-138). See
# :func:`_reads_as_latin_text` for what each threshold is worth.
#
# **Every denominator here belongs to a named tip.** VOL-138 calibrated on all 6,236
# OAG corpus documents and recorded 1,245 candidate documents / 469,357 remapped runs
# / 15,808,347 remapped characters at ITS OWN tip. Re-derived at the v13 build tip
# `677fa95` the first two are 1,272 and 473,777 -- about 0.9% higher, because eight
# fixes landed between the two tips. Anyone using 469,357 or 1,245 as a denominator
# at `677fa95` is that much low. Measured three times independently and in agreement:
# VOL-188 run 2bbcb233, VOL-319 run bf632b05, VOL-321 run 37bfbe8a.
#
# The 15,808,347 CHARACTER figure is VOL-138's and has NOT been re-derived at
# `677fa95` -- none of those three runs measured remapped characters. Do not pair it
# with the two figures above as though all three came from one tip.
#
# 473,777 is runs actually remapped, i.e. net of BOTH vetoes: 473,985 candidate-font
# runs less the 194 this veto certifies structurally and the 14 that
# `_reads_as_latin_words` certifies whole (15 spans). A count that subtracts only the
# structural 194 gives 473,791 and is a different quantity -- name which one you mean.
#
# Re-derived again at `b02b1af` (VOL-321's tip, VOL-146 run 7c712e1c): candidate
# documents **1,272** and candidate-font runs **473,985**, both agreeing exactly with
# the figures above, and measured by three independently written instruments in that
# run which agree to the digit. What moved is the structural certification: **228**,
# not 194, and the difference is precisely VOL-321's 34 all-upper runs (194 + 34 =
# 228).
#
# The `_reads_as_latin_words` figure was re-derived too, and it is **15 runs / 15
# spans / 399 characters**, not the 14 runs / 409 characters recorded above. The
# **15 spans reproduces exactly**; the run count is one higher because at this tip
# every spared span sits in its own run (15 distinct (document, page, line), 6
# documents); and no character matcher reproduces 409 -- stripped gives 399 and
# unstripped 412. Quote 15/15 at this tip and name the matcher for the characters.
#
# So net of both vetoes it is **473,742** at floor 16 and **473,726** at floor 13,
# where `677fa95` read 473,777. ⚠️ That subtraction treats a run with any spared span
# as un-remapped, which **over-subtracts** for any run whose spared span is not the
# whole run -- the rest of such a run still decodes. Three of the 15 spared spans are
# not their line's first span, so the true figure is between these and 473,985 - 228.
# The published 473,777 has the same wrinkle and does not mention it.
#
# THE MIXED-CASE FLOOR: 13 (VOL-146). Every mixed-case run is judged by this one;
# `_LATIN_VETO_MIN_CHARS_UPPER` below relaxes it for all-upper runs only.
#
# Calibrated on a READ CENSUS, not a sample: every run this floor ALONE rejected at
# length >= 10 in all 6,236 documents -- 91 runs, each read blind, 41 keystrokes and
# 50 genuine Latin. Priced at `b02b1af` through this module's own
# `_reads_as_latin_text` rather than a re-implementation of it
# (`runs/vol146-floor13-7c712e1c/floor-admission-7c712e1c.json`):
#
#     floor   admitted   LATIN   KEYSTROKE   precision
#        16          0       0           0         n/a
#        15          4       4           0       1.000
#        14          9       9           0       1.000
#        13         14      14           0       1.000
#        12         24      19           5       0.792
#        11         33      24           9       0.727
#        10         91      50          41       0.549
#
# 13 is the last floor that admits nothing but Latin, and 12 is the first that
# abandons real Nepali -- `lakb Aoa:yfkg` (बिपद ब्यबस्थापन), `Zofd afa' ofba`
# (श्याम बाबु यादब). That is why the step stops here and not at 10, where more than
# two of every five admissions would be a Nepali run published as ASCII garbage.
#
# Shipped effect, measured pairwise on two built trees at this tip and NOT inferred
# from the veto's own flags: **14 runs / 11 documents / 212 characters** recovered, 0
# documents regressed, 6/6 controls byte-identical, gate directional (exits 0/0/1).
# The corpus screen counts **16** runs / 12 documents, and the two figures are both
# right: the extra two are runs `_reads_as_latin_words` already spares per span on the
# write path, so they are certified-set gains with no transcript effect. A
# certified-set count is therefore an UPPER BOUND on the transcript effect, not equal
# to it -- name which one you mean.
_LATIN_VETO_MIN_CHARS = 13  # non-space characters, not raw length -- see below
# The floor for runs whose ASCII letters are ALL upper case (VOL-319, VOL-321).
# An acronym carries no function word and is 2-4 characters long, so neither veto
# could reach the class: `_reads_as_latin_words` declines at function-word share
# 0.0 against its 0.1 floor, and the floor above rejects before any other gate is
# consulted. Calibrated on a READ CENSUS of the whole all-upper floor-only
# population at the v13 tip -- not a sample -- where the entire false-positive set
# is length 2 (`OF` x5, `OG` x2, both Preeti fragments) and the entire noise class
# is length 1 (136 runs: `O` 92, `A` 33, `E` 10, `I` 1), so a single length cut at
# 3 separates the 34 read-verified Latin runs from both with nothing left over:
# precision 1.000, recall 1.000 over 34 runs / 16 documents / 103 characters.
# The conjunction with all-upper is load-bearing, not decoration: a bare move of
# the constant to 3 is not case-aware and admits 2,306 runs in 919 documents to
# reach the same 34, and 98 of those admissions are provably wrong on evidence
# independent of this veto. Residual risk, stated: an all-caps legacy fragment of
# length >= 3, of which there are 0 in 6,236 documents but which nothing in the
# mechanism forbids.
_LATIN_VETO_MIN_CHARS_UPPER = 3
_LATIN_VETO_MIN_ALPHA_RATIO = 0.88
_LATIN_VETO_MIN_VOWEL_RATIO = 0.30
# ASCII a legacy 8-bit Devanagari layout uses as glyph codes and English does not
# use inside running text. Their presence is a sufficient condition for keystrokes.
_LEGACY_KEYSTROKE_SYMBOLS = frozenset("][{}|~^@+_=")
_ASCII_VOWELS = frozenset("aeiouAEIOU")
_MEDIAL_CAPS = re.compile(r"[a-z][A-Z]")

# The THIRD Latin-side veto (VOL-180, calibrated in `runs/vol180/` on all 6,236 OAG
# corpus documents). It exists because the two above are both decided on the run's
# *own* text, and the residue they leave is runs that are nothing but a bare
# acronym -- `QOC` (3 chars), `ECOD ` (5 chars) -- which carry no in-run context to
# be judged on. The evidence has to come from outside the run, at document scope:
# an acronym that is genuine Latin here almost always also appears somewhere in
# this document in text the remap never rewrites.
#
# Calibration, from `runs/vol180/strict-calibration-635286f0.json`. 🛑 **Three numbers,
# two tokenizers, and they must not be chained as though they were one funnel** -- an
# earlier form of this comment wrote "cuts 7,864 to 16 fires", which mixes them:
#
#   7,864  `considered` -- runs holding a short all-caps ASCII token under the LOOSE
#          tokenizer, i.e. the candidate population before the strict shape test;
#     386  `shape_ok` -- the same population under the STRICT whitespace-delimited
#          tokenizer this module ships;
#      16  fires -- what survives requiring document-scope survivor evidence, 16/16
#          genuine English, 0 Nepali touched, every one read individually.
#
# Vetoing on shape alone would be 41x the whole of `27d74f0` -- a licence to stop
# decoding wherever two capitals appear -- so the shape is only the candidate generator
# and the survivor condition is what makes the axis usable.
#
# ⚠️ The 16 is VOL-180's own calibration figure and it is NOT the current fire count:
# later commits in this chain re-measured the axis at **26 fires**, which the purity
# clause takes to **25** (see `detect_latin_acronym_survivors`). So 7,864 / 386 / 16 is
# one snapshot, taken together, and quoting the 16 beside a later fire count chains two
# instruments. `tests/test_content_legacy_acronym_veto.py` carries the same split.
#
# Three things the calibration forces that the issue's sketch did not say:
#
#   1. `>= 2 uppercase LETTERS`, not "letters or digits". `36L` is घटी -- a real
#      whitespace-delimited all-caps ASCII keystroke word holding one letter.
#   2. `_ACRONYM_FORBIDDEN` must never be stripped as edge punctuation. The
#      assertion below keeps the two sets disjoint so that cannot regress.
#
#      §8 states this condition as if it were independent, and it is NOT: measured
#      here 2026-08-13, **every character in `_ACRONYM_FORBIDDEN` is already
#      excluded by the "ASCII uppercase or digit" condition below, so the membership
#      test can never fire.** It is kept because it is the spec's own wording and
#      because it becomes load-bearing the moment that shape condition is relaxed --
#      but do not read it as what stops the spurious class.
#
#      What actually stops the 21 spurious fires is (a) **whitespace delimitation**
#      and (b) note 1. Of the seven fragment shapes the loose tokenizer produced,
#      `G6L`, `OG` and `PG6` PASS the strict shape test and are excluded only by
#      being parts of a whitespace-delimited keystroke word; `6L`, `G6`, `G5` and
#      `36L` are excluded by note 1's two-letter floor. Weaken either and the class
#      returns -- weakening the forbidden set changes nothing.
#   3. The survivor vocabulary must be built with this same strict tokenizer. Built
#      loosely, `6L` attests itself from undecoded keystrokes elsewhere in the
#      document (`w/f}6L` = धरौटी split at `}`), and survival is only evidence of
#      Latin if the surviving occurrence is itself Latin-shaped.
#   4. ⚠️ The audit of section 8's conditions is COMPLETE only with this: alongside
#      `_ACRONYM_FORBIDDEN`, the LOWER half of the length check is also inert.
#      `_ACRONYM_MIN_LEN <= len(token)` can never reject a token
#      `_ACRONYM_MIN_UPPER` accepts, because a token shorter than 2 characters cannot
#      hold 2 uppercase letters. Ablation over 111,151 enumerated inputs: dropping the
#      lower bound to 1, or to 0, changes **0** outcomes. It is kept as the spec's own
#      wording, not as an exclusion it performs -- so a derivation reading "a single
#      upper-case letter is an initial or a list label, not an acronym" is describing
#      what MIN_UPPER does.
_ACRONYM_EDGE = ',.;:()“”‘’"?!'  # punctuation English puts against a word
_ACRONYM_FORBIDDEN = frozenset("][{}|~^@+_=\\/'&*<>%$#«»")
_ACRONYM_MIN_LEN = 2
_ACRONYM_MAX_LEN = 5
_ACRONYM_MIN_UPPER = 2
# Note 2, made executable: if these ever intersect, stripping an edge could remove
# a forbidden character and let a keystroke fragment qualify.
assert not (_ACRONYM_FORBIDDEN & frozenset(_ACRONYM_EDGE))


def _acronym_shaped(char: str) -> bool:
    """One character of a qualifying acronym token: ASCII uppercase or an ASCII digit.

    Named rather than inlined so the claim that `_ACRONYM_FORBIDDEN` is SUBSUMED by the
    shape test can be asserted against the production predicate instead of a copy of it.
    A test restating `("A" <= c <= "Z") or ("0" <= c <= "9")` as a literal is a property
    of two constants and stays green when this relaxes -- and asserting it through
    `_acronym_tokens` does not work either, because the forbidden-set check runs FIRST
    and would swallow the relaxation. See
    `test_the_forbidden_set_is_subsumed_by_the_shape_test`.
    """

    return ("A" <= char <= "Z") or ("0" <= char <= "9")


def _acronym_tokens(text: str) -> frozenset[str]:
    """The qualifying acronym-shaped tokens of ``text`` (VOL-180 §8).

    Whitespace-delimited, because the defect this replaces came from tokenizing on
    a punctuation class: `[A-Za-z0-9/&().,:;+\\-]+` splits `w/f}6L` at `}` and
    hands back `6L` as though it were a word.

    🛑 Unmarks first, and inside the function so both callers inherit it -- the same
    rule `_reads_as_latin_words` and `_reads_as_latin_text` follow. Every caller reads
    spans from `get_cid_marked_page_dict`, so a run whose glyphs failed to decode
    arrives CID-marked, and a marked codepoint is in plane 15: it fails the
    `"A" <= char <= "Z"` and `"0" <= char <= "9"` tests below, so NO token qualifies.
    Measured: `The OAG and CIAA reports` yields `{OAG, CIAA}` plain and `{}` marked.

    That silently emptied the survivor vocabulary, which disables this whole third
    axis for marked spans -- and it disables it in the direction that vetoes LESS, so
    the failure is more remapping of genuine Latin, not less. Raised in review.
    """

    text = unmark_cids(text)
    tokens: set[str] = set()
    for raw in text.split():
        token = raw.strip(_ACRONYM_EDGE)
        if not _ACRONYM_MIN_LEN <= len(token) <= _ACRONYM_MAX_LEN:
            continue
        if any(char in _ACRONYM_FORBIDDEN for char in token):
            continue
        if not all(_acronym_shaped(char) for char in token):
            continue
        if sum(1 for char in token if "A" <= char <= "Z") < _ACRONYM_MIN_UPPER:
            continue
        tokens.add(token)
    return frozenset(tokens)


# VOL-212: SURVIVOR PURITY, the narrowing note 3 above does not achieve.
#
# Note 3 makes the survivor vocabulary Latin-*shaped*. `PG6L` is Latin-shaped --
# whitespace-delimited, 4 characters, 3 uppercase ASCII letters -- and is a Preeti
# keystroke word for `एन्टी` ("anti"). On `11129__n30-Annual Report 2071.pdf` page 265
# its whole survivor vocabulary is `OG6 P06L PG6L`, three keystroke fragments and no
# English at all, and the resulting fire kept a 91-character run of correct Nepali as
# raw ASCII -- a regression against published v12 and v13, which both decode it.
# Found by VOL-197's corpus-scale A/B (`runs/vol197/FINDING-corpus-ab-f7071d15.md`).
#
# So survival is evidence of English only if the surviving token is ALSO not itself
# plausibly legacy text. Stated directly: decode the token with the document's own
# candidate map and ask whether the result is well-formed Devanagari.
#
# **No existing measure can stand in for this.** Measured over the 18 labelled decodes
# in `runs/vol212/purity-probe-a3f21c8e.json`, `_text_quality_penalty` is **0** and the
# `_CONTENT_LEGACY_DICTIONARY` hit count is **0** for every one of them, `एन्टी`
# included -- the penalty charges garble artifacts and `एन्टी` has none, and the
# dictionary holds no transliterated loanword. A threshold on either reads the same for
# the tokens that must be dropped and the tokens that must be kept.
#
# The predicate is ONE-SIDED and its direction matters: calling a decode well-formed
# only ever *removes* a token from the vocabulary, and fires are monotone in the
# vocabulary, so a false "well-formed" costs a genuine recovery and a false "malformed"
# lets the defect back. Six malformedness conditions, with the calibration's own
# attribution over the 8 tokens carrying the 25 genuine fires (`runs/vol212/`):
#
#   C3 halant-final           -- carries IEE, DPR, GI, HDPE, MIS, NS, ECOD (7 of 8)
#   C4 non-initial ind. vowel -- carries DPR, HDPE, ECOD, and QOC ALONE
#   C1 non-Devanagari char    -- carries MIS, redundant with C3
#   C2 initial combining mark -- carries nothing here
#   C5 vowel sign after halant-- carries nothing here
#   C6 two vowel signs in a row- carries nothing here
#
# **`{C3, C4}` is the minimal sufficient set and C4 cannot be dropped: it is the only
# condition keeping `QOC`, worth 2 of the 25 fires.** C1/C2/C5/C6 are kept because each
# is an independent rule of Devanagari orthography and each recovers genuine English
# evidence the two load-bearing ones discard -- distinct bystander tokens dropped falls
# from 23 distinct (28 token-map rows) to 16 distinct (19 rows) with them in.
# ⚠️ Written as '28 to 16' this mixed two units -- 28 is a row count and 16 is a
# distinct-token count -- which overstates what the four conditions buy. The
# like-for-like comparison is 23 -> 16 distinct, or 28 -> 19 rows. Those 16 are the
# measured recall cost of this filter and this comment is the only place a reader
# will look for it. None of the six fires on `एन्टी`, `एण्टी` or `इन्ट`, so
# they cannot re-admit 11129's vocabulary. Pinned by tests, per note 2's precedent.
_DEVA_HALANT = "्"
# Independent vowel LETTERS. Nepali writes a non-initial vowel as a matra, so one of
# these away from the start of a word is a positive tell of a wrong byte map.
_DEVA_INDEPENDENT_VOWEL = re.compile("[अ-औॠॡ]")
# Vowel signs (matras), including the vocalic-L pair and the four the maps emit that an
# `ा-ौ` range misses. Measured by inverting all seven maps over 0x00-0x2FFF: U+093A OE,
# U+093B OOE, U+094E PRISHTHAMATRA E and U+094F AW are all reachable, so a pair involving
# one of them was invisible to the matra-pair condition. (U+0955/0956/0957 are NOT
# emitted by any map and are left out.)
_DEVA_VOWEL_SIGN = re.compile("[\u093a\u093b\u093e-\u094c\u094e\u094f\u0962\u0963]")
# Every combining mark ANY CANDIDATE MAP CAN EMIT -- measured, not "every combining mark
# in the block", which is what this comment used to claim. A word cannot begin with one.
#
# ⚠️ The previous class omitted U+094E and U+094F, and both are emitted, so a decode
# beginning with one was NOT flagged malformed. That is the costly direction: a false
# "well-formed" disqualifies a survivor token and removes a veto (see
# `_is_wellformed_devanagari`).
_DEVA_COMBINING = re.compile("[\u0900-\u0903\u093a-\u094f\u0951-\u0957\u0962\u0963]")
_DEVA_ANY = re.compile("[ऀ-ॿ]")


def _is_wellformed_devanagari(text: str) -> bool:
    """True if ``text`` could be a Devanagari word (VOL-212).

    Generous by design -- see the six conditions above.

    🛑 **The safe error is to answer FALSE**, and this docstring said True. Answering True
    means "this decodes as Nepali", which DISQUALIFIES the survivor token, shrinks the
    vocabulary and removes a veto -- the fail-open direction, where genuine English gets
    remapped into Devanagari that spells nothing. The block comment above these
    conditions has it right ("a false 'well-formed' costs a genuine recovery"), as does
    `runs/vol212/FINDING-discriminator-a3f21c8e.md` ("a false 'well-formed' costs
    recall"). Read "generous" the same way: generous about calling a decode well-formed
    is the COSTLY direction, not the cautious one.

    ⚠️ **This predicate is not the CAUSAL discriminator, and the direction of its error
    is disclosed rather than fixed.** It separates 11129's keystroke tokens from real
    acronyms only because their final ASCII character happens to map to a glyph that is
    not halant-final -- a property independent of whether the token is English. So it
    also removes genuine English acronyms from the vocabulary: 9.3% of the real survivor
    set, which is the fail-open direction.

    The causal property is font provenance: 11129's tokens survive ONLY in spans whose
    font is NAME-mapped to a legacy map, i.e. keystrokes the name-based path decodes and
    the content-legacy remap never rewrites. A rule counting a survivor token only when
    at least one surviving occurrence sits in a span whose font is not name-mapped
    (`legacy_maps.get_converter(_span_base_font(font)) is None`) separates the same
    labelled set at ZERO bystander cost. It is candidate 2 in
    `runs/vol212/FINDING-discriminator-a3f21c8e.md`, recorded there as "not needed", and
    it is worth measuring corpus-wide before this axis ships more widely. Not swapped in
    here because the acceptance holds as shipped and the swap is its own change with its
    own corpus sweep to run.
    """

    if not text:
        return False
    if any(not (_DEVA_ANY.match(char) or char == " ") for char in text):
        return False  # C1
    if _DEVA_COMBINING.match(text[0]):
        return False  # C2
    if text.endswith(_DEVA_HALANT):
        return False  # C3
    if any(match.start() > 0 for match in _DEVA_INDEPENDENT_VOWEL.finditer(text)):
        return False  # C4
    if re.search(_DEVA_HALANT + _DEVA_VOWEL_SIGN.pattern, text):
        return False  # C5
    if re.search(_DEVA_VOWEL_SIGN.pattern + "{2}", text):
        return False  # C6
    return True


def _decodes_as_legacy_devanagari(
    token: str,
    content_legacy_maps: dict[str, LegacyMapChoice],
) -> bool:
    """True if any of this document's candidate maps reads ``token`` as Nepali.

    ``any``, not ``all``: a token that one candidate map turns into a Devanagari word
    is not usable as evidence of English, whatever the other maps make of it. That is
    the conservative side for the defect this closes.
    """

    seen: set[str] = set()
    for choice in content_legacy_maps.values():
        if choice.map_key is None or choice.map_key in seen:
            continue
        seen.add(choice.map_key)
        if _is_wellformed_devanagari(get_converter_for_map(choice.map_key)(token)):
            return True
    return False


# The SECOND, independent Latin-side veto (VOL-146, VOL-163). Both this one and
# :func:`_reads_as_latin_text` are ONE-SIDED -- each only ever declines to remap --
# so they compose as a disjunction rather than competing, and v13 carries both.
# They certify Latin on different evidence at different granularities, and each
# one's blind spot is the other's strength: the structural test reaches a bare
# technical noun phrase (`Quality Of Care, QOC`) carrying no function word, and the
# word test reaches prose whose letter statistics look like short keystroke words.
# Containment measured corpus-wide over one shared label set: runs/vol163/.
# Latin-side veto for the content-legacy pass. `detect_content_legacy_fonts`
# decides candidacy per font NAME over that font's whole-document aggregate, then
# every span of that font is remapped -- so genuine English set in the same face
# as the keystrokes is destroyed too. On one OAG report that cost 1,362 characters
# of an English appendix (VOL-126, VOL-134). The candidacy decision is correct and
# stays: `hits >= 2` over the aggregate is what excludes the digit companions
# (`Spins_EXT`, `TT33At00`), so it cannot be moved to a finer unit or relaxed.
# The veto therefore reads the RAW ASCII, before the decode, and only ever
# *declines* to remap.
#
# It must read the raw text because no post-decode axis can help: a character map
# turns ASCII letters into Devanagari letters whether or not the input was Nepali,
# so genuine English decodes to penalty 0 and ratio ~1.0 exactly like real
# keystrokes (VOL-134 §3).
#
# **Why a word list and not a structural measure.** Measured over all 469,357
# same-font runs in the 6,236-document OAG corpus, no structural axis reaches a
# usable operating point: `alpha_ratio`, `vowel_ratio`, ratio-of-legacy-punctuation
# and a conjunction of them all fire on short keystroke words, which are pure
# letters containing vowels and so structurally indistinguishable from short
# English words -- `ljifo` (विषय), `JolQmut` (व्यक्तिगत), `cWoIf` (अध्यक्ष). The
# conjunction read 17% precise. Word identity is the only signal that separates
# them.
#
# **Why three letters or more.** The obvious list -- English function words -- is
# unusable with its two-letter entries in: they are the commonest Preeti digraphs.
# `If]q` (क्षेत्र, ubiquitous in audit prose) tokenises to `If` -> "if"; `of]`
# (यो/या) -> "of"; `On]S6«f]lgs` -> "on"; `To:tf]` -> "to". Over the 33,112 runs
# that provably decode to Nepali (>= 2 dictionary words), `of` occurs in 12.4% and
# `if` in 6.2%, while not one word of three letters or more occurs at all.
#
# This is English grammar, not a corpus frequency table, so it is a legitimate
# constant for a general library. Closed, and short enough to audit by eye.
_LATIN_VETO_WORDS: frozenset[str] = frozenset(
    """also and are been but can for from has have into may not should such than
    that the their then there these this those when which who will with""".split()
)

# Share of a span's multi-letter tokens that must be one of the above. A *share*,
# not a count: a long keystroke run accumulates accidental collisions (`/l;but`
# contains "but", `can` and `aNd` occur as tokens), and normalising by length
# dilutes them while genuine English prose keeps a high function-word density.
# At this cut the veto fires on 93 runs corpus-wide, of which 93 are genuine Latin
# when every one is read (VOL-138).
_LATIN_VETO_MIN_SHARE = 0.1

# Tokens are only counted when their casing is one English actually uses. `aNd` is
# a Preeti keystroke sequence, not a word: a legacy layout puts shifted glyphs
# mid-word, so mixed case is a keystroke signature. This guard is what removes the
# single false positive the share cut leaves.
_LATIN_VETO_TOKEN = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")


def _reads_as_latin_words(text: str) -> bool:
    """True if ``text`` is genuine Latin prose rather than legacy keystrokes.

    One-sided by construction: it certifies Latin, never keystrokes, so a span it
    declines is decoded exactly as before. See ``_LATIN_VETO_WORDS`` for why the
    test is word identity at a three-letter minimum rather than any structural
    measure of the raw ASCII.

    ⚠️ **It has NO minimum-length condition, unlike :func:`_reads_as_latin_text`, and
    the share it applies was calibrated on a different unit.** Both are true and both
    are deliberate, so they are recorded here rather than left to be rediscovered:
    ``_LATIN_VETO_MIN_SHARE`` was measured over whole same-font RUNS (469,357 of them)
    and this predicate applies it to a single SPAN, where a short span with one
    accidental hit can clear 0.10 on arithmetic alone.

    Measured at the real production unit before deciding to leave it: two disjoint
    deterministic samples covering 2,183 of the 6,236 OAG PDFs, of which 432 carry a
    content-legacy candidate font, giving **224,825 candidate-font spans**. This veto
    fired **7 times, and all 7 are genuine Latin** (bill-of-quantity lines plus
    "source not found."). **Zero Nepali spans abandoned.** 65.0% of candidate-font
    spans are indeed under 16 non-space characters, so the length premise holds -- what
    does not is the collision premise: only 7 of 146,752 spans carry any
    ``_LATIN_VETO_WORDS`` token at all. The real corpus instance of the worst case
    (`can` as the land grade अबल) has 13 multi-letter tokens, share 0.077, so the veto
    declines and it decodes correctly. Corroborated from the output side: v16 was built
    with this veto and holds 525 files containing अबल against zero containing the raw
    keystrokes.

    🛑 **Two length-floor fixes were tried and both are measurably worse.** A 16-non-space
    floor fails this module's own ``test_reads_as_latin_words_requires_english_casing``
    ("And the report", 12 non-space) and destroys 2 of the 7 real true positives
    ("source not found." at 15, "of the Engineer. " at 14) while fixing 0 measured false
    positives. Evaluating over the maximal same-font run instead of the span still fires
    (share 0.222 -- `can` repeats). The failure direction is fail-CLOSED, i.e. a missed
    remap rather than corrupted English, which is the recoverable side.
    """

    # Unmark first. A marked CID is chr(_CID_MARK_BASE + ord(char)), so `isascii()` is
    # False for it and the token pattern below matches nothing -- this predicate returned
    # False for a span of plain English purely because its glyphs had failed to decode.
    # That is the case the veto needs: a PARTIALLY marked span of genuine Latin would
    # otherwise have its unmarked half remapped into well-formed Devanagari that spells
    # nothing. For a FULLY marked span the veto changes no bytes -- every plane-15 code
    # point passes the converter untouched, so remap and no-remap are byte-identical, and
    # the marks are exactly what `count_marked_cids` and `_private_use_count` notice. The
    # damaging, reachable shape is the partial one, which
    # `test_a_partially_marked_latin_span_is_returned_unchanged` covers.
    #
    # Done here rather than at the call site so every caller inherits it.
    text = unmark_cids(text)
    tokens = _LATIN_VETO_TOKEN.findall(text)
    multi_letter = [token for token in tokens if len(token) > 1]
    if not multi_letter:
        return False
    hits = sum(
        1
        for token in tokens
        if token.lower() in _LATIN_VETO_WORDS
        and (token.islower() or token.istitle() or token.isupper())
    )
    return hits / len(multi_letter) >= _LATIN_VETO_MIN_SHARE


def _span_base_font(font_name: str) -> str:
    """Base font name with any subset prefix stripped (matches _convert_span_text)."""

    return font_name.split("+", 1)[-1] if "+" in font_name else font_name


def _is_probably_legacy_ascii(text: str) -> bool:
    """True if ``text`` looks like raw legacy-font keystrokes (ASCII, no Devanagari)."""

    stripped = text.strip()
    if len(stripped) < _CONTENT_LEGACY_MIN_DEVA:
        return False
    if _DEVANAGARI_CHAR.search(stripped):
        return False
    printable_ascii = sum(1 for char in stripped if 0x20 <= ord(char) < 0x7F)
    return printable_ascii / len(stripped) >= 0.8


def _reads_as_latin_text(text: str, decoded: str) -> bool:
    """True if this run's raw ASCII is genuine Latin text, not legacy keystrokes.

    A legacy 8-bit face reaches the remap per **font**, decided on that font's whole
    aggregate (:func:`detect_content_legacy_fonts`). Any genuine Latin set in the
    same face is remapped with it, rewriting real words as Devanagari that spells
    nothing: OAG's 2077 performance audit report renders ``QOC`` as ``त्तइऋ`` and
    loses 1,362 characters of an English appendix that way (VOL-126). This is the
    per-run veto that keeps such a run as it was, leaving the font's candidacy
    decision untouched.

    ``decoded`` is the run's text under the font's chosen map, used only for the
    dictionary axis below.

    **Why the veto reads raw ASCII and not the decode.** A character map turns ASCII
    letters into Devanagari letters whatever the input language was, so genuine
    English decodes to ``penalty 0``, ``ratio 1.0`` — indistinguishable from real
    keystrokes on every purity axis (VOL-138). The evidence has to be read before
    the substitution.

    Calibrated over all 6,236 corpus documents against a lexicon built from the
    4,991 that carry no candidate legacy font at all, so the label cannot have been
    contaminated by this pass. At these thresholds the veto fires on **190 of the
    469,357 remapped runs at VOL-138's tip** — **194 of 473,777** re-derived at the
    v13 tip ``677fa95``, three times independently: 185 are labelled genuine Latin
    (7,067 characters
    recovered) and the other 5 were read individually and are **also** genuine Latin
    — ``(prophylactic antibiotics)``, ``theatre personnel)``, ``charities such as
    Lifebox)``, and two personal names — which the label missed only because it
    wants two known words and a name supplies none. **No Nepali run in the corpus
    is vetoed.** The ``alpha_ratio``-only form first proposed fires on 13,429 runs
    for the same 273, i.e. ~2% precision.

    Each condition, and what dropping it costs — measured, ``runs/vol138/ablation.json``:

    ``at least _LATIN_VETO_MIN_CHARS non-space characters``
        The load-bearing condition: without it the veto fires on 3,692 Nepali runs
        instead of 0. It counts **non-space** characters for a reason found by
        reading — ``alpha_ratio`` is itself computed over non-space characters, so a
        *raw-length* floor is cleared by padding, and 23 runs of ~75 spaces followed
        by ``gfo`` did exactly that during calibration.

        ``_LATIN_VETO_MIN_CHARS_UPPER`` relaxes **only this** condition, and only
        for runs whose ASCII letters are all upper case, because that floor is what
        rejected every acronym in the corpus before any other gate was consulted
        (VOL-188, VOL-319, VOL-321). Every other condition below still applies to
        an all-upper run: ``MIS``, ``PAN``, ``QOC`` clear the vowel ratio at 1/3
        against 0.30, and a legacy keystroke symbol or a dictionary hit still
        declines them. The 3,692 figure above is VOL-138's *labelled-Nepali* count
        at its own calibration tip, and is **not** the same quantity as the 3,755
        floor-only runs measured at the v13 tip — do not quote one as the other.
    ``vowel_ratio``
        Share of ASCII letters that are vowels, and **the axis that separates these
        two populations when no earlier one could**. Symbol-free Preeti keystrokes
        are all-letter strings — ``ljBfno ejg``, ``:yfgLo tx`` — so any letter-share
        axis is blind to them by construction. The layout's frequent codes are
        consonants (``f`` = ा, ``g`` = न, ``l`` = ि), so keystroke runs carry 10-25%
        vowels where English carries 35-40%. Dropping it: 126 Nepali runs vetoed.
    ``alpha_ratio``
        Share of non-space characters that are ASCII letters; keeps out the digit
        and punctuation companions. Dropping it: 79 Nepali runs vetoed.
    ``no medial capital``
        ``ljBfno``, ``cGo``: an 8-bit layout uses shifted keys for distinct glyphs,
        so capitals land mid-token; English does this only in CamelCase. Dropping
        it: 12 Nepali runs vetoed, for 2 more Latin runs saved.
    ``no legacy keystroke symbol``
        ``][{}|~^@+_=`` are glyph codes for common Devanagari marks and English does
        not use them in running text, so their presence is *sufficient* for
        keystrokes — 74.8% of remapped runs and 94.8% of remapped characters carry
        one. At these thresholds the other conditions already exclude almost all of
        them and this removes **1** further run. Kept because it is the one
        condition that is sufficient on its own rather than calibrated.
    ``zero Nepali dictionary hits in the decode``
        **Contributes nothing at these thresholds** — the numbers are identical with
        and without it — and it is kept deliberately, so the record should say so.
        It cannot cause a miss: ``hits == 0`` holds for **100%** of the labelled
        Latin population, measured. And it is the conjunction's only Nepali-*lexical*
        evidence, where every other condition is a character-class rate: keystrokes
        decode into real Nepali words, English decodes into Devanagari that spells
        nothing. That guards the failure this veto must not have — silently
        abandoning a Nepali run — on documents shaped unlike this corpus's.
        This is **not** the ``hits >= 2`` axis of
        :func:`_passes_content_legacy_gate`. That decides a **font's** candidacy on
        its whole aggregate and is untouched here (VOL-77, VOL-89); this asks one run
        of an already-confirmed legacy font whether it, specifically, is Nepali.

    What it deliberately does **not** save: English lines dense in numerals, which
    is what most of the residue is — ``110mmɸ uPVC Bend-45˚``, ``1/2"GI Nipple 9"
    Long``, ``40-4kg/sqcm series iii(280mm)``. They fail ``alpha_ratio``. Lowering
    it to reach them costs Nepali faster than it recovers Latin, and an undecoded
    bill-of-quantities line is legible where wrong Devanagari is not.
    """

    # Unmark first, for the same reason `_reads_as_latin_words` does and inside the
    # predicate for the same reason -- so every caller inherits it. That sibling's
    # comment claims the principle; this function did not follow it, which made the
    # claim half true. Measured on one sentence of English: `True` plain, **`False`
    # marked**, so a marked run of genuine Latin was remapped into Devanagari that
    # spells nothing -- exactly the VOL-126 damage this veto exists to stop, and
    # invisible because the remap leaves no U+FFFD behind.
    #
    # Both axes needed it, not just the ASCII ones. `decoded` is derived from the same
    # run, and a converter passes a marked codepoint through unchanged, so a marked run
    # decodes to no Devanagari at all -- the dictionary axis below then finds no word
    # and never suppresses the veto. The caller now decodes the unmarked text; unmarking
    # here as well keeps the two consistent whatever a future caller passes.
    #
    # 🛑 **The two halves fail in OPPOSITE directions, and an earlier form of this
    # comment called both of them "failing OPEN".** Under this programme's own
    # definition -- open corrupts English, closed blocks a correct remap -- the ASCII
    # axes failed OPEN (a marked run of genuine Latin was remapped into Devanagari that
    # spells nothing, invisibly, because the remap leaves no U+FFFD) and the dictionary
    # axis failed CLOSED (measured through the caller, an unmark in the predicate alone
    # makes the veto fire on a genuine NEPALI run and blocks a correct remap). Both
    # needed fixing; only the first was the dangerous one. The labelling matters because
    # the standing rule is "open is the dangerous direction", so calling the recoverable
    # half dangerous mis-prices the next change to it. Raised in review.
    text = unmark_cids(text)

    non_space = [char for char in text if not char.isspace()]
    # Hoisted above the floor test only so the all-upper floor can consult it; the
    # comprehension is pure, and a letter is never whitespace, so this is the same
    # list the alpha ratio has always divided by. An empty run still returns False
    # here -- `letters` is empty, so the floor stays at the mixed-case one -- which keeps
    # the ratio below from dividing by zero.
    letters = [char for char in non_space if char.isascii() and char.isalpha()]
    floor = (
        _LATIN_VETO_MIN_CHARS_UPPER
        if letters and "".join(letters).isupper()
        else _LATIN_VETO_MIN_CHARS
    )
    if len(non_space) < floor:
        return False
    if any(char in _LEGACY_KEYSTROKE_SYMBOLS for char in text):
        return False
    if len(letters) / len(non_space) < _LATIN_VETO_MIN_ALPHA_RATIO:
        return False
    if not letters:  # unreachable while the ratio floor is above 0, but the
        return False  # division below must not depend on that staying true
    vowels = sum(1 for char in letters if char in _ASCII_VOWELS)
    if vowels / len(letters) < _LATIN_VETO_MIN_VOWEL_RATIO:
        return False
    if _MEDIAL_CAPS.search(text):
        return False
    return not any(word in decoded for word in _CONTENT_LEGACY_DICTIONARY)


def _nepali_validity(text: str) -> dict[str, float]:
    """Score how much ``text`` reads as genuine Nepali (higher = more valid)."""

    devanagari = len(_DEVANAGARI_CHAR.findall(text))
    non_space = len(re.sub(r"\s", "", text)) or 1
    # Two garble measures, and which one goes where is load-bearing (VOL-185).
    # `_legacy_map_garble` is comparative and feeds the RANKING axis; the full
    # `_text_quality_penalty` is what the ABSOLUTE gate ceiling was calibrated
    # against and feeds `penalty_per_deva`. `ecc5338` moved both at once and
    # loosened the gate; see :func:`_legacy_map_garble`.
    # Computed ONCE each and shared. `_legacy_map_garble(text)` is
    # `_text_quality_penalty(text) - _duplicate_consonant_count(text) * weight`, so
    # calling it here recomputed the penalty a second time and the doublet count a
    # third: measured on a 7,680-character span that redundant half is 1.86 ms of
    # 4.22 ms, 44.0%, and this runs for six candidate maps on every font of every
    # document (31,174 font decisions corpus-wide). `_legacy_map_garble` stays as the
    # named helper -- it is what the tests and the docstring reason about -- and
    # `test_the_inlined_ranking_penalty_still_equals_the_named_helper` pins the two
    # forms equal so this cannot silently drift into a third definition.
    penalty = _text_quality_penalty(text)
    duplicates = _duplicate_consonant_count(text)
    hits = sum(1 for word in _CONTENT_LEGACY_DICTIONARY if word in text)
    return {
        "devanagari": devanagari,
        "ratio": devanagari / non_space,
        # Both forms of the garble measure, because they answer different
        # questions and are not interchangeable. ``penalty`` is the raw weighted
        # artifact count, comparable between two decodes OF THE SAME span;
        # ``penalty_per_deva`` normalises it so one span can be compared against
        # an absolute ceiling. See :func:`_map_ranking_key` for why using the
        # normalised form to rank candidates is a bug.
        #
        # VOL-185: and they are no longer the same numerator. ``penalty`` drops the
        # doubled-consonant term because it charges correct readings when maps are
        # compared; ``penalty_per_deva`` keeps it because that is the measure the
        # gate's 0.05 ceiling was calibrated against. See
        # :func:`_legacy_map_garble` for what happens when one substitution moves both.
        # ⚠️ `min(..., _RANKING_DOUBLET_FORGIVENESS)`, matching this commit's change to
        # `_legacy_map_garble`. The parent inlined the subtraction here to avoid
        # recomputing the penalty and the doublet count (44.0% of the call on a
        # 7,680-character span), so the forgiveness floor has to be carried into the
        # inlined copy too -- without it the floor simply does not apply on the
        # ranking path, which is the only path it exists for.
        # `test_the_inlined_ranking_penalty_still_equals_the_named_helper` is what
        # caught that, and it is the reason that test was written.
        "penalty": penalty
        - min(duplicates, _RANKING_DOUBLET_FORGIVENESS) * _DUPLICATE_CONSONANT_WEIGHT,
        "penalty_per_deva": penalty / devanagari if devanagari else float("inf"),
        # Reported separately so `_map_ranking_key` can forgive a bounded number of
        # these sites WITHOUT the gate forgiving any -- see
        # `_RANKING_IKAR_NASAL_FORGIVENESS`. It is a component of `penalty`, not an
        # extra axis, so it must not be read as evidence on its own.
        "ikar_nasal": len(_IMPOSSIBLE_IKAR_NASAL_PATTERN.findall(text)),
        "hits": hits,
        # Not part of `penalty`: see `_STRANDED_BRACKET_PATTERN`. Ranking only.
        "stranded": len(_STRANDED_BRACKET_PATTERN.findall(text)),
        # Ranking only, and below `stranded`: see :func:`_map_ranking_key`.
        "attested": _attested_word_count(text),
    }


def _passes_content_legacy_gate(validity: dict[str, float]) -> bool:
    return (
        validity["hits"] >= _CONTENT_LEGACY_MIN_HITS
        and validity["devanagari"] >= _CONTENT_LEGACY_MIN_DEVA
        and validity["ratio"] >= _CONTENT_LEGACY_MIN_DEVA_RATIO
        and validity["penalty_per_deva"] <= _CONTENT_LEGACY_MAX_PENALTY_PER_DEVA
    )


def _map_ranking_key(
    validity: dict[str, float],
) -> tuple[float, float, float, float, float, float, float]:
    """Evidence axes for a candidate map, most decisive first, higher is better.

    ``hits`` and ``penalty`` are the primary axes. ⚠️ ``penalty`` is deliberately NOT
    the calibrated quantity -- the calibration is the 0.05 ceiling on
    ``penalty_per_deva`` (:data:`_CONTENT_LEGACY_MAX_PENALTY_PER_DEVA`), which the gate
    uses and this axis keeps its hands off, which is the whole point of VOL-185's split. ``ratio`` and
    ``devanagari`` are tie-breaks only: a map that fits the face maps every
    keystroke onto Devanagari, so the residue a *wrong* map leaves behind shows up
    as non-Devanagari characters. That is what separates Spins from Preeti on a
    small span — Preeti reads Spins' ``_`` as a literal ``)`` where Spins produces
    the anusvara ``ं``.

    They sit strictly below ``penalty`` because a high Devanagari ratio on its
    own is a mirage (``test_nepali_validity_flags_garble_low``): converting ASCII
    digits into Devanagari digits also raises it, which is exactly what the
    ``Spins_EXT`` companion faces do under the map that is wrong for them. Those
    are excluded by ``hits``, not by this key. One OAG municipality report
    (``4487__…बसबरिया गाउँपालिका``, font ``Spins``, 2,156 characters) is a live
    instance: ``Spins`` there reads 0.69 Devanagari against ``PCS NEPALI``'s 0.68
    and is nonetheless the wrong map, carrying 48 penalty points to PCS NEPALI's
    zero. So ``ratio`` must not be promoted above the garble axis (VOL-89).

    **The garble axis is the raw count, not the per-Devanagari rate (VOL-89).**
    Every candidate here decodes *the same input span*, so the counts are already
    on a common scale and normalising them adds nothing — worse, it injects the
    denominator as a phantom signal. With the numerator held equal,
    ``penalty_per_deva`` is a monotone function of the Devanagari *count*, so it
    silently becomes the ``devanagari`` axis while ranking above both ``ratio``
    and ``devanagari``. That is not hypothetical: on
    ``3222__…faktalung ga.pa`` (font ``Spins``, 757 characters) all six maps score
    an identical raw penalty of **18**, and ``PCS NEPALI`` won only because
    18/576 < 18/562 — a denominator difference, not a garble difference — which
    rendered ``;_Vof`` as ``स)ख्या`` where the correct Spins read is ``संख्या``.

    Ranking on the raw count makes equal evidence an exact tie, so the axes below
    it decide those spans, and it needs no epsilon or threshold to do it.
    ``penalty_per_deva`` remains the right statistic for
    :func:`_passes_content_legacy_gate`, which compares one span against an
    absolute ceiling and therefore does need cross-span comparability.

    ⚠️ **A consequence worth naming, because splitting the two statistics is what
    creates it.** :func:`choose_legacy_map` elects a winner on this key and only
    then applies the gate, to that winner alone. While the garble axis WAS the
    gate's statistic, the winner among equal-``hits`` candidates was by
    construction the lowest-rate one, so it was the likeliest of them to clear the
    ceiling. Now the two differ, and inside the tie band ``ratio`` favours the
    candidate with the *smaller* Devanagari count — the gate's denominator. So an
    elected candidate can sit over the ceiling while a candidate ranked below it
    sits under, and the whole font unit is dropped rather than remapped.

    Measured rather than asserted: over 60,000 synthetic aggregates the two keys
    lose 49 and 50 units respectively, i.e. the mechanism is **not** a regression
    — it exists at the same rate on either side of this change, and on the corpus
    sample it fires zero times. It is recorded here and not fixed because the fix
    is a behavioural change of its own (re-rank among gate-passing candidates, the
    shape the mixed-margin gate uses for its second pass) and because a descendant
    that tightens the gate or widens ``ALL_MAP_KEYS`` will make it commoner. The
    control test :func:`test_a_real_difference_in_garble_still_outranks_the_ratio`
    names the same hazard for a ratio-*first* key; this is that hazard reached
    through the tie band instead.

    **``stranded`` sits between ``penalty`` and ``ratio`` (VOL-131).** It counts the
    wrong-map tell directly — an ASCII bracket left inside a Devanagari word — where
    ``ratio`` only sees it diluted, as one non-Devanagari character among several
    hundred. That dilution is why ``ratio`` was deciding these spans at its
    resolution limit: across the corpus ``ratio`` decides 486 remaps, and every one
    of its wrong decisions sits at a margin below 0.004 while all 404 decisions at
    a margin of 0.005 or more are right. On ``2573__…चामुण्डा विन्द्रासैनि`` the
    margin was **0.000016** and on ``3544__…Thasang Ga. Pa.`` **0.000724**; the
    stranded count on those same spans is 3 and 6 for the wrong maps against 0 for
    ``Spins``. A resolution floor on ``ratio`` was the alternative and is worse: it
    buys those two spans by abstaining on 25 to 44 others, several of them
    independently verified correct, and an abstention loses the span outright.

    It sits *below* ``penalty`` by choice, and it is deliberately absent from
    ``_text_quality_penalty`` because as an absolute quantity it is not a damage
    measure at all. See :data:`_STRANDED_BRACKET_PATTERN`.

    🛑 **The corpus record does not settle penalty-vs-stranded, and an earlier form of
    this docstring claimed it did.** It cited ``4487__…बसबरिया गाउँपालिका`` as showing
    that ``penalty`` must decide first. Re-measured on the real 4487 ``Spins`` aggregate
    (3,357 characters) at this head, the wrong map carries MORE of both — ``stranded`` 1
    and ``penalty`` 48 against PCS NEPALI's 0 and 0 — so the two axes agree, all three
    candidate orderings (this one, its parent's without ``stranded``, and
    stranded-above-penalty) return PCS NEPALI, and Spins fails the accept gate there
    anyway (48/655 = 0.0733 over the 0.05 ceiling). 4487 is a consistency check, not a
    discriminator. The ordering below is the conservative default — the axis with a
    calibrated absolute meaning goes first — and the sweep that would settle it is still
    owed. Anything reordering these two must run it rather than cite 4487.

    **The garble axis forgives a bounded margin, and that is what lets the two axes
    below it be reached (VOL-226).** ``penalty`` measures *well-formedness*, and a
    wrong legacy map does not emit malformed Devanagari — it emits well-formed
    Devanagari spelling the wrong word. So a *small* garble margin between two
    candidates is not evidence about which of them read the span, while it is more
    than enough to veto ``stranded`` and ``attested``, which are that evidence.
    :data:`_RANKING_GARBLE_FORGIVENESS` levels those margins so the decision falls
    through, exactly as the other two floors do for their own weak tells.

    **The forgiven margin is restored below ``attested``, and that restoration is
    load-bearing.** A bare floor does not hand the decision to the axes that are
    evidence -- it hands it to whichever axis is next, and when ``stranded`` and
    ``attested`` are level too, that is ``ratio``, which this docstring already
    establishes is a mirage on exactly these spans. Measured over all 6,236 corpus
    PDFs, a bare floor of 6 flipped ``2901__...Janaknandani gaupalika`` from
    ``PCS NEPALI`` to ``Preeti`` on nothing but a 0.017 ratio difference, with
    ``attested`` flat at 39 and ``stranded`` flat at 0; a floor of 12 added
    ``2384__...पनौती नगरपालिका`` on the same shape, and 24 added two more
    (``3741``, ``11319__...Nepalese Journal of Government Auditing``, the latter
    losing `निर्माण` and `ऋण` x3). With the raw count restored as the last evidence
    axis, all four of those hold their original map, because nothing that is evidence
    about map identity separates them -- while ``3843`` still flips, because
    ``stranded`` does separate it (0 against 4).

    On ``3843__…Godawari finale`` (font ``Spins``, 1,340 characters) the better
    ``Spins`` reading scores ``penalty`` **6** against ``Preeti``'s **0**, while
    carrying ``stranded`` **0** against ``Preeti``'s **4** and ``attested`` **44**
    against **40**. Both axes that identify the map pointed at ``Spins`` and both were
    vetoed by a 6-point margin, so the span was read with the ``Preeti`` map —
    mis-reading the rotated codes of :data:`_SPINS_TO_PREETI_KEYS`
    (``संख्या``->``स)ख्या`` ×3, ``कर्मचारी``->``कमंचारी``, ``सि.नं.``->``सि(न)(``) and
    leaving ``X`` raw through the residual tie (``ह्युम``->``Xयुम`` ×3). Forgiven, the
    two candidates level at the garble axis, ``stranded`` decides 0 against 4, and the
    span repairs 7 corrupt forms while losing no correct word.

    **Those 6 points are a TRUE positive, and the distinction is why this is a floor
    and not a reordering.** They are one ``_INVALID_IKAR_PATTERN`` hit on one genuinely
    malformed token, because the span **mixes both keyboard layouts**: ``l;=g+=`` is
    typed in the Preeti layout and reads ``सि.नं.`` under ``Preeti`` against a
    malformed ``सिृर्नृ`` under ``Spins``, while ``;_Vof`` is typed in the Spins layout
    and reads ``संख्या`` under ``Spins`` against ``स)ख्या`` under ``Preeti``. Neither
    map is right for the whole aggregate, so the choice is a majority judgement, and
    ``penalty`` cannot make it — one malformed token dominates an absolute damage count
    while seven wrong-but-well-formed words cost it nothing.

    **Promoting ``stranded`` above ``penalty`` was the obvious alternative and is
    measurably wrong.** Swept over all 6,236 corpus PDFs (``runs/vol226/``), it moves 7
    font decisions: it repairs ``3843`` and it breaks the other six, costing
    ``निर्माण`` **87 occurrences** and driving **three** documents — 31,338 characters
    of legacy text — to abstain outright, because the candidate it promotes then fails
    :func:`_passes_content_legacy_gate`. ``attested`` separates all seven cases (it
    rises only on ``3843``); a gate-aware axis separates only the three abstentions.
    The margin separates them too and needs no new instrument: the one decision that
    should move carries **6**, the nearest that must not carries **51**, and the rest
    carry 117 to 966. Hence a bounded floor, with the axis left where VOL-89 and
    VOL-131 put it. ``_text_quality_penalty``'s patterns are likewise untouched —
    VOL-135 measured 2 of its 8 firing on correct Nepali, but this hit is not one of
    those, and every other consumer of the penalty would move with a change to them.

    **``attested`` sits between ``stranded`` and ``ratio`` (VOL-185).** It counts how
    many distinct high-frequency Nepali word-forms a reading actually produces, and it
    is placed exactly where the axes stop being evidence: everything above it ties on
    these spans, and the paragraph above records that every wrong ``ratio`` decision
    sits at a margin below 0.004. ``ratio`` and ``devanagari`` ask how *Devanagari-shaped*
    a reading is; a wrong map for a legacy face maps every keystroke onto Devanagari too,
    so they are nearly blind here. Whether the output is Nepali *words* is a different
    question and the one that separates.

    On ``2424__…Ramechhap Nagarpalika`` (font ``Spins``, 283 characters) ``PCS NEPALI``
    beat ``Spins`` on ``ratio`` by **0.000249** — 0.981900 against 0.981651 — and
    rendered twelve repha as a misplaced anusvara (``आर्थिक``->``आथिंक``,
    ``कार्यविधि``->``कायंविधि``, ``खर्च``->``खचं``). On that same span ``Spins`` carries
    **12** attested forms against ``PCS NEPALI``'s **7**. The two readings hold 12 repha
    and 0 anusvara versus 0 repha and 12 anusvara respectively.

    It sits *below* ``stranded``, and therefore below ``penalty``, so it cannot disturb
    either calibration: it decides only spans on which both already tie. Measured on the
    corpus at ``677fa95``, that is the **127** of 1,390 gate-passing font decisions that
    ``ratio`` or ``devanagari`` were deciding. See :data:`_ATTESTED_NEPALI_WORDS` for how
    the vocabulary is derived and why it cannot be satisfied by garble.

    **Both axes above it forgive one hit** (:data:`_RANKING_DOUBLET_FORGIVENESS`,
    :data:`_RANKING_STRANDED_FORGIVENESS`), which is what lets ``attested`` reach the
    spans it is for. A lone doublet or a lone bracket is not evidence about which map read
    a span -- it lands on whichever reading happens to spell one -- and on
    ``2992``/``2993 parsa gaupalika`` (font ``Arial``) a lone bracket was picking the map
    that loses `समानीकरण`, `ऋण`, `संघिय` and `पोषण`. Forgiven, ``attested`` decides it 23
    to 22. Note that margin is one word: this axis is a tie-break of last resort, not a
    strong signal, and it is ordered accordingly.
    """

    return (
        validity["hits"],
        # The raw count, minus a bounded forgiveness -- the one-artifact floor.
        # `penalty` measures WELL-FORMEDNESS, and a wrong legacy map does not emit
        # malformed Devanagari; it emits well-formed Devanagari spelling the wrong word.
        # So a small garble margin between two decodes of ONE span is not evidence about
        # which map read it, while it is more than enough to veto `stranded` and
        # `attested`, which are that evidence. See `_RANKING_GARBLE_FORGIVENESS`, and the
        # restoration of the raw count below `attested` that bounds it.
        #
        # 🛑 **This GENERALISES the ikar+nasal forgiveness it replaces, and the
        # generalisation is exact where that term fired.** The old form subtracted
        # `_IKAR_NASAL_WEIGHT` when `_IMPOSSIBLE_IKAR_NASAL_PATTERN` matched at all;
        # since that pattern contributes exactly that weight per site, `penalty` is
        # always >= it when the term fired, so the floor below subtracts the same 6 in
        # every one of those cases. What it adds is the margins the pattern does NOT
        # explain -- `3843` and `5143` both carry 6 points of `_INVALID_IKAR_PATTERN`
        # with `ikar_nasal` at 0, so the old term left them vetoed. `ikar_nasal` is still
        # reported: it is the measured instance that motivated the floor, and the gate
        # statistic `penalty_per_deva` still charges it in full.
        -max(validity["penalty"] - _RANKING_GARBLE_FORGIVENESS, 0),
        # VOL-185: a single stranded bracket is forgiven for the same reason a single
        # doublet is -- see `_RANKING_STRANDED_FORGIVENESS`.
        -max(validity["stranded"] - _RANKING_STRANDED_FORGIVENESS, 0),
        validity["attested"],
        # VOL-226: the raw count, restored as the LAST evidence axis. What the floor
        # above forgives, this recovers -- so a forgiven margin can only ever be
        # overruled by `stranded` or `attested`, never by `ratio`, which is a mirage.
        -validity["penalty"],
        validity["ratio"],
        validity["devanagari"],
    )


# ---------------------------------------------------------------------------
# VOL-218: the mixed letter+digit margin gate. OPT-IN, OFF by default.
# ---------------------------------------------------------------------------
#
# The two legacy map families swap the number rows: `Preeti`/`Kantipur`/
# `Sagarmatha`/`Spins` put the Devanagari digits on the SHIFTED row `!@#$%^&*()`
# and read `0123456789` as consonants, while `PCS NEPALI`/`FONTASY_HIMALI_TT` do
# the reverse. So choosing the wrong family does not garble a span, it *transposes*
# letters and digits -- and on a document that types money on one row and place
# names on the other, one flip produces both directions at once. Measured on
# `3719__...Humla Sarkegad` (font `Felix Titling`, v13 `PCS NEPALI` -> v14
# `Preeti`): 49 unshifted-row keystrokes became consonants and 23 shifted-row
# keystrokes became digits, in the same table. See
# `oag-corpus/runs/vol218/FINDING-19-...-72f6752a.md`.
#
# A token carrying BOTH a Devanagari letter and a Devanagari digit is the signature
# of that transposition, because real Nepali orthography does not mix them inside a
# word. Ordinals like `१०औं` do, which is why this is a comparative measure between
# candidate readings of the SAME span and never an absolute quality score.
#
# 🛑 **Being comparative does NOT neutralise the second artifact, and this comment used
# to present it as though it did.** Span concatenation manufactures mixed tokens, and it
# does so ASYMMETRICALLY between the two map families: `PCS NEPALI` and
# `FONTASY_HIMALI_TT` read ASCII digits as digits, so an amount span abutting a word span
# produces a digit-next-to-letter token in their reading, while `Preeti`, `Kantipur` and
# `Sagarmatha` read those same digits as consonants and produce nothing. The production
# decision unit is exactly such a concatenation -- the per-font aggregate -- so the
# artifact does not cancel; it favours one family systematically.
#
# That asymmetry is the reason this gate ships OFF, and the reason it must be measured
# per generation rather than reasoned about. It was disclosed in the PR body and nobody
# re-reads a merged PR body before turning a flag on, so it lives here.
#
# **Why a margin, and not simply another ranking axis.** The bare term was priced
# corpus-wide first (`runs/vol218/FINDING-17-...-d2362b10.md`): placed below
# `attested` (P1) it costs 0 attested forms but leaves `4834...खार्पुनाथ` damaged;
# placed above (P2) it repairs all four damaged documents but takes `attested` -5
# and makes four flips that are not repairs, because the term speaks on spans where
# its advantage is a single token. Gating it on a margin keeps the shipped axis in
# charge everywhere the evidence is thin: at M=5 it makes 6 flips, a strict subset
# of P2's 11, keeps all four repairs and costs `attested` -2 -- all of which is
# `4834`'s own repair (`runs/vol218/FINDING-18-...-0aa6842c.md`).
#
# **The term is an INDICATOR, not `-mixed`.** Among eligible candidates it does not
# further prefer the lowest count; it promotes the eligible set and defers to
# `attested`. That is what "only speak when the advantage exceeds a margin" means,
# and it is why the arm cannot shrink a tie it is silent on.
#
# Which margin ships is open board decision `b70918c8`; until that is answered this
# is unreachable unless a caller asks for it, so the default transcript is
# unchanged. Enabling it also requires a generation build, the serialized
# single-writer step.

_MIXED_MARGIN_ENV_VAR = "LIKHIT_LEGACY_MAP_MIXED_MARGIN"

# Distinguishes "caller said nothing, read the environment" from "caller explicitly
# passed None", which means off. A plain ``None`` default cannot express both, and a
# test that means to force the gate OFF must not be silently overridden by an env var
# some other test or a build driver left set.
# ⚠️ Annotated `Any` deliberately. Unannotated, this is `object`, and `ty` then reports
# error[invalid-parameter-default] on `mixed_margin: int | None = _MARGIN_FROM_ENV` --
# taking likhit's `ty` baseline from the 8 AGENTS.md pins for `main` to 9, which is
# exactly the phantom-regression trap that file warns about. The sentinel's identity is
# what matters, never its type: the only thing done with it is `is _MARGIN_FROM_ENV`.
_MARGIN_FROM_ENV: Any = object()
#: Smallest legal margin, for BOTH the env route and the keyword. Named because the two
#: used to enforce it in only one place: `_mixed_margin_setting` refused < 1 while a
#: caller passing `mixed_margin=0` went straight through. See
#: :func:`choose_legacy_map_detailed`.
_MIXED_MARGIN_FLOOR = 1
#: Where the ELIGIBLE indicator is spliced into :func:`_map_ranking_key`'s tuple: below
#: the stranded-bracket tell (index 2) and above `attested` (which becomes index 4). It
#: is a named index rather than a literal because
#: :func:`_map_ranking_key_margin_gated` derives its whole key from the ungated one --
#: see the note there for the divergence that made copying the axes untenable -- and a
#: bare `3` in a slice is the sort of thing a later axis insertion moves silently.
_MIXED_ELIGIBLE_INDEX = 3

# Composed forms only. A Devanagari class written as a range over *composed*
# characters decomposes if it is ever pasted through a shell, which compiles and
# silently reclassifies every combining mark as a letter -- that has happened on
# this corpus, so these are escapes and `_assert_mixed_classes_hold` checks them.
_DEVA_LETTER_CLASS = "\u0915-\u0939\u0958-\u095f"
_DEVA_DIGIT_CLASS = "\u0966-\u096f"
_DEVA_MARK_CLASS = "\u093a-\u094f\u0951-\u0957\u0962\u0963"

_DEVA_TOKEN_PATTERN = re.compile(
    f"[{_DEVA_LETTER_CLASS}{_DEVA_MARK_CLASS}{_DEVA_DIGIT_CLASS}]{{2,}}"
)
_DEVA_HAS_LETTER = re.compile(f"[{_DEVA_LETTER_CLASS}]")
_DEVA_HAS_DIGIT = re.compile(f"[{_DEVA_DIGIT_CLASS}]")


def _mixed_letter_digit_count(text: str) -> int:
    """Tokens in ``text`` carrying both a Devanagari letter and a Devanagari digit."""

    return sum(
        1
        for token in _DEVA_TOKEN_PATTERN.findall(text)
        if _DEVA_HAS_LETTER.search(token) and _DEVA_HAS_DIGIT.search(token)
    )


def _assert_mixed_classes_hold() -> None:
    """Refuse to rank on these classes if the literals above decomposed in transit."""

    if not (
        _DEVA_HAS_LETTER.search("क")  # ka is a letter
        and not _DEVA_HAS_LETTER.search("े")  # a vowel sign is not
        and not _DEVA_HAS_LETTER.search("०")  # nor is a digit
        and _DEVA_HAS_DIGIT.search("७")  # Devanagari 7
        and not _DEVA_HAS_DIGIT.search("7")  # ASCII 7 is not
        and _mixed_letter_digit_count("गो७ी") == 1  # go-7-i, damaged
        and _mixed_letter_digit_count("गोठी") == 0  # gothi, correct
    ):
        raise ExtractionError(
            "the Devanagari letter/digit character classes do not hold; "
            f"{_MIXED_MARGIN_ENV_VAR} cannot be honoured safely"
        )


def _mixed_margin_setting() -> int | None:
    """The configured margin, or ``None`` when the gate is off (the default).

    A malformed value is a configuration error and is raised rather than silently
    ignored: a gate that quietly disables itself would make a build's provenance
    unfalsifiable.
    """

    raw = os.getenv(_MIXED_MARGIN_ENV_VAR)
    if raw is None or raw.strip() == "":
        return None
    try:
        margin = int(raw)
    except ValueError as exc:
        raise ExtractionError(
            f"{_MIXED_MARGIN_ENV_VAR}={raw!r} is not an integer"
        ) from exc
    if margin < _MIXED_MARGIN_FLOOR:
        raise ExtractionError(
            f"{_MIXED_MARGIN_ENV_VAR}={raw!r} must be >= {_MIXED_MARGIN_FLOOR}; "
            f"unset it to disable the gate"
        )
    return margin


def _map_ranking_key_margin_gated(threshold: float):
    """:func:`_map_ranking_key` plus an ELIGIBLE indicator at P2's position.

    ``threshold`` is ``mixed(shipped winner) - margin``. A candidate is eligible iff
    its own mixed count is at or below it, so the gate promotes a candidate that beats
    the shipped winner by **at least** the margin.

    ⚠️ "By MORE THAN the margin" is what three records said, and it is off by one: the
    comparison is `mixed <= threshold` with `threshold = mixed(winner) - margin`, so an
    advantage of exactly `margin` fires. The measured M=5 figures were taken against
    this code, so the code is the thing to keep and the wording is the thing to fix.
    A consequence worth carrying: "any advantage at all" is M=**1** (threshold =
    winner - 1), which `_mixed_margin_setting` ACCEPTS. M=0 is a different arm again --
    "not worse than the winner" -- and it is the one the floor refuses.
    """

    def key(validity: dict[str, float]) -> tuple[float, ...]:
        eligible = 1.0 if validity.get("mixed", 0.0) <= threshold else 0.0
        # 🛑 SPLICED into :func:`_map_ranking_key`'s tuple, never restated. This function
        # used to hand-copy those axes, and the copy drifted the moment one of them
        # gained a term: `-validity["penalty"]` here stayed RAW while the ungated key
        # started forgiving one ikar+nasal site, so the gate ranked on a different garble
        # measure than the pass it is supposed to refine. Measured on
        # `3229__...sidingwa gapa.pdf`, font `Spins`: ungated `-penalty` 0 (level with
        # `Kantipur`, so the stranded tell decides it correctly), gated **-6** (Kantipur
        # wins outright and the whole font unit is mis-decoded). Latent only because the
        # gate ships OFF -- and the gate exists precisely so it can be turned ON, which
        # is what makes a silent divergence here worse than a visible one.
        base = _map_ranking_key(validity)
        return base[:_MIXED_ELIGIBLE_INDEX] + (eligible,) + base[_MIXED_ELIGIBLE_INDEX:]

    return key


@dataclass(frozen=True)
class LegacyMapChoice:
    """What ranking every legacy map against one span decided.

    ``ambiguous`` is the set of *input* code points the tied candidates read
    differently. It is empty unless a tie survived every evidence axis, and when
    it is non-empty those code points are left as raw keystrokes by
    :func:`decode_with_legacy_map` — see :func:`choose_legacy_map_detailed`.

    ⚠️ ``validity`` is **one reading's** axis -> value dict (``hits``, ``penalty``,
    ``penalty_per_deva``, ``devanagari``, ``ratio``, ``stranded``), not a per-map
    mapping: it is the winning reading's, or the MASKED reading's when a tie was
    scoped. Nothing per-map survives the call. Records had it as "the per-map
    validity scores", which is a different object entirely.
    """

    map_key: str | None
    validity: dict[str, float] | None
    ambiguous: frozenset[str] = frozenset()


def _ambiguous_code_points(
    text: str,
    convert_best,
    convert_tied: list,
) -> frozenset[str]:
    """Input code points that ``convert_best`` and any of ``convert_tied`` disagree on.

    Compared one code point at a time, which is a *candidate* set only: these maps
    reorder (a pre-base vowel sign moves across its consonant), so a per-character
    comparison is not by itself proof that the disagreement is confined to these
    code points. :func:`choose_legacy_map_detailed` verifies that claim on the
    whole span before relying on it, and abstains when it does not hold.
    """

    ambiguous = set()
    for code_point in set(text):
        try:
            best_reading = convert_best(code_point)
        except Exception:  # noqa: BLE001 - cannot read it alone; treat as ambiguous
            ambiguous.add(code_point)
            continue
        for convert in convert_tied:
            try:
                if convert(code_point) != best_reading:
                    ambiguous.add(code_point)
                    break
            except Exception:  # noqa: BLE001 - same
                ambiguous.add(code_point)
                break
    return frozenset(ambiguous)


def _decode_masking(text: str, convert, masked: frozenset[str]) -> str:
    """Convert ``text`` with ``convert``, leaving every code point in ``masked`` raw.

    Splits on the masked code points and converts each run between them, so a
    masked keystroke survives as itself and everything around it is decoded. The
    split also bounds each conversion's context at the masked position, which is
    intended: that position is where the candidates disagree, so it is not a
    place to carry reordering context across.
    """

    if not masked:
        return convert(text)
    out: list[str] = []
    run: list[str] = []
    for char in text:
        if char in masked:
            if run:
                out.append(convert("".join(run)))
                run = []
            out.append(char)
        else:
            run.append(char)
    if run:
        out.append(convert("".join(run)))
    return "".join(out)


def choose_legacy_map_detailed(
    text: str, *, mixed_margin: int | None = _MARGIN_FROM_ENV
) -> LegacyMapChoice:
    """Rank every :data:`ALL_MAP_KEYS` map against ``text`` and decide what to read.

    Returns the winning map key only when the reading clears
    :func:`_passes_content_legacy_gate`; otherwise ``map_key`` is ``None`` (the
    ``validity`` is the best-scoring candidate's, for diagnostics).

    **The order of ALL_MAP_KEYS is not a tie-break.** It used to be, implicitly:
    the loop kept the first strict maximum, so two maps level on every axis were
    separated by whichever the tuple happened to list first. On small spans that
    decided real documents — a 303-character face in one OAG municipality report
    tied all six maps at ``hits=3, penalty=0.0`` and was decoded as Preeti purely
    because Preeti is index 0, rendering ``;_Vof`` as ``स)ख्या`` where the correct
    Spins read gives ``संख्या`` (VOL-77). Position in a tuple is not evidence.

    ⚠️ **That document is fixed by the two added AXES, not by the tie remedy below,
    and the two must not be run together in one story.** Measured: it decodes as
    ``Spins``, resolved by ``ratio``. Neither VOL-77's abstention nor VOL-156's
    scope-masking ever runs on it, so someone debugging it from this docstring
    would read the wrong branch. The axes fixed VOL-77; what follows handles the
    residue no axis separates.

    Maps that produce *identical* text are not an ambiguity at all and are decided
    on the shared reading. Preeti, Kantipur and Sagarmatha decode much ordinary
    text the same way.

    **A tie that survives every axis is resolved at the scope of the ambiguity,
    not by discarding the span (VOL-156).** VOL-77's remedy for such a tie was to
    return ``None`` for the whole font, on the reasoning that raw keystrokes are
    recoverable where well-formed Devanagari spelling the wrong word is not. That
    reasoning is right and was applied at the wrong scope: the tied candidates
    agree about almost every code point, and *decoding what they agree on commits
    to nothing*, because there is no choice being made there.

    So a surviving tie now decodes with the ranking winner and leaves only the
    code points the tied candidates read differently as raw keystrokes. On the
    OAG corpus the difference is stark — on
    ``11104__Sangathit Sangrachana 2073`` (font ``Nepali``, 4,916 characters)
    ``PCS NEPALI`` and ``FONTASY_HIMALI_TT`` tie on all four axes to the last
    digit and disagree about **one** code point, ``?``, which one reads ``रू`` and
    the other ``रु`` — the rupee abbreviation with a long or a short vowel, 19
    occurrences, 0.39% of the span. Abstaining discarded 4,433 Devanagari
    characters, the whole of that document's v11 → v12 regression, to avoid
    choosing a vowel length. Corpus-wide that shape accounts for 59,867 Devanagari
    characters across 50 documents whose decode would have passed the content gate.

    The localisation is **verified, not assumed**. These maps reorder, so a
    per-code-point comparison cannot prove the disagreement is confined to those
    code points. After masking, every tied candidate must produce a byte-identical
    reading of the whole span; when one does not, the ambiguity is not localised
    and the span abstains exactly as before.

    One consequence, deliberately left in place: where candidates read a span
    identically the returned map *name* is still whichever comes first in
    :data:`ALL_MAP_KEYS`. The decoded text cannot depend on that choice, so no
    transcript is affected, but the recorded name is not a stable label for those
    spans and should not be read as an identification of the face.

    **VOL-218's mixed letter+digit margin gate is OPT-IN and OFF by default.** When
    ``mixed_margin`` is set -- by the argument, or by
    :data:`_MIXED_MARGIN_ENV_VAR` -- this runs in two passes: pass 1 is the shipped
    ranking above, and pass 2 re-ranks with an eligibility indicator for candidates
    whose mixed letter+digit count beats the pass-1 winner's by **at least** the margin
    (⚠️ not "by more than": the comparison is `mixed <= mixed(winner) - margin`, so an
    advantage of exactly `margin` fires -- see
    :func:`_map_ranking_key_margin_gated`). Both passes are *this* function's ranking
    core, so the tie mask, the localisation check and the accept gate are the shipped
    ones in both.

    Where pass 1 abstains there is no winner to measure a margin against, so the gate
    stays silent and the shipped result is returned unchanged. That forecloses
    ``abstain -> decided`` **by construction**: the gate can never bring a rejected span
    into the transcript.

    🛑 **But "can only ever move a span from one map to another" is NOT the whole
    invariant, and it was stated as though it were. There is a THIRD outcome: the gate
    also changes which code points are emitted RAW.** Pass 2 re-derives the tie state
    under a different key, and the gated key can only shrink a tie set, never grow it, so
    it can add or drop the VOL-156 ambiguity mask. Measured on this PR's own headline
    case, `3719`/`Felix Titling` at M=5: promoting `PCS NEPALI` creates an exact tie with
    `FONTASY_HIMALI_TT` on ``?``, so `ambiguous` goes from ``{}`` to ``{'?'}`` and the
    emitted text gains **7 raw `?`** where the shipped `Preeti` reading emitted 7 ``रु``.
    The reject side of that same mechanism is the `decided -> abstain` case below; the
    accept side is this.

    🛑 ``decided -> abstain`` needed a guard, and did NOT have one. Raised in review and
    measured on real spans: across the first 3 CIAA reports, 21,376 distinct spans, 2,015
    of which pass 1 decides, the gate changes the winner on 275 and turned **2** into
    abstentions. Synthetic strings cannot find this: 60,000 of them abstained in pass 1,
    so they never reach the region where the gate does anything.

    ⚠️ **"It dropped text that ships today" overstated it, and the correction matters for
    how the fixtures are read.** Both witnesses are single SPANS whose font is literally
    `Preeti`, and `classify_font` routes that name to ``legacy_remap`` -- so
    `detect_content_legacy_fonts` skips the font and this function is never asked about
    those spans on the production path, which passes only per-font AGGREGATES of fonts the
    name classifier calls "correct". On that unit no instance was found at all: 3,148
    aggregates from 515 PDFs, 104 decided by pass 1, **0** pass-2 abstentions at every
    margin in {1, 2, 3, 5, 8, 13, 99}. So the invariant was violated at the FUNCTION
    level on real span text, and the fixtures are function-level regression tests rather
    than corpus witnesses. The fallback is still right -- an abstaining second pass is not
    evidence against the first pass's accepted answer -- it is the claimed footprint that
    was wrong.

    Both were the ACCEPT-GATE path, and neither abstention was a finding about the span:

    * ``/sddWo] ?=%,!!,**,^&).() ...`` -- pass 1 picks Spins and it passes; pass 2
      promotes Preeti, which FAILS. An unacceptable candidate is not a better one, so
      there is nothing here to prefer over pass 1's accepted answer.
    * ``lhNnf lzIff sfof{no, 88]nw'/f`` -- the same map wins BOTH passes. Pass 1 reaches
      it through the tie path, so it gates the MASKED reading and accepts; pass 2 finds
      no tie under the gated key, so it gates the UNMASKED reading and rejects. The
      abstention is a disagreement between the two passes about which text to gate, not
      evidence about the span.

    So pass 2 falls back to the shipped choice whenever it abstains. That makes "can only
    ever move a span from one map to another" true in both directions rather than one,
    which is what the sentence above always claimed.
    """

    margin = (
        _mixed_margin_setting() if mixed_margin is _MARGIN_FROM_ENV else mixed_margin
    )
    # 🛑 The KEYWORD route must clear the same floor as the env route. `_mixed_margin_
    # setting` refuses anything below 1 ("unset it to disable the gate"), but the guard
    # below is `margin is None`, so a caller passing `mixed_margin=0` -- or a negative
    # int -- went straight through to a value a build is forbidden to use. At M=0 the
    # pass-1 winner is itself eligible, so the indicator promotes nobody and the gate's
    # ONLY effect is to break pass-1 ties: that drops the VOL-156 ambiguity mask and
    # decodes those code points as if the tie had been settled, which is the one thing
    # the mask exists to prevent.
    if margin is not None and margin < _MIXED_MARGIN_FLOOR:
        raise ValidationError(
            f"mixed_margin={margin!r} must be >= {_MIXED_MARGIN_FLOOR}; "
            f"pass None to disable the gate"
        )
    shipped = _choose_legacy_map_ranked(text, _map_ranking_key, mixed_threshold=None)
    if margin is None or shipped.map_key is None:
        return shipped

    _assert_mixed_classes_hold()
    try:
        winner_reading = get_converter_for_map(shipped.map_key)(text)
    except Exception:  # noqa: BLE001 - cannot re-read the winner; leave shipped alone
        return shipped
    # The threshold is measured on the winner's UNMASKED decode, which is the same
    # text each candidate's own `mixed` is measured on, so the comparison is between
    # like quantities. A negative threshold makes every candidate ineligible, which
    # is the silent case again and orders exactly as shipped.
    threshold = float(_mixed_letter_digit_count(winner_reading) - margin)
    gated = _choose_legacy_map_ranked(
        text,
        _map_ranking_key_margin_gated(threshold),
        mixed_threshold=threshold,
    )
    # An abstaining second pass is not evidence against the first pass's accepted
    # answer -- see the `decided -> abstain` note above, where both measured cases are
    # artifacts of pass 2 rather than findings about the span. Keeping `shipped` is what
    # makes this gate strictly a map-to-map move.
    return gated if gated.map_key is not None else shipped


def _choose_legacy_map_ranked(
    text: str,
    ranking_key,
    mixed_threshold: float | None,
) -> LegacyMapChoice:
    """The shipped chooser, with the ranking key supplied by the caller.

    Split out of :func:`choose_legacy_map_detailed` so VOL-218's margin gate can run
    the *real* chooser a second time under a different key -- the tie mask, the
    localisation check and the accept gate all stay the ones that ship, which is what
    makes the gate's corpus sweep a measurement of this code path rather than of a
    reimplementation of it. ``mixed_threshold`` is ``None`` on the shipped path, and
    then no mixed count is computed at all.
    """

    # `tuple[float, ...]`, not a fixed arity: this used to spell out four floats and was
    # left at four when `_map_ranking_key` grew a fifth, so the declared arity became a
    # second, disagreeing record of how many axes there are. The margin-gated key adds
    # two more. One place to change is `_map_ranking_key`'s own annotation.
    scored: list[tuple[tuple[float, ...], str, dict[str, float], str]] = []
    for map_key in ALL_MAP_KEYS:
        try:
            converted = get_converter_for_map(map_key)(text)
        except ExtractionError:
            # A missing/broken npttf2utf is a real config error — surface it
            # rather than silently disabling Part B (the name-based path raises
            # the same way), so behavior does not depend on the font name.
            raise
        except Exception:  # noqa: BLE001 - this map does not fit; try the next
            continue
        validity = _nepali_validity(converted)
        if mixed_threshold is not None:
            validity["mixed"] = float(_mixed_letter_digit_count(converted))
        digest = hashlib.blake2b(converted.encode("utf-8"), digest_size=16).hexdigest()
        scored.append((ranking_key(validity), map_key, validity, digest))
    if not scored:
        return LegacyMapChoice(None, None)

    # Stable sort on the evidence alone, so candidates level on every axis keep
    # their walk order and the tie below is detected rather than resolved by it.
    scored.sort(key=lambda candidate: candidate[0], reverse=True)
    best_key, best, best_digest = scored[0][1], scored[0][2], scored[0][3]
    tied = [candidate for candidate in scored[1:] if candidate[0] == scored[0][0]]

    masked: frozenset[str] = frozenset()
    if any(candidate[3] != best_digest for candidate in tied):
        convert_best = get_converter_for_map(best_key)
        converters = [get_converter_for_map(candidate[1]) for candidate in tied]
        masked = _ambiguous_code_points(text, convert_best, converters)
        try:
            readings = {
                _decode_masking(text, convert, masked)
                for convert in [convert_best, *converters]
            }
        except Exception:  # noqa: BLE001 - cannot mask this span; abstain as before
            return LegacyMapChoice(None, best)
        if not masked or len(readings) != 1:
            # The disagreement is not confined to those code points, so masking
            # them does not remove it. No evidence is left to choose on: abstain.
            return LegacyMapChoice(None, best)
        # Gate the text that will actually be emitted, not the unmasked reading.
        best = _nepali_validity(readings.pop())

    if _passes_content_legacy_gate(best):
        return LegacyMapChoice(best_key, best, masked)
    return LegacyMapChoice(None, best)


def choose_legacy_map(text: str) -> tuple[str | None, dict[str, float] | None]:
    """``(map_key, validity)`` for ``text`` — see :func:`choose_legacy_map_detailed`.

    Kept as the two-element view for callers that only need the decision. Anything
    that goes on to *decode* the span must use
    :func:`choose_legacy_map_detailed`, because the masked code points are part of
    the decision and dropping them would decode an ambiguity as if it were settled.
    """

    choice = choose_legacy_map_detailed(text)
    return choice.map_key, choice.validity


def decode_with_legacy_map(text: str, choice: LegacyMapChoice) -> str:
    """Decode ``text`` under ``choice``, leaving its ambiguous code points raw."""

    if choice.map_key is None:
        return text
    # The GATED converter, and the un-lift, because this is an output path: its only
    # caller in src/ is the content-legacy branch, which emits final text. Both used to
    # be applied at that call site, and moving the decode into this helper would have
    # dropped them -- reintroducing "(1)" -> "ढ१ण्" and leaving 0xF000-lifted bytes
    # unconverted. choose_legacy_map keeps using the RAW get_converter_for_map to
    # compare candidates, which is the distinction that matters here.
    #
    # 🛑 **The un-lift runs BEFORE the mask, and that order is load-bearing.** It was
    # the other way round: `_decode_masking` split the still-lifted span, so a masked
    # code point arriving as `chr(0xF000 + ord(c))` was not recognised as masked at all
    # -- it went into a run, got un-lifted there, and was decoded with the ranking
    # winner's map. That is exactly the commitment the mask exists to avoid, and it also
    # emitted a raw PUA code point for any mask hit that WAS recognised. Un-lifting
    # first fixes both: the mask sees bare code points, and an unresolvable one is left
    # as legible ASCII rather than as U+F0xx.
    #
    # ⚠️ Reachable, contrary to an earlier form of this comment, which cited `ARAP 11`.
    # That face classifies `legacy_remap`, so `detect_content_legacy_fonts` skips it and
    # it can never appear on the content-legacy path. `Symbol`, `SymbolMT` and
    # `Wingdings` can: measured, all three classify `correct` AND are
    # `is_symbol_pua_font`, which is precisely the combination that reaches here
    # carrying lifted bytes.
    return _decode_masking(
        unlift_symbol_pua(text),
        get_output_converter_for_map(choice.map_key),
        choice.ambiguous,
    )


def detect_content_legacy_fonts(
    doc: fitz.Document,
    skip_pages: frozenset[int] = frozenset(),
) -> dict[str, LegacyMapChoice]:
    """Map base-font name -> the legacy-map choice for mislabeled legacy fonts.

    Considers every font the name-based classifier calls "correct" whose
    aggregate span text reads as raw legacy keystrokes and then validates as
    Nepali under one of the legacy maps. ``skip_pages`` (1-based) excludes
    scanned-decoy pages — so this never rescues the CIB junk layer (Part A owns
    those) — and any page outside the requested extraction range.

    The candidate set is deliberately name-agnostic, because a legacy 8-bit face
    reaches a PDF under whatever name its producer's subsetter invented. OAG's
    2070 annual report carries its body font as ``TT339t00`` and the 2067-2072
    reports carry theirs as ``Spins``; neither is a standard-14 core name, so
    restricting candidates to core fonts left 436 pages of Preeti keystrokes
    undecoded. Widening the *name* registry instead would not be safe: the same
    documents put their clause numbers ("179", "23.2") in a companion font named
    ``Spins_EXT`` / ``TT33At00`` -- 85% ASCII digits, zero dictionary hits under
    every map -- which a substring match on the name would remap into garbage.
    Content is the only signal that separates the two, and the gate below is
    where it is applied.
    """

    considered_pages = [
        page_index
        for page_index in range(doc.page_count)
        if (page_index + 1) not in skip_pages
    ]

    # Aggregate span text per FULL font name (subset prefix included) so a
    # mislabeled-Preeti embedded font ("ABCDE+Helvetica") is decided separately
    # from a genuine bare core font ("Helvetica") of the same family — mapping
    # one must not corrupt the other's spans.
    #
    # There is no metadata pre-check in front of this pass. The one that used to
    # stand here ("skip unless a standard-14 core font is present") is what hid
    # the subset-named faces above, and it bought very little: 79 of 80 sampled
    # corpus documents carry a core font somewhere and paid for the pass anyway.
    # So the pass is now unconditional, and the ~1% of documents that used to
    # skip it pay one extra text-dict pass (~10ms per page).
    text_by_font: dict[str, list[str]] = defaultdict(list)
    for page_index in considered_pages:
        page_dict = get_cid_marked_page_dict(doc[page_index])
        for block in page_dict["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text_by_font[str(span["font"])].append(str(span["text"]))

    content_maps: dict[str, LegacyMapChoice] = {}
    for font_name, parts in text_by_font.items():
        # A font the name-based classifier already routes (legacy_remap, or a
        # broken-CMap family) is left to that path; this is only for fonts it
        # calls "correct".
        if classify_font(font_name, "") != "correct":
            continue
        aggregate = "".join(parts)
        if not _is_probably_legacy_ascii(aggregate):
            continue
        choice = choose_legacy_map_detailed(aggregate)
        if choice.map_key is not None:
            content_maps[font_name] = choice
    return content_maps


#: Strategies :func:`classify_font` names that REWRITE the span's text. Both are decided
#: from the font name alone, with no per-span veto, so a span of such a font is always
#: rewritten -- which is what disqualifies it as "text the remap leaves alone".
_TEXT_REWRITING_STRATEGIES = frozenset({"legacy_remap", "broken_cmap"})


@lru_cache(maxsize=None)
def _rewritten_outside_the_content_remap(font_name: str) -> bool:
    """Is this font's text rewritten by a path OTHER than the content-legacy remap?

    Three paths are, and :func:`detect_latin_acronym_survivors` must exclude all three:
    ``legacy_remap``, ``broken_cmap``, and the symbol-PUA map. Only the first was
    checked originally, which left `Kalimati`/`Lohit`/`Symbol` spans attesting acronym
    tokens they had no business attesting.

    `is_legacy_font` is kept alongside the strategy test rather than folded into it:
    the two agree on every name measured, but `is_legacy_font` is the registry the
    remap itself consults, so it is the authority for that channel and a strategy
    string is a second opinion about it.

    Cached on the name because this runs per SPAN over the whole document, while the
    answer depends on nothing but the name.
    """

    # 🛑 The FULL name, not `_span_base_font(font_name)`. `_match_font` strips the subset
    # prefix itself, so the wrapper bought nothing and introduced a divergence from the
    # predicate this mirrors: `classify_font` and `is_latin_cid_font` both pass the full
    # name to `is_legacy_font`, while `_span_base_font` takes the tail after the FIRST
    # `+` and can discard the segment carrying the registry key. Measured on
    # `XXXXXX+Preeti+Bold`: `is_legacy_font(full)` is True and `is_legacy_font(base)` is
    # False, so the guard admitted a span the remap rewrites -- the same hole this
    # function exists to close, reopened one name-shape later.
    return (
        is_legacy_font(font_name)
        or classify_font(font_name, "") in _TEXT_REWRITING_STRATEGIES
        or is_symbol_pua_font(font_name)
    )


def detect_latin_acronym_survivors(
    doc: fitz.Document,
    content_legacy_maps: dict[str, LegacyMapChoice] | None,
    skip_pages: frozenset[int] = frozenset(),
) -> frozenset[str]:
    """Acronym-shaped tokens this document carries in text the remap leaves alone.

    This is the document-scope evidence the third Latin veto needs (VOL-180 §8).
    "Text the remap does not rewrite" is three things, and all three count:

    1. spans of a font that is not a content-legacy candidate **and that no other
       rewrite path touches either** -- see
       :func:`_rewritten_outside_the_content_remap`, which is all three of them;
    2. spans of a run `27d74f0` vetoes (:func:`_reads_as_latin_text`);
    3. spans `5084fb8` vetoes (:func:`_reads_as_latin_words`).

    ⚠️ **There is now a FOURTH source and it is not text the extractor read -- it is
    text the extractor GUESSED.** `recover_latin_cid_text` turns a run of undecodable
    glyph ids into ASCII, and that ASCII flows into this pass like any other span, so a
    recovered token can enter the survivor vocabulary un-flagged as a guess. This veto
    is one-sided in the direction "veto more", so a fabricated acronym costs a correct
    remap of genuine Nepali. Measured false-recovery rate on arbitrary glyph ids:
    1.5-2.1%.

    Not excluded here because that needs recovery provenance carried on the span, which
    `get_cid_marked_page_dict` does not record, and because the marked-CID exclusion
    below already removes the runs that were NOT recovered. Stated so the enumeration
    above is honest at three-of-four rather than reading as complete.

    A surviving token additionally has to be **pure** -- not itself a legacy keystroke
    word -- or the vocabulary attests Nepali as English. See VOL-212 and
    :func:`_decodes_as_legacy_devanagari`.

    (1)'s second clause is the fix for VOL-247's one measured false positive, and it
    is a whole second remap this pass was blind to. `detect_content_legacy_fonts`
    only ever considers fonts the name classifier calls ``"correct"``, so a font like
    `Preeti` is never a *content*-legacy candidate — but
    :meth:`_convert_span_text` routes it down ``strategy == "legacy_remap"`` to
    :func:`get_converter`, which rewrites it just the same. Counting those spans as
    survivors lets a keystroke sequence attest itself: on `11129` the token `PG6L` is
    `एन्टी` ("anti"), attested from two `Preeti` spans, and the veto it licensed
    would have shipped **91 characters of fluent Nepali** as raw keystrokes. That is
    the same self-attestation the strict tokenizer closes for `w/f}6L` -> `6L`;
    closing it there was necessary and not sufficient.

    🛑 ``legacy_remap`` is **not the only** other rewrite path, and an earlier form of
    this clause tested `is_legacy_font` alone. That closes the one channel VOL-247
    measured and leaves two open, both verified live at the time of writing:
    ``broken_cmap`` (`Kalimati`, `Lohit` — repaired from their embedded tables) and the
    symbol-PUA map (`is_symbol_pua_font`). `is_legacy_font` is False for all three, so
    their spans were still attesting. The condition is therefore expressed as "does any
    non-content path rewrite this font", once, rather than as a list of names to
    remember to extend.

    Measured over the 74 documents that can fire (`runs/vol126r/`): without this
    clause 26 fires, 25 genuine and that one false positive; with it 25 fires, 25/25
    genuine, 0 Nepali touched. No genuine fire depends on name-legacy evidence.

    VOL-212's token clause and (1)'s second clause close the SAME measured fire by
    different mechanisms and neither subsumes the other: a name-legacy span can yield
    a token that does not decode as a Devanagari word (token clause admits it, (1)
    drops it), and a non-name-legacy span can yield one that does ((1) admits it, the
    token clause drops it). Both are present deliberately. See VOL-197.

    (2) is why this pass has to exist separately and has to run **after** the first
    veto: `QOC`'s own survivor evidence is *created* by `27d74f0` firing on
    `Quality Of Care, QOC` two pages earlier. Build the vocabulary before that veto
    is decided and the axis reaches none of the three residual runs it is for.

    Returns an empty set when the document has no candidate font, which is the
    common case (1,245 of the 6,236 corpus documents carry one), so the extra
    text-dict pass is paid only where it can change an outcome.
    """

    if not content_legacy_maps:
        return frozenset()

    survivors: set[str] = set()
    for page_index in range(doc.page_count):
        if (page_index + 1) in skip_pages:
            continue
        page_dict = get_cid_marked_page_dict(doc[page_index])
        for block in page_dict["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                spans = list(line["spans"])
                # Deliberately the DEFAULT (survivor-free) call: this is the state
                # of the veto before the third axis exists, which is exactly the
                # evidence the third axis is allowed to read. Passing the set being
                # built would make the vocabulary self-attesting.
                flags = _content_legacy_veto_flags(spans, content_legacy_maps)
                for index, span in enumerate(spans):
                    text = str(span["text"])
                    if not text.strip():
                        continue
                    font_name = str(span["font"])
                    # Checked FIRST and unconditionally: each of these rewrites is
                    # decided per font NAME with no per-span veto, so such a span is
                    # always rewritten and can never be survivor evidence.
                    if _rewritten_outside_the_content_remap(font_name):
                        continue
                    choice = content_legacy_maps.get(font_name)
                    rewritten = (
                        choice is not None
                        and choice.map_key is not None
                        and not flags[index]
                        and not _reads_as_latin_words(text)
                    )
                    # 🛑 A CID-MARKED span contributes NOTHING to the vocabulary, and
                    # this is the asymmetry the run side must not copy.
                    # `get_cid_marked_page_dict` marks the raw CID of every glyph that
                    # failed to decode, and `_acronym_tokens` unmarks, recovering
                    # `chr(cid)`. For a legacy 8-bit face the CID *is* the keystroke byte
                    # and that recovery is exactly right -- which is why the run side
                    # keeps it. For a subset font the CIDs are arbitrary glyph indices
                    # and are not text at all, so any 2-5 consecutive unmapped glyphs
                    # whose indices happen to fall in {48..57, 65..90} would yield a
                    # qualifying token and enter the vocabulary as evidence.
                    #
                    # Survival is only evidence of Latin if the surviving occurrence is
                    # itself Latin, and a glyph index is not. Before the unmark was added
                    # marked input contributed nothing at all, so this restores that
                    # property on the side where it was load-bearing while keeping the
                    # unmark on the side where it fixed a real defect.
                    #
                    # ⚠️ Mechanism confirmed at the function level; corpus reachability is
                    # NOT measured (a marked-CID PDF cannot be built from PyMuPDF's core
                    # fonts), so this is a guard against a proven mechanism with an
                    # unmeasured footprint, not a repair of a counted defect.
                    if not rewritten and count_marked_cids(text) == 0:
                        # VOL-212: survivor purity. Latin *shape* is not enough --
                        # `PG6L` has it and is `एन्टी`. Drop any token this
                        # document's own candidate map reads as a Devanagari word. This
                        # is a SECOND, independent narrowing of the same set: the
                        # condition above asks "is this text the extractor really read",
                        # and this asks "is this token really English".
                        survivors |= {
                            token
                            for token in _acronym_tokens(text)
                            if not _decodes_as_legacy_devanagari(
                                token, content_legacy_maps
                            )
                        }
    return frozenset(survivors)


def _content_legacy_veto_flags(
    spans: list[dict],
    # VOL-163: `aa4caff` widened this map's values from a bare map key to a
    # `LegacyMapChoice`. This helper is `27d74f0`'s and was written against the old
    # `str`; unwidened it would hand a dataclass to `get_converter_for_map`.
    content_legacy_maps: dict[str, LegacyMapChoice] | None,
    acronym_survivors: frozenset[str] = frozenset(),
) -> list[bool]:
    """Per span: does the Latin-side veto apply to it? (VOL-138)

    The decision unit is a **maximal run of consecutive same-font spans within one
    line**, not a single span. PyMuPDF splits spans at a font change, so such a run
    is what the producer laid down as one contiguous piece of that face — and it is
    the unit :func:`_reads_as_latin_text`'s thresholds were calibrated on
    (``runs/vol138/`` in the OAG corpus, via that sweep's ``_runs_of_line``). Judging
    a lone span instead would ask a 3-character fragment whether it is English.

    Every span of a vetoed run is flagged, so the run is kept or remapped whole.
    Spans of fonts that are not content-legacy candidates are never flagged.
    """

    flags = [False] * len(spans)
    if not content_legacy_maps:
        return flags
    start = 0
    while start < len(spans):
        font_name = str(spans[start]["font"])
        end = start + 1
        while end < len(spans) and str(spans[end]["font"]) == font_name:
            end += 1
        choice = content_legacy_maps.get(font_name)
        if choice is not None and choice.map_key is not None:
            run_text = "".join(str(spans[index]["text"]) for index in range(start, end))
            # `spans` come straight from `get_cid_marked_page_dict`, so this run can
            # carry marked CIDs. Decode the UNMARKED text: a converter passes a marked
            # codepoint through untouched, so decoding the marked form yields no
            # Devanagari and silently disables the veto's dictionary axis.
            raw_text = unmark_cids(run_text)
            if raw_text.strip():
                # The unmasked decode is right here: this is the *evidence* for
                # "would decoding this run produce Nepali words", not the output. The
                # mask (VOL-156) applies when the span is actually written.
                #
                # 🛑 DELIBERATELY UNGUARDED, and this note exists because review proposed
                # wrapping it in `except Exception: decoded = None`. Two reasons, and the
                # second is decisive:
                #
                #   1. No reachable input raises. Probed all 6 candidate maps against 18
                #      adversarial inputs -- NUL, embedded NUL, U+FFFD, BMP private use,
                #      a plane-15 CID mark, an astral emoji, every Latin-1 byte, control
                #      characters, a combining storm, 2,000 chars -- 0 raises. That run probed 18
                #      inputs x 6 maps = 108 pairs; the version KEPT as a test carries 16 of the
                #      18, so it performs 96. Two inputs did not survive the move, and quoting
                #      108 for the test overstates what CI re-checks.
                #   2. A blanket `except Exception` here would swallow `ExtractionError`,
                #      which `choose_legacy_map_detailed` above RE-RAISES on purpose: "a
                #      missing/broken npttf2utf is a real config error -- surface it
                #      rather than silently disabling Part B". Catching it here would do
                #      exactly what that comment forbids, and it would do it one run at a
                #      time, so a broken install would read as a corpus with no Latin in
                #      it rather than as a broken install.
                #
                # If this ever does need a guard, it must mirror the ranking loop: let
                # `ExtractionError` through and catch only the rest.
                # `test_an_extraction_error_from_the_veto_is_not_swallowed` pins that.
                decoded = get_converter_for_map(choice.map_key)(raw_text)
                if _reads_as_latin_text(raw_text, decoded):
                    for index in range(start, end):
                        flags[index] = True
                elif acronym_survivors and (
                    _acronym_tokens(run_text) & acronym_survivors
                ):
                    # 🛑 **This axis has NO evidence floor of any kind, and that is a
                    # deliberate, priced position rather than an oversight.** The fire
                    # condition is membership alone: one 2-5 character token attested
                    # once anywhere in the document vetoes every run containing it, and
                    # the unit of damage is the whole same-font run. Axis 1 requires
                    # >=16 non-space characters, >=0.88 alpha, >=0.30 vowels and zero
                    # dictionary hits in the decode; axis 2 requires a >=10%
                    # function-word share. So the loosest of the three is the one that
                    # fires on the residue the other two rejected, and VOL-180 §9's
                    # own flag for this was dropped.
                    #
                    # Measured before leaving it: a deterministic 700-PDF sample (11.2%
                    # of the corpus, seeded) produced **0** axis-3 fires, and the
                    # obvious fix -- adding axis 1's dictionary condition here, where
                    # `decoded` is already in scope -- would have changed **0** of them.
                    # A sample this size cannot bound a rare axis tightly, and that is
                    # the point: the footprint is small enough that a floor cannot be
                    # calibrated on it, so adding one would be guessing. If this axis is
                    # ever widened, the floor is the first thing to add, and axis 1's
                    # dictionary condition is the cheapest one.
                    #
                    # ⚠️ The `elif` is a SHORT-CIRCUIT, not the
                    # second-pass rule: both branches set the same flags, so
                    # `if A: flag elif B: flag` is `if A or B: flag` and replacing the
                    # `elif` with an independent `if` leaves the whole suite green. All
                    # it buys is skipping one `_acronym_tokens` call when axis 1 already
                    # fired. §8's second-pass requirement is implemented in
                    # `detect_latin_acronym_survivors`, which calls this function with
                    # the DEFAULT survivor-free set -- that is where the ordering claim
                    # is true and it is documented there. Same run unit either way, so a
                    # vetoed acronym run is kept whole like any other.
                    for index in range(start, end):
                        flags[index] = True
        start = end
    return flags


class FontBasedStrategy(ExtractionStrategy):
    """Extract text from Nepali PDFs using PyMuPDF blocks."""

    def extract_text(self, file_path: str, pages: str | None = None) -> RawDocument:
        return self._extract_raw_document(file_path, pages=pages)

    def extract_tables(self, file_path: str) -> list[Table]:
        return self._extract_raw_document(file_path).tables

    def _extract_raw_document(
        self,
        file_path: str,
        pages: str | None = None,
    ) -> RawDocument:
        path = Path(file_path)
        if path.suffix.lower() != ".pdf":
            raise ValidationError("Unsupported file format. Please upload a PDF file")
        if not path.exists():
            raise ValidationError(f"File not found: {file_path}")

        try:
            doc = fitz.open(path)
        except Exception as exc:
            raise ExtractionError(
                "Unable to parse PDF. File may be corrupted or encrypted"
            ) from exc

        repaired_doc: fitz.Document | None = None
        try:
            page_start, page_end = 0, doc.page_count - 1
            if pages:
                page_start, page_end = parse_page_range(pages, doc.page_count)

            font_strategies_by_page = scan_pdf_fonts_by_page(doc)
            has_broken_cmap = any(
                strategy == "broken_cmap"
                for page_strategies in font_strategies_by_page.values()
                for strategy in page_strategies.values()
            )

            # Part A: pages that are a scanned raster (with or without a decoy
            # core-font text layer) carry no born-digital text and need OCR.
            ocr_pages = scan_ocr_pages(doc)
            in_range = range(page_start + 1, page_end + 2)
            needs_ocr_pages = sorted(page for page in ocr_pages if page in in_range)
            decoy_pages = frozenset(
                page
                for page, marker in ocr_pages.items()
                if marker == SCANNED_DECOY_TEXT and page in in_range
            )
            # Part B: bare Latin core fonts that actually carry legacy keystrokes.
            # Skip OCR pages AND pages outside the requested range, so text the
            # caller never asked for cannot flip the content-map gate and corrupt
            # in-range extraction (mirrors needs_ocr_pages/decoy_pages scoping).
            skip_for_content = frozenset(ocr_pages) | frozenset(
                page for page in range(1, doc.page_count + 1) if page not in in_range
            )
            content_legacy_maps = detect_content_legacy_fonts(doc, skip_for_content)
            # VOL-323 decision (a). Scoped by the SAME skip set as the content-map
            # gate, for the same reason: text the caller never asked for must not be
            # able to decide how in-range text is converted.
            digit_companion_fonts = detect_digit_companion_fonts(doc, skip_for_content)
            # VOL-180's third Latin veto reads document-scope evidence, so its
            # vocabulary is built once here and shared by every extraction pass
            # below -- including the broken-CMap repaired pass, which is the same
            # document's content and must not disagree with the first pass about
            # which acronyms this document attests.
            acronym_survivors = detect_latin_acronym_survivors(
                doc,
                content_legacy_maps,
                skip_for_content,
            )

            # On a broken-CMap PDF this document is extracted twice, before and
            # after the ToUnicode repair, and the merge below keeps the repaired
            # pass's tables whenever it found any. Table detection is the single
            # largest cost in extraction (67-87% of wall time on these documents),
            # so detecting in the first pass is usually pure waste: measured
            # across 28 corpus documents, the repaired pass found tables every
            # time and the first pass's were always discarded. Skip it here and
            # detect below only if the repaired pass comes back empty.
            #
            # The results cannot simply be shared between passes: PyMuPDF derives
            # a table's header from the page's decoded text, so on a broken-CMap
            # page the repair changes header.external, header.bbox and
            # header.names -- and captions feed the continuation-merge decision.
            detect_tables = not has_broken_cmap

            raw_document = self._extract_from_document(
                doc,
                font_strategies_by_page,
                page_start=page_start,
                page_end=page_end,
                needs_reorder=False,
                decoy_pages=decoy_pages,
                content_legacy_maps=content_legacy_maps,
                digit_companion_fonts=digit_companion_fonts,
                acronym_survivors=acronym_survivors,
                detect_tables=detect_tables,
            )
            if has_broken_cmap:
                repaired_source = fitz.open(path)
                try:
                    repaired_doc, needs_reorder = fix_kalimati_cmap(repaired_source)
                finally:
                    if repaired_source is not repaired_doc:
                        try:
                            repaired_source.close()
                        except ValueError:
                            pass
                repaired_document = self._extract_from_document(
                    repaired_doc,
                    font_strategies_by_page,
                    page_start=page_start,
                    page_end=page_end,
                    needs_reorder=needs_reorder,
                    decoy_pages=decoy_pages,
                    content_legacy_maps=content_legacy_maps,
                    digit_companion_fonts=digit_companion_fonts,
                    acronym_survivors=acronym_survivors,
                )
                tables = repaired_document.tables
                if not tables:
                    # The repaired pass found nothing, so fall back to detecting
                    # on the unrepaired document -- preserving the behaviour of
                    # the `repaired.tables or raw.tables` merge this replaces.
                    tables = self._extract_from_document(
                        doc,
                        font_strategies_by_page,
                        page_start=page_start,
                        page_end=page_end,
                        needs_reorder=False,
                        decoy_pages=decoy_pages,
                        content_legacy_maps=content_legacy_maps,
                        digit_companion_fonts=digit_companion_fonts,
                        acronym_survivors=acronym_survivors,
                    ).tables
                raw_document = _raw_document_from_fragments(
                    _merge_fragment_variants(
                        raw_document.fragments,
                        repaired_document.fragments,
                    ),
                    tables,
                )

            raw_document.needs_ocr_pages = needs_ocr_pages
            # The requested range is authoritative, not the pages that happened
            # to yield text. A suppressed scanned-raster page produces no
            # fragments, so deriving this from output would drop exactly the
            # pages `needs_ocr_pages` says need OCR merged in.
            raw_document.page_numbers = list(in_range)

            if not raw_document.raw_text:
                if needs_ocr_pages:
                    raise ScannedPdfError(
                        "PDF has no recoverable text layer; needs OCR",
                        needs_ocr_pages,
                    )
                raise ExtractionError("No text content found in document")

            return raw_document
        except (ExtractionError, ValidationError):
            raise
        except Exception as exc:
            raise ExtractionError(
                f"Failed to extract text from PDF: {path.name}"
            ) from exc
        finally:
            if repaired_doc is not None:
                repaired_doc.close()
            doc.close()

    def _extract_from_document(
        self,
        doc: fitz.Document,
        font_strategies_by_page: dict[int, dict[str, str]],
        *,
        page_start: int,
        page_end: int,
        needs_reorder: bool,
        decoy_pages: frozenset[int] = frozenset(),
        content_legacy_maps: dict[str, LegacyMapChoice] | None = None,
        # VOL-323: threaded rather than re-detected, so every extraction pass over one
        # document -- including the broken-CMap repaired pass -- agrees about which fonts
        # are digit companions. Re-detecting per pass would let two passes of the same
        # document disagree, which is the class of bug `acronym_survivors` is threaded to
        # avoid.
        digit_companion_fonts: frozenset[str] | None = None,
        acronym_survivors: frozenset[str] = frozenset(),
        detect_tables: bool = True,
    ) -> RawDocument:
        paragraphs: list[str] = []
        fragments: list[TextFragment] = []
        tables: list[Table] = []
        table_index = 0

        for page_index in range(page_start, page_end + 1):
            if (page_index + 1) in decoy_pages:
                # Scanned raster with a non-embedded core-font decoy layer: its
                # text is legacy-keystroke garbage, so drop the whole page and
                # leave it for the caller's OCR path (see needs_ocr_pages).
                continue
            page = doc[page_index]
            page_font_strategies = font_strategies_by_page.get(page_index + 1, {})
            numeric_repairs = collect_page_repairs_by_line(
                page,
                page_number=page_index + 1,
            )
            page_dict = get_cid_marked_page_dict(page)
            lines_by_key: dict[
                tuple[int, int], list[tuple[float, float, float, float, str]]
            ] = defaultdict(list)
            for block_number, block in enumerate(page_dict["blocks"]):
                if "lines" not in block:
                    continue
                for line_number, line in enumerate(block["lines"]):
                    spans = list(line["spans"])
                    latin_veto = _content_legacy_veto_flags(
                        spans,
                        content_legacy_maps,
                        acronym_survivors,
                    )
                    for span_index, span in enumerate(spans):
                        text = self._convert_span_text(
                            str(span["text"]),
                            str(span["font"]),
                            page_font_strategies,
                            needs_reorder,
                            content_legacy_maps=content_legacy_maps,
                            digit_companion_fonts=digit_companion_fonts,
                            skip_content_legacy=latin_veto[span_index],
                        )
                        if not text:
                            continue
                        x0, y0, x1, y1 = span["bbox"]
                        lines_by_key[(block_number, line_number)].append(
                            (
                                float(x0),
                                float(y0),
                                float(x1),
                                float(y1),
                                text,
                            )
                        )

            page_fragments: list[TextFragment] = []
            previous_y1: float | None = None
            for (block_number, line_number), line_words in sorted(
                lines_by_key.items(),
                key=lambda item: (
                    round(min(piece[1] for piece in item[1]), 2),
                    min(piece[0] for piece in item[1]),
                ),
            ):
                ordered_words = sorted(line_words, key=lambda piece: piece[0])
                line_text = join_spans_with_layout(ordered_words)
                line_text = apply_line_numeric_boundary_repairs(
                    line_text,
                    numeric_repairs.get(
                        (page_index + 1, block_number, line_number),
                        (),
                    ),
                )
                paragraph = normalize_press_release_paragraph(line_text)
                if not paragraph:
                    previous_y1 = None
                    continue

                x0 = min(piece[0] for piece in ordered_words)
                y0 = min(piece[1] for piece in ordered_words)
                x1 = max(piece[2] for piece in ordered_words)
                y1 = max(piece[3] for piece in ordered_words)
                gap_before = None
                if previous_y1 is not None:
                    gap_before = y0 - previous_y1
                previous_y1 = y1

                fragment = TextFragment(
                    text=paragraph,
                    page_number=page_index + 1,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    block_number=block_number,
                    line_number=line_number,
                    gap_before=gap_before,
                )
                paragraphs.append(paragraph)
                page_fragments.append(fragment)

            fragments.extend(page_fragments)
            if detect_tables:
                page_tables = detect_page_tables(page, page_fragments, table_index)
                tables.extend(page_tables)
                table_index += len(page_tables)

        return RawDocument(
            paragraphs=paragraphs,
            raw_text="\n\n".join(paragraphs).strip(),
            fragments=fragments,
            tables=merge_continuation_tables(tables),
            page_numbers=list(range(page_start + 1, page_end + 2)),
        )

    def _convert_span_text(
        self,
        text: str,
        font_name: str,
        font_strategies: dict[str, str],
        needs_reorder: bool,
        # VOL-163: `aa4caff` widened the value type from `str` to `LegacyMapChoice`
        # (it carries the ambiguous code points), and `27d74f0` added the veto flag.
        # Both, not either.
        content_legacy_maps: dict[str, LegacyMapChoice] | None = None,
        skip_content_legacy: bool = False,
        # VOL-323: full font names whose plain ASCII digit row draws `०-९` in their own
        # glyph program and which no map already handles. Keyed on the FULL name, like
        # `content_legacy_maps`, because the decision is per font resource.
        digit_companion_fonts: frozenset[str] | None = None,
    ) -> str:
        base = _span_base_font(font_name)
        strategy = font_strategies.get(base, "correct")

        # VOL-323, decision (a): the digit-dominant legacy companion. Its plain ASCII
        # digit row draws `०-९` in its own glyph program, it matches no shipped map key
        # (its decimal separator is a literal `.` where every map emits `।`), and the
        # `hits >= 2` candidacy gate excludes it by design -- so without this branch its
        # figures survive into the transcript as raw ASCII.
        #
        # FIRST, and deliberately so. This is not a legacy remap and must not reach one:
        # `Preeti`/`Spins` would turn `2075` into `द्दण्ठछ` and `FONTASY_HIMALI_TT` would
        # turn the `.` into `।`. It is a digit-row transliteration and nothing else.
        # Placing it here rather than after the strategy branches also means it cannot be
        # reached by a font that IS routed -- `detect_digit_companion_fonts` already
        # requires `_matched_registry_key(name) is None`, so the two populations are
        # disjoint by construction and this cannot double-convert.
        if digit_companion_fonts and font_name in digit_companion_fonts:
            return devanagarize_companion_digits(text)

        # Decoy suppression happens page-level in _extract_from_document (decoy
        # pages are skipped wholesale), so no span-level decoy branch is needed.
        # Content-legacy maps are keyed by full font name (subset prefix included)
        # so only the exact mislabeled font resource is remapped.
        # Three guards now stand between a candidate font and its remap, and VOL-163
        # composes all of them. They are independent and ordered cheapest-first:
        #
        #   1. `skip_content_legacy` -- the structural Latin veto (`27d74f0`),
        #      decided by `_content_legacy_veto_flags` over this span's whole
        #      same-font run.
        #   2. `_reads_as_latin_words` -- the word-identity Latin veto (`5084fb8`),
        #      decided on this span. Corpus-wide it spares 14 runs (409 chars) that
        #      (1) misses, all 14 genuine English (runs/vol163/), which is why both
        #      ship rather than one.
        #   3. `decode_with_legacy_map` -- the tie mask (`aa4caff`). Where a tie
        #      survived every evidence axis, only the *disputed* code points stay
        #      raw; the rest decode, because the tied candidates agree about them
        #      and decoding an agreed code point commits to nothing (VOL-156).
        #
        # 1 and 2 decline the remap entirely; 3 narrows it. A span vetoed by 1 or 2
        # falls through to the strategy branches below, which return its raw text
        # unchanged for a font the name classifier calls "correct".
        if content_legacy_maps and not skip_content_legacy:
            content_choice = content_legacy_maps.get(font_name)
            # ⚠️ BOTH halves, matching `_content_legacy_veto_flags`. The parent tested
            # the map key; when this became a `LegacyMapChoice` wrapper the test was
            # widened to the wrapper, and a `LegacyMapChoice(map_key=None)` is not None
            # -- so an UNDECIDED choice entered the branch, `decode_with_legacy_map`
            # returned the text unchanged, and the `legacy_remap`, `is_symbol_pua_font`
            # and reorder branches below were all short-circuited for that span. The
            # sibling helper added in the same commit kept both halves; this one did not.
            if content_choice is not None and content_choice.map_key is not None:
                # Candidacy was decided per font over the whole document, so this
                # span may be genuine Latin that merely shares the face. Leaving
                # readable English alone is strictly better than remapping it into
                # well-formed Devanagari that spells nothing.
                if _reads_as_latin_words(text):
                    return text
                return decode_with_legacy_map(text, content_choice)

        if strategy == "legacy_remap":
            converter = get_converter(font_name)
            if converter is not None:
                # VOL-704: un-lift first. A legacy font whose cmap is symbol-style
                # ("ARAP 11") hands us byte + 0xF000 instead of the byte, so the
                # converter would otherwise see private-use characters it has no
                # entry for and pass the whole span through untouched -- which is
                # exactly how 1,363 glyphs of Nepali text shipped as U+F0xx.
                # A no-op for every legacy font likhit already handled, since
                # those arrive as ASCII keystrokes.
                return converter(unlift_symbol_pua(text))
            return text

        # VOL-704: a legacy SYMBOL font (Symbol, Wingdings) classifies "correct",
        # because as far as the name-based classifier is concerned nothing is
        # wrong with it -- so without this branch its private-use codepoints fall
        # through untouched to the bare `return text` below. Placed after the
        # legacy-Devanagari branches on purpose: `pua_maps` must never see a
        # Devanagari font, since U+F020 and U+F029 are emitted by both Symbol and
        # ARAP 11 in this corpus and mean different things in each.
        if is_symbol_pua_font(font_name):
            return remap_symbol_pua(text, font_name)

        if needs_reorder and (
            strategy == "broken_cmap" or _contains_private_use_marker(text)
        ):
            text = reorder_devanagari(text)
            text = normalize_devanagari_spacing(text)
        return text
