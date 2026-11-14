"""Convert legacy-font runs inside a Word ``.docx`` to Unicode, keyed on each run's font.

Many Nepali court orders are Unicode (Kalimati/Mangal) documents with a few runs typed
in a legacy *keystroke* font -- Preeti, PCS NEPALI, Kantipur and friends. Word stores
those runs as the ASCII keystrokes and draws them with the legacy face, so a text
extractor that ignores fonts emits ``cR%]nfn /fosf]`` where the page shows
``अच्छेलाल रायको``. Some older orders are typed in Preeti from end to end.

The repair here keys on the **font each run is drawn in**, never on the shape of the
text. A shape test ("this ASCII token looks like Preeti") was tried and rejected: the
same orders carry real English -- medical findings, Latin maxims, "Know Your Customer"
-- which a shape test cannot tell from keystrokes, and it garbled them. A font name
can: an English run is in Times New Roman or Calibri, a keystroke run is in Preeti.

Which font draws a character follows the OOXML rules (ECMA-376 Part 1, 17.3.2.26
``rFonts``): ASCII characters use ``w:ascii``, the remaining Latin-range characters
use ``w:hAnsi``, and complex-script characters (Devanagari) use ``w:cs``. So a run
declared ``ascii="PCS NEPALI" cs="Kalimati"`` draws its ASCII keystrokes in PCS NEPALI
and any real Devanagari in it in Kalimati -- only the former is converted.

The font name is resolved through the usual inheritance (document defaults, the
paragraph style chain, the character style chain, then the run's own properties,
with theme fonts looked up in the theme part), and then routed through the same
name registry the PDF path uses (:func:`likhit.extractors.legacy_maps.match_legacy_map_name`),
so a font gets exactly the map it gets in a PDF. PCS NEPALI is therefore decoded with
its own table, not Preeti's -- they differ on keys such as ``%`` (छ under PCS NEPALI).

The output is a repaired ``.docx`` (same package, rewritten text nodes), so whatever
renders it -- MarkItDown's own ``DocxConverter`` here -- keeps tables, headings and
lists exactly as before. A document with no legacy-font run is returned unchanged,
byte for byte.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterator

from lxml import etree

from likhit.extractors.legacy_maps import (
    get_output_converter_for_map,
    match_legacy_map_name,
)

logger = logging.getLogger(__name__)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_XML_NS = "http://www.w3.org/XML/1998/namespace"


def _w(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


_P = _w("p")
_R = _w("r")
_T = _w("t")
_DEL_TEXT = _w("delText")
_RPR = _w("rPr")
_PPR = _w("pPr")
_RFONTS = _w("rFonts")
_RSTYLE = _w("rStyle")
_PSTYLE = _w("pStyle")
_RTL = _w("rtl")
_CS = _w("cs")
_TEXT_TAGS = frozenset({_T, _DEL_TEXT})
# Run children that carry no text and draw nothing -- formatting, revision marks. Any
# other child (tab, break, field character, drawing, footnote reference ...) separates
# the text on either side of it, so a legacy group never spans one.
_TRANSPARENT_RUN_CHILDREN = frozenset({_RPR, _w("lastRenderedPageBreak")})

#: Word parts whose paragraphs reach a transcript (the body, notes, comments) or can
#: (headers and footers, which other renderers include).
_TEXT_PART = re.compile(
    r"^word/(document|footnotes|endnotes|comments|header\d*|footer\d*)\.xml$"
)

#: Characters a legacy keystroke run never converts, because Word draws them with the
#: complex-script (``w:cs``) font, not the ASCII/hAnsi one that names the legacy face:
#: Devanagari, Devanagari Extended and Vedic Extensions, plus the joiners and the
#: dotted circle that Unicode Devanagari text carries.
_COMPLEX_SCRIPT = re.compile(
    "[\u0900-\u097f\ua8e0-\ua8ff\u1cd0-\u1cff\u200c\u200d\u25cc]+"
)

#: Characters npttf2utf's maps carry no rule for and that Word draws the same in any
#: face; they split a run into separately converted pieces only when nothing else does.
_WHITESPACE = re.compile(r"\s")
_KEYSTROKE_LETTER = re.compile(r"[A-Za-z]")


@dataclass
class DocxLegacyRunReport:
    """What :func:`repair_docx_legacy_runs` found and changed."""

    #: Characters converted, per resolved font name (as the document spells it).
    converted_chars_by_font: Counter[str] = field(default_factory=Counter)
    #: Runs that had at least one character converted, per map key.
    runs_by_map: Counter[str] = field(default_factory=Counter)
    #: Letter-free legacy stretches left alone because the segment is Unicode text.
    skipped_punctuation_groups: int = 0

    @property
    def converted(self) -> bool:
        return bool(self.runs_by_map)


@dataclass(frozen=True)
class _Fonts:
    """The two font slots that decide how a keystroke run's characters are drawn."""

    ascii: str | None = None
    h_ansi: str | None = None
    #: ``w:rtl``/``w:cs`` on the run: every character is drawn with the cs font.
    complex_script: bool = False

    def overlay(self, other: "_Fonts") -> "_Fonts":
        return _Fonts(
            ascii=other.ascii if other.ascii is not None else self.ascii,
            h_ansi=other.h_ansi if other.h_ansi is not None else self.h_ansi,
            complex_script=other.complex_script or self.complex_script,
        )


