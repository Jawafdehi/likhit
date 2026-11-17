"""Kalimati's ``े`` + repha ligature keeps its repha (JAW-53).

Kalimati shapes ``र्ने`` by ligating the e-matra with the repha into one glyph:
its GSUB carries ``uni0947 glyph00091 -> glyph00566``, the sibling of 567 (``ैर्``)
and 568 (``ीर्``). Law Commission PDFs (Word 2013 output) draw ``गर्ने`` as three
glyphs -- ``ग`` (CID 0x82), ``न`` (0x93), glyph00566 (0x236) -- and author 0x236 as a
bare ``े`` in /ToUnicode. The repair agreed with them: ``_infer_mark_variants``
matched glyph00566's metrics to ``े``, and that guess overwrote GSUB's ``ेर्``. So
``गर्ने`` came out ``गने`` in 479 of the 739 Law Commission PDFs.

Every sequence below is one that Law Commission document 11040
(औषधि (तेस्रो संशोधन) अध्यादेश, २०७७) actually draws.
"""

from __future__ import annotations

from io import BytesIO

import fitz
import pytest

from likhit.extractors import kalimati, kalimati_reference

from tests.test_kalimati_reference import _build_font, _pdf_embedding

_E_REPH = "\u0947\u0930\u094d"  # ेर्


class _FakeDoc:
    def __init__(self, mapping: dict[int, str]) -> None:
        self._stream = kalimati._build_cmap_stream(mapping)
        self.written: bytes | None = None

    def xref_stream(self, _xref: int) -> bytes:
        return self._stream

    def update_stream(self, _xref: int, data: bytes) -> None:
        self.written = data


def _patched(pdf_map: dict[int, str], correction: dict[int, str]) -> dict[int, str]:
    doc = _FakeDoc(pdf_map)
    kalimati._patch_single_cmap(  # type: ignore[arg-type]
        doc, 1, correction, font_name="Kalimati", allow_gid_exceptions=True
    )
    assert doc.written is not None
    return kalimati._parse_tounicode_cmap(doc.written)


def test_the_e_reph_ligature_is_recorded_as_e_plus_repha() -> None:
    """The reference table entry for glyph00566's outline (digest from document 11040)."""

    assert kalimati_reference.OUTLINE_TO_UNICODE["3fd07f4e711020cd"] == _E_REPH


@pytest.mark.parametrize(
    ("glyphs", "expected"),
    [
        # ग न glyph00566 छ -- "गर्नेछ" as 11040 draws it
        (("ग", "न", 0x236, "छ"), "गर्नेछ"),
        # प न glyph00566 -- "पर्ने"
        (("प", "न", 0x236), "पर्ने"),
        # a bare e-matra glyph next to it must stay bare: द uni0947 -- "दे"
        (("द", 0x22F), "दे"),
    ],
)
def test_garne_keeps_its_repha(glyphs: tuple, expected: str) -> None:
    # 0x236 authored as bare े (as in 11040); its GSUB reading is ेर्. 0x22F is the
    # plain uni0947, authored and read as े.
    patched = _patched(
        {0x236: "\u0947", 0x22F: "\u0947"}, {0x236: _E_REPH, 0x22F: "\u0947"}
    )
    text = "".join(patched[g] if isinstance(g, int) else g for g in glyphs)
    assert kalimati.reorder_devanagari(text) == expected


def test_an_authored_consonant_on_the_ligature_still_yields_the_repha() -> None:
    """Law Commission 12431 authors glyph00566 as ``न``; written raw, ``ेर्`` gave ``गनेर्``."""

    patched = _patched({0x236: "न"}, {0x236: _E_REPH})
    assert kalimati.reorder_devanagari(f"गन{patched[0x236]}") == "गर्ने"


def test_an_authored_ne_keeps_its_contextual_provenance() -> None:
    """The other producer, which authors glyph00566 as ``ने``, is unchanged."""

    patched = _patched({566: "ने"}, {566: _E_REPH})
    assert patched[566] == kalimati._PUA_CONTEXTUAL_NE
    assert kalimati.reorder_devanagari(f"गर्{patched[566]}") == "गर्ने"


