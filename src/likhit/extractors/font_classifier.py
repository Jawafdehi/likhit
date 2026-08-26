"""Font strategy classification for extraction."""

from __future__ import annotations

import logging
from io import BytesIO

import fitz
from fontTools.ttLib import TTFont

from likhit.pdf_page_analysis import analyze_text_quality, page_max_image_coverage

from . import legacy_maps

logger = logging.getLogger(__name__)

# Font families whose embeds in this corpus routinely ship a broken ToUnicode
# CMap, so a document using one is extracted a second time after repair.
#
# Naming a family here only *attempts* the repair; it does not force one. The
# rewrite is gated on the reconstructed mapping actually disagreeing with the
# PDF's own CMap (see kalimati.fix_kalimati_cmap), so a document carrying one of
# these fonts with a correct CMap is left byte-identical -- it just pays for the
# second extraction pass.
_KNOWN_BROKEN_CMAP = {"kalimati", "lohit"}

# Page-level OCR markers. A "scanned_decoy_text" page is a full-page raster whose
# only text layer is non-embedded core-font garbage (see cib-press-release
# extraction doc); an "image_only" page is a raster with no text layer at all.
# These are page-level OCR markers, handled by scan_ocr_pages and the decoy-page
# skip — they are never stored as a font-level strategy.
SCANNED_DECOY_TEXT = "scanned_decoy_text"
IMAGE_ONLY = "image_only"

_STRATEGY_PRIORITY = {
    "correct": 0,
    "broken_cmap": 1,
    "legacy_remap": 2,
}

# Standard-14 core font families. A PDF may reference these WITHOUT embedding a
# font program, in which case a viewer substitutes a local font. Scanner tools
# that flatten a page to an image sometimes leave behind a decoy text layer set
# in a bare (non-embedded, no-ToUnicode) core font whose bytes are legacy
# keystrokes — never real Unicode.
_CORE_FONT_FAMILIES = (
    "helvetica",
    "arial",
    "times",
    "courier",
    "symbol",
    "zapfdingbats",
)
_CORE_FONT_SIMPLE_SUBTYPES = {"Type1", "MMType1", "TrueType"}

# A page counts as image-dominant at this coverage (matches
# ``PdfPageAnalysis.is_image_dominant``); combined with the strict core-font +
# non-Nepali text-layer signature this cleanly separates scanned CIB releases
# (coverage >= 0.99) from born-digital Nepali PDFs (coverage <= 0.69 in-corpus).
_SCANNED_IMAGE_COVERAGE = 0.85
# A decoy text layer carries essentially no real Devanagari; a handful of stray
# Devanagari code points is tolerated before a page is treated as real text.
_DECOY_MAX_DEVANAGARI = 10


def classify_font(font_name: str, font_type: str) -> str:
    """Classify a PDF font into an extraction strategy."""

    del font_type

    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    base_lower = base.lower().strip()

    if legacy_maps.is_legacy_font(font_name):
        return "legacy_remap"

    for name in _KNOWN_BROKEN_CMAP:
        if name in base_lower:
            return "broken_cmap"

    return "correct"


_EMBEDDED_NAME_IDS: tuple[int, ...] = (16, 1, 6, 4)


def _embedded_name_candidates(font_bytes: bytes) -> list[tuple[int, str]]:
    """Return usable embedded names in deterministic name-ID precedence."""

    try:
        font = TTFont(
            BytesIO(font_bytes),
            fontNumber=0,
            lazy=True,
            ignoreDecompileErrors=True,
        )
    except Exception:  # noqa: BLE001 - a broken embed is simply no evidence
        return []
    try:
        records = font["name"].names
    except Exception:  # noqa: BLE001 - a font may have no usable name table
        return []

    candidates: list[tuple[int, str]] = []
    for record in records:
        if record.nameID not in _EMBEDDED_NAME_IDS:
            continue
        try:
            value = record.toUnicode()
        except Exception:  # noqa: BLE001 - retain a byte-wise fallback
            try:
                value = record.string.decode("latin-1", "replace")
            except Exception:  # noqa: BLE001
                continue
        value = (value or "").strip()
        if value:
            candidates.append((record.nameID, value))
    candidates.sort(key=lambda pair: _EMBEDDED_NAME_IDS.index(pair[0]))
    return candidates


