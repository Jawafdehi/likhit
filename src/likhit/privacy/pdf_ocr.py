"""Locate removal-set values on pages that have no usable text layer: scans and legacy fonts.

:mod:`likhit.privacy.pdf` redacts a Unicode page by deleting glyphs it can find in the page's
own text layer. A scanned page has no such layer (or a junk one written by the scanner's
Latin OCR), and a legacy-font page has bytes that decode to the wrong characters, so neither
can be redacted that way. This module supplies the two things those pages need instead:

1. **A locator** -- page text with one rectangle per character, read off a render of the page
   by Tesseract's hOCR character boxes (:func:`ocr_char_locator`). The rectangles are in PDF
   page space, so the same annotation machinery can burn them into the page's pixels.
2. **Guided values** -- every value likhit's *text* path removes from the page's transcript
   (:func:`transcript_removed_values`), located on the page by digit-stream matching
   (:func:`locate_value`). This is what makes the PDF agree with the published text: the
   rules alone, run on a Tesseract reading, miss values the transcript (usually a stronger
   OCR) caught, and a document whose PDF shows what its transcript hides is the failure this
   whole package exists to prevent.

🛑 **An approximate match is a box, never a proof.** Matching OCR digits approximately is
necessary (Tesseract drops or merges an edge digit often enough to matter) and is used to
draw boxes, because over-masking a similar number is acceptable here. But only an EXACT
digit match counts a value as placed. Measured on the JAW-167 POC corpus: counting approximate
matches as placed let a number under a court seal pass as "redacted" because a different
mobile number on the same page matched within the tolerance.

Nothing here calls a network service; ``tesseract`` is a local binary, and every function
that needs it says so by raising :class:`OcrUnavailable` rather than returning nothing.
"""

from __future__ import annotations

import bisect
import difflib
import html
import re
import shutil
import subprocess
from array import array
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import fitz

from ..renderers.markdown import PAGE_ANCHOR_PATTERN
from .modes import RedactionMode
from .text import redact_text

#: Render resolution for the locating read. 300 dpi is Tesseract's documented sweet spot and
#: what the JAW-167 POC measured (80/84 gold values covered with the passes below).
OCR_DPI = 300

#: Render resolution for the second read of a page whose guided values the first read could
#: not place exactly. Higher, and with a different language set (``nep`` alone): a second,
#: different reading is what recovers an edge digit the first one garbled.
OCR_SECOND_READ_DPI = 400

#: Vertical padding of a burned box, as a fraction of the glyph box height. OCR boxes hug the
#: ink; ascenders, matras and anti-aliasing sit just outside them.
OCR_BOX_PAD = 0.12

#: Horizontal padding of a burned box, as a fraction of its height (about half a glyph each
#: side). Tesseract's per-character boxes routinely stop one digit short of the visible edge
#: of a number; the POC's first renders showed a leading digit peeking out of the box.
OCR_BOX_GROW_X = 0.6

#: Seconds before one Tesseract call is abandoned (and the page fails rather than passes).
TESSERACT_TIMEOUT_SECONDS = 180

#: A guided value with fewer digits than this is never matched on its own: three digits
#: match ward numbers, ages and years everywhere on a page.
GUIDED_MIN_DIGITS = 3

#: A guided value shorter than this must sit right after something label-shaped (see
#: :data:`_SHORT_VALUE_LABEL`) to be matched. Citizenship numbers of 4-5 digits exist.
GUIDED_SHORT_VALUE_DIGITS = 6

#: Largest range (both sides' characters together) whose longest common substring
#: :func:`transcript_removed_values` leaves to difflib's own search, which costs roughly the
#: square of the range: 2,000 keeps one call in the low milliseconds. Bigger ranges use the
#: k-gram index (:class:`_TranscriptMatcher`); the answer is the same either way.
REMOVED_VALUES_DIRECT_DIFF_CHARS = 2000

#: Length of the substrings of the redacted text indexed for that search. Long enough that
#: a k-gram is rare in a transcript (few index hits per sample), short enough that the
#: unchanged text between two placeholders -- a name, a label -- still holds one; a range
#: with no common run this long falls back to difflib's search.
REMOVED_VALUES_KGRAM = 16

_TO_ASCII_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_PLACEHOLDER = re.compile(r"\[REDACTED:([A-Z-]+)\]")

#: What may precede a SHORT guided value. Tesseract ``nep+eng`` reads the small
#: ``ना.प्र.नं.`` label as Latin capitals (``ALIA,``, ``AMA.``, ``ALTA.``) often enough
#: that a Devanagari-only list misses real values; the value is already known to be
#: labelled in the transcript, so a label-SHAPED token is enough here.
_SHORT_VALUE_LABEL = re.compile(
    r"(प्र|प\.|नं|न\.|मो|फोन|खाता|ब्या|no|No|NO|#|:|ि|[A-Z][A-Za-z]{1,4}[.,])"
)
#: A column header that labels the identifier cells below it (a shareholder register's
#: `ना.प्र.नं.र प्राप्त गरेको जिल्ला`), as read on the page -- OCR included, which turns the
#: dots into commas or drops them.
_ID_COLUMN_HEADER = re.compile(r"ना[.,]?\s*प्र[.,]?\s*नं|नागरिकता|citizenship", re.I)
_DIGITISH = re.compile(r"[0-9०-९A-Za-z\-/–_|']")
_TESSERACT_TOKEN = re.compile(
    r"<span class='(ocr_line|ocr_caption|ocr_textfloat|ocr_header|ocrx_word)'"
    r"|<span class='ocrx_cinfo' title='x_bboxes (\d+) (\d+) (\d+) (\d+)[^']*'>(.*?)</span>",
    re.S,
)


class OcrUnavailable(RuntimeError):
    """Tesseract (or the language data asked for) is not installed, or a read failed."""


@dataclass
class Locator:
    """Page text with ``rects[i]`` the page-space rectangle of ``text[i]`` (or ``None``).

    ``ocr`` marks a locator read off a render: its boxes are padded and grown when burned,
    and approximate digit matching is allowed on it. A glyph map is exact and gets neither.
    """

    text: str
    rects: list
    ocr: bool


