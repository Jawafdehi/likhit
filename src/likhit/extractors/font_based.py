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
    get_output_converter_for_map,
)
from likhit.extractors.numeric_boundaries import (
    apply_line_numeric_boundary_repairs,
    collect_page_repairs_by_line,
)
from likhit.extractors.pua_maps import (
    KNOWN_UNMAPPABLE,
    is_symbol_pua_font,
    remap_symbol_pua,
    unlift_symbol_pua,
)
from likhit.extractors.tables import detect_page_tables, merge_continuation_tables
from likhit.models import Table


PAGE_RANGE_PATTERN = re.compile(r"^\d+(?:-\d+)?$")
SPAN_GAP_THRESHOLD = 0.75
# Zeroed ToUnicode maps otherwise collapse every unknown glyph to the same
# replacement character. Raw CIDs keep those glyphs distinct for later repair.
# This word REPLACES PyMuPDF's default rather than adding to it, and that is
# deliberate: OR-ing `TEXTFLAGS_RAWDICT` in stops all CID marking and deletes
# 1,250,148 glyphs corpus-wide. Do not "fix" it -- see
# `test_text_dict_flags_replace_the_default_and_must_not_be_made_additive`.
_TEXT_DICT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
# A raw CID is an arbitrary code point: observed values include 0x7a, an ordinary
# ASCII "z". Nothing distinguishes one from real text, so the glyphs stay
# distinct (which is the point) but every garble heuristic stops seeing them.
# Marked CIDs are offset into Supplementary Private Use Area A, which keeps them
# distinct AND inside the private-use range `_private_use_count` already counts.
# Plane 15 holds 0xFFFE code points, so any 16-bit CID fits.
_CID_MARK_BASE = 0xF0000
_MAX_MARKABLE_CID = 0xFFFD
_MARKED_CID_PATTERN = re.compile(r"[\U000F0000-\U000FFFFD]")
_PREFIX_IKAR_PATTERN = re.compile(r"(?:(?<=^)|(?<=[\s(]))ि(?=[\u0915-\u0939])")
_INVALID_IKAR_PATTERN = re.compile(r"ि(?=[ािीुूृॄेैोौंःँ])")
_HALANT_IKAR_PATTERN = re.compile(r"्ि")
_DUPLICATE_CONSONANT_PATTERN = re.compile(r"([क-ह])\1")
# Two identical adjacent consonants are a real garble signal, but adjacency ALONE
# is mostly wrong: in Nepali a stem ending in a consonant plus a suffix beginning
# with the same one is ordinary morphology. Measured over all 6,223 documents of
# `markdown-quality-v11` (VOL-135, `oag-corpus/runs/vol135/`), the bare pattern
# fires 1,087,029 times in 99.4% of documents, and adjudicating every one of the
# 34,684 distinct doublet-bearing words against the corpus's own 1.87M-word
# vocabulary put ~4 of every 5 hits on correct Nepali.
#
# The doublet is NOT droppable, though: the same sweep found >=209,998 occurrences
# of genuine damage, dominated by legacy i-matra loss resurfacing as a doubled
# consonant (`खररद`->`खरिद`, `ववरण`->`विवरण`, `आन्तररक`->`आन्तरिक`), whose clean forms
# are attested 100,000-800,000 times each. Down-weighting the pattern would give
# that up.
#
# So charge adjacency only when it is not explained by morphology. The two lists
# below are closed and auditable, and were chosen by scoring thirteen candidate
# rules against that adjudication (`runs/vol135/score_rules.py`): this one keeps
# 91.6% of the true damage while removing 88.6% of the spurious mass. Note the
# co-signal reading that suggests itself first -- "charge only if the token already
# shows an i-matra anomaly" -- scores 9.0% recall and is not usable.
_MORPHEME_SUFFIXES = (
    "को",
    "मा",
    "ता",
    "ताको",
    "ताले",
    "तामा",
    "सँग",
    "सङ्ग",
    "हरु",
    "हरू",
    "रेट",
    "योजना",
)
# Correct Nepali whose doubled consonant is word-internal rather than at a
# morpheme boundary, so no suffix test can reach it.
_DOUBLED_CONSONANT_LEXEMES = (
    "व्यय",
    "कक्षा",
    "अध्ययन",
    "तत्काल",
    "ललितपुर",
    "ससुरा",
    "बबरमहल",
    "उत्खनन",
    "जज",
    "ननाघे",
    "तत्सम",
    "सम्ममा",
    "ससर्त",
    "छैनन्",
    "तहहरु",
    "कार्ययोजना",
)
_DEVANAGARI_WORD_PATTERN = re.compile(r"[ऀ-ॿ]+")
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
# Escapes, not literals, for the same reason as _ORPHAN_MATRA_PATTERN in
# converters/nepali_pdf.py: U+0929/0931/0934 are composition exclusions that every
# normalization form decomposes to <base, U+093C NUKTA>. Written literally this
# class normalizes into a FIVE-member set that includes the bare consonants, so a
# garble detector would start firing on three of the commonest Nepali letters.
# Measured on 70 characters of clean prose: 0 hits as written, 11 after
# normalization, every one a false positive.
_INVALID_SIGN_PATTERN = re.compile(r"[\u094a\u0929\u0931\u0934]")


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


