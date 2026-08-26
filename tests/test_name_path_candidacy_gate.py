"""VOL-630: the NAME path's candidacy gate.

Item 3 of the issue -- a control proving the gate bites on a digit-only span,
which is the case VOL-614's Latin-*word* veto provably cannot reach.

Every fixture here is measured, not invented: `2070`, `100`, `(1)(2)(3)` and
`25,70,266/-` are the four spans the issue names, and their per-term gate
behaviour is asserted rather than summarised, because *which* term bites is the
whole finding (see `test_hits_is_the_only_term_that_bites_on_every_digit_span`).
"""

from __future__ import annotations

import inspect

import pytest

from likhit.extractors import font_based
from likhit.extractors.font_based import (
    _CONTENT_LEGACY_MAX_PENALTY_PER_DEVA,
    _CONTENT_LEGACY_MIN_DEVA,
    _CONTENT_LEGACY_MIN_DEVA_RATIO,
    _CONTENT_LEGACY_MIN_HITS,
    _nepali_validity,
    _passes_content_legacy_gate,
    _reads_as_latin_words,
    detect_name_legacy_candidates,
)
from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    get_converter,
    get_converter_for_map,
)

# The four spans VOL-630 scope item 3 names. Measured `_reads_as_latin_words`
# == False on all four, which is why the Latin veto cannot cover this hole.
DIGIT_ONLY_SPANS = ("2070", "100", "(1)(2)(3)", "25,70,266/-")

# Maps whose ASCII digit row lands on Devanagari DIGITS (10/10) rather than on
# consonants (0/10). Asserted below rather than trusted.
DIGIT_TRANSLITERATING_MAPS = frozenset({"PCS NEPALI", "FONTASY_HIMALI_TT"})
CONSONANT_DIGIT_MAPS = frozenset({"Preeti", "Kantipur", "Sagarmatha", "Spins"})

_DEVANAGARI_DIGITS = frozenset("०१२३४५६७८९")


@pytest.mark.parametrize("span", DIGIT_ONLY_SPANS)
def test_latin_word_veto_cannot_fire_on_a_digit_only_span(span: str) -> None:
    """VOL-614's veto declines by construction: no ASCII letters, no words.

    This is the premise of the whole issue. If this ever goes green the other
    way, the name path already had a guard covering digit spans and VOL-630's
    gate would be redundant rather than necessary.
    """

    assert _reads_as_latin_words(span) is False


@pytest.mark.parametrize("span", DIGIT_ONLY_SPANS)
def test_gate_declines_every_digit_only_span_under_preeti(span: str) -> None:
    """The control: the ported conjunction refuses a digit-only Preeti span."""

    convert = get_converter("Preeti")
    assert convert is not None
    decoded = convert(span)
    assert _passes_content_legacy_gate(_nepali_validity(decoded)) is False


def test_hits_is_the_only_term_that_bites_on_every_digit_span() -> None:
    """Why the CONJUNCTION had to be ported, and not any single line of it.

    Measured per term. `devanagari >= 8` passes on three of the four spans and
    `ratio`/`penalty` pass on three -- so a port that implemented the Devanagari
    floor, or the ratio, or the penalty, would have let `2070` and `(1)(2)(3)`
    through and remapped a statutory year into consonants. `hits` is the only
    term false on all four, and it is the one the issue's prose warns is quoted
    as a comment in three places without being the enforced form.
    """

    convert = get_converter("Preeti")
    assert convert is not None
    per_term = {}
    for span in DIGIT_ONLY_SPANS:
        validity = _nepali_validity(convert(span))
        per_term[span] = {
            "hits": validity["hits"] >= _CONTENT_LEGACY_MIN_HITS,
            "deva": validity["devanagari"] >= _CONTENT_LEGACY_MIN_DEVA,
            "ratio": validity["ratio"] >= _CONTENT_LEGACY_MIN_DEVA_RATIO,
            "penalty": (
                validity["penalty_per_deva"] <= _CONTENT_LEGACY_MAX_PENALTY_PER_DEVA
            ),
        }

    assert all(not terms["hits"] for terms in per_term.values()), per_term
    # Exactly the three that clear the Devanagari floor, named individually so a
    # drift in either direction is a failure rather than a silently different set.
    assert per_term["2070"]["deva"] is True
    assert per_term["(1)(2)(3)"]["deva"] is True
    assert per_term["25,70,266/-"]["deva"] is True
    assert per_term["100"]["deva"] is False
    # And the single span the penalty term catches, so "three of four" is pinned.
    assert per_term["25,70,266/-"]["penalty"] is False
    assert sum(1 for t in per_term.values() if t["penalty"]) == 3


