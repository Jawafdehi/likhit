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

import likhit
from likhit.privacy import placeholders
from likhit.quality.axes import check_legacy_ascii
from likhit.quality.normalise import normalise_for_audit


def _package_sources() -> list[pathlib.Path]:
    """Every ``.py`` in the *package*, not just in ``likhit/privacy/``.

    ⚠️ Widened deliberately. Scanning only the privacy package meant a module anywhere else in
    ``likhit`` could emit an unregistered marker invisibly and the guard would stay green. This
    does not close the real hole -- see
    ``test_release_markers_stay_registered_without_an_emitter`` -- it closes the part of it
    that is answerable from inside this repository.
    """

    root = pathlib.Path(likhit.__file__).parent
    return sorted(root.rglob("*.py"))


def test_every_placeholder_a_module_emits_is_registered() -> None:
    """A pass cannot invent a marker the quality side has never heard of.

    🛑 This is the guard the module docstring promises, and the failure it prevents is
    silent: an unregistered marker still redacts correctly, so the redaction journal looks
    perfect, and the only symptom is that the audit starts calling redacted documents
    garbled. Scanning the source for the literal is what closes it -- asserting on the
    constants alone would pass a pass that hardcoded its own string.

    🛑🛑 **Green here does not mean the vocabulary is complete.** This reads sources in this
    repository, and three markers reached a published corpus from a redaction pass that lives
    outside it. An AST scan cannot see a caller, so this guard is necessary and not
    sufficient; the sibling test below covers what it structurally cannot.
    """

    literal = re.compile(r"\[REDACTED:[A-Z0-9-]+\]")
    found: set[str] = set()
    for path in _package_sources():
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


def test_release_markers_stay_registered_without_an_emitter() -> None:
    """The three contact markers must stay in ``ALL`` even though nothing here writes them.

    🛑 **This test exists because the sibling above cannot see the pass that emits these.**
    The OAG release redacts contact details with its own pass and then hands the transcripts
    to :mod:`likhit.quality`; an AST scan of this repository finds no emitter, so nothing else
    would notice their removal. They are in a published corpus -- 287 occurrences over 104 of
    6,234 transcripts -- and unregistering one silently reinstates the bias this module exists
    to prevent.

    ⚠️ The literals are spelled out here rather than referenced through the constants on
    purpose. ``assert placeholders.PHONE in placeholders.ALL`` would pass if someone changed
    the constant's *value*, and that is the failure that actually happened: a draft of this
    change registered ``[REDACTED:PHONE]``, a string with **0** occurrences in the corpus, so
    it stripped nothing while the registration looked correct.
    """

    for marker in (
        "[REDACTED:PHONE-NO]",
        "[REDACTED:TABLE-PHONE-NO]",
        "[REDACTED:EMAIL]",
    ):
        assert marker in placeholders.ALL, (
            f"{marker} is written into published transcripts by the release's contact pass. "
            f"Unregistered, likhit.quality scores it as legacy-encoded Nepali."
        )
        assert placeholders.PLACEHOLDER_PATTERN.fullmatch(marker), marker


def test_the_contact_markers_are_not_scored_as_legacy_text() -> None:
    """The bite: the registration is worth nothing unless it moves the audit, so audit it.

    Not a restatement of the tuple. This runs each marker through
    :func:`normalise_for_audit` and the real ``legacy_ascii`` axis, so it fails if normalise
    stops calling :func:`strip_placeholders`, if the alternation stops matching, *or* if a
    marker leaves ``ALL``. Unstripped, each is two legacy runs -- ``[REDACTED`` and ``NO]`` --
    because ``LEGACY_PUNCT`` contains both brackets.
    """

    markers = ("[REDACTED:PHONE-NO]", "[REDACTED:TABLE-PHONE-NO]", "[REDACTED:EMAIL]")
    filler = "कार्यालयको लेखापरीक्षण प्रतिवेदन " * 125
    text = filler + "\n" + "\n".join(f"सम्पर्क {marker} हो" for marker in markers)

    # 🛑 Control arm, and it is the load-bearing half. `legacy_runs == 0` below proves nothing
    # on its own -- it would also hold if the axis never counted a bracketed Latin run in the
    # first place. So assert the axis DOES count them before stripping. The document has no
    # code fence, fiscal span or blank run, so the placeholder strip is the only part of
    # `normalise_for_audit` that can move this number.
    unstripped = check_legacy_ascii(text, dev_n=4000, lat_n=0)[1]
    assert unstripped["legacy_runs"] >= 2 * len(markers), unstripped

    normalised = normalise_for_audit(text)
    assert "REDACTED" not in normalised
    verdict, metrics = check_legacy_ascii(normalised, dev_n=4000, lat_n=0)
    assert metrics["legacy_runs"] == 0, metrics
    assert verdict == "clean"
