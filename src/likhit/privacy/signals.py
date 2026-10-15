"""Detect personal-data *signals* in a transcript, without ever emitting a match.

Two tiers, because the two questions have different costs and different strengths:

``high_precision``
    An occurrence is evidence on its own -- contact details and labelled identifiers.
    Cheap enough to run exhaustively, so no sampling caveat applies to it.
``name_shaped``
    Counts that need a denominator rather than standing alone.

🛑 **The honorific trap this exists to avoid.** ``श्री`` is applied to offices as readily as
to people in Nepali government prose, so a bare count of it is not a PII signal at all. It
is counted as personal only when the following token is **not** an institution word, and
:data:`INSTITUTION` is that exclusion list.

🛑🛑 **This module never returns matched text, and that is a design constraint rather than an
oversight.** A report on what personal data a corpus contains must be publishable alongside
the corpus; one that quotes its matches is itself a disclosure. Callers get counts and
shapes. ``test_scan_returns_no_matched_text`` holds the line.

Detecting is not removing: :mod:`likhit.privacy.redact` and
:mod:`likhit.privacy.redact_tables` remove a deliberately *narrower* set than this finds,
and the difference is meant to be disclosed rather than quietly closed.
"""

from __future__ import annotations

import re
from collections import Counter

from ..devanagari import ANY_DIGITS
from .placeholders import strip_placeholders


# --- high-precision: an occurrence is evidence on its own -------------------
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Nepali mobile: 10 digits opening 97/98, either script, optional separators.
MOBILE = re.compile(
    rf"(?<![{ANY_DIGITS}])[९9][७८78][\s-]?[{ANY_DIGITS}]{{8}}(?![{ANY_DIGITS}])"
)
# Kathmandu-style landline 01-4XXXXXX and the generic 0N-NNNNNNN shape.
LANDLINE = re.compile(
    rf"(?<![{ANY_DIGITS}])[०0][{ANY_DIGITS}]-[{ANY_DIGITS}]{{6,7}}(?![{ANY_DIGITS}])"
)
# 🛑 **A phone number that :data:`MOBILE` cannot see, found by its label instead.** `MOBILE`
# anchors on the 97/98 opening of a *Nepali* number, so a foreign one in an international
# format is invisible to it -- and these documents carry them. Page 1 of
# ``samples/aarop-patra.pdf`` reads `Mobile Number: 919971766006`, an Indian number written
# with its country code, which every pattern above leaves in place.
#
# 🛑🛑 **Label-anchored, never a bare digit run.** The reason is the one
# :mod:`likhit.privacy.redact` gives for all of its rules: a free-floating 7-15 digit pattern
# would match figures out of a financial table, and this project's redaction history is 572
# real hits against **11,215 non-secret values clobbered**. The label is what makes the
# adjacent digits a phone number.
#
# ⚠️ The value window admits at most ONE separator between digits. A looser
# `[\s\-]*` lets the window run out of the number and swallow the next figure on the line.
PHONE_LABEL = (
    r"(?:mobile|cell(?:ular)?|phone|tel(?:ephone)?|contact|fax)"
    r"(?:\s*(?:no\.?|number|#))?"
    r"|सम्पर्क(?:\s*(?:नं\.?|नम्बर|फोन))?|मोबाइल(?:\s*(?:नं\.?|नम्बर))?"
    r"|फोन(?:\s*(?:नं\.?|नम्बर))?|दूरभाष(?:\s*(?:नं\.?|नम्बर))?"
)
PHONE_LABELLED = re.compile(
    rf"(?:{PHONE_LABEL})\s*[:ः]?\s*"
    rf"(?P<val>\+?[{ANY_DIGITS}](?:[\s-]?[{ANY_DIGITS}]){{6,14}})(?![{ANY_DIGITS}])",
    re.I,
)
# 🛑 **A passport number is Latin-prefixed, so no digit-only pattern can reach it.** Page 1
# of ``samples/aarop-patra.pdf`` carries `Passport No. Z4362932/India` and `K9838300/India`
# -- an identity-document number of the same class as a citizenship number, and the clearest
# single case of this package seeing only what is labelled in Nepali.
PASSPORT_LABELLED = re.compile(
    rf"(?:passport|राहदानी)\s*(?:no\.?|number|नं\.?|नम्बर)?\s*[:ः]?\s*"
    rf"(?P<val>[A-Z]{{1,2}}[{ANY_DIGITS}]{{6,9}})(?![{ANY_DIGITS}])",
    re.I,
)
# Labelled identifiers: the label is what makes the adjacent digits personal.
#
# ⚠️ **Each label alternation carries its English form as well as its Nepali one.** These
# documents are bilingual -- a charge sheet naming a foreign national writes that person's
# details in English in the middle of otherwise Nepali prose -- and a scanner that reads only
# the Nepali label reports "no personal data" over a page that is carrying it in Latin script.
# That is the same failure the module docstring describes for a legacy-font page: a zero that
# is a blind spot rather than a finding.
LABELLED = {
    "citizenship": re.compile(
        rf"(?:नागरिकता|citizenship)[^\n]{{0,40}}?[{ANY_DIGITS}]{{4,}}", re.I
    ),
    "pan": re.compile(
        rf"(?:स्थायी\s*लेखा\s*(?:नम्बर|नं)|\bPAN\b)[^\n]{{0,30}}?[{ANY_DIGITS}]{{6,}}"
    ),
    "bank_account": re.compile(
        rf"(?:खाता\s*(?:नम्बर|नं)|account\s*(?:no|number))[^\n]{{0,30}}?[{ANY_DIGITS}]{{6,}}",
        re.I,
    ),
    "date_of_birth": re.compile(
        rf"(?:जन्म\s*(?:मिति|दर्ता)|date\s*of\s*birth|\bd\.?o\.?b\.?)"
        rf"[^\n]{{0,30}}?[{ANY_DIGITS}]{{4,}}",
        re.I,
    ),
    "passport": PASSPORT_LABELLED,
    "phone_labelled": PHONE_LABELLED,
}

