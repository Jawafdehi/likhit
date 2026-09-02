"""The placeholder vocabulary, and the two properties that make it worth centralising.

Both of these were real defects before this module existed, not hypotheticals:

* the two redaction passes each defined a module-level ``CITIZENSHIP_PLACEHOLDER`` with a
  *different* value, which is what ``test_no_duplicated_definitions.py`` refuses;
* ``likhit.quality``'s ``legacy_ascii`` axis read a placeholder as legacy-encoded Nepali,
  taking a synthetic document from ``clean`` to ``garbled``.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unicodedata

import likhit.privacy
from likhit.privacy import placeholders


def _privacy_sources() -> list[pathlib.Path]:
    root = pathlib.Path(likhit.privacy.__file__).parent
    return sorted(root.glob("*.py"))


def test_every_placeholder_a_module_emits_is_registered() -> None:
    """A pass cannot invent a marker the quality side has never heard of.

    🛑 This is the guard the module docstring promises, and the failure it prevents is
    silent: an unregistered marker still redacts correctly, so the redaction journal looks
    perfect, and the only symptom is that the audit starts calling redacted documents
    garbled. Scanning the source for the literal is what closes it -- asserting on the
    constants alone would pass a pass that hardcoded its own string.
    """

    literal = re.compile(r"\[REDACTED:[A-Z0-9-]+\]")
    found: set[str] = set()
    for path in _privacy_sources():
        if path.name == "placeholders.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.update(literal.findall(node.value))

    unregistered = found - set(placeholders.ALL)
    assert not unregistered, (
        f"these markers are emitted or named outside placeholders.py and are not in "
        f"ALL, so likhit.quality will score them as legacy text: {sorted(unregistered)}"
    )


def test_the_registered_markers_are_distinct() -> None:
    """The inline and table forms must not collide -- they used to share a name."""

    assert len(set(placeholders.ALL)) == len(placeholders.ALL)
    assert placeholders.CITIZENSHIP != placeholders.TABLE_CITIZENSHIP
    assert placeholders.DATE_OF_BIRTH != placeholders.TABLE_DATE_OF_BIRTH


def test_the_pattern_matches_every_registered_marker_whole() -> None:
    for marker in placeholders.ALL:
        match = placeholders.PLACEHOLDER_PATTERN.search(marker)
        assert match is not None, marker
        assert match.group(0) == marker, (
            f"{marker!r} matched only as {match.group(0)!r} -- the alternation is not "
            f"longest-first, so a longer marker is being partly consumed"
        )


def test_the_pattern_is_an_allowlist_not_a_shape() -> None:
    """An unregistered ``[REDACTED:...]`` must NOT match.

    Deliberate: a marker this package never writes, appearing in a transcript, is either a
    real decode artifact worth reporting or a typo in a pass. Both should surface, and a
    general ``\\[REDACTED:[A-Z-]+\\]`` shape would swallow both.
    """

    assert not placeholders.contains_placeholder("[REDACTED:NAME]")
    assert not placeholders.contains_placeholder("[REDACTED:CITIZENSHIP]")
    assert placeholders.contains_placeholder("[REDACTED:CITIZENSHIP-NO]")


def test_stripping_leaves_a_separator_rather_than_joining_neighbours() -> None:
    """Replaced with a space, not "" -- otherwise the strip manufactures an artifact.

    A marker sits between a label and what follows it. Removing it with the empty string
    runs those together into exactly the kind of long punctuation-bearing token that the
    ``legacy_ascii`` and ``spacing`` axes are built to notice, so a fix for one instrument
    would have fed the other.
    """

    stripped = placeholders.strip_placeholders("नं.[REDACTED:CITIZENSHIP-NO]हो")
    assert "नं. हो" == stripped
    assert "REDACTED" not in stripped


def test_no_registered_marker_needs_unicode_normalisation() -> None:
    """Pure ASCII, so a decomposed copy of the source cannot change what they match."""

    for marker in placeholders.ALL:
        assert marker.isascii(), marker
        assert unicodedata.normalize("NFD", marker) == marker


def test_the_contact_markers_the_release_pipeline_writes_are_registered() -> None:
    """🛑 Registered without an in-repo emitter, on purpose.

    ``test_every_placeholder_a_module_emits_is_registered`` above scans this package's
    sources, so it is structurally blind to a consumer that runs its own redaction pass and
    then hands the result to :mod:`likhit.quality`. The OAG release pipeline does exactly
    that for contact details, and its two markers reached a published corpus unregistered --
    where each is scored as two ``legacy_ascii`` runs, the defect this module was built to
    prevent.
    """

    assert placeholders.EMAIL == "[REDACTED:EMAIL]"
    assert placeholders.PHONE == "[REDACTED:PHONE]"
    for marker in (placeholders.EMAIL, placeholders.PHONE):
        assert marker in placeholders.ALL
        assert placeholders.PLACEHOLDER_PATTERN.fullmatch(marker)


def test_a_contact_placeholder_leaves_a_document_scoring_as_it_did() -> None:
    """The bite: the registration is worth nothing unless it moves the audit, so audit it.

    Reverting either marker out of ``ALL`` fails this -- the placeholders then survive
    ``normalise_for_audit`` and ``legacy_ascii`` counts them as legacy-encoded Nepali.
    """

    from likhit.quality import audit_text

    original = (
        "कार्यालयको लेखापरीक्षण प्रतिवेदन तयार भएको छ।\nसम्पर्क इमेल र फोन नम्बर तल दिइएको छ।\n"
    ) * 24
    contacted = original.replace(
        "तल दिइएको छ।",
        f"{placeholders.EMAIL} {placeholders.PHONE} हो।",
    )
    assert contacted.count(placeholders.EMAIL) == 24, "fixture must carry the markers"

    before, after = audit_text(original), audit_text(contacted)

    assert after["verdict"] == before["verdict"]
    for axis in before["checks"]:
        assert after["checks"][axis]["verdict"] == before["checks"][axis]["verdict"], (
            f"contact placeholders moved the {axis} axis from "
            f"{before['checks'][axis]['verdict']} to {after['checks'][axis]['verdict']}"
        )
