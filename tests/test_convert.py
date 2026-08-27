from __future__ import annotations

from functools import lru_cache
import io
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import fitz
from markitdown import MarkItDown
import pytest

from likhit.converters.nepali_docx import NepaliDocxConverter
from likhit.converters.nepali_pdf import NepaliPdfConverter
from likhit.renderers.markdown import strip_page_anchors
from likhit.markdown_assembly import assemble_markdown
from likhit.models import RepairedBlock, Table, TableCell, TableRegion
from likhit.nepali_pdf_repair import needs_nepali_pdf_repair
from likhit.pdf_page_analysis import analyze_pdf_pages, pdf_likely_needs_ocr

ROOT = Path(__file__).resolve().parents[1]


def _md(*, enable_plugins: bool = True) -> MarkItDown:
    return MarkItDown(enable_plugins=enable_plugins)


def _convert_text(path: Path, *, pages: str | None = None) -> str:
    """Convert ``path``, optionally restricting to a ``pages`` range.

    Pass ``pages`` when a test asserts only on the opening of a long document.
    ``samples/kanunpatrika.pdf`` is 128 pages and ``samples/aarop-patra.pdf`` 67, and
    a full conversion of either costs ~14.1s / ~9.6s -- measured. Together with the
    integration tests over the same two documents that was **105s of a 136s suite,
    77%**, in eight tests, with a 7.9x cliff to 9th place at 1.25s. Restricting each
    to the pages it actually asserts on:

        test_convert_preserves_two_column_reading_order          14.46s -> 0.30s
        ..._normalizes_replacement_char_bullets_in_two_column    14.69s -> 0.21s
        test_convert_keeps_aarop_patra_title_lines_readable       9.99s -> 0.38s

    This is not a weaker input. ``pages`` slices the PDF before extraction, and
    :func:`test_convert_honors_page_range_selection_for_pdf` below asserts that
    ``convert(sample, pages="1-2")`` is byte-identical to converting a
    :func:`_copy_pdf_pages` slice of that same range -- so a page-restricted
    conversion is the same code path over a smaller document, not a different one.

    Whole-document regression coverage is deliberately *not* duplicated here: it
    lives in ``tests/integration/test_sample_pdfs.py``, which converts both
    samples in full behind an ``lru_cache`` and checks markers, ordering, shape and
    ``max_replacement_chars=0``. Widen a range here only to reach a marker that is
    genuinely deeper in the document.
    """

    if pages is None:
        return _md().convert(str(path)).text_content
    return _md().convert(str(path), pages=pages).text_content


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
        lambda raw, **_kwargs: (
            DocumentConverterResult(markdown="नेपाल सरकार"),
            [],
        ),
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
        "_run_default_pdf_converter",
        lambda raw, info: DocumentConverterResult(
            markdown="(cid:0)(cid:0)(cid:0) (cid:0)(cid:0)\n\n(cid:0)(cid:0)"
        ),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_convert_with_likhit",
        lambda raw, **_kwargs: (
            DocumentConverterResult(markdown="नेपाल सरकार"),
            [],
        ),
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

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_ocr_page",
        lambda _document, _page_index: "image_only",
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_build_ocr_service",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_run_page_ocr",
        lambda _raw, _service, _pages: nepali_pdf_module._PageOcrResult(
            {1: "ओसीआर नतिजा"},
            (),
        ),
    )

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    assert "ओसीआर नतिजा" in result.markdown
    assert "needs-ocr" not in result.markdown


def test_converter_uses_standard_markitdown_llm_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module

    client = object()
    observed: list[tuple[object, str]] = []
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_ocr_page",
        lambda _document, _page_index: "image_only",
    )

    def fake_ocr(raw, service, pages):
        observed.append((service.client, service.model))
        return nepali_pdf_module._PageOcrResult({1: "ओसीआर नतिजा"}, ())

    monkeypatch.setattr(nepali_pdf_module, "_run_page_ocr", fake_ocr)

    with sample.open("rb") as stream:
        result = converter.convert(
            stream,
            stream_info,
            llm_client=client,
            llm_model="vision-model",
        )

    assert observed == [(client, "vision-model")]
    assert "ओसीआर नतिजा" in result.markdown


def test_converter_forces_ocr_when_likhit_flags_dropped_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A repair-font doc where likhit dropped a scanned page must still run OCR,
    # so the dropped page's content is not silently lost.
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module
    from markitdown import DocumentConverterResult
    from likhit.renderers.markdown import page_anchor

    ocr_calls: list[bool] = []
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Preeti": "legacy_remap"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_ocr_page",
        lambda _document, _page_index: "image_only",
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_build_ocr_service",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_try_convert_with_likhit",
        lambda raw, **_kwargs: (
            DocumentConverterResult(markdown=f"{page_anchor(1)}\n\nपहिलो पृष्ठ"),
            [1],
            None,
        ),
    )

    def _fake_ocr(raw, service, pages):
        ocr_calls.append(True)
        return nepali_pdf_module._PageOcrResult(
            {1: "ओसीआर एनेक्स पृष्ठ नतिजा हो"},
            (),
        )

    monkeypatch.setattr(nepali_pdf_module, "_run_page_ocr", _fake_ocr)

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    assert ocr_calls, "OCR must run when likhit flags dropped pages"
    assert "पहिलो पृष्ठ" in result.markdown
    assert "ओसीआर एनेक्स पृष्ठ नतिजा हो" in result.markdown


