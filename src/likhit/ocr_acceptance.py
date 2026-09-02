"""Did a vision-OCR response transcribe the page, or decline to transcribe it?

WHY THIS EXISTS. `_run_page_ocr` had four failure legs and every one of them was
mechanical -- the page would not render, the provider raised, the provider set
`error`, the response was empty. A response is otherwise accepted on truthiness
alone and inserted under the page anchor as page text. So a model that answers

    This appears to be a blank page with only a solid green vertical stripe on
    the right side.

is a *success*: its prose becomes the body of a page of a Nepali audit report,
the document carries no `needs-ocr` marker, and nothing downstream can tell the
difference between that and a transcription. `stop_reason`/`error` answer "how
did the call end", not "what did the model say", and a polite decline ends
normally.

THE DISCRIMINATOR IS NOT "NO DEVANAGARI", AND THAT IS THE WHOLE DESIGN. The OAG
corpus genuinely contains English: 18 of its 6,234 published documents hold zero
Devanagari characters and 13 of those score `clean` -- English translations and
executive summaries the Auditor General publishes himself. One document,
`11353__82M-NRA_REPORT_2020_Summarized`, is an English publication delivered
entirely by OCR: ALL 20 of its pages carry zero Devanagari, and their 38,586
characters are the whole document. A shape-only test ("too little Devanagari to
be a transcription") deletes all of it. So low Devanagari is *necessary* and not
sufficient: the page must also SAY that it was not transcribed.

(That figure was first written here as "60 pages / 116,250 characters", which was
OCR *rows* and their summed lengths -- three shards re-emitted the same 20 pages,
so it triple-counted. 20 pages is the distinct-page count and 38,586 the sum over
distinct pages. The conclusion is unchanged and in fact sharper: it is not part of
a document, it is the entire document.)

FOUR LEGS, because a decline has four observed shapes. A, B and C are ported from
the OAG corpus tooling's `ocr_refusal.py`, where each threshold was placed against
a 335,132-page negative control and each false-positive family it removed is
named; D is that tooling's separate blank-assertion pre-check, folded in here so
one call answers the whole question:

  declined  <=>  A: devanagari share < DEV_RATIO_FLOOR  AND an abstention
                    family fires near a reference to the task's own artifact
            OR   B: placeholder share of populated table cells >= 0.25
            OR   C: an abstention family fires AND the page opened a table it
                    never put a data row in
            OR   D: devanagari share < DEV_RATIO_FLOOR  AND the response asserts
                    the page is blank

Leg A alone has two measured false negatives, which is why B and C exist: a
decline written *in Nepali* can score 0.67 Devanagari because its Devanagari is
the word "unclear" repeated, and a decline that emits a column header plus its
separator and no data rows can score 0.18 -- both far above any floor a real
page could survive. Leg D exists because the commonest damage phrase of all --
"This appears to be a blank page with only a solid green vertical stripe on the
right side" -- names no image, scan or transcription, so it fires no
task-frame-guarded family and leg A cannot reach it.

WHAT IS DELIBERATELY NOT PORTED. The corpus tool's third verdict,
`no_devanagari_unexplained` -- low Devanagari with *no* abstention wording. There
it fails closed and a human adjudicates from a ledger. likhit has no ledger and
no human in the loop, and the population that lands in that bucket is exactly
the legitimate English above, so here it is DELIVERED. Refusing it would be the
regression, not the fix.

EVERY DEVANAGARI PATTERN IS WRITTEN WITH `\\uXXXX` ESCAPES, never pasted
literals. A pasted class is normalization-fragile in two distinct ways that have
both bitten this repo: a range whose endpoint is a composed character can end on
a lower code point than it starts and raise `re.error` at import -- taking the
whole module with it -- and a set of composed characters can silently widen to
their base consonants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from likhit.quality.page_refusal import (
    MIN_DATA_CELLS,
    PLACEHOLDER_CELL_SHARE_FLOOR,
    data_cells,
    is_placeholder_cell,
)

#: Devanagari block including the Devanagari digits U+0966-U+096F.
_DEVANAGARI = re.compile(r"[\u0900-\u097f]")
_LATIN = re.compile(r"[A-Za-z]")

#: Below this share of Devanagari among the script-bearing letters, the response
#: is not a transcription of a Devanagari page. Placed in a measured gap: the
#: hand-adjudicated declines of the corpus population occupy 0.0000-0.0078 and
#: its lowest legitimately Devanagari-bearing page is 0.1076, an English
#: bibliography page of an audit journal. 0.05 is 6.4x above the highest decline
#: and 2.2x below the lowest real transcription.
DEV_RATIO_FLOOR = 0.05

#: How far apart an abstention and a reference to the task's artifact may sit and
#: still be treated as one statement. Co-occurrence anywhere on the page is too
#: weak -- a 3,000-character page can hold an unrelated "image" a long way from
#: an unrelated "I cannot".
TASK_FRAME_WINDOW = 200

#: Abstention families. Named rather than merged into one alternation so a caller
#: can report WHICH family fired: a detector that cannot say why it fired cannot
#: be audited or ablated.
#:
#: EVERY ENGLISH PATTERN CARRIES ITS OWN SUBJECT. A draft that matched bare
#: modality -- `(?:cannot|unable)\W+...(?:read|provide)` -- scored 41 false
#: positives over the negative control, because audit English is full of
#: third-party inability: "cannot be identified", "has not produced the
#: accounts", and the standard ISA disclaimer "unable to obtain sufficient
#: appropriate audit evidence". Modality is not abstention. Only the assistant
#: declining, or addressing the user, is -- hence a literal `I` subject or a
#: second-person request in every English alternative.
ABSTENTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "first_person_inability": re.compile(
        r"\bI\s*(?:'|’)?\s*(?:m|am|was|have\s+been)?\s*"
        r"(?:cannot|can\s?not|can(?:'|’)t|unable|not\s+able|"
        r"could\s+not|couldn(?:'|’)t)\b",
        re.IGNORECASE,
    ),
    "conditional_capability": re.compile(
        r"\bI\s*(?:'|’)?\s*(?:d|ll|would|will)\s+"
        r"(?:need|require|be\s+able|have\s+to)\b",
        re.IGNORECASE,
    ),
    "direct_request": re.compile(
        r"\b(?:could|can|would|will)\s+you\b"
        r"|\bplease\s+(?:\w+\s+){0,2}?"
        r"(?:rotat\w*|resubmit\w*|re-?send\w*|re-?upload\w*|upload\w*|"
        r"provid\w*|shar\w*|suppl\w*|re-?scan\w*|correct\w*)\b"
        r"|\bif\s+you\s+(?:can|could|are\s+able\s+to)\s+(?:\w+\s+){0,2}?"
        r"(?:provid\w*|shar\w*|rotat\w*|upload\w*|re-?scan\w*|send\b)",
        re.IGNORECASE,
    ),
    "resubmission": re.compile(r"\bre-?submit\w*\b", re.IGNORECASE),
    "describe_instead": re.compile(
        r"\b(?:let\s+me|I\s*(?:'|’)?\s*(?:ll|can|will))\s+"
        r"(?:instead\s+)?describ\w+",
        re.IGNORECASE,
    ),
    "apology": re.compile(
        r"\bI\s+apolog(?:ise|ize)\w*\b|\bI\s*(?:'|’)?\s*(?:m|am)\s+sorry\b",
        re.IGNORECASE,
    ),
    "difficulty_as_decline": re.compile(
        r"\b(?:difficult|impossible)\s+to\s+(?:\w+\s+){0,3}?"
        r"(?:transcrib\w*|read\b|decipher\w*)",
        re.IGNORECASE,
    ),
    # ---- The Nepali families. ------------------------------------------------
    # These CANNOT carry the English guard. The English families all pivot on a
    # literal `I` subject or a second-person request, because English marks the
    # speaker. Nepali marks it on the verb and a decline here is impersonal --
    # U+092A U+0922 U+094D U+0928 U+0938 U+0915 U+093F U+090F U+0928 is "could
    # not be read", with no subject at all. So the subject test is unavailable
    # and the task frame is the ONLY guard these have.
    "np_not_readable": re.compile(
        r"\u092a\u0922\u094d\u0928\s*"
        r"(?:\u0938\u0915\u093f\u090f\u0928|\u0938\u0915\u093f\u0928"
        r"|\u0938\u0915\u093f\u0901\u0926\u0948\u0928"
        r"|\u0938\u0915\u093f\u0928\u0947\s*\u0905\u0935\u0938\u094d\u0925\u093e\u092e\u093e"
        r"\s*(?:\u0928\u0930\u0939\u0947\u0915\u094b|\u091b\u0948\u0928))"
    ),
    "np_not_identifiable": re.compile(
        r"\u092a\u0939\u093f\u091a\u093e\u0928\s*\u0917\u0930\u094d\u0928\s*"
        r"(?:\u0938\u0915\u093f\u090f\u0928|\u0938\u0915\u093f\u0901\u0926\u0948\u0928"
        r"|\u0938\u092e\u094d\u092d\u0935\s*(?:\u092d\u090f\u0928|\u091b\u0948\u0928))"
    ),
    "np_illegible": re.compile(
        r"\u0905\u092a\u0920\u0928\u0940\u092f"
        r"|\u092a\u0920\u0928\s*\u092f\u094b\u0917\u094d\u092f\s*\u091b\u0948\u0928"
    ),
    "np_transcription_precondition": re.compile(
        r"\u091f\u094d\u0930\u093e\u0928\u094d\u0938\u0915\u094d\u0930\u093f\u092a\u094d\u0936\u0928"
        r"\w*(?:\s*\S+){0,6}?\s*\u0906\u0935\u0936\u094d\u092f\u0915"
        r"|\u092a\u094d\u0930\u0924\u093f\u0932\u0947\u0916\u0928"
        r"\w*(?:\s*\S+){0,6}?\s*\u0906\u0935\u0936\u094d\u092f\u0915"
    ),
}

#: The input artifact of the transcription task. A decline is the model talking
#: about the PICTURE IT WAS HANDED; a Nepali audit report's own prose has no
#: occasion to.
#:
#: `picture` is deliberately ABSENT -- "a true and fair picture" is ordinary
#: accounting English. On the Nepali side the frame names the ARTIFACT and never
#: its quality: U+0917 U+0941 U+0923 U+0938 U+094D U+0924 U+0930 ("quality")
#: looks like the obvious companion to U+091B U+0935 U+093F ("image") and occurs
#: on 48,485 pages of the negative control, because "quality audit service" is in
#: the Auditor General's own mission statement. And no abstention term may appear
#: here: a family whose guard contains its own vocabulary is guarded by an
#: identity.
TASK_FRAME = re.compile(
    r"\b(?:image|images|scan|scans|scanned|scanning|screenshot\w*|"
    r"photo\w*|resolution|orientation|upside[-\s]?down|mirror\w+|rotat\w+|"
    r"blurr\w+|legib\w+|illegib\w+|transcrib\w+|transcription\w*|OCR)\b"
    r"|(?:\u091b\u0935\u093f|\u0938\u094d\u0915\u094d\u092f\u093e\u0928"
    r"|\u0918\u0941\u092e\u093e\u0907\w*|\u0918\u0941\u092e\u0947\u0915\u094b"
    r"|\u0918\u0941\u092e\u093e\u0907\u090f\u0915\u094b"
    r"|\u091f\u094d\u0930\u093e\u0928\u094d\u0938\u0915\u094d\u0930\u093f\u092a\u094d\u0936\u0928\w*"
    r"|\u0930\u093f\u091c\u094b\u0932\u094d\u092f\u0941\u0938\u0928"
    r"|\u0905\u092d\u093f\u092e\u0941\u0916\u0940\u0915\u0930\u0923)",
    re.IGNORECASE,
)

#: A bracketed slot whose CONTENT says the transcriber could not read it. This is
#: a self-declared non-transcription: the model emitted a marker where a value
#: belongs, so neither vocabulary nor a script statistic is needed.
#:
#: THE BRACKET IS THE ENTIRE DISCRIMINATOR, and that is measured. Over the
#: negative control the bare word U+0905 U+0938 U+094D U+092A U+0937 U+094D
#: U+091F ("unclear") occurs 652 times on 469 pages of genuine audit prose; the
#: same word *inside brackets* occurs 0 times. A real report discusses ambiguity;
#: only a transcriber writes it where a number should be.
#:
#: Content words are deliberately absent -- "blank"/"empty"/"unknown"/"missing"
#: read inside legitimate figure captions ("four photographs, of empty land and
#: wall construction" is a description of a figure on an otherwise complete page).
_PLACEHOLDER_CELL = re.compile(
    r"\[[^\[\]]{0,80}?(?:"
    r"\u0905\u0938\u094d\u092a\u0937\u094d\u091f"
    r"|\u0905\u092a\u0920\u0928\u0940\u092f"
    r"|\u092a\u0920\u0928\s*\u092f\u094b\u0917\u094d\u092f\s*\u091b\u0948\u0928"
    r"|\u092a\u0922\u094d\u0928\s*\u0938\u0915\u093f\u090f\u0928"
    r"|unclear|illegib\w*|unreadable|not\s+legible|not\s+visible"
    r"|cannot\s+read|can(?:'|’)?t\s+read|indiscernible|obscured"
    r")[^\[\]]{0,80}?\]",
    re.IGNORECASE,
)

#: The model asserting the page is blank rather than transcribing it.
#:
#: THIS ONE IS NOT TASK-FRAME GUARDED, AND THAT IS DELIBERATE. "This appears to
#: be a blank page with only a solid green vertical stripe on the right side" --
#: the exact shape this whole module exists to stop -- contains no word from
#: `TASK_FRAME` at all: no image, no scan, no rotation, no transcription. Putting
#: this pattern in `ABSTENTION_PATTERNS` therefore made it fire and then be
#: discarded for want of a frame, which is how the guard that protects the other
#: families would have silently disabled the one that catches the damage.
#:
#: It is safe unguarded because it is narrow: it matches only an ASSERTION OF
#: BLANKNESS, never mere difficulty. A looser version that also caught "cannot",
#: "this page" or "to transcribe" matched four real pages of an audit journal
#: carrying 2,735-3,187 characters of genuine content, and refusing those would
#: have deleted text the corpus paid for.
#:
#: Measured as a negative control over all 6,234 documents of the published v1.3
#: tree, searched as whole files: `appears to be blank` 0 hits, `blank page` 0
#: hits, `no visible/readable text` 0 hits, `nothing to transcribe` 0 hits. The
#: whole predicate was then run per page body over the same tree -- 332,988
#: non-empty bodies -- and declined 0 of them. No page of the corpus says these
#: things, so nothing legitimate is at risk, `11353`'s 20 English pages included.
ASSERTS_BLANK = re.compile(
    r"appears?\s+to\s+be\s+(?:a\s|an\s)?(?:blank|empty)"
    r"|is\s+(?:a\s)?blank"
    r"|blank/(?:empty|white)"
    r"|blank\s+page"
    r"|blank\s+or\s+(?:empty|contains)"
    r"|no\s+(?:visible|other|readable|actual|discernible)\s+(?:text|content)"
    r"|nothing\s+(?:visible\s+)?to\s+transcribe"
    r"|empty\s+table",
    re.IGNORECASE,
)

#: Share of a page's populated table cells that must be placeholders before the
#: table counts as not transcribed. The base is CELLS, not occurrences, and that
#: is the whole leg: thresholding on occurrences at >= 1 selects genuine full
#: transcriptions that contain one honest gap -- one unreadable cell among 206
#: populated ones, one among 145. A page allowed to mark a single illegible stamp
#: is doing its job.
#:
#: 🛑 Re-exported from `likhit.quality.page_refusal` rather than defined here, and the
#: reason is the same one `likhit/devanagari.py` exists for. Two instruments read this
#: number: THIS module decides whether a model's answer becomes page text, and the audit
#: in `quality.page_refusal` decides whether a page that shipped counts as transcribed.
#: They arrived in this package from opposite directions -- the audit with #107, this
#: module with the v19 extractor line -- each carrying its own `0.25`. A chooser and a
#: grader that disagree about the threshold accept a page they then condemn, which is
#: exactly the drift `774aee4` was written about. `quality.page_refusal` holds the fuller
#: provenance and is the pinned site, so it owns the value. Imported at the top of this
#: module; this block is the derivation, kept here because this is where the value is used.

#: Verdicts. `DELIVERED` is the only one a caller may insert as page text.
DELIVERED = "delivered"
DECLINED = "declined"

#: Which leg produced a `DECLINED`, reported for the same reason the abstention
#: family is.
LEG_SCRIPT_SHARE = "script_share_with_abstention"
LEG_PLACEHOLDER = "placeholder_cell_share"
LEG_NO_SUBSTANCE = "abstention_without_substance"
LEG_ASSERTS_BLANK = "script_share_with_blank_assertion"


@dataclass(frozen=True)
class OcrAcceptance:
    """A verdict plus the evidence that produced it."""

    verdict: str
    legs: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    devanagari_ratio: float = 1.0
    devanagari_chars: int = 0
    latin_letters: int = 0
    placeholder_cell_share: float | None = None
    populated_cells: int = 0
    empty_table_skeleton: bool = False

    @property
    def declined(self) -> bool:
        return self.verdict == DECLINED

    def reason(self) -> str:
        """One line naming the legs and families, for a log or a marker."""
        legs = ",".join(self.legs) or "none"
        families = ",".join(self.families) or "none"
        return (
            f"{self.verdict} legs={legs} families={families} "
            f"dev_ratio={self.devanagari_ratio:.4f}"
        )


def devanagari_ratio(text: str) -> float:
    """Devanagari share of the script-bearing letters in `text`.

    Returns 1.0 when there are no letters of either script, so a page of bare
    table scaffolding or Arabic digits carries no evidence of a decline. The safe
    default for an evidence-free response is to leave it alone rather than to
    manufacture a decline out of an empty denominator.
    """
    dev = len(_DEVANAGARI.findall(text))
    latin = len(_LATIN.findall(text))
    if dev + latin == 0:
        return 1.0
    return dev / (dev + latin)


def _table_lines(text: str) -> list[str]:
    """Markdown table lines: stripped, opening and closing with a pipe."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            out.append(stripped)
    return out


