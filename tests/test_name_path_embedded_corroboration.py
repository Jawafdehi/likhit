"""VOL-675: item 2's embedded-`name` candidacy must be CORROBORATED before it decodes.

Implements Nisha's VOL-673 ruling, decision (b), on VOL-630's landed tip. The
invariant, verbatim:

    The embedded `name` table resolves candidacy, not arbitration. Where the
    embedded identity names a legacy family the resource name concealed and the
    content screen corroborates the same family, item 2 decodes. Where the two
    attributions name different families, item 2 declines.

🛑 **What these tests do NOT assert, deliberately.** The ruling's criterion 4 warns
that the exchanged-digit-row class is invisible to every Devanagari purity ratio, so
a purity-based assertion is a **vacuous green** here. Nothing below asserts a purity
ratio or a Devanagari share. The witnesses assert decoded TEXT and DIGIT COUNTS,
which are the only instruments that separate the four direction classes.

Every raw byte string below is real corpus text, with its document, page and font
recorded, so a reader can go back to the PDF. Measurements come from
`oag-corpus/runs/vol675-d487f8d9/`.
"""

from __future__ import annotations

from likhit.extractors.font_based import (
    _map_ranking_key,
    _map_transliterates_ascii_digits,
    _nepali_validity,
)
from likhit.extractors.legacy_maps import (
    _REGISTRY,
    ALL_MAP_KEYS,
    get_converter_for_map,
)

DEVANAGARI_DIGITS = "०१२३४५६७८९"
ASCII_DIGIT_ROW = "0123456789"
#: The Preeti-family SHIFTED row, in 0..9 order. `legacy_maps.py:22-24` records the
#: pairing; this spells it as a string so it can be converted and inspected.
SHIFTED_DIGIT_ROW = ")!@#$%^&*("

# --- Real corpus witnesses. (document, page, resource font) recorded per span.
# `screen` is VOL-576 pass B's content-screen attribution, `embedded` is the
# embedded `name` table's claim; the pair is what makes each span a winner change.

# damage class, 39 spans -- `PCS NEPALI -> Preeti`. Doc 2997 p2, CIDFont+F2.
W_DAMAGE = "k|ltj]bg n]vfk/LIf0f P]g,@)&% sf] bkmf @)-#_ / bkmf @@ adf]lhd sf/jfxLsf] nflu cg'/f]w 5 ."
# exchanged-row class, 12 spans -- `Preeti -> FONTASY_HIMALI_TT`. Doc 2939 p5, CIDFont+F9.
W_EXCHANGED = "af}wLdfO{ gu/kflnsf"
# repair class, 24 spans; this arm is `Preeti -> PCS NEPALI`. Doc 3012 p9, CIDFont+F12.
W_REPAIR = ";+:s[lt k|a${g sfo{qmd"
# neutral class, 3 spans -- `Spins -> Preeti`. Doc 2997 p15, CIDFont+F2.
W_NEUTRAL = (
    "5 . pkef]Qmf ;ldltaf6 tf]lsPsf] hg;xeflutf gh'6fpg] ;fy} sfof+noaf6 ug]+ e'QmfgLdf"
)

# A corroboratable aggregate: real 2997 bytes carrying two `hits` dictionary words
# (`कार्यालय` and `नेपाल`), which is what `_CONTENT_LEGACY_MIN_HITS` wants.
CORROBORATABLE_PREETI_AGGREGATE = (
    "dxfn]vfk/LIfssf] sfof{no"
    "g]kfnsf] ;+ljwfgsf] wf/f @$! adf]lhd To; sfo{kflnsfsf] cfly{s jif{ "
    "@)&%.&^ sf] n]vfk/LIf0f"
)


def _deva_digits(text: str) -> int:
    return sum(1 for char in text if char in DEVANAGARI_DIGITS)


def _decode(map_key: str, raw: str) -> str:
    return get_converter_for_map(map_key)(raw)


# ---------------------------------------------------------------------------
# Criterion 3: the digit-row group of every map involved, asserted AT THIS TIP,
# read from the maps. Not inherited from the ruling document.
# ---------------------------------------------------------------------------


