"""VOL-323: the digit-dominant legacy companion face, and its digit row.

A legacy Nepali document is often typed in two faces from the same family: a PROSE
face carrying the words, and a numeric COMPANION carrying the figures. The two have
**opposite digit rows**, and that is the whole problem:

    face          plain ASCII row `0123456789` draws   decimal separator
    Spins         Preeti consonants `ण् _ _ घ _ छ ट ठ ड ढ`   `.` -> `।`
    Spins_EXT     `०१२३४५६७८९`                              a literal ASCII `.`

So the companion matches **no** shipped map key: `FONTASY_HIMALI_TT` and `PCS NEPALI`
get its digits right and its separator wrong; `Preeti` and `Spins` destroy its digits
outright (`2075` -> `द्दण्ठछ`). It is therefore excluded from candidacy by design, and
its digits survive into the transcript as raw ASCII -- **644,295 of 654,506
digit-only spans (98.44%) across 2,105 documents**, and `Spins_EXT` + `SpinsEXT` hold
**5,693,272 ASCII digit characters** whose own glyph programs draw them as `०-९`
(VOL-317 run `c8a8e41c`; those two figures are separate measurements and must not be
multiplied).

Damodaha chose **option (a), digit-row transliteration**, on 2026-08-16, and directed
on 2026-08-17 that it land on **v17** rather than waiting for a generation of its own.

## 🛑 The constraint every option had to satisfy

**No rule here may key on the font NAME.** `Spins` and `Spins_EXT` share a family name
and have opposite digit rows, so one substring rule is necessarily wrong for one of
them -- which is why `spins` is absent from `legacy_maps._REGISTRY` and must stay
absent. Measured corroboration that a name rule cannot work at all: the companion also
occurs under **generic CID subset names**. `CIDFont+F8` in
`2949__1612856563Kathariya Nagarpalika.pdf` is one -- 1,003 spans, 5,299 characters,
**0.62%** ASCII-alphabetic, 920 digit-only spans, drawing `40001098.` and `34539000.0`
with a literal ASCII period. Nothing in its name says "Spins", and it is the same face
class. So VOL-323's own framing as "the Spins digit companion" **understates the
population**.

## The discriminator: a conjunction of three conditions

Each condition excludes a different class, and dropping any one of them breaks a
measured case:

1. **NOT ALREADY ROUTED.** The font matches no `legacy_maps._REGISTRY` key. This is
   what keeps the rule away from `FONTASY_HIMALI_TT` and `PCS NEPALI`, which *also*
   draw `०-९` on the plain row -- they are true positives of condition 3 and must not
   be touched, because they are already name-routed to a map that transliterates their
   digits. Dropping this causes a DOUBLE transliteration on two correctly-handled
   families, not a shape error.

2. **DIGIT-DOMINANT CONTENT.** Almost no ASCII-alphabetic characters in the font's
   spans. This separates the companion from the prose face that shares its family name.
   Measured over the whole corpus (VOL-317):

       face          spans      ASCII-alpha share   digit-only spans
       Spins        711,792           54.7%              7.8%
       SpinsEXT     370,335            0.42%            69.9%
       Spins_EXT  1,453,507            0.85%            72.4%

3. **THE GLYPH PROGRAM DRAWS `०-९` ON THE PLAIN ROW.** The only condition that can
   answer VOL-317's constraint. A keystroke-mapped face hands PyMuPDF the same ASCII
   whichever row its digits are on, so **no byte-level test can supply the layout** --
   `2075` looking like a plausible year is not evidence that the bytes mean `२०७५`. The
   layout is in the glyphs and nowhere else.

## How condition 3 decides, and why it is two-sided

Two independent instruments must agree, because each covers the other's weakness.

**A. The family reference** (`_FAMILY_DEVANAGARI_DIGITS`): signatures of the plain row
of `3653+Spins_EXT`, whose reading was page-verified by VOL-317 at 3x from the extracted
font program. Within the family this is near-exact -- the prose face's SHIFTED row
matched it 7/7 at Hamming 0-16 of 256, and `3097+SpinsEXT`, a face from a DIFFERENT
document, also 7/7. Precise, but a single-sided threshold.

**B. An external two-sided test** (`_EXTERNAL_DEVANAGARI_DIGITS` /
`_EXTERNAL_LATIN_DIGITS`): both rows of `DroidSansDevanagari-Regular`, a font with no
connection to this corpus, which carries Latin digits at `U+0030` and Devanagari digits
at `U+0966`. Taking both references from ONE font controls for stroke weight and style.
Cross-typeface distances are large, so this is read as a **comparison, not a threshold**
-- does the row look more like `०-९` or more like `0-9`? Measured:

    face                              median vs Devanagari   median vs Latin   verdict
    companion 3653+Spins_EXT                    64                 122        Devanagari
    companion 3097+SpinsEXT                     64                 122        Devanagari
    companion 2949 CIDFont+F8                   65                 123        Devanagari
    prose 3653+Spins (consonant row)           106                 110        neither
    NimbusSans                                 114                  25        Latin
    NotoSans                                   111                   8        Latin
    DejaVuSansMono                             108                  19        Latin
    Cantarell                                  106                  13        Latin
    DroidSans                                  113                   0        Latin

The Latin margin is what the acceptance gate asked for: a genuine Latin-digit face
prefers Latin by 80-113 Hamming, and no legacy companion does.

**SCOPE LIMIT, stated because it is a consequence of making A the gate.** A is a match
against *this legacy family's* digit shapes, not against "Devanagari digits" in general
-- so `DroidSansDevanagari`'s own `०-९`, fed in as a candidate, is rejected, and a
Devanagari-digit companion from an unrelated foundry would be too. That is deliberate:
the population VOL-323 measured is this family plus generic CID subsets of the same
lineage, all of which match A tightly (`2949+CIDFont+F8`, a different document and
producer, matched 10 of 10). The failure direction is UNDER-firing -- an unrecognised
face keeps its ASCII digits, exactly as today -- and widening A to a shape-agnostic
"is this a Devanagari digit" test would trade that for a false-positive surface over
every numeric face in the corpus. `test_digit_companion.py` pins this so a later run
that wants to widen it has to argue with a test rather than discover the behaviour.

## 🛑 Two things that look like evidence and are not, both measured

* **`fitz.Font.unicode_to_glyph_name` is not a font-table read.** It returns the Unicode
  NAME OF THE INPUT CODEPOINT. All three readable faces of this family report
  `['DIGIT ZERO', 'DIGIT ONE', ...]` for `0x30`-`0x39` regardless of what they draw, so
  it cannot distinguish them. It is the first thing that looks decisive and it is inert.
* **The shirorekha test is real but too thin to ship alone.** "A Devanagari consonant
  carries a full-width top bar and a digit does not" holds -- consonants measure 1.00 --
  but Devanagari digits measure a mean of **0.76 with a maximum of 0.94**, so the classes
  nearly touch and a threshold would be calibrated on 17 glyphs. Reported by the probe,
  relied on by nothing.

## Abstention is not a negative

A subset font whose cmap is not Unicode-addressable cannot be read by this instrument at
all: `font.has_glyph(0x30)` is false and there is no shape to compare. Such a face
**abstains** and its digits are left exactly as they are today. That is a coverage limit,
not a misreading, and the failure direction is safe -- **5 of the 19** faces the acceptance
sweep classifies `companion (Spins family)` abstain this way (14 fire, 0 deny;
`ACCEPTANCE-VOL323-51d3f79c20e2107f.json`). An earlier revision of this line said "4 of 17":
the 4 is `TPFP-...json`'s `false_negative`, a different instrument with a different
denominator, and no artifact reports 17. VOL-317 makes the same point about `11102`'s two faces
(0/95 cmap-addressable glyphs): reporting them as "no Devanagari coverage" would be a
false negative, because they carry no evidence either way.

🛑 **Abstention is not the ONLY way this under-fires, and an earlier revision of this
section implied it was.** A face can render cleanly, be compared, and still be denied by a
margin. Measured on the full corpus at `main` `2f7e377`: of the 10 faces that come back
`False`, nine draw genuine Latin digits and are correctly rejected, but `CIDFont+F10` in
`2963__...Kanchan rup Nagarpalika.pdf` draws `०१२३४५६७८९`. Its distances are
`[11, 13, 14, 14, 14, 19, 28, 32, 35, 69]`, so it matches **6** of the `_ROW_MATCH_MIN` 7
it needs, with the seventh at **28** against `_FAMILY_MATCH_MAX` 25 -- three cells short.
Its spans are the money column this exists to repair (`2,427,435.00`, `1,785,718.00`,
`1,835,301.00`, `488,245.00`), and they still ship as raw ASCII.

It is a false NEGATIVE, so the failure direction is still the safe one -- the face is left
exactly as it is today -- but the honest statement of coverage is "2,781 abstentions **and
at least one near-miss denial**", not "the abstentions". Corroborated against both
references rather than asserted: summed over the row, that face sits **249** from the
Devanagari family and **1,105** from the external Latin one, the same direction as the
confirmed companion `CIDFont+F1` in `2908` (101 against 1,065), while the two
genuine-Latin `False` verdicts point the other way (1,162/280 and 1,089/289).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

import pymupdf as fitz

from likhit.extractors.legacy_maps import (
    _ASCII_DIGITS,
    _matched_registry_key,
    devanagarize_ascii_digits,
)

#: Env kill-switch. This ships **ON** -- Damodaha directed on 2026-08-17 that VOL-323
#: land on v17 rather than waiting for its own generation -- but a paired-tree release
#: gate has to be able to build the same tree with it off, which is what this is for.
#: Set to "0", "false" or "no" to disable.
DIGIT_COMPANION_ENV = "LIKHIT_DIGIT_COMPANION"

#: Rendering geometry for a glyph signature. Pinned, not tuned: the pinned signature
#: tables below were generated at exactly these values, so changing any of them
#: invalidates all three tables at once. `test_digit_companion.py` asserts the
#: reference tables still reproduce, which is what makes that coupling visible.
_RENDER_PT = 48
_ZOOM = 2
_SIG_SIZE = 16
_SIG_CELLS = _SIG_SIZE * _SIG_SIZE

#: Hamming distance below which two signatures are the SAME shape, for the
#: within-family comparison only. Measured separation, re-derived from
#: `TPFP-51d3f79c20e2107f.json` (60 PDFs, **968** distinct faces): the 22 faces that draw
#: Devanagari digits match the family reference at a median of 0 / p90 15 over 213 glyph
#: distances, and the 946 that do not sit at a median of 80 / p90 133 over 580. 25 of 256
#: cells is inside that separation.
#:
#: 🛑 **"Room on both sides" is what an earlier revision of this line claimed, and the
#: artifacts do not show it.** The wide separation above is dominated by genuine Latin
#: faces. The NEAR side is crowded: **27** unrouted, partially-matching faces (25
#: `kalimati`, 2 `spins_ext`) carry individual glyph distances as low as **0 and 3**,
#: i.e. inside this threshold, while the face as a whole does not reach
#: `_ROW_MATCH_MIN`; and the one corpus near-miss (`2963`/`CIDFont+F10`, see the module
#: docstring) sits at **28**, three cells above. So this value is one step from moving
#: faces in both directions. It still looks like the right defensive choice -- a face
#: that matches only 6 of 10 is not evidence -- but the margin is thin on the near side,
#: and a later run deciding whether it is safe to widen must re-measure rather than lean
#: on the word "room".
_FAMILY_MATCH_MAX = 25

#: How many of the ten plain-row glyphs must match, and the minimum readable for a verdict
#: at all. Below this the instrument returns `None` (abstains) rather than `False`.
#:
#: 🛑 **The loosening from 10 to 7 has ZERO measured effect on which faces are
#: transliterated, and an earlier revision of this line justified it with faces that
#: condition 1 already excludes.** It said "3 of the readable companion faces carry 7-9 of
#: the row". Re-derived from `TPFP-51d3f79c20e2107f.json`: exactly three faces would fire
#: with a matched count in 7..9 -- `ABCDEE+Fontasy Himali` (9), `ABCDEE+Fontasy Himali`
#: (7), `BCEGEE+FontasyHimali` (7) -- and **all three are `routed_by_name: true`**, so
#: `detect_digit_companion_fonts`' first condition removes them before they can be
#: companions. Meanwhile every one of the 14 firing companions in
#: `ACCEPTANCE-VOL323-51d3f79c20e2107f.json` sits at 10 of 10, and so do all **131**
#: faces that fire over the full corpus at `main` `2f7e377` (matched 10 / comparable 10).
#:
#: So 7 is a **defensive margin for subsets that omit glyphs**, held on the argument that
#: a real subset can be incomplete -- not a threshold any observed companion needed.
#: Raising it back to 10 would change nothing measured today. It is one of the two margins
#: that deny `2963`/`CIDFont+F10` (matched 6), so it is not free either.
_ROW_MATCH_MIN = 7

# --------------------------------------------------------------------------------- #
# The pinned signature tables. Each entry is a 16x16 ink-density bitmap as 64 hex
# digits, in plain-row order 0..9. Generated by
# `oag-corpus/runs/local-main-51d3f79c20e2107f/sweep_digit_companion_51d3f79c20e2107f.py`
# and reproduced by this module's tests from the fonts named above, so a table that
# drifts from its source font fails rather than silently deciding differently.
# --------------------------------------------------------------------------------- #

#: `3653+Spins_EXT`'s plain row -- Devanagari digits, page-verified by VOL-317.
_FAMILY_DEVANAGARI_DIGITS: tuple[str, ...] = (
    "03e00ff81e0c3c067802f003e003e003c003c003c0074007600e301e187c07f0",
    "1fe07c18f01cf00e700e381e0ffe001e001e001e001e001e001e001e001e000f",
    "3fc0fff820060002000200020e0e1ffc0ff003c0002000100008000400020001",
    "7fe07ffc00060006001e1ffc000e000300011f831ffe0ff80018000c00060002",
    "c0004003300e181c0c3c067803e001c003c00360063006300638063803f001e0",
    "c000e060e070e070e0f061e03fe001c003803f007f007ec07870001c000f0003",
    "1fe07fe0c000c0007fc01fe004000800181c0e3e07fe00f80010000c00060003",
    "8000800080f881e481828183c187c0df403f6003600330031c0e1ffe07f801f0",
    "7fff7ffffffe1800300060006000c000e000e0f87ffc7ffe3f0600060006000c",
    "0f003fe0f820e070c0f07fe01f800c000300018000600018000e00030007000f",
)

#: `DroidSansDevanagari-Regular` at `U+0966`-`U+096F`. External, unrelated to this corpus.
_EXTERNAL_DEVANAGARI_DIGITS: tuple[str, ...] = (
    "07e00ff81ffc3c3e781e700ff00ff007f007f007f00f780e781e3ffc1ffc07f0",
    "0ff03ffe781e781f3e1e047c03f01fc0fe001f8007e000fc001e000f001f001e",
    "1fc0fff8607c001c001e001e001c103c7ef87fe03fc001f00078003c000f0006",
    "3ff0fffc401e001e003e07f807fe000f000f000f7e3e7ff81fe00078003e000e",
    "e007e007f007700e381e1e3c0ff007e003e00ff01e781c3c381c3c3c1ff807f0",
    "380070007000e000e000e000e07878fc3ffc07f000380038001c000e000e0007",
    "1fc07fe0f000e000f0003fc03fc078007018707e3e7e0ffc0018001c000e0006",
    "e000e000e1f8e3fce70e6706738771f730f7301738071c071e070f0e07fe01f8",
    "0018003c00f801e003c007800f003e0038007000f000e000e000700f3fff0ff8",
    "1f807fe0f0e0e070e07070e07fc01f00078001e00078001e000f000700070006",
)

#: The SAME font at `U+0030`-`U+0039`, so the Latin comparison is style-controlled.
_EXTERNAL_LATIN_DIGITS: tuple[str, ...] = (
    "07e01ff8381c700e7007f007e007e007e007e007f007f007700e381e1ffc07f0",
    "003f01ff1fff7f1f301f001f001f001f001f001f001f001f001f001f001f001f",
    "1ff07ffc701e000e000e000e001c003c007001e003c00f001e007800ffffffff",
    "0fe07ffc701e000e000e000e003c0fe00ff8001e000700070007000efffe7ff0",
    "0038007800f801b803b807380e381c3838387038e038ffffffff003800380038",
    "3ffc3ffc70007000700070007fe07ffc001e000f00070007000f001efffc7ff0",
    "01fc07fc1e00380078007000f3f0fffef80ef007f00770077007380e1ffe07f8",
    "ffffffff0007000e001e001c00380070007000e001e001c00380078007000e00",
    "07f03ffc781e700e700e381e1e7c0ff01ff8381e700fe007e007700f3ffe1ff8",
    "07e03ff8781c700ee007e007e007700f3e7f1fe70007000e001e003c3ff83fc0",
)

#: Maximum share of a companion's characters that may be ASCII letters. Set from the
#: measured gap, which is nearly three orders of magnitude wide: the two companion faces
#: sit at 0.42% and 0.85%, the prose face at 54.7%. 5% is deliberately far above the
#: companions and far below the prose face, so it is not a boundary anyone discovered.
_MAX_ALPHA_SHARE = 0.05

#: Minimum share of a companion's non-space characters that must be digits.
_MIN_DIGIT_SHARE = 0.5

#: Minimum characters before the content test is trusted at all. A three-character font
#: is not evidence of anything.
_MIN_CHARS = 40

_ASCII_LETTER = re.compile(r"[A-Za-z]")


def digit_companion_enabled() -> bool:
    """Whether to transliterate a detected companion's digit row. Ships ON."""

    return os.environ.get(DIGIT_COMPANION_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def devanagarize_companion_digits(text: str) -> str:
    """`0-9` -> `०-९`, leaving every other character alone.

    🛑 The decimal separator is deliberately untouched. The companion draws a LITERAL
    ASCII period -- `8500.00` renders as `८५००.००` -- so mapping `.` to `।` here, which
    is what every shipped map key would do, would be a new corruption rather than a
    repair. VOL-323 names the digit row and `.` as the only adjudicated slots; `,`
    (192,549 occurrences) and `/` (518,336) are explicitly NOT adjudicated and so are
    also left alone.
    """

    # Delegated rather than given its own translation table. It IS the same transform
    # as VOL-660's exempted-run writer, and two tables that must agree are two tables
    # that can drift -- which is exactly what `test_no_duplicated_definitions.py` caught
    # when this module carried its own copy. The distinct NAME is kept because the
    # caller's intent differs: that one is the digit half of a bracket gate, this one is
    # a whole-row transform for a face no map handles.
    return devanagarize_ascii_digits(text)


def _hex_to_bits(value: str) -> tuple[int, ...]:
    number = int(value, 16)
    return tuple(
        (number >> (_SIG_CELLS - 1 - index)) & 1 for index in range(_SIG_CELLS)
    )


@lru_cache(maxsize=8)
def _reference_bits(table: tuple[str, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(_hex_to_bits(entry) for entry in table)


def _render_glyph(font: fitz.Font, code: int):
    if not font.has_glyph(code):
        return None
    doc = fitz.open()
    try:
        page = doc.new_page(width=_RENDER_PT * 3, height=_RENDER_PT * 3)
        writer = fitz.TextWriter(page.rect)
        writer.append(
            fitz.Point(_RENDER_PT, _RENDER_PT * 2),
            chr(code),
            font=font,
            fontsize=_RENDER_PT,
        )
        writer.write_text(page)
        return page.get_pixmap(matrix=fitz.Matrix(_ZOOM, _ZOOM), colorspace=fitz.csGRAY)
    except Exception:  # noqa: BLE001 - an unrenderable glyph is an abstention, not an error
        return None
    finally:
        doc.close()


def glyph_signature(pix) -> tuple[int, ...] | None:
    """A 16x16 ink-density signature over the glyph's own ink box, or ``None``.

    Cropped to the ink box before sampling, so two faces drawing the same shape at
    different advance widths still compare equal -- which is what lets one pinned table
    serve faces from different documents and different foundries.
    """

    if pix is None:
        return None
    samples, stride, width, height = pix.samples, pix.stride, pix.width, pix.height
    top = bottom = -1
    min_x, max_x = width, -1
    for y in range(height):
        base = y * stride
        row = samples[base : base + width]
        first = last = -1
        for x, value in enumerate(row):
            if value < 128:
                if first < 0:
                    first = x
                last = x
        if first >= 0:
            if top < 0:
                top = y
            bottom = y
            min_x = min(min_x, first)
            max_x = max(max_x, last)
    if top < 0 or max_x < min_x:
        return None
    box_w, box_h = max_x - min_x + 1, bottom - top + 1
    if box_w < _SIG_SIZE or box_h < _SIG_SIZE:
        return None
    cells: list[int] = []
    for gy in range(_SIG_SIZE):
        y0 = top + gy * box_h // _SIG_SIZE
        y1 = max(top + (gy + 1) * box_h // _SIG_SIZE, y0 + 1)
        for gx in range(_SIG_SIZE):
            x0 = min_x + gx * box_w // _SIG_SIZE
            x1 = max(min_x + (gx + 1) * box_w // _SIG_SIZE, x0 + 1)
            total = count = 0
            for y in range(y0, y1):
                base = y * stride
                for x in range(x0, x1):
                    total += 1
                    if samples[base + x] < 128:
                        count += 1
            cells.append(1 if total and count * 2 >= total else 0)
    return tuple(cells)


def _hamming(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return sum(1 for a, b in zip(left, right) if a != b)


def plain_row_signatures(font_buffer: bytes) -> list[tuple[int, ...] | None]:
    """Signatures of the font's glyphs at `0x30`-`0x39`, ``None`` where unreadable."""

    try:
        font = fitz.Font(fontbuffer=font_buffer)
    except Exception:  # noqa: BLE001
        return [None] * 10
    return [glyph_signature(_render_glyph(font, 0x30 + index)) for index in range(10)]


def content_is_digit_dominant(text: str) -> bool:
    """Condition 2: is this font's aggregate text figures rather than words?"""

    non_space = [char for char in text if not char.isspace()]
    if len(non_space) < _MIN_CHARS:
        return False
    letters = sum(1 for char in non_space if _ASCII_LETTER.match(char))
    digits = sum(1 for char in non_space if char in _ASCII_DIGITS)
    return (
        letters / len(non_space) <= _MAX_ALPHA_SHARE
        and digits / len(non_space) >= _MIN_DIGIT_SHARE
    )


def glyphs_draw_devanagari_digits(font_buffer: bytes) -> bool | None:
    """Condition 3, from a font program. See :func:`decide_from_plain_row_signatures`."""

    return decide_from_plain_row_signatures(plain_row_signatures(font_buffer))


def decide_from_plain_row_signatures(
    signatures: list[tuple[int, ...] | None],
) -> bool | None:
    """Condition 3 on already-rendered signatures. ``True`` / ``False`` / ``None``.

    Split from the rendering so the DECISION can be tested without a font on the host:
    the pinned tables are themselves valid inputs, and a test that feeds
    :data:`_FAMILY_DEVANAGARI_DIGITS` must get ``True`` while one that feeds
    :data:`_EXTERNAL_LATIN_DIGITS` must get ``False``. A test that needs a real font
    file has to be skipped when the font is absent, and a skipped test proves nothing.

    ``None`` is a real answer and callers must not coerce it: a subset whose cmap is not
    Unicode-addressable carries no evidence either way, and treating that as ``False`` is
    the false negative VOL-317 warned about. The caller leaves such a face untouched,
    which is the same outcome as ``False`` but for an honest reason.
    """

    family = _reference_bits(_FAMILY_DEVANAGARI_DIGITS)

    family_matched = comparable = 0
    for index, signature in enumerate(signatures):
        if signature is None:
            continue
        comparable += 1
        if _hamming(signature, family[index]) <= _FAMILY_MATCH_MAX:
            family_matched += 1

    if comparable < _ROW_MATCH_MIN:
        return None
    return family_matched >= _ROW_MATCH_MIN


def prefers_devanagari_over_latin(
    signatures: list[tuple[int, ...] | None],
) -> bool | None:
    """Does this row look more like `०-९` or more like `0-9`, against an EXTERNAL font?

    🛑 NOT part of :func:`decide_from_plain_row_signatures`, and the reason is measured.
    It was, as a second gate, and a mutation sweep showed the clause was **unreachable**:
    every input that clears the family match already prefers Devanagari here, so
    replacing this whole comparison with ``return True`` left the suite green. An
    untested branch in a conversion path is worse than no branch -- it reads as a
    safeguard while guaranteeing nothing -- so it was removed from the decision and kept
    here, where it does the job it is actually good at.

    That job is CORROBORATING THE PINNED FAMILY TABLE. The family reference is the
    instrument's single point of failure: if
    :data:`_FAMILY_DEVANAGARI_DIGITS` were ever regenerated from the wrong row -- the
    prose face's consonants, or a Latin face -- every verdict would be confidently wrong
    and nothing within the family could tell. This compares against
    `DroidSansDevanagari-Regular`, a font with no connection to this corpus, which
    carries Latin digits at `U+0030` and Devanagari digits at `U+0966`; taking both
    references from ONE font controls for stroke weight and style.

    Read as a comparison, never a threshold: cross-typeface distances are large on both
    sides. Measured medians (Devanagari / Latin):

        companion 3653+Spins_EXT      64 / 122      Devanagari
        companion 3097+SpinsEXT       64 / 122      Devanagari
        companion 2949 CIDFont+F8     65 / 123      Devanagari
        prose 3653+Spins             106 / 110      neither, correctly
        NimbusSans                   114 /  25      Latin
        NotoSans                     111 /   8      Latin
        DejaVuSansMono               108 /  19      Latin
        Cantarell                    106 /  13      Latin
        DroidSans                    113 /   0      Latin

    ``None`` when too few glyphs are readable to compare.
    """

    external_deva = _reference_bits(_EXTERNAL_DEVANAGARI_DIGITS)
    external_latin = _reference_bits(_EXTERNAL_LATIN_DIGITS)

    deva_distances: list[int] = []
    latin_distances: list[int] = []
    for index, signature in enumerate(signatures):
        if signature is None:
            continue
        deva_distances.append(_hamming(signature, external_deva[index]))
        latin_distances.append(_hamming(signature, external_latin[index]))

    if len(deva_distances) < _ROW_MATCH_MIN:
        return None
    deva_distances.sort()
    latin_distances.sort()
    return (
        deva_distances[len(deva_distances) // 2]
        < latin_distances[len(latin_distances) // 2]
    )


def detect_digit_companion_fonts(
    doc: fitz.Document,
    skip_pages: frozenset[int] = frozenset(),
    embedded_legacy_maps: dict[str, str] | None = None,
) -> frozenset[str]:
    """Full font names whose digit row is Devanagari and which no map already handles.

    The three conditions are applied CHEAPEST FIRST, and that ordering is the reason
    this is affordable in a generation build: condition 1 is a dict lookup and condition
    2 is a character count, so a face only reaches the glyph rendering in condition 3 if
    it is already an unrouted digit-dominant face.

    ⚠️ An earlier revision quantified that as "60 of 966 distinct faces on the acceptance
    sample". Neither number is in the artifacts: `ACCEPTANCE-VOL323-51d3f79c20e2107f.json`
    reports `distinct_faces: 981` and `TPFP-...json` 968, and the 60 is `sample_pdfs`, the
    PDF count. Measured properly, over all 6,236 corpus PDFs at `main` `2f7e377` and
    counted as (document, face) pairs rather than distinct faces:

        all faces                84,230
        unrouted (condition 1)   75,102
        digit-dominant (cond 2)   2,922   <- only these reach the renderer
        fire (condition 3)          131

    So the renderer runs on 3.5% of the pairs, which is what makes this affordable in a
    generation build.
    """

    if not digit_companion_enabled():
        return frozenset()

    texts: dict[str, list[str]] = {}
    for page_number in range(1, doc.page_count + 1):
        if page_number in skip_pages:
            continue
        try:
            page = doc[page_number - 1]
            # Non-additive on purpose, exactly as in `font_based.py` and
            # `numeric_boundaries.py`: `flags=` REPLACES PyMuPDF's default word,
            # and every `TEXTFLAGS_*` default sets `TEXT_MEDIABOX_CLIP`, which
            # deletes 1,250,148 glyphs across 4,022 of 6,236 corpus documents.
            # A clipped span is a span this gate never counts, so the default
            # would let page geometry decide whether a face reads as
            # digit-dominant. See `tests/test_pymupdf_flag_words.py`.
            page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        except Exception:  # noqa: BLE001
            continue
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    name = span.get("font")
                    text = span.get("text") or ""
                    if name and text:
                        texts.setdefault(name, []).append(text)

    # Condition 1 and 2, before any rendering. An embedded-name binding is the
    # same name route as a registry match; admitting it here would let this path
    # return before the legacy decoder and shadow that binding.
    candidates = [
        name
        for name, parts in texts.items()
        if _matched_registry_key(name) is None
        and (
            not embedded_legacy_maps
            or name.split("+", 1)[-1] not in embedded_legacy_maps
        )
        and content_is_digit_dominant("".join(parts))
    ]
    if not candidates:
        return frozenset()

    buffers = _font_buffers(doc)
    companions = {
        name
        for name in candidates
        if name in buffers and glyphs_draw_devanagari_digits(buffers[name]) is True
    }
    return frozenset(companions)


def _font_buffers(doc: fitz.Document) -> dict[str, bytes]:
    """Full font name -> embedded program bytes, for every embedded font in ``doc``."""

    buffers: dict[str, bytes] = {}
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Type")[1] != "/Font":
                continue
            info = doc.extract_font(xref)
        except Exception:  # noqa: BLE001
            continue
        if not info or len(info) < 4 or not info[3]:
            continue
        name = info[0]
        if name and name not in buffers:
            buffers[name] = info[3]
    return buffers
