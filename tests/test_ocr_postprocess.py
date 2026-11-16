"""Multi-pass OCR correction: the rules, the cross-engine check, the re-read, and the
opt-in hook in `_run_page_ocr`.

The rule cases are the step-1 unit tests of the JAW-51 benchmark (real misreads from
Azure Read and Claude on scanned Attorney General charge sheets), ported unchanged
in meaning. The consumer tests pin the contract that matters most for existing
users: with no correction configured, `_run_page_ocr` output is byte-identical.
"""

from typing import cast

import fitz
import pytest
from markitdown_ocr import LLMVisionOCRService, OCRResult

from likhit.converters.nepali_pdf import (
    OcrCorrection,
    _ocr_correction_from_env,
    _run_page_ocr,
)
from likhit.ocr_postprocess import (
    NUMBER_WORD_COLLISIONS,
    Disagreement,
    apply_rereads,
    check_amounts,
    cross_check,
    normalise_digit_script,
    normalise_numbers,
    ocr_search_copy,
    parse_amount_words,
    parse_reread_answer,
    postprocess,
    remove_loops,
    repair_dates,
    reread_prompt,
    value_shape_score,
)


def _fix(s: str) -> str:
    return repair_dates(s)[0]


class TestDates:
    def test_danda_read_as_digit(self) -> None:
        assert _fix("मिति 2078701117 गते") == "मिति 2078।01।17 गते"
        assert _fix("मिति २०७८७०१११७ गते") == "मिति २०७८।०१।१७ गते"
        assert _fix("मिति 2078111।20)को") == "मिति 2078।11।20)को"
        assert _fix("मिति 2078 1 091 20 को") == "मिति 2078।09।20 को"
        assert _fix("मिति 2082105126 2 27 गते") == "मिति 2082।05।26 2 27 गते"

    def test_dropped_and_rare_separators(self) -> None:
        assert _fix("मिति 20798113 गते") == "मिति 2079।8।13 गते"
        assert _fix("मिति 208066116 गतेको") == "मिति 2080।6।16 गतेको"
        assert _fix("मिति २०७८ / १२२८ गते") == "मिति २०७८/१२/२८ गते"

    def test_fiscal_year(self) -> None:
        assert _fix("आ.व.2081182 सालको") == "आ.व.2081/82 सालको"
        assert _fix("सम्बत 2082883 सालको") == "सम्बत 2082/83 सालको"

    @pytest.mark.parametrize(
        "s",
        [
            "मिति 2080/02/05 गते",
            "मिति २०७८।०१।१७ गते",
            "सम्वत् 2081/082 सालको",
            "आ.व. २०८२/०८२ को",
            "मिति २०७९।१।०१।१",
            "मो.नं. 9849005858",
        ],
    )
    def test_leaves_good_values_alone(self, s: str) -> None:
        assert _fix(s) == s

    def test_ambiguous_is_flagged_not_guessed(self) -> None:
        out, flags = repair_dates("मिति 207811117 गते")
        assert out == "मिति 207811117 गते"
        assert flags[0].action == "flag"

    def test_needs_context_or_strong_shape(self) -> None:
        out, flags = repair_dates("2 19 208018118 त्रियुगा")
        assert out == "2 19 208018118 त्रियुगा"
        assert flags[0].replacement == "2080।8।18"
        assert _fix("कागजातहरु 2082107114 विवरण") == "कागजातहरु 2082।07।14 विवरण"

    def test_implausible_years_untouched(self) -> None:
        assert _fix("दर्ता मिति: 2009108106") == "दर्ता मिति: 2009108106"


class TestAmounts:
    def test_words(self) -> None:
        r = parse_amount_words(
            "दुई करोड अठ्ठाईस लाख सत्तसठ्ठी हजार तीन सय बाइस रुपैया पन्ध पैसा"
        )
        assert r is not None and r[:2] == (22867322, 15)
        r = parse_amount_words("बैसाठीलाख असि हजार नौसय तिरानव्वे मात्र")
        assert r is not None and r[0] == 6280993
        r = parse_amount_words("35 लाख 63 हजार 7 सय सरसठी रुपैया 95")
        assert r is not None and r[:2] == (3563767, 95)
        r = parse_amount_words("एघार लाख तेत्तिस हजार चार सय उनान्नव्बे पैसा बयालिस")
        assert r is not None and r[:2] == (1133489, 42)

    def test_number_words_do_not_collide(self) -> None:
        assert NUMBER_WORD_COLLISIONS == {}

    def test_decimal_danda(self) -> None:
        s = "रु 1,66,983125 (एक लाख छैसठी हजार नौ सय त्रीयासी रुपैया पच्चीस पैसा)"
        out, _ = check_amounts(s, correct="decimal")
        assert "1,66,983।25" in out
        out, flags = check_amounts(s, correct=False)
        assert out == s
        assert flags[0].action == "flag"

    def test_single_digit_mismatch_is_flag_only_by_default(self) -> None:
        s = "रु.62,80,093। (बैसाठीलाख असि हजार नौसय तिरानव्वे मात्र)"
        out, flags = check_amounts(s, correct="decimal")
        assert out == s
        assert flags[0].replacement == "62,80,993"

    def test_matching_amount_is_silent(self) -> None:
        assert check_amounts("रु. 2,25,000/- (दुई लाख पच्चीस हजार रुपैया)")[1] == []


