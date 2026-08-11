"""Unicode recovery for Lohit-Devanagari subsets that carry no usable ``cmap``.

The Nepali government PDFs in scope embed Lohit-Devanagari as an
``/Identity-H`` ``CIDFontType2`` subset, and three things are true of every
such subset at once:

* its ``ToUnicode`` CMap is wrong, so text extracts as Latin-1 mojibake --
  ``महालेखापरीक्षकको`` comes out ``महालेखापरीçकको``;
* the font's own ``cmap`` is emptied and ``GSUB`` is stripped, so the
  reconstruction in :mod:`likhit.extractors.kalimati` -- which reads exactly
  those two tables -- has nothing to work from and returns no mapping;
* glyph *order* is preserved from the upstream release, and ``/Identity-H``
  with ``CIDToGIDMap /Identity`` makes CID == GID, so a CID still identifies
  the same glyph it identifies upstream.

The last point is what makes recovery possible without OCR: one table derived
once from the upstream font decodes every subset. :data:`GID_TO_UNICODE` is
that table.

Provenance
----------
Derived from the upstream Fedora/Debian release of Lohit-Devanagari 2.5.3
(SIL Open Font License 1.1), the build these PDFs embed:

* ``name`` ID 3 ``FontForge 2.0 : Lohit Devanagari : 1-6-2013``
* ``name`` ID 5 ``Version 2.5.3``
* ``sha256`` ``fcc1bfc221ca7b610ce6f36301bd5ac79c81124bc2a70a90872048df27c6416f``
* source ``https://snapshot.debian.org/archive/debian/20130601T160308Z/pool/main/f/fonts-lohit-deva/fonts-lohit-deva_2.5.3-1_all.deb``

The table is not hand-written: it is what
``kalimati._get_font_correction_map``'s own derivation (``cmap`` reversal,
:func:`~likhit.extractors.kalimati._infer_mark_variants`, then
:func:`~likhit.extractors.kalimati._analyze_gsub`) produces when handed that
font, with one class of correction applied on top --
:data:`BELOW_FORM_RA_CORRECTIONS`. ``tests/test_lohit.py`` re-derives the table
and asserts that equality, so the two cannot drift.

``_analyze_gsub`` reads below-form (``blwf``) semantics only from single
substitutions, and Lohit builds its ra below-form with a *ligature* rule, so a
bare rakar decodes to the component order ``ra + virama`` when a rakar is
``virama + ra``. ``_analyze_gsub``'s trailing ra-virama swap repairs the
precomposed vatu ligatures (``क्र``, ``प्र``, ``त्र`` and the rest are correct
here) but cannot repair a value that *begins* with the pair, because there is no
preceding base consonant to anchor the swap. Those three glyphs are corrected;
in corpus terms this is the difference between ``स्टर्ीट`` and ``स्ट्रीट``.

Whatever this table does not cover is left alone rather than guessed at: an
uncovered CID keeps whatever the PDF's own broken CMap said, so it degrades to the
status quo rather than to a new, confident error. Of 8,093,837 Lohit glyph
instances measured across 150 corpus documents, the uncovered remainder is 127.

CID 229 (``u095E_u0930_u094D.blwf.vatu``, ``फ़्र``) was one such gap until
``_analyze_gsub``'s ra-virama swap learned to look past a nukta: its base is the
precomposed ``फ़`` (U+095E), which the swap did not recognise as a base, so the
inverted pair survived. It derives correctly now and needs no correction.

The 127 remaining instances are CID 292. This paragraph deliberately does not say
whether that one is coverable -- the branch that decodes it is separate work, and
an earlier version of this text asserted it was not, on the incorrect grounds that
it is an i-matra needing two reordering markers. If a ``GSUB_VARIANT_ADDITIONS``
dict is present below, 292 is covered and this note is spent.

Applying the table to the wrong font would silently emit confident nonsense, so
:func:`is_known_lohit_subset` gates it twice: the ``name``/``head`` records
identify the build, and :data:`_ANCHOR_OUTLINES` then proves the glyph order is
the one the table was built against by hashing glyph outlines the subset still
carries. A later Lohit release fails the first check -- 2.95.4 reports build
``17-9-2013`` and 711 glyphs -- and is left alone rather than mis-decoded.

Requiring a surviving anchor means a subset that happens to use none of them
cannot be verified and so is not repaired. Measured over 18,748 Lohit embeds in
120 corpus documents, that is 9 of them; the other 18,739 verify. Those 9 keep
their original CMap, which is the same outcome as before this module existed.
"""

from __future__ import annotations

import hashlib
from typing import Any

# ``name`` ID 3 (unique identifier) and ID 5 (version) of the build this table
# was derived from. FontForge writes the build date into ID 3, which makes the
# pair a tighter fingerprint than the version string alone.
EXPECTED_BUILD = "FontForge 2.0 : Lohit Devanagari : 1-6-2013"
EXPECTED_VERSION = "Version 2.5.3"
EXPECTED_UNITS_PER_EM = 1024
# The upstream font has this many glyphs; a subset truncates the tail but never
# reorders, so a subset may report fewer and never more.
UPSTREAM_GLYPH_COUNT = 407

# A subset must match this many anchor outlines (and mismatch none) before the
# table is trusted. One is enough to pin the glyph order: the anchors are base
# letters whose outlines differ from each other, so a shifted order mismatches
# rather than coincidentally agreeing.
_MIN_ANCHOR_MATCHES = 1

