"""Helpers for repairing Kalimati-encoded Nepali PDFs."""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
from collections.abc import Collection, Container
from typing import Optional

import fitz

from likhit.errors import ExtractionError
from likhit.extractors.kalimati_reference import (
    in_line_ra_cids,
    kalimati_reference_map,
    outline_digest,
)
from likhit.extractors.lohit import lohit_correction_map, with_reordering_markers
from likhit.extractors.mangal_reference import (
    mangal_in_line_ra_cids,
    mangal_reference_map,
)
from likhit.extractors.pua_maps import _base_font_name, _font_name_matches_family

logger = logging.getLogger(__name__)

_PUA_REPH = "\uf000"
_PUA_IKAR = "\uf001"
_PUA_CONTEXTUAL_NE = "\uf002"
_PUA_KOKILA_IKAR = "\uf003"
_PUA_KOKILA_TA = "\uf004"
_PUA_KOKILA_HALF_SA = "\uf005"
_PUA_KOKILA_HALF_THA = "\uf006"
_VIRAMA = "\u094d"
_RA = "\u0930"
_IKAR = "\u093f"
_BROKEN_PURYA_PATTERN = re.compile(r"(?<=पुर्) (?=य)")
# Nya + virama followed by a consonant: a broken conjunct, never a word boundary,
# because nya occurs in Nepali only as the first member of one -- सञ्चालन, पञ्च,
# अञ्चल. Both halves are written as escapes rather than as a literal Devanagari
# class: a literal range in a pattern is normalization-fragile in this repo, where
# one such range has already raised `re.error` at import time and another silently
# widened to bare consonants once its endpoints decomposed. The range below is
# ka..ha, the same bounds `_is_devanagari_consonant` uses.
_BROKEN_NYA_CONJUNCT_PATTERN = re.compile(r"(?<=\u091e\u094d) (?=[\u0915-\u0939])")
_NUKTA = "\u093c"
_CONTEXTUAL_NE_GID = 566
_KOKILA_HALF_SA_GID = 214
_KOKILA_HALF_THA_GID = 195
_KOKILA_YA_GID = 94
_KOKILA_HALF_THA_OUTLINE_DIGESTS = frozenset({"cd36bc7e3b37b80f"})
_KOKILA_DISPLACEMENT_GIDS = frozenset({83, 108})
_KOKILA_LITERAL_TH_STATUS_SEQUENCE = (
    _PUA_KOKILA_IKAR + "\u0925\u0925" + _PUA_KOKILA_IKAR + _PUA_KOKILA_TA
)
_DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097F]")
_SIMPLE_STANDARD_ENCODING_PATTERN = re.compile(
    r"/Encoding\s*/(?:StandardEncoding|MacRomanEncoding|MacExpertEncoding|"
    r"WinAnsiEncoding)\b"
)


def _is_devanagari_consonant(char: str) -> bool:
    return "\u0915" <= char <= "\u0939"


def _is_rakar_base(char: str) -> bool:
    """True for a consonant a below-form ra can attach under.

    Covers the precomposed nukta letters (U+0958-U+095F: ``क़``, ``ढ़``, ``फ़``)
    as well as the plain range, because a font may spell a nukta'd base either
    way and the below-form ra attaches to both. Ra itself is excluded: ra plus a
    below-form ra is not a cluster this swap can reason about.
    """

    return (_is_devanagari_consonant(char) or "क़" <= char <= "य़") and char != _RA


def _is_devanagari_matra(char: str) -> bool:
    return "\u093e" <= char <= "\u094c" or char in {"\u0962", "\u0963"}


def _contains_devanagari_or_marker(text: str) -> bool:
    return bool(_DEVANAGARI_PATTERN.search(text)) or any(
        marker in text for marker in (_PUA_REPH, _PUA_IKAR)
    )


def _trace_value_is_better(pdf_value: str, trace_value: str) -> bool:
    if trace_value == pdf_value:
        return False
    if len(trace_value) > len(pdf_value):
        return True
    if _VIRAMA in trace_value and _VIRAMA not in pdf_value:
        return True
    return False


def _safe_get_best_cmap(font) -> dict[int, str]:
    try:
        if "cmap" not in font:
            return {}
        best_cmap = font["cmap"].getBestCmap()
    except Exception:  # noqa: BLE001 - a malformed cmap table means no mapping
        return {}
    return best_cmap or {}


def _parse_tounicode_cmap(cmap_bytes: bytes | None) -> dict[int, str]:
    # `doc.xref_stream()` returns None when /ToUnicode names an object that is
    # not a stream -- a malformed but readable PDF, which MuPDF reports as
    # "format error: object is not a stream" on stderr and then recovers from.
    # This was annotated `bytes` and dereferenced directly, so such a document
    # raised AttributeError out of the Kalimati repair. The blanket handler in
    # `font_based._extract_raw_document` turned that into ExtractionError, and
    # `nepali_pdf` fell back to pdfminer -- which renders every glyph it cannot
    # decode as U+0000. On OAG document 13006 that fallback shipped 8,834 NULs
    # in place of 8,834 conjuncts and matras, in every generation v6..v12.
    #
    # No ToUnicode mapping is the same situation as a gid missing from one, and
    # the loop over `trace_maps` below already handles that per gid by taking
    # the trace value, so an empty map degrades to the trace fallback rather
    # than losing the document.
    if not cmap_bytes:
        return {}
    text = cmap_bytes.decode("utf-8", errors="replace")
    mapping: dict[int, str] = {}

    for block in re.finditer(r"beginbfchar\s*(.*?)\s*endbfchar", text, re.DOTALL):
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block.group(1)
        ):
            gid = int(match.group(1), 16)
            unicode_hex = match.group(2)
            chars = "".join(
                chr(int(unicode_hex[index : index + 4], 16))
                for index in range(0, len(unicode_hex), 4)
            )
            if chars:
                mapping[gid] = chars

    for block in re.finditer(r"beginbfrange\s*(.*?)\s*endbfrange", text, re.DOTALL):
        content = block.group(1)
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
            content,
            re.DOTALL,
        ):
            start, end = int(match.group(1), 16), int(match.group(2), 16)
            for index, hex_value in enumerate(
                re.findall(r"<([0-9A-Fa-f]+)>", match.group(3))
            ):
                gid = start + index
                if gid > end:
                    break
                chars = "".join(
                    chr(int(hex_value[offset : offset + 4], 16))
                    for offset in range(0, len(hex_value), 4)
                )
                if chars:
                    mapping[gid] = chars

        cleaned = re.sub(r"<[^>]+>\s*<[^>]+>\s*\[.*?\]", "", content, flags=re.DOTALL)
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", cleaned
        ):
            start, end = int(match.group(1), 16), int(match.group(2), 16)
            if len(match.group(3)) <= 4:
                unicode_start = int(match.group(3), 16)
                for gid in range(start, end + 1):
                    mapping[gid] = chr(unicode_start + (gid - start))

    return mapping


