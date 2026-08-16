"""Every ``page.get_text()`` call site in ``src/``, and the flag word it gets.

``flags=`` **replaces** PyMuPDF's default word rather than adding to it. That is the
trap; what makes it dangerous here is that the natural remediation -- OR-ing the
mode's ``TEXTFLAGS_*`` default back in -- is destructive twice over, measured over
all 6,236 documents of the Nepali audit corpus (VOL-239):

* ``TEXTFLAGS_RAWDICT`` already sets ``TEXT_USE_CID_FOR_UNKNOWN_UNICODE``, so an
  additive *plain* pass returns no U+FFFD at all and every one of 22,871,324 CID
  markings silently stops.
* it also sets ``TEXT_MEDIABOX_CLIP``, which deletes 1,250,148 glyphs across 4,022
  of 6,236 documents.

``test_font_based.py`` pins both of those for the two call sites in
``font_based.py``. This module exists because that is **not enough**: nothing
enumerated the call sites, so a fourth one -- or a change to the third, which had no
test at all -- passed the entire suite. The guard is only as wide as the set of
places it looks, so the set is derived from the source here rather than remembered.

Two facts this file records that are easy to get backwards:

* **Omitting ``flags=`` is not the safe option.** *Every* ``TEXTFLAGS_*`` default
  sets ``TEXT_MEDIABOX_CLIP``, so a bare ``page.get_text()`` clips. Two call sites
  do that deliberately and are registered below with the consequence spelled out.
* **``flags`` can be passed positionally.** ``get_text(option, clip, flags)`` -- a
  positional third argument would carry a flag word straight past a scan that only
  looks at keywords, so it is rejected outright.
"""

from __future__ import annotations

import ast
from collections import Counter
import importlib
from pathlib import Path

import pymupdf as fitz
import pytest

from likhit.extractors.numeric_boundaries import collect_page_numeric_boundary_repairs

_SRC = Path(__file__).resolve().parent.parent / "src"

# --------------------------------------------------------------------------- #
# The registry. A call site absent from here fails the coverage test below, which
# is the whole point: the reviewer has to say what word the new site gets and why.
# --------------------------------------------------------------------------- #

# (module path relative to src/, enclosing function, the flags= expression) -> why.
#
# The enclosing function is part of the key, and that is load-bearing rather than
# decorative. Keyed on (module, expression) alone, a SECOND site in the same module
# reusing an EXISTING expression collapses onto the existing key and the comparison
# below is unchanged -- measured, fully green at 28 passed. The reverse is equally
# invisible: delete one of two same-keyed sites and nothing notices it left. Since the
# whole thesis of this file is that a fourth site must not pass silently, and
# `fitz.TEXT_PRESERVE_WHITESPACE` is the expression a fourth site is likeliest to
# reuse, that hole was the file's central claim failing on its own terms.
#
# It is still not a perfect key -- two sites in one function with one expression would
# collapse -- which is why the comparison is a MULTISET, not a set. The count catches
# what the key cannot.
_EXPLICIT_FLAG_SITES: dict[tuple[str, str, str], str] = {
    (
        "likhit/extractors/font_based.py",
        "get_cid_marked_page_dict",
        "fitz.TEXT_PRESERVE_WHITESPACE",
    ): "the plain detection pass. Must NOT carry bit 128: it detects unmappable "
    "glyphs BY their U+FFFD, and the CID bit decodes them to raw CIDs instead, "
    "so an additive word returns zero U+FFFD and ends all CID marking.",
    (
        "likhit/extractors/font_based.py",
        "get_cid_marked_page_dict",
        "_TEXT_DICT_FLAGS",
    ): "the CID pass. Deliberately the plain word PLUS bit 128 and nothing else -- "
    "that one-bit difference is the contrast the marking is derived from.",
    (
        "likhit/extractors/numeric_boundaries.py",
        "collect_page_numeric_boundary_repairs",
        "fitz.TEXT_PRESERVE_WHITESPACE",
    ): "the numeric-boundary repair reads character ORIGINS, so a clipped glyph is "
    "a lost digit, not merely a lost glyph. See the behavioural test at the "
    "bottom of this file, which is what this site was missing.",
}