def test_criterion_3_ascii_digit_row_group_at_this_tip() -> None:
    """PCS NEPALI and FONTASY_HIMALI_TT are 10/10 on ASCII `0-9`; the rest are 0/10.

    Read from the maps by converting the row, so this is falsifiable rather than
    quoted. It is the split VOL-630's `_is_digit_transliteration` leans on and the
    one the ruling prices the direction classes with.
    """

    group = {
        key: _map_transliterates_ascii_digits(get_converter_for_map(key))
        for key in ALL_MAP_KEYS
    }
    assert group == {
        "Preeti": False,
        "Kantipur": False,
        "PCS NEPALI": True,
        "FONTASY_HIMALI_TT": True,
        "Sagarmatha": False,
        "Spins": False,
    }, group


def test_criterion_3_the_two_row_groups_are_COMPLEMENTARY() -> None:
    """🛑 The addition that makes the ruling's direction labels falsifiable.

    The ruling's trap 1 says a consonant map can still render digits, because the
    Preeti-family **shifted** row `!@#$%^&*()` maps to Devanagari digits even though
    ASCII `0-9` does not. Measured here: that is not an exception, it is the exact
    complement. Every map is 10/10 on precisely ONE of the two rows.

    Consequence, and it is why the labels invert (`runs/vol675-d487f8d9/
    DIGIT-CENSUS-d487f8d9.json`): "which map preserves this span's digits" is a
    property of **which row the span carries**, not of the map alone. A direction
    label read off the map property is therefore unsound on its own.
    """

    for key in ALL_MAP_KEYS:
        ascii_row_ok = all(
            _decode(key, digit) in DEVANAGARI_DIGITS for digit in ASCII_DIGIT_ROW
        )
        shifted_row_ok = all(
            _decode(key, char) in DEVANAGARI_DIGITS for char in SHIFTED_DIGIT_ROW
        )
        assert ascii_row_ok != shifted_row_ok, (
            f"{key}: ascii={ascii_row_ok} shifted={shifted_row_ok} -- "
            "expected exactly one row to transliterate"
        )
        assert ascii_row_ok is _map_transliterates_ascii_digits(
            get_converter_for_map(key)
        )


def test_every_registry_map_key_is_an_ALL_MAP_KEYS_member() -> None:
    """Why a corroboration term may compare the two attributions' map keys with `==`.

    The embedded side comes from `_REGISTRY`'s values (via `match_legacy_map_name`)
    and the arbitration side from `ALL_MAP_KEYS`. If those vocabularies could differ
    the compare would need normalising, and a silent mismatch would decline every
    font. They do not differ, and this pins it -- for whichever form the re-decision
    on card `b7d4aa21` restores.
    """

    # 🛑 v17: the subset claim is FALSE on the composed tree and the compare it guards is
    # not live in this form. The Siddhi carry (VOL-471/494, likhit `1e9e6e9`) adds a
    # `_REGISTRY` target that is deliberately NOT in `ALL_MAP_KEYS` -- routed by name so
    # it does not become a 7th content candidate corpus-wide -- and `Spins` is the mirror
    # (in `ALL_MAP_KEYS`, absent from `_REGISTRY`). Neither set contains the other.
    #
    # The hazard this test names is real but conditional: IF the re-decision on card
    # `b7d4aa21` restores a form that compares the two vocabularies with `==`, that form
    # must normalise, because a name-routed-only key would otherwise silently decline
    # every font carrying it. The live corroboration term (`_passes_content_legacy_gate`)
    # compares CONVERTERS and validity, not map keys, so nothing is declined today.
    #
    # So the assertion becomes the property that actually has to hold for either form: a
    # name-matched key must resolve to a usable converter.
    for map_key in set(_REGISTRY.values()):
        assert get_converter_for_map(map_key)("kl/R5]b"), map_key
    name_only = set(_REGISTRY.values()) - set(ALL_MAP_KEYS)
    assert name_only in ({"Siddhi"}, set()), (
        f"a name-routed-only map key appeared that this test has not adjudicated: "
        f"{name_only}. If a key-equality compare is restored (card b7d4aa21) it must "
        f"normalise across the two vocabularies."
    )


# ---------------------------------------------------------------------------
# Criterion 4: one witness per direction class. Four separate tests, because a
# single witness cannot distinguish them.
# ---------------------------------------------------------------------------


