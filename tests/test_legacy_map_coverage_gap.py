"""Source 0x3c is a KEY whose letter depends on the FACE, and both readings are real.

An earlier form of this change treated 0x3c as a coverage *gap* — a slot whose decode to
a bare `?` was always damage — and repaired it map-wide by substituting `र` into
the output of five maps. That is right for one population and destroys the other, which
is larger.

🛑 **On the faces that dominate both downstream corpora, 0x3c draws a real question
mark**, so npttf2utf's table is correct and `?` is the faithful read. The embedded
`BOFDOE+Preeti` was extracted and its slots rendered at 200 dpi: 0x2f draws
`र`, 0x3f draws `रु`, and 0x3c is a genuine two-contour question
mark. Page-verified `?` on `Preeti`, `Preeti,Bold` and `Kantipur` across three corpora —
914 occurrences under a formerly-repaired map key, 541 isolated, 101 doc-font pairs, over
all 6,236 OAG documents and all 35 CIAA reports. The decisive case is OAG `11115` p296,
where the sentence holding the mark reads `भन्ने प्रश्नमा` ("in
the question"), so the `?` is not an inference from glyph shape at all.

🛑 **And the converse population is equally real**, which is why this is a per-face table
rather than a revert: 0x3c is page-verified `र` on `FONTASY_ HIMALI_ TT`
(OAG `2335` p21) and on `Spins_EXT` (OAG `3861` p5). 24 of the 101 doc-font pairs carry a
word-internal 0x3c, 412 occurrences.

⚠️ **Position is not a proxy for the letter.** Word-internal 0x3c that the page renders as
`?` exists (OAG `2933`, `3699`); isolated 0x3c that the page renders as
`र` exists. Only a per-face read decides, so the isolated/in-word split is not
available as a discriminator and these tests deliberately do not use one.

The repair that remains is expressed as a **pre-decode key translation**, the shape
`Siddhi` already uses (`_SIDDHI_TO_HIMALI_KEYS`'s `'<' -> '/'`). The reordering argument
that once motivated an output substitution does not apply to it: that argument was about
SPLITTING a span at 0x3c, which strands a prefix matra, and translating one key splits
nothing — `test_translating_the_key_does_not_strand_a_prefix_matra` pins it.
"""

from __future__ import annotations

import pytest

from likhit.extractors.legacy_maps import (
    DECODABLE_MAP_KEYS,
    SHIPPED_MAP_KEYS,
    SIDDHI_MAP_KEY,
    SPINS_MAP_KEY,
    SYNTHESISED_MAP_KEYS,
    _get_compiled_map,
    _RA_KEYSTROKE_MAPS,
    _SIDDHI_BASE_MAP_KEY,
    _SIDDHI_TO_HIMALI_KEYS,
    _SPINS_BASE_MAP_KEY,
    _SPINS_TO_PREETI_KEYS,
    get_converter_for_map,
)

#: Source code point 0x3c, the slot whose letter is face-dependent.
FACE_DEPENDENT_KEYSTROKE = "<"

#: Source 0x3f. Decodes to रु/रू under every map — so it is NOT the
#: source of any output `?`, which is what made the one-to-one premise true.
NOT_THE_SLOT = "?"

#: The key 0x3c is translated to on the faces where it draws र.
RA_KEYSTROKE = "/"

#: Faces where a rendered page shows 0x3c drawing र. Map keys, since that is
#: what the converter is selected by.
RA_MAP_KEYS = ("FONTASY_HIMALI_TT", "Spins", "Siddhi")

#: Faces where a rendered page shows 0x3c drawing a question mark.
QUESTION_MARK_MAP_KEYS = ("Preeti", "Kantipur")


@pytest.mark.parametrize("map_key", RA_MAP_KEYS)
def test_a_ra_face_decodes_the_slot_as_ra(map_key: str) -> None:
    assert get_converter_for_map(map_key)(FACE_DEPENDENT_KEYSTROKE) == "र"


