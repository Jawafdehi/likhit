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
#
# ⚠️ Adding a family here is not free even given that gating, because the repair can
# also *refuse* a document (see kalimati._INCIDENTAL_FACE_GLYPH_SHARE), so each one is
# added on its own and measured over its whole affected population rather than a
# sample. What bounds the risk empirically: `kalimati` already fires on 4,879 corpus
# documents of which 4,502 audit `clean`, and they stay clean.
#
# ⚠️ This set once ALSO scoped the authored-repha refusal, so routing a family silently
# turned that guard off for it. It does not any more: the guard is keyed on whether the
# reconstruction places any repha at all (`kalimati._reconstruction_supplies_no_repha`),
# which is a property of the reconstruction rather than of the family. Adding a family
# here therefore changes only when the repair is ATTEMPTED, which is what this set should
# ever have meant.
#
#   kokila  -- ROUTED. 64 no-gate documents, all of them measured against f7dd065 on the
#              bare extractor probe: 28 verdicts better, 0 worse; `matra_damage` 29
#              better and 0 worse (33 -> 24 non-clean, `malformed_conjunct_ra`
#              1,327 -> 10); canonical repha words 34,300 -> 34,411. On the 55-document
#              stratified sample of the 1,861 documents where the repair already ran,
#              verdicts and every axis are 0 better / 0 worse and canonical is +43, with
#              2 of 55 transcripts differing at all.
#              It only became landable behind that guard. Unguarded, four documents
#              (5471, 5487, 5492, 5493) lost CORRECTLY SPELLED words -- `आर्थिक`
#              49/52/56/62 -> 2/2/2/5 -- because the repair rewrites their MANGAL CMaps,
#              not their Kokila ones, and a metric guess overwrote an authored `र्`. With
#              the guard all four are restored to 49/52/56/62 and `repha_loss` is `clean`
#              on all four, while the matra gain is untouched.
#              Two things earlier records said about this that MEASUREMENT REFUTED:
#              the four are NOT separated from the other 60 by base repha count -- that
#              count runs 93-5,392 over the 64 and the four sit at 2,566-2,912, inside
#              the bulk; and `_INCIDENTAL_FACE_GLYPH_SHARE` is not "the same shape", it
#              refuses a whole document over an *unrepairable named* face and cannot
#              reach this case at all.
#              The repair's Kokila corroboration logic (`_has_corroborated_kokila_half_sa`,
#              `_has_proven_kokila_half_tha_outline`, `_kokila_displacement_corrections`)
#              is unreachable without this entry.
#   mangal  -- ROUTED, and its outline reference table is what made that safe.
#              2,874 documents carry a Mangal face by their OWN name table; 425 carry no
#              Kalimati or Lohit at all, so nothing opened the gate for them (295 clean,
#              96 suspect, 34 garbled -- and those 34 were the entire matra-garbled
#              cohort). Table: `mangal_reference.OUTLINE_TO_UNICODE`, 962 corroborated
#              outlines, answering 98.2% of the glyph instances Mangal draws corpus-wide.
#              WHY THE TABLE IS LOAD-BEARING AND NOT AN OPTIMISATION. Routing alone, on
#              those 425: matra +78/-0 but repha_loss -20, `repha_corrupt` 9,482 ->
#              18,346, and 20,915 Devanagari characters LOST -- the classic trade. With
#              the table, the same 425: 74 verdicts better, 0 worse, matra +80/-0,
#              repha_loss +19/-0, `repha_corrupt` 9,482 -> 61, +98,918 Devanagari. At
#              document grain routing alone takes 0 of the 34 garbled to clean; with the
#              table 32 of 34 reach clean and none stays garbled. So the trade was never
#              a property of routing -- it was incompleteness.
#              THE GUARD INTERACTION, WHICH NO LONGER EXISTS. When the guard was scoped by
#              this set, routing a family turned it off for that family, and kokila's
#              protection came from the guard covering the MANGAL faces those documents
#              also carry -- so this entry should have re-broken 5471/5487/5492/5493. It
#              did not: all four stayed `clean` with `आर्थिक` at 49/52/56/62, byte-identical
#              with and without the entry, because the table answers those glyphs EXACTLY
#              so they were never metric-guessed. The guard has since been re-keyed on
#              whether the reconstruction places any repha, which removes the coupling
#              rather than relying on that measurement holding.
#              Measured together over the 34 matra-garbled documents plus those four:
#              20 garbled -> clean, 1 garbled -> suspect, 0 worse; `repha_corrupt`
#              9,318 -> 10; `malformed_conjunct_ra` 325 -> 0; repha-bearing canonical
#              words +165.7%.
#              RESIDUE, and it is the guard's case not the table's: a CMap with a
#              COMPENSATING SWAP, where a CID is drawn but lies outside its own subset's
#              glyph range, so it has no outline and no outline-keyed table can ever reach
#              it. 559 such instances corpus-wide; one document (2511) loses three words.
#              Record: work/2026-08-29-v19-mangal-table/_recon/measurements/.
#   nirmala -- 75 documents, 1 garbled.  arial unicode -- 100, 0 garbled.  utsaah -- 5.
_KNOWN_BROKEN_CMAP = {"kalimati", "lohit", "kokila", "mangal"}

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
    """Resolve resource base names through each embedded font's own name table.

    A document-wide binding cannot represent two identities for the same resource
    base name, so conflicting embedded claims are logged and left unresolved.
    """

    resolved: dict[str, str] = {}
    conflicted_bases: set[str] = set()
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
            if legacy_maps.is_legacy_font(resource_name):
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
            if map_key is None or base in conflicted_bases:
                continue
            previous = resolved.get(base)
            if previous is None:
                resolved[base] = map_key
            elif previous != map_key:
                logger.warning(
                    "Font '%s' has conflicting embedded legacy identities %s and "
                    "%s; declining its document-scoped binding",
                    base,
                    previous,
                    map_key,
                )
                resolved.pop(base)
                conflicted_bases.add(base)
    return resolved


