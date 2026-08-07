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
access, and `--skip-synthetic` to publish only the real government documents —
which is what the deployed site does. Public inputs are downloaded from their
recorded Government of Nepal URLs and checked against the SHA-256 values in
`catalog.json`. Each catalog entry states whether the original file is published
unchanged or a page-scoped, metadata-free excerpt is used. The original URL and
full-source hash remain in the result record for provenance.

## Configurations

Every document runs against three configurations:

| Configuration | Label | OCR backend |
| --- | --- | --- |
| `likhit` | Likhit (no OCR) | none; OCR credentials are stripped from the run |
| `likhit-ocr` | Likhit (with OCR) | a hosted vision API, billed per token |
| `likhit-ocr-local` | Likhit (offline OCR) | a locally served vision model, no API and no per-token charge |

A configuration declares the backend it `requires`, and one whose backend is
absent is **skipped rather than run** — otherwise a missing credential would be
recorded as a Likhit defect. CI supplies neither backend, so the published build
runs `likhit` alone and reports the other two as unavailable with a reason.

Point the hosted backend at a provider with `MARKITDOWN_OCR_MODEL` plus
`OPENAI_API_KEY` (or `GEMINI_API_KEY`); see the root README for the full OCR
configuration. The offline backend needs an OpenAI-compatible server — Ollama,
llama.cpp, vLLM — and no change to Likhit itself:

```bash
export LIKHIT_LOCAL_OCR_BASE_URL=http://127.0.0.1:11434/v1
export LIKHIT_LOCAL_OCR_MODEL=qwen2.5vl:7b
```

It counts as available only when the server answers *and* serves that model, so a
running-but-empty server skips instead of producing a column of failures. A
configuration may raise its own `timeout_s` in `catalog.json`, which the offline
backend does: vision inference on CPU is far slower than a hosted API, and
timing it out would misreport a slow machine as a conversion failure.

If a backend exposes a cumulative token counter, set `LIKHIT_OCR_USAGE_URL` (or
`LIKHIT_LOCAL_OCR_USAGE_URL`) and each run records the calls and tokens it spent,
along with the model that spent them. Tokens only — no cost is derived, because
vendor rates are not published for every model and a built-in price table would
silently produce wrong money.

## Artifact contract

`_site/data/results.json` conforms to `schema.json`, and the test suite validates
the generated artifact against it. Each document record carries:

- source provenance, privacy classification, size, page count, and SHA-256;
- one run per available configuration, with an outcome and explicit checks;
- extracted Markdown and diagnostic-log artifact paths;
- quality, timing, and memory signals;
- OCR calls, tokens and model for runs that used a vision backend;
- source download, modal PDF view, and first-page preview paths.

Alongside the documents, `configurations` records whether each one was
`available` and, if not, the `unavailable_reason`; `summary.skipped` counts the
runs that were not executed for that reason.

The generator also reads pytest JUnit XML when supplied with `--junit`, allowing
the dashboard to publish the exact integration-suite status that produced the
artifact.

Pass `--fail-on-regression` to exit non-zero when any run failed, so a build can
gate on the benchmark. Note that skipped configurations do not count as failures,
so this gate is blind to a backend that was never exercised — in CI, where no OCR
backend is configured, it says nothing about the OCR paths.

## Source policy

Synthetic fixtures remain explicitly PII-free. Real inputs may include public
notices, press releases, legislation, or other records published by government
institutions and must be marked `public-institutional` in `catalog.json`. That
classification describes provenance; it is not a claim that the document has no
names, contact details, or other personal information. Each public record carries
a content note, an HTTPS source URL, and a pinned SHA-256. Any excerpt must also
record its page scope and sanitization step.
