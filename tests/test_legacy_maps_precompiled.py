"""The precompiled legacy mapper must agree with npttf2utf exactly.

``legacy_maps._CompiledMap`` bypasses ``FontMapper.map_to_unicode`` for speed
(31.9x on real spans, see its docstring), so its whole justification rests on the
output being byte-identical. These tests compare the two implementations directly
rather than asserting on expected strings: a hand-written expectation would drift
if npttf2utf ever ships a new ``map.json``, whereas a differential test keeps
pointing at whatever upstream currently does.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from likhit.extractors.legacy_maps import (
    ALL_MAP_KEYS,
    _CompiledMap,
    _get_compiled_map,
    _get_mapper,
    get_converter_for_map,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "samples"

# Hand-built cases for shapes the samples may not contain. The regex-flavoured
# ones matter because upstream passes each rule's replacement straight to
# ``re.sub``, so a backreference or an anchor must behave the same after
# precompilation.
EDGE_CASES = (
    "",
    " ",
    "\t\n ",
    "(1)",
    "  (12)  ",
    "g]kfn ;/sf/ cbfnt cg';Gwfg",
    "a b",
    "� x",
    "ABC def   GHI",
    "x" * 500,
    "१२३",
    "-1_",
    "\\1 backref",
    "^anchor$",
    "trailing   ",
    "   leading",
)


def _sample_spans(file_name: str, limit: int | None = None) -> list[str]:
    """Distinct span texts from a sample PDF, or [] when it is absent."""

    path = SAMPLES_DIR / file_name
    if not path.exists():
        return []

    seen: set[str] = set()
    doc = fitz.open(path)
    try:
        for page_number in range(doc.page_count):
            for block in doc[page_number].get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if span["text"]:
                            seen.add(span["text"])
            if limit is not None and len(seen) >= limit:
                break
    finally:
        doc.close()
    return sorted(seen)


@pytest.mark.parametrize("map_key", ALL_MAP_KEYS)
def test_compiled_map_matches_upstream_on_edge_cases(map_key: str) -> None:
    mapper = _get_mapper()
    convert = get_converter_for_map(map_key)

    for text in EDGE_CASES:
        assert convert(text) == mapper.map_to_unicode(text, from_font=map_key), (
            f"{map_key} diverged on {text!r}"
        )


@pytest.mark.parametrize("map_key", ALL_MAP_KEYS)
def test_compiled_map_matches_upstream_on_real_spans(map_key: str) -> None:
    # A page cap keeps this to 0.20s per map while still covering real legacy
    # text; the full 12,153-case sweep over every distinct span of all four
    # samples ran 0 differences on all five maps when the mapper was written.
    spans = _sample_spans("kanunpatrika.pdf", limit=600)
    if not spans:
        pytest.skip("sample missing: kanunpatrika.pdf")

    mapper = _get_mapper()
    convert = get_converter_for_map(map_key)
    differing = [
        text
        for text in spans
        if convert(text) != mapper.map_to_unicode(text, from_font=map_key)
    ]

    assert differing == [], f"{map_key} diverged on {len(differing)} spans"


def test_unknown_map_raises_the_same_exception_as_upstream() -> None:
    from npttf2utf.base.exceptions import NoMapForOriginException

    mapper = _get_mapper()
    # Lowercase "preeti" is not a key: npttf2utf's lookup is case-sensitive, and
    # only the literal "unicode" check below is not.
    for probe in ("NotAMap", "preeti", "PREETI"):
        with pytest.raises(NoMapForOriginException):
            mapper.map_to_unicode("x", from_font=probe)
        with pytest.raises(NoMapForOriginException):
            get_converter_for_map(probe)("x")


@pytest.mark.parametrize("probe", ["Unicode", "unicode", "UNICODE"])
def test_unicode_origin_passes_text_through_untouched(probe: str) -> None:
    text = "abc १ �"

    assert get_converter_for_map(probe)(text) == text
    assert _get_mapper().map_to_unicode(text, from_font=probe) == text


@pytest.mark.parametrize("map_key", ALL_MAP_KEYS)
def test_every_map_uses_the_translate_fast_path(map_key: str) -> None:
    """All five maps have single-character keys, so none should need the fold.

    If npttf2utf ever ships a multi-character key this test fails while
    :func:`test_compiled_map_matches_upstream_on_real_spans` keeps passing --
    which is the intended signal: correctness is preserved by the fallback, only
    the speed claim in the docstring stops holding.
    """

    compiled = _get_compiled_map(map_key)

    assert compiled._translate_table is not None


def test_compiled_map_falls_back_when_a_character_map_key_is_multi_character() -> None:
    """A multi-character key must not raise, and must stay dead as upstream is.

    ``str.maketrans`` rejects a key longer than one character, which is the only
    reason the fold survives as a fallback. It is not a correctness difference:
    upstream folds ``character-map.get(character, character)`` over *single*
    characters, so a two-character key can never match there either. Both
    implementations therefore leave ``"ab"`` alone and apply only the ``"c"``
    entry -- this asserts the fallback reproduces that rather than "fixing" it.
    """

    rules = {
        "pre-rules": [],
        "post-rules": [],
        "character-map": {"ab": "क", "c": "ख"},
    }
    compiled = _CompiledMap(rules)
    upstream_fold = "".join(rules["character-map"].get(ch, ch) for ch in "abc")

    assert compiled._translate_table is None
    assert compiled.convert("abc") == upstream_fold == "abख"


def test_per_map_word_caches_do_not_leak_across_maps() -> None:
    """Each map memoizes words in its own cache, keyed on the word alone.

    This is the failure mode that would be worst and quietest: one shared
    word->output cache would serve whichever map ran first, so a Kantipur span
    would silently come back decoded as Preeti. The differential tests above cover
    it only incidentally (the second map to run would start disagreeing with
    upstream), so assert it directly.

    ``"~"`` is the probe because it is maximally discriminating -- searched over
    single ASCII characters and 169 two-character combinations, no input makes all
    five maps disagree, but ``"~"`` splits them three ways, which is enough for a
    shared cache to show up.
    """

    mapper = _get_mapper()
    probe = "~"
    expected = {
        key: mapper.map_to_unicode(probe, from_font=key) for key in ALL_MAP_KEYS
    }
    assert len(set(expected.values())) == 3, (
        f"probe is no longer discriminating: {expected}"
    )

    # Interleave, and repeat in reverse, so a shared cache cannot be masked by
    # every map happening to be asked in one order only.
    order = list(ALL_MAP_KEYS) + list(reversed(ALL_MAP_KEYS)) + list(ALL_MAP_KEYS)
    for key in order:
        assert get_converter_for_map(key)(probe) == expected[key], (
            f"{key} returned another map's cached result"
        )

    caches = {id(_get_compiled_map(key).convert_word) for key in ALL_MAP_KEYS}
    assert len(caches) == len(ALL_MAP_KEYS)


def test_word_splitting_is_lossless() -> None:
    """The tokenizer must cover the input exactly, as upstream's does.

    ``convert`` joins the per-word results with no separator, so any character the
    split dropped would silently vanish from the output.
    """

    from likhit.extractors.legacy_maps import _WORD_SPLIT

    for text in EDGE_CASES:
        assert "".join(_WORD_SPLIT.findall(text)) == text