def resolve_embedded_legacy_maps(doc: fitz.Document) -> dict[str, str]:
    """Resolve resource base names through each embedded font's own name table."""

    resolved: dict[str, str] = {}
    seen_xrefs: dict[int, str | None] = {}
    for page_index in range(doc.page_count):
        try:
            fonts = doc[page_index].get_fonts(full=True)
        except Exception:  # noqa: BLE001 - one malformed page is no evidence
            continue
        for font_info in fonts:
            if len(font_info) < 4:
                continue
            xref, ext, base_font = font_info[0], font_info[1], font_info[3]
            resource_name = str(base_font)
            base = (
                resource_name.split("+", 1)[-1]
                if "+" in resource_name
                else resource_name
            )
            if base in resolved or legacy_maps.is_legacy_font(resource_name):
                continue
            if ext in ("n/a", ""):
                continue

            if xref in seen_xrefs:
                map_key = seen_xrefs[xref]
            else:
                map_key = None
                try:
                    extracted = doc.extract_font(xref, named=True)
                except Exception:  # noqa: BLE001 - an unextractable embed is no evidence
                    extracted = None
                content = extracted.get("content") if extracted else None
                if content:
                    for name_id, value in _embedded_name_candidates(content):
                        candidate = legacy_maps.match_legacy_map_name(value)
                        if candidate is None:
                            continue
                        map_key = candidate
                        logger.debug(
                            "Font '%s' (xref %s) embedded nameID %s = '%s' -> %s",
                            base,
                            xref,
                            name_id,
                            value,
                            candidate,
                        )
                        break
                seen_xrefs[xref] = map_key
            if map_key is not None:
                resolved[base] = map_key
    return resolved


def _core_font_family(base_font_name: str) -> str | None:
    """Return the core-font family for a ``/BaseFont`` name, else ``None``."""

    base = base_font_name.lstrip("/")
    base = base.split("+", 1)[-1] if "+" in base else base
    base = base.split(",")[0].split("-")[0]
    base_lower = base.lower().strip()
    for family in _CORE_FONT_FAMILIES:
        if base_lower.startswith(family):
            return family
    return None


def is_core_font_name(base_font_name: str) -> bool:
    """True if ``base_font_name`` names a standard-14 core font family."""

    return _core_font_family(base_font_name) is not None


def _is_non_embedded_core_font(doc: fitz.Document, font_info: tuple) -> bool:
    """True for a bare core font: no embedded program and no ToUnicode map.

    ``font_info`` is a ``page.get_fonts(full=True)`` tuple
    ``(xref, ext, type, basefont, refname, encoding)``. This is the exact
    signature of the CIB decoy layer (``/Helvetica`` /WinAnsiEncoding, no
    FontDescriptor, no ToUnicode) and never matches a real embedded Nepali font.
    """

    xref, ext, font_type, base_font, _refname, _encoding = font_info[:6]
    if ext not in ("n/a", ""):
        # An embedded font program is present -> a real, trustworthy font.
        return False
    if font_type not in _CORE_FONT_SIMPLE_SUBTYPES:
        return False
    if not is_core_font_name(str(base_font)):
        return False
    # Encoding is intentionally NOT constrained: the subtype (a simple, non-CID
    # font) + a standard-14 base name + no embedded program + no ToUnicode is
    # already the decoy signature, and requiring a specific encoding let decoys
    # using an exotic/Differences encoding leak through.
    #
    # A ToUnicode CMap means the producer supplied a trustworthy byte->Unicode
    # mapping; the decoy layer deliberately has none.
    if doc.xref_get_key(xref, "ToUnicode")[0] != "null":
        return False
    return True


