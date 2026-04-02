"""Sample PDF regression coverage for the local ``samples/`` directory."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from markitdown import MarkItDown
import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = ROOT / "samples"
_REPLACEMENT_CHAR = "\ufffd"
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SamplePdfExpectation:
    file_name: str
    required_markers: tuple[str, ...] = ()
    forbidden_markers: tuple[str, ...] = ()
    ordered_markers: tuple[str, ...] = ()
    min_nonempty_lines: int = 5
    # Coarse smoke/shape signal only; not a correctness metric.
    min_characters: int = 200
    max_replacement_chars: int = 0


@dataclass(frozen=True)
class SampleCase:
    expectation: SamplePdfExpectation
    known_broken: bool = False
    xfail_reason: str | None = None


@dataclass(frozen=True)
class ConvertedSample:
    expectation: SamplePdfExpectation
    raw_markdown: str
    normalized_markdown: str
    raw_nonempty_lines: list[str]
    replacement_char_count: int

    @property
    def preview(self) -> str:
        return "\n".join(self.raw_nonempty_lines[:12])


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\u00a0", " ")
    normalized = normalized.replace("\u200b", "")
    normalized = normalized.replace("–", "-")
    normalized = normalized.replace("—", "-")
    normalized = normalized.replace("। ", "।")
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def _normalize_lines(markdown: str) -> list[str]:
    return [line.strip() for line in markdown.splitlines() if line.strip()]


def _marker_present(marker: str, normalized_markdown: str) -> bool:
    return _normalize_text(marker) in normalized_markdown


def _matched_markers(sample: ConvertedSample) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for marker in sample.expectation.required_markers:
        if _marker_present(marker, sample.normalized_markdown):
            matched.append(marker)
        else:
            missing.append(marker)
    return matched, missing


def _build_failure_context(sample: ConvertedSample) -> str:
    return (
        f"file={sample.expectation.file_name}\n"
        f"nonempty_lines={len(sample.raw_nonempty_lines)}\n"
        f"characters={len(sample.raw_markdown)}\n"
        f"replacement_chars={sample.replacement_char_count}\n"
        f"preview=\n{sample.preview}"
    )


STABLE_SAMPLE_CASES = (
    SampleCase(
        expectation=SamplePdfExpectation(
            file_name="kanunpatrika.pdf",
            required_markers=(
                "नेपाल कानून पत्रिका द्दण्टछ, अंक ट",
                "निर्णय नं.७९७३ ने.का.प. २०६५",
                "बिषयः– नेपालको अन्तरिम संविधान २०६३",
                "- अपराध गर्ने व्यक्तिको पीडितसंगको",
                "जवर्जस्ती करणीको महलमा भएको",
                "प्रचलित मुलुकी ऐन, २०२० को",
            ),
            ordered_markers=(
                "नेपाल कानून पत्रिका द्दण्टछ, अंक ट",
                "निर्णय नं.७९७३ ने.का.प. २०६५",
                "सर्बोच्च अदालत विशेष इजलास",
                "सम्माननीय प्रधानन्यायाधीश श्री केदारप्रसाद",
                "गरिपाऊँ।",
            ),
            min_nonempty_lines=25,
            min_characters=2000,
            max_replacement_chars=1,
        )
    ),
    SampleCase(
        expectation=SamplePdfExpectation(
            file_name="pressrelease.pdf",
            required_markers=(
                "अख्तियार दुरुपयोग अनुसन्धान आयोग",
                "टङ्गाल, काठमाडौं",
                "विषय: आरोपपत्र दायर गररएको।",
                "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा",
                "आह्वान गरिएको बोलपत्र",
                "प्रवक्ता",
                "नरहरि घिमिरे",
            ),
            forbidden_markers=(
                "प्रष्ट्रिधध",
                "काठमाड�",
            ),
            ordered_markers=(
                "अख्तियार दुरुपयोग अनुसन्धान आयोग",
                "टङ्गाल, काठमाडौं",
                "विषय: आरोपपत्र दायर गररएको।",
                "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा",
            ),
            min_nonempty_lines=15,
            min_characters=2000,
        )
    ),
)

# Known-broken samples:
# - should still convert to non-empty output unless otherwise noted
# - are expected to fail one or more quality assertions until extraction improves
# - should be moved into the stable set once all regression checks pass reliably
KNOWN_BROKEN_SAMPLE_CASES = (
    SampleCase(
        expectation=SamplePdfExpectation(
            file_name="Press Release.pdf",
            required_markers=(
                "अख्तियार दुरुपयोग अनुसन्धान आयोग",
                "टङ्गाल, काठमाडौं",
                "प्रेस विज्ञप्ति",
                "विषय: आरोपपत्र दायर गरिएको।",
                "मधेस प्रदेश, धनुषा जिल्ला प्रदेश जनस्वास्थ्य प्रयोगशाला, जनकपुरधामबाट",
            ),
            min_nonempty_lines=12,
            min_characters=1500,
        ),
        known_broken=True,
        xfail_reason="Current extraction still misreads the heading and subject lines in this sample.",
    ),
    SampleCase(
        expectation=SamplePdfExpectation(
            file_name="aarop-patra.pdf",
            required_markers=(
                "श्री विशेष अदालत, काठमाडौं समक्ष पेस गरेको",
                "आरोप-पत्र",
                "मुद्दा: घुस ररसवत लिई दिई भ्रष्टाचार गरेको।",
                "NITC/G/NCB-7-074/75",
            ),
            min_nonempty_lines=20,
            min_characters=5000,
        ),
        known_broken=True,
        xfail_reason="Current accusation-letter extraction is readable but still not accurate enough end to end.",
    ),
    SampleCase(
        expectation=SamplePdfExpectation(
            file_name="my-table.pdf",
            required_markers=(
                "गैरकानुनी लाभ वा हातनिोक्सानी गरी भ्रष्टाचार गरेका मुद्दा",
                "उजुरीको व्यहोरा",
                "अनुसन्धानबाट पुष्टि भएको व्यहोरा",
                "प्रतिवादीको नाम, पद र कार्यालय",
            ),
            min_nonempty_lines=12,
            min_characters=1500,
        ),
        known_broken=True,
        xfail_reason="Current table extraction for this annual-report slice still contains remapping errors.",
    ),
    SampleCase(
        expectation=SamplePdfExpectation(
            file_name="nirnaya.pdf",
            required_markers=(
                "नेपाल कानून पत्रिका",
                "निर्णय नं.७९७३",
                "सर्बोच्च अदालत विशेष इजलास",
                "बिषयः– नेपालको अन्तरिम संविधान २०६३",
            ),
            min_nonempty_lines=20,
            min_characters=1000,
        ),
        known_broken=True,
        xfail_reason="Current extraction for this sample is still garbled and should fail until repaired.",
    ),
    SampleCase(
        expectation=SamplePdfExpectation(
            file_name="table.pdf",
            required_markers=(
                "आयोगको निर्णय मिति",
                "आरोपपत्र दायर मिति",
                "राष्ट्रसेवक प्रतिवादीको नाम, पद र कार्यालय",
                "बिगो (रु.)",
            ),
            min_nonempty_lines=10,
            min_characters=1000,
        ),
        known_broken=True,
        xfail_reason="Current extraction still leaves this legacy-font table header partially garbled.",
    ),
)

ALL_SAMPLE_CASES = STABLE_SAMPLE_CASES + KNOWN_BROKEN_SAMPLE_CASES
ALL_EXPECTED_SAMPLE_FILES = sorted(
    case.expectation.file_name for case in ALL_SAMPLE_CASES
)


def _case_id(case: SampleCase) -> str:
    return case.expectation.file_name


@lru_cache(maxsize=None)
def _load_markdown(file_name: str) -> str:
    sample_path = SAMPLES_DIR / file_name
    assert sample_path.exists(), f"Missing sample PDF: {sample_path}"
    md = MarkItDown(enable_plugins=True)
    return md.convert(str(sample_path)).text_content


def _convert_sample(case: SampleCase) -> ConvertedSample:
    expectation = case.expectation
    sample_path = SAMPLES_DIR / expectation.file_name
    assert sample_path.exists(), f"Missing sample PDF: {sample_path}"

    raw_markdown = _load_markdown(expectation.file_name)
    normalized_markdown = _normalize_text(raw_markdown)
    raw_nonempty_lines = _normalize_lines(raw_markdown)
    return ConvertedSample(
        expectation=expectation,
        raw_markdown=raw_markdown,
        normalized_markdown=normalized_markdown,
        raw_nonempty_lines=raw_nonempty_lines,
        replacement_char_count=raw_markdown.count(_REPLACEMENT_CHAR),
    )


def _known_broken_params() -> list[pytest.ParameterSet]:
    return [
        pytest.param(
            case,
            id=_case_id(case),
            marks=pytest.mark.xfail(
                reason=case.xfail_reason or "Known broken sample",
                strict=True,
            ),
        )
        for case in KNOWN_BROKEN_SAMPLE_CASES
    ]


def _assert_sample_shape(sample: ConvertedSample) -> None:
    expectation = sample.expectation
    assert len(sample.raw_nonempty_lines) >= expectation.min_nonempty_lines, (
        f"Too few non-empty lines for {expectation.file_name}\n"
        f"{_build_failure_context(sample)}"
    )
    assert len(sample.raw_markdown) >= expectation.min_characters, (
        f"Too few characters for {expectation.file_name}\n"
        f"{_build_failure_context(sample)}"
    )


def _assert_no_known_artifacts(sample: ConvertedSample) -> None:
    expectation = sample.expectation
    assert sample.replacement_char_count <= expectation.max_replacement_chars, (
        f"Too many replacement characters for {expectation.file_name}\n"
        f"{_build_failure_context(sample)}"
    )
    for marker in expectation.forbidden_markers:
        assert not _marker_present(marker, sample.normalized_markdown), (
            f"Unexpected artifact {marker!r} found in "
            f"{expectation.file_name}\n"
            f"{_build_failure_context(sample)}"
        )


def _assert_required_markers_present(sample: ConvertedSample) -> None:
    matched, missing = _matched_markers(sample)
    assert not missing, (
        f"Missing expected markers for {sample.expectation.file_name}.\n"
        f"Matched markers: {matched}\n"
        f"Missing markers: {missing}\n"
        f"{_build_failure_context(sample)}"
    )


def _assert_ordered_markers(sample: ConvertedSample) -> None:
    expectation = sample.expectation
    if not expectation.ordered_markers:
        return

    normalized_lines = [_normalize_text(line) for line in sample.raw_nonempty_lines]
    cursor = 0
    for marker in expectation.ordered_markers:
        normalized_marker = _normalize_text(marker)
        while (
            cursor < len(normalized_lines)
            and normalized_marker not in normalized_lines[cursor]
        ):
            cursor += 1
        assert cursor < len(normalized_lines), (
            f"Ordered marker not found in sequence for {expectation.file_name}: {marker!r}\n"
            f"{_build_failure_context(sample)}"
        )
        cursor += 1


class TestSamplePdfRegistry:
    """Keep sample coverage explicit and exhaustive."""

    def test_every_sample_pdf_has_an_expectation_case(self) -> None:
        sample_files = sorted(path.name for path in SAMPLES_DIR.glob("*.pdf"))
        assert sample_files == ALL_EXPECTED_SAMPLE_FILES

    def test_known_broken_cases_have_reasons(self) -> None:
        for case in KNOWN_BROKEN_SAMPLE_CASES:
            assert case.xfail_reason and case.xfail_reason.strip(), (
                f"Known-broken case is missing an xfail reason: "
                f"{case.expectation.file_name}"
            )


class TestSamplePdfRegression:
    """Corpus-wide governance checks for the local sample PDF set."""

    @pytest.mark.parametrize("case", ALL_SAMPLE_CASES, ids=_case_id)
    def test_conversion_smoke(self, case: SampleCase) -> None:
        sample = _convert_sample(case)
        assert sample.raw_markdown.strip(), _build_failure_context(sample)

    @pytest.mark.parametrize("case", ALL_SAMPLE_CASES, ids=_case_id)
    def test_minimum_shape(self, case: SampleCase) -> None:
        sample = _convert_sample(case)
        _assert_sample_shape(sample)

    @pytest.mark.parametrize("case", STABLE_SAMPLE_CASES, ids=_case_id)
    def test_stable_expected_markers(self, case: SampleCase) -> None:
        sample = _convert_sample(case)
        _assert_required_markers_present(sample)

    @pytest.mark.parametrize("case", STABLE_SAMPLE_CASES, ids=_case_id)
    def test_corruption_artifacts(self, case: SampleCase) -> None:
        sample = _convert_sample(case)
        _assert_no_known_artifacts(sample)

    @pytest.mark.parametrize("case", STABLE_SAMPLE_CASES, ids=_case_id)
    def test_stable_ordered_markers(self, case: SampleCase) -> None:
        sample = _convert_sample(case)
        _assert_ordered_markers(sample)

    @pytest.mark.parametrize("case", _known_broken_params())
    def test_known_broken_quality_gaps_stay_visible(self, case: SampleCase) -> None:
        sample = _convert_sample(case)
        _assert_required_markers_present(sample)
