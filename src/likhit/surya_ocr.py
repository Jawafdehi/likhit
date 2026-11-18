"""Surya 2.5 as a page-OCR engine, served from an OpenAI-compatible endpoint (llama.cpp).

WHY THIS EXISTS. On 9 scanned Attorney General charge-sheet pages from the JAW-51
gold set, Surya 2.5 (open weights, Datalab) read 97.7% of digit fields and 96.9% of
person names, close to the best hosted vision models and far ahead of Tesseract
(about 82% / 84% on the full set). It is also the only engine in that benchmark that can OCR
a large archive for a few hundred dollars of rented GPU time, or for free on a
machine you already have.

WHY IT IS NOT JUST ``MARKITDOWN_OCR_MODEL``. A generic vision client gets Surya wrong
in three ways, each of which costs accuracy or text:

* Surya is trained on one fixed prompt, with the image BEFORE the text. Its own
  package says "the exact wording is the model's training-time contract".
  `LLMVisionOCRService` sends a different prompt, text first.
* It answers in HTML, and Surya 2.5 heads each block with compact tokens
  (``<|Text|><|71|><|33|><|143|><|45|>``) instead of a ``<div data-bbox>``. Taken
  as page text, that is markup soup.
* It wants an image whose sides are multiples of 28 px and whose area is at most
  3072 x 2048. likhit renders at 300 DPI, which is larger.

So `SuryaOCRService` sends Surya's own request, resizes the page the way Surya's
client does, and turns the answer into plain Markdown-ish text: headings, paragraphs,
list items and Markdown tables. Everything after that -- the refusal gate, the rules,
the Tesseract cross-check -- is the ordinary `_run_page_ocr` path.

A free-form prompt is refused rather than sent: Surya cannot answer one, so the
optional re-read of disputed spans (`LIKHIT_OCR_REREAD`) is skipped for this engine
and those spans stay flagged for a human.

Stdlib + PyMuPDF only; no `surya` package, no PyTorch.
"""

from __future__ import annotations

import base64
from html.parser import HTMLParser
import logging
import os
import re
from typing import Any, BinaryIO

import fitz
from markitdown_ocr import LLMVisionOCRService, OCRResult

logger = logging.getLogger(__name__)

#: Surya's full-page prompt, verbatim from ``surya.inference.prompts`` (surya-ocr
#: 0.24, Surya 2.5). Do not paraphrase: the model was trained on these exact words.
SURYA_PROMPT = (
    "OCR this image to HTML. Each block is a div with data-label and data-bbox "
    "(x0 y0 x1 y1, normalized 0-1000)."
)
SURYA_DEFAULT_MODEL = "datalab-to/surya-ocr-2.5"
#: ``SURYA_MAX_TOKENS_FULL_PAGE`` in Surya's settings. A dense charge-sheet page is
#: 3-5k tokens; the cap only stops a runaway loop.
SURYA_DEFAULT_MAX_TOKENS = 12288
#: Surya's CPU speed on a shared 8-core host was 1-12 minutes a page (JAW-51).
SURYA_DEFAULT_TIMEOUT = 1800.0

# Surya's `scale_to_fit` defaults: area bounds and the vision encoder's patch grid.
_MAX_SIZE = (3072, 2048)
_MIN_SIZE = (1792, 28)
_GRID = 28

#: Block labels whose content is a picture, not text (Surya's own SKIP_OCR_LABELS
#: plus ``Picture``). Their inner HTML is at most an ``<img alt>``.
_SKIP_LABELS = frozenset({"Figure", "Image", "Picture", "Diagram", "Blank-Page"})
#: With llama-server ``--special`` the end-of-turn token is part of the text.
_END_MARKERS = ("<|im_end|>", "<|im_end!>", "<|endoftext|>")
_LABEL = r"[A-Z][A-Za-z]*(?:-[A-Z][A-Za-z]*)*"
_COMPACT_HEADER = re.compile(rf"<\|({_LABEL})\|>(?:<\|\d{{1,4}}\|>){{4}}")
_COMPACT_BOX = re.compile(r"<\|box\|>(?:<\|\d{1,4}\|>){4}")
_DIV_HEADER = re.compile(
    r"<div\b(?=[^>]*\bdata-bbox=)[^>]*\bdata-label=\"([^\"]*)\"[^>]*>", re.IGNORECASE
)
_FENCE = re.compile(r"^\s*```(?:html)?\s*\n?|\n?```\s*$")


