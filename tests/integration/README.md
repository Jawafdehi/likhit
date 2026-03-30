# Integration Test Fixtures

This directory contains integration test fixtures for validating end-to-end extraction success across multiple document formats.

## Fixture Rules

### Allowed Formats
- `.pdf` - PDF documents (born-digital, Nepali government documents)
- `.docx` - Microsoft Word documents (Office Open XML format)
- `.doc` - Legacy Microsoft Word documents (requires antiword)

### Size Policy
- **Target**: <= 10 MB total for all fixtures
- **Hard cap**: < 50 MB total (enforced by `test_fixture_size_under_threshold`)
- Keep individual files as small as possible (trim pages, use minimal content)

### Naming Convention
Fixtures should follow intent-based naming to make test purposes obvious:

- `notice_layout_sample.pdf` - single-column notice style PDF
- `two_column_layout_sample.pdf` - two-column PDF
- `notice_layout_sample.docx` - notice style DOCX
- `notice_layout_sample.doc` - legacy DOC with notice-style structure
- `generic_<type>_sample.<ext>` - Generic documents without specific structure

### Content Guidelines
1. **Public domain only**: Use only redistributable public samples
2. **No sensitive data**: Avoid copyrighted or sensitive content
3. **Minimal size**: Trim to essential pages/content for testing
4. **Representative**: Include key layout markers for structure detection

## Platform Caveats

### DOC File Support
- **Supported platforms**: Linux, macOS
- **Unsupported platforms**: Windows
- **Reason**: Requires `antiword` system dependency (installed via `pyantiword`)
- **Test behavior**: Tests automatically skip DOC files on Windows with explicit skip message

### Skip Behavior
Tests use `pytest.mark.skipif` with explicit reasons:
```python
SKIP_DOC_ON_WINDOWS = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="DOC extraction requires antiword and is unsupported on Windows",
)
```

## Adding New Fixtures

### Step 1: Prepare the File
1. Ensure file is public domain or has clear redistribution rights
2. Trim to minimal size (remove unnecessary pages/content)
3. Verify file contains representative markers for its layout structure

### Step 2: Add to test_data/
```bash
cp /path/to/document.pdf tests/integration/test_data/descriptive_name.pdf
```

### Step 3: Verify Size
```bash
poetry run pytest tests/integration::TestFixtureGovernance::test_fixture_size_under_threshold -v
```

### Step 4: Run Integration Tests
```bash
poetry run pytest tests/integration -v
```

## Current Fixtures

| Filename | Format | Size | Purpose | Source |
|----------|--------|------|---------|--------|
| `ciaa_pressrelease_sample.pdf` | PDF | ~1.2 MB | Single-column notice extraction | `samples/pressrelease.pdf` |
| `kanun_patrika_sample.pdf` | PDF | ~1.1 MB | Two-column structured extraction | `samples/kanunpatrika.pdf` |
| `ciaa_pressrelease_sample.docx` | DOCX | ~10 KB | DOCX passthrough coverage alongside enabled plugins | Generated |
| `ciaa_legacy_sample.doc` | DOC | ~1 KB | Legacy DOC notice extraction | Generated |

**Total**: ~2.35 MB (well under 10 MB target)

## Running Tests

### Run all integration tests
```bash
poetry run pytest tests/integration -v
```

### Run specific test class
```bash
poetry run pytest tests/integration::TestPluginConversion -v
```

### Run with fixture size check
```bash
poetry run pytest tests/integration::TestFixtureGovernance -v
```

### Quick run (quiet mode)
```bash
poetry run pytest tests/integration -q
```

## Maintenance

### When to Add Fixtures
- New structure support added
- Edge case discovered that needs coverage
- Regression test needed for specific issue

### When to Remove Fixtures
- Fixture becomes obsolete (document type no longer supported)
- Total size approaches 50 MB cap
- Fixture is redundant with existing coverage

### Size Management
If approaching size limits:
1. Trim existing PDFs to fewer pages
2. Remove redundant fixtures
3. Consider splitting into separate test suites if needed