# Redeclared rather than imported from :mod:`likhit.extractors.kalimati`, which
# imports this module. ``tests/test_lohit.py`` asserts they stay identical.
_PUA_REPH = "\uf000"
_PUA_IKAR = "\uf001"
_IKAR = "\u093f"
_REPHA = "\u0930\u094d"  # ra + virama


def _is_matra(char: str) -> bool:
    """True for a Devanagari dependent vowel sign (U+093E-U+094C)."""

    return "\u093e" <= char <= "\u094c"


# ``{gid: sha256(outline)[:16]}`` for common base glyphs, used to prove the
# subset's glyph order matches the build the table came from. Truncated because
# these guard against a different *font*, not a forged one.
_ANCHOR_OUTLINES: dict[int, str] = {
    71: "ae36d2f0ad3feefa",  # क
    86: "265fca8f1ab19213",  # त
    90: "3c2b23b49d2e7057",  # न
    92: "8f49a907559b33d1",  # प
    96: "01afe738b034ffec",  # म
    97: "c5d6ab451d11c4d1",  # य
    98: "c9ed9990ca4fc8f4",  # र
    100: "bae13a39d2c6b3cc",  # ल
    106: "4e44b86feb2c8121",  # स
    112: "5c706ce30161e18e",  # ा
    114: "2d8fd9bc87f6f9f8",  # ी
    121: "429d531b034a1e8a",  # े
    125: "9db7ddf5f0d03bd0",  # ो
    152: "8805b9e4fb38fd3b",  # ०
    153: "aafe59d5c36b03df",  # १
    154: "de4a05a5d9e1bc45",  # २
}

# ``{CID: (as derived, corrected to)}`` for the below-form ra glyphs described
# in the module docstring. Recorded rather than silently folded in, so a reader
# can see every place the table departs from the mechanical derivation.
BELOW_FORM_RA_CORRECTIONS: dict[int, tuple[str, str]] = {
    227: ("\u0930\u094d", "\u094d\u0930"),  # u0930_u094D.blwf: र् -> ्र
    294: (
        "\u0930\u094d\u0941",
        "\u094d\u0930\u0941",
    ),  # u0930_u094D.blwf_u0941.blws: र्ु -> ्रु
    295: (
        "\u0930\u094d\u0942",
        "\u094d\u0930\u0942",
    ),  # u0930_u094D.blwf_u0942.blws: र्ू -> ्रू
}

# ``{CID: (source CID, value)}`` for glyphs the derivation cannot reach at all.
#
# ``_analyze_gsub`` gives up on this font -- "GSUB ligature resolution did not
# converge within 177 passes over 176 rule(s); font has conflicting ligature
# substitutions" -- so a handful of glyphs it would otherwise have named come out
# missing, among them three unnamed ones (``glyph237``/``238``/``239``) that no
# glyph name can speak for either.
#
# A ``SingleSubst`` rule names them anyway: it substitutes one glyph for another
# that *is* known, which makes the pair a positional variant carrying the same
# text. Only such pairs belong here, and each is recorded with its source so the
# test can check that the rule still exists in the reference font rather than
# trusting the value typed below.
GSUB_VARIANT_ADDITIONS: dict[int, tuple[int, str]] = {
    # lookup 82: u0940_u0930_u094D.rphf.abvs (288) -> glyph238 (292). 2,843
    # glyphs across the OAG corpus, every one of them in a document whose Lohit
    # subsets verify, which makes it the largest single gap left in this table.
    292: (288, "\u0940\u0930\u094d"),  # glyph238 -> ीर्
}

