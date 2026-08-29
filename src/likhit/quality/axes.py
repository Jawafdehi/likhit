"""Deterministic garble audit over every converted transcript.

Motivation: a whole-file Devanagari *ratio* is not sufficient. A legacy-Kalimati
report recovered by the fixed likhit scored ratio=0.977 yet its first line was
`dxfn]vfk/LIfssf] jflif{s n]vfk/LIf0f k|ltj]bg` — untranslated legacy bytes —
and elsewhere read `गाउँपाललका` where the source says `गाउँपालिका` (a मात्रा
/ vowel-sign corruption). Localized garble hides inside a good global score, so
every transcript is scored on several independent axes and the WORST one wins.

Checks (all pure text statistics, no model calls):

1. `legacy_ascii`     Preeti/Kantipur/Sagarmatha legacy encoding leaking through
                      as Latin (`dxfn]vfk/LIfssf]` for `महालेखापरीक्षकको`).
                      Scored as a fraction of the WHOLE document — see the
                      calibration note on the function.
2. `mojibake`         U+FFFD replacement chars, C1 controls, Latin-1-through-
                      UTF-8 artifacts. In this corpus U+FFFD lands specifically
                      on Devanagari conjuncts: `लेखापरी�ण` for `लेखापरीक्षण`.
3. `repha_loss`       Repha (र्) silently destroyed — `कार्यालय` → `कायायलय`.
                      Well-formed Devanagari, so NO other check sees it.
4. `numeric_damage`   Amounts corrupted (`4,187.�`) or several table cells
                      merged into one implausible figure. Audit-critical.
5. `structure`        Signals a real audit report must have: OAG letterhead,
                      fiscal year, digits, plausible line profile.
6. `repetition`       Pathologically repeated lines (long text, no information).
7. `spacing`          Glued or per-character-spaced words (`म ह ा ल े ख ा`).
8. `matra_damage`     Vowel-sign corruption. ADVISORY: precision is 0.986 but
                      96.6% of its hits are already caught by `mojibake`; kept
                      because it independently detects visual/glyph reading
                      order (pre-base `ि` emitted before its consonant).

Verdict per file = worst check: `clean` < `suspect` < `garbled`.
A whole-file verdict is deliberately conservative — most "garbled" files are
partially good (a legacy cover page over a correct body), so consult the
per-check `legacy_frac_of_doc` / `purity` evidence before discarding a document.

A ninth axis, `page_refusal`, is **opt-in**: turning a gate on by default is a decision for
whoever cuts a generation, not for the tool.

:func:`audit_text` is the entry point and it is pure -- text in, verdicts out, no filesystem
and no corpus schema. The version this was extracted from also opened a `.json` sidecar
beside each document to read `bucket`/`fiscal_year`/`province`, which is one corpus's shape
and no business of a general audit.

🛑 **Three of this module's Devanagari classes were normalization-fragile and nothing was
checking.** `DEV_CONSONANT`, `MALFORMED_CONJUNCT_RA` and the orphan-matra class each spanned
U+0958-U+095F, the Unicode composition exclusions, written as literals. Every normalization
form rewrites those as <base, U+093C NUKTA>, which leaves a descending range and makes
`re.compile` raise -- so a normalized copy of this file would not IMPORT. The package's
`test_regex_normalization_stability` caught all three the moment the file arrived; in the
untracked directory it came from, it had carried them for the corpus's whole history.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ..devanagari import (
    DOUBLED_MATRA_PATTERN,
    ORPHAN_MATRA_PATTERN,
    VIRAMA_MATRA_PATTERN,
)
from .normalise import normalise_for_audit, split_pages
from .page_refusal import classify_page
from .shapes import digit_count, merge_shaped

DEV = re.compile(r"[ऀ-ॿ]")
#: Devanagari consonants including the nukta letters U+0958-U+095F. **Escapes, not
#: literals.** Written literally this class stops COMPILING under every normalization
#: form -- see :mod:`likhit.devanagari`. The package's normalization guard caught all
#: three fragile classes in this file the moment it entered the package; in the
#: untracked run directory it came from, nothing was checking.
DEV_CONSONANT = re.compile(r"[\u0915-\u0939\u0958-\u095f]")
MALFORMED_CONJUNCT_RA = re.compile(
    r"[\u0915-\u0939\u0958-\u095f]\u0930\u094d[\u093e-\u094c]"
)
LATIN = re.compile(r"[A-Za-z]")

#: Bigrams/trigrams that legacy Preeti/Kalimati encodings emit constantly when
#: read as Latin-1. Empirically these dominate a mis-decoded Nepali page:
#: e.g. `महालेखापरीक्षकको` -> `dxfn]vfk/LIfssf]`.
LEGACY_MARKERS = (
    "n]v",
    "k|",
    "/LIf",
    "jflif",
    "ltj]",
    "sf]",
    "sf{",
    "nf]",
    "gf]",
    "]bg",
    "df",
    "if{",
    "b]",
    "u/",
    "kf",
    "hf",
    "cf",
    "If0",
    "vfk",
    "fnf",
)
#: Only the >=3-char markers. The seven 2-char ones in LEGACY_MARKERS produced
#: 57.5% of all hits while also matching ordinary text ("दफा 24 (2) df 10"), so
#: they are excluded from scoring.
LEGACY_MARKERS_STRONG = tuple(m for m in LEGACY_MARKERS if len(m) >= 3)
#: Punctuation these encodings scatter *inside* words.
#:
#: ⚠️ These are exactly the characters :data:`LEGACY_RUN_RE`'s bracket class contains, so this
#: is two spellings of one fact -- which is why `test_the_legacy_punct_set_matches_the_run_
#: pattern` asserts they agree rather than leaving them to drift. It stays a named set because
#: :mod:`likhit.privacy.placeholders` cites it by name to explain why a `[REDACTED:...]` marker
#: reads as a legacy run, and a cross-reference to a constant that does not exist is the defect
#: this package keeps finding.
LEGACY_PUNCT = set("]{|/[}~^`")
#: A legacy-encoded token: Latin/digit word chars carrying at least one of the
#: bracket-class chars these encodings emit. Requiring the bracket avoids
#: matching ordinary English, and the length floor avoids "A/C", "and/or".
LEGACY_RUN_RE = re.compile(
    r"""[A-Za-z0-9"']*[\]\{\[\}~^`|/][A-Za-z0-9"']*"""
    r"""(?:[\]\{\[\}~^`|/"']*[A-Za-z0-9]+)*"""
)
#: Fenced code blocks — some transcripts wrap whole pages in ```text fences.
#: Matches the fence *and its contents*, so substituting it deletes the page.
#: 🛑 NOT for scoring -- use `strip_fences`. Retained because the pre-2026-08-12 behaviour is
#: what `strip_fences`' docstring and two spacing tests describe by contrast, and that history
#: is not expressible without the pattern that caused it. The corpus-side
#: `measure_legacy_denominator.py` also scores that old behaviour with it, but that tool lives
#: outside this package, so it is not the reason this constant is here.
FENCE_RE = re.compile(r"```.*?```", re.S)
#: A fence *delimiter* line only — the pattern `measure_legacy_denominator.py`
#: measured the fix with, character for character (`test_audit_legacy_ascii.py`
#: asserts the two have not drifted apart).
FENCE_DELIMITER_RE = re.compile(r"^\s*```[a-zA-Z]*\s*$", re.M)
#: Slash-separated numeric spans: fiscal years ("2070/71", "२०८०/८१",
#: "2013/14"), simple ratios, and Bikram Sambat dates ("076/3/23", "०७६/३/२३").
#: All pervasive in these reports, and none of them legacy garble.
#:
#: The three-component form and single-digit components matter: the earlier
#: two-component, {2,4}-only pattern left "076/3/23" untouched, so LEGACY_RUN_RE
#: read it as a bracket-punctuation token. That put 24 documents from clean to
#: suspect purely because V8 recovered date columns V7 had dropped. Legacy
#: Preeti garble ("dxfn]vfk/LIfssf]") is letters around its slashes, not digits,
#: so a digits-only span cannot swallow it.
FISCAL_SPAN_RE = re.compile(r"[0-9०-९]{1,4}(?:\s*/\s*[0-9०-९]{1,4}){1,2}")
#: Numeric tokens, incl. Devanagari digits, separators, and any U+FFFD damage.
NUM_TOKEN_RE = re.compile(r"[0-9०-९][0-9०-९,.�]*")
#: Digits a token needs before `numeric_damage` will consider it a merged cell.
#: Not evidence by itself -- see `check_numeric_damage` -- but it bounds the
#: candidates to the population the geometry oracle has actually measured.
MERGE_MIN_DIGITS = 15
#: (correct, repha-stripped) word pairs. Present in nearly every OAG report, so
#: their ratio is a reliable purity probe for the र् loss mode.
REPHA_PAIRS = (
    ("कार्यालय", "कायायलय"),
    ("निर्माण", "निमायण"),
    ("वर्ष", "वषय"),
    ("आर्थिक", "आिथक"),
    ("प्रदेश", "पदेश"),
    ("सूर्य", "सूयय"),
    ("कार्य", "कायय"),
    ("वर्गीकरण", "वगीकरण"),
    ("पूर्व", "पूवय"),
    ("दर्ता", "दताय"),
    ("खर्च", "खचय"),
)
#: The SAME repha loss, but with `ि` substituted where REPHA_PAIRS expects `य`
#: -- the form the VOL-169 visual-glyph-order mechanism actually emits, and the
#: DOMINANT one for nine of the eleven pairs above. Measured over all 6,223 v12
#: transcripts, the shipped probe sees 6,635 whole-token corruptions against
#: 53,915 in these nine forms: **11.0% of the corruption it is named for**
#: (VOL-168, `runs/vol168/FINDING-01-repha-forms-87393de3.md`).
#:
#: Discovered, not assumed. For each canonical word every form the VOL-169
#: mechanism can emit was generated -- drop `र्`, insert `य` or `ि` at any
#: position, and/or move a `ि` one position left -- and all 6-21 candidates
#: counted; each form below came back rank 1 of its own canonical's candidate
#: set. Ruled out: taking every *attested* candidate instead (33 forms), which
#: picks up legitimate Nepali and lifts the fully-clean median from 0 to 19.
#:
#: `प्रदेश` and `वर्गीकरण` get no entry because their corruption DELETES `र्`
#: without substituting a glyph (`प्रदेश` -> `पदेश`), so no `ि`-variant exists
#: for a probe to miss. Those two are the only pairs above that were already
#: probing their own dominant form.
#:
#: This list is stated explicitly and in the source on purpose: every figure
#: VOL-168 published depends on which forms are in it, and three of that issue's
#: numbers could not be reproduced precisely because it was left unstated.
REPHA_PAIRS_I_FORM = (
    ("कार्यालय", "कायािलय"),
    ("निर्माण", "निमािण"),
    ("वर्ष", "वषि"),
    ("आर्थिक", "आथििक"),
    ("सूर्य", "सूयि"),
    ("कार्य", "कायि"),
    ("पूर्व", "पूवि"),
    ("दर्ता", "दताि"),
    ("खर्च", "खचि"),
)
#: The full probe used by `check_repha_loss(..., extended=True)`.
REPHA_PAIRS_EXTENDED = REPHA_PAIRS + REPHA_PAIRS_I_FORM
#: `repha_corrupt` at or above which a document is at least `suspect` whatever
#: its purity says. Purity is a whole-document ratio, so a report can carry
#: hundreds of destroyed words and still read >= 0.75 clean on the strength of
#: its undamaged pages: 102 of the 299 v12 documents holding >= 5 whole-token
#: repha corruptions shipped clean on all eight checks.
#:
#: 12 = one above the 99th percentile (11) of v13's *floor-decidable*
#: population -- the 5,701 documents clean on the other seven checks and already
#: above the 0.75 purity cut, i.e. the ones a floor is the only thing that can
#: move. That is VOL-168's own stated rule, applied to the field this code
#: emits: the issue proposed 20 "above the 99th percentile", but its percentile
#: was computed on a different quantity than the field it named, so a literal
#: implementation ran ~8.6x weaker than intended (18 documents against 155).
#: On the correct field 20 sits at roughly p99.6 and flags 25 documents; 12
#: flags 54, every one of which ships `clean` today.
#:
#: Nothing argues for sitting further above p99: per-form precision was measured
#: on all 6,223 v13 transcripts and 96.4% of hits have the corrupt form as the
#: token prefix, with the residue being adjacent corruption (`वावषिक` for
#: `वार्षिक`, glued pairs like `निमािणकायि`) rather than legitimate Nepali. The
#: one clear false positive found corpus-wide is `उपदेश्य` matching the shipped
#: form `पदेश`, 6 occurrences. Calibration and per-form table:
#: `runs/vol168/FINDING-04-floor-calibrated-5aa38d07.md`.
REPHA_CORRUPT_FLOOR = 12

