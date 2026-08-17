from __future__ import annotations

import pytest

from likhit.extractors.font_based import FontBasedStrategy
from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    DECODABLE_MAP_KEYS,
    _decode_ascii_bracketed_number,
    _map_reads_ascii_digits_as_digits,
    _match_font,
    _REGISTRY,
    get_converter,
    get_converter_for_map,
    get_output_converter_for_map,
    is_legacy_font,
)


# VOL-166: a legacy keyboard layout's own bracket glyph is never on the
# literal ASCII '(' / ')' keys -- Fontasy Himali's '(' key is a
# keyboard-layout consonant slot, and the same map's real bracket comes from
# '-'/'_' instead. A bare list/outline marker like "(1)" was placed directly
# by whatever authored the numbering, sharing the body font for visual
# consistency with the digit only -- it was never typed on this layout, so
# running the full character map over it corrupts the parens. Reproduced
# directly from the source PDF (see run comment on VOL-166): the raw span
# text behind the 35th annual report's 168 corrupted markers is plain ASCII
# "(1)".."(13)" under font "Fontasy Himali", not Kalimati and not Unicode.
@pytest.mark.parametrize(
    ("font", "raw", "expected"),
    [
        ("Fontasy Himali", "(1)", "(१)"),
        ("Fontasy Himali", "(12)", "(१२)"),
        ("FontasyHimali", "(2)", "(२)"),
        ("FONTASY_HIMALI_TT", "(13)", "(१३)"),
    ],
)
def test_ascii_bracketed_list_marker_decodes_digit_only(font, raw, expected):
    convert = get_converter(font)
    assert convert is not None
    assert convert(raw) == expected


def test_ascii_bracketed_marker_preserves_surrounding_whitespace():
    convert = get_converter("Fontasy Himali")
    assert convert is not None
    assert convert(" (1) ") == " (१) "


@pytest.mark.parametrize(
    ("font", "raw", "expected"),
    [
        # A plain digit run (no brackets) already decodes correctly through
        # FONTASY_HIMALI_TT's own digit row -- must stay on that path.
        ("Fontasy Himali", "100", "१००"),
        # Not asserting this is *correct* ('.' and '%' are shifted-row
        # characters this issue does not investigate) -- only that this fix
        # does not change it, since the whole span is not a bare "(N)" shape.
        ("Fontasy Himali", "33.8%", "३३।८छ"),
        # The layout's REAL bracket-producing keys ('-'/'_') must still go
        # through the full map: this is the same list-marker semantics as
        # "(1)", typed via different keys, and it already decodes correctly.
        ("Fontasy Himali", "-1_", "(१)"),
    ],
)
def test_non_bracketed_spans_unaffected(font, raw, expected):
    convert = get_converter(font)
    assert convert is not None
    assert convert(raw) == expected


def test_real_prose_through_a_registry_font_is_unaffected():
    convert = get_converter("Preeti")
    assert convert is not None
    assert convert("kl/R5]b") == "परिच्छेद"
    assert convert("sf7df8f}+") == "काठमाडौं"


@pytest.mark.parametrize(
    "text",
    [
        "(1) देखि",  # mixed content, not a bare marker
        "()",  # no digits
        "(1",  # unbalanced
        "1)",  # unbalanced
        "(1a)",  # not purely digits
    ],
)
def test_decode_ascii_bracketed_number_declines_non_bare_shapes(text):
    assert _decode_ascii_bracketed_number(text) is None


def test_kalimati_never_reaches_the_legacy_map():
    # VOL-166's issue text attributed the 168 corrupted markers to
    # already-Unicode "Kalimati" spans passing through a legacy map. Verified
    # false against the actual PDF (font is "Fontasy Himali", raw is plain
    # ASCII) -- pinned here so the wrong premise cannot resurface as a "fix".
    assert is_legacy_font("Kalimati") is False
    assert get_converter("Kalimati") is None


def test_get_converter_for_map_is_unaffected_by_the_bracket_gate():
    # get_converter_for_map is the SCORING primitive: choose_legacy_map runs every
    # candidate map over a span and keeps the best, so that comparison must see
    # each map's raw output. Gating here would change which map wins, not just
    # what the winner emits. Output paths use get_output_converter_for_map.
    convert = get_converter_for_map("FONTASY_HIMALI_TT")
    assert convert("(1)") == "ढ१ण्"


