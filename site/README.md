# Likhit benchmark site

This directory contains the source for the generated GitHub Pages benchmark.
The published `_site/` directory is generated and intentionally not committed.

## Generate locally

```bash
poetry run python site/generate.py \
  --output _site \
  --source-cache /path/to/research/corpus/gon_mixed
python -m http.server 8000 --directory _site
```

Use `--skip-public` to build only the PII-free synthetic corpus without network
access. Public inputs are downloaded from their recorded Government of Nepal
URLs and checked against the SHA-256 values in `catalog.json`. Only the
catalogued benchmark pages are then repackaged without document metadata and
included in the generated Pages artifact. The original URL and full-source hash
remain in the result record for provenance.

## Artifact contract

`_site/data/results.json` conforms to `schema.json`. Each document record carries:

- source provenance, privacy classification, size, page count, and SHA-256;
- one or more configuration runs with an outcome and explicit checks;
- extracted Markdown and diagnostic-log artifact paths;
- quality, timing, and memory signals;
- source download and first-page preview paths.

The generator also reads pytest JUnit XML when supplied with `--junit`, allowing
the dashboard to publish the exact integration-suite status that produced the
artifact.

## Privacy policy

Synthetic reproductions are used for scanned, legacy-font, and mixed-layout
failure modes that were discovered in documents containing personal data. Public
inputs are limited to institutional policy or legislation and must be marked
`public-institutional` in `catalog.json`. Notices, charge sheets, and records
naming private defendants are not eligible for publication. Public excerpts must
also record their page scope and sanitization step.
