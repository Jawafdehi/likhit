"""Scanned and legacy-font pages: ``redact_pdf(..., ocr=True)`` and :mod:`likhit.privacy.pdf_ocr`.

Most tests replace Tesseract with a stub locator built from a vector page's own glyph map,
so the geometry is exact and the suite needs no OCR binary. The stub is the contract the
real reader must meet (page-space rectangles, one per character); the last tests run the
real ``tesseract`` and skip where it, or its ``nep`` data, is absent.
"""

from __future__ import annotations

import os

import fitz
from collections import Counter

import pytest

from likhit.privacy import RedactionMode, redact_pdf, redact_text
from likhit.privacy import pdf as pdf_module
from likhit.privacy import pdf_ocr
from likhit.privacy.pdf_ocr import (
    Locator,
    locate_value,
    parse_hocr_chars,
    place_guided_values,
    span_rects,
    split_transcript_pages,
    transcript_removed_values,
)

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
)

# Synthetic values only (repo is public): a mobile, and a citizenship number under its label.
MOBILE = "९८५१२३४५६७"
MOBILE_LINE = f"सम्पर्क नं. {MOBILE}"
CITIZENSHIP_LINE = "ना.प्र.नं. १२-३४-५६-७८९०१"
PLAIN_LINE = "रकम रु. ५०,०००।- बुझेको"


def _font() -> str:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    pytest.skip("no Devanagari system font available to build a synthetic PDF")


def _vector_page_doc(lines: list[str], fontsize: float = 14) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="dev", fontfile=_font())
    y = 90.0
    for line in lines:
        page.insert_text((72, y), line, fontname="dev", fontsize=fontsize)
        y += fontsize * 2
    return doc


def _scan_of(lines: list[str], *, junk_layer: str | None = None, dpi: int = 150):
    """``(pdf bytes, stub locator)``: an image-only page showing ``lines``, and the exact
    character geometry of what the image shows (from the vector page it was rendered from)."""

    vec = _vector_page_doc(lines)
    page = vec[0]
    text, rects = pdf_module.page_glyph_map(page)
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    scan = fitz.open()
    out = scan.new_page(width=page.rect.width, height=page.rect.height)
    out.insert_image(out.rect, pixmap=pix)
    if junk_layer is not None:
        # What a scanner's OCR writes: invisible Latin text over the image.
        out.insert_text(
            (72, 300), junk_layer, fontname="helv", fontsize=9, render_mode=3
        )
    data = scan.tobytes()
    return data, Locator(text, rects, ocr=True)


def _read_what_is_visible(page: fitz.Page, loc: Locator) -> Locator:
    """The stub's 'OCR': the known characters, minus any now under a black box."""

    pix = page.get_pixmap(dpi=72, colorspace=fitz.csGRAY)
    chars = []
    for ch, rect in zip(loc.text, loc.rects):
        if rect is not None:
            area = fitz.IRect(rect.round()) & pix.irect
            if not area.is_empty:
                dark = sum(
                    pix.pixel(x, y)[0] < 60
                    for x in range(area.x0, area.x1)
                    for y in range(area.y0, area.y1)
                )
                if dark > 0.6 * area.width * area.height:
                    ch = "█"
        chars.append(ch)
    return Locator("".join(chars), list(loc.rects), ocr=True)


@pytest.fixture
def stub_ocr(monkeypatch):
    """Install a fixed locator as the 'OCR' of every page; returns a setter."""

    state: dict = {"loc": None, "calls": 0}

    def fake(page, **_kwargs):
        state["calls"] += 1
        loc = state["loc"]
        if callable(loc):
            return loc(page, state["calls"])
        return _read_what_is_visible(page, loc)

    monkeypatch.setattr(pdf_ocr, "ocr_char_locator", fake)
    monkeypatch.setattr(pdf_ocr, "tesseract_available", lambda _lang="nep": True)

    def set_locator(loc):
        state["loc"] = loc

    return set_locator


def _black_share(path, rect: fitz.Rect, page_no: int = 0) -> float:
    with fitz.open(path) as doc:
        pix = doc[page_no].get_pixmap(dpi=72, colorspace=fitz.csGRAY, clip=rect)
        samples = pix.samples
        return sum(1 for b in samples if b < 60) / max(len(samples), 1)


def _value_rect(loc: Locator, value: str) -> fitz.Rect:
    start = loc.text.index(value)
    rect = fitz.Rect(loc.rects[start])
    for r in loc.rects[start : start + len(value)]:
        if r is not None:
            rect |= r
    return rect


# --------------------------------------------------------------------------- the page paths
def test_a_scanned_page_is_burned_and_keeps_no_text(tmp_path, stub_ocr):
    data, loc = _scan_of([MOBILE_LINE, PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)

    report = redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    assert report.pages_by_path["image"] == 1
    assert report.values_removed == 1
    out = tmp_path / "out.pdf"
    with fitz.open(out) as doc:
        assert doc[0].get_text().strip() == ""
    assert _black_share(out, _value_rect(loc, MOBILE)) > 0.9
    # The amount on the next line is not a removal-set value and stays legible.
    assert _black_share(out, _value_rect(loc, "५०,०००")) < 0.5


def test_a_scanners_junk_text_layer_is_removed_whole(tmp_path, stub_ocr):
    # The junk layer carries a number the image does not show where the OCR reads it. It
    # must go anyway: nobody checked it, and text extraction would publish it.
    data, loc = _scan_of([PLAIN_LINE], junk_layer="Mob 9851234567 acct 01234567890123")
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    before = fitz.open(src)[0].get_pixmap(dpi=36, colorspace=fitz.csGRAY)

    redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    with fitz.open(tmp_path / "out.pdf") as doc:
        assert doc[0].get_text().strip() == ""
        # ...and only the text: the picture is untouched (no white fill over the scan).
        after = doc[0].get_pixmap(dpi=36, colorspace=fitz.csGRAY)
    diff = sum(abs(a - b) for a, b in zip(before.samples, after.samples))
    assert diff / len(before.samples) < 3
    assert min(after.samples) < 200, "the scan's ink is gone"


def test_without_ocr_a_scan_still_refuses(tmp_path):
    data, _loc = _scan_of([MOBILE_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    with pytest.raises(ValueError, match="cannot be redacted"):
        redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT)
    assert not (tmp_path / "out.pdf").exists()


def test_ocr_refuses_when_tesseract_is_missing(tmp_path, monkeypatch):
    data, _loc = _scan_of([MOBILE_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    monkeypatch.setattr(pdf_ocr, "tesseract_available", lambda _lang="nep": False)
    with pytest.raises(ValueError, match="tesseract"):
        redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)


def test_a_guided_value_is_burned_where_the_rules_alone_miss_it(tmp_path, stub_ocr):
    # The OCR mangled the label, so the rules find nothing on the page; the transcript
    # (a better reading) has the label, and its redaction removes the value. The PDF must
    # hide what the text hides.
    data, loc = _scan_of([f"ALIA, {MOBILE} बुझेको"])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    transcript = {1: f"सम्पर्क नं. {MOBILE} बुझेको\n"}

    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        ocr=True,
        transcript=transcript,
    )

    assert report.guided_values == 1 and report.guided_placed == 1
    assert _black_share(tmp_path / "out.pdf", _value_rect(loc, MOBILE)) > 0.9


def test_a_guided_value_that_cannot_be_placed_refuses_the_document(tmp_path, stub_ocr):
    data, loc = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    transcript = {1: f"{PLAIN_LINE}\n{MOBILE_LINE}\n"}

    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )
    assert dry.unplaced == [{"page": 1, "kind": "PHONE-NO", "shape": "##########"}]
    # The journal never carries the value itself.
    assert MOBILE not in repr(dry.as_dict())

    with pytest.raises(ValueError, match="could not be placed"):
        redact_pdf(
            src,
            tmp_path / "out.pdf",
            mode=RedactionMode.STRICT,
            ocr=True,
            transcript=transcript,
        )
    assert not (tmp_path / "out.pdf").exists()


def test_a_legacy_font_page_is_flattened_and_burned(tmp_path, stub_ocr):
    # Preeti keystrokes in a Latin font: the text layer is not what a reader sees.
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), ";Dks{ g+= (*%!@#$%^&", fontname="helv", fontsize=14)
    src = tmp_path / "legacy.pdf"
    doc.save(src)
    # What OCR of the render "reads": the decoded line, at the same place.
    vec = _vector_page_doc([MOBILE_LINE])
    text, rects = pdf_module.page_glyph_map(vec[0])
    stub_ocr(Locator(text, rects, ocr=True))

    report = redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    assert report.pages_by_path["flatten"] == 1
    with fitz.open(tmp_path / "out.pdf") as out:
        assert out[0].get_text().strip() == ""
        assert len(out[0].get_images()) == 1
    loc = Locator(text, rects, ocr=False)
    assert _black_share(tmp_path / "out.pdf", _value_rect(loc, MOBILE)) > 0.9