@pytest.mark.parametrize(
    ("ocr_configured", "expected_reason"),
    [
        (False, "not-configured"),
        (True, "ocr-failed"),
    ],
)
def test_converter_marks_why_required_ocr_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    ocr_configured: bool,
    expected_reason: str,
) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    converter = NepaliPdfConverter()
    stream_info = SimpleNamespace(extension=".pdf", mimetype="application/pdf")

    import likhit.converters.nepali_pdf as nepali_pdf_module

    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_fonts_from_stream",
        lambda _raw: {"Helvetica": "correct"},
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "classify_ocr_page",
        lambda _document, _page_index: "image_only",
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_build_ocr_service",
        lambda **_kwargs: object() if ocr_configured else None,
    )
    if ocr_configured:
        monkeypatch.setattr(
            nepali_pdf_module,
            "_run_page_ocr",
            lambda _raw, _service, pages: nepali_pdf_module._PageOcrResult(
                {},
                tuple(pages),
            ),
        )

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    marker = nepali_pdf_module.NEEDS_OCR_MARKER_PATTERN.search(result.markdown)
    assert marker is not None
    assert marker.group(2) == expected_reason


def _content_lines(markdown: str) -> list[str]:
    """Lines of `markdown` with page anchors, and the gap each leaves, removed.

    These assertions are about text and reading order, not about anchors, so
    they compare the content stream rather than the raw output.
    """

    stripped = strip_page_anchors(markdown)
    return re.sub(r"\n{3,}", "\n\n", stripped).strip().splitlines()


