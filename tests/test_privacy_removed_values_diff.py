"""``transcript_removed_values`` without difflib's quadratic ``find_longest_match``.

The whole-text character diff was quadratic: production backfills spent 20-45 minutes per
court order inside ``difflib.find_longest_match``. The values it returns are what
``redact_pdf`` must find and burn on the page, so the fast matcher has to give exactly
difflib's opcodes, not similar ones. :func:`_reference_removed_values` is the old
implementation, kept verbatim as the oracle; the matcher is also checked against
``difflib.SequenceMatcher`` directly, on strings built to have many equally long matches.

All values are synthetic (the repo is public), and so are the names.
"""

from __future__ import annotations

import difflib
import random
import re
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from likhit.privacy import RedactionMode, redact_text
from likhit.privacy import pdf_ocr
from likhit.privacy.pdf_ocr import transcript_removed_values

_PLACEHOLDER = re.compile(r"\[REDACTED:([A-Z-]+)\]")


def _reference_removed_values(text: str, mode: RedactionMode) -> list[tuple[str, str]]:
    """The pre-change implementation: one character diff over the whole text."""
    redacted = redact_text(text, mode=mode).text
    if redacted == text:
        return []
    matcher = difflib.SequenceMatcher(None, text, redacted, autojunk=False)
    groups: list[list] = [[]]
    for op in matcher.get_opcodes():
        if op[0] == "equal" and op[2] - op[1] > 3:
            groups.append([])
        else:
            groups[-1].append(op)
    values: list[tuple[str, str]] = []
    for group in groups:
        ops = [op for op in group if op[0] != "equal"]
        if not ops:
            continue
        i1, i2, j1, j2 = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
        kinds = _PLACEHOLDER.findall(redacted[j1:j2])
        value = text[i1:i2].strip()
        if not kinds or not re.search(r"[0-9०-९@]", value):
            continue
        if len(kinds) > 1:
            parts = [
                p
                for p in re.split(r"[,;]\s*|\s+", value)
                if "@" in p or any(ch.isalnum() for ch in p)
            ]
            if len(parts) == len(kinds):
                values.extend(zip(kinds, parts))
                continue
        values.append((kinds[0], value))
    return values


NAMES = (
    "रामबहादुर थापा",
    "सीता कुमारी श्रेष्ठ",
    "हरिप्रसाद अधिकारी",
    "गीता देवी पौडेल",
    "Krishna Bahadur Karki",
)
WORDS = (
    "प्रतिवादी",
    "वादी",
    "अदालत",
    "मुद्दा",
    "फैसला",
    "जिल्ला",
    "काठमाडौं",
    "बयान",
    "रकम",
    "रु.",
    "बुझेको",
    "गरेको",
    "छ।",
    "हो।",
    "the",
    "court",
    "order",
    "witness",
    "मिति",
    "२०८०/०१/०२",
    "०७९-CR-०१२३",
    "५०,०००।-",
    "12,500",
    "धारा",
    "३",
    "नं.",
    "|",
    "/",
    ",",
)
_DEV = "०१२३४५६७८९"


def _digits(draw: st.DrawFn, n: int, devanagari: bool) -> str:
    alphabet = _DEV if devanagari else "0123456789"
    return draw(st.text(alphabet=alphabet, min_size=n, max_size=n))


@st.composite
def _sensitive(draw: st.DrawFn) -> str:
    dev = draw(st.booleans())
    nine = "९८" if dev else "98"
    shape = draw(st.integers(0, 8))
    if shape == 0:
        return f"सम्पर्क नं. {nine}{_digits(draw, 8, dev)}"
    if shape == 1:
        sep = draw(st.sampled_from([" | ", " / ", ", ", " र "]))
        return (
            f"मोबाइल नं. {nine}{_digits(draw, 8, dev)}{sep}{nine}{_digits(draw, 8, dev)}"
        )
    if shape == 2:
        parts = [_digits(draw, n, dev) for n in (2, 2, 2, 5)]
        return f"ना.प्र.नं. {'-'.join(parts)}"
    if shape == 3:
        return f"नागरिकता नं. {_digits(draw, 6, dev)}/{_digits(draw, 3, dev)}"
    if shape == 4:
        return f"खाता नं. {_digits(draw, 14, dev)}"
    if shape == 5:
        name = draw(st.sampled_from(NAMES))
        return f"| {name} | {nine}{_digits(draw, 8, dev)} | {_digits(draw, 5, dev)} |"
    if shape == 6:
        return f"इमेल ram{_digits(draw, 3, False)}@example.com"
    if shape == 7:
        return f"जन्म मिति २०{_digits(draw, 2, True)}/०५/१२"
    return f"मोबाइल नं. {nine}{_digits(draw, 4, dev)}-{_digits(draw, 4, dev)}"


