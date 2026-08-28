"""No module-level function may be defined in two modules, and duplicated constants
must be an explicit, agreeing list.

A definition that exists twice is a fix waiting to be applied to one copy. That is not
hypothetical here: ``_looks_like_page_furniture`` existed in both the converter and the
renderer, both copies decided the same question about the same block on either side of
the extractor/renderer seam, and the known pending fix to it (a length bound, so a
216-character paragraph merely *mentioning* a running-head phrase is not discarded) had
to be landed twice or not at all.

🛑 The class was **6.5x larger than the record of it**. A note in the landing-plan
ledger said closing the seam gap "surfaced two duplicated-definition pairs in main".
An AST sweep of every module-level name found **13** duplicated definitions across 6
modules -- 4 functions and 9 constants. Nothing had drifted yet; all 13 were identical.
So the honest statement is that the risk was latent, not live, and 11 of the 13 were
unrecorded.

Four functions are now single definitions. The nine remaining duplicates are all
constants, listed below with a reason, and every one is asserted identical -- so drift
becomes a failure rather than a divergence nobody notices.

This scan sees only **module-level** names. A name defined inside a class or a function
is invisible to it, and so is a value duplicated as an inline literal rather than a
named constant -- the same blind spot ``tests/test_tuning_constants.py`` records for
itself.
"""

from __future__ import annotations

import ast
import collections
import pathlib

import pytest

import likhit

SRC_ROOT = pathlib.Path(likhit.__file__).parent

# Names that are *supposed* to appear in many modules. These are per-module by
# definition, so counting them as duplication would be noise that hides the real thing.
#
# `main` joined this set when the package gained a second and third console script
# (`likhit-audit`, `likhit-redact` alongside `likhit-save`). It is the conventional entry
# point name and `[project.scripts]` points at it, so renaming one to satisfy this scan
# would be the tail wagging the dog. Note what that costs: this scan can no longer catch a
# genuine duplicate named `main`. That is acceptable because `main` is by convention a thin
# argparse shim over library code and is never the shared logic this guard exists to
# protect -- and the three here were each checked to be exactly that before it was added.
#
# ⚠️ The parser builders were NOT added. Three modules each wanted `build_parser`, which is
# the same argument -- but a distinct name per command costs nothing and keeps `grep` useful,
# so they are `build_audit_parser` / `build_redact_parser` / `build_parser` instead. Widening
# this set is the last resort, not the first.
BY_DESIGN = frozenset({"__all__", "logger", "main"})

# Duplicated CONSTANTS that are accepted, each with why it is not merged. Every one is
# also asserted byte-equal by shape below, so accepting a duplicate is not the same as
# ignoring it.
#
# The bar for merging: multi-line logic gets merged, because that is where a one-sided
# fix is a real hazard and where the two copies can silently disagree in a way review
# will not see. A single-line value or compiled pattern is left duplicated when merging
# it would mean a new import edge between sibling modules purely to share one line.
ACCEPTED_DUPLICATE_CONSTANTS: dict[str, str] = {
    "_HEADER_Y_MAX": (
        "layout geometry, shared by the module that DECIDES a document is two-column "
        "and the module that then SPLITS it. Merging needs a new import edge between "
        "sibling handlers; the agreement assertion below is what actually matters, "
        "because drift here means a document is detected with one geometry and split "
        "with another."
    ),
    "_COLUMN_GUTTER": "as _HEADER_Y_MAX -- same pair of modules, same reasoning.",
    "_DEVANAGARI_PATTERN": (
        "one compiled class, [\\u0900-\\u097F], in three modules. Sharing it would "
        "couple the converter, the Kalimati extractor and the page analyser for a "
        "single line."
    ),
    "_LATIN_PATTERN": "one compiled class, [A-Za-z].",
    "_TOKEN_PATTERN": "one compiled class, \\S+.",
    "_SUSPICIOUS_LATIN_TOKEN_PATTERN": (
        "a compiled pattern used by two quality scorers. Its bracket class carries a "
        "documented subtlety, so the agreement assertion below is the point."
    ),
    "_IKAR": "a two-code-point Devanagari constant shared by the Kalimati and Lohit tables.",
    "_PUA_IKAR": "as _IKAR -- private-use sentinel for the same pair of tables.",
    "_PUA_REPH": "as _IKAR.",
}


def _module_level_definitions() -> dict[tuple[str, str], list[tuple[str, ast.AST]]]:
    """``(kind, name) -> [(module path, node)]`` for every module-level definition."""

    files = sorted(SRC_ROOT.rglob("*.py"))
    # rglob on a wrong root yields nothing and raises nothing, which would make every
    # assertion below pass for the wrong reason.
    assert files, f"no source files under {SRC_ROOT}"

    found: dict[tuple[str, str], list[tuple[str, ast.AST]]] = collections.defaultdict(
        list
    )
    for path in files:
        rel = str(path.relative_to(SRC_ROOT))
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # ⚠️ BY_DESIGN applies here too, and it did not until a second console
                # script arrived. The set's own comment says "names that are supposed to
                # appear in many modules", but it was consulted only on the constant
                # branch -- so adding `main` to it looked like it worked and changed
                # nothing. Honouring it on both branches is what makes the set mean what
                # it says.
                if node.name not in BY_DESIGN:
                    found[("func", node.name)].append((rel, node))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in BY_DESIGN:
                        found[("const", target.id)].append((rel, node))
    return found


