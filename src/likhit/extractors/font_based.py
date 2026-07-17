"""Font-based extraction for Nepali PDFs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import re
from pathlib import Path

import fitz

from likhit.errors import ExtractionError, ScannedPdfError, ValidationError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment
from likhit.extractors.font_classifier import (
    SCANNED_DECOY_TEXT,
    classify_font,
    is_core_font_name,
    scan_ocr_pages,
    scan_pdf_fonts_by_page,
)
from likhit.extractors.kalimati import (
    fix_kalimati_cmap,
    normalize_devanagari_spacing,
    reorder_devanagari,
)
from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    get_converter,
    get_converter_for_map,
)
from likhit.extractors.tables import detect_page_tables, merge_continuation_tables
from likhit.models import Table


PAGE_RANGE_PATTERN = re.compile(r"^\d+(?:-\d+)?$")
SPAN_GAP_THRESHOLD = 0.75
_PREFIX_IKAR_PATTERN = re.compile(r"(?:(?<=^)|(?<=[\s(]))ि(?=[\u0915-\u0939])")
_INVALID_IKAR_PATTERN = re.compile(r"ि(?=[ािीुूृॄेैोौंःँ])")
_HALANT_IKAR_PATTERN = re.compile(r"्ि")
_DUPLICATE_CONSONANT_PATTERN = re.compile(r"([क-ह])\1")
_SUSPICIOUS_ARTIFACT_PATTERN = re.compile(
    r"(ख्ज|अधध|धिरूद्ध|धिरुद्ध|प्रविधध|राविय|नम्िर|िडा|ितन|उज्वल|उज्जवल)"
)
# Devanagari signs that are valid Unicode but essentially never occur in real
# Nepali: short-O (U+094A) and the nukta-form consonants NNNA/RRA/LLLA
# (U+0929/0931/0934). They are produced almost exclusively by a mis-applied
# legacy-font byte map (e.g. Preeti read as WinAnsi), so they are a reliable
# signal that a fragment is garbled even when the rest looks Devanagari.
# NOTE: candra-O (U+0949 ॉ) is deliberately EXCLUDED — it appears in legitimate
# Nepali/Hindi loanwords (डॉलर "dollar", कॉल "call", डॉक्टर "doctor"), so
# flagging it would penalise clean text. The remaining signs have no such use.
_INVALID_SIGN_PATTERN = re.compile(r"[ॊऩऱऴ]")


def parse_page_range(spec: str, total_pages: int) -> tuple[int, int]:
    """Parse a 1-based inclusive page range to 0-based bounds."""

    if not PAGE_RANGE_PATTERN.fullmatch(spec.strip()):
        raise ValidationError("Invalid page range format. Use format: '1-3' or '5'")

    if "-" in spec:
        start_text, end_text = spec.split("-", 1)
        start = int(start_text)
        end = int(end_text)
    else:
        start = end = int(spec)

    if start < 1 or end < 1 or end < start:
        raise ValidationError("Invalid page range format. Use format: '1-3' or '5'")

    if start > total_pages:
        raise ValidationError(
            f"Requested page range starts beyond document length ({total_pages} pages)"
        )

    end = min(end, total_pages)
    return start - 1, end - 1


def normalize_press_release_paragraph(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    normalized = text
    normalized = re.sub(r"^\ufffd(?=\s)", "-", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s+([।,:;])", r"\1", normalized)
    if re.fullmatch(r"प्रेस\s+विज्ञ\S*", normalized):
        return "प्रेस विज्ञप्ति"
    return normalized


def join_words_with_spacing(words: list[str]) -> str:
    """Reconstruct a line from extracted word tokens."""

    return " ".join(word.strip() for word in words if word.strip())


def join_spans_with_layout(
    spans: list[tuple[float, float, float, float, str]],
) -> str:
    """Reconstruct a line from positioned spans without forcing spaces inside words."""

    if not spans:
        return ""

    parts: list[str] = []
    previous_x1: float | None = None
    for x0, _y0, x1, _y1, text in spans:
        if not text:
            continue
        if (
            previous_x1 is not None
            and x0 - previous_x1 > SPAN_GAP_THRESHOLD
            and parts
            and not parts[-1].endswith((" ", "\t"))
            and not text.startswith((" ", "\t"))
        ):
            parts.append(" ")
        parts.append(text)
        previous_x1 = x1

    return "".join(parts)


def normalize_extracted_word(text: str) -> str:
    """Normalize a single extracted token without touching inter-word spacing."""

    normalized = reorder_devanagari(text)
    normalized = normalize_devanagari_spacing(normalized)
    return normalized.strip()


def _line_key(fragment: TextFragment) -> tuple[int, int, int]:
    return fragment.page_number, fragment.block_number, fragment.line_number


def _private_use_count(text: str) -> int:
    return sum(1 for char in text if 0xE000 <= ord(char) <= 0xF8FF)


def _contains_private_use_marker(text: str) -> bool:
    return _private_use_count(text) > 0


def _text_quality_penalty(text: str) -> int:
    return (
        text.count("\ufffd") * 12
        + _private_use_count(text) * 12
        + len(_INVALID_SIGN_PATTERN.findall(text)) * 8
        + len(_PREFIX_IKAR_PATTERN.findall(text)) * 6
        + len(_INVALID_IKAR_PATTERN.findall(text)) * 6
        + len(_HALANT_IKAR_PATTERN.findall(text)) * 4
        + len(_DUPLICATE_CONSONANT_PATTERN.findall(text)) * 3
        + len(_SUSPICIOUS_ARTIFACT_PATTERN.findall(text)) * 8
    )


def _is_garbled_orphan(text: str) -> bool:
    """True if a fragment with no clean counterpart is clearly legacy-font garble.

    Used only to decide whether to DROP an unpaired fragment during variant
    merging, so it is deliberately conservative: it fires only when the text
    carries the unambiguous mis-map signals (replacement char, private-use
    glyphs, or invalid Devanagari signs) AND those signals are dense relative to
    the Devanagari content. Clean Nepali has zero invalid signs, so this never
    triggers on readable text.
    """
    stripped = text.strip()
    if not stripped:
        return True
    invalid = (
        stripped.count("�")
        + _private_use_count(stripped)
        + len(_INVALID_SIGN_PATTERN.findall(stripped))
    )
    if invalid == 0:
        return False
    devanagari = sum(1 for char in stripped if 0x0900 <= ord(char) <= 0x097F)
    if devanagari == 0:
        return True
    # >=2 invalid signals, or invalid signs making up a meaningful share of the
    # Devanagari characters, marks a fragment as garble rather than a stray typo.
    return invalid >= 2 or invalid / devanagari >= 0.08


def _has_severe_noise(text: str) -> bool:
    return any(
        (
            "\ufffd" in text,
            _private_use_count(text) > 0,
            bool(_INVALID_SIGN_PATTERN.search(text)),
            bool(_PREFIX_IKAR_PATTERN.search(text)),
            bool(_INVALID_IKAR_PATTERN.search(text)),
            bool(_HALANT_IKAR_PATTERN.search(text)),
        )
    )


def _choose_token_text(original: str, repaired: str) -> str:
    if repaired == original:
        return original

    original_penalty = _text_quality_penalty(original)
    repaired_penalty = _text_quality_penalty(repaired)
    if repaired_penalty < original_penalty:
        return repaired
    if original_penalty < repaired_penalty:
        return original

    return repaired


def _merge_tokenwise(original: str, repaired: str) -> str | None:
    original_tokens = original.split()
    repaired_tokens = repaired.split()
    if len(original_tokens) != len(repaired_tokens):
        return None

    merged_tokens = [
        _choose_token_text(original_token, repaired_token)
        for original_token, repaired_token in zip(original_tokens, repaired_tokens)
    ]
    return " ".join(merged_tokens)


def _choose_fragment_text(original: str, repaired: str | None) -> str:
    if repaired is None or repaired == original:
        return original

    candidates: list[tuple[str, int, int]] = [
        (repaired, 1, len(repaired.strip())),
        (original, 2, len(original.strip())),
    ]
    merged = None
    if _has_severe_noise(original) or _has_severe_noise(repaired):
        merged = _merge_tokenwise(original, repaired)
    if merged and merged not in {original, repaired}:
        candidates.append((merged, 0, len(merged.strip())))

    best_text, _rank, _length = min(
        candidates,
        key=lambda item: (_text_quality_penalty(item[0]), item[1], -item[2]),
    )
    return best_text


def _merge_fragment_variants(
    original_fragments: list[TextFragment],
    repaired_fragments: list[TextFragment],
) -> list[TextFragment]:
    repaired_by_key = {_line_key(fragment): fragment for fragment in repaired_fragments}
    merged: list[TextFragment] = []

    for fragment in original_fragments:
        repaired = repaired_by_key.pop(_line_key(fragment), None)
        if repaired is None and _is_garbled_orphan(fragment.text):
            # An original-only fragment (no repaired counterpart to compare
            # against) that is itself severely garbled is a legacy-font
            # mis-map duplicate of text already captured by another fragment.
            # Keeping it produces the "clean line + garbled tail" artifact, so
            # drop it rather than emitting unreadable Devanagari.
            continue
        merged.append(
            replace(
                fragment,
                text=_choose_fragment_text(
                    fragment.text,
                    repaired.text if repaired is not None else None,
                ),
            )
        )

    merged.extend(
        fragment
        for fragment in repaired_by_key.values()
        if not _is_garbled_orphan(fragment.text)
    )
    return sorted(
        merged,
        key=lambda fragment: (
            fragment.page_number,
            round(fragment.y0, 2),
            fragment.x0,
            fragment.block_number,
            fragment.line_number,
        ),
    )


def _raw_document_from_fragments(
    fragments: list[TextFragment],
    tables: list[Table],
) -> RawDocument:
    paragraphs = [fragment.text for fragment in fragments if fragment.text.strip()]
    return RawDocument(
        paragraphs=paragraphs,
        raw_text="\n\n".join(paragraphs).strip(),
        fragments=fragments,
        tables=merge_continuation_tables(tables),
    )


# --- Part B: content-based (name-agnostic) legacy-font detection ---------------
#
# The font name alone cannot tell a legacy-font span apart when the producer
# mislabels an embedded Preeti glyf as a generic core font ("Helvetica"). We
# detect it from CONTENT: try every legacy map on the font's aggregate text and
# accept a remap only when the output validates as real Nepali. Validation is
# deliberately anchor/dictionary based, NOT Devanagari-ratio based — every
# legacy map emits Devanagari code points from any ASCII, so a high Devanagari
# ratio is a mirage (proven on the CIB decoy layer, which yields ~0.95 ratio yet
# zero real words under all five maps).

_DEVANAGARI_CHAR = re.compile(r"[ऀ-ॿ]")

# Common Nepali admin/legal words, each >= 4 Devanagari code points so they do
# not appear by chance inside garble. A genuine mislabeled-Preeti document hits
# several of these; a wrong-map read of scanned-page junk hits none.
_CONTENT_LEGACY_DICTIONARY: frozenset[str] = frozenset(
    {
        "नेपाल",
        "सरकार",
        "गरेको",
        "गरेका",
        "गरिएको",
        "भएको",
        "अनुसार",
        "अनुसन्धान",
        "कार्यालय",
        "मन्त्रालय",
        "प्रतिवादी",
        "निर्णय",
        "सम्बन्धी",
        "सम्बन्धमा",
        "अदालत",
        "मुद्दा",
        "भ्रष्टाचार",
        "प्रहरी",
        "आयोग",
        "आरोप",
        "दायर",
        "विषय",
        "जिल्ला",
        "काठमाडौं",
        "प्रदेश",
        "कारबाही",
        "बरामद",
        "रहेको",
        "फैसला",
        "विरुद्ध",
        "निजले",
        "रकम",
        "हिनामिना",
        "मिति",
    }
)

# Accept gate thresholds. Calibrated so hand-built real Preeti keystrokes pass
# (hits >= 2, penalty-per-Devanagari ~0.0) while CIB decoy text fails under all
# five maps (hits == 0, penalty-per-Devanagari 0.09-0.17).
_CONTENT_LEGACY_MIN_HITS = 2
_CONTENT_LEGACY_MAX_PENALTY_PER_DEVA = 0.05
_CONTENT_LEGACY_MIN_DEVA_RATIO = 0.6
_CONTENT_LEGACY_MIN_DEVA = 8


def _span_base_font(font_name: str) -> str:
    """Base font name with any subset prefix stripped (matches _convert_span_text)."""

    return font_name.split("+", 1)[-1] if "+" in font_name else font_name


def _is_probably_legacy_ascii(text: str) -> bool:
    """True if ``text`` looks like raw legacy-font keystrokes (ASCII, no Devanagari)."""

    stripped = text.strip()
    if len(stripped) < _CONTENT_LEGACY_MIN_DEVA:
        return False
    if _DEVANAGARI_CHAR.search(stripped):
        return False
    printable_ascii = sum(1 for char in stripped if 0x20 <= ord(char) < 0x7F)
    return printable_ascii / len(stripped) >= 0.8


def _nepali_validity(text: str) -> dict[str, float]:
    """Score how much ``text`` reads as genuine Nepali (higher = more valid)."""

    devanagari = len(_DEVANAGARI_CHAR.findall(text))
    non_space = len(re.sub(r"\s", "", text)) or 1
    penalty = _text_quality_penalty(text)
    hits = sum(1 for word in _CONTENT_LEGACY_DICTIONARY if word in text)
    return {
        "devanagari": devanagari,
        "ratio": devanagari / non_space,
        "penalty_per_deva": penalty / devanagari if devanagari else float("inf"),
        "hits": hits,
    }


def _passes_content_legacy_gate(validity: dict[str, float]) -> bool:
    return (
        validity["hits"] >= _CONTENT_LEGACY_MIN_HITS
        and validity["devanagari"] >= _CONTENT_LEGACY_MIN_DEVA
        and validity["ratio"] >= _CONTENT_LEGACY_MIN_DEVA_RATIO
        and validity["penalty_per_deva"] <= _CONTENT_LEGACY_MAX_PENALTY_PER_DEVA
    )


def choose_legacy_map(text: str) -> tuple[str | None, dict[str, float] | None]:
    """Pick the best legacy map for ``text`` if one validates, else ``(None, best)``.

    Tries every :data:`ALL_MAP_KEYS` map and keeps the candidate with the most
    dictionary hits (ties broken by lower penalty). Returns the winning map key
    only when it clears :func:`_passes_content_legacy_gate`; otherwise the map
    key is ``None`` (the second element is the best-scoring validity for
    diagnostics).
    """

    best_key: str | None = None
    best: dict[str, float] | None = None
    for map_key in ALL_MAP_KEYS:
        try:
            converted = get_converter_for_map(map_key)(text)
        except Exception:
            continue
        validity = _nepali_validity(converted)
        if best is None or (validity["hits"], -validity["penalty_per_deva"]) > (
            best["hits"],
            -best["penalty_per_deva"],
        ):
            best, best_key = validity, map_key
    if best is not None and _passes_content_legacy_gate(best):
        return best_key, best
    return None, best


def detect_content_legacy_fonts(
    doc: fitz.Document,
    skip_pages: frozenset[int] = frozenset(),
) -> dict[str, str]:
    """Map base-font name -> legacy map key for mislabeled legacy fonts.

    Considers only bare Latin core fonts that the name-based classifier calls
    "correct" and whose aggregate span text reads as raw legacy keystrokes.
    ``skip_pages`` (1-based) excludes scanned-decoy pages — so this never rescues
    the CIB junk layer (Part A owns those) — and any page outside the requested
    extraction range.
    """

    # Cheap pre-check on font metadata: unless a bare Latin core font is present
    # somewhere, there is nothing to reinterpret, so skip the expensive per-page
    # text-dict pass entirely (the common pure-Unicode Nepali PDF hits this).
    core_font_names: set[str] = set()
    for page_index in range(doc.page_count):
        if (page_index + 1) in skip_pages:
            continue
        for font_info in doc[page_index].get_fonts(full=True):
            if is_core_font_name(str(font_info[3])):
                core_font_names.add(_span_base_font(str(font_info[3])))
    if not core_font_names:
        return {}

    text_by_font: dict[str, list[str]] = defaultdict(list)
    for page_index in range(doc.page_count):
        if (page_index + 1) in skip_pages:
            continue
        page = doc[page_index]
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for block in page_dict["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    base = _span_base_font(str(span["font"]))
                    text_by_font[base].append(str(span["text"]))

    content_maps: dict[str, str] = {}
    for base, parts in text_by_font.items():
        if base not in core_font_names:
            continue
        if classify_font(base, "") != "correct":
            continue
        aggregate = "".join(parts)
        if not _is_probably_legacy_ascii(aggregate):
            continue
        map_key, _validity = choose_legacy_map(aggregate)
        if map_key is not None:
            content_maps[base] = map_key
    return content_maps


class FontBasedStrategy(ExtractionStrategy):
    """Extract text from Nepali PDFs using PyMuPDF blocks."""

    def extract_text(self, file_path: str, pages: str | None = None) -> RawDocument:
        return self._extract_raw_document(file_path, pages=pages)

    def extract_tables(self, file_path: str) -> list[Table]:
        return self._extract_raw_document(file_path).tables

    def _extract_raw_document(
        self,
        file_path: str,
        pages: str | None = None,
    ) -> RawDocument:
        path = Path(file_path)
        if path.suffix.lower() != ".pdf":
            raise ValidationError("Unsupported file format. Please upload a PDF file")
        if not path.exists():
            raise ValidationError(f"File not found: {file_path}")

        try:
            doc = fitz.open(path)
        except Exception as exc:
            raise ExtractionError(
                "Unable to parse PDF. File may be corrupted or encrypted"
            ) from exc

        repaired_doc: fitz.Document | None = None
        try:
            page_start, page_end = 0, doc.page_count - 1
            if pages:
                page_start, page_end = parse_page_range(pages, doc.page_count)

            font_strategies_by_page = scan_pdf_fonts_by_page(doc)
            has_broken_cmap = any(
                strategy == "broken_cmap"
                for page_strategies in font_strategies_by_page.values()
                for strategy in page_strategies.values()
            )

            # Part A: pages that are a scanned raster (with or without a decoy
            # core-font text layer) carry no born-digital text and need OCR.
            ocr_pages = scan_ocr_pages(doc)
            in_range = range(page_start + 1, page_end + 2)
            needs_ocr_pages = sorted(page for page in ocr_pages if page in in_range)
            decoy_pages = frozenset(
                page
                for page, marker in ocr_pages.items()
                if marker == SCANNED_DECOY_TEXT and page in in_range
            )
            # Part B: bare Latin core fonts that actually carry legacy keystrokes.
            # Skip OCR pages AND pages outside the requested range, so text the
            # caller never asked for cannot flip the content-map gate and corrupt
            # in-range extraction (mirrors needs_ocr_pages/decoy_pages scoping).
            skip_for_content = frozenset(ocr_pages) | frozenset(
                page for page in range(1, doc.page_count + 1) if page not in in_range
            )
            content_legacy_maps = detect_content_legacy_fonts(doc, skip_for_content)

            raw_document = self._extract_from_document(
                doc,
                font_strategies_by_page,
                page_start=page_start,
                page_end=page_end,
                needs_reorder=False,
                decoy_pages=decoy_pages,
                content_legacy_maps=content_legacy_maps,
            )
            if has_broken_cmap:
                repaired_source = fitz.open(path)
                try:
                    repaired_doc, needs_reorder = fix_kalimati_cmap(repaired_source)
                finally:
                    if repaired_source is not repaired_doc:
                        try:
                            repaired_source.close()
                        except ValueError:
                            pass
                repaired_document = self._extract_from_document(
                    repaired_doc,
                    font_strategies_by_page,
                    page_start=page_start,
                    page_end=page_end,
                    needs_reorder=needs_reorder,
                    decoy_pages=decoy_pages,
                    content_legacy_maps=content_legacy_maps,
                )
                raw_document = _raw_document_from_fragments(
                    _merge_fragment_variants(
                        raw_document.fragments,
                        repaired_document.fragments,
                    ),
                    repaired_document.tables or raw_document.tables,
                )

            raw_document.needs_ocr_pages = needs_ocr_pages

            if not raw_document.raw_text:
                if needs_ocr_pages:
                    raise ScannedPdfError(
                        "PDF has no recoverable text layer; needs OCR",
                        needs_ocr_pages,
                    )
                raise ExtractionError("No text content found in document")

            return raw_document
        except (ExtractionError, ValidationError):
            raise
        except Exception as exc:
            raise ExtractionError(
                f"Failed to extract text from PDF: {path.name}"
            ) from exc
        finally:
            if repaired_doc is not None:
                repaired_doc.close()
            doc.close()

    def _extract_from_document(
        self,
        doc: fitz.Document,
        font_strategies_by_page: dict[int, dict[str, str]],
        *,
        page_start: int,
        page_end: int,
        needs_reorder: bool,
        decoy_pages: frozenset[int] = frozenset(),
        content_legacy_maps: dict[str, str] | None = None,
    ) -> RawDocument:
        paragraphs: list[str] = []
        fragments: list[TextFragment] = []
        tables: list[Table] = []
        table_index = 0

        for page_index in range(page_start, page_end + 1):
            if (page_index + 1) in decoy_pages:
                # Scanned raster with a non-embedded core-font decoy layer: its
                # text is legacy-keystroke garbage, so drop the whole page and
                # leave it for the caller's OCR path (see needs_ocr_pages).
                continue
            page = doc[page_index]
            page_font_strategies = font_strategies_by_page.get(page_index + 1, {})
            page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            lines_by_key: dict[
                tuple[int, int], list[tuple[float, float, float, float, str]]
            ] = defaultdict(list)
            for block_number, block in enumerate(page_dict["blocks"]):
                if "lines" not in block:
                    continue
                for line_number, line in enumerate(block["lines"]):
                    for span in line["spans"]:
                        text = self._convert_span_text(
                            str(span["text"]),
                            str(span["font"]),
                            page_font_strategies,
                            needs_reorder,
                            content_legacy_maps=content_legacy_maps,
                        )
                        if not text:
                            continue
                        x0, y0, x1, y1 = span["bbox"]
                        lines_by_key[(block_number, line_number)].append(
                            (
                                float(x0),
                                float(y0),
                                float(x1),
                                float(y1),
                                text,
                            )
                        )

            page_fragments: list[TextFragment] = []
            previous_y1: float | None = None
            for (block_number, line_number), line_words in sorted(
                lines_by_key.items(),
                key=lambda item: (
                    round(min(piece[1] for piece in item[1]), 2),
                    min(piece[0] for piece in item[1]),
                ),
            ):
                ordered_words = sorted(line_words, key=lambda piece: piece[0])
                line_text = join_spans_with_layout(ordered_words)
                paragraph = normalize_press_release_paragraph(line_text)
                if not paragraph:
                    previous_y1 = None
                    continue

                x0 = min(piece[0] for piece in ordered_words)
                y0 = min(piece[1] for piece in ordered_words)
                x1 = max(piece[2] for piece in ordered_words)
                y1 = max(piece[3] for piece in ordered_words)
                gap_before = None
                if previous_y1 is not None:
                    gap_before = y0 - previous_y1
                previous_y1 = y1

                fragment = TextFragment(
                    text=paragraph,
                    page_number=page_index + 1,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    block_number=block_number,
                    line_number=line_number,
                    gap_before=gap_before,
                )
                paragraphs.append(paragraph)
                page_fragments.append(fragment)

            fragments.extend(page_fragments)
            page_tables = detect_page_tables(page, page_fragments, table_index)
            tables.extend(page_tables)
            table_index += len(page_tables)

        return RawDocument(
            paragraphs=paragraphs,
            raw_text="\n\n".join(paragraphs).strip(),
            fragments=fragments,
            tables=merge_continuation_tables(tables),
        )

    def _convert_span_text(
        self,
        text: str,
        font_name: str,
        font_strategies: dict[str, str],
        needs_reorder: bool,
        content_legacy_maps: dict[str, str] | None = None,
    ) -> str:
        base = _span_base_font(font_name)
        strategy = font_strategies.get(base, "correct")

        # Decoy suppression happens page-level in _extract_from_document (decoy
        # pages are skipped wholesale), so no span-level decoy branch is needed.
        if content_legacy_maps:
            content_map_key = content_legacy_maps.get(base)
            if content_map_key is not None:
                return get_converter_for_map(content_map_key)(text)

        if strategy == "legacy_remap":
            converter = get_converter(font_name)
            if converter is not None:
                return converter(text)
            return text

        if needs_reorder and (
            strategy == "broken_cmap" or _contains_private_use_marker(text)
        ):
            text = reorder_devanagari(text)
            text = normalize_devanagari_spacing(text)
        return text