@pytest.mark.parametrize("map_key", QUESTION_MARK_MAP_KEYS)
def test_a_question_mark_face_decodes_the_slot_faithfully(map_key: str) -> None:
    """🛑 The regression this file exists to prevent, and it is the LARGER population.

    A `?` here is not damage to be repaired; it is what the page draws. Repairing it
    map-wide converted 914 occurrences of interrogative punctuation into
    `र`.
    """

    assert get_converter_for_map(map_key)(FACE_DEPENDENT_KEYSTROKE) == "?"


def test_the_corpus_interrogatives_survive() -> None:
    """Real source lines, from the two corpora, that the map-wide repair destroyed.

    The two CIAA lines are questionnaire boilerplate present in 10 of the 35 reports
    (the 15th through the 24th); 11 reports carry `Preeti` 0x3c at all. The OAG line is
    the `kanunpatrika` sample, page-verified at 380 dpi.
    """

    preeti = get_converter_for_map("Preeti")
    assert preeti("s] s;f] ePsf] xf] <") == "के कसो भएको हो ?"
    assert preeti("ljifo j:t' s] xf] <") == "विषय वस्तु के हो ?"
    assert preeti("k5{, kb}{g <") == "पर्छ, पर्दैन ?"


def test_the_page_verified_ra_cases_still_decode() -> None:
    """The other side of the trade, so narrowing the repair cannot silently widen.

    `Spins` carries its own translation entry rather than inheriting one from Preeti,
    because Preeti's 0x3c is a question mark — see
    `test_spins_does_not_inherit_the_translation_from_preeti`.
    """

    assert get_converter_for_map("Spins")("gu< k|x<L xjnbf<") == ("नगर प्रहरी हवलदार")
    # #74's Siddhi translation already did this, and is untouched by the rework.
    assert get_converter_for_map("Siddhi")("<sd") == "रकम"
    assert get_converter_for_map("Siddhi")("2. <fhZj") == "२. राजश्व"


def test_spins_does_not_inherit_the_translation_from_preeti() -> None:
    """Spins decodes through Preeti's TABLE but must not take Preeti's 0x3c reading.

    Before the rework Spins got `र` for free, because Preeti repaired its
    output and Spins runs Preeti's converter. Once Preeti reads 0x3c faithfully that
    inheritance would have silently flipped Spins to `?`, losing the page-verified
    `Spins_EXT` evidence. Pinned as a pair so the mechanism is visible.
    """

    assert get_converter_for_map("Preeti")(FACE_DEPENDENT_KEYSTROKE) == "?"
    assert get_converter_for_map("Spins")(FACE_DEPENDENT_KEYSTROKE) == "र"


@pytest.mark.parametrize("map_key", sorted(SYNTHESISED_MAP_KEYS))
def test_a_synthesised_map_is_not_in_the_translation_set(map_key: str) -> None:
    """Both express 0x3c in their OWN key translation, not through this set.

    `_RA_KEYSTROKE_MAPS` selects a converter by map key, and a synthesised map's
    converter is built from its base's. Listing one here would translate twice.
    """

    assert map_key not in _RA_KEYSTROKE_MAPS
    assert get_converter_for_map(map_key)(FACE_DEPENDENT_KEYSTROKE) == "र"


def test_sagarmatha_is_deliberately_not_translated() -> None:
    """Pinned as a decision, on the same principle that keeps PCS NEPALI out.

    There is no page read for Sagarmatha's 0x3c either way, and it has zero occurrences
    in both corpora, so nothing licenses a reading. Its table says `?` and that is what
    it decodes to. Repairing on a sibling inference is precisely the reasoning that made
    the first report of this defect name the wrong outlier.
    """

    assert "Sagarmatha" in SHIPPED_MAP_KEYS
    assert "Sagarmatha" not in _RA_KEYSTROKE_MAPS
    assert get_converter_for_map("Sagarmatha")(FACE_DEPENDENT_KEYSTROKE) == "?"