def surya_image_size(width: int, height: int) -> tuple[int, int]:
    """The (width, height) Surya's client would resize a page image to.

    A port of ``surya.inference.util.scale_to_fit``: scale the area into
    [1792*28, 3072*2048] pixels, then snap each side to the 28-px patch grid,
    trimming whichever side keeps the aspect ratio closest until the area fits.
    """
    if width <= 0 or height <= 0:
        return width, height
    aspect = width / height
    pixels = width * height
    max_pixels = _MAX_SIZE[0] * _MAX_SIZE[1]
    min_pixels = _MIN_SIZE[0] * _MIN_SIZE[1]
    scale = 1.0
    if pixels > max_pixels:
        scale = (max_pixels / pixels) ** 0.5
    elif pixels < min_pixels:
        scale = (min_pixels / pixels) ** 0.5
    w_blocks = max(1, round(width * scale / _GRID))
    h_blocks = max(1, round(height * scale / _GRID))
    while w_blocks * h_blocks * _GRID * _GRID > max_pixels:
        if w_blocks == 1 and h_blocks == 1:
            break
        if w_blocks == 1:
            h_blocks -= 1
        elif h_blocks == 1:
            w_blocks -= 1
        elif abs((w_blocks - 1) / h_blocks - aspect) < abs(
            w_blocks / (h_blocks - 1) - aspect
        ):
            w_blocks -= 1
        else:
            h_blocks -= 1
    return w_blocks * _GRID, h_blocks * _GRID


def _resize_png(png: bytes) -> bytes:
    pixmap = fitz.Pixmap(png)
    if pixmap.alpha:
        pixmap = fitz.Pixmap(pixmap, 0)
    size = surya_image_size(pixmap.width, pixmap.height)
    if size == (pixmap.width, pixmap.height):
        return png
    return fitz.Pixmap(pixmap, size[0], size[1], None).tobytes("png")


