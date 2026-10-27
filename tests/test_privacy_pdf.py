"""PDF redaction: what it removes, and -- more importantly -- what it refuses.

Every test here that asserts a refusal is pinning a way this module could ship a file that
looks redacted and is not. Those matter more than the happy path: a redaction that does not
happen is visible, and one that half-happens is not.
"""

from __future__ import annotations

from collections import Counter

import fitz
import pytest

from likhit.privacy import (
    PageClass,
    RedactionMode,
    classify_text_layer,
    redact_pdf,
    scan_text,
)
from likhit.privacy.pdf import page_glyph_map
from likhit.privacy.redact import iter_inline_targets, redact_inline_text

# A Devanagari font is needed for the text to survive as Devanagari in the written PDF.
# Noto is what the test corpus's own synthetic builders use; skip rather than fail where it
# is absent, because a missing system font is not a defect in this module.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
)


def _devanagari_font() -> str:
    import os

    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    pytest.skip("no Devanagari system font available to build a synthetic PDF")


def _make_pdf(path, lines: list[str]) -> None:
    font = _devanagari_font()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="dev", fontfile=font)
    y = 72.0
    for line in lines:
        page.insert_text((72, y), line, fontname="dev", fontsize=12)
        y += 20
    doc.save(path)
    doc.close()


CITIZENSHIP_LINE = "नागरिकता नं. १२-३४-५६-७८९०१"
MOBILE_LINE = "सम्पर्क ९८५१२३४५६७"


def test_classify_text_layer_names_the_four_classes(tmp_path):
    source = tmp_path / "unicode.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE])
    with fitz.open(source) as doc:
        assert classify_text_layer(doc[0]) is PageClass.UNICODE

    with fitz.open() as doc:
        doc.new_page()
        assert classify_text_layer(doc[0]) is PageClass.BLANK


def test_legacy_and_scanned_pages_are_not_redactable():
    assert not PageClass.LEGACY.is_redactable
    assert not PageClass.SCANNED.is_redactable
    assert PageClass.LEGACY.needs_work and PageClass.SCANNED.needs_work
    # 🛑 A blank page must NOT block a document, or every report with a cover sheet refuses.
    assert not PageClass.BLANK.needs_work


def test_glyph_map_is_index_aligned_with_its_text(tmp_path):
    source = tmp_path / "aligned.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE, MOBILE_LINE])
    with fitz.open(source) as doc:
        text, rects = page_glyph_map(doc[0])
    assert len(text) == len(rects)
    # Every character that is not the synthetic newline carries a rectangle.
    assert all((rect is None) == (char == "\n") for char, rect in zip(text, rects))


def test_identifiers_mode_removes_the_value_and_keeps_the_label(tmp_path):
    source, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE])
    report = redact_pdf(source, out, mode=RedactionMode.IDENTIFIERS)
    assert report.values_removed == 1

    with fitz.open(out) as doc:
        after = doc[0].get_text()
    assert "नागरिकता" in after, "the label is kept -- only the value is removed"
    assert "१२-३४-५६-७८९०१" not in after
    # True removal, not a drawn box: the digits are gone from the file's bytes.
    assert "१२-३४-५६-७८९०१".encode() not in out.read_bytes()


def test_identifiers_mode_leaves_contact_details_alone(tmp_path):
    source, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE, MOBILE_LINE])
    redact_pdf(source, out, mode=RedactionMode.IDENTIFIERS)
    with fitz.open(out) as doc:
        after = doc[0].get_text()
    assert "९८५१२३४५६७" in after, (
        "promoting contact details to the removal set is a scope change; the default tier "
        "must not quietly make it"
    )


def test_contact_mode_removes_the_mobile(tmp_path):
    source, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE, MOBILE_LINE])
    report = redact_pdf(source, out, mode=RedactionMode.CONTACT)
    assert report.values_removed == 2

    with fitz.open(out) as doc:
        after = doc[0].get_text()
    assert not sum(scan_text(after)[0].values())
    assert "९८५१२३४५६७".encode() not in out.read_bytes()


def test_journal_records_shapes_and_never_a_value(tmp_path):
    source, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE])
    report = redact_pdf(source, out, mode=RedactionMode.IDENTIFIERS)
    (shape,) = report.span_shapes
    assert shape["value_digit_count"] == 11
    assert shape["kind"] == "citizenship"
    assert "१२-३४-५६-७८९०१" not in str(report.as_dict())