#: Latin-1-through-UTF-8 artifacts, the replacement char, NUL and BOM.
#: Written as escapes: a literal NUL in source is itself a syntax error.
MOJIBAKE_MARKERS = ("�", "Ã", "â€", "Ð", "Â»", "Â«", "\x00", "\ufeff")


def _sample(text: str, budget: int = 600_000) -> str:
    """Head+mid+tail sample. Head-only truncation judged a 2.6M-char annual
    report on its first 15%, and damage is often concentrated in cover pages
    (legacy fonts) or trailing annexes (tables)."""
    if len(text) <= budget:
        return text
    k = budget // 3
    mid = (len(text) - k) // 2
    return text[:k] + "\n" + text[mid : mid + k] + "\n" + text[-k:]


def _count_longest(text: str, forms: tuple[str, ...]) -> int:
    """Non-overlapping count of `forms`, longest form winning at each position.

    Substrings, not whole tokens, and deliberately so: Nepali is agglutinative,
    so `कार्यालयको` is a genuine occurrence of `कार्यालय` that whole-token
    equality would discard (that reading loses 918k canonical occurrences
    corpus-wide, on top of the nesting below).

    Longest-match-first is what removes VOL-168's third defect. `कार्य` is a
    prefix substring of `कार्यालय` and the shipped check counts with
    `str.count` per form, so every `कार्यालय` was counted twice on the canonical
    side -- 5,643,123 substring against 2,838,251 whole-token corpus-wide, a
    1.99x inflation of which `कार्य` alone is 1,886,017. `purity` is
    `good / (good + bad)`, so inflating `good` biases the verdict toward
    `clean`: the same direction as the missing forms and the missing floor.

    `re` alternation is leftmost-first rather than longest, so the alternatives
    are sorted by descending length to make the longest one win; `finditer`
    supplies the non-overlapping part.
    """
    uniq = sorted(set(forms), key=len, reverse=True)
    if not uniq:
        return 0
    return sum(1 for _ in re.finditer("|".join(re.escape(f) for f in uniq), text))


