"""Redact a PDF into a redacted PDF, under the same discipline :mod:`likhit.privacy.tree`
applies to a directory of transcripts -- plus four rules a PDF needs and text does not.

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

🛑 **3. PII lives outside the content stream.** Metadata, XMP, embedded attachments,
annotations, form fields and bookmarks all survive every redaction annotation ever applied,
because none of them is in the stream the annotation covers. They are swept here, because a
document redacted in the only place anyone looked is worse than one nobody redacted. The
transcript passes never see these surfaces at all -- conversion reads the page -- so this
module is the only place in the package that can.

🛑 **4. A removal is visible.** ``Page.add_redact_annot`` fills with **white** unless told
otherwise, so the default output is a document with silent gaps in it: the value is genuinely
gone, and a reader cannot tell whether we removed something, the scanner dropped a line, or
the court left the field blank. That is the wrong default anywhere and a worse one in an
accountability archive, where the published document is evidence and an unexplained hole in
it is a question nobody can answer. NSA's *Redacting with Confidence* makes the same point
from the other side -- if deletion disturbs the layout, put filler of comparable size in its
place -- and every court redaction guideline assumes a mark a reader can see. So every
rectangle is filled with :data:`REDACTION_FILL`, which is black, and the shape of the mark
carries what the gap could not: that a removal happened, and roughly how much.

What this module does **not** do on its own is the other two page classes: legacy-encoded
text needs likhit's own font decode plumbed through to glyph rectangles, and a pure scan needs
bounding-box OCR, which likhit's LLM-vision OCR cannot provide because it returns text without
coordinates. By default both refuse loudly rather than silently passing a page through. With
``ocr=True`` they are read by Tesseract instead (:mod:`likhit.privacy.pdf_ocr`) and burned:
a scan in its pixels with its whole text layer removed, a legacy page by replacing it with a
burned render. ``transcript`` adds the values the text path removed, so the PDF never shows
what the published transcript hides.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import fitz

from collections.abc import Callable, Mapping

from . import pdf_ocr
from .contact import iter_contact_targets
from .modes import DEFAULT_MODE, RedactionMode
from .redact import iter_inline_targets
from .text import redact_text

#: What a removed value is replaced with on the page: an opaque black rectangle, module
#: rule 4.
#:
#: 🛑 **The default is white, and white is indistinguishable from "nothing was here".**
#: ``add_redact_annot(rect)`` with no ``fill`` paints the span in white and leaves the page
#: looking untouched. The redaction is real either way -- the glyphs are gone from the
#: content stream -- so this is not a disclosure, it is a *legibility* defect, which is why
#: it survived a review that was looking for leaks.
#:
#: ⚠️ **No text label is written into the box, and that is a deliberate limit rather than a
#: later nicety.** ``add_redact_annot(text=...)`` renders with a base-14 font, which has no
#: Devanagari, so a Nepali marker comes out as a row of empty boxes; and a Latin one
#: (`[REDACTED]`, 10 characters) does not fit the span of the 10-digit value it replaces, so
#: PyMuPDF shrinks it until it does or clips it. A clipped label is worse than no label: it
#: looks like a rendering fault in our output rather than a statement about it. The typed
#: marker lives where it can be set in full -- `[REDACTED:PHONE-NO]` in the transcript -- and
#: the box says *that* a removal happened, with the journal saying of what kind.
REDACTION_FILL = (0.0, 0.0, 0.0)

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

#: A page on which images cover at least this share of the area is a scan, whatever its text
#: layer says (scanners add an invisible OCR layer). Measured on the JAW-167 corpus: scanned
#: charge sheets are ~1.0 (one full-page raster), born-digital pages with a logo or a stamp
#: well under 0.2, so the cut sits in a wide empty gap.
IMAGE_PAGE_COVERAGE = 0.5

#: Render resolution of a flattened page. 200 dpi keeps 10-pt Devanagari legible (matras
#: included) at roughly a third of the bytes of 300 dpi.
FLATTEN_DPI = 200

#: JPEG quality for gray/colour images re-encoded after pixel redaction. 75 is libjpeg's
#: default; on the JAW-167 corpus the total size went from 52.8 MB in to 50.9 MB out.
REENCODE_JPEG_QUALITY = 75

#: OCR re-reads of the written file. The JAW-167 corpus needed a second round on 2 pages of
#: 255 (a box changes the segmentation of its line) and never a third.
RESIDUE_ROUNDS = 2


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


def classify_text_layer(
    page: fitz.Page, text: str | None = None, *, trusted_unicode: bool = False
) -> PageClass:
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

    ``trusted_unicode`` changes ONE decision: a page with text whose Devanagari share is
    below :data:`UNICODE_DEVANAGARI_RATIO` is :data:`PageClass.UNICODE` instead of
    :data:`PageClass.LEGACY`. The ratio cannot tell an English page from a legacy-font one
    (both are Latin codepoints); the caller can, when it produced the text layer itself
    from Unicode text. See :func:`redact_pdf` for that contract. ``BLANK`` and ``SCANNED``
    are decided before the ratio and never change, and a page that images mostly cover
    stays ``LEGACY``: a scanner's invisible OCR layer is Latin too, and trusting it would
    redact the text layer while the picture a reader sees kept every value.
    """

    if text is None:
        text = page_glyph_map(page)[0]
    text = text.strip()
    if not text:
        # Images OR vector drawings: a page can carry a scanned stamp as either, and both
        # are ink that may be a signature, a seal or a handwritten number.
        # get_image_info, not get_images: an INLINE image (BI ... EI) is ink too, and
        # get_images does not list it -- a text-free inline scan was called BLANK.
        has_ink = bool(page.get_image_info()) or bool(page.get_drawings())
        return PageClass.SCANNED if has_ink else PageClass.BLANK
    if (
        len(_TEXT_LAYER_DEVANAGARI.findall(text)) / len(text)
        >= UNICODE_DEVANAGARI_RATIO
    ):
        return PageClass.UNICODE
    if trusted_unicode and not _is_image_page(page):
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
    #: Values removed from annotations, form fields and bookmarks. Counted apart from
    #: :attr:`values_removed`, which is the page count and is what ``rects_applied`` pairs
    #: with -- a value taken out of a sticky note has no rectangle and changed no page.
    outside_content_removed: int = 0
    annotations_deleted: int = 0
    counters: Counter = field(default_factory=Counter)
    #: One row per removed value: kind and shape, never the value. Same rule as
    #: :mod:`likhit.privacy.tree` -- a record of what was removed has to be publishable
    #: beside the thing it was removed from.
    span_shapes: list[dict] = field(default_factory=list)
    #: Pages this module cannot redact, with the class that blocked them.
    blocked_pages: list[dict] = field(default_factory=list)
    #: How each page was redacted: ``vector`` (glyphs deleted), ``image`` (a scan, burned
    #: into its pixels, text layer stripped), ``flatten`` (replaced by a burned render) or
    #: ``blank``. Only ``vector`` and ``blank`` occur without ``ocr=True``.
    pages_by_path: Counter = field(default_factory=Counter)
    #: Values the page transcripts' text redaction removes, and how many of them were
    #: placed on their page by an exact match.
    guided_values: int = 0
    guided_placed: int = 0
    #: Guided values with no exact match on any read: page, kind and masked shape.
    unplaced: list[dict] = field(default_factory=list)
    #: Boxes added by the OCR re-read of the written file (see ``_residue_pass``).
    residue_boxes: int = 0
    #: Whether the caller vouched for the text layer (``redact_pdf(trusted_unicode=True)``),
    #: and the 1-based pages that were Unicode only because of it -- below the Devanagari
    #: ratio, so they would have been ``legacy`` without it.
    trusted_unicode: bool = False
    trusted_unicode_pages: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "pages": self.pages,
            "pages_by_class": dict(sorted(self.pages_by_class.items())),
            "pages_changed": self.pages_changed,
            "values_removed": self.values_removed,
            "rects_applied": self.rects_applied,
            "outside_content_removed": self.outside_content_removed,
            "annotations_deleted": self.annotations_deleted,
            "counters": dict(sorted(self.counters.items())),
            "blocked_pages": self.blocked_pages,
            "pages_by_path": dict(sorted(self.pages_by_path.items())),
            "guided_values": self.guided_values,
            "guided_placed": self.guided_placed,
            "unplaced": self.unplaced,
            "residue_boxes": self.residue_boxes,
            "trusted_unicode": self.trusted_unicode,
            "trusted_unicode_pages": self.trusted_unicode_pages,
            "span_shapes": self.span_shapes,
            "no_matched_digits_recorded": True,
        }