def _iter_dict_spans(page_dict: dict) -> list[dict]:
    """Flatten a page dict to its spans in document order."""

    return [
        span
        for block in page_dict.get("blocks", [])
        if "lines" in block
        for line in block["lines"]
        for span in line["spans"]
    ]


def _char_position(char: dict) -> tuple[float, ...]:
    """Position key pairing one character across two extractions of a page.

    Rounded because the two extractions agree on glyph geometry to well within
    a hundredth of a point, but not always bit-for-bit.
    """

    return tuple(round(value, 2) for value in char["bbox"])


def _replacement_and_decoded_positions(
    page_dict: dict,
) -> tuple[set[tuple[float, ...]], set[tuple[float, ...]]]:
    """Split a raw page dict's character positions by whether they decoded."""

    replacement: set[tuple[float, ...]] = set()
    decoded: set[tuple[float, ...]] = set()
    for span in _iter_dict_spans(page_dict):
        for char in span.get("chars", ()):
            target = replacement if char["c"] == "�" else decoded
            target.add(_char_position(char))
    return replacement, decoded


def _to_dict_shape(page_dict: dict) -> dict:
    """Collapse a `rawdict` page to `dict` shape: span `text`, no `chars`.

    `dict` mode's span text is exactly the concatenation of `rawdict`'s per-glyph
    characters, so callers cannot tell which mode produced the page.
    """

    for span in _iter_dict_spans(page_dict):
        if "chars" in span:
            span["text"] = "".join(char["c"] for char in span.pop("chars"))
    return page_dict


def mark_unmappable_cids(text: str) -> str:
    """Offset every character of `text` into the marked-CID range."""

    return "".join(
        chr(_CID_MARK_BASE + ord(char)) if ord(char) <= _MAX_MARKABLE_CID else char
        for char in text
    )


def strip_marked_cids(text: str, replacement: str = "�") -> str:
    """Render marked CIDs back to a visible replacement character."""

    return _MARKED_CID_PATTERN.sub(replacement, text)


def count_marked_cids(text: str) -> int:
    """Count marked CIDs in `text`.

    Kept deliberately, though nothing in `src/` calls it since marks stopped
    being charged in the candidate comparison. It is the only way to observe how
    much a page failed to decode without re-deriving the mark range, which is
    what the tests and the corpus instruments use it for.
    """

    return len(_MARKED_CID_PATTERN.findall(text))


