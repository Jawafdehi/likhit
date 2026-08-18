"""The vowel-structure Latin veto axis, with a vocabulary-gated lower floor.

VOL-446. The **third** structural certification for the content-legacy remap, wired in
:func:`likhit.extractors.font_based._content_legacy_veto_flags` alongside
:func:`_reads_as_latin_text` (VOL-138) and :func:`_reads_as_latin_words` (VOL-146). Like
both of those it is **one-sided** -- it only ever declines to remap -- so the three
compose as a disjunction rather than competing.

NOT TO BE CONFUSED WITH ``vowel_floor.py``. That module (VOL-435) is the vowel-*length*
ranking term for ``रु``/``रू`` inside ``_map_ranking_key``, is deliberately unseated, and
has nothing to do with this file beyond the word "vowel".

WHAT IT IS FOR. ``_reads_as_latin_text`` reaches genuine English only where the letter
statistics are dense: its alpha ratio is 0.88 of non-space characters. ``QOC``-style
technical noun phrases clear it, but ordinary English prose carrying punctuation and
digits -- ``P Raj Trade Concern``, ``Khim Bahadur Khadka``, ``source not found.`` -- does
not, and ``_reads_as_latin_words`` declines them too because they carry no function word.
This axis is the measure that reaches them: **word shape** rather than letter density.

THE RULE, and it is exactly the rule that was measured. Fire when EITHER

  1. the axis fires at length floor 25; OR
  2. the axis fires at length floor 12 AND at least 60% of the run's length->=3
     ``[A-Za-z]+`` tokens are in the gate vocabulary below (case-folded).

The axis itself is five conjuncts, ALL required:

  * no ``[]{}|~^@+_=`` character anywhere in the run. The NARROW legacy set -- 11
    code points. ``font_based._LEGACY_KEYSTROKE_SYMBOLS`` is the same 11 in a different
    order; a wider set gives different counts and is not what was calibrated.
  * ``len(text) >= floor``. RAW length, including whitespace -- deliberately not the
    non-space count ``_reads_as_latin_text`` uses, because that veto's floor is a
    padding defence and this one is a "long enough to have word structure" test. Both
    are named so neither can be read as the other.
  * at least 3 alphabetic tokens of length >= 3.
  * alpha-to-total character ratio >= 0.6, over ``len(text)`` -- again raw length.
  * >= 90% of those tokens contain an ASCII vowel.

OPERATING CHARACTERISTIC, measured on a 256-run read band (VOL-134's floor-12 fire
band, every run adjudicated LATIN/LEGACY by hand):

    rule                fires  LATIN  LEGACY   false-veto rate
    floor 25 alone        152    126      26            17.1%
    floor 20 alone        223    163      60            26.9%
    gated df>=100         187    161      26            13.9%
    gated df>=60  SHIPPED 191    165      26            13.6%

LEGACY is exactly **26 at every cut from 200 down to 20**, so the vocabulary gate admits
**zero** legacy fires beyond floor 25's own band at any cut tested: it buys 39 further
genuine-Latin runs for nothing. Recall on an independently built Latin pool, contaminants
dropped: 65.2% per occurrence / 66.4% per type, against floor 25's 46.3% / 61.1%.

WHAT IT DOES NOT REACH, stated because a reader will otherwise assume it does.
``Quality Of Care, QOC`` -- 17 non-space characters, one occurrence in that pool -- is
the single pool type floor 20 catches that this rule does not. It is NOT the bare ``QOC``
acronym token, which is a different run of 3 characters and IS certified, by
``_LATIN_VETO_MIN_CHARS_UPPER`` (VOL-319 at precision 1.000, landed VOL-321). Do not
merge the two in any write-up.

THE FALSE-VETO RATE IS NOT ZERO AND IS NOT MEANT TO BE. 26 of 191 fires are runs a
reader called legacy keystrokes; the veto leaves them as raw ASCII rather than remapping
them to Devanagari. That is the trade this axis was calibrated to make -- an undecoded
keystroke run is illegible, and so is Devanagari that spells nothing, but the axis
recovers 165 runs of real English for those 26. The band was read non-natively with
mechanical corroboration, not by a Nepali speaker (VOL-223 wants one).
"""

from __future__ import annotations

import re

#: ASCII letter runs. Deliberately ASCII-only: the tokens are compared against a
#: case-folded ASCII vocabulary, and a Devanagari "token" is not a Latin word.
_ALPHA_TOKEN = re.compile(r"[A-Za-z]+")
_VOWEL = re.compile(r"[aeiouAEIOU]")