def test_ascii_digit_row_splits_the_maps_ten_to_zero() -> None:
    """The discriminator behind VOL-630's over-reach finding, asserted.

    The gate's verdict on a digit-only font is only *correct* where decoding a
    digit yields a Devanagari digit. This asserts the split is total -- 10/10 or
    0/10, never partial -- because a partial map would mean the two populations
    cannot be separated by map identity at all.
    """

    seen = {}
    for map_key in ALL_MAP_KEYS:
        decoded = get_converter_for_map(map_key)("0123456789")
        seen[map_key] = sum(1 for char in decoded if char in _DEVANAGARI_DIGITS)

    for map_key in DIGIT_TRANSLITERATING_MAPS:
        assert seen[map_key] == 10, (map_key, seen)
    for map_key in CONSONANT_DIGIT_MAPS & set(seen):
        assert seen[map_key] == 0, (map_key, seen)
    assert set(seen) == set(ALL_MAP_KEYS)


def test_gate_admits_a_digit_only_span_on_a_digit_transliterating_map() -> None:
    """The other arm: on FONTASY_HIMALI_TT a digit row is not garbage.

    `2070` decodes to `२०७०`, the correct reading. The gate still declines it --
    `hits` is 0 for a span with no words on any map -- and that is precisely the
    over-reach VOL-630 measured at 796,630 Devanagari characters. This test
    records the behaviour so a future amendment that exempts these maps has a
    red test to turn green, rather than changing an unasserted behaviour.
    """

    decoded = get_converter_for_map("FONTASY_HIMALI_TT")("2070")
    assert decoded == "२०७०"
    assert _passes_content_legacy_gate(_nepali_validity(decoded)) is False


def test_gate_ships_on_with_no_flag_to_turn_it_off() -> None:
    """VOL-635 decision (b): the gate ships ON. There is no opt-in flag any more.

    This replaces `test_gate_is_opt_in_and_off_by_default`. The predecessor
    landed the gate behind `LIKHIT_NAME_LEGACY_GATE` because a VERBATIM port was
    a measured 8:1 regression on the Devanagari instrument; decision (b) amends
    the `hits` term with a map-derived digit disjunct, which removes the
    regression, so default-OFF became "a decision to keep a live hole open".

    Asserted on the source of the production caller, because that is where the
    previous failure mode lived: a guard that is computed but never passed, or
    gated behind an env read, is inert while every unit test of the guard itself
    still passes. Both halves are checked -- the set is computed unconditionally,
    and no environment lookup guards it.
    """

    source = inspect.getsource(font_based.FontBasedStrategy._extract_raw_document)
    assert "name_legacy_confirmed = detect_name_legacy_candidates(" in source
    assert "LIKHIT_NAME_LEGACY_GATE" not in source
    assert "os.environ" not in source
    # And nothing anywhere in the module may reintroduce an off switch for it.
    module_source = inspect.getsource(font_based)
    assert "LIKHIT_NAME_LEGACY_GATE" not in module_source


def test_all_three_extract_call_sites_pass_the_confirmed_set() -> None:
    """Activation, not implementation -- the VOL-614 / VOL-588 failure mode.

    `_convert_span_text` fails OPEN when `name_legacy_confirmed is None`, so a
    call site that forgets the keyword leaves the guard inert on that pass and
    every unit test of the guard itself still passes. VOL-614 shipped exactly
    that bug (its own fix commit is "pass the embedded binding at all three
    call sites"), so this asserts the count at the source.
    """

    source = inspect.getsource(font_based.FontBasedStrategy._extract_raw_document)
    assert source.count("name_legacy_confirmed=name_legacy_confirmed") == 3

    signature = inspect.signature(font_based.FontBasedStrategy._convert_span_text)
    assert signature.parameters["name_legacy_confirmed"].default is None

    inner = inspect.getsource(font_based.FontBasedStrategy._extract_from_document)
    assert inner.count("name_legacy_confirmed=name_legacy_confirmed") == 1


