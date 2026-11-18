"""Surya 2.5 as a page-OCR engine (`likhit.surya_ocr`).

No network: the server is a stub client that returns answers recorded from a real
Surya 2.5 server (llama.cpp, Q8_0) on JAW-51 gold pages, trimmed to the parts with no
personal data. The pipeline tests pin what this engine must NOT change: a decline is
still a failed page, and a digit dispute with Tesseract is flagged, never guessed.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import base64
import io

import fitz
from markitdown_ocr import LLMVisionOCRService
import pytest

from likhit.converters.nepali_pdf import (
    OcrCorrection,
    _build_ocr_service,
    _run_page_ocr,
)
from likhit.surya_ocr import (
    SURYA_DEFAULT_MODEL,
    SURYA_PROMPT,
    SuryaOCRService,
    surya_answer_to_text,
    surya_image_size,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "surya"
COMPACT = (FIXTURES / "charge_sheet_heading.compact.txt").read_text(encoding="utf-8")
TABLE = (FIXTURES / "balance_sheet_table.html").read_text(encoding="utf-8")


class _Completions:
    def __init__(self, answer: str, finish_reason: str = "stop") -> None:
        self.answer, self.finish_reason = answer, finish_reason
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.answer)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=self.finish_reason)]
        )


class _Client:
    def __init__(self, answer: str, finish_reason: str = "stop") -> None:
        self.completions = _Completions(answer, finish_reason)
        self.chat = SimpleNamespace(completions=self.completions)


def _pdf(width: float = 595, height: float = 842) -> bytes:
    document = fitz.open()
    try:
        document.new_page(width=width, height=height)
        return document.tobytes()
    finally:
        document.close()


class TestImageSize:
    # Expected values are surya-ocr 0.24's own scale_to_fit on images of these sizes.
    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            ((1631, 2331), (1624, 2324)),  # a 200-DPI gold page
            ((2480, 3508), (2100, 2968)),  # A4 at likhit's 300 DPI: scaled down
            ((3508, 2480), (2968, 2100)),
            ((600, 800), (588, 812)),
            ((200, 300), (196, 308)),
            ((5000, 40), (5012, 28)),
        ],
    )
    def test_matches_suryas_scale_to_fit(
        self, size: tuple[int, int], expected: tuple[int, int]
    ) -> None:
        assert surya_image_size(*size) == expected


class TestAnswerToText:
    def test_compact_headers_become_blocks(self) -> None:
        text = surya_answer_to_text(COMPACT)
        assert "<|" not in text and "<" not in text
        assert "## श्री भक्तपुर जिल्ला अदालतमा" in text
        assert "### अभियोग पत्र" in text
        assert "आ.व. २०८१/०८२ सालको निर्णय नं." in text
        # the llama-server --special end token is not page text
        assert "im_end" not in text
        # the stamp is an Image block: its looping caption and English description go
        assert "नगरपालिका वडा" not in text and "stamp" not in text.lower()

    def test_picture_blocks_are_dropped(self) -> None:
        answer = (
            "<|Text|><|10|><|10|><|500|><|40|><p>मुद्दा: सम्पत्ति शुद्धीकरण</p>"
            '<|Picture|><|600|><|10|><|900|><|90|><img alt="Signature of the official"/>'
            "<|Page-Footer|><|480|><|950|><|520|><|970|><p>९</p>"
        )
        assert surya_answer_to_text(answer) == "मुद्दा: सम्पत्ति शुद्धीकरण\n\n९"

    def test_div_blocks_read_the_same_as_compact(self) -> None:
        compact = "<|Section-Header|><|1|><|2|><|3|><|4|><h2>अभियोग पत्र</h2><|Text|><|5|><|6|><|7|><|8|><p>दफा २२</p>"
        divs = (
            '<div data-bbox="1 2 3 4" data-label="Section-Header"><h2>अभियोग पत्र</h2></div>\n'
            '<div data-bbox="5 6 7 8" data-label="Text"><p>दफा २२</p></div>'
        )
        assert surya_answer_to_text(compact) == surya_answer_to_text(divs)
        assert surya_answer_to_text(divs) == "## अभियोग पत्र\n\nदफा २२"

    def test_table_becomes_markdown(self) -> None:
        lines = surya_answer_to_text(TABLE).splitlines()
        assert lines[0] == (
            "| GL Name | 001-Head Office Kausaltar | 002-Branch Office Kamalbinayak "
            "| 008-Branch Office Sallaghari | Total |"
        )
        assert lines[1] == "| --- | --- | --- | --- | --- |"
        assert lines[3].startswith("| Paid Up Capital | 54,317,400.00 |")

    def test_blocks_inside_a_cell_do_not_merge_numbers(self) -> None:
        answer = (
            "<table><tr><th>रकम</th></tr><tr><td><p>१२</p><p>३४</p></td></tr></table>"
        )
        assert surya_answer_to_text(answer).splitlines()[2] == "| १२ ३४ |"

    def test_compact_table_without_closing_tags_keeps_its_cells(self) -> None:
        # Surya 2.5's compact form leaves out </td>, </th> and </tr>
        answer = "<|Table|><|1|><|2|><|3|><|4|><table><tr><th>विवरण<th>रकम<tr><td>जम्मा<td>१००</table>"
        assert surya_answer_to_text(answer).splitlines() == [
            "| विवरण | रकम |",
            "| --- | --- |",
            "| जम्मा | १०० |",
        ]

    def test_merged_cells_keep_values_in_their_columns(self) -> None:
        answer = (
            "<table><tr><th>विवरण</th><th>२०७९</th><th>२०८०</th></tr>"
            '<tr><td rowspan="2">रकम</td><td>१</td><td>२</td></tr>'
            "<tr><td>३</td><td>४</td></tr>"
            '<tr><td colspan="2">जम्मा</td><td>१०</td></tr></table>'
        )
        assert surya_answer_to_text(answer).splitlines()[2:] == [
            "| रकम | १ | २ |",
            "|  | ३ | ४ |",
            "| जम्मा |  | १० |",
        ]

    def test_box_tokens_and_fences_are_removed(self) -> None:
        answer = "```html\n<|Text|><|1|><|2|><|3|><|4|><p>क<|box|><|1|><|2|><|3|><|4|>ख</p>\n```"
        assert surya_answer_to_text(answer) == "कख"


class TestService:
    def test_request_is_suryas_own(self) -> None:
        client = _Client(COMPACT)
        service = SuryaOCRService(client, max_tokens=4096)
        png = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1631, 2331), False).tobytes(
            "png"
        )
        result = service.extract_text(io.BytesIO(png))
        assert result.error is None and "अभियोग पत्र" in result.text
        (call,) = client.completions.calls
        assert call["model"] == SURYA_DEFAULT_MODEL
        assert call["temperature"] == 0 and call["max_tokens"] == 4096
        image, prompt = call["messages"][0]["content"]
        # image first, then the exact training-time prompt
        assert image["type"] == "image_url" and prompt == {
            "type": "text",
            "text": SURYA_PROMPT,
        }
        sent = fitz.Pixmap(base64.b64decode(image["image_url"]["url"].split(",")[1]))
        assert (sent.width, sent.height) == (1624, 2324)

    def test_truncated_answer_is_an_error(self) -> None:
        service = SuryaOCRService(_Client(COMPACT, finish_reason="length"))
        png = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 56, 56), False).tobytes("png")
        result = service.extract_text(io.BytesIO(png))
        assert result.text == "" and "max_tokens" in (result.error or "")

    def test_free_form_prompt_is_refused_without_a_call(self) -> None:
        client = _Client(COMPACT)
        result = SuryaOCRService(client).extract_text(
            io.BytesIO(b""), prompt="Which reading is right?"
        )
        assert result.error and client.completions.calls == []


class TestConfiguration:
    def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (
            "LIKHIT_OCR_ENGINE",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MARKITDOWN_OCR_MODEL",
            "OPENAI_MODEL",
            "GEMINI_MODEL",
        ):
            monkeypatch.delenv(name, raising=False)
        assert _build_ocr_service() is None

    def test_engine_surya(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIKHIT_OCR_ENGINE", "surya")
        monkeypatch.setenv("LIKHIT_SURYA_BASE_URL", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("LIKHIT_SURYA_MAX_TOKENS", "8000")
        monkeypatch.setenv("LIKHIT_SURYA_TIMEOUT", "nonsense")
        service = _build_ocr_service()
        assert isinstance(service, SuryaOCRService)
        assert service.model == SURYA_DEFAULT_MODEL and service.max_tokens == 8000
        assert service.timeout == 1800.0

    def test_engine_surya_without_endpoint_stays_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LIKHIT_OCR_ENGINE", "surya")
        monkeypatch.delenv("LIKHIT_SURYA_BASE_URL", raising=False)
        assert _build_ocr_service() is None

    def test_explicit_client_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIKHIT_OCR_ENGINE", "surya")
        service = _build_ocr_service(llm_client=object(), llm_model="m")
        assert type(service) is LLMVisionOCRService


class TestPipeline:
    def _run(self, answer: str, correction: OcrCorrection) -> tuple[Any, _Client]:
        client = _Client(answer)
        service = cast(LLMVisionOCRService, SuryaOCRService(client))
        return _run_page_ocr(_pdf(), service, (1,), correction=correction), client

    def test_decline_is_still_a_failed_page(self) -> None:
        run, _ = self._run(
            "<|Text|><|0|><|0|><|1000|><|1000|><p>I'm sorry, but I can't transcribe this image.</p>",
            OcrCorrection(),
        )
        assert run.text_by_page == {} and run.declined_pages == (1,)

    def test_tesseract_digit_dispute_is_flagged_not_guessed(self) -> None:
        surya = surya_answer_to_text(COMPACT)
        tesseract = surya.replace("२०८१/०८२", "२०८१/०८३")
        run, client = self._run(
            COMPACT,
            OcrCorrection(
                rules=True, second_reader=lambda _png: tesseract, reread=True
            ),
        )
        page = run.text_by_page[1]
        assert "२०८१/०८२" in page  # Surya's reading kept
        assert page.endswith("<!-- likhit:ocr-review spans=1 -->")
        # the re-read is not sent to Surya: one call, the transcription
        assert len(client.completions.calls) == 1