def _targets(text: str, mode: RedactionMode, stats: Counter) -> list:
    """Every target in ``text`` for ``mode``, sorted by position and de-duplicated.

    🛑 **The two passes run against the same offsets here, unlike in
    :mod:`likhit.privacy.text`.** There, inline runs first and the contact pass sees its
    *output*, so a value already replaced by a placeholder carries no digits and cannot match
    twice. A PDF has no intermediate text to hand on: both passes read one glyph map, so one
    value can be claimed by both -- a citizenship number that happens to open `98` is a
    citizenship target and a mobile target over the identical span.

    Left alone that is not a disclosure (the rectangle is applied either way) but it is a
    **false record**: the same removal is counted twice in ``values_removed``, written twice
    to ``span_shapes`` and annotated twice. The journal is how this pass's precision is
    measured, so a count that double-reports is worse than no count.

    ⚠️ Only a target **fully covered** by one already accepted is dropped. A merely
    overlapping one is kept, because dropping it could leave the uncovered part of its span
    in the file -- the one outcome this module exists to prevent. Boundary assertions on both
    detectors make a partial overlap unreachable today; this is the safe behaviour if that
    ever stops being true, rather than an assumption that it cannot.
    """

    found: list = []
    if mode.removes_identifiers:
        found.extend(iter_inline_targets(text, stats, mode))
    if mode.removes_contact:
        found.extend(iter_contact_targets(text, stats))

    # Longest-first within a position, so the widest span is the one that gets to cover the
    # narrower duplicates nested inside it rather than the other way round.
    found.sort(key=lambda target: (target.start, -(target.end - target.start)))
    kept: list = []
    for target in found:
        if any(
            accepted.start <= target.start and target.end <= accepted.end
            for accepted in kept
        ):
            stats[f"{target.kind}_refused_duplicate_span"] += 1
            continue
        kept.append(target)
    return kept


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
    page: fitz.Page,
    mode: RedactionMode,
    report: PdfRedactionReport,
    extra: list[fitz.Rect] | None = None,
) -> int:
    """Redact one Unicode page. Returns the number of values removed.

    🛑 **Every character of every value must carry a rectangle, or the page refuses.** A span
    the detector found but the geometry cannot place is a value this module would leave in the
    file while reporting it as removed, which is the one outcome worse than refusing.

    ``extra`` are boxes for guided values (see :mod:`likhit.privacy.pdf_ocr`) that were placed
    on this page's glyph map; they are burned in the same ``apply_redactions`` call.
    """

    text, rects = page_glyph_map(page)
    targets = _targets(text, mode, report.counters)
    if not targets and not extra:
        return 0

    to_redact: list[fitz.Rect] = []
    for target in targets:
        # 🛑 **The synthetic line separator is not an unplaceable glyph.** `page_glyph_map`
        # inserts `"\n"` with a `None` rect between lines; it is in the text so the patterns
        # can see line structure, and it has no glyph *by construction*. Counting it as a
        # character this pass "could not place" refused any value that wrapped -- which the
        # same module's docstring advertises as supported -- and refused the whole
        # **document** for it. An English date makes that routine rather than exotic:
        # `Date of Birth: 27 August 1979` carries two mandatory spaces, so any column break
        # inside it tripped the guard.
        #
        # The guard itself is unchanged in substance: every character that *does* have a
        # glyph must carry a rectangle, or the value is one this pass would report as
        # removed while leaving it in the file.
        span: list[fitz.Rect] = []
        unplaceable = 0
        for index in range(target.start, target.end):
            rect = rects[index]
            if rect is not None:
                span.append(rect)
            elif text[index] != "\n":
                unplaceable += 1
        if unplaceable:
            raise ValueError(
                f"page {page.number + 1}: {unplaceable} of "
                f"{target.end - target.start} characters of a {target.kind} value have no "
                "glyph rectangle; refusing rather than reporting a value as removed that "
                "this pass could not place"
            )
        to_redact.extend(_merge_rects(span))
        report.span_shapes.append(
            {
                "surface": "page",
                "page": page.number + 1,
                "kind": target.kind,
                "value_chars_removed": target.end - target.start,
                "value_digit_count": target.digit_count,
                "value_had_separators": target.had_separators,
            }
        )

    to_redact.extend(extra or ())
    # 🛑 Every annotation first, then ONE apply_redactions for the page. Applying per
    # annotation is the documented performance trap and the usual cause of a loop that
    # redacts only its last iteration.
    for rect in to_redact:
        page.add_redact_annot(rect, fill=REDACTION_FILL)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)

    report.rects_applied += len(to_redact)
    return len(targets)