def tesseract_available(lang: str = "nep") -> bool:
    exe = shutil.which("tesseract")
    if exe is None:
        return False
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            [exe, "--list-langs"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    langs = set(out.stdout.decode("utf-8", "replace").split())
    return all(part in langs for part in lang.split("+"))


def parse_hocr_chars(hocr: str, scale: float) -> tuple[str, list]:
    """``(text, rects)`` from Tesseract hOCR with ``hocr_char_boxes``.

    A line start becomes ``\\n`` and a word start a space (both with no rectangle), so the
    text keeps the line structure the detectors' patterns rely on. ``scale`` maps image
    pixels to PDF points.
    """

    chars: list[str] = []
    rects: list = []
    for m in _TESSERACT_TOKEN.finditer(hocr):
        if m.group(1):
            sep = " " if m.group(1) == "ocrx_word" else "\n"
            if chars and chars[-1] not in ("\n", " "):
                chars.append(sep)
                rects.append(None)
            elif chars and sep == "\n" and chars[-1] == " ":
                chars[-1] = "\n"
            continue
        x0, y0, x1, y1, cluster = m.group(2, 3, 4, 5, 6)
        rect = fitz.Rect(
            int(x0) * scale, int(y0) * scale, int(x1) * scale, int(y1) * scale
        )
        # One cinfo can carry a grapheme cluster; every codepoint in it gets its box.
        for ch in html.unescape(cluster):
            chars.append(ch)
            rects.append(rect)
    return "".join(chars), rects


def ocr_char_locator(
    page: fitz.Page, *, dpi: int = OCR_DPI, lang: str = "nep+eng", psm: str = "3"
) -> Locator:
    """Read ``page``'s render with Tesseract; rectangles are in unrotated page space."""

    exe = shutil.which("tesseract")
    if exe is None:
        raise OcrUnavailable("tesseract is not installed")
    # annots=False: the page body is what gets burned; an annotation's text is removed by
    # the structured sweep, and must not supply placement credit for a body copy.
    png = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, annots=False).tobytes("png")
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, image on stdin
            [
                exe,
                "stdin",
                "stdout",
                "-l",
                lang,
                "--psm",
                psm,
                "-c",
                "hocr_char_boxes=1",
                "-c",
                "tessedit_create_hocr=1",
            ],
            input=png,
            capture_output=True,
            timeout=TESSERACT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OcrUnavailable(
            f"tesseract failed on page {page.number + 1}: {exc}"
        ) from exc
    if out.returncode != 0:
        raise OcrUnavailable(
            f"tesseract exited {out.returncode} on page {page.number + 1}: "
            f"{out.stderr.decode('utf-8', 'replace')[:200]}"
        )
    text, rects = parse_hocr_chars(out.stdout.decode("utf-8", "replace"), 72.0 / dpi)
    if (
        page.rotation
    ):  # the render is the rotated view; annotations want unrotated space
        derotate = page.derotation_matrix
        rects = [None if r is None else r * derotate for r in rects]
    return Locator(text, rects, ocr=True)


# --------------------------------------------------------------------------- guided values
def split_transcript_pages(markdown: str) -> dict[int, str] | str:
    """Split a likhit transcript into ``{page_number: text}`` on its page anchors.

    Text before the first anchor is dropped. A transcript with NO anchors is returned
    unchanged, as a ``str``: it is a whole-document transcript, and :func:`redact_pdf
    <likhit.privacy.pdf.redact_pdf>` places its values anywhere in the document. Mapping
    it to page 1 -- as this did -- checked a 40-page document's values against its first
    page only, and held nearly every real transcript (production's have no anchors).
    """

    anchors = list(PAGE_ANCHOR_PATTERN.finditer(markdown))
    if not anchors:
        return markdown
    pages: dict[int, str] = {}
    for i, anchor in enumerate(anchors):
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(markdown)
        number = int(anchor.group(1))
        pages[number] = pages.get(number, "") + markdown[anchor.end() : end]
    return pages


def transcript_removed_values(text: str, mode: RedactionMode) -> list[tuple[str, str]]:
    """``(placeholder kind, value)`` for every span :func:`redact_text` removes from ``text``.

    Derived by diffing the text against its own redaction, so it covers every pass the text
    path runs -- inline, contact AND the table pass, which the PDF glyph path has no
    equivalent of -- without restating any rule. Values the diff merges across a
    separator (``98…, 98…``, ``98… | 98…``) are split so each is located on its own, but
    only when the text path left one placeholder per value: a span it removed as ONE value
    (``खाता नं. १२३४५६ / ७८९०१२``) stays whole, never cut into fragments that could match
    unrelated numbers on the page.

    The diff is difflib's, opcode for opcode, but its longest-match search is the
    near-linear one of :class:`_TranscriptMatcher`: difflib's own took 20-45 minutes on a
    long court order.
    """

    redacted = redact_text(text, mode=mode).text
    if redacted == text:
        return []
    # difflib splits a value wherever its characters happen to recur in the placeholder;
    # merge non-equal opcodes separated by an equal run of 3 characters or fewer.
    groups: list[list] = [[]]
    for op in _TranscriptMatcher(text, redacted).get_opcodes():
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
        # The placeholder count, not the value's shape, says how many values this is.
        # Several placeholders: the merge above joined values the text path removed
        # separately -- `98… | 98…` (adjacent table cells), `98… / 98…`, `98…, 98…` --
        # and a part that is only punctuation is the separator between them, not part
        # of either (kept, it made the pair one "value" no page prints: live charge
        # sheets held 28 phone numbers this way). One placeholder: the text path
        # removed ONE value (`खाता नं. १२३४५६ / ७८९०१२`), kept whole. Split, its
        # fragments are short numbers that match unrelated amounts on the page and
        # earn placement credit while the value itself stays visible.
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


#: The longest matches of one range, grouped by their text: ``text -> (sorted starts in
#: a, sorted starts in b)``. Every start in ``a`` pairs with every start in ``b``.
_LongestMatches = dict[str, tuple[list[int], list[int]]]