class _FontResolver:
    """Effective ``ascii``/``hAnsi`` font for a run, following OOXML inheritance.

    Order, lowest first: document defaults, the paragraph's style chain (or the default
    paragraph style), the run's character style chain, the run's direct properties.
    Theme references (``w:asciiTheme``) override the explicit name at the same level and
    are looked up in the theme part. Table-style and numbering fonts are not modelled:
    they apply to table text and list markers and have not been seen naming a legacy
    face, while the run-level declaration that does is almost always explicit.
    """

    def __init__(self, styles_xml: bytes | None, theme_xml: bytes | None) -> None:
        self._theme = _theme_fonts(theme_xml)
        self._styles: dict[str, tuple[str | None, _Fonts]] = {}
        self._defaults = _Fonts()
        self._default_paragraph_style: str | None = None
        if styles_xml:
            self._load_styles(styles_xml)
        self._chain_cache: dict[str, _Fonts] = {}

    def _load_styles(self, styles_xml: bytes) -> None:
        root = etree.fromstring(styles_xml, parser=_PARSER)
        defaults = root.find(f"{_w('docDefaults')}/{_w('rPrDefault')}/{_RPR}")
        if defaults is not None:
            self._defaults = self._fonts_of(defaults)
        for style in root.iter(_w("style")):
            style_id = style.get(_w("styleId"))
            if not style_id:
                continue
            based_on = style.find(_w("basedOn"))
            rpr = style.find(_RPR)
            self._styles[style_id] = (
                based_on.get(_w("val")) if based_on is not None else None,
                self._fonts_of(rpr) if rpr is not None else _Fonts(),
            )
            if (
                style.get(_w("type")) == "paragraph"
                and style.get(_w("default")) in ("1", "true", "on")
                and self._default_paragraph_style is None
            ):
                self._default_paragraph_style = style_id

    def _fonts_of(self, rpr: etree._Element) -> _Fonts:
        complex_script = any(
            child is not None and child.get(_w("val"), "1") not in ("0", "false", "off")
            for child in (rpr.find(_RTL), rpr.find(_CS))
        )
        rfonts = rpr.find(_RFONTS)
        if rfonts is None:
            return _Fonts(complex_script=complex_script)
        return _Fonts(
            ascii=self._theme_or_name(rfonts, "ascii"),
            h_ansi=self._theme_or_name(rfonts, "hAnsi"),
            complex_script=complex_script,
        )

    def _theme_or_name(self, rfonts: etree._Element, slot: str) -> str | None:
        theme = rfonts.get(_w(f"{slot}Theme"))
        if theme:
            # A theme reference wins over the explicit name at the same level.
            return self._theme.get(theme, "")
        return rfonts.get(_w(slot))

    def _style_fonts(self, style_id: str | None) -> _Fonts:
        if not style_id:
            return _Fonts()
        cached = self._chain_cache.get(style_id)
        if cached is not None:
            return cached
        chain: list[_Fonts] = []
        seen: set[str] = set()
        current: str | None = style_id
        while current and current not in seen and current in self._styles:
            seen.add(current)
            based_on, fonts = self._styles[current]
            chain.append(fonts)
            current = based_on
        resolved = _Fonts()
        for fonts in reversed(chain):
            resolved = resolved.overlay(fonts)
        self._chain_cache[style_id] = resolved
        return resolved

    def paragraph_fonts(self, paragraph: etree._Element) -> _Fonts:
        style_id = self._default_paragraph_style
        ppr = paragraph.find(_PPR)
        if ppr is not None:
            pstyle = ppr.find(_PSTYLE)
            if pstyle is not None and pstyle.get(_w("val")):
                style_id = pstyle.get(_w("val"))
        return self._defaults.overlay(self._style_fonts(style_id))

    def run_fonts(self, run: etree._Element, paragraph_fonts: _Fonts) -> _Fonts:
        rpr = run.find(_RPR)
        if rpr is None:
            return paragraph_fonts
        fonts = paragraph_fonts
        rstyle = rpr.find(_RSTYLE)
        if rstyle is not None:
            fonts = fonts.overlay(self._style_fonts(rstyle.get(_w("val"))))
        return fonts.overlay(self._fonts_of(rpr))