def test_criterion_4_witness_DAMAGE_class_39_spans() -> None:
    """`PCS NEPALI -> Preeti`, doc 2997 p2 CIDFont+F2. The label is INVERTED here.

    The ruling prices these as the damage direction because the digit-row group
    moves 10/10 -> 0/10. On the span, the embedded map (Preeti) produces MORE
    correct Devanagari digits than the screen's pick, because the numerals are in
    the shifted row. Corpus-wide over all 39: 296 Devanagari digits under Preeti
    against 38 under PCS NEPALI, embedded better on 35 spans, screen on 3, tied 1.
    """

    under_embedded = _decode("Preeti", W_DAMAGE)
    under_screen = _decode("PCS NEPALI", W_DAMAGE)

    assert _deva_digits(under_embedded) == 9
    assert _deva_digits(under_screen) == 2
    assert _deva_digits(under_embedded) > _deva_digits(under_screen)

    # The prose reads correctly under the embedded map, numerals included.
    assert "प्रतिवेदन लेखापरीक्षण ऐन,२०७५ को दफा २०(३)" in under_embedded
    # And the screen's pick destroys exactly those numerals.
    assert "२०७५" not in under_screen
    assert "द्दण्ठछ" in under_screen


def test_criterion_4_witness_EXCHANGED_ROW_class_12_spans() -> None:
    """`Preeti -> FONTASY_HIMALI_TT`, doc 2939 p5 CIDFont+F9. Letters agree exactly.

    🛑 This is the class no Devanagari purity ratio can see: the two maps agree on
    every letter slot and have the two number rows exchanged, so both readings are
    well-formed Devanagari and both score identically on any purity measure. The
    only instrument that separates them is the digits, and this span carries none --
    which is why the swap is silent here and why the whole class must be withheld
    rather than adjudicated by a ratio.
    """

    under_embedded = _decode("FONTASY_HIMALI_TT", W_EXCHANGED)
    under_screen = _decode("Preeti", W_EXCHANGED)

    # No digits either side: the axis that distinguishes the maps is absent, so
    # nothing in the span's content can arbitrate between them.
    assert _deva_digits(under_embedded) == 0
    assert _deva_digits(under_screen) == 0

    # Letters identical -- the two attributions read this span the same way, so a
    # purity assertion would be a vacuous green.
    assert under_embedded == under_screen == "बौधीमाई नगरपालिका"

    # The maps are nonetheless genuinely different, on the rows this span lacks.
    assert _decode("Preeti", "2070") != _decode("FONTASY_HIMALI_TT", "2070")


def test_criterion_4_witness_REPAIR_class_24_spans() -> None:
    """`Preeti -> PCS NEPALI`, doc 3012 p9 CIDFont+F12. The 'repair' is vacuous.

    Corpus-wide the 24 carry ZERO ASCII `0-9` characters, so there is no ASCII digit
    row for the 0/10 -> 10/10 move to repair. Where they carry the shifted row
    instead, the direction is the other way: 0 Devanagari digits under the embedded
    map against 9 under the screen's, over 9 spans.
    """

    assert not any(char in ASCII_DIGIT_ROW for char in W_REPAIR)

    under_embedded = _decode("PCS NEPALI", W_REPAIR)
    under_screen = _decode("Preeti", W_REPAIR)

    assert _deva_digits(under_embedded) == 0
    assert _deva_digits(under_screen) == 1
    assert _deva_digits(under_screen) > _deva_digits(under_embedded)


def test_criterion_4_witness_NEUTRAL_class_3_spans() -> None:
    """`Spins -> Preeti`, doc 2997 p15 CIDFont+F2. Both maps are 0/10 on ASCII.

    Neutral on the digit axis by construction, and this witness shows the axis is
    genuinely silent: identical (zero) Devanagari digit counts under both. So the
    class cannot be decided on digits at all, which is the point of separating it
    from the other three.
    """

    under_embedded = _decode("Preeti", W_NEUTRAL)
    under_screen = _decode("Spins", W_NEUTRAL)

    assert not _map_transliterates_ascii_digits(get_converter_for_map("Preeti"))
    assert not _map_transliterates_ascii_digits(get_converter_for_map("Spins"))
    assert _deva_digits(under_embedded) == _deva_digits(under_screen) == 0
    # Spins is a permutation of Preeti on three key pairs, so the readings differ
    # even though the digit axis says nothing.
    assert under_embedded != under_screen


