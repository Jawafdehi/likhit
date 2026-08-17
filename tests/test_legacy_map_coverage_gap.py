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
    SYNTHESISED_MAP_KEYS,
    _get_compiled_map,
    _RA_KEYSTROKE_MAPS,
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
