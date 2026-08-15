"""Tests for the Siddhi legacy layout (VOL-471).

Siddhi is a legacy Nepali layout npttf2utf does not ship and that `_REGISTRY` did
not model, so before this it was handed to content-based detection and ranked
against five maps none of which is its layout. `choose_legacy_map` could only pick
the least-wrong, and the corpus carries **79 documents / 12,371 characters** under
the names `Siddhi` and `SiddhiNormal` (measured over all 6,236 OAG documents, run
`d0121829`).

**Every expected string below was read off a rendered page, not produced by the
converter.** They are page pixels at zoom 6 / zoom 16 from
`oag-corpus/pdfs/local-level-report/2688__…गेरूवा गाउँपालिका.pdf` and
`2835__…सोलुदुधकुण्ड नगरपालिका, सोलुखुम्बु.pdf`, recorded in
`oag-corpus/runs/vol471-d0121829/`. Writing the converter's own output back as the
expectation would make every one of these vacuous, which is the specific way a
layout test fails silently.

The five shipped maps score, on the same 21 readings: FONTASY_HIMALI_TT 11,
PCS NEPALI 9, Preeti 6, Kantipur 6, Sagarmatha 6, Spins 2. That spread is what
:func:`test_no_shipped_map_reproduces_the_page` pins, so a future "just use
Himali" simplification has to fail rather than look plausible.
"""

from __future__ import annotations

import pytest

from likhit.extractors.font_classifier import classify_font
from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    SIDDHI_MAP_KEY,
    _match_font,
    get_converter,
    get_converter_for_map,
    is_legacy_font,
)

# (source keystrokes, what the page draws). Read from the renders named in the
# record; the Nepali is corroborated by meaning -- OAG budget vocabulary, district
# names, a contractor name and financial columns -- so it is not a glyph guess.
_PAGE_READINGS = (
    # 0x3c is र, and 0x7b is र् -- both in one span, which is the pair no single
    # shipped map gets right (Preeti/Himali lose 0x3c, Spins loses 0x7b).
    ("cfo <sd ?= ", "आय रकम रु. "),
    ("11. ul<j;_u ljZj]Zj< sfo{qmd ", "११. गरिवसंग विश्वेश्वर कार्यक्रम "),
    ("2. <fhZj af_Škmf_Š ", "२. राजश्व बांडफांड "),
    # 0x5f is the anusvara, which is Spins' reading and not Himali's.
    ("50985303 k'_lhut vr{ ", "५०९८५३०३ पुंजिगत खर्च "),
    (";_# ", "संघ "),
    ("19. 5 g_ k|b]z cGo cg'bfg ", "१९. ५ नं प्रदेश अन्य अनुदान "),
    # The number rows are Himali's, not Preeti's.
    ("235187839 ", "२३५१८७८३९ "),
    ("128604127 ", "१२८६०४१२७ "),
    ("9. ;fdflhs ;'<Iff cg'bfg ", "९. सामाजिक सुरक्षा अनुदान "),
    ("16= t<fO dw]z ;d[l$ sfo{qmd ", "१६. तराइ मधेश समृद्धि कार्यक्रम "),
    ("6. a]?h' c;'nL ", "६. बेरुजु असुली "),
    ("u]?jf ", "गेरुवा "),
    # 0x2f is a literal slash and 0x3c is र IN THE SAME SPAN. A sequential
    # replace would send '<' through '/' to '÷' and lose the र.
    ("cd</dhb'< h]=le ;'v]{t ", "अमर/मजदुर जे.भि सुर्खेत "),
    # SiddhiNormal, the second name and 35% of the corpus character mass.
    ("ljj<)f  ", "विवरण  "),
    ("l;=g ", "सि.न "),
    ("31079333.10 ", "३१०७९३३३.१० "),
    ("309735938.83 ", "३०९७३५९३८.८३ "),
    ("ª- ;_#Lo <fhZj jfŠkmf^ ", "ङ- संघीय राजश्व वाडफाट "),
    ("^- ;Šs jf]Š{ ", "ट- सडक वोर्ड "),
    ("<fhZj -cfGt<Ls cfo ", "राजश्व -आन्तरीक आय "),
    ("s-", "क-"),
)

# Per-codepoint truths, each read from a zoom-40 crop of ONE occurrence of that
# glyph (or, for the anusvara, from five word readings, because its own crop is
# 33px wide). These are what the layout table is built from.
_GLYPH_READINGS = (
    ("-", "-", "horizontal bar at mid height"),
    (".", ".", "square dot on the baseline"),
    ("/", "/", "forward slash"),
    ("<", "र", "full ra with headline"),
    ("_", "ं", "anusvara"),
    ("Š", "ड", "full da with headline"),
)

_SIDDHI_NAMES = ("Siddhi", "SiddhiNormal", "ABCDEE+Siddhi", "ABCDEE+SiddhiNormal")


@pytest.fixture(scope="module")
def convert():
    return get_converter_for_map(SIDDHI_MAP_KEY)


@pytest.mark.parametrize("font_name", _SIDDHI_NAMES)
def test_both_corpus_spellings_route_to_the_siddhi_map(font_name: str) -> None:
    """`SiddhiNormal` is 21 of the 79 aggregates and must not fall through."""
    assert _match_font(font_name) == SIDDHI_MAP_KEY
    assert is_legacy_font(font_name)