def test_a_unicode_page_whose_glyph_layer_lacks_a_transcript_value_is_flattened(
    tmp_path, stub_ocr
):
    # A broken ToUnicode map: the page SHOWS the number, the glyph layer does not carry it.
    doc = _vector_page_doc([PLAIN_LINE])
    picture = _vector_page_doc([MOBILE_LINE])
    text, rects = pdf_module.page_glyph_map(picture[0])
    value_rect = _value_rect(Locator(text, rects, ocr=False), MOBILE)
    clip = fitz.Rect(
        value_rect.x0 - 2, value_rect.y0 - 2, value_rect.x1 + 2, value_rect.y1 + 2
    )
    doc[0].insert_image(clip, pixmap=picture[0].get_pixmap(dpi=200, clip=clip))
    src = tmp_path / "broken.pdf"
    doc.save(src)
    stub_ocr(Locator(text, rects, ocr=True))

    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        ocr=True,
        transcript={1: f"{PLAIN_LINE}\n{MOBILE_LINE}\n"},
    )

    assert report.pages_by_path["flatten"] == 1
    assert report.unplaced == []
    assert _black_share(tmp_path / "out.pdf", value_rect) > 0.9


def test_a_unicode_page_takes_guided_values_on_its_glyph_map(tmp_path):
    # The glyph layer garbles the label (as real Kalimati exports do), so the rules miss the
    # value; the transcript's redaction removes it, and the vector path deletes the glyphs.
    doc = _vector_page_doc([f"सम्फ्क नं. {MOBILE}"])
    src = tmp_path / "garbled.pdf"
    doc.save(src)

    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        transcript={1: f"{MOBILE_LINE}\n"},
    )

    assert report.pages_by_path["vector"] == 1 and report.guided_placed == 1
    with fitz.open(tmp_path / "out.pdf") as out:
        assert "९८५१" not in out[0].get_text()


def test_the_residue_pass_burns_what_a_reread_of_the_written_file_finds(
    tmp_path, stub_ocr
):
    data, loc = _scan_of([MOBILE_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    empty = Locator("", [], ocr=True)

    # First read misses the number; the re-read of the written file finds it; after that
    # it is gone.
    def reads(_page, call):
        return Locator(loc.text, list(loc.rects), ocr=True) if call == 2 else empty

    stub_ocr(reads)
    report = redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    assert report.residue_boxes >= 1
    assert _black_share(tmp_path / "out.pdf", _value_rect(loc, MOBILE)) > 0.9


def test_residue_that_survives_every_round_refuses(tmp_path, stub_ocr):
    data, loc = _scan_of([MOBILE_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    # Every read, including the final check, still "sees" the number (as if the box missed).
    stub_ocr(lambda _page, _call: Locator(loc.text, list(loc.rects), ocr=True))
    with pytest.raises(ValueError, match="residue rounds"):
        redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)
    assert not (tmp_path / "out.pdf").exists()


def test_a_redacted_scan_is_reencoded_not_stored_as_lossless_flate(tmp_path, stub_ocr):
    data, loc = _scan_of([MOBILE_LINE, PLAIN_LINE], dpi=200)
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    with fitz.open(tmp_path / "out.pdf") as out:
        xref = out[0].get_images(full=True)[0][0]
        assert "DCTDecode" in out.xref_get_key(xref, "Filter")[1]


# --------------------------------------------------------------------------- pdf_ocr units
def test_parse_hocr_chars_keeps_lines_words_and_scales_boxes():
    hocr = (
        "<span class='ocr_line' id='l1'>"
        "<span class='ocrx_word' id='w1'>"
        "<span class='ocrx_cinfo' title='x_bboxes 10 20 30 40; x_conf 90'>९</span>"
        "<span class='ocrx_cinfo' title='x_bboxes 30 20 50 40; x_conf 90'>८</span>"
        "</span><span class='ocrx_word' id='w2'>"
        "<span class='ocrx_cinfo' title='x_bboxes 60 20 80 40; x_conf 90'>&amp;</span>"
        "</span></span><span class='ocr_line' id='l2'><span class='ocrx_word' id='w3'>"
        "<span class='ocrx_cinfo' title='x_bboxes 10 60 30 80; x_conf 90'>क</span>"
    )
    text, rects = parse_hocr_chars(hocr, 0.5)
    assert text == "९८ &\nक"
    assert rects[0] == fitz.Rect(5, 10, 15, 20)
    assert rects[2] is None and rects[4] is None


def test_split_transcript_pages_uses_likhits_page_anchors():
    md = "front\n<!-- likhit:page 1 -->\nएक\n<!-- likhit:page 2 -->\nदुई\n"
    assert split_transcript_pages(md) == {1: "\nएक\n", 2: "\nदुई\n"}
    # No anchors: a whole-document transcript, placed anywhere in the PDF.
    assert split_transcript_pages("no anchors") == "no anchors"


def test_transcript_removed_values_match_the_text_path_and_split_lists():
    text = f"{CITIZENSHIP_LINE}\nसम्पर्क नं. {MOBILE}, ९८०१२३४५६७ छ।\n{PLAIN_LINE}\n"
    values = transcript_removed_values(text, RedactionMode.STRICT)
    kinds = sorted(k for k, _ in values)
    assert kinds == ["CITIZENSHIP-NO", "PHONE-NO", "PHONE-NO"]
    assert (("PHONE-NO", MOBILE)) in values
    assert transcript_removed_values(PLAIN_LINE, RedactionMode.STRICT) == []


def _loc(text: str) -> Locator:
    return Locator(
        text, [fitz.Rect(i, 0, i + 1, 10) for i in range(len(text))], ocr=True
    )


def test_locate_value_exact_ignores_separators_and_finds_every_occurrence():
    loc = _loc(f"मो. {MOBILE} र फेरि मो. ९८५-१२३-४५६७")
    assert len(locate_value(loc, MOBILE, approximate=False)) == 2


def test_locate_value_approximate_tolerates_an_ocr_slip_but_exact_does_not():
    garbled = "मो. ९८५१२३४५६१"  # last digit misread
    loc = _loc(garbled)
    assert locate_value(loc, MOBILE, approximate=False) == []
    assert locate_value(loc, MOBILE, approximate=True)


def test_a_short_value_needs_a_label_shaped_token_before_it():
    assert locate_value(
        _loc("वडा नं. ४ बर्ष ४५६७ को"), "४५६७", approximate=False
    )  # 'नं' within reach
    assert locate_value(_loc("ALIA, ४५६७"), "४५६७", approximate=False)
    assert locate_value(_loc("बस्ने ४५६७ जना मध्ये"), "४५६७", approximate=False) == []


def test_place_guided_values_counts_only_exact_matches_per_occurrence():
    loc = _loc(f"मो. {MOBILE}")
    values = [("PHONE-NO", MOBILE), ("PHONE-NO", MOBILE)]  # the transcript has it twice
    placement = place_guided_values(values, [loc])
    assert placement.placed == 1
    assert placement.unplaced == [("PHONE-NO", "##########")]


def test_place_guided_values_uses_the_second_read_only_when_needed():
    first = _loc("मो. ९८५१२३४५६१")
    second = _loc(f"मो. {MOBILE}")
    calls = []
    placement = place_guided_values(
        [("PHONE-NO", MOBILE)], [first], lambda: calls.append(1) or second
    )
    assert placement.placed == 1 and placement.unplaced == [] and calls == [1]
    placement = place_guided_values(
        [("PHONE-NO", MOBILE)], [second], lambda: calls.append(1) or second
    )
    assert calls == [1]


def test_span_rects_grow_over_the_token_and_pad_on_ocr_only():
    loc = _loc("x १२३४५६७ y")
    exact = Locator(loc.text, loc.rects, ocr=False)
    start = loc.text.index("२")
    (grown,) = span_rects(loc, start, start + 3)
    (plain,) = span_rects(exact, start, start + 3)
    assert plain == fitz.Rect(start, 0, start + 3, 10)
    assert grown.x0 < loc.text.index("१") and grown.x1 > loc.text.index("७") + 1


# --------------------------------------------------------------------------- real Tesseract
_needs_tesseract = pytest.mark.skipif(
    not pdf_ocr.tesseract_available("nep+eng"),
    reason="tesseract with nep+eng not installed",
)


@_needs_tesseract
def test_real_tesseract_redacts_a_rendered_scan(tmp_path):
    data, loc = _scan_of([MOBILE_LINE, PLAIN_LINE], dpi=300)
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)

    report = redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    assert report.pages_by_path["image"] == 1
    # OCR boxes hug the ink, not the font's em box: measure over the digits' ink.
    with fitz.open(src) as original:
        pix = original[0].get_pixmap(
            dpi=72, colorspace=fitz.csGRAY, clip=_value_rect(loc, MOBILE)
        )
        xs = [i % pix.width for i, b in enumerate(pix.samples) if b < 128]
        ys = [i // pix.width for i, b in enumerate(pix.samples) if b < 128]
        r = _value_rect(loc, MOBILE)
        ink = fitz.Rect(
            r.x0 + min(xs), r.y0 + min(ys), r.x0 + max(xs) + 1, r.y0 + max(ys) + 1
        )
    assert _black_share(tmp_path / "out.pdf", ink) > 0.95
    with fitz.open(tmp_path / "out.pdf") as out:
        reread = pdf_ocr.ocr_char_locator(out[0])
    assert "९८५१२३" not in reread.text and "985123" not in reread.text


def test_the_cli_reads_a_transcript_and_exits_1_on_a_dry_run_with_unplaced_values(
    tmp_path, stub_ocr
):
    from likhit.privacy._cli import main

    data, loc = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    transcript = tmp_path / "t.md"
    transcript.write_text(
        f"<!-- likhit:page 1 -->\n{PLAIN_LINE}\n{MOBILE_LINE}\n", encoding="utf-8"
    )

    code = main(
        [
            "pdf",
            str(src),
            "--journal",
            str(tmp_path / "j.json"),
            "--dry-run",
            "--ocr",
            "--transcript",
            str(transcript),
            "--mode",
            "strict",
        ]
    )

    assert code == 1
    assert "##########" in (tmp_path / "j.json").read_text(encoding="utf-8")


def test_two_reads_finding_the_same_copy_do_not_place_a_value_printed_twice():
    # The transcript has the number twice; both reads find only the FIRST copy (the
    # second is garbled on both). One occurrence is one occurrence, whoever found it.
    page_text = f"मो. {MOBILE} र मो. ९८५१२३४५६१"
    first, second = _loc(page_text), _loc(page_text)
    placement = place_guided_values(
        [("PHONE-NO", MOBILE), ("PHONE-NO", MOBILE)], [first], lambda: second
    )
    assert placement.placed == 1
    assert placement.unplaced == [("PHONE-NO", "##########")]


def test_an_unplaced_email_is_masked_in_the_journal(tmp_path, stub_ocr):
    data, loc = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    transcript = {1: f"{PLAIN_LINE}\nइमेल: ram.bahadur@example.com\n"}

    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )

    assert dry.unplaced, dry.unplaced
    journal = repr(dry.as_dict())
    assert "ram.bahadur" not in journal and "example" not in journal


def test_a_transcript_page_the_pdf_does_not_have_refuses(tmp_path, stub_ocr):
    data, loc = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    with pytest.raises(ValueError, match="does not"):
        redact_pdf(
            src,
            tmp_path / "out.pdf",
            mode=RedactionMode.STRICT,
            ocr=True,
            transcript={1: PLAIN_LINE, 2: MOBILE_LINE},
        )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_rotated_landscape_legacy_page_is_flattened_whole(
    tmp_path, stub_ocr, rotation
):
    # Text and a mark in the corner that only the UNROTATED page rect covers.
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((72, 90), ";Dks{ g+= (*%!", fontname="helv", fontsize=14)
    page.insert_text((700, 580), "(*%!@#", fontname="helv", fontsize=14)
    page.draw_rect(fitz.Rect(780, 540, 842, 595), fill=(0, 0, 0))
    page.set_rotation(rotation)
    src = tmp_path / "rotated.pdf"
    doc.save(src)
    before = doc[0].get_pixmap(dpi=36, colorspace=fitz.csGRAY)
    stub_ocr(Locator("", [], ocr=True))

    report = redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    assert report.pages_by_path["flatten"] == 1
    with fitz.open(tmp_path / "out.pdf") as out:
        assert out[0].get_text().strip() == ""
        assert out[0].get_drawings() == []
        after = out[0].get_pixmap(dpi=36, colorspace=fitz.csGRAY)
    assert (after.width, after.height) == (before.width, before.height)
    diff = sum(abs(a - b) for a, b in zip(before.samples, after.samples))
    assert diff / len(before.samples) < 3, (
        "the flattened render is misplaced or clipped"
    )
    # The corner mark itself, wherever this rotation displays it: a clipped corner
    # moves the whole-page mean by under 2, so it is checked directly.
    corner = (
        fitz.Rect(790, 550, 832, 585) * doc[0].rotation_matrix * fitz.Matrix(0.5, 0.5)
    )
    for pix in (before, after):
        x, y = int((corner.x0 + corner.x1) / 2), int((corner.y0 + corner.y1) / 2)
        assert pix.pixel(x, y)[0] < 80, "the corner mark is gone"


def test_transcript_values_for_a_blank_page_refuse_the_document(tmp_path, stub_ocr):
    doc = fitz.open()
    doc.new_page()
    src = tmp_path / "blank.pdf"
    doc.save(src)
    stub_ocr(Locator("", [], ocr=True))
    transcript = {1: MOBILE_LINE}

    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )
    assert [u["kind"] for u in dry.unplaced] == ["PHONE-NO"]
    with pytest.raises(ValueError, match="could not be placed"):
        redact_pdf(
            src,
            tmp_path / "out.pdf",
            mode=RedactionMode.STRICT,
            ocr=True,
            transcript=transcript,
        )


def test_a_scan_drawn_upside_down_keeps_its_picture(tmp_path, stub_ocr):
    # Some scanners place the page image with a 180° matrix instead of /Rotate.
    vec = _vector_page_doc([PLAIN_LINE, MOBILE_LINE])
    pix = vec[0].get_pixmap(dpi=100, colorspace=fitz.csGRAY)
    doc = fitz.open()
    page = doc.new_page(width=vec[0].rect.width, height=vec[0].rect.height)
    page.insert_image(page.rect, pixmap=pix, rotate=180)
    src = tmp_path / "flipped.pdf"
    doc.save(src)
    before = doc[0].get_pixmap(dpi=36, colorspace=fitz.csGRAY)
    # The 'OCR' finds the mobile number, so the page is burned and re-encoded. Where the
    # box lands does not matter here, only that the rest of the picture survives.
    text, rects = pdf_module.page_glyph_map(vec[0])
    stub_ocr(Locator(text, rects, ocr=True))

    report = redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)
    assert report.pages_changed == 1

    with fitz.open(tmp_path / "out.pdf") as out:
        after = out[0].get_pixmap(dpi=36, colorspace=fitz.csGRAY)
    diff = sum(abs(a - b) for a, b in zip(before.samples, after.samples))
    assert diff / len(before.samples) < 3