def test_pcs_nepali_is_untouched_and_its_slot_is_elsewhere() -> None:
    """PCS NEPALI's `?` slot is 0xa9, not 0x3c, so none of this reaches it."""

    assert "PCS NEPALI" not in _RA_KEYSTROKE_MAPS
    assert get_converter_for_map("PCS NEPALI")(FACE_DEPENDENT_KEYSTROKE) == "्र"
    assert _get_compiled_map("PCS NEPALI").convert("©") == "?"


def test_translating_the_key_does_not_strand_a_prefix_matra() -> None:
    """The reordering argument, retired by measurement rather than by assertion.

    An output substitution was originally chosen because SPLITTING a span at 0x3c and
    converting the pieces strands a pending prefix matra: `ul<` becomes
    `गिर` instead of `गरि`. Translating a single key
    splits nothing, so npttf2utf reorders from the correct consonant and the cluster is
    right. Asserted on a `र` face, since that is where the translation runs.
    """

    himali = get_converter_for_map("FONTASY_HIMALI_TT")
    assert himali("ul" + FACE_DEPENDENT_KEYSTROKE) == "गरि"
    # And the split design, for contrast: the two halves converted separately.
    split = himali("ul") + himali(RA_KEYSTROKE)
    assert split != "गरि"


@pytest.mark.parametrize("map_key", sorted(_RA_KEYSTROKE_MAPS))
def test_the_translation_agrees_with_the_output_form_where_it_is_kept(
    map_key: str,
) -> None:
    """Changing the SHAPE of the repair must not change its RESULT on the kept faces.

    The rework narrows which faces are repaired and moves the repair before the decode.
    On a face that keeps it, the new form has to produce exactly what the old output
    substitution produced, or the narrowing is smuggling a behaviour change.
    """

    raw = _get_compiled_map(map_key).convert
    for source in ("rfFu'gf<fo)f", "ul<", "<sd", ";<sf<"):
        assert get_converter_for_map(map_key)(source) == raw(source).replace("?", "र")


@pytest.mark.parametrize("map_key", sorted(DECODABLE_MAP_KEYS))
def test_source_0x3f_is_still_ra_plus_a_vowel_sign(map_key: str) -> None:
    """0x3f is not the source of any output `?`, on any map.

    This was the premise that made an output-wide substitution defensible, and it is
    still true — it is just no longer sufficient, because 0x3c legitimately produces a
    `?` on some faces. Kept because a map whose 0x3f started emitting `?` would make the
    per-face reading below unreadable.
    """

    decoded = get_converter_for_map(map_key)(NOT_THE_SLOT)
    assert "?" not in decoded
    assert decoded.startswith("र")


def test_every_decodable_map_is_classified_one_way_or_the_other() -> None:
    """No map may be left with an unexamined 0x3c reading.

    The census that keeps this file honest as maps are added: each decodable map either
    draws `र` there (and is in the translation set, directly or through its
    base) or draws something else that its own table already gets right. A new map
    lands in neither list and fails here until someone reads a page.
    """

    accounted = (
        set(RA_MAP_KEYS)
        | set(QUESTION_MARK_MAP_KEYS)
        | {
            "Sagarmatha",
            "PCS NEPALI",
        }
    )
    assert set(DECODABLE_MAP_KEYS) == accounted, set(DECODABLE_MAP_KEYS) ^ accounted


# ------------------------------------------ the family invariant, with its exceptions
#: Every source code point whose RAW table decode is a bare `?`, per map, measured over
#: 0x00-0x2FFF rather than the byte range alone.
#:
#: Exactly one per map. Note PCS NEPALI is the odd one: its slot is at 0xa9, not 0x3c.
#: MEASURED here, not predicted -- an earlier account of this defect guessed which map was
#: the outlier and guessed wrong.
#:
#: ⚠️ These are facts about npttf2utf's TABLES, and a table saying `?` is not by itself
#: evidence of damage. On four of these five faces a rendered page shows that the `?` is
#: what the glyph draws, so the table is right. Which is why this registry is paired with
#: :data:`FAITHFUL_QUESTION_MARKS` below rather than with a list of things to repair.
RAW_TABLE_QUESTION_MARK_SLOTS: dict[str, int] = {
    "Preeti": 0x3C,
    "Kantipur": 0x3C,
    "FONTASY_HIMALI_TT": 0x3C,
    "Sagarmatha": 0x3C,
    "PCS NEPALI": 0xA9,
}

