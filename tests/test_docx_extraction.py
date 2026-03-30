"""Tests for legacy DOC extraction and plugin routing."""

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
    """Test the simplified legacy DOC extraction strategy."""

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

    def test_extract_empty_doc_raises_error(self):
        """Test that empty DOC files raise an error."""
        strategy = DocxBasedStrategy()

        with patch(
            "pyantiword.antiword_wrapper.extract_text_with_antiword"
        ) as mock_extract:
            mock_extract.return_value = ""

            with pytest.raises(ExtractionError, match="No extractable text found"):
                strategy.extract_text("empty.doc")

    def test_extract_unsupported_format_raises_error(self):
        """Test that unsupported file formats raise an error."""
        strategy = DocxBasedStrategy()

        with pytest.raises(ExtractionError, match="Unsupported file format"):
            strategy.extract_text("test.docx")

    def test_extract_tables_returns_empty_list(self):
        """Test that extract_tables returns empty list (no table support)."""
        strategy = DocxBasedStrategy()

        assert strategy.extract_tables("test.doc") == []


class TestSingleColumnNoticeDocRouting:
    """Test single-column notice handler routes legacy DOC files correctly."""

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
    """Test two-column layout handler routes legacy DOC files generically."""

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
    """Test that legacy DOC files are routed by structure cues."""

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