def test_one_number_written_two_ways_is_still_two_occurrences_to_place():
    # The transcript has the mobile twice, once in ASCII digits with dashes; the page
    # shows one copy legibly and the other garbled. One match cannot place both.
    page_text = f"मो. {MOBILE} र मो. ९८५१२३४५६१"
    placement = place_guided_values(
        [("PHONE-NO", MOBILE), ("PHONE-NO", "985-123-4567")], [_loc(page_text)]
    )
    assert placement.placed == 1
    assert len(placement.unplaced) == 1


def test_one_digit_run_never_places_two_copies_of_a_value():
    # Two copies required; the page shows one run in which the needle fits twice
    # (overlapping) -- and which, being longer than the value, is not it at all.
    run = _loc("मो. 989898989898")
    assert len(locate_value(run, "9898989898", approximate=False)) <= 1
    placement = place_guided_values(
        [("PHONE-NO", "9898989898"), ("PHONE-NO", "9898989898")], [run]
    )
    assert placement.placed <= 1 and placement.unplaced
    # Two separate copies are two occurrences.
    two = _loc("मो. 9898989898 र 9898989898")
    assert len(locate_value(two, "9898989898", approximate=False)) == 2


def test_two_separate_fields_do_not_join_into_an_exact_match():
    # `98512` and `34567` in neighbouring cells: their digit stream holds the mobile,
    # but no single value on the page is it.
    loc = _loc("वडा 98512 घर नं 34567")
    assert locate_value(loc, "9851234567", approximate=False) == []
    assert (
        len(locate_value(_loc("मो. 985-123 4567"), "9851234567", approximate=False))
        == 1
    )


def test_on_a_scan_only_the_picture_can_place_a_value():
    # The junk glyph layer has the number; the OCR of the picture does not see it.
    glyphs = _loc(f"मो. {MOBILE}")
    glyphs = Locator(glyphs.text, glyphs.rects, ocr=False)
    picture = _loc("मो. ##########")
    placement = place_guided_values([("PHONE-NO", MOBILE)], [picture, glyphs])
    assert placement.placed == 0
    assert placement.boxes, "the glyph match is still burned"


def test_coincident_images_count_their_area_once():
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 10, 10), 0)
    top, middle = fitz.Rect(0, 0, 600, 240), fitz.Rect(0, 240, 600, 480)  # 30% each

    def page_with(*areas):
        page = fitz.open().new_page(width=600, height=800)
        for area in areas:
            page.insert_image(area, pixmap=pix, keep_proportion=False)
        return page

    assert not pdf_module._is_image_page(page_with(top, top))  # one 30% area, twice
    assert pdf_module._is_image_page(page_with(top, middle))  # disjoint tiles: 60%


def test_the_cli_refuses_an_unreadable_transcript_by_path(tmp_path, capsys):
    from likhit.privacy._cli import main

    data, _loc_ = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    code = main(
        [
            "pdf",
            str(src),
            "--journal",
            str(tmp_path / "j.json"),
            "--dry-run",
            "--ocr",
            "--transcript",
            str(tmp_path / "missing.md"),
        ]
    )
    assert code == 2
    assert "missing.md" in capsys.readouterr().err