#: Slots where the PUBLIC converter still emits `?`, and should. Not a limitation list:
#: on Preeti and Kantipur the page draws a question mark (verified, three corpora), and
#: Sagarmatha and PCS NEPALI have no page read either way so their tables stand.
#:
#: 🛑 An earlier revision of this file had ONE entry here and treated every other `?` as
#: damage. That is the defect the per-face rework fixed: three of these four were being
#: rewritten to र, destroying 914 occurrences of interrogative punctuation.
FAITHFUL_QUESTION_MARKS: dict[str, int] = {
    "Preeti": 0x3C,
    "Kantipur": 0x3C,
    "Sagarmatha": 0x3C,
    "PCS NEPALI": 0xA9,
}


@pytest.mark.parametrize("map_key", sorted(RAW_TABLE_QUESTION_MARK_SLOTS))
def test_the_raw_table_slots_are_exactly_the_documented_ones(map_key: str) -> None:
    """One `?` slot per raw table, at the recorded code point, and nothing else.

    Swept over 0x00-0x2FFF. "Exactly one" is what makes a per-face key translation a
    complete description of that face's disagreement with its table: if a map ever had
    two, translating one key would leave the other unaccounted for.
    """

    convert = _get_compiled_map(map_key).convert
    slots = [cp for cp in range(0x00, 0x3000) if convert(chr(cp)) == "?"]

    assert slots == [RAW_TABLE_QUESTION_MARK_SLOTS[map_key]], [hex(s) for s in slots]


def test_the_registries_partition_the_shipped_family_exactly() -> None:
    """A map added later must be read off a page, not silently defaulted either way."""

    assert set(RAW_TABLE_QUESTION_MARK_SLOTS) == set(SHIPPED_MAP_KEYS), (
        "a shipped map was added or removed -- sweep its raw table for a `?` slot and "
        "record it in RAW_TABLE_QUESTION_MARK_SLOTS"
    )
    # Translated-to-ra and faithful-question-mark must partition the shipped family.
    assert set(_RA_KEYSTROKE_MAPS) | set(FAITHFUL_QUESTION_MARKS) == set(
        SHIPPED_MAP_KEYS
    )
    assert set(_RA_KEYSTROKE_MAPS) & set(FAITHFUL_QUESTION_MARKS) == set()


@pytest.mark.parametrize("map_key", sorted(FAITHFUL_QUESTION_MARKS))
def test_every_faithful_question_mark_is_still_emitted(map_key: str) -> None:
    """The direction that broke: these must NOT be repaired away.

    A registry naming a reading that no longer happens is worse than no registry -- it
    reads as a decision after the decision has been reversed. This is also the test that
    fails first if someone reintroduces a map-wide substitution.
    """

    slot = chr(FAITHFUL_QUESTION_MARKS[map_key])

    assert get_converter_for_map(map_key)(slot) == "?", (
        f"{map_key} 0x{FAITHFUL_QUESTION_MARKS[map_key]:02x} no longer decodes as `?` -- "
        f"if that is intended, a rendered page must say so and this entry must move"
    )