def _ratio(n: float, d: float) -> float:
    return n / d if d else 0.0


def strip_fences(text: str) -> str:
    """Drop ``` delimiter lines and keep what they wrap.

    FIXED 2026-08-12. Both scoring checks used to call `FENCE_RE.sub(" ", text)`.
    likhit wraps whole pages in ```text fences, so that deleted the page itself —
    numerator and denominator together — and the check then scored whatever was
    left outside the fences. Measured by `measure_legacy_denominator.py` on V10
    (`runs/v10/legacy-denominator-v10.json`): the shipped check saw a median
    **14.07%** of a document, 2,213 documents under 10%, 5,144 under 50%, and only
    85 documents in full.

    What correcting the denominator does to the published bands: **658
    suspect->clean and 138 clean->suspect, with 0 clean->garbled and 0
    garbled->clean**. So the blindness hid nothing severe — `garbled` is 105
    before and after — but the `suspect` band was 3.5x too big (726 -> 206).

    `check_spacing` had the identical call and escaped the bug only by accident:
    it ran `_sample()` first, and sampling cut mid-fence, leaving unbalanced
    markers that ```` ```.*?``` ```` cannot match. Both checks now strip
    delimiters only, and strip before sampling, so neither depends on where a
    sample boundary happens to land.
    """
    return FENCE_DELIMITER_RE.sub(" ", text)


