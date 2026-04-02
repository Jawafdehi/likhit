# likhit

`likhit` is a public MarkItDown plugin that adds Nepal-specific document support.

The default path is powered by [MarkItDown](https://github.com/microsoft/markitdown), with `likhit` intercepting born-digital Nepali PDFs that need Nepal-specific repair before Markdown is emitted. That repair layer handles Kalimati broken-CMap fixes, Devanagari reordering and spacing normalization, and legacy Nepali font remapping where applicable.

## Installation

```bash
pip install markitdown-likhit
```

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

### What likhit does

likhit intercepts only the formats where it adds behavior beyond MarkItDown:

- **PDF**: Detected automatically by scanning embedded fonts. If any font is classified
  as `broken_cmap` (Kalimati variants) or `legacy_remap` (Preeti, Kantipur, PCS Nepali,
  Sagarmatha, Himali), likhit's repair pipeline runs. All other PDFs fall through to
  markitdown's built-in converter.
- **DOC**: Legacy Microsoft Word `.doc` files are handled by likhit's extraction pipeline.
- **DOCX**: Left to MarkItDown's built-in Word converter.

### Supported document types
- Generic Nepali born-digital PDFs
- Legacy `.doc` files


### OCR Configuration

For image-dominant or scanned PDFs, `likhit` can use `markitdown-ocr` when OCR is configured.

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