@pytest.mark.parametrize("map_key", sorted(DECODABLE_MAP_KEYS))
def test_no_map_emits_an_undocumented_question_mark(map_key: str) -> None:
    """The family invariant: every `?` a map emits is a recorded, page-backed reading.

    Stated over every DECODABLE map, so the synthesised two are covered as well. Both of
    them translate 0x3c to the ra key and neither inherits a base map's `?`, so both are
    expected to emit none at all.
    """

    convert = get_converter_for_map(map_key)
    remaining = [cp for cp in range(0x00, 0x3000) if convert(chr(cp)) == "?"]
    expected = (
        [FAITHFUL_QUESTION_MARKS[map_key]] if map_key in FAITHFUL_QUESTION_MARKS else []
    )

    assert remaining == expected, (
        f"{map_key} emits `?` for {[hex(c) for c in remaining]}; documented: "
        f"{[hex(c) for c in expected]}"
    )


@pytest.mark.parametrize("map_key", sorted(DECODABLE_MAP_KEYS))
def test_no_map_emits_a_question_mark_among_other_characters(map_key: str) -> None:
    """A `?` inside a longer decode is invisible to the single-character sweeps.

    Those ask "which code point decodes to exactly `?`". A rule could instead emit one as
    part of a cluster -- undocumented either way, and not covered by that question.

    ⚠️ Parametrized, not a loop with one assertion at the end. As a loop the FIRST
    offending map aborts the sweep and the rest are never examined. Measured, with two
    independent source mutations applied together -- `_RA_KEYSTROKE_MAPS` emptied
    (offends `FONTASY_HIMALI_TT`, 4th of the seven) and a `'~' -> '<'` entry added to
    `_SPINS_TO_PREETI_KEYS` (offends `Spins`, 6th): parametrized reports **both**, the
    loop reports **only FONTASY_HIMALI_TT** and masks Spins entirely.
    """

    convert = get_converter_for_map(map_key)
    offenders = [
        hex(cp)
        for cp in range(0x00, 0x3000)
        if "?" in convert(chr(cp)) and FAITHFUL_QUESTION_MARKS.get(map_key) != cp
    ]

    assert offenders == [], f"{map_key}: {offenders}"


#: Each synthesised map's (base map, key translation), for the RAW-side sweep below.
#: Read off `legacy_maps` rather than restated, so a base or table swap reaches the test.
_SYNTHESISED_ARMS: dict[str, tuple[str, dict[int, str]]] = {
    SPINS_MAP_KEY: (_SPINS_BASE_MAP_KEY, _SPINS_TO_PREETI_KEYS),
    SIDDHI_MAP_KEY: (_SIDDHI_BASE_MAP_KEY, _SIDDHI_TO_HIMALI_KEYS),
}


@pytest.mark.parametrize("map_key", sorted(_SYNTHESISED_ARMS))
def test_a_synthesised_map_cannot_reach_its_base_tables_question_mark(
    map_key: str,
) -> None:
    """🛑 States the invariant on the RAW side, where `Siddhi`'s half of it is REDUNDANT
    today and therefore invisible to every other test in the repo.

    The sweeps above run the PUBLIC converter, which for a synthesised map composes the
    key translation with the base map's own public converter. For `Spins` that base is
    `Preeti`, whose converter really does emit `?` at 0x3c, so the public sweeps already
    cover Spins with teeth -- dropping its `'<' -> '/'` entry fails 7 tests here.

    `Siddhi` is the opposite case and the reason this test exists. Its base is
    `FONTASY_HIMALI_TT`, which is itself in `_RA_KEYSTROKE_MAPS` and has already
    translated 0x3c away, so Siddhi's `?`-freedom rests on TWO independent
    mechanisms and the public path is satisfied by either alone. Measured on this head:

        drop Siddhi's own `'<' -> '/'`        1 failed at SUITE scope -- this test
        empty `_RA_KEYSTROKE_MAPS`            Siddhi's arms stay GREEN
        do both                              Siddhi's public arms fail, and 21 of
                                             tests/test_siddhi_layout.py fail

    So each single edit is invisible in Siddhi's public behaviour and the pair is
    catastrophic. This is the assertion that makes the first one fail on its own.

    It works by translating the keystrokes and then decoding with the base map's RAW
    npttf2utf table, skipping the base's public repair. Both bases have exactly one `?`
    slot (0x3c, asserted above), so it fires two ways: drop or reverse the
    `'<' -> '/'` entry and 0x3c reaches the raw table unchanged; add an entry whose
    VALUE is `'<'` and some other key starts decoding as `?`.

    (Single characters only, so `Siddhi`'s literal `-` separator split does not arise --
    it is covered by `tests/test_siddhi_layout.py`.)
    """

    base_map_key, translation = _SYNTHESISED_ARMS[map_key]
    raw_base = _get_compiled_map(base_map_key).convert
    assert RAW_TABLE_QUESTION_MARK_SLOTS[base_map_key] == ord(FACE_DEPENDENT_KEYSTROKE)

    reachable = [
        hex(cp)
        for cp in range(0x00, 0x3000)
        if raw_base(chr(cp).translate(translation)) == "?"
    ]

    assert reachable == [], (
        f"{map_key} routes {reachable} onto {base_map_key}'s raw `?` slot; a "
        f"synthesised map has no page evidence for a question mark of its own"
    )


