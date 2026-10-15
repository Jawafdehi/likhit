"""Redact a PDF into a redacted PDF, under the same discipline :mod:`likhit.privacy.tree`
applies to a directory of transcripts -- plus three rules a PDF needs and text does not.

🛑 **1. Never in place, and never an incremental save.** The first half is
:mod:`~likhit.privacy.tree`'s rule 1. The second is specific to this format and is the easiest
way to ship a file that looks redacted and is not: ``Document.save(incremental=True)`` appends
a revision and **leaves the original content recoverable inside the same file**. Every save
here is a full save with ``garbage=4``.

🛑 **2. A refusal is a result.** Only a page whose text layer is Unicode Devanagari can be
redacted by this module. A legacy-encoded page decodes to Latin-looking bytes, so the
detectors find almost nothing in it -- measured on two CIAA reports, the text layer yields
**1 and 4** high-precision hits where likhit's decoded output yields **16 and 62**, and the
survivors are the ASCII email addresses in both cases. So on those pages "no targets found" is
not evidence of no personal data, and emitting the page unchanged would be the failure this
whole module exists to prevent. Those pages refuse the document. This is the same argument
``likhit-redact scan`` already makes about an empty tree.

🛑 **3. PII lives outside the content stream.** Metadata, XMP and embedded attachments survive
every redaction annotation ever applied. They are scrubbed here, because a document redacted
in the only place anyone looked is worse than one nobody redacted.

What this module does **not** do yet is the other two page classes: legacy-encoded text needs
likhit's own font decode plumbed through to glyph rectangles, and a pure scan needs
bounding-box OCR, which likhit's LLM-vision OCR cannot provide because it returns text without
coordinates. Both refuse loudly rather than silently passing a page through.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import fitz

from .contact import iter_contact_targets
from .modes import DEFAULT_MODE, RedactionMode
from .redact import iter_inline_targets

#: Devanagari, as a character class. A page's ratio of these to its total characters is what
#: separates a page likhit never needed to touch from one whose bytes are a legacy font.
_TEXT_LAYER_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

#: A page at or above this ratio is treated as Unicode text.
#:
#: 🛑 Measured, not chosen: across 1,283 pages of five documents the distribution is sharply
#: bimodal. Legacy pages sit at **exactly 0%** (nirnaya 2/2, kanunpatrika 128/128, the 23rd
#: CIAA report 534/542) and Unicode pages above 20% (aarop-patra 67/67, the 34th report
#: 527/544). Only **10 pages of 1,283** land in between, and every one of them refuses rather
#: than being guessed at -- a mixed page may be part legacy, and the cost of being wrong in
#: that direction is publishing an identifier.
UNICODE_DEVANAGARI_RATIO = 0.20


class PageClass(str, Enum):
    """What can be done with one page, which is a question about its text layer."""

    #: Unicode Devanagari text. Redactable by this module today.
    UNICODE = "unicode"
    #: A text layer that is not Unicode Devanagari -- a legacy pre-Unicode font.
    #:
    #: 🛑 **likhit already decodes these; what is missing is provenance, not decoding.**
    #: ``decode_with_legacy_map`` is string-to-string, so which glyph produced which output
    #: character is discarded -- and that mapping is exactly what redaction needs.
    #:
    #: The good news, measured against ``npttf2utf``'s Preeti table: **every value this
    #: package removes is a digit run, and digits decode 1:1 and are never reordered.** All
    #: ten Devanagari digits come from the shifted number row ``!@#$%^&*()``, each to a
    #: single distinct codepoint, and neither the ``pre-rules`` nor the ``post-rules``
    #: reordering groups mentions a digit at all. The one-to-many complexity -- 56 of 136
    #: entries -- is consonant ligatures, which only ever occur in the *labels*. So the
    #: k-th decoded digit corresponds to the k-th digit-producing glyph, and the glyph
    #: rectangles are already correct on these pages (1,245 of them on ``samples/nirnaya.pdf``
    #: page 1, every one with a bbox).
    #:
    #: This class therefore refuses because the work is **unbuilt, not impossible**, and
    #: refusing is the only safe state until it is built.
    LEGACY = "legacy"
    #: No text layer, but ink on the page. Needs bounding-box OCR.
    SCANNED = "scanned"
    #: No text and no images. Nothing to redact, and that is a measurement rather than an
    #: assumption -- a cover sheet with a scanned stamp is :data:`SCANNED`, not this.
    BLANK = "blank"

    @property
    def is_redactable(self) -> bool:
        return self is PageClass.UNICODE

    @property
    def needs_work(self) -> bool:
        """Does this page block the document? ``BLANK`` does not; the other two do."""
        return self in (PageClass.LEGACY, PageClass.SCANNED)


def classify_text_layer(page: fitz.Page, text: str | None = None) -> PageClass:
    """Which of the four classes this page is.

    🛑 **Classified from :func:`page_glyph_map`'s text, not from ``page.get_text()``.** The
    two do not see the same characters: a bare ``get_text()`` takes PyMuPDF's default flag
    word, which sets ``TEXT_MEDIABOX_CLIP``, while this module's extraction passes
    ``flags=fitz.TEXT_PRESERVE_WHITESPACE`` and therefore does not clip. Classifying on one
    and redacting on the other would leave a gap exactly the width of the difference --
    a page could be called ``BLANK`` on its clipped text while the glyph map carries
    an identifier outside the mediabox.

    ``text`` is accepted so a caller that already built the map does not build it twice; it
    must be that same text, which is why this is not a general "classify this string".
    """

    if text is None:
        text = page_glyph_map(page)[0]
    text = text.strip()
    if not text:
        # Images OR vector drawings: a page can carry a scanned stamp as either, and both
        # are ink that may be a signature, a seal or a handwritten number.
        has_ink = bool(page.get_images(full=True)) or bool(page.get_drawings())
        return PageClass.SCANNED if has_ink else PageClass.BLANK
    if (
        len(_TEXT_LAYER_DEVANAGARI.findall(text)) / len(text)
        >= UNICODE_DEVANAGARI_RATIO
    ):
        return PageClass.UNICODE
    return PageClass.LEGACY


@dataclass
class PdfRedactionReport:
    """What a run did, in the shape a journal is written from."""

    pages: int = 0
    pages_by_class: Counter = field(default_factory=Counter)
    pages_changed: int = 0
    values_removed: int = 0
    rects_applied: int = 0
    counters: Counter = field(default_factory=Counter)
    #: One row per removed value: kind and shape, never the value. Same rule as
    #: :mod:`likhit.privacy.tree` -- a record of what was removed has to be publishable
    #: beside the thing it was removed from.
    span_shapes: list[dict] = field(default_factory=list)
    #: Pages this module cannot redact, with the class that blocked them.
    blocked_pages: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "pages": self.pages,
            "pages_by_class": dict(sorted(self.pages_by_class.items())),
            "pages_changed": self.pages_changed,
            "values_removed": self.values_removed,
            "rects_applied": self.rects_applied,
            "counters": dict(sorted(self.counters.items())),
            "blocked_pages": self.blocked_pages,
            "span_shapes": self.span_shapes,
            "no_matched_digits_recorded": True,
        }


def _targets(text: str, mode: RedactionMode, stats: Counter) -> list:
    """Every target in ``text`` for ``mode``, sorted by position."""

    found: list = []
    if mode.removes_identifiers:
        found.extend(iter_inline_targets(text, stats, mode))
    if mode.removes_contact:
        found.extend(iter_contact_targets(text, stats))
    return sorted(found, key=lambda target: target.start)


def page_glyph_map(page: fitz.Page) -> tuple[str, list[fitz.Rect | None]]:
    """``(text, rects)`` where ``rects[i]`` is the rectangle of ``text[i]``, or ``None``.

    🛑 **This is the mapping the whole module turns on, and it is not ``search_for``.** An
    earlier version located values with ``page.search_for(value)``, which does plain substring
    matching: on page 66 of ``samples/aarop-patra.pdf`` it returned **3 rectangles for a
    citizenship value the page carries once**, the other two being the same digits sitting
    inside longer figures. Redacting those would have destroyed audit data -- this project's
    redaction history is 572 real hits against 11,215 non-secret values clobbered -- and
    `search_for` offers no way to ask which hit was the one the detector meant.

    Working from the detector's own character offsets removes the question. It also buys two
    things for free: a value broken across a line is still located, and the **label is kept**,
    because the span being removed is the value's own character range rather than whatever
    word happens to contain it.

    ``None`` marks the synthetic newline inserted between lines -- it is in the text so the
    patterns see line structure, but it has no glyph and therefore no rectangle.

    ⚠️ This is the same ``rawdict`` per-glyph geometry that
    :func:`likhit.extractors.font_based._char_position` already pairs characters on. On a
    legacy-encoded page the rectangles here are equally correct and only ``ch["c"]`` is wrong,
    which is why mapping likhit's decode onto this is what unblocks :data:`PageClass.LEGACY`.
    """

    raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    chars: list[str] = []
    rects: list[fitz.Rect | None] = []
    for block in raw.get("blocks", ()):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span.get("chars", ()):
                    chars.append(char["c"])
                    rects.append(fitz.Rect(char["bbox"]))
            chars.append("\n")
            rects.append(None)
    return "".join(chars), rects


def _merge_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """Union runs of rectangles that share a line, so one value is one annotation.

    Purely a reduction in annotation count -- the union of a contiguous run of glyph boxes on
    one line is the run's own bounding box, so this covers exactly what the individual boxes
    did. Values that wrap keep one rectangle per line.
    """

    merged: list[fitz.Rect] = []
    for rect in rects:
        if merged and abs(merged[-1].y0 - rect.y0) < 1.0 and rect.x0 >= merged[-1].x0:
            merged[-1] |= rect
        else:
            merged.append(fitz.Rect(rect))
    return merged


def _redact_page(
    page: fitz.Page, mode: RedactionMode, report: PdfRedactionReport
) -> int:
    """Redact one Unicode page. Returns the number of values removed.

    🛑 **Every character of every value must carry a rectangle, or the page refuses.** A span
    the detector found but the geometry cannot place is a value this module would leave in the
    file while reporting it as removed, which is the one outcome worse than refusing.
    """

    text, rects = page_glyph_map(page)
    targets = _targets(text, mode, report.counters)
    if not targets:
        return 0

    to_redact: list[fitz.Rect] = []
    for target in targets:
        span = [rect for rect in rects[target.start : target.end] if rect is not None]
        if len(span) != target.end - target.start:
            raise ValueError(
                f"page {page.number + 1}: {target.end - target.start - len(span)} of "
                f"{target.end - target.start} characters of a {target.kind} value have no "
                "glyph rectangle; refusing rather than reporting a value as removed that "
                "this pass could not place"
            )
        to_redact.extend(_merge_rects(span))
        report.span_shapes.append(
            {
                "page": page.number + 1,
                "kind": target.kind,
                "value_chars_removed": target.end - target.start,
                "value_digit_count": target.digit_count,
                "value_had_separators": target.had_separators,
            }
        )

    # 🛑 Every annotation first, then ONE apply_redactions for the page. Applying per
    # annotation is the documented performance trap and the usual cause of a loop that
    # redacts only its last iteration.
    for rect in to_redact:
        page.add_redact_annot(rect)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)

    report.rects_applied += len(to_redact)
    return len(targets)


def _scrub_document_metadata(doc: fitz.Document) -> None:
    """Remove the PII that never appears on a page: metadata, XMP, embedded files."""

    doc.set_metadata({})
    doc.del_xml_metadata()
    for name in reversed(doc.embfile_names()):
        doc.embfile_del(name)


def _verify(data: bytes, mode: RedactionMode, checked_pages: frozenset[int]) -> None:
    """Re-read the written bytes and refuse if anything in the removal set survived.

    :mod:`likhit.privacy.tree`'s rule 4, at document grain: a pass that disagrees with itself
    about what it just wrote has not redacted the document, and the only honest place to find
    that out is the output.

    🛑 **``checked_pages`` is passed in rather than recomputed, and that is the whole point of
    this signature.** An earlier version re-classified each page of the output and skipped any
    that was no longer :data:`PageClass.UNICODE`. That is unsound, because **redaction itself
    can change a page's class**: Devanagari digits ``०-९`` are U+0966--U+096F, inside the
    ``[ऀ-ॿ]`` class, so removing them lowers the Devanagari count *and* the total, and a page
    just above :data:`UNICODE_DEVANAGARI_RATIO` can fall below it purely by being redacted.
    Measured on a constructed page: ratio 0.257 before, **0.103 after**. The page would then
    be skipped by the one check whose job is to catch a partial redaction -- and a partial
    redaction is exactly when its class is most likely to have shifted.

    So the set of pages to verify is decided on the **input**, where the class means what it
    says, and carried here.
    """

    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            if page.number not in checked_pages:
                continue
            # The same extraction the redaction ran against, not `get_text()`: verifying
            # against a different extraction than the one that located the values would
            # leave a gap exactly the size of the difference between them.
            residue = _targets(page_glyph_map(page)[0], mode, Counter())
            if residue:
                kinds = sorted({target.kind for target in residue})
                raise ValueError(
                    f"verification failed: {len(residue)} value(s) of kind {kinds} remain on "
                    f"page {page.number + 1} after redaction"
                )


def redact_pdf(
    source: Path,
    destination: Path | None,
    *,
    mode: RedactionMode = DEFAULT_MODE,
    dry_run: bool = False,
) -> PdfRedactionReport:
    """Redact ``source`` into ``destination``, or measure it with ``dry_run``.

    ``destination`` must not exist and must not be ``source``. Raises :class:`ValueError` for
    anything that makes the result untrustworthy -- a page class this module cannot handle, a
    value it cannot locate, or residue in its own output -- and writes nothing when it does.
    """

    if not source.is_file():
        raise ValueError(f"no such PDF: {source}")
    if mode is RedactionMode.NONE:
        raise ValueError(
            "mode=none removes nothing; copy the file instead of asking this module to "
            "produce a 'redacted' artifact that is byte-identical to its input"
        )
    if not dry_run:
        if destination is None:
            raise ValueError("a destination is required unless dry_run is set")
        if destination.resolve() == source.resolve():
            raise ValueError(
                "refusing to redact in place: the unredacted original is the retained "
                "record, and overwriting it destroys the only copy of what was removed"
            )
        if destination.exists():
            raise ValueError(f"refusing to overwrite {destination}")

    report = PdfRedactionReport()
    with fitz.open(source) as doc:
        # 🛑 An encrypted document reaches here happily and only fails on the first page
        # access, as a bare `ValueError: document closed or encrypted` from deep inside
        # PyMuPDF. Named here instead, because the caller's next question is "so what do I
        # do", and because the failure must be unambiguously a refusal rather than something
        # that looks like a corrupt file.
        if doc.needs_pass:
            raise ValueError(
                f"{source} is password-protected; its text cannot be read, so it cannot be "
                "redacted. Decrypt it first -- an unreadable document is not an unredacted "
                "one, and must not be emitted as though it had been processed"
            )
        # 🛑 Zero pages is not a clean document. The same argument `likhit-redact scan` makes
        # about an empty tree: a redaction of nothing cannot be distinguished, downstream,
        # from a redaction that found nothing, and this one would write a "redacted"
        # derivative that had never been examined.
        if doc.page_count == 0:
            raise ValueError(
                f"{source} has no pages; refusing to emit a redacted derivative of a "
                "document that was never examined"
            )
        report.pages = doc.page_count
        # Decided on the INPUT and carried to `_verify`, because redaction can change a
        # page's class -- see that function.
        redactable: set[int] = set()
        for page in doc:
            page_class = classify_text_layer(page)
            report.pages_by_class[page_class.value] += 1
            if page_class.is_redactable:
                redactable.add(page.number)
            if page_class.needs_work:
                report.blocked_pages.append(
                    {"page": page.number + 1, "page_class": page_class.value}
                )

        # 🛑 Rule 2, and before a single annotation is added: a partial redaction of a
        # document whose other pages cannot be read is not a redacted document.
        if report.blocked_pages:
            classes = sorted({row["page_class"] for row in report.blocked_pages})
            raise ValueError(
                f"{len(report.blocked_pages)} of {report.pages} pages are {', '.join(classes)}"
                " and cannot be redacted by this module; refusing the document rather than "
                "emitting one whose unreadable pages were passed through unchanged"
            )

        for page in doc:
            removed = _redact_page(page, mode, report)
            if removed:
                report.pages_changed += 1
                report.values_removed += removed

        if dry_run:
            return report

        _scrub_document_metadata(doc)
        # Full save, never incremental -- see this module's rule 1.
        data = doc.tobytes(garbage=4, deflate=True)

    _verify(data, mode, frozenset(redactable))

    assert destination is not None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return report