def _scrub_document_metadata(doc: fitz.Document) -> None:
    """Remove the PII that never appears on a page: metadata, XMP, embedded files."""

    doc.set_metadata({})
    doc.del_xml_metadata()
    for name in reversed(doc.embfile_names()):
        doc.embfile_del(name)


def _annotation_text(annot: fitz.Annot) -> str:
    """The free text a reader can get out of one annotation.

    ``content`` is the note body and ``subject`` its one-line heading; both are typed by
    whoever made the annotation and both are where a phone number ends up. ``title`` is
    **not** read: PyMuPDF puts the annotation's author there, and an author is a name -- the
    one class no tier removes (see :mod:`likhit.privacy.modes`). Reading it would find
    nothing this package would act on and would put a name through the detectors.
    """

    info = annot.info or {}
    return "\n".join(str(info.get(key) or "") for key in ("content", "subject"))


def _sweep_outside_content(
    doc: fitz.Document, mode: RedactionMode, report: PdfRedactionReport
) -> None:
    """Rule 3, for the three surfaces that hold *text* rather than document properties.

    Annotations, form-field values and bookmark titles are not in any page's content stream,
    so :func:`_redact_page` cannot see them and ``apply_redactions`` does not touch them. A
    sticky note reading "call him on 98XXXXXXXX" survives a redaction that removed the same
    number from the page beneath it. NSA's *Redacting with Confidence* makes verifying that
    comments are gone a step of its own, which is the shape of a mistake people keep making.

    🛑 **An annotation carrying a value is deleted whole, not rewritten.** Rewriting
    ``content`` leaves the annotation's *appearance stream* -- the pre-rendered picture of
    itself that a viewer draws -- holding the old text until something regenerates it, and
    whether ``Annot.update()`` can regenerate it depends on the annotation's type. Deleting
    takes the appearance with it and asks no questions. The cost is that an innocuous
    sentence wrapped around a phone number goes too; the journal records that it happened,
    and an annotation is commentary layered onto the record rather than the record.

    ⚠️ **Field values and bookmark titles are redacted in place instead, because neither has
    an appearance this module would be guessing about:** a widget's is regenerated by
    ``update()`` on every supported field type, and a bookmark has none -- it is a string in
    the outline tree. They also carry structure worth keeping, where a note does not: a
    cleared form field is still a form, and a redacted bookmark still navigates.
    """

    for page in doc:
        # Collected first, deleted second: `delete_annot` relinks the annotation chain it is
        # iterating, so deleting mid-walk silently skips whatever followed the deleted one.
        doomed = []
        for annot in page.annots():
            targets = _targets(_annotation_text(annot), mode, Counter())
            if targets:
                doomed.append((annot, targets))
        for annot, targets in doomed:
            for target in targets:
                report.counters["annotation_targeted"] += 1
                report.span_shapes.append(
                    {
                        "surface": "annotation",
                        "page": page.number + 1,
                        "kind": target.kind,
                        "value_chars_removed": target.end - target.start,
                        "value_digit_count": target.digit_count,
                        "value_had_separators": target.had_separators,
                    }
                )
            page.delete_annot(annot)
            report.annotations_deleted += 1
            report.outside_content_removed += len(targets)

        for widget in page.widgets() or ():
            value = widget.field_value
            if not isinstance(value, str) or not value:
                continue
            result = redact_text(value, mode=mode)
            if not result.changed:
                continue
            widget.field_value = result.text
            widget.update()
            report.counters["form_field_targeted"] += len(result.journal)
            report.outside_content_removed += len(result.journal)
            for row in result.journal:
                report.span_shapes.append(
                    {"surface": "form_field", "page": page.number + 1, **row}
                )

    toc = doc.get_toc(simple=True)
    rewritten, removed_in_toc = [], 0
    for level, title, target_page in toc:
        result = redact_text(title or "", mode=mode)
        removed_in_toc += len(result.journal)
        for row in result.journal:
            report.span_shapes.append({"surface": "bookmark", "page": None, **row})
        rewritten.append([level, result.text, target_page])
    if removed_in_toc:
        doc.set_toc(rewritten)
        report.counters["bookmark_targeted"] += removed_in_toc
        report.outside_content_removed += removed_in_toc