def check_legacy_ascii(text: str, dev_n: int, lat_n: int) -> tuple[str, dict]:
    """Legacy-encoded Nepali (Preeti/Kantipur/…) leaking through as Latin.

    CALIBRATION (red-teamed 2026-08-04): the first version divided marker hits by
    `lat_n` (Latin chars only) and scored AUC **0.213** — anti-correlated with
    real damage, precision 0.202. A clean Nepali report whose only Latin is a
    3-line Preeti letterhead has a tiny denominator, so it scored 50+/1k and was
    called garbled: 1,414 files were flagged on this check ALONE at a median
    Devanagari ratio of 0.984 (i.e. almost entirely correct Nepali).

    Fixes: measure legacy text as a fraction of the WHOLE document, count only
    the ≥3-char markers (the seven 2-char ones — `df`, `kf`, … — were 57.5% of
    hits and match ordinary text like "दफा 24 (2) df 10"), and ignore fence
    delimiters and markdown table pipes so `X|Y` is not read as in-word
    punctuation.

    "the WHOLE document" only became true on 2026-08-12: until then this deleted
    each fence's contents along with its delimiters, i.e. the page. See
    `strip_fences` for what that cost — a median document scored on 14.07% of
    itself, and a `suspect` band 3.5x too big.
    """
    stripped = strip_fences(text)
    # Fiscal-year spans ("2070/71", "2013/14") are the single biggest FP source
    # in English reports — 24 hits of "2070/71" alone in one summary. Also drop
    # markdown table pipes so "X|Y" is not read as in-word legacy punctuation.
    stripped = FISCAL_SPAN_RE.sub(" ", stripped)
    stripped = stripped.replace("|", " ")
    # A legacy run: a word-ish token carrying the bracket punctuation these
    # encodings emit. Requiring the bracket class avoids matching English.
    runs = LEGACY_RUN_RE.findall(stripped)
    run_chars = sum(len(r) for r in runs)
    leg_frac = _ratio(run_chars, max(1, len(re.sub(r"\s", "", stripped))))
    hits = sum(stripped.count(m) for m in LEGACY_MARKERS_STRONG)
    ev = {
        "legacy_run_chars": run_chars,
        "legacy_runs": len(runs),
        "legacy_frac_of_doc": round(leg_frac, 4),
        "strong_marker_hits": hits,
        "latin_chars": lat_n,
        "devanagari_chars": dev_n,
    }
    if leg_frac > 0.15:
        return "garbled", ev
    if leg_frac > 0.02:
        return "suspect", ev
    return "clean", ev