class _TranscriptMatcher(difflib.SequenceMatcher):
    """``SequenceMatcher(None, a, b, autojunk=False)`` with the same opcodes, but fast.

    🛑 ``difflib``'s :meth:`find_longest_match` walks, for each character of ``a``, every
    position of that character in ``b``: quadratic on a transcript, which is tens to
    hundreds of KB over a ~60-letter alphabet. Production backfills spent 20-45 minutes per
    court order inside it. Only that method is replaced; ``get_matching_blocks`` and
    ``get_opcodes`` are difflib's own and recurse exactly as before. The replacement returns
    the SAME match difflib's would -- the longest common substring of the two ranges, ties
    going to the smallest ``i``, then the smallest ``j`` -- so the opcodes are identical,
    not merely similar: :func:`transcript_removed_values` turns them into the values a PDF
    must burn, and "close" would mean a different set of boxes.

    * A range of at most :data:`REMOVED_VALUES_DIRECT_DIFF_CHARS` characters goes to
      difflib itself, on slices (its answer does not depend on where the range sits).
    * A larger one is searched through an index of ``b``'s
      :data:`REMOVED_VALUES_KGRAM`-character substrings: sampling ``a`` every ``step``
      characters meets every common substring of at least ``step + KGRAM - 1`` characters,
      so once the longest one met is that long, it is the longest there is. ``b`` is ``a``
      with a few spans swapped for placeholders, so the unchanged stretches are long and a
      handful of samples settle a range.
    * A table whose every row lost a value is the opposite: hundreds of equally long
      matches, and difflib's recursion peels one row per call. The search that settles a
      range finds all of its longest matches, grouped by text; the range after the answer
      inherits them, and while it still holds one whole, the earliest is its answer with no
      new search (a longer or equally long match there would be one of them). Nothing goes
      to the range before the answer: it cannot hold one, the answer being the earliest.
      Inherited lists hold starts, never pairs: N rows are 2N numbers, not N x N tuples.
    * Identical rows defeat sampling: each row's window hits every other row, N x N
      index hits for one search. A search that visits more hits than a quarter of its
      range's length stops and hands the range to
      :func:`_removed_values_automaton_longest`, which is linear whatever repeats.
    """

    def __init__(self, a: str, b: str) -> None:
        super().__init__(None, a, b, autojunk=False)
        self._kgrams: dict[str, list[int]] | None = None
        self._budget = 0  # index hits the current search may still visit
        #: (alo, ahi, blo, bhi) -> every longest match of the range that range came from.
        self._inherited: dict[tuple[int, int, int, int], _LongestMatches] = {}

    def find_longest_match(
        self, alo: int = 0, ahi: int | None = None, blo: int = 0, bhi: int | None = None
    ) -> difflib.Match:
        a, b = self.a, self.b
        ahi = len(a) if ahi is None else ahi
        bhi = len(b) if bhi is None else bhi
        inherited = self._inherited.pop((alo, ahi, blo, bhi), None)
        width = REMOVED_VALUES_KGRAM
        if (ahi - alo) + (bhi - blo) <= REMOVED_VALUES_DIRECT_DIFF_CHARS or (
            min(ahi - alo, bhi - blo) < width
        ):
            match = difflib.SequenceMatcher(
                None, a[alo:ahi], b[blo:bhi], autojunk=False
            ).find_longest_match()
            return difflib.Match(alo + match.a, blo + match.b, match.size)
        if inherited is not None:
            settled = self._settled(alo, ahi, blo, bhi, inherited)
            if settled is not None:
                return settled
        # Coarse to fine. A round looking for matches of at least `need` characters
        # samples `a` every `step` characters and checks the `span` characters after each
        # sample (step + span - 1 == need, so every such match covers one whole window).
        # The first round that finds any has found all of them, the longest included.
        need = min(ahi - alo, bhi - blo)
        # Index hits the rounds may visit before the range counts as repetitive and
        # goes to the automaton instead. Prose visits under 1% of the range's length
        # (300 KB transcript: 0.6%), a register of distinct names up to ~60%, and N
        # identical rows N x N: a quarter of the range bounds the work either way.
        self._budget = ((ahi - alo) + (bhi - blo)) // 4
        while True:
            span = max(width, need * 3 // 4)
            longest = self._sampled_longest(alo, ahi, blo, bhi, need - span + 1, span)
            if longest is None:
                longest = _removed_values_automaton_longest(a, b, alo, ahi, blo, bhi)
                if not longest:
                    return difflib.Match(alo, blo, 0)
            if longest:
                settled = self._settled(alo, ahi, blo, bhi, longest)
                if settled is not None:  # always: every match found lies in the range
                    return settled
                break
            if need == width:
                break
            need = max(width, need * 3 // 4)
        # No common run as long as one k-gram: rare in a transcript, left to difflib.
        match = difflib.SequenceMatcher(
            None, a[alo:ahi], b[blo:bhi], autojunk=False
        ).find_longest_match()
        return difflib.Match(alo + match.a, blo + match.b, match.size)

    def _settled(
        self, alo: int, ahi: int, blo: int, bhi: int, longest: _LongestMatches
    ) -> difflib.Match | None:
        """The earliest of ``longest`` lying whole in the range (smallest ``i``, then
        ``j``), or ``None`` if none does; ``longest`` is handed on to the range after it."""
        best: tuple[int, int, int] | None = None
        for text, (starts_a, starts_b) in longest.items():
            size = len(text)
            n = bisect.bisect_left(starts_a, alo)
            m = bisect.bisect_left(starts_b, blo)
            if n == len(starts_a) or m == len(starts_b):
                continue
            i, j = starts_a[n], starts_b[m]
            if (
                i + size <= ahi
                and j + size <= bhi
                and (best is None or (i, j) < best[:2])
            ):
                best = (i, j, size)
        if best is None:
            return None
        i, j, size = best
        if i + size < ahi and j + size < bhi:  # difflib skips an empty range
            self._inherited[(i + size, ahi, j + size, bhi)] = longest
        return difflib.Match(i, j, size)

    def _sampled_longest(
        self, alo: int, ahi: int, blo: int, bhi: int, step: int, span: int
    ) -> _LongestMatches | None:
        """The longest of the maximal matches covering ``a[p:p + span]`` for a sample ``p``
        taken every ``step`` characters (``span`` >= KGRAM), all of them, grouped by text;
        empty if none is ``step + span - 1`` characters long. Every match that long
        covers such a window, so the result is the range's longest matches whenever it is
        not empty."""
        a, b = self.a, self.b
        width = REMOVED_VALUES_KGRAM
        need = step + span - 1
        if self._kgrams is None:
            self._kgrams = {}
            for q in range(len(b) - width + 1):
                self._kgrams.setdefault(b[q : q + width], []).append(q)
        # A match of step + span - 1 characters starts at most step - 1 before some sample
        # p and so covers the window a[p:p + span]. Every k-gram in that window recurs in
        # `b` at the same offset, so look up the RAREST of a few: a repeated name has
        # hundreds of index hits, the window around it rarely more than one. Four evenly
        # spread k-grams are enough to find a rare one; more only cost lookups.
        offsets = sorted({(span - width) * n // 3 for n in range(4)})
        best = need
        found: dict[str, tuple[set[int], set[int]]] = {}
        # Diagonal j - i -> end of the match met on it, so a later window inside that
        # match is not extended again. Only ends past the current window matter, so the
        # map is pruned as `p` advances.
        reached: dict[int, int] = {}
        prune_at = 1024
        for p in range(alo, ahi - span + 1, step):
            hits: list[int] = []
            offset = 0
            for o in offsets:
                hit = self._kgrams.get(a[p + o : p + o + width])
                if not hit:
                    hits = []
                    break
                if not hits or len(hit) < len(hits):
                    hits, offset = hit, o
            if not hits:
                continue
            if len(reached) > prune_at:
                reached = {d: end for d, end in reached.items() if end >= p + span}
                prune_at = max(1024, 2 * len(reached))
            low = bisect.bisect_left(hits, blo + offset)
            high = bisect.bisect_right(hits, bhi - span + offset)
            self._budget -= high - low
            if self._budget < 0:
                return None  # repetitive: rows x rows pairs; see the automaton
            for q in hits[low:high]:
                q -= offset
                if reached.get(q - p, -1) >= p + span:
                    continue  # this window sits inside a match already met
                if span > width and a[p : p + span] != b[q : q + span]:
                    continue  # no match covers the whole window here
                ahead = _removed_values_common_run(a, b, p, q, min(ahi - p, bhi - q))
                short = best - ahead  # characters the match must still have before p
                if short > 0 and (
                    short > min(p - alo, q - blo)
                    or a[p - short : p] != b[q - short : q]
                ):
                    continue  # shorter than the longest met so far
                back = _removed_values_common_run(a, b, p, q, -min(p - alo, q - blo))
                size = back + ahead
                reached[q - p] = p - back + size
                if size > best or not found:
                    best, found = size, {}
                text = a[p - back : p + ahead]
                starts_a, starts_b = found.setdefault(text, (set(), set()))
                starts_a.add(p - back)
                starts_b.add(q - back)
        return {
            text: (sorted(starts_a), sorted(starts_b))
            for text, (starts_a, starts_b) in found.items()
        }


def _removed_values_automaton_longest(
    a: str, b: str, alo: int, ahi: int, blo: int, bhi: int
) -> _LongestMatches:
    """Every longest common substring of ``a[alo:ahi]`` and ``b[blo:bhi]``, with ALL its
    starts in each: the search for a range too repetitive to sample (identical rows meet
    rows x rows index hits). Linear in the range whatever repeats in it.

    A suffix automaton of the ``b`` range (the standard construction: per state its
    longest length, suffix link and transitions) is walked with ``a``; at each end ``e``
    the walk holds the longest substring ending at ``e`` that occurs in the ``b`` range.
    Every occurrence in ``a`` of a longest common substring ends where that length
    peaks, so the peaks give all its starts in ``a``; ``str.find`` gives those in ``b``.
    A state keeps its first transition in two arrays and only the rest in a dict (most
    states have one), so the automaton of a 400 KB range takes ~15 MB, not ~230 MB.
    Empty if nothing matches."""
    size = 2 * (bhi - blo) + 2  # an automaton has fewer states than that
    length = array("i", [0]) * size
    link = array("i", [-1]) * size
    first = array("i", [-1]) * size  # code point of the first transition, -1: none
    target = array("i", [0]) * size
    more: dict[int, dict[int, int]] = {}

    def step(state: int, code: int) -> int:
        if first[state] == code:
            return target[state]
        rest = more.get(state)
        return -1 if rest is None else rest.get(code, -1)

    def add(state: int, code: int, to: int) -> None:
        if first[state] in (code, -1):
            first[state], target[state] = code, to
        else:
            more.setdefault(state, {})[code] = to

    states, last = 1, 0
    for ch in b[blo:bhi]:
        code = ord(ch)
        cur, states = states, states + 1
        length[cur], link[cur] = length[last] + 1, 0
        state = last
        while state != -1 and step(state, code) == -1:
            add(state, code, cur)
            state = link[state]
        if state != -1:
            nxt = step(state, code)
            if length[state] + 1 == length[nxt]:
                link[cur] = nxt
            else:
                clone, states = states, states + 1
                length[clone], link[clone] = length[state] + 1, link[nxt]
                first[clone], target[clone] = first[nxt], target[nxt]
                if nxt in more:
                    more[clone] = dict(more[nxt])
                while state != -1 and step(state, code) == nxt:
                    add(state, code, clone)
                    state = link[state]
                link[nxt] = link[cur] = clone
        last = cur
    state = run = best = 0
    ends: list[int] = []
    for e in range(alo, ahi):
        code = ord(a[e])
        while state and step(state, code) == -1:
            state = link[state]
            run = length[state]
        nxt = step(state, code)
        if nxt != -1:
            state, run = nxt, run + 1
        if run > best:
            best, ends = run, [e]
        elif run == best and best:
            ends.append(e)
    starts_a: dict[str, list[int]] = {}
    for e in ends:
        starts_a.setdefault(a[e + 1 - best : e + 1], []).append(e + 1 - best)
    longest: _LongestMatches = {}
    for text, starts in starts_a.items():
        starts_b: list[int] = []
        at = b.find(text, blo, bhi)
        while at != -1:
            starts_b.append(at)
            at = b.find(text, at + 1, bhi)
        longest[text] = (starts, starts_b)
    return longest


def _removed_values_common_run(a: str, b: str, p: int, q: int, limit: int) -> int:
    """Length of the common run of ``a`` and ``b`` from ``p``/``q`` forward (``limit`` > 0)
    or backward (``limit`` < 0), at most ``abs(limit)``. Compared in doubling slices, so a
    50 KB unchanged stretch costs a dozen C-speed comparisons rather than a Python loop,
    and a short run one."""
    run, chunk, cap = 0, 32, abs(limit)
    while run < cap:
        size = min(chunk, cap - run)
        if limit > 0:
            x, y = a[p + run : p + run + size], b[q + run : q + run + size]
        else:
            x = a[p - run - size : p - run][::-1]
            y = b[q - run - size : q - run][::-1]
        if x != y:
            low, high = 0, size  # x[:low] == y[:low], x[:high] != y[:high]
            while high - low > 1:
                mid = (low + high) // 2
                if x[:mid] == y[:mid]:
                    low = mid
                else:
                    high = mid
            return run + low
        run += size
        chunk *= 2
    return run


def _digits(text: str) -> str:
    return "".join(c for c in text.translate(_TO_ASCII_DIGITS) if c in "0123456789")


def _digit_stream(text: str) -> tuple[str, list[int]]:
    index = [
        i for i, c in enumerate(text) if c.translate(_TO_ASCII_DIGITS) in "0123456789"
    ]
    return "".join(text[i].translate(_TO_ASCII_DIGITS) for i in index), index


def _bounded_levenshtein(a: str, b: str, cap: int) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


#: What may stand between two digits of one written value: whitespace and the
#: separators IDs, dates and phone numbers are printed with (the danda in
#: `२०४०।०५।१२`; `|` is how OCR often reads a danda or a slash).
#: Not a newline: a line break joins two digits only after a printed separator (below).
_SEPARATORS = re.compile(r"[ \t\-–/.,:।|]*")
MAX_SEPARATOR_RUN = 3


def _joins_one_value(gap: str) -> bool:
    """Whether ``gap`` (the text between two digits) keeps them in one written value.

    Separators only, a few at a time; or a line break with at most one stray mark beside
    it -- a value wrapped at the end of a line, where OCR often reads the margin or a
    hyphen as a letter (`##-##-##-\\nछ #####` on a JAW-167 charge sheet).
    """

    if len(gap) <= MAX_SEPARATOR_RUN and _SEPARATORS.fullmatch(gap):
        return True
    # A wrap only after a printed separator (`२७-०१-७५-` / `२०४०।०५।`): a value is broken
    # at its separators, while `98512\n34567` -- digits ending one line and starting the
    # next -- is as likely two values as one.
    if "\n" in gap and len(gap) <= MAX_SEPARATOR_RUN + 1 and gap[0] in "-–/.।|":
        return len(_SEPARATORS.sub("", gap.replace("\n", ""))) <= 1
    return False


#: A value wrapped at a line end with nothing printed at the break, or with a hyphen
#: the typesetter inserted there (`98000-` / `00001`, `98000` / `-00001`): spaces, at most
#: one hyphen, exactly one line break.
_BARE_WRAP = re.compile(r"[ \t]*[-–]?[ \t]*\n[ \t]*[-–]?[ \t]*")


def _wraps_one_value(gap: str) -> bool:
    """Whether ``gap`` is a bare line wrap inside one value (see :data:`_BARE_WRAP`).

    Weaker evidence than :func:`_joins_one_value` -- digits ending one line and digits
    starting the next can be two fields -- so :func:`locate_value` accepts it only for a
    long value, at most once per match, where the two lines' digits must still spell the
    whole value exactly and stand alone (no digit before or after).
    """

    return gap.count("\n") == 1 and len(gap) <= 8 and bool(_BARE_WRAP.fullmatch(gap))


def _email_pattern(needle: str) -> str:
    """A regex for the address ``needle`` that also matches it wrapped over lines.

    Between any two characters the page may break the line, with or without a hyphen
    inserted at the break (`name@kathmandu-` / `post.example`; `name@kath-` / `mandu.np`).
    The address's own characters must all be there, in order: a hyphen of the address
    dropped at the break is not matched, so `kathmandu` / `post` does not place
    `kathmandu-post`.
    """

    wrap = r"(?:-?[ \t]*\n[ \t]*)?"
    return wrap.join(re.escape(ch) for ch in needle)


def _email_continues_before(text: str, start: int) -> bool:
    """Whether the line before ``start`` ends with an address mark (`-_%+@`), so the
    address at ``start`` may be the tail of a longer one wrapped there. Not a period:
    that ends a sentence or a label (`Email address.` ⏎ `ann@example.com`) far more
    often than it splits an address. Walks the whole whitespace run back (a fixed
    window would be outrun by a long one)."""

    i = start
    while i and text[i - 1] in " \t":
        i -= 1
    if not i or text[i - 1] != "\n":
        return False
    i -= 1
    while i and text[i - 1] in " \t":
        i -= 1
    return bool(i) and text[i - 1] in "-_%+@"


def locate_value(
    loc: Locator, value: str, *, approximate: bool
) -> list[tuple[int, int]]:
    """Character spans of ``loc.text`` holding ``value``; separators are ignored.

    Exact digit-stream matches always; with ``approximate`` (OCR locators only) also
    windows within ``len // 4`` edits. Short values need a label-shaped token just before.
    A value wrapped over two lines is found whole: an address at any character, a number
    of :data:`GUIDED_SHORT_VALUE_DIGITS` digits or more at one bare line break (with or
    without a hyphen at it) or at any break after a printed separator.
    """

    if "@" in value:
        needle, hay = value.strip().lower(), loc.text.lower()
        # A whole address, not part of a longer one (`ann@` inside `joann@`, or a
        # domain that continues): only then does a match prove THIS value is placed.
        # ...nor one that continues over a line break: a next line starting with an
        # address mark (`ann@example.com` ⏎ `.np`), or a previous line ending with one
        # other than a sentence's period (`jo-` ⏎ `ann@example.com`). Either may be a
        # longer, different address.
        spans = [
            (m.start(), m.end())
            for m in re.finditer(
                rf"(?<![\w.%+-]){_email_pattern(needle)}"
                r"(?![\w-]|\.\w|[ \t]*\n[ \t]*[.\-_%+@])",
                hay,
            )
            if not _email_continues_before(hay, m.start())
        ]
        if not spans and approximate:
            local = needle.split("@")[0]
            start = hay.find(local) if len(local) >= 4 else -1
            if start >= 0:
                end = hay.find(" ", start)
                spans.append((start, end if end > 0 else len(hay)))
        return spans

    want = _digits(value)
    n = len(want)
    if n < GUIDED_MIN_DIGITS:
        return []
    stream, index = _digit_stream(loc.text)
    headers = _column_headers(loc) if n < GUIDED_SHORT_VALUE_DIGITS else []

    def plausible(a: int, b: int) -> bool:
        start, end = index[a], index[b - 1] + 1
        if end - start > 3 * n + 12:  # digits scattered over the page: not one value
            return False
        if n < GUIDED_SHORT_VALUE_DIGITS:
            return bool(
                _SHORT_VALUE_LABEL.search(loc.text[max(0, start - 25) : start])
            ) or _under_a_header(loc, start, end, headers)
        return True

    def whole_run(a: int, b: int) -> bool:
        # Not a slice of a longer number: `9851234567` inside `009851234567` (an account)
        # is not the phone, and crediting it would leave the real, garbled phone unplaced
        # but counted.
        start, end = index[a], index[b - 1] + 1
        before = loc.text[start - 1 : start].translate(_TO_ASCII_DIGITS)
        after = loc.text[end : end + 1].translate(_TO_ASCII_DIGITS)
        return not before.isdigit() and not after.isdigit()

    def one_value(a: int, b: int) -> bool:
        # 🛑 An EXACT match must be one written value: between its digits only the
        # separators a number is written with, a few at a time. Otherwise two nearby
        # fields (`१२३४५ ... ६७८९०`) concatenate into the needle in the digit stream,
        # their boxes are burned, and the real value elsewhere counts as placed.
        # A bare line wrap (`98000` / `00001`, nothing printed at the break) is weaker
        # evidence than a printed separator: accepted once per match, and only for a
        # long value, whose exact digits two unrelated fields will not spell by chance.
        wraps = 0
        for i in range(a, b - 1):
            gap = loc.text[index[i] + 1 : index[i + 1]]
            if _joins_one_value(gap):
                continue
            if n < GUIDED_SHORT_VALUE_DIGITS or wraps or not _wraps_one_value(gap):
                return False
            wraps += 1
        return not wraps or stands_alone(a, b)

    def stands_alone(a: int, b: int) -> bool:
        # A match through a bare wrap is weak evidence, so it must not be glued to
        # another digit at either end either: not by a line break after a printed
        # separator (`9-` / `98512` / `34567` is a longer number), nor by a single
        # separator mark (`9-98512` / `34567`), nor by another bare wrap (`98512` /
        # `34567` / `7`). A list's `, ` still ends it.
        for gap in (
            loc.text[index[a - 1] + 1 : index[a]] if a > 0 else None,
            loc.text[index[b - 1] + 1 : index[b]] if b < len(index) else None,
        ):
            if gap is None:
                continue
            if _wraps_one_value(gap):  # `98000` / `00001` / `7`: a longer number
                return False
            if _joins_one_value(gap) and ("\n" in gap or len(gap) <= 1):
                return False
        return True

    spans = []
    last_end = -1
    # Non-overlapping, left to right: an exact match counts ONE physical occurrence, and
    # `989898989898` must not satisfy two required copies of `9898989898`.
    for m in re.finditer(f"(?={re.escape(want)})", stream):
        a, b = m.start(), m.start() + n
        if a < last_end or not (
            plausible(a, b) and one_value(a, b) and whole_run(a, b)
        ):
            continue
        spans.append((index[a], index[b - 1] + 1))
        last_end = b
    if spans or not approximate or n < 4:
        return spans
    cap = max(1, n // 4)
    candidates = []
    for length in range(n - cap, n + cap + 1):
        for a in range(0, len(stream) - length + 1):
            distance = _bounded_levenshtein(stream[a : a + length], want, cap)
            if distance <= cap and plausible(a, a + length):
                candidates.append((distance, a, a + length))
    taken: list[tuple[int, int]] = []
    for _distance, a, b in sorted(candidates):
        if any(not (b <= x or a >= y) for x, y in taken):
            continue
        taken.append((a, b))
        spans.append((index[a], index[b - 1] + 1))
    return spans


def _column_headers(loc: Locator) -> list[fitz.Rect]:
    """Boxes of the identifier column headers on the page."""

    boxes = []
    for m in _ID_COLUMN_HEADER.finditer(loc.text):
        rects = [r for r in loc.rects[m.start() : m.end()] if r is not None]
        if rects:
            box = fitz.Rect(rects[0])
            for r in rects[1:]:
                box |= r
            boxes.append(box)
    return boxes


def _under_a_header(
    loc: Locator, start: int, end: int, headers: list[fitz.Rect]
) -> bool:
    """A short value in a TABLE: no label beside it, but an ID column header above it.

    The value must sit below the header and inside its column (its centre within the
    header's span, widened by half the header's width each side: a header's text is
    usually narrower than its column). A 4-5 digit run anywhere else on the page --
    a year, an amount -- is no more placed than before.
    """

    rects = [r for r in loc.rects[start:end] if r is not None]
    if not rects or not headers:
        return False
    box = fitz.Rect(rects[0])
    for r in rects[1:]:
        box |= r
    centre = (box.x0 + box.x1) / 2
    for h in headers:
        slack = h.width / 2
        if box.y0 >= h.y1 - 1 and h.x0 - slack <= centre <= h.x1 + slack:
            return True
    return False


def span_rects(loc: Locator, start: int, end: int) -> list[fitz.Rect]:
    """The boxes to burn for ``loc.text[start:end]``, one per line it spans.

    On an OCR locator the span first grows over the rest of its digit-like token (OCR glues
    and misreads edge characters) and every box is padded; a glyph map is used as is.
    """

    if loc.ocr:
        while start > 0 and _DIGITISH.match(loc.text[start - 1]):
            start -= 1
        while end < len(loc.text) and _DIGITISH.match(loc.text[end]):
            end += 1
    merged: list[fitz.Rect] = []
    for rect in (loc.rects[i] for i in range(start, end)):
        if rect is None:
            continue
        if merged:
            last = merged[-1]
            overlap = min(last.y1, rect.y1) - max(last.y0, rect.y0)
            if overlap > 0.5 * min(
                last.height, rect.height
            ) and rect.x0 - last.x1 < 2 * max(last.height, rect.height):
                merged[-1] = last | rect
                continue
        merged.append(fitz.Rect(rect))
    if not loc.ocr:
        return merged
    out = []
    for rect in merged:
        pad_y = OCR_BOX_PAD * max(rect.height, 1)
        pad_x = pad_y + OCR_BOX_GROW_X * rect.height
        out.append(
            fitz.Rect(
                rect.x0 - pad_x, rect.y0 - pad_y, rect.x1 + pad_x, rect.y1 + pad_y
            )
        )
    return out


@dataclass
class GuidedPlacement:
    """Where the guided values of one page landed."""

    boxes: list
    placed: int
    unplaced: list  # (kind, value-shape) for values with no exact match on any read
    #: Exact occurrences of each WHOLE value credited per :func:`match_key`, for
    #: document-level guidance, which sums them across pages (different pages are
    #: different occurrences).
    placed_by_key: Counter = field(default_factory=Counter)
    #: Exact occurrences of each PART of a composite value (:func:`composite_parts`) the
    #: page could give that value, per part key -- outside the composite's own whole
    #: matches. Summed across pages by document-level guidance like ``placed_by_key``.
    part_supply: Counter = field(default_factory=Counter)
    #: The UNCAPPED exact count behind ``placed_by_key``. Document-level guidance
    #: combines a page's reads on it: a capped count hides a whole match a retry must
    #: debit an overlapping value for (:meth:`GuidedNeeds.combine`).
    whole_by_key: Counter = field(default_factory=Counter)
    #: Where those counts were found (:attr:`GuidedRead.prints`), for the same merge.
    prints: list = field(default_factory=list)


#: Words that join two values in one written list (`९८५१११११११ वा ९८४१११११११`).
_COMPOSITE_CONNECTORS = frozenset({"वा", "र", "तथा", "एवं", "and", "or", "&"})
#: Separators that always end one value of a list: slash, comma, semicolon, danda, a
#: table bar, or a connector word between spaces.
_COMPOSITE_STRONG_SPLIT = re.compile(
    r"\s*[/,;।॥|]+\s*|\s+(?:वा|र|तथा|एवं|and|or|&)\s+", re.I
)
_COMPOSITE_ANY_SPLIT = re.compile(r"[/,;।॥|\s]+")
_COMPOSITE_HYPHEN_SPLIT = re.compile(r"\s*[-–]\s*")
_COMPOSITE_NUMBER = re.compile(r"\+?[0-9०-९][0-9०-९ .\-–()]*")
_COMPOSITE_EMAIL = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")


def _composite_phone_part(part: str) -> bool:
    """A whole Nepali phone number: a mobile (10 digits, `9…`), a landline with its area
    code (`0…`, 8-11 digits) or either with the country code (`977…`, 11-13 digits).
    Never a bare 6-7 digit local number: that is an amount's shape too."""

    if not _COMPOSITE_NUMBER.fullmatch(part):
        return False
    digits = _digits(part)
    return (
        (len(digits) == 10 and digits[0] == "9")
        or (digits[0] == "0" and 8 <= len(digits) <= 11)
        or (digits.startswith("977") and 11 <= len(digits) <= 13)
    )


def _composite_account_part(part: str) -> bool:
    """A whole bank account number: 9-20 digits (a six-digit half of one account is not
    one -- see :func:`composite_parts`)."""

    return bool(_COMPOSITE_NUMBER.fullmatch(part)) and 9 <= len(_digits(part)) <= 20


def _composite_email_part(part: str) -> bool:
    return bool(_COMPOSITE_EMAIL.fullmatch(part))


#: Two bank accounts are told apart only by a list word or a comma (`… र …`, `…, …`):
#: a slash, a space or a hyphen can sit inside ONE account (`१२३४५६७८९ / ९८७६५४३२१` may
#: be one number written in two groups), and no shape check can tell the two apart.
_COMPOSITE_ACCOUNT_SPLIT = re.compile(
    r"\s*[,;]\s*|\s+(?:वा|र|तथा|एवं|and|or|&)\s+", re.I
)

#: Kind -> (shape every part must have, the separators tried in turn). A phone number
#: and an address are whole values by shape (a ten-digit `9…` mobile is never half of
#: one), so any list separator splits them; an account needs a list word or a comma.
_COMPOSITE_KINDS: dict[str, tuple[Callable[[str], bool], tuple[re.Pattern, ...]]] = {
    "PHONE-NO": (
        _composite_phone_part,
        (_COMPOSITE_STRONG_SPLIT, _COMPOSITE_ANY_SPLIT, _COMPOSITE_HYPHEN_SPLIT),
    ),
    "TABLE-PHONE-NO": (
        _composite_phone_part,
        (_COMPOSITE_STRONG_SPLIT, _COMPOSITE_ANY_SPLIT, _COMPOSITE_HYPHEN_SPLIT),
    ),
    "BANK-ACCOUNT-NO": (_composite_account_part, (_COMPOSITE_ACCOUNT_SPLIT,)),
    "EMAIL": (_composite_email_part, (_COMPOSITE_STRONG_SPLIT, _COMPOSITE_ANY_SPLIT)),
}


def composite_parts(kind: str, value: str) -> list[str] | None:
    """The values a guided ``value`` is a list of, or ``None`` if it is one value.

    The transcript diff can hand over two values as one (`९८५१११११११ / ९८४१११११११`,
    `… वा …`, two accounts joined by `र`) -- one placeholder over the list, or a separator
    :func:`transcript_removed_values` does not split on -- while the page prints them
    where the joined string never matches. Each part is then placed on its own.

    🛑 Every part must be a whole value of ``kind`` on its own (a full phone number, an
    account of 9+ digits, an address), or there is no split: a part that is only a
    fragment of one value (`खाता नं. १२३४५६ / ७८९०१२`) would earn exact credit on an
    unrelated amount while the value stays visible. An account is split only at a list
    word or a comma (:data:`_COMPOSITE_ACCOUNT_SPLIT`). Kinds without a part shape (IDs,
    dates) are never split: a slash or space inside them is part of the value.
    """

    spec = _COMPOSITE_KINDS.get(kind)
    if spec is None:
        return None
    is_part, splitters = spec
    for splitter in splitters:
        parts = [
            part.strip(" \t\n.:()")
            for part in splitter.split(value.strip())
            if part.strip(" \t\n.:()").lower() not in _COMPOSITE_CONNECTORS
        ]
        parts = [part for part in parts if part]
        if len(parts) >= 2 and all(is_part(part) for part in parts):
            return parts
    return None


def _print_boxes(loc: Locator, start: int, end: int) -> list[fitz.Rect]:
    """Where ``loc.text[start:end]`` is printed: its character boxes, unpadded,
    merged per line (a wrapped value is two boxes, not one spanning both lines)."""

    return span_rects(Locator(loc.text, loc.rects, ocr=False), start, end)


@dataclass
class GuidedRead:
    """Exact occurrences one read (or a sound combination of reads) shows of a page:
    ``whole`` per value key, ``supply`` per part key (:meth:`GuidedNeeds.read`).

    ``prints`` is where each counted occurrence is on the page -- ``(stage, key,
    boxes)``, stage 0 a composite's whole match, 1 another value's, 2 a part -- so
    reads of one page can be merged print by print (:meth:`GuidedNeeds.combine`).
    """

    whole: Counter
    supply: Counter
    prints: list = field(default_factory=list)


@dataclass
class GuidedNeeds:
    """What a set of guided values needs placed, and how part credit is shared out.

    ``needed`` counts occurrences per :func:`match_key`; ``parts`` maps the key of a
    composite value (:func:`composite_parts`) to its parts' keys, ``part_values`` each
    part key to a written form to locate.
    """

    needed: Counter
    sample: dict
    parts: dict
    part_values: dict

    @classmethod
    def of(cls, values: list[tuple[str, str]]) -> GuidedNeeds:
        needed: Counter[str] = Counter()
        sample: dict[str, tuple[str, str]] = {}
        for kind, value in values:
            key = match_key(value)
            needed[key] += 1
            sample.setdefault(key, (kind, value))
        parts: dict[str, list[str]] = {}
        part_values: dict[str, str] = {}
        for key, (kind, value) in sample.items():
            split = composite_parts(kind, value)
            if split is None:
                continue
            parts[key] = [match_key(part) for part in split]
            for part in split:
                part_values.setdefault(match_key(part), part)
        return cls(needed, sample, parts, part_values)

    def read(self, loc: Locator, keys: list[str], boxes: list[fitz.Rect]) -> GuidedRead:
        """What ONE read shows: exact whole matches of ``keys`` (and of every composite
        value), and the parts of each composite value it does not show whole often
        enough. Every exact match's boxes are appended to ``boxes``.

        A part -- and any other value -- is counted only outside the composites' whole
        matches on this read: those already credit the composite, and the same digits
        must not credit a second required value (a standalone `A` inside the one
        printed `A/B`).
        """

        whole: Counter[str] = Counter()
        prints: list[tuple[int, str, list[fitz.Rect]]] = []
        taken: list[tuple[int, int]] = []
        credited: dict[str, list[tuple[int, int]]] = {}
        # Composites first, so every other value knows the prints they took.
        for key in dict.fromkeys([*self.parts, *keys]):
            spans = locate_value(loc, self.sample[key][1], approximate=False)
            for s, e in spans:
                boxes += span_rects(loc, s, e)
            if key in self.parts:
                # Nor a second composite inside the print an earlier one took: `A/B`
                # and `B/C` both match in `A/B/C`, and its one `B` places one of them.
                spans = [
                    (s, e)
                    for s, e in spans
                    if not any(s < te and ts < e for ts, te in taken)
                ]
                taken += spans
            else:
                if taken:
                    spans = [
                        (s, e)
                        for s, e in spans
                        if not any(s < te and ts < e for ts, te in taken)
                    ]
                credited[key] = spans
            if spans:
                whole[key] = len(spans)
            stage = 0 if key in self.parts else 1
            prints += [(stage, key, _print_boxes(loc, s, e)) for s, e in spans]
        supply: Counter[str] = Counter()
        wanted = {
            part
            for key, part_keys in self.parts.items()
            if whole[key] < self.needed[key]
            for part in part_keys
        }
        claimed: list[tuple[int, int]] = []
        # Longest part first, and one print to one part: `123456789 9800000001` is an
        # account's print, not also one of the list's `9800000001`.
        for part in sorted(wanted, key=lambda p: (-len(p), p)):
            # Nor inside another value's credited print: the phone-shaped group of
            # an account written `123456789 / 9800000001` is that account's.
            own = taken + claimed
            own += [
                span
                for other, spans in credited.items()
                if other != part
                for span in spans
            ]
            spans = [
                (s, e)
                for s, e in locate_value(loc, self.part_values[part], approximate=False)
                if not any(s < te and ts < e for ts, te in own)
            ]
            for s, e in spans:
                boxes += span_rects(loc, s, e)
            claimed += spans
            supply[part] = len(spans)
            prints += [(2, part, _print_boxes(loc, s, e)) for s, e in spans]
        return GuidedRead(whole, +supply, prints)

    def second_read_keys(self, pending: list[str]) -> list[str]:
        """What a second read must count: the pending values -- and, when there are
        composites, every value. :meth:`combine` may debit a part's first-read credit
        for a composite the second read newly sees whole, and a part counts only
        outside other values' credited prints; only a read that counts them all can
        keep a separate print credited and an overlapping one excluded."""

        if not self.parts:
            return pending
        # Every value, not just the parts: a part is counted only outside the prints
        # other values are credited with (:meth:`read`), so the read must see them.
        return list(dict.fromkeys([*pending, *self.needed]))

    def combine(self, reads: list[GuidedRead]) -> GuidedRead:
        """Several reads of the same page as one: never more than the page shows.

        Without composites, per key the best read, as always: different values are
        different prints, and one print found by two reads is still one print.

        With composites, two reads may credit ONE print to two overlapping values: `A/B`
        whole here, `A … B` as parts there; `A/B` here, `B/C` there (one `A/B/C`); an
        account `123456789 9800000001` here, a list's part `9800000001 987654321`
        there. No relation between the values' digits can rule that out, so the reads
        are merged by WHERE their prints are: every read's prints are taken in
        :meth:`read`'s order (composites, other values, parts longest first); a print
        overlapping one already taken for the same value is the same occurrence, and
        one overlapping a print taken for another value is that value's (a part may
        still lie on its own standalone value's print: :meth:`allocate` spends those
        once). One print never places two required occurrences.
        """

        if not self.parts:
            whole: Counter[str] = Counter()
            supply: Counter[str] = Counter()
            for one in reads:
                for key, n in one.whole.items():
                    whole[key] = max(whole[key], n)
                for part, n in one.supply.items():
                    supply[part] = max(supply[part], n)
            return GuidedRead(+whole, +supply)

        def overlaps(mine: list[fitz.Rect], theirs: list[fitz.Rect]) -> bool:
            if not mine or not theirs:  # nowhere known: assume the worst
                return True
            return any(not (r & o).is_empty for r in mine for o in theirs)

        taken: list[tuple[int, str, list[fitz.Rect]]] = []
        for stage in (0, 1, 2):
            found = [p for one in reads for p in one.prints if p[0] == stage]
            if stage == 2:
                found.sort(key=lambda p: -len(p[1]))  # stable: read order within
            for print_ in found:
                _stage, key, rects = print_
                clash = False
                for other_stage, other, other_rects in taken:
                    if not overlaps(rects, other_rects):
                        continue
                    # A part on its own standalone value's print: counted as
                    # supply, and allocate() spends it once.
                    if stage == 2 and other_stage == 1 and other == key:
                        continue
                    # Plain values sharing digits are not reconciled (as before).
                    if stage == 1 and other_stage == 1 and other != key:
                        continue
                    clash = True
                    break
                if not clash:
                    taken.append(print_)
        whole = Counter(key for stage, key, _ in taken if stage < 2)
        supply = Counter(key for stage, key, _ in taken if stage == 2)
        return GuidedRead(whole, supply, taken)

    def allocate(self, whole: Counter, supply: Counter) -> Counter:
        """Occurrences placed per key: whole matches first, then composites from parts.

        🛑 A composite occurrence is placed only when EVERY one of its parts has an
        occurrence of its own, and each part occurrence is spent once: on a value the
        transcript also has standalone first, then on one composite. One printed number
        never places two required occurrences.
        """

        used = Counter(
            {key: min(whole[key], need) for key, need in self.needed.items()}
        )
        pool = Counter(supply)
        for part in pool:
            if part in self.needed and part not in self.parts:
                pool[part] = max(0, pool[part] - used[part])
        for key, part_keys in self.parts.items():
            short = self.needed[key] - used[key]
            if short <= 0:
                continue
            per_occurrence = Counter(part_keys)
            can = min(
                short,
                min(pool[part] // count for part, count in per_occurrence.items()),
            )
            for part, count in per_occurrence.items():
                pool[part] -= can * count
            used[key] += can
        return used

    def pending(self, used: Counter) -> list[str]:
        return [key for key, need in self.needed.items() if used[key] < need]

    def needles(self, key: str) -> list[str]:
        """What to box approximately for an unplaced key: the value, and its parts."""

        return [self.sample[key][1]] + [
            self.part_values[part] for part in dict.fromkeys(self.parts.get(key, ()))
        ]

    def unplaced(self, used: Counter) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for key, need in self.needed.items():
            kind, value = self.sample[key]
            out += [(kind, _shape(value))] * (need - used[key])
        return out


def place_guided_values(
    values: list[tuple[str, str]],
    locators: list[Locator],
    second_read: Callable[[], Locator] | None = None,
    *,
    approximate_boxes: bool = True,
) -> GuidedPlacement:
    """Box every guided value; count a value placed only on exact matches, per occurrence.

    ``second_read`` is a zero-argument callable returning another :class:`Locator`; it is
    only called when some value is not placed exactly by ``locators``.

    ``approximate_boxes=False`` boxes exact matches only. Document-level guidance passes
    it on its first sweep, where a value missing from a page is the normal case (it is on
    another page) and fuzzy boxes on every page would black out look-alike numbers.

    A composite value (:func:`composite_parts`) not found whole is placed part by part:
    every part is boxed where it matches exactly, and the value counts as placed only if
    every part is (:meth:`GuidedNeeds.allocate`).
    """

    boxes: list[fitz.Rect] = []
    # 🛑 Placement CREDIT comes only from what the page shows. On a scan the glyph layer
    # (a stale or junk OCR overlay) may put a value where the picture does not; its
    # matches still get boxes, but only a read of the pixels can prove a value placed.
    credit = [loc for loc in locators if loc.ocr] or locators
    # 🛑 Grouped by what MATCHING sees, not by the raw string: `9851234567` and
    # `९८५-१२३-४५६७` are one needle to locate_value, so as two groups each would credit
    # the same single occurrence on the page and a garbled second copy would pass.
    needs = GuidedNeeds.of(values)
    reads = []
    for loc in locators:
        one = needs.read(loc, list(needs.needed), boxes)
        if loc in credit:
            reads.append(one)
    # 🛑 The best of the reads, never their sum: every read sees the same page, so one
    # physical occurrence found by each is still one occurrence. Summing let a value
    # printed twice pass when both reads found only the same copy and the other was
    # garbled.
    seen = needs.combine(reads)
    used = needs.allocate(seen.whole, seen.supply)
    pending = needs.pending(used)
    if approximate_boxes:
        for key in pending:
            for loc in locators:
                if loc.ocr:
                    for needle in needs.needles(key):
                        for s, e in locate_value(loc, needle, approximate=True):
                            boxes += span_rects(loc, s, e)

    loc2 = second_read() if pending and second_read is not None else None
    if loc2 is not None:
        reads.append(needs.read(loc2, needs.second_read_keys(pending), boxes))
        seen = needs.combine(reads)
        used = needs.allocate(seen.whole, seen.supply)
        # The same contract as the first read: approximate boxes only when asked.
        if approximate_boxes:
            for key in needs.pending(used):
                for needle in needs.needles(key):
                    for s, e in locate_value(loc2, needle, approximate=True):
                        boxes += span_rects(loc2, s, e)
    placed_by_key = Counter(
        {key: min(seen.whole[key], need) for key, need in needs.needed.items()}
    )
    return GuidedPlacement(
        boxes,
        sum(used.values()),
        needs.unplaced(used),
        +placed_by_key,
        Counter(seen.supply),
        +Counter(seen.whole),
        list(seen.prints),
    )


def match_key(value: str) -> str:
    """The identity locate_value matches on: an address case-folded, else its digits."""

    return value.strip().lower() if "@" in value else _digits(value)


def unplaced_all(guided: list[tuple[str, str]]) -> GuidedPlacement:
    """A placement in which nothing was found: for a page that has nothing to read."""

    return GuidedPlacement(
        boxes=[], placed=0, unplaced=[(kind, _shape(value)) for kind, value in guided]
    )


def _shape(value: str) -> str:
    """A value with every letter and digit masked: what a journal may record about it.

    Letters too, not just digits: guided values include e-mail addresses, whose
    identifying part is letters.
    """

    return "".join("#" if ch.isalnum() else ch for ch in value)