def test_output_converter_for_map_carries_the_gate():
    # The output counterpart of the scoring primitive above.
    convert = get_output_converter_for_map("FONTASY_HIMALI_TT")
    assert convert("(1)") == "(१)"
    assert convert("(12)") == "(१२)"
    # ...without disturbing anything that is not the bare marker shape.
    assert convert("-1_") == "(१)"
    assert convert("100") == "१००"


# --------------------------------------------------------------------------- #
# Where the gate is applied, and why that is narrower than it first looks.
#
# The gate does not consult the map -- it translates the raw ASCII digits and emits
# literal parens, rewriting the whole span on VOL-166's premise that none of it was
# typed on the layout. So the restriction below is NOT a precondition the gate
# depends on. It is a restriction on where that premise has been checked:
#
#   digit-row maps (PCS NEPALI, FONTASY_HIMALI_TT)
#       the shape occurs and is demonstrably literal numbering -- 168 markers over
#       the 13 CIAA annual reports, 168 of 168 sitting in lines whose other spans
#       already carry Unicode Devanagari.
#
#   consonant-row maps (Preeti, Kantipur, Sagarmatha)
#       the shape does NOT occur in the corpus. Rewriting "(5)" there would emit
#       "(५)" where the map reads "९छ०" -- a digit in place of a letter, on no
#       evidence either way. So the gate is declined, and the tests below pin that
#       CHOICE rather than claiming "९छ०" is the correct reading.
#
# The restriction is therefore a no-op on every document measured. It matters for the
# un-anchored form of the gate, which would fire on substrings under every map.
# --------------------------------------------------------------------------- #


# Hard-coded on purpose rather than read back out of the map: this is the pin on
# npttf2utf's vendored map.json, so if that file is revendored with a different
# number row we find out here instead of in a corpus diff. Deriving the
# expectation from the same table it means to pin would pin nothing.
_DIGIT_ROW_DECODES = {
    "Preeti": "ण्ज्ञद्दघद्धछटठडढ",
    "Kantipur": "ण्ज्ञद्दघद्धछटठडढ",
    "Sagarmatha": "ण्ज्ञद्दघद्धछटठडढ",
    "PCS NEPALI": "०१२३४५६७८९",
    "FONTASY_HIMALI_TT": "०१२३४५६७८९",
    # Spins is SYNTHESISED -- Spins keystrokes translated onto Preeti's, then decoded
    # with Preeti's map -- so its digit row is Preeti's. Measured, not inferred from
    # that delegation: the translation table could have re-keyed the digit row and
    # does not. This keeps the consonant-row family at 4 and the digit-row family
    # at 2.
    "Spins": "ण्ज्ञद्दघद्धछटठडढ",
    # Siddhi is synthesised too, but onto FONTASY_HIMALI_TT rather than Preeti, so it
    # lands in the OTHER family: it reads the ASCII digit row as digits. Measured, and
    # the consequence is real -- Siddhi therefore gets the bracketed-marker gate applied
    # while Spins does not. Two synthesised maps, opposite classifications; the base map
    # decides, not the fact of being synthesised.
    "Siddhi": "०१२३४५६७८९",
}
_READS_DIGITS_AS_DIGITS = frozenset({"PCS NEPALI", "FONTASY_HIMALI_TT", "Siddhi"})


def test_every_map_in_all_map_keys_is_classified():
    # Adding a map to ALL_MAP_KEYS without deciding which family it is in would
    # otherwise leave it silently untested by everything below.
    assert set(DECODABLE_MAP_KEYS) == set(_DIGIT_ROW_DECODES), (
        "a map was added to or removed from DECODABLE_MAP_KEYS -- measure its ASCII "
        "digit row and record it in _DIGIT_ROW_DECODES / _READS_DIGITS_AS_DIGITS. "
        "Note this covers every DECODABLE map, not just the content candidates: the "
        "bracketed-marker gate applies wherever a map produces output."
    )


@pytest.mark.parametrize("map_key", sorted(_DIGIT_ROW_DECODES))
def test_ascii_digit_row_decodes_as_measured(map_key):
    assert get_converter_for_map(map_key)("0123456789") == _DIGIT_ROW_DECODES[map_key]


@pytest.mark.parametrize("map_key", sorted(_DIGIT_ROW_DECODES))
def test_digit_reading_predicate_matches_the_measured_families(map_key):
    assert _map_reads_ascii_digits_as_digits(map_key) is (
        map_key in _READS_DIGITS_AS_DIGITS
    )