def check_repha_loss(text: str, extended: bool = False) -> tuple[str, dict]:
    """Devanagari repha (र्) destroyed: कार्यालय -> कायायलय.

    This mode was a pure FALSE NEGATIVE of the original audit: the output is
    well-formed Devanagari with no U+FFFD and no Latin, so every other check
    passes it. Measured corpus-wide, 294 transcripts show severe loss and 157 of
    those were labelled clean/suspect. Verified case
    `6426__…सहिदभूमी गाउपालिका, २०८१.md`: 0 correct `कार्यालय` vs 68 `कायायलय`,
    0 `निर्माण` vs 275 `निमायण` — and all six original checks reported clean.

    Detection compares canonical Nepali words against their repha-stripped
    corruptions; these words appear in essentially every OAG audit report.

    `extended=True` is VOL-168's fix and turns on all three of its corrections
    together, because two of them are near-inert alone:

      * the `ि`-substituting forms (`REPHA_PAIRS_I_FORM`), which are the
        DOMINANT corruption for 9 of the 11 pairs and 89.0% of the phenomenon;
      * longest-match counting (`_count_longest`), which stops `कार्य` being
        counted a second time inside every `कार्यालय`;
      * `REPHA_CORRUPT_FLOOR`, an absolute count that fires whatever the
        whole-document purity ratio says.

    The floor and the forms have to land together: on the *shipped* field a
    floor of 20 moves 18 documents, on the extended count 155. Landing the
    floor alone is the ~8.6x-weaker reading VOL-168 asked for by name.

    Default is OFF and byte-identical to the shipped instrument -- asserted over
    all 6,223 v13 transcripts, not assumed (`runs/vol168/`). Turning it on
    relabels documents in a published tree, so it is the generation owner's call
    and not a silent reinterpretation of v12's or v13's verdict counts.
    """
    if extended:
        good = _count_longest(text, [g for g, _ in REPHA_PAIRS_EXTENDED])
        bad = _count_longest(text, [b for _, b in REPHA_PAIRS_EXTENDED])
    else:
        good = sum(text.count(g) for g, _ in REPHA_PAIRS)
        bad = sum(text.count(b) for _, b in REPHA_PAIRS)
    if good + bad < 10:
        # VOL-523: this branch used to collapse the payload to
        # `{"probe_hits": n}`, which serialises indistinguishably from a
        # *measured* clean -- so "2,494 well-formed repha" and "no repha at all"
        # read the same downstream. A mutant of the VOL-508 lane A transcript
        # with all 7,425 of its `र्` replaced by `ं` lands here with
        # good = bad = 0 and scores `clean` on the whole audit, plain and
        # extended (`runs/vol523-2561ac89/`).
        #
        # The verdict stays `clean` and is deliberately NOT changed: with fewer
        # than 10 probe hits there is genuinely nothing to compare, and any
        # other verdict would relabel documents in a published tree. What
        # changes is only that the zero becomes visible -- `canonical` is
        # reported unconditionally, and `undetermined` marks the band as
        # "no comparison was possible" so a reader can tell it from a
        # measurement. `purity` is omitted rather than faked, because none was
        # computed.
        #
        # Verdict-neutrality is measured, not asserted: all 6,223 v14r1 verdicts
        # are identical before and after this change, on both probes
        # (`VERDICT-NEUTRALITY-2561ac89.md`).
        ev = {
            "canonical": good,
            "repha_corrupt": bad,
            "probe_hits": good + bad,
            "undetermined": True,
        }
        if extended:
            ev["probe"] = "extended"
        return "clean", ev
    purity = _ratio(good, good + bad)
    ev = {"canonical": good, "repha_corrupt": bad, "purity": round(purity, 3)}
    if extended:
        ev["probe"] = "extended"
    if purity < 0.35:
        return "garbled", ev
    if purity < 0.75:
        return "suspect", ev
    if extended and bad >= REPHA_CORRUPT_FLOOR:
        return "suspect", ev
    return "clean", ev


