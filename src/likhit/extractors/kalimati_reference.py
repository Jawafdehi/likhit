"""Unicode recovery for Kalimati subsets whose ``GSUB`` table was stripped.

The Nepali government PDFs in scope embed Kalimati as an ``/Identity-H``
``CIDFontType2`` subset whose ``ToUnicode`` CMap is wrong, and the reconstruction
in :mod:`likhit.extractors.kalimati` normally repairs that by reading the font's
own ``cmap`` and ``GSUB``. On these subsets it only half works: ``cmap``
survives, so base letters resolve, but ``GSUB`` is gone -- so every conjunct and
half-form, which is exactly what ``GSUB`` encodes, stays unmapped. Those glyphs
end up marked into the private-use range by
:func:`~likhit.extractors.font_based.get_cid_marked_page_dict`, which is honest
but unreadable: ``लेखापरीक्षण`` keeps a hole where ``क्ष`` belongs.

A reference font supplies what the subset dropped, as in
:mod:`likhit.extractors.lohit`. What differs is the key.

Keyed on outlines, not glyph IDs
--------------------------------
:mod:`~likhit.extractors.lohit`'s table is keyed on CID, which works because
every Lohit subset in the corpus descends from one upstream build, so a CID
identifies the same glyph everywhere. Kalimati has no such single order. The
corpus carries two lineages at once -- the damaged subsets top out at 751 glyphs
and appear as tail truncations of that order (750, 646, 625, 620, 585), while
every ``GSUB``-bearing Kalimati reports 696 or 699 -- and the two orders
disagree: at glyph 106 they carry different drawings. A CID-keyed table derived
from the reference would therefore decode the cohort *confidently and wrongly*,
which is the one failure mode this corpus cannot absorb.

The damaged subsets also cannot be identified the way
:func:`~likhit.extractors.lohit.is_known_lohit_subset` identifies its font: the
dominant one is stripped down to ``cmap glyf head hhea hmtx loca maxp`` with no
``name`` table at all, so there is no build to recognise.

Keying on the glyph *outline* answers both. An outline identifies a glyph design
whatever CID a subsetter gave it, so the table is order-independent, and it needs
no font-identity guard: a font that is not this design does not match, because
matching means its contours are identical coordinate for coordinate. That also
makes the guard stricter than a name check rather than weaker -- a renamed or
re-versioned build with the same drawings still decodes correctly, and a
different drawing is refused even if it claims to be Kalimati.

Provenance
----------
The table is not hand-written. It is what
:func:`~likhit.extractors.kalimati._get_font_correction_map`'s own derivation
produces -- ``cmap`` reversal,
:func:`~likhit.extractors.kalimati._infer_mark_variants`, then
:func:`~likhit.extractors.kalimati._analyze_gsub` -- when handed one reference
font, re-keyed from CID to outline digest. That font is an embed carried by a
corpus document -- the most complete one found, though still a subset:

* ``name`` ID 1 ``Kalimati``, ID 3 ``Mangal Regular``, ID 5 ``Version 1.20``
* 696 glyphs, 2048 units per em, ``cmap``, ``post`` and ``GSUB`` intact
* 555 of its 696 glyphs still carry outlines, the rest having been blanked by
  the subsetter
* ``sha256`` ``113aeb3236fb18fd0a0ea85eea07de5630b6d5646b7b8b542e5f551d8217cb61``
* extracted from ``pdfs/local-level-report/6842__qGJhQ1717999883भक्तपुर नगरपालिका, २०८१.pdf``

``tests/test_kalimati_reference.py`` re-derives the table and asserts equality
when ``LIKHIT_KALIMATI_REFERENCE_TTF`` points at a copy, so table and reference
cannot drift. The seed is restricted to ``cmap`` entries in the Devanagari block:
several fonts in this corpus are legacy-encoded and map ASCII to Devanagari
glyphs, and an unrestricted seed records a Devanagari outline as ``3`` or ``k``.

Why 511 entries and not 518
---------------------------
The derivation assigns a value to 518 glyph IDs, and every one of those 518 has
an outline -- blanking costs nothing here, because a glyph the subsetter blanked
was never resolved in the first place. The 7 that do not become table entries are
lost to the re-keying, not to the font:

* **4** are the ambiguous ra cluster. One outline is shared by CIDs 298, 334, 370
  and 406, which the derivation values ``रर्``, ``र्र्``, ``ऱ्र`` and ``ऱ्र्``. An outline
  that two CIDs disagree about is dropped rather than resolved by preference.
* **3** are duplicate-outline collapses, where two CIDs share an outline and
  *agree* on its value, so the pair yields one entry: ``three``/``uni0969``,
  ``nine``/``uni096F``, and ``glyph00212``/``glyph00620``.

518 - 4 - 3 = 511. Blanking explains 696 -> 555; it explains none of 518 -> 511.

What it recovers, and what it does not
--------------------------------------
Measured over a mark-weighted sample of the affected cohort -- 19,337 marked
glyph instances drawn proportional to how much damage each document carries --
the table resolves 73.75% of them. End to end that halves the damage: over 20
sampled documents the marked glyphs fall 54.7% and the share of Devanagari words
carrying any damage goes 14.08% to 7.74%, with no document getting worse.

The rest cannot be recovered from these documents at all, and not for want of
looking. Searching 700 corpus PDFs for the eight commonest survivors finds each
of them in 100 to 136 embeds -- and every one of those embeds has no ``GSUB``, no
``cmap`` entry for the glyph, and a ``ToUnicode`` that maps it to U+0000. The
publisher zeroes exactly the conjuncts, which is the same thing
:mod:`likhit.extractors.lohit` found. Two of the eight are ``प्र`` and ``क्ष``,
which is why the improvement is large but not complete. They keep their marks and
so degrade to the status quo rather than to a confident error.

Two independent checks argue the outline match means what it claims. Where a
damaged subset's own ``cmap`` already resolves a Devanagari glyph -- which needs
no repair, and so was not used to build anything -- the reference agrees on all
206 of them. And the values recovered are precisely the class ``GSUB`` encodes:
half-forms and matra variants, ``र्``, ``ि``, ``क्ष``, ``म्``.

One outline in the reference carries more than one value: the ra cluster
(रर्, र्र्, ऱ्र, ऱ्र्). It is dropped rather than resolved by preference, on the same
reasoning as everything else here.

Below-form ra
-------------
:data:`BELOW_FORM_RA_CORRECTIONS` records the one place the table departs from the
mechanical derivation, for the reason
:data:`likhit.extractors.lohit.BELOW_FORM_RA_CORRECTIONS` documents:
``_analyze_gsub`` gives every ``rphf`` output the repha value ``र्``, and this
font reaches its below-form ra that way too. Left alone, a rakar would be handed
out as a repha, and ``kalimati.reorder_devanagari`` moves a repha to the front of
its cluster -- so ``प्र`` would come back as ``र्प``: not a hole but a different
word, which is the one failure this corpus cannot absorb.

The two are told apart by geometry rather than by hand: a repha is drawn above
the cluster it opens, a rakar hangs below the base it attaches to, so a glyph
whose outline lies entirely below the baseline is a below-form.

In-line ra
----------
Geometry distinguishes *three* cases here, not two, and reading it as two was a
defect. Four outlines in this font carry a value beginning ``र्``:

===== ================== ==========================================
 CID   y extent           reading
===== ================== ==========================================
 91    1433-2092          above the headline, hanging left -> repha
 588   1433-2092          repha + candrabindu
 589   1247-2092          repha + anusvara
 226   629-1434           in the body of the line -> **not** a repha
 89    below the baseline rakar, see above
===== ================== ==========================================

CID 226 sits in an unmistakable half-form run -- 224 ``म्``, 225 ``य्``, 226
``र्``, 227 ``ल्``, 229 ``व्`` -- and is the in-line half-form of ra, reached
through the ``half`` feature. Its Unicode value is genuinely ``ra + virama``, the
same string a repha decodes to, so the value alone cannot tell them apart; what
differs is whether the glyph needs *reordering*. A repha is drawn over the end of
the cluster it opens, so it must be moved to the front; an in-line half-form is
already in logical order, so moving it turns ``प्र`` into ``र्प``. Because
:func:`~likhit.extractors.lohit.with_reordering_markers` keys on the value, it
would hand both the repha marker.

:data:`IN_LINE_RA_DIGESTS` records the outlines exempt from that marker, and
:func:`in_line_ra_cids` is how a caller applies the exemption. An earlier version
of this docstring read 627-1434 as "well above" the baseline, which is true and
beside the point: what matters is whether the glyph sits above the *headline*, and
this one does not.

Values are plain Unicode. Turning the visual-order marks into reordering markers
is the caller's job -- see
:func:`~likhit.extractors.lohit.with_reordering_markers`, which
:mod:`likhit.extractors.kalimati` applies when it merges this map -- so that this
module stays a faithful record of the reference font.
"""

from __future__ import annotations

import hashlib
from collections.abc import Container
from typing import Any

# The reference font's em square. Outline digests are computed from raw
# font-unit coordinates, so they are only comparable at this value.
REFERENCE_UNITS_PER_EM = 2048

# The reference font reports this many glyphs. Recorded for provenance
# only: nothing here is keyed on glyph count, which is the point.
REFERENCE_GLYPH_COUNT = 696