def _embedded_broken_cmap_family(font_bytes: bytes) -> str | None:
    """The :data:`_KNOWN_BROKEN_CMAP` family this embed's own name table claims."""

    for _name_id, value in _embedded_name_candidates(font_bytes):
        lowered = value.lower()
        for family in _KNOWN_BROKEN_CMAP:
            if family in lowered:
                return family
    return None


def resolve_embedded_broken_cmap(doc: fitz.Document) -> dict[str, str]:
    """Resource base names whose EMBEDDED name table claims a broken-CMap family.

    :func:`classify_font` can only read the PDF *resource* name, and a producer is
    free to make that name say nothing: a document may embed Kalimati as
    ``CIDFont+F2``. The resource name is not the font's identity -- the embedded
    ``name`` table is -- so the family match is repeated against it here, exactly as
    :func:`resolve_embedded_legacy_maps` already does for the legacy registry.

    Measured over the 97 documents the published v1.3 audit fails on ``repha_loss``:
    only 7 trip the resource-name gate, while **71 embed a Kalimati face under a name
    that does not say so**. Those 71 never reached ``fix_kalimati_cmap`` at all,
    because :func:`~likhit.nepali_pdf_repair.extract_repaired_text_blocks` gates the
    whole repair on some font classifying ``broken_cmap``.

    Naming a font here still only *attempts* the repair, as the module comment on
    :data:`_KNOWN_BROKEN_CMAP` says: the rewrite is gated on the reconstructed
    mapping disagreeing with the PDF's own CMap, so a document whose CMap is fine is
    left byte-identical and merely pays for a second extraction pass.

    Returns ``{resource base name: family}``. Only fonts that classify ``correct``
    are probed -- a font already routed to ``legacy_remap`` must not be downgraded,
    and one already ``broken_cmap`` needs no further evidence.
    """

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
            xref, ext, font_type, resource_name = (
                font_info[0],
                font_info[1],
                font_info[2],
                str(font_info[3]),
            )
            base = (
                resource_name.split("+", 1)[-1]
                if "+" in resource_name
                else resource_name
            )
            if base in resolved:
                continue
            if classify_font(resource_name, font_type) != "correct":
                continue
            if ext in ("n/a", ""):
                # No embedded program, so no name table to ask. A bare core font
                # is never one of these families.
                continue

            if xref in seen_xrefs:
                family = seen_xrefs[xref]
            else:
                try:
                    extracted = doc.extract_font(xref, named=True)
                except Exception:  # noqa: BLE001 - an unextractable embed is no evidence
                    extracted = None
                content = extracted.get("content") if extracted else None
                family = _embedded_broken_cmap_family(content) if content else None
                seen_xrefs[xref] = family
            if family is None:
                continue
            resolved[base] = family
            logger.debug(
                "Font '%s' (xref %s) embeds a %s face -> broken_cmap",
                base,
                xref,
                family,
            )
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


def scan_pdf_fonts(
    doc: fitz.Document,
    embedded_broken_cmap: dict[str, str] | None = None,
) -> dict[str, str]:
    """Scan all PDF fonts and return a strategy per unique base font name.

    The embedded-name binding is resolved here when the caller does not supply one,
    because this function's result is what
    :func:`~likhit.nepali_pdf_repair.extract_repaired_text_blocks` gates the entire
    CMap repair on. Leaving it to the caller made the resource name the whole
    identity, which silently withheld the repair from every document that embeds a
    broken-CMap face under a name that does not say so.
    """

    if embedded_broken_cmap is None:
        embedded_broken_cmap = resolve_embedded_broken_cmap(doc)

    font_strategies: dict[str, str] = {}

    for page_index in range(doc.page_count):
        page = doc[page_index]
        for font_info in page.get_fonts(full=True):
            _xref, _ext, font_type, name, _encoding = font_info[:5]
            base = name.split("+", 1)[-1] if "+" in name else name
            if base in font_strategies:
                continue
            strategy = classify_font(name, font_type)
            if strategy == "correct" and base in embedded_broken_cmap:
                strategy = "broken_cmap"
            font_strategies[base] = strategy
            logger.debug("Font '%s' (type=%s) -> %s", base, font_type, strategy)

    return font_strategies


def scan_pdf_fonts_by_page(
    doc: fitz.Document,
    embedded_legacy_maps: dict[str, str] | None = None,
    embedded_broken_cmap: dict[str, str] | None = None,
) -> dict[int, dict[str, str]]:
    """Scan fonts by page, including document-scoped embedded-name bindings."""

    if embedded_broken_cmap is None:
        embedded_broken_cmap = resolve_embedded_broken_cmap(doc)

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
                # The legacy registry wins: it is the higher-priority strategy, and
                # a font it claims decodes by keystroke map rather than by CMap
                # repair. Only a font it does NOT claim falls through to the
                # broken-CMap binding below.
                strategy = "legacy_remap"
            elif strategy == "correct" and base in embedded_broken_cmap:
                strategy = "broken_cmap"
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