def test_convert_repairs_broken_cmap_sample() -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"

    raw_markitdown = _md(enable_plugins=False).convert(str(sample)).text_content
    repaired = _convert_text(sample)

    assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" not in raw_markitdown
    assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" in repaired
    assert "प्रष्ट्रिधध" in raw_markitdown
    assert "प्रष्ट्रिधध" not in repaired
    assert not repaired.startswith("---")
    assert _content_lines(repaired)[:6] == [
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

    raw_markitdown = _md(enable_plugins=False).convert(str(sample)).text_content
    repaired = _convert_text(sample)

    assert "नेपाल कानून पत्रिका" not in raw_markitdown
    assert "नेपाल कानून पत्रिका" in repaired


def test_convert_honors_single_page_selection_for_pdf(tmp_path: Path) -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"
    expected = _copy_pdf_pages(
        sample,
        tmp_path / "pressrelease-page-1.pdf",
        start=0,
        end=0,
    )

    selected_markdown = _md().convert(str(sample), pages="1").text_content
    expected_markdown = _md().convert(str(expected)).text_content

    assert selected_markdown == expected_markdown
    assert "प्रेस विज्ञप्ति" in selected_markdown


def test_convert_honors_page_range_selection_for_pdf(tmp_path: Path) -> None:
    sample = ROOT / "samples" / "kanunpatrika.pdf"
    expected = _copy_pdf_pages(
        sample,
        tmp_path / "kanunpatrika-pages-1-2.pdf",
        start=0,
        end=1,
    )

    selected_markdown = _md().convert(str(sample), pages="1-2").text_content
    expected_markdown = _md().convert(str(expected)).text_content

    assert selected_markdown == expected_markdown


def test_convert_preserves_two_column_reading_order() -> None:
    sample = ROOT / "samples" / "kanunpatrika.pdf"

    # Every marker below is on page 1; see _convert_text on why the range.
    markdown = _convert_text(sample, pages="1-2")

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

    fragments = [
        TextFragment("HEADER", 1, 50, 50, 120, 60),
        TextFragment("LEFT_1", 1, 50, 120, 120, 130),
        TextFragment("RIGHT_1", 1, 300, 120, 360, 130),
        TextFragment("LEFT_2", 1, 50, 140, 120, 150),
        TextFragment("RIGHT_2", 1, 300, 140, 360, 150),
        TextFragment("LEFT_3", 1, 50, 160, 120, 170),
        TextFragment("RIGHT_3", 1, 300, 160, 360, 170),
        TextFragment("LEFT_4", 1, 50, 180, 120, 190),
        TextFragment("RIGHT_4", 1, 300, 180, 360, 190),
        TextFragment("LEFT_5", 1, 50, 200, 120, 210),
        TextFragment("RIGHT_5", 1, 300, 200, 360, 210),
        TextFragment("LEFT_6", 1, 50, 220, 120, 230),
        TextFragment("RIGHT_6", 1, 300, 220, 360, 230),
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
        lambda self, path, **_kwargs: raw_document,
    )
    monkeypatch.setattr(
        nepali_pdf_module,
        "_markdown_quality_score",
        lambda markdown: 100 if "LEFT_1" in markdown else 0,
    )

    with sample.open("rb") as stream:
        result = converter.convert(stream, stream_info)

    assert result.markdown.index("LEFT_1") < result.markdown.index("LEFT_6")
    assert result.markdown.index("LEFT_6") < result.markdown.index("RIGHT_1")
    assert result.markdown.index("RIGHT_1") < result.markdown.index("RIGHT_6")


def test_convert_renders_tables_as_raw_pipe_separated_rows() -> None:
    sample = ROOT / "samples" / "my-table.pdf"

    markdown = _convert_text(sample)

    assert "तालिका २.१९" in markdown
    assert (
        "| क्र.सं. | उजुरीको व्यहोरा |  |  | अनुसन्धानबाट पुष्टि भएको व्यहोरा |  |  |  | "
        "आयोगको निर्णय मिति/आरोपपत्र दायर मिति/मुद्दा नं र प्रतिवादी सङ्ख्या |  | "
        "प्रतिवादीको नाम, पद र कार्यालय |  |  | "
        "भ्रष्टाचार निवारण ऐन, २०५९ बमोजिम कसुर/सजाय मागदाबी/बिगो |  |  |"
    ) in markdown
    assert (
        "|  |  |  |  | व्यहोरा |  |  |  |  |  |  |  |  | बमोजिम कसुर/सजाय |  |  |"
    ) not in markdown
    # Each cell here holds whole printed lines. This used to read
    # `| 1 |  | आन्तरिक |  | प्रतिवादीहरूको |  ...` with `मामिला` and `तथा` on the two
    # rows below, because PyMuPDF splits this table's lines into one fragment per
    # word -- `आन्तरिक`, `मामिला` and `तथा` all sit at y 241.49..258.21 -- and
    # `_extract_cell_text` emitted one output row per fragment. VOL-91.
    assert (
        "| 1 |  | आन्तरिक मामिला तथा |  | प्रतिवादीहरूको मिलेमतोमा |  |  |  | 2081/04/24, |"
    ) in markdown
    assert ("|  |  |  |  | इलाका प्रहरी कार्यालय, |") in markdown
    # 🛑 Both rows above USED TO carry a second copy of the decision date, because
    # this table's grid has a FRAME cell spanning it whole and `_extract_cell_text`
    # read every fragment into that too (VOL-744). The two assertions were written
    # against the doubled output and are corrected here, not relaxed.
    #
    # The frame's copy was also MISALIGNED, which is why keeping it was worse than
    # dropping it rather than merely redundant: `2081/04/31` belongs to the record on
    # the `कानुन मन्त्रालय, मधेस` row, and the frame printed it against
    # `इलाका प्रहरी कार्यालय,` five rows earlier. Each date now appears exactly once,
    # on its own row -- assert the count, so a returning frame fails here.
    assert markdown.count("2081/04/24") == 1
    assert markdown.count("2081/04/31") == 1
    assert ("|  |  | कानुन मन्त्रालय, मधेस |  |  |  |  |  | 2081/04/31, |") in markdown
    assert "**1**" not in markdown
    assert "- **उजुरीको व्यहोरा:**" not in markdown


def test_quality_score_ignores_a_rows_enclosing_pipe_delimiters() -> None:
    # Enclosing a row in pipes describes the same columns, so it must not move
    # the score. Raw table rows are enclosed so leading and trailing blank
    # cells stay visible; that must not read as pipe spam.
    import likhit.converters.nepali_pdf as nepali_pdf_module

    bare = "क्र.सं. | नाम\n1 | राम"
    enclosed = "| क्र.सं. | नाम |\n| 1 | राम |"

    assert nepali_pdf_module._pipe_heavy_line_count(
        bare
    ) == nepali_pdf_module._pipe_heavy_line_count(enclosed)
    # Three columns is genuine pipe spam either way.
    assert nepali_pdf_module._pipe_heavy_line_count("| a | b | c |") == 1


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

    # The bulleted "अपराध" run is on page 1; see _convert_text on why the range.
    markdown = _convert_text(sample, pages="1-2")

    assert "� अपराध" not in markdown
    assert "- अपराध" in markdown


def test_convert_keeps_aarop_patra_title_lines_readable() -> None:
    sample = ROOT / "samples" / "aarop-patra.pdf"
    assert sample.exists()

    # The asserted lines are the document's first four; see _convert_text.
    markdown = _convert_text(sample, pages="1")

    assert "श्री विशेष अदालत, काठमाडौं समक्ष पेस गरेको" in markdown
    assert "आरोप-पत्र" in markdown
    assert "श्री ववशेष अदालत, काठमाड� समक्ष पेस गरेको" not in markdown
    assert _content_lines(markdown)[:4] == [
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