# --- name-shaped: needs a denominator --------------------------------------
# Institution words that follow `श्री` in ordinary government prose.
INSTITUTION = (
    "कार्यालय गाउँपालिका नगरपालिका उपमहानगरपालिका महानगरपालिका मन्त्रालय "
    "विभाग समिति आयोग परिषद् संस्था कम्पनी बैंक विद्यालय अस्पताल प्रतिष्ठान "
    "निर्देशनालय सचिवालय अदालत प्राधिकरण निगम कोष केन्द्र शाखा इकाई डिभिजन "
    "नेपाल सरकार संघ प्रदेश जिल्ला"
).split()
SHRI = re.compile(r"श्री\s*([^\s,।\n]{2,})")
SIGNATURE = re.compile(r"हस्ताक्षर|दस्तखत|सही\s*[:ः]")
# A name field with something after the separator.
NAME_FIELD = re.compile(r"(?:^|\n)\s*नाम\s*[:ः]\s*(\S+)")


def scan_text(t: str) -> tuple[Counter, Counter]:
    """Return ``(high_precision, name_shaped)`` counters. Never any matched text.

    🛑 **Registered placeholders are stripped first, because this scanner can otherwise
    detect its own redactions.** The markers this package writes spell out the kind they
    replaced -- ``[REDACTED:CITIZENSHIP-NO]``, ``[REDACTED:PAN-NO]`` -- and several label
    alternations here match on exactly those words. With a case-insensitive `citizenship`
    alternative, `[REDACTED:CITIZENSHIP-NO]) ... २०१५` is a citizenship hit: the label is the
    redaction marker and the "value" is a year four tokens away. Measured over the 13 CIAA
    transcripts, that was **+3 phantom citizenship signals** on redacted text.

    That is not a cosmetic over-count. This scanner's output is the *disclosure* -- the
    report published beside the corpus saying what personal data remains -- so a redaction
    that makes the "remaining" number go **up** turns the one artifact a reader checks the
    redaction against into an argument that it failed.

    ⚠️ ``\\bPAN\\b`` had the same collision latent against ``[REDACTED:PAN-NO]`` and happened
    not to fire only because no 6-digit run followed one closely enough. Stripping handles
    the class rather than the instance.
    """

    t = strip_placeholders(t)
    hp = Counter()
    hp["email"] = len(EMAIL.findall(t))
    hp["mobile"] = len(MOBILE.findall(t))
    hp["landline"] = len(LANDLINE.findall(t))
    for k, rx in LABELLED.items():
        hp[k] = len(rx.findall(t))

    ns = Counter()
    shri_total = shri_personal = 0
    for m in SHRI.finditer(t):
        shri_total += 1
        tok = m.group(1)
        # `tok.startswith(w)` implied `w in tok`, so the first clause was dead. Dropped
        # rather than kept for symmetry, because it disguised what the rule actually is:
        # substring-anywhere, not prefix. With three-character entries (`कोष`, `संघ`, `आयोग`)
        # that is broader than "institution words that FOLLOW the honorific" reads, so this
        # can under-count a real person whose name happens to contain one. Left as measured
        # rather than narrowed: the exclusion list was calibrated against this behaviour, and
        # tightening it is a change to the signal, not a cleanup.
        if not any(w in tok for w in INSTITUTION):
            shri_personal += 1
    ns["shri_total"] = shri_total
    ns["shri_not_institution"] = shri_personal
    ns["signature_marker"] = len(SIGNATURE.findall(t))
    ns["name_field_filled"] = len(NAME_FIELD.findall(t))
    return hp, ns