# Call sites that pass no flags at all and therefore accept the clipping default.
# (module path relative to src/, enclosing function) -> why that is tolerable here.
_DEFAULT_FLAG_SITES: dict[tuple[str, str], str] = {
    (
        "likhit/extractors/font_classifier.py",
        "classify_ocr_page",
    ): "classification, not extraction: the text feeds a garble ratio and a "
    "Devanagari count, and nothing here reaches output. Clipping is still not "
    "free -- if it empties page_text the page is called IMAGE_ONLY, which is "
    "what routes a page to paid OCR -- so this is registered rather than "
    "ignored. Changing it needs a corpus measurement, not a patch.",
    (
        "likhit/pdf_page_analysis.py",
        "analyze_pdf_pages",
    ): "reporting only: text_length, token_count and the ratios on "
    "PdfPageAnalysis. Same clipping caveat as above.",
}


def _iter_get_text_sites():
    """Yield ``(relpath, enclosing_function, flags_expression | None, positional)``."""

    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        # Map each node to its enclosing function so a site can be named by what it
        # is in rather than by a line number, which churns on every edit above it.
        #
        # Recursed explicitly rather than via `ast.walk` + `setdefault`: walk is
        # breadth-first, so an outer `def` is visited before a `def` nested inside it
        # and `setdefault` makes the OUTER name stick. Verified -- a call inside
        # `inner()` reported `outer`. No site is in a nested function today, but both
        # of these modules use inner helpers, and a wrong name here also widens the
        # key collision the registry comment describes.
        enclosing: dict[int, str] = {}

        def annotate(node: ast.AST, name: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    annotate(child, child.name)
                else:
                    enclosing[id(child)] = name
                    annotate(child, name)

        annotate(tree, "<module>")

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute) and node.func.attr == "get_text"
            ):
                continue
            flags = next(
                (ast.unparse(kw.value) for kw in node.keywords if kw.arg == "flags"),
                None,
            )
            yield rel, enclosing.get(id(node), "<module>"), flags, len(node.args)


def _all_sites() -> list[tuple[str, str, str | None, int]]:
    return list(_iter_get_text_sites())


# A `flags=` expression must be a pure constant expression -- a name, an attribute, or
# those combined with bitwise operators. That is a real restriction on what src/ may
# write, and it is deliberate: `_resolve` below evaluates whatever it finds, so anything
# richer (a call, a subscript, a conditional) would be both un-analysable by this scan
# and executed by it. The positional-argument test sets the same precedent -- constrain
# what the source may write so the scan stays honest.
_ALLOWED_FLAG_NODES = (
    ast.Expression,
    ast.Name,
    ast.Attribute,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Load,
    ast.BitOr,
    ast.BitAnd,
    ast.BitXor,
    ast.Invert,
)


@pytest.mark.parametrize(
    ("rel", "expression"),
    sorted({(rel, flags) for rel, _fn, flags, _n in _all_sites() if flags is not None}),
    ids=lambda v: v,
)
def test_every_flag_expression_is_a_pure_constant_expression(rel, expression):
    tree = ast.parse(expression, mode="eval")
    offenders = [
        type(node).__name__
        for node in ast.walk(tree)
        if not isinstance(node, _ALLOWED_FLAG_NODES)
    ]
    assert offenders == [], (
        f"{rel} passes flags={expression!r}, which contains {offenders}. Keep it to "
        "names, attributes and bitwise operators: this module evaluates the "
        "expression to check its bits, and it must not execute anything richer."
    )


def _resolve(rel: str, expression: str) -> int:
    """Evaluate a ``flags=`` expression in its own module's namespace.

    Safe because `test_every_flag_expression_is_a_pure_constant_expression` bounds what
    can appear here. Module globals win over the injected `fitz`, which is correct --
    each module imports `pymupdf as fitz` itself.
    """

    module = importlib.import_module(rel.removesuffix(".py").replace("/", "."))
    return eval(expression, {"fitz": fitz, **vars(module)})  # noqa: S307


# --------------------------------------------------------------------------- #
# What PyMuPDF's defaults actually are. Hard-coded rather than computed from the
# library, because this is the pin: if a PyMuPDF upgrade changes a default word,
# the reasoning in every comment above stops holding and we want to be told.
# --------------------------------------------------------------------------- #

_MEASURED_DEFAULTS = {
    "TEXTFLAGS_TEXT": 195,
    "TEXTFLAGS_WORDS": 195,
    "TEXTFLAGS_BLOCKS": 195,
    "TEXTFLAGS_DICT": 199,
    "TEXTFLAGS_RAWDICT": 199,
    "TEXTFLAGS_HTML": 199,
    "TEXTFLAGS_XHTML": 199,
    "TEXTFLAGS_XML": 195,
    "TEXTFLAGS_SEARCH": 210,
}


