"""A vision-OCR decline must never become page text, and English must survive.

Two halves. The first pins `likhit.ocr_acceptance` itself -- each leg on a case
only that leg can reach, and each of the measured false-positive families that
shaped its guards. The second pins the consumer, `_run_page_ocr`: before this,
every failure leg there was mechanical, so a model answering "This appears to be
a blank page" counted as a success and its prose became the body of a page of a
Nepali audit report.

The negative cases are the point, not decoration. The published corpus contains
18 documents with zero Devanagari characters, 13 of them scoring `clean`, and one
English publication, `11353`, delivered entirely by OCR -- all 20 of its pages
carry zero Devanagari. A fix that drops English is a worse defect than the one it
replaces.
"""

from typing import cast

import fitz
import pytest
from markitdown_ocr import LLMVisionOCRService, OCRResult

from likhit.converters.nepali_pdf import (
    NEEDS_OCR_MARKER_PATTERN,
    _needs_ocr_reason,
    _PageOcrResult,
    _run_full_page_ocr,
)
from likhit.ocr_acceptance import (
    ABSTENTION_PATTERNS,
    ASSERTS_BLANK,
    DECLINED,
    DELIVERED,
    LEG_ASSERTS_BLANK,
    LEG_NO_SUBSTANCE,
    LEG_PLACEHOLDER,
    LEG_SCRIPT_SHARE,
    _PLACEHOLDER_CELL,
    TASK_FRAME,
    classify_ocr_response,
    is_declined,
)

# The exact response shape measured on a live OCR buy: 1,554 of its rows carry no
# Devanagari at all and describe a blank page instead of transcribing one.
BLANK_COMMENTARY = (
    "This appears to be a blank page with only a solid green vertical "
    "stripe/border on the right side."
)

# A real page of the corpus's one English publication, `11353`. Zero Devanagari,
# 100% Latin, and legitimate content that must reach the transcript.
ENGLISH_PUBLICATION_PAGE = (
    "3. Glimpses of Some Audit Observations\n"
    "1. Public Accountability Status - Pursuant to compliance with public "
    "accountability, the reconstruction authority has not maintained the "
    "prescribed records of grant disbursement, and the reported physical "
    "progress could not be reconciled with the financial progress."
)

DEVANAGARI_PAGE = "लेखापरीक्षण प्रतिवेदन आर्थिक वर्ष २०६०/२०६१"


class TestLegs:
    """Each leg on a case only that leg reaches."""

    def test_blank_commentary_is_declined(self) -> None:
        acceptance = classify_ocr_response(BLANK_COMMENTARY)

        assert acceptance.verdict == DECLINED
        assert acceptance.legs == (LEG_ASSERTS_BLANK,)
        # The whole reason leg D exists: this phrase names no image, scan,
        # rotation or transcription, so it fires NO task-frame-guarded family and
        # leg A cannot see it.
        assert acceptance.families == ()
        assert TASK_FRAME.search(BLANK_COMMENTARY) is None

    def test_english_refusal_naming_the_image_is_declined_by_leg_a(self) -> None:
        text = "I cannot read this scanned image; the resolution is too low."

        acceptance = classify_ocr_response(text)

        assert acceptance.legs[0] == LEG_SCRIPT_SHARE
        assert "first_person_inability" in acceptance.families

    def test_placeholder_table_is_declined_by_leg_b(self) -> None:
        # Devanagari-rich, so leg A cannot reach it: its Devanagari IS the word
        # "unclear" repeated. 3 of 4 populated cells are placeholders.
        unclear = "अस्पष्ट"
        text = (
            f"| शिर्षक | रकम |\n"
            f"| --- | --- |\n"
            f"| [{unclear}] | [{unclear}] |\n"
            f"| [{unclear}] | १२३ |\n"
        )

        acceptance = classify_ocr_response(text)

        assert acceptance.legs == (LEG_PLACEHOLDER,)
        assert acceptance.devanagari_ratio > 0.05
        assert acceptance.placeholder_cell_share is not None
        assert acceptance.placeholder_cell_share >= 0.25

    def test_header_only_table_with_abstention_is_declined_by_leg_c(self) -> None:
        # A column header, its separator, and zero data rows -- "here is the shape
        # of what I did not transcribe". Devanagari share is far above the floor,
        # so leg A cannot reach it either.
        text = "| क्र.स. | विवरण |\n| --- | --- |\n\nयस पेजको ट्रान्सक्रिप्शन गर्न पढ्न सकिएन्।"

        acceptance = classify_ocr_response(text)

        assert LEG_NO_SUBSTANCE in acceptance.legs
        assert acceptance.empty_table_skeleton is True
        assert acceptance.devanagari_ratio > 0.05