def _is_separator(line: str) -> bool:
    """A markdown header separator: every cell holds only dashes and colons."""
    inner = line.strip().strip("|")
    if not inner:
        return False
    return all(
        cell.strip() and set(cell.strip()) <= set("-:") for cell in inner.split("|")
    )


def populated_cells(text: str) -> list[str]:
    """Non-empty cells of the non-separator table rows of `text`.

    Separator rows are excluded because their cells are scaffolding, and empty
    cells because an unfilled cell is a different failure from a cell filled with
    a declaration of failure. Conflating them would let bare scaffolding read as
    a decline.
    """
    out = []
    for line in _table_lines(text):
        if _is_separator(line):
            continue
        for cell in line.strip("|").split("|"):
            cell = cell.strip()
            if cell:
                out.append(cell)
    return out


def placeholder_cell_share(text: str) -> float | None:
    """Placeholder share of the page's data cells; None when the share is not a measurement.

    🛑 This DELEGATES to `quality.page_refusal` rather than recomputing the statistic,
    and that is the whole point of importing from it. The module imported
    `PLACEHOLDER_CELL_SHARE_FLOOR` so a chooser and a grader could not disagree about
    the threshold -- then rewrote the statistic under it, dropping both companions
    `page_refusal` applies together with that floor. Review measured the consequence on
    two pages of genuine Nepali audit prose, both 100% Devanagari:

      * a 2-cell signature table whose signature is `[अस्पष्ट]` -- share 0.5, DECLINED
        here, not a refusal there, because 2 < MIN_DATA_CELLS;
      * `[अस्पष्ट]` inside a 287-character prose cell among 4 -- share 0.25, DECLINED
        here, share 0.0 there, because `is_placeholder_cell` requires the placeholder to
        DOMINATE the cell rather than merely occur in it.

    A false decline is expensive twice over: the page's transcription is discarded, the
    document is stamped needs-ocr, and the OCR call is paid for again.

    `MIN_DATA_CELLS` returns None rather than 0.0 for a small table, because "the share
    is not a measurement" is exactly what None means here -- the same abstention this
    function already makes for a page with no cells at all.
    """
    cells = data_cells(text)
    if len(cells) < MIN_DATA_CELLS:
        return None
    return sum(1 for cell in cells if is_placeholder_cell(cell)) / len(cells)


