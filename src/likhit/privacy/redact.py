"""Redact citizenship numbers and dates of birth from one transcript's text.

Scope is deliberately narrow -- the two classes adjudicated as unambiguously personal and
of no research value. Mobile numbers, personal PANs and email addresses are disclosed
rather than removed, because their adjudication left undecided cases and over-redaction
costs more than it saves.

**Anchored on the label, replacing only the value.** A bare digit run can never match, so a
financial table cannot be touched. This is the guard that matters: this project's redaction
history is 572 real hits against **11,215 non-secret values clobbered**, and a separate rule
destroyed 33 audit values.

The three guards inside :func:`redact` are each a measured bound, not a guess -- see their
comments. Every replacement is journalled with its *shape* so precision is measured rather
than asserted; the journal records no matched digits, only lengths and classes.

This module redacts a *string* and writes nothing. The tree-level discipline that has to
surround it -- never redact in place, rewrite only what the journal names, normalise for
matching only -- is in :mod:`likhit.privacy.tree`. Each of those three rules exists because
it was once broken: normalising for output as well as for matching rewrote 308 PII-free
documents while the journal reported zero changes for them.
"""

from __future__ import annotations

import re
from collections import Counter

from ..devanagari import ANY_DIGITS as AD
from .placeholders import CITIZENSHIP, DATE_OF_BIRTH


# label ... value  — the label is kept, only group("val") is replaced.
#
# The label alternation covers the forms actually present in this corpus, found
# by measuring the intervening text over all 12,310 label occurrences rather
# than by guessing: a case suffix (`नागरिकताको`), the certificate wording
# (`नागरिकता प्रमाणपत्र नं.`) and the abbreviation (`ना.प्र.नं`).
#
# It deliberately does NOT reach across `सिफारिस` ("recommendation"), which is
# a municipal *service category* whose adjacent number is a count of services
# delivered, nor across a `|` cell boundary. Those two account for ~2,650 of the
# occurrences and none of them is an identifier.
_CIT_LABEL = (
    r"(?:नागरिकता(?:को|मा|का|की)?\s*(?:प्रमाणपत्र|प्र\.?)?\s*(?:नम्बर|नं\.?|न\.?)?"
    r"|ना\.?\s*प्र\.?\s*नं\.?)"
)
# 🛑 The danda U+0964 is a FIELD SEPARATOR inside these values, not sentence punctuation,
# and leaving it out was a measured privacy defect rather than a cosmetic gap. Nepali writes
# a date as `२०२०।०४।०१` -- danda between the digit groups -- so a class listing `- / .` and
# stopping there truncates the value window at the first separator, and `VALUE_FORBIDDEN`
# below, built as the complement of the same set, then reads the danda as surrounding prose
# and refuses the replacement outright. Both arms fail toward *keeping* the value.
#
# Measured over the 6,235 transcripts of the published corpus, this class against the one
# that omits the danda: **92 dates of birth in 40 documents and 27 citizenship numbers in 11
# documents** -- 49 documents -- kept a named private individual's identifier in public text
# for this reason alone. `जन्म मिति २०२०।०४।०१` is a verbatim example.
DANDA = "\u0964"

#: Every separator a date value may contain between its digit groups. One fragment, used by
#: the pattern and by :data:`VALUE_FORBIDDEN`, because those sites are only correct
#: *relative to each other*: the guard is the complement of the class, so a separator added
#: to one and not the other reintroduces exactly the refusal described above.
_DOB_SEPARATORS = rf"\-/\.\s{DANDA}"

#: Citizenship numbers deliberately do NOT admit `.`, unlike dates. Measured on the same
#: 6,235 transcripts: admitting it changes nothing at all -- 0 further values in 0
#: documents -- because `ना.प्र.नं.` puts its dots in the *label*, which the pattern already
#: matches and keeps. Left out so the value window cannot run past a sentence-ending period
#: into prose: a widening with no measured benefit is still a widening.
_CIT_SEPARATORS = rf"\-/\s{DANDA}"