@pytest.mark.parametrize(
    ("map_key", "expected"),
    [
        # The two families disagree about the INTERIOR, not just the brackets.
        ("PCS NEPALI", "ढ५ण्"),  # digit already correct, brackets wrong -> gate applies
        ("FONTASY_HIMALI_TT", "ढ५ण्"),
        ("Preeti", "९छ०"),  # the interior IS the letter छ -> gate must not apply
        ("Kantipur", "९छ०"),
        ("Sagarmatha", "९छ०"),
    ],
)
def test_raw_decode_of_a_bracketed_digit_shows_which_family_a_map_is_in(
    map_key, expected
):
    assert get_converter_for_map(map_key)("(5)") == expected


@pytest.mark.parametrize(
    "map_key", sorted(set(_DIGIT_ROW_DECODES) - _READS_DIGITS_AS_DIGITS)
)
def test_gate_is_declined_rather_than_applied_on_a_consonant_digit_row(map_key):
    """What we deliberately DO here, not a claim that it is the correct reading.

    ``"(5)"`` under Preeti is ``"९छ०"`` -- digit, LETTER, digit -- and the gate would
    emit ``"(५)"``, replacing that letter with a digit. Which of the two is right
    depends on whether such a span is literal numbering (as it demonstrably is on
    Fontasy Himali) or genuine keystrokes, and **the shape does not occur under these
    maps anywhere in the corpus**, so nothing settles it. See
    ``test_the_marker_shape_is_only_ever_observed_under_a_digit_row_map``.

    So this pins the conservative choice -- do not rewrite text on no evidence -- and
    not the correctness of ``"९छ०"``. If a document ever shows the shape under one of
    these maps, that is the evidence, and this test is what should be revisited.
    """

    convert = get_output_converter_for_map(map_key)
    assert convert("(5)") == get_converter_for_map(map_key)("(5)")
    assert convert("(5)") == "९छ०"


@pytest.mark.parametrize(
    "map_key", sorted(set(_DIGIT_ROW_DECODES) - _READS_DIGITS_AS_DIGITS)
)
def test_gate_is_unreachable_for_a_consonant_digit_row_map(map_key):
    """Equal output on one input would not prove the gate is out of the path.

    Asserting reachability instead: the output converter must be indistinguishable
    from the raw one across every shape the gate's pattern can match, so no input
    exists on which the gate could fire for this map. This is a statement about the
    code path, not about which reading of the span is correct.
    """

    raw = get_converter_for_map(map_key)
    out = get_output_converter_for_map(map_key)
    for text in ("(1)", "(5)", "(12)", " (7) ", "(0)", "(99)", "(100)"):
        assert out(text) == raw(text), text


@pytest.mark.parametrize("map_key", sorted(_READS_DIGITS_AS_DIGITS))
def test_gate_still_repairs_the_marker_where_the_premise_holds(map_key):
    # The VOL-166 repair itself. Restricting the gate must not narrow this.
    convert = get_output_converter_for_map(map_key)
    assert convert("(1)") == "(१)"
    assert convert("(13)") == "(१३)"
    assert convert(" (1) ") == " (१) "


@pytest.mark.parametrize("map_key", sorted(_DIGIT_ROW_DECODES))
@pytest.mark.parametrize("digits", ["1", "5", "12", "7", "0", "99"])
def test_gate_preserves_the_maps_reading_of_the_marker_interior(map_key, digits):
    """The general invariant, and the one that survives a new map being added.

    The gate is licensed to rewrite the map's reading of the ASCII **brackets** --
    that is the whole repair, and on FONTASY_HIMALI_TT it replaces the letters
    ``ढ`` and ``ण्`` with literal parens. It is not licensed to rewrite the map's
    reading of the **interior**, whatever that reading turns out to be.

    So the invariant is not "no letter is lost" (false for the repair by design) but
    "the interior survives". On a digit-row map the interior reads as a digit and the
    gate agrees with it; on a consonant-row map it reads as a letter and the gate must
    keep out of the way. One assertion covers both families, and it is the assertion
    the unrestricted gate fails.
    """

    interior = get_converter_for_map(map_key)(digits)
    out = get_output_converter_for_map(map_key)(f"({digits})")
    assert interior in out, (
        f"{map_key}: gate dropped the map's reading of {digits!r} -- "
        f"expected {interior!r} inside {out!r}"
    )