def test_confirmed_set_is_consulted_before_the_converter_is_resolved() -> None:
    """A font absent from the set must not decode, whatever its name says.

    Exercised through `_convert_span_text` directly, which is where the guard
    lives, with a font name the registry certainly claims.
    """

    extractor = font_based.FontBasedStrategy()
    strategies = {"Preeti": "legacy_remap"}

    decoded = extractor._convert_span_text(
        "2070",
        "Preeti",
        strategies,
        False,
        name_legacy_confirmed=frozenset({"Preeti"}),
    )
    declined = extractor._convert_span_text(
        "2070",
        "Preeti",
        strategies,
        False,
        name_legacy_confirmed=frozenset(),
    )
    unguarded = extractor._convert_span_text("2070", "Preeti", strategies, False)

    assert declined == "2070", "an unconfirmed font must keep its raw text"
    assert decoded != "2070", "a confirmed font must still decode"
    assert unguarded == decoded, "None must preserve pre-VOL-630 behaviour"


class _FakePage:
    def __init__(self, spans: list[tuple[str, str]]) -> None:
        self._spans = spans


class _FakeDoc:
    """Enough of a `fitz.Document` for `detect_name_legacy_candidates`.

    The function needs `page_count` and indexing; the span text arrives through
    `get_cid_marked_page_dict`, which the tests below monkeypatch. Driving it
    this way tests the aggregation and the gate rather than PyMuPDF's font
    embedding, and lets a span carry an arbitrary resource name -- which is the
    whole subject here and is not reachable via `insert_font` without shipping a
    fixture TTF named `Preeti`.
    """

    def __init__(self, pages: list[list[tuple[str, str]]]) -> None:
        self._pages = [_FakePage(spans) for spans in pages]
        self.page_count = len(self._pages)

    def __getitem__(self, index: int) -> _FakePage:
        return self._pages[index]


def _page_dict_for(page: _FakePage) -> dict:
    return {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {"font": font, "text": text} for font, text in page._spans
                        ]
                    }
                ]
            }
        ]
    }


@pytest.fixture
def fake_page_dicts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(font_based, "get_cid_marked_page_dict", _page_dict_for)


# Real Preeti keystrokes: 'clVtof/ b'?kof]u cg';Gwfg cfof]u' decodes to
# 'अख्तियार दुरुपयोग अनुसन्धान आयोग' -- the citation legacy_maps.py uses to pin the
# Preeti-vs-Himali distinction, so it is attested in-repo, not invented here.
PREETI_PROSE = "clVtof/ b'?kof]u cg';Gwfg cfof]u " * 4


def test_detector_confirms_a_prose_font_and_refuses_its_digit_companion(
    fake_page_dicts: None,
) -> None:
    """The behavioural control: two fonts, one document, opposite verdicts.

    `Preeti` carries keystroke prose and must be confirmed -- otherwise the gate
    is not a gate but an off switch. `PreetiExt` carries only clause numbers and
    must not be, and it is claimed by the registry through the very same
    `preeti` substring, which is the defect VOL-630 exists to close.
    """

    doc = _FakeDoc(
        [
            [
                ("Preeti", PREETI_PROSE),
                ("PreetiExt", "2070 179 23.2 25,70,266/- 100"),
            ]
        ]
    )

    confirmed = detect_name_legacy_candidates(doc)

    assert "Preeti" in confirmed, "a genuine keystroke prose font must still decode"
    assert "PreetiExt" not in confirmed, "a digit-only companion must be refused"


def test_detector_decides_on_the_aggregate_not_the_span(
    fake_page_dicts: None,
) -> None:
    """Scope, asserted where it actually matters.

    The same font's short spans are individually far below `devanagari >= 8`;
    aggregated across pages they clear the gate. A per-span port would refuse
    every one of these, which is the regression the aggregate scope avoids.
    """

    per_span = PREETI_PROSE.split()
    assert all(len(chunk) < 12 for chunk in per_span[:4])

    doc = _FakeDoc([[("Preeti", chunk)] for chunk in per_span])
    assert "Preeti" in detect_name_legacy_candidates(doc)

    # And the negative control for the same instrument: one short span alone,
    # in its own single-page document, does NOT clear the floor.
    lone = _FakeDoc([[("Preeti", per_span[0])]])
    assert detect_name_legacy_candidates(lone) == frozenset()


def test_detector_honours_skip_pages(fake_page_dicts: None) -> None:
    """`skip_pages` must remove a page's evidence, not just its output.

    The caller passes `skip_for_content`, so text on an OCR page cannot
    corroborate a font the extraction will never emit from that page.
    """

    doc = _FakeDoc([[("Preeti", PREETI_PROSE)], [("Preeti", "2070")]])

    assert "Preeti" in detect_name_legacy_candidates(doc)
    # Skipping the prose page (1-based) removes the only real evidence.
    assert detect_name_legacy_candidates(doc, frozenset({1})) == frozenset()


