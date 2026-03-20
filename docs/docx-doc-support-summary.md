# DOCX/DOC Support Implementation Summary

## Overview

This implementation adds simplified DOCX and DOC file support to likhit, following a text-first approach that extracts plain text without attempting to preserve complex document structure.

## Changes Made

### 1. Dependencies (pyproject.toml)

Added two lightweight packages:
- `docx2txt2>=1.0.4,<2.0.0` - For DOCX text extraction (maintained fork, Python 3.13+ compatible)
- `pyantiword>=0.1.2,<1.0.0` - For legacy DOC text extraction (bundles antiword binary)

### 2. New Extractor (src/likhit/extractors/docx_based.py)

Created `DocxBasedStrategy` class (~100 lines) that:
- Extracts plain text from DOCX files using `docx2txt2.process()`
- Extracts plain text from DOC files using `pyantiword.antiword_wrapper.extract_text_with_antiword()`
- Splits text into paragraph fragments with sequential positioning
- Returns `RawDocument` compatible with existing handlers
- No table structure preservation (tables extracted as plain text)

### 3. Handler Updates

#### Base Handler (src/likhit/handlers/base.py)
- Added `get_extraction_strategy_for_file()` method for file-based routing
- Default implementation returns `get_extraction_strategy()` for backward compatibility

#### CIAA Press Release Handler (src/likhit/handlers/ciaa_press_release.py)
- Added `DocxBasedStrategy` instance
- Implemented `get_extraction_strategy_for_file()` to route:
  - `.docx` files → `DocxBasedStrategy`
  - `.doc` files → `DocxBasedStrategy`
  - `.pdf` files → `FontBasedStrategy` (existing)

#### Kanun Patrika Handler (src/likhit/handlers/kanun_patrika.py)
- Added `DocxBasedStrategy` instance
- Implemented `get_extraction_strategy_for_file()` to route:
  - `.docx` files → `DocxBasedStrategy`
  - `.doc` files → Raises error (legacy format not supported for Kanun Patrika)
  - `.pdf` files → `FontBasedStrategy` (existing)

### 4. Tests (tests/test_docx_extraction.py)

Created comprehensive test suite with 11 tests covering:
- DOCX text extraction with mocked `docx2txt2.process()`
- DOC text extraction with mocked `pyantiword.antiword_wrapper.extract_text_with_antiword()`
- Empty file error handling
- Unsupported format error handling
- File routing for both handlers
- Legacy DOC rejection for Kanun Patrika

## Design Decisions

### Text-First Approach
- No structural parsing (tables, formatting, styles)
- Simple paragraph splitting on newlines
- Minimal dependencies and maintenance burden

### Package Selection
- `docx2txt2` over `python-docx`: Lighter weight, simpler API for text extraction
- `pyantiword` over `textract`: Bundles antiword binary, no external dependencies

### Handler Routing
- File extension-based routing via `get_extraction_strategy_for_file()`
- Backward compatible with existing `get_extraction_strategy()` method
- Kanun Patrika explicitly rejects legacy DOC format

## Test Results

- 54 out of 55 tests pass
- 11 new DOCX/DOC tests all pass
- 1 pre-existing test fails (Windows font path issue, unrelated to changes)

## Comparison to Original PR

### Original PR (feat/likhit-docxtomd)
- 450-line `docx_based.py` with structural parsing
- `python-docx` for DOCX structure analysis
- `doc2docx` for DOC→DOCX conversion (Python 3.13 incompatible)
- LibreOffice fallback for DOC conversion
- Custom table/pipe-table rendering
- Complex fragment ordering and layout preservation

### This Implementation
- ~100-line `docx_based.py` with text extraction
- `docx2txt2` for simple DOCX text extraction
- `pyantiword` for direct DOC text extraction (Python 3.13 compatible)
- No conversion pipeline needed
- Tables extracted as plain text
- Simple sequential fragment ordering

## Usage

```python
from likhit.core import extract, render_markdown

# Extract from DOCX
result = extract("document.docx", "ciaa-press-release")
markdown = render_markdown(result)

# Extract from DOC
result = extract("document.doc", "ciaa-press-release")
markdown = render_markdown(result)

# Extract from PDF (existing functionality)
result = extract("document.pdf", "ciaa-press-release")
markdown = render_markdown(result)
```

## Limitations

1. No table structure preservation (tables become plain text)
2. No formatting preservation (bold, italic, etc.)
3. No image extraction
4. No header/footer extraction
5. Kanun Patrika does not support legacy DOC format

These limitations are acceptable for the current use case of extracting text content for markdown conversion.

## Future Enhancements

If structural preservation becomes necessary:
1. Consider using `python-docx` for DOCX table extraction
2. Add table detection and markdown table rendering
3. Consider OCR integration for scanned documents
4. Add support for other Office formats (XLSX, PPTX)


## Windows Compatibility Note

**DOC files are not supported on Windows** due to antiword binary compatibility issues. The pyantiword package bundles a Linux antiword binary which cannot run on Windows.

**Workaround for Windows users:**
1. Convert DOC files to DOCX format using Microsoft Word or LibreOffice
2. Use the DOCX file with likhit (fully supported on Windows)
3. Alternatively, use Linux/Mac systems for DOC file support

**DOCX files work perfectly on all platforms** including Windows.
