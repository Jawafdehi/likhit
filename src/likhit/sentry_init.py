"""Optional Sentry initialization.

Sentry is disabled by default. It is enabled only when a DSN is supplied via the
``LIKHIT_SENTRY_DSN`` environment variable, so importing likhit never phones home
unless the deployment explicitly opts in. likhit is a library that runs inside
other services, so it deliberately does NOT honor a generic ``SENTRY_DSN`` — that
would let it inherit (and report to) an unrelated host's Sentry project.
"""

import os

import sentry_sdk

_DSN_ENV_VAR = "LIKHIT_SENTRY_DSN"


def _resolve_dsn() -> str | None:
    dsn = os.getenv(_DSN_ENV_VAR)
    if dsn and dsn.strip():
        return dsn.strip()
    return None


def init_sentry() -> bool:
    """Initialize Sentry if ``LIKHIT_SENTRY_DSN`` is set. Returns whether it was enabled."""

    dsn = _resolve_dsn()
    if not dsn:
        return False

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=float(os.getenv("LIKHIT_SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        send_default_pii=False,
        environment=os.getenv("LIKHIT_SENTRY_ENVIRONMENT", "production"),
    )
    return True


init_sentry()