def test_gsub_outranks_an_inferred_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ligature rule is an exact reading; a metric match is a guess about a shape."""

    font = _build_font(character_map={0x0915: "g3"})
    compiled = BytesIO()
    font.save(compiled)
    monkeypatch.setattr(
        kalimati,
        "_infer_mark_variants",
        lambda font, glyph_order, gid_to_correct: {5: "\u0947"},
    )
    monkeypatch.setattr(
        kalimati,
        "_analyze_gsub",
        lambda font, glyph_order, gid_to_correct: {5: _E_REPH},
    )

    doc = fitz.open(stream=_pdf_embedding(compiled.getvalue()), filetype="pdf")
    try:
        merged: dict[int, str] = {}
        guessed: set[int] = set()
        for info in doc[0].get_fonts(full=True):
            if info[2] == "Type0":
                reconstruction = kalimati._get_font_correction_map(doc, info[0])
                merged.update(reconstruction)
                guessed |= set(kalimati._reconstruction_guesses(reconstruction))
    finally:
        doc.close()

    assert merged.get(5) == _E_REPH
    assert 5 not in guessed, "a GSUB-derived value is not a guess"


@pytest.mark.parametrize(
    ("value", "marked"),
    [
        ("\u0947\u0930\u094d", "\u0947" + kalimati._PUA_REPH),  # glyph00566 ेर्
        ("\u0947\u0930\u094d\u0901", "\u0947" + kalimati._PUA_REPH + "\u0901"),  # 590
        ("\u0947\u0930\u094d\u0902", "\u0947" + kalimati._PUA_REPH + "\u0902"),  # 591
        ("\u094b\u0930\u094d", "\u094b" + kalimati._PUA_REPH),  # 574 ोर्
        ("\u093f\u0930\u094d", None),  # pre-base i-matra has its own marker
        ("\u0930\u094d", None),  # a bare repha is handled elsewhere
        ("\u0915\u0930\u094d", None),  # consonant + ra + virama is not a matra ligature
    ],
)
def test_matra_repha_marking(value: str, marked: str | None) -> None:
    assert kalimati._matra_repha_marked(value) == marked


def test_garnen_with_candrabindu_keeps_its_repha() -> None:
    # ग न glyph00590 -- the e-matra + repha + candrabindu ligature (court order
    # janakpurhc.080-cs-0952 draws it), GSUB glyph00566 + uni0901 -> glyph00590.
    patched = _patched({0x24E: "\u0947"}, {0x24E: "\u0947\u0930\u094d\u0901"})
    assert kalimati.reorder_devanagari(f"गन{patched[0x24E]}") == "गर्नेँ"


def test_a_ligature_built_on_a_gsub_glyph_reads_gsub_not_the_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """glyph00590 = glyph00566 + ँ must carry 566's GSUB value, not its metric guess."""

    font = _build_font(character_map={0x0915: "g3"})
    compiled = BytesIO()
    font.save(compiled)
    monkeypatch.setattr(
        kalimati,
        "_infer_mark_variants",
        lambda font, glyph_order, gid_to_correct: {5: "\u0947"},
    )

    def fake_gsub(font, glyph_order, gid_to_correct):
        # 5 is the e + repha ligature; 4 is built on 5, so it reads whatever value
        # the caller hands in for 5 -- the guess, if the guess is still there.
        five = gid_to_correct.get(5, _E_REPH)
        return {5: _E_REPH, 4: five + "\u0901"}

    monkeypatch.setattr(kalimati, "_analyze_gsub", fake_gsub)
    doc = fitz.open(stream=_pdf_embedding(compiled.getvalue()), filetype="pdf")
    try:
        merged: dict[int, str] = {}
        for info in doc[0].get_fonts(full=True):
            if info[2] == "Type0":
                merged.update(kalimati._get_font_correction_map(doc, info[0]))
    finally:
        doc.close()

    assert merged.get(5) == _E_REPH
    assert merged.get(4) == _E_REPH + "\u0901"
