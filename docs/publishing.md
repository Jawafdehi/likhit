# Publishing

`likhit` uses a tag-driven PyPI release flow with GitHub Actions Trusted Publishing.

## Release Process

1. Update the package version in `pyproject.toml`.
2. Commit that change.
3. Create a matching git tag such as `v0.1.1`.
4. Push the commit and tag to GitHub (`https://github.com/Jawafdehi/likhit/`).

Example:

```bash
poetry version patch
git add pyproject.toml poetry.lock
git commit -m "Bump version to 0.1.1"
git tag v0.1.1
git push origin main --follow-tags
```

The publish workflow verifies that the git tag matches the version in `pyproject.toml` before uploading to PyPI.