def get_cid_marked_page_dict(page: fitz.Page) -> dict:
    """Extract a page dict whose unmappable glyphs are marked as private-use.

    A page with nothing unmappable is extracted once, exactly as before. Only a
    page that actually decodes some glyph to U+FFFD pays a second extraction, and
    the two are paired to learn *which* characters the CID flag substituted --
    the flag alone cannot tell us, because a raw CID is indistinguishable from
    real text.

    Pairing is positional over individual glyph boxes, not over spans. Spans are
    the wrong unit: dropping the CID flag regroups them, so the two extractions
    routinely disagree on span count even though every glyph still sits at the
    same coordinates. Span-level pairing therefore failed wholesale on real
    documents -- measured on the Nepali audit corpus, 38 of 40 sampled pages had
    mismatched span counts and came back with no marking at all, which is the
    exact blindness this marking exists to remove. Glyph boxes survive the
    regrouping: the same measurement pairs 98.6% of replacement characters.

    A position that decodes to real text somewhere on the page and to U+FFFD
    somewhere else cannot be attributed to either, so it keeps its raw CID rather
    than being guessed at.
    """

    # Bit 128 is dropped here on purpose: this pass detects unmappable glyphs BY
    # their U+FFFD, and `TEXTFLAGS_RAWDICT` already sets the CID bit, so making
    # this word additive returns zero U+FFFD and silently ends every marking below.
    plain_dict = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    replacement, decoded = _replacement_and_decoded_positions(plain_dict)
    if not replacement:
        return _to_dict_shape(plain_dict)

    unmappable = replacement - decoded
    cid_dict = page.get_text("rawdict", flags=_TEXT_DICT_FLAGS)
    for span in _iter_dict_spans(cid_dict):
        for char in span.get("chars", ()):
            if _char_position(char) in unmappable:
                char["c"] = mark_unmappable_cids(char["c"])
    return _to_dict_shape(cid_dict)