def _build_cmap_stream(mapping: dict[int, str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    entries = sorted(mapping.items())
    for chunk_start in range(0, len(entries), 100):
        chunk = entries[chunk_start : chunk_start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for gid, unicode_value in chunk:
            hex_gid = f"<{gid:04X}>"
            hex_unicode = (
                "<" + "".join(f"{ord(char):04X}" for char in unicode_value) + ">"
            )
            lines.append(f"{hex_gid} {hex_unicode}")
        lines.append("endbfchar")
    lines.extend(
        [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        ]
    )
    return "\n".join(lines).encode("ascii")


def _analyze_gsub(
    font, glyph_order: list[str], gid_to_correct: dict[int, str]
) -> dict[int, str]:
    if "GSUB" not in font:
        return {}

    gsub = font["GSUB"]
    lookup_features: dict[int, set[str]] = {}

    for feature_record in gsub.table.FeatureList.FeatureRecord:
        tag = feature_record.FeatureTag
        for lookup_index in feature_record.Feature.LookupListIndex:
            lookup_features.setdefault(lookup_index, set()).add(tag)

    glyph_to_gid = {glyph_name: gid for gid, glyph_name in enumerate(glyph_order)}

    derived: dict[int, str] = {}
    ligature_rules: list[tuple[set[str], str, list[str], int]] = []
    for lookup_index, lookup in enumerate(gsub.table.LookupList.Lookup):
        features = lookup_features.get(lookup_index, set())
        for subtable in lookup.SubTable:
            if lookup.LookupType == 1 and hasattr(subtable, "mapping"):
                for from_name, to_name in subtable.mapping.items():
                    from_gid = glyph_to_gid.get(from_name)
                    to_gid = glyph_to_gid.get(to_name)
                    if from_gid is None or to_gid is None:
                        continue
                    from_unicode = gid_to_correct.get(from_gid)
                    if from_unicode is None:
                        continue

                    if features & {"half", "haln"}:
                        derived[to_gid] = from_unicode + _VIRAMA
                    elif features & {"rphf"}:
                        derived[to_gid] = _RA + _VIRAMA
                    elif features & {"blwf"}:
                        derived[to_gid] = _VIRAMA + from_unicode
                    elif features & {"nukt"}:
                        derived[to_gid] = from_unicode + _NUKTA
                    else:
                        derived[to_gid] = from_unicode

            elif lookup.LookupType == 4 and hasattr(subtable, "ligatures"):
                for first_name, ligatures in subtable.ligatures.items():
                    if first_name not in glyph_to_gid:
                        continue
                    for ligature in ligatures:
                        output_gid = glyph_to_gid.get(ligature.LigGlyph)
                        if output_gid is None:
                            continue
                        ligature_rules.append(
                            (features, first_name, list(ligature.Component), output_gid)
                        )

    # A convergent ligature fixpoint resolves at least one new output glyph per
    # pass, so it settles in at most len(ligature_rules) passes. Some embedded
    # fonts have conflicting rules that write the same output_gid with different
    # strings, which makes `changed` oscillate forever, spinning at 100% CPU and
    # never terminating (observed as an unbounded hang on born-digital PDFs that
    # embed several unrelated fonts). Bound the loop so a non-convergent GSUB
    # degrades gracefully — the remaining glyphs keep their last resolved value —
    # instead of never returning.
    max_passes = len(ligature_rules) + 1
    passes = 0
    changed = True
    while changed and passes < max_passes:
        passes += 1
        changed = False
        for features, first_name, component_names, output_gid in ligature_rules:
            component_gids: list[int] = []
            for component_name in [first_name] + component_names:
                component_gid = glyph_to_gid.get(component_name)
                if component_gid is None:
                    component_gids = []
                    break
                component_gids.append(component_gid)
            if not component_gids:
                continue

            pieces: list[str] = []
            for component_index, component_gid in enumerate(component_gids):
                value = gid_to_correct.get(component_gid) or derived.get(component_gid)
                if (
                    value is None
                    and component_index > 0
                    and features & {"half", "haln"}
                    and len(component_gids) == 2
                ):
                    value = _VIRAMA
                if value is None:
                    pieces = []
                    break
                pieces.append(value)
            if not pieces:
                continue

            resolved = "".join(pieces)
            if derived.get(output_gid) == resolved:
                continue
            derived[output_gid] = resolved
            changed = True

    if changed:
        # The pass cap was reached with resolutions still oscillating: this GSUB
        # has conflicting ligature rules (see the bound above). Surface it so the
        # offending font is diagnosable; affected glyphs keep their last value.
        logger.warning(
            "GSUB ligature resolution did not converge within %d passes over "
            "%d rule(s); font has conflicting ligature substitutions.",
            max_passes,
            len(ligature_rules),
        )

    for gid, value in list(derived.items()):
        for index in range(len(value) - 2):
            if not _is_rakar_base(value[index]):
                continue
            # A nukta binds to the consonant in front of it, so it sits between a
            # base and its below-form ra without separating the two. Skipping it
            # is what lets the swap reach a nukta'd base; without this the
            # ligature `छ़` + rakar keeps the component order `छ़र्` instead of
            # `छ़्र`.
            cursor = index + 1
            if value[cursor : cursor + 1] == _NUKTA:
                cursor += 1
            if value[cursor : cursor + 2] == _RA + _VIRAMA:
                derived[gid] = value[:cursor] + _VIRAMA + _RA + value[cursor + 2 :]
                break

    return derived


def _glyph_feature(font, glyph_name: str) -> tuple[int, int, int, int, int, int, int]:
    glyph = font["glyf"][glyph_name]
    advance_width, left_side_bearing = font["hmtx"][glyph_name]
    glyph.recalcBounds(font["glyf"])
    return (
        advance_width,
        left_side_bearing,
        glyph.numberOfContours,
        glyph.xMin,
        glyph.yMin,
        glyph.xMax,
        glyph.yMax,
    )


def _infer_mark_variants(
    font,
    glyph_order: list[str],
    gid_to_correct: dict[int, str],
) -> dict[int, str]:
    candidate_codepoints = {
        ord(_IKAR),
        0x0941,  # ु
        0x0942,  # ू
        0x0947,  # े
        0x0948,  # ै
    }

    best_cmap = _safe_get_best_cmap(font)
    candidate_features: list[tuple[str, tuple[int, int, int, int, int, int, int]]] = []
    for codepoint, glyph_name in best_cmap.items():
        if codepoint not in candidate_codepoints:
            continue
        candidate_features.append((chr(codepoint), _glyph_feature(font, glyph_name)))

    if not candidate_features:
        return {}

    inferred: dict[int, str] = {}
    for gid, glyph_name in enumerate(glyph_order):
        if gid in gid_to_correct:
            continue
        if not glyph_name.startswith("glyph"):
            continue

        current = _glyph_feature(font, glyph_name)
        best_match: str | None = None
        best_distance: int | None = None
        for candidate, feature in candidate_features:
            distance = sum(abs(left - right) for left, right in zip(current, feature))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_match = candidate

        if best_match is None or best_distance is None:
            continue
        if best_distance <= 350:
            inferred[gid] = best_match

    return inferred


def _is_ra_virama_swap(old_value: str, new_value: str) -> bool:
    """True when replacing `old_value` with `new_value` would INVERT a rakar.

    A rakar (below-form ra, the stroke under `त` in `त्र`) orders virama then ra:
    `त` `्` `र`. The reverse order `त` `र` `्` is a repha bound to `त`, which
    reads as a different word -- `प्रति` against `पर्ति`, `मन्त्रालय` against
    `मन्तर्ालय`.

    This predicate is asked at two sites, both comparing the PDF's own
    `/ToUnicode` value (`old_value`) against the value derived from the embedded
    font program (`new_value`), to decide whether the difference is worth acting
    on. It answers only for the direction where the PDF is already right and the
    derivation regressed it:

        old `्र` -> new `र्`   True   the PDF holds the valid rakar; keep it
        old `र्` -> new `्र`   False  the PDF holds the inverted order; FIX it

    DIRECTIONALITY IS THE WHOLE POINT, and it was not always here. VOL-705: this
    returned True for both directions, so `_patch_single_cmap` skipped the second
    case as well and the PDF's inverted order survived into the Markdown. On the
    CIAA 33rd annual report (FY 2079-80) that left 169 structurally invalid
    `[consonant] र ् [matra]` sequences -- `मन्तर्ालय` for `मन्त्रालय` 106 times
    -- plus a larger, well-formed-but-wrong tail (`पर्ति` for `प्रति`) that no
    structural check can see.

    Measured over all 13 CIAA report PDFs (run 384bcc86): the `old र् -> new ्र`
    direction fires 10 times in the 33rd, over 8 distinct GIDs
    (क्र ट्र त्र द्र प्र भ्र श्र ह्र) -- 10 rather than 8 because two of them,
    प्र and श्र, occur in both embedded Kalimati subsets (xref 2469 and 2490) --
    and once in the 32nd. The `old ्र -> new र्` direction fires on **none**, in
    any report. So the branch retained below is the one with no measured hits and
    the branch removed was carrying the entire defect.

    The retained direction is kept rather than dropped because likhit derives
    correction values per font at run time: `_analyze_gsub` reaching a rakar
    through a ligature rule produces ra-then-virama and has to swap it back. Both
    reference tables record the pair it swaps --
    `kalimati_reference.BELOW_FORM_RA_CORRECTIONS` keyed by outline digest and
    `lohit.BELOW_FORM_RA_CORRECTIONS` keyed by CID -- and
    `tests/test_kalimati_reference.py::test_below_form_ra_corrections_are_applied_to_the_table`
    pins the direction of every entry (`derived == र्`, `corrected == ्र`).
    `::test_analyze_gsub_orders_a_rakar_after_its_base` pins the swap itself. If
    that swap ever misses for some subset, the PDF's own correct value is the
    better of the two and this predicate preserves it.
    """

    if len(old_value) != len(new_value) or len(old_value) < 2:
        return False
    for index in range(len(old_value) - 1):
        if (
            old_value[index] == _VIRAMA
            and old_value[index + 1] == _RA
            and new_value[index] == _RA
            and new_value[index + 1] == _VIRAMA
            and old_value[:index] == new_value[:index]
            and old_value[index + 2 :] == new_value[index + 2 :]
        ):
            return True
    return False


_DESCENDANT_REFERENCE = re.compile(r"/DescendantFonts\s*\[?\s*(\d+)\s+\d+\s+R")
_DESCRIPTOR_REFERENCE = re.compile(r"/FontDescriptor\s+(\d+)\s+\d+\s+R")
_FONTFILE2_REFERENCE = re.compile(r"/FontFile2\s+(\d+)\s+\d+\s+R")
_ARRAY_REFERENCE = re.compile(r"(\d+)\s+\d+\s+R")


def _resolve_fontfile2_xref(doc: fitz.Document, type0_xref: int) -> Optional[int]:
    """The xref of a Type0 font's embedded TrueType program, or None.

    `/DescendantFonts` is a one-element array whose element may be written either
    as an indirect reference or as the CIDFont dictionary itself, and that
    dictionary's `/FontDescriptor` may likewise be either. Insisting on the
    indirect form skipped every font written the other way, whatever its program
    held: no correction map, and every glyph left unmapped. Measured over 6,223
    OAG transcripts, fonts written that way account for 834,146 unmapped glyphs
    (29.65% of all of them), and 808,710 of those are in fonts that had kept a
    usable `cmap` -- most also `GSUB` -- and so were recoverable all along.

    Whichever form the dictionaries take, the program is a stream and so an object
    of its own, meaning the reference to it is always indirect. A hop that cannot
    be followed is therefore a hop that was written inline, and its contents are
    already part of the text in hand -- so keep reading from that text rather than
    giving up.

    Reaching the program is necessary but not sufficient for those fonts: they are
    named `CIDFont+F1`-style, so `font_classifier.classify_font` cannot recognise
    them and `_meaningful_cmap_diff_count`'s guard skips a font not named
    "kalimati". Both gates are untouched here.
    """

    def follow(xref: int) -> int:
        obj = doc.xref_object(xref, compressed=False).strip()
        if obj.startswith("["):
            match = _ARRAY_REFERENCE.search(obj)
            if match:
                return int(match.group(1))
        return xref

    text = doc.xref_object(type0_xref, compressed=False)
    descendant = _DESCENDANT_REFERENCE.search(text)
    if descendant:
        text = doc.xref_object(follow(int(descendant.group(1))), compressed=False)
    descriptor = _DESCRIPTOR_REFERENCE.search(text)
    if descriptor:
        text = doc.xref_object(int(descriptor.group(1)), compressed=False)
    fontfile = _FONTFILE2_REFERENCE.search(text)
    return int(fontfile.group(1)) if fontfile else None


def _kalimati_reference_map(font, skip: Container[int] = frozenset()) -> dict[int, str]:
    """Reference-derived ``{CID: Unicode}`` for ``font``, carrying markers.

    :mod:`likhit.extractors.kalimati_reference` records plain Unicode, so the
    visual-order marks are turned into reordering markers here rather than left
    to :func:`_patch_single_cmap`: every marker rule there is conditioned on the
    value the PDF's *broken* CMap supplied, which says nothing useful about a
    value that came from a reference table. Same reasoning, and the same
    transform, as :func:`likhit.extractors.lohit.lohit_correction_map`.

    The one exception is the in-line half-form of ra. It decodes to the same
    ``ra + virama`` string as a repha, so :func:`with_reordering_markers`, which
    keys on the value, would mark it for reordering -- and moving it to the front
    of its cluster turns ``प्र`` into ``र्प``. The reference tells the two apart by
    geometry; see :data:`~likhit.extractors.kalimati_reference.IN_LINE_RA_DIGESTS`.
    """

    return _with_markers(kalimati_reference_map(font, skip=skip), font, in_line_ra_cids)


def _mangal_reference_map(font, skip: Container[int] = frozenset()) -> dict[int, str]:
    """Reference-derived ``{CID: Unicode}`` for a Mangal face, carrying markers.

    Mangal's half of what :func:`_kalimati_reference_map` does for Kalimati, with
    the same contract, the same marker handling and the same in-line-ra
    exemption -- see :mod:`likhit.extractors.mangal_reference`.
    """

    return _with_markers(
        mangal_reference_map(font, skip=skip), font, mangal_in_line_ra_cids
    )


def _with_markers(reference, font, in_line_ra) -> dict[int, str]:
    """Apply reordering markers to a reference map, exempting the in-line ra."""

    repha_valued = {
        gid for gid, value in reference.items() if value.startswith(_RA + _VIRAMA)
    }
    exempt = in_line_ra(font, repha_valued) if repha_valued else set()

    return {
        gid: value if gid in exempt else with_reordering_markers(value)
        for gid, value in reference.items()
    }


def _reference_correction_map(
    font, skip: Collection[int] = frozenset()
) -> dict[int, str]:
    """Reference-derived ``{CID: Unicode}`` from whichever family drew the glyphs.

    Both reference tables are keyed on the glyph OUTLINE and their key sets are
    disjoint -- pinned by
    ``tests/test_mangal_reference.py::test_the_two_reference_tables_share_no_outline``
    -- so which table answers a glyph is decided by the drawing, not by
    precedence. Kalimati is asked first only so the Mangal pass can skip the
    glyphs it already answered, which saves outline hashes; it is not a
    tie-break, and if the two tables ever did collide the test would fail rather
    than the collision being silently resolved here.
    """

    kalimati = _kalimati_reference_map(font, skip=skip)
    if kalimati:
        skip = set(skip) | set(kalimati)
    return {**_mangal_reference_map(font, skip=skip), **kalimati}


def _get_font_correction_map(doc: fitz.Document, type0_xref: int) -> dict[int, str]:
    try:
        from fontTools.ttLib import TTFont
    except ModuleNotFoundError:
        raise ExtractionError(
            "fonttools is required for Kalimati font fixing but is not installed"
        )

    try:
        fontfile_xref = _resolve_fontfile2_xref(doc, type0_xref)
        if fontfile_xref is None:
            return {}

        font_data = doc.xref_stream(fontfile_xref)
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tmp:
                tmp.write(font_data)
                temp_path = tmp.name
            font = TTFont(temp_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        glyph_order = font.getGlyphOrder()
        best_cmap = _safe_get_best_cmap(font)
        if not best_cmap:
            # The subsetter emptied the font's own cmap, so there is nothing
            # here to reconstruct a mapping from. Fall back to a reference
            # table. Lohit's is keyed on CID, which holds because subsetting
            # preserves glyph order and every Lohit subset here descends from
            # one build; Kalimati's and Mangal's are keyed on the glyph outline,
            # because their subsets come from several lineages whose orders
            # disagree -- see likhit.extractors.lohit, .kalimati_reference and
            # .mangal_reference. All return empty for a font they do not
            # recognise, i.e. no repair.
            reference_map = lohit_correction_map(font) or _reference_correction_map(
                font
            )
            font.close()
            return reference_map
        name_to_unicode = {name: codepoint for codepoint, name in best_cmap.items()}

        from_cmap: dict[int, str] = {}
        for gid, glyph_name in enumerate(glyph_order):
            if glyph_name in name_to_unicode:
                from_cmap[gid] = chr(name_to_unicode[glyph_name])

        inferred = _infer_mark_variants(font, glyph_order, from_cmap)
        gid_to_correct = {**from_cmap, **inferred}

        derived = _analyze_gsub(font, glyph_order, gid_to_correct)
        # A subset can keep its cmap and still have GSUB stripped, and GSUB is
        # where the conjuncts and half-forms live -- so the base letters resolve
        # from the cmap above while every conjunct stays unmapped. A reference
        # table fills those, but it only ever speaks for glyphs this font could
        # not resolve itself: skip whatever the font's own cmap or its own GSUB
        # already answered.
        reference = _reference_correction_map(font, skip=set(from_cmap) | set(derived))

        full_map = dict(derived)
        full_map.update(inferred)
        # An outline match is exact -- identical contours are the same drawing --
        # where _infer_mark_variants only matched a glyph's metrics against five
        # candidate marks. So the reference outranks an inferred value, and never
        # the font's own cmap or GSUB.
        for gid, value in reference.items():
            if gid not in from_cmap and gid not in derived:
                full_map[gid] = value
        full_map.update(from_cmap)
        font.close()
        return full_map
    except Exception as exc:  # noqa: BLE001 - degrade to no repair, never fail
        logger.warning(
            "Failed to build Kalimati correction map for xref=%s: %s",
            type0_xref,
            exc,
        )
        return {}


def _get_fontfile_xref(doc: fitz.Document, type0_xref: int) -> Optional[int]:
    try:
        return _resolve_fontfile2_xref(doc, type0_xref)
    except Exception:  # noqa: BLE001 - unparsable xref object means no FontFile2
        return None


def _type0_uses_identity_gid_mapping(doc: fitz.Document, type0_xref: int) -> bool:
    """Whether a Type0 font's character codes are also TrueType glyph IDs."""

    try:
        type0 = doc.xref_object(type0_xref, compressed=False)
        if not re.search(r"/Encoding\s*/Identity-H\b", type0):
            return False

        descendant_match = _DESCENDANT_REFERENCE.search(type0)
        if descendant_match:
            descendant = doc.xref_object(
                int(descendant_match.group(1)),
                compressed=False,
            ).strip()
            if descendant.startswith("["):
                array_match = _ARRAY_REFERENCE.search(descendant)
                if not array_match:
                    return False
                descendant = doc.xref_object(
                    int(array_match.group(1)),
                    compressed=False,
                )
        else:
            marker = "/DescendantFonts"
            if marker not in type0:
                return False
            # Direct arrays may contain the CIDFont dictionary inline. The
            # checks below only need its subtype and CIDToGIDMap entries.
            descendant = type0.split(marker, 1)[1]

        if not re.search(r"/Subtype\s*/CIDFontType2\b", descendant):
            return False
        # For CIDFontType2, an omitted CIDToGIDMap defaults to Identity.
        return "/CIDToGIDMap" not in descendant or bool(
            re.search(r"/CIDToGIDMap\s+/Identity\b", descendant)
        )
    except Exception:  # noqa: BLE001 - malformed font dictionaries fail closed
        return False


def _type0_codes_are_gids(doc: fitz.Document, font_xref: int) -> bool:
    """Whether a Type0 font's ToUnicode codes are also embedded-font GIDs."""

    try:
        text = doc.xref_object(font_xref, compressed=False)
        if not re.search(r"/Encoding\s*/Identity-H\b", text):
            return False

        descendant = _DESCENDANT_REFERENCE.search(text)
        if descendant:
            descendant_text = doc.xref_object(
                int(descendant.group(1)),
                compressed=False,
            ).strip()
            if descendant_text.startswith("["):
                item = _ARRAY_REFERENCE.search(descendant_text)
                if not item:
                    return False
                descendant_text = doc.xref_object(
                    int(item.group(1)),
                    compressed=False,
                )
        else:
            descendant_text = text

        return bool(re.search(r"/CIDToGIDMap\s*/Identity\b", descendant_text))
    except Exception:  # noqa: BLE001 - missing identity evidence means no fill
        return False


def _simple_font_uses_embedded_encoding(
    doc: fitz.Document,
    font_xref: int,
) -> bool:
    """Whether a simple TrueType font maps PDF bytes through its own cmap."""

    try:
        font_dict = doc.xref_object(font_xref, compressed=False)
        if re.search(r"/Encoding\b", font_dict):
            return False

        descriptor = _DESCRIPTOR_REFERENCE.search(font_dict)
        descriptor_text = (
            doc.xref_object(int(descriptor.group(1)), compressed=False)
            if descriptor
            else font_dict
        )
        flags = re.search(r"/Flags\s+(\d+)\b", descriptor_text)
        return bool(flags and int(flags.group(1)) & 4)
    except Exception:  # noqa: BLE001 - missing encoding evidence means no repair
        return False


def _get_simple_font_correction_map(
    doc: fitz.Document,
    font_xref: int,
    gid_correction_map: dict[int, str],
) -> dict[int, str]:
    """Translate a simple TrueType font's GID repairs to PDF character codes.

    A simple TrueType font's content bytes are not its glyph IDs. The embedded
    font's non-Unicode ``cmap`` records the missing bridge: PDF character code to
    glyph name. Subsetters commonly empty the Unicode cmap while retaining this
    Mac format-6 table so a renderer can still select the right drawing.
    """

    if not _simple_font_uses_embedded_encoding(doc, font_xref):
        return {}

    try:
        from fontTools.ttLib import TTFont

        fontfile_xref = _resolve_fontfile2_xref(doc, font_xref)
        if fontfile_xref is None:
            return {}

        font = TTFont(io.BytesIO(doc.xref_stream(fontfile_xref)))
        try:
            glyph_to_gid = {
                glyph_name: gid for gid, glyph_name in enumerate(font.getGlyphOrder())
            }
            code_candidates: dict[int, set[int]] = {}
            if "cmap" in font:
                for table in font["cmap"].tables:
                    if table.isUnicode():
                        continue
                    for code, glyph_name in table.cmap.items():
                        gid = glyph_to_gid.get(glyph_name)
                        if gid is not None:
                            code_candidates.setdefault(code, set()).add(gid)

            return {
                code: gid_correction_map[gid]
                for code, gids in code_candidates.items()
                if len(gids) == 1
                for gid in gids
                if gid in gid_correction_map
            }
        finally:
            font.close()
    except Exception as exc:  # noqa: BLE001 - an unusable cmap means no repair
        logger.warning(
            "Failed to translate simple-font Kalimati map for xref=%s: %s",
            font_xref,
            exc,
        )
        return {}


def _stream_allows_face_specific_gids(
    doc: fitz.Document,
    owner_xrefs: set[int],
    font_names: dict[int, str],
    face: str,
) -> bool:
    """Whether every owner of one shared CMap proves the same face and GID space."""

    if not owner_xrefs or any(
        not _font_name_matches_family(font_names.get(xref, ""), face)
        for xref in owner_xrefs
    ):
        return False
    if any(not _type0_uses_identity_gid_mapping(doc, xref) for xref in owner_xrefs):
        return False
    if len(owner_xrefs) == 1:
        return True

    fontfiles = {_get_fontfile_xref(doc, xref) for xref in owner_xrefs}
    return None not in fontfiles and len(fontfiles) == 1


def _stream_font_program_xref(
    doc: fitz.Document,
    owner_xrefs: set[int],
    font_names: dict[int, str],
    face: str,
) -> int | None:
    """The one safely shared font program behind a face-specific CMap."""

    if not _stream_allows_face_specific_gids(
        doc,
        owner_xrefs,
        font_names,
        face,
    ):
        return None
    fontfiles = {_get_fontfile_xref(doc, xref) for xref in owner_xrefs}
    if None in fontfiles or len(fontfiles) != 1:
        return None
    return next(iter(fontfiles))


def _font_program_gid_outline_digest(
    doc: fitz.Document,
    fontfile_xref: int,
    gid: int,
) -> str | None:
    """Return exact contour evidence for one embedded TrueType glyph."""

    try:
        from fontTools.ttLib import TTFont

        font_data = doc.xref_stream(fontfile_xref)
        if not font_data:
            return None
        font = TTFont(io.BytesIO(font_data), lazy=False)
        try:
            glyph_order = font.getGlyphOrder()
            if gid < 0 or gid >= len(glyph_order):
                return None
            return outline_digest(font, glyph_order[gid])
        finally:
            font.close()
    except Exception:  # noqa: BLE001 - absent or unreadable contours prove nothing
        return None


def _has_corroborated_kokila_gid(
    doc: fitz.Document,
    pdf_maps: dict[int, dict[int, str]],
    candidate_font_owners: dict[int, set[int]],
    font_names: dict[int, str],
    gid: int,
    expected: str,
    *,
    target_to_unicode_xref: int | None = None,
) -> bool:
    """Whether a safe Kokila CMap authors one GID for this target program."""

    target_program = None
    if target_to_unicode_xref is not None:
        target_program = _stream_font_program_xref(
            doc,
            candidate_font_owners[target_to_unicode_xref],
            font_names,
            "kokila",
        )
        if target_program is None:
            return False

    for to_unicode_xref, pdf_map in pdf_maps.items():
        if pdf_map.get(gid) != expected:
            continue
        owners = candidate_font_owners[to_unicode_xref]
        if target_to_unicode_xref is None:
            if _stream_allows_face_specific_gids(
                doc,
                owners,
                font_names,
                "kokila",
            ):
                return True
            continue
        if to_unicode_xref == target_to_unicode_xref:
            continue
        sibling_program = _stream_font_program_xref(
            doc,
            owners,
            font_names,
            "kokila",
        )
        if sibling_program == target_program:
            return True
        # OAG 5604 embeds two separately subsetted copies of the same regular
        # Kokila face. Their program xrefs differ, but GID 214 has byte-identical
        # contours; the sibling's authored half-sa mapping therefore applies to
        # this one glyph without granting its whole map to the target.
        if gid == _KOKILA_HALF_SA_GID and sibling_program is not None:
            target_digest = _font_program_gid_outline_digest(
                doc,
                target_program,
                gid,
            )
            sibling_digest = _font_program_gid_outline_digest(
                doc,
                sibling_program,
                gid,
            )
            if target_digest is not None and target_digest == sibling_digest:
                return True
    return False


def _has_corroborated_kokila_half_sa(
    doc: fitz.Document,
    pdf_maps: dict[int, dict[int, str]],
    candidate_font_owners: dict[int, set[int]],
    font_names: dict[int, str],
    *,
    target_to_unicode_xref: int | None = None,
) -> bool:
    """Whether a safely owned Kokila CMap authors GID 214 as half-sa."""

    return _has_corroborated_kokila_gid(
        doc,
        pdf_maps,
        candidate_font_owners,
        font_names,
        _KOKILA_HALF_SA_GID,
        "\u0938\u094d",
        target_to_unicode_xref=target_to_unicode_xref,
    )


def _has_proven_kokila_half_tha_outline(
    doc: fitz.Document,
    owner_xrefs: set[int],
    font_names: dict[int, str],
) -> bool:
    """Whether this target program carries the measured broken GID-195 outline."""

    font_program = _stream_font_program_xref(
        doc,
        owner_xrefs,
        font_names,
        "kokila",
    )
    if font_program is None:
        return False
    return (
        _font_program_gid_outline_digest(
            doc,
            font_program,
            _KOKILA_HALF_THA_GID,
        )
        in _KOKILA_HALF_THA_OUTLINE_DIGESTS
    )


def _collect_trace_fallback_map(
    doc: fitz.Document,
    font_name: str,
) -> dict[int, str]:
    counts: dict[int, dict[str, int]] = {}

    for page_index in range(doc.page_count):
        for trace in doc[page_index].get_texttrace():
            if trace.get("font") != font_name:
                continue

            current_gid: int | None = None
            current_chars: list[str] = []
            for codepoint, gid, *_rest in trace.get("chars", ()):
                char = chr(codepoint)

                if gid >= 0:
                    if current_gid is not None and current_chars:
                        text = "".join(current_chars)
                        if "\ufffd" not in text:
                            bucket = counts.setdefault(current_gid, {})
                            bucket[text] = bucket.get(text, 0) + 1
                    current_gid = gid
                    current_chars = [char]
                    continue

                if current_gid is not None:
                    current_chars.append(char)

            if current_gid is not None and current_chars:
                text = "".join(current_chars)
                if "\ufffd" not in text:
                    bucket = counts.setdefault(current_gid, {})
                    bucket[text] = bucket.get(text, 0) + 1

    fallback: dict[int, str] = {}
    for gid, text_counts in counts.items():
        fallback[gid] = max(text_counts.items(), key=lambda item: item[1])[0]
    return fallback


def _patch_single_cmap(
    doc: fitz.Document,
    to_unicode_xref: int,
    correction_map: dict[int, str],
    *,
    font_name: str = "",
    allow_gid_exceptions: bool = False,
) -> int:
    pdf_map = _parse_tounicode_cmap(doc.xref_stream(to_unicode_xref))
    patched_map = dict(pdf_map)
    corrections = 0

    for gid, pdf_value in pdf_map.items():
        if gid not in correction_map:
            continue
        correct_value = correction_map[gid]
        if pdf_value == correct_value or _is_ra_virama_swap(pdf_value, correct_value):
            continue
        if (
            allow_gid_exceptions
            and gid == _CONTEXTUAL_NE_GID
            and _font_name_matches_family(font_name, "kalimati")
            and pdf_value == "\u0928\u0947"
            and correct_value == "\u0947"
        ):
            # This glyph means bare e-matra in ordinary contexts, but the authored
            # CMap proves its consonant on the measured `र्` + glyph sequence.
            # Keep provenance in-band until reorder_devanagari can inspect context.
            patched_map[gid] = _PUA_CONTEXTUAL_NE
        elif correct_value == _RA + _VIRAMA:
            patched_map[gid] = _PUA_REPH
        elif correct_value == _IKAR and pdf_value != _IKAR:
            patched_map[gid] = _PUA_IKAR
        elif (
            len(correct_value) >= 2
            and correct_value.endswith(_RA + _VIRAMA)
            and len(pdf_value) >= 1
            and _is_devanagari_matra(pdf_value[0])
            and pdf_value[0] != _IKAR
        ):
            patched_map[gid] = pdf_value[0] + _PUA_REPH
        else:
            patched_map[gid] = correct_value
        corrections += 1

    for gid, correct_value in correction_map.items():
        if gid in patched_map:
            continue
        if correct_value == _RA + _VIRAMA:
            patched_map[gid] = _PUA_REPH
        elif correct_value == _IKAR:
            patched_map[gid] = _PUA_IKAR
        else:
            patched_map[gid] = correct_value

    doc.update_stream(to_unicode_xref, _build_cmap_stream(patched_map))
    return corrections


def _meaningful_cmap_diff_count(
    pdf_map: dict[int, str],
    correction_map: dict[int, str],
) -> int:
    count = 0
    for gid, correct_value in correction_map.items():
        pdf_value = pdf_map.get(gid)
        if pdf_value is None:
            continue
        if pdf_value == correct_value or _is_ra_virama_swap(pdf_value, correct_value):
            continue
        if not (
            _contains_devanagari_or_marker(pdf_value)
            or _contains_devanagari_or_marker(correct_value)
        ):
            continue
        count += 1
    return count


def _agreed_missing_cmap_entries(
    pdf_map: dict[int, str],
    correction_map: dict[int, str],
) -> dict[int, str]:
    """Safe missing entries from a generic font's embedded cmap reconstruction."""

    def normalized_overlap_value(value: str) -> str:
        return value.replace("\xa0", " ").replace("\xad", "-")

    overlap = pdf_map.keys() & correction_map.keys()
    if not overlap or any(
        normalized_overlap_value(pdf_map[code])
        != normalized_overlap_value(correction_map[code])
        for code in overlap
    ):
        return {}

    missing: dict[int, str] = {}
    for code, value in correction_map.items():
        if code in pdf_map or not value or "\x00" in value or "\ufffd" in value:
            continue
        if value == _RA + _VIRAMA:
            value = _PUA_REPH
        elif value == _IKAR:
            value = _PUA_IKAR
        missing[code] = value
    return missing


def _patch_missing_cmap_entries(
    doc: fitz.Document,
    to_unicode_xref: int,
    pdf_map: dict[int, str],
    missing_entries: dict[int, str],
) -> None:
    patched_map = dict(pdf_map)
    patched_map.update(missing_entries)
    doc.update_stream(to_unicode_xref, _build_cmap_stream(patched_map))


def _merge_missing_cmap_entries(
    *entry_maps: dict[int, str],
) -> dict[int, str]:
    """Merge independently validated fills, dropping conflicting answers."""

    merged: dict[int, str] = {}
    conflicts: set[int] = set()
    for entries in entry_maps:
        for code, value in entries.items():
            if code in conflicts:
                continue
            previous = merged.get(code)
            if previous is not None and previous != value:
                merged.pop(code)
                conflicts.add(code)
            else:
                merged[code] = value
    return merged


def _simple_font_correction_is_credible(
    pdf_map: dict[int, str],
    correction_map: dict[int, str],
) -> bool:
    """Whether a simple-font reconstruction identifies the authored encoding."""

    overlap = pdf_map.keys() & correction_map.keys()
    if not overlap:
        return False
    agreements = sum(pdf_map[code] == correction_map[code] for code in overlap)
    return agreements >= 2 and agreements * 4 >= len(overlap) * 3


def _simple_font_is_ascii_digit_normalization(
    pdf_map: dict[int, str],
    correction_map: dict[int, str],
) -> bool:
    """Whether the authored CMap intentionally normalizes Devanagari digits."""

    if not correction_map:
        return False

    ascii_digits = str.maketrans("०१२३४५६७८९", "0123456789")
    for code, value in correction_map.items():
        normalized = value.translate(ascii_digits)
        if normalized == value or pdf_map.get(code) != normalized:
            return False
    return True


def _is_named_repair_font(font_name: str) -> bool:
    return any(
        _font_name_matches_family(font_name, family) for family in ("kalimati", "lohit")
    )


#: Share of a document's drawn glyphs below which an unrepairable named face is
#: incidental, and refusing the whole document over it costs far more than it
#: protects. OAG's Performance Audit Report 2074 (document 11113) declares a
#: Kalimati face that draws ONE glyph of 433,222 -- the report itself is set in
#: Preeti -- so the refusal below withheld 351,643 correctly decoded Devanagari
#: characters on account of that single glyph, while its own transcript measured
#: cleaner than the corpus median (0.06 vs 0.13 word-initial vowel signs per
#: 10,000). Across the 18 OAG documents the refusal withholds, the two
#: populations are four orders of magnitude apart: that face draws 0.0002% of
#: the glyphs and the next-smallest genuine offender draws 10.04%. This floor
#: sits ~20x clear of each, so it separates them without being fitted to either.
_INCIDENTAL_FACE_GLYPH_SHARE = 0.005


def _unrepaired_faces_draw_enough_to_refuse(
    doc: fitz.Document, font_names: set[str]
) -> bool:
    """Whether unrepairable named faces draw enough to justify refusing the PDF.

    Counted on drawn GLYPHS rather than decoded characters, because a face whose
    CMap is broken decodes to little or nothing and so understates exactly the
    faces this has to judge: on the 13 OAG documents that mix an unrepairable
    Kalimati with a repairable Lohit-Devanagari, the decoded characters read
    10-21% where the glyphs say 78-84%.

    Fails closed. A page whose glyphs cannot be traced returns True, so the
    refusal stands unless a face is *proven* incidental -- never because the
    measurement was unavailable.
    """

    wanted = {name.casefold() for name in font_names}
    drawn = 0
    total = 0
    for page_index in range(doc.page_count):
        trace = getattr(doc[page_index], "get_texttrace", None)
        if trace is None:
            return True
        try:
            spans = trace()
        except Exception:  # noqa: BLE001 - an untraceable page cannot clear a face
            return True
        for span in spans:
            glyphs = len(span.get("chars", ()))
            total += glyphs
            name = str(span.get("font", ""))
            if name.split("+", 1)[-1].casefold() in wanted:
                drawn += glyphs
    if total == 0:
        # Nothing is drawn at all, so the unrepaired face garbles nothing. The
        # document has no text layer to protect and `needs_ocr` handles it.
        return False
    return drawn / total > _INCIDENTAL_FACE_GLYPH_SHARE


def _is_generic_type0_font_name(font_name: str) -> bool:
    return bool(re.fullmatch(r"(?:cidfont\+)?f\d+", font_name, re.IGNORECASE))


def _font_owner_family(font_name: str) -> str:
    """Stable ownership class used before selecting a shared-CMap representative."""

    for family in ("kalimati", "lohit", "kokila"):
        if _font_name_matches_family(font_name, family):
            return family
    if _is_generic_type0_font_name(font_name):
        return "generic"
    return f"other:{_base_font_name(font_name)}"


def _shared_type0_owners_are_homogeneous(
    doc: fitz.Document,
    owner_xrefs: set[int],
    font_names: dict[int, str],
) -> bool:
    """Whether shared owners prove one font family backed by one program."""

    if len(owner_xrefs) <= 1:
        return bool(owner_xrefs)
    if len({_font_owner_family(font_names[xref]) for xref in owner_xrefs}) != 1:
        return False
    if any(not _type0_uses_identity_gid_mapping(doc, xref) for xref in owner_xrefs):
        return False
    fontfiles = {_get_fontfile_xref(doc, xref) for xref in owner_xrefs}
    return None not in fontfiles and len(fontfiles) == 1


def _has_ra_virama_ikar_displacement_pair(
    font_name: str,
    pdf_map: dict[int, str],
    correction_map: dict[int, str],
) -> bool:
    """Whether this is the measured Kokila two-GID displacement.

    Either difference alone is too weak to bypass the generic three-difference
    floor. Requiring the face and both exact GIDs keeps unrelated reciprocal-looking
    pairs from activating an entire correction map.
    """

    return (
        _font_name_matches_family(font_name, "kokila")
        and pdf_map.get(83) == _IKAR
        and correction_map.get(83) == "\u0924"
        and pdf_map.get(108) == _RA + _VIRAMA
        and correction_map.get(108) == _IKAR
    )


def _kokila_displacement_corrections(
    font_name: str,
    pdf_map: dict[int, str],
    correction_map: dict[int, str],
    *,
    half_sa_corroborated_elsewhere: bool,
    half_tha_proven_for_target: bool,
) -> dict[int, str]:
    """Context-preserving corrections for the measured Kokila displacement."""

    if not _has_ra_virama_ikar_displacement_pair(
        font_name,
        pdf_map,
        correction_map,
    ):
        return {}

    selected = {
        83: _PUA_KOKILA_TA,
        108: _PUA_KOKILA_IKAR,
    }
    half_sa_correction = correction_map.get(_KOKILA_HALF_SA_GID)
    if pdf_map.get(_KOKILA_HALF_SA_GID) == "\u0925" and (
        half_sa_correction == "\u0938\u094d"
        or (half_sa_correction is None and half_sa_corroborated_elsewhere)
    ):
        selected[_KOKILA_HALF_SA_GID] = _PUA_KOKILA_HALF_SA
    half_tha_correction = correction_map.get(_KOKILA_HALF_THA_GID)
    if (
        pdf_map.get(_KOKILA_HALF_THA_GID) == _VIRAMA
        and pdf_map.get(_KOKILA_YA_GID) == _RA + _VIRAMA
        and correction_map.get(_KOKILA_YA_GID) == "\u092f"
        and (
            half_tha_correction == "\u0925\u094d"
            or (half_tha_correction is None and half_tha_proven_for_target)
        )
    ):
        selected[_KOKILA_HALF_THA_GID] = _PUA_KOKILA_HALF_THA
    return selected


def _scope_kokila_displacement_corrections(
    correction_map: dict[int, str],
    displacement_corrections: dict[int, str],
    meaningful_diffs: int,
) -> dict[int, str]:
    """Keep a proven full map intact while adding corroborated contextual GIDs."""

    if meaningful_diffs < 3:
        return displacement_corrections
    contextual = {
        gid: displacement_corrections[gid]
        for gid in (_KOKILA_HALF_SA_GID, _KOKILA_HALF_THA_GID)
        if gid in displacement_corrections
    }
    if not contextual:
        return correction_map
    return {
        **correction_map,
        **contextual,
    }


def fix_kalimati_cmap(doc: fitz.Document) -> tuple[fitz.Document, bool]:
    candidate_font_owners: dict[int, set[int]] = {}
    representative_fonts: dict[int, int] = {}
    fontfile_maps: dict[int, dict[int, str]] = {}
    to_unicode_maps: dict[int, dict[int, str]] = {}
    font_names: dict[int, str] = {}
    font_types: dict[int, str] = {}
    trace_maps: dict[str, dict[int, str]] = {}

    for page_index in range(doc.page_count):
        for font_info in doc[page_index].get_fonts(full=True):
            xref, _ext, font_type, name, _encoding = font_info[:5]
            base_name = name.split("+", 1)[-1] if "+" in name else name
            if font_type != "Type0" and not (
                font_type == "TrueType" and _is_named_repair_font(base_name)
            ):
                continue
            font_dict = doc.xref_object(xref, compressed=False)
            if font_type == "TrueType" and _SIMPLE_STANDARD_ENCODING_PATTERN.search(
                font_dict
            ):
                continue
            match = re.search(r"/ToUnicode\s+(\d+)\s+\d+\s+R", font_dict)
            if match:
                to_unicode_xref = int(match.group(1))
                # A /ToUnicode reference can name an object that is not a stream.
                # Such a font has no CMap to read and none to patch: both
                # `xref_stream` (returns None) and `update_stream` (raises
                # "object is no PDF dict") fail on it, and because the caller in
                # `font_based._extract_raw_document` wraps every exception into
                # ExtractionError, one malformed font cost the whole document --
                # `nepali_pdf` then fell back to pdfminer, which renders each
                # glyph it cannot decode as U+0000. OAG document 13006 shipped
                # 8,834 NULs this way in every generation v6..v12.
                #
                # Skipping the font leaves the rest of the document's fonts to be
                # repaired normally, which is strictly better than losing all of
                # it. MuPDF reports the malformation on stderr as
                # "format error: object is not a stream" and reads the page text
                # regardless, so there is nothing here that stops extraction.
                if not doc.xref_is_stream(to_unicode_xref):
                    logger.debug(
                        "Kalimati repair: /ToUnicode xref=%d of font %s is not a "
                        "stream; skipping this font.",
                        to_unicode_xref,
                        base_name,
                    )
                    continue
                candidate_font_owners.setdefault(to_unicode_xref, set()).add(xref)
                font_names[xref] = base_name
                font_types[xref] = font_type

    if not candidate_font_owners:
        return doc, False

    pdf_maps = {
        to_unicode_xref: _parse_tounicode_cmap(doc.xref_stream(to_unicode_xref))
        for to_unicode_xref in candidate_font_owners
    }
    patched = False
    unrepaired_named_fonts: set[str] = set()
    for to_unicode_xref, owner_xrefs in candidate_font_owners.items():
        owner_types = {font_types[xref] for xref in owner_xrefs}
        named_owner_names = {
            font_names[xref]
            for xref in owner_xrefs
            if _is_named_repair_font(font_names[xref])
        }
        pdf_map = pdf_maps[to_unicode_xref]
        generic_type0 = owner_types == {"Type0"} and all(
            _is_generic_type0_font_name(font_names[xref]) for xref in owner_xrefs
        )

        # A simple font maps PDF character codes through its embedded non-Unicode
        # cmap. It is safe only when this CMap has one unambiguous owner.
        if owner_types != {"Type0"}:
            if len(owner_xrefs) != 1:
                unrepaired_named_fonts.update(named_owner_names)
                continue
            font_xref = next(iter(owner_xrefs))
            font_name = font_names[font_xref]
            named_repair_font = _is_named_repair_font(font_name)
            if font_types[font_xref] != "TrueType":
                unrepaired_named_fonts.update(named_owner_names)
                continue

            correction_map = _get_font_correction_map(doc, font_xref)
            if correction_map:
                correction_map = _get_simple_font_correction_map(
                    doc,
                    font_xref,
                    correction_map,
                )
            if not _simple_font_correction_is_credible(pdf_map, correction_map):
                if _simple_font_is_ascii_digit_normalization(pdf_map, correction_map):
                    continue
                correction_map = {}
            if not correction_map:
                if named_repair_font:
                    unrepaired_named_fonts.add(font_name)
                continue

            _patch_single_cmap(doc, to_unicode_xref, correction_map)
            patched = True
            continue

        # A representative is meaningful only after all owners prove one family
        # and, when shared, one embedded font program. This makes the decision
        # independent of page resource order.
        if not _shared_type0_owners_are_homogeneous(
            doc,
            owner_xrefs,
            font_names,
        ):
            unrepaired_named_fonts.update(named_owner_names)
            continue

        font_xref = min(owner_xrefs)
        font_name = font_names[font_xref]
        named_repair_font = _is_named_repair_font(font_name)
        representative_fonts[to_unicode_xref] = font_xref

        owner_trace_maps: list[dict[int, str]] = []
        for owner_xref in sorted(owner_xrefs):
            owner_name = font_names[owner_xref]
            if owner_name not in trace_maps:
                trace_maps[owner_name] = _collect_trace_fallback_map(doc, owner_name)
            owner_trace_maps.append(trace_maps[owner_name])

        filtered_trace_maps: list[dict[int, str]] = []
        for owner_trace_map in owner_trace_maps:
            filtered_trace_maps.append(
                {
                    gid: value
                    for gid, value in owner_trace_map.items()
                    if (pdf_value := pdf_map.get(gid)) is None
                    or _trace_value_is_better(pdf_value, value)
                }
            )
        trace_map = _merge_missing_cmap_entries(*filtered_trace_maps)

        fontfile_xref = _get_fontfile_xref(doc, font_xref)
        if fontfile_xref is not None and fontfile_xref in fontfile_maps:
            correction_map = fontfile_maps[fontfile_xref]
        else:
            correction_map = _get_font_correction_map(doc, font_xref)
            if fontfile_xref is not None:
                fontfile_maps[fontfile_xref] = correction_map

        # Generic F<n> resources are not face-attributed. They may fill only
        # missing entries, and only when every owner proves the same GID space.
        if generic_type0:
            if any(not _type0_codes_are_gids(doc, xref) for xref in owner_xrefs):
                continue
            missing_entries = _merge_missing_cmap_entries(
                _agreed_missing_cmap_entries(pdf_map, correction_map),
                *(
                    _agreed_missing_cmap_entries(pdf_map, owner_trace_map)
                    for owner_trace_map in owner_trace_maps
                ),
            )
            if missing_entries:
                _patch_missing_cmap_entries(
                    doc,
                    to_unicode_xref,
                    pdf_map,
                    missing_entries,
                )
                patched = True
            continue

        if not correction_map:
            if trace_map:
                to_unicode_maps[to_unicode_xref] = trace_map
            elif named_repair_font:
                unrepaired_named_fonts.add(font_name)
            continue

        meaningful_diffs = _meaningful_cmap_diff_count(pdf_map, correction_map)
        has_displacement_pair = _has_ra_virama_ikar_displacement_pair(
            font_name,
            pdf_map,
            correction_map,
        )
        pair_scoped = has_displacement_pair and _stream_allows_face_specific_gids(
            doc,
            candidate_font_owners[to_unicode_xref],
            font_names,
            "kokila",
        )
        if pair_scoped:
            displacement_corrections = _kokila_displacement_corrections(
                font_name,
                pdf_map,
                correction_map,
                half_sa_corroborated_elsewhere=_has_corroborated_kokila_half_sa(
                    doc,
                    pdf_maps,
                    candidate_font_owners,
                    font_names,
                    target_to_unicode_xref=to_unicode_xref,
                ),
                half_tha_proven_for_target=_has_proven_kokila_half_tha_outline(
                    doc,
                    candidate_font_owners[to_unicode_xref],
                    font_names,
                ),
            )
            if not displacement_corrections:
                if trace_map:
                    to_unicode_maps[to_unicode_xref] = trace_map
                continue
            # Above the generic floor the embedded map already proves ordinary
            # GID 83/108 corrections. Add only separately corroborated GIDs.
            correction_map = _scope_kokila_displacement_corrections(
                correction_map,
                displacement_corrections,
                meaningful_diffs,
            )
        elif meaningful_diffs < 3 and not named_repair_font:
            if trace_map:
                to_unicode_maps[to_unicode_xref] = trace_map
            continue

        combined_map = dict(trace_map)
        combined_map.update(correction_map)
        to_unicode_maps[to_unicode_xref] = combined_map

    if unrepaired_named_fonts and _unrepaired_faces_draw_enough_to_refuse(
        doc, unrepaired_named_fonts
    ):
        names = ", ".join(sorted(unrepaired_named_fonts))
        raise ExtractionError(
            f"Unable to repair named Kalimati/Lohit font mappings: {names}"
        )

    for to_unicode_xref, correction_map in to_unicode_maps.items():
        type0_xref = representative_fonts[to_unicode_xref]
        owners = candidate_font_owners[to_unicode_xref]
        _patch_single_cmap(
            doc,
            to_unicode_xref,
            correction_map,
            font_name=font_names[type0_xref],
            allow_gid_exceptions=_stream_allows_face_specific_gids(
                doc,
                owners,
                font_names,
                "kalimati",
            ),
        )
        patched = True

    if not patched:
        if any(
            _font_name_matches_family(font_name, "kokila")
            for font_name in font_names.values()
        ):
            raise ExtractionError(
                "Unable to repair Kalimati font mappings for this PDF"
            )
        return doc, False

    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    buffer.seek(0)
    return fitz.open(stream=buffer, filetype="pdf"), True


def _contextual_ne_has_matra_base(chars: list[str], marker_index: int) -> bool:
    """Whether a bare e-matra can attach immediately before the marker."""

    if not marker_index:
        return False
    base = chars[marker_index - 1]
    if _is_devanagari_consonant(base) or "\u0958" <= base <= "\u095f":
        return True
    return (
        base == _NUKTA
        and marker_index > 1
        and _is_devanagari_consonant(chars[marker_index - 2])
    )


def resolve_contextual_ne(text: str) -> str:
    """Resolve the Kalimati e-matra marker after neighboring spans are joined."""

    if _PUA_CONTEXTUAL_NE not in text:
        return text

    chars = list(text)
    index = 0
    while index < len(chars):
        if chars[index] != _PUA_CONTEXTUAL_NE:
            index += 1
            continue
        if not _contextual_ne_has_matra_base(chars, index):
            # Without a consonant base the PDF's original value is safer than
            # manufacturing an orphan or invalid combining mark.
            replacement = ["\u0928", "\u0947"]
        else:
            replacement = ["\u0947"]
        chars[index : index + 1] = replacement
        index += len(replacement)

    return "".join(chars)


def resolve_kokila_half_sa(text: str) -> str:
    """Resolve the measured half-sa GID, retaining authored tha without proof."""

    if _PUA_KOKILA_HALF_SA not in text:
        return text
    chars = list(text)
    index = 0
    while index < len(chars):
        if chars[index] != _PUA_KOKILA_HALF_SA:
            index += 1
            continue
        if index + 1 < len(chars) and (
            _is_devanagari_consonant(chars[index + 1])
            or "\u0958" <= chars[index + 1] <= "\u095f"
        ):
            replacement = ["\u0938", _VIRAMA]
        else:
            replacement = ["\u0925"]
        chars[index : index + 1] = replacement
        index += len(replacement)
    return "".join(chars)


def resolve_kokila_half_tha(text: str) -> str:
    """Resolve the measured half-tha GID only before its corroborated ya."""

    if _PUA_KOKILA_HALF_THA not in text:
        return text
    chars = list(text)
    index = 0
    while index < len(chars):
        if chars[index] != _PUA_KOKILA_HALF_THA:
            index += 1
            continue
        replacement = (
            ["\u0925", _VIRAMA]
            if index + 1 < len(chars) and chars[index + 1] == "\u092f"
            else [_VIRAMA]
        )
        chars[index : index + 1] = replacement
        index += len(replacement)
    return "".join(chars)


def resolve_kokila_displacement(text: str) -> str:
    """Resolve measured Kokila GIDs only in contexts their sequence proves."""

    if (
        _PUA_KOKILA_IKAR not in text
        and _PUA_KOKILA_TA not in text
        and _PUA_KOKILA_HALF_SA not in text
        and _PUA_KOKILA_HALF_THA not in text
    ):
        return text

    for half_sa in (_PUA_KOKILA_HALF_SA, "\u0938\u094d"):
        status_sequence = (
            _PUA_KOKILA_IKAR + half_sa + "\u0925" + _PUA_KOKILA_IKAR + _PUA_KOKILA_TA
        )
        text = text.replace(status_sequence, "\u0938\u094d\u0925\u093f\u0924\u093f")
    text = text.replace(
        _KOKILA_LITERAL_TH_STATUS_SEQUENCE,
        "\u0938\u094d\u0925\u093f\u0924\u093f",
    )
    text = resolve_kokila_half_tha(text)
    text = resolve_kokila_half_sa(text)

    text = re.sub(
        rf"(^|[\s|(:]){_PUA_KOKILA_TA}(?=\u0939(?:[\s:|]|$))",
        lambda match: match.group(1) + "\u0924",
        text,
        flags=re.MULTILINE,
    )
    return text.replace(_PUA_KOKILA_IKAR, _RA + _VIRAMA).replace(
        _PUA_KOKILA_TA,
        _IKAR,
    )


def reorder_devanagari(
    text: str,
    *,
    resolve_contextual: bool = True,
) -> str:
    if resolve_contextual:
        text = resolve_contextual_ne(text)
    # Kokila GIDs must resolve before generic pre-base matras reorder.
    text = resolve_kokila_displacement(text)
    if _PUA_REPH not in text and _PUA_IKAR not in text:
        return text

    chars = list(text)
    index = 0
    while index < len(chars):
        if chars[index] == _PUA_IKAR:
            if index + 1 < len(chars) and _is_devanagari_consonant(chars[index + 1]):
                end = index + 1
                while (
                    end + 2 < len(chars)
                    and chars[end + 1] == _VIRAMA
                    and _is_devanagari_consonant(chars[end + 2])
                ):
                    end += 2
                chars.pop(index)
                chars.insert(end, _IKAR)
            else:
                chars[index] = _IKAR
                index += 1
        else:
            index += 1

    index = 0
    while index < len(chars):
        if chars[index] == _PUA_REPH:
            cursor = index - 1
            while cursor >= 0 and (
                _is_devanagari_matra(chars[cursor])
                or chars[cursor] in "\u0901\u0902\u0903\u094d"
            ):
                cursor -= 1
            while (
                cursor >= 2
                and _is_devanagari_consonant(chars[cursor])
                and chars[cursor - 1] == _VIRAMA
                and _is_devanagari_consonant(chars[cursor - 2])
            ):
                cursor -= 2
            if cursor >= 0 and _is_devanagari_consonant(chars[cursor]):
                chars.pop(index)
                chars.insert(cursor, _VIRAMA)
                chars.insert(cursor, _RA)
                index += 2
            else:
                chars[index] = _RA
                chars.insert(index + 1, _VIRAMA)
                index += 2
        else:
            index += 1

    return "".join(chars)


def _is_devanagari_combining(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x093E <= codepoint <= 0x094D
        or 0x0901 <= codepoint <= 0x0903
        or codepoint == 0x093C
        or codepoint in {0x0962, 0x0963}
    )


def normalize_devanagari_spacing(
    text: str,
    *,
    preserve_marker_spaces: bool = False,
) -> str:
    if not text:
        return text

    result: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == " ":
            next_char = text[index + 1] if index + 1 < len(text) else None
            remove = False
            # Reconciliation note: the v18 line still carried the blanket
            # ``previous == _VIRAMA`` deletion, narrowed by ``next_char !=
            # _PUA_CONTEXTUAL_NE``. Upstream removed that rule outright and
            # replaced it with the targeted ``_BROKEN_PURYA_PATTERN`` below,
            # because a blanket virama-space deletion also joins real word
            # boundaries. Upstream's rule is kept: with no blanket deletion
            # there is no space for the contextual-ne exception to protect, so
            # the narrowing is unnecessary here rather than lost.
            protected_boundary = preserve_marker_spaces and next_char in {
                _PUA_REPH,
                _PUA_IKAR,
                _PUA_KOKILA_IKAR,
                _PUA_KOKILA_TA,
                _PUA_KOKILA_HALF_SA,
                _PUA_KOKILA_HALF_THA,
            }
            if (
                not protected_boundary
                and next_char
                and (
                    _is_devanagari_combining(next_char)
                    or next_char in {_PUA_REPH, _PUA_IKAR}
                )
            ):
                remove = True
            if remove:
                index += 1
                continue
        result.append(text[index])
        index += 1

    normalized = "".join(result)
    # PyMuPDF exposes some Kalimati conjuncts with an internal space. A blanket
    # virama-space deletion -- which the v18 extractor line still carried -- also
    # joins real word boundaries, so what decides it is the consonant BEFORE the
    # virama, not the virama:
    #
    #   preserve  सम्वत् २०८०  पश्चात् 2024  एवम् ।  अर्थात् अब  छन् तथा  क् ख
    #   delete    पुर् याएको   नपुर् याई     सञ् चालन
    #
    # Every preserved case ends in a consonant Nepali words do end in (त् म् न्,
    # and क् kept conservatively); both deleted classes end in one that cannot be
    # word-final -- a forward-attaching repha, and ञ्, which occurs only as the
    # first member of a conjunct (सञ्चालन, पञ्च, अञ्चल). So this is upstream's
    # targeted rule, extended to the second class rather than widened to all
    # viramas.
    normalized = _BROKEN_PURYA_PATTERN.sub("", normalized)
    return _BROKEN_NYA_CONJUNCT_PATTERN.sub("", normalized)
