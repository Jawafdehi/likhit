"""Scanned and legacy-font pages: ``redact_pdf(..., ocr=True)`` and :mod:`likhit.privacy.pdf_ocr`.

Most tests replace Tesseract with a stub locator built from a vector page's own glyph map,
so the geometry is exact and the suite needs no OCR binary. The stub is the contract the
real reader must meet (page-space rectangles, one per character); the last tests run the
real ``tesseract`` and skip where it, or its ``nep`` data, is absent.
"""

from __future__ import annotations

import os

import fitz
import pytest

from likhit.privacy import RedactionMode, redact_pdf
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
    assert split_transcript_pages("no anchors") == {1: "no anchors"}


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
    # ...not when digits merely end one line and start the next,
    assert (
        locate_value(_loc("मो. 98512\n34567 घर"), "9851234567", approximate=False) == []
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