def test_metadata_is_scrubbed(tmp_path):
    source, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE])
    with fitz.open(source) as doc:
        doc.set_metadata({"author": "राम बहादुर", "title": "आरोप पत्र"})
        doc.save(tmp_path / "meta.pdf")
    redact_pdf(tmp_path / "meta.pdf", out, mode=RedactionMode.IDENTIFIERS)
    with fitz.open(out) as doc:
        assert not doc.metadata.get("author")
        assert not doc.metadata.get("title")


def test_mode_none_is_refused_rather_than_producing_a_redacted_looking_copy(tmp_path):
    source = tmp_path / "in.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE])
    with pytest.raises(ValueError, match="removes nothing"):
        redact_pdf(source, tmp_path / "out.pdf", mode=RedactionMode.NONE)


def test_refuses_to_redact_in_place(tmp_path):
    source = tmp_path / "in.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE])
    with pytest.raises(ValueError, match="in place"):
        redact_pdf(source, source, mode=RedactionMode.IDENTIFIERS)


def test_refuses_to_overwrite_an_existing_destination(tmp_path):
    source, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _make_pdf(source, [CITIZENSHIP_LINE])
    out.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        redact_pdf(source, out, mode=RedactionMode.IDENTIFIERS)


def test_a_scanned_page_blocks_the_document_and_writes_nothing(tmp_path):
    """🛑 The failure this module exists to prevent: a page nothing can read, passed through.

    A scanned page yields no text, so the detectors find nothing in it -- which is not the
    same as there being nothing there.
    """

    source, out = tmp_path / "scan.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    page = doc.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
    pixmap.set_rect(pixmap.irect, (128, 128, 128))
    page.insert_image(fitz.Rect(72, 72, 172, 172), pixmap=pixmap)
    doc.save(source)
    doc.close()

    with fitz.open(source) as opened:
        assert classify_text_layer(opened[0]) is PageClass.SCANNED

    with pytest.raises(ValueError, match="scanned"):
        redact_pdf(source, out, mode=RedactionMode.CONTACT)
    assert not out.exists(), "a refusal must leave no partial artifact behind"


def test_a_value_that_wraps_across_a_line_is_redacted_not_refused(tmp_path):
    """🛑 `page_glyph_map` advertises that a value broken across a line is still located --
    and the rectangle check then refused the whole **document** for it.

    The synthetic `"\\n"` between lines carries a `None` rect *by construction*: it is in the
    text so the patterns can see line structure, and it has no glyph. Counting it as a
    character the pass "could not place" turned every wrapped value into a refusal. An
    English date makes that routine rather than exotic -- `Date of Birth: 27 August 1979`
    has two mandatory spaces, so any column break inside it trips it.
    """

    font = _devanagari_font()
    source, out = tmp_path / "wrap.pdf", tmp_path / "wrap-out.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="dev", fontfile=font)
    # Devanagari so the page classifies UNICODE; the date is split as a column would wrap it.
    page.insert_text((72, 52), "नागरिकता सम्बन्धी विवरण", fontname="dev", fontsize=12)
    page.insert_text((72, 72), "Date of Birth: 27", fontname="helv", fontsize=12)
    page.insert_text((72, 92), "August 1979", fontname="helv", fontsize=12)
    doc.save(source)
    doc.close()

    report = redact_pdf(source, out, mode=RedactionMode.STRICT)

    assert report.values_removed == 1
    # One rectangle per line, which is what `_merge_rects` is for.
    assert report.rects_applied == 2

    with fitz.open(out) as written:
        text = written[0].get_text()
    for fragment in ("27", "August", "1979"):
        assert fragment not in text, f"{fragment} survived a wrapped redaction"
    assert "Date of Birth" in text, "the label is kept"