def check_numeric_damage(
    text: str, confirmed_merges: int | None = None
) -> tuple[str, dict]:
    """Corrupted amounts — audit-critical, and unchecked in v1.

    Two independent failures, both of which silently produce wrong figures:
      * a digit run containing U+FFFD (`4,187.�`) — the amount is unreadable;
      * several table cells merged into one figure (`१,०१,५७,६७८१,६२,३०३…`),
        because the PDF text layer left no gap between the spans. Verified
        against the source page: those are four separate beruju figures.

    A long digit run is NOT evidence of the second on its own, which is what this
    check used to assume. A Nepali bank account number runs to 15, 16 or 20 digits
    and sits alone in its own column: `verify_numeric_merges.py` put every flagged
    run to the page geometry and found 12,540 of 14,608 were single cells, so the
    `>= 15 digits` rule ran at precision 0.142. The token has to also be
    *merge-shaped* — not a valid single value, but cleanly divisible into ones —
    which raises precision to 0.872 at recall 0.896 on that same population.

    The digit floor stays, so the candidate population is unchanged and the
    improvement is attributable to the shape test alone. Dropping the floor
    collapses precision to 0.14: the sub-15-digit flags are overwhelmingly
    three-place ratios and comma-separated lists, measured over a 30-document
    sample with the oracle run down to 4 digits.

    Pass `confirmed_merges` to take the count from the geometry oracle instead.
    That removes both residual error classes; the text estimate is what the audit
    can do alone, on any tree, without opening a PDF.
    """
    nums = NUM_TOKEN_RE.findall(text)
    if not nums:
        return "clean", {}
    corrupt = sum(1 for n in nums if "�" in n)
    long_runs = [n for n in nums if digit_count(n) >= MERGE_MIN_DIGITS]
    shaped = sum(1 for n in long_runs if merge_shaped(n))
    merged = shaped if confirmed_merges is None else confirmed_merges
    per1k = _ratio(corrupt + merged, len(nums)) * 1000
    ev = {
        "numeric_tokens": len(nums),
        "fffd_in_number": corrupt,
        "merged_cell_runs": merged,
        "bad_per_1k_numbers": round(per1k, 2),
        # Retained so a reader can see what the old rule counted, and what the
        # shape test rejected out of it.
        "long_runs": len(long_runs),
        "merge_shaped_runs": shaped,
        "merge_source": "text" if confirmed_merges is None else "geometry",
    }
    if corrupt > 3 or merged > 5:
        return "garbled", ev
    if corrupt or merged:
        return "suspect", ev
    return "clean", ev


def check_matra_damage(text: str) -> tuple[str, dict]:
    """Devanagari vowel-sign corruption (the `गाउँपाललका` class)."""
    dev_n = len(DEV.findall(text))
    # Count this exact shape over the whole transcript. It is sparse enough for
    # the aggregate matra rate to dilute it, and `_sample` can omit a hit in a
    # long report entirely; either would let a known malformed conjunct score
    # clean.
    malformed_conjunct_ra = len(MALFORMED_CONJUNCT_RA.findall(text))
    if dev_n < 200 and not malformed_conjunct_ra:
        return "clean", {}
    sample = _sample(text)
    # 🛑 These three come from `likhit.devanagari`, and that is a fix for a measured
    # defect rather than a tidy-up. This module used to define its own copies, and the
    # orphan one omitted U+093C NUKTA from its lookbehind, so a matra following a
    # DECOMPOSED nukta consonant read as orphaned. The converter's copy already carried
    # the fix and could not reach here across the repository boundary.
    #
    # ⚠️ Corpus effect, measured rather than asserted: 20 false positives removed across
    # 9 documents, out of 483,095 orphan-matra counts, and **no verdict moves**. The
    # defect is real and provable on a unit test; its corpus impact is negligible because
    # this corpus is mostly NFC-precomposed, which the old class already spanned.
    doubled = len(DOUBLED_MATRA_PATTERN.findall(sample))
    orphan = len(ORPHAN_MATRA_PATTERN.findall(sample))
    bad_virama = len(VIRAMA_MATRA_PATTERN.findall(sample))
    dsample = max(1, len(DEV.findall(sample)))

    def per1k(n: int) -> float:
        return round(_ratio(n, dsample) * 1000, 2)

    ev = {
        "doubled_matra": doubled,
        "orphan_matra": orphan,
        "virama_then_matra": bad_virama,
        "malformed_conjunct_ra": malformed_conjunct_ra,
        "doubled_per_1k_dev": per1k(doubled),
        "orphan_per_1k_dev": per1k(orphan),
    }
    if dev_n < 200:
        return "suspect", ev
    score = _ratio(doubled + orphan + bad_virama, dsample) * 1000
    if score > 25:
        return "garbled", ev
    if score > 8 or malformed_conjunct_ra:
        return "suspect", ev
    return "clean", ev


def check_mojibake(text: str) -> tuple[str, dict]:
    sample = _sample(text)
    hits = {m: sample.count(m) for m in MOJIBAKE_MARKERS if m in sample}
    total = sum(hits.values())
    ctrl = sum(
        1
        for ch in sample[:100_000]
        # 🛑 KNOWN BLIND SPOT: this counts `Cc` -- C0/C1 controls -- and NOT `Co`, the
        # private-use area. Undecoded legacy fonts in this corpus land in PUA, so this axis
        # cannot see the single commonest glyph-mapping signature it has. Measured: 40 PUA
        # glyphs appended to a clean document leave it `clean` with `control_chars: 0`, while
        # 40 C1 controls make it `suspect`.
        #
        # Widening the class is a measured TRADE, not a refactor -- it moves a large number of
        # documents at once and needs pricing against the other axes first -- so it is
        # deliberately out of scope here. Recorded so a reader of this function cannot mistake
        # silence for coverage.
        if unicodedata.category(ch) == "Cc" and ch not in "\t\n\r"
    )
    per1k = _ratio(total, max(1, len(sample))) * 1000
    ev = {"markers": hits, "control_chars": ctrl, "per_1k_chars": round(per1k, 2)}
    # Thresholds re-cut after calibration: the affected band's 10th percentile is
    # ~56 per 1k, so the old >1.0 cut left a dead zone, and "any marker at all"
    # made suspect fire on a single stray char (43.8% of that band was usable).
    if per1k > 5.0 or ctrl > 50:
        return "garbled", ev
    if per1k > 0.2 or total >= 3 or ctrl:
        return "suspect", ev
    return "clean", ev


