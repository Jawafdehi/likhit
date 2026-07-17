# Extracting Nepal Police CIB press releases — what likhit needs

Status: **implemented** (2026-07-17). Written 2026-07-16 while evaluating cib.nepalpolice.gov.np as an ingestion source for Jawafdehi. All findings below are first-hand (PDFs downloaded and dissected with the likhit `.venv` — `fitz` + `npttf2utf`).

Implementation summary (2026-07-17):

- **Part A** — `font_classifier.classify_ocr_page`/`scan_ocr_pages` detect scanned-decoy and image-only pages; `FontBasedStrategy` suppresses decoy pages, sets `RawDocument.needs_ocr_pages`, and raises `errors.ScannedPdfError` (a catchable `ExtractionError`) when a document has no recoverable text. Validated on all sampled CIB releases: 3 decoy-layer → `scanned_decoy_text`, the rest → `image_only`; none emit the `qt+:` junk.
- **Part B** — `font_based.detect_content_legacy_fonts`/`choose_legacy_map` rescue mislabeled bare-core legacy fonts via a `>=4`-char dictionary + penalty gate (never Devanagari-ratio, per §2). Proven to decline on CIB under all five maps and accept genuine Preeti.
- **npttf2utf** — the invalid-escape `SyntaxWarning` is suppressed at the `legacy_maps._get_mapper` import site (upstream raw-string PR still warranted).
- **Tests** — `tests/test_scanned_pdf_detection.py` (CI, PII-free synthetic fixtures via `tests/synthetic_pdfs.py`) and `tests/integration/test_cib_pdfs.py` (skip-when-absent over the git-ignored real originals).

## TL;DR — the premise changed, read this first

The working assumption was: "CIB press releases are born-digital Preeti PDFs; teach likhit the Preeti *variant* they use and we get clean text." That assumption is **wrong for CIB**, and building a new font map would be wasted effort.

What the CIB PDFs actually are:

- **Every** sampled press-release PDF is a **full-page scanned raster image** (JPEG / `DCTDecode`, ~200–300 DPI), produced by desktop scanner software (`HP Scan Extended Application`, `AVScan X`, Canon `IJ Scan Utility`, some re-run through `iLovePDF`).
- Roughly a quarter of them *also* carry a **text layer**, which my first pass mis-read as "born-digital Preeti." On dissection that text layer is **junk**: it is set in a **non-embedded standard `Helvetica` / `WinAnsiEncoding` core font with no `ToUnicode`**, and its bytes are Preeti-keystroke ASCII that **do not decode to real Nepali under any of the five npttf2utf maps** (Preeti, Kantipur, PCS NEPALI, FONTASY_HIMALI_TT, Sagarmatha). It is an artifact of whatever tool flattened the page to an image; it is not a recoverable Preeti text run.
- The **real, readable content lives only in the raster** — and the raster is clean, legible Devanagari (letterhead, press-release number, body, and a "पक्राउ भएका व्यक्तिको विवरण" table with mugshots + accused PII). A vision-OCR pass reads it cleanly; `pdftotext`/`fitz` text extraction never will.

So the correct extraction path for CIB is **OCR**, not legacy-font conversion. likhit's job here is therefore **not** "add a variant map" — it is:

1. **Stop trusting the junk text layer.** Detect the "full-page image + non-embedded core-font text layer that fails Nepali validation" anti-pattern and route the page to OCR instead of emitting garbage. This is the fix that directly unblocks CIB.
2. **Generalize legacy-font detection from name-based to content-based** so genuinely-mislabeled *embedded* Preeti fonts (from *other* sources — this is the real "support those variants" work) get converted. This will correctly *decline* on CIB (no map passes the validity gate) and defer to (1).

## Evidence

### 1. The text layer is a non-embedded core font, not Preeti

`fitz` on `news/391` (and 346, 392 — identical) reports exactly one font, and its PDF object is a bare core-font reference with no descriptor, no embedded program, no `ToUnicode`:

```
-- font xref 6  info=(6, 'n/a', 'Type1', 'Helvetica', 'F3', 'WinAnsiEncoding')
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>
ToUnicode key: ('null','null')      FontDescriptor: ('null','null')
```

Per-char codes are plain ASCII (`q`=113 `t`=116 `+`=43 `:`=58 → the raw stream really is `qt+:`). Because `/BaseFont` is generic `Helvetica`, likhit's **name-based** classifier never fires (see below), so the bytes pass through untouched as `qt+:` mojibake.

### 2. No existing map decodes that layer to Nepali

