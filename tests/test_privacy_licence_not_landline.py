"""A driving licence number has the landline shape. Only its label says otherwise.

The sentence below is quoted from the CIAA's 25th annual report (page 191, fiscal year
2071/72), where it enumerates the licences seized in a licence-tampering case. Before this
rule, ``LANDLINE`` matched six of the eleven and left five -- the survivors spared not by
any judgement but by the digit lookbehind (``००६-`` prefixes) and the ``{6,7}`` bound
(five-digit tails). Those numbers are the evidence in that finding.
"""

from __future__ import annotations

import re

from likhit.privacy import RedactionMode, redact_text, scan_text
from likhit.privacy.contact import iter_contact_targets
from likhit.privacy.signals import LANDLINE, introduced_by_document_id_label

#: Quoted verbatim, line breaks included. In one entry the label ends a line and its `नं.`
#: opens the next, which is why the gap has to cross a single newline -- a horizontal-only
#: gap spared five of these six and removed that one.
SEIZED_LICENCES = (
    "कुमार भण्डारी स.चा.अ.पत्र नं. ००६-७२५९२६ को वि वर्ग,  \n"
    "शान्तिलक्ष्मी शाक्य स.चा.अ.पत्र नं. ०६-१४००६८ को वि वर्ग, काशीराम भण्डारी  \n"
    "स.चा.अ.पत्र नं. ०६-०१८१५५, ओमप्रकाश प्रसाद स.चा.अ.पत्र नं.००६-७१४०९० को ए  \n"
    "वर्ग, रामशरण देवकोटा स.चा.अ.पत्र नं. ०५-५२१२६, रामशरण श्रीमलको स.चा.अ.पत्र  \n"
    "नं.०६-४८०२०२ वि वर्ग, टीकाराम वि.क. स.चा.अ.पत्र नं.०६-४८०२०३ वि वर्ग,  \n"
    "जितबहादुर मगरको स.चा.अ.पत्र नं. ०६-४८१९७८ ए वर्ग गरी जम्मा ९ जना, राम  \n"
    "मल्लको स.चा.अ.पत्र नं. ०६-२५९७८९ को वि वर्ग"
)


def test_no_licence_number_is_removed_as_a_landline() -> None:
    """The whole enumeration survives redaction, not a majority of it."""

    redacted = redact_text(SEIZED_LICENCES, mode=RedactionMode.STRICT).text

    assert "[REDACTED:PHONE-NO]" not in redacted
    assert redacted == SEIZED_LICENCES


def test_the_pattern_alone_still_matches_them() -> None:
    """🛑 Pins that the label is doing the work, not a narrowed :data:`LANDLINE`.

    Without this, the test above would keep passing if someone "fixed" the problem by
    tightening the shape pattern -- which would silently stop removing real Kathmandu
    landlines, since `०६-१४००६८` and `०१-४४१२३४५` are the same shape.
    """

    assert len(LANDLINE.findall(SEIZED_LICENCES)) == 6


def test_the_scanner_does_not_report_them_either() -> None:
    """The disclosure and the removal have to agree about what is present.

    A licence counted as a landline in the published "what remains" report is the same
    defect as one removed, so suppressing it on only one side trades a redaction bug for a
    reporting bug.
    """

    assert scan_text(SEIZED_LICENCES)[0]["landline"] == 0


def test_a_real_landline_beside_a_licence_is_still_removed() -> None:
    """⚠️ The suppression is anchored and bounded, so it does not leak down the sentence.

    Both appear in one paragraph in these reports. A window that merely looked for the
    label *somewhere nearby* would spare the switchboard too.
    """

    text = "रामशरण श्रीमलको स.चा.अ.पत्र नं.०६-४८०२०२ वि वर्ग, कार्यालयको फोन ०१-४४१२३४५ हो।"

    redacted = redact_text(text, mode=RedactionMode.STRICT).text

    assert "०६-४८०२०२" in redacted
    assert "०१-४४१२३४५" not in redacted
    assert redacted.count("[REDACTED:PHONE-NO]") == 1
    assert scan_text(text)[0]["landline"] == 1


def test_the_label_may_sit_hard_against_the_number() -> None:
    """`नं.०६-४८०२०२` occurs with no space at all, and `नं. ०६-१४००६८` with one."""

    for spacing in ("नं.०६-४८०२०२", "नं. ०६-४८०२०२", "नं ०६-४८०२०२", "नम्बर ०६-४८०२०२"):
        text = f"प्रतिवादीको स.चा.अ.पत्र {spacing} को वि वर्ग"
        assert "[REDACTED:PHONE-NO]" not in redact_text(
            text, mode=RedactionMode.STRICT
        ).text, spacing


def test_suppression_is_counted_rather_than_silent() -> None:
    """A refusal a reader cannot see is indistinguishable from a pattern that missed."""

    from collections import Counter

    stats: Counter = Counter()
    list(iter_contact_targets(SEIZED_LICENCES, stats))

    assert stats["landline_refused_document_id"] == 6
    assert stats["landline_targeted"] == 0


def test_the_helper_is_anchored_to_the_value_start() -> None:
    """🛑 The anchor, tested directly, because it -- not the read window -- is the guard.

    The 32-character slice in :func:`introduced_by_document_id_label` looks like the thing
    stopping a label from reaching a later number, and it is not: widening it to 200 changes
    no result, because the pattern has to match *ending* at the value. So the anchor is
    asserted here on its own, where dropping it fails even though every window-based
    assertion would still pass.
    """

    text = "स.चा.अ.पत्र नं. ०६-१४००६८"
    start = re.search(r"०६-१४००६८", text).start()

    assert introduced_by_document_id_label(text, start)
    # The same label, with a real landline later in the same sentence: the label does not
    # end at that value, so it must not suppress it however close the two sit.
    far = "स.चा.अ.पत्र नं. ०६-१४००६८ भनी उल्लेख गरिएको र सोही कार्यालयको ०१-४४१२३४५"
    assert not introduced_by_document_id_label(far, re.search(r"०१-४४१२३४५", far).start())


def test_a_mention_of_licences_does_not_suppress_a_nearby_phone() -> None:
    """🛑 Isolates the anchor from the read window -- they were each covering the other.

    Every other case here is rejected by *both* the ``$`` anchor and the 32-character
    slice, so removing either one alone left the suite green. Here the label sits well
    inside the window but does not run up to the value: `स.चा.अ.पत्र` is followed by
    ordinary prose, not by `नं.` and digits. Only the anchor rejects it -- every component
    after the label in the pattern is optional, so an unanchored search matches the bare
    label anywhere in the slice and would suppress a real switchboard number.
    """

    text = "स.चा.अ.पत्र बारे सोध्न ०१-४४१२३४५ मा फोन गर्नुहोस्"
    start = re.search(r"०१-४४१२३४५", text).start()

    assert start < 32, "the label must sit inside the read window for this to isolate"
    assert not introduced_by_document_id_label(text, start)
    assert "०१-४४१२३४५" not in redact_text(text, mode=RedactionMode.STRICT).text
