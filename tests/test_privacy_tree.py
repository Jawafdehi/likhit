"""The three tree-level rules, each asserted against the failure that produced it."""

from __future__ import annotations

import unicodedata

import pytest

from likhit.privacy import redact_tree

# 🛑 The fixture has to be a document NFC actually CHANGES, and getting that right is
# subtle in exactly the direction that matters here. U+0958-U+095F are Unicode composition
# exclusions, so NFC *decomposes* them: the decomposed `ज` + U+093C form is ALREADY NFC and
# a fixture written that way cannot detect this defect at all -- the first version of this
# test was written that way and passed vacuously. The PRECOMPOSED U+095B is what NFC moves.
_PRECOMPOSED_PII_FREE = "\u095bिल्ला को विवरण हो।\n"

_WITH_PII = "नेपाल सरकार\nमहालेखापरीक्षकको कार्यालय\nनागरिकता नं. १२-३४-५६७८९ भएको निवेदक।\n"


def _tree(tmp_path):
    root = tmp_path / "markdown"
    (root / "bucket").mkdir(parents=True)
    (root / "bucket" / "1__pii.md").write_text(_WITH_PII, encoding="utf-8")
    (root / "bucket" / "2__clean.md").write_text(
        _PRECOMPOSED_PII_FREE, encoding="utf-8"
    )
    (root / "bucket" / "2__clean.json").write_text('{"source": {}}', encoding="utf-8")
    return root


def test_redacting_in_place_is_refused(tmp_path) -> None:
    """🛑 Rule 1. The measured tree is what the audit and the run record describe."""

    root = _tree(tmp_path)

    with pytest.raises(ValueError, match="refusing to redact in place"):
        redact_tree(root, root)


def test_an_existing_destination_is_refused(tmp_path) -> None:
    """The obvious mistake is pointing the output at something that already matters."""

    root = _tree(tmp_path)
    existing = tmp_path / "staged"
    existing.mkdir()

    with pytest.raises(ValueError, match="refusing to overwrite"):
        redact_tree(root, existing)


def test_a_document_with_no_span_is_emitted_byte_for_byte(tmp_path) -> None:
    """🛑🛑 Rule 2, and the exact defect it exists for.

    Normalisation happens for MATCHING only, because the label patterns need composed
    Devanagari. Emitting the normalised text for every document rewrote **308 PII-free
    documents** while the journal reported zero changes for them.

    The fixture is deliberately both PII-free AND non-NFC: those are the documents the
    defect touched, and a fixture that is already NFC cannot detect it at all.
    """

    root = _tree(tmp_path)
    out = tmp_path / "staged"

    report = redact_tree(root, out)

    clean_in = (root / "bucket" / "2__clean.md").read_bytes()
    clean_out = (out / "bucket" / "2__clean.md").read_bytes()
    assert clean_out == clean_in, (
        "a document the journal does not name was rewritten -- this is the 308-document "
        "defect, and it is invisible in the journal by construction"
    )
    assert unicodedata.normalize("NFC", clean_in.decode()) != clean_in.decode(), (
        "the fixture must be non-NFC or this test cannot detect the defect"
    )
    assert report.documents_changed == 1
    assert [d["path"] for d in report.changed_documents] == ["bucket/1__pii.md"]


def test_the_char_baseline_is_the_bytes_on_disk_not_the_normalised_text(
    tmp_path,
) -> None:
    """The counter that could not see the defect.

    ``chars_before`` measured against the normalised text folded the NFC residual into the
    baseline, where it cancelled inside ``net_char_delta`` -- so the tree diverged from the
    measured one and every counter still balanced.
    """

    root = _tree(tmp_path)
    on_disk = sum(
        len(p.read_text(encoding="utf-8")) for p in sorted(root.rglob("*.md"))
    )

    report = redact_tree(root, tmp_path / "staged")

    assert report.chars_before == on_disk


def test_the_journal_records_shapes_and_never_a_matched_digit(tmp_path) -> None:
    """🛑 Rule 3. A record of what was removed must be publishable beside the corpus."""

    root = _tree(tmp_path)

    report = redact_tree(root, tmp_path / "staged")
    payload = report.as_dict()

    assert payload["no_matched_digits_recorded"] is True
    assert report.span_shapes
    for shape in report.span_shapes:
        for key, value in shape.items():
            assert isinstance(value, (int, bool, str)), (key, value)
            if isinstance(value, str):
                assert not any(ch.isdigit() for ch in value), (
                    f"{key}={value!r} carries digits; the journal must record lengths and "
                    f"classes, never the value that was removed"
                )


def test_a_dry_run_writes_nothing_but_still_measures(tmp_path) -> None:
    root = _tree(tmp_path)
    out = tmp_path / "staged"

    report = redact_tree(root, out, dry_run=True)

    assert not out.exists()
    assert report.documents_changed == 1
    assert report.spans_redacted == 1


def test_non_transcript_files_ride_along_unchanged(tmp_path) -> None:
    """Sidecars carry no transcript text, so they are copied rather than considered."""

    root = _tree(tmp_path)
    out = tmp_path / "staged"

    redact_tree(root, out)

    assert (out / "bucket" / "2__clean.json").read_bytes() == (
        root / "bucket" / "2__clean.json"
    ).read_bytes()


def test_the_char_accounting_layer_of_rule_two_has_its_own_guard(tmp_path) -> None:
    """⚠️ Rule 2 is defended TWICE, and this covers the layer the test above cannot.

    ``emitted = redacted if spans else raw`` decides what the accounting counts;
    ``if spans: write_text else: copy2`` decides what lands on disk. Mutating either one
    ALONE leaves
    :func:`test_a_document_with_no_span_is_emitted_byte_for_byte` passing, because the other
    covers it -- measured by running both mutations separately. Defence in depth is good, but
    it means a reader could remove one layer as redundant and no test would complain.

    This asserts the accounting layer directly: for a document with no span, the bytes
    counted must be the bytes on disk, not the normalised form. That is the counter whose
    failure made the original defect invisible.

    ⚠️ Stated precisely, because "each layer is covered" would be too strong. Layer 1 (this
    test) and both-layers-together (the test above) are covered. **Layer 2 alone is not**:
    with layer 1 intact, `emitted` is already the on-disk bytes, so replacing `copy2` with
    `write_text` produces identical content and differs only in mtime. That is a real
    property -- a diff of the two trees should name only what was redacted -- but it is not
    worth a timestamp-sensitive test, so it is recorded here instead of asserted.
    """

    root = _tree(tmp_path)
    untouched = root / "bucket" / "2__clean.md"
    on_disk = len(untouched.read_text(encoding="utf-8"))
    with_pii = len((root / "bucket" / "1__pii.md").read_text(encoding="utf-8"))

    report = redact_tree(root, tmp_path / "staged")

    assert report.chars_before == on_disk + with_pii
    # The untouched document must contribute the SAME length on both sides of the ledger,
    # so the only net delta is the span that was actually replaced.
    assert (
        report.net_char_delta
        == len(
            (tmp_path / "staged" / "bucket" / "1__pii.md").read_text(encoding="utf-8")
        )
        - with_pii
    )