def _duplicates(kind: str) -> dict[str, list[tuple[str, ast.AST]]]:
    return {
        name: items
        for (k, name), items in _module_level_definitions().items()
        if k == kind and len(items) > 1
    }


def test_the_sweep_sees_a_real_population():
    """Guard against the whole file passing because the scan found nothing.

    FLOORS plus named instances, deliberately not exact counts. An exact count would
    also catch coverage silently shrinking, but it breaks on every unrelated change that
    adds a module-level definition anywhere in src/ -- which on a repo with concurrent
    work means an unrelated red for whoever merges second. Measured: that happened.

    The real guards against shrinkage are elsewhere and are specific: the duplicate
    tests below assert a SET rather than a count, so both a new duplicate and a
    disappeared one are named, and test_the_four_merged_functions_are_each_defined_
    exactly_once pins the four with history individually.
    """

    definitions = _module_level_definitions()
    functions = [k for k in definitions if k[0] == "func"]
    constants = [k for k in definitions if k[0] == "const"]

    assert len(functions) >= 240, len(functions)
    assert len(constants) >= 90, len(constants)
    # And the sweep really reaches the modules this file is about.
    assert ("func", "_looks_like_page_furniture") in definitions
    assert ("const", "_HEADER_Y_MAX") in definitions


def test_no_module_level_function_is_defined_in_two_modules():
    """The rule this file exists to enforce.

    Four functions used to break it: ``_looks_like_page_furniture``,
    ``_paragraph_ends_with_caption``, ``_render_paragraph_markdown`` (converter and
    renderer) and ``_is_vowel_poor_latin_token`` (converter and page analyser). All
    four now live in one module and are imported by the other, using import edges that
    already existed -- no new module and no new dependency direction.
    """

    duplicated = _duplicates("func")
    assert duplicated == {}, {
        name: [path for path, _ in items] for name, items in duplicated.items()
    }


def test_the_four_merged_functions_are_each_defined_exactly_once():
    """Named individually, so a re-duplication is reported as itself.

    The test above would also catch it, but it reports "some function is duplicated";
    this reports which one, and these four are the ones with a history.
    """

    definitions = _module_level_definitions()
    for name in (
        "_looks_like_page_furniture",
        "_paragraph_ends_with_caption",
        "_render_paragraph_markdown",
        "_is_vowel_poor_latin_token",
    ):
        sites = definitions[("func", name)]
        assert len(sites) == 1, [path for path, _ in sites]


def test_duplicated_constants_are_exactly_the_accepted_list():
    """A NEW duplicate constant must be a decision, not an accident.

    Asserting set equality both ways matters: a missing entry means someone merged one
    and the list is stale, and an extra means a new duplicate arrived unreviewed. Only
    the second is a problem, but a list that quietly disagrees with reality stops being
    read at all.
    """

    duplicated = set(_duplicates("const"))
    accepted = set(ACCEPTED_DUPLICATE_CONSTANTS)

    assert duplicated - accepted == set(), (
        "new duplicated constant(s). Merge them, or add an entry to "
        "ACCEPTED_DUPLICATE_CONSTANTS saying why not."
    )
    assert accepted - duplicated == set(), (
        "ACCEPTED_DUPLICATE_CONSTANTS lists a constant that is no longer duplicated -- "
        "delete the entry."
    )
    assert len(duplicated) == 9


@pytest.mark.parametrize("name", sorted(ACCEPTED_DUPLICATE_CONSTANTS))
def test_every_accepted_duplicate_constant_still_agrees(name):
    """The assertion that makes accepting a duplicate safe rather than resigned.

    Compared as parsed syntax, not as bytes: byte-equality fails on a trailing comment
    or different line wrapping, neither of which is divergence, and that failure would
    be indistinguishable from the real thing.
    """

    items = _duplicates("const")[name]
    shapes = {ast.dump(node) for _, node in items}
    assert len(shapes) == 1, (
        f"{name} has drifted between {[path for path, _ in items]}. These copies decide "
        f"the same thing in different modules; make them agree or merge them."
    )


def test_the_two_layout_constants_agree_at_runtime_not_only_in_source():
    """The source-shape check above compares syntax; this compares the live values.

    Kept separate because they can fail independently: a constant could be reassigned
    after definition, or shadowed by a conditional, and the AST would still match.
    """

    from likhit.handlers import structure_detection, two_column_layout

    assert structure_detection._HEADER_Y_MAX == two_column_layout._HEADER_Y_MAX
    assert structure_detection._COLUMN_GUTTER == two_column_layout._COLUMN_GUTTER