def test_the_raw_arm_actually_rotates_the_spins_keys() -> None:
    """Pins the rotation's DIRECTION, not merely its presence.

    ⚠️ **This test's original justification was wrong and is corrected here.** It said
    dropping the translation "fails 1 test, because every other Spins assertion is about
    0x3c rather than the rotation". Re-measured at suite scope on this head: dropping it
    fails **8** -- this one plus **seven that already exist on the parent branch**
    (`test_spins_reads_the_anusvara_where_preeti_reads_a_paren`,
    `test_spins_decodes_the_real_corpus_span`,
    `test_spins_and_preeti_differ_on_exactly_the_rotated_keys`,
    `test_the_synthesised_maps_are_outside_these_sweeps`, and three
    `test_compiled_map_matches_upstream_on_real_spans` arms). The original "1" was
    FILE-scoped while the bite table's other rows were suite-scoped, so the two were not
    comparable. Against the mutation it names, this test's incremental power is zero.

    What it does add, and what nothing else covered, is the DIRECTION. A rotation that is
    present but permuted (`-` -> `[` instead of `-` -> `=`) leaves `differing` exactly
    `['+', '-', '<', '=', '[', '_', '{']` and every membership assertion below true.
    So the six rotated keys are pinned as LITERAL decodes -- the page evidence already
    recorded in `_SPINS_TO_PREETI_KEYS`' comment: repha, anusvara, the vocalic ृ,
    a decimal point and balanced parens.

    🛑 Do NOT rewrite these as `spins(src) == preeti(_SPINS_TO_PREETI_KEYS[src])`. That
    form derives its expectation from the very table under test, so it passes under a
    reversed table too -- measured.

    ⚠️ SEVEN keys differ, not the six the rotation contributes: the seventh is 0x3c, which
    the rework added to the same translation table because Spins_EXT draws
    र there while Preeti draws a question mark. The count and the table are
    asserted together so the two reasons a key can appear here stay distinguishable.
    """

    spins = get_converter_for_map("Spins")
    preeti = get_converter_for_map("Preeti")
    differing = [
        chr(cp) for cp in range(0x20, 0x7F) if spins(chr(cp)) != preeti(chr(cp))
    ]

    assert differing, "Spins is indistinguishable from Preeti -- the rotation is gone"
    assert len(differing) == 7
    assert "<" in differing, "0x3c is the seventh, and it is not part of the rotation"
    # and the disagreement is exactly on keys the translation table moves
    for char in differing:
        assert ord(char) in _SPINS_TO_PREETI_KEYS, char

    # The direction, as literals. Six rotated keys, plus 0x3c which is not a rotation.
    assert spins("-") == "."
    assert spins("=") == "ृ"
    assert spins("[") == "("
    assert spins("+") == "र्"
    assert spins("_") == "ं"
    assert spins("{") == ")"
    assert spins(FACE_DEPENDENT_KEYSTROKE) == "र"
