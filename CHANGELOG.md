# Changelog

All notable changes to `likhit` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file starts at 0.1.8. For earlier releases, see the
[commit history](https://github.com/Jawafdehi/likhit/commits/main/) and the
[git tags](https://github.com/Jawafdehi/likhit/tags).

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
