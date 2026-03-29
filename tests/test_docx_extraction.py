"""Tests for DOCX and DOC extraction."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from likhit.converters.nepali_docx import NepaliDocxConverter
from likhit.errors import ExtractionError
from likhit.extractors.docx_based import DocxBasedStrategy
from likhit.handlers.single_column_notice import SingleColumnNoticeHandler
from likhit.handlers.two_column_layout import TwoColumnLayoutHandler


class TestDocxBasedStrategy:
    """Test the simplified DOCX/DOC extraction strategy."""

    def test_extract_docx_creates_fragments(self):
        """Test that DOCX extraction creates proper text fragments."""
        strategy = DocxBasedStrategy()

        with patch.object(strategy._markitdown, "convert") as mock_convert:
            mock_result = MagicMock()
            mock_result.text_content = "विषय: परीक्षण\n\nयो एउटा परीक्षण हो।"
            mock_convert.return_value = mock_result

            result = strategy.extract_text("test.docx")

            assert result.raw_text == "विषय: परीक्षण\n\nयो एउटा परीक्षण हो।"
            assert len(result.fragments) == 2
            assert result.fragments[0].text == "विषय: परीक्षण"
            assert result.fragments[1].text == "यो एउटा परीक्षण हो।"
            assert result.paragraphs == ["विषय: परीक्षण", "यो एउटा परीक्षण हो।"]

    def test_extract_doc_creates_fragments(self):
        """Test that DOC extraction creates proper text fragments."""
        strategy = DocxBasedStrategy()

        with patch(
            "pyantiword.antiword_wrapper.extract_text_with_antiword"
        ) as mock_extract:
            mock_extract.return_value = "विषय: परीक्षण\n\nयो एउटा परीक्षण हो।"

            result = strategy.extract_text("test.doc")

            assert result.raw_text == "विषय: परीक्षण\n\nयो एउटा परीक्षण हो।"
            assert len(result.fragments) == 2
            assert result.fragments[0].text == "विषय: परीक्षण"
            assert result.fragments[1].text == "यो एउटा परीक्षण हो।"

    def test_extract_empty_docx_raises_error(self):
        """Test that empty DOCX files raise an error."""
        strategy = DocxBasedStrategy()

        with patch.object(strategy._markitdown, "convert") as mock_convert:
            mock_result = MagicMock()
            mock_result.text_content = ""
            mock_convert.return_value = mock_result

            with pytest.raises(ExtractionError, match="No extractable text found"):
                strategy.extract_text("empty.docx")

    def test_extract_unsupported_format_raises_error(self):
        """Test that unsupported file formats raise an error."""
        strategy = DocxBasedStrategy()

        with pytest.raises(ExtractionError, match="Unsupported file format"):
            strategy.extract_text("test.txt")

    def test_extract_tables_returns_empty_list(self):
        """Test that extract_tables returns empty list (no table support)."""
        strategy = DocxBasedStrategy()

        assert strategy.extract_tables("test.docx") == []


class TestSingleColumnNoticeDocxRouting:
    """Test single-column notice handler routes word files correctly."""

    def test_get_extraction_strategy_for_docx(self):
        handler = SingleColumnNoticeHandler()
        assert isinstance(
            handler.get_extraction_strategy_for_file("test.docx"),
            DocxBasedStrategy,
        )

    def test_get_extraction_strategy_for_doc(self):
        handler = SingleColumnNoticeHandler()
        assert isinstance(
            handler.get_extraction_strategy_for_file("test.doc"),
            DocxBasedStrategy,
        )

    def test_get_extraction_strategy_for_pdf(self):
        handler = SingleColumnNoticeHandler()
        assert not isinstance(
            handler.get_extraction_strategy_for_file("test.pdf"),
            DocxBasedStrategy,
        )


class TestTwoColumnLayoutDocxRouting:
    """Test two-column layout handler routes word files generically."""

    def test_get_extraction_strategy_for_docx(self):
        handler = TwoColumnLayoutHandler()
        assert isinstance(
            handler.get_extraction_strategy_for_file("test.docx"),
            DocxBasedStrategy,
        )

    def test_get_extraction_strategy_for_doc(self):
        handler = TwoColumnLayoutHandler()
        assert isinstance(
            handler.get_extraction_strategy_for_file("test.doc"),
            DocxBasedStrategy,
        )

    def test_get_extraction_strategy_for_pdf(self):
        handler = TwoColumnLayoutHandler()
        assert not isinstance(
            handler.get_extraction_strategy_for_file("test.pdf"),
            DocxBasedStrategy,
        )


class TestDocxStructureDetection:
    """Test that DOCX/DOC files are routed by structure cues."""

    def test_notice_style_docx_detected_and_routed_correctly(self):
        with patch("likhit.extractors.docx_based.MarkItDown") as mock_md_class:
            mock_md = MagicMock()
            mock_result = MagicMock()
            mock_result.text_content = (
                "विषय: परीक्षण\n\nमिति: २०८२।०१।१४\n\nयो एउटा परीक्षण हो।"
            )
            mock_md.convert.return_value = mock_result
            mock_md_class.return_value = mock_md

            result = NepaliDocxConverter().convert(
                file_stream=MagicMock(read=lambda: b"fake-docx"),
                stream_info=SimpleNamespace(extension=".docx", mimetype=""),
            )

            assert result.text_content.startswith("# परीक्षण")
            assert "मिति: २०८२।०१।१४" in result.text_content

    def test_dense_docx_without_notice_cues_falls_back_to_plain_text(self):
        with patch("likhit.extractors.docx_based.MarkItDown") as mock_md_class:
            mock_md = MagicMock()
            mock_result = MagicMock()
            mock_result.text_content = (
                "अनुच्छेद १\n\nअनुच्छेद २\n\nअनुच्छेद ३"
            )
            mock_md.convert.return_value = mock_result
            mock_md_class.return_value = mock_md

            result = NepaliDocxConverter().convert(
                file_stream=MagicMock(read=lambda: b"fake-docx"),
                stream_info=SimpleNamespace(extension=".docx", mimetype=""),
            )

            assert result.text_content == mock_result.text_content

    def test_unknown_docx_returns_plain_text(self):
        with patch("likhit.extractors.docx_based.MarkItDown") as mock_md_class:
            mock_md = MagicMock()
            mock_result = MagicMock()
            mock_result.text_content = "This is just plain text with no markers."
            mock_md.convert.return_value = mock_result
            mock_md_class.return_value = mock_md

            result = NepaliDocxConverter().convert(
                file_stream=MagicMock(read=lambda: b"fake-docx"),
                stream_info=SimpleNamespace(extension=".docx", mimetype=""),
            )

            assert result.text_content == "This is just plain text with no markers."

    def test_notice_style_doc_is_processed(self):
        with patch(
            "pyantiword.antiword_wrapper.extract_text_with_antiword"
        ) as mock_extract:
            mock_extract.return_value = (
                "विषय: परीक्षण\n\nमिति: २०८२।०१।१४\n\nयो एउटा परीक्षण हो।"
            )

            result = NepaliDocxConverter().convert(
                file_stream=MagicMock(read=lambda: b"fake-doc"),
                stream_info=SimpleNamespace(extension=".doc", mimetype="application/msword"),
            )

            assert "विषय: परीक्षण" in result.text_content
