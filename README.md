# likhit

`likhit` is Jawafdehi's public MarkItDown plugin for Nepal-specific document support.

It extends [MarkItDown](https://github.com/microsoft/markitdown) with Nepal-specific PDF repair, layout-aware Markdown assembly, optional OCR fallback for image-dominant PDFs, and legacy `.doc` support. For PDFs, `likhit` now evaluates multiple extraction paths and returns the best result instead of relying on a single fixed pipeline.

Owned and maintained by [Jawafdehi](https://jawafdehi.org/).

## Installation

```bash
pip install likhit
```

## Project Links

- Website: https://jawafdehi.org/
- GitHub: https://github.com/Jawafdehi/likhit/
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

## License

Licensed under the [Hippocratic License 3.0](./LICENSE), an [Ethical Source](https://ethicalsource.dev) license. See [LICENSING.md](./LICENSING.md) for details.

## Ownership

`likhit` is owned and maintained by Jawafdehi.

- Organization: Jawafdehi
- Website: https://jawafdehi.org/
- GitHub: https://github.com/Jawafdehi/likhit/
- Contact: inquiry@jawafdehi.org
