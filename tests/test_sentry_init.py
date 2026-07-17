"""Sentry must be opt-in: off unless LIKHIT_SENTRY_DSN is explicitly set."""

from __future__ import annotations

import pytest

from likhit import sentry_init


def test_sentry_disabled_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIKHIT_SENTRY_DSN", raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(sentry_init.sentry_sdk, "init", lambda **kw: calls.append(kw))

    assert sentry_init.init_sentry() is False
    assert calls == []


def test_sentry_ignores_generic_sentry_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    # A host's generic SENTRY_DSN must NOT enable likhit's reporter.
    monkeypatch.delenv("LIKHIT_SENTRY_DSN", raising=False)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/42")
    calls: list[dict] = []
    monkeypatch.setattr(sentry_init.sentry_sdk, "init", lambda **kw: calls.append(kw))

    assert sentry_init.init_sentry() is False
    assert calls == []


def test_sentry_enabled_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIKHIT_SENTRY_DSN", "https://public@example.com/1")
    calls: list[dict] = []
    monkeypatch.setattr(sentry_init.sentry_sdk, "init", lambda **kw: calls.append(kw))

    assert sentry_init.init_sentry() is True
    assert len(calls) == 1
    assert calls[0]["dsn"] == "https://public@example.com/1"
    assert calls[0]["send_default_pii"] is False