def _theme_fonts(theme_xml: bytes | None) -> dict[str, str]:
    """Theme slot name (``minorHAnsi`` ...) -> Latin typeface from the theme part."""

    if not theme_xml:
        return {}
    try:
        root = etree.fromstring(theme_xml, parser=_PARSER)
    except etree.XMLSyntaxError:
        return {}
    fonts: dict[str, str] = {}
    for kind in ("major", "minor"):
        latin = root.find(f".//{{{_A_NS}}}{kind}Font/{{{_A_NS}}}latin")
        face = latin.get("typeface", "") if latin is not None else ""
        for slot in ("Ascii", "HAnsi"):
            fonts[f"{kind}{slot}"] = face
    return fonts


# resolve_entities=False and no network: a .docx is untrusted input.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


@dataclass
class _Piece:
    """A slice of one text node, drawn in one font."""

    node: etree._Element
    text: str
    map_key: str | None
    font: str | None


def _legacy_map(font: str | None) -> str | None:
    return match_legacy_map_name(font) if font else None


def _pieces_of_text(node: etree._Element, text: str, fonts: _Fonts) -> Iterator[_Piece]:
    """Split one text node into slices by which font draws each character."""

    if fonts.complex_script:
        yield _Piece(node, text, None, None)
        return
    ascii_map = _legacy_map(fonts.ascii)
    h_ansi_map = _legacy_map(fonts.h_ansi)
    if ascii_map is None and h_ansi_map is None:
        yield _Piece(node, text, None, None)
        return

    def slot(char: str) -> tuple[str | None, str | None]:
        if _COMPLEX_SCRIPT.match(char):
            return None, None
        if ord(char) < 0x80:
            return ascii_map, fonts.ascii
        return h_ansi_map, fonts.h_ansi

    start = 0
    current = slot(text[0]) if text else (None, None)
    for index in range(1, len(text) + 1):
        nxt = slot(text[index]) if index < len(text) else None
        if nxt != current:
            yield _Piece(node, text[start:index], current[0], current[1])
            if nxt is not None:
                start, current = index, nxt


def _paragraph_runs(paragraph: etree._Element) -> list[etree._Element]:
    """Runs whose nearest enclosing paragraph is ``paragraph``.

    Excludes runs inside a text box nested in this paragraph: those belong to the text
    box's own paragraphs, which are visited separately.
    """

    runs = []
    for run in paragraph.iter(_R):
        owner = next(run.iterancestors(_P), None)
        if owner is paragraph:
            runs.append(run)
    return runs


def _segments(
    paragraph: etree._Element, resolver: _FontResolver
) -> Iterator[list[_Piece]]:
    """Maximal stretches of text with nothing but text between the pieces."""

    paragraph_fonts = resolver.paragraph_fonts(paragraph)
    segment: list[_Piece] = []
    for run in _paragraph_runs(paragraph):
        fonts = resolver.run_fonts(run, paragraph_fonts)
        for child in run:
            if child.tag in _TEXT_TAGS:
                if child.text:
                    segment.extend(_pieces_of_text(child, child.text, fonts))
            elif child.tag not in _TRANSPARENT_RUN_CHILDREN:
                if segment:
                    yield segment
                segment = []
    if segment:
        yield segment


def _convert_segment(
    segment: list[_Piece],
    converters: dict[str, Callable[[str], str]],
    report: DocxLegacyRunReport,
) -> bool:
    """Convert every legacy stretch in ``segment`` in place; True if any changed.

    Adjacent pieces on the same map are converted together, because Word splits runs
    mid-word (proofing marks, revision ids) and npttf2utf reorders within a word: a
    Preeti ``l`` (ि) typed before its consonant only lands right if both are in the
    same call. The converted text of each word-joined unit goes to its first piece and
    the rest are emptied, so formatting changes are kept at word boundaries.
    """

    changed = False
    # Unicode Devanagari in this segment that no legacy face draws. Where there is
    # some, a legacy-mapped stretch with no ASCII letter in it -- a lone "(", ".",
    # "-" or a digit run -- is punctuation typed between Unicode words in a run
    # whose ASCII slot happens to name a keystroke font (often via the style), not
    # keystrokes: converting it turns "(दुई महिना)" into "९दुई महिना०" and "नं." into
    # "नं।". Measured on Supreme Court orders 081-WH-0114 and 074-RC-0137. A real
    # keystroke stretch carries letters (Preeti spells every word with them), and a
    # whole-legacy paragraph has no Unicode to trigger this, so its digits still
    # convert.
    segment_has_unicode = any(
        piece.map_key is None and _COMPLEX_SCRIPT.search(piece.text)
        for piece in segment
    )
    index = 0
    while index < len(segment):
        map_key = segment[index].map_key
        if map_key is None:
            index += 1
            continue
        end = index
        while end + 1 < len(segment) and segment[end + 1].map_key == map_key:
            end += 1
        group = segment[index : end + 1]
        if segment_has_unicode and not any(
            _KEYSTROKE_LETTER.search(piece.text) for piece in group
        ):
            report.skipped_punctuation_groups += 1
            index = end + 1
            continue
        converter = converters.get(map_key)
        if converter is None:
            converter = converters[map_key] = get_output_converter_for_map(map_key)
        unit_start = 0
        for position in range(1, len(group) + 1):
            at_end = position == len(group)
            if not at_end:
                left, right = group[position - 1].text, group[position].text
                word_continues = not (
                    _WHITESPACE.match(left[-1:]) or _WHITESPACE.match(right[:1])
                )
                if word_continues:
                    continue
            unit = group[unit_start:position]
            source = "".join(piece.text for piece in unit)
            converted = converter(source)
            for piece in unit:
                if piece.font:
                    report.converted_chars_by_font[piece.font] += len(piece.text)
            unit[0].text = converted
            for piece in unit[1:]:
                piece.text = ""
            changed = True
            unit_start = position
        runs = {piece.node.getparent() for piece in group}
        report.runs_by_map[map_key] += len(runs)
        index = end + 1
    if changed:
        texts: dict[etree._Element, list[str]] = {}
        for piece in segment:
            texts.setdefault(piece.node, []).append(piece.text)
        for node, parts in texts.items():
            node.text = "".join(parts)
            node.set(f"{{{_XML_NS}}}space", "preserve")
    return changed