# ``{CID: Unicode}`` for Lohit-Devanagari 2.5.3. Values are the font's plain
# Unicode semantics, so that this stays a faithful record of the reference font
# and can be checked against it. :func:`with_reordering_markers` is what turns
# the visual-order marks into reordering markers, and it is applied when the map
# is handed out -- see :func:`lohit_correction_map`.
GID_TO_UNICODE: dict[int, str] = {
    2: "\u000c",  # nonmarkingreturn ->
    3: "\u0020",  # space ->
    4: "\u0021",  # exclam -> !
    5: "\u0022",  # quotedbl -> "
    6: "\u0023",  # numbersign -> #
    7: "\u0024",  # dollar -> $
    8: "\u0025",  # percent -> %
    9: "\u0026",  # ampersand -> &
    10: "\u0027",  # quotesingle -> '
    11: "\u0028",  # parenleft -> (
    12: "\u0029",  # parenright -> )
    13: "\u002a",  # asterisk -> *
    14: "\u002b",  # plus -> +
    15: "\u002c",  # comma -> ,
    16: "\u002d",  # hyphen -> -
    17: "\u002e",  # period -> .
    18: "\u002f",  # slash -> /
    19: "\u0030",  # zero -> 0
    20: "\u0031",  # one -> 1
    21: "\u0032",  # two -> 2
    22: "\u0033",  # three -> 3
    23: "\u0034",  # four -> 4
    24: "\u0035",  # five -> 5
    25: "\u0036",  # six -> 6
    26: "\u0037",  # seven -> 7
    27: "\u0038",  # eight -> 8
    28: "\u0039",  # nine -> 9
    29: "\u003a",  # colon -> :
    30: "\u003b",  # semicolon -> ;
    31: "\u003c",  # less -> <
    32: "\u003d",  # equal -> =
    33: "\u003e",  # greater -> >
    34: "\u003f",  # question -> ?
    35: "\u0040",  # at -> @
    36: "\u005b",  # bracketleft -> [
    37: "\u005c",  # backslash -> \
    38: "\u005d",  # bracketright -> ]
    39: "\u005e",  # asciicircum -> ^
    40: "\u005f",  # underscore -> _
    41: "\u0060",  # grave -> `
    42: "\u007b",  # braceleft -> {
    43: "\u007c",  # bar -> |
    44: "\u007d",  # braceright -> }
    45: "\u007e",  # asciitilde -> ~
    46: "\u00a2",  # cent -> ¢
    47: "\u00d7",  # multiply -> ×
    48: "\u00f7",  # divide -> ÷
    49: "\u02bc",  # afii57929 -> ʼ
    50: "\u0900",  # uni0900 -> ऀ
    51: "\u0901",  # u0901 -> ँ
    52: "\u0902",  # u0902 -> ं
    53: "\u0903",  # u0903 -> ः
    54: "\u0904",  # uni0904 -> ऄ
    55: "\u0905",  # u0905 -> अ
    56: "\u0906",  # u0906 -> आ
    57: "\u0907",  # u0907 -> इ
    58: "\u0908",  # u0908 -> ई
    59: "\u0909",  # u0909 -> उ
    60: "\u090a",  # u090A -> ऊ
    61: "\u090b",  # u090B -> ऋ
    62: "\u090c",  # u090C -> ऌ
    63: "\u090d",  # u090D -> ऍ
    64: "\u090e",  # u090E -> ऎ
    65: "\u090f",  # u090F -> ए
    66: "\u0910",  # u0910 -> ऐ
    67: "\u0911",  # u0911 -> ऑ
    68: "\u0912",  # u0912 -> ऒ
    69: "\u0913",  # u0913 -> ओ
    70: "\u0914",  # u0914 -> औ
    71: "\u0915",  # u0915 -> क
    72: "\u0916",  # u0916 -> ख
    73: "\u0917",  # u0917 -> ग
    74: "\u0918",  # u0918 -> घ
    75: "\u0919",  # u0919 -> ङ
    76: "\u091a",  # u091A -> च
    77: "\u091b",  # u091B -> छ
    78: "\u091c",  # u091C -> ज
    79: "\u091d",  # u091D -> झ
    80: "\u091e",  # u091E -> ञ
    81: "\u091f",  # u091F -> ट
    82: "\u0920",  # u0920 -> ठ
    83: "\u0921",  # u0921 -> ड
    84: "\u0922",  # u0922 -> ढ
    85: "\u0923",  # u0923 -> ण
    86: "\u0924",  # u0924 -> त
    87: "\u0925",  # u0925 -> थ
    88: "\u0926",  # u0926 -> द
    89: "\u0927",  # u0927 -> ध
    90: "\u0928",  # u0928 -> न
    91: "\u0929",  # u0929 -> ऩ
    92: "\u092a",  # u092A -> प
    93: "\u092b",  # u092B -> फ
    94: "\u092c",  # u092C -> ब
    95: "\u092d",  # u092D -> भ
    96: "\u092e",  # u092E -> म
    97: "\u092f",  # u092F -> य
    98: "\u0930",  # u0930 -> र
    99: "\u0931",  # u0931 -> ऱ
    100: "\u0932",  # u0932 -> ल
    101: "\u0933",  # u0933 -> ळ
    102: "\u0934",  # u0934 -> ऴ
    103: "\u0935",  # u0935 -> व
    104: "\u0936",  # u0936 -> श
    105: "\u0937",  # u0937 -> ष
    106: "\u0938",  # u0938 -> स
    107: "\u0939",  # u0939 -> ह
    108: "\u093a",  # uni093A -> ऺ
    109: "\u093b",  # uni093B -> ऻ
    110: "\u093c",  # u093C -> ़
    111: "\u093d",  # u093D -> ऽ
    112: "\u093e",  # u093E -> ा
    113: "\u093f",  # u093F -> ि
    114: "\u0940",  # u0940 -> ी
    115: "\u0941",  # u0941 -> ु
    116: "\u0942",  # u0942 -> ू
    117: "\u0943",  # u0943 -> ृ
    118: "\u0944",  # u0944 -> ॄ
    119: "\u0945",  # u0945 -> ॅ
    120: "\u0946",  # u0946 -> ॆ
    121: "\u0947",  # u0947 -> े
    122: "\u0948",  # u0948 -> ै
    123: "\u0949",  # u0949 -> ॉ
    124: "\u094a",  # u094A -> ॊ
    125: "\u094b",  # u094B -> ो
    126: "\u094c",  # u094C -> ौ
    127: "\u094d",  # u094D -> ्
    128: "\u094e",  # uni094E -> ॎ
    129: "\u094f",  # uni094F -> ॏ
    130: "\u0950",  # u0950 -> ॐ
    131: "\u0951",  # u0951 -> ॑
    132: "\u0952",  # u0952 -> ॒
    133: "\u0953",  # u0953 -> ॓
    134: "\u0954",  # u0954 -> ॔
    135: "\u0955",  # uni0955 -> ॕ
    136: "\u0956",  # uni0956 -> ॖ
    137: "\u0957",  # uni0957 -> ॗ
    138: "\u0958",  # u0958 -> क़
    139: "\u0959",  # u0959 -> ख़
    140: "\u095a",  # u095A -> ग़
    141: "\u095b",  # u095B -> ज़
    142: "\u095c",  # u095C -> ड़
    143: "\u095d",  # u095D -> ढ़
    144: "\u095e",  # u095E -> फ़
    145: "\u095f",  # u095F -> य़
    146: "\u0960",  # u0960 -> ॠ
    147: "\u0961",  # u0961 -> ॡ
    148: "\u0962",  # u0962 -> ॢ
    149: "\u0963",  # u0963 -> ॣ
    150: "\u0964",  # u0964 -> ।
    151: "\u0965",  # u0965 -> ॥
    152: "\u0966",  # u0966 -> ०
    153: "\u0967",  # u0967 -> १
    154: "\u0968",  # u0968 -> २
    155: "\u0969",  # u0969 -> ३
    156: "\u096a",  # u096A -> ४
    157: "\u096b",  # u096B -> ५
    158: "\u096c",  # u096C -> ६
    159: "\u096d",  # u096D -> ७
    160: "\u096e",  # u096E -> ८
    161: "\u096f",  # u096F -> ९
    162: "\u0970",  # u0970 -> ॰
    163: "\u0971",  # uni0971 -> ॱ
    164: "\u0972",  # uni0972 -> ॲ
    165: "\u0973",  # uni0973 -> ॳ
    166: "\u0974",  # uni0974 -> ॴ
    167: "\u0975",  # uni0975 -> ॵ
    168: "\u0976",  # uni0976 -> ॶ
    169: "\u0977",  # uni0977 -> ॷ
    170: "\u0979",  # uni0979 -> ॹ
    171: "\u097a",  # uni097A -> ॺ
    172: "\u097b",  # uni097B -> ॻ
    173: "\u097c",  # uni097C -> ॼ
    174: "\u097d",  # uni097D -> ॽ
    175: "\u097e",  # uni097E -> ॾ
    176: "\u097f",  # uni097F -> ॿ
    177: "\u200c",  # afii61664 -> ‌
    178: "\u200d",  # afii301 -> ‍
    179: "\u2013",  # endash -> –
    180: "\u2014",  # emdash -> —
    181: "\u2018",  # quoteleft -> ‘
    182: "\u2019",  # quoteright -> ’
    183: "\u201c",  # quotedblleft -> “
    184: "\u201d",  # quotedblright -> ”
    185: "\u2026",  # ellipsis -> …
    186: "\u20b9",  # uni20B9 -> ₹
    187: "\u2212",  # minus -> −
    188: "\u25cc",  # uni25CC -> ◌
    189: "\ua8e0",  # uniA8E0 -> ꣠
    190: "\ua8e1",  # uniA8E1 -> ꣡
    191: "\ua8e2",  # uniA8E2 -> ꣢
    192: "\ua8e3",  # uniA8E3 -> ꣣
    193: "\ua8e4",  # uniA8E4 -> ꣤
    194: "\ua8e5",  # uniA8E5 -> ꣥
    195: "\ua8e6",  # uniA8E6 -> ꣦
    196: "\ua8e7",  # uniA8E7 -> ꣧
    197: "\ua8e8",  # uniA8E8 -> ꣨
    198: "\ua8e9",  # uniA8E9 -> ꣩
    199: "\ua8ea",  # uniA8EA -> ꣪
    200: "\ua8eb",  # uniA8EB -> ꣫
    201: "\ua8ec",  # uniA8EC -> ꣬
    202: "\ua8ed",  # uniA8ED -> ꣭
    203: "\ua8ee",  # uniA8EE -> ꣮
    204: "\ua8ef",  # uniA8EF -> ꣯
    205: "\ua8f0",  # uniA8F0 -> ꣰
    206: "\ua8f1",  # uniA8F1 -> ꣱
    207: "\ua8f2",  # uniA8F2 -> ꣲ
    208: "\ua8f3",  # uniA8F3 -> ꣳ
    209: "\ua8f4",  # uniA8F4 -> ꣴ
    210: "\ua8f5",  # uniA8F5 -> ꣵ
    211: "\ua8f6",  # uniA8F6 -> ꣶ
    212: "\ua8f7",  # uniA8F7 -> ꣷ
    213: "\ua8f8",  # uniA8F8 -> ꣸
    214: "\ua8f9",  # uniA8F9 -> ꣹
    215: "\ua8fa",  # uniA8FA -> ꣺
    216: "\ua8fb",  # uniA8FB -> ꣻ
    217: "\u093f",  # SignI_extended_1 -> ि
    218: "\u0947\u0902",  # u0947_u0902.abvs -> ें
    219: "\u0947\u0930\u094d",  # u0947_u0930_u094D.rphf.abvs -> ेर्
    220: "\u0947\u0930\u094d\u0902",  # u0947_u0930_u094D.rphf.abvs_u0902.abvs -> ेर्ं
    221: "\u0948\u0902",  # u0948_u0902.abvs -> ैं
    222: "\u0948\u0930\u094d",  # u0948_u0930_u094D.rphf.abvs -> ैर्
    223: "\u0948\u0930\u094d\u0902",  # u0948_u0930_u094D.rphf.abvs_u0902.abvs -> ैर्ं
    224: "\u0930\u094d",  # u0930_u094D.rphf -> र्
    225: "\u0930\u094d\u0902",  # u0930_u094D.rphf_u0902.abvs -> र्ं
    227: "\u094d\u0930",  # u0930_u094D.blwf -> ्र
    228: "\u0936\u094d\u0930",  # u0936_u0930_u094D.blwf.vatu -> श्र
    229: "\u095e\u094d\u0930",  # u095E_u0930_u094D.blwf.vatu -> फ़्र
    230: "\u0924\u094d\u0924",  # u0924_u094D.half_u0924.pres -> त्त
    231: "\u0915\u094d\u0937",  # u0915_u094D_u0937.akhn -> क्ष
    232: "\u091c\u094d\u091e",  # u091C_u094D_u091E.akhn -> ज्ञ
    233: "\u0915\u094d",  # u0915_u094D.half -> क्
    234: "\u0916\u094d",  # u0916_u094D.half -> ख्
    235: "\u0917\u094d",  # u0917_u094D.half -> ग्
    236: "\u0918\u094d",  # u0918_u094D.half -> घ्
    237: "\u0919\u094d",  # u0919_u094D.half -> ङ्
    238: "\u091a\u094d",  # u091A_u094D.half -> च्
    239: "\u091b\u094d",  # u091B_u094D.half -> छ्
    240: "\u091c\u094d",  # u091C_u094D.half -> ज्
    241: "\u091d\u094d",  # u091D_u094D.half -> झ्
    242: "\u091e\u094d",  # u091E_u094D.half -> ञ्
    243: "\u091f\u094d",  # u091F_u094D.half -> ट्
    244: "\u0920\u094d",  # u0920_u094D.half -> ठ्
    245: "\u0921\u094d",  # u0921_u094D.half -> ड्
    246: "\u0922\u094d",  # u0922_u094D.half -> ढ्
    247: "\u0923\u094d",  # u0923_u094D.half -> ण्
    248: "\u0924\u094d",  # u0924_u094D.half -> त्
    249: "\u0925\u094d",  # u0925_u094D.half -> थ्
    250: "\u0926\u094d",  # u0926_u094D.half -> द्
    251: "\u0927\u094d",  # u0927_u094D.half -> ध्
    252: "\u0928\u094d",  # u0928_u094D.half -> न्
    253: "\u0929\u094d",  # u0929_u094D.half -> ऩ्
    254: "\u092a\u094d",  # u092A_u094D.half -> प्
    255: "\u092b\u094d",  # u092B_u094D.half -> फ्
    256: "\u092c\u094d",  # u092C_u094D.half -> ब्
    257: "\u092d\u094d",  # u092D_u094D.half -> भ्
    258: "\u092e\u094d",  # u092E_u094D.half -> म्
    259: "\u092f\u094d",  # u092F_u094D.half -> य्
    260: "\u093f",  # glyph206 -> ि
    262: "\u0932\u094d",  # u0932_u094D.half -> ल्
    263: "\u0933\u094d",  # u0933_u094D.half -> ळ्
    264: "\u0934\u094d",  # u0934_u094D.half -> ऴ्
    265: "\u0935\u094d",  # u0935_u094D.half -> व्
    266: "\u0936\u094d",  # u0936_u094D.half -> श्
    267: "\u0937\u094d",  # u0937_u094D.half -> ष्
    268: "\u0938\u094d",  # u0938_u094D.half -> स्
    269: "\u0939\u094d",  # u0939_u094D.half -> ह्
    270: "\u0958\u094d",  # u0958_u094D.half -> क़्
    271: "\u0959\u094d",  # u0959_u094D.half -> ख़्
    272: "\u095a\u094d",  # u095A_u094D.half -> ग़्
    273: "\u095b\u094d",  # u095B_u094D.half -> ज़्
    274: "\u095e\u094d",  # u095E_u094D.half -> फ़्
    275: "\u0936\u094d\u0930\u094d",  # u0936_u094D.half_u0930_u094D.blwf.vatu -> श्र्
    276: "\u0924\u094d\u0930\u094d",  # u0924_u094D.half_u0930_u094D.blwf.vatu -> त्र्
    277: "\u0924\u094d\u0924\u094d",  # u0924_u094D.half_u0924_u094D.half.pres -> त्त्
    278: "\u0915\u094d\u0937\u094d",  # u0915_u094D_u0937.akhn_u094D.half -> क्ष्
    279: "\u091c\u094d\u091e\u094d",  # u091C_u094D_u091E.akhn_u094D.half -> ज्ञ्
    280: "\u093f",  # glyph226 -> ि
    281: "\u093f",  # glyph227 -> ि
    282: "\u093f",  # glyph228 -> ि
    283: "\u093f",  # glyph229 -> ि
    284: "\u093f",  # glyph230 -> ि
    285: "\u093f",  # glyph231 -> ि
    286: "\u093f",  # glyph232 -> ि
    287: "\u0940\u0902",  # u0940_u0902.abvs -> ीं
    288: "\u0940\u0930\u094d",  # u0940_u0930_u094D.rphf.abvs -> ीर्
    289: "\u0940\u0930\u094d\u0902",  # u0940_u0930_u094D.rphf.abvs_u0902.abvs -> ीर्ं
    290: "\u0940",  # glyph236 -> ी
    292: "\u0940\u0930\u094d",  # glyph238 -> ीर् (SingleSubst variant of 288)
    294: "\u094d\u0930\u0941",  # u0930_u094D.blwf_u0941.blws -> ्रु
    295: "\u094d\u0930\u0942",  # u0930_u094D.blwf_u0942.blws -> ्रू
    296: "\u0941",  # glyph242 -> ु
    297: "\u0942",  # glyph243 -> ू
    298: "\u0941",  # glyph244 -> ु
    300: "\u0931\u094d",  # u0931_u094D.half -> ऱ्
    301: "\u0915\u094d\u0930",  # u0915_u0930_u094D.blwf.vatu -> क्र
    302: "\u0916\u094d\u0930",  # u0916_u0930_u094D.blwf.vatu -> ख्र
    303: "\u0917\u094d\u0930",  # u0917_u0930_u094D.blwf.vatu -> ग्र
    304: "\u091c\u094d\u0930",  # u091C_u0930_u094D.blwf.vatu -> ज्र
    305: "\u091d\u094d\u0930",  # u091D_u0930_u094D.blwf.vatu -> झ्र
    306: "\u0924\u094d\u0930",  # u0924_u0930_u094D.blwf.vatu -> त्र
    307: "\u0926\u094d\u0930",  # u0926_u0930_u094D.blwf.vatu -> द्र
    308: "\u092a\u094d\u0930",  # u092A_u0930_u094D.blwf.vatu -> प्र
    309: "\u092b\u094d\u0930",  # u092B_u0930_u094D.blwf.vatu -> फ्र
    310: "\u092c\u094d\u0930",  # u092C_u0930_u094D.blwf.vatu -> ब्र
    311: "\u092d\u094d\u0930",  # u092D_u0930_u094D.blwf.vatu -> भ्र
    312: "\u092e\u094d\u0930",  # u092E_u0930_u094D.blwf.vatu -> म्र
    313: "\u0935\u094d\u0930",  # u0935_u0930_u094D.blwf.vatu -> व्र
    314: "\u0938\u094d\u0930",  # u0938_u0930_u094D.blwf.vatu -> स्र
    315: "\u0939\u094d\u0930",  # u0939_u0930_u094D.blwf.vatu -> ह्र
    316: "\u0936\u0943",  # glyph263 -> शृ
    318: "\u0915\u094d\u0924",  # u0915_u094D.half_u0924.pres -> क्त
    321: "\u0917\u094d\u0928",  # u0917_u094D.half_u0928.pres -> ग्न
    322: "\u0919\u094d\u0915",  # u0919_u094D.half_u0915.pres -> ङ्क
    323: "\u0919\u094d\u0916",  # u0919_u094D.half_u0916.pres -> ङ्ख
    324: "\u0919\u094d\u0917",  # u0919_u094D.half_u0917.pres -> ङ्ग
    325: "\u0919\u094d\u0918",  # u0919_u094D.half_u0918.pres -> ङ्घ
    326: "\u0919\u094d\u092e",  # u0919_u094D.half_u092E.pres -> ङ्म
    327: "\u0919\u094d\u0915\u094d\u0937",  # u0919_u094D.half_u0915_u094D_u0937.akhn.pres -> ङ्क्ष
    329: "\u091b\u094d\u0935",  # u091B_u094D.half_u0935.pres -> छ्व
    331: "\u091e\u094d\u091a",  # u091E_u094D.half_u091A.pres -> ञ्च
    332: "\u091e\u094d\u091c",  # u091E_u094D.half_u091C.pres -> ञ्ज
    333: "\u091f\u094d\u091f",  # u091F_u094D.half_u091F.pres -> ट्ट
    334: "\u091f\u094d\u0920",  # u091F_u094D.half_u0920.pres -> ट्ठ
    335: "\u091f\u094d\u092f",  # u091F_u094D.half_u092F.pres -> ट्य
    336: "\u091f\u094d\u0935",  # u091F_u094D.half_u0935.pres -> ट्व
    337: "\u0920\u094d\u0920",  # u0920_u094D.half_u0920.pres -> ठ्ठ
    338: "\u0920\u094d\u092f",  # u0920_u094D.half_u092F.pres -> ठ्य
    339: "\u0921\u094d\u0921",  # u0921_u094D.half_u0921.pres -> ड्ड
    340: "\u0921\u094d\u0922",  # u0921_u094D.half_u0922.pres -> ड्ढ
    341: "\u0921\u094d\u092f",  # u0921_u094D.half_u092F.pres -> ड्य
    342: "\u0922\u094d\u0922",  # u0922_u094D.half_u0922.pres -> ढ्ढ
    343: "\u0922\u094d\u092f",  # u0922_u094D.half_u092F.pres -> ढ्य
    345: "\u0926\u094d\u0918",  # u0926_u094D.half_u0918.pres -> द्घ
    346: "\u0926\u094d\u0926",  # u0926_u094D.half_u0926.pres -> द्द
    347: "\u0926\u094d\u0927",  # u0926_u094D.half_u0927.pres -> द्ध
    349: "\u0926\u094d\u092c",  # u0926_u094D.half_u092C.pres -> द्ब
    350: "\u0926\u094d\u092d",  # u0926_u094D.half_u092D.pres -> द्भ
    351: "\u0926\u094d\u092e",  # u0926_u094D.half_u092E.pres -> द्म
    352: "\u0926\u094d\u092f",  # u0926_u094D.half_u092F.pres -> द्य
    353: "\u0926\u094d\u0935",  # u0926_u094D.half_u0935.pres -> द्व
    354: "\u0928\u094d\u0928",  # u0928_u094D.half_u0928.pres -> न्न
    355: "\u092a\u094d\u0924",  # u092A_u094D.half_u0924.pres -> प्त
    358: "\u0932\u094d\u0932",  # u0932_u094D.half_u0932.pres -> ल्ल
    359: "\u0936\u094d\u0930\u094d\u091a",  # u0936_u094D.half_u0930_u094D.blwf.vatu_u091A.pres -> श्र्च
    360: "\u0936\u094d\u0930\u094d\u0928",  # u0936_u094D.half_u0930_u094D.blwf.vatu_u0928.pres -> श्र्न
    361: "\u0936\u094d\u0930\u094d\u0932",  # u0936_u094D.half_u0930_u094D.blwf.vatu_u0932.pres -> श्र्ल
    362: "\u0936\u094d\u0930\u094d\u0935",  # u0936_u094D.half_u0930_u094D.blwf.vatu_u0935.pres -> श्र्व
    363: "\u0937\u094d\u091f",  # u0937_u094D.half_u091F.pres -> ष्ट
    364: "\u0937\u094d\u0920",  # u0937_u094D.half_u0920.pres -> ष्ठ
    365: "\u0939\u094d\u0923",  # u0939_u094D.half_u0923.pres -> ह्ण
    366: "\u0939\u094d\u0928",  # u0939_u094D.half_u0928.pres -> ह्न
    367: "\u0939\u094d\u092e",  # u0939_u094D.half_u092E.pres -> ह्म
    368: "\u0939\u094d\u092f",  # u0939_u094D.half_u092F.pres -> ह्य
    369: "\u0939\u094d\u0932",  # u0939_u094D.half_u0932.pres -> ह्ल
    370: "\u0939\u094d\u0935",  # u0939_u094D.half_u0935.pres -> ह्व
    371: "\u0938\u094d\u0924\u094d\u0930",  # u0938_u094D.half_u0924_u0930_u094D.blwf.vatu.pres -> स्त्र
    372: "\u091c\u094d\u091c\u094d",  # u091C_u094D.half_u091C_u094D.half.pres -> ज्ज्
    373: "\u0926\u0943",  # u0926_u0943.blws -> दृ
    374: "\u0930\u0941",  # u0930_u0941.psts -> रु
    375: "\u0930\u0942",  # u0930_u0942.psts -> रू
    376: "\u0939\u0943",  # u0939_u0943.blws -> हृ
    378: "\u0918\u094d\u0930",  # u0918_u0930_u094D.blwf.vatu -> घ्र
    379: "\u091a\u094d\u0930",  # u091A_u0930_u094D.blwf.vatu -> च्र
    380: "\u0925\u094d\u0930",  # u0925_u0930_u094D.blwf.vatu -> थ्र
    381: "\u0927\u094d\u0930",  # u0927_u0930_u094D.blwf.vatu -> ध्र
    382: "\u0928\u094d\u0930",  # u0928_u0930_u094D.blwf.vatu -> न्र
    383: "\u0932\u094d\u0930",  # u0932_u0930_u094D.blwf.vatu -> ल्र
    384: "\u0919\u094d\u0915\u094d",  # u0919_u094D.half_u0915_u094D.half.half -> ङ्क्
    385: "\u094b\u0902",  # u094B_u0902.abvs -> ों
    386: "\u094b\u0930\u094d",  # u094B_u0930_u094D.rphf.abvs -> ोर्
    387: "\u094b\u0930\u094d\u0902",  # u094B_u0930_u094D.rphf.abvs_u0902.abvs -> ोर्ं
    388: "\u094c\u0902",  # u094C_u0902.abvs -> ौं
    389: "\u094c\u0930\u094d",  # u094C_u0930_u094D.rphf.abvs -> ौर्
    390: "\u094c\u0930\u094d\u0902",  # u094C_u0930_u094D.rphf.abvs_u0902.abvs -> ौर्ं
    391: "\u091e\u094d\u0930",  # u091E_u0930_u094D.blwf.vatu -> ञ्र
    392: "\u0923\u094d\u0930",  # u0923_u0930_u094D.blwf.vatu -> ण्र
    393: "\u092f\u094d\u0930",  # u092F_u0930_u094D.blwf.vatu -> य्र
    394: "\u0937\u094d\u0930",  # u0937_u0930_u094D.blwf.vatu -> ष्र
    395: "\u0915\u094d\u0937\u094d\u0930",  # u0915_u094D_u0937.akhn_u0930_u094D.blwf.vatu -> क्ष्र
    396: "\u091c\u094d\u091e\u094d\u0930",  # u091C_u094D_u091E.akhn_u0930_u094D.blwf.vatu -> ज्ञ्र
    397: "\u0915\u094d\u0930",  # npu0915_u0930_u094D.blwf.vatu -> क्र
    398: "\u096e",  # np8 -> ८
    399: "\u096b",  # np5 -> ५
    401: "\u0926\u094d\u0927\u094d\u0930\u094d\u092f",  # lig_0926_0927_0930_092f -> द्ध्र्य
    402: "\u091d",  # nepali_jha_091d -> झ
    403: "\u091d\u094d",  # nepali_jha_half -> झ्
    404: "\u0932",  # mr_la -> ल
    405: "\u0936",  # mr_sha -> श
    406: "\u0936\u094d",  # mr_sha_half -> श्
}


