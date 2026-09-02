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
# 🛑 The danda U+0964 is a FIELD SEPARATOR inside these values as well as being the Nepali
# sentence terminator, and leaving it out of the value class was a measured privacy defect
# rather than a cosmetic gap. Nepali writes a date as `२०२०।०४।०१` -- danda between the digit
# groups -- so a class listing `- / .` and stopping there truncates the value window at the
# first separator, and `VALUE_FORBIDDEN` below, built as the complement of the same set, then
# reads the danda as surrounding prose and refuses the replacement outright. Both arms fail
# toward *keeping* the value.
#
# Measured over the 6,234 transcripts of the v19 tree this redactor runs against, this class
# against the one that omits the danda: **+79 dates of birth in 40 documents and +27
# citizenship numbers in 12 documents** kept a named private individual's identifier in
# public text for this reason alone. `जन्म मिति २०२०।०४।०१` is a verbatim example.
#
# ⚠️ Do not read that +27 as disagreeing with the **41** danda-bearing citizenship numbers
# the v1.4 release record reports for the same defect. They are **different instruments**:
# the release pass matches a grouped-identifier *shape*, this one matches a label followed by
# a digit-and-separator window, so the two admit different spans. The document counts -- 40
# and 12 -- do agree, and are the figures worth comparing across the two.
DANDA = "\u0964"

#: The separators a date value admitted before the danda was added, each free to sit anywhere
#: inside the window. Left exactly as they were -- see :func:`_value_window` for why
#: constraining them too would be a regression rather than a tidy-up.
_DOB_LEGACY_SEPARATORS = r"\-/\.\s"

#: Citizenship numbers deliberately do NOT admit `.`, unlike dates. Measured on the same
#: tree: admitting it changes nothing at all -- 0 further values in 0 documents -- because
#: `ना.प्र.नं.` puts its dots in the *label*, which the pattern already matches and keeps.
#: Left out so the value window cannot run past a sentence-ending period into prose: a
#: widening with no measured benefit is still a widening. A subset of the date set, which
#: :data:`VALUE_FORBIDDEN` depends on and a test asserts.
_CIT_LEGACY_SEPARATORS = r"\-/\s"

#: Every non-digit character a value may contain, derived from the widest legacy set so it
#: cannot drift from it. One fragment, shared by :data:`VALUE_FORBIDDEN` and
#: :data:`VALUE_SEPARATOR`, because those sites are only correct *relative to* the value
#: classes: the guard is the complement of the class, so a separator added to a class and not
#: to the guard reintroduces exactly the refusal described above.
_VALUE_SEPARATORS = rf"{_DOB_LEGACY_SEPARATORS}{DANDA}"


def _value_window(legacy_separators: str, lo: int, hi: int) -> str:
    """One value window: a digit, ``lo``--``hi`` inner characters, then a digit.

    🛑 **The danda is admitted only where a digit follows it**, unlike every other separator.
    It is the one separator that is also the sentence terminator, and nothing else constrains
    it to sit between digit groups, so admitting it flatly lets a window leave the identifier,
    cross a sentence boundary and take the next sentence's leading number with it:
    `जन्म मिति २०७७। ३ जना` spans ``२०७७। ३`` and removes a staff count, and
    `जन्म मिति २०५८।०९।१२। ४५ वर्ष` spans ``२०५८।०९।१२। ४५`` and removes an age.

    Neither guard below can see that. ``VALUE_FORBIDDEN`` is built as the complement of the
    same set, so it permits the danda *by construction*, and both spans land inside the
    digit-count window. Both are then journalled as ordinary date-of-birth redactions, so the
    "values removed" total absorbs them silently -- the failure is in the safe direction for
    disclosure but it destroys audit data, and it is invisible in the one record that would
    show it.

    A real date never carries whitespace after its internal danda, which is what makes the
    lookahead free. Measured over the same 6,234 transcripts, against the flat class:
    **identical on every count** -- 88 dates of birth in 48 documents, 83 citizenship numbers
    in 45, +79 and +27 gained -- refusing nothing it admits and admitting nothing it refuses,
    while both spans above stop matching.

    ⚠️ **Only the danda is constrained, and that asymmetry is deliberate.** Applying the same
    lookahead to `- / .` reads tidier and costs a real identifier: it drops
    ``ना.प्र.नं.१९१०/ १५७``, a senior citizen's citizenship number carrying a stray space
    after the slash, which `main` redacts today. Measured, it is the only span the wider form
    changes -- one loss, no gain -- so the legacy separators keep the freedom they had.
    """

    return rf"[{AD}](?:[{AD}{legacy_separators}]|{DANDA}(?=[{AD}])){{{lo},{hi}}}[{AD}]"


CITIZENSHIP_LABEL_VALUE = re.compile(
    rf"(?P<label>{_CIT_LABEL}\s*[:ः]?\s*)"
    rf"(?P<val>{_value_window(_CIT_LEGACY_SEPARATORS, 3, 24)})"
)
DOB_LABEL_VALUE = re.compile(
    rf"(?P<label>जन्म\s*(?:मिति|दर्ता\s*मिति|दिन)|जन्ममिति)"
    rf"(?P<sep>\s*[:ः]?\s*)"
    rf"(?P<val>{_value_window(_DOB_LEGACY_SEPARATORS, 5, 18)})"
)

# If any of these sits inside the value we are about to remove, refuse: it means
# the window ran past the identifier into surrounding prose or a money column.
#
# Built from the widest separator set: the guard's job is to catch a window that escaped into
# prose, and a `.` inside a citizenship span is already unreachable because the pattern above
# cannot match one.
VALUE_FORBIDDEN = re.compile(rf"[^{AD}{_VALUE_SEPARATORS}]")

#: What counts as a separator when the journal records a value's *shape*.
#:
#: 🛑 Derived from the same fragment as the value classes rather than spelled out again. It
#: was spelled out, as ``[-/.\s]``, and admitting the danda made that a **third** place the
#: same set is written down -- so a danda-separated date was journalled
#: ``value_had_separators=False``, which is the one field a reader has to judge whether the
#: span was a grouped identifier or a bare digit run. This pass's precision is measured from
#: its journal rather than asserted, so a shape field that quietly disagrees with the pattern
#: that produced it is worse than no field at all.
VALUE_SEPARATOR = re.compile(rf"[{_VALUE_SEPARATORS}]")
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
                    "value_had_separators": bool(VALUE_SEPARATOR.search(val)),
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