def test_the_four_witnesses_are_four_distinct_spans() -> None:
    """A single witness cannot distinguish the classes -- so assert there are four."""

    witnesses = [W_DAMAGE, W_EXCHANGED, W_REPAIR, W_NEUTRAL]
    assert len(set(witnesses)) == 4


# ---------------------------------------------------------------------------
# Criterion 6: reversibility, in one place, so restoring the 66 needs no
# re-derivation of the mechanism.
# ---------------------------------------------------------------------------


def test_criterion_6_reversibility_price_is_recorded() -> None:
    """What withholding the 66 would cost, so the decision needs no re-derivation.

    🛑 **This tip carries NO corroboration term, and that is a measured outcome, not
    an omission.** Both implementable forms were built and priced by paired
    byte-for-byte extraction against an untouched `27d39b7`
    (`runs/vol675-d487f8d9/PAIRED-EXTRACT-d487f8d9.json`):

    * **gated form** (`choose_legacy_map_detailed`): withholds the 24 as chartered
      but changes **15 of 20 documents for -4,103 Devanagari characters**, because
      that function ends in the *unamended* `_passes_content_legacy_gate` and a
      numeral-only aggregate cannot clear `hits >= 2`. It destroys `3012`'s audit
      money column. It re-opens the hole VOL-630 decision (b) closed.
    * **ranking form** (`_map_ranking_key`, comparative, composes correctly):
      3 documents / -866 characters, but withholds **0 of the 66**.

    Both mechanisms are preserved for restoration in
    `runs/vol675-d487f8d9/MECHANISM-ranking-form-d487f8d9.patch` and
    `MECHANISM-gated-form-d487f8d9.txt`, which also record the two test changes a
    restoration needs. Pending Nisha's re-decision card `b7d4aa21`.

    Prices measured over item 2's 534 reached spans:

    * the ruling's full 66 would withhold **3,775 chars / 14 documents** --
      damage 39 / 3,014 / 1 doc, repair 24 / 514 / 13 docs, neutral 3 / 247 / 1 doc,
      of which the exchanged-row 12 / 225 / 12 docs is a **subset** of repair;
    * the 42 that are damage + neutral are **3,261 chars, all in document `2997`**;
    * ⚠️ and the 534 is pass B's *screened* population, so any FONT-grain term costs
      more than these span figures -- a decline reverts every span of the font,
      including the numeral-only ones the screen filtered out. That gap is what made
      the gated form's real price 8x its predicted one.
    """

    ruling_target = {"spans": 66, "chars": 3775, "documents": 14}
    damage = {"spans": 39, "chars": 3014}
    repair = {"spans": 24, "chars": 514}
    neutral = {"spans": 3, "chars": 247}

    assert (
        damage["spans"] + repair["spans"] + neutral["spans"] == ruling_target["spans"]
    )
    assert (
        damage["chars"] + repair["chars"] + neutral["chars"] == ruling_target["chars"]
    )
    assert damage["spans"] + neutral["spans"] == 42
    assert damage["chars"] + neutral["chars"] == 3261


# ---------------------------------------------------------------------------
# Why no content-screen predicate can implement the invariant for this class.
# These are properties of the MAPS and of the corpus, so they outlive whichever
# predicate the re-decision picks.
# ---------------------------------------------------------------------------


def test_the_exchanged_row_pair_TIES_on_every_ranking_axis() -> None:
    """🛑 The structural block. Corroboration is unsatisfiable for this class.

    On a numeral-only aggregate, `PCS NEPALI` and `FONTASY_HIMALI_TT` produce
    readings that tie on **all six** of `_map_ranking_key`'s axes, so no
    content-evidence ranking can prefer either. Measured over all 13 of item 2's
    pure-disagreement `(doc, font)` pairs: tied 13 of 13, with identical Devanagari
    digit counts (13=13, 12=12, 893=893).

    This is `legacy_maps.py:26-29` stated as a test rather than a comment: the swap
    is "invisible to every existing gate: no U+FFFD, and no drop in any Devanagari
    purity ratio".
    """

    # `2939`/`CIDFont+F9`'s real aggregate: a cover page's numerals and title.
    aggregate = "2075.076"
    keys = {
        key: _map_ranking_key(_nepali_validity(_decode(key, aggregate)))
        for key in ("PCS NEPALI", "FONTASY_HIMALI_TT")
    }
    assert keys["PCS NEPALI"] == keys["FONTASY_HIMALI_TT"], keys
    assert _deva_digits(_decode("PCS NEPALI", aggregate)) == _deva_digits(
        _decode("FONTASY_HIMALI_TT", aggregate)
    )