def check_structure(text: str, dev_n: int, lat_n: int) -> tuple[str, dict]:
    """Does this read like an actual OAG audit report?"""
    sample = _sample(text)
    has_letterhead = bool(
        re.search(r"महालेखापरीक्षक|Auditor\s*General|लेखापरीक्षण", sample)
    )
    has_year = bool(re.search(r"२०[०-९]{2}|20[0-9]{2}", sample))
    digits = len(re.findall(r"[0-9०-९]", sample))
    words = len(re.findall(r"\S+", sample))
    ev = {
        "letterhead": has_letterhead,
        "fiscal_year": has_year,
        "digits": digits,
        "words": words,
    }
    if words < 100:
        return "garbled", ev
    # Real reports are dense with numbers (amounts, para refs, dates).
    if not has_letterhead and not has_year:
        return "garbled", ev
    if not has_letterhead or digits < 20:
        return "suspect", ev
    return "clean", ev


def check_repetition(text: str) -> tuple[str, dict]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 20:
        return "clean", {"lines": len(lines)}
    c = Counter(lines)
    top, topn = c.most_common(1)[0]
    dup_ratio = _ratio(topn, len(lines))
    uniq_ratio = _ratio(len(c), len(lines))
    ev = {
        "lines": len(lines),
        "unique_ratio": round(uniq_ratio, 3),
        "top_line_share": round(dup_ratio, 3),
        "top_line": top[:60],
    }
    if uniq_ratio < 0.10 or dup_ratio > 0.5:
        return "garbled", ev
    if uniq_ratio < 0.30 or dup_ratio > 0.25:
        return "suspect", ev
    return "clean", ev


def check_spacing(text: str) -> tuple[str, dict]:
    # Markdown table pipes are single-character tokens, and an explicit blank
    # cell renders as a bare `|`. A wide table therefore looks exactly like
    # per-character-spaced garble to this check: measured on
    # `11747__तामाकोशी गाउँपालिका`, 32,949 of its single-char tokens are `|`,
    # which alone carries it from 0.037 to 0.551 and flips it to `garbled`.
    # `check_legacy_ascii` already drops fence delimiters and pipes for the same
    # reason; this applies the same treatment rather than inventing a new rule.
    # Stripping runs BEFORE sampling: this check used to sample first, which is
    # the only reason it escaped the fence-deletion bug `strip_fences` describes,
    # and "escapes it when the sample happens to cut mid-fence" is not a rule.
    sample = _sample(strip_fences(text)).replace("|", " ")
    toks = re.findall(r"\S+", sample)
    if len(toks) < 100:
        return "clean", {}
    single = sum(1 for t in toks if len(t) == 1)
    huge = sum(1 for t in toks if len(t) > 45)
    ev = {
        "tokens": len(toks),
        "single_char_share": round(_ratio(single, len(toks)), 3),
        "very_long_tokens": huge,
    }
    # "म ह ा ल े ख ा" -> nearly all tokens length 1.
    if _ratio(single, len(toks)) > 0.55:
        return "garbled", ev
    if _ratio(single, len(toks)) > 0.35 or _ratio(huge, len(toks)) > 0.05:
        return "suspect", ev
    return "clean", ev


RANK = {"clean": 0, "suspect": 1, "garbled": 2}

#: The geometry-oracle verdicts that mean "this really was two cells". This is the vocabulary a
#: caller filters oracle rows by to build the COUNT it passes as `confirmed_merges` -- see
#: `test_the_geometry_oracle_path_is_reachable_across_every_band`, which exercises that path.
#: The corpus-side tool that produces those rows is `verify_numeric_merges.py`, which lives
#: outside this package; this constant is for whoever consumes its output.
ORACLE_MERGE_VERDICTS = frozenset({"merge_rule", "merge_gap"})


