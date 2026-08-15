"""Legacy Nepali font conversion helpers."""

from __future__ import annotations

import os
import re
import threading
from typing import Callable
import warnings

from likhit.errors import ExtractionError

_REGISTRY: dict[str, str] = {
    "preeti": "Preeti",
    "fontasy_himali": "FONTASY_HIMALI_TT",
    "fontasyhimali": "FONTASY_HIMALI_TT",
    "himali": "FONTASY_HIMALI_TT",
    "himalb": "FONTASY_HIMALI_TT",
    "kantipur": "Kantipur",
    "pcs nepali": "PCS NEPALI",
    "pcs_nepali": "PCS NEPALI",
    "pcsnepali": "PCS NEPALI",
    "sagarmatha": "Sagarmatha",
}

# The full set of npttf2utf map keys, used by content-based (name-agnostic)
# legacy-font detection to try every known legacy encoding against a span.
ALL_MAP_KEYS: tuple[str, ...] = (
    "Preeti",
    "Kantipur",
    "PCS NEPALI",
    "FONTASY_HIMALI_TT",
    "Sagarmatha",
)

# No legacy keyboard layout in _REGISTRY puts its own bracket glyph on the
# literal ASCII '(' / ')' keys -- confirmed for FONTASY_HIMALI_TT (whose '('
# key is a keyboard-layout consonant slot, decoding to 'ढ') and for Preeti
# (whose '(' key decodes to '९'); both layouts render a real bracket from '-'
# instead. So when a whole span is nothing but an ASCII-bracketed number -- a
# list/outline marker like "(1)" -- the parens were placed directly by
# whatever authored the numbering, sharing the body font only for visual
# consistency with the digit, not typed on this keyboard layout at all.
# Running the full map over it anyway retargets the parens at whatever
# consonant sits in that layout's slot: Fontasy Himali's "(1)" becomes "ढ१ण्"
# even though the very same map correctly reads a same-shaped "-1_" marker as
# "(१)" (VOL-166). Verified against all 13 CIAA annual reports: this exact
# ASCII-bracketed shape never occurs under any other legacy map or font in
# the corpus, so digit-only conversion here changes no other document.
_ASCII_BRACKETED_NUMBER = re.compile(r"^(\s*)\((\d+)\)(\s*)$")
_LATIN_TO_DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def _decode_ascii_bracketed_number(text: str) -> str | None:
    """Digit-only decode for a whole span shaped like ``"(12)"``, else ``None``."""

    match = _ASCII_BRACKETED_NUMBER.match(text)
    if match is None:
        return None
    lead, digits, trail = match.groups()
    return f"{lead}({digits.translate(_LATIN_TO_DEVANAGARI_DIGITS)}){trail}"


_mapper = None
_mapper_lock = threading.Lock()


def _match_font(font_name: str) -> str | None:
    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    base = base.split(",")[0]
    base_lower = base.lower().strip()
    for key, map_key in _REGISTRY.items():
        if key in base_lower:
            return map_key
    return None


def _get_mapper():
    global _mapper
    if _mapper is not None:
        return _mapper

    # Double-checked lock: build the mapper once even under concurrent PDF
    # conversions. This also confines the process-global warnings-filter mutation
    # in the catch_warnings block below to a single initializing thread.
    with _mapper_lock:
        if _mapper is not None:
            return _mapper

        try:
            # npttf2utf's bundled preetimapper uses a few non-raw string literals
            # ('b\\w' etc.) that emit SyntaxWarning when first compiled. The bug
            # is upstream (a raw-string PR is warranted); suppress it here so it
            # does not leak into our logs/output. The catch_warnings block scopes
            # this to the npttf2utf import only. A ``module=`` filter is
            # intentionally not used: the compile-time warning's module name does
            # not reliably match it, which would let a strict SyntaxWarning filter
            # turn it fatal.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                import npttf2utf
                from npttf2utf.base.fontmapper import FontMapper
        except ModuleNotFoundError as exc:
            raise ExtractionError(
                "npttf2utf is required for legacy Nepali font conversion but is not installed"
            ) from exc

        map_json = os.path.join(os.path.dirname(npttf2utf.__file__), "map.json")
        _mapper = FontMapper(map_json)
    return _mapper


def get_converter(font_name: str) -> Callable[[str], str] | None:
    map_key = _match_font(font_name)
    if map_key is None:
        return None
    return get_output_converter_for_map(map_key)


def get_converter_for_map(map_key: str) -> Callable[[str], str]:
    """Return the **raw** converter for an explicit npttf2utf map key.

    This is the scoring primitive. :func:`choose_legacy_map` runs every candidate
    map over a span and keeps the best-scoring one, and that comparison has to see
    each map's unmodified output -- a gate applied here would change which map
    wins, not just what the winner emits. So this deliberately does **not** carry
    the bracketed-marker gate.

    Anything producing *final text* wants :func:`get_output_converter_for_map`
    instead. The two call sites are easy to conflate, which is how VOL-166's fix
    first shipped covering only one of them: `font_based._convert_span_text` calls
    the content-based path before the name-based one and returns from it directly,
    so an ungated call there reintroduces `"(1)" -> "ढ१ण्"` even with
    :func:`get_converter` fixed.
    """

    mapper = _get_mapper()

    def _convert(text: str) -> str:
        return mapper.map_to_unicode(text, from_font=map_key)

    return _convert


def get_output_converter_for_map(map_key: str) -> Callable[[str], str]:
    """Return the converter to use when emitting final text for ``map_key``.

    :func:`get_converter_for_map` plus the bracketed-list-marker gate described
    at :data:`_ASCII_BRACKETED_NUMBER`. Use this from every path that produces
    output, whether the map was chosen by font name or by span content; use the
    raw converter only for scoring.
    """

    base_convert = get_converter_for_map(map_key)

    def _convert(text: str) -> str:
        decoded = _decode_ascii_bracketed_number(text)
        if decoded is not None:
            return decoded
        return base_convert(text)

    return _convert


def is_legacy_font(font_name: str) -> bool:
    return _match_font(font_name) is not None