#: The NARROW legacy keystroke set: 11 code points a legacy 8-bit Devanagari layout
#: uses as glyph codes and English does not use in running text. A sufficient
#: condition for keystrokes.
#:
#: ⚠️ Defined HERE and imported by ``font_based``, not duplicated. VOL-446 shipped a
#: second copy with the note "kept local so this module has no import cycle ... and
#: asserted equal to it by test" -- but no such test was added, and
#: ``tests/test_no_duplicated_definitions.py`` refused the unregistered duplicate.
#: There is also no cycle to avoid: this module is a LEAF, importing nothing from
#: ``likhit``, and ``font_based`` already imports ``reads_as_latin_structure`` from
#: it. So the accepted-duplicate precedent (`_HEADER_Y_MAX`: "merging needs a new
#: import edge") does not apply -- the edge exists, so the copy is merged instead of
#: registered. The two copies were the same 11 code points in a different ORDER,
#: which is equal as a frozenset and would have made any byte comparison of them
#: disagree while the sets agreed.
_LEGACY_KEYSTROKE_SYMBOLS = frozenset("[]{}|~^@+_=")

#: Length floor for the ungated arm.
STRUCTURE_FLOOR = 25
#: Length floor for the vocabulary-gated arm.
GATED_FLOOR = 12
#: Share of the run's length->=3 tokens that must be in ``LATIN_DF_VOCABULARY``.
VOCABULARY_SHARE = 0.60
#: A token shorter than this is not evidence of word structure; see
#: ``font_based._LATIN_VETO_WORDS`` for why two-letter tokens are Preeti digraphs.
TOKEN_MIN_LENGTH = 3
#: The run needs this many such tokens before the shape question is even asked.
MIN_TOKENS = 3
#: Alphabetic share of RAW length -- see the module docstring on why raw.
MIN_ALPHA_RATIO = 0.6
#: Share of tokens that must contain an ASCII vowel.
MIN_VOWEL_TOKEN_SHARE = 0.9


def _tokens(text: str) -> list[str]:
    """The run's length->=3 ASCII letter tokens, in order."""

    return [t for t in _ALPHA_TOKEN.findall(text) if len(t) >= TOKEN_MIN_LENGTH]


def _axis_fires(text: str, floor: int, tokens: list[str]) -> bool:
    """The five-conjunct vowel-structure axis at ``floor``. All five are required.

    ``tokens`` is passed in rather than recomputed so the two floors the caller tries
    share one tokenisation -- the same hoist ``_reads_as_latin_text`` makes for
    ``letters``, and for the same reason: a token set needed above a floor test must be
    computed above it.
    """

    if any(char in _LEGACY_KEYSTROKE_SYMBOLS for char in text):
        return False
    if len(text) < floor:
        return False
    if len(tokens) < MIN_TOKENS:
        return False
    if not text:
        return False
    # `str.isalpha` here is UNICODE-aware, while `_ALPHA_TOKEN` above is ASCII-only.
    # That asymmetry is not a bug and not tidied: it is the predicate the 256-run band
    # was read against, so changing it would ship a rule nobody measured. Its one
    # visible effect is that a stray non-ASCII letter -- the `ɸ` of `110mmɸ uPVC` -- is
    # counted toward the ratio but can never be a token. Pinned by test.
    if sum(char.isalpha() for char in text) / len(text) < MIN_ALPHA_RATIO:
        return False
    with_vowel = sum(1 for t in tokens if _VOWEL.search(t))
    return with_vowel / len(tokens) >= MIN_VOWEL_TOKEN_SHARE


def reads_as_latin_structure(text: str) -> bool:
    """True if this run's raw ASCII has the word structure of genuine Latin text.

    One-sided: it certifies Latin, never keystrokes, so a run it declines is decoded
    exactly as before. See the module docstring for the rule, its operating
    characteristic, and the one pool type it does not reach.
    """

    tokens = _tokens(text)
    if _axis_fires(text, STRUCTURE_FLOOR, tokens):
        return True
    if not _axis_fires(text, GATED_FLOOR, tokens):
        return False
    if not tokens:  # unreachable while MIN_TOKENS >= 1, but the division below must
        return False  # not depend on that staying true
    known = sum(1 for t in tokens if t.lower() in LATIN_DF_VOCABULARY)
    return known / len(tokens) >= VOCABULARY_SHARE


