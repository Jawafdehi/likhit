# likhit

Extract structured and textual data from Nepali official documents.

## Installation

### With Poetry

```bash
poetry install
```

Activate the virtual environment and run the package tools with:

```bash
poetry shell
```

Or run commands directly:

```bash
poetry run pytest
poetry run ruff check .
poetry run black --check .
```

### With pip

Install the package from the repository root:

```bash
pip install .
```

## Project Layout

- `src/likhit/` contains the Python package.
- `tests/` contains the test suite.
- `samples/` holds sample documents and fixtures.
- `docs/` is reserved for project documentation.