def test_a_number_inside_a_longer_number_is_not_an_exact_match():
    # An account number that happens to contain the mobile, and the real mobile garbled.
    loc = _loc("खाता नं. 009851234567 मो. 98S1234567")
    assert locate_value(loc, "9851234567", approximate=False) == []
    placement = place_guided_values([("PHONE-NO", "9851234567")], [loc])
    assert placement.placed == 0 and len(placement.unplaced) == 1


def test_an_address_inside_a_longer_address_is_not_an_exact_match():
    loc = _loc("इमेल joann@example.com र ann@exarnple.com")
    assert locate_value(loc, "ann@example.com", approximate=False) == []
    assert (
        locate_value(_loc("ann@example.com.np"), "ann@example.com", approximate=False)
        == []
    )
    assert (
        len(
            locate_value(
                _loc("(ann@example.com)."), "ann@example.com", approximate=False
            )
        )
        == 1
    )


def test_dates_with_dandas_and_values_wrapped_at_a_line_end_still_match():
    assert (
        len(
            locate_value(_loc("जन्ममिति २०४०।०५।१२ गते"), "२०४०।०५।१२", approximate=False)
        )
        == 1
    )
    wrapped = _loc("(ना.प्र.प.न.२७-०१-७५-\nछ १२३४५,काभ्रे)")
    assert len(locate_value(wrapped, "२७-०१-७५-१२३४५", approximate=False)) == 1
    # ...not when a word stands between the two lines' digits,
    assert (
        locate_value(_loc("मो. 98512\nघर 34567"), "9851234567", approximate=False) == []
    )
    # ...and two words between two numbers on one line still keep them apart.
    assert (
        locate_value(_loc("२७०१७५ घर नं १२३४५"), "२७०१७५१२३४५", approximate=False) == []
    )


def test_two_nearby_hits_of_one_locator_are_two_values_but_agreeing_locators_one():
    page = fitz.open().new_page()
    text = "मो. ९८५१२३४५६७, ९८५१२३४५६८ मा"
    report = pdf_module.PdfRedactionReport()
    seen: list = []
    pdf_module._record_targets(
        _loc(text), page, RedactionMode.STRICT, report, "page", seen
    )
    assert report.values_removed == 2  # padded boxes touch; still two numbers
    glyphs = _loc(text)
    glyphs = Locator(glyphs.text, glyphs.rects, ocr=False)
    pdf_module._record_targets(glyphs, page, RedactionMode.STRICT, report, "page", seen)
    assert report.values_removed == 2  # the glyph layer agreeing adds nothing


def test_one_ocr_box_cannot_stand_for_two_nearby_glyph_values():
    page = fitz.open().new_page()
    report = pdf_module.PdfRedactionReport()
    seen: list = []
    # OCR reads the first number; the second is garbled beyond the rules.
    pdf_module._record_targets(
        _loc("मो. ९८५१२३४५६७, ९८५१२३#$%६८ मा"),
        page,
        RedactionMode.STRICT,
        report,
        "page",
        seen,
    )
    assert report.values_removed == 1
    glyphs = _loc("मो. ९८५१२३४५६७, ९८५१२३४५६८ मा")
    glyphs = Locator(glyphs.text, glyphs.rects, ocr=False)
    pdf_module._record_targets(glyphs, page, RedactionMode.STRICT, report, "page", seen)
    assert report.values_removed == 2


def test_an_inline_full_page_image_makes_a_scan():
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 72), "x")  # a content stream to append to
    xref = page.get_contents()[0]
    doc.update_stream(
        xref,
        doc.xref_stream(xref)
        + b"\nq 600 0 0 800 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x00 EI Q\n",
    )
    assert page.get_images(full=True) == []  # the case get_images misses
    assert pdf_module._is_image_page(page)


def test_a_wrapped_ocr_hit_is_one_value_against_two_nearby_glyph_values():
    page = fitz.open().new_page()
    report = pdf_module.PdfRedactionReport()
    seen: list = []
    # The OCR hit wraps (its rects on two lines); each glyph value overlaps one line.
    ocr_text = "मो. ९८५१२३४५६७ मा"
    ocr = Locator(
        ocr_text,
        [fitz.Rect(i, 0, i + 1, 10) for i in range(9)]
        + [fitz.Rect(i - 9, 20, i - 8, 30) for i in range(9, len(ocr_text))],
        ocr=True,
    )
    pdf_module._record_targets(ocr, page, RedactionMode.STRICT, report, "page", seen)
    assert report.values_removed == 1
    glyph_text = "मो. ९८५१२३४५६७ ९८५१२३४५६८"
    glyphs = Locator(
        glyph_text,
        [fitz.Rect(i, 0, i + 1, 10) for i in range(15)]
        + [fitz.Rect(i - 15, 20, i - 14, 30) for i in range(15, len(glyph_text))],
        ocr=False,
    )
    pdf_module._record_targets(glyphs, page, RedactionMode.STRICT, report, "page", seen)
    assert report.values_removed == 2


def test_a_text_free_inline_scan_is_not_blank_and_is_redacted(tmp_path, stub_ocr):
    vec = _vector_page_doc([MOBILE_LINE])
    text, rects = pdf_module.page_glyph_map(vec[0])
    pix = vec[0].get_pixmap(dpi=72, colorspace=fitz.csGRAY)
    doc = fitz.open()
    page = doc.new_page(width=pix.width, height=pix.height)
    page.insert_text((1, 1), " ")  # gives the page a content stream; no glyphs
    xref = page.get_contents()[0]
    inline = (
        (
            f"q {pix.width} 0 0 {pix.height} 0 0 cm BI /W {pix.width} /H {pix.height} "
            "/CS /G /BPC 8 ID "
        ).encode()
        + bytes(pix.samples)
        + b" EI Q\n"
    )
    doc.update_stream(xref, inline)
    src = tmp_path / "inline.pdf"
    doc.save(src)
    assert (
        pdf_module.classify_text_layer(fitz.open(src)[0])
        is not pdf_module.PageClass.BLANK
    )
    stub_ocr(Locator(text, rects, ocr=True))

    report = redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    assert report.pages_by_path["image"] == 1
    assert (
        _black_share(
            tmp_path / "out.pdf", _value_rect(Locator(text, rects, ocr=False), MOBILE)
        )
        > 0.9
    )


def test_two_locators_finding_different_nearby_values_journal_both():
    page = fitz.open().new_page()
    report = pdf_module.PdfRedactionReport()
    seen: list = []
    # OCR finds A; the glyph layer finds a different value B right next to it, whose
    # box the padded OCR box overlaps. Two values, not one.
    pdf_module._record_targets(
        _loc("मो. ९८५१२३४५६७ ##########"),
        page,
        RedactionMode.STRICT,
        report,
        "page",
        seen,
    )
    glyphs = _loc("मो. ########## ९८४१७६५४३२")
    glyphs = Locator(glyphs.text, glyphs.rects, ocr=False)
    pdf_module._record_targets(glyphs, page, RedactionMode.STRICT, report, "page", seen)
    assert report.values_removed == 2


def _grid(rows: list[list[str]], col_x: list[float]) -> Locator:
    """A table as a locator: each cell's characters laid out from its column's x."""

    text, rects = "", []
    for y, row in enumerate(rows):
        for x0, cell in zip(col_x, row):
            for i, ch in enumerate(cell + " "):
                text += ch
                rects.append(fitz.Rect(x0 + i * 6, y * 20, x0 + i * 6 + 6, y * 20 + 12))
        text += "\n"
        rects.append(None)
    return Locator(text, rects, ocr=True)


def test_a_short_value_under_an_id_column_header_is_placed():
    loc = _grid(
        [
            ["शेयरधनीको नाम", "ना.प्र.नं.र प्राप्त गरेको जिल्ला", "शेयर"],
            # A name long enough that nothing label-like sits within the inline-label
            # window before the value: only the column header can place it.
            ["कमल कमल कमल कमल कमल", "४५६७८ ललितपुर", "२०७९"],
        ],
        [0, 150, 400],
    )
    assert len(locate_value(loc, "४५६७८", approximate=False)) == 1
    placement = place_guided_values([("TABLE-CITIZENSHIP-NO", "४५६७८ ललितपुर")], [loc])
    assert placement.placed == 1 and not placement.unplaced


def test_a_short_run_outside_the_id_column_is_still_not_placed():
    loc = _grid(
        [
            ["शेयरधनीको नाम", "ना.प्र.नं.र प्राप्त गरेको जिल्ला", "शेयर"],
            ["कमल कमल कमल कमल कमल", "क ख", "४५६७८"],  # nothing label-like just before
        ],
        [0, 150, 400],
    )
    assert locate_value(loc, "४५६७८", approximate=False) == []
    # ...and above the header is not under it.
    above = _grid([["४५६७८"], ["ना.प्र.नं."]], [150])
    assert locate_value(above, "४५६७८", approximate=False) == []