def check_page_refusal(text: str) -> tuple[str, dict]:
    """VOL-560 item 1: is any single PAGE of this document a refusal?

    The one check here that is not scored on the document. Every other check divides
    by the whole body, which is why `11356` certified `clean, failing: []` while its
    page 5 recovered no figures: 845 refusal chars inside 14,703 clear every floor a
    document-grain threshold can sanely carry. `dev_ratio 0.989` is not wrong, it is
    just not an answer to "did every page deliver".

    Runs on the UNSTRIPPED text, because `strip_page_anchors` is what removes the only
    marker of where a page begins. `audit_one` therefore calls this before stripping;
    reordering the two lines silently restores the document grain and the vacuity with
    it, which is what the VOL-560 mutation arm reverts to reproduce today's defect.

    A refusal is `garbled`, not `suspect`: the page is not damaged text, it is absent
    text wearing the shape of a table.
    """
    _preamble, pages = split_pages(text)
    if not pages:
        # No anchors, so there is no page grain to score. Reported as undetermined
        # rather than clean: `verify_page_coverage` handles an unanchored transcript
        # the same way, and a silent `clean` here would be a second false empty.
        return "clean", {
            "pages": 0,
            "undetermined": True,
            "why": "no likhit page anchors; nothing to score per page",
        }
    results = {page: classify_page(body) for page, body in pages.items()}
    refused = {page: r for page, r in results.items() if r["verdict"] != "delivered"}
    evidence = {
        "pages": len(pages),
        "refusal_pages": sorted(refused),
        "refusal_page_count": len(refused),
        "terms_fired": sorted({t for r in refused.values() for t in r["terms"]}),
        "detail": {
            str(page): {
                k: r[k]
                for k in (
                    "terms",
                    "data_cells",
                    "placeholder_cells",
                    "placeholder_cell_share",
                    "prose_placeholders",
                    "placeholder_per_kchar",
                    "chars",
                    "examples",
                )
            }
            for page, r in sorted(refused.items())
        },
    }
    return ("garbled" if refused else "clean"), evidence


def audit_text(
    text: str,
    *,
    confirmed_merges: int | None = None,
    repha_extended: bool = False,
    page_refusal: bool = False,
) -> dict:
    """Every axis over one transcript's text, plus the verdict they combine to.

    Pure: no filesystem, no corpus schema, no sidecar. That separation is the reason this
    is a library function at all -- the version this was extracted from also opened a
    ``.json`` sidecar beside the document and read ``bucket``/``fiscal_year``/``province``
    from it, which is the OAG corpus's shape and no business of a general audit. A caller
    that has such metadata joins it on afterwards.

    ``page_refusal`` is opt-in, as it was: turning a gate on by default is a decision for
    whoever is cutting a generation, not for the tool.

    The verdict is the **worst** axis, not an average. An axis reporting ``garbled`` is
    making a positive claim about damage, and averaging lets seven clean axes bury it --
    which is exactly how a real defect stayed invisible on this corpus while 6,004
    documents scored clean.
    """

    # ⚠️ Scored BEFORE normalisation, which is the line that destroys the page grain.
    page_refusal_check = check_page_refusal(text) if page_refusal else None

    # Page anchors, the blank runs they leave, and redaction placeholders are all markers
    # this package inserted. Their Latin text scales with page count and redaction density,
    # so scoring them inflates the very ratios `legacy_ascii` and `spacing` key on. See
    # `likhit.quality.normalise` for the two defects that came of not doing this.
    text = normalise_for_audit(text)

    dev_n, lat_n = len(DEV.findall(text)), len(LATIN.findall(text))
    checks = {
        "legacy_ascii": check_legacy_ascii(text, dev_n, lat_n),
        "mojibake": check_mojibake(text),
        "repha_loss": check_repha_loss(text, extended=repha_extended),
        "numeric_damage": check_numeric_damage(text, confirmed_merges),
        "structure": check_structure(text, dev_n, lat_n),
        "repetition": check_repetition(text),
        "spacing": check_spacing(text),
        # Kept last and advisory-only: precision is high (0.986) but 96.6% of its
        # hits are already caught by `mojibake`, and its sole-cause contribution
        # is 37 files. Retained because it independently detects visual/glyph
        # reading order (pre-base ि emitted before its consonant).
        "matra_damage": check_matra_damage(text),
    }
    if page_refusal_check is not None:
        checks["page_refusal"] = page_refusal_check

    verdict = max((v[0] for v in checks.values()), key=lambda s: RANK[s])
    failing = sorted(
        [k for k, v in checks.items() if v[0] != "clean"],
        key=lambda k: -RANK[checks[k][0]],
    )
    return {
        "verdict": verdict,
        "failing": failing,
        "chars": len(text),
        "devanagari": dev_n,
        "latin": lat_n,
        "dev_ratio": round(_ratio(dev_n, max(1, dev_n + lat_n)), 3),
        "checks": {k: {"verdict": v[0], **v[1]} for k, v in checks.items()},
    }
