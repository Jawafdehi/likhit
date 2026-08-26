"""Tests for the token-initial (orphan) repha guard.

``{`` is the repha keystroke in every legacy Nepali map npttf2utf ships, and it is
handled only by post-rules: three that move it LEFT past the consonant it was
struck after, and then one unconditional ``['{', 'र्']`` that converts it.

The relocating rules are not a guard on the converting rule -- they **feed** it.
Legacy Nepali is typed in visual order, so the repha hook is struck after its
consonant, and walking it back to the front of the word is exactly how the
converter lands it correctly in logical order. ``k{`` -> ``प{`` -> ``{प`` ->
``र्प``, and that ``{`` is at position 0 when the converting rule fires.

So "rule 12 fires at position 0" is the *normal* case, and the defect is the
narrower one where the ``{`` was **already** at position 0 before any relocation
ran -- nothing to its left, nothing to close. The converter fabricates a repha
the source never encoded.

The two failure directions are both covered here, and the second is the one that
matters, because the obvious fix gets it wrong:

* :func:`test_the_guard_repairs_an_orphan_repha` -- the orphan must be repaired.
* :func:`test_the_guard_leaves_a_relocated_repha_alone` -- a *correct* repha must
  be byte-identical with the guard on. A position condition on the converting
  rule passes the first test and fails this one, silently deleting repha across
  the whole corpus. Measured on the OAG corpus: on eight documents the ratio was
  9 orphan against 2,356 relocated, so getting this direction wrong costs ~262x
  what it repairs.
"""

from __future__ import annotations

import os

import pytest

from likhit.errors import ExtractionError
from likhit.extractors import legacy_maps


@pytest.fixture(autouse=True)
def _reset_mapper_singleton():
    """The mapper is a module-level singleton and the guard mutates its rules.

    Without this, whichever test ran first would decide every later test's
    behaviour, and enabling the guard twice would insert the rule twice.
    """

    legacy_maps._mapper = None
    yield
    legacy_maps._mapper = None


@pytest.fixture
def guard_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LIKHIT_ORPHAN_REPHA_GUARD", "1")


@pytest.fixture
def guard_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LIKHIT_ORPHAN_REPHA_GUARD", raising=False)


# Spins is likhit's sixth map and rotates '+' onto '{', so '+' is its repha key.
SPINS = legacy_maps.SPINS_MAP_KEY


