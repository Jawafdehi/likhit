"""Every path a Markdown doc points at must exist, be deliberately absent, or be pending.

Two kinds of reference, and the second is the one that matters here.

* ``[text](path)`` links. A conventional link checker covers these.
* **Backticked repo-relative paths** -- ``` `tests/test_foo.py` ``` -- which a link
  checker does not see at all. `docs/writing-tests.md` cross-references two test files
  that way, so the constraint "land the docs after the tests they name" was held only in
  a pull-request body. Pull-request bodies are not in the tree.

That is the doc's own thesis applied to itself: a guard is only as wide as the set of
places it looks.

Three things a naive version of this test gets wrong, all found by writing it:

1. **A line-cite is not a path.** ``` `src/likhit/extractors/legacy_maps.py:26` ``` is a
   legitimate way to point at a line, and a trailing ``:NN`` has to be stripped before
   the path is checked.
2. **Some paths are deliberately absent.** `docs/cib-press-release-extraction.md`
   correctly documents `tests/fixtures/cib/`, which is git-ignored (PII) and present on
   nobody's checkout. A doc naming it is right, not stale.
3. **A pending reference must expire.** A doc may name a file a sibling change adds. That
   is allowed once, declared, and only until the file exists -- see `_PENDING`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Markdown links, minus absolute URLs, mailto: and bare anchors.
_LINK = re.compile(r"\]\((?!https?:|mailto:|#)([^)\s]+)\)")

# Backticked paths that look repo-relative. Anchored on the top-level directories that
# exist, so ordinary code spans -- `_TEXT_DICT_FLAGS`, `re.MULTILINE` -- are not mistaken
# for paths. An optional trailing `:NN` line-cite is captured separately and discarded.
_PATHREF = re.compile(r"`((?:src|tests|docs|tools|site|samples)/[\w./-]+?)(?::\d+)?`")

# Paths a doc in this tree names that a SIBLING change adds. Each entry must say which,
# and each is an error once the file exists -- see the expiry test below. This is how the
# merge order is recorded in the tree instead of in a pull-request description.
# Empty, and it got there by working. Both entries -- `tests/test_pymupdf_flag_words.py`
# and `tests/test_extractor_renderer_seam.py` -- were declared here while their sibling
# changes were in review. When those landed,
# `test_no_pending_reference_has_arrived_yet` went red and named them, and the entries
# were deleted. Both references are now guarded like any other.
_PENDING: dict[str, str] = {}


def _is_generated(path: Path) -> bool:
    """Skip generated trees, so the collected set does not depend on tool state.

    `.pytest_cache/README.md` is the reason this exists: it is git-ignored, it is a
    README pytest writes, and its presence depends on whether the suite has been run in
    this checkout before -- so including it made the number of collected tests vary
    between two worktrees at the same commit. Found by comparing those two counts.
    """

    return any(
        part.startswith(".") or part in {"_site", "build", "dist", "node_modules"}
        for part in path.relative_to(_REPO).parts[:-1]
    )


_DOCS = sorted(path for path in _REPO.rglob("*.md") if not _is_generated(path))


def _ids(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _gitignored_prefixes() -> tuple[str, ...]:
    """Directory patterns from ``.gitignore``, so a deliberately-absent path is excused.

    A light read rather than a `git check-ignore` subprocess: only directory-style
    entries matter here, and this keeps the test working in a checkout with no git.
    """

    gitignore = _REPO / ".gitignore"
    if not gitignore.exists():
        return ()
    return tuple(
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip().endswith("/") and not line.strip().startswith("#")
    )


_IGNORED = _gitignored_prefixes()


def _deliberately_absent(target: str) -> bool:
    return any(target.startswith(prefix) for prefix in _IGNORED)


def test_the_scan_found_the_docs():
    # A glob matching nothing passes every parametrized test below vacuously.
    assert len(_DOCS) >= 5, [_ids(path) for path in _DOCS]


@pytest.mark.parametrize("doc", _DOCS, ids=_ids)
def test_every_relative_markdown_link_resolves(doc: Path):
    text = doc.read_text(encoding="utf-8")
    missing = [
        target
        for target in _LINK.findall(text)
        if not (doc.parent / target.split("#")[0]).exists()
    ]
    assert missing == [], f"{_ids(doc)} links to paths that do not exist: {missing}"


def _heading_anchors(markdown: str) -> set[str]:
    anchors: set[str] = set()
    for line in markdown.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match is None:
            continue
        heading = re.sub(r"<[^>]+>", "", match.group(1))
        heading = re.sub(r"[^\w\s-]", "", heading.casefold())
        anchors.add(re.sub(r"[\s-]+", "-", heading).strip("-"))
    return anchors


@pytest.mark.parametrize("doc", _DOCS, ids=_ids)
def test_every_relative_markdown_anchor_resolves(doc: Path):
    broken: list[str] = []
    for target in _LINK.findall(doc.read_text(encoding="utf-8")):
        path_text, separator, fragment = target.partition("#")
        if not separator or not fragment:
            continue
        target_path = doc if not path_text else doc.parent / path_text
        if target_path.suffix.lower() != ".md" or not target_path.exists():
            continue
        anchors = _heading_anchors(target_path.read_text(encoding="utf-8"))
        if fragment.casefold() not in anchors:
            broken.append(target)

    assert broken == [], f"{_ids(doc)} links to headings that do not exist: {broken}"


@pytest.mark.parametrize("doc", _DOCS, ids=_ids)
def test_every_backticked_repo_path_resolves(doc: Path):
    """The half a link checker misses.

    Resolved from the repository root, because a backticked path in prose is written as
    the reader would type it from the top of the tree.
    """

    text = doc.read_text(encoding="utf-8")
    missing = [
        target
        for target in _PATHREF.findall(text)
        if not (_REPO / target).exists()
        and not _deliberately_absent(target)
        and target not in _PENDING
    ]
    assert missing == [], (
        f"{_ids(doc)} names paths that do not exist: {missing}. If a sibling change adds "
        "them, add them to _PENDING with which change -- that records the merge order "
        "here instead of in a pull-request body. If they are git-ignored on purpose, the "
        ".gitignore entry excuses them automatically."
    )


def test_no_pending_reference_has_arrived_yet():
    """``_PENDING`` must expire. An entry whose file EXISTS is the failure.

    Without this the map is a permanent excuse list, and the ordering it encodes stops
    being checked the moment it is satisfied. With it, the sibling change landing makes
    this red until the entry is deleted -- and from then on the reference is guarded like
    any other.
    """

    arrived = {
        target: why for target, why in _PENDING.items() if (_REPO / target).exists()
    }
    assert arrived == {}, (
        f"these now exist and must be removed from _PENDING: {sorted(arrived)}. Their "
        "references are guarded by test_every_backticked_repo_path_resolves from here on."
    )


def test_the_backticked_path_scan_actually_matches_something():
    """Guards the pattern itself.

    `_PATHREF` is the only reason the ordering constraint is enforced at all, and a
    pattern that silently stopped matching would leave every doc passing.
    """

    found = {
        target
        for doc in _DOCS
        for target in _PATHREF.findall(doc.read_text(encoding="utf-8"))
    }
    assert len(found) >= 5, sorted(found)
    assert any(target.startswith("tests/") for target in found), sorted(found)
    # And the line-cite form must survive stripping rather than being skipped.
    assert "src/likhit/extractors/legacy_maps.py" in found, sorted(found)
