"""Every non-ASCII regex in ``src/`` must survive Unicode normalization.

The Devanagari block contains eight **composition exclusions** -- U+0958..U+095F, the
precomposed nukta consonants -- and every Unicode normalization form (NFC, NFD, NFKC,
NFKD) replaces each with a two-code-point ``<base, U+093C NUKTA>`` sequence. NFC is
"decompose, then recompose", and recomposition is *blocked* for these, so even NFC
leaves them apart.

U+0929, U+0931 and U+0934 are a **different family** and the difference is
load-bearing: they are ordinary precomposed characters, so NFC and NFKC recompose them
and only NFD and NFKD take them apart. Measured, not assumed -- treating the two
families as one gets the exposure wrong, which is why the pattern below is fragile
under four forms and the other under two.

Inside a regex character class that is catastrophic, and in two distinct grades:

* **A range breaks the pattern.** ``[क-हक़-य़]`` written with literal characters
  normalizes into ``U+0915-U+0939 U+0915 U+093C - U+092F U+093C``, so the class ends on
  the DESCENDING range U+093C-U+092F. ``re.compile`` raises ``bad character range``,
  and because these patterns are module-level constants the module fails to **import**.
  The library stops working entirely -- this is not a subtle mismatch.

* **A set silently widens.** ``[ॊऩऱऴ]`` normalizes into a five-member set that
  includes the bare consonants ``न``, ``र`` and ``ळ``. That pattern is a *garble
  detector*, so the widened form convicts clean text: measured on 70 characters of
  ordinary Nepali prose, 0 hits as written and 11 after normalization.

Both instances existed in ``src/`` and both are fixed by writing the classes as
code-point escapes. This test is what stops the next one arriving, because nothing
else can see it: the fragile and the safe form are **visually identical**, so review
cannot catch it, and the fragile form compiles and passes the whole suite until
something normalizes the file.

That "something" is not exotic for this project. Normalizing Devanagari is a routine
operation in the domain, and an editor, a formatter, or a copy through any
normalizing tool is enough.

🛑 The scan is deliberately narrowed to strings in a **regex-function call position**.
An earlier version walked every string constant and reported 74 "fragile patterns" in
``src/``, but most were ordinary data -- a ``'क़'`` key in a lookup table. A data
string that normalization changes is a much milder problem than a pattern that stops
compiling, and conflating them buries the one instance that breaks an import under 73
that do not.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unicodedata

import pytest

import likhit

SRC_ROOT = pathlib.Path(likhit.__file__).parent

NORMALIZATION_FORMS = ("NFC", "NFD", "NFKC", "NFKD")

# Functions whose first positional argument is a regex pattern.
_REGEX_FUNCS = frozenset(
    {
        "compile",
        "match",
        "fullmatch",
        "search",
        "sub",
        "subn",
        "split",
        "findall",
        "finditer",
    }
)


def _source_files() -> list[pathlib.Path]:
    files = sorted(SRC_ROOT.rglob("*.py"))
    # rglob on a wrong or missing root yields NOTHING and raises nothing, so a
    # misconfigured scan reports zero findings and reads as a clean bill of health.
    # Assert the population instead of trusting it.
    assert files, f"no source files found under {SRC_ROOT}"
    return files


def _regex_patterns() -> list[tuple[pathlib.Path, int, str]]:
    """Every string literal passed as a pattern to an ``re.*`` call in ``src/``."""

    found: list[tuple[pathlib.Path, int, str]] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name not in _REGEX_FUNCS:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((path, first.lineno, first.value))
    return found


def _non_ascii_patterns() -> list[tuple[pathlib.Path, int, str]]:
    return [(p, n, v) for p, n, v in _regex_patterns() if not v.isascii()]


# Collected once at import so a parametrized id names the file and line of any
# failure, rather than a single test reporting "something somewhere is fragile".
_NON_ASCII = _non_ascii_patterns()


def test_the_scan_finds_the_patterns_it_is_meant_to_guard():
    """Without this, every test below could pass by finding nothing at all.

    A FLOOR plus named instances, deliberately not an exact count. An exact count also
    catches coverage silently shrinking -- a pattern moved behind a variable becomes
    invisible to an AST scan -- but it breaks on every unrelated change that adds a
    regex anywhere in src/, which on a repo with concurrent work means two unrelated
    reds for whoever merges second. Measured: that happened. The floor protects against
    the failure that actually matters here (a scan finding nothing and passing
    everything), and the named modules below protect the specific sites with history.
    """

    all_patterns = _regex_patterns()
    assert len(all_patterns) >= 80, len(all_patterns)
    assert len(_NON_ASCII) >= 25, len(_NON_ASCII)

    # The scan really is finding real modules, not an empty tree -- and specifically the
    # two that carried the repaired patterns, so neither can drop out unnoticed.
    modules = {path.name for path, _, _ in _NON_ASCII}
    assert "nepali_pdf.py" in modules
    assert "font_based.py" in modules

    # Every pattern the scan finds is automatically covered by the parametrized tests
    # below, so a newly added pattern is guarded without this count changing.
    assert len(_NON_ASCII) == len(_non_ascii_patterns())


@pytest.mark.parametrize(
    ("path", "lineno", "pattern"),
    _NON_ASCII,
    ids=[f"{p.name}:{n}" for p, n, _ in _NON_ASCII],
)
def test_every_non_ascii_regex_source_is_normalization_stable(path, lineno, pattern):
    """The strong property: the pattern's SOURCE is a normalization fixed point.

    Asserting stability of the source rather than "it still compiles" is deliberate.
    A pattern can survive normalization and mean something different -- that is
    exactly what ``_INVALID_SIGN_PATTERN`` did -- so compilability is too weak a
    property to guard.
    """

    for form in NORMALIZATION_FORMS:
        assert unicodedata.normalize(form, pattern) == pattern, (
            f"{path}:{lineno} changes under {form}. Write the class with code-point "
            f"escapes (\\uXXXX) instead of literal characters."
        )


@pytest.mark.parametrize(
    ("path", "lineno", "pattern"),
    _NON_ASCII,
    ids=[f"{p.name}:{n}" for p, n, _ in _NON_ASCII],
)
def test_every_non_ascii_regex_still_compiles_after_normalization(
    path, lineno, pattern
):
    """The weaker property, kept because its failure mode is the loud one.

    A source that normalization breaks makes the module unimportable, so this is the
    difference between a subtly wrong library and no library. Kept separate from the
    test above so a failure says which grade of harm it is.
    """

    for form in NORMALIZATION_FORMS:
        normalized = unicodedata.normalize(form, pattern)
        try:
            re.compile(normalized)
        except re.error as exc:  # pragma: no cover - the guarded condition
            pytest.fail(f"{path}:{lineno} stops compiling under {form}: {exc}")


# The two families behave DIFFERENTLY, and the difference decides which
# normalization forms a given literal is fragile under. Written as escapes, because a
# test file that spells these out in literal characters is fragile in exactly the way
# it is testing for -- and the first draft of this file was: two literals typed on one
# line arrived in different byte forms and the assertion compared a precomposed
# character against a decomposed pair.
_ALL_FORMS_DECOMPOSE = (
    "\u0958",
    "\u0959",
    "\u095a",
    "\u095b",
    "\u095c",
    "\u095d",
    "\u095e",
    "\u095f",
)
_NFD_ONLY_DECOMPOSE = ("\u0929", "\u0931", "\u0934")
_NEVER_DECOMPOSES = ("\u094a", "\u0949")


@pytest.mark.parametrize("char", _ALL_FORMS_DECOMPOSE)
def test_the_composition_exclusions_decompose_under_every_form(char):
    """U+0958-U+095F are Unicode composition exclusions.

    NFC is "decompose, then recompose", and recomposition is *blocked* for these, so
    even NFC leaves them decomposed. This is the family that breaks
    ``_ORPHAN_MATRA_PATTERN``, and it is why that pattern is fragile under all four
    forms rather than only the D ones.
    """

    for form in NORMALIZATION_FORMS:
        normalized = unicodedata.normalize(form, char)
        assert normalized != char, f"{char!r} unchanged under {form}"
        assert len(normalized) == 2
        assert normalized[1] == "\u093c"  # NUKTA


@pytest.mark.parametrize("char", _NFD_ONLY_DECOMPOSE)
def test_the_nfd_only_family_survives_nfc(char):
    """U+0929/0931/0934 are NOT composition exclusions, so NFC recomposes them.

    They decompose under NFD and NFKD only. That is why ``_INVALID_SIGN_PATTERN`` was
    reported fragile under two forms where ``_ORPHAN_MATRA_PATTERN`` was fragile under
    four -- one hazard, two different exposures, and treating them as one family gets
    the exposure wrong.
    """

    assert unicodedata.normalize("NFC", char) == char
    assert unicodedata.normalize("NFKC", char) == char
    for form in ("NFD", "NFKD"):
        assert unicodedata.normalize(form, char) != char


@pytest.mark.parametrize("char", _NEVER_DECOMPOSES)
def test_the_signs_with_no_decomposition_are_safe_in_a_literal(char):
    """Not every Devanagari character is a hazard -- short-O and candra-O are not.

    Without this the file would read as "literal Devanagari is always unsafe", which
    is both false and the kind of overreach that gets a guard deleted later.
    """

    for form in NORMALIZATION_FORMS:
        assert unicodedata.normalize(form, char) == char


def test_a_literal_range_over_an_exclusion_becomes_an_invalid_regex():
    """The actual failure mode, end to end: a valid pattern becomes ``re.error``.

    This is the pin that keeps the tests above from going vacuously green. If a future
    Python stopped decomposing these, every stability assertion would pass for a
    reason unrelated to the source being safe.
    """

    literal_range = "[\u0915-\u0939\u0958-\u095f]"
    assert re.compile(literal_range)  # fine as written

    for form in NORMALIZATION_FORMS:
        with pytest.raises(re.error, match="bad character range"):
            re.compile(unicodedata.normalize(form, literal_range))


def test_a_literal_set_over_the_nfd_family_silently_widens():
    """The quieter failure mode: still compiles, matches more.

    ``_INVALID_SIGN_PATTERN`` is a garble detector, so widening it to include the bare
    consonants convicts clean text rather than crashing. Compilability is therefore
    too weak a property to guard on, which is why the stability test above asserts the
    source is a fixed point instead.
    """

    literal_set = "[\u094a\u0929\u0931\u0934]"
    shipped = re.compile(literal_set)
    widened = re.compile(unicodedata.normalize("NFD", literal_set))

    # It still compiles -- no error to notice.
    assert widened

    # But the bare consonants now match, and they are among the commonest in Nepali.
    for bare in ("\u0928", "\u0930", "\u0933"):  # na, ra, lla
        assert not shipped.search(bare)
        assert widened.search(bare)

    # Priced on real prose: the widened form convicts every clean document. The
    # sample is 70 characters of ordinary Nepali -- the same sample quoted at the
    # patched site in extractors/font_based.py, so the two figures cannot drift apart.
    prose = "\u0905\u0916\u094d\u0924\u093f\u092f\u093e\u0930 \u0926\u0941\u0930\u0941\u092a\u092f\u094b\u0917 \u0905\u0928\u0941\u0938\u0928\u094d\u0927\u093e\u0928 \u0906\u092f\u094b\u0917 \u0928\u0947\u092a\u093e\u0932 \u0938\u0930\u0915\u093e\u0930 \u092a\u0930\u093f\u091a\u094d\u091b\u0947\u0926 \u0930\u0923\u0928\u0940\u0924\u093f\u0915 \u0915\u093e\u0920\u092e\u093e\u0921\u094c\u0902"
    assert shipped.findall(prose) == []
    assert len(widened.findall(prose)) == 11


def test_the_two_repaired_patterns_kept_their_exact_meaning():
    """The escapes must be a pure rewrite, not a redefinition.

    Both classes are compared against a spelled-out expected member set over the
    whole Devanagari block. Writing the expectation as escapes rather than reusing
    the pattern is the point -- a fixture derived from the thing it pins would hold
    at any value.
    """

    from likhit.converters.nepali_pdf import _ORPHAN_MATRA_PATTERN
    from likhit.extractors.font_based import _INVALID_SIGN_PATTERN

    block = [chr(c) for c in range(0x0900, 0x0980)]

    # _INVALID_SIGN_PATTERN: short-O plus the three nukta-form consonants, and
    # nothing else. Candra-O (U+0949) is deliberately excluded -- it is legitimate in
    # loanwords -- so its absence here is an assertion, not an omission.
    assert [c for c in block if _INVALID_SIGN_PATTERN.search(c)] == [
        "ऩ",
        "ऱ",
        "ऴ",
        "ॊ",
    ]
    assert not _INVALID_SIGN_PATTERN.search("ॉ")
    assert not _INVALID_SIGN_PATTERN.search("न")  # bare न must NOT match
    assert not _INVALID_SIGN_PATTERN.search("र")  # bare र must NOT match
    assert not _INVALID_SIGN_PATTERN.search("ळ")  # bare ळ must NOT match

    # _ORPHAN_MATRA_PATTERN: a vowel sign U+093E-U+094C not preceded by a consonant,
    # a virama or a nukta. Checked as a lookbehind, in context, both ways.
    assert _ORPHAN_MATRA_PATTERN.search("ा")  # bare matra: orphan
    assert _ORPHAN_MATRA_PATTERN.search(" ा")  # after a space: orphan
    assert not _ORPHAN_MATRA_PATTERN.search("का")  # after क: fine
    assert not _ORPHAN_MATRA_PATTERN.search("हा")  # after ह: fine
    assert not _ORPHAN_MATRA_PATTERN.search("क़ा")  # after क़: fine
    assert not _ORPHAN_MATRA_PATTERN.search("्ा")  # after virama: fine
    assert not _ORPHAN_MATRA_PATTERN.search("़ा")  # after nukta: fine
