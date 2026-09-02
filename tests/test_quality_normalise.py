"""Normalisation before measurement, and the two defects that come of skipping it.

Every test here is a bite-proof for something that was measured wrong before this module
existed. None of them is hypothetical.
"""

from __future__ import annotations

from likhit.privacy import placeholders, redact_inline_text
from likhit.quality import normalise_for_audit, split_pages
from likhit.quality.axes import audit_text, check_spacing
from likhit.renderers.markdown import strip_page_anchors

_ANCHORED = (
    "<!-- likhit:page 1 -->\n\n"
    "नेपाल कानून पत्रिका २०६५\n\n"
    "<!-- likhit:page 2 -->\n\n"
    "यो दोस्रो पृष्ठको पाठ हो।\n"
)


def test_splitting_pages_is_lossless() -> None:
    """Re-joining the preamble and bodies must reproduce the document exactly.

    Named in :func:`likhit.quality.normalise.split_pages`'s docstring, which is why it
    exists: a docstring that cites a test the repository does not have is a defect this
    project has paid for before.
    """

    preamble, bodies = split_pages(_ANCHORED)

    rebuilt = preamble + "".join(
        f"<!-- likhit:page {number} -->{body}"
        for number, body in sorted(bodies.items())
    )
    assert rebuilt == _ANCHORED
    assert sorted(bodies) == [1, 2]


def test_splitting_an_unanchored_document_returns_it_whole() -> None:
    text = "कुनै एंकर नभएको पाठ।"
    assert split_pages(text) == (text, {})


def test_normalising_collapses_the_blank_runs_anchors_leave() -> None:
    """The behavioural difference between the two ``strip_page_anchors`` functions.

    That difference is the reason there were two of them, and pinning it is what lets the
    renderer's public function stay exactly as its PyPI consumers expect while the audit
    gets the canonical form it was measured with.

    ⚠️ This asserts the difference EXISTS, not that it matters -- see
    :func:`test_collapsing_blank_runs_is_inert_on_the_spacing_axis`, which measures that it
    does not move a verdict. The rationale the corpus tool gave for the collapse was wrong.
    """

    bare = strip_page_anchors(_ANCHORED)
    normalised = normalise_for_audit(_ANCHORED)

    assert "\n\n\n" in bare, (
        "the renderer's function is supposed to leave the blank run -- if it no longer "
        "does, this whole module's reason for existing has changed"
    )
    assert "\n\n\n" not in normalised
    assert "likhit:page" not in normalised


def test_collapsing_blank_runs_is_inert_on_the_spacing_axis() -> None:
    """⚠️ Pins the OPPOSITE of what the corpus tool's docstring claimed.

    That docstring said the ``spacing`` axis "reads whitespace ratios", making the collapse
    load-bearing. It does not: :func:`check_spacing` reads a token-length distribution over
    ``\\S+`` tokens, and a blank run contributes no tokens. Measured over 624 corpus
    documents -- 622 of which leave a blank run when their anchors go -- collapsing changes
    zero document verdicts and zero axis verdicts.

    Asserted so the claim cannot quietly come back. The collapse stays for
    canonicalisation; if a future axis ever does read whitespace, this test fails and that
    is the signal to re-derive the rationale rather than inherit it.
    """

    padded = ("<!-- likhit:page 1 -->\n\n\n\n" + "नेपाली पाठ यहाँ छ। " * 30) * 8

    bare = check_spacing(strip_page_anchors(padded))
    normalised = check_spacing(normalise_for_audit(padded))

    assert bare[0] == normalised[0]
    assert bare[1].get("single_char_share") == normalised[1].get("single_char_share")


def test_a_redacted_document_scores_the_same_as_the_original() -> None:
    """🛑 Defect 2, as an invariant rather than a count.

    ``[REDACTED:CITIZENSHIP-NO]`` is Latin text in square brackets, which is exactly the
    shape ``legacy_ascii`` treats as legacy-encoded Nepali leaking through. Before the
    placeholders were registered and stripped, redacting a document moved its verdict.
    Measured on the corpus: 500 documents are changed by redaction, 1 moved verdict with the
    old instrument and 0 move with this one.
    """

    # ⚠️ The fixture carries OTHER digits on purpose. A first version's only digits were
    # the ones redaction removes, which took the `structure` axis from clean to suspect --
    # correctly, since that axis wants a report to contain digits at all. Real audit
    # reports carry thousands, which is why the corpus measurement moved no axis on any of
    # its 500 redaction-affected documents. A fixture thin enough to lose its digit signal
    # is measuring its own thinness.
    original = (
        "नेपाल सरकार\nमहालेखापरीक्षकको कार्यालय\n\n"
        "आर्थिक वर्ष २०७४/७५ को बेरुजु रकम रु. ९,८२,८४,२८८ रहेको छ।\n"
        "जम्मा खर्च रु. ५,२३,९६,९०८ र राजस्व रु. ४,०७,००,७९२ छ।\n"
        "नागरिकता नं. १२-३४-५६७८९ भएका निवेदकको विवरण तल दिइएको छ।\n"
        "जन्म मिति २०५०/०१/०२ रहेको छ।\n"
    ) * 12
    redacted, journal, _ = redact_inline_text(original)
    assert journal, "the fixture must actually be redacted or this test is vacuous"
    assert placeholders.contains_placeholder(redacted)

    before, after = audit_text(original), audit_text(redacted)

    assert after["verdict"] == before["verdict"]
    for axis in before["checks"]:
        assert after["checks"][axis]["verdict"] == before["checks"][axis]["verdict"], (
            f"redaction moved the {axis} axis from "
            f"{before['checks'][axis]['verdict']} to {after['checks'][axis]['verdict']}"
        )


def test_normalising_removes_every_registered_placeholder() -> None:
    """Not just the two the inline pass writes -- every registered form, including the
    table forms and the three the release pipeline writes outside this package."""

    text = "क " + " ".join(placeholders.ALL) + " ख"
    normalised = normalise_for_audit(text)

    assert "REDACTED" not in normalised
    assert normalised.startswith("क") and normalised.endswith("ख")
