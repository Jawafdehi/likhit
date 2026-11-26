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
    AADHAAR,
    BANK_ACCOUNT,
    CITIZENSHIP,
    DATE_OF_BIRTH,
    DRIVING_LICENCE,
    FOREIGN_REGISTRATION,
    IDENTITY_CARD,
    IMEI,
    NATIONAL_ID,
    PASSPORT,
    REFUGEE_ID,
    VOTER_ID,
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
    # ⚠️ **The number marker stays OPTIONAL here, unlike in `signals.LABELLED`.** Requiring
    # it looked symmetrical and was measured to be pure loss: `_LABEL_GAP` already refuses to
    # cross `ऐन` or `नियमावली`, so a statute year was never reachable by this rule -- the old
    # digit floor of 7 was redundant protection against a match that does not occur. The one
    # thing the requirement did change was dropping
    # `नागरिकताको प्रमाणपत्रमा २०२४।३।११`, which IS personal data: a second claimed birth
    # date, in a sentence comparing it with the one on an educational certificate.
    #
    # 🛑 So the detector and the remover are deliberately asymmetric for this class, and the
    # direction is the safe one -- the remover reaches slightly further than the scanner
    # reports. The gate in `materials.redaction.residual_classes` asks "what did the scanner
    # still find", so a removal the scanner never counted costs nothing; a detection without
    # a removal is what strands a document.
    # ``प्रशासन`` is what the full label reads as in some charge sheets
    # (``नागरिकता प्रशासन नं ######/##``, ag/60206). Without it the label stopped at
    # ``नागरिकता`` and the one-word gap could not cross ``प्रशासन नं``.
    r"(?:नागरिकता(?:को|मा|का|की)?\s*(?:प्रमाणपत्र|प्रशासन|प्र\.?)?\s*(?:नम्बर|नं\.?|न\.)?"
    r"|citizenship(?:\s*certificate)?(?:\s*(?:no\.?|number))?)"
)

