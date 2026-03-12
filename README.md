# likhit

Extract Nepali official documents into structured Markdown.

The current MVP supports CIAA press release PDFs and focuses on non-tabular text extraction with Kalimati font fixing.

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

- Supported document type: `ciaa-press-release`
- Supported input format: PDF
- Current output format: Markdown
- Current content scope: non-tabular press release body
- Batch mode works through a single `extract` command with one or many input files

## Extraction Pipeline

The current pipeline is:

1. Open the PDF with `pymupdf`.
2. Repair Kalimati font mappings with `fonttools`.
3. Extract positioned text lines from the PDF.
4. Normalize Devanagari ordering and token-level spacing.
5. Detect CIAA press release metadata such as title and publication date.
6. Remove header noise and stop before tabular/list sections.
7. Merge visual lines back into prose paragraphs.
8. Render the result as Markdown with YAML frontmatter.

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
- `samples/Press Release.pdf`
- `samples/table.pdf`
- `samples/my-table.pdf`
- `samples/82.pdf`


## Dependencies

- `pymupdf` handles PDF parsing.
- `fonttools` powers Kalimati font fixing.
- `pyyaml` renders Markdown frontmatter.