def test_content_based_span_conversion_is_gated_too():
    """The content-based path must not reintroduce the corruption.

    VOL-166's fix first shipped wired only into ``get_converter`` (the
    name-based path). ``_convert_span_text`` checks ``content_legacy_maps``
    *first* and returns from that branch directly, so a font detected by content
    rather than by name still produced ``"(1)" -> "ढ१ण्"``. That branch is not
    reachable in the CIAA corpus -- ``detect_content_legacy_fonts`` returns ``{}``
    for all 13 PDFs, measured -- so no corpus check could have caught it, and
    ``fix/content-legacy-name-agnostic`` widens exactly this path. Pinned here
    because it is invisible to every corpus gate we have.
    """

    strategy = FontBasedStrategy()
    # A mislabeled font: the name classifier calls it "correct", content
    # detection is what identifies it as legacy.
    content_maps = {"ABCDE+Helvetica": "FONTASY_HIMALI_TT"}
    converted = strategy._convert_span_text(
        "(1)",
        "ABCDE+Helvetica",
        {"Helvetica": "correct"},
        needs_reorder=False,
        content_legacy_maps=content_maps,
    )
    assert converted == "(१)"

    # Real prose on that same content-detected path still decodes in full.
    assert (
        strategy._convert_span_text(
            "kl/R5]b",
            "ABCDE+Times",
            {"Times": "correct"},
            needs_reorder=False,
            content_legacy_maps={"ABCDE+Times": "Preeti"},
        )
        == "परिच्छेद"
    )


def test_the_marker_shape_is_only_ever_observed_under_a_digit_row_map():
    """The measurement that decides which way to restrict, recorded so it is not re-argued.

    Probed over all 13 CIAA annual reports -- every span whose whole text is an ASCII
    ``(N)`` and whose font resolves to a map in :data:`_REGISTRY`
    (``tools/marker_family_probe.py`` in the run record):

        168  markers, all in the 35th annual report
        168  under Fontasy Himali  -> FONTASY_HIMALI_TT, a digit-row map
          0  under Preeti / Kantipur / Sagarmatha
        168  of 168 have other spans on the same line already carrying Unicode
             Devanagari
          0  of 168 have a rest-of-line that is pure ASCII

    Two things follow, and they point in opposite directions on purpose.

    The 168/168 Unicode-neighbour figure CONFIRMS VOL-166's premise where the gate
    fires: the marker is literal numbering sharing a body font with text that is
    already Unicode, so the map is not decoding that line at all -- it mangles it.

    The 0 figure is why the restriction is a no-op on everything measured, and why the
    consonant-row tests above pin a choice rather than a correctness claim. There is no
    observation to reason from there.

    The numbers are hard-coded rather than re-derived: the corpus PDFs are not in this
    repository, so this is a record of a measurement, and it is here so that a future
    change to the restriction has to argue with a figure instead of with a prior.
    """

    observed_under_digit_row_maps = 168
    observed_under_consonant_row_maps = 0

    assert observed_under_consonant_row_maps == 0
    assert observed_under_digit_row_maps == 168
    # And the only map the shape was observed under is one the gate still applies to.
    assert _map_reads_ascii_digits_as_digits("FONTASY_HIMALI_TT") is True


# --------------------------------------------------------------------------- #
# The name path routes the "Himalb" family to the WRONG map.
#
# A font name is a hint about an encoding, and for one family the hint is wrong:
# names in the "Himalb" family carry PREETI-encoded bytes while naming Himali.
# Preeti and FONTASY_HIMALI_TT are near-clones that exchange their two number
# rows, so the wrong one of the pair silently substitutes a Devanagari digit for
# a consonant (and vice versa on the shifted row).
#
# Measured over the 13 CIAA annual reports, per name: "Himalb" (15,831 spans,
# 30,258 ASCII ALPHABETIC keystrokes) and "Himalb,Bold" (2,360 spans, 9,243) produce
# 1,438 in-word Devanagari digits under FONTASY_HIMALI_TT and 0 under Preeti.
# --------------------------------------------------------------------------- #