#: The abbreviated certificate label, ``ना.प्र.नं.``, with its own rule
#: (:data:`CITIZENSHIP_ABBREVIATED_LABEL_VALUE`) because it reaches values the spelled-out
#: label must not.
#:
#: ``ना.प्र.प.नं.`` -- the ``प`` of प्रमाणपत्र, written as its own abbreviated field. Without
#: it the label stops at ``ना.प्र.`` and the rest has to be crossed as prose, which the gap
#: (one word) will not do. ``ना.प्र.पत्र नं.`` spells that field out (``पत्र``) and was
#: missed for the same reason: ``प`` matched, then ``त्र`` was neither a marker nor a gap.
#:
#: 🛑 **The label is matched as the PDF delivers it, not as it was typed.** Kathmandu District
#: Court's charge-sheet PDFs (JAW-73) render every ``ना.प्र.नं.`` as ``ना.प्रर्.नं.``: the
#: font's repha glyph lands after the conjunct, so the text layer carries a stray ``र्``.
#: Fourteen citizenship numbers in one charge sheet sat behind that label and none was
#: removed, at ``strict``. The same artefact moves the repha in front of ``नं``
#: (``र्नं.``), and the verdicts (JAW-74) write the marker without its anusvara
#: (``ना.प्र.न.####/####``). Each of those is a tolerated artefact here, and nothing wider:
#: ``र्`` is admitted only in the two places the font puts it.
#:
#: 🛑 **Four more spellings of the same field, all found in published Special Government
#: Attorney charge sheets (JAW-164).** Together they left 105 citizenship numbers in 16
#: charge sheets in public text at ``strict``, 45 of them in one (ag/96611):
#:
#: - ``ना.प्र.प्र.नं.`` -- the प्रमाणपत्र abbreviated as ``प्र.`` rather than ``प.``; nearly
#:   all of the 105. The middle field admitted only ``प``/``पत्र``, so ``प्र`` was neither
#:   and the label stopped short.
#: - ``ना.प्र.न.न.`` -- the marker typed twice, without anusvara.
#: - ``ना.प.प.न.`` -- every ``प्र`` written as a bare ``प``. Admitted only in this fully
#:   dotted form: an undotted ``नापन…`` is an ordinary word stem.
#: - ``अ.ना.नं.`` -- the अंगीकृत (naturalised) citizenship number, again fully dotted, since
#:   a bare ``अना`` begins ordinary words.
CIT_ABBREVIATED_LABEL = (
    r"(?:ना\.?\s*प्र(?:र्)?\.?\s*(?:(?:प(?:्र|त्र)?|न)\.?\s*)?(?:र्)?नं?"
    r"|ना\.\s*प\.\s*प\.\s*नं?"
    r"|अ\.\s*ना\.\s*नं?)"
    # The marker ends the label: a following letter or vowel sign means the ``न`` was the
    # start of the next word (``ना.प्र.नियमावली २०८१``), whose year is not an identifier.
    r"(?![\u0900-\u0963\u0970-\u097f])\.?"
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
#:
#: 🛑 **The en dash and em dash are here too, as hyphens, and leaving them out kept the
#: value.** Word's autocorrect turns a typed ``-`` between digit groups into ``–``, so the
#: Supreme Court's Word orders write accounts as ``खाता नं.१९–००००४–०`` and
#: ``खाता नं ५१–०१–५४२–६३५०१२–०१–०५``. With only ``-`` admitted, the window stopped at the
#: first dash, fell under the digit floor, and the whole number stayed in the published text
#: -- and the scanner, which wants an unbroken run of digits, never reported it either. Found
#: in 25 published court-order transcripts (JAW-49). A dash between digit groups is never
#: sentence punctuation, so it takes the hyphen's freedom rather than the danda's lookahead.
_DOB_LEGACY_SEPARATORS = r"\-–—/\.\s"

#: Citizenship numbers deliberately do NOT admit `.`, unlike dates. Measured on the same
#: tree: admitting it changes nothing at all -- 0 further values in 0 documents -- because
#: `ना.प्र.नं.` puts its dots in the *label*, which the pattern already matches and keeps.
#: Left out so the value window cannot run past a sentence-ending period into prose: a
#: widening with no measured benefit is still a widening. A subset of the date set, which
#: :data:`VALUE_FORBIDDEN` depends on and a test asserts.
_CIT_LEGACY_SEPARATORS = r"\-–—/\s"

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


#: Whitespace and gap punctuation, in any order and any number.
_GAP_SPACE = rf"(?:[^\S\n]|{_GAP_PUNCT})*"


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
_LABEL_GAP = (
    rf"(?P<sep>{_GAP_SPACE}"
    rf"(?:(?!{_OTHER_FIELD_CUE.pattern})[^\s\[|A-Za-z{AD}{_GAP_PUNCT_CHARS}]{{1,12}}{_GAP_SPACE})?"
    # 🛑 **Two line breaks, and the bound is measured.** Across 173 documents, values
    # whose gap to their label is whitespace and punctuation only sit 3,164 on the
    # label's own line, 536 one line below, 10 two lines below, and NONE further. An
    # earlier commit made this unbounded on the strength of one invented three-newline
    # fixture; unbounded buys nothing the corpus contains and costs the thing the gap
    # exists to prevent — in a flattened two-column or page-broken extraction, a label
    # at the foot of one block claiming an unrelated number from the next.
    rf"(?:\n{_GAP_SPACE}){{0,2}})"
)


#: 🛑 **The digit anchor comes FIRST, and without it this veto does not veto.** A bare
#: negative lookahead sits after a backtrackable value window, so the engine answers it by
#: handing one digit back and matching the shorter value — `नागरिकता लिएका ७८९०१२३४५६ जना`
#: came out as `[REDACTED:CITIZENSHIP-NO]६ जना`, which destroys the count AND publishes a
#: stray digit, journalled as an ordinary identifier removal. `(?![AD])` forces the value
#: to its full run, so shortening can no longer satisfy the tail and the whole match fails.
#:
#: ⚠️ The whitespace is unbounded. A `{0,3}` cap let column-aligned output slip the veto —
#: `७८९०१२३४५६    जना` padded to a tab stop was redacted with no check attempted.
#:
#: These are units that follow a COUNT, never an identifier. `नागरिकता लिएका ७८९०१२३ जना`
#: is a number of people; the `सिफारिस` veto exists for exactly this class and catches it
#: only when the count is introduced by that one word. Testing what FOLLOWS the value
#: catches the construction instead of the vocabulary — which is why this paragraph belongs
#: here and not above :data:`_LABEL_GAP`, where it governed what sits BEFORE a value.
_COUNTED_UNIT = rf"(?![{AD}])(?![^\S\n]*(?:जना|वटा|पटक|प्रतिशत|किस्ता|थान))"


CITIZENSHIP_LABEL_VALUE = re.compile(
    rf"(?P<label>{_CIT_LABEL}){_LABEL_GAP}"
    # ⚠️ **The window floor is a CHARACTER count, not a digit count, and it binds first.**
    # `_value_window` builds "a digit, lo-hi inner characters, then a digit", so `lo=3` set a
    # five-character minimum and refused `नागरिकता नं.२३९५` before `DIGIT_BOUNDS` was ever
    # consulted. Lowering only the digit floor therefore changed nothing -- measured, the
    # four short certificate numbers in the CIAA corpus stayed in the text. Both floors are
    # part of the same decision.
    rf"(?P<val>{_value_window(_CIT_LEGACY_SEPARATORS, 2, 24)})"
    rf"{_COUNTED_UNIT}",
    re.I,
)

#: 🛑 **A table row break whose crossed cells are all EMPTY.** A charge sheet laid out as a
#: two-column table wraps a long line inside its cell, and the extractor emits the wrap as a
#: new row: ``(ना.प्र.नं. |  |\n| ##/#####)``. The label ends one row and its value opens the
#: next, with nothing between them but pipes and whitespace.
#:
#: ⚠️ Crossing a ``|`` is what :data:`_LABEL_GAP` refuses, and that refusal stays: a cell
#: boundary with *text* in the crossed cells is a different field. This gap admits a row
#: break only when every cell it crosses is empty, so it cannot reach a number in another
#: column, and it crosses exactly one line break.
EMPTY_CELL_WRAP = r"[^\S\n]*(?:\|[^\S\n]*)+\n(?:[^\S\n]*\|)*[^\S\n]*"

#: The abbreviated label's own rule. See :data:`CIT_ABBREVIATED_LABEL` for the label forms.
#:
#: 🛑 **Its value floor is three digits, not four** (:data:`RULE_DIGIT_BOUNDS`). The JAW-73
#: charge sheet lists its accused as ``<name> (ना.प्र.नं.###)`` -- old district certificates
#: carry three-digit serials -- and every one stayed in the text under the four-digit floor
#: of the spelled-out label. The floor is safe here for the reason it is not safe there:
#: this label always carries its number marker, so it cannot reach ``नागरिकता ऐन, २०६३``
#: or a count the way an optional-marker label can.
CITIZENSHIP_ABBREVIATED_LABEL_VALUE = re.compile(
    rf"(?P<label>{CIT_ABBREVIATED_LABEL})(?:{_LABEL_GAP}|{EMPTY_CELL_WRAP})"
    rf"(?P<val>{_value_window(_CIT_LEGACY_SEPARATORS, 1, 24)})"
    rf"{_COUNTED_UNIT}",
)

#: 🛑 **A citizenship number with no label in front of it, identified by the sentence around
#: it.** Kathmandu District Court verdicts (JAW-74) describe a seized certificate as
#: ``…कार्यालयबाट जारी भएको ######/### नम्बरको <name>को नाममा जारी भएको नागरिकताको प्रतिलिपि``
#: -- "the citizenship certificate no. ######/### issued in <name>'s name". The number is
#: followed, not preceded, by what makes it personal.
#:
#: ⚠️ Every part of that frame is required, and the ``नागरिकता`` at its end is what keeps
#: it narrow: ``जारी भएको … नम्बरको`` alone also describes a letter, a licence or a
#: vehicle, and only the closing word says this one is a citizenship certificate.
CITIZENSHIP_ISSUED_IN_NAME_VALUE = re.compile(
    r"(?P<label>जारी\s*भएको[^\S\n]*\n?[^\S\n]*)"
    rf"(?P<val>{_value_window(_CIT_LEGACY_SEPARATORS, 2, 24)})"
    rf"(?=\s*नम्[बव]रको\s[^।\n|]{{0,40}}?को\s*नाममा\s*(?:जारी\s*भएको\s*)?नागरिकता)",
)

#: 🛑 **A refugee identity card number.** The JAW-74 verdict concerns a fake Bhutanese-refugee
#: resettlement scheme and tabulates its victims, noting against one of them
#: ``शरणार्थी परिचय पत्र`` with ``द.नं. ###-########`` -- the card's registration number --
#: in the cell below. That is an identity-document number of the same class as a
#: citizenship number, and nothing removed it.
#:
#: ⚠️ ``द.नं.`` (``दर्ता नम्बर``, "registration number") is **never a label on its own**: it
#: is how every letter's dispatch register number is written, which is public filing data.
#: Only the refugee-card words before it make the number personal, so the rule starts there.
#: On the label's own line the marker is optional. Across a line break it is REQUIRED, and
#: at most one break is crossed: the verdict's table wraps the label at the foot of one row
#: and puts ``द.नं. …`` in the same column of the next, with other columns' wrapped text
#: (``(२)``) in between, so the crossed cells cannot be required to be empty here.
REFUGEE_ID_LABEL_VALUE = re.compile(
    r"(?P<label>(?:शरणार्थी\s*परिचय\s*-?\s*पत्र(?:को)?|refugee\s*(?:id(?:entity)?|card)"
    r"(?:\s*card)?)"
    r"(?:[^\S\n]*(?:(?:द\.?\s*नं\.?|दर्ता\s*(?:नं\.?|नम्बर)|नं\.?|नम्बर|no\.?|number)"
    r"[^\S\n]*[:ः]?[^\S\n]*)?"
    r"|[^\n]{0,200}\n[^\n]{0,200}?(?:द\.?\s*नं\.?|दर्ता\s*(?:नं\.?|नम्बर))[^\S\n]*[:ः]?[^\S\n]*))"
    rf"(?P<val>{_value_window(_CIT_LEGACY_SEPARATORS, 3, 24)})"
    rf"{_COUNTED_UNIT}",
    re.I,
)

#: 🛑 **An Indian Voter ID (EPIC) number** -- three Latin letters and seven digits. The JAW-73
#: charge sheet identifies an Indian national by ``भारतिय निवार्चन आयोग कार्ड नं. XGV#######``
#: ("Election Commission of India card no."), exactly as it identifies Nepali nationals by
#: citizenship number, and no rule reached it.
#:
#: ⚠️ Label-anchored, never a bare shape: three capitals and seven digits is also how some
#: vehicle, voucher and file references are written. The anchor is "commission card number"
#: (``आयोग कार्ड नं.``) rather than the commission's name, because the PDF's own text layer
#: -- which :mod:`likhit.privacy.pdf` redacts against -- garbles निर्वाचन beyond any
#: spelling (``मर्नवािार्न``) and ``कार्ड नं.`` into ``काडा र्नं.``; the name is matched when
#: it is legible, in both the right and the corpus's misspelt form (``निवार्चन``), and
#: ``मतदाता परिचयपत्र`` is the Nepali name for the same card.
_VOTER_ID_LABEL = (
    r"(?:(?:(?:निर्वाचन|निवार्चन|निर्बाचन)\s*)?आयोग(?:को)?\s*(?:परिचय\s*)?"
    r"(?:का(?:र्)?डा?|-?\s*पत्र)\s*(?:र्)?(?:नं?\.?|नम्बर)?"
    r"|मतदाता\s*परिचय\s*-?\s*(?:पत्र|कार्ड)\s*(?:नं\.?|नम्बर)?"
    r"|(?:voter\s*(?:id(?:entity)?\s*)?(?:card)?|\bEPIC)\s*(?:no\.?|number)?)"
)
VOTER_ID_LABEL_VALUE = re.compile(
    rf"(?P<label>{_VOTER_ID_LABEL})(?P<sep>[^\S\n]*[:ः]?[^\S\n]*)"
    rf"(?P<val>(?<![A-Za-z])[A-Z]{{3}}[{AD}]{{7}})(?![{AD}A-Za-z])",
    re.I,
)

#: 🛑 **An Aadhaar number** -- India's twelve-digit national identity number, written in
#: groups of four. The JAW-73 charge sheet carries two, as ``भारतिय आधार कार्ड नं.
#: #### #### ####`` and, through the same font artefact :data:`CIT_ABBREVIATED_LABEL`
#: describes, ``आधार काडा र्नं. #### ########``.
#:
#: ⚠️ ``आधार`` alone is the ordinary word "basis" (``के आधारमा``, "on what basis") and is
#: everywhere in these documents, so the label requires ``कार्ड`` after it. The value must be
#: exactly twelve digits, in groups of four or not grouped, so neither an amount nor a case
#: number can satisfy it.
_AADHAAR_LABEL = (
    r"(?:आधार\s*का(?:र्)?डा?\s*(?:र्)?(?:नं?\.?|नम्बर)?"
    r"|aadhaa?r(?:\s*card)?\s*(?:no\.?|number)?)"
)
AADHAAR_LABEL_VALUE = re.compile(
    rf"(?P<label>{_AADHAAR_LABEL})(?P<sep>[^\S\n]*[:ः]?[^\S\n]*)"
    rf"(?P<val>(?<![{AD}])[{AD}]{{4}}(?:[^\S\n]?-?[^\S\n]?[{AD}]{{4}}){{2}}"
    rf"|(?<![{AD}])[{AD}]{{4}}[^\S\n]?-?[^\S\n]?[{AD}]{{8}})(?![{AD}])",
    re.I,
)

#: 🛑 **A Nepali national identity card number** (``राष्ट्रिय परिचयपत्र``, the NID). It is
#: an identity-document number of the same class as a citizenship number, and the charge
#: sheets have begun to cite it beside one; no rule reached it, so it passed ``strict`` and
#: the residue check alike.
#:
#: ⚠️ **The number marker is required**: ``राष्ट्रिय परिचयपत्र`` alone is also the name of
#: the programme and of its department (``राष्ट्रिय परिचयपत्र तथा पञ्जीकरण विभाग``), and a
#: count or a date follows it as often as a number does. ``राष्ट्रिय परिचय नम्बर`` -- the
#: number's own name -- carries its marker in the name. The value is one digit run that may
#: be grouped by hyphens, never by spaces, so it cannot join the next figure on the line.
_NATIONAL_ID_LABEL = (
    r"(?:राष्ट्रिय\s*परिचय\s*-?\s*(?:पत्र(?:को)?\s*)?(?:नं?\.|नं|नम्[बव]र)"
    r"|national\s*id(?:entity)?(?:\s*card)?\s*(?:no(?![a-z])\.?|number)"
    r"|\bNID\s*(?:no(?![a-z])\.?|number))"
)
NATIONAL_ID_LABEL_VALUE = re.compile(
    rf"(?P<label>{_NATIONAL_ID_LABEL})(?P<sep>[^\S\n]*[:ः]?[^\S\n]*\n?[^\S\n]*)"
    rf"(?P<val>(?<![{AD}])[{AD}](?:[{AD}]|[\-–—](?=[{AD}])){{5,16}}(?![{AD}]))"
    rf"(?![\-–—/.][{AD}]){_COUNTED_UNIT}",
    re.I,
)

#: A number marker after an identity-card label: ``नं.``, ``नं``, ``न.``, ``नम्बर`` (and the
#: corpus's ``नंम्बर``/``नम्वर``), with the stray repha some fonts put in front of it
#: (``र्नं.``, see :data:`CIT_ABBREVIATED_LABEL`).
#:
#: ⚠️ The bare, undotted ``नं`` ends with a letter lookahead, or it would match the start
#: of an ordinary word (``नंगा``) and hand the next number to the rule as a value.
_ID_CARD_MARKER = r"(?:र्)?(?:नं?म्[बव]र|नं?\.|नं(?![\u0900-\u0963\u0970-\u097f]))"


#: The separators an identity-card value may be grouped by (spaces excluded, see below).
_ID_CARD_SEPARATORS = r"\-–—/"


def _id_card_value(lo: int, hi: int) -> str:
    """An identity-card value: one digit run, grouped only by ``- – — /``.

    A danda is admitted only before a digit, as :func:`_value_window` admits it. Never
    spaces, so the value cannot join the next figure on the line; refused when it
    continues as a date or a case number would.
    """

    return (
        rf"(?P<val>(?<![{AD}]){_value_window(_ID_CARD_SEPARATORS, lo, hi)})"
        rf"(?![\-–—/।.][{AD}]){_COUNTED_UNIT}"
    )


#: The run between an identity-card label and its value: whitespace, a colon or an opening
#: bracket on the label's own line, and at most one line break.
_ID_CARD_GAP = r"(?P<sep>[^\S\n]*[:ः(]?[^\S\n]*\n?[^\S\n]*)"

#: 🛑 **A Nepali voter ID card number written as digits** -- ``मतदाता परिचय पत्र नं.
#: ########``. :data:`VOTER_ID_LABEL_VALUE` already matched this label, but only in front of
#: an Indian EPIC (three letters, seven digits), so every Nepali voter card number -- digits
#: only -- passed ``strict`` and the residue check. Same class, so the same ``voter_id``
#: kind and placeholder, with its own digit bounds (:data:`RULE_DIGIT_BOUNDS`).
#:
#: ⚠️ The marker is required: ``मतदाता परिचयपत्र`` alone is also the name of the card in
#: prose (``मतदाता परिचयपत्र वितरण``), and a count follows it there.
VOTER_ID_DIGITS_LABEL_VALUE = re.compile(
    r"(?P<label>मतदाता\s*परिचय\s*-?\s*(?:पत्र|कार्ड)(?:को)?\s*"
    rf"{_ID_CARD_MARKER}){_ID_CARD_GAP}{_id_card_value(4, 14)}",
)

#: 🛑 **A driving licence number** (``सवारी चालक अनुमति पत्र नं. ##-######``). Court orders
#: in vehicle and smuggling cases identify the accused driver by it, and nothing removed it.
#: It is a personal identity-document number of the same class as a passport.
#:
#: ⚠️ ``सवारी चालक`` is required: ``अनुमति पत्र`` alone is any permit or licence -- a
#: teaching licence (``अध्यापन अनुमति पत्र``), a construction or export permit -- which are
#: professional and business registrations, public by design. The class letters that may
#: precede the label (``A र B वर्गको सवारी चालक …``) are outside it and kept.
_DRIVING_LICENCE_LABEL = (
    r"(?:सवारी\s*-?\s*चालक\s*-?\s*(?:अनुमति\s*-?\s*पत्र|लाइसेन्स)(?:को)?\s*"
    rf"{_ID_CARD_MARKER}"
    r"|driv(?:ing|er'?s?)\s*licen[cs]e\s*(?:no(?![a-z])\.?|number))"
)
DRIVING_LICENCE_LABEL_VALUE = re.compile(
    rf"(?P<label>{_DRIVING_LICENCE_LABEL}){_ID_CARD_GAP}{_id_card_value(4, 22)}",
    re.I,
)

#: 🛑 **A foreign national's registration number with their embassy** -- a Special Court
#: money-laundering order identifies an Indian national as ``<name> (भारतीय नागरिक
#: रजिष्ट्रेसन नं. ####/####, भारतीय राजदूतावास काठमाडौँ, नेपाल)``. It is that person's
#: identity number, of the same class as the Aadhaar and voter ID numbers the same corpus
#: carries for Indian nationals, and no rule reached it.
#:
#: ⚠️ ``नागरिक`` (citizen) is required in front of ``रजिष्ट्रेसन``. The bare word is how a
#: land deed (``मालपोत कार्यालय … रजिष्ट्रेशन नं. ####``), a company or firm
#: (``कम्पनी रजिष्ट्रेसन नं.``) and an exam candidate are numbered -- public registers, or
#: (exams) an open question this rule does not settle. Every spelling the corpus uses is
#: admitted: ष/स, स/श, ``रजिस्टे्रशन`` (the e-matra typed before the virama by legacy-font
#: conversion).
_FOREIGN_REGISTRATION_LABEL = (
    r"(?:(?:(?:भारती[यि]|विदेशी)\s*)?नागरिक\s*रजि(?:ष्|स्)?ट(?:्रे|े्र)(?:स|श)न\s*"
    rf"{_ID_CARD_MARKER}"
    r"|(?:indian\s*|foreign\s*)?citizen\s*registration\s*(?:no(?![a-z])\.?|number))"
)
FOREIGN_REGISTRATION_LABEL_VALUE = re.compile(
    rf"(?P<label>{_FOREIGN_REGISTRATION_LABEL}){_ID_CARD_GAP}{_id_card_value(2, 22)}",
    re.I,
)

#: 🛑 **A personal identity card's number** -- ``परिचय पत्र नं.`` / ``परिचयपत्र नं.``.
#: Court orders and audit reports cite whatever card identifies a person: a social-security
#: beneficiary card (``सामाजिक सुरक्षा भत्ता परिचय पत्र नं. #############``), a freed-Haliya
#: or disability card, a provident-fund card (``कर्मचारी संचय कोषको परिचय पत्र नं.``), a
#: police ID (``नेपाल प्रहरीको परिचय पत्र नं.########``), an embassy-issued card for a
#: foreign national (``भारतीय दुतावासबाट जारी भएको परिचय पत्र नं. ####/##``). Measured over
#: the local corpus, every ``परिचय(-)पत्र`` + number marker with a number behind it was one
#: person's card; none was a public document number.
#:
#: ⚠️ **The number marker is required** -- ``परिचयपत्र वितरण ५०० जना`` is a count -- and the
#: cards that already have their own class are excluded by the word in front, so a value
#: is counted under one kind: national ID (``राष्ट्रिय``), refugee card (``शरणार्थी``) and
#: voter ID (``मतदाता``, ``आयोग(को)``). A letter directly in front (``राष्ट्रियपरिचयपत्र``)
#: also excludes it; that glued form is the national ID's label.
#:
#: 🛑 ``करदाता परिचय पत्र नं.`` (taxpayer identity number) is a **PAN** and is excluded too:
#: PAN is detected and never removed, by design -- see the ``pan`` note in
#: :data:`INLINE_RULES`. Taking it here would undo that decision through a side door.
#:
#: The floor is three digits (``अपाङ्गता परिचयपत्र नं. ###``, ``भारतीय ना. परिचय पत्र नं.
#: ###``): the marker is required, so a statute year or a bare count cannot reach it.
#:
#: 🛑 **A form's numbered field list is not a card.** A blank identity-document form reads
#: ``१. कागजातको किसिम २. परिचय पत्र नं. ३. जारी गर्ने कार्यालय``: the "value" behind the
#: label is the NEXT field's list number. Three-digit list numbers reach the floor, so a
#: run of at most three digits followed by a full stop (and no digit) is refused -- a card
#: serial is not written that way, a list number always is.
#:
#: The excluded words are matched with up to two spaces or a line break before ``परिचय``
#: (``आयोग``/``करदाता`` also with ``को``; ``राष्ट्रियको परिचयपत्र`` is not the national ID's
#: label, so that card stays a generic one rather than nobody's). Python's look-behind is
#: fixed-width, so the variants are enumerated.
_IDENTITY_CARD_EXCLUDED = ("राष्ट्रिय", "शरणार्थी", "मतदाता", "आयोग", "करदाता")
_IDENTITY_CARD_LABEL = (
    r"(?<![\u0900-\u0963\u0970-\u097f])"
    + "".join(
        rf"(?<!{word}{suffix}{space})"
        for word in _IDENTITY_CARD_EXCLUDED
        for suffix in (("", "को") if word in ("आयोग", "करदाता") else ("",))
        for space in ("", " ", "  ", "\n", " \n")
    )
    + rf"परिचय\s*-?\s*पत्र(?:को)?\s*{_ID_CARD_MARKER}"
)
#: The same exclusion without the fixed-width limit: the excluded word (``को`` allowed on
#: ``आयोग``/``करदाता``) ending where the whitespace run before the label begins (see
#: :func:`_identity_card_excluded_before`, which skips the run however long it is).
#: :func:`_match_values` applies it to every ``identity_card`` match, so a taxpayer
#: label wrapped with three spaces, a tab or an indented line break stays a PAN (visible) in
#: the remover, the scanner and the PDF path alike. The look-behinds above are kept as the
#: cheap first filter; this is the guarantee.
_IDENTITY_CARD_EXCLUDED_BEFORE = re.compile(
    r"(?:राष्ट्रिय|शरणार्थी|मतदाता|आयोग(?:को)?|करदाता(?:को)?)\Z"
)
IDENTITY_CARD_LABEL_VALUE = re.compile(
    rf"(?P<label>{_IDENTITY_CARD_LABEL}){_ID_CARD_GAP}"
    rf"(?![{AD}]{{1,3}}\.(?![{AD}])){_id_card_value(1, 22)}",
)


def _identity_card_excluded_before(match: re.Match[str]) -> bool:
    """Whether an excluded word sits before this card label, across ANY whitespace run.

    Walks back over the whole whitespace run first (no window to fall out of, however
    long the run), then tests the word that ends there.
    """
    text, i = match.string, match.start()
    while i > 0 and text[i - 1].isspace():
        i -= 1
    # The longest excluded word, `करदाताको`/`राष्ट्रिय`, is well under 16 code points.
    return bool(_IDENTITY_CARD_EXCLUDED_BEFORE.search(text[max(0, i - 16) : i]))


#: 🛑 **A phone's IMEI number** -- fifteen digits that identify one handset, and through it one
#: person. The JAW-74 verdict lists every phone seized from the accused by IMEI, as
#: ``IMEI No.###############/###############`` (one per SIM slot), ``IME नम्बर ###…`` and
#: ``IMEI No.(Slot-1):-###…``.
#:
#: ⚠️ **Uppercase only**, with no ``re.I``: ``IME`` is also the name of a Nepali bank
#: (Global IME), but a bank name is not followed directly by fifteen digits, and the fixed
#: fifteen-digit value is what holds the rule. The second number after ``/`` is reached as a
#: continuation, never on its own, as :data:`PASSPORT_LABEL_VALUE` reaches a second passport.
_IMEI_VALUE = rf"(?<![{AD}])[{AD}]{{2}}-?[{AD}]{{6}}-?[{AD}]{{6}}-?[{AD}](?![{AD}])"
IMEI_LABEL_VALUE = re.compile(
    r"(?P<label>\bIMEI?\b[^\S\n]*\n?[^\S\n]*(?:No\.?|Number|नम्बर|नं\.?)?[^\S\n]*"
    rf"(?:\([^\S\n]*Slot[^\S\n]*-?[^\S\n]*[{AD}][^\S\n]*\))?[^\S\n]*[:ः]?[^\S\n]*-?[^\S\n]*)"
    rf"(?P<val>{_IMEI_VALUE}(?:[^\S\n]*/[^\S\n]*{_IMEI_VALUE})*)",
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
    r"(?:जन्म\s*(?:मिति|दर्ता(?:को)?\s*मिति|दिन)|जन्ममिति"
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

#: 🛑 **A Nepali passport number is often written with no letter prefix at all.** The JAW-79
#: charge sheet (Patan High Court, ``ag/115641``) lists four accused as
#: ``राहदानी नं. ######`` and ``राहदानी नं. ########, ########`` in Devanagari digits, and
#: one more with the number on the line after its label. None was removed at ``strict`` and
#: the scanner reported none, because the only value shape this rule knew was
#: :data:`_PASSPORT_VALUE`. The prefix is therefore optional here -- but only behind a
#: number marker or a colon (:data:`PASSPORT_LABEL_VALUE`), never behind the bare word.
#:
#: ⚠️ A bare 6-9 digit run is what a cheque, a file or a dispatch number looks like too, so
#: what follows the run has to rule those out: no further digit group (``/``, ``-`` or ``.``
#: then a digit -- a date or a case number -- or a comma then a short group -- an amount),
#: and no unit or money word after it
#: (``######## जना`` is a count, ``######## रुपैयाँ`` an amount).
_PASSPORT_ANY_VALUE = (
    rf"(?<![A-Za-z{AD}])(?:[A-Z]{{1,2}})?[{AD}]{{6,9}}(?![{AD}])"
    rf"(?![\-–—/.][{AD}])"
    # A comma straight before digits is the grouping of an amount (`#######,##`), unless a
    # full passport-length run follows -- that is an unspaced list (`########,########`).
    rf"(?!,(?![{AD}]{{6}})[{AD}])"
    rf"(?![^\S\n]*(?:जना|वटा|पटक|प्रतिशत|किस्ता|थान|रु|रुपैयाँ|रुपैया|/-|।-|लाख|करोड))"
)

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

#: The label, in both scripts. ``पासपोर्ट`` is the loanword the charge sheets use beside
#: ``राहदानी``.
#:
#: 🛑 **Both are also matched as a Word-made PDF's glyph layer delivers them**, which is what
#: :mod:`likhit.privacy.pdf` redacts against. The JAW-79 charge sheet's layer reads
#: ``राहदानी नं.`` as ``रािदार्नी र्नं.`` (``ह`` comes out as the vowel sign ``ि``, and the
#: repha lands before ``नी`` and ``नं``), so a label that only matched the repaired text
#: removed the numbers from the transcript and left every one in the PDF. ``पासपोर्ट`` gets
#: the repha tolerance :data:`_VOTER_ID_LABEL` gives ``कार्ड`` (``पासपोटा``).
_PASSPORT_LABEL = r"(?:passport|रा(?:ह|ि)दा(?:र्)?नी|पासपो(?:र्)?टा?)(?:को)?"

#: The number marker. ``न.`` is the marker without its anusvara and ``र्नं.`` the marker with
#: the glyph layer's stray repha in front, as for citizenship. The dot after ``न`` is
#: required, so ``राहदानी नभएको`` ("has no passport") is not a label.
_PASSPORT_MARKER = r"(?:no(?![a-z])\.?|number|(?:र्)?नं?\.|(?:र्)?नं|नम्[बव]र)"

#: 🛑 ``val`` spans the **whole list**, connectors and all, and :data:`VALUE_ITEMS` splits it
#: back into the individual numbers. An earlier form gave the list one capture group per
#: value (``val``/``val2``/``val3``) and so had a hard cap: a fourth passport under the same
#: label was left in the file, and *silently* -- nothing downstream could see a value the
#: pattern never captured, so the page carried three visible redactions and one live
#: identifier. The repetition is unbounded here, and the split is what makes that safe.
#:
#: 🛑 **The first value may be digits only when the label carries a marker or a colon**
#: (``राहदानी नं. ########``, ``Passport: ########``). Behind the bare word the Latin prefix
#: is still required, as before: ``राहदानी ######## जना`` style prose is where a bare number
#: after the word is something else.
#:
#: ⚠️ **At most two line breaks between label and value**, where the gap used to be an
#: unbounded ``\s*``. The charge sheet wraps straight after ``राहदानी नं.``, so one break is
#: needed; :data:`_LABEL_GAP` measured none beyond two across 173 documents, and an
#: unbounded gap is what lets a label claim an unrelated number from the next block.
PASSPORT_LABEL_VALUE = re.compile(
    rf"(?P<label>{_PASSPORT_LABEL}(?:[^\S\n]*\n?[^\S\n]*(?P<mark>{_PASSPORT_MARKER}))?)"
    rf"(?P<sep>[^\S\n]*(?P<colon>[:ः])?[^\S\n]*(?:\n[^\S\n]*){{0,2}})"
    rf"(?P<val>(?(mark){_PASSPORT_ANY_VALUE}|(?(colon){_PASSPORT_ANY_VALUE}|{_PASSPORT_VALUE}))"
    rf"(?:{_PASSPORT_CONNECTOR}{_PASSPORT_ANY_VALUE})*)",
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
#:
#: 🛑 **The opening code may stand apart from the digits by one space.** The court orders
#: write ``खाता नं. KL 2035931``, ``खाता नं. Md 2216599`` and ``खाता नं TD ०७३२०५`` -- a
#: branch or product code, a space, then the number -- and with no whitespace admitted the
#: window could not begin on the code, while the digit window could not begin on a letter,
#: so the account stayed. One space and at most three letters, and never ``Rs``, so an amount
#: written in English (``खाता नं. Rs 500000``) still cannot be taken for an account.
_BANK_VALUE = (
    rf"(?:(?![Rr][Ss][^A-Za-z])[A-Za-z]{{1,3}}[^\S\n](?=[{AD}]))?"
    rf"[A-Za-z]{{0,6}}[{AD}A-Za-z][{AD}A-Za-z\-–—/,.]{{2,28}}[{AD}]"
)

BANK_LABEL_VALUE = re.compile(
    # `न.` is the label with its anusvara dropped -- `खाता न.##############` in the JAW-74
    # verdicts. The dot is required, so `खाता नभएको` ("has no account") is not a label.
    r"(?P<label>(?:खाता|बैंक\s*खाता|account)\s*(?:नम्बर|नं\.?|न\.|no\.?|number))"
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

#: One account in a list: an unbroken run of nine or more digits that is not the integer
#: part of an amount and is not followed by a money word -- the same value
#: :data:`BANK_NAME_VALUE` takes, for the same reasons.
#:
#: ⚠️ A comma straight before more digits is refused only when fewer than nine follow:
#: ``#########,##`` is an amount, but ``A,B`` with two full-length runs is two accounts
#: written without a space, and refusing it left a third account after ``A,B, C`` in the text.
_BANK_LIST_RUN = (
    rf"(?<![{AD}])[{AD}]{{9,24}}(?![{AD}]|\.[{AD}]|,(?![{AD}]{{9}})[{AD}])"
    r"(?![^\S\n]*(?:रु|रुपैयाँ|रुपैया|/-|।-|लाख|करोड))"
)

#: 🛑 **The second and later accounts in a list under one label**: ``खाता नं. A, B`` and
#: ``खाता नं. A र B``. :data:`BANK_LABEL_VALUE` takes ``A`` and stops at the comma (its comma
#: must be followed by a digit), so ``B`` stayed in the text -- and the scanner, whose
#: window ends at the placeholder, then reported nothing either.
#:
#: ⚠️ ``val`` holds only the continuation; the label and the first account stay in
#: ``label`` for :data:`BANK_LABEL_VALUE`, which runs AFTER this rule (see
#: :data:`INLINE_RULES`): run first, it would rewrite ``A`` and this rule's label would no
#: longer match. :data:`RULE_VALUE_ITEMS` splits ``val`` into the individual accounts.
BANK_LIST_VALUE = re.compile(
    r"(?P<label>(?:खाता|बैंक\s*खाता|account)\s*(?:नम्बर|नं\.?|न\.|no\.?|number)"
    rf"{_LABEL_GAP.replace('(?P<sep>', '(?:')}{_BANK_LIST_RUN})"
    rf"(?P<val>(?:[^\S\n]*(?:,|र|and|&)[^\S\n]*{_BANK_LIST_RUN})+)",
    re.I,
)

#: 🛑 **An account number written straight after the bank's name, with no account label.**
#: The JAW-74 verdicts write ``कुमारी बैंक लि.को ################ न.को खातामा``,
#: ``नेपाल ईनभेष्टमेन्ट बैक लि. ##############`` and ``…बैकमा रहेको बैंक खातामा\n####``: the
#: bank names the account and the label, if any, comes after the number or not at all.
#:
#: ⚠️ **The value is one unbroken run of at least nine digits**, and that is what keeps money
#: and cheque numbers out. An amount is written grouped (``रु.##,##,###``) and always carries
#: a separator by then; a cheque number is reached only through ``चेक नं.``, which this
#: label does not cross; and a value followed by a rupee cue is refused outright. A run
#: that continues through ``.`` or ``,`` into more digits is the integer part of an amount
#: (``123456789.00 रुपैयाँ``) and is refused before the rupee cue is even consulted.
_BANK_NAME = r"(?:बैंक|बैक|bank)"
BANK_NAME_VALUE = re.compile(
    rf"(?P<label>{_BANK_NAME}(?:[^\S\n]*\n?[^\S\n]*(?:लि(?:मिटेड)?|ltd)\.?)?(?:[^\S\n]*(?:को|मा))?"
    r"(?:[^\S\n]*खाता(?:मा)?)?)"
    r"(?P<sep>[^\S\n]*\n?[^\S\n]*)"
    rf"(?P<val>(?<![{AD}])[{AD}]{{9,24}})(?![{AD}]|[,.][{AD}])"
    r"(?![^\S\n]*(?:रु|रुपैयाँ|रुपैया|/-|।-|लाख|करोड))",
    re.I,
)

#: 🛑 **An account number whose label comes AFTER it.** ``################ न.को खातामा``
#: ("into the account no. …") and ``################ नं. मा ट्रान्सफर`` ("transferred to
#: no. …") are how the JAW-74 verdicts write an account they have already introduced.
#:
#: ⚠️ Again one unbroken run of nine or more digits, the number marker (``न.``/``नं.``) is
#: required, and the words after it must say *account* or *transfer*: a bare
#: ``######### नं.`` is not enough, because a case or file reference is written the same way.
BANK_TRAILING_LABEL_VALUE = re.compile(
    rf"(?P<val>(?<![{AD}\-/.])[{AD}]{{9,24}})(?![{AD}])"
    r"(?P<label>[^\S\n]*नं?\.[^\S\n]*(?:को[^\S\n]*खाता|मा[^\S\n]*ट्रान्सफर))",
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
DIGIT_BOUNDS = {
    # 🛑 **The floor is 4, not 7, and that depends on `_CIT_LABEL` requiring `नं.`.** Real
    # certificate numbers in this corpus are routinely short -- `नागरिकता नं.२३९५`,
    # `प्रमाणपत्र नं. २९९४`, `नं. १०४४९`, `नं.१५१०८७` are 4, 4, 5 and 6 digits -- and a floor
    # of 7 left every one of them in the published text while the scanner went on reporting
    # them, which is what stranded 29 of 35 CIAA annual reports behind the residue gate.
    #
    # ⚠️ Lowering it alone would have been destructive: with an optional marker, a 4-digit
    # floor reaches `नागरिकता ऐन, २०६३` and deletes the statute's year. The two changes are
    # one change.
    "citizenship": (4, 16),
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
    # Fixed-length national numbers: an EPIC is three letters (stripped by VALUE_PRESTRIP)
    # and seven digits, an Aadhaar twelve digits, an IMEI fifteen. Anything else behind
    # those labels is not one.
    "voter_id": (7, 7),
    "aadhaar": (12, 12),
    "imei": (15, 15),
    # A refugee card's registration number is a district-style prefix and a serial
    # (`###-########`); the floor keeps a bare count out, the ceiling a run of table digits.
    "refugee_id": (5, 16),
    # A national identity number is ten digits; the window allows one either side for a
    # mistyped digit rather than refusing the value -- a refusal here keeps the identifier.
    "national_id": (9, 11),
    # A driving licence is a two-digit zone code and a serial (`##-######`) or, on the
    # smart-card licence, `##-##-########`; the floor keeps a short count out.
    "driving_licence": (6, 16),
    # An embassy registration seen as `####/####`; the floor keeps a bare year out, though
    # the required `नागरिक … नं.` label already does.
    "foreign_registration": (4, 16),
    # Cards of many issuers: a disability card is a 3-digit serial (`नं. ###`), a social
    # security card 13 digits. The required marker is what makes 3 safe -- see the 🛑 on
    # `RULE_DIGIT_BOUNDS` for why a floor this low needs a marker-only label.
    "identity_card": (3, 16),
}

#: Per-RULE bounds that override :data:`DIGIT_BOUNDS` for one pattern of a kind.
#:
#: 🛑 Exists for one rule: :data:`CITIZENSHIP_ABBREVIATED_LABEL_VALUE`, whose label always
#: carries its number marker and whose real values include three-digit serials. Lowering
#: the citizenship floor for the whole kind would hand that floor to the spelled-out label
#: too, whose marker is optional -- see the 🛑 note on ``DIGIT_BOUNDS["citizenship"]``.
RULE_DIGIT_BOUNDS: dict[re.Pattern[str], tuple[int, int]] = {
    CITIZENSHIP_ABBREVIATED_LABEL_VALUE: (3, 16),
    # A Nepali voter card number is all digits (8 on the cards seen); the EPIC rule keeps
    # its fixed seven digits behind three letters.
    VOTER_ID_DIGITS_LABEL_VALUE: (6, 12),
}

#: Years a birth date can plausibly carry, covering both calendars the corpus uses --
#: Bikram Sambat (currently ~2080) and the Christian era. Applied only at four digits,
#: where the value IS the year and there is no second field to corroborate it.
YEAR_RANGE = (1900, 2200)


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
    # The EPIC's three-letter series code, and nothing else: the pattern admits Latin
    # letters only in that position.
    "voter_id": re.compile(r"^[A-Z]{3}", re.I),
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
    (
        "citizenship",
        CITIZENSHIP_ABBREVIATED_LABEL_VALUE,
        CITIZENSHIP,
        RedactionMode.IDENTIFIERS,
    ),
    (
        "citizenship",
        CITIZENSHIP_ISSUED_IN_NAME_VALUE,
        CITIZENSHIP,
        RedactionMode.IDENTIFIERS,
    ),
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
    # The list rule runs FIRST: it needs the first account still in the text as its anchor.
    ("bank_account", BANK_LIST_VALUE, BANK_ACCOUNT, RedactionMode.STRICT),
    ("bank_account", BANK_LABEL_VALUE, BANK_ACCOUNT, RedactionMode.STRICT),
    ("bank_account", BANK_NAME_VALUE, BANK_ACCOUNT, RedactionMode.STRICT),
    ("bank_account", BANK_TRAILING_LABEL_VALUE, BANK_ACCOUNT, RedactionMode.STRICT),
    # 🛑🛑 **`age` is detected by nothing and removed by no tier, and that is the decision.**
    # It was removable at `strict` until it was measured. An age is an attribute rather than
    # an identifier, and in an accountability corpus it is overwhelmingly the *subject* of
    # the finding rather than an incidental detail about a person. Measured on every corpus
    # available, it was wrong in essentially every instance and right in none:
    #
    # - **CIAA annual reports, 5,864 pages: 36 removals, all wrong.** Twelve are the
    #   statutory tenure age of CIAA commissioners -- a constitutional provision, not
    #   anyone's age. The rest are the age bars whose breach IS the finding:
    #   `[AGE] वर्ष उमेर नाघेका व्यक्तिलाई … नियुक्त`.
    # - **Auditor General shelf: 76 removals**, effectively all statutory retirement
    #   thresholds (`कर्मचारीको उमेर ५८ वर्ष पुरा`).
    # - **Charge sheets: 34 removals.** Around twenty are statutes quoted verbatim
    #   (`सदस्यको उमेर [AGE] वर्ष पूरा भएमा स्वतः अवकाश हुनेछ`) or the hiring age range a
    #   teacher appointment allegedly breached; one is an age cited as a *sentencing
    #   factor*; several are the age on a record, which is the evidence in a
    #   citizenship-fraud case.
    #
    # The residue -- roughly four depositions reading `मेरो उमेर [AGE] वर्ष, पेसा व्यापार`
    # -- is the only arguably personal use, and there the speaker's name, father's name,
    # grandfather's name and full address sit in the same sentence and are all kept. An age
    # removed beside them protects nobody and costs the reader a fact that is frequently
    # load-bearing.
    #
    # ⚠️ :data:`AGE_LABEL_VALUE` is deliberately kept here, and :data:`placeholders.AGE`
    # stays registered in its own module -- no longer imported into this one, since no
    # rule writes it. The placeholder matters because text redacted by an earlier pass
    # must still be recognised
    # by ``strip_placeholders`` and ``contains_placeholder`` -- the same reason
    # :data:`placeholders.PAN` stays. The pattern stays because the decision is "this is not
    # personal data worth removing *from these corpora*", which a future corpus could
    # revisit with its own measurement; deleting it would make that a rewrite rather than a
    # one-line re-entry.
    ("passport", PASSPORT_LABEL_VALUE, PASSPORT, RedactionMode.STRICT),
    # Identity-document numbers of the same class as a passport, at the same tier and for
    # the same reason it sits there: `IDENTIFIERS` is the adjudicated scope and names two
    # classes. Found on the JAW-73 charge sheet (an Indian national's voter ID and Aadhaar
    # number) and the JAW-74 verdicts (a refugee card number, seized phones' IMEIs).
    ("voter_id", VOTER_ID_LABEL_VALUE, VOTER_ID, RedactionMode.STRICT),
    ("voter_id", VOTER_ID_DIGITS_LABEL_VALUE, VOTER_ID, RedactionMode.STRICT),
    ("aadhaar", AADHAAR_LABEL_VALUE, AADHAAR, RedactionMode.STRICT),
    ("refugee_id", REFUGEE_ID_LABEL_VALUE, REFUGEE_ID, RedactionMode.STRICT),
    ("imei", IMEI_LABEL_VALUE, IMEI, RedactionMode.STRICT),
    ("national_id", NATIONAL_ID_LABEL_VALUE, NATIONAL_ID, RedactionMode.STRICT),
    # A driving licence, a foreign national's embassy registration and any other person's
    # identity card (`परिचय पत्र नं.`): identity-document numbers of the passport's class,
    # found in Special Court and district court orders. The generic identity card runs
    # LAST and its label excludes the cards above by the word in front, so a national ID,
    # refugee or voter card is never also counted as one.
    (
        "driving_licence",
        DRIVING_LICENCE_LABEL_VALUE,
        DRIVING_LICENCE,
        RedactionMode.STRICT,
    ),
    (
        "foreign_registration",
        FOREIGN_REGISTRATION_LABEL_VALUE,
        FOREIGN_REGISTRATION,
        RedactionMode.STRICT,
    ),
    ("identity_card", IDENTITY_CARD_LABEL_VALUE, IDENTITY_CARD, RedactionMode.STRICT),
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
    # Either shape: a list may mix `PA#######` with all-digit numbers.
    "passport": re.compile(_PASSPORT_ANY_VALUE, re.I),
    # One IMEI per SIM slot, `###…/###…` under one label.
    "imei": re.compile(_IMEI_VALUE),
}

#: Per-RULE item splitters, for a rule whose ``val`` is a list when the other rules of its
#: kind take one value. Looked up before :data:`VALUE_ITEMS`, as :data:`RULE_DIGIT_BOUNDS` is
#: before :data:`DIGIT_BOUNDS`.
RULE_VALUE_ITEMS: dict[re.Pattern[str], re.Pattern[str]] = {
    BANK_LIST_VALUE: re.compile(_BANK_LIST_RUN),
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

    if match.re is IDENTITY_CARD_LABEL_VALUE and _identity_card_excluded_before(match):
        # A card with its own class (or a PAN) behind a whitespace run the fixed-width
        # look-behinds cannot see -- see `_IDENTITY_CARD_EXCLUDED_BEFORE`.
        return []
    items = RULE_VALUE_ITEMS.get(match.re, VALUE_ITEMS.get(kind))
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
#: ⚠️ The fractional tail is BOUNDED at two digits, and that is what lets
#: :func:`_is_amount` be a single test again. An unbounded `\.[AD]+` admitted
#: `१२,३४,५६७.५२०३८२३` as money — a 7-digit run no amount carries — which is the
#: whole reason a second "longest run < 6" clause existed, and the reason deleting
#: that clause as "unreachable" leaked an account. Money is written to two
#: decimal places; an account with a long tail no longer matches at all.
_MONEY_GROUPED = re.compile(rf"^[{AD}]{{1,3}}(?:,[{AD}]{{2,3}})+(?:\.[{AD}]{{1,2}})?$")


def _is_amount(val: str) -> bool:
    """Whether a bank value is money rather than an account.

    🛑 **Both clauses are load-bearing.** A previous commit deleted the second one
    as unreachable; it is not. :data:`_MONEY_GROUPED` caps the INTEGER groups at
    three digits and leaves the fractional tail unbounded (``\\.[AD]+``), so
    `खाता नं. १२,३४,५६७.५२०३८२३` fullmatches it carrying a 7-digit run — and with
    the length test gone that account is classified as money and published. The
    reasoning behind the deletion inspected the integer groups and never executed
    the pattern, which is the whole lesson: "unreachable" is a claim to run, not
    to read.
    """
    val = val.strip()
    if not _MONEY_GROUPED.fullmatch(val):
        return False
    return max((len(run) for run in re.findall(rf"[{AD}]+", val)), default=0) < 6


def _accept(
    val: str,
    kind: str,
    stats: Counter,
    bounds: tuple[int, int] | None = None,
) -> int | None:
    """The three guards. Returns the value's digit count, or ``None`` if any guard refuses.

    ``bounds`` overrides :data:`DIGIT_BOUNDS` for the kind; callers pass the rule's entry in
    :data:`RULE_DIGIT_BOUNDS`, which is ``None`` for every rule but one.

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
    lo, hi = DIGIT_BOUNDS[kind] if bounds is None else bounds
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
    # Two rules of one kind can reach the same value (a bank-name account that is also
    # followed by `न.को खाता`); `redact` removes it once, so it is one target here too.
    yielded: set[tuple[int, int]] = set()
    for kind, pattern, _, _ in _rules_for(mode):
        for match in pattern.finditer(text):
            # One match can carry several values -- see `_match_values`. Each is guarded on
            # its own, so one value failing a guard never suppresses its neighbour.
            for val, start, end in _match_values(match, kind):
                digit_count = _accept(
                    val, kind, counters, RULE_DIGIT_BOUNDS.get(pattern)
                )
                if digit_count is None:
                    continue
                if (start, end) in yielded:
                    continue
                yielded.add((start, end))
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


def count_rule_values(text: str, patterns: tuple[re.Pattern[str], ...]) -> int:
    """How many values ``patterns`` would remove from ``text``, guards included.

    🛑 **For the scanner, so the residue check and the remover cannot disagree.** The
    identifier rules added for JAW-73/JAW-74 (voter ID, Aadhaar, refugee card, IMEI, the
    unlabelled citizenship number, the bank-name account) have no older, looser detector to
    fall back on, and writing one would recreate the gap :mod:`likhit.privacy.signals`
    documents at length: a detection no rule removes strands a document at the publishing
    gate forever. Counting through the same patterns *and the same guards* makes the scanner
    report exactly what a ``strict`` pass would still take, which is the residue question.

    ⚠️ Tier-blind: it counts what the rule removes, not what a given tier removes, because
    the scanner reports presence and the gate decides which classes its tier covers.

    🛑 **A value is counted once however many of ``patterns`` reach it.**
    ``कुमारी बैंक लि.को ################ न.को खातामा`` is matched by both the bank-name and
    the trailing-label rule, and :func:`redact` removes it once (the second rule runs on
    text the first has already rewritten), so counting per pattern would report a residue
    of two where one value was ever present.
    """

    rules = {pattern: kind for kind, pattern, _, _ in INLINE_RULES}
    scratch: Counter = Counter()
    seen: set[tuple[int, int]] = set()
    for pattern in patterns:
        kind = rules[pattern]
        for match in pattern.finditer(text):
            for val, start, end in _match_values(match, kind):
                if _accept(val, kind, scratch, RULE_DIGIT_BOUNDS.get(pattern)):
                    seen.add((start, end))
    return len(seen)


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
                digit_count = _accept(val, kind, stats, RULE_DIGIT_BOUNDS.get(rx))
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