def _name_record(font: Any, name_id: int) -> str | None:
    """Return the first readable ``name`` table record for ``name_id``."""

    try:
        records = font["name"].names
    except Exception:  # noqa: BLE001 - a malformed name table is just "unknown"
        return None
    for record in records:
        if record.nameID != name_id:
            continue
        try:
            return record.toUnicode()
        except Exception:  # noqa: BLE001 - skip undecodable records, try the next
            continue
    return None


def _outline_digest(font: Any, glyph_name: str) -> str | None:
    """Hash a glyph's contours, or ``None`` if it has none.

    A subset blanks the glyphs it does not use, so "no contours" is the normal
    way an anchor goes missing and must not read as a mismatch.
    """

    try:
        glyph = font["glyf"][glyph_name]
        if glyph.numberOfContours == 0:
            return None
        coordinates, end_points, _flags = glyph.getCoordinates(font["glyf"])
    except Exception:  # noqa: BLE001 - unreadable glyph proves nothing either way
        return None

    digest = hashlib.sha256()
    digest.update(repr(list(coordinates)).encode())
    digest.update(repr(list(end_points)).encode())
    return digest.hexdigest()[:16]


def is_known_lohit_subset(font: Any) -> bool:
    """True if ``font`` is the Lohit-Devanagari build :data:`GID_TO_UNICODE` maps.

    Checks identity before glyph order: the ``name``/``head`` records reject
    another release cheaply, then the surviving anchor outlines prove the glyph
    order really is the one the table was built against.
    """

    if _name_record(font, 3) != EXPECTED_BUILD:
        return False
    if _name_record(font, 5) != EXPECTED_VERSION:
        return False
    try:
        if font["head"].unitsPerEm != EXPECTED_UNITS_PER_EM:
            return False
        glyph_count = font["maxp"].numGlyphs
    except Exception:  # noqa: BLE001 - can't identify it, so don't claim it
        return False
    if glyph_count > UPSTREAM_GLYPH_COUNT:
        # More glyphs than upstream means this is not a subset of that build.
        return False

    glyph_order = font.getGlyphOrder()
    matches = 0
    for gid, expected_digest in _ANCHOR_OUTLINES.items():
        if gid >= len(glyph_order):
            continue
        digest = _outline_digest(font, glyph_order[gid])
        if digest is None:
            continue
        if digest != expected_digest:
            return False
        matches += 1
    return matches >= _MIN_ANCHOR_MATCHES


