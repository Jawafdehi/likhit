# Changelog

All notable changes to `likhit` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file starts at 0.1.8. For earlier releases, see the
[commit history](https://github.com/Jawafdehi/likhit/commits/main/) and the
[git tags](https://github.com/Jawafdehi/likhit/tags).

## [Unreleased]

### Added

- **`redact_pdf(..., trusted_unicode=True)` / `likhit-redact pdf --trusted-unicode`** for a
  PDF the caller rendered itself from Unicode text: a page with a low Devanagari share (an
  English news article) is redacted as Unicode text instead of refusing as a legacy-font
  page. Scanned, blank and image-covered pages are unaffected; the journal records the flag
  and `trusted_unicode_pages`. Default off -- never for third-party PDFs.

### Fixed

- **`redact_pdf(..., transcript=...)` places list-valued and line-wrapped values instead of
  holding the document** (JAW-167). A removed value the transcript wrote as a list -- two
  phone numbers joined by `/`, `,`, `;`, a danda, a space or `वा`/`र`, two bank accounts
  joined by `र`, two addresses -- is placed part by part when the page does not print it
  joined; it counts as placed only when every part is found exactly, and one printed digit
  run is never credited to two required occurrences, not even when two reads see the same
  print once as parts and once whole. Parts must each be a whole value of the kind (a
  full phone number, an account of 9+ digits, an address); accounts split only at a list
  word, a comma or a semicolon, never at a slash, space or hyphen that may sit inside one
  account. Values wrapped over two lines are matched in both the text layer and the OCR
  read: an address at any character (with or without a hyphen at the break; not when the
  next line continues it, e.g. `.np`), a number of 6+ digits at one bare line break when
  no other digit is glued to either end. The post-redaction verification and residue
  check are unchanged.
- **`redact_pdf(..., transcript=...)` no longer spends minutes diffing a long transcript.**
  `transcript_removed_values` diffed the whole transcript against its redaction with one
  character-level `difflib.SequenceMatcher`, whose `find_longest_match` is quadratic:
  production backfills spent 20-45 minutes per court order inside it. A synthetic 300 KB
  transcript with 232 values now takes ~0.7 s (it took ~20 min; 80 KB took 80 s). The
  longest-match search is replaced by an exact k-gram-indexed one, so the opcodes -- and the
  guided values -- are identical to difflib's, not approximately so: checked against
  difflib on generated transcripts, adversarial repetitive strings and the POC corpus.
- **Four identity-card numbers court orders cite are now redacted at `strict` and counted
  by the residue check** (JAW-167): a foreign national's citizen registration with their
  embassy (`भारतीय नागरिक रजिष्ट्रेसन नं.`, new `[REDACTED:FOREIGN-REGISTRATION-NO]`), a
  driving licence (`सवारी चालक अनुमति पत्र नं.`, new `[REDACTED:DRIVING-LICENCE-NO]`), any
  other person's identity card (`परिचय पत्र नं.` -- social security, disability, freed
  Haliya, provident fund, police; new `[REDACTED:IDENTITY-CARD-NO]`), and a Nepali voter
  card written as digits (`मतदाता परिचय पत्र नं.`, existing `[REDACTED:VOTER-ID-NO]`).
  Land-deed, company, exam-candidate and case/file registration numbers stay visible, as
  do a taxpayer card (`करदाता परिचय पत्र नं.`, a PAN) and a blank form's numbered fields.

- **Citizenship numbers behind the spelled-out label `नागरिकता प्रशासन नं` are now redacted and counted** (JAW-167, ag/60206). The scanner also counts a value wrapped onto the next line after `नं`, which the remover already took.
- **A table column headed by a citizenship label now redacts cells that carry the issuing district too** (`ना.प्र.नं.र प्राप्त गरेको जिल्ला` → `| १२३४५/६ ७ ओखलढुंगा |`), with a four-digit floor for old certificates. This applies under a header only; a same-row label still needs a whole-cell value.

### Added

- **`redact_pdf(..., ocr=True, transcript=...)` / `likhit-redact pdf --ocr --transcript`
  redact scanned and legacy-font pages instead of refusing them** (JAW-167). Scans are
  located with Tesseract `nep+eng` character boxes and burned into the image pixels with the
  whole text layer removed; legacy-font pages are replaced by a burned render. Every value
  the transcript's text redaction removes must be placed exactly on its page or the document
  is refused, and the written file is re-OCR'd and re-burned until the rules find nothing.
  On 23 archive PDFs (439 pages) it covered 80 of 84 hand-verified sensitive numbers (the 4
  misses are shared with the text path) and a fresh OCR read of the outputs found 0 values.
  New module `likhit.privacy.pdf_ocr`.

### Fixed

- **Strict redaction now reads four more spellings of the abbreviated citizenship
  label: `ना.प्र.प्र.नं.`, `ना.प्र.न.न.`, `ना.प.प.न.` and the naturalised-citizenship
  `अ.ना.नं.`.** Special Government Attorney charge sheets write them in their party
  lists, and none was redacted, so 105 citizenship numbers in 16 published charge sheets
  stayed public (JAW-164). Over the 24 affected published transcripts, the branch removes
  100 citizenship values (main: 0), and the abbreviated labels still followed by a digit
  fall from 103 to 6: five two-digit serials, below the rule's three-digit floor, and one
  `ना.प्र. ####/###` with no number marker.

- **`scan_text` no longer counts a birth-registration count or a `dob…` word as a date
  of birth.** Its `date_of_birth` label matched bare `जन्मदर्ता` ("births registered |
  1234" in municipal audit reports) and any word starting `dob`, values no rule removes,
  so JawafdehiAPI's residue gate withheld whole transcripts (JAW-53). The label now reads
  what the remover's does, plus a birth-registration number; scanner and both removers now also read `जन्म दर्ताको मिति`. Over 421 staged documents
  the gate's withheld count fell from 81 to 14.

### Added

- **Surya 2.5 as an OCR engine** (`likhit.surya_ocr`, JAW-51). Off by default.
  `LIKHIT_OCR_ENGINE=surya` plus `LIKHIT_SURYA_BASE_URL` (an OpenAI-compatible Surya
  server, e.g. llama.cpp's `llama-server`) sends each scanned page with Surya's own
  prompt, image first, resized like Surya's client, and turns its HTML answer
  (Surya 2.5's compact block headers or `<div>` blocks) into text with Markdown
  headings and tables. The answer then goes through the same refusal gate as any
  other engine, and through the rules and Tesseract cross-check when those are turned
  on (`LIKHIT_OCR_POSTPROCESS=1`, `LIKHIT_OCR_SECOND_READER=tesseract`; without them
  there is no digit-review marker). Disputed digit spans are flagged in the
  `likhit:ocr-review` marker, and the re-read is skipped because Surya only
  transcribes. A page that hits `LIKHIT_SURYA_MAX_TOKENS` fails instead of being kept
  half-read. Optional: `LIKHIT_SURYA_MODEL`, `LIKHIT_SURYA_API_KEY`,
  `LIKHIT_SURYA_TIMEOUT`. No new dependencies.

- **Opt-in multi-pass correction of vision-OCR pages** (`likhit.ocr_postprocess`).
  Off by default: output is unchanged unless one of these is set.
  - `LIKHIT_OCR_POSTPROCESS=1` — deterministic rules after the refusal gate: BS
    dates whose danda was read as a digit (`2078701117` → `2078।01।17`), case-type
    codes and section spacing, amount figure vs amount in words, repetition loops.
  - `LIKHIT_OCR_SECOND_READER=tesseract` — a second, free reading from a local
    `tesseract` (`LIKHIT_OCR_TESSERACT_LANG`, default `nep`). Digit spans the two
    readings disagree on are settled by value-shape validators; ties keep the first
    reading and add `<!-- likhit:ocr-review spans=N -->` to the page.
  - `LIKHIT_OCR_REREAD=1` — ask the page's vision model only about those disputed
    spans (one extra call per disputed page).

  Measured on 112 scanned Attorney General charge-sheet pages (JAW-51): Azure Read's
  digit-field recall goes from 82.2% raw to 94.9% with the rules and 95.2% with a
  Tesseract cross-check added. The benchmark reached 97.3% when the disputed spans
  were also re-read, but that run used a third reader as well, so the re-read gain
  for this exact configuration is not yet measured. Callers can also pass an `OcrCorrection` to
  `_run_page_ocr` directly.

### Fixed

- **`strict` redaction and the residue scan now catch all-digit passport numbers**
  (JAW-79). The passport rule only knew the Latin-prefixed shape (`PA1234567`), so a
  number written in digits alone passed both redaction and the residue check, in either
  digit script, after `राहदानी नं.`, `राहदानी नम्बर`, `पासपोर्ट नं.` or `Passport No.`, in
  a list, or on the line after its label. Measured on the Patan High Court charge sheet
  that surfaced it (`ag/115641`): before, 1 of 5 passport numbers removed and 1 reported;
  after, 5 of 5 removed and 0 reported left. A digit-only value needs a number marker or
  a colon after the label, must be 6-9 digits, and is refused when a date or case-number
  group, a count word or a money word follows it. `scan_text` now counts passports
  through the remover (`redact.count_rule_values`), one per number in a list. Checked the
  sibling labels for the same gap and closed three:
  - citizenship behind `ना.प्र.पत्र नं.` (the प्रमाणपत्र field spelled out);
  - Nepali national identity card numbers (`राष्ट्रिय परिचयपत्र नं.`, `राष्ट्रिय परिचय
    नम्बर`, `National ID No.`, `NID No.`), a new kind at `strict` with its own
    placeholder `[REDACTED:NATIONAL-ID-NO]`;
  - the second and later bank accounts in a list under one `खाता नं.` label.
  PAN is unchanged: no tier removes it, by design.

- **`strict` redaction and the residue scan now catch the identity numbers a Kathmandu
  District Court charge sheet and two verdicts kept** (JAW-73, JAW-74). Measured on those
  three documents: before, 0 of 17 citizenship numbers, 0 of 3 Indian ID numbers, 0 of 7
  refugee card numbers, 0 of 14 IMEIs, 9 bank accounts and 5 mobiles were removed or
  reported; after, all are removed and the scanner reports 0 left. What changed:
  - citizenship: the abbreviated label as PDFs garble it (`ना.प्रर्.नं.`, `र्नं.`) or
    write it without the anusvara (`ना.प्र.न.`), three-digit serials behind that label,
    a value pushed onto the next table row by an empty-cell wrap, and the unlabelled
    `जारी भएको ######/### नम्बरको <name>को नाममा जारी भएको नागरिकता…` sentence;
  - new kinds, all at `strict` with their own placeholders: Indian voter ID (EPIC,
    `[REDACTED:VOTER-ID-NO]`), Aadhaar (`[REDACTED:AADHAAR-NO]`), refugee identity card
    (`[REDACTED:REFUGEE-ID-NO]`) and phone IMEI (`[REDACTED:IMEI-NO]`), each anchored on
    its label;
  - bank accounts behind `खाता न.`, straight after a bank's name
    (`कुमारी बैंक लि.को ################`) or followed by `न.को खाता` / `नं. मा ट्रान्सफर`;
  - mobiles glued to a contact name ending in a digit (`NAME2` + number) or typed with
    one digit too many before `न.को मोबाइल`.
  Cheque numbers, amounts, case numbers and dates are tested to stay. `scan_text` counts
  the new shapes through the same rules and guards that remove them
  (`redact.count_rule_values`), so the residue check cannot report a value no rule takes.

- **The repha over `र्ने` and its siblings is kept in Kalimati PDFs** (JAW-53). Kalimati
  ligates `े` + repha into one glyph (glyph00566, GSUB `uni0947 glyph00091`), and the
  repair let a metric guess of bare `े` outrank that GSUB reading, so `गर्ने` came out
  `गने` in 479 of 739 Law Commission PDFs and in court-order PDFs too. GSUB now outranks
  the guess, also as a component of larger ligatures (`ेर्ँ`, `ेर्ं`), and a matra +
  repha value is always handed to the reorder as a marker.
- **Legacy fonts in two more Word shapes** (JAW-49). An RTF saved under a `.doc`
  name now has its font table read, so a whole-Preeti RTF is converted instead of
  published as keystrokes; and `PCS NEPAL` (no final I) routes to the PCS NEPALI map.
- **Two account and identifier shapes the strict tier left public** (JAW-49). The
  en dash and em dash Word's autocorrect puts between digit groups now separate a value
  like a hyphen (`खाता नं.१९–००००४–०`), and a bank code may stand one space apart from its
  digits (`खाता नं. KL 2035931`). The en-dash accounts were in published court-order
  transcripts, because the scanner did not report them either.
- **Word documents with runs in a legacy keystroke font** (Preeti, PCS NEPALI,
  Kantipur, Fontasy Himali and the rest of the PDF path's font registry) now
  convert those runs to Unicode Devanagari. Before, a Kalimati court order with a
  defendant's name typed in PCS NEPALI came out as `cR%]nfn /fosf] gflt`; it now
  reads `अच्छेलाल रायको नाति`. The repair keys on the font each run is drawn in
  (`w:rFonts` with style and theme inheritance), never on the shape of the text, so
  real English in the same document is untouched. `.docx` goes through a new
  `LegacyFontDocxConverter`; a legacy `.doc` that names a legacy font is converted
  to `.docx` with LibreOffice first and repaired the same way, and falls back to
  the existing plain-text path when LibreOffice is absent or nothing needed
  repair. Documents without a legacy-font run convert exactly as before.

## [0.2.0] - 2026-10-16

Two new subpackages, and the first release to reach PyPI since 0.1.7 — 0.1.8
was tagged but never published, so everything in that entry ships here too.

Releases are now cut automatically: merging a version bump to `main` tags it
and publishes it. See [docs/publishing.md](docs/publishing.md).

### Changed

- **`likhit-save` now redacts transcripts by default**, at the `strict` tier.
  Conversion previously always wrote the unredacted text, and redaction was
  only reachable by converting first and then running `likhit-redact` over the
  result — so the unredacted artifact was always written to disk first. Passing
  `--redact none` restores the old behaviour exactly, and is logged at WARNING
  rather than refused. No tier removes personal names; see
  `likhit.privacy.modes` for why that is a deliberate limit and not a gap.

### Added

- **`likhit.privacy`** and the `likhit-redact` CLI — removal of personal data
  from transcripts, over four tiers (`none`, `identifiers`, `contact`,
  `strict`). `likhit-redact pdf` takes a PDF and writes a genuinely redacted
  PDF; `likhit-redact scan` reports without writing; `likhit-redact redact`
  writes a redacted copy of a tree.
- **`likhit.quality`** and the `likhit-audit` CLI — transcript quality
  auditing, scoring a converted tree so bad extractions can be found without
  reading them.
- `likhit[quality]` and `likhit[privacy]` extras. Both are currently empty and
  install nothing extra; they name the separable surfaces so a downstream
  requirements file can record which part of the package it depends on.
- Rendered Markdown is anchored to its source pages, so a line in the
  transcript can be traced back to the page it came from.
- OCR acceptance checks, and documented OCR runs both locally and against
  Bedrock.

### Fixed

Roughly sixty extraction fixes, overwhelmingly in the legacy-font PDF path.
The themes, rather than the list:

- **Legacy-map selection.** Which Preeti-family map wins is no longer decided
  by dictionary ordering, font names alone, or a raw garble ratio; the Latin-
  side vetoes, the stranded-bracket tell and word-structure checks all feed the
  choice. Fonts whose CMap is embedded, inline in a descendant dictionary, or
  outline-keyed (Kalimati, Lohit-Devanagari, Mangal, Kokila) now resolve.
- **Garble measurement.** Ordinary Nepali, ASCII list markers, acronyms and
  bare digits no longer read as garble, and unmappable CIDs are marked rather
  than silently destroyed so the heuristics can still see them.
- **Tables and layout.** Ruled frames no longer make a page emit everything
  twice, swallowed sub-tables keep their rows and columns, erased numeric cell
  boundaries are rebuilt from glyph origins, and page furniture is stripped by
  line rather than by paragraph.
- **Legacy `.doc`.** Anchored on LibreOffice, with the profile path built as a
  proper file URI and system `antiword` pinned to UTF-8.

### Internal

- The test suite runs in 21s parallel (was 136s serial), with more tests and no
  coverage removed. Every tuning constant in `src/` is now pinned by a test.

## [0.1.8] - 2026-08-06

The extraction-quality release: three classes of Nepali PDF that previously
produced mojibake, a duplicated garble layer, or no result at all now convert
correctly. Also removes an unwanted network call on import — see **Changed**.

### Changed

- **Sentry error reporting is now opt-in.** Previously `likhit` initialized
  Sentry at import time with a hardcoded DSN, so merely importing the library
  phoned home. It is a library embedded in other services and must not report by
  default, nor inherit an unrelated host's generic `SENTRY_DSN`. Reporting now
  activates only when `LIKHIT_SENTRY_DSN` is set, and is silent otherwise.
  If you relied on the previous behaviour, set `LIKHIT_SENTRY_DSN` to restore
  it. ([#21](https://github.com/Jawafdehi/likhit/pull/21))

### Added

- `likhit.errors.ScannedPdfError` (subclass of `ExtractionError`), raised when a
  PDF has no recoverable text layer and needs OCR. It carries
  `needs_ocr_pages`, the 1-based page numbers requiring OCR, so callers can
  route the document to their own OCR path instead of storing junk output.
  ([#20](https://github.com/Jawafdehi/likhit/pull/20))

### Fixed

- **Scanned CIB press releases no longer emit mojibake.** Nepal Police CIB
  releases are full-page scanned rasters, and roughly a quarter carry a
  non-embedded core-font "decoy" text layer whose bytes decode to garbage under
  every legacy map. The name-based font classifier passed that layer through as
  if it were correct Unicode. Image-dominant pages are now classified by their
  structural signature (core font, no `ToUnicode`, near-zero Devanagari, garbled
  layer), decoy pages are suppressed, and documents with nothing recoverable
  raise `ScannedPdfError` instead. Genuinely mislabeled bare-core legacy fonts
  from other sources are separately rescued by content-based detection, gated on
  a Nepali-dictionary hit count rather than Devanagari ratio — which declines on
  the CIB decoy layer under all five maps, so it never resurrects that junk.
  ([#20](https://github.com/Jawafdehi/likhit/pull/20))

- **Legacy-font garble is no longer appended alongside clean Nepali text.** Some
  born-digital Nepali PDFs embed the same text twice in different encodings — a
  correct Kalimati Type0/Identity-H layer *and* legacy Preeti/Kalimati-as-WinAnsi
  byte-mapped layers on the same baseline. The quality heuristics only recognized
  replacement chars and private-use glyphs, but the legacy mis-map emits
  valid-but-never-used Devanagari signs (short-O `U+094A`, nukta consonants
  ऩ/ऱ/ऴ), which scored as clean and survived the merge. Those signs now count
  against text quality, and unpaired fragments that are dense invalid-sign
  content are dropped. Candra-O (`U+0949` ॉ) is deliberately excluded, as it
  occurs in legitimate loanwords (डॉलर, कॉल, डॉक्टर). Measured on real CIAA
  case PDFs: a 2-page press release goes from 8 invalid signs to 0, and a
  60-page verdict drops ~30%.
  ([#19](https://github.com/Jawafdehi/likhit/pull/19))

- **Ligature analysis can no longer hang forever.** `_analyze_gsub` resolved GSUB
  ligature substitutions with a `while changed` fixpoint. When two rules write
  the same output glyph with different resolved strings, the result flip-flops
  between them and the loop spins at 100% CPU without ever returning — observed
  on born-digital PDFs embedding several unrelated fonts, at over 90 minutes of
  CPU with no result. A convergent fixpoint resolves at least one new glyph per
  pass, so the loop is now bounded at `len(ligature_rules)` passes: a
  non-convergent GSUB degrades gracefully and logs a warning naming the offending
  font. Convergent fonts are unaffected (verified byte-identical), and the hang
  case becomes a ~3.5 minute conversion.
  ([#22](https://github.com/Jawafdehi/likhit/pull/22))

### Performance

- Table-heavy PDFs with a broken `ToUnicode` CMap convert **43.9% faster**.
  Such documents need a second, repaired extraction pass whose tables always win
  when present, so native table detection is now skipped during the unrepaired
  pass and run lazily only if the repaired pass finds none. Output is
  byte-identical on the four table-heavy documents measured.
  ([#24](https://github.com/Jawafdehi/likhit/pull/24))

### Internal

No effect on the published library; listed for contributors.

- Migrated the toolchain from Poetry to uv and the Astral stack: `uv build`
  replaces `poetry build`, `ruff format` replaces `black`, and `ty` runs as an
  advisory type check. The published artifacts were diffed against the last
  Poetry-built 0.1.7 — the wheel is identical and the sdist differs only by
  `.gitignore`. ([#27](https://github.com/Jawafdehi/likhit/pull/27))
- Added a real government-document benchmark corpus with regression gating, plus
  a Devanagari well-formedness metric that can actually distinguish repaired text
  from mojibake — `likhit`'s own quality score cannot, and rates broken-CMap
  mojibake *higher* than the repaired text.
  ([#26](https://github.com/Jawafdehi/likhit/pull/26))

[0.1.8]: https://github.com/Jawafdehi/likhit/compare/v0.1.7...v0.1.8