_PIECE = st.one_of(
    st.sampled_from(WORDS),
    st.sampled_from(NAMES),
    _sensitive(),
    st.sampled_from(["\n", "\n\n", "\r\n", " ", "  "]),
    st.text(alphabet="0123456789०१२३४५६७८९ -/|", min_size=1, max_size=12),
)


@st.composite
def _transcripts(draw: st.DrawFn) -> str:
    pieces = draw(st.lists(_PIECE, min_size=0, max_size=120))
    return " ".join(pieces)


def _assert_same(text: str, mode: RedactionMode = RedactionMode.STRICT) -> None:
    redacted = redact_text(text, mode=mode).text
    assert (
        pdf_ocr._TranscriptMatcher(text, redacted).get_opcodes()
        == difflib.SequenceMatcher(None, text, redacted, autojunk=False).get_opcodes()
    )
    assert transcript_removed_values(text, mode) == _reference_removed_values(
        text, mode
    )


@pytest.fixture(params=[0, 40, 400])
def small_blocks(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> int:
    # The generated texts are a few KB at most; shrinking the range difflib still
    # searches itself sends them through the k-gram search (0: every range of at least
    # one k-gram, down to the smallest).
    monkeypatch.setattr(pdf_ocr, "REMOVED_VALUES_DIRECT_DIFF_CHARS", request.param)
    return request.param


@pytest.mark.parametrize("kgram", [2, 3, 5, 16])
@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    a=st.text(alphabet="ab|१२ \n", max_size=300),
    edits=st.lists(
        st.tuples(st.integers(0, 300), st.integers(0, 6), st.text("ab[]X", max_size=6)),
        max_size=8,
    ),
)
def test_matcher_gives_difflibs_opcodes_on_repetitive_strings(
    monkeypatch: pytest.MonkeyPatch,
    kgram: int,
    a: str,
    edits: list[tuple[int, int, str]],
) -> None:
    # A tiny alphabet makes equally long matches everywhere, so every tie-break (smallest
    # i, then smallest j) and every inherited-match shortcut is exercised; `b` is `a`
    # with spans replaced, the shape a redaction has.
    monkeypatch.setattr(pdf_ocr, "REMOVED_VALUES_DIRECT_DIFF_CHARS", 0)
    monkeypatch.setattr(pdf_ocr, "REMOVED_VALUES_KGRAM", kgram)
    b = a
    for at, cut, insert in edits:
        at = min(at, len(b))
        b = b[:at] + insert + b[at + cut :]
    fast = pdf_ocr._TranscriptMatcher(a, b)
    slow = difflib.SequenceMatcher(None, a, b, autojunk=False)
    assert fast.get_opcodes() == slow.get_opcodes()
    assert fast.find_longest_match(0, len(a), 0, len(b)) == slow.find_longest_match(
        0, len(a), 0, len(b)
    )


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    a=st.text(alphabet="ab|१२ \n", max_size=300),
    edits=st.lists(
        st.tuples(st.integers(0, 300), st.integers(0, 6), st.text("ab[]X", max_size=6)),
        max_size=8,
    ),
)
def test_the_automaton_gives_difflibs_opcodes_on_repetitive_strings(
    monkeypatch: pytest.MonkeyPatch, a: str, edits: list[tuple[int, int, str]]
) -> None:
    # Every search that would sample goes to the suffix automaton instead (the path a
    # repetitive range takes once its index hits pass the budget), so its answer --
    # every longest match with all its starts -- must settle the same opcodes.
    monkeypatch.setattr(pdf_ocr, "REMOVED_VALUES_DIRECT_DIFF_CHARS", 0)
    monkeypatch.setattr(pdf_ocr, "REMOVED_VALUES_KGRAM", 2)
    monkeypatch.setattr(
        pdf_ocr._TranscriptMatcher, "_sampled_longest", lambda self, *args: None
    )
    b = a
    for at, cut, insert in edits:
        at = min(at, len(b))
        b = b[:at] + insert + b[at + cut :]
    fast = pdf_ocr._TranscriptMatcher(a, b)
    slow = difflib.SequenceMatcher(None, a, b, autojunk=False)
    assert fast.get_opcodes() == slow.get_opcodes()


@settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(text=_transcripts())
def test_fast_matcher_returns_exactly_what_the_whole_text_diff_did(
    small_blocks: int, text: str
) -> None:
    _assert_same(text)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(text=_transcripts())
