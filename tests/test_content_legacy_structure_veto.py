"""Production-path coverage for the Latin word-structure veto."""

from __future__ import annotations

import pytest

from likhit.extractors import font_based
from likhit.extractors.font_based import (
    LegacyMapChoice,
    _content_legacy_veto_flags,
    _reads_as_latin_text,
)
from likhit.extractors.latin_structure import reads_as_latin_structure
from likhit.extractors.legacy_maps import get_converter_for_map


def test_structure_axis_bites_through_the_shipping_veto_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "plaster (1:2 cement: sand) including MIS mortar "
    spans = [{"font": "LegacyBody", "text": raw}]
    choices = {"LegacyBody": LegacyMapChoice("Preeti", None)}
    decoded = get_converter_for_map("Preeti")(raw)

    assert not _reads_as_latin_text(raw, decoded)
    assert reads_as_latin_structure(raw)
    assert _content_legacy_veto_flags(spans, choices) == [True]

    monkeypatch.setattr(font_based, "reads_as_latin_structure", lambda _text: False)
    assert _content_legacy_veto_flags(spans, choices) == [False]