def _verify_outside_content(doc: fitz.Document, mode: RedactionMode) -> None:
    """Re-read the three text surfaces of the written file and refuse on any survivor.

    The page check next to this one reads the content stream and would pass a document whose
    every removed number is still sitting in a bookmark. Checking the surfaces a pass writes
    to is the only way its own failure becomes visible: ``Widget.update()`` can decline to
    take a value, and ``set_toc`` can decline a tree, and neither raises where the caller is
    looking.

    ⚠️ Every page is checked here, not just the redactable ones. A page's *class* describes
    its glyph layer; an annotation is a string hanging off the page object and is equally
    readable whatever the page beneath it is made of.
    """

    for page in doc:
        for annot in page.annots():
            if _targets(_annotation_text(annot), mode, Counter()):
                raise ValueError(
                    f"verification failed: an annotation on page {page.number + 1} still "
                    "carries a value in the removal set after redaction"
                )
        for widget in page.widgets() or ():
            value = widget.field_value
            if isinstance(value, str) and _targets(value, mode, Counter()):
                raise ValueError(
                    f"verification failed: form field {widget.field_name!r} on page "
                    f"{page.number + 1} still carries a value in the removal set"
                )
    for _level, title, _page in doc.get_toc(simple=True):
        if _targets(title or "", mode, Counter()):
            raise ValueError(
                "verification failed: a bookmark title still carries a value in the "
                "removal set after redaction"
            )


def _verify(
    data: bytes,
    mode: RedactionMode,
    checked_pages: frozenset[int],
    raster_pages: frozenset[int] = frozenset(),
) -> None:
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
            if page.number in raster_pages:
                # A scan or flattened page keeps NO text layer: whatever a scanner's OCR
                # wrote there was never checked by anyone, so none of it may survive.
                if page_glyph_map(page)[0].strip():
                    raise ValueError(
                        f"verification failed: page {page.number + 1} was redacted as an "
                        "image but still carries a text layer"
                    )
                continue
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
        _verify_outside_content(doc, mode)


def _unrotated_rect(page: fitz.Page) -> fitz.Rect:
    """The whole page in the coordinates PyMuPDF's drawing and redaction calls use.

    🛑 Not ``page.rect``: on a page with ``/Rotate 90`` or ``270`` that is the *displayed*
    shape (width and height swapped), while annotations, ``insert_image``, and
    ``get_image_rects`` work in the unrotated space. A whole-page redaction drawn with
    ``page.rect`` on a rotated landscape page missed its far corner, leaving text there.
    """

    return fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)


def _is_image_page(page: fitz.Page) -> bool:
    """Is most of this page one raster -- a scan, whatever its text layer claims?

    A scanner's own OCR layer (invisible Latin text over the image) makes a scan classify
    :data:`PageClass.LEGACY` or even :data:`PageClass.UNICODE`; the page is still a picture,
    and the picture is what a reader sees, so the picture is what must be redacted.
    """

    whole = _unrotated_rect(page)
    if whole.is_empty:
        return False
    # get_image_info, not get_images: it also lists INLINE images (BI ... EI), which a
    # scanner may use for the whole page.
    rects = [fitz.Rect(info["bbox"]) & whole for info in page.get_image_info()]
    rects = [r for r in rects if not r.is_empty]
    if not rects:
        return False
    # The UNION of the image areas, on a coarse grid: two coincident 30% images are 30%,
    # not 60%, while disjoint tiles of one scan still add up.
    steps = 64
    dx, dy = whole.width / steps, whole.height / steps
    covered = sum(
        any(
            r.contains(fitz.Point(whole.x0 + (i + 0.5) * dx, whole.y0 + (j + 0.5) * dy))
            for r in rects
        )
        for i in range(steps)
        for j in range(steps)
    )
    return covered >= IMAGE_PAGE_COVERAGE * steps * steps


