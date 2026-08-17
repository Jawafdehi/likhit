"""What Preeti and FONTASY_HIMALI_TT actually disagree about, exhaustively.

These two maps are near-clones, and *which* of the pair decodes a span decides whether
a word comes out as a word. Nothing in this repo recorded where they differ, so two
existing write-ups disagreed about it -- one said the maps "agree on every letter
slot", another said 40 of the differences are in letter slots. Neither was checkable
against anything.

Both are reconciled below, and the reason they looked contradictory is that **each was
quoting a different denominator without saying so**. There are three separate
instruments here, they give three different correct answers, and they must never go in
one table:

    A  the character-map DATA, over keys the two maps SHARE
    B  the same data, over the UNION of both key sets
    C  the CONVERTER, over printable-ASCII singletons

Instrument A says 30 differ; B says 60; C says 25. All three are right. A figure from
this family is meaningless without its instrument named, which is why every test below
names one.

🛑 The maps are pinned by the sha256 of the vendored ``map.json``. These are measured
facts about a data file, not a design, so a dependency bump that changes the maps must
fail here and be re-measured rather than silently rewriting what the library decodes.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib

import pytest

from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    SHIPPED_MAP_KEYS,
    SPINS_MAP_KEY,
    _match_font,
    get_converter_for_map,
)

# The two rows of a US keyboard's number row. "Number row" is a property of the KEY you
# press, not of what comes out -- classifying by output would beg the question, since
# the whole point is that the two maps disagree about what these keys produce.
UNSHIFTED_DIGIT_ROW = "0123456789"
SHIFTED_DIGIT_ROW = ")!@#$%^&*("
DIGIT_ROW_KEYS = frozenset(UNSHIFTED_DIGIT_ROW) | frozenset(SHIFTED_DIGIT_ROW)

DEVANAGARI_BLOCK = range(0x0900, 0x0980)

# A few of the map keys below are written as \uXXXX escapes rather than as characters.
# U+00D2/D9/DA and U+00A8 decompose under NFD/NFKD, so spelling them literally would
# make THIS FILE change under normalization -- the same defect class as the two regex
# classes in src/, applied to a test that would then compare a one-character key
# against a two-character sequence. The keys with no decomposition are left readable.

# sha256 of npttf2utf's bundled map.json, which every figure in this file was measured
# against. If this fails, npttf2utf changed its data: re-run the derivation rather than
# adjusting numbers until they pass.
MAP_JSON_SHA256 = "66a0a91f1209eb1c73540e443144f306d6daf27c426c09d24ec307a1506212e5"


def _map_json_path() -> pathlib.Path:
    import npttf2utf

    return pathlib.Path(os.path.dirname(npttf2utf.__file__)) / "map.json"


def _character_map(map_key: str) -> dict[str, str]:
    data = json.loads(_map_json_path().read_bytes())
    return data[map_key]["rules"]["character-map"]


@pytest.fixture(scope="module")
def preeti_chars() -> dict[str, str]:
    return _character_map("Preeti")


@pytest.fixture(scope="module")
def himali_chars() -> dict[str, str]:
    return _character_map("FONTASY_HIMALI_TT")


def test_the_vendored_map_data_is_the_one_these_figures_were_measured_against():
    digest = hashlib.sha256(_map_json_path().read_bytes()).hexdigest()
    assert digest == MAP_JSON_SHA256, (
        "npttf2utf's map.json changed. Every count in this file is a measurement of "
        "that file, so re-derive them all rather than editing numbers until green."
    )


def test_map_json_carries_exactly_the_maps_all_map_keys_advertises():
    """A map present in the data but absent from the key lists is never tried by
    content-based detection, and one advertised but absent would raise at decode time.

    Stated against ``SHIPPED_MAP_KEYS``, not ``ALL_MAP_KEYS``: those two used to be the
    same tuple, and are not any more. ``ALL_MAP_KEYS`` now also carries the SYNTHESISED
    Spins layout, which npttf2utf does not ship and ``map.json`` therefore does not
    contain -- see ``test_spins_is_synthesised_and_therefore_outside_these_sweeps``.
    Both directions still matter, so the difference is asserted rather than tolerated.
    """

    data = json.loads(_map_json_path().read_bytes())
    assert sorted(data) == sorted(SHIPPED_MAP_KEYS)
    # ...and the only thing ALL_MAP_KEYS adds is the synthesised one.
    assert set(ALL_MAP_KEYS) - set(SHIPPED_MAP_KEYS) == {SPINS_MAP_KEY}


# --------------------------------------------------------------------------- #
# Instrument A -- the character-map data, over SHARED keys
# --------------------------------------------------------------------------- #


def test_instrument_a_shared_keys_and_how_many_differ(preeti_chars, himali_chars):
    shared = set(preeti_chars) & set(himali_chars)
    differing = {k for k in shared if preeti_chars[k] != himali_chars[k]}

    assert len(preeti_chars) == 136
    assert len(himali_chars) == 124
    assert len(shared) == 115
    assert len(differing) == 30

    on_digit_row = {k for k in differing if k in DIGIT_ROW_KEYS}
    outside = differing - on_digit_row
    assert len(on_digit_row) == 20
    assert len(outside) == 10


def test_instrument_a_the_twenty_digit_row_differences_are_the_whole_row(
    preeti_chars, himali_chars
):
    """Not "20 differences that happen to be on digit keys" -- it is *every* digit key.

    Both rows, complete, with no key agreeing. That is what makes the pair
    interchangeable-looking on prose and destructive on anything containing a digit.
    """

    for key in UNSHIFTED_DIGIT_ROW + SHIFTED_DIGIT_ROW:
        assert key in preeti_chars and key in himali_chars
        assert preeti_chars[key] != himali_chars[key], key

    assert len(set(UNSHIFTED_DIGIT_ROW) | set(SHIFTED_DIGIT_ROW)) == 20


def test_instrument_a_the_ten_differences_outside_the_digit_rows(
    preeti_chars, himali_chars
):
    """Named individually, because this is the set both existing write-ups got wrong.

    Four are ASCII and six are high-byte, which is why a probe over printable ASCII
    reports fewer than a probe over the data's 10.

    ⚠️ Instrument C's outside-the-digit-row count is **5**, not these 4: it also sees
    `<`, where the two TABLES agree (both `?`) but the two FACES do not, so only the
    converter has an opinion. The two instruments are not off by a rounding error; they
    are answering different questions, which is this file's subject.
    """

    shared = set(preeti_chars) & set(himali_chars)
    outside = sorted(
        k
        for k in shared
        if k not in DIGIT_ROW_KEYS and preeti_chars[k] != himali_chars[k]
    )

    assert outside == ["F", "X", "`", "~", "¤", "¥", "°", "\u00d2", "\u00d9", "\u00da"]
    assert [k for k in outside if k.isascii()] == ["F", "X", "`", "~"]
    assert len([k for k in outside if not k.isascii()]) == 6


def test_instrument_a_six_of_those_ten_are_silent_devanagari_substitutions(
    preeti_chars, himali_chars
):
    """The dangerous subset: both readings are pure Devanagari, so choosing the wrong
    map replaces one Devanagari letter with another and no damage signal fires.

    The other four have a non-Devanagari side -- a ZWJ, a diaeresis (U+00A8), a semicolon,
    a curly quote -- so a script check *can* see those. Splitting the ten is the point:
    they are not one class.
    """

    def pure_devanagari(text: str) -> bool:
        return all(ord(ch) in DEVANAGARI_BLOCK for ch in text)

    shared = set(preeti_chars) & set(himali_chars)
    outside = sorted(
        k
        for k in shared
        if k not in DIGIT_ROW_KEYS and preeti_chars[k] != himali_chars[k]
    )
    silent = [
        k
        for k in outside
        if pure_devanagari(preeti_chars[k]) and pure_devanagari(himali_chars[k])
    ]

    assert silent == ["F", "X", "`", "~", "¤", "°"]
    assert len(silent) == 6

    # And the four that are NOT silent, with what makes each visible.
    assert sorted(set(outside) - set(silent)) == ["¥", "\u00d2", "\u00d9", "\u00da"]
    assert "‍" in himali_chars["¥"]  # ZERO WIDTH JOINER
    assert preeti_chars["\u00d2"] == "\u00a8"  # DIAERESIS
    assert preeti_chars["\u00d9"] == ";"
    assert preeti_chars["\u00da"] == "’"  # RIGHT SINGLE QUOTATION MARK


def test_instrument_a_the_one_map_only_class(preeti_chars, himali_chars):
    """30 codes exist in exactly one of the two maps, and SPACE is one of them.

    A code in only one map is not a *disagreement* -- it is a coverage gap, and it
    behaves differently: the map without it leaves the byte alone. Counting these as
    differences is how instrument B reaches 60, so they are pinned separately.
    """

    preeti_only = set(preeti_chars) - set(himali_chars)
    himali_only = set(himali_chars) - set(preeti_chars)

    assert len(preeti_only) == 21
    assert len(himali_only) == 9
    assert len(preeti_only | himali_only) == 30
    assert " " in preeti_only


def test_instrument_a_the_rules_other_than_the_character_map_are_identical():
    """The two maps differ ONLY in their character map.

    Worth asserting, because a difference in ``post-rules`` would mean the two maps
    reorder text differently and none of the per-key analysis above would compose.
    """

    data = json.loads(_map_json_path().read_bytes())
    preeti = data["Preeti"]["rules"]
    himali = data["FONTASY_HIMALI_TT"]["rules"]

    assert preeti["post-rules"] == himali["post-rules"]
    assert len(preeti["post-rules"]) == 32
    assert not preeti["pre-rules"] and not himali["pre-rules"]


# --------------------------------------------------------------------------- #
# Instrument B -- the UNION denominator, which reconciles the two write-ups
# --------------------------------------------------------------------------- #


def test_instrument_b_reconciles_the_figures_the_records_disagreed_about(
    preeti_chars, himali_chars
):
    """ "60 of 145 differ, 20 in the number rows and 40 in letter slots" is CORRECT --
    under the union denominator, which that record never stated.

    Reconciled exactly:

        differing shared 30  +  one-map-only 30  =  60
        shared          115  +  one-map-only 30  = 145
        outside-row      10  +  one-map-only 30  =  40

    The competing claim -- that the maps "agree on every letter slot" -- is simply
    false, and this is the test that says so. Neither record was checkable before, so
    both survived; that is the actual lesson, and it is why every count here is
    asserted rather than described.
    """

    shared = set(preeti_chars) & set(himali_chars)
    differing = {k for k in shared if preeti_chars[k] != himali_chars[k]}
    one_map_only = (set(preeti_chars) - set(himali_chars)) | (
        set(himali_chars) - set(preeti_chars)
    )
    outside = differing - DIGIT_ROW_KEYS

    assert len(differing) + len(one_map_only) == 60
    assert len(shared) + len(one_map_only) == 145
    assert len(outside) + len(one_map_only) == 40
    assert len(differing & DIGIT_ROW_KEYS) == 20

    # The falsified claim, stated as an assertion so it cannot come back.
    assert len(outside) > 0, "the maps do NOT agree outside the digit rows"


# --------------------------------------------------------------------------- #
# Instrument C -- the CONVERTER, which is what actually decodes a document
# --------------------------------------------------------------------------- #


def test_instrument_c_converter_level_difference_over_printable_ascii():
    """The instrument closest to production, and it gives a third answer: 25 of 95.

    Lower than instrument A's 30 because six of the ten outside-row differences are on
    high-byte keys, which are not printable ASCII. A document made only of ASCII
    keystrokes therefore sees 25 disagreements, not 30 and not 60.

    🛑 This was **24** while the 0x3c repair was applied map-wide, and the move to 25 is
    a fact about the CONVERTER, not about npttf2utf's data. Both tables map `<` to a
    literal `?` -- they agree there, which is why instruments A and B do not move. What
    differs is the FACE: a rendered page shows Preeti drawing a question mark at 0x3c and
    Himali drawing `र`, so the converter translates the key for Himali only.
    Repairing both to `र` made them agree at the one printable-ASCII key
    where the two faces genuinely part company.

    That is the instrument discipline this whole file is about: A and B read the data, C
    reads the converter, and only C can see a difference the library itself introduces.
    """

    preeti = get_converter_for_map("Preeti")
    himali = get_converter_for_map("FONTASY_HIMALI_TT")

    singletons = [chr(code) for code in range(0x20, 0x7F)]
    differing = [ch for ch in singletons if preeti(ch) != himali(ch)]

    assert len(singletons) == 95
    assert len(differing) == 25
    assert len([ch for ch in differing if ch in DIGIT_ROW_KEYS]) == 20
    assert [ch for ch in differing if ch not in DIGIT_ROW_KEYS] == [
        "<",
        "F",
        "X",
        "`",
        "~",
    ]
    # The new entry, spelled out so it cannot be read as a count drifting on its own.
    assert (preeti("<"), himali("<")) == ("?", "र")


def test_the_three_instruments_do_not_agree_and_that_is_the_point():
    """Guards against someone "fixing" the apparent inconsistency by unifying them.

    30, 60 and 25 are three correct answers to three different questions. This asserts
    they are genuinely different numbers so a future reader cannot conclude that two of
    them are stale.

    ⚠️ `c` was 24 until the 0x3c repair stopped being applied map-wide. A and B read the
    map TABLES, which AGREE at `<` (both say `?`), so neither ever counted it and neither
    moves. Only `c` goes through the converter, which is where this library's per-face
    knowledge lives -- so A and B holding at 30 and 60 while `c` moves is the evidence
    that the rework changed the converter and not the data.
    """

    preeti_chars = _character_map("Preeti")
    himali_chars = _character_map("FONTASY_HIMALI_TT")
    shared = set(preeti_chars) & set(himali_chars)
    a = len({k for k in shared if preeti_chars[k] != himali_chars[k]})
    b = a + len(set(preeti_chars) ^ set(himali_chars))
    preeti = get_converter_for_map("Preeti")
    himali = get_converter_for_map("FONTASY_HIMALI_TT")
    c = len([chr(x) for x in range(0x20, 0x7F) if preeti(chr(x)) != himali(chr(x))])

    assert (a, b, c) == (30, 60, 25)
    assert len({a, b, c}) == 3


# --------------------------------------------------------------------------- #
# _match_font -- the routing primitive, and what was asserted by nothing
# --------------------------------------------------------------------------- #


def test_a_subset_prefix_spelling_a_registry_key_must_not_match():
    """🛑 The PDF subset-prefix split is load-bearing and nothing asserted it.

    ``_match_font`` drops everything before the first ``+`` because a PDF subset
    prefix is not part of the font name. Removing that line does NOT stop real legacy
    faces matching -- the registry lookup is a *substring* test, so ``'preeti' in
    'abcdee+preeti'`` is already true. The harm runs the other way: the PREFIX starts
    matching, and a Latin face gets routed through a legacy Devanagari map, which
    DESTROYS correct text rather than merely failing to decode it.

    That is not a contrived name. A conforming subset prefix is six uppercase letters,
    and ``PREETI`` and ``HIMALI`` are both exactly six uppercase letters -- so a prefix
    spelling a registry key is a legal prefix a subsetter can emit.
    """

    assert _match_font("PREETI+Helvetica") is None
    assert _match_font("HIMALI+Calibri") is None
    assert _match_font("PREETI+Arial") is None


def test_a_genuinely_prefixed_legacy_font_must_still_match():
    """The other half. A "fix" for one direction breaks the other silently, so both
    are pinned -- guarding only the case above would be satisfied by deleting the
    registry entirely.
    """

    assert _match_font("ABCDEE+Preeti") == "Preeti"
    assert _match_font("XYZABC+Kantipur") == "Kantipur"
    assert _match_font("QWERTY+Sagarmatha") == "Sagarmatha"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Preeti", "Preeti"),
        ("Preeti,Bold", "Preeti"),
        ("  Preeti  ", "Preeti"),
        ("PREETI", "Preeti"),
        ("preeti", "Preeti"),
        ("ABCDEE+Preeti,Bold", "Preeti"),
        ("Kalimati", None),
        ("Helvetica", None),
        ("", None),
    ],
)
def test_match_font_accepts_the_name_forms_a_pdf_actually_carries(name, expected):
    """The routing outcomes, pinned as a table.

    ⚠️ Read this as a behaviour pin, NOT as proof that each transformation inside
    ``_match_font`` is necessary -- see the mutation results in
    :func:`test_only_two_of_the_four_normalisation_steps_are_load_bearing`. Three of
    these rows pass whether or not the step they look like they test is present.
    """

    assert _match_font(name) == expected


def test_only_two_of_the_four_normalisation_steps_are_load_bearing():
    """Which of ``_match_font``'s four steps actually change an outcome. Measured by
    mutation, because reading the function suggests all four matter and they do not.

    ``_match_font`` does: split at ``+``, split at ``,``, ``.lower()``, ``.strip()``,
    then a SUBSTRING test against each registry key. That last detail is what makes
    two of the four redundant -- removing text from the *end* or the *edges* of a name
    cannot stop a key matching, because ``'preeti' in 'preeti,bold'`` and ``'preeti' in
    '  preeti  '`` are already true.

    Mutation results, each arm run against this whole file:

        drop the '+' split   2 FAILED   <- load-bearing
        drop .lower()        7 FAILED   <- load-bearing
        drop the ',' split   0 failed   <- redundant under a substring match
        drop .strip()        0 failed   <- redundant under a substring match

    The ``+`` split is the exception because it removes a PREFIX, and a prefix can
    spell a registry key: PDF subset prefixes are six uppercase letters, and both
    ``PREETI`` and ``HIMALI`` are six uppercase letters. A style suffix cannot do the
    same -- ``,Preeti`` is not a legal PDF style -- so the ``,`` split has no
    equivalent failure mode.

    This test asserts the *redundancy* directly, so nobody "hardens" the two redundant
    steps on the strength of the ``+`` finding, and nobody deletes the ``+`` split on
    the strength of the other two looking harmless. They are not the same situation.
    """

    # Redundant: the same answer with or without the trailing/edge trimming, because
    # the key is still a substring either way.
    assert _match_font("Preeti,Bold") == "Preeti"
    assert "preeti" in "Preeti,Bold".lower()
    assert _match_font("  Preeti  ") == "Preeti"
    assert "preeti" in "  Preeti  ".lower()

    # Load-bearing: here the trimming is what PREVENTS a match, and prevention cannot
    # be achieved by a substring test.
    assert _match_font("PREETI+Helvetica") is None
    assert (
        "preeti" in "PREETI+Helvetica".lower()
    )  # ... it WOULD match without the split

    # Load-bearing: without .lower() an uppercase name misses every key, since the
    # registry keys are all lowercase.
    assert all(key == key.lower() for key in ("preeti", "kantipur", "sagarmatha"))
    assert _match_font("PREETI") == "Preeti"