def classify_ocr_page(doc: fitz.Document, page_index: int) -> str | None:
    """Classify a page as needing OCR, or ``None`` if it has real text.

    Returns :data:`IMAGE_ONLY` for a pure raster with no text layer,
    :data:`SCANNED_DECOY_TEXT` for a raster whose only text is a non-embedded
    core-font decoy that fails Nepali validation, or ``None`` otherwise.
    """

    page = doc[page_index]
    if page_max_image_coverage(page) < _SCANNED_IMAGE_COVERAGE:
        return None

    page_text = page.get_text()
    fonts = page.get_fonts(full=True)
    if not page_text.strip() or not fonts:
        # Image-dominant page with no usable text layer at all.
        return IMAGE_ONLY

    if not all(_is_non_embedded_core_font(doc, font_info) for font_info in fonts):
        # A real embedded font is present -> treat as genuine text, not a decoy.
        return None

    token_count, devanagari_char_count, suspicious_ratio, vowel_poor_ratio = (
        analyze_text_quality(page_text)
    )
    if devanagari_char_count >= _DECOY_MAX_DEVANAGARI:
        return None
    if token_count == 0:
        return IMAGE_ONLY
    is_garbled = suspicious_ratio >= 0.12 or (
        suspicious_ratio >= 0.06 and vowel_poor_ratio >= 0.45
    )
    return SCANNED_DECOY_TEXT if is_garbled else None


def scan_ocr_pages(doc: fitz.Document) -> dict[int, str]:
    """Return ``{1-based page number: OCR marker}`` for pages needing OCR."""

    ocr_pages: dict[int, str] = {}
    for page_index in range(doc.page_count):
        marker = classify_ocr_page(doc, page_index)
        if marker is not None:
            ocr_pages[page_index + 1] = marker
            logger.debug("Page %s classified for OCR: %s", page_index + 1, marker)
    return ocr_pages


def scan_pdf_fonts(doc: fitz.Document) -> dict[str, str]:
    """Scan all PDF fonts and return a strategy per unique base font name."""

    font_strategies: dict[str, str] = {}

    for page_index in range(doc.page_count):
        page = doc[page_index]
        for font_info in page.get_fonts(full=True):
            _xref, _ext, font_type, name, _encoding = font_info[:5]
            base = name.split("+", 1)[-1] if "+" in name else name
            if base in font_strategies:
                continue
            strategy = classify_font(name, font_type)
            font_strategies[base] = strategy
            logger.debug("Font '%s' (type=%s) -> %s", base, font_type, strategy)

    return font_strategies


def scan_pdf_fonts_by_page(
    doc: fitz.Document,
    embedded_legacy_maps: dict[str, str] | None = None,
) -> dict[int, dict[str, str]]:
    """Scan fonts by page, including document-scoped embedded-name bindings."""

    strategies_by_page: dict[int, dict[str, str]] = {}

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_strategies: dict[str, str] = {}
        for font_info in page.get_fonts(full=True):
            _xref, _ext, font_type, name, _encoding = font_info[:5]
            base = name.split("+", 1)[-1] if "+" in name else name
            strategy = classify_font(name, font_type)
            if (
                strategy == "correct"
                and embedded_legacy_maps
                and base in embedded_legacy_maps
            ):
                strategy = "legacy_remap"
            current = page_strategies.get(base)
            if (
                current is None
                or _STRATEGY_PRIORITY[strategy] > _STRATEGY_PRIORITY[current]
            ):
                page_strategies[base] = strategy
                logger.debug(
                    "Page %s font '%s' (type=%s) -> %s",
                    page_index + 1,
                    base,
                    font_type,
                    strategy,
                )
        strategies_by_page[page_index + 1] = page_strategies

    return strategies_by_page