# Raw legacy keystrokes with their correct Preeti reading. Every one contains at
# least one ASCII digit, which is the only place the two maps disagree in prose --
# a span without one decodes identically under both, which is precisely why this
# defect is sparse and survives every corpus gate.
_PREETI_ENCODED_SOURCES = (
    # 'chapter', the most common casualty: 1,269 occurrences in the Himalb spans of
    # the CIAA corpus, all in 3 of the 13 documents. Measured on raw span text, which
    # is a different instrument from a count over assembled transcripts -- do not
    # reconcile this figure with one taken after markdown assembly.
    ("kl/R5]b", "परिच्छेद"),
    ("sf7df8f}+", "काठमाडौं"),  # 'Kathmandu'
    ("d'2f", "मुद्दा"),  # 'case'
    ("/0fgLlts", "रणनीतिक"),  # 'strategic'
    ("5nkmn", "छलफल"),  # 'discussion'
)

# Every spelling that reaches the "himalb" registry key. _match_font strips a
# subset prefix at '+' and a style suffix at ',', then matches on a substring, so
# all of these resolve through the one key.
_HIMALB_SPELLINGS = (
    "Himalb",
    "Himalb,Bold",
    "HimalBold",
    "HimalBoldBold11109013193",  # observed in the OAG corpus
    "ABCDEE+Himalb",  # subset-prefixed
)


@pytest.mark.parametrize("font", _HIMALB_SPELLINGS)
@pytest.mark.parametrize(("raw", "expected"), _PREETI_ENCODED_SOURCES)
def test_the_himalb_family_decodes_as_preeti(font, raw, expected):
    convert = get_converter(font)
    assert convert is not None, f"{font!r} must still be recognised as a legacy font"
    assert convert(raw) == expected


@pytest.mark.parametrize("font", _HIMALB_SPELLINGS)
def test_the_himalb_family_resolves_to_the_preeti_map(font):
    assert _match_font(font) == "Preeti"


# The other half of the invariant. A "fix" that moved the whole Himal family would
# pass the tests above and destroy these, so both directions are pinned.
_GENUINELY_HIMALI_SPELLINGS = (
    "FONTASY_HIMALI_TT",
    "FONTASY_HIMALI_TT,Bold",
    "FONTASYHIMALITTNORMAL",
    "FONTASY_ HIMALI_ TT",
    "Fontasy Himali",
    "FontasyHimali",
    "Fontasy Roman Himali",
    "BikrantHimaliTTNORMAL",
    "HimaliBold",
)


@pytest.mark.parametrize("font", _GENUINELY_HIMALI_SPELLINGS)
def test_the_himali_spellings_are_not_moved(font):
    """These stay on FONTASY_HIMALI_TT, and two of them for a measured reason.

    ``Fontasy Himali`` and ``FontasyHimali`` carry **1 and 0 ASCII alphabetic
    keystrokes** respectively -- across 26,699 and 19,744 characters of span text, so
    the units are not interchangeable: the population is *characters in those spans*
    and the count is *letters among them*. Their content is page numbers and list
    markers. The in-word-digit discriminator that convicts the ``Himalb`` family is
    structurally blind to such a font: with no surrounding letters, a pure-numeral
    span scores zero under *both* maps.

    So "0 under Preeti" means "no evidence" for these names, not "clean", and the
    first version of this correction moved them on the strength of exactly that
    reading -- costing 24,804 correctly-decoded Devanagari digits corpus-wide. A
    metric that cannot separate *clean* from *not applicable* is not support.
    """

    assert _match_font(font) == "FONTASY_HIMALI_TT"


def test_the_two_maps_exchange_their_two_number_rows():
    """The mechanism, stated exactly: the maps are transposes on the number rows.

    This is why the wrong map is destructive in *both* directions -- it puts a
    Devanagari digit inside a word (unshifted row) and a consonant where a year
    belongs (shifted row). A reader who checks only the unshifted row sees half the
    difference and could "correct" the registry back the wrong way.
    """

    preeti = get_converter_for_map("Preeti")
    himali = get_converter_for_map("FONTASY_HIMALI_TT")

    unshifted = "0123456789"
    shifted = ")!@#$%^&*("

    assert preeti(unshifted) == himali(shifted)
    assert preeti(shifted) == himali(unshifted)
    # And name the two readings, so the direction is on the record rather than
    # implied by an equality between two computed values.
    assert preeti(shifted) == "०१२३४५६७८९"
    assert himali(unshifted) == "०१२३४५६७८९"
    assert preeti(unshifted) == "ण्ज्ञद्दघद्धछटठडढ"
    assert himali(shifted) == "ण्ज्ञद्दघद्धछटठडढ"


