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
recorded as a Likhit defect. That is what happens on a live build with no
credentials: `likhit` runs alone and the other two are reported unavailable with a
reason. The deployed site is not a live build — it replays a recording and
publishes all three columns, taking availability from the record rather than from
the runner; see [Recorded results](#recorded-results-snapshotjson).

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

## Token usage

Nothing in Likhit or MarkItDown exposes token usage — the figures are in the OCR
response, which the converter does not surface — so the generator reads a
cumulative counter and takes the difference across each conversion. Point
`LIKHIT_OCR_USAGE_URL` (or `LIKHIT_LOCAL_OCR_USAGE_URL`) at one that serves
`{"calls", "input_tokens", "output_tokens"}`.

`ocr_usage_proxy.py` is a reference implementation: it forwards to any
OpenAI-compatible endpoint and accumulates the `usage` block each response
carries. Run one per backend, so a hosted run can never be credited with a
locally served model's spend:

```bash
python site/ocr_usage_proxy.py --port 8141 --upstream http://127.0.0.1:11434
export LIKHIT_LOCAL_OCR_BASE_URL=http://127.0.0.1:8141/v1
export LIKHIT_LOCAL_OCR_USAGE_URL=http://127.0.0.1:8141/usage
```

**Zero is a measurement, not a gap.** Likhit adds an OCR candidate only for pages
a text layer cannot serve, so most documents spend nothing even with a vision
backend configured — in the committed recording, 13 of 16 hosted runs make no
call at all. Those record `calls: 0` and the dashboard says "no OCR call";
`ocr_usage: null` is reserved for a counter that was genuinely unreachable, and
renders as "not recorded". Collapsing the two would make the common case
indistinguishable from lost data.

The model id is recorded per **configuration**, not per run, because a run that
made no call has no usage record to carry it — and "which model produced this
column" is exactly what the dashboard needs to answer. It is read from the
environment when recording and from the snapshot when replaying.

Tokens only — no cost is derived, because vendor rates are not published for
every model and a built-in price table would silently produce wrong money.

## Recorded results (`snapshot.json`)

The deployed site does not measure anything. CI has no vision backend, and
converting the whole corpus three ways takes far longer than a Pages build, so
`--snapshot site/snapshot.json` **replays a recorded run** instead:

```bash
uv run python site/generate.py --output _site --skip-synthetic \
  --snapshot site/snapshot.json
```

A replay performs no conversion and calls no backend. It takes each run's status,
text, timing, memory and OCR usage from the record, and takes **availability from
the record too** — otherwise CI would skip the very OCR columns the snapshot
exists to publish. Everything downstream is recomputed from the recorded text, so
editing a check threshold in `catalog.json` changes a replayed result without
re-recording. Sources are still downloaded and hash-checked, so a replayed build
still needs network access to the publisher URLs; only the conversions are
replayed.

Re-record after any change to conversion behaviour, **from a machine where all
three backends work** — the recording is only as complete as the environment that
produced it:

```bash
uv run python site/generate.py --output _site --skip-synthetic \
  --write-snapshot site/snapshot.json
```

The two flags are mutually exclusive — `generate()` rejects the pair, not just the
CLI — because reading a recording while writing one would copy it forward under a
new timestamp without measuring anything, stamping the replaying build's commit
onto numbers it never took. `--write-snapshot` prints the configurations it
covered; check that line, because a snapshot recorded without a backend records
that configuration as unavailable, and the published page then shows it as not
run. `test_committed_snapshot_covers_every_published_run_and_configuration` fails
when the committed file stops covering the catalog.

Two unavailable configurations are **not** the same thing, and the artifact keeps
them apart. One the recording covers as `available: false` had no backend when the
recording was made, and carries that reason. One the recording never mentions —
because the catalog gained it afterwards — has no measurement at all: its runs are
named in `measured.missing_runs`, and its reason is "not covered by the recording"
rather than a claim about credentials that says nothing about the gap.

The committed snapshot holds the extracted text of every run (~700 KB). That is
the evidence behind the numbers, and it is the same text already published to
Pages, so committing it exposes nothing new.

Provenance is deliberately split in the artifact: `build` is the commit that
published the page, `measured` is the commit whose behaviour the numbers describe.
`measured.stale` marks the case where they differ and `measured.missing_runs` names
catalog runs the recording predates; the dashboard renders both above the summary.
A live build sets `measured` to null.

`stale` is reported, not warned about. Committing a recording necessarily creates a
commit later than the one it was recorded on, so it is true on effectively every
deploy — warning on it would fire every time and teach readers to ignore the
banner. The recorded commit and the published one are both named and left at that.
`missing_runs` is the opposite case: a real gap, which does warn.

## Job summary

`summarize.py` renders an artifact as Markdown for `$GITHUB_STEP_SUMMARY`, so the
published numbers, the per-configuration outcomes and any missing-run warning
appear on the Actions run itself rather than only in the deployed page:

```bash
uv run python site/summarize.py _site/data/results.json
```

It runs with `if: always()` and exits 0 when the artifact is absent, so a failed
generation is not replaced by a second, less useful error.

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