def with_reordering_markers(value: str) -> str:
    """Replace a glyph's visual-order marks with reordering markers.

    A PDF content stream lists glyphs in the order they are *drawn*, so a mark
    that renders before or above its base is emitted before or after it
    positionally rather than in the logical order Unicode requires: the i-matra
    ``ि`` is drawn to the left of the consonant it belongs to, and the repha
    ``र्`` is drawn over the end of the cluster it opens. Decoding a glyph to
    plain Unicode therefore yields ``प्रादेिशक`` where the word is
    ``प्रादेशिक``.

    :func:`likhit.extractors.kalimati.reorder_devanagari` already moves both
    marks into logical order, but it acts on the private-use markers rather than
    on the plain characters -- it cannot tell a repha glyph from a literal
    ``र`` + virama otherwise. So substitute the markers here.

    ``kalimati._patch_single_cmap`` derives the same markers for a font whose
    own ``cmap`` survived, but each of its rules is conditioned on the value the
    PDF's *broken* CMap supplied: the i-matra rule is skipped whenever that CMap
    happens to already say ``ि`` (true for roughly a third of these subsets),
    and the matra-plus-repha rule both requires the broken value to begin with a
    matra and then keeps that broken matra. Neither holds for a mapping that
    came from a reference table, so the markers are applied here instead, where
    the value is known to be correct and the stream known to be visual order.
    """

    if value == _IKAR:
        return _PUA_IKAR
    if value.startswith(_REPHA):
        # A leading ra + virama is a repha: it opens the cluster it is drawn
        # over. Covers the bare repha and the repha-plus-anusvara glyph.
        return _PUA_REPH + value[len(_REPHA) :]
    if len(value) == len(_REPHA) + 1 and value.endswith(_REPHA) and _is_matra(value[0]):
        # A single matra carrying a repha, e.g. `ेर्`. The matra stays where it
        # is and only the repha moves.
        return value[0] + _PUA_REPH
    # Anything else is left alone. In particular a *trailing* ra + virama after
    # a consonant is a half-form awaiting the next consonant (`त्र्`, `श्र्`),
    # not a repha, and must not be moved.
    return value


def lohit_correction_map(font: Any) -> dict[int, str]:
    """``{CID: Unicode}`` for ``font``, or empty if it is not the known build.

    Values carry reordering markers where the glyph is a visual-order mark; see
    :func:`with_reordering_markers`.

    Entries beyond the subset's glyph count are dropped: those CIDs cannot
    appear in its content streams, and keeping them would bloat every rewritten
    ToUnicode CMap with unreachable ranges.
    """

    if not is_known_lohit_subset(font):
        return {}
    try:
        glyph_count = font["maxp"].numGlyphs
    except Exception:  # noqa: BLE001 - guarded above, but never raise from here
        return {}
    return {
        gid: with_reordering_markers(value)
        for gid, value in GID_TO_UNICODE.items()
        if gid < glyph_count
    }