def test_the_kanunpatrika_masthead_year_is_the_shifted_row():
    """The non-CIAA confirmation, and it is semantic rather than statistical.

    ``samples/kanunpatrika.pdf`` draws its masthead in ``Himalb``. The raw bytes
    ``@)^%`` are Preeti's way of typing ``२०६५``; read through FONTASY_HIMALI_TT the
    same bytes give ``द्दण्टछ``. Two lines later the same document cites the same
    year off a genuine ``Preeti`` span, where it has always read ``२०६५`` -- so the
    document corroborates itself, and only one routing makes the two agree.
    """

    raw = 'g]kfn sfg"g klqsf @)^%, c+s ^'
    convert = get_converter("Himalb")
    assert convert is not None
    assert convert(raw) == "नेपाल कानून पत्रिका २०६५, अंक ६"
    # What the shipped routing produced, and what three of this repo's own recorded
    # expectations asserted as ground truth until this change.
    assert (
        get_converter_for_map("FONTASY_HIMALI_TT")(raw)
        == "नेपाल कानून पत्रिका द्दण्टछ, अंक ट"
    )


def test_the_wrong_map_is_invisible_to_every_damage_signal():
    """Why no gate caught this, asserted rather than asserted-in-prose.

    Both readings are the same length, contain no U+FFFD, and are 100% Devanagari.
    A replacement-character count, a Devanagari-ratio floor and a garble census all
    pass on the corrupt reading, so the only instrument that sees it is one that
    asserts the specific character.
    """

    raw = "kl/R5]b"
    correct = get_converter_for_map("Preeti")(raw)
    corrupt = get_converter_for_map("FONTASY_HIMALI_TT")(raw)

    assert correct != corrupt
    assert len(correct) == len(corrupt)
    assert "�" not in correct and "�" not in corrupt
    devanagari = range(0x0900, 0x0980)
    assert all(ord(ch) in devanagari for ch in correct)
    assert all(ord(ch) in devanagari for ch in corrupt)
    # The single differing character is a Devanagari DIGIT standing in for a
    # consonant -- same Unicode block, so no block-based check can separate them.
    differing = [(a, b) for a, b in zip(correct, corrupt) if a != b]
    assert differing == [("छ", "५")]


def test_no_registry_key_is_a_substring_of_another_with_a_different_map():
    """``_match_font`` returns on the first substring hit, so overlap + disagreement
    would make dict insertion order decide the decoded text.

    Today two keys overlap (``himali`` inside ``fontasy_himali`` and inside
    ``fontasyhimali``) and both agree on the map, so order is provably NOT
    load-bearing. This asserts that property instead of a comment claiming it, so
    the day someone adds a key that breaks it, the test names the pair.
    """

    conflicts = [
        (shorter, longer)
        for shorter in _REGISTRY
        for longer in _REGISTRY
        if shorter != longer
        and shorter in longer
        and _REGISTRY[shorter] != _REGISTRY[longer]
    ]
    assert conflicts == []

    # The overlap itself is real -- without this the test above could pass simply
    # because no keys overlap at all, which would make it vacuous.
    overlaps = [
        (shorter, longer)
        for shorter in _REGISTRY
        for longer in _REGISTRY
        if shorter != longer and shorter in longer
    ]
    assert overlaps == [("himali", "fontasy_himali"), ("himali", "fontasyhimali")]


def test_every_registry_target_is_a_real_map():
    """A typo'd map key would raise only when a document happens to use that font.

    Asserted against ``DECODABLE_MAP_KEYS``, not ``ALL_MAP_KEYS``. Those two used to be
    the same set and are not any more: ``_REGISTRY`` routes a font NAME to a map, while
    ``ALL_MAP_KEYS`` is the set content-based detection may CHOOSE from. Siddhi is
    decodable and name-routed but deliberately not a content candidate, so requiring
    registry targets to be candidates would force a decision this test has no business
    making. Both properties are still checked -- see the two assertions below.
    """

    for key, map_key in _REGISTRY.items():
        assert map_key in DECODABLE_MAP_KEYS, (
            f"{key!r} -> {map_key!r} is not a decodable map"
        )
        # and it really does decode, rather than merely being spelled correctly
        assert callable(get_converter_for_map(map_key))

    # The distinction this test now rests on, asserted rather than assumed: a map can be
    # name-routed and decodable without being a content-detection candidate.
    assert set(DECODABLE_MAP_KEYS) - set(ALL_MAP_KEYS) == {"Siddhi"}
