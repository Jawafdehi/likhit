# Likhit benchmark site

This directory contains the source for the generated GitHub Pages benchmark.
The published `_site/` directory is generated and intentionally not committed.

## Generate locally

```bash
uv run python site/generate.py \
  --output _site \
  --source-cache /path/to/research/corpus/gon_mixed
python -m http.server 8000 --directory _site
```

Use `--skip-public` to build only the PII-free synthetic corpus without network
access. Public inputs are downloaded from their recorded Government of Nepal
URLs and checked against the SHA-256 values in `catalog.json`. Each catalog
entry states whether the original file is published unchanged or a page-scoped,
metadata-free excerpt is used. The original URL and full-source hash remain in
the result record for provenance.

## Artifact contract

`_site/data/results.json` conforms to `schema.json`. Each document record carries:

- source provenance, privacy classification, size, page count, and SHA-256;
- one or more configuration runs with an outcome and explicit checks;
- extracted Markdown and diagnostic-log artifact paths;
- quality, timing, and memory signals;
- source download, inline PDF view, and first-page preview paths.

The generator also reads pytest JUnit XML when supplied with `--junit`, allowing
the dashboard to publish the exact integration-suite status that produced the
artifact.

## Source policy

Synthetic fixtures remain explicitly PII-free. Real inputs may include public
notices, press releases, legislation, or other records published by government
institutions and must be marked `public-institutional` in `catalog.json`. That
classification describes provenance; it is not a claim that the document has no
names, contact details, or other personal information. Each public record carries
a content note, an HTTPS source URL, and a pinned SHA-256. Any excerpt must also
record its page scope and sanitization step.