def test_every_mode_agrees_with_the_whole_text_diff(
    small_blocks: int, text: str
) -> None:
    for mode in RedactionMode:
        _assert_same(text, mode)


# Shapes the existing tests pin (tests/test_privacy_pdf_ocr.py) plus the edges of the
# matcher: empty text, nothing removed, no newline at all, CRLF, a value at the very
# start and end, a redaction joining lines.
_FIXTURES = (
    "",
    "रकम रु. ५०,०००।- बुझेको",
    "ना.प्र.नं. १२-३४-५६-७८९०१\nसम्पर्क नं. ९८५१२३४५६७, ९८०१२३४५६७ छ।\nरकम रु. ५०,०००।- बुझेको\n",
    "सम्पर्क नं. ९८५१२३४५६७ | ९८६१२३४५६७ बुझेको",
    "सम्पर्क नं. ९८५१२३४५६७ / ९८६१२३४५६७ बुझेको",
    "सम्पर्क नं. ९८५१२३४५६७, ९८६१२३४५६७ बुझेको",
    "| फोन | ९८५१२३४५६७ | ९८६१२३४५६७ |",
    "मोबाइल नं. ९८५१-२३४५६७ बुझेको",
    "खाता नं. १२३४५६ / ७८९०१२",
    "बैंक खाता नं. १२३४५६ / ७८९०१२ रहेको",
    "खाता नं. १२३४५६ ७८९०१२ रहेको",
    "सम्पर्क नं. ९८५१२३४५६७",
    "रामबहादुर थापा\r\nसम्पर्क नं. ९८५१२३४५६७\r\nसीता कुमारी श्रेष्ठ\r\n",
    "ना.प्र.नं.\n१२-३४-५६-७८९०१ भएका रामबहादुर थापा",
    "| नाम | ना.प्र.नं. | जिल्ला |\n|---|---|---|\n| रामबहादुर थापा | १२३४५/६७ | ओखलढुंगा |\n"
    "| सीता कुमारी श्रेष्ठ | ७६५४३/२१ | काठमाडौं |\n",
)


@pytest.mark.parametrize("text", _FIXTURES)
def test_known_shapes_match_the_whole_text_diff(small_blocks: int, text: str) -> None:
    _assert_same(text)


def _synthetic_transcript(size: int, newline: str = "\n") -> str:
    """A court-order-like transcript of ``size`` chars, one sensitive value per ~1.5 KB."""
    rng = random.Random(20261127)

    def digits(n: int) -> str:
        return "".join(rng.choice(_DEV) for _ in range(n))

    values = (
        lambda: f"सम्पर्क नं. ९८{digits(8)}",
        lambda: f"मोबाइल नं. 98{rng.randrange(10**7, 10**8)}",
        lambda: f"ना.प्र.नं. {digits(2)}-{digits(2)}-{digits(2)}-{digits(5)}",
        lambda: f"खाता नं. {digits(14)}",
        lambda: f"| {rng.choice(NAMES)} | ९८{digits(8)} | ९८{digits(8)} |",
        lambda: f"इमेल ram{rng.randrange(1000)}@example.com",
    )
    lines: list[str] = []
    total = 0
    while total < size:
        line = " ".join(rng.choice(WORDS[:-3]) for _ in range(rng.randrange(6, 18)))
        if rng.random() < 0.3:
            line = f"{rng.choice(NAMES)} {line}"
        if len(lines) % 18 == 0:
            line = f"{line} {rng.choice(values)()} {rng.choice(WORDS[:12])}"
        lines.append(line)
        if rng.random() < 0.05:
            lines.append("")
        total += len(line) + len(newline)
    return newline.join(lines) + newline


def _timed(text: str) -> tuple[list[tuple[str, str]], float]:
    start = time.perf_counter()
    values = transcript_removed_values(text, RedactionMode.STRICT)
    return values, time.perf_counter() - start


def test_a_300_kb_transcript_with_200_values_takes_seconds_not_minutes() -> None:
    # The whole-text diff took ~20 minutes on this (measured for likhit's PR); it must
    # now finish in well under the 3 s budget even on a loaded CI runner.
    text = _synthetic_transcript(300_000)
    redacted = redact_text(text, mode=RedactionMode.STRICT).text
    assert redacted.count("[REDACTED:") >= 200
    values, seconds = _timed(text)
    assert len(values) >= 200
    assert seconds < 3, f"{seconds:.1f}s"


def test_one_100_kb_line_with_no_newline_stays_fast() -> None:
    text = _synthetic_transcript(100_000, newline=" ")
    assert "\n" not in text
    values, seconds = _timed(text)
    assert len(values) >= 60
    assert seconds < 3, f"{seconds:.1f}s"