class TestNumbers:
    @pytest.mark.parametrize(
        ("s", "expected"),
        [
            ("उजुरीमा उ.द.नं. ८-०६३२०४, मिति", "उजुरीमा उ.द.नं. C-०६३२०४, मिति"),
            ("दर्ता नं. ०८२-०२-००७६ मा", "दर्ता नं. ०८२-CR-००७६ मा"),
            (
                "(मुद्दा नं .: ०८०-२१-०९८७)",
                "(मुद्दा नं .: ०८०-२१-०९८७)",
            ),  # ambiguous: flag only
            ("७१/०८१८८२, मिति", "७१/०८१/८२, मिति"),
            ("दफा३२ को उपदफा( १ ) को देहाय ( ख )", "दफा ३२ को उपदफा (१) को देहाय (ख)"),
        ],
    )
    def test_numbers(self, s: str, expected: str) -> None:
        assert normalise_numbers(s)[0] == expected


class TestDigitScript:
    def test_devanagari_context_only(self) -> None:
        s = "मिति 2080/01/25 गते सम्मको रकम रु. 5,000 बुझाएको\nDate : 2080/01/25 (BS) Account 0450000002760"
        out = normalise_digit_script(s)[0]
        assert "मिति २०८०/०१/२५" in out
        assert "Date : 2080/01/25 (BS) Account 0450000002760" in out
        assert ocr_search_copy(out) == ocr_search_copy(s)

    def test_latin_tokens_kept(self) -> None:
        out = normalise_digit_script(
            "मुद्दा नं. 082-CR-0076 च्यासिस नं. ME373C5FHSN000827 भएको गाडी नम्बर 12"
        )[0]
        assert "082-CR-0076" in out
        assert "ME373C5FHSN000827" in out
        assert "नम्बर १२" in out


class TestLoops:
    def test_loop_collapsed(self) -> None:
        s = "सुरु " + "नेपाल सरकार विरुद्ध " * 8 + "अन्त्य"
        out, flags = remove_loops(s)
        assert out.count("नेपाल सरकार विरुद्ध") == 1
        assert flags[0].rule == "loop"

    def test_table_cells_are_not_loops(self) -> None:
        s = "| | | | | | | | | | | |\n" * 3 + "| १ | २ |"
        assert remove_loops(s)[0] == s


class TestPipeline:
    def test_postprocess_chains_rules_and_keeps_search_copy(self) -> None:
        r = postprocess("मिति 2078701117 गते")
        assert r.text == "मिति २०७८।०१।१७ गते"
        assert r.search_text == "मिति 2078।01।17 गते"
        assert any(f.rule == "date" and f.action == "fixed" for f in r.flags)


class TestValueShape:
    @pytest.mark.parametrize(
        "v",
        [
            "२०७८।०१।१७",
            "2080/6/10",
            "१,७५,०००",
            "519,155,461.27",
            "०८२-CR-००७६",
            "२०८१/०८२",
        ],
    )
    def test_well_formed(self, v: str) -> None:
        assert value_shape_score(v) == 1

    @pytest.mark.parametrize("v", ["2078701117", "2078/13/40", "1,7,5000", "20O8"])
    def test_damaged(self, v: str) -> None:
        assert value_shape_score(v) == -1

    def test_not_a_value(self) -> None:
        assert value_shape_score("नेपाल") == 0


