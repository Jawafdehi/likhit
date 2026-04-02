from __future__ import annotations

from functools import lru_cache
import io
import logging
from pathlib import Path
import subprocess
from types import SimpleNamespace

import fitz
from markitdown import MarkItDown
import pytest

from likhit.converters.nepali_docx import NepaliDocxConverter
from likhit.converters.nepali_pdf import NepaliPdfConverter
from likhit.markdown_assembly import assemble_markdown
from likhit.models import RepairedBlock, Table, TableCell, TableRegion
from likhit.nepali_pdf_repair import needs_nepali_pdf_repair
from likhit.pdf_page_analysis import analyze_pdf_pages, pdf_likely_needs_ocr

ROOT = Path(__file__).resolve().parents[1]


def _md() -> MarkItDown:
    return MarkItDown(enable_plugins=True)


def _convert_text(path: Path) -> str:
    return _md().convert(str(path)).text_content


@lru_cache(maxsize=1)
def _devanagari_font_path() -> Path | None:
    """Get path to a Devanagari font. Returns None on Windows."""
    import platform

    if platform.system() == "Windows":
        return None

    try:
        result = subprocess.check_output(
            [
                "bash",
                "-lc",
                "fc-match -f '%{file}\\n' 'Noto Sans Devanagari' | head -n 1",
            ],
            text=True,
        ).strip()
        return Path(result)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _create_unicode_pdf(path: Path, *, title: str, body: str) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    font_path = _devanagari_font_path()
    if font_path is None:
        doc.close()
        pytest.skip("Devanagari font not available on Windows")
    page.insert_font(fontname="noto", fontfile=str(font_path))
    page.insert_text((72, 72), title, fontname="noto", fontsize=20)
    page.insert_text((72, 120), body, fontname="noto", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _create_blank_pdf(path: Path) -> Path:
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


def _copy_pdf_pages(source: Path, destination: Path, *, start: int, end: int) -> Path:
    source_doc = fitz.open(source)
    trimmed = fitz.open()
    trimmed.insert_pdf(source_doc, from_page=start, to_page=end)
    trimmed.save(destination)
    trimmed.close()
    source_doc.close()
    return destination


def test_plain_unicode_pdf_falls_through_plugin_accepts_check(tmp_path: Path) -> None:
    font_path = _devanagari_font_path()
    if font_path is None:
        pytest.skip("Devanagari font not available (Windows or font not installed)")

    pdf_path = _create_unicode_pdf(
        tmp_path / "unicode.pdf",
        title="नेपाल सरकार",
        body="यो एउटा परीक्षण अनुच्छेद हो।",
    )

    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    with pdf_path.open("rb") as stream:
        assert converter.accepts(stream, stream_info) is True

    markdown = _convert_text(pdf_path)

    # Font extraction varies across CI runners for generated Unicode PDFs.
    # Keep this test focused on plugin acceptance and successful conversion.
    assert markdown.strip()
    assert needs_nepali_pdf_repair(str(pdf_path)) is False
    assert pdf_likely_needs_ocr(str(pdf_path)) is False


def test_converter_escalates_bad_default_pdf_output_to_likhit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module
    from markitdown import DocumentConverterResult

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "pdf_likely_needs_ocr",
        lambda _raw: False,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda raw, info: DocumentConverterResult(
            markdown=(
                "t\\,&H\nuoo5 hrD SD\n| --- | --- |\nI),lhlD UaXl\n"
                'ptunlh nu"r rgt\nhnl+UD Udtl\nerhealq\nerg$t+ P".t\n'
                "hBrbharehl qcrrh)F.pglrrtr"
            )
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_convert_with_likhit",
        lambda raw: DocumentConverterResult(markdown="नेपाल सरकार"),
    )

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    assert result.markdown == "नेपाल सरकार"


def test_converter_escalates_cid_garbage_default_pdf_output_to_likhit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module
    from markitdown import DocumentConverterResult

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "pdf_likely_needs_ocr",
        lambda _raw: False,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda raw, info: DocumentConverterResult(
            markdown="(cid:0)(cid:0)(cid:0) (cid:0)(cid:0)\n\n(cid:0)(cid:0)"
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_convert_with_likhit",
        lambda raw: DocumentConverterResult(markdown="नेपाल सरकार"),
    )

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    assert result.markdown == "नेपाल सरकार"


def test_converter_prefers_ocr_for_image_dominant_bad_text_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module
    from markitdown import DocumentConverterResult

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "pdf_likely_needs_ocr",
        lambda _raw: True,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda raw, info: DocumentConverterResult(
            markdown="t\\,&H\nuoo5 hrD SD\nI),lhlD UaXl"
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_ocr_pdf_converter",
        lambda raw, info, **kwargs: DocumentConverterResult(markdown="ओसीआर नतिजा"),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_convert_with_likhit",
        lambda raw: DocumentConverterResult(markdown='t+ "Ut"U U^'),
    )

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    assert result.markdown == "ओसीआर नतिजा"


def test_converter_logs_when_ocr_is_needed_but_not_configured(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module
    from markitdown import DocumentConverterResult

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "pdf_likely_needs_ocr",
        lambda _raw: True,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_default_pdf_converter",
        lambda raw, info: DocumentConverterResult(markdown="t\\,&H\nuoo5 hrD SD"),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_ocr_pdf_converter",
        lambda raw, info, **kwargs: None,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda raw: None,
    )

    with caplog.at_level(logging.INFO):
        with sample.open("rb") as stream:
            converter.convert(stream, stream_info)

    assert "OCR appears necessary, but OCR is not configured" in caplog.text


def test_convert_repairs_broken_cmap_sample() -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"

    raw_markitdown = MarkItDown().convert(str(sample)).text_content
    repaired = _convert_text(sample)

    assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" not in raw_markitdown
    assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" in repaired
    assert "प्रष्ट्रिधध" in raw_markitdown
    assert "प्रष्ट्रिधध" not in repaired
    assert not repaired.startswith("---")
    assert repaired.splitlines()[:6] == [
        "अख्तियार दुरुपयोग अनुसन्धान आयोग",
        "टङ्गाल, काठमाडौं",
        "मिति: २०८१।१०। २४ गते।",
        "प्रेस विज्ञप्ति",
        "विषय: आरोपपत्र दायर गरिएको।",
        "",
    ]


def test_convert_repairs_legacy_font_sample(tmp_path: Path) -> None:
    sample = _copy_pdf_pages(
        ROOT / "samples" / "kanunpatrika.pdf",
        tmp_path / "kanunpatrika-first-page.pdf",
        start=0,
        end=0,
    )

    raw_markitdown = MarkItDown().convert(str(sample)).text_content
    repaired = _convert_text(sample)

    assert "नेपाल कानून पत्रिका" not in raw_markitdown
    assert "नेपाल कानून पत्रिका" in repaired


def test_convert_preserves_two_column_reading_order() -> None:
    sample = ROOT / "samples" / "kanunpatrika.pdf"

    markdown = _convert_text(sample)

    assert "निर्णय नं.७९७३" in markdown
    assert "सर्बोच्च अदालत विशेष इजलास" in markdown
    assert "जवर्जस्ती करणीको महलमा भएको" in markdown
    assert markdown.index("सर्बोच्च अदालत विशेष इजलास") < markdown.index(
        "जवर्जस्ती करणीको महलमा भएको"
    )
    assert not markdown.startswith("---")


def test_converter_reorders_two_column_fragments_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module
    from likhit.extractors.base import RawDocument, TextFragment
    from likhit.models import DocumentType

    fragments = [
        TextFragment("HEADER", 1, 50, 50, 120, 60),
        TextFragment("LEFT", 1, 50, 120, 120, 130),
        TextFragment("RIGHT", 1, 300, 220, 360, 230),
    ]
    raw_document = RawDocument(
        paragraphs=[fragment.text for fragment in fragments],
        raw_text="\n".join(fragment.text for fragment in fragments),
        fragments=fragments,
        tables=[],
    )

    monkeypatch.setattr(
        nepali_pdf_module.FontBasedStrategy,
        "extract_text",
        lambda self, path: raw_document,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "detect_structure",
        lambda seen_raw_document: DocumentType.TWO_COLUMN_LAYOUT,
    )

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    assert result.markdown.splitlines() == ["HEADER", "", "LEFT", "", "RIGHT"]


def test_convert_renders_tables_as_raw_pipe_separated_rows() -> None:
    sample = ROOT / "samples" / "my-table.pdf"

    markdown = _convert_text(sample)

    assert "तालिका २.१९" in markdown
    assert "क्र.सं. | उजुरीको व्यहोरा | अनुसन्धानबाट पुष्टि भएको | आयोगको निर्णय" in markdown
    assert "व्यहोरा | बमोजिम कसुर/सजाय" in markdown
    assert "1 | आन्तरिक | प्रतिवादीहरूको | 2081/04/24," in markdown
    assert "मामिला | मिलेमतोमा | 2081/04/31," in markdown
    assert "**1**" not in markdown
    assert "- **उजुरीको व्यहोरा:**" not in markdown


def test_convert_preserves_pre_table_line_breaks_in_markdown() -> None:
    sample = ROOT / "samples" / "my-table.pdf"

    result = _md().convert(str(sample))

    assert "विवरण देहायबमोजिम\nरहेको छः\nतालिका २.१९" in result.markdown
    assert result.markdown.index("विवरण देहायबमोजिम") < result.markdown.index(
        "तालिका २.१९"
    )
    assert result.markdown.count("तालिका २.१९") >= 1


def test_convert_normalizes_replacement_char_bullets_in_two_column_output() -> None:
    sample = ROOT / "samples" / "kanunpatrika.pdf"

    markdown = _convert_text(sample)

    assert "� अपराध" not in markdown
    assert "- अपराध" in markdown


def test_convert_keeps_aarop_patra_title_lines_readable() -> None:
    sample = ROOT / "samples" / "aarop-patra.pdf"
    if not sample.exists():
        pytest.skip("aarop-patra sample not available")

    markdown = _convert_text(sample)

    assert "श्री विशेष अदालत, काठमाडौं समक्ष पेस गरेको" in markdown
    assert "आरोप-पत्र" in markdown
    assert "श्री ववशेष अदालत, काठमाड� समक्ष पेस गरेको" not in markdown
    assert markdown.splitlines()[:4] == [
        "(महाशाखा नं. ९)",
        "श्री विशेष अदालत, काठमाडौं समक्ष पेस गरेको",
        "आरोप-पत्र",
        "२०८१/08२ सालको नम्वर .................",
    ]


def test_nirnaya_pages_are_detected_as_image_dominant_bad_text_layers() -> None:
    sample = ROOT / "samples" / "nirnaya.pdf"

    analyses = analyze_pdf_pages(str(sample))

    assert analyses
    assert all(analysis.is_image_dominant for analysis in analyses)
    assert all(analysis.likely_needs_ocr for analysis in analyses)
    assert pdf_likely_needs_ocr(str(sample)) is True


def test_assemble_markdown_preserves_headings_lists_and_tables() -> None:
    table = Table(
        row_count=2,
        col_count=2,
        cells=[
            TableCell(row=0, col=0, text="नाम"),
            TableCell(row=0, col=1, text="रकम"),
            TableCell(row=1, col=0, text="परियोजना"),
            TableCell(row=1, col=1, text="१०००"),
        ],
        caption="तालिका १",
        regions=[TableRegion(page_number=1, x0=0, y0=0, x1=100, y1=50)],
    )
    markdown = assemble_markdown(
        [
            RepairedBlock(
                text="रिपोर्ट शीर्षक",
                order_index=0,
                page_number=1,
                heading_level=1,
            ),
            RepairedBlock(
                text="यसमा सामान्य अनुच्छेद छ।",
                order_index=1,
                page_number=1,
            ),
            RepairedBlock(
                text="1. पहिलो बुँदा",
                order_index=2,
                page_number=1,
                list_marker="1.",
            ),
            RepairedBlock(text="तालिका १", order_index=3, page_number=1, table=table),
        ]
    )

    assert "# रिपोर्ट शीर्षक" in markdown
    assert "यसमा सामान्य अनुच्छेद छ।" in markdown
    assert "1. पहिलो बुँदा" in markdown
    assert "| नाम | रकम |" in markdown
    assert "| परियोजना | १००० |" in markdown


def test_convert_rejects_empty_pdf(tmp_path: Path) -> None:
    pdf_path = _create_blank_pdf(tmp_path / "blank.pdf")

    assert _convert_text(pdf_path) == ""


def test_docx_converter_accepts_only_doc() -> None:
    converter = NepaliDocxConverter()

    assert converter.accepts(
        io.BytesIO(b""),
        SimpleNamespace(extension=".doc", mimetype="application/msword"),
    )
    assert (
        converter.accepts(
            io.BytesIO(b""),
            SimpleNamespace(
                extension=".docx",
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
        is False
    )