def _repair_part(
    xml: bytes,
    resolver: _FontResolver,
    converters: dict[str, Callable[[str], str]],
    report: DocxLegacyRunReport,
) -> bytes | None:
    root = etree.fromstring(xml, parser=_PARSER)
    changed = False
    for paragraph in root.iter(_P):
        for segment in _segments(paragraph, resolver):
            if any(piece.map_key for piece in segment):
                changed |= _convert_segment(segment, converters, report)
    if not changed:
        return None
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _theme_part(archive: zipfile.ZipFile) -> bytes | None:
    names = sorted(
        name
        for name in archive.namelist()
        if name.startswith("word/theme/") and name.endswith(".xml")
    )
    return archive.read(names[0]) if names else None


def repair_docx_legacy_runs(data: bytes) -> tuple[bytes, DocxLegacyRunReport]:
    """Return ``data`` with every legacy-font run's text converted to Unicode.

    ``data`` is the bytes of a ``.docx`` package. When no run is drawn in a legacy
    face the input is returned unchanged (the same object), so callers can test
    ``result is data``. Raises ``zipfile.BadZipFile`` / ``lxml`` errors on a malformed
    package; :class:`~likhit.converters.legacy_font_docx.LegacyFontDocxConverter`
    treats any failure as "no repair" and converts the original.
    """

    report = DocxLegacyRunReport()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        styles = archive.read("word/styles.xml") if "word/styles.xml" in names else None
        resolver = _FontResolver(styles, _theme_part(archive))
        converters: dict[str, Callable[[str], str]] = {}
        replaced: dict[str, bytes] = {}
        for name in names:
            if _TEXT_PART.match(name):
                repaired = _repair_part(
                    archive.read(name), resolver, converters, report
                )
                if repaired is not None:
                    replaced[name] = repaired
        if not replaced:
            return data, report
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
            for info in archive.infolist():
                payload = replaced.get(info.filename)
                if payload is None:
                    payload = archive.read(info.filename)
                target.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED)
    if report.converted:
        logger.info(
            "likhit: converted legacy-font runs in .docx: %s",
            dict(report.converted_chars_by_font),
        )
    return output.getvalue(), report


def docx_font_census(data: bytes) -> Counter[str]:
    """Characters of text per effective ASCII-slot font, for diagnostics.

    Counts only non-whitespace, non-Devanagari characters -- the ones whose font
    decides whether they are keystrokes -- so a Kalimati document reports its Latin
    case numbers, not its body text.
    """

    census: Counter[str] = Counter()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        styles = archive.read("word/styles.xml") if "word/styles.xml" in names else None
        resolver = _FontResolver(styles, _theme_part(archive))
        for name in names:
            if not _TEXT_PART.match(name):
                continue
            root = etree.fromstring(archive.read(name), parser=_PARSER)
            for paragraph in root.iter(_P):
                paragraph_fonts = resolver.paragraph_fonts(paragraph)
                for run in _paragraph_runs(paragraph):
                    fonts = resolver.run_fonts(run, paragraph_fonts)
                    for child in run:
                        if child.tag in _TEXT_TAGS and child.text:
                            for char in child.text:
                                if _COMPLEX_SCRIPT.match(char) or char.isspace():
                                    continue
                                font = fonts.ascii if ord(char) < 0x80 else fonts.h_ansi
                                census[font or "(unset)"] += 1
    return census