class TestCrossCheck:
    def test_better_validating_second_reading_wins(self) -> None:
        r = cross_check("मिति 2078701117 गते सम्म", "मिति २०७८।०१।१७ गते सम्म")
        assert r.text == "मिति २०७८।०१।१७ गते सम्म"
        assert r.fixed and not r.disagreements

    def test_equal_shapes_become_a_disagreement_not_a_guess(self) -> None:
        r = cross_check("खाता नं. ०४५०००००२७६० मा", "खाता नं. ०४५००००००२७६० मा")
        assert r.text == "खाता नं. ०४५०००००२७६० मा"
        assert [(d.primary, d.secondary) for d in r.disagreements] == [
            ("०४५०००००२७६०", "०४५००००००२७६०")
        ]
        d = r.disagreements[0]
        assert r.text[d.start : d.end] == "०४५०००००२७६०"

    def test_spacing_only_differences_are_not_disputes(self) -> None:
        r = cross_check("रु. ३, ८५,०००/- बुझाएको", "रु. ३,८५,०००।- बुझाएको")
        assert r.text == "रु. ३, ८५,०००/- बुझाएको"
        assert not r.fixed and not r.disagreements

    def test_word_span_needs_a_third_vote(self) -> None:
        primary, second = "प्रतिवादी राम काकी", "प्रतिवादी राम कार्की"
        assert cross_check(primary, second).text == primary
        assert (
            cross_check(primary, second, third="प्रतिवादी राम कार्की को").text == second
        )

    def test_untouched_lines_are_byte_identical(self) -> None:
        primary = "| क | ख |\n|---|---|\nमिति 2078701117 गते"
        r = cross_check(primary, "| क | ख |\n|---|---|\nमिति २०७८।०१।१७ गते")
        assert r.text.split("\n")[:2] == ["| क | ख |", "|---|---|"]


class TestReviewFindings:
    """Cases raised in review of the likhit port (PR #126)."""

    def test_fiscal_year_needs_context(self) -> None:
        out, flags = repair_dates("खाता नं. 2081182")
        assert out == "खाता नं. 2081182"
        assert flags[0].action == "flag" and flags[0].replacement == "2081/82"

    def test_repeated_digit_coefficients(self) -> None:
        r = parse_amount_words("11 लाख")
        assert r is not None and r[0] == 1100000
        r = parse_amount_words("22 हजार 333")
        assert r is not None and r[0] == 22333

    def test_non_decimal_digits_do_not_raise(self) -> None:
        postprocess("रु. 100 (² रुपैयाँ)")
        postprocess("मिति 2078²1117 गते")
        assert parse_reread_answer('{"²": "x", "1": "y"}') == {1: "y"}

    def test_rupee_word_closes_the_rupee_part(self) -> None:
        assert parse_amount_words("एक लाख रुपैयाँ पच्चीस पैसा") == (100000, 25, False)

    def test_amount_decimals_are_compared_exactly(self) -> None:
        r = cross_check("रु. 1,000.01 मात्र", "रु. 1,000.1 मात्र")
        assert [(d.primary, d.secondary) for d in r.disagreements] == [
            ("1,000.01", "1,000.1")
        ]

    def test_a_word_never_replaces_a_value(self) -> None:
        r = cross_check("मिति 2078701117 गते", "मिति स्वीकृत गते")
        assert r.text == "मिति 2078701117 गते"

    def test_column_whitespace_is_kept_on_a_corrected_line(self) -> None:
        primary = "१    मिति 2078701117 गते      बुझेको\nदोस्रो पङ्क्ति"
        r = cross_check(primary, "१ मिति २०७८।०१।१७ गते बुझेको\nदोस्रो पङ्क्ति")
        assert r.text == "१    मिति २०७८।०१।१७ गते      बुझेको\nदोस्रो पङ्क्ति"

    def test_replacement_never_crosses_a_column_gap(self) -> None:
        primary = "मिति 2078701117    2079701117 गते"
        r = cross_check(primary, "मिति २०७८।०१।१७ २०७९।०१।१७ गते")
        assert "    " in r.text

    def test_reread_answer_goes_to_the_disputed_occurrence(self) -> None:
        r = cross_check("पहिलो 100 दोस्रो 100 जना", "पहिलो 100 दोस्रो 200 जना")
        assert len(r.disagreements) == 1
        text, left = apply_rereads(r.text, r.disagreements, {1: "200"})
        assert text == "पहिलो 100 दोस्रो 200 जना"
        assert left == []

    def test_unanswered_positions_follow_earlier_edits(self) -> None:
        r = cross_check("क 2,00,000 ख 3,00,000 ग", "क 2,50,000 ख 3,80,000 ग")
        assert len(r.disagreements) == 2
        text, left = apply_rereads(r.text, r.disagreements, {1: "2,50,000"})
        assert text == "क 2,50,000 ख 3,00,000 ग"
        assert [text[d.start : d.end] for d in left] == ["3,00,000"]