def test_detector_ignores_fonts_no_name_mechanism_claims(
    fake_page_dicts: None,
) -> None:
    """A font the registry does not claim is not this path's business.

    `Spins_EXT` is the companion the issue names, and it is absent from
    `_REGISTRY` today -- so it must be absent from the confirmed set too, rather
    than being silently adopted by the name path.
    """

    doc = _FakeDoc([[("Helvetica", "ordinary text"), ("Spins_EXT", "179 23.2")]])
    assert detect_name_legacy_candidates(doc) == frozenset()


def test_detect_name_legacy_candidates_has_no_tie_mask() -> None:
    """The tie mask is deliberately NOT ported, and this pins the reason.

    Masking resolves candidates level on every axis. The name path evaluates
    exactly one map -- the one the name selects -- so there is no tie to detect
    and nothing to mask. Asserted so a later reader does not "restore" a mask
    that would have no referent, and so the omission is a recorded decision
    rather than a gap.
    """

    source = inspect.getsource(detect_name_legacy_candidates)
    assert "_ambiguous_code_points" not in source
    assert "_decode_masking" not in source
    # It must reach the gate, though -- the omission is the mask, not the gate.
    assert "_passes_name_legacy_gate" in source


# --------------------------------------------------------------------------
# Card `6980ed7f` conditions 3 and 5 (run 3a6cc95e).
# --------------------------------------------------------------------------

#: One population, scored by BOTH gates, so "the content path is untouched" is a
#: set comparison rather than a sentence in a report (condition 5, same discipline
#: as VOL-649). Every entry is real keystrokes over a real map: the four fixtures
#: VOL-630 scope item 3 names, the two mastheads card `6980ed7f` was decided on,
#: `legacy_maps.py`'s own attested Preeti citation, and the digit companion
#: aggregate the detector must refuse.
GATE_POPULATION: tuple[tuple[str, str, str], ...] = (
    ("prose_hits2_preeti", "clVtof/ b'?kof]u cg';Gwfg cfof]u ", "Preeti"),
    ("prose_masthead_preeti", "dxfn]vfk/LIfssf] sfof{no", "Preeti"),
    ("prose_himalb_masthead", "g]kfn sfg]g klqsf @)^%, c+s ^", "Preeti"),
    ("lone_word_preeti", "clVtof/", "Preeti"),
    ("digits_2070_preeti", "2070", "Preeti"),
    ("digits_parens_preeti", "(1)(2)(3)", "Preeti"),
    ("money_preeti", "25,70,266/-", "Preeti"),
    ("short_100_preeti", "100", "Preeti"),
    ("companion_aggregate_preeti", "2070 179 23.2 25,70,266/- 100", "Preeti"),
    (
        "english_prose_preeti",
        "Office of the Auditor General Nepal Annual Report",
        "Preeti",
    ),
    ("digits_2070_pcs", "2070", "PCS NEPALI"),
    ("digits_parens_pcs", "(1)(2)(3)", "PCS NEPALI"),
    ("money_pcs", "25,70,266/-", "PCS NEPALI"),
    ("digits_agg_himali", "2070 179 23.2 25,70,266/- 100", "FONTASY_HIMALI_TT"),
    ("garble_repeat_kantipur", "¨¨¨", "Kantipur"),
)

#: 🛑 PINNED. The content path's fire set over `GATE_POPULATION`. This is the
#: assertion that makes VOL-630 safe: the name path's disjuncts are confined to a
#: near-copy, so this set must not move when they change. If a future edit moves
#: it, the change has reached content-path policy and VOL-635's decision puts that
#: explicitly out of bounds -- re-derive and justify, do not re-pin.
CONTENT_FIRE_SET = frozenset({"prose_hits2_preeti"})

#: 🛑 PINNED. What the name path's two disjuncts add on top, and nothing else.
#: Three digit cases on maps whose own ASCII digit row lands on Devanagari digits,
#: and the two mastheads -- `hits == 1` with a corroborated zero-garble decode.
NAME_RESCUED_SET = frozenset(
    {
        "prose_masthead_preeti",
        "prose_himalb_masthead",
        "digits_parens_pcs",
        "money_pcs",
        "digits_agg_himali",
    }
)