# ------------------------------------------------------------ whole-document transcripts
def _pages(*docs: fitz.Document) -> fitz.Document:
    out = fitz.open()
    for d in docs:
        out.insert_pdf(d)
    return out


def test_an_unanchored_transcript_places_a_value_on_whatever_page_shows_it(tmp_path):
    # Production transcripts have no page anchors. The value is on page 3, under a
    # garbled label the rules miss: only the transcript can place it -- anywhere.
    doc = _pages(
        _vector_page_doc([PLAIN_LINE]),
        _vector_page_doc([PLAIN_LINE]),
        _vector_page_doc([f"सम्फ्क नं. {MOBILE}"]),
    )
    src = tmp_path / "three.pdf"
    doc.save(src)
    transcript = split_transcript_pages(f"{PLAIN_LINE}\n\n{MOBILE_LINE}\n")

    report = redact_pdf(
        src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, transcript=transcript
    )

    assert report.pages_by_path["vector"] == 3
    assert report.guided_placed == report.guided_values == 1
    assert report.unplaced == []
    with fitz.open(tmp_path / "out.pdf") as out:
        assert "९८५१" not in out[2].get_text()


def test_an_unanchored_transcript_still_needs_every_occurrence_in_the_document(
    tmp_path,
):
    # The transcript has the number twice; the PDF shows it once. One copy is unaccounted
    # for, so the document is held -- with no page to blame, and no digits in the journal.
    doc = _pages(_vector_page_doc([PLAIN_LINE]), _vector_page_doc([MOBILE_LINE]))
    src = tmp_path / "two.pdf"
    doc.save(src)
    transcript = f"{MOBILE_LINE}\n\n{MOBILE_LINE}\n"

    dry = redact_pdf(
        src, None, mode=RedactionMode.STRICT, dry_run=True, transcript=transcript
    )
    assert [(u["page"], u["shape"].strip("#")) for u in dry.unplaced] == [(None, "")]
    assert "९८५" not in repr(dry.as_dict())
    with pytest.raises(ValueError, match="could not be placed"):
        redact_pdf(
            src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, transcript=transcript
        )


def test_an_unanchored_value_the_first_read_misses_is_found_by_the_second(
    tmp_path, stub_ocr
):
    data1, loc1 = _scan_of([PLAIN_LINE])
    data2, loc2 = _scan_of([f"सम्फ्क नं. {MOBILE}"])
    src = tmp_path / "scan.pdf"
    _pages(fitz.open("pdf", data1), fitz.open("pdf", data2)).save(src)
    reads = Counter()

    def ocr(page, _calls):
        reads[page.number] += 1
        if page.number == 0:
            return _read_what_is_visible(page, loc1)
        if reads[1] == 1:  # page 2's first read garbles the number
            return Locator("", [], ocr=True)
        return _read_what_is_visible(page, loc2)

    stub_ocr(ocr)
    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        ocr=True,
        transcript=f"{PLAIN_LINE}\n{MOBILE_LINE}\n",
    )

    assert report.unplaced == [] and report.guided_placed == 1
    assert (
        _black_share(tmp_path / "out.pdf", _value_rect(loc2, MOBILE), page_no=1) > 0.9
    )
    assert _black_share(tmp_path / "out.pdf", _value_rect(loc1, PLAIN_LINE[:4])) < 0.1


def test_removed_values_joined_only_by_a_separator_are_split():
    # Two numbers in adjacent table cells (`|`) or under one label with a slash are
    # merged by the diff; the separator belongs to neither, so each is placed alone.
    for sep in (" | ", " / "):
        text = f"सम्पर्क नं. ९८५१२३४५६७{sep}९८६१२३४५६७ बुझेको"
        values = transcript_removed_values(text, RedactionMode.STRICT)
        assert [v for _, v in values] == ["९८५१२३४५६७", "९८६१२३४५६७"], sep
    # ...while one value's own separators still keep it whole.
    one = transcript_removed_values("मोबाइल नं. ९८५१-२३४५६७ बुझेको", RedactionMode.STRICT)
    assert [v for _, v in one] == ["९८५१-२३४५६७"]


def test_two_phones_merged_across_a_separator_keep_one_kind_each():
    # Each phone got its own placeholder in the text, so each is its own guided value,
    # whatever stands between them: a table cell border, a slash, a comma.
    for text in (
        "| फोन | ९८५१२३४५६७ | ९८६१२३४५६७ |",
        "सम्पर्क नं. ९८५१२३४५६७ | ९८६१२३४५६७ बुझेको",
        "सम्पर्क नं. ९८५१२३४५६७ / ९८६१२३४५६७ बुझेको",
        "सम्पर्क नं. ९८५१२३४५६७, ९८६१२३४५६७ बुझेको",
    ):
        values = transcript_removed_values(text, RedactionMode.STRICT)
        assert values == [
            ("PHONE-NO", "९८५१२३४५६७"),
            ("PHONE-NO", "९८६१२३४५६७"),
        ], text


def test_one_bank_account_with_a_slash_stays_one_guided_value():
    # The bank rule removes `१२३४५६ / ७८९०१२` as ONE span (one placeholder). Split at the
    # slash, its six-digit halves would earn exact credit on unrelated amounts elsewhere.
    for text in (
        "खाता नं. १२३४५६ / ७८९०१२",
        "बैंक खाता नं. १२३४५६ / ७८९०१२ रहेको",
        "खाता नं. १२३४५६ ७८९०१२ रहेको",
    ):
        assert (
            redact_text(text, mode=RedactionMode.STRICT).text.count("[REDACTED:") == 1
        )
        values = transcript_removed_values(text, RedactionMode.STRICT)
        assert len(values) == 1, text
        kind, value = values[0]
        assert kind == "BANK-ACCOUNT-NO"
        assert _digits_of(value) == "१२३४५६७८९०१२", text


def _digits_of(text: str) -> str:
    return "".join(c for c in text if c in "०१२३४५६७८९")


def test_a_fragment_of_one_value_never_earns_credit_on_unrelated_amounts(
    tmp_path, stub_ocr
):
    # OCR misses the account; the page prints its two halves as unrelated amounts. The
    # account must stay unplaced (the document refuses), not be credited by the amounts.
    data, loc = _scan_of(["कमल कमल रकम १२३४५६ र ७८९०१२ बुझेको"])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    transcript = f"{PLAIN_LINE}\nखाता नं. १२३४५६ / ७८९०१२\n"

    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )
    assert dry.guided_placed == 0
    assert [(u["kind"], u["page"]) for u in dry.unplaced] == [("BANK-ACCOUNT-NO", None)]
    with pytest.raises(ValueError, match="could not be placed"):
        redact_pdf(
            src,
            tmp_path / "out.pdf",
            mode=RedactionMode.STRICT,
            ocr=True,
            transcript=transcript,
        )


def test_an_unanchored_value_only_the_overlay_found_is_credited_by_the_second_read(
    tmp_path, stub_ocr
):
    # A scan whose Unicode overlay is right: the overlay boxes the number (burned, but
    # uncredited), the first OCR read misses it. The second read must look at the
    # ORIGINAL page -- the burned one no longer shows the number to credit.
    data, loc = _scan_of([f"सम्फ्क नं. {MOBILE}"])
    scan = fitz.open("pdf", data)
    page = scan[0]
    page.insert_font(fontname="dev", fontfile=_font())
    page.insert_text(
        (72, 90), f"सम्फ्क नं. {MOBILE}", fontname="dev", fontsize=14, render_mode=3
    )
    src = tmp_path / "overlaid.pdf"
    scan.save(src)
    reads = Counter()

    def ocr(page, _calls):
        reads[page.number] += 1
        if reads[page.number] == 1:  # the first read garbles it
            return Locator("", [], ocr=True)
        return _read_what_is_visible(page, loc)

    stub_ocr(ocr)
    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        ocr=True,
        transcript=f"{MOBILE_LINE}\n",
    )

    assert report.pages_by_path["image"] == 1
    assert report.unplaced == [] and report.guided_placed == 1
    assert _black_share(tmp_path / "out.pdf", _value_rect(loc, MOBILE)) > 0.9


def test_the_second_read_honours_approximate_boxes_off():
    # Neither read shows the value exactly; the second read has a one-digit slip.
    first = _loc("मो. कुनै अंक छैन")
    slipped = _loc("मो. ९८५१२३४५६१")
    off = place_guided_values(
        [("MOBILE", MOBILE)], [first], lambda: slipped, approximate_boxes=False
    )
    assert off.boxes == [] and off.placed == 0 and len(off.unplaced) == 1
    on = place_guided_values([("MOBILE", MOBILE)], [first], lambda: slipped)
    assert on.boxes and on.placed == 0


# ------------------------------------------- composite values and values wrapped over lines
# Synthetic values only: obviously fake numbers and addresses.
PHONE_A, PHONE_B = "9800000001", "9800000002"
ACCOUNT_A, ACCOUNT_B = "100000000001", "20000000002"
EMAIL = "sita.sharma@nepal-example.org"