def _record_targets(
    loc: pdf_ocr.Locator,
    page: fitz.Page,
    mode: RedactionMode,
    report: PdfRedactionReport,
    surface: str,
    seen: list[tuple[int, str, str, list[fitz.Rect]]] | None = None,
) -> list[fitz.Rect]:
    """Boxes for every rule hit on a locator's text, journalled like a glyph-page hit.

    ``seen`` is shared between the locators of one page: a hit of the same kind over a
    box another LOCATOR already journalled (the glyph layer and OCR agreeing) is burned
    but not counted again. Hits of one locator are never merged -- two nearby values
    whose padded boxes touch are two values.
    """

    boxes: list[fitz.Rect] = []
    mine: list[tuple[int, str, str, list[fitz.Rect]]] = []
    for target in _targets(loc.text, mode, report.counters):
        rects = pdf_ocr.span_rects(loc, target.start, target.end)
        boxes += rects
        if seen is not None:
            # One entry per HIT (all its rects): a wrapped hit is one value.
            # Matched on the VALUE as well as the place: padded boxes of two different
            # nearby values can overlap, and overlap alone merged them into one.
            key = pdf_ocr.match_key(loc.text[target.start : target.end])
            mine.append((id(loc), target.kind, key, rects))
            # One-to-one: the first unclaimed hit of another locator that this one
            # overlaps is the same value; it is claimed (removed) so it cannot also
            # stand for a second, nearby value of this locator.
            match = next(
                (
                    n
                    for n, (owner, kind, other_key, others) in enumerate(seen)
                    if owner != id(loc)
                    and kind == target.kind
                    and other_key == key
                    and any(not (r & o).is_empty for r in rects for o in others)
                ),
                None,
            )
            if match is not None:
                del seen[match]
                continue
        report.values_removed += 1
        report.span_shapes.append(
            {
                "surface": surface,
                "page": page.number + 1,
                "kind": target.kind,
                "value_chars_removed": target.end - target.start,
                "value_digit_count": target.digit_count,
                "value_had_separators": target.had_separators,
            }
        )
    if seen is not None:
        seen += mine
    return boxes


def _record_guided(
    placement: pdf_ocr.GuidedPlacement, page: fitz.Page, report: PdfRedactionReport
) -> None:
    report.guided_placed += placement.placed
    report.guided_values += placement.placed + len(placement.unplaced)
    for kind, shape in placement.unplaced:
        report.unplaced.append({"page": page.number + 1, "kind": kind, "shape": shape})


class _Guidance:
    """The transcript's removed values, and where each must be found.

    A ``{page: text}`` transcript (one with likhit page anchors) is checked PAGE BY PAGE:
    a value is placed only on the page it belongs to.

    A ``str`` transcript has no anchors -- production's transcripts do not -- so a value
    may be anywhere. Every page is searched for every value (exact matches only, and
    without the second read), and the counts are summed across pages: two pages are two
    physical occurrences. What is still short after that is looked for once more by
    :meth:`settle`, with the second read of every rasterised page's ORIGINAL, and only
    what that misses is unplaced. Vector pages are not flattened for a missing value here, as the
    page-by-page path does: a value absent from a page is the normal case.
    """

    def __init__(
        self, transcript: Mapping[int, str] | str, mode: RedactionMode
    ) -> None:
        self.mode = mode
        self.document = isinstance(transcript, str)
        self.pages: Mapping[int, str] = (
            {} if isinstance(transcript, str) else transcript
        )
        self.values: list[tuple[str, str]] = (
            pdf_ocr.transcript_removed_values(transcript, mode)
            if isinstance(transcript, str)
            else []
        )
        self.found: dict[int, pdf_ocr.GuidedPlacement] = {}

    def values_for(self, number: int) -> list[tuple[str, str]]:
        if self.document:
            return self.values
        text = self.pages.get(number + 1)
        return pdf_ocr.transcript_removed_values(text, self.mode) if text else []

    def locate(
        self, page: fitz.Page, locators: list[pdf_ocr.Locator], *, second_read: bool
    ) -> pdf_ocr.GuidedPlacement:
        guided = self.values_for(page.number)
        if not guided:
            return pdf_ocr.GuidedPlacement([], 0, [])
        if self.document:
            return pdf_ocr.place_guided_values(
                guided, locators, approximate_boxes=False
            )
        return pdf_ocr.place_guided_values(
            guided, locators, _second_read(page) if second_read else None
        )

    def record(
        self,
        page: fitz.Page,
        placement: pdf_ocr.GuidedPlacement,
        report: PdfRedactionReport,
    ) -> None:
        if self.document:
            self.found[page.number] = placement
        else:
            _record_guided(placement, page, report)

    def blank(self, page: fitz.Page, report: PdfRedactionReport) -> None:
        # Page by page, a blank page cannot show its transcript's values, so the transcript
        # is not this PDF's: every one is unplaced. Document-level, they are elsewhere.
        if not self.document:
            _record_guided(
                pdf_ocr.unplaced_all(self.values_for(page.number)), page, report
            )

    def settle(
        self,
        doc: fitz.Document,
        source: Path,
        raster_pages: set[int],
        report: PdfRedactionReport,
    ) -> None:
        if not self.document or not self.values:
            return
        needs = pdf_ocr.GuidedNeeds.of(self.values)
        per_page: dict[int, pdf_ocr.GuidedRead] = {
            n: pdf_ocr.GuidedRead(
                Counter(placement.whole_by_key),
                Counter(placement.part_supply),
                list(placement.prints),
            )
            for n, placement in self.found.items()
        }

        def allocate() -> Counter[str]:
            # Pages are different physical occurrences: their counts add up.
            whole: Counter[str] = Counter()
            supply: Counter[str] = Counter()
            for seen in per_page.values():
                whole.update(seen.whole)
                supply.update(seen.supply)
            return needs.allocate(whole, supply)

        used = allocate()
        pending = needs.pending(used)
        if pending and raster_pages:
            # 🛑 The second read is of the ORIGINAL page, not the burned one. A scan's
            # Unicode overlay boxes a value the first OCR read missed: it is burned, but
            # uncredited, and re-reading the burned page could never find it -- a
            # correctly redacted document would refuse. Reading the original instead
            # sees what pass 1 burned as well, so per page the count is the BEST of the
            # two reads, never their sum (GuidedNeeds.combine, as in
            # place_guided_values).
            with fitz.open(source) as original:
                reads = {n: _second_read(original[n])() for n in sorted(raster_pages)}
            boxes: dict[int, list[fitz.Rect]] = {n: [] for n in reads}
            for n, loc in reads.items():
                again = needs.read(loc, needs.second_read_keys(pending), boxes[n])
                first = per_page.get(n)
                per_page[n] = needs.combine(
                    [again] if first is None else [first, again]
                )
            used = allocate()
            for key in needs.pending(used):
                for n, loc in reads.items():
                    for needle in needs.needles(key):
                        for s, e in pdf_ocr.locate_value(loc, needle, approximate=True):
                            boxes[n] += pdf_ocr.span_rects(loc, s, e)
            for n, page_boxes in boxes.items():
                _burn(doc[n], page_boxes)
                report.rects_applied += len(page_boxes)
        for key, n_needed in needs.needed.items():
            report.guided_values += n_needed
            report.guided_placed += used[key]
            kind, value = needs.sample[key]
            report.unplaced += [
                {"page": None, "kind": kind, "shape": pdf_ocr._shape(value)}
            ] * (n_needed - used[key])