#: Case-folded Latin types with ``df_clean >= 60`` in the purged document-frequency
#: table over the 3,130 CLEAN documents of v11 -- ``runs/vol144/latin-df-purged.json``,
#: sha256 ``e48278f2d941fd6eae6d7afdc57a2f2fa3b4b626ffe0c1f75617bce972ab217e``, schema ``token -> [df_clean, df_all_v11]``, element 0.
#: Generated by ``runs/vol446/generate_latin_structure-e6f4d4f7.py``; that script's
#: ``--check`` mode re-derives this frozenset from the artifact and diffs it.
#:
#: **This is a corpus frequency table, not English grammar,** and the distinction
#: matters for a general library. ``font_based._LATIN_VETO_WORDS`` is 24 English
#: function words and is defensible anywhere; this is 588 types of OAG audit
#: vocabulary, Nepali proper nouns in transliteration (``adhikari``, ``bahadur``,
#: ``anamnagar``) and programme acronyms (``iemis``, ``npsas``, ``copomis``). It
#: generalises to documents shaped like this corpus and its behaviour outside them is
#: unmeasured. It is only ever consulted to LOWER the floor from 25 to 12, and every
#: other conjunct of the axis still applies, so an out-of-domain document loses recall
#: rather than gaining false vetoes.
#:
#: **Why cut 60 and not 100.** VOL-301 measured gate-vocabulary contamination -- types
#: that are really legacy keystrokes -- at **5.45%** for cut 60 against **6.6%** for cut
#: 100, so the 4 extra fires cut 60 buys are not bought with a dirtier word list. Two
#: independent lines pick the same cut: that contamination axis and the fire band above.
#: Cuts 40 and 20 read better on the fire band (13.4%, 13.1%) and are NOT shipped --
#: nothing has measured contamination below 60, and the read band cannot see it.
LATIN_DF_VOCABULARY: frozenset[str] = frozenset(
    """
    above access account accountability accounting acid adhikari adjustment
    administrative advance after all allowance amount amoxycillin analysis anamnagar
    and apg application approved apr april are area assessment asset assets
    assistant assurance assure audit auditor aug authorized babar back bahadur bal
    bank bar base based basic basnet batch beam bed below benefit between bhandari
    bhawan bid bidding bill bir bishnu bituminous black block board book boq boulder
    box brick bridge buddha builders building built camera cap capacity case cbimnci
    cctv cement center centre certificate cgas chandra check chowk ciprofloxacin
    citizen citizenship civil class clearance coat code collection compaction
    company complete complex computer concrete condition construction consult
    consultancy consultant consulting consumption contract contractor control
    copomis core corner cost course covid credible cross ctevt cum cutting damai
    data date day days dec dekhi delivery dell description design detail detailed
    development devi dhan dia different digital dil disclosure disposal distribution
    dlp document door dor dormant double dpr drain drawing dry durga earth earthwork
    ecd education efficiently eft electric electrical electronic elmis embankment
    emergency emis engineer engineering enterprises entry environmental equipment
    estimate estimation etc examination excavation existing expiry eye far fax
    feasibility feb feed field filer filling final first fixing floor for form
    forward foundation fraud from fund funds gabion ganesh ganga gas gate gautam gcc
    general gmp goods gov government gps grade grant gravel ground group guidelines
    gurung hall hard hari hddf hdpe head health heavy home hospital house household
    https hume ibuprofen ict iee iemis including independence independent index
    information infrastructure initial inj inspection installation institution
    institutional insurance integrated integrity international internet
    investigation ipc iron irrigation item items jan jcb july jun kalika kami karki
    kathmandu khadka khatri khola krishna kumar kumari lab labor labour lal lalitpur
    land laptop laxmi laying lead led letter level liability life light limited line
    lisa list lmis load loc local long ltd machine machinery made magar mahal main
    maintenance man management mandir manual march masonary masonry master material
    materials maximum may maya means measurement mechanical media medicine medium
    member meter metronidazole minimum mis mission mix mobile model mortar mrm multi
    municipality name nams narayan nation national ncb nepal nepsas new nirman non
    norms not nov npsas number oag oct office ojt one online only operation order
    ordinary other others our out over padam page pams pan paracetamol park part
    parts payment pcc pcs people per performance period personal phase phone pipe
    plain plan plant plaster plum point pole post power practical prasad preparation
    prepared price primary prime printer private pro procurement professional
    professionalism program project promoting protection provide providing public
    pump pvt quality quantity rai raj ram ranitidine rate ratio rcc real reasonable
    regulatory reinforcement report research revenue reverse river road roads
    roadway rock rubble rural sadak salbutamol samsung sand sanitary saraswati sarki
    scc schedule school secondary section sector security senior sep service
    services serving set sewa shall sharma sheet shiva shree shrestha side since
    singh single sip sita site size slab smart sms social soft software soil soiling
    solar soling solution source special specification specifications sqm
    stakeholders standard statement steel stock stone store street strive structural
    structure study sub subedi subsidy sulphate sum suppliers supply supplying
    support surface surgical survey sutra system tab table tablet tack tamang tax
    team technical technology test testing text thapa that the thick thickness time
    tmt tole tools top tor total tourism trade training transfer transparency
    transport transportation treasury tresury truss two type types unit update
    upgrading use used user using value values vat vision visit vitamin volume wall
    ward wash water way who wire with wood work works zinc
    """.split()
)
