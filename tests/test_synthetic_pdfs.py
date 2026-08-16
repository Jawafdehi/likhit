"""The fixture builders themselves, which nothing tested.

`tests/synthetic_pdfs.py` is the source of every synthetic PDF in this suite, so a
defect in it is a defect in whatever those fixtures are taken to prove -- and it had no
tests of its own. This file covers the part review raised: writing a font name into a
PDF name object.

The finding's stated mechanism -- "can corrupt the PDF object syntax" -- splits in two
under measurement, and only one half is corruption. Delimiters make PyMuPDF RAISE, so
nothing is written. An unescaped `#` followed by two hex digits is written literally and
UNescaped on read, so the name silently becomes a different one. Both halves are pinned
here, separately, so the refuted half is not reused as a rationale and the real one is
not mistaken for it.
"""

from __future__ import annotations

import fitz
import pytest

from tests.synthetic_pdfs import (
    _PDF_NAME_MUST_ESCAPE,
    _SUBSET_STYLE_FONT_NAME,
    _pdf_name,
    _rename_base_fonts,
)

#: Names PyMuPDF REJECTS when written unescaped -- it raises, so nothing is corrupted.
#: A space is the realistic one here: "Wingdings 2" is a face this project routes.
REJECTED_RAW = ["Wingdings 2", "a/b", "(x)"]

#: Names that are SILENTLY changed when written unescaped, which is the real defect.
#: PyMuPDF writes `#` literally but unescapes `#XX` on read, so the fixture ends up
#: carrying a different font name than it asked for, with no error anywhere.
#: `(written raw, what comes back)`.
CORRUPTED_RAW = [("c#41d", "cAd"), ("Font#20Two", "Font Two")]

#: Needs no escape and must pass through byte-identical -- so a no-op escaper cannot
#: pass the round-trip tests while mangling everything above.
ALREADY_SAFE = [_SUBSET_STYLE_FONT_NAME, "Symbol,Bold", "ABCDEE+Symbol"]


def _font_names(doc: fitz.Document, name: str) -> set[str]:
    """Write ``name`` as every font's /BaseFont, then read it back through PyMuPDF."""

    page = doc.new_page()
    page.insert_text((72, 72), "t", fontname="helv", fontsize=12)
    _rename_base_fonts(doc, name)
    raw = doc.tobytes()
    reopened = fitz.open(stream=raw, filetype="pdf")
    try:
        return {entry[3] for entry in reopened.get_page_fonts(0)}
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "name",
    REJECTED_RAW + [raw for raw, _ in CORRUPTED_RAW] + ALREADY_SAFE + ["c#d"],
)
def test_a_font_name_round_trips_through_the_pdf(name: str) -> None:
    """The property that matters: what goes in comes back out.

    Escaping is only useful if the reader unescapes, so this asserts the round trip
    rather than the escaped form -- pinning `Wingdings#202` would pin an encoding
    detail instead of the behaviour.
    """

    doc = fitz.open()
    try:
        assert name in _font_names(doc, name)
    finally:
        doc.close()


@pytest.mark.parametrize("name", REJECTED_RAW)
def test_a_delimiter_fails_loudly_rather_than_corrupting(name: str) -> None:
    """Half of the review's mechanism, refuted.

    For DELIMITERS an unescaped name cannot "corrupt the PDF object syntax": PyMuPDF
    validates and raises, so nothing is written. The cost is an opaque error that never
    names the font, not corruption. Pinned so the refuted half is not reintroduced as a
    rationale -- and so the genuinely corrupting half below is not confused with it.
    """

    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "t", fontname="helv", fontsize=12)
        with pytest.raises(ValueError):
            for xref in range(1, doc.xref_length()):
                if doc.xref_get_key(xref, "Type")[1] == "/Font":
                    doc.xref_set_key(xref, "BaseFont", f"/{name}")
    finally:
        doc.close()


@pytest.mark.parametrize(("raw", "reads_back_as"), CORRUPTED_RAW)
def test_an_unescaped_hash_is_the_one_silent_corruption(
    raw: str, reads_back_as: str
) -> None:
    """The other half, CONFIRMED -- and the reason escaping is not merely convenience.

    PyMuPDF writes `#` literally but unescapes `#XX` on read, so an unescaped name
    containing `#` followed by two hex digits comes back as a DIFFERENT name, with no
    error at any point. A fixture built that way would be testing a font nobody named.
    """

    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "t", fontname="helv", fontsize=12)
        for xref in range(1, doc.xref_length()):
            if doc.xref_get_key(xref, "Type")[1] == "/Font":
                doc.xref_set_key(xref, "BaseFont", f"/{raw}")
        written = doc.tobytes()
    finally:
        doc.close()

    reopened = fitz.open(stream=written, filetype="pdf")
    try:
        assert {entry[3] for entry in reopened.get_page_fonts(0)} == {reads_back_as}
    finally:
        reopened.close()

    # ...and the escaped form is what makes it survive intact.
    escaped_doc = fitz.open()
    try:
        assert raw in _font_names(escaped_doc, raw)
    finally:
        escaped_doc.close()


@pytest.mark.parametrize("name", ALREADY_SAFE)
def test_a_safe_name_is_not_rewritten(name: str) -> None:
    """No existing fixture may move. `_SUBSET_STYLE_FONT_NAME` is the live one."""

    assert _pdf_name(name) == name


def test_the_escape_set_is_the_pdf_delimiter_set() -> None:
    """Pinned against the spec's classes rather than a copied literal (PDF 1.7 §7.3.5).

    `#` must be in it because it introduces the escape; a regular character must not,
    or safe names start being rewritten.
    """

    assert set("()<>[]{}/%") <= _PDF_NAME_MUST_ESCAPE, "delimiters"
    assert set(" \t\r\n\f") <= _PDF_NAME_MUST_ESCAPE, "whitespace"
    assert "#" in _PDF_NAME_MUST_ESCAPE, "the escape introducer itself"
    assert not any(char.isalnum() for char in _PDF_NAME_MUST_ESCAPE)
    for regular in "+-.,_":
        assert regular not in _PDF_NAME_MUST_ESCAPE, regular


def test_an_empty_or_non_ascii_name_is_refused() -> None:
    """Refused rather than encoded: PDF names are byte strings, and choosing an
    encoding is not this helper's decision to make."""

    with pytest.raises(ValueError, match="empty"):
        _pdf_name("")
    with pytest.raises(ValueError, match="non-ASCII"):
        _pdf_name("नेपाली")