def test_composite_parts_split_lists_of_whole_values_only():
    from likhit.privacy.pdf_ocr import composite_parts

    for joined in (
        f"{PHONE_A}/{PHONE_B}",
        f"{PHONE_A} / {PHONE_B}",
        f"{PHONE_A}, {PHONE_B}",
        f"{PHONE_A}; {PHONE_B}",
        f"{PHONE_A} {PHONE_B}",
        f"{PHONE_A}।{PHONE_B}",
        f"{PHONE_A} वा {PHONE_B}",
        f"{PHONE_A} र {PHONE_B}",
        f"{PHONE_A}-{PHONE_B}",
    ):
        assert composite_parts("PHONE-NO", joined) == [PHONE_A, PHONE_B], joined
    for joined in (f"{ACCOUNT_A} र {ACCOUNT_B}", f"{ACCOUNT_A}, {ACCOUNT_B}"):
        assert composite_parts("BANK-ACCOUNT-NO", joined) == [ACCOUNT_A, ACCOUNT_B]
    assert composite_parts("EMAIL", f"{EMAIL}, ram@example.org") == [
        EMAIL,
        "ram@example.org",
    ]
    # One value, never cut: its own separators, halves too short to be whole values,
    # a local landline, and kinds whose slashes and spaces belong to the value.
    for kind, one in (
        ("PHONE-NO", PHONE_A),
        ("PHONE-NO", "९८५१-२३४५६७"),
        ("PHONE-NO", "01 4123456"),
        ("PHONE-NO", f"{PHONE_A}/02"),
        ("BANK-ACCOUNT-NO", "१२३४५६ / ७८९०१२"),
        ("BANK-ACCOUNT-NO", "0123 4567 8901"),
        ("BANK-ACCOUNT-NO", f"{ACCOUNT_A} 2 {ACCOUNT_B}"),
        # A slash, space or hyphen may sit inside ONE account: never split there,
        # however long the halves are.
        ("BANK-ACCOUNT-NO", "123456789 / 987654321"),
        ("BANK-ACCOUNT-NO", "123456789 987654321"),
        ("BANK-ACCOUNT-NO", "123456789-987654321"),
        ("CITIZENSHIP-NO", "१२३४५/६७८९०"),
        ("EMAIL", EMAIL),
    ):
        assert composite_parts(kind, one) is None, one


def test_two_phones_joined_in_the_transcript_are_placed_where_the_page_separates_them():
    # The transcript wrote `A/B` (one diff value: the slash merged the two placeholders);
    # the page prints each number on its own line, where the joined string never matches.
    text = f"सम्पर्क नं. {PHONE_A}/{PHONE_B} बुझेको"
    values = transcript_removed_values(text, RedactionMode.STRICT)
    assert values == [("PHONE-NO", f"{PHONE_A}/{PHONE_B}")]
    loc = _loc(f"मो. {PHONE_A} कमल कमल कमल\nफोन {PHONE_B}")
    placement = place_guided_values(values, [loc])
    assert placement.placed == 1 and placement.unplaced == []
    # Both parts are boxed: every one of their characters lies under a box.
    for part in (PHONE_A, PHONE_B):
        assert any(b.contains(_value_rect(loc, part)) for b in placement.boxes), part
    # Written joined on the page, the whole value places it as before.
    joined = place_guided_values(values, [_loc(f"मो. {PHONE_A}/{PHONE_B}")])
    assert joined.placed == 1 and joined.unplaced == []


def test_a_composite_with_one_part_missing_keeps_the_document_held(tmp_path):
    values = [("PHONE-NO", f"{PHONE_A}/{PHONE_B}")]
    # The second number is garbled on the page (O for 0): one part placed is not enough.
    loc = _loc(f"मो. {PHONE_A} कमल कमल कमल\nफोन 98000000O2")
    placement = place_guided_values(values, [loc])
    assert placement.placed == 0
    assert [kind for kind, _ in placement.unplaced] == ["PHONE-NO"]
    assert any(b.contains(_value_rect(loc, PHONE_A)) for b in placement.boxes)
    # End to end, unanchored transcript: the document refuses, the journal is masked.
    doc = _vector_page_doc([f"सम्फ्क नं. {PHONE_A}", "फोन 98000000O2"])
    src = tmp_path / "held.pdf"
    doc.save(src)
    transcript = f"सम्पर्क नं. {PHONE_A}/{PHONE_B} बुझेको\n"
    dry = redact_pdf(
        src, None, mode=RedactionMode.STRICT, dry_run=True, transcript=transcript
    )
    assert [(u["page"], u["kind"]) for u in dry.unplaced] == [(None, "PHONE-NO")]
    assert "98000" not in repr(dry.as_dict())
    with pytest.raises(ValueError, match="could not be placed"):
        redact_pdf(
            src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, transcript=transcript
        )


def test_one_printed_part_is_spent_once_across_values():
    # The transcript has A standalone AND inside `A/B`; the page prints A once and B once.
    # The single A cannot stand for both required occurrences.
    values = [("PHONE-NO", PHONE_A), ("PHONE-NO", f"{PHONE_A}/{PHONE_B}")]
    loc = _loc(f"मो. {PHONE_A} कमल कमल कमल\nफोन {PHONE_B}")
    placement = place_guided_values(values, [loc])
    assert placement.placed == 1 and len(placement.unplaced) == 1
    # A composite written whole does not credit its parts a second time either.
    whole = _loc(f"मो. {PHONE_A}/{PHONE_B}")
    twice = place_guided_values([("PHONE-NO", f"{PHONE_A}/{PHONE_B}")] * 2, [whole])
    assert twice.placed == 1 and len(twice.unplaced) == 1


def test_a_two_part_bank_account_value_is_placed_part_by_part(tmp_path):
    text = f"बैंक खाता नं. {ACCOUNT_A} र {ACCOUNT_B} रहेको"
    values = transcript_removed_values(text, RedactionMode.STRICT)
    assert values == [("BANK-ACCOUNT-NO", f"{ACCOUNT_A} र {ACCOUNT_B}")]
    # The page is a table: each account in its own row, under neutral words.
    doc = _vector_page_doc(
        [f"कमल कमल पहिलो {ACCOUNT_A}", "कमल कमल कमल", f"कमल कमल दोस्रो {ACCOUNT_B}"]
    )
    src = tmp_path / "accounts.pdf"
    doc.save(src)
    report = redact_pdf(
        src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, transcript=text
    )
    assert report.unplaced == [] and report.guided_placed == report.guided_values == 1
    with fitz.open(tmp_path / "out.pdf") as out:
        page_text = out[0].get_text()
    assert ACCOUNT_A not in page_text and ACCOUNT_B not in page_text
    assert "पहिलो" in page_text and "दोस्रो" in page_text


def test_a_composite_on_a_scan_is_burned_part_by_part(tmp_path, stub_ocr):
    data, loc = _scan_of([f"सम्फ्क नं. {PHONE_A} कमल", f"कमल कमल अर्को {PHONE_B}"])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        ocr=True,
        transcript=f"सम्पर्क नं. {PHONE_A}/{PHONE_B} बुझेको\n",
    )
    assert report.unplaced == [] and report.guided_placed == 1
    for part in (PHONE_A, PHONE_B):
        assert _black_share(tmp_path / "out.pdf", _value_rect(loc, part)) > 0.9


def test_the_post_check_still_refuses_when_a_placed_part_stays_readable(
    tmp_path, stub_ocr, monkeypatch
):
    # Both parts are placed (credited), but the burn of the second one is lost: the
    # residue check on the written file must still read it and refuse the document.
    data, loc = _scan_of([f"सम्फ्क नं. {PHONE_A} कमल", f"कमल कमल फोन {PHONE_B}"])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    stub_ocr(loc)
    keep = _value_rect(loc, PHONE_B)
    real_burn = pdf_module._burn

    def lossy_burn(page, boxes):
        real_burn(page, [b for b in boxes if (b & keep).is_empty])

    monkeypatch.setattr(pdf_module, "_burn", lossy_burn)
    transcript = f"सम्पर्क नं. {PHONE_A}/{PHONE_B} बुझेको\n"
    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )
    assert dry.unplaced == []  # placement is satisfied...
    with pytest.raises(
        ValueError, match="verification failed"
    ):  # ...the post-check is not
        redact_pdf(
            src,
            tmp_path / "out.pdf",
            mode=RedactionMode.STRICT,
            ocr=True,
            transcript=transcript,
        )
    assert not (tmp_path / "out.pdf").exists()