@pytest.mark.parametrize(("name", "value"), sorted(_MEASURED_DEFAULTS.items()))
def test_pymupdf_default_flag_words_are_what_the_comments_assume(name, value):
    assert getattr(fitz, name) == value, (
        f"a PyMuPDF upgrade changed {name} from {value} to {getattr(fitz, name)}. "
        "Every comment in this module and in font_based.py that reasons about 199 or "
        "195 is now unverified -- re-derive the two harms on the corpus before "
        "adjusting this pin. This failure is the signal, not a broken test."
    )


@pytest.mark.parametrize("name", sorted(_MEASURED_DEFAULTS))
def test_every_pymupdf_default_flag_word_clips_to_the_mediabox(name):
    """Which is why ``flags=`` is passed explicitly at all.

    There is no mode whose default is safe for this library, so "just leave the
    default alone" is not an available answer -- the choice is between an explicit
    word and a clipping one.
    """

    assert getattr(fitz, name) & fitz.TEXT_MEDIABOX_CLIP


def test_the_clip_bit_and_the_cid_bit_are_the_ones_named_in_the_comments():
    assert fitz.TEXT_MEDIABOX_CLIP == 64
    assert fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE == 128


# --------------------------------------------------------------------------- #
# Coverage: the source decides the set of sites, not this file.
# --------------------------------------------------------------------------- #


def test_every_explicit_flags_site_in_src_is_registered():
    # Counter, not set: a duplicate site raises a count the registry does not have, and
    # a deleted one lowers a count. A set comparison sees neither.
    found = Counter(
        (rel, fn, flags) for rel, fn, flags, _n in _all_sites() if flags is not None
    )
    # `Counter(dict)` would read the dict's VALUES as counts -- here the rationale
    # strings -- so the keys are taken explicitly.
    expected = Counter(_EXPLICIT_FLAG_SITES.keys())
    assert found == expected, (
        "a get_text(flags=...) call site in src/ was added, removed, moved or "
        "reworded.\n"
        f"  unregistered: {sorted((found - expected).elements())}\n"
        f"  stale entries: {sorted((expected - found).elements())}\n"
        "flags= REPLACES PyMuPDF's default word. Read this module's docstring "
        "before adding an entry."
    )


def test_every_flagless_get_text_site_in_src_is_registered():
    found = Counter((rel, fn) for rel, fn, flags, _n in _all_sites() if flags is None)
    expected = Counter(_DEFAULT_FLAG_SITES.keys())
    assert found == expected, (
        "a get_text() call with no flags= was added or removed. Every TEXTFLAGS_* "
        "default sets TEXT_MEDIABOX_CLIP, so this is not the neutral choice.\n"
        f"  unregistered: {sorted((found - expected).elements())}\n"
        f"  stale entries: {sorted((expected - found).elements())}"
    )


def test_get_text_flags_are_never_passed_positionally():
    """``get_text(option, clip, flags)`` -- a third positional argument is a flag word.

    It would carry any value at all past both coverage tests above, which look only
    at keywords. Two positional arguments (option, clip) are already more than
    anything here needs, so the bar is set where the scan can still see everything.
    """

    positional = [(rel, fn, n) for rel, fn, _flags, n in _all_sites() if n > 1]
    assert positional == [], (
        f"pass flags by keyword so the coverage tests can see it: {positional}"
    )


# Parametrized over what the SOURCE has, not over the registry above. Asserting the
# property about the record of reality would only ever restate the record: an
# unregistered additive site would be caught by the coverage test as a bookkeeping
# mismatch, and the actual reason it is wrong would go unstated.
# Deduplicated on (module, expression): the flag word's PROPERTIES are a function of the
# expression alone, so two sites sharing one word need the property checked once. The
# coverage test above is where duplicate SITES are caught, on the fuller key.
_FOUND_EXPLICIT = sorted(
    {(rel, flags) for rel, _fn, flags, _n in _all_sites() if flags is not None}
)


@pytest.mark.parametrize(("rel", "expression"), _FOUND_EXPLICIT, ids=lambda v: v)
def test_no_explicit_flag_word_clips_to_the_mediabox(rel, expression):
    """The property that holds for every site, whatever else it needs.

    Clipping deletes glyphs that are inside the mediabox, cropbox and rect alike --
    on one 16-page bulletin, 60.6% of the text -- so no reading of this library wants
    it. This is the assertion that survives a new site being added for a new reason.
    """

    assert _resolve(rel, expression) & fitz.TEXT_MEDIABOX_CLIP == 0


