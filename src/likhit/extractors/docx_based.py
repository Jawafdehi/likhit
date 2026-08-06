"""Legacy Microsoft Word `.doc` extraction."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from likhit.errors import ExtractionError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment

# LibreOffice hangs rather than failing on some malformed .doc inputs, and this
# runs inside a queue consumer, so the conversion has to be bounded.
SOFFICE_TIMEOUT_SECONDS = 120


class DocxBasedStrategy(ExtractionStrategy):
    """Extract plain text from legacy `.doc` files."""

    def extract_text(self, file_path: str, pages: str | None = None) -> RawDocument:
        """Extract text from a legacy `.doc` file.

        Args:
            file_path: Path to the DOC file
            pages: Ignored for DOC files (no page concept)

        Returns:
            RawDocument with extracted text fragments

        Raises:
            ExtractionError: If extraction fails or file format is unsupported
        """
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".doc":
            text = self._extract_doc(file_path)
        else:
            raise ExtractionError(
                f"Unsupported file format: {suffix}. Only .doc is supported."
            )

        if not text or not text.strip():
            raise ExtractionError(
                "No extractable text found in document. The file may be empty or corrupted."
            )

        # Split into paragraphs and create fragments
        fragments = self._create_fragments(text)
        paragraphs = [f.text for f in fragments]

        return RawDocument(
            fragments=fragments,
            raw_text=text,
            paragraphs=paragraphs,
        )

    def extract_tables(self, file_path: str) -> list:
        """Extract tables from a DOC file.

        Note: Plain-text DOC extraction doesn't preserve table structure.
        Returns empty list as tables are extracted as plain text.
        """
        return []

    def _extract_doc(self, file_path: str) -> str:
        """Extract plain text from legacy DOC file using antiword.

        The primary path uses pyantiword. If pyantiword's bundled antiword binary
        is incompatible with the current platform, we fall back to other
        locally available extractors, cheapest first: a system antiword, macOS
        textutil, then a headless LibreOffice conversion.

        pyantiword ships an x86-64 binary, so on every other architecture the
        primary path raises `Exec format error` and one of the fallbacks has to
        carry the conversion. LibreOffice is the only one packaged for all the
        architectures we deploy to, which is why it anchors the chain.
        """
        try:
            # pyantiword.extract_text_with_antiword() takes a file path
            from pyantiword.antiword_wrapper import extract_text_with_antiword

            text = extract_text_with_antiword(file_path)
            return text if text else ""
        except Exception as e:
            fallback_text = self._extract_doc_with_system_antiword(file_path)
            if fallback_text is not None:
                return fallback_text

            fallback_text = self._extract_doc_with_textutil(file_path)
            if fallback_text is not None:
                return fallback_text

            fallback_text = self._extract_doc_with_soffice(file_path)
            if fallback_text is not None:
                return fallback_text

            err = str(e)
            if "Win32" in err or "WinError" in err:
                raise ExtractionError(
                    "Failed to extract text from DOC: pyantiword is not compatible with Windows. "
                    "Install antiword separately and ensure it is on PATH, or convert DOC to DOCX first. "
                    f"Original error: {e}"
                ) from e
            if "Exec format error" in err:
                raise ExtractionError(
                    "Failed to extract text from DOC: pyantiword bundled binary is not compatible with this OS/architecture. "
                    "Install antiword or LibreOffice in your system PATH "
                    "(Debian/Ubuntu: apt-get install antiword libreoffice-writer; macOS: brew install antiword). "
                    f"Original error: {e}"
                ) from e
            raise ExtractionError(f"Failed to extract text from DOC: {e}") from e

    def _extract_doc_with_system_antiword(self, file_path: str) -> str | None:
        """Try extracting DOC text with a system antiword executable.

        `-m UTF-8.txt` is not optional. antiword picks its output charmap from
        the locale, and under a non-UTF-8 or unset `LANG` — the default in most
        containers — it renders every Devanagari character as an ASCII `?` while
        exiting 0 with an empty stderr. That is valid UTF-8 carrying no
        replacement characters, so nothing downstream can tell it went wrong.
        Measured on a CIAA press release: 1853 Devanagari codepoints with the
        flag, 0 without it. The failure is locale-driven, not architecture-driven
        — it reproduces on x86-64 under `LANG=C`.
        """
        antiword_bin = shutil.which("antiword")
        if not antiword_bin:
            return None
        try:
            result = subprocess.run(
                [antiword_bin, "-m", "UTF-8.txt", file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",  # Replace invalid UTF-8 with replacement character
                check=True,
            )
            text = result.stdout
            return text if text else ""
        except Exception:  # noqa: BLE001 - antiword failed; try the next strategy
            return None

    def _extract_doc_with_textutil(self, file_path: str) -> str | None:
        """Try extracting DOC text via macOS textutil when available."""
        textutil_bin = shutil.which("textutil")
        if not textutil_bin:
            return None
        try:
            result = subprocess.run(
                [textutil_bin, "-convert", "txt", "-stdout", file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",  # Replace invalid UTF-8 with replacement character
                check=True,
            )
            text = result.stdout
            return text if text else ""
        except Exception:  # noqa: BLE001 - textutil failed; try the next strategy
            return None

    def _extract_doc_with_soffice(self, file_path: str) -> str | None:
        """Try extracting DOC text via a headless LibreOffice conversion.

        LibreOffice is packaged for every architecture we deploy to, so this is
        the path that carries the conversion where pyantiword's bundled x86-64
        binary cannot execute at all.

        Three details are load-bearing:

        * `-env:UserInstallation` gives each call its own profile directory.
          Without it LibreOffice writes to `$HOME/.config`, which is absent or
          read-only in a container, and two concurrent calls sharing one profile
          abort on a lock file.
        * The filter is `Text (encoded)` with `UTF8` rather than plain `txt`.
          The default writes the host locale's charset, which mangles
          Devanagari into replacement characters.
        * The call is bounded by a timeout; LibreOffice hangs rather than
          failing on some malformed inputs.
        """
        soffice_bin = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice_bin:
            return None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                profile = Path(tmpdir) / "profile"
                subprocess.run(
                    [
                        soffice_bin,
                        f"-env:UserInstallation=file://{profile}",
                        "--headless",
                        "--norestore",
                        "--convert-to",
                        "txt:Text (encoded):UTF8",
                        "--outdir",
                        tmpdir,
                        file_path,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    # LibreOffice reports soft failures on stderr and still
                    # exits 0, so the converted file is the real success signal.
                    check=False,
                    timeout=SOFFICE_TIMEOUT_SECONDS,
                )
                converted = Path(tmpdir) / f"{Path(file_path).stem}.txt"
                if not converted.is_file():
                    return None
                # utf-8-sig, not utf-8: the `Text (encoded):UTF8` filter emits a
                # BOM, which would otherwise survive as a zero-width character
                # at the head of the first fragment.
                text = converted.read_text(encoding="utf-8-sig", errors="replace")
                return text if text else ""
        except Exception:  # noqa: BLE001 - LibreOffice failed; try the next strategy
            return None

    def _create_fragments(self, text: str) -> list[TextFragment]:
        """Split text into paragraph fragments with sequential positioning."""
        fragments = []
        paragraphs = text.split("\n")

        for idx, para in enumerate(paragraphs):
            para = para.strip()
            if para:  # Skip empty paragraphs
                fragments.append(
                    TextFragment(
                        text=para,
                        page_number=0,  # No page concept in legacy DOC extraction
                        x0=0.0,
                        y0=float(idx * 20),  # Simulate vertical positioning
                        x1=100.0,
                        y1=float(idx * 20 + 15),
                        block_number=idx,
                        line_number=idx,
                        gap_before=None,
                    )
                )

        return fragments