def test_a_dry_run_reports_blocked_pages_instead_of_raising(tmp_path):
    """🛑 `blocked_pages` was unreachable: the only code path that filled it raised on the
    next line, so every report a caller could hold had it empty.

    A dry run writes nothing, so rule 2 has no artifact to protect -- while "which of these
    pages can this tool actually read" is the first question anyone pointing it at a corpus
    has to answer.
    """

    source = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
    pixmap.set_rect(pixmap.irect, (128, 128, 128))
    page.insert_image(fitz.Rect(72, 72, 172, 172), pixmap=pixmap)
    doc.save(source)
    doc.close()

    report = redact_pdf(source, None, mode=RedactionMode.STRICT, dry_run=True)

    assert report.blocked_pages == [{"page": 1, "page_class": "scanned"}]
    assert report.pages_by_class["scanned"] == 1
    # Redaction is not attempted on a blocked document, so these stay zero rather than
    # carrying whatever the detectors scraped off an unreadable page.
    assert report.values_removed == 0

    # The write path still refuses -- the dry run is a measurement, not a permission.
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError, match="scanned"):
        redact_pdf(source, out, mode=RedactionMode.STRICT)
    assert not out.exists()


def test_the_cli_does_not_exit_zero_on_a_dry_run_it_could_not_read(tmp_path):
    """⚠️ "values removed: 0" for a document whose every page is unreadable must not look
    like a clean bill of health to a CI step reading the exit code."""

    from likhit.privacy._cli import main as redact_main

    source = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
    pixmap.set_rect(pixmap.irect, (128, 128, 128))
    page.insert_image(fitz.Rect(72, 72, 172, 172), pixmap=pixmap)
    doc.save(source)
    doc.close()

    code = redact_main(
        ["pdf", str(source), "--dry-run", "--journal", str(tmp_path / "j.json")]
    )
    assert code == 1


def test_one_value_claimed_by_both_passes_is_counted_once(tmp_path):
    """🛑 In a PDF both passes read ONE glyph map, so a value can be claimed twice.

    Unlike `redact_text`, there is no intermediate text for the contact pass to see the
    inline pass's placeholders in. A labelled Nepali mobile matches the labelled contact rule
    and `signals.MOBILE` over the identical span; counting it twice would make the journal --
    the record this pass's precision is measured from -- disagree with the page.
    """

    source = tmp_path / "dup.pdf"
    _make_pdf(source, [MOBILE_LINE])

    report = redact_pdf(source, None, mode=RedactionMode.CONTACT, dry_run=True)

    assert report.values_removed == 1
    assert len(report.span_shapes) == 1