class TestEnglishSurvives:
    """The measured false-positive families that shaped the guards."""

    def test_english_publication_page_is_delivered(self) -> None:
        acceptance = classify_ocr_response(ENGLISH_PUBLICATION_PAGE)

        assert acceptance.verdict == DELIVERED
        assert acceptance.devanagari_chars == 0
        # Shape alone would condemn it: this is exactly why leg A is a conjunction.
        assert acceptance.devanagari_ratio < 0.05

    def test_isa_disclaimer_is_delivered_because_of_the_subject_guard(self) -> None:
        # "unable to obtain sufficient appropriate audit evidence" is the standard
        # ISA disclaimer and a genuine sentence in a genuine report; "cannot be
        # identified" is ordinary audit English. Bare modality matched 41 pages of
        # the negative control.
        #
        # THE SUBJECT REQUIREMENT is what protects this one, NOT the task frame:
        # every English family pivots on a literal `I`, and this sentence has
        # none. Asserting the raw patterns miss is the point -- a version of this
        # test that only checked the verdict passed even with the frame guard
        # removed, because the frame guard was never what saved it.
        text = (
            "We were unable to obtain sufficient appropriate audit evidence "
            "about the carrying amount of inventories, and the entity cannot be "
            "identified as having maintained the prescribed records."
        )

        acceptance = classify_ocr_response(text)

        assert acceptance.verdict == DELIVERED
        assert acceptance.families == ()
        assert [
            name for name, rx in ABSTENTION_PATTERNS.items() if rx.search(text)
        ] == []

    def test_first_person_inability_away_from_the_task_frame_is_delivered(self) -> None:
        # This one DOES fire a family raw -- "I cannot" -- and is saved only by the
        # task frame: it mentions no image, scan, rotation, resolution or
        # transcription, so the model is talking about the audited entity's
        # records, not about the picture it was handed.
        text = (
            "I cannot confirm the opening balances because the entity did not "
            "produce its ledgers."
        )

        acceptance = classify_ocr_response(text)

        assert ABSTENTION_PATTERNS["first_person_inability"].search(text) is not None
        assert TASK_FRAME.search(text) is None
        assert acceptance.families == ()
        assert acceptance.verdict == DELIVERED

    def test_one_illegible_cell_among_many_is_delivered(self) -> None:
        # A page allowed to mark a single illegible stamp is doing its job. The
        # base is cells, not occurrences: on the corpus population an occurrence
        # threshold selected 7 genuine full transcriptions.
        unclear = "अस्पष्ट"
        rows = "".join(f"| विवरण {n} | १२३{n} |\n" for n in range(12))
        text = f"| शिर्षक | रकम |\n| --- | --- |\n{rows}| [{unclear}] | १० |\n"

        acceptance = classify_ocr_response(text)

        assert acceptance.verdict == DELIVERED
        assert acceptance.placeholder_cell_share is not None
        assert acceptance.placeholder_cell_share < 0.25

    def test_a_small_table_is_not_a_denominator(self) -> None:
        """🛑 A 2-cell signature table with one illegible signature is DELIVERED.

        Review's reproduction. `placeholder_cell_share` used to count populated cells
        itself, so this page -- 100% Devanagari, a full page of audit prose plus a
        signature block -- scored 0.5 and was declined, while `page_refusal`, reading
        the same page, does not refuse it because 2 < `MIN_DATA_CELLS`. A false decline
        discards the whole page and pays for the OCR call twice.

        The share is None rather than 0.0: a two-cell denominator is not a measurement,
        which is the same abstention this module already makes for a page with no cells.
        """
        prose = (
            "यस कार्यालयको आर्थिक वर्ष २०७९।८० को लेखापरीक्षण प्रतिवेदन अनुसार "
            "आन्तरिक नियन्त्रण प्रणाली कमजोर रहेको देखिन्छ। " * 5
        )
        text = prose + "\n\n| हस्ताक्षर | [अस्पष्ट] |\n"

        acceptance = classify_ocr_response(text)

        assert acceptance.verdict == DELIVERED
        assert acceptance.placeholder_cell_share is None
        assert acceptance.devanagari_ratio > 0.9

    def test_a_marker_mentioned_inside_a_prose_cell_is_not_a_placeholder_cell(
        self,
    ) -> None:
        """Review's second reproduction: occurrence is not dominance.

        Four data cells, one of them 287 characters of genuine prose that happens to
        contain `[अस्पष्ट]`. Counting occurrences gives 0.25 and declines the page;
        `page_refusal.is_placeholder_cell` requires the placeholder to DOMINATE the
        cell, which is the rule that separates a transcript refusing a cell from audit
        prose discussing ambiguity.
        """
        prose = (
            "यस कार्यालयको आर्थिक वर्ष २०७९।८० को लेखापरीक्षण प्रतिवेदन अनुसार "
            "आन्तरिक नियन्त्रण प्रणाली कमजोर रहेको देखिन्छ। " * 5
        )
        long_cell = (
            "यो विवरण निकै लामो छ र यसमा एक ठाउँमा मात्र [अस्पष्ट] लेखिएको छ "
            + "थप विवरण यहाँ छ " * 14
        )
        text = prose + f"\n\n| १ | {long_cell} | ५०००० | ठीक |\n"

        acceptance = classify_ocr_response(text)

        assert acceptance.verdict == DELIVERED
        assert acceptance.placeholder_cell_share == 0.0

    def test_a_table_of_nothing_but_placeholders_still_declines(self) -> None:
        """The other direction, so the two fixes above cannot have disabled leg B."""

        text = (
            "| क्र | रकम | कैफियत | जम्मा |\n| [अस्पष्ट] | [अस्पष्ट] | [अस्पष्ट] | [अस्पष्ट] |\n"
        )

        acceptance = classify_ocr_response(text)

        assert acceptance.verdict == DECLINED
        assert LEG_PLACEHOLDER in acceptance.legs

    def test_plain_devanagari_page_is_delivered(self) -> None:
        assert not is_declined(DEVANAGARI_PAGE)

    def test_full_devanagari_table_is_delivered(self) -> None:
        text = "| शिर्षक | रकम |\n| --- | --- |\n| राजस्व | १२३ |\n"

        assert classify_ocr_response(text).verdict == DELIVERED


