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

Convert a single document to editable Markdown:

```bash
# PDF
poetry run likhit convert path/to/document.pdf --out path/to/document.md

# DOCX
poetry run likhit convert path/to/document.docx --out path/to/document.md

# DOC (legacy Word format)
poetry run likhit convert path/to/document.doc --out path/to/document.md
```

Convert multiple documents at once:

```bash
poetry run likhit convert path/to/a.pdf path/to/b.docx path/to/c.doc --out-dir path/to/output-dir
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

- Supported input formats: 
  - PDF (born-digital, with Nepali text repair)
  - DOCX (Microsoft Word 2007+, text extraction only)
  - DOC (legacy Microsoft Word, text extraction only via CIAA handler)
- Supported output: Markdown only
- Supported document types: CIAA press releases, Kanun Patrika journals
- Unsupported in this branch: OCR, scanned/image-only PDFs, image inputs

### DOCX/DOC Support Notes

- Text-first extraction approach (no table structure preservation)
- DOCX files supported for both CIAA and Kanun Patrika document types
- DOC files supported only for CIAA press releases (Kanun Patrika rejects legacy format)
- **Windows limitation**: DOC file extraction may not work on Windows due to antiword binary compatibility. Please convert DOC files to DOCX format on Windows, or use Linux/Mac systems for DOC support.
- Tables are extracted as plain text
- No formatting preservation (bold, italic, etc.)

## Project Layout

- `src/likhit/core.py`: public `convert` and `convert_many` entry points
- `src/likhit/markitdown_integration.py`: MarkItDown instance setup and custom PDF converter
- `src/likhit/nepali_pdf_repair.py`: reusable Nepal-specific PDF repair layer
- `src/likhit/markdown_assembly.py`: generic Markdown assembly for the default conversion path
- `src/likhit/extractors/`: extraction strategies (PDF, DOCX, DOC)
  - `font_based.py`: PDF extraction with Nepali font repair
  - `docx_based.py`: DOCX/DOC text extraction
- `src/likhit/handlers/`: document type handlers (CIAA, Kanun Patrika)
- `src/likhit/renderers/`: Markdown rendering
- `tests/`: conversion, extraction, and CLI coverage

## References

- MarkItDown: https://github.com/microsoft/markitdown
- MarkItDown sample plugin: https://github.com/microsoft/markitdown/tree/main/packages/markitdown-sample-plugin