# ``{outline digest: Unicode}``, ordered by the reference font's own CID so
# the table reads in glyph order. The comment on each line records where
# the value came from: ``cmap`` for the font's own character map,
# ``gsub`` for a substitution rule, ``inferred`` for a mark variant
# matched on metrics by ``kalimati._infer_mark_variants``.
OUTLINE_TO_UNICODE: dict[str, str] = {
    "56c3d387b5fbf1f8": "\u0966",  # cid 18 zero (gsub) -> ०  devanagari digit zero
    "86594a0bde88c8fa": "\u0967",  # cid 19 one (gsub) -> १  devanagari digit one
    "f8043142b8eca1f2": "\u0968",  # cid 20 two (gsub) -> २  devanagari digit two
    "8ba14b9f236112e1": "\u096a",  # cid 22 four (gsub) -> ४  devanagari digit four
    "96dd4fff89330881": "\u096b",  # cid 23 five (gsub) -> ५  devanagari digit five
    "38088e962eae6da3": "\u096c",  # cid 24 six (gsub) -> ६  devanagari digit six
    "964ab0e22c793cba": "\u096d",  # cid 25 seven (gsub) -> ७  devanagari digit seven
    "0b16225449039091": "\u096e",  # cid 26 eight (gsub) -> ८  devanagari digit eight
    "474526cbf56139e6": "\u0964",  # cid 64 uni0964 (cmap) -> ।  devanagari danda
    "7577c8af4c7079c4": "\u0966",  # cid 66 uni0966 (cmap) -> ०  devanagari digit zero
    "ade22b2c60f4359d": "\u0967",  # cid 67 uni0967 (cmap) -> १  devanagari digit one
    "2cc92db6667938a1": "\u0968",  # cid 68 uni0968 (cmap) -> २  devanagari digit two
    "25d681333454fe99": "\u0969",  # cid 69 uni0969 (cmap) -> ३  devanagari digit three
    "e3b822c2490eeb46": "\u096a",  # cid 70 uni096A (cmap) -> ४  devanagari digit four
    "ec3183635c2f75f4": "\u096b",  # cid 71 uni096B (cmap) -> ५  devanagari digit five
    "b279fa4e2f5fa615": "\u096c",  # cid 72 uni096C (cmap) -> ६  devanagari digit six
    "84babfeb6bbbf462": "\u096d",  # cid 73 uni096D (cmap) -> ७  devanagari digit seven
    "c30b8723c5b97932": "\u096e",  # cid 74 uni096E (cmap) -> ८  devanagari digit eight
    "19214b4fbb565ccf": "\u096f",  # cid 75 uni096F (cmap) -> ९  devanagari digit nine
    "fdd71b55b62e5722": "\u093c",  # cid 80 uni093C (cmap) -> ़  devanagari sign nukta
    "0dc2b3475e673bf0": "\u094d",  # cid 81 uni094D (cmap) -> ्  devanagari sign virama
    "810a9d62dd21e441": "\u0901",  # cid 85 glyph00085 (gsub) -> ँ  devanagari sign candrabindu
    "25c6cdf6e65128e3": "\u094d\u0930",  # cid 89 glyph00089 (gsub) -> ्र  devanagari sign virama + devanagari letter ra
    "eb5afb4e3036ce53": "\u0930\u094d",  # cid 91 glyph00091 (gsub) -> र्  devanagari letter ra + devanagari sign virama
    "7f1da96741ef7c8e": "\u0905",  # cid 92 uni0905 (cmap) -> अ  devanagari letter a
    "235f306084060d76": "\u0906",  # cid 93 uni0906 (cmap) -> आ  devanagari letter aa
    "dcdcd38f36530956": "\u0907",  # cid 94 uni0907 (cmap) -> इ  devanagari letter i
    "5c50af25f49401eb": "\u0908",  # cid 95 uni0908 (cmap) -> ई  devanagari letter ii
    "94168be194dab946": "\u0909",  # cid 96 uni0909 (cmap) -> उ  devanagari letter u
    "259f84cf0ac969c4": "\u090b",  # cid 98 uni090B (cmap) -> ऋ  devanagari letter vocalic r
    "cd126dfb06cd56fb": "\u090f",  # cid 102 uni090F (cmap) -> ए  devanagari letter e
    "33b21ba31eefe2d8": "\u0910",  # cid 103 uni0910 (cmap) -> ऐ  devanagari letter ai
    "e12ace433287c5a6": "\u0913",  # cid 106 uni0913 (cmap) -> ओ  devanagari letter o
    "3efdd6a4c92a9f5f": "\u0914",  # cid 107 uni0914 (cmap) -> औ  devanagari letter au
    "1f71cde7dc9f1bcc": "\u0915",  # cid 128 uni0915 (cmap) -> क  devanagari letter ka
    "aa62f60522f9bda0": "\u0916",  # cid 129 uni0916 (cmap) -> ख  devanagari letter kha
    "61acc71fbe6234e7": "\u0917",  # cid 130 uni0917 (cmap) -> ग  devanagari letter ga
    "3af6f96008408a6f": "\u0918",  # cid 131 uni0918 (cmap) -> घ  devanagari letter gha
    "f9de24606ca63ebf": "\u0919",  # cid 132 uni0919 (cmap) -> ङ  devanagari letter nga
    "b67488b013058b13": "\u091a",  # cid 133 uni091A (cmap) -> च  devanagari letter ca
    "0714254897ab778c": "\u091b",  # cid 134 uni091B (cmap) -> छ  devanagari letter cha
    "e70cff3b3acefa56": "\u091c",  # cid 135 uni091C (cmap) -> ज  devanagari letter ja
    "4fa83569d2291fb1": "\u091d",  # cid 136 uni091D (cmap) -> झ  devanagari letter jha
    "1aadbdb4a55c1fb2": "\u091f",  # cid 138 uni091F (cmap) -> ट  devanagari letter tta
    "fbcafac72ec2fed3": "\u0920",  # cid 139 uni0920 (cmap) -> ठ  devanagari letter ttha
    "298e330acdaa2b34": "\u0921",  # cid 140 uni0921 (cmap) -> ड  devanagari letter dda
    "8d81f9b5ed6f68bc": "\u0922",  # cid 141 uni0922 (cmap) -> ढ  devanagari letter ddha
    "e4347660372def57": "\u0923",  # cid 142 uni0923 (cmap) -> ण  devanagari letter nna
    "5f485694813a6cc3": "\u0924",  # cid 143 uni0924 (cmap) -> त  devanagari letter ta
    "9833f1621f69f039": "\u0925",  # cid 144 uni0925 (cmap) -> थ  devanagari letter tha
    "1577e5806ee88b1a": "\u0926",  # cid 145 uni0926 (cmap) -> द  devanagari letter da
    "9cb786a54ac6cba7": "\u0927",  # cid 146 uni0927 (cmap) -> ध  devanagari letter dha
    "4647c021e0ca0e69": "\u0928",  # cid 147 uni0928 (cmap) -> न  devanagari letter na
    "719a345425753e15": "\u092a",  # cid 148 uni092A (cmap) -> प  devanagari letter pa
    "5e21ae182a6919de": "\u092b",  # cid 149 uni092B (cmap) -> फ  devanagari letter pha
    "1745644a64b43e6a": "\u092c",  # cid 150 uni092C (cmap) -> ब  devanagari letter ba
    "a1c04ea4a5f68138": "\u092d",  # cid 151 uni092D (cmap) -> भ  devanagari letter bha
    "3528f90350012b0c": "\u092e",  # cid 152 uni092E (cmap) -> म  devanagari letter ma
    "f8abd3c2e9c59bd8": "\u092f",  # cid 153 uni092F (cmap) -> य  devanagari letter ya
    "eba228612d254791": "\u0930",  # cid 154 uni0930 (cmap) -> र  devanagari letter ra
    "bbe31151ad70577e": "\u0932",  # cid 155 uni0932 (cmap) -> ल  devanagari letter la
    "47c81c1d1930b3ab": "\u0935",  # cid 157 uni0935 (cmap) -> व  devanagari letter va
    "c3c12cd868c73840": "\u0936",  # cid 158 uni0936 (cmap) -> श  devanagari letter sha
    "5dd4eaec38b933cc": "\u0937",  # cid 159 uni0937 (cmap) -> ष  devanagari letter ssa
    "9188b482c12d1e7d": "\u0938",  # cid 160 uni0938 (cmap) -> स  devanagari letter sa
    "f6801a99df092f84": "\u0939",  # cid 161 uni0939 (cmap) -> ह  devanagari letter ha
    "c562ce29c1ba2409": "\u0915\u094d\u0937",  # cid 162 glyph00162 (gsub) -> क्ष  devanagari letter ka + devanagari sign virama + devanagari letter ssa
    "b74cb52968ba54ac": "\u0958",  # cid 164 uni0958 (cmap) -> क़  devanagari letter qa
    "fc18d5ff8a0b7904": "\u0959",  # cid 165 uni0959 (cmap) -> ख़  devanagari letter khha
    "e0e26cddf381b1b9": "\u095a",  # cid 166 uni095A (cmap) -> ग़  devanagari letter ghha
    "fc08d26447491d4a": "\u0918\u093c",  # cid 167 glyph00167 (gsub) -> घ़  devanagari letter gha + devanagari sign nukta
    "673ec9e801f84c6a": "\u0919\u093c",  # cid 168 glyph00168 (gsub) -> ङ़  devanagari letter nga + devanagari sign nukta
    "f86c04db95579a48": "\u091a\u093c",  # cid 169 glyph00169 (gsub) -> च़  devanagari letter ca + devanagari sign nukta
    "6cb2484b4204ecaf": "\u091b\u093c",  # cid 170 glyph00170 (gsub) -> छ़  devanagari letter cha + devanagari sign nukta
    "b6c92609dc49cadb": "\u095b",  # cid 171 uni095B (cmap) -> ज़  devanagari letter za
    "2979a5441c5dfc60": "\u091d\u093c",  # cid 172 glyph00172 (gsub) -> झ़  devanagari letter jha + devanagari sign nukta
    "cd127e3bd117dfc7": "\u091f\u093c",  # cid 174 glyph00174 (gsub) -> ट़  devanagari letter tta + devanagari sign nukta
    "3949d7e9bb0ef0c4": "\u0920\u093c",  # cid 175 glyph00175 (gsub) -> ठ़  devanagari letter ttha + devanagari sign nukta
    "0506d852ee388960": "\u095c",  # cid 176 uni095C (cmap) -> ड़  devanagari letter dddha
    "f51a62f1c5d5bcbd": "\u095d",  # cid 177 uni095D (cmap) -> ढ़  devanagari letter rha
    "19c45bac1b9b9d8e": "\u0923\u093c",  # cid 178 glyph00178 (gsub) -> ण़  devanagari letter nna + devanagari sign nukta
    "e9d430074d25d7b2": "\u0924\u093c",  # cid 179 glyph00179 (gsub) -> त़  devanagari letter ta + devanagari sign nukta
    "e40e0e63bcca1400": "\u0925\u093c",  # cid 180 glyph00180 (gsub) -> थ़  devanagari letter tha + devanagari sign nukta
    "d8a28f25e8c2747e": "\u0926\u093c",  # cid 181 glyph00181 (gsub) -> द़  devanagari letter da + devanagari sign nukta
    "68dd03b0fd138314": "\u0927\u093c",  # cid 182 glyph00182 (gsub) -> ध़  devanagari letter dha + devanagari sign nukta
    "c9bc1d01688a3499": "\u0929",  # cid 183 uni0929 (cmap) -> ऩ  devanagari letter nnna
    "e4298d04b5e3e985": "\u092a\u093c",  # cid 184 glyph00184 (gsub) -> प़  devanagari letter pa + devanagari sign nukta
    "49bb9477853d14c4": "\u095e",  # cid 185 uni095E (cmap) -> फ़  devanagari letter fa
    "ade697a4f3cba2d9": "\u092c\u093c",  # cid 186 glyph00186 (gsub) -> ब़  devanagari letter ba + devanagari sign nukta
    "7fb90b8fcf5250ac": "\u092d\u093c",  # cid 187 glyph00187 (gsub) -> भ़  devanagari letter bha + devanagari sign nukta
    "abd49fc0d68868ca": "\u092e\u093c",  # cid 188 glyph00188 (gsub) -> म़  devanagari letter ma + devanagari sign nukta
    "346c9ddb132579e7": "\u095f",  # cid 189 uni095F (cmap) -> य़  devanagari letter yya
    "601d42b7002cef45": "\u0931",  # cid 190 uni0931 (cmap) -> ऱ  devanagari letter rra
    "21eda9e3297ea495": "\u0932\u093c",  # cid 191 glyph00191 (gsub) -> ल़  devanagari letter la + devanagari sign nukta
    "d30db03a3c937128": "\u0935\u093c",  # cid 193 glyph00193 (gsub) -> व़  devanagari letter va + devanagari sign nukta
    "861e6fc2f8254364": "\u0936\u093c",  # cid 194 glyph00194 (gsub) -> श़  devanagari letter sha + devanagari sign nukta
    "3e24479c2a1e3cd6": "\u0937\u093c",  # cid 195 glyph00195 (gsub) -> ष़  devanagari letter ssa + devanagari sign nukta
    "a51325e33a8e2147": "\u0938\u093c",  # cid 196 glyph00196 (gsub) -> स़  devanagari letter sa + devanagari sign nukta
    "1ec8c16e003364eb": "\u0939\u093c",  # cid 197 glyph00197 (gsub) -> ह़  devanagari letter ha + devanagari sign nukta
    "af3d5c47e3d8e020": "\u0915\u094d",  # cid 200 glyph00200 (gsub) -> क्  devanagari letter ka + devanagari sign virama
    "996f9b5e2d5666aa": "\u0916\u094d",  # cid 201 glyph00201 (gsub) -> ख्  devanagari letter kha + devanagari sign virama
    "e54a114e12ad8766": "\u0917\u094d",  # cid 202 glyph00202 (gsub) -> ग्  devanagari letter ga + devanagari sign virama
    "d99f48ea2aeb6de8": "\u0918\u094d",  # cid 203 glyph00203 (gsub) -> घ्  devanagari letter gha + devanagari sign virama
    "13cae622de21ce55": "\u0919\u094d",  # cid 204 glyph00204 (gsub) -> ङ्  devanagari letter nga + devanagari sign virama
    "4b6bfc8c3d0c38d6": "\u091a\u094d",  # cid 205 glyph00205 (gsub) -> च्  devanagari letter ca + devanagari sign virama
    "20a0c08433840bc9": "\u091b\u094d",  # cid 206 glyph00206 (gsub) -> छ्  devanagari letter cha + devanagari sign virama
    "02318aa4876433f1": "\u091c\u094d",  # cid 207 glyph00207 (gsub) -> ज्  devanagari letter ja + devanagari sign virama
    "acbf982fa61094dc": "\u091d\u094d",  # cid 208 glyph00208 (gsub) -> झ्  devanagari letter jha + devanagari sign virama
    "e37444e45f940462": "\u091f\u094d",  # cid 210 glyph00210 (gsub) -> ट्  devanagari letter tta + devanagari sign virama
    "6d6328910ce5390b": "\u0920\u094d",  # cid 211 glyph00211 (gsub) -> ठ्  devanagari letter ttha + devanagari sign virama
    "6fd96a572615d839": "\u0922\u094d",  # cid 213 glyph00213 (gsub) -> ढ्  devanagari letter ddha + devanagari sign virama
    "a238e28a1fd35485": "\u0923\u094d",  # cid 214 glyph00214 (gsub) -> ण्  devanagari letter nna + devanagari sign virama
    "a3c1a132a9804856": "\u0924\u094d",  # cid 215 glyph00215 (gsub) -> त्  devanagari letter ta + devanagari sign virama
    "3bc4b1f216458aba": "\u0925\u094d",  # cid 216 glyph00216 (gsub) -> थ्  devanagari letter tha + devanagari sign virama
    "bc8fa2af67595ced": "\u0926\u094d",  # cid 217 glyph00217 (gsub) -> द्  devanagari letter da + devanagari sign virama
    "d5706807092f1819": "\u0927\u094d",  # cid 218 glyph00218 (gsub) -> ध्  devanagari letter dha + devanagari sign virama
    "41a1099971b86c0e": "\u0928\u094d",  # cid 219 glyph00219 (gsub) -> न्  devanagari letter na + devanagari sign virama
    "bcff0265a9da8c63": "\u092a\u094d",  # cid 220 glyph00220 (gsub) -> प्  devanagari letter pa + devanagari sign virama
    "01db1022cdc3c44b": "\u092b\u094d",  # cid 221 glyph00221 (gsub) -> फ्  devanagari letter pha + devanagari sign virama
    "b901e07842553035": "\u092c\u094d",  # cid 222 glyph00222 (gsub) -> ब्  devanagari letter ba + devanagari sign virama
    "88cf8dd5ff6ede73": "\u092d\u094d",  # cid 223 glyph00223 (gsub) -> भ्  devanagari letter bha + devanagari sign virama
    "ccfffb575c103438": "\u092e\u094d",  # cid 224 glyph00224 (gsub) -> म्  devanagari letter ma + devanagari sign virama
    "9ae923e0818443cc": "\u092f\u094d",  # cid 225 glyph00225 (gsub) -> य्  devanagari letter ya + devanagari sign virama
    "dcc59849863c7ad9": "\u0930\u094d",  # cid 226 glyph00226 (gsub) -> र्  devanagari letter ra + devanagari sign virama
    "6fe93263d69259b1": "\u0932\u094d",  # cid 227 glyph00227 (gsub) -> ल्  devanagari letter la + devanagari sign virama
    "217dccceef51a915": "\u0935\u094d",  # cid 229 glyph00229 (gsub) -> व्  devanagari letter va + devanagari sign virama
    "7cf7273123356760": "\u0936\u094d",  # cid 230 glyph00230 (gsub) -> श्  devanagari letter sha + devanagari sign virama
    "bff812fcf385311f": "\u0937\u094d",  # cid 231 glyph00231 (gsub) -> ष्  devanagari letter ssa + devanagari sign virama
    "bfc5883163280ca7": "\u0938\u094d",  # cid 232 glyph00232 (gsub) -> स्  devanagari letter sa + devanagari sign virama
    "fb323113b0f99e74": "\u0939\u094d",  # cid 233 glyph00233 (gsub) -> ह्  devanagari letter ha + devanagari sign virama
    "fa59a91836a1c756": "\u0915\u094d\u0937\u094d",  # cid 234 glyph00234 (gsub) -> क्ष्  devanagari letter ka + devanagari sign virama + devanagari letter ssa + devanagari sign virama
    "6a37c8556ce69c87": "\u0958\u094d",  # cid 236 glyph00236 (gsub) -> क़्  devanagari letter qa + devanagari sign virama
    "163157142b942da0": "\u0959\u094d",  # cid 237 glyph00237 (gsub) -> ख़्  devanagari letter khha + devanagari sign virama
    "d56bff1c4b183da2": "\u095a\u094d",  # cid 238 glyph00238 (gsub) -> ग़्  devanagari letter ghha + devanagari sign virama
    "b5b03691a167910e": "\u0918\u093c\u094d",  # cid 239 glyph00239 (gsub) -> घ़्  devanagari letter gha + devanagari sign nukta + devanagari sign virama
    "e67c1f066c6bae1c": "\u0919\u093c\u094d",  # cid 240 glyph00240 (gsub) -> ङ़्  devanagari letter nga + devanagari sign nukta + devanagari sign virama
    "8d89a21d2d642423": "\u091a\u093c\u094d",  # cid 241 glyph00241 (gsub) -> च़्  devanagari letter ca + devanagari sign nukta + devanagari sign virama
    "009b8ae62031cf72": "\u091b\u093c\u094d",  # cid 242 glyph00242 (gsub) -> छ़्  devanagari letter cha + devanagari sign nukta + devanagari sign virama
    "cace9777ef52d55b": "\u095b\u094d",  # cid 243 glyph00243 (gsub) -> ज़्  devanagari letter za + devanagari sign virama
    "08b0d22695ef3289": "\u091d\u093c\u094d",  # cid 244 glyph00244 (gsub) -> झ़्  devanagari letter jha + devanagari sign nukta + devanagari sign virama
    "8d578f89c67b5da2": "\u091f\u093c\u094d",  # cid 246 glyph00246 (gsub) -> ट़्  devanagari letter tta + devanagari sign nukta + devanagari sign virama
    "ae57eb9a4ce1c454": "\u0920\u093c\u094d",  # cid 247 glyph00247 (gsub) -> ठ़्  devanagari letter ttha + devanagari sign nukta + devanagari sign virama
    "c2eee74ec6740690": "\u095c\u094d",  # cid 248 glyph00248 (gsub) -> ड़्  devanagari letter dddha + devanagari sign virama
    "a1beea36d441a1a1": "\u095d\u094d",  # cid 249 glyph00249 (gsub) -> ढ़्  devanagari letter rha + devanagari sign virama
    "349c36e4f19e52af": "\u0923\u093c\u094d",  # cid 250 glyph00250 (gsub) -> ण़्  devanagari letter nna + devanagari sign nukta + devanagari sign virama
    "793e8c37148836fc": "\u0924\u093c\u094d",  # cid 251 glyph00251 (gsub) -> त़्  devanagari letter ta + devanagari sign nukta + devanagari sign virama
    "b2b705174abf5ae2": "\u0925\u093c\u094d",  # cid 252 glyph00252 (gsub) -> थ़्  devanagari letter tha + devanagari sign nukta + devanagari sign virama
    "169a6e52320e1e87": "\u0926\u093c\u094d",  # cid 253 glyph00253 (gsub) -> द़्  devanagari letter da + devanagari sign nukta + devanagari sign virama
    "554c627fccef7107": "\u0927\u093c\u094d",  # cid 254 glyph00254 (gsub) -> ध़्  devanagari letter dha + devanagari sign nukta + devanagari sign virama
    "fa70674b1120d5c6": "\u0929\u094d",  # cid 255 glyph00255 (gsub) -> ऩ्  devanagari letter nnna + devanagari sign virama
    "e447bda9a45716c5": "\u092a\u093c\u094d",  # cid 256 glyph00256 (gsub) -> प़्  devanagari letter pa + devanagari sign nukta + devanagari sign virama
    "afa211e08d8d54b8": "\u095e\u094d",  # cid 257 glyph00257 (gsub) -> फ़्  devanagari letter fa + devanagari sign virama
    "6c7fd7a0ffdefe97": "\u092c\u093c\u094d",  # cid 258 glyph00258 (gsub) -> ब़्  devanagari letter ba + devanagari sign nukta + devanagari sign virama
    "98c81b6b13937c8d": "\u092d\u093c\u094d",  # cid 259 glyph00259 (gsub) -> भ़्  devanagari letter bha + devanagari sign nukta + devanagari sign virama
    "f1efcc080e1c65dd": "\u092e\u093c\u094d",  # cid 260 glyph00260 (gsub) -> म़्  devanagari letter ma + devanagari sign nukta + devanagari sign virama
    "2e8f5c65fa4aef4a": "\u095f\u094d",  # cid 261 glyph00261 (gsub) -> य़्  devanagari letter yya + devanagari sign virama
    "c4e366b7598c052f": "\u0931\u094d",  # cid 262 glyph00262 (gsub) -> ऱ्  devanagari letter rra + devanagari sign virama
    "2fc3ad994d8e848a": "\u0932\u093c\u094d",  # cid 263 glyph00263 (gsub) -> ल़्  devanagari letter la + devanagari sign nukta + devanagari sign virama
    "bcda2770744e90a0": "\u0935\u093c\u094d",  # cid 265 glyph00265 (gsub) -> व़्  devanagari letter va + devanagari sign nukta + devanagari sign virama
    "b7328781ca3d6eb7": "\u0936\u093c\u094d",  # cid 266 glyph00266 (gsub) -> श़्  devanagari letter sha + devanagari sign nukta + devanagari sign virama
    "cbcdcc09100500f5": "\u0937\u093c\u094d",  # cid 267 glyph00267 (gsub) -> ष़्  devanagari letter ssa + devanagari sign nukta + devanagari sign virama
    "bc5c348612c516c7": "\u0938\u093c\u094d",  # cid 268 glyph00268 (gsub) -> स़्  devanagari letter sa + devanagari sign nukta + devanagari sign virama
    "30b06656383b4b7a": "\u0939\u093c\u094d",  # cid 269 glyph00269 (gsub) -> ह़्  devanagari letter ha + devanagari sign nukta + devanagari sign virama
    "ff788509f4def64c": "\u0915\u094d\u0930",  # cid 272 glyph00272 (gsub) -> क्र  devanagari letter ka + devanagari sign virama + devanagari letter ra
    "8a169afbfdde3b50": "\u0916\u094d\u0930",  # cid 273 glyph00273 (gsub) -> ख्र  devanagari letter kha + devanagari sign virama + devanagari letter ra
    "0e89348b5762812d": "\u0917\u094d\u0930",  # cid 274 glyph00274 (gsub) -> ग्र  devanagari letter ga + devanagari sign virama + devanagari letter ra
    "9404cac51c07ec67": "\u0918\u094d\u0930",  # cid 275 glyph00275 (gsub) -> घ्र  devanagari letter gha + devanagari sign virama + devanagari letter ra
    "8876d53b0f0683c9": "\u0919\u094d\u0930",  # cid 276 glyph00276 (gsub) -> ङ्र  devanagari letter nga + devanagari sign virama + devanagari letter ra
    "7827d1affecfeeac": "\u091a\u094d\u0930",  # cid 277 glyph00277 (gsub) -> च्र  devanagari letter ca + devanagari sign virama + devanagari letter ra
    "e4dc71102d24123f": "\u091b\u094d\u0930",  # cid 278 glyph00278 (gsub) -> छ्र  devanagari letter cha + devanagari sign virama + devanagari letter ra
    "d13c2313b14fe80e": "\u091c\u094d\u0930",  # cid 279 glyph00279 (gsub) -> ज्र  devanagari letter ja + devanagari sign virama + devanagari letter ra
    "b834f4a04f7f09fc": "\u091d\u094d\u0930",  # cid 280 glyph00280 (gsub) -> झ्र  devanagari letter jha + devanagari sign virama + devanagari letter ra
    "598a7b37bcd39244": "\u091f\u094d\u0930",  # cid 282 glyph00282 (gsub) -> ट्र  devanagari letter tta + devanagari sign virama + devanagari letter ra
    "62cc2b8e4a0b964e": "\u0920\u094d\u0930",  # cid 283 glyph00283 (gsub) -> ठ्र  devanagari letter ttha + devanagari sign virama + devanagari letter ra
    "dfa1f879686ffae5": "\u0921\u094d\u0930",  # cid 284 glyph00284 (gsub) -> ड्र  devanagari letter dda + devanagari sign virama + devanagari letter ra
    "23728e8644c327b3": "\u0922\u094d\u0930",  # cid 285 glyph00285 (gsub) -> ढ्र  devanagari letter ddha + devanagari sign virama + devanagari letter ra
    "d363cdddd588d99e": "\u0923\u094d\u0930",  # cid 286 glyph00286 (gsub) -> ण्र  devanagari letter nna + devanagari sign virama + devanagari letter ra
    "a4d4fad48129f3b5": "\u0924\u094d\u0930",  # cid 287 glyph00287 (gsub) -> त्र  devanagari letter ta + devanagari sign virama + devanagari letter ra
    "63accddd561dd1e1": "\u0925\u094d\u0930",  # cid 288 glyph00288 (gsub) -> थ्र  devanagari letter tha + devanagari sign virama + devanagari letter ra
    "eaf73cf07df8d90b": "\u0926\u094d\u0930",  # cid 289 glyph00289 (gsub) -> द्र  devanagari letter da + devanagari sign virama + devanagari letter ra
    "037c7d568a13697f": "\u0927\u094d\u0930",  # cid 290 glyph00290 (gsub) -> ध्र  devanagari letter dha + devanagari sign virama + devanagari letter ra
    "0753a68dc31ff4be": "\u0928\u094d\u0930",  # cid 291 glyph00291 (gsub) -> न्र  devanagari letter na + devanagari sign virama + devanagari letter ra
    "ade3b5e02895f23d": "\u092a\u094d\u0930",  # cid 292 glyph00292 (gsub) -> प्र  devanagari letter pa + devanagari sign virama + devanagari letter ra
    "533efbec473acc30": "\u092b\u094d\u0930",  # cid 293 glyph00293 (gsub) -> फ्र  devanagari letter pha + devanagari sign virama + devanagari letter ra
    "a32205b24c20d2eb": "\u092c\u094d\u0930",  # cid 294 glyph00294 (gsub) -> ब्र  devanagari letter ba + devanagari sign virama + devanagari letter ra
    "aea00eee8c1c0084": "\u092d\u094d\u0930",  # cid 295 glyph00295 (gsub) -> भ्र  devanagari letter bha + devanagari sign virama + devanagari letter ra
    "101dcab89e4639de": "\u092e\u094d\u0930",  # cid 296 glyph00296 (gsub) -> म्र  devanagari letter ma + devanagari sign virama + devanagari letter ra
    "02c960499c2fb6d5": "\u092f\u094d\u0930",  # cid 297 glyph00297 (gsub) -> य्र  devanagari letter ya + devanagari sign virama + devanagari letter ra
    "ff49bbde42ff6547": "\u0932\u094d\u0930",  # cid 299 glyph00299 (gsub) -> ल्र  devanagari letter la + devanagari sign virama + devanagari letter ra
    "618ae73f7aba2223": "\u0935\u094d\u0930",  # cid 301 glyph00301 (gsub) -> व्र  devanagari letter va + devanagari sign virama + devanagari letter ra
    "ea08d36fa4ca4a30": "\u0936\u094d\u0930",  # cid 302 glyph00302 (gsub) -> श्र  devanagari letter sha + devanagari sign virama + devanagari letter ra
    "bdb8280a5c2e4a6f": "\u0937\u094d\u0930",  # cid 303 glyph00303 (gsub) -> ष्र  devanagari letter ssa + devanagari sign virama + devanagari letter ra
    "8481c3118c22e7da": "\u0938\u094d\u0930",  # cid 304 glyph00304 (gsub) -> स्र  devanagari letter sa + devanagari sign virama + devanagari letter ra
    "068af65ed182624a": "\u0939\u094d\u0930",  # cid 305 glyph00305 (gsub) -> ह्र  devanagari letter ha + devanagari sign virama + devanagari letter ra
    "09a002630bfed08e": "\u0915\u094d\u0937\u094d\u0930",  # cid 306 glyph00306 (gsub) -> क्ष्र  devanagari letter ka + devanagari sign virama + devanagari letter ssa + devanagari sign virama + devanagari letter ra
    "a6d0bceec82ee266": "\u0958\u094d\u0930",  # cid 308 glyph00308 (gsub) -> क़्र  devanagari letter qa + devanagari sign virama + devanagari letter ra
    "1d2d1696e23af54c": "\u0959\u094d\u0930",  # cid 309 glyph00309 (gsub) -> ख़्र  devanagari letter khha + devanagari sign virama + devanagari letter ra
    "516d35c43ee94306": "\u095a\u094d\u0930",  # cid 310 glyph00310 (gsub) -> ग़्र  devanagari letter ghha + devanagari sign virama + devanagari letter ra
    "57f02fc6f62a266b": "\u0918\u093c\u094d\u0930",  # cid 311 glyph00311 (gsub) -> घ़्र  devanagari letter gha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "dc6006ac5523253b": "\u0919\u093c\u094d\u0930",  # cid 312 glyph00312 (gsub) -> ङ़्र  devanagari letter nga + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "f81ecb4514d663ee": "\u091a\u093c\u094d\u0930",  # cid 313 glyph00313 (gsub) -> च़्र  devanagari letter ca + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "9c58b1ae4c36bc6f": "\u091b\u093c\u094d\u0930",  # cid 314 glyph00314 (gsub) -> छ़्र  devanagari letter cha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "e66c7ec972867f2a": "\u095b\u094d\u0930",  # cid 315 glyph00315 (gsub) -> ज़्र  devanagari letter za + devanagari sign virama + devanagari letter ra
    "0643a41a473ca27c": "\u091d\u093c\u094d\u0930",  # cid 316 glyph00316 (gsub) -> झ़्र  devanagari letter jha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "e8b4f41047e017e8": "\u091f\u093c\u094d\u0930",  # cid 318 glyph00318 (gsub) -> ट़्र  devanagari letter tta + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "4e1148dc21aab0bc": "\u0920\u093c\u094d\u0930",  # cid 319 glyph00319 (gsub) -> ठ़्र  devanagari letter ttha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "ea2591cb134b7eb2": "\u095c\u094d\u0930",  # cid 320 glyph00320 (gsub) -> ड़्र  devanagari letter dddha + devanagari sign virama + devanagari letter ra
    "0f2381eae9cf9566": "\u095d\u094d\u0930",  # cid 321 glyph00321 (gsub) -> ढ़्र  devanagari letter rha + devanagari sign virama + devanagari letter ra
    "e4a2ba75454ed575": "\u0923\u093c\u094d\u0930",  # cid 322 glyph00322 (gsub) -> ण़्र  devanagari letter nna + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "7cdbc58ecfa4418c": "\u0924\u093c\u094d\u0930",  # cid 323 glyph00323 (gsub) -> त़्र  devanagari letter ta + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "198ab3779a71a064": "\u0925\u093c\u094d\u0930",  # cid 324 glyph00324 (gsub) -> थ़्र  devanagari letter tha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "f15f9b0557ef82fa": "\u0926\u093c\u094d\u0930",  # cid 325 glyph00325 (gsub) -> द़्र  devanagari letter da + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "d7358693e86c81c1": "\u0927\u093c\u094d\u0930",  # cid 326 glyph00326 (gsub) -> ध़्र  devanagari letter dha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "17c6c0a5a48c5d78": "\u0929\u094d\u0930",  # cid 327 glyph00327 (gsub) -> ऩ्र  devanagari letter nnna + devanagari sign virama + devanagari letter ra
    "8317888e9e7a0ba8": "\u092a\u093c\u094d\u0930",  # cid 328 glyph00328 (gsub) -> प़्र  devanagari letter pa + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "19a1ea8d7ae7b29e": "\u095e\u094d\u0930",  # cid 329 glyph00329 (gsub) -> फ़्र  devanagari letter fa + devanagari sign virama + devanagari letter ra
    "fff5bb33a0bf5bb9": "\u092c\u093c\u094d\u0930",  # cid 330 glyph00330 (gsub) -> ब़्र  devanagari letter ba + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "436cc92274ae8ec3": "\u092d\u093c\u094d\u0930",  # cid 331 glyph00331 (gsub) -> भ़्र  devanagari letter bha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "443c1c7234c948e7": "\u092e\u093c\u094d\u0930",  # cid 332 glyph00332 (gsub) -> म़्र  devanagari letter ma + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "3240f2693a62ced7": "\u095f\u094d\u0930",  # cid 333 glyph00333 (gsub) -> य़्र  devanagari letter yya + devanagari sign virama + devanagari letter ra
    "934ba1241d4ffd73": "\u0932\u093c\u094d\u0930",  # cid 335 glyph00335 (gsub) -> ल़्र  devanagari letter la + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "1ad5ab3696674f00": "\u0935\u093c\u094d\u0930",  # cid 337 glyph00337 (gsub) -> व़्र  devanagari letter va + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "518af5081c3df615": "\u0936\u093c\u094d\u0930",  # cid 338 glyph00338 (gsub) -> श़्र  devanagari letter sha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "4d9c48f84076e2d3": "\u0937\u093c\u094d\u0930",  # cid 339 glyph00339 (gsub) -> ष़्र  devanagari letter ssa + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "ddd5f5d8d856f87c": "\u0938\u093c\u094d\u0930",  # cid 340 glyph00340 (gsub) -> स़्र  devanagari letter sa + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "43af7a9b8a5e91dd": "\u0939\u093c\u094d\u0930",  # cid 341 glyph00341 (gsub) -> ह़्र  devanagari letter ha + devanagari sign nukta + devanagari sign virama + devanagari letter ra
    "fe880f7ad816d8fe": "\u0915\u094d\u0930\u094d",  # cid 344 glyph00344 (gsub) -> क्र्  devanagari letter ka + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "e868e8bef1db1891": "\u0916\u094d\u0930\u094d",  # cid 345 glyph00345 (gsub) -> ख्र्  devanagari letter kha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "622bd56ecb316c6e": "\u0917\u094d\u0930\u094d",  # cid 346 glyph00346 (gsub) -> ग्र्  devanagari letter ga + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "a11aa76f049f376d": "\u0918\u094d\u0930\u094d",  # cid 347 glyph00347 (gsub) -> घ्र्  devanagari letter gha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "62d6f38fbb38778c": "\u0919\u094d\u0930\u094d",  # cid 348 glyph00348 (gsub) -> ङ्र्  devanagari letter nga + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "af3bf673e3fb9565": "\u091a\u094d\u0930\u094d",  # cid 349 glyph00349 (gsub) -> च्र्  devanagari letter ca + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "031327a21bfa75e0": "\u091b\u094d\u0930\u094d",  # cid 350 glyph00350 (gsub) -> छ्र्  devanagari letter cha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "fc13cfaad6632b82": "\u091c\u094d\u0930\u094d",  # cid 351 glyph00351 (gsub) -> ज्र्  devanagari letter ja + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "81d16da97c87b4e8": "\u091d\u094d\u0930\u094d",  # cid 352 glyph00352 (gsub) -> झ्र्  devanagari letter jha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "2fb3740ecf44718a": "\u091f\u094d\u0930\u094d",  # cid 354 glyph00354 (gsub) -> ट्र्  devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "9588e1a86fc89871": "\u0920\u094d\u0930\u094d",  # cid 355 glyph00355 (gsub) -> ठ्र्  devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "383c2b56e8d369be": "\u0921\u094d\u0930\u094d",  # cid 356 glyph00356 (gsub) -> ड्र्  devanagari letter dda + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "764c84ed31481c17": "\u0922\u094d\u0930\u094d",  # cid 357 glyph00357 (gsub) -> ढ्र्  devanagari letter ddha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "06e3c752714591c0": "\u0923\u094d\u0930\u094d",  # cid 358 glyph00358 (gsub) -> ण्र्  devanagari letter nna + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "76966b963418ac6a": "\u0924\u094d\u0930\u094d",  # cid 359 glyph00359 (gsub) -> त्र्  devanagari letter ta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "052d9edfb79f0b7d": "\u0925\u094d\u0930\u094d",  # cid 360 glyph00360 (gsub) -> थ्र्  devanagari letter tha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "f28d08c2e9f6fd51": "\u0926\u094d\u0930\u094d",  # cid 361 glyph00361 (gsub) -> द्र्  devanagari letter da + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "a62a09d8a24945c2": "\u0927\u094d\u0930\u094d",  # cid 362 glyph00362 (gsub) -> ध्र्  devanagari letter dha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "b79bb13a2e0fd8bf": "\u0928\u094d\u0930\u094d",  # cid 363 glyph00363 (gsub) -> न्र्  devanagari letter na + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "4740a6c6b2b85b3d": "\u092a\u094d\u0930\u094d",  # cid 364 glyph00364 (gsub) -> प्र्  devanagari letter pa + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "4536a0d07f259b73": "\u092b\u094d\u0930\u094d",  # cid 365 glyph00365 (gsub) -> फ्र्  devanagari letter pha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "0b23f151f4b57edd": "\u092c\u094d\u0930\u094d",  # cid 366 glyph00366 (gsub) -> ब्र्  devanagari letter ba + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "d4d55b1b6f7aa468": "\u092d\u094d\u0930\u094d",  # cid 367 glyph00367 (gsub) -> भ्र्  devanagari letter bha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "4ee68e07afe29022": "\u092e\u094d\u0930\u094d",  # cid 368 glyph00368 (gsub) -> म्र्  devanagari letter ma + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "9d035a6e60f09ca8": "\u092f\u094d\u0930\u094d",  # cid 369 glyph00369 (gsub) -> य्र्  devanagari letter ya + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "cdd824f275a10480": "\u0932\u094d\u0930\u094d",  # cid 371 glyph00371 (gsub) -> ल्र्  devanagari letter la + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "f00dde55bdbc8cfc": "\u0935\u094d\u0930\u094d",  # cid 373 glyph00373 (gsub) -> व्र्  devanagari letter va + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "572a93df6a3ea42b": "\u0936\u094d\u0930\u094d",  # cid 374 glyph00374 (gsub) -> श्र्  devanagari letter sha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "8121e5d99d03390a": "\u0937\u094d\u0930\u094d",  # cid 375 glyph00375 (gsub) -> ष्र्  devanagari letter ssa + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "262971c6416b7e4f": "\u0938\u094d\u0930\u094d",  # cid 376 glyph00376 (gsub) -> स्र्  devanagari letter sa + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "e4b039462756447e": "\u0939\u094d\u0930\u094d",  # cid 377 glyph00377 (gsub) -> ह्र्  devanagari letter ha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "186a91eb2d053b10": "\u0915\u094d\u0937\u094d\u0930\u094d",  # cid 378 glyph00378 (gsub) -> क्ष्र्  devanagari letter ka + devanagari sign virama + devanagari letter ssa + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "ad2a3b64a67d5ba9": "\u0958\u094d\u0930\u094d",  # cid 380 glyph00380 (gsub) -> क़्र्  devanagari letter qa + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "1465f6dc854b9c3d": "\u0959\u094d\u0930\u094d",  # cid 381 glyph00381 (gsub) -> ख़्र्  devanagari letter khha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "576d8b6c22241a89": "\u095a\u094d\u0930\u094d",  # cid 382 glyph00382 (gsub) -> ग़्र्  devanagari letter ghha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "36b13409b5751611": "\u0918\u093c\u094d\u0930\u094d",  # cid 383 glyph00383 (gsub) -> घ़्र्  devanagari letter gha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "fb1128340ea9896e": "\u0919\u093c\u094d\u0930\u094d",  # cid 384 glyph00384 (gsub) -> ङ़्र्  devanagari letter nga + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "fa2be35f7f9add75": "\u091a\u093c\u094d\u0930\u094d",  # cid 385 glyph00385 (gsub) -> च़्र्  devanagari letter ca + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "3b6571cc24027625": "\u091b\u093c\u094d\u0930\u094d",  # cid 386 glyph00386 (gsub) -> छ़्र्  devanagari letter cha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "66450213846f6a29": "\u095b\u094d\u0930\u094d",  # cid 387 glyph00387 (gsub) -> ज़्र्  devanagari letter za + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "41af7fbf108f954d": "\u091d\u093c\u094d\u0930\u094d",  # cid 388 glyph00388 (gsub) -> झ़्र्  devanagari letter jha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "b7b7b77284024f04": "\u091f\u093c\u094d\u0930\u094d",  # cid 390 glyph00390 (gsub) -> ट़्र्  devanagari letter tta + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "3405a8698f9be46b": "\u0920\u093c\u094d\u0930\u094d",  # cid 391 glyph00391 (gsub) -> ठ़्र्  devanagari letter ttha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "d4489410857c4791": "\u095c\u094d\u0930\u094d",  # cid 392 glyph00392 (gsub) -> ड़्र्  devanagari letter dddha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "79c5c529f57b6cb9": "\u095d\u094d\u0930\u094d",  # cid 393 glyph00393 (gsub) -> ढ़्र्  devanagari letter rha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "616c68f5012ffe2d": "\u0923\u093c\u094d\u0930\u094d",  # cid 394 glyph00394 (gsub) -> ण़्र्  devanagari letter nna + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "4935fb5974073d31": "\u0924\u093c\u094d\u0930\u094d",  # cid 395 glyph00395 (gsub) -> त़्र्  devanagari letter ta + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "be15a03d30db2d46": "\u0925\u093c\u094d\u0930\u094d",  # cid 396 glyph00396 (gsub) -> थ़्र्  devanagari letter tha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "dff6a1306fc6f0fe": "\u0926\u093c\u094d\u0930\u094d",  # cid 397 glyph00397 (gsub) -> द़्र्  devanagari letter da + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "789583bab16ef816": "\u0927\u093c\u094d\u0930\u094d",  # cid 398 glyph00398 (gsub) -> ध़्र्  devanagari letter dha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "6838748b91db549c": "\u0929\u094d\u0930\u094d",  # cid 399 glyph00399 (gsub) -> ऩ्र्  devanagari letter nnna + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "bb05b74c9af7fc5a": "\u092a\u093c\u094d\u0930\u094d",  # cid 400 glyph00400 (gsub) -> प़्र्  devanagari letter pa + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "9c00e1c111d267ed": "\u095e\u094d\u0930\u094d",  # cid 401 glyph00401 (gsub) -> फ़्र्  devanagari letter fa + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "6a4426766d5d3b78": "\u092c\u093c\u094d\u0930\u094d",  # cid 402 glyph00402 (gsub) -> ब़्र्  devanagari letter ba + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "524b160587b1bca4": "\u092d\u093c\u094d\u0930\u094d",  # cid 403 glyph00403 (gsub) -> भ़्र्  devanagari letter bha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "048bc7cf73e6027d": "\u092e\u093c\u094d\u0930\u094d",  # cid 404 glyph00404 (gsub) -> म़्र्  devanagari letter ma + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "d19951340f40a504": "\u095f\u094d\u0930\u094d",  # cid 405 glyph00405 (gsub) -> य़्र्  devanagari letter yya + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "8217c16209477250": "\u0932\u093c\u094d\u0930\u094d",  # cid 407 glyph00407 (gsub) -> ल़्र्  devanagari letter la + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "5de2702be7515c6f": "\u0935\u093c\u094d\u0930\u094d",  # cid 409 glyph00409 (gsub) -> व़्र्  devanagari letter va + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "2448616eccbca7ed": "\u0936\u093c\u094d\u0930\u094d",  # cid 410 glyph00410 (gsub) -> श़्र्  devanagari letter sha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "ecd11798a94bae7f": "\u0937\u093c\u094d\u0930\u094d",  # cid 411 glyph00411 (gsub) -> ष़्र्  devanagari letter ssa + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "307b3969da1964a8": "\u0938\u093c\u094d\u0930\u094d",  # cid 412 glyph00412 (gsub) -> स़्र्  devanagari letter sa + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "351122f351b25bab": "\u0939\u093c\u094d\u0930\u094d",  # cid 413 glyph00413 (gsub) -> ह़्र्  devanagari letter ha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "8ea192d3070e99ae": "\u0915\u094d\u0924",  # cid 416 glyph00416 (gsub) -> क्त  devanagari letter ka + devanagari sign virama + devanagari letter ta
    "159e0810b1d42312": "\u0916\u094d\u0928",  # cid 417 glyph00417 (gsub) -> ख्न  devanagari letter kha + devanagari sign virama + devanagari letter na
    "573e4ea68f9f7e74": "\u0919\u094d\u092e",  # cid 418 glyph00418 (gsub) -> ङ्म  devanagari letter nga + devanagari sign virama + devanagari letter ma
    "654edd9578c24a02": "\u0924\u094d\u0924",  # cid 419 glyph00419 (gsub) -> त्त  devanagari letter ta + devanagari sign virama + devanagari letter ta
    "59ad30ce1e08defd": "\u0924\u094d\u0928",  # cid 420 glyph00420 (gsub) -> त्न  devanagari letter ta + devanagari sign virama + devanagari letter na
    "fc7a7885051aadc1": "\u0926\u094d\u0917",  # cid 421 glyph00421 (gsub) -> द्ग  devanagari letter da + devanagari sign virama + devanagari letter ga
    "808aada0b60cc694": "\u0926\u094d\u0918",  # cid 422 glyph00422 (gsub) -> द्घ  devanagari letter da + devanagari sign virama + devanagari letter gha
    "0cd6a07612cf3bd5": "\u0926\u094d\u0926",  # cid 423 glyph00423 (gsub) -> द्द  devanagari letter da + devanagari sign virama + devanagari letter da
    "e7a12f694bb92052": "\u0926\u094d\u0927",  # cid 424 glyph00424 (gsub) -> द्ध  devanagari letter da + devanagari sign virama + devanagari letter dha
    "8537cd37c67b0316": "\u0926\u094d\u0928",  # cid 425 glyph00425 (gsub) -> द्न  devanagari letter da + devanagari sign virama + devanagari letter na
    "344a7bb11c4b2951": "\u0926\u094d\u092c",  # cid 426 glyph00426 (gsub) -> द्ब  devanagari letter da + devanagari sign virama + devanagari letter ba
    "09465db97f20c8c0": "\u0926\u094d\u092d",  # cid 427 glyph00427 (gsub) -> द्भ  devanagari letter da + devanagari sign virama + devanagari letter bha
    "b55d6c43a5af5cb2": "\u0926\u094d\u092e",  # cid 428 glyph00428 (gsub) -> द्म  devanagari letter da + devanagari sign virama + devanagari letter ma
    "78cb83a3c7806fbd": "\u0926\u094d\u092f",  # cid 429 glyph00429 (gsub) -> द्य  devanagari letter da + devanagari sign virama + devanagari letter ya
    "a004c39177b2aae1": "\u0926\u094d\u0935",  # cid 430 glyph00430 (gsub) -> द्व  devanagari letter da + devanagari sign virama + devanagari letter va
    "a821333a355f2b7c": "\u092a\u094d\u0924",  # cid 431 glyph00431 (gsub) -> प्त  devanagari letter pa + devanagari sign virama + devanagari letter ta
    "e87e865208cddd48": "\u0936\u094d\u0928",  # cid 432 glyph00432 (gsub) -> श्न  devanagari letter sha + devanagari sign virama + devanagari letter na
    "a739b8394ccbf91e": "\u0936\u094d\u091a",  # cid 433 glyph00433 (gsub) -> श्च  devanagari letter sha + devanagari sign virama + devanagari letter ca
    "907a2c4085983683": "\u0936\u094d\u0932",  # cid 434 glyph00434 (gsub) -> श्ल  devanagari letter sha + devanagari sign virama + devanagari letter la
    "9aa5c07dc772e310": "\u0936\u094d\u0935",  # cid 435 glyph00435 (gsub) -> श्व  devanagari letter sha + devanagari sign virama + devanagari letter va
    "9e80a0e7d9d297d1": "\u0937\u094d\u091f",  # cid 436 glyph00436 (gsub) -> ष्ट  devanagari letter ssa + devanagari sign virama + devanagari letter tta
    "259856a7d8700c7d": "\u0937\u094d\u0920",  # cid 437 glyph00437 (gsub) -> ष्ठ  devanagari letter ssa + devanagari sign virama + devanagari letter ttha
    "d19114c526a17f6e": "\u0937\u094d\u091f\u094d\u0930",  # cid 438 glyph00438 (gsub) -> ष्ट्र  devanagari letter ssa + devanagari sign virama + devanagari letter tta + devanagari sign virama + devanagari letter ra
    "5a4f4e7d000ead51": "\u0937\u094d\u0920\u094d\u0930",  # cid 439 glyph00439 (gsub) -> ष्ठ्र  devanagari letter ssa + devanagari sign virama + devanagari letter ttha + devanagari sign virama + devanagari letter ra
    "07f22fac4a39f0c5": "\u0938\u094d\u0924\u094d\u0930",  # cid 440 glyph00440 (gsub) -> स्त्र  devanagari letter sa + devanagari sign virama + devanagari letter ta + devanagari sign virama + devanagari letter ra
    "27475b9c74b681c0": "\u0939\u094d\u0928",  # cid 441 glyph00441 (gsub) -> ह्न  devanagari letter ha + devanagari sign virama + devanagari letter na
    "c5b7acf5edafe387": "\u0939\u094d\u092e",  # cid 442 glyph00442 (gsub) -> ह्म  devanagari letter ha + devanagari sign virama + devanagari letter ma
    "c781670c72b35bb6": "\u0939\u094d\u092f",  # cid 443 glyph00443 (gsub) -> ह्य  devanagari letter ha + devanagari sign virama + devanagari letter ya
    "e1e429055e867996": "\u0939\u094d\u0923",  # cid 444 glyph00444 (gsub) -> ह्ण  devanagari letter ha + devanagari sign virama + devanagari letter nna
    "82d90eb25d0ef17d": "\u0939\u094d\u0932",  # cid 445 glyph00445 (gsub) -> ह्ल  devanagari letter ha + devanagari sign virama + devanagari letter la
    "ae61eb9c71a2e9d2": "\u0939\u094d\u0935",  # cid 446 glyph00446 (gsub) -> ह्व  devanagari letter ha + devanagari sign virama + devanagari letter va
    "57e5ee0a662a7b57": "\u0916\u094d\u0928\u094d",  # cid 448 glyph00448 (gsub) -> ख्न्  devanagari letter kha + devanagari sign virama + devanagari letter na + devanagari sign virama
    "6b4ce88522ee5896": "\u0928\u094d\u0928\u094d",  # cid 449 glyph00449 (gsub) -> न्न्  devanagari letter na + devanagari sign virama + devanagari letter na + devanagari sign virama
    "e5fa4a331913385a": "\u0924\u094d\u0924\u094d",  # cid 450 glyph00450 (gsub) -> त्त्  devanagari letter ta + devanagari sign virama + devanagari letter ta + devanagari sign virama
    "9acb777f56a74f7c": "\u0924\u094d\u0928\u094d",  # cid 451 glyph00451 (gsub) -> त्न्  devanagari letter ta + devanagari sign virama + devanagari letter na + devanagari sign virama
    "815c92eb02f6a5d1": "\u0926\u094d\u092e\u094d",  # cid 452 glyph00452 (gsub) -> द्म्  devanagari letter da + devanagari sign virama + devanagari letter ma + devanagari sign virama
    "b4b9130bbdc6e074": "\u092a\u094d\u0924\u094d",  # cid 453 glyph00453 (gsub) -> प्त्  devanagari letter pa + devanagari sign virama + devanagari letter ta + devanagari sign virama
    "248ef5fd1abc18f2": "\u0936\u094d\u0928\u094d",  # cid 454 glyph00454 (gsub) -> श्न्  devanagari letter sha + devanagari sign virama + devanagari letter na + devanagari sign virama
    "c16a96a9651d6606": "\u0936\u094d\u091a\u094d",  # cid 455 glyph00455 (gsub) -> श्च्  devanagari letter sha + devanagari sign virama + devanagari letter ca + devanagari sign virama
    "6131033c2d9c2fe5": "\u0936\u094d\u0932\u094d",  # cid 456 glyph00456 (gsub) -> श्ल्  devanagari letter sha + devanagari sign virama + devanagari letter la + devanagari sign virama
    "f58a287d9229b4eb": "\u0936\u094d\u0935\u094d",  # cid 457 glyph00457 (gsub) -> श्व्  devanagari letter sha + devanagari sign virama + devanagari letter va + devanagari sign virama
    "baf28beca626d748": "\u0938\u094d\u0924\u094d\u0930\u094d",  # cid 458 glyph00458 (gsub) -> स्त्र्  devanagari letter sa + devanagari sign virama + devanagari letter ta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "4ef610b5aa98e59a": "\u0919\u094d\u0915",  # cid 459 glyph00459 (gsub) -> ङ्क  devanagari letter nga + devanagari sign virama + devanagari letter ka
    "d45d36ed055b4edf": "\u0919\u094d\u0916",  # cid 460 glyph00460 (gsub) -> ङ्ख  devanagari letter nga + devanagari sign virama + devanagari letter kha
    "26539313f5745d6e": "\u0919\u094d\u0917",  # cid 461 glyph00461 (gsub) -> ङ्ग  devanagari letter nga + devanagari sign virama + devanagari letter ga
    "780c4f69e4648499": "\u0919\u094d\u0918",  # cid 462 glyph00462 (gsub) -> ङ्घ  devanagari letter nga + devanagari sign virama + devanagari letter gha
    "a8bf0b5ce979eeb9": "\u0928\u094d\u0928",  # cid 463 glyph00463 (gsub) -> न्न  devanagari letter na + devanagari sign virama + devanagari letter na
    "ba45bcaf84d4e33b": "\u093f",  # cid 464 glyph00464 (gsub) -> ि  devanagari vowel sign i
    "c7cef33e95f5f82c": "\u093f",  # cid 465 glyph00465 (gsub) -> ि  devanagari vowel sign i
    "04b71286b30b4d85": "\u093f",  # cid 466 glyph00466 (inferred) -> ि  devanagari vowel sign i
    "033dd947be3b2915": "\u093f",  # cid 467 glyph00467 (inferred) -> ि  devanagari vowel sign i
    "2b162e5ede5de272": "\u093f",  # cid 468 uni093F (cmap) -> ि  devanagari vowel sign i
    "3c78bfeaf1426e4b": "\u0941",  # cid 469 uni0941 (cmap) -> ु  devanagari vowel sign u
    "843c8815f1fdc048": "\u0941",  # cid 470 glyph00470 (inferred) -> ु  devanagari vowel sign u
    "2c08e0f02f04e558": "\u0942",  # cid 471 uni0942 (cmap) -> ू  devanagari vowel sign uu
    "a25468baf3f2f34d": "\u0942",  # cid 472 glyph00472 (inferred) -> ू  devanagari vowel sign uu
    "a06b867113e6b657": "\u0943",  # cid 473 uni0943 (cmap) -> ृ  devanagari vowel sign vocalic r
    "50078245f420cb1a": "\u0919\u094d\u0930\u0941",  # cid 482 glyph00482 (gsub) -> ङ्रु  devanagari letter nga + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "ebab3ddc3839eb4d": "\u0919\u094d\u0930\u0942",  # cid 483 glyph00483 (gsub) -> ङ्रू  devanagari letter nga + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "55974e9a94bb5318": "\u0919\u094d\u0930\u0943",  # cid 484 glyph00484 (gsub) -> ङ्रृ  devanagari letter nga + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "71546182bd2ca895": "\u091b\u094d\u0930\u0941",  # cid 486 glyph00486 (gsub) -> छ्रु  devanagari letter cha + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "0b8de950d9d55bac": "\u091b\u094d\u0930\u0942",  # cid 487 glyph00487 (gsub) -> छ्रू  devanagari letter cha + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "96e44dbbb64c3cd9": "\u091b\u094d\u0930\u0943",  # cid 488 glyph00488 (gsub) -> छ्रृ  devanagari letter cha + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "64ff0347ab11d6df": "\u091f\u094d\u0930\u0941",  # cid 490 glyph00490 (gsub) -> ट्रु  devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "4ec42400b27c7ae5": "\u091f\u094d\u0930\u0942",  # cid 491 glyph00491 (gsub) -> ट्रू  devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "b07aa2168964bbf4": "\u091f\u094d\u0930\u0943",  # cid 492 glyph00492 (gsub) -> ट्रृ  devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "e89607e9697c4698": "\u0920\u094d\u0930\u0941",  # cid 494 glyph00494 (gsub) -> ठ्रु  devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "21360044c60b63e4": "\u0920\u094d\u0930\u0942",  # cid 495 glyph00495 (gsub) -> ठ्रू  devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "ef0ce5540bf7ad18": "\u0920\u094d\u0930\u0943",  # cid 496 glyph00496 (gsub) -> ठ्रृ  devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "b26a90fe4dbdfee6": "\u0921\u094d\u0930\u0941",  # cid 498 glyph00498 (gsub) -> ड्रु  devanagari letter dda + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "46930da62291a724": "\u0921\u094d\u0930\u0942",  # cid 499 glyph00499 (gsub) -> ड्रू  devanagari letter dda + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "0f87bae6a397bcb4": "\u0921\u094d\u0930\u0943",  # cid 500 glyph00500 (gsub) -> ड्रृ  devanagari letter dda + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "089f4fa8419e76cc": "\u0922\u094d\u0930\u0941",  # cid 502 glyph00502 (gsub) -> ढ्रु  devanagari letter ddha + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "0516b453c2d22731": "\u0922\u094d\u0930\u0942",  # cid 503 glyph00503 (gsub) -> ढ्रू  devanagari letter ddha + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "7ffeb6279116041f": "\u0922\u094d\u0930\u0943",  # cid 504 glyph00504 (gsub) -> ढ्रृ  devanagari letter ddha + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "21eb361721b7acee": "\u0926\u0943",  # cid 506 glyph00506 (gsub) -> दृ  devanagari letter da + devanagari vowel sign vocalic r
    "d3d32f55753a1cf7": "\u0930\u0941",  # cid 509 glyph00509 (gsub) -> रु  devanagari letter ra + devanagari vowel sign u
    "d350163924dfa8e3": "\u0930\u0942",  # cid 510 glyph00510 (gsub) -> रू  devanagari letter ra + devanagari vowel sign uu
    "51abb597dc3203db": "\u0939\u0943",  # cid 511 glyph00511 (gsub) -> हृ  devanagari letter ha + devanagari vowel sign vocalic r
    "83a0e82ac616a252": "\u0919\u093c\u094d\u0930\u0941",  # cid 513 glyph00513 (gsub) -> ङ़्रु  devanagari letter nga + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "764880c624ec2eab": "\u0919\u093c\u094d\u0930\u0942",  # cid 514 glyph00514 (gsub) -> ङ़्रू  devanagari letter nga + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "a393a313baf23db2": "\u0919\u093c\u094d\u0930\u0943",  # cid 515 glyph00515 (gsub) -> ङ़्रृ  devanagari letter nga + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "bf4af2e27be9d751": "\u091b\u093c\u094d\u0930\u0941",  # cid 517 glyph00517 (gsub) -> छ़्रु  devanagari letter cha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "d54b28150aae6ebb": "\u091b\u093c\u094d\u0930\u0942",  # cid 518 glyph00518 (gsub) -> छ़्रू  devanagari letter cha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "2e3b182e17f91ce3": "\u091b\u093c\u094d\u0930\u0943",  # cid 519 glyph00519 (gsub) -> छ़्रृ  devanagari letter cha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "06cef44818f590da": "\u091f\u093c\u094d\u0930\u0941",  # cid 521 glyph00521 (gsub) -> ट़्रु  devanagari letter tta + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "d88dee9190af7954": "\u091f\u093c\u094d\u0930\u0942",  # cid 522 glyph00522 (gsub) -> ट़्रू  devanagari letter tta + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "7424e256997f1751": "\u091f\u093c\u094d\u0930\u0943",  # cid 523 glyph00523 (gsub) -> ट़्रृ  devanagari letter tta + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "bf4897e1427cf164": "\u0920\u093c\u094d\u0930\u0941",  # cid 525 glyph00525 (gsub) -> ठ़्रु  devanagari letter ttha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "53a9c522a8c89bd5": "\u0920\u093c\u094d\u0930\u0942",  # cid 526 glyph00526 (gsub) -> ठ़्रू  devanagari letter ttha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "6eab78194090f16a": "\u0920\u093c\u094d\u0930\u0943",  # cid 527 glyph00527 (gsub) -> ठ़्रृ  devanagari letter ttha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "19877fdb24ae38c6": "\u095c\u094d\u0930\u0941",  # cid 529 glyph00529 (gsub) -> ड़्रु  devanagari letter dddha + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "36e246944c714dd6": "\u095c\u094d\u0930\u0942",  # cid 530 glyph00530 (gsub) -> ड़्रू  devanagari letter dddha + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "b7c2ce3d8b31f3bb": "\u095c\u094d\u0930\u0943",  # cid 531 glyph00531 (gsub) -> ड़्रृ  devanagari letter dddha + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "01c28450b126bbaa": "\u095d\u094d\u0930\u0941",  # cid 533 glyph00533 (gsub) -> ढ़्रु  devanagari letter rha + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "327b85cc6087f40a": "\u095d\u094d\u0930\u0942",  # cid 534 glyph00534 (gsub) -> ढ़्रू  devanagari letter rha + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "028b133ad4068db3": "\u095d\u094d\u0930\u0943",  # cid 535 glyph00535 (gsub) -> ढ़्रृ  devanagari letter rha + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "ac18c97acbf65bf1": "\u0937\u094d\u091f\u094d\u0930\u0941",  # cid 537 glyph00537 (gsub) -> ष्ट्रु  devanagari letter ssa + devanagari sign virama + devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "b0e6d71895f616b1": "\u0937\u094d\u091f\u094d\u0930\u0942",  # cid 538 glyph00538 (gsub) -> ष्ट्रू  devanagari letter ssa + devanagari sign virama + devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "e6d11fbac59a98fe": "\u0937\u094d\u091f\u094d\u0930\u0943",  # cid 539 glyph00539 (gsub) -> ष्ट्रृ  devanagari letter ssa + devanagari sign virama + devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "322ce0731c6ba4c8": "\u0937\u094d\u0920\u094d\u0930\u0941",  # cid 541 glyph00541 (gsub) -> ष्ठ्रु  devanagari letter ssa + devanagari sign virama + devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari vowel sign u
    "dc2816b018e7c9cd": "\u0937\u094d\u0920\u094d\u0930\u0942",  # cid 542 glyph00542 (gsub) -> ष्ठ्रू  devanagari letter ssa + devanagari sign virama + devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari vowel sign uu
    "913cfa633601b95c": "\u0937\u094d\u0920\u094d\u0930\u0943",  # cid 543 glyph00543 (gsub) -> ष्ठ्रृ  devanagari letter ssa + devanagari sign virama + devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari vowel sign vocalic r
    "4a2249acd2440943": "\u093e",  # cid 545 uni093E (cmap) -> ा  devanagari vowel sign aa
    "784e7085427d9316": "\u0940",  # cid 546 uni0940 (cmap) -> ी  devanagari vowel sign ii
    "fdd398b42ca33af9": "\u0940",  # cid 547 glyph00547 (gsub) -> ी  devanagari vowel sign ii
    "a563cab54a3e882e": "\u0940",  # cid 548 glyph00548 (gsub) -> ी  devanagari vowel sign ii
    "4ee52bbf8e36b664": "\u0940",  # cid 551 glyph00551 (gsub) -> ी  devanagari vowel sign ii
    "08016252eee9e965": "\u094b",  # cid 554 uni094B (cmap) -> ो  devanagari vowel sign o
    "33dd0337ae86cd97": "\u094c",  # cid 555 uni094C (cmap) -> ौ  devanagari vowel sign au
    "d15618b65fb26d1a": "\u0903",  # cid 556 uni0903 (cmap) -> ः  devanagari sign visarga
    "ea315a4a94dc3112": "\u0947",  # cid 559 uni0947 (cmap) -> े  devanagari vowel sign e
    "0d2eab482502b16a": "\u0948",  # cid 560 uni0948 (cmap) -> ै  devanagari vowel sign ai
    "04d7c55d0e21c268": "\u0901",  # cid 561 uni0901 (cmap) -> ँ  devanagari sign candrabindu
    "18746a95bf26ef5c": "\u0902",  # cid 562 uni0902 (cmap) -> ं  devanagari sign anusvara
    "3fd07f4e711020cd": "\u0947",  # cid 566 glyph00566 (inferred) -> े  devanagari vowel sign e
    "2ed8ee430c945ac9": "\u0948\u0930\u094d",  # cid 567 glyph00567 (gsub) -> ैर्  devanagari vowel sign ai + devanagari letter ra + devanagari sign virama
    "a9e08a101b2971b9": "\u0940\u0930\u094d",  # cid 568 glyph00568 (gsub) -> ीर्  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama
    "6f62eb820bd1acd7": "\u0940\u0930\u094d",  # cid 569 glyph00569 (gsub) -> ीर्  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama
    "44ab5eaf10d2374e": "\u0940\u0930\u094d",  # cid 570 glyph00570 (gsub) -> ीर्  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama
    "8372d54fa53541d2": "\u0940\u0930\u094d",  # cid 573 glyph00573 (gsub) -> ीर्  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama
    "b858f023907d0d84": "\u094b\u0930\u094d",  # cid 574 glyph00574 (gsub) -> ोर्  devanagari vowel sign o + devanagari letter ra + devanagari sign virama
    "8febc3d0eab4a302": "\u094c\u0930\u094d",  # cid 575 glyph00575 (gsub) -> ौर्  devanagari vowel sign au + devanagari letter ra + devanagari sign virama
    "805d24bfc93fec6f": "\u0908\u0901",  # cid 578 glyph00578 (gsub) -> ईँ  devanagari letter ii + devanagari sign candrabindu
    "44ff250d52f6f830": "\u0908\u0902",  # cid 579 glyph00579 (gsub) -> ईं  devanagari letter ii + devanagari sign anusvara
    "57e3631eb9e95f99": "\u0947",  # cid 580 glyph00580 (inferred) -> े  devanagari vowel sign e
    "82b8825a6237e790": "\u0947\u0902",  # cid 581 glyph00581 (gsub) -> ें  devanagari vowel sign e + devanagari sign anusvara
    "ce0636f8d0a841cf": "\u0948\u0901",  # cid 582 glyph00582 (gsub) -> ैँ  devanagari vowel sign ai + devanagari sign candrabindu
    "9d346045bb003ebc": "\u0948\u0902",  # cid 583 glyph00583 (gsub) -> ैं  devanagari vowel sign ai + devanagari sign anusvara
    "5f3467ff318455c8": "\u094b\u0901",  # cid 584 glyph00584 (gsub) -> ोँ  devanagari vowel sign o + devanagari sign candrabindu
    "318d6201d3387692": "\u094b\u0902",  # cid 585 glyph00585 (gsub) -> ों  devanagari vowel sign o + devanagari sign anusvara
    "3b1757f2a7db307f": "\u094c\u0901",  # cid 586 glyph00586 (gsub) -> ौँ  devanagari vowel sign au + devanagari sign candrabindu
    "cc07acaf1c94bcec": "\u094c\u0902",  # cid 587 glyph00587 (gsub) -> ौं  devanagari vowel sign au + devanagari sign anusvara
    "01b2a4827d33ad97": "\u0930\u094d\u0901",  # cid 588 glyph00588 (gsub) -> र्ँ  devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "858342df2361c4b6": "\u0930\u094d\u0902",  # cid 589 glyph00589 (gsub) -> र्ं  devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "b984d7970502f1e2": "\u0947",  # cid 590 glyph00590 (inferred) -> े  devanagari vowel sign e
    "32b17b3355250f46": "\u0947",  # cid 591 glyph00591 (inferred) -> े  devanagari vowel sign e
    "c1504ea623a7df8c": "\u0948\u0930\u094d\u0901",  # cid 592 glyph00592 (gsub) -> ैर्ँ  devanagari vowel sign ai + devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "dfd521a626b1393d": "\u0948\u0930\u094d\u0902",  # cid 593 glyph00593 (gsub) -> ैर्ं  devanagari vowel sign ai + devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "8c069eeab2029687": "\u0940\u0930\u094d\u0901",  # cid 594 glyph00594 (gsub) -> ीर्ँ  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "60bf69e65e4bc066": "\u0940\u0930\u094d\u0901",  # cid 595 glyph00595 (gsub) -> ीर्ँ  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "059261ee53f5aaf6": "\u0940\u0930\u094d\u0901",  # cid 596 glyph00596 (gsub) -> ीर्ँ  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "732eac5bfe9cb0ad": "\u0940\u0930\u094d\u0901",  # cid 599 glyph00599 (gsub) -> ीर्ँ  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "0ed90e47312c42e1": "\u0940\u0930\u094d\u0902",  # cid 600 glyph00600 (gsub) -> ीर्ं  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "8511a5d19da07a3f": "\u0940\u0930\u094d\u0902",  # cid 601 glyph00601 (gsub) -> ीर्ं  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "56a62329f34390e8": "\u0940\u0930\u094d\u0902",  # cid 602 glyph00602 (gsub) -> ीर्ं  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "c519e7d28ca5b77f": "\u0940\u0930\u094d\u0902",  # cid 605 glyph00605 (gsub) -> ीर्ं  devanagari vowel sign ii + devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "094511338cb7776c": "\u094b\u0930\u094d\u0901",  # cid 606 glyph00606 (gsub) -> ोर्ँ  devanagari vowel sign o + devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "c30bd7f2ae37f200": "\u094b\u0930\u094d\u0902",  # cid 607 glyph00607 (gsub) -> ोर्ं  devanagari vowel sign o + devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "53dd4055b3161c25": "\u094c\u0930\u094d\u0901",  # cid 608 glyph00608 (gsub) -> ौर्ँ  devanagari vowel sign au + devanagari letter ra + devanagari sign virama + devanagari sign candrabindu
    "596634c828dc63df": "\u094c\u0930\u094d\u0902",  # cid 609 glyph00609 (gsub) -> ौर्ं  devanagari vowel sign au + devanagari letter ra + devanagari sign virama + devanagari sign anusvara
    "2e3e4df5775e60e3": "\u0937\u094d\u091f\u0901",  # cid 614 glyph00614 (gsub) -> ष्टँ  devanagari letter ssa + devanagari sign virama + devanagari letter tta + devanagari sign candrabindu
    "0a90c69a472083a1": "\u0937\u094d\u0920\u0901",  # cid 615 glyph00615 (gsub) -> ष्ठँ  devanagari letter ssa + devanagari sign virama + devanagari letter ttha + devanagari sign candrabindu
    "5655bb8e95acaf81": "\u0919\u094d",  # cid 616 glyph00616 (gsub) -> ङ्  devanagari letter nga + devanagari sign virama
    "22db156e56df3564": "\u091b\u094d",  # cid 617 glyph00617 (gsub) -> छ्  devanagari letter cha + devanagari sign virama
    "e7f7027fce6cafc2": "\u091f\u094d",  # cid 618 glyph00618 (gsub) -> ट्  devanagari letter tta + devanagari sign virama
    "8c820ed30e6e344d": "\u0920\u094d",  # cid 619 glyph00619 (gsub) -> ठ्  devanagari letter ttha + devanagari sign virama
    "69c9f6ffc1e9737c": "\u0921\u094d",  # cid 620 glyph00620 (gsub) -> ड्  devanagari letter dda + devanagari sign virama
    "03e26790acfca156": "\u0922\u094d",  # cid 621 glyph00621 (gsub) -> ढ्  devanagari letter ddha + devanagari sign virama
    "ecc6574913ef5d5b": "\u0926\u094d",  # cid 622 glyph00622 (gsub) -> द्  devanagari letter da + devanagari sign virama
    "f05cb2887a4944ec": "\u0939\u094d",  # cid 623 glyph00623 (gsub) -> ह्  devanagari letter ha + devanagari sign virama
    "5720d7f3b768d16d": "\u0919\u093c\u094d",  # cid 624 glyph00624 (gsub) -> ङ़्  devanagari letter nga + devanagari sign nukta + devanagari sign virama
    "ba255084a1b52bee": "\u091b\u093c\u094d",  # cid 625 glyph00625 (gsub) -> छ़्  devanagari letter cha + devanagari sign nukta + devanagari sign virama
    "fd8451bb66f6bdfd": "\u091f\u093c\u094d",  # cid 626 glyph00626 (gsub) -> ट़्  devanagari letter tta + devanagari sign nukta + devanagari sign virama
    "45ca3f974df2f1a2": "\u0920\u093c\u094d",  # cid 627 glyph00627 (gsub) -> ठ़्  devanagari letter ttha + devanagari sign nukta + devanagari sign virama
    "551fde0fb3c0f1e8": "\u095c\u094d",  # cid 628 glyph00628 (gsub) -> ड़्  devanagari letter dddha + devanagari sign virama
    "26a1a1b73997e5da": "\u095d\u094d",  # cid 629 glyph00629 (gsub) -> ढ़्  devanagari letter rha + devanagari sign virama
    "6bc9b89f418783a0": "\u0926\u093c\u094d",  # cid 630 glyph00630 (gsub) -> द़्  devanagari letter da + devanagari sign nukta + devanagari sign virama
    "82644f4783d7d36f": "\u0939\u093c\u094d",  # cid 631 glyph00631 (gsub) -> ह़्  devanagari letter ha + devanagari sign nukta + devanagari sign virama
    "27b1060698ffd6b1": "\u0919\u094d\u0930\u094d",  # cid 632 glyph00632 (gsub) -> ङ्र्  devanagari letter nga + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "be0ecc43f077702a": "\u091b\u094d\u0930\u094d",  # cid 633 glyph00633 (gsub) -> छ्र्  devanagari letter cha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "69eb28cdfa7c1c03": "\u091f\u094d\u0930\u094d",  # cid 634 glyph00634 (gsub) -> ट्र्  devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "8e5ca79ada3bc879": "\u0920\u094d\u0930\u094d",  # cid 635 glyph00635 (gsub) -> ठ्र्  devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "7c10a810a9b0a6c7": "\u0921\u094d\u0930\u094d",  # cid 636 glyph00636 (gsub) -> ड्र्  devanagari letter dda + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "b3c04fad590cabaf": "\u0922\u094d\u0930\u094d",  # cid 637 glyph00637 (gsub) -> ढ्र्  devanagari letter ddha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "1b944c073609e6cf": "\u0926\u094d\u0930\u094d",  # cid 638 glyph00638 (gsub) -> द्र्  devanagari letter da + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "e0aaa3560050c3df": "\u0939\u094d\u0930\u094d",  # cid 639 glyph00639 (gsub) -> ह्र्  devanagari letter ha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "cb055869222440d1": "\u0919\u093c\u094d\u0930\u094d",  # cid 640 glyph00640 (gsub) -> ङ़्र्  devanagari letter nga + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "b9622c0208bdbe3f": "\u091b\u093c\u094d\u0930\u094d",  # cid 641 glyph00641 (gsub) -> छ़्र्  devanagari letter cha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "514ef67e98adc17b": "\u091f\u093c\u094d\u0930\u094d",  # cid 642 glyph00642 (gsub) -> ट़्र्  devanagari letter tta + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "3da51136b1657601": "\u0920\u093c\u094d\u0930\u094d",  # cid 643 glyph00643 (gsub) -> ठ़्र्  devanagari letter ttha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "9f69155fbdc12a2a": "\u095c\u094d\u0930\u094d",  # cid 644 glyph00644 (gsub) -> ड़्र्  devanagari letter dddha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "9ea5e79e7bd8c2ee": "\u095d\u094d\u0930\u094d",  # cid 645 glyph00645 (gsub) -> ढ़्र्  devanagari letter rha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "c43697d8a58e60fa": "\u0926\u093c\u094d\u0930\u094d",  # cid 646 glyph00646 (gsub) -> द़्र्  devanagari letter da + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "ce05bfc781e879fe": "\u0939\u093c\u094d\u0930\u094d",  # cid 647 glyph00647 (gsub) -> ह़्र्  devanagari letter ha + devanagari sign nukta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "6b00395a7bb2a703": "\u0926\u094d\u0917\u094d",  # cid 648 glyph00648 (gsub) -> द्ग्  devanagari letter da + devanagari sign virama + devanagari letter ga + devanagari sign virama
    "4e673ca182b78cde": "\u0926\u094d\u0918\u094d",  # cid 649 glyph00649 (gsub) -> द्घ्  devanagari letter da + devanagari sign virama + devanagari letter gha + devanagari sign virama
    "d961000256efabb9": "\u0926\u094d\u0926\u094d",  # cid 650 glyph00650 (gsub) -> द्द्  devanagari letter da + devanagari sign virama + devanagari letter da + devanagari sign virama
    "8db91a105927fd94": "\u0926\u094d\u0927\u094d",  # cid 651 glyph00651 (gsub) -> द्ध्  devanagari letter da + devanagari sign virama + devanagari letter dha + devanagari sign virama
    "04eac46559a39ab1": "\u0926\u094d\u0928\u094d",  # cid 652 glyph00652 (gsub) -> द्न्  devanagari letter da + devanagari sign virama + devanagari letter na + devanagari sign virama
    "b575877e5671eac8": "\u0926\u094d\u092c\u094d",  # cid 653 glyph00653 (gsub) -> द्ब्  devanagari letter da + devanagari sign virama + devanagari letter ba + devanagari sign virama
    "ef920963daec2317": "\u0926\u094d\u092d\u094d",  # cid 654 glyph00654 (gsub) -> द्भ्  devanagari letter da + devanagari sign virama + devanagari letter bha + devanagari sign virama
    "9f672dbb8ee66acb": "\u0926\u094d\u092e\u094d",  # cid 655 glyph00655 (gsub) -> द्म्  devanagari letter da + devanagari sign virama + devanagari letter ma + devanagari sign virama
    "0e3e8029c76eb591": "\u0926\u094d\u092f\u094d",  # cid 656 glyph00656 (gsub) -> द्य्  devanagari letter da + devanagari sign virama + devanagari letter ya + devanagari sign virama
    "8ef689d590a906b6": "\u0926\u094d\u0935\u094d",  # cid 657 glyph00657 (gsub) -> द्व्  devanagari letter da + devanagari sign virama + devanagari letter va + devanagari sign virama
    "d62cf591a175efb9": "\u0937\u094d\u091f\u094d",  # cid 658 glyph00658 (gsub) -> ष्ट्  devanagari letter ssa + devanagari sign virama + devanagari letter tta + devanagari sign virama
    "5bfcf7a3c957209d": "\u0937\u094d\u0920\u094d",  # cid 659 glyph00659 (gsub) -> ष्ठ्  devanagari letter ssa + devanagari sign virama + devanagari letter ttha + devanagari sign virama
    "e2eb792748e6ae0a": "\u0937\u094d\u091f\u094d\u0930\u094d",  # cid 660 glyph00660 (gsub) -> ष्ट्र्  devanagari letter ssa + devanagari sign virama + devanagari letter tta + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "de6a97bea13c5c30": "\u0937\u094d\u0920\u094d\u0930\u094d",  # cid 661 glyph00661 (gsub) -> ष्ठ्र्  devanagari letter ssa + devanagari sign virama + devanagari letter ttha + devanagari sign virama + devanagari letter ra + devanagari sign virama
    "cc23132d68a06c00": "\u0939\u094d\u0928\u094d",  # cid 662 glyph00662 (gsub) -> ह्न्  devanagari letter ha + devanagari sign virama + devanagari letter na + devanagari sign virama
    "0b6298b69a5f8e4a": "\u0939\u094d\u0923\u094d",  # cid 663 glyph00663 (gsub) -> ह्ण्  devanagari letter ha + devanagari sign virama + devanagari letter nna + devanagari sign virama
    "591cc65b7d2918a1": "\u0939\u094d\u0932\u094d",  # cid 664 glyph00664 (gsub) -> ह्ल्  devanagari letter ha + devanagari sign virama + devanagari letter la + devanagari sign virama
    "48c7602556f1f9e9": "\u0939\u094d\u0935\u094d",  # cid 665 glyph00665 (gsub) -> ह्व्  devanagari letter ha + devanagari sign virama + devanagari letter va + devanagari sign virama
    "10c10bc93187aeb3": "\u0921\u094d\u0917",  # cid 669 glyph00669 (gsub) -> ड्ग  devanagari letter dda + devanagari sign virama + devanagari letter ga
    "f3431eb1fde78393": "\u0915\u094d\u0915",  # cid 670 glyph00670 (gsub) -> क्क  devanagari letter ka + devanagari sign virama + devanagari letter ka
    "7ce983dacfda394b": "\u091f\u094d\u091f",  # cid 675 glyph00675 (gsub) -> ट्ट  devanagari letter tta + devanagari sign virama + devanagari letter tta
    "8c5712a2fa6a9f40": "\u091f\u094d\u0920",  # cid 676 glyph00676 (gsub) -> ट्ठ  devanagari letter tta + devanagari sign virama + devanagari letter ttha
    "7c7905d9ed5d8532": "\u091f\u094d\u0921",  # cid 677 glyph00677 (gsub) -> ट्ड  devanagari letter tta + devanagari sign virama + devanagari letter dda
    "c341c197bf7f181d": "\u091f\u094d\u0922",  # cid 678 glyph00678 (gsub) -> ट्ढ  devanagari letter tta + devanagari sign virama + devanagari letter ddha
    "7d8712801be48bd1": "\u0920\u094d\u0920",  # cid 679 glyph00679 (gsub) -> ठ्ठ  devanagari letter ttha + devanagari sign virama + devanagari letter ttha
    "3652815898285ec9": "\u0921\u094d\u0921",  # cid 680 glyph00680 (gsub) -> ड्ड  devanagari letter dda + devanagari sign virama + devanagari letter dda
    "21f731590a2ee604": "\u0921\u094d\u0922",  # cid 681 glyph00681 (gsub) -> ड्ढ  devanagari letter dda + devanagari sign virama + devanagari letter ddha
    "5c242231da75af35": "\u091b\u094d\u092f",  # cid 682 glyph00682 (gsub) -> छ्य  devanagari letter cha + devanagari sign virama + devanagari letter ya
    "7a4cf89d7a88a382": "\u091f\u094d\u092f",  # cid 683 glyph00683 (gsub) -> ट्य  devanagari letter tta + devanagari sign virama + devanagari letter ya
    "5b07d5e94639915b": "\u0920\u094d\u092f",  # cid 684 glyph00684 (gsub) -> ठ्य  devanagari letter ttha + devanagari sign virama + devanagari letter ya
    "9dc524f8c66cec8a": "\u0921\u094d\u092f",  # cid 685 glyph00685 (gsub) -> ड्य  devanagari letter dda + devanagari sign virama + devanagari letter ya
    "1edb3a439eaa1eb5": "\u0922\u094d\u092f",  # cid 686 glyph00686 (gsub) -> ढ्य  devanagari letter ddha + devanagari sign virama + devanagari letter ya
    "587eb7fa89695018": "\u0919\u094d\u092f",  # cid 687 glyph00687 (gsub) -> ङ्य  devanagari letter nga + devanagari sign virama + devanagari letter ya
    "82e11fcc867cc44b": "\u0920\u094d\u0921",  # cid 688 glyph00688 (gsub) -> ठ्ड  devanagari letter ttha + devanagari sign virama + devanagari letter dda
    "85c96a4f16f0a9d0": "\u0920\u094d\u0922",  # cid 689 glyph00689 (gsub) -> ठ्ढ  devanagari letter ttha + devanagari sign virama + devanagari letter ddha
    "c4b4704bd85fc6ed": "\u0922\u094d\u0922",  # cid 690 glyph00690 (gsub) -> ढ्ढ  devanagari letter ddha + devanagari sign virama + devanagari letter ddha
    "035f2fb7cbee3916": "\u0922\u094d\u0921",  # cid 691 glyph00691 (gsub) -> ढ्ड  devanagari letter ddha + devanagari sign virama + devanagari letter dda
}