def normalize_press_release_paragraph(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    normalized = text
    # Any unmappable glyph opening a list item is a bullet, whatever CID it came
    # from. Enumerating CIDs does not converge: the law-report sample alone emits
    # two (0x83 and 0x7a, an ASCII "z" that no literal class would ever cover).
    #
    # VOL-704 adds two things to the class. U+E000-U+F8FF covers a symbol-font
    # bullet that reached here unmapped -- an unregistered font. U+2022/U+25AA/U+27A2
    # are the real bullet characters `pua_maps` resolves Symbol and Wingdings to, and
    # they need converting for the same reason the CIDs do.
    #
    # 🛑 A codepoint in `pua_maps.KNOWN_UNMAPPABLE` is EXCLUDED, and the exclusion is
    # a decision rather than an oversight. This comment used to name that dict as an
    # intended target of the class, while the dict's own docstring promises the
    # opposite -- that such glyphs "stay in the output and keep being counted ... a
    # glyph we cannot faithfully represent should remain visible as a gap". Both
    # could not be true. The recorded human judgement wins over the positional guess:
    # `KNOWN_UNMAPPABLE` is an explicit per-codepoint finding, this rule is a
    # heuristic, and rewriting such a glyph to "- " would assert we know what it is
    # while deleting the audit's only evidence that we do not. The cost of getting it
    # this way round is a missing list marker; the other way round it is a silently
    # destroyed damage signal, which is the failure mode this corpus keeps paying for.
    #
    # Dormant today -- `KNOWN_UNMAPPABLE` is empty since U+F093 resolved to U+1F668 --
    # so this guards the next entry, which is when it would otherwise have bitten
    # silently. Raised in review of #64.
    #
    # Position is what decides this, not identity, and the split is deliberate: a
    # LEADING bullet is document *structure*, so it becomes "- " -- real Markdown
    # list syntax that a parser sees as a list, which is the point of a corpus
    # meant to be machine-readable. An INLINE bullet is *content* and is left as
    # the literal glyph, because rewriting a mid-sentence bullet as a hyphen would
    # corrupt the sentence. Measured on the CIAA corpus: 2,227 of the 4,210 U+F0B7
    # are leading.
    # The private-use part of this class is U+F020-U+F0FF, i.e. pua_maps.SYMBOL_PUA_RANGE,
    # NOT the whole BMP private-use area. A full \ue000-\uf8ff class also matches
    # kalimati._PUA_REPH (U+F000) and _PUA_IKAR (U+F001), and this rule fires on
    # position: a sentinel that reached the start of a line followed by whitespace was
    # rewritten to "- ", destroying it and disguising the failure as a list item.
    # Verified both ways -- U+F0B7, the bullet this rule exists for and the corpus's most
    # common private-use character at 4,210 occurrences, is inside F020-F0FF; both
    # sentinels are below it. The agreement with SYMBOL_PUA_RANGE is asserted in
    # tests/test_pua_maps.py so the two cannot drift.
    leading = normalized[:1]
    if not (leading and ord(leading) in KNOWN_UNMAPPABLE):
        normalized = re.sub(
            r"^[\ufffd\u2022\u25aa\u27a2\uf020-\uf0ff\U000F0000-\U000FFFFD](?=\s)",
            "-",
            normalized,
        )
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
    # Plane 15 is included because marked CIDs live there: a glyph the font could
    # not map is damage whether it arrived as a private-use code point from a
    # legacy map or as a CID we marked.
    return sum(
        1
        for char in text
        if 0xE000 <= ord(char) <= 0xF8FF
        or _CID_MARK_BASE <= ord(char) <= _CID_MARK_BASE + _MAX_MARKABLE_CID
    )


def _contains_private_use_marker(text: str) -> bool:
    return _private_use_count(text) > 0


def _duplicate_consonant_count(text: str) -> int:
    """Doubled consonants that morphology does not explain. See the pattern's note.

    Scoped to one Devanagari word at a time because both tests are about where the
    doublet sits inside a word. A doublet can never span a word boundary -- both
    halves are Devanagari, so they are always inside the same maximal run -- which
    makes this exactly the bare pattern's match set, minus the excused ones.
    """
    total = 0
    for word_match in _DEVANAGARI_WORD_PATTERN.finditer(text):
        word = word_match.group(0)
        matches = list(_DUPLICATE_CONSONANT_PATTERN.finditer(word))
        if not matches:
            continue
        # Excuse only the doublets that fall INSIDE a lexeme, not every doublet in a
        # word that happens to contain one. Devanagari compounds carry no internal
        # space, so a garbled token can hold a listed lexeme and unrelated damage at
        # once -- measured: "कक्षा" + "गग" scored 0 when the whole word was
        # excused, hiding damage that scores 1 on its own. Under-charging matters as
        # much as over-charging here, because this term helps decide which legacy map
        # wins.
        lexeme_spans = [
            (m.start(), m.end())
            for lexeme in _DOUBLED_CONSONANT_LEXEMES
            for m in re.finditer(re.escape(lexeme), word)
        ]
        for match in matches:
            if any(
                start <= match.start() and match.end() <= end
                for start, end in lexeme_spans
            ):
                continue
            # The doublet's second consonant opening a known suffix means the pair
            # straddles a morpheme boundary: `\u0915\u094d\u0930\u092e` + `\u092e\u093e`, `...\u0915` + `\u0915\u094b`. It only
            # counts as a boundary if a real stem precedes it -- a single bare
            # consonant is not a Nepali stem, which is what separates the damage
            # `\u0915\u0915\u094b` -> `\u0915\u094b` and `\u092e\u092e\u093e` -> `\u092e\u093e` from `\u092e\u0939\u093e\u0932\u0947\u0916\u093e\u092a\u0930\u0940\u0915\u094d\u0937\u0915\u0915\u094b` and `\u0915\u094d\u0930\u092e\u092e\u093e`.
            stem_len = match.start() + 1
            if stem_len >= 2 and word[match.start() + 1 :].startswith(
                _MORPHEME_SUFFIXES
            ):
                continue
            total += 1
    return total


def _text_quality_penalty(text: str) -> int:
    return (
        text.count("\ufffd") * 12
        + _private_use_count(text) * 12
        + len(_INVALID_SIGN_PATTERN.findall(text)) * 8
        + len(_PREFIX_IKAR_PATTERN.findall(text)) * 6
        + len(_INVALID_IKAR_PATTERN.findall(text)) * 6
        + len(_HALANT_IKAR_PATTERN.findall(text)) * 4
        + _duplicate_consonant_count(text) * 3
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
        # This path has no document handle, so the pages it covered can only be
        # recovered from what it produced.
        page_numbers=sorted(
            {fragment.page_number for fragment in fragments}
            | {region.page_number for table in tables for region in table.regions}
        ),
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
        except ExtractionError:
            # A missing/broken npttf2utf is a real config error — surface it
            # rather than silently disabling Part B (the name-based path raises
            # the same way), so behavior does not depend on the font name.
            raise
        except Exception:  # noqa: BLE001 - this map does not fit; try the next
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

    Considers every font the name-based classifier calls "correct" whose
    aggregate span text reads as raw legacy keystrokes and then validates as
    Nepali under one of the legacy maps. ``skip_pages`` (1-based) excludes
    scanned-decoy pages — so this never rescues the CIB junk layer (Part A owns
    those) — and any page outside the requested extraction range.

    The candidate set is deliberately name-agnostic, because a legacy 8-bit face
    reaches a PDF under whatever name its producer's subsetter invented. OAG's
    2070 annual report carries its body font as ``TT339t00`` and the 2067-2072
    reports carry theirs as ``Spins``; neither is a standard-14 core name, so
    restricting candidates to core fonts left 436 pages of Preeti keystrokes
    undecoded. Widening the *name* registry instead would not be safe: the same
    documents put their clause numbers ("179", "23.2") in a companion font named
    ``Spins_EXT`` / ``TT33At00`` -- 85% ASCII digits, zero dictionary hits under
    every map -- which a substring match on the name would remap into garbage.
    Content is the only signal that separates the two, and the gate below is
    where it is applied.
    """

    considered_pages = [
        page_index
        for page_index in range(doc.page_count)
        if (page_index + 1) not in skip_pages
    ]

    # Aggregate span text per FULL font name (subset prefix included) so a
    # mislabeled-Preeti embedded font ("ABCDE+Helvetica") is decided separately
    # from a genuine bare core font ("Helvetica") of the same family — mapping
    # one must not corrupt the other's spans.
    #
    # There is no metadata pre-check in front of this pass. The one that used to
    # stand here ("skip unless a standard-14 core font is present") is what hid
    # the subset-named faces above, and it bought very little: 79 of 80 sampled
    # corpus documents carry a core font somewhere and paid for the pass anyway.
    # So the pass is now unconditional, and the ~1% of documents that used to
    # skip it pay one extra text-dict pass (~10ms per page).
    text_by_font: dict[str, list[str]] = defaultdict(list)
    for page_index in considered_pages:
        page_dict = get_cid_marked_page_dict(doc[page_index])
        for block in page_dict["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text_by_font[str(span["font"])].append(str(span["text"]))

    content_maps: dict[str, str] = {}
    for font_name, parts in text_by_font.items():
        # A font the name-based classifier already routes (legacy_remap, or a
        # broken-CMap family) is left to that path; this is only for fonts it
        # calls "correct".
        if classify_font(font_name, "") != "correct":
            continue
        aggregate = "".join(parts)
        if not _is_probably_legacy_ascii(aggregate):
            continue
        map_key, _validity = choose_legacy_map(aggregate)
        if map_key is not None:
            content_maps[font_name] = map_key
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

            # On a broken-CMap PDF this document is extracted twice, before and
            # after the ToUnicode repair, and the merge below keeps the repaired
            # pass's tables whenever it found any. Table detection is the single
            # largest cost in extraction (67-87% of wall time on these documents),
            # so detecting in the first pass is usually pure waste: measured
            # across 28 corpus documents, the repaired pass found tables every
            # time and the first pass's were always discarded. Skip it here and
            # detect below only if the repaired pass comes back empty.
            #
            # The results cannot simply be shared between passes: PyMuPDF derives
            # a table's header from the page's decoded text, so on a broken-CMap
            # page the repair changes header.external, header.bbox and
            # header.names -- and captions feed the continuation-merge decision.
            detect_tables = not has_broken_cmap

            raw_document = self._extract_from_document(
                doc,
                font_strategies_by_page,
                page_start=page_start,
                page_end=page_end,
                needs_reorder=False,
                decoy_pages=decoy_pages,
                content_legacy_maps=content_legacy_maps,
                detect_tables=detect_tables,
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
                tables = repaired_document.tables
                if not tables:
                    # The repaired pass found nothing, so fall back to detecting
                    # on the unrepaired document -- preserving the behaviour of
                    # the `repaired.tables or raw.tables` merge this replaces.
                    tables = self._extract_from_document(
                        doc,
                        font_strategies_by_page,
                        page_start=page_start,
                        page_end=page_end,
                        needs_reorder=False,
                        decoy_pages=decoy_pages,
                        content_legacy_maps=content_legacy_maps,
                    ).tables
                raw_document = _raw_document_from_fragments(
                    _merge_fragment_variants(
                        raw_document.fragments,
                        repaired_document.fragments,
                    ),
                    tables,
                )

            raw_document.needs_ocr_pages = needs_ocr_pages
            # The requested range is authoritative, not the pages that happened
            # to yield text. A suppressed scanned-raster page produces no
            # fragments, so deriving this from output would drop exactly the
            # pages `needs_ocr_pages` says need OCR merged in.
            raw_document.page_numbers = list(in_range)

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
        detect_tables: bool = True,
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
            numeric_repairs = collect_page_repairs_by_line(
                page,
                page_number=page_index + 1,
            )
            page_dict = get_cid_marked_page_dict(page)
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
                line_text = apply_line_numeric_boundary_repairs(
                    line_text,
                    numeric_repairs.get(
                        (page_index + 1, block_number, line_number),
                        (),
                    ),
                )
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
            if detect_tables:
                page_tables = detect_page_tables(page, page_fragments, table_index)
                tables.extend(page_tables)
                table_index += len(page_tables)

        return RawDocument(
            paragraphs=paragraphs,
            raw_text="\n\n".join(paragraphs).strip(),
            fragments=fragments,
            tables=merge_continuation_tables(tables),
            page_numbers=list(range(page_start + 1, page_end + 2)),
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
        # Content-legacy maps are keyed by full font name (subset prefix included)
        # so only the exact mislabeled font resource is remapped.
        if content_legacy_maps:
            content_map_key = content_legacy_maps.get(font_name)
            if content_map_key is not None:
                # Output, not scoring, so this takes the gated converter -- same
                # as the name-based branch below. choose_legacy_map keeps using
                # the raw get_converter_for_map to compare candidates (VOL-166).
                return get_output_converter_for_map(content_map_key)(
                    unlift_symbol_pua(text)
                )

        if strategy == "legacy_remap":
            converter = get_converter(font_name)
            if converter is not None:
                # VOL-704: un-lift first. A legacy font whose cmap is symbol-style
                # ("ARAP 11") hands us byte + 0xF000 instead of the byte, so the
                # converter would otherwise see private-use characters it has no
                # entry for and pass the whole span through untouched -- which is
                # exactly how 1,363 glyphs of Nepali text shipped as U+F0xx.
                # A no-op for every legacy font likhit already handled, since
                # those arrive as ASCII keystrokes.
                return converter(unlift_symbol_pua(text))
            return text

        # VOL-704: a legacy SYMBOL font (Symbol, Wingdings) classifies "correct",
        # because as far as the name-based classifier is concerned nothing is
        # wrong with it -- so without this branch its private-use codepoints fall
        # through untouched to the bare `return text` below. Placed after the
        # legacy-Devanagari branches on purpose: `pua_maps` must never see a
        # Devanagari font, since U+F020 and U+F029 are emitted by both Symbol and
        # ARAP 11 in this corpus and mean different things in each.
        if is_symbol_pua_font(font_name):
            return remap_symbol_pua(text, font_name)

        if needs_reorder and (
            strategy == "broken_cmap" or _contains_private_use_marker(text)
        ):
            text = reorder_devanagari(text)
            text = normalize_devanagari_spacing(text)
        return text