def _second_read(page: fitz.Page) -> Callable[[], pdf_ocr.Locator]:
    return lambda: pdf_ocr.ocr_char_locator(
        page, dpi=pdf_ocr.OCR_SECOND_READ_DPI, lang="nep", psm="6"
    )


def _strip_text_layer(page: fitz.Page) -> None:
    """Delete every glyph on the page and nothing else (images and line art stay)."""

    page.add_redact_annot(_unrotated_rect(page))
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )


def _burn(page: fitz.Page, boxes: list[fitz.Rect]) -> None:
    for rect in boxes:
        page.add_redact_annot(rect, fill=REDACTION_FILL)
    if boxes:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)


def _redact_image_page(
    page: fitz.Page,
    page_class: PageClass,
    mode: RedactionMode,
    report: PdfRedactionReport,
    guidance: _Guidance,
) -> None:
    """A scan: locate by OCR (plus the glyph map if it is Unicode), burn into the pixels,
    then delete the page's whole text layer -- a junk OCR layer carries digits too."""

    locators = [pdf_ocr.ocr_char_locator(page)]
    if page_class is PageClass.UNICODE:
        text, rects = page_glyph_map(page)
        locators.append(pdf_ocr.Locator(text, rects, ocr=False))
    boxes: list[fitz.Rect] = []
    seen: list[tuple[int, str, str, list[fitz.Rect]]] = []
    for loc in locators:
        boxes += _record_targets(loc, page, mode, report, "page", seen)
    placement = guidance.locate(page, locators, second_read=True)
    boxes += placement.boxes
    guidance.record(page, placement, report)
    _strip_text_layer(page)
    _burn(page, boxes)
    report.rects_applied += len(boxes)
    report.pages_changed += 1


def _flatten_page(
    page: fitz.Page,
    mode: RedactionMode,
    report: PdfRedactionReport,
    guidance: _Guidance,
) -> None:
    """A vector page whose text cannot be trusted (legacy font, or a Unicode layer missing
    values the transcript has): replace the page by a burned render of itself."""

    loc = pdf_ocr.ocr_char_locator(page)
    boxes = _record_targets(loc, page, mode, report, "page")
    placement = guidance.locate(page, [loc], second_read=True)
    boxes += placement.boxes
    guidance.record(page, placement, report)
    # Rendered UNROTATED and inserted upright, so /Rotate keeps doing the turning and the
    # boxes (already in unrotated space) need no turning. The earlier way -- render the
    # rotated view, insert it counter-rotated -- clipped 90/270 pages, and after
    # rewrite_images striped the image of 180/270 pages (measured, PyMuPDF 1.26).
    rotation = page.rotation
    page.set_rotation(0)
    try:
        # annots=False: an annotation's text is not in the OCR read, so baking it into the
        # pixels could publish a value nothing checked; the structured sweep removes it.
        render = page.get_pixmap(dpi=FLATTEN_DPI, colorspace=fitz.csGRAY, annots=False)
    finally:
        page.set_rotation(rotation)
    to_render = fitz.Matrix(FLATTEN_DPI / 72.0, FLATTEN_DPI / 72.0)
    for rect in boxes:
        area = fitz.IRect((rect * to_render).round()) & render.irect
        if not area.is_empty:
            render.set_rect(area, (0,))
    whole = _unrotated_rect(page)
    page.add_redact_annot(whole)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_REMOVE,
        graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )
    page.clean_contents()
    page.insert_image(whole, stream=render.tobytes("png"))
    report.rects_applied += len(boxes)
    report.pages_changed += 1


