"""Legacy keystroke-font runs inside Word documents, repaired by each run's font.

Fixtures are built here as minimal ``.docx`` packages so every case states exactly which
font draws which characters. The cases come from Supreme Court and Special Court orders
on the court portal (Jawafdehi JAW-49), reduced to the shape that broke:

* a defendant's name typed in PCS NEPALI inside a Kalimati table cell
  (Special Court 076-CR-0120: ``cR%]nfn /fosf] gflt`` -> ``अच्छेलाल रायको नाति``);
* an order typed in Preeti from end to end (Supreme 067-WO-1212);
* real English inside a Unicode order -- medical findings, Latin maxims -- which a
  shape-based test garbled and a font-based one must leave alone;
* punctuation typed between Unicode words in a run whose ASCII slot names Preeti
  (Supreme 081-WH-0114: ``(दुई महिना)`` must not become ``९दुई महिना०``).
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from markitdown import MarkItDown, StreamInfo

from likhit.converters.legacy_font_docx import (
    LegacyFontDocxConverter,
    convert_legacy_font_doc,
    names_legacy_font,
)
from likhit.extractors.docx_legacy_runs import (
    docx_font_census,
    repair_docx_legacy_runs,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)


def run(text: str, ascii_font: str | None = None, cs_font: str = "Kalimati") -> str:
    fonts = ""
    if ascii_font is not None:
        fonts = (
            f'<w:rPr><w:rFonts w:ascii="{ascii_font}" w:hAnsi="{ascii_font}" '
            f'w:cs="{cs_font}"/></w:rPr>'
        )
    return f'<w:r>{fonts}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def para(*runs: str) -> str:
    return f"<w:p>{''.join(runs)}</w:p>"


def table(*cells: str) -> str:
    tcs = "".join(f"<w:tc>{cell}</w:tc>" for cell in cells)
    return f"<w:tbl><w:tr>{tcs}</w:tr></w:tbl>"


def docx(*blocks: str, default_ascii: str | None = None) -> bytes:
    defaults = ""
    if default_ascii:
        defaults = (
            "<w:docDefaults><w:rPrDefault><w:rPr>"
            f'<w:rFonts w:ascii="{default_ascii}" w:hAnsi="{default_ascii}" w:cs="Kalimati"/>'
            "</w:rPr></w:rPrDefault></w:docDefaults>"
        )
    styles = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">{defaults}</w:styles>'
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{"".join(blocks)}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
    return out.getvalue()


def markdown(data: bytes) -> str:
    return (
        LegacyFontDocxConverter()
        .convert(io.BytesIO(data), StreamInfo(extension=".docx"))
        .markdown
    )


def test_pcs_nepali_name_in_a_kalimati_table_cell_is_converted() -> None:
    data = docx(
        table(
            para(run("cR%]nfn /fosf] gflt", "PCS NEPALI"), run(" सर्लाही जिल्ला")),
            para(run("प्रतिवादी")),
        )
    )
    md = markdown(data)
    assert "अच्छेलाल रायको नाति" in md
    assert "cR%]nfn" not in md
    assert "सर्लाही जिल्ला" in md
    assert "प्रतिवादी" in md


def test_pcs_nepali_and_preeti_use_their_own_maps() -> None:
    # `%` is छ under PCS NEPALI; Preeti reads the same key as a digit.
    pcs = markdown(docx(para(run("cR%]nfn", "PCS NEPALI"))))
    assert "अच्छेलाल" in pcs


def test_whole_document_preeti_by_document_default() -> None:
    data = docx(
        para(run(";jf]{Rr cbfnt")),
        para(run("dfggLo GofofwLz >L sNof0f >]i7")),
        default_ascii="Preeti",
    )
    md = markdown(data)
    assert "सर्वोच्च अदालत" in md
    assert "माननीय न्यायाधीश श्री कल्याण श्रेष्ठ" in md


def test_preeti_digits_convert_in_a_whole_legacy_paragraph() -> None:
    md = markdown(docx(para(run("@)^& ;fn", "Preeti"))))
    assert "२०६७ साल" in md


def test_english_in_a_latin_font_is_untouched() -> None:
    english = "Hymen tear old in nature and fresh laceration at fourchette"
    data = docx(
        para(
            run("पीडितको स्वास्थ्य परीक्षण: "),
            run(english, "Times New Roman"),
            run(" ;fy}", "Preeti"),
        )
    )
    md = markdown(data)
    assert english in md
    assert "साथै" in md


def test_latin_case_number_next_to_preeti_is_untouched() -> None:
    data = docx(para(run("066-WO-1203", "Times New Roman"), run(" cfb]z", "Preeti")))
    md = markdown(data)
    assert "066-WO-1203" in md
    assert "आदेश" in md


def test_punctuation_between_unicode_words_is_left_alone() -> None:
    # The style names Preeti for the ASCII slot, but the words are Unicode: the
    # brackets and full stops are punctuation, not keystrokes.
    data = docx(
        para(run("कैद "), run("(", "Preeti"), run("दुई महिना"), run(")", "Preeti")),
        para(run("वडा नं"), run(".", "Preeti"), run(" ४")),
    )
    md = markdown(data)
    assert "(दुई महिना)" in md
    assert "९दुई" not in md
    assert "वडा नं." in md


def test_word_split_across_runs_reorders_the_i_matra() -> None:
    # Preeti types ि (`l`) before its consonant; Word often splits that word
    # into separate runs, and the conversion must still see them together.
    data = docx(para(run("l", "Preeti"), run("dlt", "Preeti")))
    assert "मिति" in markdown(data)


def test_document_without_legacy_fonts_is_returned_unchanged() -> None:
    data = docx(para(run("सर्वोच्च अदालत, संयुक्त इजलास")))
    repaired, report = repair_docx_legacy_runs(data)
    assert repaired is data
    assert not report.converted


def test_census_counts_latin_slot_characters_by_font() -> None:
    data = docx(para(run("cfb]z", "Preeti"), run("CI", "Times New Roman")))
    census = docx_font_census(data)
    assert census["Preeti"] == 5
    assert census["Times New Roman"] == 2


def test_plugin_routes_docx_through_the_repair() -> None:
    data = docx(para(run("cfb]z", "Preeti")))
    md = MarkItDown(enable_plugins=True).convert_stream(
        io.BytesIO(data), stream_info=StreamInfo(extension=".docx")
    )
    assert "आदेश" in md.text_content


def test_names_legacy_font_reads_a_word97_font_table() -> None:
    # A .doc stores font names UTF-16LE in its font table.
    blob = b"\xd0\xcf\x11\xe0" + b"\x00" * 64 + "PCS NEPALI".encode("utf-16-le")
    assert names_legacy_font(blob)
    assert not names_legacy_font(b"\xd0\xcf\x11\xe0" + "Kalimati".encode("utf-16-le"))


def test_names_legacy_font_reads_docx_xml() -> None:
    assert names_legacy_font(docx(para(run("x", "Preeti"))))
    assert not names_legacy_font(docx(para(run("x", "Times New Roman"))))


def test_doc_named_docx_is_repaired_without_libreoffice(tmp_path) -> None:
    # The court portal serves many .docx packages under a .doc name.
    path = tmp_path / "order.doc"
    path.write_bytes(docx(para(run("cfb]z", "Preeti"))))
    md = convert_legacy_font_doc(str(path))
    assert md is not None and "आदेश" in md


def test_doc_without_legacy_font_keeps_the_existing_path(tmp_path) -> None:
    path = tmp_path / "order.doc"
    path.write_bytes(docx(para(run("आदेश"))))
    assert convert_legacy_font_doc(str(path)) is None


def test_doc_needing_libreoffice_falls_back_when_it_is_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    path = tmp_path / "order.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0" + "Preeti".encode("utf-16-le"))
    assert convert_legacy_font_doc(str(path)) is None


def test_missing_file_falls_back() -> None:
    assert convert_legacy_font_doc("/nonexistent/order.doc") is None