# ``{outline digest: (as derived, corrected to)}`` -- the below-form ra
# described in the module docstring. Recorded rather than silently folded
# in, so a reader can see every place the table departs from the
# mechanical derivation.
BELOW_FORM_RA_CORRECTIONS: dict[str, tuple[str, str]] = {
    "25c6cdf6e65128e3": ("\u0930\u094d", "\u094d\u0930"),  # cid 89 र् -> ्र
}

# Outlines whose ``र्`` is the in-line half-form of ra rather than a repha, and so
# must not be given the repha reordering marker -- see the module docstring. The
# value is correct as it stands; only the reordering would be wrong.
#
# Identified by geometry, not by hand: this glyph's outline lies in the body of the
# line (y 629-1434), while every repha in this font sits above the headline
# (y 1247-2092). Its neighbours 224 म्, 225 य्, 227 ल् and 229 व् are the rest of
# the half-form run it belongs to.
IN_LINE_RA_DIGESTS: frozenset[str] = frozenset({"dcc59849863c7ad9"})  # cid 226 र्


def outline_digest(font: Any, glyph_name: str) -> str | None:
    """Hash a glyph's contours, or ``None`` if it has none.

    A subset blanks the glyphs it does not use, and a blanked glyph proves
    nothing about the font, so "no contours" is not a mismatch -- it is simply
    not evidence. Truncated to 16 hex characters: this guards against a
    different *drawing*, not against a forged one.

    Deliberately the same algorithm as
    :func:`likhit.extractors.lohit._outline_digest` rather than an import of it,
    so that neither module owns a primitive the other's correctness depends on.
    There is no import cycle to avoid -- :mod:`likhit.extractors.lohit` imports
    nothing from this package, so ``kalimati -> kalimati_reference -> lohit`` is a
    DAG, and an earlier version of this docstring was wrong to say otherwise.
    Duplicating a correctness-critical function is a real cost, paid for by
    ``tests/test_kalimati_reference.py`` asserting the two agree on real glyphs:
    the table's keys were computed with exactly this function, and a silent
    divergence would invalidate every lookup.
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


def kalimati_reference_map(
    font: Any, skip: Container[int] = frozenset()
) -> dict[int, str]:
    """``{CID: Unicode}`` for the glyphs of ``font`` this reference recognises.

    ``skip`` holds CIDs the caller has already resolved from the font itself.
    Those are authoritative -- the font's own ``cmap`` beats any reference -- and
    skipping them also keeps this from hashing outlines whose answer is already
    known, which is most of them.

    Only glyphs whose outline the reference carries are returned, so a font from
    another family yields an empty map without needing to be recognised or
    rejected by name.
    """

    if not _has_reference_units_per_em(font):
        # A digest is taken over raw font-unit coordinates, so it is only
        # comparable at the em square the table was built on. Refusing here
        # states that precondition instead of leaving it implicit in the fact
        # that a rescaled outline happens not to collide.
        return {}

    try:
        glyph_order = font.getGlyphOrder()
    except Exception:  # noqa: BLE001 - can't enumerate glyphs, so claim nothing
        return {}

    recovered: dict[int, str] = {}
    for gid, glyph_name in enumerate(glyph_order):
        if gid in skip:
            continue
        digest = outline_digest(font, glyph_name)
        if digest is None:
            continue
        value = OUTLINE_TO_UNICODE.get(digest)
        if value is not None:
            recovered[gid] = value
    return recovered


def in_line_ra_cids(font: Any, cids: Container[int]) -> set[int]:
    """Which of ``cids`` are the in-line ra, whose ``र्`` must not be reordered.

    Pass only the CIDs whose recovered value begins with ``ra + virama`` -- at
    most a handful per font -- so this costs a few extra outline hashes rather
    than a second pass over the whole glyph order.

    Returns an empty set for a font this reference does not recognise, which is
    the same "claim nothing" default as :func:`kalimati_reference_map`.
    """

    if not _has_reference_units_per_em(font):
        return set()
    try:
        glyph_order = font.getGlyphOrder()
    except Exception:  # noqa: BLE001 - can't enumerate glyphs, so claim nothing
        return set()

    return {
        gid
        for gid, glyph_name in enumerate(glyph_order)
        if gid in cids and outline_digest(font, glyph_name) in IN_LINE_RA_DIGESTS
    }


def _has_reference_units_per_em(font: Any) -> bool:
    try:
        return bool(font["head"].unitsPerEm == REFERENCE_UNITS_PER_EM)
    except Exception:  # noqa: BLE001 - unreadable head means unknown geometry
        return False