def _residue_pass(
    data: bytes, raster_pages: set[int], mode: RedactionMode, report: PdfRedactionReport
) -> bytes:
    """Re-read every raster page of the WRITTEN bytes; burn whatever the rules still find.

    🛑 **On the saved file, not the in-memory document.** Re-encoding a changed scan alters
    what OCR reads, and so does every box already drawn (segmentation changes around it).
    Measured on the JAW-167 corpus: a re-read of the written outputs found 21 values the
    first pass had missed (wallet and mobile numbers in transaction tables, IMEIs, e-mail
    IDs), all on pages with no transcript. Up to :data:`RESIDUE_ROUNDS` rounds; anything
    still found after that refuses the document.
    """

    pending = set(raster_pages)
    for round_number in range(1, RESIDUE_ROUNDS + 1):
        if not pending:
            return data
        with fitz.open(stream=data, filetype="pdf") as doc:
            changed: set[int] = set()
            for number in sorted(pending):
                page = doc[number]
                loc = pdf_ocr.ocr_char_locator(page)
                boxes = _record_targets(
                    loc, page, mode, report, f"page_residue_{round_number}"
                )
                if boxes:
                    _burn(page, boxes)
                    report.residue_boxes += len(boxes)
                    report.rects_applied += len(boxes)
                    changed.add(number)
            if not changed:
                return data
            _reencode_images(doc)
            data = doc.tobytes(garbage=4, deflate=True, deflate_images=True)
        pending = changed
    with fitz.open(stream=data, filetype="pdf") as doc:
        for number in sorted(pending):
            loc = pdf_ocr.ocr_char_locator(doc[number])
            residue = _targets(loc.text, mode, Counter())
            if residue:
                raise ValueError(
                    f"verification failed: OCR still reads {len(residue)} value(s) on page "
                    f"{number + 1} after {RESIDUE_ROUNDS} residue rounds"
                )
    return data


def _reencode_images(doc: fitz.Document) -> None:
    """Undo the size cost of pixel redaction.

    ``PDF_REDACT_IMAGE_PIXELS`` writes a changed image back as lossless Flate: a 5-page
    CCITT/JPEG charge sheet grew from 1.5 MB to 7.4 MB. 1-bit images go back to CCITT and
    gray/colour to JPEG; images that were already lossy are left alone.

    🛑 **8-bit GRAY is re-encoded here, not by ``rewrite_images``.** PyMuPDF 1.27's
    ``rewrite_images(gray=True)`` writes a 3-component JPEG under the image's 1-component
    colour space; MuPDF then renders it wrong -- a black block (a burned value, or a scan's
    solid areas) came back WHITE (measured). A gray pixmap encoded as a gray JPEG and put
    back with ``replace_image`` renders correctly.
    """

    doc.rewrite_images(
        quality=REENCODE_JPEG_QUALITY,
        lossy=False,
        lossless=True,
        bitonal=True,
        color=True,
        gray=False,
    )
    done: set[int] = set()
    for page in doc:
        for xref, smask, _w, _h, bpc, _cs, _alt, _name, filt, *_ in page.get_images(
            full=True
        ):
            if xref in done or smask or bpc != 8 or filt not in ("FlateDecode", ""):
                continue
            done.add(xref)
            pix = fitz.Pixmap(doc, xref)
            if pix.n != 1 or pix.alpha:
                continue
            # The stream swapped in place (no new image object, nothing re-drawn): a gray
            # JPEG under DeviceGray, the parameters of the Flate stream dropped.
            doc.update_stream(
                xref,
                pix.tobytes("jpeg", jpg_quality=REENCODE_JPEG_QUALITY),
                compress=False,
            )
            doc.xref_set_key(xref, "Filter", "/DCTDecode")
            doc.xref_set_key(xref, "DecodeParms", "null")
            # The pixmap already applied any /Decode inversion; keeping it would invert
            # the scan a second time.
            doc.xref_set_key(xref, "Decode", "null")
            doc.xref_set_key(xref, "ColorSpace", "/DeviceGray")
            doc.xref_set_key(xref, "BitsPerComponent", "8")