I extracted the text layer four ways (`get_text()` default, `sort=True`, `words` re-ordered by position, `blocks`) and ran all five npttf2utf maps against each, scoring by presence of anchor words we *know* are in these releases from the filenames (भ्रष्टाचार, प्रतिवादी, पक्राउ, प्रहरी, अनुसन्धान, ब्यूरो, केन्द्रीय …):

```
mode=default        Preeti/PCS/Kantipur  anchors=0
mode=sort           Preeti/PCS/Kantipur  anchors=0
mode=words_reorder  Preeti/PCS/Kantipur  anchors=0
mode=blocks         Preeti/PCS/Kantipur  anchors=0
```

Zero anchor hits under every combination. (Raw deva-*ratio* after mapping looks high, ~0.95, but that is a mirage — Preeti/Kantipur/Sagarmatha share a map, so any of them emits Devanagari code points; the *words* are nonsense. deva-ratio alone is a bad scorer; use anchor/dictionary validation.)

### 3. The page is 100% image; the text layer is decoration

```
cib_391  page 594x853pts   image xref4 2476x3556px placed 100% of page   text-layer chars 1516
cib_346  page 595x842pts   image xref4 1653x2338px placed 100% of page   text-layer chars 1587
cib_392  page 594x886pts   image xref4 2476x3692px placed 100% of page   text-layer chars 1282
```

### 4. The raster is clean, OCR-able Nepali

Rendered `cib_346` page (see `tests/fixtures/cib/view_346.jpg`): letterhead "नेपाल सरकार / प्रहरी प्रधान कार्यालय / केन्द्रीय अनुसन्धान ब्यूरो", "प्रेस विज्ञप्ति नं ४०", a cooperative fund-embezzlement (सहकारी रकम हिनामिना) narrative with amounts and BS dates, and a bottom table of arrested persons with photographs, home addresses, ages, and arrest dates. This is exactly the structured content a vision-LLM OCR pass extracts well — and exactly the accused-not-convicted PII that the ingestion policy has to gate.

## Where likhit is today (the actual bug)

Detection is **purely name-based**, so any legacy font whose `/BaseFont` is generic or mislabeled is invisible to it.

- `src/likhit/extractors/legacy_maps.py:10` — `_REGISTRY` maps substrings ("preeti", "kantipur", "pcs nepali", "himali", "sagarmatha") → map keys.
- `src/likhit/extractors/legacy_maps.py:26` — `_match_font()` only substring-matches the `/BaseFont` name. `_match_font("Helvetica")` → `None`.
- `src/likhit/extractors/font_classifier.py:17` — `classify_font()` returns `"legacy_remap"` only if `is_legacy_font(name)`; otherwise `"correct"`.
- `src/likhit/extractors/font_based.py:485` — `strategy = font_strategies.get(base, "correct")`; for `base="Helvetica"` this is `"correct"`, so `_convert_span_text` returns the raw `qt+:` bytes verbatim.

Net: likhit currently emits the junk text layer as if it were correct Unicode. Confirmed live — `FontBasedStrategy().extract_text(cib_391.pdf)` returns `'h\n\nqt+:\n\n$TTDtit\n\n…'`.

## Proposed fix

### Part A — junk-text-layer guard → route to OCR (this is the CIB unblock)

Add a page-level classification that recognizes the scanned-page-with-decoy-text-layer pattern and suppresses the text layer rather than emitting it.

Fire when **all** hold for a page:

- one image covers ≥ ~95% of the page area (reuse `get_image_rects` coverage math), and
- the page's text fonts are **non-embedded standard core fonts** — `/BaseFont` in {Helvetica, Arial, Times*, Courier*} with `StandardEncoding`/`WinAnsiEncoding`, **no `FontDescriptor`/`FontFile`**, **no `ToUnicode`**, and
- the extracted text fails Devanagari validation (near-zero valid Nepali by the anchor/dictionary check, or high `_text_quality_penalty`).

Then: mark the page `image_only` / `needs_ocr`, drop its text fragments, and surface that on the returned `RawDocument` (a per-page flag or a raised, catchable "scanned, needs OCR" signal) so the ingestion caller runs its OCR path instead of storing garbage.

Touch-points: a new strategy value ("scanned_decoy_text") in `font_classifier.py`; `font_based.py:_convert_span_text` returns `""` for it; `font_based.py:_extract_raw_document` propagates an `image_only`/`needs_ocr` marker instead of `raise ExtractionError("No text content found")`.

### Part B — content-based legacy-font detection (the generalized "variants" fix)

Make `classify_font` fall back to content when the name matches nothing:

- if a span's font resolves to no `_REGISTRY` entry **and** the span text is majority printable-ASCII inside a document that is otherwise Devanagari (or the font is a bare Latin core font), attempt each registered map,
- score each candidate with a **positive Nepali-validity** metric (Devanagari ratio **plus** anchor/common-word/matra hits) **minus** the existing `_text_quality_penalty` (`font_based.py:137`), and
- accept a remap only if the best candidate clears a validity threshold; otherwise leave the text unmapped.

This is what actually "supports the variants": embedded-but-mislabeled Preeti/Kantipur/PCS fonts from other sources get rescued. And it composes safely with Part A — on CIB, **no** candidate clears the gate (proven in Evidence §2), so B declines and A takes over → OCR. B must never resurrect the CIB junk layer.

### Part C — regression fixtures

Add the four dissected samples (see paths below) as fixtures and assert:

- `cib_489_scan.pdf` (pure scan, no text layer) → `image_only`/`needs_ocr`.
- `cib_346/391/392.pdf` (scan + decoy text layer) → text layer suppressed, `image_only`/`needs_ocr`, and **never** silent text extraction of `qt+:`-class mojibake.
- (Backfill later) a genuine embedded-mislabeled-Preeti PDF from another source → Part B converts it to valid Nepali above threshold.

### Minor: npttf2utf ships with a bug

`npttf2utf/base/preetimapper.py:210,215,216` raise `SyntaxWarning: invalid escape sequence` (`'b\w'`, `'b\lj'`, `'b\j'` should be raw strings). Since the stack is ours to fix, worth a one-line PR upstream/vendored.

## Real example paths

Source (canonical, stable): CIB Django site, `nginx/1.18.0`, no Cloudflare, no robots.txt, no sitemap. Detail pages are sequential integers `…/news/<id>/`; the PDF hangs off a django-filer `/media/filer_public/<hex>/<hex>/<uuid>/<slug>.pdf` URL.

| id | detail page | media PDF | flavor |
|----|-------------|-----------|--------|
| 489 | https://cib.nepalpolice.gov.np/news/489/ | https://cib.nepalpolice.gov.np/media/filer_public/b2/41/b241ab15-407c-4322-98c9-d2ca33d56835/gen-z_aandlnk_krmm_phrr_22_vrss_kd_sjy_pek_kd_pkru_2083-03-31.pdf | pure scan (HP Scan), no text layer |
| 346 | https://cib.nepalpolice.gov.np/news/346/ | https://cib.nepalpolice.gov.np/media/filer_public/94/e3/94e37da0-9a11-4399-8233-f010302b53c3/skvyr_shkrk_rkm_hnmn_grn__sclkhr_pkru_2081-02-27.pdf | scan + decoy Helvetica text layer |
| 391 | https://cib.nepalpolice.gov.np/news/391/ | https://cib.nepalpolice.gov.np/media/filer_public/cf/1e/cf1efe75-51ff-4cae-af19-24ad3501966f/bhrssttcr_mddhm_phrr_prtvd_pkru_2081-12-11.pdf | scan + decoy Helvetica text layer |
| 392 | https://cib.nepalpolice.gov.np/news/392/ | https://cib.nepalpolice.gov.np/media/filer_public/23/1a/231aac45-3fe5-4f1b-a3f2-929bb9dede98/hty_pshct_sdhk_bhssm_lkchp_bsk_aprdh_17_brsspch_pkru_2081-12-26.pdf | scan + decoy Helvetica text layer |

Local fixtures (this repo, **git-ignored** — see below): `tests/fixtures/cib/cib_489_scan.pdf`, `cib_346.pdf`, `cib_391.pdf`, `cib_392.pdf`, and the rendered `view_346.jpg`.

Press-release index (single server-rendered HTML table, ~385 entries, ids 97–489 as of 2026-07-16): https://cib.nepalpolice.gov.np/news/press-releases/

## Caveats

- **PII / do-not-commit.** The fixtures show photographs, names, and addresses of **arrested — not convicted — persons**. `tests/fixtures/cib/` is git-ignored for this reason. If these become committed test data, redact faces/PII first, and treat the same sensitivity in any ingestion of CIB content downstream (naming un-convicted accused is the central legal/defamation risk of this source).
- **Site is flaky under load.** Concurrent fetching drew frequent connection resets / timeouts from the origin; a crawler must be polite (1–2 concurrent, backoff, retries).
- Sample size for the "scan + decoy layer" flavor is three; all three were identical in mechanism, which is why I treat it as systematic — but a wider sweep should confirm before hard-coding thresholds.
