"""Word documents whose runs are drawn in a legacy keystroke font (Preeti and friends).

Two entry points share :func:`likhit.extractors.docx_legacy_runs.repair_docx_legacy_runs`:

* :class:`LegacyFontDocxConverter` takes a ``.docx`` that names a legacy font. It
  repairs the package and hands the result to MarkItDown's own ``DocxConverter``, so
  tables, headings and lists render exactly as they do for any ``.docx``. A package
  naming no legacy font is not claimed at all and converts exactly as it does
  without likhit. A repaired package loses markitdown-ocr's image reading, which
  plain ``DocxConverter`` lacks; legacy-font Word files seen so far carry no
  images.
* :func:`convert_legacy_font_doc` takes a legacy ``.doc`` (or a ``.docx`` saved
  with a ``.doc`` name, which is common on the Supreme Court portal). The plain-text
  ``.doc`` extractors (antiword, LibreOffice ``txt``) drop the font of every run, so
  a Preeti run reaches the transcript as ASCII keystrokes. When the file names a
  legacy font at all, it is converted to ``.docx`` with LibreOffice, repaired by
  font, and rendered like any ``.docx``. It returns ``None`` (the caller keeps its
  existing path, unchanged) when the file names no legacy font, when LibreOffice is
  not installed, or when the repair converted nothing.

The ``.doc`` gate reads the font table rather than guessing from text: a Word 97
binary stores font names as UTF-16LE in its font table (``SttbfFfn``), so a
legacy face is found by name without parsing the format. A false positive (a font
listed but never used) costs one LibreOffice conversion and changes nothing,
because the repair then converts nothing and the existing path runs.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo
from markitdown.converters import DocxConverter

from likhit.extractors.docx_based import SOFFICE_TIMEOUT_SECONDS
from likhit.extractors.docx_legacy_runs import repair_docx_legacy_runs
from likhit.extractors.legacy_maps import legacy_font_name_keys

logger = logging.getLogger(__name__)

_DOCX_EXTENSIONS = {".docx"}
_DOCX_MIMETYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
_ZIP_MAGIC = b"PK\x03\x04"


class LegacyFontDocxConverter(DocumentConverter):
    """``.docx`` -> Markdown, with legacy-font runs converted to Unicode first."""

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        del kwargs
        ext = (stream_info.extension or "").lower()
        mime = (stream_info.mimetype or "").lower()
        if ext:
            if ext not in _DOCX_EXTENSIONS:
                return False
        elif mime not in _DOCX_MIMETYPES:
            return False
        # Claim only packages that name a legacy font. Everything else is left to
        # the converter that would have taken it without likhit -- including
        # markitdown-ocr's image-reading DOCX converter when OCR is configured --
        # so a document with nothing to repair converts exactly as before.
        position = file_stream.tell()
        try:
            return names_legacy_font(file_stream.read())
        finally:
            file_stream.seek(position)

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        data = file_stream.read()
        return DocxConverter().convert(
            io.BytesIO(_repaired_or_original(data)), stream_info, **kwargs
        )


def _repaired_or_original(data: bytes) -> bytes:
    """The repaired package, or ``data`` itself if nothing changed or repair failed."""

    try:
        repaired, _report = repair_docx_legacy_runs(data)
    except Exception:  # noqa: BLE001 - a repair failure must not lose the document
        logger.warning(
            "likhit: legacy-font repair failed; converting as is", exc_info=True
        )
        return data
    return repaired


def names_legacy_font(data: bytes) -> bool:
    """Whether a Word file names a legacy keystroke font anywhere in its bytes.

    Covers both containers: a ``.docx`` names fonts in UTF-8 XML, a Word 97 ``.doc``
    in UTF-16LE in its font table. The keys are the same lower-case substrings the
    font-name router matches on (:func:`likhit.extractors.legacy_maps.legacy_font_name_keys`).
    """

    if data.startswith(_ZIP_MAGIC):
        haystacks = [_docx_font_names(data)]
    else:
        haystacks = [
            data[offset:].decode("utf-16-le", errors="ignore").lower()
            for offset in (0, 1)
        ]
    keys = legacy_font_name_keys()
    return any(key in hay for hay in haystacks for key in keys)


def _docx_font_names(data: bytes) -> str:
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            parts = [
                name
                for name in archive.namelist()
                if name.startswith("word/") and name.endswith(".xml")
            ]
            return " ".join(
                archive.read(name).decode("utf-8", errors="ignore").lower()
                for name in parts
            )
    except Exception:  # noqa: BLE001 - not a readable package: nothing to route on
        return ""


def _doc_to_docx(file_path: str) -> bytes | None:
    """Convert a legacy ``.doc`` to ``.docx`` bytes with LibreOffice, or ``None``.

    Same isolation as the ``txt`` path in :mod:`likhit.extractors.docx_based`: a
    per-call profile directory (two calls sharing one abort on a lock file) and a
    timeout (LibreOffice hangs on some malformed inputs).
    """

    soffice_bin = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice_bin:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            profile = Path(tmpdir) / "profile"
            source = Path(tmpdir) / "in.doc"
            source.write_bytes(Path(file_path).read_bytes())
            subprocess.run(
                [
                    soffice_bin,
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "docx:MS Word 2007 XML",
                    "--outdir",
                    tmpdir,
                    str(source),
                ],
                capture_output=True,
                check=False,
                timeout=SOFFICE_TIMEOUT_SECONDS,
            )
            converted = Path(tmpdir) / "in.docx"
            return converted.read_bytes() if converted.is_file() else None
    except Exception:  # noqa: BLE001 - fall back to the plain-text .doc path
        logger.warning("likhit: LibreOffice .doc -> .docx failed", exc_info=True)
        return None


def convert_legacy_font_doc(file_path: str) -> str | None:
    """Markdown for a ``.doc`` whose runs need a legacy-font repair, else ``None``."""

    try:
        data = Path(file_path).read_bytes()
    except OSError:
        return None
    if not names_legacy_font(data):
        return None
    docx = data if data.startswith(_ZIP_MAGIC) else _doc_to_docx(file_path)
    if docx is None:
        logger.info(
            "likhit: %s names a legacy font but LibreOffice is unavailable; "
            "its legacy-font runs stay unconverted",
            Path(file_path).name,
        )
        return None
    try:
        repaired, report = repair_docx_legacy_runs(docx)
    except Exception:  # noqa: BLE001 - keep the existing path on any repair failure
        logger.warning("likhit: legacy-font repair failed", exc_info=True)
        return None
    if not report.converted:
        return None
    try:
        result = DocxConverter().convert(
            io.BytesIO(repaired), StreamInfo(extension=".docx")
        )
    except Exception:  # noqa: BLE001 - a render failure keeps the plain .doc path
        logger.warning("likhit: rendering the repaired .docx failed", exc_info=True)
        return None
    return result.markdown
