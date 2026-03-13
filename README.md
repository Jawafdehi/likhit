# likhit

Extract Nepali official documents into structured Markdown.

The current MVP supports CIAA press release PDFs and Kanun Patrika PDFs, and automatically detects the font strategy needed for extraction, including Kalimati broken-CMap repair and legacy Nepali font remapping.

## Installation

### With Poetry

```bash
poetry install
```

Run project commands with `poetry run`:

```bash
poetry run pytest
poetry run ruff check .
poetry run black --check .
```

## How To Run

Extract one press release:

```bash
poetry run likhit extract path/to/pressrelease.pdf --type ciaa-press-release --out path/to/pressrelease.md
```

Extract multiple press releases at once:

```bash
poetry run likhit extract path/to/pressrelease.pdf path/to/pressrelease-2.pdf --type ciaa-press-release --out-dir path/to/output-dir
```

Auto-generate output names from extracted metadata:

```bash
poetry run likhit extract path/to/pressrelease.pdf path/to/pressrelease-2.pdf --type ciaa-press-release
```

Limit extraction to specific pages:

```bash
poetry run likhit extract path/to/pressrelease.pdf --type ciaa-press-release --pages 1-2 --out path/to/pressrelease.md
```


## Current Scope

- Supported document types: `ciaa-press-release`, `kanun-patrika`
- Supported input format: PDF
- Current output format: Markdown
- Current content scope: non-tabular press release body
- Batch mode works through a single `extract` command with one or many input files

## Extraction Pipeline

The current pipeline is:

1. Open the PDF with `pymupdf`.
2. Scan embedded fonts and choose the correct extraction strategy automatically.
3. Repair broken Kalimati font mappings with `fonttools` when needed.
4. Remap supported legacy Nepali fonts with `npttf2utf` when needed.
5. Extract positioned text lines from the PDF.
6. Normalize Devanagari ordering and token-level spacing for broken-CMap output.
7. Detect CIAA press release metadata such as title and publication date.
8. Merge visual lines back into prose paragraphs.
9. Render the result as Markdown with YAML frontmatter.

## Project Layout

Top-level folders:

- `src/likhit/` holds the library and CLI.
- `tests/` includes unit and integration coverage.
- `samples/` provides PDFs for development and test fixtures.
- `docs/` remains reserved for project documentation.

Key package files:

- `src/likhit/cli.py`: CLI entry point for `likhit extract`
- `src/likhit/core.py`: public extraction/rendering entry points
- `src/likhit/errors.py`: project exceptions
- `src/likhit/version.py`: version constant

Package modules:

- `src/likhit/models/`: enums and result models
- `src/likhit/extractors/`: PDF extraction and Kalimati repair
- `src/likhit/handlers/`: document-type-specific parsing
- `src/likhit/renderers/`: Markdown rendering

Key test files:

- `tests/test_smoke.py`: package import sanity
- `tests/test_models.py`: model validation
- `tests/test_extraction.py`: extraction behavior and integration coverage
- `tests/test_cli.py`: CLI behavior

Current sample files:

- `samples/pressrelease.pdf`
- `samples/kanunpatrika.pdf`
- `samples/Press_Release.pdf`
- `samples/table.pdf`
- `samples/my-table.pdf`
- `samples/82.pdf`


## Dependencies

- `pymupdf` handles PDF parsing.
- `fonttools` powers Kalimati font fixing.
- `npttf2utf` powers legacy Nepali font remapping.
- `pyyaml` renders Markdown frontmatter.