def test_PCS_NEPALI_and_FONTASY_agree_on_BOTH_digit_rows() -> None:
    """So the winner change is consequence-free on a numeral-only aggregate.

    🛑 **Two different map relationships, and conflating them is an error.**
    `Preeti` ⇄ `FONTASY_HIMALI_TT` is the *exchanged digit row* pair -- that is the
    ruling's exchanged-row axis and the 12 `Preeti -> FONTASY_HIMALI_TT` spans.
    `PCS NEPALI` ⇄ `FONTASY_HIMALI_TT` is a different pair: they agree on **both**
    rows, and over the printable ASCII range they differ on exactly **five**
    characters -- `<`, `?`, `C`, `X`, `~`. `PCS NEPALI` ⇄ `FONTASY_HIMALI_TT` is the
    pair the *ranking* could not separate on item 2's 13 declining fonts.

    Measured corpus-wide: **11 of the 13** pure-disagreement pairs decode
    byte-identically, and the 2 that differ do so only via `?` -- `रू` against `रु`,
    vowel length. That is precisely the case `choose_legacy_map_detailed:2072-2079`
    documents and **VOL-156 already ruled on** (abstaining over a vowel length cost
    4,433 Devanagari characters).
    """

    for row in (ASCII_DIGIT_ROW, SHIFTED_DIGIT_ROW):
        for char in row:
            assert _decode("PCS NEPALI", char) == _decode("FONTASY_HIMALI_TT", char)
    assert _decode("PCS NEPALI", "2075.076") == _decode("FONTASY_HIMALI_TT", "2075.076")

    # Not vacuous: the two maps ARE different, on five non-digit characters.
    differing = [
        char
        for char in map(chr, range(32, 127))
        if _decode("PCS NEPALI", char) != _decode("FONTASY_HIMALI_TT", char)
    ]
    assert differing == ["<", "?", "C", "X", "~"], differing
    # And `?` is the exact vowel-length disagreement the 2 of 13 turn on.
    assert _decode("PCS NEPALI", "?") == "रू"
    assert _decode("FONTASY_HIMALI_TT", "?") == "रु"


def test_Preeti_and_FONTASY_are_the_EXCHANGED_ROW_pair() -> None:
    """The ruling's exchanged-row axis, asserted separately from the pair above.

    `legacy_maps.py:22-24` records the exchange. Both readings are well-formed
    Devanagari, which is why the swap is invisible to a purity ratio: one produces
    digits where the other produces consonants, and vice versa.
    """

    assert _decode("FONTASY_HIMALI_TT", ASCII_DIGIT_ROW) == "०१२३४५६७८९"
    assert _decode("Preeti", ASCII_DIGIT_ROW) == "ण्ज्ञद्दघद्धछटठडढ"
    # And the exchange runs both ways: the shifted row swaps back.
    assert _decode("Preeti", SHIFTED_DIGIT_ROW) == "०१२३४५६७८९"
    assert _decode("FONTASY_HIMALI_TT", SHIFTED_DIGIT_ROW) == "ण्ज्ञद्दघद्धछटठडढ"


def test_a_purity_ratio_cannot_screen_the_exchanged_row_class() -> None:
    """⚠️ The vacuous-green warning, as an executable assertion.

    A prose-damage screen built on a Devanagari share or digit count returns the SAME
    verdict for both candidate maps here, so it certifies nothing. Any screen over
    this class has to be prose identity -- attested-word or read -- not a ratio.
    """

    aggregate = "2075.076"
    left = _nepali_validity(_decode("PCS NEPALI", aggregate))
    right = _nepali_validity(_decode("FONTASY_HIMALI_TT", aggregate))
    assert left["ratio"] == right["ratio"]
    assert left["devanagari"] == right["devanagari"]
    assert left["penalty_per_deva"] == right["penalty_per_deva"]
