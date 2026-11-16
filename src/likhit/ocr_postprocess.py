"""Post-OCR correction for Nepali (Devanagari) legal documents: rules, a cross-engine check, a re-read.

WHY THIS EXISTS. Vision OCR of scanned Nepali charge sheets fails in a few narrow,
repeatable ways, and the failures land on exactly the fields readers search for:
dates, amounts, case numbers and दफा citations. Measured on a hand-verified gold set
of 112 scanned Attorney General pages (2,273 key fields, JAW-51, 2026-11):

* Azure Read reads the danda in a BS date as a digit (`२०७८।०१।१७` -> `2078701117`):
  date recall 57%. The date rule here takes it to 97%.
* Every engine misreads some digits; two engines rarely misread the SAME digit. A
  token-level comparison with a second reading, settled by value-shape validators,
  and a re-read of only the spans that are still disputed, took Azure Read from
  82.2% to 97.3% digit-field recall on image-only pages (with Tesseract and
  PaddleOCR as the other readers and Claude Sonnet re-reading).
* Even the strongest model tested (Claude Opus 5.5, 98%) miscounts zeros in long
  account numbers -- plausible-looking errors no reader would catch. A second
  reading is the only cheap way to see them.

THREE PASSES, each a pure function so a caller can use any subset:

1. ``postprocess(text)`` -- deterministic rules (dates, case/section numbers, amount
   figure vs amount in words, repetition loops, digit script). Each rule returns
   ``(text, flags)``; a rule changes text only when exactly one reading is possible,
   and FLAGS (never guesses) otherwise.
2. ``cross_check(primary, secondary, third=None)`` -- align two readings token by
   token. Where they disagree on a span holding digits, the side whose values are
   well-formed (BS date in range, lakh/western grouping, case-number shape) wins; a
   tie becomes a ``Disagreement`` for pass 3. Word spans switch only on a 2-of-3
   vote with a third reading.
3. ``reread_prompt`` / ``parse_reread_answer`` / ``apply_rereads`` -- ask a stronger
   model only about the disputed spans, then substitute its answers.

Nothing here calls a network service. ``tesseract_reading`` shells out to a local
``tesseract`` binary if one is installed, as a free second reader.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from itertools import product
from typing import Any

_DEV_DIGITS = "०१२३४५६७८९"
_TO_ASCII = str.maketrans(_DEV_DIGITS, "0123456789")
_TO_DEV = str.maketrans("0123456789", _DEV_DIGITS)
_D = "0-9०-९"  # digit class body for regexes
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200c\u200d\u00ad\ufeff"), None)


@dataclass
class OcrFlag:
    """One decision. ``action``: ``fixed`` (text changed), ``flag`` (looks wrong, left
    unchanged for review) or ``info`` (harmless normalisation)."""

    rule: str
    action: str
    original: str
    replacement: str = ""
    message: str = ""
    pos: int = -1  # offset in the text the rule received

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dev_majority(s: str) -> bool:
    return sum(c in _DEV_DIGITS for c in s) > sum(
        c.isascii() and c.isdigit() for c in s
    )


def _script(s_ascii: str, use_dev: bool) -> str:
    return s_ascii.translate(_TO_DEV) if use_dev else s_ascii


# ---------------------------------------------------------------------------
# Rule 1: dates
# ---------------------------------------------------------------------------
_EXPLICIT_SEP = set("।/|-.:")
# Digits seen standing in for a danda/slash. '1'/'7' are by far the most common;
# '8' is rarer than a dropped separator, so it costs more than _DROP_COST
# ('20798113' is 2079।8।13, not 2079।1।3).
_SEP_COST = {"1": 1, "7": 1, "9": 2, "6": 2, "8": 3.5}
_DROP_COST = 3
# Only plausible BS years. Handwritten court registration dates read as
# '2009108106' are not real dates and are left alone.
_YEAR_MIN, _YEAR_MAX = 2015, 2099
_WEEKDAY_COST = 1
_DATE_CUE_BEFORE = re.compile(
    r"(मिति|मिती|मितिः|जन्म|साल|सम्वत|सम्बत|संवत|आ\.?\s?व|आर्थिक वर्ष|Date|date|गते|ऐ\.)"
    r"[\s:;,\-–)\]।.]*$"
)
_DATE_CUE_AFTER = re.compile(
    r"^[\s)\]]*(गते|गतेको|गतेका|गतेसम्म|मा\b|को\b|सम्म|देखि|सालको|साल)"
)
# year 20xx, then digits / separators / single spaces, ending on a digit
_DATE_SPAN = re.compile(
    rf"(?<![{_D}A-Za-z,])([2२][0०][{_D}]{{2}}(?:[ ]?[{_D}।/|\-.:](?:[ ]?[{_D}।/|\-.:])*)?)(?![{_D}])"
)

_Parse = tuple[float, str, str, "str | None", tuple[str, ...]]


def _parse_date_body(rest: str) -> list[_Parse]:
    """All parses of ``rest`` (ASCII digits + explicit separators, no spaces) as
    SEP MM SEP DD [SEP W]; each is (cost, month, day, weekday, separators)."""
    out: list[_Parse] = []

    def seps_at(i: int) -> list[tuple[int, float, str]]:
        r: list[tuple[int, float, str]] = [(0, _DROP_COST, "")]
        if i < len(rest):
            c = rest[i]
            if c in _EXPLICIT_SEP:
                return [(1, 0, c)]  # an explicit separator is never dropped
            if c in _SEP_COST:
                r.append((1, _SEP_COST[c], c))
        return r

    def num_at(i: int, lo: int, hi: int) -> list[tuple[int, str]]:
        r = []
        for length in (1, 2):
            s = rest[i : i + length]
            if (
                len(s) == length
                and s.isdecimal()
                and lo <= int(s) <= hi
                and not (length == 2 and s == "00")
            ):
                r.append((length, s))
        return r

    for c1, k1, s1 in seps_at(0):
        if c1 == 0 and k1 == _DROP_COST and rest[:1] in _EXPLICIT_SEP:
            continue
        for lm, mm in num_at(c1, 1, 12):
            i = c1 + lm
            for c2, k2, s2 in seps_at(i):
                for ld, dd in num_at(i + c2, 1, 32):
                    j = i + c2 + ld
                    cost = k1 + k2
                    if j == len(rest):
                        out.append((cost, mm, dd, None, (s1, s2)))
                        continue
                    for c3, k3, s3 in seps_at(j):
                        if c3 == 0:
                            continue  # a weekday needs a visible separator
                        for lw, ww in num_at(j + c3, 1, 7):
                            if s3 not in _EXPLICIT_SEP and len(ww) != 2:
                                continue  # after a misread separator only a zero-padded weekday
                            if j + c3 + lw == len(rest):
                                out.append(
                                    (
                                        cost + k3 + _WEEKDAY_COST,
                                        mm,
                                        dd,
                                        ww,
                                        (s1, s2, s3),
                                    )
                                )
    return out


def _fiscal(year: str, rest: str) -> tuple[str, str] | None:
    """'2081882' / '2081182' -> ('82', sep). The year+1 check makes this unambiguous."""
    nxt = (int(year) + 1) % 100
    for tail in (f"{nxt:02d}", f"0{nxt:02d}"):
        if (
            len(rest) == len(tail) + 1
            and rest.endswith(tail)
            and (rest[0] in _SEP_COST or rest[0] in _EXPLICIT_SEP)
        ):
            return tail, rest[0]
    return None


def _has_date_cue(text: str, start: int, end: int) -> bool:
    return bool(
        _DATE_CUE_BEFORE.search(text[max(0, start - 30) : start])
        or _DATE_CUE_AFTER.search(text[end : end + 12])
    )


def _fmt_date(year: str, p: _Parse, sep: str = "।") -> str:
    s = f"{year}{sep}{p[1]}{sep}{p[2]}"
    return s + f"{sep}{p[3]}" if p[3] else s


def repair_dates(
    text: str, require_context: bool = True, allow_drop: bool = True
) -> tuple[str, list[OcrFlag]]:
    """Rebuild YYYY।MM।DD where a separator was misread as a digit or dropped.

    A rewrite happens only when (a) the token is not already a well-formed date,
    (b) exactly one parse has the lowest cost, and (c) with ``require_context`` a
    date cue (मिति, गते, जन्म, सम्वत ...) is next to it or the token carries an
    explicit separator. Otherwise a ``flag`` is emitted and the text is unchanged.
    """
    flags: list[OcrFlag] = []
    out: list[str] = []
    last = 0
    for m in _DATE_SPAN.finditer(text):
        span = m.group(1)
        chunks = span.split(" ")
        best = None
        # every run of leading space-separated chunks; the cheapest parse wins (longer wins a tie)
        for n in range(len(chunks), 0, -1):
            piece = " ".join(chunks[:n])
            if not piece[-1:].translate(_TO_ASCII).isdecimal():
                continue
            raw = piece.replace(" ", "").translate(_TO_ASCII)
            year, rest = raw[:4], raw[4:]
            if not rest:
                break
            if (
                all(c in _EXPLICIT_SEP or c.isdecimal() for c in rest)
                and len(rest) <= 10
            ):
                if not (_YEAR_MIN <= int(year) <= _YEAR_MAX):
                    break
                fy = _fiscal(year, rest)
                parses = _parse_date_body(rest)
                if not allow_drop:
                    parses = [p for p in parses if "" not in p[4]]
                n_exp = sum(c in _EXPLICIT_SEP for c in rest)
                if (
                    n_exp >= 2
                ):  # structure is visible: no separator may be assumed dropped
                    parses = [p for p in parses if "" not in p[4]]
                elif (
                    n_exp == 1
                ):  # a dropped separator beside a visible one only between 2-digit parts
                    parses = [
                        p
                        for p in parses
                        if "" not in p[4][:2] or (len(p[1]) == 2 and len(p[2]) == 2)
                    ]
                if fy or parses:
                    c = -1 if fy else min(p[0] for p in parses)
                    if best is None or c < best[0]:
                        best = (c, piece, raw, year, rest, fy, parses)
        if not best:
            digits = re.sub(r"\D", "", span.translate(_TO_ASCII))
            yr = int(digits[:4]) if len(digits) >= 4 else 0
            if (
                6 <= len(digits) <= 12
                and _YEAR_MIN <= yr <= _YEAR_MAX
                and _has_date_cue(text, m.start(1), m.end(1))
                and not re.fullmatch(
                    r"\d{4}\s*[/।-]\s*\d{2,3}", span.translate(_TO_ASCII)
                )
            ):
                flags.append(
                    OcrFlag(
                        "date",
                        "flag",
                        span,
                        "",
                        "date-like token with no valid YYYY?MM?DD parse",
                        m.start(1),
                    )
                )
            continue
        _, piece, raw, year, rest, fy, parses = best
        start, end = m.start(1), m.start(1) + len(piece)
        dev = _dev_majority(piece)
        explicit = [c for c in rest if c in _EXPLICIT_SEP]
        if fy:
            tail, sepc = fy
            if sepc in _EXPLICIT_SEP:
                continue  # already fine
            new = _script(f"{year}/{tail}", dev)
            if require_context and not _has_date_cue(text, start, end):
                # '2081182' with no आ.व./सम्वत/साल next to it may be an account number
                flags.append(
                    OcrFlag(
                        "date",
                        "flag",
                        piece,
                        new,
                        "fiscal-year-like token without date context",
                        start,
                    )
                )
                continue
            out.append(text[last:start] + new)
            last = end
            flags.append(
                OcrFlag(
                    "date",
                    "fixed",
                    piece,
                    new,
                    "fiscal year: separator read as digit (year+1 check)",
                    start,
                )
            )
            continue
        parses.sort(key=lambda p: p[0])
        top = parses[0]
        if top[0] == 0:
            continue  # well-formed, or explicit separators with only spacing differences
        tied = [p for p in parses if p[0] == top[0] and p[1:4] != top[1:4]]
        cue = bool(explicit) or _has_date_cue(text, start, end)
        if tied:
            alts = sorted({_fmt_date(year, p) for p in [top, *tied]})
            flags.append(
                OcrFlag(
                    "date",
                    "flag",
                    piece,
                    " | ".join(alts),
                    "ambiguous date reading",
                    start,
                )
            )
            continue
        # 'strong shape': YYYY s 0M|1M s DD, 10 chars, both separators misread as 1/7/9 --
        # date-specific enough to repair without a cue (table rows, document lists).
        strong = (
            len(raw) == 10
            and len(top[1]) == 2
            and len(top[2]) == 2
            and top[3] is None
            and all(s in "179" for s in top[4])
        )
        if require_context and not cue and not strong:
            flags.append(
                OcrFlag(
                    "date",
                    "flag",
                    piece,
                    _fmt_date(year, top),
                    "date-like token without date context",
                    start,
                )
            )
            continue
        sepc = max(set(explicit), key=explicit.count) if explicit else "।"
        if sepc in "-.:":
            sepc = "।"
        new = _script(_fmt_date(year, top, sepc), dev)
        if new.replace(" ", "") == piece.replace(" ", ""):
            continue
        out.append(text[last:start] + new)
        last = end
        flags.append(
            OcrFlag(
                "date", "fixed", piece, new, f"separator misread (cost {top[0]})", start
            )
        )
    out.append(text[last:])
    return "".join(out), flags


# ---------------------------------------------------------------------------
# Rule 2: digit script
# ---------------------------------------------------------------------------
_LATIN_LETTER = re.compile(r"[A-Za-z]")
_DEV_LETTER = re.compile(r"[\u0900-\u0963\u0970-\u097F]")


def normalise_digit_script(text: str) -> tuple[str, list[OcrFlag]]:
    """ASCII digits -> Devanagari in predominantly Devanagari lines.

    Leaves a token alone when it holds Latin letters (CR, C1, chassis numbers,
    e-mail, URL) or its neighbour token is Latin (English table cells), and leaves
    whole lines alone when they are mostly Latin.
    """
    flags: list[OcrFlag] = []
    lines: list[str] = []
    n_conv = 0
    src_lines = text.split("\n")

    def script_of(i: int) -> str | None:
        d = len(_DEV_LETTER.findall(src_lines[i]))
        la = len(_LATIN_LETTER.findall(src_lines[i]))
        if d + la == 0:
            return None
        return "dev" if d >= 2 and d >= 3 * la else "lat"

    def neighbour(i: int, step: int) -> str | None:
        j = i + step
        while 0 <= j < len(src_lines):
            s = script_of(j)
            if s:
                return s
            j += step
        return None

    for li, line in enumerate(src_lines):
        sc = script_of(li)
        if (
            sc is None
        ):  # digits-only line (table cell): Devanagari only between Devanagari lines
            sc = (
                "dev"
                if neighbour(li, -1) == "dev" and neighbour(li, 1) == "dev"
                else "lat"
            )
        if sc != "dev" or not re.search(r"[0-9]", line):
            lines.append(line)
            continue
        toks = re.split(r"(\s+)", line)
        words = [i for i, t in enumerate(toks) if t and not t.isspace()]
        for k, i in enumerate(words):
            t = toks[i]
            if (
                not re.search(r"[0-9]", t)
                or _LATIN_LETTER.search(t)
                or "@" in t
                or "://" in t
            ):
                continue
            nb = [
                toks[words[k - 1]] if k > 0 else "",
                toks[words[k + 1]] if k + 1 < len(words) else "",
            ]
            if any(_LATIN_LETTER.search(x) and not _DEV_LETTER.search(x) for x in nb):
                continue
            toks[i] = t.translate(_TO_DEV)
            n_conv += 1
        lines.append("".join(toks))
    if n_conv:
        flags.append(
            OcrFlag(
                "digits",
                "info",
                "",
                "",
                f"{n_conv} tokens converted to Devanagari digits",
            )
        )
    return "\n".join(lines), flags


def ocr_search_copy(text: str) -> str:
    """Normalised copy for search/indexing: NFC, no zero-width chars, ASCII digits, collapsed spaces."""
    s = unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH).translate(_TO_ASCII)
    return re.sub(r"[ \t]+", " ", s)


# ---------------------------------------------------------------------------
# Rule 3: amounts (figure vs amount in words)
# ---------------------------------------------------------------------------
_NUM_WORDS = {
    1: "एक", 2: "दुई दुइ", 3: "तीन तिन", 4: "चार", 5: "पाँच पाच पांच", 6: "छ छः", 7: "सात", 8: "आठ", 9: "नौ नव",
    10: "दश दस", 11: "एघार एगार", 12: "बाह्र बाहृ बार", 13: "तेह्र तेरह तेह", 14: "चौध", 15: "पन्ध्र पन्ध पन्द्र",
    16: "सोह्र सोर", 17: "सत्र", 18: "अठार अठाह्र", 19: "उन्नाइस उन्नाईस उन्नाइस", 20: "बीस विस",
    21: "एक्काइस एक्काईस", 22: "बाइस बाईस", 23: "तेइस तेईस", 24: "चौबीस चौविस", 25: "पच्चीस पच्चिस",
    26: "छब्बीस छब्बिस", 27: "सत्ताइस सत्ताईस", 28: "अठ्ठाइस अठ्ठाईस अट्ठाइस", 29: "उनन्तीस उनन्तिस",
    30: "तीस तिस", 31: "एकतीस एकत्तीस", 32: "बत्तीस", 33: "तेत्तीस तेत्तिस", 34: "चौँतीस चौंतीस चौतीस",
    35: "पैँतीस पैंतीस पैतीस", 36: "छत्तीस", 37: "सैँतीस सैंतीस सइतीस सईतिस सैतिस", 38: "अठतीस अड्तीस",
    39: "उनन्चालीस उन्चालीस उनन्चालिस", 40: "चालीस", 41: "एकचालीस", 42: "बयालीस ब्यालीस", 43: "त्रिचालीस",
    44: "चवालीस चौवालीस", 45: "पैँतालीस पैंतालीस", 46: "छयालीस", 47: "सतचालीस सत्चालीस", 48: "अठचालीस अठ्चालीस",
    49: "उनन्चास", 50: "पचास", 51: "एकाउन्न", 52: "बाउन्न", 53: "त्रिपन्न", 54: "चवन्न चौवन्न", 55: "पचपन्न",
    56: "छपन्न", 57: "सन्ताउन्न", 58: "अन्ठाउन्न", 59: "उनन्साठी", 60: "साठी", 61: "एकसट्ठी एकसठ्ठी",
    62: "बयसट्ठी बैसट्ठी बैसठ्ठी बैसाठी", 63: "त्रिसट्ठी त्रिसठ्ठी", 64: "चौसट्ठी चौसठ्ठी", 65: "पैँसट्ठी पैंसठ्ठी",
    66: "छयसट्ठी छैसट्ठी छैसठ्ठी", 67: "सतसट्ठी सरसट्ठी सरसठ्ठी सत्तसठ्ठी", 68: "अठसट्ठी अठसठ्ठी",
    69: "उनन्सत्तरी", 70: "सत्तरी सत्तरि", 71: "एकहत्तर", 72: "बहत्तर", 73: "त्रिहत्तर", 74: "चौहत्तर",
    75: "पचहत्तर", 76: "छयहत्तर", 77: "सतहत्तर", 78: "अठहत्तर", 79: "उनासी", 80: "असी अस्सी", 81: "एकासी",
    82: "बयासी", 83: "त्रियासी", 84: "चौरासी", 85: "पचासी", 86: "छयासी", 87: "सतासी", 88: "अठासी",
    89: "उनान्नब्बे उनानब्बे उनन्नब्बे उन्नानब्बे", 90: "नब्बे", 91: "एकानब्बे", 92: "बयानब्बे", 93: "त्रियानब्बे तिरानब्बे",
    94: "चौरानब्बे", 95: "पन्चानब्बे पञ्चानब्बे", 96: "छयानब्बे", 97: "सन्तानब्बे", 98: "अन्ठानब्बे", 99: "उनान्सय",
}  # fmt: skip
_SCALES = {
    "सय": 100,
    "हजार": 1000,
    "लाख": 100000,
    "करोड": 10**7,
    "अर्ब": 10**9,
    "अरब": 10**9,
}


def _fold(s: str) -> str:
    """Lenient spelling key: drop nukta/halant/chandrabindu/anusvara/visarga, merge
    short/long vowels and ब/व, श/ष/स, ट/ठ."""
    s = unicodedata.normalize("NFC", s).translate(_ZERO_WIDTH)
    for a, b in (
        ("\u093c", ""), ("्", ""), ("ँ", ""), ("ं", ""), ("ः", ""), ("ी", "ि"), ("ू", "ु"), ("ई", "इ"),
        ("ऊ", "उ"), ("व", "ब"), ("ष", "स"), ("श", "स"), ("ट", "ठ"), ("ै", "े"), ("ौ", "ो"), ("ड", "द"),
    ):  # fmt: skip
        s = s.replace(a, b)
    return s


def _hfold(s: str) -> str:
    """Heavier fold used only for number-word lookup (closed vocabulary; collisions are checked)."""
    s = _fold(s)
    for a, b in (
        ("ठ", "त"), ("ढ", "त"), ("द", "त"), ("थ", "त"), ("ध", "त"), ("ड", "त"), ("ण", "न"), ("ञ", "न"),
        ("ृ", "र"), ("य", "इ"), ("े", "ि"), ("ो", "ु"), ("ा", ""),
    ):  # fmt: skip
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)


def _build_word_key() -> dict[str, set[int]]:
    key: dict[str, set[int]] = {}
    for v, ws in _NUM_WORDS.items():
        for w in ws.split():
            key.setdefault(_hfold(w), set()).add(v)
    return key


_WORD_KEY = _build_word_key()
#: Folded keys that map to two numbers; treated as ambiguous. Pinned empty by a test.
NUMBER_WORD_COLLISIONS = {k: v for k, v in _WORD_KEY.items() if len(v) > 1}
_SCALE_KEY = {_fold(k): v for k, v in _SCALES.items()}
_RUPEE = {_fold(w) for w in "रुपैयाँ रुपैया रुपैंया रूपैयाँ रुपिया".split()}
_FILLER = _RUPEE | {
    _fold(w) for w in "मात्र मात्रै अक्षेरुपी अक्षरुपी अक्षरूपी अक्षरेपी अक्षेरूपी".split()
}
_PAISA = {_fold(w) for w in "पैसा पैंसा".split()}


def _lev1(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    if len(a) > len(b):
        a, b = b, a
    return any(b[:i] + b[i + 1 :] == a for i in range(len(b)))


def _word_value(tok: str) -> set[int] | None:
    """Number value of a word, or None. Fuzzy (edit distance 1 on the folded key) only when unambiguous."""
    if (
        tok.isdecimal()
    ):  # before folding: _hfold collapses repeated letters, so '11' -> '1'
        return {int(tok)}
    k = _hfold(tok)
    if k in _WORD_KEY:
        return _WORD_KEY[k]
    if len(k) >= 4:
        c: set[int] = set().union(
            *[v for kk, v in _WORD_KEY.items() if len(kk) >= 3 and _lev1(k, kk)]
            or [set()]
        )
        if c:
            return c  # more than one value: the caller treats it as ambiguous
    return None


def _split_scale(tok: str) -> list[str]:
    """'बैसाठीलाख' -> ['बैसाठी', 'लाख'];  'नौसय' -> ['नौ', 'सय']."""
    for sc in sorted(_SCALES, key=len, reverse=True):
        if tok.endswith(sc) and len(tok) > len(sc):
            return [tok[: -len(sc)], sc]
    return [tok]


def parse_amount_words(s: str) -> tuple[int, int | None, bool] | None:
    """Parse a Nepali amount in words: (rupees, paisa or None, ambiguous) or None."""
    toks: list[str] = []
    for t in re.split(r"[\s,()\-।/]+", s.translate(_TO_ASCII)):
        if t:
            toks.extend(_split_scale(t))
    total, cur, paisa, amb, seen_num = 0, 0, None, False, False
    pending: int | None = None  # last number not yet attached to a scale
    rupee_end = (
        False  # a रुपैयाँ word closed the rupee part; a following number + पैसा is paisa
    )
    i = 0
    last_scale = 10**12
    while i < len(toks):
        t = toks[i]
        k = _fold(t)
        if k in _SCALE_KEY:
            sc = _SCALE_KEY[k]
            if pending is None or sc >= last_scale:
                return None
            if sc == 100:
                cur += pending * 100
            else:
                total += (cur + pending) * sc
                cur = 0
                last_scale = sc
            pending = None
        elif k in _FILLER:
            if k in _RUPEE:
                if pending is not None:
                    cur += pending
                    pending = None
                rupee_end = True
        elif k in _PAISA:
            nxt = _word_value(toks[i + 1]) if i + 1 < len(toks) else None
            if nxt and len(nxt) == 1 and paisa is None:
                paisa = next(
                    iter(nxt)
                )  # '... पैसा बयालिस': the number after पैसा is paisa
                i += 1
            elif pending is not None and paisa is None and rupee_end:
                paisa = pending  # 'रुपैया पन्ध्र पैसा'
                pending = None
        else:
            v = _word_value(t)
            if not v:
                if seen_num:
                    break  # end of the words
                i += 1
                continue
            if len(v) > 1:
                amb = True
            if pending is not None or (rupee_end and paisa is not None):
                break  # two numbers in a row: end of the amount
            pending = min(v)
            seen_num = True
        i += 1
    if pending is not None:
        if rupee_end and paisa is None:
            paisa = pending
        else:
            cur += pending
    if not seen_num:
        return None
    return total + cur, paisa, amb


_FIG_RE = re.compile(
    rf"(?P<pre>(?:भा\.?\s?)?(?:रु|रू|₹)\.?\s*)(?P<fig>[{_D}](?:[{_D},]|, (?=[{_D}]{{2}},))*"
    rf"(?:[.।][{_D}]{{1,2}}(?![{_D}]))?)(?P<tail>\s*[।/]?-?\s*)"
)
_WORDS_RE = re.compile(
    r"^\s*\(?\s*((?:अक्षे?र\S*\s*)?[^\d०-९()]{3,200}?)(?:\)|मात्र|$|\n\n)"
)


def _fig_value(fig: str) -> tuple[int, int | None] | None:
    f = fig.translate(_TO_ASCII).replace(" ", "")
    m = re.fullmatch(r"([0-9,]+)(?:[.।]([0-9]{1,2}))?", f)
    if not m:
        return None
    return int(m.group(1).replace(",", "") or 0), (
        int(m.group(2)) if m.group(2) else None
    )


def check_amounts(text: str, correct: bool | str = False) -> tuple[str, list[OcrFlag]]:
    """Compare each रु. figure with the amount in words that follows it.

    * figure == words: nothing;
    * figure == words + '1'/'7' + paisa (the decimal danda read as a digit): fixed when
      ``correct`` is True or ``'decimal'`` and the words give the same paisa, else flagged;
    * one digit differs (same length) and the words parse unambiguously: fixed only
      when ``correct`` is True, else flagged;
    * any other mismatch: flagged.
    """
    flags: list[OcrFlag] = []
    out: list[str] = []
    last = 0
    for m in _FIG_RE.finditer(text):
        fig = m.group("fig").rstrip(",")
        wm = _WORDS_RE.match(text[m.end() : m.end() + 260])
        if not wm:
            continue
        pw = parse_amount_words(wm.group(1))
        if not pw:
            continue
        wv, wp, amb = pw
        fv = _fig_value(fig)
        if wv < 10 or fv is None:
            continue
        fint, fdec = fv
        fs, ws = str(fint), str(wv)
        if fint == wv and (wp is None or fdec is None or fdec == wp):
            continue
        start = m.start("fig")
        dev = _dev_majority(fig)
        new = None
        if (
            fdec is None
            and len(fs) == len(ws) + 3
            and fs.startswith(ws)
            and fs[len(ws)] in "17"
        ):
            msg = "decimal danda read as digit"
            pai = fs[len(ws) + 1 :]
            if wp is not None and int(pai) == wp and not amb:
                idx = [i for i, c in enumerate(fig) if c.translate(_TO_ASCII).isdigit()]
                cut = idx[-3]  # the '1'/'7' before the last two (paisa) digits
                new = fig[:cut] + "।" + fig[cut + 1 :]
            else:
                msg += " (paisa not confirmed by words)"
        elif (
            len(fs) == len(ws) and sum(a != b for a, b in zip(fs, ws, strict=True)) == 1
        ):
            msg = f"figure {fs} vs words {ws}: one digit differs"
            if not amb:
                i = next(
                    i for i, (a, b) in enumerate(zip(fs, ws, strict=True)) if a != b
                )
                idx = [k for k, c in enumerate(fig) if c.translate(_TO_ASCII).isdigit()]
                new = fig[: idx[i]] + _script(ws[i], dev) + fig[idx[i] + 1 :]
        else:
            fd = f".{fdec}" if fdec is not None else ""
            wd = f".{wp}" if wp is not None else ""
            msg = f"figure {fs}{fd} vs words {ws}{wd}"
        if new is not None and (
            correct is True or (correct == "decimal" and msg.startswith("decimal"))
        ):
            out.append(text[last:start] + new)
            last = start + len(fig)
            flags.append(OcrFlag("amount", "fixed", fig, new, msg, start))
        else:
            flags.append(
                OcrFlag(
                    "amount",
                    "flag",
                    fig,
                    new or "",
                    msg + ("" if new else " (no safe correction)"),
                    start,
                )
            )
    out.append(text[last:])
    return "".join(out), flags


# ---------------------------------------------------------------------------
# Rule 4: case numbers, उ.द.नं., short fiscal years, दफा spacing; repetition loops
# ---------------------------------------------------------------------------
# Case-type codes in AG/court case numbers (०८२-CR-००७६, ०८०-C1-०९८७). Kept small on
# purpose: a larger set makes digit->letter guesses ambiguous or wrong ('21' -> 'RI').
_CASE_CODES = {"CR", "C1", "CI", "WO", "RE"}
_CONFUSE = {"0": "COD", "2": "RZ", "1": "1IL", "5": "S", "8": "B", "6": "G"}
_CASE_RE = re.compile(
    rf"(?<![{_D}A-Za-z])([{_D}]{{3}})\s*-\s*([A-Za-z{_D}]{{2}})\s*-\s*([{_D}]{{4}})(?![{_D}])"
)
_UDN_RE = re.compile(
    rf"(उ\.?\s?द\.?\s?नं\.?\s*:?\s*(?:[{_D}]{{1,5}}\s+)?)([86G0(८६०])\s*-\s*([{_D}]{{6}})(?![{_D}])"
)
_SHORT_FY_RE = re.compile(
    rf"(?<=/)([0०][5-9५-९][{_D}])([/8169८१६९])([{_D}]{{2}})(?![{_D}])"
)
_DAFA_RE = re.compile(rf"(दफा|उपदफा|देहाय|परिच्छेद)\s*(-?)\s*([{_D}]+|\([^)]{{1,4}}\))")
_PAREN_RE = re.compile(
    r"\(\s+([^()\s]{1,4})\s+\)|\(\s+([^()\s]{1,4})\)|\(([^()\s]{1,4})\s+\)"
)
_CASE_CUE = re.compile(r"मुद्दा|मु\.\s?नं|दर्ता\s?नं|केस")


def normalise_numbers(text: str) -> tuple[str, list[OcrFlag]]:
    """Case-type codes read as digits, the उ.द.नं. 'C-' prefix, short fiscal years, दफा spacing."""
    flags: list[OcrFlag] = []

    def case_fix(m: re.Match[str]) -> str:
        yy, mid, nn = m.groups()
        mid = mid.translate(_TO_ASCII)
        whole = m.group(0)
        if mid.isalpha() or (not mid.isdigit() and mid.upper() in _CASE_CODES):
            new = f"{yy}-{mid}-{nn}"
            if new != whole:
                flags.append(
                    OcrFlag(
                        "number", "info", whole, new, "case number spacing", m.start()
                    )
                )
            return new
        if mid.upper() in _CASE_CODES:
            return whole
        opts = {
            "".join(p)
            for p in product(
                *[
                    (_CONFUSE.get(c, "") + (c if c.isalpha() else "")) or c
                    for c in mid.upper()
                ]
            )
        }
        hits = sorted(o for o in opts if o in _CASE_CODES)
        if len(hits) == 1:
            new = f"{yy}-{hits[0]}-{nn}"
            flags.append(
                OcrFlag(
                    "number",
                    "fixed",
                    whole,
                    new,
                    "case-type code read as digits",
                    m.start(),
                )
            )
            return new
        flags.append(
            OcrFlag(
                "number",
                "flag",
                whole,
                " | ".join(hits),
                "case-type code unreadable",
                m.start(),
            )
        )
        return whole

    def case_sub(m: re.Match[str]) -> str:
        # NNN-XX-NNNN is a case number only next to a case cue, or with letters in the middle
        if not _CASE_CUE.search(
            text[max(0, m.start() - 40) : m.start()]
        ) and not _LATIN_LETTER.search(m.group(2)):
            return m.group(0)
        return case_fix(m)

    text = _CASE_RE.sub(case_sub, text)

    def udn(m: re.Match[str]) -> str:
        new = f"{m.group(1)}C-{m.group(3)}"
        flags.append(
            OcrFlag(
                "number",
                "fixed",
                m.group(0),
                new,
                "उ.द.नं. prefix C read as digit",
                m.start(),
            )
        )
        return new

    text = _UDN_RE.sub(udn, text)

    def sfy(m: re.Match[str]) -> str:
        a, sep, b = m.groups()
        if (
            sep == "/"
            or int(b.translate(_TO_ASCII)) != (int(a.translate(_TO_ASCII)) + 1) % 100
        ):
            return m.group(0)
        new = f"{a}/{b}"
        flags.append(
            OcrFlag(
                "number",
                "fixed",
                m.group(0),
                new,
                "short fiscal year: slash read as digit",
                m.start(),
            )
        )
        return new

    text = _SHORT_FY_RE.sub(sfy, text)
    text = _DAFA_RE.sub(
        lambda m: f"{m.group(1)}{'-' if m.group(2) else ' '}{m.group(3)}", text
    )
    text = _PAREN_RE.sub(
        lambda m: "(" + (m.group(1) or m.group(2) or m.group(3)) + ")", text
    )
    return text, flags


def remove_loops(
    text: str, min_repeats: int = 4, min_chars: int = 20
) -> tuple[str, list[OcrFlag]]:
    """Collapse an n-gram (1-12 tokens) or a whole line repeated back-to-back
    ``min_repeats`` times to one copy. Table pipes, dots and dashes are not content,
    so empty markdown table cells are never a loop."""
    flags: list[OcrFlag] = []
    toks = text.split(" ")
    changed = True
    while changed:
        changed = False
        for n in range(1, 13):
            i = 0
            while i + 2 * n <= len(toks):
                unit = toks[i : i + n]
                r = 1
                while toks[i + r * n : i + (r + 1) * n] == unit:
                    r += 1
                content = re.sub(r"[\s|.\-_:*#]", "", " ".join(unit))
                if (
                    r >= min_repeats
                    and len(content) * r >= min_chars
                    and len(content) >= 2
                ):
                    flags.append(
                        OcrFlag(
                            "loop",
                            "fixed",
                            " ".join(unit)[:80],
                            "",
                            f"{n}-gram repeated {r}x",
                        )
                    )
                    toks[i + n : i + r * n] = []
                    changed = True
                i += 1
    lines = " ".join(toks).split("\n")
    out: list[str] = []
    j = 0
    while j < len(lines):
        k = j
        while k + 1 < len(lines) and lines[k + 1] == lines[j]:
            k += 1
        if k - j + 1 >= min_repeats and len(re.sub(r"[\s|.\-_:*#]", "", lines[j])) >= 5:
            flags.append(
                OcrFlag(
                    "loop", "fixed", lines[j][:80], "", f"line repeated {k - j + 1}x"
                )
            )
            out.append(lines[j])
        else:
            out.extend(lines[j : k + 1])
        j = k + 1
    return "\n".join(out), flags


# ---------------------------------------------------------------------------
# Pass 1: the rule pipeline
# ---------------------------------------------------------------------------
OCR_RULES: dict[str, Callable[[str], tuple[str, list[OcrFlag]]]] = {
    "loops": remove_loops,
    "dates": repair_dates,
    "dates_nocontext": lambda t: repair_dates(t, require_context=False),
    "numbers": normalise_numbers,
    "amounts_flag": lambda t: check_amounts(t, correct=False),
    "amounts_decimal": lambda t: check_amounts(t, correct="decimal"),
    "amounts_correct": lambda t: check_amounts(t, correct=True),
    "digits": normalise_digit_script,
}
#: The set measured on the gold set (gained 108 fields, regressed 0). ``digits``
#: changes only the script of digits, never their value.
DEFAULT_OCR_RULES = ("loops", "dates", "numbers", "amounts_decimal", "digits")


@dataclass
class OcrPostprocessResult:
    text: str
    search_text: str
    flags: list[OcrFlag] = field(default_factory=list)


def postprocess(
    text: str, rules: tuple[str, ...] = DEFAULT_OCR_RULES
) -> OcrPostprocessResult:
    """Run the named rules in order on one page of OCR text."""
    text = unicodedata.normalize("NFC", text)
    flags: list[OcrFlag] = []
    for r in rules:
        text, f = OCR_RULES[r](text)
        flags.extend(f)
    return OcrPostprocessResult(text, ocr_search_copy(text), flags)


# ---------------------------------------------------------------------------
# Pass 2: cross-check two (or three) readings of the same page
# ---------------------------------------------------------------------------
_SEP_CLASS = r"[/।|.\-]"
_R_DATE = re.compile(
    rf"^(?:मिति:?)?((?:19|20)\d\d){_SEP_CLASS}(\d{{1,2}}){_SEP_CLASS}(\d{{1,2}})(?:गते|मा|को|[,।)]*)$"
)
_R_FY = re.compile(r"^\(?(?:20)?\d\d\d?[/\-]\d\d\d?\)?[,।]?$")
_R_AMT_LAKH = re.compile(r"^\(?\d{1,2}(?:,\d\d)*,\d{3}(?:[.।]\d+)?\)?[/\-।]*$")
_R_AMT_WEST = re.compile(r"^\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?[/\-।]*$")
_R_CASE = re.compile(r"^\d{2,3}-[A-Z]{1,2}\d?-\d{4}[,।]?$")
_R_BROKEN_DATE = re.compile(
    r"^20[5-9]\d\d{4,6}$"
)  # 2078701117: separators read as digits
_R_CORE = re.compile(r"[0-9०-९A-Z]+(?:[,./।|\-][0-9०-९A-Z]+)*")


def _key(tok: str) -> str:
    return unicodedata.normalize("NFC", tok).translate(_ZERO_WIDTH).translate(_TO_ASCII)


def value_shape_score(value: str) -> int:
    """+1 a well-formed value (BS date in range, fiscal year, lakh or western amount,
    case number), -1 a damaged one (separators read as digits, Latin look-alikes in
    a number, broken grouping), 0 not a value."""
    t = _key(value).strip("()[]{}:;\"'")
    if not re.search(r"\d", t):
        return 0
    m = _R_DATE.match(t)
    if m:
        _, mo, d = map(int, m.groups())
        return 1 if 1 <= mo <= 12 and 1 <= d <= 32 else -1
    if (
        _R_FY.match(t)
        or _R_AMT_LAKH.match(t)
        or _R_AMT_WEST.match(t)
        or _R_CASE.match(t)
    ):
        return 1
    if _R_BROKEN_DATE.match(t):
        return -1
    if re.search(r"\d[OoIlS]|[OoIlS]\d", t) and not re.search(r"[A-Z]{2}", t):
        return -1
    if "," in t and re.fullmatch(r"[\d,.]+", t):
        return -1  # commas, but neither lakh nor western grouping
    return 0


def _cores(toks: list[str]) -> list[str]:
    """Numeric cores of a span: '७५,०००/-मेरो' -> ['75,000']; words dropped."""
    return [c for c in _R_CORE.findall(_key(" ".join(toks))) if re.search(r"\d", c)]


_DATE_CORE = re.compile(r"^\d{4}[/।|.\-]\d{1,2}[/।|.\-]\d{1,2}(?:[/।|.\-]\d{1,2})?$")


def _canon(cores: list[str]) -> list[str]:
    """Comparison form. Dates ignore separator style and leading zeros
    (2078/01/7 == 2078।1।07); every other value -- amounts above all -- is compared
    exactly, so 1,000.01 and 1,000.1 stay different."""
    return [
        re.sub(r"(?<!\d)0+(?=\d)", "", re.sub(r"[/।|.\-]", "/", c))
        if _DATE_CORE.match(c)
        else c
        for c in cores
    ]


def _has_digit(toks: list[str]) -> bool:
    return any(re.search(r"[0-9०-९]", t) for t in toks)


@dataclass(frozen=True)
class Disagreement:
    """A span two readings disagree on that no validator could settle.

    ``start``/``end`` locate the disputed occurrence in ``CrossCheckResult.text``
    (-1 when unknown), so a re-read answer replaces that occurrence and not an
    earlier identical one."""

    primary: str
    secondary: str
    start: int = -1
    end: int = -1


@dataclass
class CrossCheckResult:
    text: str
    fixed: list[OcrFlag] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)


def cross_check(
    primary: str, secondary: str, third: str | None = None, max_span: int = 6
) -> CrossCheckResult:
    """Keep ``primary``'s text and layout; replace a disputed span with ``secondary``'s
    only when it validates better (digits) or ``third`` agrees with it (2-of-3 vote).

    Digit spans whose values validate equally and still differ become
    ``Disagreement``s -- never guessed. A span is replaced in place: whitespace
    outside it (column gaps, indentation, line breaks) is kept byte for byte, and a
    span that would cross a line break is never replaced.
    """
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", primary)]
    at = [primary[s:e] for s, e in spans]
    bt = secondary.split()
    ka, kb = [_key(t) for t in at], [_key(t) for t in bt]
    cnorm = re.sub(r"\s+", "", _key(third)) if third is not None else None
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    # (primary char start, end, replacement text or None for a dispute, payload)
    events: list[tuple[int, int, str | None, Disagreement | None]] = []
    fixed: list[OcrFlag] = []

    def replace(i1: int, i2: int, b: list[str], why: str) -> None:
        s, e = spans[i1][0], spans[i2 - 1][1]
        if re.search(r"\n|\t| {2}", primary[s:e]):
            return  # never merge lines or close a column gap (_split_ocr_columns needs it)
        events.append((s, e, " ".join(b), None))
        fixed.append(OcrFlag("cross-check", "fixed", primary[s:e], " ".join(b), why, s))

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace" or i2 - i1 > max_span or j2 - j1 > max_span:
            continue
        a, b = at[i1:i2], bt[j1:j2]
        a_in = b_in = False
        if cnorm is not None:
            a_in, b_in = "".join(ka[i1:i2]) in cnorm, "".join(kb[j1:j2]) in cnorm
        if _has_digit(a) or _has_digit(b):
            if _canon(_cores(["".join(a)])) == _canon(_cores(["".join(b)])):
                continue  # same numbers; only spacing or attached words differ
            # score token by token (measured: joining tokens first lost 0.4 points)
            ca, cb = _cores(a), _cores(b)
            if not ca or not cb:
                continue  # one side holds no value at all: nothing comparable
            sa, sb = sum(map(value_shape_score, ca)), sum(map(value_shape_score, cb))
            if sb > sa:
                replace(i1, i2, b, "second reading validates")
                continue
            la, lb = len("".join(ca)), len("".join(cb))
            if sa != sb or min(la, lb) < 0.6 * max(la, lb):
                continue  # primary validates better, or one side is noise
            if b_in and not a_in:
                replace(i1, i2, b, "2-of-3 vote (digits)")
                continue
            s, e = spans[i1][0], spans[i2 - 1][1]
            events.append((s, e, None, Disagreement(primary[s:e], " ".join(b))))
        elif b_in and not a_in:
            replace(i1, i2, b, "2-of-3 vote (words)")
    out: list[str] = []
    disputes: list[Disagreement] = []
    last = shift = 0
    for s, e, new, d in sorted(events, key=lambda ev: ev[0]):
        out.append(primary[last:s])
        if d is not None:
            disputes.append(Disagreement(d.primary, d.secondary, s + shift, e + shift))
            out.append(primary[s:e])
        else:
            assert new is not None
            out.append(new)
            shift += len(new) - (e - s)
        last = e
    out.append(primary[last:])
    return CrossCheckResult("".join(out), fixed, disputes)


# ---------------------------------------------------------------------------
# Pass 3: re-read the disputed spans with a stronger model
# ---------------------------------------------------------------------------
def reread_prompt(disagreements: list[Disagreement]) -> str:
    """A prompt asking a vision model for the printed text of each disputed span."""
    items = "\n".join(
        f"{i + 1}. A: «{d.primary}»  B: «{d.secondary}»"
        for i, d in enumerate(disagreements)
    )
    return (
        "This image is one scanned page of a Nepali government document. Two OCR engines disagree on the "
        "spans below. For each numbered item, find the span on the page and give its exact printed text "
        "(same script and digits as printed; keep । / - punctuation as printed). It may match A, B, or "
        "neither. If you cannot read it, answer null.\n"
        f"{items}\n"
        'Answer with only a JSON object mapping item number to text, e.g. {"1": "...", "2": null}.'
    )


def parse_reread_answer(answer: str) -> dict[int, str]:
    """The first JSON object in a model answer, as {item number: text}; {} if none."""
    m = re.search(r"\{", answer or "")
    if not m:
        return {}
    try:
        obj = json.JSONDecoder().raw_decode(answer[m.start() :])[0]
    except json.JSONDecodeError:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[int, str] = {}
    for k, v in obj.items():
        if isinstance(v, str) and v.strip() and str(k).isdecimal():
            out[int(k)] = v.strip()
    return out


def apply_rereads(
    text: str, disagreements: list[Disagreement], answers: dict[int, str]
) -> tuple[str, list[Disagreement]]:
    """Substitute each answered span at its own position (``start``/``end`` from
    ``cross_check``). Without a position, only a primary reading that occurs exactly
    once is replaced. Anything else stays unresolved. Returns the new text and the
    disagreements still unresolved, with positions valid in the new text."""
    located: list[tuple[int, int, int, Disagreement]] = []  # (start, end, item no, d)
    left: list[Disagreement] = []
    for i, d in enumerate(disagreements, 1):
        start, end = d.start, d.end
        if start < 0 and text.count(d.primary) == 1:
            start = text.index(d.primary)
            end = start + len(d.primary)
        if start >= 0 and text[start:end] == d.primary:
            located.append((start, end, i, d))
        else:
            left.append(d)
    edits: list[tuple[int, int]] = []  # (position, length change)
    for start, end, i, d in sorted(located, key=lambda x: x[0], reverse=True):
        v = answers.get(i)
        if not v:
            left.append(Disagreement(d.primary, d.secondary, start, end))
            continue
        if v != d.primary:
            text = text[:start] + v + text[end:]
            edits.append((start, len(v) - (end - start)))
    # positions of the unresolved ones, moved past the edits that came before them
    moved = [
        Disagreement(
            d.primary,
            d.secondary,
            d.start + sum(dl for pos, dl in edits if pos < d.start)
            if d.start >= 0
            else -1,
            d.end + sum(dl for pos, dl in edits if pos < d.start)
            if d.start >= 0
            else -1,
        )
        for d in left
    ]
    return text, sorted(moved, key=lambda d: d.start)


def tesseract_reading(
    png: bytes, lang: str = "nep", timeout: float = 120
) -> str | None:
    """A free second reading from a local ``tesseract`` binary (tessdata_best ``nep``
    is the measured choice), or None when tesseract is absent or fails."""
    exe = shutil.which("tesseract")
    if exe is None:
        return None
    try:
        r = subprocess.run(  # noqa: S603 - fixed argv, image on stdin
            [exe, "stdin", "stdout", "-l", lang, "--psm", "3"],
            input=png,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")
