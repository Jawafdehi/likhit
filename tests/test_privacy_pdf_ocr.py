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

    redact_pdf(src, tmp_path / "out.pdf", mode=RedactionMode.STRICT, ocr=True)

    with fitz.open(tmp_path / "out.pdf") as doc:
        assert doc[0].get_text().strip() == ""


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