def redact_pdf(
    source: Path,
    destination: Path | None,
    *,
    mode: RedactionMode = DEFAULT_MODE,
    dry_run: bool = False,
    ocr: bool = False,
    transcript: Mapping[int, str] | str | None = None,
    trusted_unicode: bool = False,
) -> PdfRedactionReport:
    """Redact ``source`` into ``destination``, or measure it with ``dry_run``.

    ``destination`` must not exist and must not be ``source``. Raises :class:`ValueError` for
    anything that makes the result untrustworthy -- a page class this module cannot handle, a
    value it cannot locate, or residue in its own output -- and writes nothing when it does.

    ``ocr=True`` lifts the refusal of scanned and legacy-font pages (rule 2): they are read
    with Tesseract (:mod:`likhit.privacy.pdf_ocr`) and burned instead. ``transcript`` maps
    1-based page numbers to that page's UNREDACTED transcript text; every value likhit's text
    path removes from it must be placed on the page by an exact match, or the document
    refuses (``report.unplaced`` names them in a dry run). This is what keeps the published
    PDF from showing what the published text hides.

    A ``str`` ``transcript`` is a whole-document transcript with no page anchors (what
    :func:`~likhit.privacy.pdf_ocr.split_transcript_pages` returns for one): each value
    must then be placed somewhere in the document, as often as the transcript has it.

    ``trusted_unicode=True`` tells this function the text layer is Unicode by
    construction, so a page with little or no Devanagari (an English page) is redacted as
    a Unicode page -- normal detectors, glyph deletion, and the same post-redaction
    verification -- instead of refusing as a legacy-font page. Scanned, blank and
    image-covered pages are classified exactly as without it.

    🛑 **Only for a PDF whose text layer the caller produced itself from Unicode text** (for
    example rendered by Chromium/Gotenberg from HTML or markdown). Never for a third-party
    PDF: a legacy-font page decodes to Latin-looking bytes the detectors cannot read, so
    trusting it would publish its identifiers as a "redacted" document (module rule 2). The
    report records the flag and the pages it changed (``trusted_unicode_pages``).
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
    if ocr and not pdf_ocr.tesseract_available("nep+eng"):
        raise ValueError(
            "ocr=True needs the tesseract binary with the nep and eng language data; "
            "refusing rather than passing scanned pages through unread"
        )
    guidance = _Guidance(transcript or {}, mode)

    report = PdfRedactionReport(trusted_unicode=trusted_unicode)
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
        # A transcript page with no PDF page would have its guided values silently
        # dropped -- the document would pass without the check this argument exists
        # for. A mismatched transcript is the wrong transcript; refuse it.
        outside = sorted(n for n in guidance.pages if not 1 <= n <= doc.page_count)
        if outside:
            raise ValueError(
                f"the transcript has page(s) {outside[:5]} that this {doc.page_count}-page "
                "PDF does not; refusing rather than ignoring their removed values"
            )
        # Decided on the INPUT and carried to `_verify`, because redaction can change a
        # page's class -- see that function.
        classes: dict[int, PageClass] = {}
        image_pages: set[int] = set()
        for page in doc:
            text = page_glyph_map(page)[0]
            page_class = classify_text_layer(page, text)
            if trusted_unicode and page_class is PageClass.LEGACY:
                page_class = classify_text_layer(page, text, trusted_unicode=True)
                if page_class is PageClass.UNICODE:
                    report.trusted_unicode_pages.append(page.number + 1)
            classes[page.number] = page_class
            report.pages_by_class[page_class.value] += 1
            if ocr and page_class is not PageClass.BLANK and _is_image_page(page):
                image_pages.add(page.number)
            if page_class.needs_work:
                report.blocked_pages.append(
                    {"page": page.number + 1, "page_class": page_class.value}
                )

        # 🛑 Rule 2, and before a single annotation is added: a partial redaction of a
        # document whose other pages cannot be read is not a redacted document.
        if report.blocked_pages and not ocr:
            classes_seen = sorted({row["page_class"] for row in report.blocked_pages})
            if not dry_run:
                raise ValueError(
                    f"{len(report.blocked_pages)} of {report.pages} pages are "
                    f"{', '.join(classes_seen)} and cannot be redacted by this module; "
                    "refusing the document rather than emitting one whose unreadable pages "
                    "were passed through unchanged"
                )
            # ⚠️ A dry run **reports** the blockage instead of raising it, and that is the
            # difference between a refusal and a measurement. Rule 2 protects the written
            # artifact; a dry run writes nothing, so there is nothing to protect -- while
            # "which of these 1,283 pages can this tool actually read" is the question
            # anyone pointing it at a corpus has to answer first, and raising made
            # `blocked_pages` unreachable: every report a caller could hold had it empty.
            #
            # Redaction is not attempted. The page census IS the answer here, and counting
            # whatever the detectors scrape off a garbled legacy page would put numbers in
            # the journal that mean nothing.
            return report
        if ocr:
            # With OCR every page class is handled, so nothing blocks the document.
            report.blocked_pages = []

        vector_pages: set[int] = set()
        raster_pages: set[int] = set()
        for page in doc:
            number = page.number
            page_class = classes[number]
            if page_class is PageClass.BLANK and number not in image_pages:
                report.pages_by_path["blank"] += 1
                guidance.blank(page, report)
                continue
            if number in image_pages:
                _redact_image_page(page, page_class, mode, report, guidance)
                raster_pages.add(number)
                report.pages_by_path["image"] += 1
                continue
            if page_class is PageClass.UNICODE:
                text, rects = page_glyph_map(page)
                placement = guidance.locate(
                    page, [pdf_ocr.Locator(text, rects, ocr=False)], second_read=False
                )
                if not placement.unplaced or not ocr or guidance.document:
                    guidance.record(page, placement, report)
                    removed = _redact_page(page, mode, report, extra=placement.boxes)
                    if removed or placement.boxes:
                        report.pages_changed += 1
                        report.values_removed += removed
                    vector_pages.add(number)
                    report.pages_by_path["vector"] += 1
                    continue
                # The glyph layer lacks a value the transcript has (a broken ToUnicode map,
                # a CID font): the text layer is not the page a reader sees. Flatten it.
            _flatten_page(page, mode, report, guidance)
            raster_pages.add(number)
            report.pages_by_path["flatten"] += 1
        guidance.settle(doc, source, raster_pages, report)

        # Rule 3. After the pages, because adding redaction annotations puts entries in the
        # same chain this walks -- ours are consumed by `apply_redactions` above, so by here
        # every annotation left on a page is the document's own.
        #
        # ⚠️ Before the dry-run return, so a measurement counts these surfaces too. The
        # mutations are in-memory and this document is never written on that path; a census
        # that silently omitted every sticky note would be the wrong number to plan with.
        _sweep_outside_content(doc, mode, report)

        if dry_run:
            return report
        if report.unplaced:
            raise ValueError(
                f"{len(report.unplaced)} value(s) the transcript removes could not be placed "
                "exactly on the page (see report.unplaced in a dry run); refusing rather than "
                "publishing a PDF that may show what the published text hides"
            )

        _scrub_document_metadata(doc)
        if raster_pages:
            _reencode_images(doc)
        # Full save, never incremental -- see this module's rule 1.
        data = doc.tobytes(garbage=4, deflate=True, deflate_images=True)

    if raster_pages:
        data = _residue_pass(data, raster_pages, mode, report)
    _verify(data, mode, frozenset(vector_pages), frozenset(raster_pages))

    assert destination is not None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return report
