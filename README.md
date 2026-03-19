# likhit

`likhit` is a public PDF-to-Markdown tool for Nepali government documents.

The default path is powered by [MarkItDown](https://github.com/microsoft/markitdown), with `likhit` intercepting born-digital Nepali PDFs that need Nepal-specific repair before Markdown is emitted. That repair layer handles Kalimati broken-CMap fixes, Devanagari reordering and spacing normalization, and legacy Nepali font remapping where applicable.

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

## Recommended Usage

Convert a single PDF to editable Markdown:

```bash
poetry run likhit convert path/to/document.pdf --out path/to/document.md
```

Convert multiple PDFs at once:

```bash
poetry run likhit convert path/to/a.pdf path/to/b.pdf --out-dir path/to/output-dir
```

If `--out` or `--out-dir` is omitted, `likhit` writes Markdown files in the current directory using the input filename stem.

## Usage

`convert` is the public path.

- Input scope: born-digital PDFs only
- Output: generic editable Markdown
- Engine: MarkItDown by default
- `likhit` value-add: Nepali PDF repair before Markdown output when needed
- Recognized document layouts such as Kanun Patrika and CIAA-style PDFs are auto-detected internally so `likhit` can preserve better text order and structure without a `--type` flag
- No OCR support is included in this branch

## Architecture

The new default pipeline is:

1. `likhit convert` opens the PDF and checks whether it matches a known structure-aware document type.
2. If the PDF matches a known layout such as Kanun Patrika or a CIAA-style document, `likhit` reuses its existing structure-aware extraction logic internally.
3. Otherwise, MarkItDown handles the default conversion path.
4. When the PDF needs Nepali repair, `likhit` repairs the text first:
   - Kalimati broken-CMap repair
   - Devanagari reordering
   - Devanagari spacing normalization
   - Legacy-font remapping through `npttf2utf`
5. `likhit` assembles repaired text blocks into Markdown.

This keeps the public product story simple: `likhit` is the tool users call, while MarkItDown is embedded infrastructure.

## Current Scope

- Supported default input: PDF only
- Supported default output: Markdown only
- Supported default document class: born-digital PDFs
- Unsupported in this branch: OCR, scanned/image-only PDFs, `.doc`, `.docx`, and image inputs

## Project Layout

- `src/likhit/core.py`: public `convert` and `convert_many` entry points
- `src/likhit/markitdown_integration.py`: MarkItDown instance setup and custom PDF converter
- `src/likhit/nepali_pdf_repair.py`: reusable Nepal-specific PDF repair layer
- `src/likhit/markdown_assembly.py`: generic Markdown assembly for the default conversion path
- `src/likhit/extractors/`, `src/likhit/handlers/`, `src/likhit/renderers/`: internal Nepali PDF repair and legacy extraction internals
- `tests/`: conversion, extraction, and CLI coverage

## References

- MarkItDown: https://github.com/microsoft/markitdown
- MarkItDown sample plugin: https://github.com/microsoft/markitdown/tree/main/packages/markitdown-sample-plugin