class TestPatternHygiene:
    """No pasted Devanagari in any compiled pattern.

    A literal class is normalization-fragile two ways that have both bitten this
    repo: a range whose endpoint is composed can invert and raise `re.error` at
    import -- taking the module with it -- and a set of composed characters can
    silently widen to their base consonants. `\\uXXXX` escapes cannot do either,
    and the escapes survive any normalization of the source file.

    NOT REDUNDANT WITH `tests/test_regex_normalization_stability.py`, and that is
    measured rather than assumed. That file asserts the weaker, broader property --
    every non-ASCII regex in `src/` survives NFC/NFD/NFKC/NFKD -- and it does
    already collect this module's patterns. But a pasted literal that happens to
    contain no composition exclusion is normalization-STABLE, so it passes there:
    replacing one escape sequence in `ocr_acceptance` with its pasted character
    left that file green at 110 passed and failed only the test below. The
    difference matters because whether a given Devanagari word is stable is not
    something a future editor should have to know -- "never paste" is checkable,
    "paste only stable words" is not.
    """

    def test_no_devanagari_literal_in_any_pattern(self) -> None:
        patterns = {
            **{name: rx for name, rx in ABSTENTION_PATTERNS.items()},
            "TASK_FRAME": TASK_FRAME,
            "ASSERTS_BLANK": ASSERTS_BLANK,
            "_PLACEHOLDER_CELL": _PLACEHOLDER_CELL,
        }
        offenders = {
            name: [ch for ch in rx.pattern if "ऀ" <= ch <= "ॿ"]
            for name, rx in patterns.items()
        }
        assert {n: chars for n, chars in offenders.items() if chars} == {}

    def test_nepali_families_still_match_their_own_words(self) -> None:
        """The escapes are not vacuous -- guard against a mangled rewrite."""
        cases = {
            "np_not_readable": "पढ्न सकिएन",
            "np_not_identifiable": ("पहिचान गर्न सकिएन"),
            "np_illegible": "अपठनीय",
        }
        for name, sample in cases.items():
            assert ABSTENTION_PATTERNS[name].search(sample) is not None, name


