"""Tests for legacy DOC extraction and plugin routing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from likhit.converters.nepali_docx import NepaliDocxConverter
from likhit.errors import ExtractionError
from likhit.extractors.docx_based import SOFFICE_TIMEOUT_SECONDS, DocxBasedStrategy
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


class TestSystemAntiwordFallback:
    """A system antiword must be pinned to UTF-8 rather than trusting the locale."""

    @staticmethod
    def _exec_format_error(*_args, **_kwargs):
        raise OSError(8, "Exec format error")

    def test_system_antiword_is_forced_to_utf8(self):
        """Without `-m UTF-8.txt` Devanagari silently degrades to ASCII `?`.

        antiword reads its charmap from the locale and containers usually have no
        LANG, so the bare call exits 0 with valid UTF-8 that has lost every
        Devanagari codepoint. Nothing downstream can detect that, which is why
        the flag is asserted here.
        """
        strategy = DocxBasedStrategy()

        with (
            patch(
                "pyantiword.antiword_wrapper.extract_text_with_antiword",
                self._exec_format_error,
            ),
            patch(
                "likhit.extractors.docx_based.shutil.which",
                lambda name: "/usr/bin/antiword" if name == "antiword" else None,
            ),
            patch("likhit.extractors.docx_based.subprocess.run") as mock_run,
        ):
            mock_run.return_value = SimpleNamespace(
                returncode=0, stdout="अख्तियार दुरुपयोग अनुसन्धान आयोग", stderr=""
            )
            result = strategy.extract_text("sample.doc")

        argv = mock_run.call_args[0][0]
        assert "-m" in argv
        assert "UTF-8.txt" in argv
        assert argv.index("UTF-8.txt") == argv.index("-m") + 1
        assert result.raw_text == "अख्तियार दुरुपयोग अनुसन्धान आयोग"


class TestSofficeFallback:
    """LibreOffice carries .doc conversion where pyantiword's binary cannot run.

    pyantiword ships an x86-64 executable, so on arm64 the primary path dies with
    `Exec format error` and a fallback has to do the work. LibreOffice is the only
    converter packaged for every architecture we deploy to. Verified against a
    real CIAA press release on arm64: byte-identical to amd64 antiword.
    """

    @staticmethod
    def _exec_format_error(*_args, **_kwargs):
        raise OSError(8, "Exec format error")

    @staticmethod
    def _which_only_soffice(name):
        return "/usr/bin/soffice" if name in ("soffice", "libreoffice") else None

    @staticmethod
    def _fake_soffice(text, *, bom=False):
        """Stand in for LibreOffice: write `<stem>.txt` into `--outdir`."""

        def _run(argv, **kwargs):
            argv = list(argv)
            outdir = Path(argv[argv.index("--outdir") + 1])
            source = Path(argv[-1])
            payload = ("﻿" if bom else "") + text
            (outdir / f"{source.stem}.txt").write_text(payload, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return _run

    def test_soffice_used_when_bundled_binary_cannot_exec(self):
        """arm64: pyantiword raises, no antiword/textutil, LibreOffice converts."""
        strategy = DocxBasedStrategy()
        nepali = "अख्तियार दुरुपयोग अनुसन्धान आयोग\n\nप्रेस विज्ञप्ति"

        with (
            patch(
                "pyantiword.antiword_wrapper.extract_text_with_antiword",
                self._exec_format_error,
            ),
            patch(
                "likhit.extractors.docx_based.shutil.which", self._which_only_soffice
            ),
            patch(
                "likhit.extractors.docx_based.subprocess.run",
                side_effect=self._fake_soffice(nepali),
            ),
        ):
            result = strategy.extract_text("sample.doc")

        assert result.raw_text == nepali
        assert result.fragments[0].text == "अख्तियार दुरुपयोग अनुसन्धान आयोग"

    def test_soffice_bom_is_stripped(self):
        """The `Text (encoded):UTF8` filter emits a BOM; it must not survive."""
        strategy = DocxBasedStrategy()

        with (
            patch(
                "pyantiword.antiword_wrapper.extract_text_with_antiword",
                self._exec_format_error,
            ),
            patch(
                "likhit.extractors.docx_based.shutil.which", self._which_only_soffice
            ),
            patch(
                "likhit.extractors.docx_based.subprocess.run",
                side_effect=self._fake_soffice("विषय: परीक्षण", bom=True),
            ),
        ):
            result = strategy.extract_text("sample.doc")

        assert not result.raw_text.startswith("﻿")
        assert result.raw_text == "विषय: परीक्षण"

    def test_profile_uri_is_percent_encoded(self, tmp_path, monkeypatch):
        """A TMPDIR with a space must still yield a well-formed file:// URI.

        The profile directory is created under TMPDIR, so an operator-set TMPDIR
        containing a space or non-ASCII character would produce a malformed URI.
        LibreOffice falls back to $HOME in that case, which is unset in the
        image, and the conversion fails.
        """
        spaced = tmp_path / "dir with space"
        spaced.mkdir()
        # tempfile.gettempdir() caches into tempfile.tempdir on first use, so
        # setting TMPDIR here would be ignored; override the cache itself.
        monkeypatch.setattr("tempfile.tempdir", str(spaced))
        strategy = DocxBasedStrategy()

        with (
            patch(
                "pyantiword.antiword_wrapper.extract_text_with_antiword",
                self._exec_format_error,
            ),
            patch(
                "likhit.extractors.docx_based.shutil.which", self._which_only_soffice
            ),
            patch(
                "likhit.extractors.docx_based.subprocess.run",
                side_effect=self._fake_soffice("पाठ"),
            ) as mock_run,
        ):
            strategy.extract_text("sample.doc")

        argv = mock_run.call_args[0][0]
        profile_arg = next(a for a in argv if a.startswith("-env:UserInstallation="))
        assert "%20" in profile_arg
        assert " " not in profile_arg

    def test_soffice_invocation_forces_utf8_and_a_private_profile(self):
        """Guard the three flags that make this work in a container."""
        strategy = DocxBasedStrategy()

        with (
            patch(
                "pyantiword.antiword_wrapper.extract_text_with_antiword",
                self._exec_format_error,
            ),
            patch(
                "likhit.extractors.docx_based.shutil.which", self._which_only_soffice
            ),
            patch(
                "likhit.extractors.docx_based.subprocess.run",
                side_effect=self._fake_soffice("पाठ"),
            ) as mock_run,
        ):
            strategy.extract_text("sample.doc")

        argv, kwargs = mock_run.call_args[0][0], mock_run.call_args[1]
        # UTF-8 or Devanagari comes back as the host locale's charset.
        assert "txt:Text (encoded):UTF8" in argv
        # Without a private profile LibreOffice writes to $HOME (absent in the
        # image) and concurrent calls deadlock on a lock file.
        assert any(a.startswith("-env:UserInstallation=file://") for a in argv)
        assert "--headless" in argv
        # LibreOffice hangs rather than failing on some malformed inputs.
        assert kwargs["timeout"] == SOFFICE_TIMEOUT_SECONDS

    def test_no_converter_at_all_names_libreoffice_in_the_error(self):
        """With nothing installed the operator is told what to install."""
        strategy = DocxBasedStrategy()

        with (
            patch(
                "pyantiword.antiword_wrapper.extract_text_with_antiword",
                self._exec_format_error,
            ),
            patch("likhit.extractors.docx_based.shutil.which", lambda _name: None),
            pytest.raises(ExtractionError, match="LibreOffice"),
        ):
            strategy.extract_text("sample.doc")


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
                stream_info=SimpleNamespace(
                    extension=".doc", mimetype="application/msword"
                ),
            )

            assert "विषय: परीक्षण" in result.text_content