class _HtmlToText(HTMLParser):
    """Surya's block HTML -> text. Headings become ``#`` lines, tables Markdown rows.

    List items become plain lines, not ``- `` bullets: Surya keeps the printed number
    (``३)``) inside the ``<li>``, so a bullet would be text the page does not have, and
    it showed up as a false digit dispute in the Tesseract cross-check.
    """

    _BREAK = frozenset(
        {"p", "div", "br", "li", "tr", "table", "ul", "ol", "caption", "pre", "hr"}
        | {f"h{n}" for n in range(1, 7)}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.rows_in_table = 0
        self.ignore = 0  # inside <math> annotations, <style>, <script>

    def _flush(self) -> None:
        text = re.sub(r"[ \t]+", " ", "".join(self.buf)).strip()
        if text:
            self.lines.append(text)
        self.buf = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script"):
            self.ignore += 1
        elif tag == "table":
            self._flush()
            self.rows_in_table = 0
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")
        elif tag in self._BREAK and self.cell is not None:
            # a block inside a cell (<td><p>१२</p><p>३४</p></td>) is a word break,
            # or two numbers would merge into a third, wrong one
            self.cell.append(" ")
        elif tag in self._BREAK:
            self._flush()
            if tag[0] == "h" and tag[1:].isdigit():
                self.buf.append("#" * int(tag[1:]) + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script"):
            self.ignore = max(0, self.ignore - 1)
        elif tag in ("td", "th") and self.cell is not None:
            cell = re.sub(r"\s+", " ", "".join(self.cell)).strip()
            if self.row is not None:
                self.row.append(cell.replace("|", "\\|"))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.lines.append("| " + " | ".join(self.row) + " |")
                if self.rows_in_table == 0:
                    self.lines.append("|" + " --- |" * len(self.row))
                self.rows_in_table += 1
            self.row = None
        elif tag in self._BREAK and self.cell is not None:
            self.cell.append(" ")
        elif tag in self._BREAK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.ignore:
            return
        if self.cell is not None:
            self.cell.append(data)
        else:
            self.buf.append(data)

    def text(self) -> str:
        self._flush()
        return "\n".join(self.lines)


def _html_to_text(html: str) -> str:
    parser = _HtmlToText()
    parser.feed(html)
    parser.close()
    return parser.text()


def _surya_blocks(raw: str) -> list[tuple[str | None, str]]:
    """Split an answer into (label, inner html) blocks, for either header style."""
    for header in (_COMPACT_HEADER, _DIV_HEADER):
        found = list(header.finditer(raw))
        if found:
            out: list[tuple[str | None, str]] = []
            if raw[: found[0].start()].strip():
                out.append((None, raw[: found[0].start()]))
            for i, m in enumerate(found):
                end = found[i + 1].start() if i + 1 < len(found) else len(raw)
                out.append((m.group(1), raw[m.end() : end]))
            return out
    return [(None, raw)]


def surya_answer_to_text(raw: str) -> str:
    """Turn a Surya answer (compact block headers, ``<div>`` blocks, or bare block
    HTML) into page text, in reading order. Picture blocks are dropped."""
    text = raw.strip()
    for marker in _END_MARKERS:
        if text.endswith(marker):
            text = text[: -len(marker)].rstrip()
    text = _FENCE.sub("", text)
    text = _COMPACT_BOX.sub("", text)
    parts = [
        _html_to_text(inner)
        for label, inner in _surya_blocks(text)
        if label not in _SKIP_LABELS
    ]
    return "\n\n".join(p for p in parts if p.strip()).strip()


class SuryaOCRService(LLMVisionOCRService):
    """Page OCR through a Surya 2.5 server; same interface as `LLMVisionOCRService`."""

    def __init__(
        self,
        client: Any,
        model: str = SURYA_DEFAULT_MODEL,
        *,
        max_tokens: int = SURYA_DEFAULT_MAX_TOKENS,
        timeout: float = SURYA_DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(client=client, model=model, default_prompt=SURYA_PROMPT)
        self.max_tokens = max_tokens
        self.timeout = timeout

    def extract_text(
        self,
        image_stream: BinaryIO,
        prompt: str | None = None,
        stream_info: Any = None,
        **kwargs: Any,
    ) -> OCRResult:
        if prompt is not None and prompt != SURYA_PROMPT:
            # The re-read hook asks a question; Surya can only transcribe.
            return OCRResult(
                text="",
                backend_used="surya",
                error="Surya answers only its own OCR prompt; free-form prompts are not sent",
            )
        try:
            image_stream.seek(0)
            png = _resize_png(image_stream.read())
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,"
                                    + base64.b64encode(png).decode("ascii")
                                },
                            },
                            {"type": "text", "text": SURYA_PROMPT},
                        ],
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=0.0,
                top_p=0.1,
                timeout=self.timeout,
            )
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                # A page cut off by the token cap is a partial transcription; Surya's
                # own client never accepts one either.
                return OCRResult(
                    text="",
                    backend_used="surya",
                    error=f"Surya answer hit max_tokens={self.max_tokens}",
                )
            return OCRResult(
                text=surya_answer_to_text(choice.message.content or ""),
                backend_used="surya",
            )
        except Exception as exc:  # noqa: BLE001 - reported per page, like the base class
            return OCRResult(text="", backend_used="surya", error=str(exc))
        finally:
            image_stream.seek(0)


def _env_number(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0
    if value <= 0:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    return value


def surya_service_from_env() -> SuryaOCRService | None:
    """Build the Surya service from ``LIKHIT_SURYA_*``; None if no endpoint is set."""
    base_url = os.getenv("LIKHIT_SURYA_BASE_URL", "").strip()
    if not base_url:
        logger.warning(
            "LIKHIT_OCR_ENGINE=surya needs LIKHIT_SURYA_BASE_URL "
            "(e.g. http://127.0.0.1:8080/v1); OCR stays off."
        )
        return None
    from openai import OpenAI

    # llama-server ignores the key, but the client refuses to start without one.
    client = OpenAI(
        api_key=os.getenv("LIKHIT_SURYA_API_KEY") or "surya", base_url=base_url
    )
    return SuryaOCRService(
        client,
        model=os.getenv("LIKHIT_SURYA_MODEL") or SURYA_DEFAULT_MODEL,
        max_tokens=int(
            _env_number("LIKHIT_SURYA_MAX_TOKENS", SURYA_DEFAULT_MAX_TOKENS)
        ),
        timeout=_env_number("LIKHIT_SURYA_TIMEOUT", SURYA_DEFAULT_TIMEOUT),
    )
