# likhit

`likhit` is Jawafdehi's public MarkItDown plugin for Nepal-specific document support.

It extends [MarkItDown](https://github.com/microsoft/markitdown) with Nepal-specific PDF repair, layout-aware Markdown assembly, optional OCR fallback for image-dominant PDFs, and legacy `.doc` support. For PDFs, `likhit` now evaluates multiple extraction paths and returns the best result instead of relying on a single fixed pipeline.

It also ships two things that work on transcripts *after* extraction: a **quality audit** that scores a document on independent damage axes, and **personal-data detection and redaction**. Both are pure-Python and importable on their own — see [Transcript quality](#transcript-quality) and [Personal data](#personal-data).

Owned and maintained by [Jawafdehi](https://jawafdehi.org/).

## Installation

```bash
pip install likhit
```

Two extras name the post-extraction feature sets:

```bash
pip install "likhit[quality]"   # the transcript quality audit
pip install "likhit[privacy]"   # personal-data detection and redaction
```

> **These extras currently install nothing extra.** Both feature sets are pure-stdlib, so
> there is no dependency for an extra to gate, and an extra cannot withhold modules from a
> wheel — `likhit.quality` and `likhit.privacy` are importable after a plain
> `pip install likhit`, and `likhit-audit` / `likhit-redact` are always on your PATH. The
> extras exist to *name* the separable surfaces, so a downstream requirements file can
> record which part of this package it depends on, and to reserve the name for the first
> real optional dependency.

## Project Links

- Website: https://jawafdehi.org/
- GitHub: https://github.com/Jawafdehi/likhit/
- Benchmark: https://jawafdehi.github.io/likhit/ — real, published Government of
  Nepal documents converted with and without OCR, with the extracted Markdown and
  the assertions behind every result
- Contact: inquiry@jawafdehi.org

## Usage

`likhit` is primarily used as a [MarkItDown](https://github.com/microsoft/markitdown) plugin.

### Python

Once installed, enable plugins when creating a `MarkItDown` instance:

```python
from markitdown import MarkItDown

md = MarkItDown(enable_plugins=True)
result = md.convert("path/to/nepali-document.pdf")
print(result.text_content)
```

### MarkItDown CLI

You can also use `likhit` through the standard MarkItDown CLI:

```bash
markitdown --use-plugins path/to/nepali-document.pdf
```

To write the output to a file:

```bash
markitdown --use-plugins path/to/nepali-document.pdf -o output.md
```

To verify the plugin is registered:

```bash
markitdown --list-plugins
```

You should see `likhit` in the output.

### `likhit-save` CLI

This package also installs a small helper CLI that runs MarkItDown with the `likhit` plugin enabled and writes Markdown files for you:

```bash
likhit-save path/to/nepali-document.pdf --out output.md
```

Convert multiple files into a directory:

```bash
likhit-save samples/pressrelease.pdf samples/kanunpatrika.pdf --out-dir converted/
```

Extract only one page or a page range from a PDF:

```bash
likhit-save path/to/nepali-document.pdf --pages 5 --out page-5.md
likhit-save path/to/nepali-document.pdf --pages 2-4 --out pages-2-4.md
```

## Transcript quality

A whole-document Devanagari ratio is not enough to tell a good transcript from a bad one: a
legacy-Kalimati report recovered by this package scored `0.977` while its first line was
untranslated legacy bytes. `likhit.quality` scores a transcript on independent axes —
legacy-ASCII leakage, mojibake, repha loss, numeric damage, structure, repetition, spacing
and matra damage — and **the worst axis wins**, because averaging lets seven clean axes bury
an eighth that is making a positive claim about damage.

```python
from likhit.quality import audit_text

result = audit_text(markdown)
result["verdict"]            # "clean" | "suspect" | "garbled"
result["failing"]            # the axes that are not clean, worst first
result["checks"]["spacing"]  # that axis's verdict and its evidence
```

`audit_text` is pure — text in, verdicts out, no filesystem and no assumptions about how your
corpus is laid out. For a directory, `audit_tree` walks it and takes an optional `enrich`
callback so you can join your own metadata onto each row.

```bash
likhit-audit path/to/markdown --out report.json --workers 8
```

The command **exits non-zero on a tree it cannot evaluate** — missing, not a directory, or
holding no `*.md`. That guard is the point: `rglob` raises nothing for any of those, so
without it an audit of a mistyped path writes a report of zero records and exits 0, which is
indistinguishable from a clean corpus.

## Personal data

`likhit.privacy` separates finding personal data from removing it, and removes a deliberately
narrower set than it finds.

```python
from likhit.privacy import scan_text, redact_inline_text

high_precision, name_shaped = scan_text(markdown)   # counts and shapes, never a match
redacted, journal, counters = redact_inline_text(markdown)
```

`scan_text` **never returns matched text**, so a report of what a corpus contains can be
published beside the corpus. Redaction is anchored on the *label* and replaces only the
value, so a bare digit run can never match and a financial table cannot be touched.

```bash
likhit-redact scan   path/to/markdown --out pii-report.json
likhit-redact redact path/to/markdown --out-tree staged --journal journal.json
```

Redaction is **never in place**: it reads a tree and writes a staging copy, and refuses if the
destination exists or is the source. Documents with nothing to redact are copied
byte-for-byte rather than rewritten, and the journal records value *shapes* — lengths, digit
counts, separator presence — never a matched digit.

Scope is narrow on purpose: citizenship numbers and dates of birth. Mobile numbers, personal
PANs and email addresses are *detected and disclosed* rather than removed, because their
adjudication left undecided cases and over-redaction costs more than it saves.

### What likhit does

`likhit` adds behavior beyond MarkItDown in these places:

- **PDF**: `likhit` intercepts PDF inputs, runs the default MarkItDown PDF converter first, and then decides whether to keep that result, retry with Nepal-specific extraction, or add an OCR candidate for image-dominant pages. It prefers direct `likhit` extraction immediately when known Nepali repair fonts are detected.
- **DOC**: Legacy Microsoft Word `.doc` files are handled by `likhit`'s own extraction pipeline.
- **DOCX**: `.docx` files are still handled by MarkItDown's built-in Word converter, even when plugins are enabled.

### Supported document types
- PDFs, including Nepal-specific born-digital PDFs and image-dominant PDFs that may need OCR
- Legacy `.doc` files
- `.docx` passthrough via MarkItDown


### OCR Configuration

For image-dominant or scanned PDFs, `likhit` can add an OCR extraction candidate through `markitdown-ocr` when OCR is configured.

Required model configuration:

```bash
export MARKITDOWN_OCR_MODEL="your-model-name"
```

You can also provide the model through `OPENAI_MODEL` or `GEMINI_MODEL`.

Authentication options:

1. OpenAI-compatible provider with a standard OpenAI key:

```bash
export OPENAI_API_KEY="your-api-key"
```

2. OpenAI-compatible provider with a custom base URL:

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://your-provider.example/v1/"
export MARKITDOWN_OCR_MODEL="your-model-name"
```

3. Gemini using the OpenAI compatibility endpoint:

```bash
export GEMINI_API_KEY="your-gemini-api-key"
export GEMINI_MODEL="gemini-2.5-flash"
```

When `GEMINI_API_KEY` is set, `likhit` automatically uses Gemini's OpenAI-compatible base URL unless you explicitly override `OPENAI_BASE_URL`.

Optional variables:

```bash
export MARKITDOWN_OCR_PROMPT="Custom OCR instructions"
```

#### Recipe: a vision model on your own machine

Ollama, llama.cpp and vLLM all serve an OpenAI-compatible endpoint, so no `likhit`
change is needed — and no page image leaves the machine:

```bash
ollama pull qwen2.5vl:7b

# Ollama ignores the key; the client refuses to start without one
export OPENAI_API_KEY="ollama"
export OPENAI_BASE_URL="http://127.0.0.1:11434/v1"
export MARKITDOWN_OCR_MODEL="qwen2.5vl:7b"

likhit-save scanned-notice.pdf --out notice.md
```

#### Recipe: AWS Bedrock, through a gateway

Bedrock does not speak the OpenAI API, so put a translating proxy in front of it.
The proxy resolves AWS credentials the usual way — `AWS_PROFILE`, environment
variables or an instance role — and `likhit` only ever sees an OpenAI endpoint:

```bash
pip install 'litellm[proxy]'
litellm --model bedrock/anthropic.claude-sonnet-5

# whatever you configured the proxy to accept
export OPENAI_API_KEY="local-proxy-key"
export OPENAI_BASE_URL="http://127.0.0.1:4000/v1"
export MARKITDOWN_OCR_MODEL="bedrock/anthropic.claude-sonnet-5"
```

Any vision-capable model your account has enabled will do; swap the model id for
one you have access to.

#### When OCR actually runs

`likhit` adds an OCR candidate only for pages whose text layer cannot serve them
(see [Architecture](#architecture) below), so configuring a vision backend does
not mean paying for one on every document. In the published benchmark, 13 of 16
Government of Nepal documents are converted without a single vision call; only the
image-only scans need one.

If OCR is left unconfigured, `likhit` still converts. It logs that a page needed
OCR and returns the best result it could reach without it, rather than failing:

```text
PDF converter: OCR appears necessary, but OCR is not configured.
Set OPENAI_API_KEY or GEMINI_API_KEY, plus MARKITDOWN_OCR_MODEL, to enable markitdown-ocr.
```

## Architecture

The high-level PDF pipeline is:

1. MarkItDown loads the plugin when `enable_plugins=True` or `--use-plugins` is used.
2. For PDF inputs, `likhit` reads the file and optionally slices it to the requested page range.
3. `likhit` scans embedded fonts. If it detects known Nepali repair fonts such as Kalimati broken-CMap fonts or legacy remap fonts, it tries the Nepal-specific extraction pipeline immediately.
4. `likhit` also runs the default MarkItDown PDF converter and keeps that result as a candidate.
5. `likhit` analyzes the PDF pages. If the file looks image-dominant with a suspicious text layer and OCR is configured, it adds an OCR candidate.
6. If the default Markdown output looks suspicious for Nepali text, `likhit` retries extraction with its own PDF pipeline.
7. The Nepal-specific PDF pipeline can apply:
   - Kalimati broken-CMap repair
   - Devanagari reordering
   - Devanagari spacing normalization
   - Legacy-font remapping through `npttf2utf`
8. After extraction, `likhit` checks whether the document matches a whole-document semantic structure such as a single-column notice.
9. PDF layout ordering is assigned locally while assembling content blocks, so single-column, row-aligned, and two-column regions can coexist in one file.
10. If multiple candidate outputs exist, `likhit` scores them and returns the best one.




## Project Layout

- `src/likhit/_plugin.py`: MarkItDown plugin entry point and converter registration
- `src/likhit/converters/`: plugin converters for PDF and legacy DOC inputs
- `src/likhit/nepali_pdf_repair.py`: reusable Nepal-specific PDF repair layer
- `src/likhit/markdown_assembly.py`: generic Markdown assembly for the default conversion path
- `src/likhit/extractors/`: extraction strategies (PDF, DOC)
  - `font_based.py`: PDF extraction with Nepali font repair
  - `docx_based.py`: legacy DOC text extraction
- `src/likhit/handlers/`: structure-aware handlers and detection logic
- `src/likhit/renderers/`: Markdown rendering
- `src/likhit/devanagari.py`: Devanagari character classes and matra-damage patterns shared across the package
- `src/likhit/quality/`: transcript quality axes, tree walker and `likhit-audit`
- `src/likhit/privacy/`: personal-data signals, redaction passes, placeholder vocabulary and `likhit-redact`
- `tests/`: conversion, extraction, and plugin coverage
  - `tests/integration/`: end-to-end integration tests 
  - `tests/integration/test_data/`: committed test fixtures (PDF, DOCX, DOC samples)

## Development

`likhit` uses [uv](https://docs.astral.sh/uv/) for dependency management, and the
rest of Astral's toolchain — [ruff](https://docs.astral.sh/ruff/) for linting and
formatting, [ty](https://github.com/astral-sh/ty) for type checking.

Install the project together with its dev dependencies:
```bash
uv sync
```

Install the pre-commit hooks once per clone:
```bash
uv run pre-commit install
```

### Testing

Run all tests:
```bash
uv run pytest           # ~63s
uv run pytest -n auto   # ~21s on 64 cores, ~31s on 4 — same results
```

`-n auto` (pytest-xdist) is not in `addopts`, because worker startup is a net
loss on the single-file runs that dominate local iteration — use plain `pytest`
for those. CI passes `-n auto` itself. `pyproject.toml`'s
`[tool.pytest.ini_options]` comment carries the measured worker-count table.

Run only the end-to-end integration suite:
```bash
uv run pytest tests/integration
```

**Read [`docs/writing-tests.md`](docs/writing-tests.md) before adding one.** `likhit`
reads adversarial input, and several classes of defect here are invisible to a suite
that merely passes — that file covers how to prove a test bites, and the
library-specific traps a test can be blind to.

The default suite is hermetic and should report executed tests only. Checks that need
private PDFs, unvendored fonts, paid OCR, or an adversarial external fixture are explicit
manual commands; their prerequisites and CI coverage are in
[`docs/writing-tests.md`](docs/writing-tests.md#external-checks-are-manual-not-skipped).

### Gates

CI runs the following. Run them yourself before opening a pull request:

```bash
uv run ruff check .           # lint — gated
uv run ruff format --check .  # formatting — gated
uv run pytest -n auto         # tests — gated (CI uses -n auto; plain works too)
uv run ty check               # types — ADVISORY, see pyproject.toml
```

`ty` is advisory rather than a gate: it is pre-1.0, and several of this package's
dependencies (PyMuPDF among them) ship no type information, so the rule families
it cannot yet see through are silenced in `pyproject.toml`. Read its output; do
not ignore it. **`ty check` reports a different number depending on the scope you give
it and on whether the worktree has a `.venv`** — see
[`docs/writing-tests.md`](docs/writing-tests.md#ty-has-three-different-correct-answers-and-they-differ-by-their-denominator).

Before concluding a fix did or did not land in `main`, read
[Checking whether a fix is actually in `main`](docs/writing-tests.md#checking-whether-a-fix-is-actually-in-main)
— `git cherry` and `git patch-id` are both insufficient here, in two independent ways.


## References

- MarkItDown: https://github.com/microsoft/markitdown
- MarkItDown sample plugin: https://github.com/microsoft/markitdown/tree/main/packages/markitdown-sample-plugin

## License

Licensed under the [Hippocratic License 3.0](./LICENSE), an [Ethical Source](https://ethicalsource.dev) license. See [LICENSING.md](./LICENSING.md) for details.

## Ownership

`likhit` is owned and maintained by Jawafdehi.

- Organization: Jawafdehi
- Website: https://jawafdehi.org/
- GitHub: https://github.com/Jawafdehi/likhit/
- Contact: inquiry@jawafdehi.org