class TestReread:
    def test_prompt_lists_every_dispute(self) -> None:
        p = reread_prompt(
            [Disagreement("१,५०,०००", "१,८०,०००"), Disagreement("A", "B")]
        )
        assert "1. A: «१,५०,०००»  B: «१,८०,०००»" in p
        assert "2. A: «A»" in p

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ('{"1": "१,५०,०००", "2": null}', {1: "१,५०,०००"}),
            ('Here you go:\n{"1": "x"}\n{"2": "y"}', {1: "x"}),
            ("no json at all", {}),
            ('["1"]', {}),
        ],
    )
    def test_parse_answer(self, answer: str, expected: dict[int, str]) -> None:
        assert parse_reread_answer(answer) == expected

    def test_apply_keeps_unanswered(self) -> None:
        d = [Disagreement("१,५०,०००", "१,८०,०००"), Disagreement("२४", "३४")]
        text, left = apply_rereads("रु. १,५०,००० र २४ जना", d, {1: "१,८०,०००"})
        assert text == "रु. १,८०,००० र २४ जना"
        assert [(x.primary, text[x.start : x.end]) for x in left] == [("२४", "२४")]

    def test_ambiguous_unlocated_span_is_left_alone(self) -> None:
        text, left = apply_rereads(
            "१०० र १००", [Disagreement("१००", "२००")], {1: "२००"}
        )
        assert text == "१०० र १००"
        assert len(left) == 1


# ---------------------------------------------------------------------------
# The consumer: `_run_page_ocr`
# ---------------------------------------------------------------------------
PAGE = "मिति 2078701117 गते खाता नं. ०४५०००००२७६० मा जम्मा"


class _StubOCRService:
    def __init__(self, page: str, reread: str = "{}") -> None:
        self.page, self.reread_answer = page, reread
        self.prompts: list[str | None] = []

    def extract_text(
        self, _image_stream: object, prompt: str | None = None
    ) -> OCRResult:
        self.prompts.append(prompt)
        return OCRResult(text=self.page if prompt is None else self.reread_answer)


def _blank_pdf() -> bytes:
    document = fitz.open()
    try:
        document.new_page(width=200, height=300)
        return document.tobytes()
    finally:
        document.close()


def _run(service: _StubOCRService, correction: OcrCorrection | None) -> str:
    run = _run_page_ocr(
        _blank_pdf(), cast(LLMVisionOCRService, service), (1,), correction=correction
    )
    return run.text_by_page[1]


class TestConsumer:
    def test_default_is_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (
            "LIKHIT_OCR_POSTPROCESS",
            "LIKHIT_OCR_SECOND_READER",
            "LIKHIT_OCR_REREAD",
        ):
            monkeypatch.delenv(name, raising=False)
        service = _StubOCRService(PAGE)
        assert _run(service, None) == PAGE
        assert service.prompts == [None]  # exactly one call, the transcription

    def test_rules_only(self) -> None:
        out = _run(_StubOCRService(PAGE), OcrCorrection(rules=True))
        assert "२०७८।०१।१७" in out
        assert "likhit:ocr-review" not in out

    def test_second_reading_dispute_is_marked_for_review(self) -> None:
        second = PAGE.replace("०४५०००००२७६०", "०४५००००००२७६०")
        out = _run(
            _StubOCRService(PAGE), OcrCorrection(second_reader=lambda _png: second)
        )
        assert "०४५०००००२७६०" in out  # primary kept, never guessed
        assert out.endswith("<!-- likhit:ocr-review spans=1 -->")

    def test_reread_settles_the_dispute(self) -> None:
        second = PAGE.replace("०४५०००००२७६०", "०४५००००००२७६०")
        service = _StubOCRService(PAGE, reread='{"1": "०४५००००००२७६०"}')
        out = _run(
            service, OcrCorrection(second_reader=lambda _png: second, reread=True)
        )
        assert "०४५००००००२७६०" in out
        assert "likhit:ocr-review" not in out
        assert len(service.prompts) == 2 and service.prompts[1] is not None

    def test_failing_second_reader_keeps_the_page(self) -> None:
        def boom(_png: bytes) -> str:
            raise RuntimeError("tesseract crashed")

        assert _run(_StubOCRService(PAGE), OcrCorrection(second_reader=boom)) == PAGE

    def test_env_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIKHIT_OCR_POSTPROCESS", "1")
        monkeypatch.setenv("LIKHIT_OCR_SECOND_READER", "tesseract")
        monkeypatch.setenv("LIKHIT_OCR_REREAD", "yes")
        c = _ocr_correction_from_env()
        assert c.rules and c.reread and c.second_reader is not None
        monkeypatch.setenv("LIKHIT_OCR_SECOND_READER", "something-else")
        assert _ocr_correction_from_env().second_reader is None