def test_an_encrypted_pdf_is_refused_by_name(tmp_path):
    """Without this guard the failure is a bare `ValueError: document closed or encrypted`
    from inside PyMuPDF, on first page access -- which reads like a corrupt file rather than
    a refusal, and tells the caller nothing about what to do."""

    source, out = tmp_path / "enc.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "contact 9851234567")
    doc.save(
        str(source),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()

    with pytest.raises(ValueError, match="password-protected"):
        redact_pdf(source, out, mode=RedactionMode.CONTACT)
    assert not out.exists()


def test_verify_checks_a_page_even_if_redaction_changed_its_class(tmp_path):
    """🛑 Redaction can push a page out of the class that selected it for verification.

    Devanagari digits ०-९ are U+0966-U+096F, inside the `[ऀ-ॿ]` class, so removing them
    lowers the Devanagari count *and* the total. A page just above
    `UNICODE_DEVANAGARI_RATIO` can fall below it purely by being redacted -- measured on a
    constructed page, 0.257 before and 0.103 after. An earlier `_verify` re-classified the
    output and skipped anything no longer `UNICODE`, which skipped exactly the pages most
    likely to have been partially redacted.
    """

    from likhit.privacy.pdf import _verify

    source = tmp_path / "latin-heavy.pdf"
    # Deliberately below the Devanagari ratio, so `classify_text_layer` calls it LEGACY --
    # and it still carries a value in the removal set.
    _make_pdf(
        source,
        [
            "Reference ABCDEFGHIJKLMNOP QRSTUVWXYZ 0123456789",
            "Continued ABCDEFGHIJKLMNOP QRSTUVWXYZ 0123456789",
            "Continued ABCDEFGHIJKLMNOP QRSTUVWXYZ 0123456789",
            "सम्पर्क ९८५१२३४५६७",
        ],
    )
    with fitz.open(source) as doc:
        assert not classify_text_layer(doc[0]).is_redactable, (
            "fixture must be a page the old class-based check would have skipped"
        )
        data = doc.tobytes()

    # Not in the checked set -> no opinion offered.
    _verify(data, RedactionMode.CONTACT, frozenset())
    # In the checked set -> the residue is found despite the page's class.
    with pytest.raises(ValueError, match="verification failed"):
        _verify(data, RedactionMode.CONTACT, frozenset({0}))


def test_targets_agree_with_redact_on_the_corpus_samples(tmp_path):
    """`iter_inline_targets` and `redact` must qualify the same values.

    They iterate differently -- `redact` substitutes one pattern's matches before the next
    pattern runs, `iter_inline_targets` reports offsets into the text as given -- so this
    pins the claim in `iter_inline_targets`'s docstring rather than leaving it as an argument.
    """

    text = "\n".join(
        [
            CITIZENSHIP_LINE,  # 11 digits -- redacted
            "जन्म मिति २०४५।०३।१२",  # danda-separated date -- redacted
            MOBILE_LINE,  # not in the identifier scope at all
            # 🛑 Refused by guard 2, and it is the only guard this fixture CAN exercise.
            # Guard 1 (`VALUE_FORBIDDEN`) and guard 3 (`AMOUNT_CUE`) are built as the
            # complement of the value classes and search inside the value, so a span that
            # reaches them has already been constrained to digits and separators -- an
            # earlier version of this test asserted all three fire and asserted nothing.
            "नागरिकता नं. ४५६७८",  # 5 digits, below the floor of 7 -- a service count
        ]
    )
    stats: Counter = Counter()
    targets = list(iter_inline_targets(text, stats))
    _, journal, redact_stats = redact_inline_text(text)

    assert len(targets) == len(journal)
    assert Counter(t.kind for t in targets) == Counter(row["kind"] for row in journal)
    assert [t.digit_count for t in targets] == [
        row["value_digit_count"] for row in journal
    ]
    # The guards' verdicts must match exactly. `*_redacted` is deliberately NOT compared:
    # `redact` counts replacements written, `iter_inline_targets` counts targets selected,
    # and conflating the two is what the `_targeted` naming exists to prevent.
    refusals = {k: v for k, v in redact_stats.items() if "refused" in k}
    assert refusals, "this fixture must exercise at least one guard refusal"
    assert {k: v for k, v in stats.items() if "refused" in k} == refusals
    assert sum(v for k, v in stats.items() if k.endswith("_targeted")) == len(journal)


# ---------------------------------------------------------------------------
# Rule 4: a removal is visible.
# ---------------------------------------------------------------------------

MOBILE_VALUE = "९८५१२३४५६७"


def _ink_ratio(path, rect) -> float:
    """Fraction of dark pixels inside ``rect``, which is how a mark is told from a gap."""

    with fitz.open(path) as doc:
        pixmap = doc[0].get_pixmap(clip=rect, dpi=150)
    samples, stride = pixmap.samples, pixmap.n
    pixels = len(samples) // stride
    dark = sum(1 for i in range(0, len(samples), stride) if samples[i] < 50)
    return dark / pixels


def test_a_removed_value_leaves_a_mark_a_reader_can_see(tmp_path):
    """🛑 The regression this pins is **not** a leak: the value is gone either way.

    It is that PyMuPDF fills a redaction with white unless told otherwise, so the page came
    out with a silent hole in it -- indistinguishable, to a reader, from a scanner dropping
    a line or a court leaving a field blank. See this module's rule 4.
    """

    source = tmp_path / "mobile.pdf"
    _make_pdf(source, [MOBILE_LINE])
    with fitz.open(source) as doc:
        boxes = doc[0].search_for(MOBILE_VALUE)
    assert boxes, "fixture must put the value on the page where it can be located"
    span = boxes[0]

    destination = tmp_path / "mobile.redacted.pdf"
    redact_pdf(source, destination, mode=RedactionMode.STRICT)

    assert _ink_ratio(destination, span) > 0.9, "the span must be painted, not emptied"
    assert _ink_ratio(source, span) < 0.9, "the fixture must not be black to begin with"


def test_the_fill_is_opaque_black():
    """The constant is asserted directly as well: the pixel test above would pass just as
    happily on any dark colour, and the choice of colour is the policy."""

    from likhit.privacy.pdf import REDACTION_FILL

    assert REDACTION_FILL == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Rule 3: the text surfaces outside the content stream.
# ---------------------------------------------------------------------------


def _make_pdf_with(path, lines: list[str], *, note=None, field=None, toc=None) -> None:
    """``_make_pdf`` plus the surfaces that are not in any content stream.

    Built and saved in one pass: PyMuPDF refuses a non-incremental save back over the file
    it opened, and an incremental one is what this module's rule 1 forbids.
    """

    base = path.with_name(path.stem + ".base.pdf")
    _make_pdf(base, lines)
    with fitz.open(base) as doc:
        if note is not None:
            annot = doc[0].add_text_annot(fitz.Point(300, 300), note)
            annot.update()
        if field is not None:
            widget = fitz.Widget()
            widget.field_name = "contact"
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.rect = fitz.Rect(300, 400, 500, 420)
            widget.field_value = field
            doc[0].add_widget(widget)
        if toc is not None:
            doc.set_toc(toc)
        doc.save(str(path))


def test_an_annotation_carrying_a_value_is_deleted(tmp_path):
    """A sticky note is not in any content stream, so `apply_redactions` never saw it."""

    source = tmp_path / "noted.pdf"
    _make_pdf_with(source, [CITIZENSHIP_LINE], note=f"witness {MOBILE_LINE}")

    destination = tmp_path / "noted.redacted.pdf"
    report = redact_pdf(source, destination, mode=RedactionMode.STRICT)

    with fitz.open(destination) as doc:
        assert list(doc[0].annots()) == [], "the annotation must not survive"
    assert report.annotations_deleted == 1
    assert report.outside_content_removed == 1
    assert report.counters["annotation_targeted"] == 1
    assert {row["surface"] for row in report.span_shapes} == {"page", "annotation"}


def test_an_annotation_with_nothing_in_the_removal_set_survives(tmp_path):
    """⚠️ The sweep deletes whole annotations, so over-reach here costs a reader real text.
    Deletion has to be driven by a detector hit, not by the annotation existing."""

    source = tmp_path / "innocent.pdf"
    _make_pdf_with(source, [CITIZENSHIP_LINE], note="हेर्नुहोस् अनुसूची ३")

    destination = tmp_path / "innocent.redacted.pdf"
    report = redact_pdf(source, destination, mode=RedactionMode.STRICT)

    with fitz.open(destination) as doc:
        assert len(list(doc[0].annots())) == 1
    assert report.annotations_deleted == 0


def test_a_form_field_value_is_redacted_in_place(tmp_path):
    """A widget keeps its structure -- a cleared field is still a field -- so unlike an
    annotation it is rewritten rather than deleted."""

    source = tmp_path / "form.pdf"
    _make_pdf_with(source, [CITIZENSHIP_LINE], field="Contact 9851234567")

    destination = tmp_path / "form.redacted.pdf"
    report = redact_pdf(source, destination, mode=RedactionMode.STRICT)

    with fitz.open(destination) as doc:
        values = [widget.field_value for widget in doc[0].widgets()]
    assert values, "the field itself must survive; only its value is removed"
    assert "9851234567" not in values[0]
    assert "[REDACTED:PHONE-NO]" in values[0]
    assert report.counters["form_field_targeted"] == 1


def test_a_bookmark_title_is_redacted(tmp_path):
    source = tmp_path / "toc.pdf"
    _make_pdf_with(source, [CITIZENSHIP_LINE], toc=[[1, f"जाहेरवाला {MOBILE_VALUE}", 1]])

    destination = tmp_path / "toc.redacted.pdf"
    report = redact_pdf(source, destination, mode=RedactionMode.STRICT)

    with fitz.open(destination) as doc:
        titles = [row[1] for row in doc.get_toc(simple=True)]
    assert titles, "the outline must still navigate"
    assert MOBILE_VALUE not in titles[0]
    assert "[REDACTED:PHONE-NO]" in titles[0]
    assert report.counters["bookmark_targeted"] == 1


def test_verification_refuses_when_the_sweep_does_not_run(tmp_path, monkeypatch):
    """🛑 The verifier has to be able to fail, or it is decoration.

    Neutralising the sweep is the only way to produce the output it exists to catch: a file
    whose pages are clean and whose annotations are not.
    """

    import likhit.privacy.pdf as pdf_module

    source = tmp_path / "unswept.pdf"
    _make_pdf_with(source, [CITIZENSHIP_LINE], note=f"witness {MOBILE_LINE}")

    monkeypatch.setattr(pdf_module, "_sweep_outside_content", lambda *a, **k: None)
    destination = tmp_path / "unswept.redacted.pdf"
    with pytest.raises(ValueError, match="annotation on page 1 still carries"):
        redact_pdf(source, destination, mode=RedactionMode.STRICT)
    assert not destination.exists(), "a refusal writes nothing"