def test_a_number_wrapped_at_a_line_end_is_matched_whole():
    for page in (
        f"मो. {PHONE_A[:5]}\n{PHONE_A[5:]} मा",
        f"मो. {PHONE_A[:5]}\n{PHONE_A[5:]}, 9800000002 मा",  # a list's `, ` ends it
        f"मो. {PHONE_A[:5]} \n  {PHONE_A[5:]} मा",
        f"मो. {PHONE_A[:5]}-\n{PHONE_A[5:]} मा",
        f"मो. {PHONE_A[:5]}\n-{PHONE_A[5:]} मा",
    ):
        assert len(locate_value(_loc(page), PHONE_A, approximate=False)) == 1, page
    # Still not: a word between, a blank line between, two bare wraps in one value,
    # a short value, or a slice of a longer number on the next line.
    for page, value in (
        (f"मो. {PHONE_A[:5]}\nघर {PHONE_A[5:]}", PHONE_A),
        (f"मो. {PHONE_A[:5]}\n\n{PHONE_A[5:]}", PHONE_A),
        (f"मो. {PHONE_A[:4]}\n{PHONE_A[4:7]}\n{PHONE_A[7:]}", PHONE_A),
        ("ना.प्र.नं. १२३\n४५ मा", "१२३४५"),
        (f"मो. {PHONE_A[:5]}\n{PHONE_A[5:]}7 मा", PHONE_A),
        # ...or a wrapped tail of a longer number glued on by a separator.
        (f"मो. 9-\n{PHONE_A[:5]}\n{PHONE_A[5:]} मा", PHONE_A),
        (f"मो. 9-{PHONE_A[:5]}\n{PHONE_A[5:]} मा", PHONE_A),
        (f"मो. {PHONE_A[:5]}\n{PHONE_A[5:]}-\n7 मा", PHONE_A),
    ):
        assert locate_value(_loc(page), value, approximate=False) == [], page


def test_an_email_wrapped_at_its_hyphen_is_matched_whole():
    local, domain = EMAIL.split("@")
    head, tail = domain.split("-")
    for page in (
        f"इमेल {local}@{head}-\n{tail} मा",  # broken after the hyphen
        f"इमेल {local}@{head}\n-{tail} मा",  # ...or before it
        f"इमेल {local[:4]}-\n{local[4:]}@{domain} मा",  # a hyphen inserted at the break
    ):
        assert len(locate_value(_loc(page), EMAIL, approximate=False)) == 1, page
    # The address's own hyphen dropped at the break is a different address; a longer
    # address continuing on the next line is not this one.
    assert locate_value(_loc(f"{local}@{head}\n{tail}"), EMAIL, approximate=False) == []
    for page in (
        f"{local}@{head}-\n{tail}.np",
        f"{local}@{head}-\n{tail}\n.np",  # the longer address wraps again
        f"{EMAIL}\n.np",
        f"{EMAIL} \n  -np मा",
        f"jo-\n{EMAIL}",  # ...or started on the line before
        f"jo_  \n   {local}@{head}-\n{tail}",
    ):
        assert locate_value(_loc(page), EMAIL, approximate=False) == [], page
    # A sentence's or a label's period at the end of the line before is not one.
    for page in (
        f"Email address.\n{EMAIL}",
        f"Email address.  \n  {local}@{head}-\n{tail}",
    ):
        assert len(locate_value(_loc(page), EMAIL, approximate=False)) == 1, page
    # A line break followed by ordinary text still ends the address.
    assert len(locate_value(_loc(f"{EMAIL}\nमा पठाइयो"), EMAIL, approximate=False)) == 1
    assert len(locate_value(_loc(f"इमेल:\n{EMAIL}"), EMAIL, approximate=False)) == 1


def test_a_wrapped_email_on_a_unicode_page_is_placed_and_deleted(tmp_path):
    local, domain = EMAIL.split("@")
    head, tail = domain.split("-")
    filler = "रामबहादुर थापाले निवेदन दिएको व्यहोरा सदर कायम गरिएको छ"
    doc = _vector_page_doc([filler, "", "", filler])
    # The address in a Latin font, wrapped after its hyphen (lines 2 and 3).
    doc[0].insert_text((72, 118), f"{local}@{head}-", fontname="helv", fontsize=14)
    doc[0].insert_text((72, 146), tail, fontname="helv", fontsize=14)
    src = tmp_path / "email.pdf"
    doc.save(src)
    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        transcript=f"इमेल {EMAIL} मा\n",
    )
    assert report.unplaced == [] and report.guided_placed == 1
    with fitz.open(tmp_path / "out.pdf") as out:
        page_text = out[0].get_text()
    assert local not in page_text and head not in page_text and tail not in page_text


def test_composite_parts_on_different_pages_place_an_unanchored_value(tmp_path):
    doc = _pages(
        _vector_page_doc([f"कमल कमल पहिलो {PHONE_A}"]),
        _vector_page_doc([PLAIN_LINE]),
        _vector_page_doc([f"कमल कमल दोस्रो {PHONE_B}"]),
    )
    src = tmp_path / "three.pdf"
    doc.save(src)
    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        transcript=f"सम्पर्क नं. {PHONE_A}/{PHONE_B} बुझेको\n",
    )
    assert report.unplaced == [] and report.guided_placed == 1


def test_a_composite_part_the_first_read_misses_is_found_by_the_second(
    tmp_path, stub_ocr
):
    data1, loc1 = _scan_of([f"कमल कमल पहिलो {PHONE_A}"])
    data2, loc2 = _scan_of([f"कमल कमल दोस्रो {PHONE_B}"])
    src = tmp_path / "scan.pdf"
    _pages(fitz.open("pdf", data1), fitz.open("pdf", data2)).save(src)
    reads = Counter()

    def ocr(page, _calls):
        reads[page.number] += 1
        if page.number == 0:
            return _read_what_is_visible(page, loc1)
        if reads[1] == 1:  # page 2's first read misses the second number
            return Locator("", [], ocr=True)
        return _read_what_is_visible(page, loc2)

    stub_ocr(ocr)
    report = redact_pdf(
        src,
        tmp_path / "out.pdf",
        mode=RedactionMode.STRICT,
        ocr=True,
        transcript=f"सम्पर्क नं. {PHONE_A}/{PHONE_B} बुझेको\n",
    )
    assert report.unplaced == [] and report.guided_placed == 1
    assert (
        _black_share(tmp_path / "out.pdf", _value_rect(loc2, PHONE_B), page_no=1) > 0.9
    )


def test_one_print_read_as_parts_then_whole_places_one_occurrence_not_two():
    # The transcript needs `A/B` twice; the page prints one legible pair and one garbled.
    # The first read sees the legible pair as `A … B` (parts), the second as `A/B`
    # (whole). That is still one print: one occurrence placed, the other held.
    values = [("PHONE-NO", f"{PHONE_A}/{PHONE_B}")] * 2
    garbled = "मो. 98000000O1/98000000O2"
    first = _loc(f"मो. {PHONE_A} कमल {PHONE_B}\n{garbled}")
    second = _loc(f"मो. {PHONE_A}/{PHONE_B}\n{garbled}")
    placement = place_guided_values(values, [first], lambda: second)
    assert placement.placed == 1 and len(placement.unplaced) == 1
    # ...and the same pair read whole first, as parts second.
    placement = place_guided_values(values, [second], lambda: first)
    assert placement.placed == 1 and len(placement.unplaced) == 1


def test_one_print_read_as_parts_then_whole_is_one_occurrence_document_wide(
    tmp_path, stub_ocr
):
    data, _ = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    garbled = "मो. 98000000O1/98000000O2"
    reads = Counter()

    def ocr(page, _calls):
        reads[page.number] += 1
        if reads[page.number] == 1:
            return _loc(f"मो. {PHONE_A} कमल {PHONE_B}\n{garbled}")
        return _loc(f"मो. {PHONE_A}/{PHONE_B}\n{garbled}")

    stub_ocr(ocr)
    transcript = f"सम्पर्क नं. {PHONE_A}/{PHONE_B}\n\nसम्पर्क नं. {PHONE_A}/{PHONE_B}\n"
    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )
    assert dry.guided_values == 2 and dry.guided_placed == 1
    assert [(u["page"], u["kind"]) for u in dry.unplaced] == [(None, "PHONE-NO")]


def test_a_slash_joined_account_is_never_placed_from_its_halves():
    # One account written `123456789 / 987654321` (one placeholder): each nine-digit
    # half printed elsewhere on the page must not place it.
    text = "खाता नं. 123456789 / 987654321 रहेको"
    values = transcript_removed_values(text, RedactionMode.STRICT)
    assert [kind for kind, _ in values] == ["BANK-ACCOUNT-NO"]
    loc = _loc("कमल कमल रकम 123456789 कमल कमल कमल\nरकम 987654321 बुझेको")
    placement = place_guided_values(values, [loc])
    assert placement.placed == 0 and len(placement.unplaced) == 1


def test_a_standalone_value_inside_the_one_printed_composite_is_not_placed_again():
    # The transcript needs `A` on its own and `A/B`; the page prints `A/B` once. That
    # one print places the composite; the standalone `A` has no print of its own.
    values = [("PHONE-NO", PHONE_A), ("PHONE-NO", f"{PHONE_A}/{PHONE_B}")]
    whole = _loc(f"मो. {PHONE_A}/{PHONE_B} कमल कमल")
    placement = place_guided_values(values, [whole])
    assert placement.placed == 1 and len(placement.unplaced) == 1
    # A second, separate print of `A` places it.
    both = _loc(f"मो. {PHONE_A}/{PHONE_B} कमल कमल\nफोन {PHONE_A}")
    placement = place_guided_values(values, [both])
    assert placement.placed == 2 and placement.unplaced == []
    # One read sees the print whole, the other as `A … B`: still one print.
    parts = _loc(f"मो. {PHONE_A} कमल {PHONE_B} कमल कमल")
    for first, second in ((whole, parts), (parts, whole)):
        placement = place_guided_values(values, [first], lambda s=second: s)
        assert placement.placed == 1 and len(placement.unplaced) == 1


