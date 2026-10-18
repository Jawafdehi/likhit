# Publishing

`likhit` releases to PyPI automatically. **Merging a version bump to `main` is
the whole release.** There is no manual tagging step, and you should not create
release tags by hand.

## Cutting a release

1. Bump the version. `uv version` re-locks, and `uv.lock` records `likhit`'s own
   version, so both files go in the commit:

   ```bash
   uv version --bump minor      # or: patch / major, or an explicit 0.2.0
   ```

2. Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`. This is required —
   the release fails without it, and it becomes the GitHub Release notes.

3. Open a PR with `pyproject.toml`, `uv.lock` and `CHANGELOG.md`, and merge it.

Use `uv version --bump patch --dry-run` to see the new version without writing
it.

Everything after the merge is automatic:

| | |
|---|---|
| `release.yml` | Reads the version, refuses a stale `uv.lock` or a missing changelog entry, pushes the `vX.Y.Z` tag, triggers the publish, and creates the GitHub Release. |
| `pypi-publish.yml` | Re-runs lint and tests, builds with `uv build`, checks the tag matches `pyproject.toml`, and uploads to PyPI. |

If the version in `pyproject.toml` is unchanged, `release.yml` finds the tag
already exists and does nothing. Ordinary merges to `main` never publish.

## When something goes wrong

Both workflows are idempotent and safe to re-run.

- **Release failed before the tag was pushed** (stale lockfile, missing
  changelog entry): fix it on `main` in a normal PR. The merge re-runs
  `release.yml`.
- **Tag was pushed but the publish failed**: re-run `pypi-publish.yml` from the
  Actions tab against the `vX.Y.Z` **tag**, not a branch — every job in it is
  gated on a `v*` ref, so dispatching it against a branch tests and builds but
  publishes nothing. The upload uses `skip-existing`, so re-running a release
  that already reached PyPI is harmless.
- **Publish fails on `InvalidDistribution` / "not a valid metadata version"**:
  the `pypa/gh-action-pypi-publish` pin has gone stale. That action runs its
  own bundled twine inside a container, so it can reject a wheel that `uv
  build` produced and that a local `uvx twine check` passed — uvx resolves a
  current twine, the action does not. Bump the pin. This bit 0.2.0: hatchling
  emits `Metadata-Version: 2.5` and the then-pinned v1.13.0 predated twine 7.
- **Version number is already on PyPI**: PyPI will not accept a re-upload. Bump
  to the next version; a burned number cannot be reused. (0.1.2–0.1.5 were
  burned this way, which is why PyPI jumps 0.1.1 → 0.1.6.)

## How it is wired, and why

Publishing uses [PyPI Trusted
Publishing](https://docs.pypi.org/trusted-publishers/) over OIDC through the
`pypi` GitHub environment. There is no PyPI token stored anywhere — the
repository has no Actions secrets, and it should stay that way.

The two-workflow split looks redundant and is not:

- The Trusted Publisher registered on PyPI names **`pypi-publish.yml`**, so the
  upload has to happen in that file. It cannot be factored into a reusable
  workflow shared with `release.yml` either — PyPI [cannot name a reusable
  workflow](https://docs.pypi.org/trusted-publishers/troubleshooting/) as a
  Trusted Publisher.
- `release.yml` therefore has to *reach* `pypi-publish.yml`, and it cannot do
  that by pushing a tag. A tag pushed with the default `GITHUB_TOKEN` does not
  raise a `push: tags` event, so the publish would silently never run.
  `workflow_dispatch` is explicitly exempt from that rule, so that is the
  bridge. `release.yml` then waits for the dispatched run to appear and fails
  if it does not.

That silent-no-trigger failure is not hypothetical: `v0.1.8` was tagged on
origin in August 2026 and never published, leaving PyPI stuck on 0.1.7 from May
— five months and ~80 commits behind `main`.

### If the trusted publisher is ever reconfigured

Keep the workflow filename as `pypi-publish.yml` and the environment as `pypi`.
Renaming either breaks publishing with an OIDC error that reads like a
permissions problem.