def is_empty_table_skeleton(text: str) -> bool:
    """Did the response open a table and then put no data rows in it?

    A table with a header and nothing under it is a page saying "here is the
    shape of what I did not transcribe". Data rows are counted as non-separator
    lines minus separator lines, because each separator implies exactly one
    header line above it.

    Requires at least one separator, so a response that never opened a table is
    never judged by this term -- which matters, because a genuine full delivery
    can be free-form text with no table anywhere on the page.
    """
    lines = _table_lines(text)
    seps = sum(1 for line in lines if _is_separator(line))
    non_seps = len(lines) - seps
    return seps >= 1 and (non_seps - seps) <= 0


def abstention_families(text: str) -> tuple[str, ...]:
    """Which abstention families fire in `text` near the task frame; sorted.

    A family counts only if one of its matches sits within `TASK_FRAME_WINDOW`
    characters of a `TASK_FRAME` reference, so "I was unable to obtain sufficient
    appropriate audit evidence" -- a genuine sentence in a genuine report -- does
    not read as a model declining to transcribe an image.
    """
    found = []
    for name, pattern in ABSTENTION_PATTERNS.items():
        for match in pattern.finditer(text):
            lo = max(0, match.start() - TASK_FRAME_WINDOW)
            if TASK_FRAME.search(text, lo, match.end() + TASK_FRAME_WINDOW):
                found.append(name)
                break
    return tuple(sorted(found))