@pytest.mark.parametrize("font_name", _SIDDHI_NAMES)
def test_the_name_path_is_what_handles_these_faces(font_name: str) -> None:
    """`legacy_remap` takes them OUT of content-based ranking.

    `detect_content_legacy_fonts` skips every font `classify_font` does not call
    "correct", so this is the assertion that keeps a seventh candidate out of every
    other document's ranking. Before this change all 79 classified as "correct".
    """
    assert classify_font(font_name, "") == "legacy_remap"


@pytest.mark.parametrize("source,expected", _PAGE_READINGS)
def test_the_layout_reproduces_the_rendered_page(
    source: str, expected: str, convert
) -> None:
    assert convert(source) == expected


@pytest.mark.parametrize("source,expected", _PAGE_READINGS)
def test_the_name_path_and_the_map_key_agree(source: str, expected: str) -> None:
    """`get_converter("Siddhi")` is the entry point extraction actually calls."""
    converter = get_converter("ABCDEE+SiddhiNormal")
    assert converter is not None
    assert converter(source) == expected


@pytest.mark.parametrize("source,expected,_description", _GLYPH_READINGS)
def test_each_reassigned_codepoint_decodes_to_its_glyph(
    source: str, expected: str, _description: str, convert
) -> None:
    assert convert(source) == expected


def test_no_shipped_map_reproduces_the_page(convert) -> None:
    """Every shipped map is wrong on this population, which is why the key exists.

    Also guards the reverse: if some future map.json edit made one of the five
    exactly right, this fails and the Siddhi key should then be revisited rather
    than silently duplicated.
    """
    exact = {
        key: sum(
            1 for src, want in _PAGE_READINGS if get_converter_for_map(key)(src) == want
        )
        for key in ALL_MAP_KEYS
    }
    assert all(score < len(_PAGE_READINGS) for score in exact.values()), exact
    assert exact["FONTASY_HIMALI_TT"] > exact["Preeti"], exact
    assert exact["Preeti"] > exact["Spins"], exact
    assert sum(1 for src, want in _PAGE_READINGS if convert(src) == want) == len(
        _PAGE_READINGS
    )


def test_the_base_map_is_himali_and_not_preeti(convert) -> None:
    """The number rows pick the base, and they pick it unambiguously.

    Under Preeti the unshifted row is consonants; the page draws digits. Stated
    against decoded output rather than against `_SIDDHI_BASE_MAP_KEY`, so renaming
    the constant cannot make this pass vacuously.
    """
    assert convert("0123456789") == "०१२३४५६७८९"
    assert get_converter_for_map("Preeti")("0123456789") != "०१२३४५६७८९"
    # The shifted row is the Devanagari alphabet, which is how the sub-item
    # markers on page 4 of 2835 run क ख ग घ ङ च छ … ट.
    assert convert("#") == "घ"
    assert convert("%") == "छ"
    assert convert("^") == "ट"


def test_the_hyphen_is_a_separator_and_not_a_translation(convert) -> None:
    """0x2d has no translate target: no map emits "-" anywhere in 0x00-0x2FFF.

    So it is split on and rejoined, and the parts on both sides must still decode.
    The spans it fires on are in `_PAGE_READINGS`; this pins the degenerate cases
    those spans do not reach.
    """
    assert convert("-") == "-"
    assert convert("--") == "--"
    assert convert("") == ""
    for key in ALL_MAP_KEYS:
        assert "-" not in {
            get_converter_for_map(key)(chr(cp)) for cp in range(0x00, 0x3000)
        }


def test_zero_x_3c_and_zero_x_2f_do_not_chain(convert) -> None:
    """'<' -> '/' -> '÷' must not compose. Two codes, one intermediate value.

    A sequential `str.replace` chain that does '<' before '/' sends every '<' all
    the way to '÷' and emits a literal slash where the page draws र. Note the
    mutation run: the *other* sequential order ('/' before '<') is a CORRECT
    implementation and this test does not kill it, which is right -- it is only the
    chaining that is wrong, not the sequence. `str.translate` cannot chain at all,
    which is why the shipped shape uses it.
    """
    assert convert("<") == "र"
    assert convert("/") == "/"
    assert convert("</") == "र/"
    assert convert("/<") == "/र"


def test_siddhi_is_deliberately_absent_from_the_content_candidates() -> None:
    """Pinned as a decision, not left as an oversight.

    Adding the key here puts a seventh candidate in front of every gate-passing
    aggregate in the corpus -- a corpus-wide false-positive surface for a
    population the name already identifies. If a later run measures that surface
    and decides to add it, this assertion is the thing it has to argue with.
    """
    assert SIDDHI_MAP_KEY not in ALL_MAP_KEYS
    assert len(ALL_MAP_KEYS) == 6


def test_the_other_maps_are_untouched() -> None:
    """The change must be additive. A shared-base regression would show here."""
    assert get_converter_for_map("Preeti")("kl/R5]b") == "परिच्छेद"
    assert get_converter_for_map("FONTASY_HIMALI_TT")("100") == "१००"
    assert get_converter_for_map("Preeti")("2. <fhZj") == "द्द। ?ाजश्व"
    assert get_converter_for_map("Spins")("k'_lhut") == "पुंजिगत"
    assert _match_font("Preeti") == "Preeti"
    assert _match_font("Himalb") == "Preeti"
    assert _match_font("Times New Roman") is None
