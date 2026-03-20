"""Tests for DOCX and DOC extraction."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from likhit.errors import ExtractionError
from likhit.extractors.docx_based import DocxBasedStrategy
from likhit.handlers.ciaa_press_release import CIAAPressReleaseHandler
from likhit.handlers.kanun_patrika import KanunPatrikaHandler


class TestDocxBasedStrategy:
    """Test the simplified DOCX/DOC extraction strategy."""

    def test_extract_docx_creates_fragments(self):
        """Test that DOCX extraction creates proper text fragments."""
        strategy = DocxBasedStrategy()

        # Mock docx2txt2.process to return sample text
        with patch("docx2txt2.process") as mock_process:
            mock_process.return_value = "विषय: परीक्षण\n\nयो एउटा परीक्षण हो।"

            result = strategy.extract_text("test.docx")

            assert result.raw_text == "विषय: परीक्षण\n\nयो एउटा परीक्षण हो।"
            assert len(result.fragments) == 2  # Two non-empty paragraphs
            assert result.fragments[0].text == "विषय: परीक्षण"
            assert result.fragments[1].text == "यो एउटा परीक्षण हो।"
            assert result.paragraphs == ["विषय: परीक्षण", "यो एउटा परीक्षण हो।"]

    def test_extract_doc_creates_fragments(self):
        """Test that DOC extraction creates proper text fragments."""
        strategy = DocxBasedStrategy()

        # Mock pyantiword.extract_text_with_antiword to return sample text
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

        with patch("docx2txt2.process") as mock_process:
            mock_process.return_value = ""

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

        tables = strategy.extract_tables("test.docx")

        assert tables == []


class TestCIAAHandlerDocxRouting:
    """Test CIAA handler routes DOCX/DOC files correctly."""

    def test_get_extraction_strategy_for_docx(self):
        """Test that DOCX files are routed to DocxBasedStrategy."""
        handler = CIAAPressReleaseHandler()

        strategy = handler.get_extraction_strategy_for_file("test.docx")

        assert isinstance(strategy, DocxBasedStrategy)

    def test_get_extraction_strategy_for_doc(self):
        """Test that DOC files are routed to DocxBasedStrategy."""
        handler = CIAAPressReleaseHandler()

        strategy = handler.get_extraction_strategy_for_file("test.doc")

        assert isinstance(strategy, DocxBasedStrategy)

    def test_get_extraction_strategy_for_pdf(self):
        """Test that PDF files are routed to FontBasedStrategy."""
        handler = CIAAPressReleaseHandler()

        strategy = handler.get_extraction_strategy_for_file("test.pdf")

        # Should not be DocxBasedStrategy
        assert not isinstance(strategy, DocxBasedStrategy)


class TestKanunPatrikaHandlerDocxRouting:
    """Test Kanun Patrika handler routes DOCX correctly and rejects DOC."""

    def test_get_extraction_strategy_for_docx(self):
        """Test that DOCX files are routed to DocxBasedStrategy."""
        handler = KanunPatrikaHandler()

        strategy = handler.get_extraction_strategy_for_file("test.docx")

        assert isinstance(strategy, DocxBasedStrategy)

    def test_get_extraction_strategy_for_doc_raises_error(self):
        """Test that DOC files are rejected for Kanun Patrika."""
        handler = KanunPatrikaHandler()

        with pytest.raises(
            ExtractionError, match="Legacy .doc format is not supported"
        ):
            handler.get_extraction_strategy_for_file("test.doc")

    def test_get_extraction_strategy_for_pdf(self):
        """Test that PDF files are routed to FontBasedStrategy."""
        handler = KanunPatrikaHandler()

        strategy = handler.get_extraction_strategy_for_file("test.pdf")

        # Should not be DocxBasedStrategy
        assert not isinstance(strategy, DocxBasedStrategy)