def classify_ocr_response(text: str) -> OcrAcceptance:
    """Did this vision-OCR response transcribe the page, or decline to?

    Reads the response text and nothing else. Says nothing about transport
    errors, emptiness or how generation stopped: those are separate questions
    `_run_page_ocr` already answers, and folding them in here would make a caller
    unable to tell a decline from a truncation.
    """
    ratio = devanagari_ratio(text)
    families = abstention_families(text)
    share = placeholder_cell_share(text)
    skeleton = is_empty_table_skeleton(text)

    legs: list[str] = []
    # LEG A -- the share of script is too low to be a transcription, AND the page
    # says so. Both halves are load-bearing: shape alone fires on every page of a
    # genuine English publication, and vocabulary alone cannot separate two
    # responses that open with the same sentence and then diverge into a
    # description and a full Devanagari table.
    if ratio < DEV_RATIO_FLOOR and families:
        legs.append(LEG_SCRIPT_SHARE)
    # LEG B -- self-declared: the cells hold markers instead of values. Needs no
    # vocabulary and no script statistic, which is why it is the leg that reaches
    # a decline written in Nepali.
    if share is not None and share >= PLACEHOLDER_CELL_SHARE_FLOOR:
        legs.append(LEG_PLACEHOLDER)
    # LEG C -- an abstention fired and the page opened a table it never filled.
    # Gated ON the abstention: an empty table skeleton by itself is an extraction
    # artifact, not a decline.
    if families and skeleton:
        legs.append(LEG_NO_SUBSTANCE)
    # LEG D -- the response asserts the page is blank. Still gated on the script
    # floor, so a genuine Nepali page that happens to discuss a blank form cannot
    # reach it; not gated on the task frame, because the assertion carries no
    # frame word. See `ASSERTS_BLANK`.
    if ratio < DEV_RATIO_FLOOR and ASSERTS_BLANK.search(text):
        legs.append(LEG_ASSERTS_BLANK)

    return OcrAcceptance(
        verdict=DECLINED if legs else DELIVERED,
        legs=tuple(legs),
        families=families,
        devanagari_ratio=ratio,
        devanagari_chars=len(_DEVANAGARI.findall(text)),
        latin_letters=len(_LATIN.findall(text)),
        placeholder_cell_share=share,
        populated_cells=len(populated_cells(text)),
        empty_table_skeleton=skeleton,
    )


def is_declined(text: str) -> bool:
    """Did the model decline to transcribe this page rather than transcribe it?"""
    return classify_ocr_response(text).declined
