"""Manual provenance checks against Lohit-Devanagari 2.5.3."""

import os
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from likhit.extractors import kalimati, lohit

_NAME_BUILD = 3
_NAME_VERSION = 5


@pytest.fixture(scope="module")
def reference_font() -> TTFont:
    raw = os.environ.get("LIKHIT_LOHIT_REFERENCE_TTF")
    assert raw, (
        "set LIKHIT_LOHIT_REFERENCE_TTF to the upstream Lohit-Devanagari "
        "2.5.3 TTF before running this module"
    )
    path = Path(raw)
    assert path.is_file(), f"LIKHIT_LOHIT_REFERENCE_TTF is not a file: {path}"
    return TTFont(path, lazy=False)


def test_table_re_derives_from_the_reference_font(reference_font: TTFont) -> None:
    """The shipped table is exactly the derivation plus recorded corrections."""

    glyph_order = reference_font.getGlyphOrder()
    best_cmap = kalimati._safe_get_best_cmap(reference_font)
    assert best_cmap, "the reference font must still have its own cmap"
    name_to_unicode = {name: codepoint for codepoint, name in best_cmap.items()}

    gid_to_correct = {
        gid: chr(name_to_unicode[name])
        for gid, name in enumerate(glyph_order)
        if name in name_to_unicode
    }
    gid_to_correct.update(
        kalimati._infer_mark_variants(
            reference_font,
            glyph_order,
            gid_to_correct,
        )
    )
    derived = kalimati._analyze_gsub(
        reference_font,
        glyph_order,
        gid_to_correct,
    )
    expected = dict(derived)
    expected.update(gid_to_correct)
    for cid, (was, now) in lohit.BELOW_FORM_RA_CORRECTIONS.items():
        assert expected[cid] == was, f"CID {cid} no longer derives as {was!r}"
        expected[cid] = now
    for cid, (source, value) in lohit.GSUB_VARIANT_ADDITIONS.items():
        assert cid not in expected, f"CID {cid} now derives on its own"
        assert expected[source] == value, f"CID {source} no longer derives as {value!r}"
        expected[cid] = value

    assert expected == lohit.GID_TO_UNICODE


def test_reference_font_matches_the_declared_identity_and_anchors(
    reference_font: TTFont,
) -> None:
    assert lohit._name_record(reference_font, _NAME_BUILD) == lohit.EXPECTED_BUILD
    assert lohit._name_record(reference_font, _NAME_VERSION) == lohit.EXPECTED_VERSION
    assert reference_font["head"].unitsPerEm == lohit.EXPECTED_UNITS_PER_EM
    assert reference_font["maxp"].numGlyphs == lohit.UPSTREAM_GLYPH_COUNT
    glyph_order = reference_font.getGlyphOrder()
    for gid, expected_digest in lohit._ANCHOR_OUTLINES.items():
        assert (
            lohit._outline_digest(reference_font, glyph_order[gid]) == expected_digest
        )
    assert lohit.is_known_lohit_subset(reference_font) is True


def test_variant_additions_rest_on_a_single_subst_rule_in_the_font(
    reference_font: TTFont,
) -> None:
    """Every recorded addition is reached by a positional GSUB feature."""

    positional_features = {"psts", "pres", "abvs", "blws", "half", "rphf", "vatu"}
    glyph_order = reference_font.getGlyphOrder()
    gsub = reference_font["GSUB"].table

    direct: dict[int, set[str]] = {}
    for record in gsub.FeatureList.FeatureRecord:
        for index in record.Feature.LookupListIndex:
            direct.setdefault(index, set()).add(record.FeatureTag)

    def nested_indices(subtable: object) -> set[int]:
        found: set[int] = set()
        for records in _substitution_record_lists(subtable):
            for record in records:
                found.add(record.LookupListIndex)
        return found

    reaching: dict[int, set[str]] = {index: set(tags) for index, tags in direct.items()}
    for index, lookup in enumerate(gsub.LookupList.Lookup):
        for subtable in lookup.SubTable:
            for nested in nested_indices(subtable):
                reaching.setdefault(nested, set()).update(direct.get(index, set()))

    substitutions: dict[str, set[tuple[str, int]]] = {}
    for index, lookup in enumerate(gsub.LookupList.Lookup):
        for subtable in lookup.SubTable:
            if subtable.__class__.__name__ != "SingleSubst":
                continue
            for source_name, target_name in subtable.mapping.items():
                substitutions.setdefault(target_name, set()).add((source_name, index))

    for cid, (source, _value) in lohit.GSUB_VARIANT_ADDITIONS.items():
        target_name = glyph_order[cid]
        source_name = glyph_order[source]
        rules = {
            index
            for name, index in substitutions.get(target_name, set())
            if name == source_name
        }
        assert rules, (
            f"no SingleSubst produces {target_name} (CID {cid}) from CID {source}"
        )
        tags = {tag for index in rules for tag in reaching.get(index, set())}
        assert tags & positional_features, (
            f"CID {source} -> {cid} is reached only by {sorted(tags)}, none of "
            "which means 'same text, different position'"
        )


def _substitution_record_lists(subtable: object) -> list[list[object]]:
    lists: list[list[object]] = []
    records = getattr(subtable, "SubstLookupRecord", None)
    if records:
        lists.append(list(records))
    for container in ("ChainSubClassSet", "SubRuleSet", "ChainSubRuleSet"):
        for entry in getattr(subtable, container, None) or []:
            for attribute in ("ChainSubClassRule", "SubRule", "ChainSubRule"):
                for rule in getattr(entry, attribute, None) or []:
                    rule_records = getattr(rule, "SubstLookupRecord", None)
                    if rule_records:
                        lists.append(list(rule_records))
    return lists