def test_the_guard_is_off_by_default(guard_off: None) -> None:
    """It changes decoded output, so it must not arrive without a generation slot."""

    assert legacy_maps.orphan_repha_guard_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_the_guard_reads_its_env_flag(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LIKHIT_ORPHAN_REPHA_GUARD", value)
    assert legacy_maps.orphan_repha_guard_enabled() is True


@pytest.mark.parametrize(
    ("source", "unguarded", "guarded"),
    [
        # '+kflnsf' is 'पालिका' (municipality). The leading '+' has no consonant
        # to its left, so it encodes no repha -- but the converter makes one.
        ("+kflnsf", "र्पालिका", "पालिका"),
        ("+vGg]", "र्खन्ने", "खन्ने"),
        # A lone repha keystroke: nothing at all should come out of it.
        ("+", "र्", ""),
    ],
)
def test_the_guard_repairs_an_orphan_repha(
    guard_on: None, source: str, unguarded: str, guarded: str
) -> None:
    convert = legacy_maps.get_converter_for_map(SPINS)
    assert convert(source) == guarded

    legacy_maps._mapper = None
    os.environ.pop("LIKHIT_ORPHAN_REPHA_GUARD")
    assert legacy_maps.get_converter_for_map(SPINS)(source) == unguarded


@pytest.mark.parametrize(
    ("map_key", "source", "expected"),
    [
        # 'k{' is the canonical correctly-typed repha: consonant, then the hook.
        # The relocating rule moves '{' to position 0 and the converter turns it
        # into 'र्प'. A position condition on the converting rule DELETES this.
        ("Preeti", "k{", "र्प"),
        ("Preeti", "kk{", "पर्प"),
        # Under Spins the same shape is spelled with '+', mid-word.
        (SPINS, ";+:yf", "र्सस्था"),
        (SPINS, "Pj+", "एर्व"),
        (SPINS, ";+3,", "र्सघ,"),
        # The founding token of VOL-219/VOL-378. Its '+' is at index 2, so it is
        # relocated, not orphaned -- the guard must not claim it. Its residual
        # ill-formedness is a source-keystroke error and no guard can undo it.
        (SPINS, "jf+sL", "र्वाकी"),
        # ...and the correct spelling of that same word, which never involves the
        # repha keystroke at all.
        (SPINS, "afFsL", "बाँकी"),
    ],
)
def test_the_guard_leaves_a_relocated_repha_alone(
    guard_on: None, map_key: str, source: str, expected: str
) -> None:
    assert legacy_maps.get_converter_for_map(map_key)(source) == expected


def test_the_guard_is_scoped_to_the_word_not_the_run(guard_on: None) -> None:
    """npttf2utf applies post-rules per whitespace-split word, so '^' is per word.

    Both words here start with the repha keystroke, so both are orphans; if the
    anchor were run-scoped only the first would be repaired.
    """

    convert = legacy_maps.get_converter_for_map(SPINS)
    assert convert("+kflnsf +vGg]") == "पालिका खन्ने"


def test_the_guard_runs_before_an_m_relocation_rule(guard_on: None) -> None:
    """An orphan followed by ``m`` escapes if the guard starts at rule eight."""

    assert legacy_maps.get_converter_for_map("Preeti")("{m") == "m"


def test_the_guard_does_not_touch_sagarmathas_direct_repha(guard_on: None) -> None:
    """Sagarmatha emits repha from its character-map, before any post-rule.

    A guard that stripped a leading 'र्' from decoded text would eat these too.
    Scoping the guard to the converting post-rule provably cannot, and this is
    what holds that distinction in place.
    """

    convert = legacy_maps.get_converter_for_map("Sagarmatha")
    assert convert("Š") == "र्"
    assert convert("¥") == "र्‍"


def test_installing_the_guard_covers_every_shipped_map(guard_on: None) -> None:
    mapper = legacy_maps._get_mapper()
    for map_name, block in mapper.all_rules.items():
        post_rules = block["rules"]["post-rules"]
        assert ["^\\{", ""] in [list(r) for r in post_rules], (
            f"{map_name} was not guarded"
        )
        guard_at = [list(r) for r in post_rules].index(["^\\{", ""])
        converting = [i for i, r in enumerate(post_rules) if r[0] == "{"]
        relocating = [
            i
            for i, r in enumerate(post_rules)
            if "{" in r[0] and r[0] != "{" and list(r) != ["^\\{", ""]
        ]
        assert guard_at == 0, f"{map_name}: guard must precede every post-rule"
        assert guard_at < min(relocating), f"{map_name}: guard is not before relocation"
        assert guard_at < min(converting), f"{map_name}: guard is not before conversion"


def test_the_guard_refuses_a_map_whose_rules_are_out_of_order() -> None:
    """If upstream reorders the post-rules, position the guard on nothing.

    Refusing loudly is the only safe answer: a guard inserted after the relocating
    rules would silently delete correct repha, which is the failure this whole
    change exists to avoid.
    """

    class _FakeMapper:
        def __init__(self) -> None:
            self.all_rules = {
                "Broken": {
                    "rules": {
                        # Converting rule BEFORE the relocating rule.
                        "post-rules": [["{", "र्"], ["(.[ािी]*?){", "{\\1"]],
                    }
                }
            }

    with pytest.raises(
        ExtractionError, match="orphan-repha guard cannot be positioned"
    ):
        legacy_maps._install_orphan_repha_guard(_FakeMapper())


def test_a_map_with_no_repha_rule_is_skipped_not_refused() -> None:
    class _FakeMapper:
        def __init__(self) -> None:
            self.all_rules = {"NoRepha": {"rules": {"post-rules": [["x", "y"]]}}}

    assert legacy_maps._install_orphan_repha_guard(_FakeMapper()) == 0
