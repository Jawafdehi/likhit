# Writing tests for likhit

`likhit` reads adversarial input. A legacy Nepali PDF can be wrong in ways that are
individually plausible and jointly invisible, so a test that *runs* is not the same
thing as a test that *would notice*. Most of what follows was learned by shipping a
change with a green suite and finding the defect later.

Read this alongside the [Gates](../README.md#gates) section of the README. That section
tells you which commands to run; this one is about whether a passing suite means
anything.

Every figure here was measured on `main` at the commit that added this file. Re-derive
before quoting one; they move.

## The gates, and what their numbers actually are

```
uv run ruff check .           gated
uv run ruff format --check .  gated   <- blocks here, unlike some sibling repos
uv run pytest                 gated
uv run ty check               advisory
```

`ruff format --check` is a **hard gate** in this repo. Sibling Jawafdehi repos leave
formatting ungated, so a habit carried across will fail CI here.

### `ty` has three different correct answers, and they differ by their denominator

```
uv run ty check src      3
uv run ty check tests    5
uv run ty check          8
```

None of these is *the* count. Quoting one without saying which scope it came from is
how a reviewer concludes a change added five diagnostics that were always there. The
`|| true` in CI and the rule suppressions in `pyproject.toml` are calibrated against
the **src** figure.

### `ty`'s count also depends on the worktree having a `.venv`

`ty` resolves imports against the project environment. In a worktree with no `.venv`
it falls back to whatever interpreter it finds, and every third-party import becomes
unresolved:

```
in a worktree with `uv sync --locked` run     8
in a fresh `git worktree add`, same commit   80
```

Same code, 72 diagnostics that are not yours. So the rule is **not** "expect 8" — that
number belongs to one invocation. Run the gate on an untouched worktree with the
*identical* invocation and diff the two counts. `uv sync --locked` in the new worktree
also gets you back to the 8.

### The skips are three different things

Measured on this commit. Re-derive with `uv run pytest -q -rs` rather than quoting the
total, which moves as tests are added:

```
5   real CIB fixtures -- git-ignored (PII), absent locally and in CI
3   LIKHIT_LOHIT_REFERENCE_TTF unset
2   LIKHIT_KALIMATI_REFERENCE_TTF unset
```

Only the first group is unavoidable. The other five are **coverage you do not have
unless you opt in** — they compare against upstream reference fonts, and they are the
tests most likely to catch a glyph-mapping regression. If you are touching
`lohit.py` or `kalimati_reference.py`, set the variable.

This table lives here and nowhere else; the README points at it rather than repeating
it. A figure copied into two files is a figure that will eventually disagree with
itself — the same defect class as the predicate defined twice in
`src/likhit/renderers/markdown.py`.

## Prove the test bites

> A test that passes both ways is worse than no test: it costs the same to maintain
> and it certifies nothing.

Break the thing the test covers, watch it fail, restore, watch it pass. Then say so in
the PR. Three ways that goes wrong here:

### 1. A guard cannot be bite-proven by reverting it

Some changes are *only* a comment plus a test — a guard against a future edit. Reverting
the source changes nothing, and the suite passes in both arms. A real example from this
repo: the change that guarded the non-additive `get_text` flag words has a source diff
of comments only, and 60 tests pass whether or not it is applied.

For a guard you must **attempt the thing it forbids**. Make the flag word additive; make
the constant the value the comment warns against. Then the test fires.

### 2. A revert that yields a collection error proves nothing

If the change added a symbol and the tests import it, reverting the source gives
`ImportError` at collection. Every test in the file "fails", and you have learned only
that the imports resolve — nothing about a single assertion.

Mutate **behaviourally** instead: leave every symbol importable and neuter what it does.
Forcing one predicate to return `True` reproduced a real shipped defect exactly and fired
27 cases across 4 test functions; reverting the same file gave one `ImportError` and no
information.

### 3. A surviving mutant is often a no-op mutant

Before concluding a test is weak, check that the mutation actually moved the observable.
Assert the defect is reproduced — print the wrong value — and only then read the suite's
verdict. A mutation that changes nothing survives everything.

## Purge `__pycache__` between mutation arms

`python -B` does not reach pytest's own subprocesses, and CPython decides a `.pyc` is
current from `(mtime, size)` alone. A length-preserving edit applied inside the same
mtime second is therefore invisible: the suite runs the **pre-mutation bytecode** and a
mutation that bites is scored a survivor.

```sh
find . -name __pycache__ -type d -prune -exec rm -rf {} +
```

Between every arm, not once at the start.

## Do not derive a fixture from the constant it means to pin

```python
# pins nothing
assert score(_make_case(_RANKING_FORGIVENESS + 1)) == 0
```

Both sides move together, so the assertion holds at *any* value of the constant. Write
the literal:

```python
assert _RANKING_FORGIVENESS == 12   # and say where 12 came from
```

A constant that no test can see is a constant a reviewer can change silently. If you add
a tuning constant, add an exact pin with its derivation, and prove the pin bites by
perturbing the value.

## Establish the baseline before you edit

Run the gates on the untouched branch first, in the same worktree layout you will use for
the change. A quoted pass count is worthless on its own; the only meaningful statement is
"branch N against base M". This is also the only way to tell a pre-existing failure from
one you introduced — check `main` directly before putting a fix in your PR.

## Library-specific traps a test can be blind to

These are the ones that have actually cost transcripts.

**`page.get_text(flags=...)` replaces PyMuPDF's default word — it does not add to it.**
And every `TEXTFLAGS_*` default sets `TEXT_MEDIABOX_CLIP`, so omitting `flags=` is not
the safe option either. `tests/test_pymupdf_flag_words.py` enumerates every call site
from source for this reason; a guard is only as wide as the set of places it looks.

**The legacy maps do not agree about what an ASCII digit is.** `PCS NEPALI` and
`FONTASY_HIMALI_TT` read `0-9` as `०-९`; `Preeti`, `Kantipur` and `Sagarmatha` read them
as consonants. A repair that assumes a digit is a digit destroys a letter on three of
the five maps. Any change to `legacy_maps.py` should be checked against **every** map,
not the one in the fixture.

**Nothing about a document's Devanagari ratio can see a letter-for-digit substitution.**
Both characters are in the same Unicode block, so a ratio check, a garble count and a
marker census all pass. If your change can swap a letter for a digit, the test has to
assert the specific character.

**The extractor and the renderer classify the same text and neither owns the contract.**
The renderer decides what a row is from the *shape* of a cell's text; the extractor
decides what text a cell holds. A correct change to either can silently reprice the
other, and it has: keeping a sub-table's register rows separate in the extractor made
the renderer's bare-figure test stop matching, and the two correct changes together were
worse than either alone. `tests/test_extractor_renderer_seam.py` is where that contract
lives — extend it rather than adding another one-sided test.

## Checking whether a fix is actually in `main`

*Linked from the README's Gates section, because it answers a question people ask
constantly and is otherwise the least discoverable part of this file.*

`git cherry` and `git patch-id` are not sufficient here, in two independent ways:

* **`patch-id` hashes context lines.** A rebased change reports as different for reasons
  that are not the change, so "not patch-identical" carries no information about whether
  the logic differs.
* **A squash merge defeats it entirely.** One commit lands in `main` for a branch of
  three, so `git cherry` reports all three as absent. It called 2 of 4 merged pull
  requests unlanded.

Compare content instead — the squash commit's diff against the branch's cumulative diff,
context and hunk headers stripped — and run a **negative control** (the same comparison
against an unrelated commit) so you know the instrument discriminates rather than
reporting identity for everything.