class TestConsumerDoesNotDeliverADecline:
    """`_run_page_ocr` must not insert a decline under a page anchor."""

    def test_declined_page_is_not_inserted_and_is_marked(self) -> None:
        raw = _blank_pdf(page_count=2)
        service = _StubOCRService(
            [
                OCRResult(text=BLANK_COMMENTARY),
                OCRResult(text=DEVANAGARI_PAGE),
            ]
        )

        result = _run_full_page_ocr(raw, cast(LLMVisionOCRService, service))

        assert result is not None
        # The decline is nowhere in the body, and the page that did deliver is.
        assert "blank page" not in result.markdown
        assert "green vertical" not in result.markdown
        assert DEVANAGARI_PAGE in result.markdown
        marker = NEEDS_OCR_MARKER_PATTERN.search(result.markdown)
        assert marker is not None
        assert marker.groups() == ("1", "ocr-declined")

    def test_every_page_declined_yields_no_candidate(self) -> None:
        raw = _blank_pdf(page_count=2)
        service = _StubOCRService(
            [OCRResult(text=BLANK_COMMENTARY), OCRResult(text=BLANK_COMMENTARY)]
        )

        result = _run_full_page_ocr(raw, cast(LLMVisionOCRService, service))

        # No page delivered anything, so there is no OCR candidate at all and the
        # caller falls back rather than publishing a document of commentary.
        assert result is None

    def test_english_page_is_still_delivered_by_the_consumer(self) -> None:
        raw = _blank_pdf(page_count=1)
        service = _StubOCRService([OCRResult(text=ENGLISH_PUBLICATION_PAGE)])

        result = _run_full_page_ocr(raw, cast(LLMVisionOCRService, service))

        assert result is not None
        assert "Glimpses of Some Audit Observations" in result.markdown
        assert NEEDS_OCR_MARKER_PATTERN.search(result.markdown) is None

    def test_mixed_failure_kinds_report_the_wider_reason(self) -> None:
        raw = _blank_pdf(page_count=3)
        service = _StubOCRService(
            [
                OCRResult(text="", error="provider rejected image"),
                OCRResult(text=BLANK_COMMENTARY),
                OCRResult(text=DEVANAGARI_PAGE),
            ]
        )

        result = _run_full_page_ocr(raw, cast(LLMVisionOCRService, service))

        assert result is not None
        marker = NEEDS_OCR_MARKER_PATTERN.search(result.markdown)
        assert marker is not None
        # `ocr-declined` would be false of page 1, so the wider label is used and
        # both pages are still listed.
        assert marker.groups() == ("1,2", "ocr-failed")


class TestNeedsOcrReason:
    def test_all_declined_reports_declined(self) -> None:
        run = _PageOcrResult({}, failed_pages=(2, 5), declined_pages=(2, 5))

        assert _needs_ocr_reason(run) == "ocr-declined"

    def test_any_mechanical_failure_reports_failed(self) -> None:
        run = _PageOcrResult({}, failed_pages=(2, 5), declined_pages=(5,))

        assert _needs_ocr_reason(run) == "ocr-failed"

    def test_no_declines_reports_failed(self) -> None:
        run = _PageOcrResult({}, failed_pages=(2,))

        assert _needs_ocr_reason(run) == "ocr-failed"


class _StubOCRService:
    def __init__(self, results: list[OCRResult]) -> None:
        self.results = iter(results)

    def extract_text(self, _image_stream: object) -> OCRResult:
        return next(self.results)


def _blank_pdf(page_count: int) -> bytes:
    document = fitz.open()
    try:
        for _ in range(page_count):
            document.new_page(width=200, height=300)
        return document.tobytes()
    finally:
        document.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
