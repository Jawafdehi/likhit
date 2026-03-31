# likhit

`likhit` is a public MarkItDown plugin that adds Nepal-specific document support.

The default path is powered by [MarkItDown](https://github.com/microsoft/markitdown), with `likhit` intercepting born-digital Nepali PDFs that need Nepal-specific repair before Markdown is emitted. That repair layer handles Kalimati broken-CMap fixes, Devanagari reordering and spacing normalization, and legacy Nepali font remapping where applicable.

## Installation

```bash
pip install markitdown-likhit
```

## Release Process

`likhit` uses a tag-driven PyPI release flow with GitHub Actions Trusted Publishing.

1. Update the package version in `pyproject.toml`.
2. Commit that change.
3. Create a matching git tag such as `v0.1.1`.
4. Push the commit and tag to GitHub.

Example:

```bash
poetry version patch
git add pyproject.toml poetry.lock
git commit -m "Bump version to 0.1.1"
git tag v0.1.1
git push origin main --follow-tags
```

The publish workflow verifies that the git tag matches the version in `pyproject.toml` before uploading to PyPI.

## Usage

likhit is a [markitdown](https://github.com/microsoft/markitdown) plugin. Once installed,
enable it when creating a MarkItDown instance:

```python
from markitdown import MarkItDown

md = MarkItDown(enable_plugins=True)
result = md.convert("path/to/nepali-document.pdf")
print(result.text_content)
```

Or from the markitdown CLI:

```bash
markitdown --use-plugins path/to/nepali-document.pdf
```

To verify the plugin is registered:

```bash
markitdown --list-plugins
```

You should see `likhit` in the output.

### What likhit does

likhit intercepts only the formats where it adds behavior beyond MarkItDown:

- **PDF**: Detected automatically by scanning embedded fonts. If any font is classified
  as `broken_cmap` (Kalimati variants) or `legacy_remap` (Preeti, Kantipur, PCS Nepali,
  Sagarmatha, Himali), likhit's repair pipeline runs. All other PDFs fall through to
  markitdown's built-in converter.
- **DOC**: Legacy Microsoft Word `.doc` files are handled by likhit's extraction pipeline.
- **DOCX**: Left to MarkItDown's built-in Word converter.

### Supported document types

- Single-column notice and press-release style layouts
- Dense two-column article and journal style layouts
- Generic Nepali born-digital PDFs
- Legacy `.doc` files

### Not supported

- Scanned or image-only PDFs without OCR configuration
- DOC files on Windows (requires antiword — Linux/Mac only)

## Architecture

The pipeline is:

1. MarkItDown loads the plugin when `enable_plugins=True` or `--use-plugins` is used.
2. For PDFs that need Nepali repair, likhit scans fonts and runs its repair pipeline.
3. After extraction, likhit checks whether the document matches a known structure such as a single-column notice or a dense two-column layout.
4. If a known structure is detected, likhit applies its structure-aware ordering and paragraph assembly.
5. Otherwise, MarkItDown handles the default conversion path.
6. When the PDF needs Nepali repair, `likhit` repairs the text first:
   - Kalimati broken-CMap repair
   - Devanagari reordering
   - Devanagari spacing normalization
   - Legacy-font remapping through `npttf2utf`
7. `likhit` assembles repaired text blocks into Markdown.

This keeps the public product story simple: `likhit` is the tool users call, while MarkItDown is embedded infrastructure.

## Current Scope

- Supported input formats:
  - PDF (born-digital, with Nepali text repair)
  - DOC (legacy Microsoft Word, text extraction only, Linux/Mac only)
- Supported output: Markdown only
- Supported structures: single-column notice layouts, two-column layouts
- Optional in this branch: OCR for image-dominant PDFs when `markitdown-ocr` is configured
- Unsupported in this branch: image inputs

### DOC Support Notes

- Text-first extraction approach (no table structure preservation)
- **DOC files**: Supported for generic extraction and notice-style structure detection
- **Windows limitation**: DOC file extraction does not work on Windows due to antiword binary compatibility
  - Windows users must convert DOC files to DOCX format first
  - Use Microsoft Word, LibreOffice, or online converters
  - Linux/Mac users can process DOC files directly
- Tables are extracted as plain text
- No formatting preservation (bold, italic, etc.)

## Project Layout

- `src/likhit/_plugin.py`: MarkItDown plugin entry point and converter registration
- `src/likhit/converters/`: plugin converters for Nepali PDF and legacy DOC inputs
- `src/likhit/nepali_pdf_repair.py`: reusable Nepal-specific PDF repair layer
- `src/likhit/markdown_assembly.py`: generic Markdown assembly for the default conversion path
- `src/likhit/extractors/`: extraction strategies (PDF, DOC)
  - `font_based.py`: PDF extraction with Nepali font repair
  - `docx_based.py`: legacy DOC text extraction
- `src/likhit/handlers/`: structure-aware handlers and detection logic
- `src/likhit/renderers/`: Markdown rendering
- `tests/`: conversion, extraction, and plugin coverage
  - `tests/integration/`: end-to-end integration tests 
  - `tests/integration/test_data/`: committed test fixtures (PDF, DOCX, DOC samples)

## Testing

### Running Tests

Run all tests:
```bash
poetry run pytest
```


## References

- MarkItDown: https://github.com/microsoft/markitdown
- MarkItDown sample plugin: https://github.com/microsoft/markitdown/tree/main/packages/markitdown-sample-plugin
