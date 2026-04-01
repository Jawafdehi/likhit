"""Integration tests for end-to-end conversion success."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from markitdown import MarkItDown
import pytest

from likhit.save_cli import main as save_cli_main

from .conftest import (
    TEST_DATA_DIR,
    assert_fixture_size_under_threshold,
    compute_total_fixture_size,
    discover_all_fixtures,
    discover_fixtures_by_extension,
)

IS_WINDOWS = platform.system() == "Windows"


def _has_working_doc_runtime() -> bool:
    """Return True when at least one DOC extractor runtime is available."""
    if shutil.which("textutil"):
        return True

    antiword_bin = shutil.which("antiword")
    if antiword_bin:
        return True

    try:
        import pyantiword

        bundled = Path(pyantiword.__file__).resolve().parent / "antiword"
        if not bundled.exists():
            return False
        subprocess.run(
            [str(bundled), "-h"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return True
    except (OSError, ImportError):
        return False


DOC_EXTRACTION_AVAILABLE = (not IS_WINDOWS) and _has_working_doc_runtime()
SKIP_DOC_WHEN_UNAVAILABLE = pytest.mark.skipif(
    not DOC_EXTRACTION_AVAILABLE,
    reason=(
        "DOC extraction requires a working runtime (antiword or macOS textutil). "
        "On macOS you can also install antiword with: brew install antiword"
    ),
)


def _md() -> MarkItDown:
    return MarkItDown(enable_plugins=True)


def _normalize_lines(markdown: str) -> list[str]:
    return [line.strip() for line in markdown.splitlines() if line.strip()]


def _assert_lines_in_order(markdown: str, expected_lines: list[str]) -> None:
    lines = _normalize_lines(markdown)
    cursor = 0
    for expected in expected_lines:
        while cursor < len(lines) and lines[cursor] != expected:
            cursor += 1
        assert cursor < len(lines), f"Expected line not found in order: {expected!r}"
        cursor += 1


def _assert_markers_absent(markdown: str, markers: list[str]) -> None:
    for marker in markers:
        assert marker not in markdown, f"Unexpected marker found: {marker!r}"


class TestFixtureGovernance:
    """Tests to ensure fixture directory meets requirements."""

    def test_fixture_directory_exists(self) -> None:
        assert TEST_DATA_DIR.exists(), f"Test data directory not found: {TEST_DATA_DIR}"
        assert (
            TEST_DATA_DIR.is_dir()
        ), f"Test data path is not a directory: {TEST_DATA_DIR}"

    def test_fixture_size_under_threshold(self) -> None:
        assert_fixture_size_under_threshold(threshold_mb=50)

        total_bytes = compute_total_fixture_size()
        total_mb = total_bytes / (1024 * 1024)
        print(f"\nTotal fixture size: {total_mb:.2f} MB")

    def test_required_formats_present(self) -> None:
        pdf_fixtures = discover_fixtures_by_extension(".pdf")
        docx_fixtures = discover_fixtures_by_extension(".docx")
        doc_fixtures = discover_fixtures_by_extension(".doc")

        assert len(pdf_fixtures) > 0, "No PDF fixtures found"
        assert len(docx_fixtures) > 0, "No DOCX fixtures found"
        assert len(doc_fixtures) > 0, "No DOC fixtures found"

        print(
            f"\nFound {len(pdf_fixtures)} PDF, {len(docx_fixtures)} DOCX, {len(doc_fixtures)} DOC fixtures"
        )


class TestPluginConversion:
    """Integration tests for plugin-backed MarkItDown conversion."""

    @pytest.mark.parametrize("fixture_path", discover_all_fixtures())
    def test_convert_produces_nonempty_output(self, fixture_path: Path) -> None:
        if fixture_path.suffix.lower() == ".doc" and not DOC_EXTRACTION_AVAILABLE:
            pytest.skip(
                "DOC extraction requires a working runtime (antiword or macOS textutil)"
            )

        markdown = _md().convert(str(fixture_path)).text_content

        assert markdown, f"Empty output for {fixture_path.name}"
        assert len(markdown) > 0, f"Zero-length output for {fixture_path.name}"
        assert isinstance(
            markdown, str
        ), f"Output is not a string for {fixture_path.name}"

    def test_notice_style_pdf_output_matches_expected_structure(self) -> None:
        notice_pdf = TEST_DATA_DIR / "ciaa_pressrelease_sample.pdf"
        if not notice_pdf.exists():
            pytest.skip("Notice-style PDF sample not found")

        markdown = _md().convert(str(notice_pdf)).text_content
        first_lines = markdown.splitlines()[:7]

        assert first_lines == [
            "अख्तियार दुरुपयोग अनुसन्धान आयोग",
            "टङ्गाल, काठमाडौं",
            "मिमि: २०८१।१०। २४ गिे।",
            "प्रेस विज्ञवि",
            "विषय: आरोपपत्र दायर गररएको।",
            "",
            "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा आ.व. २०७४/७५ मा आह्वान गरिएको बोलपत्र NITC/G/NCB-7-",
        ]
        _assert_lines_in_order(markdown, ["प्रवक्ता", "नरहरि घिमिरे"])
        assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" in markdown
        assert "आह्वान गरिएको बोलपत्र" in markdown
        assert not markdown.startswith("---")
        assert "प्रष्ट्रिधध" not in markdown
        assert "काठमाड�" not in markdown

    def test_two_column_pdf_output_preserves_reading_order(self) -> None:
        two_column_pdf = TEST_DATA_DIR / "kanun_patrika_sample.pdf"
        if not two_column_pdf.exists():
            pytest.skip("Two-column PDF sample not found")

        markdown = _md().convert(str(two_column_pdf)).text_content

        _assert_lines_in_order(
            markdown,
            [
                "नेपाल कानून पत्रिका द्दण्टछ, अंक ट",
                "निर्णय नं.७९७३ ने.का.प. २०६५",
                "सर्बोच्च अदालत विशेष इजलास",
                "सम्माननीय प्रधानन्यायाधीश श्री केदारप्रसाद",
                "गिरी",
            ],
        )
        lines = _normalize_lines(markdown)
        assert lines.index("सर्बोच्च अदालत विशेष इजलास") < lines.index(
            "जवर्जस्ती करणीको महलमा भएको"
        )
        assert "सम्बत् २०६३ सालको रिट नं. ०६४–००३५" in markdown
        assert "बिषयः– नेपालको अन्तरिम संविधान २०६३" in markdown
        assert "गरिपाऊँ।" in markdown
        assert not markdown.startswith("---")

    def test_docx_passthrough_still_converts_with_plugins_enabled(self) -> None:
        notice_docx = TEST_DATA_DIR / "ciaa_pressrelease_sample.docx"
        if not notice_docx.exists():
            pytest.skip("Notice-style DOCX sample not found")

        markdown = _md().convert(str(notice_docx)).text_content
        first_lines = markdown.splitlines()[:13]

        assert markdown
        assert first_lines == [
            "**अख्तियार दुरुपयोग अनुसन्धान आयोग**",
            "",
            "![](data:image/x-emf;base64...)",
            "",
            "**टङ्गाल, काठमाडौं**",
            "",
            "मिति: २०८२।१०।२८",
            "",
            "**प्रेस विज्ञप्ति**",
            "",
            "**विषय: अख्तियार दुरुपयोग अनुसन्धान आयोगको 3५ औं स्थापना दिवसको मूल समारोह कार्यक्रम सम्पन्‍न।**",
            "",
            "**“सुशासन र समृद्धि नागरिकको अधिकारः भ्रष्‍टाचार नियन्त्रणमा बनौं सबै जिम्मेवार”** भन्‍ने नाराका साथ अख्तियार दुरुपयोग अनुसन्धान आयोगको 3५औं स्थापना दिवसको मूल समारोह सम्माननीय राष्‍ट्रपति श्री रामचन्द्र पौडेलको प्रमुख आतिथ्यमा सम्पन्‍न भयो।",
        ]
        assert "मिति: २०८२।१०।२८" in markdown
        assert "प्रेस विज्ञप्ति" in markdown
        assert "सुशासन र समृद्धि नागरिकको अधिकारः" in markdown
        _assert_lines_in_order(
            markdown,
            [
                "मिति: २०८२।१०।२८",
                "**प्रेस विज्ञप्ति**",
                "प्रवक्ता",
                "सुरेश न्यौपाने",
            ],
        )
        _assert_markers_absent(markdown, ["---", "काठमाड�", "प्रष्ट्रिधध"])

    @SKIP_DOC_WHEN_UNAVAILABLE
    def test_notice_style_doc_output_contains_expected_content(self) -> None:
        notice_doc = TEST_DATA_DIR / "ciaa_legacy_sample.doc"
        if not notice_doc.exists():
            pytest.skip("Notice-style DOC sample not found")

        markdown = _md().convert(str(notice_doc)).text_content

        assert markdown
        assert markdown.splitlines()[:2] == [
            "# आरोपपत्र दायर गरिएको",
            "",
        ]
        assert "अख्तियार दुरुपयोग अनुसन्धान आयोग टङ्गाल, काठमाडौं" in markdown
        assert "मिति: २०८२।०३।०२ गते ।" in markdown
        assert "प्रेस विज्ञप्ति" in markdown
        assert "विषय: आरोपपत्र दायर गरिएको ।" in markdown
        assert "विशेष अदालत, काठमाडौंमा आरोपपत्र दायर गरिएको छ।" in markdown
        _assert_lines_in_order(
            markdown,
            [
                "# आरोपपत्र दायर गरिएको",
                "अख्तियार दुरुपयोग अनुसन्धान आयोग टङ्गाल, काठमाडौं मिति: २०८२।०३।०२ गते । प्रेस विज्ञप्ति विषय: आरोपपत्र दायर गरिएको । जिल्ला बाँके, मालपोत कार्यालय कोहलपुरको मुद्दाफाँटमा कार्यरत खरिदार हरिचन्द्र पाण्डे समेतले जग्गाको मोठ भिडाउने कार्यमा सहजीकरण गरिदिने भनी लेखापढी व्यवसायी (विचौलिया) मार्फत सेवाग्राहीसँग घुस/रिसवत माग गरिरहेको भन्ने सूचना एवं उजुरी निवेदनका आधारमा अख्तियार दुरुपयोग अनुसन्धान आयोगको कार्यालय, बुटवल, सम्पर्क कार्यालय नेपालगञ्जबाट खटिई गएको टोलीले निज खरिदार हरिचन्द्र पाण्डेलाई विचौलिया मार्फत सेवाग्राहीसँग रु.२०,०००।– (बीस हजार रुपैयाँ) घुस/रिसवत लिई तिवारी रेष्टुरेण्ट संचालक घनश्याम तिवारीलाई राख्न दिएको अवस्थामा उक्त रकमसहित निज खरिदार हरिचन्द्र पाण्डे, लेखापढी व्यवसायी गर्ने शान्ती पुन र घनश्याम तिवारीलाई नियन्त्रणमा लिइएको थियो। अनुसन्धानको सिलसिलामा मालपोत कार्यालय कोहलपुर, बाँकेका खरिदार हरिचन्द्र पाण्डे, खरिदार रतन बहादुर जि.सी., लेखापढी व्यवसाय गर्ने शान्ति पुन र घनश्याम तिवारी समेतको आपसी मिलेमतोमा जग्गाको मोठ भिडाउने कार्यमा सहजीकरण गरिदिने भनी सेवाग्राहीसँग रु.२०,०००।- घुस/रिसवत लिए लिन लगाएको हुँदा राष्ट्रसेवकहरू हरिचन्द्र पाण्डे र रतन बहादुर जि.सी.लाई भ्रष्टाचार निवारण ऐन, २०५९ को दफा ३ को उपदफा (१) बमोजिमको कसुरमा बिगो रु.२०,०००।-(बीस हजार रुपैयाँ) कायम गरी भ्रष्टाचार निवारण ऐन, २०५९ को दफा ३ को उपदफा (१) को देहाय (क) बमोजिम कैद र सोही ऐनको दफा ३ को उपदफा (१) अनुसार बिगो बमोजिम जरिवाना सजाय हुन मागदाबी लिइएको छ। साथै, प्रतिवादीहरु शान्ति पुन र घनश्याम तिवारीले सेवाग्राहीसँग घुस/रिसवत रु.२०,०००।–माग गरी गराई उक्त रकम लेखापढी व्यवसाय गर्ने शान्ति पुनले लिई खरिदार हरिचन्द्र पाण्डेलाई दिए पश्चात निज हरिचन्द्र पाण्डेले प्रतिवादी घनश्याम तिवारीलाई दिएकोमा उक्त घुस/रिसवत रकम रु.२०,000।- सहित निज घनश्याम तिवारी पक्राउ परेको देखिएकोबाट सेवाग्राहीलार्इ मोलाहिजा गरी आफ्नो लागि र राष्ट्रसेवकलार्इ उक्त रिसवत रकमको लाभ लिन दिनका निमित्त लिर्इ दिर्इ सहयोग पुर्‍याउने कार्य गरेको हुँदा निजहरु शान्ति पुन र घनश्याम तिवारी उपर भ्रष्टाचार निवारण ऐन, २०५९ को दफा ३ को उपदफा (२) बमोजिमको कसुरमा बिगो रु.२०,०००।-(बीस हजार रुपैयाँ) कायम गरी सोही ऐनको दफा 3 को उपदफा (2) ले निर्देश गरे वमोजिम भ्रष्टाचार निवारण ऐन, २०५९ को दफा ३ को उपदफा (१) को देहाय (क) बमोजिम कैद र सोही ऐनको दफा ३ को उपदफा (१) अनुसार बिगो बमोजिम जरिवाना सजाय हुन मागदाबी लिई आज विशेष अदालत, काठमाडौंमा आरोपपत्र दायर गरिएको छ। सहायक प्रवक्ता देवी प्रसाद थपलिया",
            ],
        )
        _assert_markers_absent(markdown, ["---", "काठमाड�", "प्रष्ट्रिधध"])

    def test_save_cli_writes_markdown_file_with_expected_output(
        self, tmp_path: Path
    ) -> None:
        notice_pdf = TEST_DATA_DIR / "ciaa_pressrelease_sample.pdf"
        if not notice_pdf.exists():
            pytest.skip("Notice-style PDF sample not found")

        output_path = tmp_path / "notice.md"
        exit_code = save_cli_main([str(notice_pdf), "--out", str(output_path)])
        expected_markdown = _md().convert(str(notice_pdf)).text_content

        assert exit_code == 0
        assert output_path.exists()

        markdown = output_path.read_text(encoding="utf-8")
        assert markdown == expected_markdown
        assert "विषय: आरोपपत्र दायर गररएको।" in markdown
        assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" in markdown