def _fire_sets() -> tuple[frozenset[str], frozenset[str]]:
    content: set[str] = set()
    name: set[str] = set()
    for case_id, raw, map_key in GATE_POPULATION:
        converter = get_converter_for_map(map_key)
        validity = _nepali_validity(converter(raw))
        if _passes_content_legacy_gate(validity):
            content.add(case_id)
        if font_based._passes_name_legacy_gate(validity, raw, converter):
            name.add(case_id)
    return frozenset(content), frozenset(name)


def test_content_path_fire_set_is_unchanged_by_the_name_path_disjuncts() -> None:
    """Condition 5: the content path is untouched, asserted as a SET comparison.

    A report sentence cannot catch a leak; an equality on the fire set can. The
    two gates are scored over one population so the comparison is meaningful --
    scoring the content gate against a population only it can pass would be
    vacuous.
    """

    content_fires, name_fires = _fire_sets()

    assert content_fires == CONTENT_FIRE_SET
    # The name gate is a strict WEAKENING of the content gate: it may admit more,
    # never less. A content decision that the name path loses would be a
    # regression this equality would miss, so it is asserted separately.
    assert content_fires < name_fires
    assert name_fires - content_fires == NAME_RESCUED_SET


def test_content_gate_structurally_cannot_consult_name_path_evidence() -> None:
    """And the content path cannot drift into name-path policy by accident.

    `_passes_content_legacy_gate` takes the validity dict and nothing else, so it
    has no access to the raw aggregate or the converter -- the two inputs both
    name-path disjuncts read. That is a structural guarantee, not a convention,
    and it is what lets the near-copy stay a near-copy.
    """

    assert list(inspect.signature(_passes_content_legacy_gate).parameters) == [
        "validity"
    ]
    assert "_is_digit_transliteration" not in inspect.getsource(
        _passes_content_legacy_gate
    )
    assert "attested" not in inspect.getsource(_passes_content_legacy_gate)


@pytest.mark.parametrize(
    ("case_id", "raw", "map_key"),
    [
        ("digits_2070_preeti", "2070", "Preeti"),
        ("digits_parens_preeti", "(1)(2)(3)", "Preeti"),
        ("lone_word_preeti", "clVtof/", "Preeti"),
    ],
)
def test_clean_decode_disjunct_needs_both_arms(
    case_id: str, raw: str, map_key: str
) -> None:
    """🛑 Why `penalty_per_deva == 0.0` is NOT sufficient on its own.

    Each span here decodes with `deva >= 8`, `ratio >= 0.6` and a penalty of
    exactly zero under a CONSONANT map, and spells nothing -- `2070` becomes
    `द्दण्ठण्`. So the uncorroborated disjunct admits them, which re-opens the very
    hole `_is_digit_transliteration` closes. This asserts the mechanism directly:
    the three unamended terms all pass, the penalty really is exactly zero, and
    the gate still refuses -- which can only be the corroboration arm.
    """

    converter = get_converter_for_map(map_key)
    validity = _nepali_validity(converter(raw))

    assert validity["penalty_per_deva"] == 0.0
    assert validity["devanagari"] >= _CONTENT_LEGACY_MIN_DEVA
    assert validity["ratio"] >= _CONTENT_LEGACY_MIN_DEVA_RATIO
    assert validity["hits"] < _CONTENT_LEGACY_MIN_HITS
    # Neither corroborating signal is present: a consonant map, and no attested word.
    assert font_based._map_transliterates_ascii_digits(converter) is False
    assert validity["attested"] == 0

    assert font_based._passes_name_legacy_gate(validity, raw, converter) is False


def test_text_quality_penalty_is_an_integer_count() -> None:
    """Condition 3: the exact `== 0.0` compare is only correct while this holds.

    `penalty_per_deva` is `_text_quality_penalty(text) / devanagari`. If the
    numerator ever becomes fractional, `== 0.0` stops being a test for "no
    artifacts" and a tolerance would be needed instead -- so the type is asserted
    rather than assumed. A `0.0000` display is not a demonstration of exact zero;
    an `int` numerator is.
    """

    for _case_id, raw, map_key in GATE_POPULATION:
        decoded = get_converter_for_map(map_key)(raw)
        assert type(font_based._text_quality_penalty(decoded)) is int

    assert type(font_based._DUPLICATE_CONSONANT_WEIGHT) is int
    # And the compare is reachable rather than vacuous: at least one real case in
    # the population above scores exactly zero.
    assert any(
        _nepali_validity(get_converter_for_map(map_key)(raw))["penalty_per_deva"] == 0.0
        for _case_id, raw, map_key in GATE_POPULATION
    )