def test_two_composites_sharing_a_part_are_not_both_placed_by_one_print():
    # The transcript needs `A/B` and `B/C`; the page prints `A/B/C` once. Its one `B`
    # places at most one of them, on one read or across two.
    phone_c = "9800000003"
    values = [
        ("PHONE-NO", f"{PHONE_A}/{PHONE_B}"),
        ("PHONE-NO", f"{PHONE_B}/{phone_c}"),
    ]
    joined = _loc(f"मो. {PHONE_A}/{PHONE_B}/{phone_c} कमल")
    placement = place_guided_values(values, [joined])
    assert placement.placed == 1 and len(placement.unplaced) == 1
    split = _loc(f"मो. {PHONE_A} कमल {PHONE_B}/{phone_c} कमल")
    for first, second in ((joined, split), (split, joined)):
        placement = place_guided_values(values, [first], lambda s=second: s)
        assert placement.placed <= 1 and placement.unplaced
    # Printed apart, each is placed.
    apart = _loc(f"मो. {PHONE_A}/{PHONE_B} कमल कमल\nफोन {PHONE_B}/{phone_c}")
    placement = place_guided_values(values, [apart])
    assert placement.placed == 2 and placement.unplaced == []


def test_a_standalone_part_keeps_its_own_print_when_the_second_read_finds_the_composite():
    # The transcript needs `A` and `A/B`; the page prints `A` on its own and `A/B`. The
    # first read sees only the lone `A`, the second both. Both are placed: the second
    # read's own count of the lone `A` survives the debit for the composite it adds.
    values = [("PHONE-NO", PHONE_A), ("PHONE-NO", f"{PHONE_A}/{PHONE_B}")]
    first = _loc(f"फोन {PHONE_A} कमल\nमो. कुनै अंक छैन")
    second = _loc(f"फोन {PHONE_A} कमल\nमो. {PHONE_A}/{PHONE_B}")
    placement = place_guided_values(values, [first], lambda: second)
    assert placement.placed == 2 and placement.unplaced == []


def test_a_standalone_part_keeps_its_print_document_wide(tmp_path, stub_ocr):
    data, _ = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    reads = Counter()

    def ocr(page, _calls):
        reads[page.number] += 1
        if reads[page.number] == 1:
            return _loc(f"फोन {PHONE_A} कमल\nमो. कुनै अंक छैन")
        return _loc(f"फोन {PHONE_A} कमल\nमो. {PHONE_A}/{PHONE_B}")

    stub_ocr(ocr)
    transcript = f"फोन {PHONE_A}\n\nसम्पर्क नं. {PHONE_A}/{PHONE_B}\n"
    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )
    assert dry.guided_values == 2 and dry.guided_placed == 2 and dry.unplaced == []


def test_a_part_inside_another_values_credited_print_does_not_place_a_composite():
    # An account written `123456789 / 9800000001` (one value, placed whole) and the
    # list `9800000001/9800000002`; the page prints the account and a lone
    # `9800000002`. The account's phone-shaped group is the account's, not a print of
    # the phone: the list stays held.
    account = f"123456789 / {PHONE_A}"
    values = [("BANK-ACCOUNT-NO", account), ("PHONE-NO", f"{PHONE_A}/{PHONE_B}")]
    loc = _loc(f"खाता नं. {account} कमल कमल\nफोन {PHONE_B}")
    placement = place_guided_values(values, [loc])
    assert placement.placed == 1
    assert [kind for kind, _ in placement.unplaced] == ["PHONE-NO"]
    # With a print of its own, the phone is placed too.
    loc = _loc(f"खाता नं. {account} कमल कमल\nफोन {PHONE_B} र {PHONE_A}")
    assert place_guided_values(values, [loc]).placed == 2

    # ...and across reads: one read credits the account, another (which garbles the
    # account's first group) sees only the phone-shaped group. Still one print.
    garbled = _loc(f"खाता नं. 12345678O / {PHONE_A} कमल कमल\nफोन {PHONE_B}")
    whole = _loc(f"खाता नं. {account} कमल कमल\nफोन {PHONE_B}")
    for first, second in ((whole, garbled), (garbled, whole)):
        placement = place_guided_values(values, [first], lambda s=second: s)
        assert [kind for kind, _ in placement.unplaced] == ["PHONE-NO"]


def test_a_repeated_part_is_debited_once_per_copy_across_reads():
    # The transcript needs `A` on its own and the list `A/A`; the page prints only the
    # pair (two copies of `A`). One read sees `A … A`, the other the pair whole: two
    # prints place one of the two values, never both (that would need three).
    values = [("PHONE-NO", PHONE_A), ("PHONE-NO", f"{PHONE_A}/{PHONE_A}")]
    parts = _loc(f"मो. {PHONE_A} कमल {PHONE_A} कमल")
    whole = _loc(f"मो. {PHONE_A}/{PHONE_A} कमल कमल")
    for first, second in ((parts, whole), (whole, parts)):
        placement = place_guided_values(values, [first], lambda s=second: s)
        assert placement.placed <= 1 and placement.unplaced
    # Three separate prints place both.
    three = _loc(f"मो. {PHONE_A}/{PHONE_A} कमल कमल\nफोन {PHONE_A}")
    assert place_guided_values(values, [three]).placed == 2


def test_an_account_overlapping_a_composite_print_is_not_placed_by_it_across_reads():
    # The transcript needs the account `123456789 / A` and the list `A/B`; the page
    # prints only `123456789 / A/B`. One read credits the list (and so not the
    # account); another garbles `B` and credits the account. One printed `A`: one value.
    account = f"123456789 / {PHONE_A}"
    values = [("BANK-ACCOUNT-NO", account), ("PHONE-NO", f"{PHONE_A}/{PHONE_B}")]
    clear = _loc(f"खाता नं. {account}/{PHONE_B} कमल")
    garbled = _loc(f"खाता नं. {account}/98000000O2 कमल")
    assert place_guided_values(values, [clear]).placed == 1
    for first, second in ((clear, garbled), (garbled, clear)):
        placement = place_guided_values(values, [first], lambda s=second: s)
        assert placement.placed <= 1 and placement.unplaced


def test_one_printed_run_never_supplies_two_parts_of_a_list():
    # The list `123456789 9800000001 र 9800000001` (two accounts); the page prints only
    # the first. Its trailing group is that account's, not a print of the second.
    account = f"123456789 {PHONE_A}"
    values = [("BANK-ACCOUNT-NO", f"{account} र {PHONE_A}")]
    from likhit.privacy.pdf_ocr import composite_parts

    assert composite_parts(*values[0]) == [account, PHONE_A]
    alone = _loc(f"खाता नं. {account} कमल कमल")
    assert place_guided_values(values, [alone]).placed == 0
    # ...on one read or across two (the second garbles the first group).
    garbled = _loc(f"खाता नं. 12345678O {PHONE_A} कमल कमल")
    for first, second in ((alone, garbled), (garbled, alone)):
        assert place_guided_values(values, [first], lambda s=second: s).placed == 0
    # A separate print of the second account places the list.
    both = _loc(f"खाता नं. {account} कमल कमल\nखाता {PHONE_A}")
    assert place_guided_values(values, [both]).placed == 1


def test_document_retries_combine_on_uncapped_whole_counts(tmp_path, stub_ocr):
    # The transcript needs `A/B` once and `A` once. The first read finds two whole
    # `A/B` prints (and no lone `A`); the retry sees one pair whole and the other as
    # `A … B`. The `A` inside the second pair is that pair's: `A` stays held.
    data, _ = _scan_of([PLAIN_LINE])
    src = tmp_path / "scan.pdf"
    src.write_bytes(data)
    reads = Counter()

    def ocr(page, _calls):
        reads[page.number] += 1
        if reads[page.number] == 1:
            return _loc(f"मो. {PHONE_A}/{PHONE_B} कमल\nफोन {PHONE_A}/{PHONE_B}")
        return _loc(f"मो. {PHONE_A}/{PHONE_B} कमल\nफोन {PHONE_A} कमल {PHONE_B}")

    stub_ocr(ocr)
    transcript = f"फोन {PHONE_A}\n\nसम्पर्क नं. {PHONE_A}/{PHONE_B}\n"
    dry = redact_pdf(
        src,
        None,
        mode=RedactionMode.STRICT,
        dry_run=True,
        ocr=True,
        transcript=transcript,
    )
    assert dry.guided_values == 2 and dry.guided_placed == 1
    assert [u["kind"] for u in dry.unplaced] == ["PHONE-NO"]