def _register(rows: int) -> str:
    """A table whose every row lost a phone number: hundreds of equally long matches,
    the shape that makes difflib's recursion peel one row per call."""
    rng = random.Random(7)
    lines = ["| क्र.सं. | नाम | सम्पर्क नं. |", "|---|---|---|"]
    for n in range(rows):
        phone = "".join(rng.choice(_DEV) for _ in range(8))
        lines.append(f"| {n} | {rng.choice(NAMES)} | ९८{phone} |")
    return "\n".join(lines) + "\n"


def test_a_register_with_a_phone_in_every_row_matches_the_whole_text_diff() -> None:
    _assert_same(_register(80))


def test_a_register_with_a_phone_in_every_row_stays_fast() -> None:
    # 1,500 rows, 1,500 values (~60 KB): the old diff took 158 s on 600 rows.
    values, seconds = _timed(_register(1500))
    assert len(values) == 1500
    assert seconds < 3, f"{seconds:.1f}s"


def test_a_mid_sized_transcript_matches_the_whole_text_diff() -> None:
    # Big enough for the k-gram search at the shipped direct-search size, small enough
    # for the quadratic oracle to finish in a few seconds.
    for newline in ("\n", "\r\n", " "):
        text = _synthetic_transcript(8_000, newline=newline)
        assert len(text) > pdf_ocr.REMOVED_VALUES_DIRECT_DIFF_CHARS
        _assert_same(text)


def _identical_rows(rows: int, plain_every: int = 0) -> str:
    """The same name on every row, a different phone on each: every row's window hits
    every other row in the index, rows x rows hits for one sampled search. With
    ``plain_every``, rows carry no value except one in ``plain_every`` -- long runs of
    identical lines between values."""
    rng = random.Random(11)
    lines = ["| नाम | सम्पर्क नं. |", "|---|---|"]
    for n in range(rows):
        phone = "".join(rng.choice(_DEV) for _ in range(8))
        if plain_every and n % plain_every:
            lines.append("सीता कुमारी श्रेष्ठ उपस्थित भइन्।")
        else:
            lines.append(f"| सीता कुमारी श्रेष्ठ | ९८{phone} |")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("plain_every", [0, 2, 25])
def test_identical_rows_match_the_whole_text_diff(plain_every: int) -> None:
    _assert_same(_identical_rows(90, plain_every))


@pytest.mark.parametrize("plain_every", [0, 2, 500])
def test_thousands_of_identical_rows_stay_fast(plain_every: int) -> None:
    # Before the automaton took over repetitive ranges, the matcher spent 39 s on 2,500
    # identical rows and 115 s on 5,000 (rows x rows index hits); the old whole-text
    # diff took 21 s on 200. The matcher alone is timed: redact_text has its own cost.
    text = _identical_rows(3000, plain_every)
    redacted = redact_text(text, mode=RedactionMode.STRICT).text
    start = time.perf_counter()
    opcodes = pdf_ocr._TranscriptMatcher(text, redacted).get_opcodes()
    seconds = time.perf_counter() - start
    replaced = sum(1 for op in opcodes if op[0] != "equal")
    assert replaced >= redacted.count("[REDACTED:")
    assert seconds < 3, f"{seconds:.1f}s"


def test_a_repetitive_range_goes_to_the_automaton_not_the_sampled_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The budget is what keeps the search linear: identical rows must trip it, and the
    # sampled search must visit no more index hits than it allows.
    visited: list[int] = []
    calls: list[int] = []
    sampled = pdf_ocr._TranscriptMatcher._sampled_longest
    automaton = pdf_ocr._removed_values_automaton_longest

    def counting(self, alo, ahi, blo, bhi, step, span):  # type: ignore[no-untyped-def]
        before = self._budget
        result = sampled(self, alo, ahi, blo, bhi, step, span)
        visited.append(before - self._budget)
        return result

    def automaton_counting(*args):  # type: ignore[no-untyped-def]
        calls.append(1)
        return automaton(*args)

    monkeypatch.setattr(pdf_ocr._TranscriptMatcher, "_sampled_longest", counting)
    monkeypatch.setattr(
        pdf_ocr, "_removed_values_automaton_longest", automaton_counting
    )
    text = _identical_rows(2000)
    redacted = redact_text(text, mode=RedactionMode.STRICT).text
    pdf_ocr._TranscriptMatcher(text, redacted).get_opcodes()
    assert calls
    # A search stops at a quarter of its range (a few hits past it in the round that
    # crosses); never anywhere near rows x rows = 4,000,000.
    assert sum(visited) < 4 * (len(text) + len(redacted))
