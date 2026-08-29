from __future__ import annotations

import pytest

from likhit.extractors import kalimati
import likhit.nepali_pdf_repair as repair


class _FakeDoc:
    page_count = 1

    def __getitem__(self, index: int) -> object:
        assert index == 0
        return object()

    def close(self) -> None:
        pass


def _extract_repaired_line(
    monkeypatch: pytest.MonkeyPatch,
    spans: tuple[tuple[str, str], ...],
    *,
    boxes: tuple[tuple[float, float], ...] | None = None,
) -> str:
    span_boxes = boxes or tuple(
        (10.0 + index * 10.0, 20.0 + index * 10.0) for index in range(len(spans))
    )
    assert len(span_boxes) == len(spans)
    page_dict = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {
                                "text": text,
                                "font": font_name,
                                "bbox": (
                                    span_boxes[index][0],
                                    10.0,
                                    span_boxes[index][1],
                                    20.0,
                                ),
                            }
                            for index, (text, font_name) in enumerate(spans)
                        ]
                    }
                ]
            }
        ]
    }
    strategies = {
        "Kalimati": "broken_cmap",
        "Kokila": "broken_cmap",
        "Helvetica": "correct",
    }
    doc = _FakeDoc()
    monkeypatch.setattr(repair, "_open_pdf", lambda _source: doc)
    monkeypatch.setattr(repair, "scan_pdf_fonts", lambda _doc: strategies)
    monkeypatch.setattr(repair, "fix_kalimati_cmap", lambda _doc: (_doc, True))
    monkeypatch.setattr(
        repair,
        "get_cid_marked_page_dict",
        lambda _page: page_dict,
    )
    monkeypatch.setattr(
        repair,
        "collect_page_repairs_by_line",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        repair,
        "detect_page_tables",
        lambda *_args, **_kwargs: [],
    )

    blocks = repair.extract_repaired_text_blocks(b"%PDF")

    assert len(blocks) == 1
    return blocks[0].text


@pytest.mark.parametrize(
    ("spans", "expected"),
    [
        (
            (
                ("द", "AAAAAA+Kalimati"),
                (kalimati._PUA_CONTEXTUAL_NE, "AAAAAA+Kalimati"),
            ),
            "दे",
        ),
        (
            (
                (kalimati._PUA_KOKILA_HALF_SA, "AAAAAA+Kokila"),
                ("थानीय", "AAAAAA+Kokila"),
            ),
            "स्थानीय",
        ),
        (
            (
                ("त" + kalimati._PUA_KOKILA_HALF_THA, "AAAAAA+Kokila"),
                ("य", "AAAAAA+Kokila"),
            ),
            "तथ्य",
        ),
    ],
)
def test_reusable_repair_resolves_context_after_cross_span_assembly(
    monkeypatch: pytest.MonkeyPatch,
    spans: tuple[tuple[str, str], ...],
    expected: str,
) -> None:
    assert _extract_repaired_line(monkeypatch, spans) == expected


@pytest.mark.parametrize(
    "marker",
    [
        kalimati._PUA_CONTEXTUAL_NE,
        kalimati._PUA_KOKILA_IKAR,
        kalimati._PUA_KOKILA_TA,
        kalimati._PUA_KOKILA_HALF_SA,
        kalimati._PUA_KOKILA_HALF_THA,
    ],
)
def test_reusable_repair_does_not_resolve_unrelated_font_pua(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    assert (
        _extract_repaired_line(
            monkeypatch,
            ((marker, "AAAAAA+Helvetica"),),
        )
        == marker
    )


@pytest.mark.parametrize(
    ("marker", "font_name"),
    [
        (kalimati._PUA_CONTEXTUAL_NE, "AAAAAA+NotKalimati"),
        (kalimati._PUA_CONTEXTUAL_NE, "AAAAAA+KalimatiExtra"),
        (kalimati._PUA_KOKILA_HALF_SA, "AAAAAA+NotKokila"),
        (kalimati._PUA_KOKILA_HALF_SA, "AAAAAA+KokilaExtra"),
    ],
)
def test_reusable_repair_rejects_embedded_and_suffix_family_names(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    font_name: str,
) -> None:
    assert _extract_repaired_line(monkeypatch, ((marker, font_name),)) == marker


def test_reusable_repair_does_not_join_distant_context_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = kalimati._PUA_KOKILA_HALF_THA

    assert (
        _extract_repaired_line(
            monkeypatch,
            (
                ("त" + marker, "AAAAAA+Kokila"),
                ("य", "AAAAAA+Kokila"),
            ),
            boxes=((10.0, 20.0), (24.0, 30.0)),
        )
        == "त् य"
    )


def test_reusable_repair_tracks_same_marker_per_span_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = kalimati._PUA_KOKILA_HALF_SA

    assert (
        _extract_repaired_line(
            monkeypatch,
            (
                (marker + "थानीय ", "AAAAAA+Kokila"),
                (marker + "थापा", "AAAAAA+Helvetica"),
            ),
        )
        == "स्थानीय " + marker + "थापा"
    )