CITIZENSHIP_LABEL_VALUE = re.compile(
    rf"(?P<label>{_CIT_LABEL}\s*[:ः]?\s*)"
    rf"(?P<val>[{AD}][{AD}{_CIT_SEPARATORS}]{{3,24}}[{AD}])"
)
DOB_LABEL_VALUE = re.compile(
    rf"(?P<label>जन्म\s*(?:मिति|दर्ता\s*मिति|दिन)|जन्ममिति)"
    rf"(?P<sep>\s*[:ः]?\s*)"
    rf"(?P<val>[{AD}][{AD}{_DOB_SEPARATORS}]{{5,18}}[{AD}])"
)

# If any of these sits inside the value we are about to remove, refuse: it means
# the window ran past the identifier into surrounding prose or a money column.
#
# Built from the wider of the two separator sets: the guard's job is to catch a window that
# escaped into prose, and a `.` inside a citizenship span is already unreachable because the
# pattern above cannot match one.
VALUE_FORBIDDEN = re.compile(rf"[^{AD}{_DOB_SEPARATORS}]")
AMOUNT_CUE = re.compile(r"रकम|जम्मा|खर्च|बजेट|राजस्व|बेरुजु|रु")


def redact(text: str, journal: list[dict], stats: Counter) -> str:
    """Return redacted text. Appends one journal row per replacement.

    ``journal`` and ``stats`` are mutated rather than returned so a caller walking a corpus
    can accumulate totals across documents; :func:`redact_inline_text` is the wrapper for
    callers that only want one transcript.
    """

    def sub(rx: re.Pattern[str], placeholder: str, kind: str) -> str:
        def _r(m: re.Match[str]) -> str:
            val = m.group("val")
            # Guard 1: the value must be digits and separators only.
            if VALUE_FORBIDDEN.search(val):
                stats[f"{kind}_refused_nondigit"] += 1
                return m.group(0)
            digits = re.sub(rf"[^{AD}]", "", val)
            # Guard 2: plausible identifier length, set from the measured digit
            # distribution rather than guessed. A Nepali citizenship number runs
            # ~9-13 digits, often `XX-XX-XX-XXXXX`; a floor of 7 keeps those and
            # drops the 47 spans of <=6 digits, which are counts beside a service
            # label (`नागरिकता सिफारिस`) and are real audit data worth keeping.
            # The ceiling of 16 drops the five 18-19 digit spans, which are runs
            # of table digits rather than an identifier.
            lo, hi = (7, 16) if kind == "citizenship" else (5, 10)
            if not (lo <= len(digits) <= hi):
                stats[f"{kind}_refused_length"] += 1
                return m.group(0)
            # Guard 3: an amount cue inside the value means we mis-scoped.
            if AMOUNT_CUE.search(val):
                stats[f"{kind}_refused_amount_cue"] += 1
                return m.group(0)
            journal.append(
                {
                    "kind": kind,
                    "label_kept": True,
                    "value_chars_removed": len(val),
                    "value_digit_count": len(digits),
                    "value_had_separators": bool(re.search(r"[-/.\s]", val)),
                }
            )
            stats[f"{kind}_redacted"] += 1
            gd = m.groupdict()
            return m.group("label") + gd.get("sep", "") + placeholder

        return rx.sub(_r, text)

    text = sub(CITIZENSHIP_LABEL_VALUE, CITIZENSHIP, "citizenship")
    text = sub(DOB_LABEL_VALUE, DATE_OF_BIRTH, "date_of_birth")
    return text


def redact_inline_text(text: str) -> tuple[str, list[dict], Counter]:
    """``(redacted, journal, counters)`` for one transcript.

    The public entry point, shaped to match :func:`likhit.privacy.redact_tables.redact_table_text`
    so a caller can drive both passes the same way. :func:`redact` keeps the
    accumulate-into-caller's-collections form because the tree walker wants corpus-wide
    totals, and because it is the form the measured behaviour was calibrated in.
    """

    journal: list[dict] = []
    stats: Counter = Counter()
    return redact(text, journal, stats), journal, stats
