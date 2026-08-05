# Publishing

`likhit` uses a tag-driven PyPI release flow with GitHub Actions Trusted Publishing.

## Release Process

1. Update the package version in `pyproject.toml`.
2. Commit that change.
3. Create a matching git tag such as `v0.1.1`.
4. Push the commit and tag to GitHub (`https://github.com/Jawafdehi/likhit/`).

Example:

```bash
uv version --bump patch
git add pyproject.toml uv.lock
git commit -m "Bump version to 0.1.1"
git tag v0.1.1
git push origin main --follow-tags
```

`uv version` re-locks the project, and `uv.lock` records `likhit`'s own version,
so both files must go in the bump commit — otherwise CI's `uv sync --locked`
fails on the release tag with a stale lockfile.

Use `uv version --bump patch --dry-run` to see the new version without writing it.

The publish workflow verifies that the git tag matches the version in
`pyproject.toml` before uploading to PyPI, then builds with `uv build`.
