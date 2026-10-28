"""Redact labelled personal identifiers from one transcript's text.

Which classes are removed is decided by :mod:`likhit.privacy.modes`; see
:data:`INLINE_RULES` for the rule-to-tier map.

**Anchored on the label, replacing only the value.** A bare digit run can never match, so a
financial table cannot be touched. This is the guard that matters: this project's redaction
history is 572 real hits against **11,215 non-secret values clobbered**, and a separate rule
destroyed 33 audit values.

🛑 **Every label alternation carries an English form as well as a Nepali one, and that is a
correctness requirement rather than a convenience.** These documents are bilingual, and the
English is not decoration -- it is where the *foreign* parties are written. Page 1 of
``samples/aarop-patra.pdf`` is Nepali prose carrying, in Latin script, one Indian national's
`Passport No. Z4362932`, `Date of Birth: 27 August 1979` and `Mobile Number: 919971766006`.
With Nepali-only labels the Nepali-labelled mobile two clauses earlier was removed and all
three of those were not -- at ``strict``, the widest tier this package offers.

That is worse than an ordinary miss, because it is **systematic and it correlates with the
subject**. A rule that reads only Nepali labels does not fail at random; it fails on exactly
the people whose details were written in English, which in this corpus means foreign
nationals and anyone transcribed from an English-language document. The removal set then has
a shape nobody chose.

⚠️ English labels widen the *label* alternation and nothing else. Every value still runs the
three guards in :func:`_accept`, so widening what counts as a label cannot widen what counts
as a plausible value.

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
from collections.abc import Iterator
from dataclasses import dataclass

from ..devanagari import ANY_DIGITS as AD
from .modes import RedactionMode
from .placeholders import (
    AGE,
    BANK_ACCOUNT,
    CITIZENSHIP,
    DATE_OF_BIRTH,
    PASSPORT,
)


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
    # `ना.प्र.प.नं.` -- the `प` of प्रमाणपत्र, written as its own abbreviated field. Without
    # it the label stops at `ना.प्र.` and the rest has to be crossed as prose, which the gap
    # (one word) will not do.
    r"|ना\.?\s*प्र\.?\s*(?:प\.?\s*)?नं\.?"
    r"|citizenship(?:\s*certificate)?(?:\s*(?:no\.?|number))?)"
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
# public text for this reason alone. `जन्म मिति ####।##।##` is a verbatim example.
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


def _value_window(
    legacy_separators: str, lo: int, hi: int, digit_bounded: str = DANDA
) -> str:
    """One value window: a digit, ``lo``--``hi`` inner characters, then a digit.

    🛑 **The danda is admitted only where a digit follows it**, unlike every other separator.
    It is the one separator that is also the sentence terminator, and nothing else constrains
    it to sit between digit groups, so admitting it flatly lets a window leave the identifier,
    cross a sentence boundary and take the next sentence's leading number with it:
    `जन्म मिति ####। # जना` spans ``२०७७। ३`` and removes a staff count, and
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
    ``ना.प्र.नं.####/ ###``, a senior citizen's citizenship number carrying a stray space
    after the slash, which `main` redacts today. Measured, it is the only span the wider form
    changes -- one loss, no gain -- so the legacy separators keep the freedom they had.
    """

    bounded = "|".join(f"{re.escape(c)}(?=[{AD}])" for c in digit_bounded)
    return rf"[{AD}](?:[{AD}{legacy_separators}]|{bounded}){{{lo},{hi}}}[{AD}]"


#: Words that mean the number beside them is money. Guard 3 refuses a *value* carrying one;
#: :data:`_LABEL_GAP` refuses one in the run between a label and its value. Defined here
#: rather than beside the other guards because the gap needs it first.
AMOUNT_CUE = re.compile(r"रकम|जम्मा|खर्च|बजेट|राजस्व|बेरुजु|रु")

#: Words that mean the number after them belongs to a **different field**, so a label
#: reaching across one has stopped describing its own value.
#:
#: 🛑 Found by measurement, not by reading: against 106 control documents carrying these
#: labels, the widened gap newly removed 20 citizenship values, and the law-journal ones
#: among them were all dates -- `नागरिकता बनाउन इन्कार गरेको र मिति २०७८/०६/२२` scored the
#: date as a citizenship number, and `जन्ममिति झूठो विवरण दिई मिति २०६२।११।२८` scored it as
#: a date of birth. Narrative prose puts a `मिति` between a label and the next number far
#: more often than charge-sheet tables do, which is why the original measurement over the
#: charge-sheet shelf alone did not show it.
#:
#: ⚠️ `मिति` is safe to veto even for the date rules, whose own labels (`जन्म मिति`,
#: `जन्ममिति`) contain the word: the label is consumed before the gap begins, so a `मिति`
#: *inside* the gap is always a second one.
#: ⚠️ `ऐन` and its neighbours are here because a law's year is not an identifier:
#: `नेपाल नागरिकता ऐन, #### / ####` is a citation, and redacting it damages the one thing a
#: law-journal document is read for.
_OTHER_FIELD_CUE = re.compile(
    rf"{AMOUNT_CUE.pattern}|सिफारिस|मिति|गते|जारी|ऐन|नियमावली|धारा|दफा"
)

#: Punctuation a gap may contain freely. The danda is **not** here: it ends a sentence, and
#: a label on the far side of one has stopped describing the number in front of it.
_GAP_PUNCT_CHARS = r":ः,.()/\-–—"
_GAP_PUNCT = rf"[{_GAP_PUNCT_CHARS}]"


#: Everything a label may be separated from its value by.
#:
#: 🛑 **This exists because the detector reaches further than the removers did, and the
#: difference was found-and-left data.** ``signals.LABELLED`` matches a label, then
#: ``[^\n]{0,30}?``, then the digits; these patterns matched a label, optional colon and
#: whitespace, and nothing else. Every value sitting behind a word (`खाता नं. क्रमशः
#: ०१७…`), a comma (`खाता नं, १०४…`), a bracket (`खाता नं. (ZRTKKB…)`) or a long column
#: gap was therefore *counted by the scanner and kept by the redactor* -- which is the
#: defect the platform's gate started refusing documents over on 27 October 2026.
#: Reaching exactly as far as the detector is what makes the two agree: a span either both
#: see or neither does.
#:
#: Three things the gap may never contain, and each is load-bearing:
#:
#: * **a digit** -- otherwise the window skips the value it was anchored to and takes the
#:   *next* number in the sentence,
#: * **a `|`** -- the cell boundary the citizenship label was already written not to cross,
#:   kept here rather than restated,
#: * **another field's cue** (:data:`_OTHER_FIELD_CUE`) -- `खाता नम्बरमा रहेको रकम #,##,###`
#:   is an audit figure, not an account; `नागरिकता सिफारिस ##` is a count of services
#:   delivered; `नागरिकता … मिति ####/##/##` is a date. Guard 3 only inspects the value, so
#:   without this the widened gap would reach straight past the cue to the number behind it.
#: 🛑 **Words on the label's own line; across a line break, whitespace only.** Both halves
#: were measured the hard way. Forbidding the line break outright *lost* 306 bank accounts
#: the old gap had been removing -- in one document 599 -> 440 -- because the corpus wraps
#: immediately after the label (`खाता नं.\n################`). Allowing words after it is
#: worse: a label whose value has just been removed is still sitting there, and a gap that
#: reads on through the next line reaches the NEXT field's number. On a two-line fixture
#: that is `नागरिकता नं.` taking the mobile from the line below -- a redaction pass eating
#: an unrelated identifier, which is the exact failure this module's guards exist for.
#:
#: ⚠️ `[` is excluded for the same reason, one line down: this package's own placeholders
#: start with it, and a second pass over already-redacted text would otherwise read across
#: `[REDACTED:CITIZENSHIP-NO]` -- 26 characters, no digits, well inside the window -- and
#: take whatever number came after it. Redaction has to be idempotent; re-running the sweep
#: is normal.
#: 🛑 **Punctuation freely, and at most ONE word.** A gap of arbitrary prose reaches the
#: next number in the sentence, and in a law-journal document that number is a date or a
#: statute year rather than an identifier: measured over 106 control documents, an unlimited
#: 30-character gap newly removed 20 citizenship values of which 5 were prose crossings --
#: `नागरिकताको प्रमाणपत्र लिनुभएको छ । आमा <नाम>ले ####।##।##` took a date, and
#: `नागरिकतासम्बन्धी दर्ता किताब ####/###` took a register's year.
#:
#: One word is what the real gaps need: `खाता नं. क्रमशः ###…` ("respectively"),
#: `Account Number भनी ###…` ("stating"). Two words is already a sentence.
#: Units that follow a COUNT, never an identifier. `नागरिकता लिएका ७८९०१२३ जना` is a
#: number of people; the `सिफारिस` veto exists for exactly this class and catches it only
#: when the count is introduced by that one word. Testing what follows the value catches
#: the construction instead of the vocabulary.
_COUNTED_UNIT = r"(?![^\S\n]{0,3}(?:जना|वटा|पटक|प्रतिशत|किस्ता|थान))"

_GAP_SPACE = rf"(?:[^\S\n]|{_GAP_PUNCT})*"
_LABEL_GAP = (
    rf"(?P<sep>{_GAP_SPACE}"
    rf"(?:(?!{_OTHER_FIELD_CUE.pattern})[^\s\[|A-Za-z{AD}{_GAP_PUNCT_CHARS}]{{1,12}}{_GAP_SPACE})?"
    # ⚠️ Unbounded, because the `\s*` this replaced was. Capping it at two line breaks
    # was an arbitrary number that quietly narrowed the pass: `खाता नं.\n\n\n<digits>` is
    # redacted on main and was kept here. What makes a line break safe to cross is that
    # only WHITESPACE may follow one — a word on the far side is still refused — and that
    # property does not weaken with the third newline.
    rf"(?:\n{_GAP_SPACE})*)"
)


CITIZENSHIP_LABEL_VALUE = re.compile(
    rf"(?P<label>{_CIT_LABEL}){_LABEL_GAP}"
    rf"(?P<val>{_value_window(_CIT_LEGACY_SEPARATORS, 3, 24)})"
    rf"{_COUNTED_UNIT}",
    re.I,
)

#: The date-of-birth label, in both scripts. ``D.O.B`` is admitted with and without its dots
#: because the corpus writes it both ways; `\bdob\b` alone would also match the English word
#: nothing else in these documents uses, so the word boundary is what keeps it safe.
#: 🛑 The **era marker is part of the label**, not something to be crossed as prose.
#: `जन्म मिति वि.स. ####` and `जन्म मिति ई.सं. ####।#।#` name the calendar the date is in
#: -- Bikram Sambat or the Christian era -- and the corpus writes one often enough that a
#: gap which will not cross it leaves whole rosters unredacted: one document went from 8
#: values left to 59 when the gap tightened and the marker had nowhere else to be matched.
#: ⚠️ The Devanagari markers are **alternatives, not a character class**: `वि` is two
#: codepoints (व + ि), so `[विबई]` is the set {व, ि, ब, ई} and matches the vowel sign on its
#: own while matching `वि.स.` not at all.
_DOB_ERA = r"(?:\s*(?:(?:वि|बि|ई|इ)\.?\s*सं?\.?|B\.?\s*S\.?|A\.?\s*D\.?))?"
_DOB_LABEL = (
    r"(?:जन्म\s*(?:मिति|दर्ता\s*मिति|दिन)|जन्ममिति"
    rf"|date\s*of\s*birth|birth\s*date|\bd\.o\.b\.?|\bdob\b){_DOB_ERA}"
)

DOB_LABEL_VALUE = re.compile(
    rf"(?P<label>{_DOB_LABEL}){_LABEL_GAP}"
    rf"(?P<val>{_value_window(_DOB_LEGACY_SEPARATORS, 5, 18)})",
    re.I,
)

#: 🛑 **A birth YEAR on its own, which is the single largest thing the removers were
#: leaving.** `जन्म मिति #### साल` is how this corpus states a date of birth when it states
#: only the year, and it is 100 of the 102 date-of-birth values the platform's gate found
#: still present in text it had already redacted (one roster alone carried 63).
#:
#: ⚠️ It needs its own rule because :func:`_value_window` describes a *grouped* value -- a
#: digit, separators, a digit -- and a bare year has no inner separator to describe. The
#: digit floor moves to 4 with it; see :data:`DIGIT_BOUNDS`.
#:
#: 🛑 **The trailing context is what keeps this from eating a document date.** A four-digit
#: run behind a birth label is a year only when nothing else claims it, so the rule refuses
#: one followed by a danda-or-slash date separator (`जन्म मिति ####।##।##` belongs to
#: :data:`DOB_LABEL_VALUE`, which removes the whole date rather than its first field).
#:
#: ⚠️ Removing it rather than narrowing the scanner is deliberate. The scanner's output is
#: the disclosure published beside the corpus, so teaching it not to *count* a birth year
#: would make the residue number fall without any value leaving the text -- the one failure
#: mode that turns the disclosure into an argument that the redaction worked.
DOB_YEAR_LABEL_VALUE = re.compile(
    rf"(?P<label>{_DOB_LABEL}){_LABEL_GAP}"
    rf"(?P<val>(?<![{AD}{DANDA}/\-.])[{AD}]{{4}}"
    # …and its month, when the document gives one. `जन्म मिति ####।#` and
    # `जन्म मिति ####/#` are year-and-month dates of birth, too short for the window rules
    # (which need five inner characters) and too long for a bare year. They were 24 of the
    # 25 date values still standing after the year rule landed.
    # 🛑 No whitespace after the separator, for the reason :func:`_value_window` gives at
    # length: a real date never carries one, and admitting it spans `जन्म मिति ####। # जना`
    # and removes a staff count.
    rf"(?:[{DANDA}/\-.][{AD}]{{1,2}})?)"
    # Not "no separator may follow" but "no separator that leads to another digit may
    # follow". A trailing full stop is punctuation (`जन्म मिति २००० ..देखेकोले`); a
    # separator with a digit behind it means this four-digit run is the first FIELD of a
    # date, which belongs to the window rules above.
    rf"(?![^\S\n]*[{AD}])(?![^\S\n]*[{DANDA}/\-.][^\S\n]*[{AD}])"
    # 🛑 **A range bound is not a birth date.** `जन्म मिति #### सम्मको विवरण` is "records of
    # births up to 2070" -- the year belongs to the query, not to a person, and there is no
    # individual in the sentence to identify. The window rules never reached these because a
    # bare year was too short for them; a year rule has to say so itself.
    # ⚠️ Each cue must end the word, and `\b` cannot say so in Devanagari: Python's `\w`
    # follows ``str.isalnum()``, which excludes the combining vowel signs, so there is a
    # word boundary *inside* `सम्मको` (after `ो`) and none at all after `देखि`. The
    # distinction still has to be made -- a bare `देखि` matches inside `देखिन्छ` ("is
    # seen"), which is narrative, not a range -- so the test is "no further Devanagari".
    rf"(?![^\S\n]*(?:सम्म(?:को|मा)?|देखि(?:को)?|भन्दा|अघि|पछि|बीच)(?![ऀ-ॿ]))",
    re.I,
)

#: English month names, long and abbreviated. Spelled out rather than matched as "a run of
#: letters" on purpose: :data:`VALUE_FORBIDDEN` refuses any non-digit inside a value, and the
#: only safe way to admit a word into a date is to say exactly which words.
_EN_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?"
    r"|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

#: 🛑 **A second date-of-birth rule, because an English date is not a digit-and-separator
#: window.** `Date of Birth: 27 August 1979` carries a *word* in the middle of the value, so
#: :func:`_value_window` cannot describe it and ``VALUE_FORBIDDEN`` would refuse it even if it
#: could. It shares the ``date_of_birth`` kind, placeholder and tier with
#: :data:`DOB_LABEL_VALUE` -- it is the same class of fact in a different notation, and
#: splitting it into its own kind would make the journal's counts answer a question nobody
#: asks ("how many birth dates were written in English").
#:
#: ⚠️ The four shapes are the ones that occur; a bare `08/1979` is deliberately not among
#: them, because a two-field date is far more often a fiscal period than a birth date.
DOB_ENGLISH_LABEL_VALUE = re.compile(
    rf"(?P<label>{_DOB_LABEL}){_LABEL_GAP}(?P<val>"
    rf"[0-9]{{1,2}}(?:st|nd|rd|th)?\s+{_EN_MONTH}\.?,?\s+[0-9]{{4}}"
    rf"|{_EN_MONTH}\.?\s+[0-9]{{1,2}}(?:st|nd|rd|th)?,?\s+[0-9]{{4}}"
    rf"|[0-9]{{4}}[-/][0-9]{{1,2}}[-/][0-9]{{1,2}}"
    rf"|[0-9]{{1,2}}[-/][0-9]{{1,2}}[-/][0-9]{{4}}"
    rf")(?![0-9])",
    re.I,
)

#: 🛑 **A passport number is the clearest case of a value no digit-only rule can reach.** It
#: opens with a Latin letter -- `Passport No. Z4362932/India` on page 1 of
#: ``samples/aarop-patra.pdf`` -- so the value window, which must begin and end with a digit,
#: cannot describe it, and ``VALUE_FORBIDDEN`` refuses the letter as surrounding prose.
#:
#: The prefix is **part of the identifier** and is removed with it, exactly as
#: :data:`BANK_LATIN_CODE` is for an account number. The trailing `/India` is not, and is
#: kept: it is the issuing country, which is research data rather than an identifier, and the
#: value stops at the last digit.
#: One passport-shaped token: a 1-2 letter issuer prefix and its digits.
_PASSPORT_VALUE = rf"[A-Z]{{1,2}}[{AD}]{{6,9}}(?![{AD}])"

#: 🛑 **What joins a second passport to the same label.** The corpus writes them as a list --
#: `Passport No. Z4362932/India र K9838300/India` -- so the first value is labelled and the
#: second is only reachable through the conjunction. The optional `/India` is the issuing
#: country, which is kept.
#:
#: ⚠️ This connector is **not a label** and must never be reachable on its own: `र` is simply
#: "and" and is one of the commonest words in Nepali. It is only ever matched as a
#: continuation of a match that already began with a real passport label, which is what keeps
#: a cheque or voucher number elsewhere on the page out of reach.
_PASSPORT_CONNECTOR = r"(?:\s*/\s*[^\s,;।/]{1,20})?\s*(?:र|and|&|,)\s*"

#: 🛑 ``val`` spans the **whole list**, connectors and all, and :data:`VALUE_ITEMS` splits it
#: back into the individual numbers. An earlier form gave the list one capture group per
#: value (``val``/``val2``/``val3``) and so had a hard cap: a fourth passport under the same
#: label was left in the file, and *silently* -- nothing downstream could see a value the
#: pattern never captured, so the page carried three visible redactions and one live
#: identifier. The repetition is unbounded here, and the split is what makes that safe.
PASSPORT_LABEL_VALUE = re.compile(
    r"(?P<label>(?:passport|राहदानी)\s*(?:no\.?|number|नं\.?|नम्बर)?)"
    rf"(?P<sep>\s*[:ः]?\s*)"
    rf"(?P<val>{_PASSPORT_VALUE}(?:{_PASSPORT_CONNECTOR}{_PASSPORT_VALUE})*)",
    re.I,
)
#: As :data:`BANK_LATIN_CODE`: stripped before the digits-only guard. It carries ``re.I``
#: because its class is uppercase-only and the pattern above admits a lowercase prefix, so a
#: strip without the flag would leave one behind for guard 1 to refuse as a non-digit, which
#: is a silent miss. (``BANK_LATIN_CODE`` needs no flag: it names both cases outright.)
PASSPORT_LATIN_PREFIX = re.compile(r"^[A-Z]{1,2}", re.I)

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


#: 🛑 A bank account value may open with a short uppercase Latin code -- a branch or product
#: prefix -- and `खाता नं. MM##########` is a real example from the CIAA corpus. It is the one
#: account the digits-only value window missed (1 of 44), and missing it is systematic rather
#: than incidental: the prefix is a bank's convention, so it applies to every account that
#: bank issues. The prefix is part of the identifier and is removed with it.
#:
#: ⚠️ Admitted for **bank accounts only**. :data:`VALUE_FORBIDDEN` is shared, and widening it
#: for every kind would let a citizenship or date window run out of the identifier and into
#: the Latin-transliterated name beside it, which is exactly the escape that guard exists to
#: catch.
#: ⚠️ ``re.I`` here is **load-bearing, not decoration**: :data:`BANK_LABEL_VALUE` carries
#: ``re.I``, which makes its ``[A-Z]{0,4}`` prefix class match lowercase too. Without the same
#: flag here, a lowercase prefix matched the pattern, survived this strip, and was then
#: refused by guard 1 as a non-digit -- a **silent miss**, the pattern and the guard
#: disagreeing about the same bytes.
BANK_LATIN_CODE = re.compile(r"[A-Za-z]{1,6}|,")

#: 🛑 **A bank's product code is not always a prefix, and a long account is not always
#: digits.** `BANK_LATIN_CODE` was written for `खाता नं. MM##########`, where the code
#: opens the value. The live charge-sheet shelf also writes `###FFN#########` (code in the
#: middle), `SA-####-######-####` and `RS-####-######-#####` (code, then a *separator*,
#: which the digit-first window cannot even begin on) and `(ZRTKKB#######)` in brackets.
#: 108 of the 136 bank-account values the gate refused on 27 October 2026 were one of these
#: shapes -- not an edge case, a bank convention, so every account that bank issues is
#: written this way.
#:
#: ⚠️ Admitting Latin runs *inside* the value is a real loosening of guard 1 for this kind,
#: and it is bounded three ways: the run is at most 6 letters, the class admits **no
#: Devanagari letter at all** -- so a window still cannot escape into the Nepali prose
#: around it, which is what guard 1 is there to catch -- and the 6-digit floor plus the
#: amount cue in :data:`_LABEL_GAP` still have to pass.
_BANK_VALUE = rf"[A-Za-z]{{0,6}}[{AD}A-Za-z][{AD}A-Za-z\-/,.]{{2,28}}[{AD}]"

BANK_LABEL_VALUE = re.compile(
    r"(?P<label>(?:खाता|बैंक\s*खाता|account)\s*(?:नम्बर|नं\.?|no\.?|number))"
    rf"{_LABEL_GAP}"
    # 🛑 The digit window is offered FIRST and the alphanumeric shape is the fallback.
    # Alternation is ordered, and leading with `_BANK_VALUE` -- which admits no whitespace --
    # truncated every value that wraps mid-number, so a span the old pattern removed whole
    # came back partly redacted. Measured: 306 accounts lost that way before the reorder.
    # 🛑 `.` joins the bank window as a **digit-bounded** separator, never a free one.
    # `खाता नम्बर १२३४५६७.५२०३८२३` is one account written with a dot, and the citizenship
    # separator set stops at the dot -- so the first half was replaced and the second half
    # left sitting next to the label, which is then exactly what the scanner reports as
    # residue. 21 of the 37 remaining bank values were this one shape. Free admission would
    # let the window run past a sentence-ending period into the next number (`खाता नं.
    # १२३४५६. मिति २०७७`); requiring a digit after the dot makes that unreachable.
    rf"(?P<val>[A-Z]{{0,4}}"
    # `,` joins it for the same reason and with the same constraint. Some banks print the
    # thousands grouping; the digit-bounded form keeps `रु ५,००,०००` unreachable, and
    # leaving it out truncated a value at the comma -- which then looked like a *second*
    # account sitting beside an already-redacted one.
    rf"{_value_window(_CIT_LEGACY_SEPARATORS, 3, 24, DANDA + '.,')}|{_BANK_VALUE})"
    rf"{_COUNTED_UNIT}",
    re.I,
)

#: 🛑 **Age anchors on `उमेर`, never on `वर्ष` alone** -- see :data:`placeholders.AGE` for the
#: measurement. Both orders occur: `उमेर ४५` and `४५ वर्षको उमेर`, so both are matched, and the
#: value group is whichever side carried it.
#:
#: ⚠️ The English `age` joins the **leading-label** branch only, and never the trailing one.
#: `45 years old` would be its mirror, but `years` is `वर्ष`'s own problem in English: a
#: trailing-`years` rule matches a contract term or a period of service exactly as the
#: `वर्ष`-adjacent rule does. `Age:` is the anchor, in both scripts.
AGE_LABEL_VALUE = re.compile(
    rf"(?:(?P<label>उमेर|\bage\b)(?P<sep>\s*[:ः]?\s*)(?P<val>(?<![{AD}])[{AD}]{{1,3}}(?![{AD}]))"
    rf"|(?P<val2>(?<![{AD}])[{AD}]{{1,3}}(?![{AD}]))(?P<label2>\s*(?:वर्ष|बर्ष)\s*(?:को\s*)?उमेर))",
    re.I,
)

#: Plausible identifier length per kind, set from the measured digit distribution rather than
#: guessed. A Nepali citizenship number runs ~9-13 digits, often `XX-XX-XX-XXXXX`; a floor of 7
#: keeps those and drops the 47 spans of <=6 digits, which are counts beside a service label
#: (`नागरिकता सिफारिस`) and are real audit data worth keeping. The ceiling of 16 drops the five
#: 18-19 digit spans, which are runs of table digits rather than an identifier.
#:
#: ``pan`` is 9 digits in Nepal; the window is widened a little either side because the corpus
#: writes them with separators. ``bank_account`` spans several banks' conventions, so its floor
#: is the lowest that is still an account rather than a count. ``age`` is 2-3 digits: a floor of
#: 2 drops single digits, which beside `उमेर` are a child's age in a welfare table far more
#: often than a person being identified, and which a bare `#` is too weak a span to remove on.
#: ``passport`` is 6-9 digits behind a 1-2 letter prefix across the issuers this corpus
#: carries, and the prefix is not counted -- :data:`VALUE_PRESTRIP` removes it before the
#: digits are counted, so the bound describes the digits alone.
#: Years a birth date can plausibly carry, covering both calendars the corpus uses --
#: Bikram Sambat (currently ~2080) and the Christian era. Applied only at four digits,
#: where the value IS the year and there is no second field to corroborate it.
YEAR_RANGE = (1900, 2200)

DIGIT_BOUNDS = {
    "citizenship": (7, 16),
    # The floor is 4 for :data:`DOB_YEAR_LABEL_VALUE` -- a year is the whole value when the
    # document states only a year. No other date rule can produce four digits: both windows
    # require a digit, separators and a digit, so the floor only ever reaches the year rule.
    "date_of_birth": (4, 10),
    # The ceiling is 24 because the corpus holds real 21-digit accounts
    # (`खाता नं.#####################`). At 20 they failed guard 2 and were left whole,
    # which is the worst of both answers: a refusal that keeps the identifier.
    "bank_account": (6, 24),
    "age": (2, 3),
    "passport": (6, 9),
}

#: Text that is **part of a value but is not a digit or a separator**, removed before guard 1
#: in :func:`_accept` sees the value.
#:
#: 🛑 This exists because ``VALUE_FORBIDDEN`` is deliberately absolute -- any non-digit inside
#: a value means the window escaped into prose -- and three kinds legitimately contain one: a
#: bank account's branch prefix, a passport's issuer prefix, and an English date's month name.
#: Each entry names *exactly* what may be stripped, never "letters". A generic letter strip
#: would disable guard 1 for that kind entirely, which is the guard standing between this
#: pass and the 11,215 non-secret values its predecessor clobbered.
VALUE_PRESTRIP: dict[str, re.Pattern[str]] = {
    # Latin runs anywhere, not just the opening one -- see :data:`_BANK_VALUE` for the
    # shapes and for why "letters" is safe to say *here* and nowhere else: the pattern that
    # produced this value admits no Devanagari letter, so what is being stripped can only be
    # the bank's own code. The comma is the thousands grouping some banks print.
    "bank_account": BANK_LATIN_CODE,
    "passport": PASSPORT_LATIN_PREFIX,
    # Month name, ordinal suffix, comma and the abbreviating dot -- an English date's entire
    # non-digit, non-separator content and nothing else.
    "date_of_birth": re.compile(rf"{_EN_MONTH}|st|nd|rd|th|,", re.I),
}

#: Each removal rule, with the **lowest tier that removes it**. The tier lives here rather
#: than in :mod:`likhit.privacy.modes` so that adding a rule is one edit in one file, and so a
#: rule can never exist without a tier deciding when it fires.
#:
#: ⚠️ ``date_of_birth`` appears twice -- the Nepali digit-window form and the English
#: month-name form. Two patterns, one kind, one tier: they are the same fact in two
#: notations. Order within a kind does not matter; the two shapes cannot both match one span.
#:
#: 🛑 ``passport`` sits at ``STRICT`` rather than at ``IDENTIFIERS``, and that is **not** a
#: judgement that it is less personal -- it is plainly an identity-document number of the same
#: class as a citizenship number. ``IDENTIFIERS`` is the scope that was *formally adjudicated*,
#: and that adjudication names two classes. A caller choosing ``identifiers`` is choosing "the
#: scope I was authorised for", so quietly adding a third class to it would break the one
#: promise that tier makes. Moving it is a one-line change here if the scope decision in
#: ``docs/2026-09-25-pii-redaction-pipeline/PLAN.md`` §1 lands that way.
INLINE_RULES = (
    ("citizenship", CITIZENSHIP_LABEL_VALUE, CITIZENSHIP, RedactionMode.IDENTIFIERS),
    ("date_of_birth", DOB_LABEL_VALUE, DATE_OF_BIRTH, RedactionMode.IDENTIFIERS),
    (
        "date_of_birth",
        DOB_ENGLISH_LABEL_VALUE,
        DATE_OF_BIRTH,
        RedactionMode.IDENTIFIERS,
    ),
    # Last of the three date rules, and the order matters here even though it does not
    # within a kind generally: a full date must be offered to the window rules first, or
    # the year rule takes its leading field and leaves the rest.
    ("date_of_birth", DOB_YEAR_LABEL_VALUE, DATE_OF_BIRTH, RedactionMode.IDENTIFIERS),
    # 🛑 **`pan` is deliberately absent at every tier.** A PAN is a *business registration*
    # number and is public by design -- printed on invoices, filed with the registrar,
    # searchable. No pattern can tell a company PAN from a sole trader's, so a rule that
    # removed either would remove both, and removing a contractor's PAN from a procurement
    # finding deletes the one identifier a reader can use to check the claim against the
    # public register. It is **detected and disclosed** instead, by
    # ``signals.LABELLED["pan"]`` -- which is where the pattern and its calibration live, so
    # nothing here is left half-wired. :data:`~likhit.privacy.placeholders.PAN` stays
    # registered so text redacted by the earlier out-of-tree pass is still recognised by
    # ``strip_placeholders`` and ``contains_placeholder``.
    ("bank_account", BANK_LABEL_VALUE, BANK_ACCOUNT, RedactionMode.STRICT),
    ("age", AGE_LABEL_VALUE, AGE, RedactionMode.STRICT),
    ("passport", PASSPORT_LABEL_VALUE, PASSPORT, RedactionMode.STRICT),
)

#: The tiers that remove a given rule, as a set per mode. Derived from :data:`INLINE_RULES`
#: rather than restated, so the two cannot disagree.
INLINE_PATTERNS = tuple(
    (kind, pattern, marker) for kind, pattern, marker, _ in INLINE_RULES
)


@dataclass(frozen=True)
class InlineTarget:
    """One inline value that passed every guard, located in the text it was found in.

    🛑 ``start``/``end`` are offsets of the **value**, not of the whole match: the label is
    kept and only ``text[start:end]`` is removed. A caller that redacts something other than
    text -- :mod:`likhit.privacy.pdf` maps these offsets to glyph rectangles on a PDF page --
    needs the value's own span, and reconstructing it from the match by subtracting the label
    would be a second place the label/value split is written down.
    """

    start: int
    end: int
    kind: str
    digit_count: int
    had_separators: bool

    @property
    def placeholder(self) -> str:
        return dict((kind, marker) for kind, _, marker in INLINE_PATTERNS)[self.kind]


#: The value groups a rule may fill, in order. A pattern uses as many as it needs.
VALUE_GROUPS = ("val", "val2", "val3")

#: For a rule whose ``val`` group spans a **list** of values rather than one, the pattern
#: that finds the individual values inside it.
#:
#: 🛑 This exists because the obvious alternative -- one capture group per list position --
#: has a cap, and overflowing it is a *silent* miss: a fourth passport under one label was
#: simply never captured, so no counter, journal row or verification pass could see it, and
#: the page ended up carrying three visible redactions beside one live identifier. Splitting
#: an unbounded span has no such edge.
#:
#: ⚠️ Each item is still guarded individually by :func:`_accept`, so a list does not buy a
#: value any relief from the digit bounds or the amount cue.
VALUE_ITEMS: dict[str, re.Pattern[str]] = {
    "passport": re.compile(_PASSPORT_VALUE, re.I),
}


def _match_values(match: re.Match[str], kind: str) -> list[tuple[str, int, int]]:
    """Every ``(value, start, end)`` this match carried, in text order.

    🛑 **A match can carry more than one value, for two unrelated reasons, and both are
    handled here rather than at each call site.**

    *Alternation.* :data:`AGE_LABEL_VALUE` matches `उमेर ४५` **and** `४५ वर्षको उमेर`, so its
    value is in ``val`` on one branch and ``val2`` on the other. Exactly one is ever filled.

    *A list under one label.* :data:`PASSPORT_LABEL_VALUE` matches
    `Passport No. Z4362932/India र K9838300/India` -- two identifiers, one label, joined by a
    conjunction. Both are filled, and both must be removed. Reading ``group("val")`` alone
    took the first and left the second in the file, which is the failure mode that looks most
    like success: the page visibly has a redaction on it.

    ⚠️ **The list is matched as a continuation of the label, never as a free-standing shape.**
    A bare `[A-Z]{1,2}` + digits pattern would also find the second passport, and would also
    find cheque numbers, voucher references and the branch-prefixed bank accounts
    :data:`BANK_LATIN_CODE` exists for. The connector is only reachable inside a match that
    already began with a real label, so the label still decides.
    """

    items = VALUE_ITEMS.get(kind)
    found = []
    for name in VALUE_GROUPS:
        if name not in match.re.groupindex:
            continue
        value = match.group(name)
        if value is None:
            continue
        if items is None:
            found.append((value, match.start(name), match.end(name)))
            continue
        # A list span: offsets are relative to the group, so carry its start.
        base = match.start(name)
        for item in items.finditer(value):
            found.append((item.group(0), base + item.start(), base + item.end()))
    return sorted(found, key=lambda item: item[1])


def _rules_for(mode: RedactionMode) -> tuple:
    """The rules ``mode`` removes, in the order they are applied."""

    return tuple(rule for rule in INLINE_RULES if mode.removes(rule[3]))


#: 🛑 **A thousands-grouped amount is not an account number.** `खाता नं. बाट ५,००,०००`
#: reaches guard 3 with no `रु` anywhere in the value, so the amount cue never fires, and
#: the comma this branch admits as a bank separator makes the digits look like one span.
#: The discriminator is the GROUPING: Nepali money is written in groups of two and three
#: (`#,##,###`), while an account number carries a long unbroken run. A value whose
#: longest contiguous run is under six digits is an amount.
#:
#: ⚠️ Bank only. Citizenship and date values are grouped too, and their own digit bounds
#: already describe them.
_MONEY_GROUPED = re.compile(rf"^[{AD}]{{1,3}}(?:,[{AD}]{{2,3}})+(?:\.[{AD}]+)?$")


def _is_amount(val: str) -> bool:
    if not _MONEY_GROUPED.fullmatch(val.strip()):
        return False
    return max((len(run) for run in re.findall(rf"[{AD}]+", val)), default=0) < 6


def _accept(val: str, kind: str, stats: Counter) -> int | None:
    """The three guards. Returns the value's digit count, or ``None`` if any guard refuses.

    Shared by :func:`redact` and :func:`iter_inline_targets` rather than written twice: the
    guards are what make this pass safe, and this project's redaction history is 572 real hits
    against **11,215 non-secret values clobbered**. A second copy that drifted from this one
    would reintroduce that by construction, and ``test_no_duplicated_definitions`` refuses the
    shape anyway.
    """

    # Guard 1: the value must be digits and separators only -- after whatever non-digit
    # content this kind legitimately carries inside the identifier. See VALUE_PRESTRIP.
    prestrip = VALUE_PRESTRIP.get(kind)
    body = prestrip.sub("", val) if prestrip is not None else val
    if VALUE_FORBIDDEN.search(body):
        stats[f"{kind}_refused_nondigit"] += 1
        return None
    digits = re.sub(rf"[^{AD}]", "", val)
    # Guard 2: plausible identifier length -- see DIGIT_BOUNDS.
    lo, hi = DIGIT_BOUNDS[kind]
    if not (lo <= len(digits) <= hi):
        stats[f"{kind}_refused_length"] += 1
        return None
    # Guard 2b: at four digits a date of birth IS its year, and length stops being
    # a test -- `जन्म मिति १२३४` is four digits and no year at all. Only
    # DOB_YEAR_LABEL_VALUE can produce a four-digit date (both window rules need a
    # digit, separators and a digit), so this reaches that rule and nothing else.
    if kind == "date_of_birth" and len(digits) == 4:
        # int() reads Devanagari digits as readily as ASCII ones.
        if not YEAR_RANGE[0] <= int(digits) <= YEAR_RANGE[1]:
            stats[f"{kind}_refused_year"] += 1
            return None
    # Guard 3: an amount cue inside the value means we mis-scoped.
    if AMOUNT_CUE.search(val):
        stats[f"{kind}_refused_amount_cue"] += 1
        return None
    # Guard 3b: …or the value is SHAPED like money even with no cue to say so.
    if kind == "bank_account" and _is_amount(val):
        stats[f"{kind}_refused_amount_shape"] += 1
        return None
    return len(digits)


def iter_inline_targets(
    text: str,
    stats: Counter | None = None,
    mode: RedactionMode = RedactionMode.IDENTIFIERS,
) -> Iterator[InlineTarget]:
    """Every inline value ``mode`` would redact, as a span in ``text``.

    ⚠️ **Offsets are into the text as given**, so unlike :func:`redact` this does not see one
    pattern's replacements while applying the next. The two cannot disagree about *which*
    values qualify -- a citizenship placeholder carries no digits, so it can neither create
    nor destroy a date-of-birth match -- and ``test_targets_agree_with_redact`` pins that
    against the corpus rather than leaving it as an argument.

    ``mode`` defaults to :data:`~likhit.privacy.modes.RedactionMode.IDENTIFIERS` rather than to
    the package default, so that widening the default cannot silently widen what an existing
    caller of this function removes.
    """

    counters: Counter = Counter() if stats is None else stats
    for kind, pattern, _, _ in _rules_for(mode):
        for match in pattern.finditer(text):
            # One match can carry several values -- see `_match_values`. Each is guarded on
            # its own, so one value failing a guard never suppresses its neighbour.
            for val, start, end in _match_values(match, kind):
                digit_count = _accept(val, kind, counters)
                if digit_count is None:
                    continue
                # `_targeted`, not `_redacted` -- see the note in `likhit.privacy.contact`.
                # The `*_refused_*` counters ARE shared with `redact`, because they are the
                # guards' own verdicts and the two must agree on those.
                counters[f"{kind}_targeted"] += 1
                yield InlineTarget(
                    start=start,
                    end=end,
                    kind=kind,
                    digit_count=digit_count,
                    had_separators=bool(VALUE_SEPARATOR.search(val)),
                )


def redact(
    text: str,
    journal: list[dict],
    stats: Counter,
    mode: RedactionMode = RedactionMode.IDENTIFIERS,
) -> str:
    """Return redacted text. Appends one journal row per replacement.

    ``journal`` and ``stats`` are mutated rather than returned so a caller walking a corpus
    can accumulate totals across documents; :func:`redact_inline_text` is the wrapper for
    callers that only want one transcript.

    ``mode`` defaults to ``IDENTIFIERS`` for the same reason
    :func:`iter_inline_targets` does: :mod:`likhit.privacy.tree` and
    :func:`redact_inline_text` call this directly, and a change to the package default must
    not silently change what the tree walker removes.
    """

    def sub(rx: re.Pattern[str], placeholder: str, kind: str) -> str:
        def _r(m: re.Match[str]) -> str:
            # 🛑 The replacement is built by **cutting the accepted value spans out of the
            # match**, right-to-left, rather than by reassembling it from named groups. The
            # reassembling version had to know where the label sat relative to the value --
            # `label + sep + placeholder` for `उमेर ४५`, `placeholder + label2` for
            # `४५ वर्षको उमेर` -- so every new pattern shape needed another branch here, and a
            # match carrying two values could not be expressed at all. Cutting spans is
            # indifferent to all of it: whatever is not an accepted value survives verbatim,
            # which is exactly the "keep the label" rule stated once.
            accepted = []
            for val, start, end in _match_values(m, kind):
                digit_count = _accept(val, kind, stats)
                if digit_count is None:
                    continue
                accepted.append((start, end))
                journal.append(
                    {
                        "kind": kind,
                        "label_kept": True,
                        "value_chars_removed": len(val),
                        "value_digit_count": digit_count,
                        "value_had_separators": bool(VALUE_SEPARATOR.search(val)),
                    }
                )
                stats[f"{kind}_redacted"] += 1
            if not accepted:
                return m.group(0)
            out = m.group(0)
            for start, end in sorted(accepted, reverse=True):
                out = out[: start - m.start()] + placeholder + out[end - m.start() :]
            return out

        return rx.sub(_r, text)

    for kind, pattern, placeholder, _ in _rules_for(mode):
        text = sub(pattern, placeholder, kind)
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
