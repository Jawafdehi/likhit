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

import difflib
import html
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

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

_TO_ASCII_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_PLACEHOLDER = re.compile(r"\[REDACTED:([A-Z-]+)\]")

#: What may precede a SHORT guided value. Tesseract ``nep+eng`` reads the small
#: ``ना.प्र.नं.`` label as Latin capitals (``ALIA,``, ``AMA.``, ``ALTA.``) often enough
#: that a Devanagari-only list misses real values; the value is already known to be
#: labelled in the transcript, so a label-SHAPED token is enough here.
_SHORT_VALUE_LABEL = re.compile(
    r"(प्र|प\.|नं|न\.|मो|फोन|खाता|ब्या|no|No|NO|#|:|ि|[A-Z][A-Za-z]{1,4}[.,])"
)
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
    png = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY).tobytes("png")
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
def split_transcript_pages(markdown: str) -> dict[int, str]:
    """Split a likhit transcript into ``{page_number: text}`` on its page anchors.

    Text before the first anchor is dropped; a transcript with no anchors maps to page 1.
    """

    anchors = list(PAGE_ANCHOR_PATTERN.finditer(markdown))
    if not anchors:
        return {1: markdown}
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
    equivalent of -- without restating any rule. A list under one label (``98…, 98…``) is
    one placeholder in the text and is split here so each item is located on its own.
    """

    redacted = redact_text(text, mode=mode).text
    if redacted == text:
        return []
    matcher = difflib.SequenceMatcher(None, text, redacted, autojunk=False)
    # difflib splits a value wherever its characters happen to recur in the placeholder;
    # merge non-equal opcodes separated by an equal run of 3 characters or fewer.
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
        parts = [p for p in re.split(r"[,;]\s*|\s+", value) if p]
        if len(parts) > 1 and all(
            "@" in p or len(_digits(p)) >= GUIDED_SHORT_VALUE_DIGITS for p in parts
        ):
            values.extend((kinds[0], p) for p in parts)
        else:
            values.append((kinds[0], value))
    return values


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


def locate_value(
    loc: Locator, value: str, *, approximate: bool
) -> list[tuple[int, int]]:
    """Character spans of ``loc.text`` holding ``value``; separators are ignored.

    Exact digit-stream matches always; with ``approximate`` (OCR locators only) also
    windows within ``len // 4`` edits. Short values need a label-shaped token just before.
    """

    if "@" in value:
        needle, hay = value.strip().lower(), loc.text.lower()
        spans = [
            (m.start(), m.start() + len(needle))
            for m in re.finditer(re.escape(needle), hay)
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

    def plausible(a: int, b: int) -> bool:
        start, end = index[a], index[b - 1] + 1
        if end - start > 3 * n + 12:  # digits scattered over the page: not one value
            return False
        if n < GUIDED_SHORT_VALUE_DIGITS:
            return bool(_SHORT_VALUE_LABEL.search(loc.text[max(0, start - 25) : start]))
        return True

    spans = [
        (index[m.start()], index[m.start() + n - 1] + 1)
        for m in re.finditer(f"(?={re.escape(want)})", stream)
        if plausible(m.start(), m.start() + n)
    ]
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


def place_guided_values(
    values: list[tuple[str, str]],
    locators: list[Locator],
    second_read: Callable[[], Locator] | None = None,
) -> GuidedPlacement:
    """Box every guided value; count a value placed only on exact matches, per occurrence.

    ``second_read`` is a zero-argument callable returning another :class:`Locator`; it is
    only called when some value is not placed exactly by ``locators``.
    """

    boxes: list[fitz.Rect] = []
    placed = 0
    pending: list[tuple[str, str, int, int]] = []  # kind, value, needed, placed so far
    for (kind, value), needed in Counter(values).items():
        exact_counts = []
        for loc in locators:
            spans = locate_value(loc, value, approximate=False)
            for s, e in spans:
                boxes += span_rects(loc, s, e)
            exact_counts.append(len(spans))
        got = max(exact_counts, default=0)
        placed += min(got, needed)
        if got >= needed:
            continue
        for loc in locators:
            if loc.ocr:
                for s, e in locate_value(loc, value, approximate=True):
                    boxes += span_rects(loc, s, e)
        pending.append((kind, value, needed, got))

    unplaced = []
    loc2 = second_read() if pending and second_read is not None else None
    for kind, value, needed, got in pending:
        if loc2 is not None:
            spans = locate_value(loc2, value, approximate=False)
            for s, e in spans:
                boxes += span_rects(loc2, s, e)
            # 🛑 The best of the two reads, never their sum: both reads see the same
            # page, so one physical occurrence found by each is still one occurrence.
            # Summing let a value printed twice pass when both reads found only the
            # same copy and the other was garbled.
            best = max(got, len(spans))
            if best < needed:
                for s, e in locate_value(loc2, value, approximate=True):
                    boxes += span_rects(loc2, s, e)
            placed += min(best, needed) - got
            got = min(best, needed)
        unplaced += [(kind, _shape(value))] * (needed - got)
    return GuidedPlacement(boxes, placed, unplaced)


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