def test_only_the_cid_pass_carries_the_cid_bit():
    """The plain pass detects unmappable glyphs by their U+FFFD; bit 128 removes them.

    Pinned across all sites at once rather than per site, so a *second* pass
    acquiring the bit is caught even though the plain pass kept its own word.
    """

    with_cid = {
        (rel, expression)
        for rel, expression in _FOUND_EXPLICIT
        if _resolve(rel, expression) & fitz.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
    }
    assert with_cid == {("likhit/extractors/font_based.py", "_TEXT_DICT_FLAGS")}


# --------------------------------------------------------------------------- #
# The behavioural half for the third site, which had none.
# --------------------------------------------------------------------------- #

_ADDITIVE = fitz.TEXTFLAGS_RAWDICT | fitz.TEXT_PRESERVE_WHITESPACE


class _AdditivelyFlaggedPage:
    """A page as a well-meant ``flags=`` "fix" would present it.

    Wrapping the page rather than editing the source keeps this a test of the
    *consequence*. The site itself is unchanged and stays under the scan above.
    """

    def __init__(self, page: fitz.Page) -> None:
        self._page = page

    def get_text(self, mode: str, flags: int | None = None) -> dict:
        # Discarding `flags` is the point, but not blindly: the call under test passes
        # the SHIPPED word, and this wrapper's claim is that substituting the additive
        # one changes the outcome. If the site's word ever changes, the substitution is
        # no longer the comparison this test describes -- so it is asserted rather than
        # assumed.
        assert flags == fitz.TEXT_PRESERVE_WHITESPACE, (
            f"the call site passed flags={flags}, not the shipped "
            f"{fitz.TEXT_PRESERVE_WHITESPACE}; this wrapper would then be comparing "
            "the additive word against something other than what ships"
        )
        return self._page.get_text(mode, flags=_ADDITIVE)

    def get_cdrawings(self) -> list:
        return self._page.get_cdrawings()

    def __getattr__(self, name: str) -> object:
        return getattr(self._page, name)


def _bleeding_ruled_amount_pdf() -> tuple[fitz.Document, fitz.Page]:
    """A ruled amount whose first digit starts 6pt left of the page edge.

    Not a contrived offset: a table column that bleeds into the binding margin puts
    a leading digit exactly here, and PyMuPDF keeps it -- ``TEXT_MEDIABOX_CLIP``
    is what removes it.
    """

    doc = fitz.open()
    page = doc.new_page(width=400, height=160)
    page.insert_text((-6.0, 80.0), "1234.56789.01", fontname="helv", fontsize=12)
    chars = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"][0][
        "lines"
    ][0]["spans"][0]["chars"]
    boundary_x = float(chars[6]["origin"][0])
    page.draw_line((boundary_x, 55), (boundary_x, 92), width=0.5)
    return doc, page


def test_clipping_the_numeric_boundary_pass_silently_loses_a_leading_digit():
    """The third site's flag word is load-bearing on the VALUE, not just the count.

    The repair splits a run of digits at a ruling. Under the clipping word the run it
    splits is missing its first character, so the repair still succeeds and still
    looks well-formed -- it just reports a number five times too small. Nothing
    downstream can detect that: the parts are plausible, the ruling is real, and the
    line text agrees with the (clipped) span.
    """

    doc, page = _bleeding_ruled_amount_pdf()
    try:
        shipped = collect_page_numeric_boundary_repairs(page, page_number=1)
        clipped = collect_page_numeric_boundary_repairs(
            _AdditivelyFlaggedPage(page), page_number=1
        )
    finally:
        doc.close()

    assert [(r.merged_text, r.parts) for r in shipped] == [
        ("1234.56789.01", ("1234.5", "6789.01"))
    ]
    assert [(r.merged_text, r.parts) for r in clipped] == [
        ("234.56789.01", ("234.5", "6789.01"))
    ]


def test_the_clipping_word_is_what_drops_the_glyph_not_the_page_geometry():
    """Isolates the cause, so the test above cannot pass for the wrong reason.

    PyMuPDF drops glyphs that are entirely off-page under *either* word. The delta
    between the two words is exactly one glyph, and it is a glyph the shipped word
    keeps -- so the fixture is measuring the flag, not the layout.
    """

    doc, page = _bleeding_ruled_amount_pdf()
    try:

        def chars(flags: int) -> str:
            page_dict = page.get_text("rawdict", flags=flags)
            return "".join(
                char["c"]
                for block in page_dict["blocks"]
                for line in block.get("lines", ())
                for span in line.get("spans", ())
                for char in span.get("chars", ())
            )

        shipped = chars(fitz.TEXT_PRESERVE_WHITESPACE)
        clipped = chars(_ADDITIVE)
    finally:
        doc.close()

    assert shipped == "1234.56789.01"
    assert clipped == "234.56789.01"
    assert shipped[1:] == clipped
