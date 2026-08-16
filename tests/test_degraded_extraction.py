"""A resource failure must not masquerade as a document verdict.

`_try_convert_with_likhit` swallowed EVERY exception at `logger.debug` and returned
None, which `convert()` reads as "likhit cannot read this document" and answers by
falling through to the default path. That path applies no legacy font decode and
emits no page anchors, so a machine running out of memory produced a silently
degraded transcript and exited 0.

Measured on the whole of `samples/kanunpatrika.pdf` (128 pages), through the real
MarkItDown plugin loop, with a MemoryError injected at the extraction call:

    healthy                 286,677 chars   128 page anchors
    resource failure        368,487 chars     0 page anchors   exit 0

🛑 The degraded transcript is 81,810 characters **LONGER** than the healthy one --
undecoded legacy keystrokes expand rather than shrink -- so a "more text is better"
quality heuristic actively prefers the corrupted output. Nothing downstream could
have caught this by size.

🛑 The obvious fix does not work, and this file pins that so it is not re-attempted.
Re-raising MemoryError out of `convert()` cannot reach the caller: likhit is
registered as a MarkItDown plugin (`_plugin.py`, priority -2.0) and MarkItDown's
converter loop wraps every `convert()` call in `except Exception`. The raise is
recorded as a failed attempt, the loop advances to the plain PdfConverter, and its
output is returned with exit 0 -- 279,829 chars and 0 anchors, *worse* than the
fallback the converter already takes, because it loses the numeric-boundary repairs
too.

So the signal is in-band: a marker in the transcript itself, in the same
HTML-comment shape as the page anchors. It survives Markdown rendering, any
transport, and a driver that captures the child's stderr and discards it on success
-- which is what the v11 build did, recording zero errors while shipping five
damaged documents.

⚠️ The figures above come from the MarkItDown loop. Calling
`NepaliPdfConverter.convert` directly is a DIFFERENT instrument and gives 302,049 /
369,913 on the same file, because the loop also runs candidate scoring. Don't
reconcile them; say which one a number is.
"""

from __future__ import annotations

import io
import logging
import pathlib

from markitdown import DocumentConverterResult, StreamInfo
import pytest

from likhit.converters import nepali_pdf as nepali_pdf_module
from likhit.converters.nepali_pdf import (
    DEGRADED_MARKER_PATTERN,
    NepaliPdfConverter,
    _RESOURCE_FAILURES,
    _stamp_degraded,
    degraded_marker,
)

SAMPLE = "samples/kanunpatrika.pdf"

#: Every behavioural test below slices to three pages. Converting all 128 costs 11.5s
#: per call and there are a dozen calls; three pages costs 0.3s. Verified that the
#: slice preserves every property asserted here -- the marker appears, the anchor
#: count goes 3 -> 0, and the degraded transcript is still LONGER than the healthy one
#: (9,864 vs 6,671) -- so this is a speed choice and not a weakening of the tests.
PAGES = "1-3"


@pytest.fixture(scope="module")
def sample_pdf() -> bytes:
    path = pathlib.Path(SAMPLE)
    assert path.is_file(), f"missing fixture {path}"
    return path.read_bytes()


def _convert(raw: bytes) -> DocumentConverterResult:
    return NepaliPdfConverter().convert(
        io.BytesIO(raw),
        StreamInfo(extension=".pdf", mimetype="application/pdf"),
        pages=PAGES,
    )


def _inject(monkeypatch, exc: BaseException) -> None:
    """Fail the extraction itself, leaving every other frame real."""

    def boom(raw: bytes):
        raise exc

    monkeypatch.setattr(nepali_pdf_module, "_convert_with_likhit", boom)


# --------------------------------------------------------------------------- marker


def test_the_marker_and_its_pattern_agree() -> None:
    """Written twice -- as a formatter and as a regex -- so they are pinned together."""

    match = DEGRADED_MARKER_PATTERN.search(degraded_marker("MemoryError"))
    assert match is not None
    assert match.group(1) == "MemoryError"


def test_the_marker_is_shaped_like_a_page_anchor() -> None:
    """An HTML comment, so it is invisible to a Markdown reader but greppable."""

    marker = degraded_marker("MemoryError")
    assert marker.startswith("<!--") and marker.endswith("-->")


def test_stamping_is_a_no_op_when_there_was_no_resource_failure() -> None:
    result = DocumentConverterResult(markdown="ठीक छ")
    assert _stamp_degraded(result, None) is result


def test_stamping_preserves_the_title() -> None:
    """A new result object is built, so anything not copied is silently dropped."""

    result = DocumentConverterResult(markdown="ठीक छ", title="शीर्षक")
    assert _stamp_degraded(result, "MemoryError").title == "शीर्षक"


def test_stamping_keeps_the_whole_original_transcript() -> None:
    result = DocumentConverterResult(markdown="पहिलो\n\nदोस्रो")
    assert "पहिलो\n\nदोस्रो" in _stamp_degraded(result, "MemoryError").markdown


# ------------------------------------------------------------------ classification


def test_memory_error_is_classified_as_a_resource_failure() -> None:
    assert issubclass(MemoryError, _RESOURCE_FAILURES)


def test_oserror_is_classified_as_a_resource_failure() -> None:
    """ENOSPC on the temp-file write and EMFILE are the same class of thing as
    MemoryError: a property of the machine, never of the document."""

    assert issubclass(OSError, _RESOURCE_FAILURES)


def test_an_extraction_error_is_not_a_resource_failure() -> None:
    """A malformed or image-only PDF genuinely IS a document verdict. If this ever
    becomes a resource failure the marker starts appearing on ordinary documents and
    stops meaning anything."""

    from likhit.errors import ExtractionError

    assert not issubclass(ExtractionError, _RESOURCE_FAILURES)


# ------------------------------------------------------------- behaviour, converter


def test_a_resource_failure_stamps_the_transcript(monkeypatch, sample_pdf) -> None:
    _inject(monkeypatch, MemoryError("simulated double-buffering"))
    match = DEGRADED_MARKER_PATTERN.search(_convert(sample_pdf).markdown)
    assert match is not None, "a resource failure produced an unmarked transcript"
    assert match.group(1) == "MemoryError"


def test_an_oserror_stamps_the_transcript_with_its_own_type(
    monkeypatch, sample_pdf
) -> None:
    """The reason carries the TYPE, so the marker distinguishes out-of-memory from
    out-of-disk without anyone reading the log."""

    _inject(monkeypatch, OSError("No space left on device"))
    match = DEGRADED_MARKER_PATTERN.search(_convert(sample_pdf).markdown)
    assert match is not None
    assert match.group(1) == "OSError"


def test_a_document_verdict_does_not_stamp_the_transcript(
    monkeypatch, sample_pdf
) -> None:
    """The distinction the whole change rests on. likhit declining a document is
    common and correct; marking it would make the marker meaningless."""

    _inject(monkeypatch, ValueError("not a PDF likhit can read"))
    assert DEGRADED_MARKER_PATTERN.search(_convert(sample_pdf).markdown) is None


def test_the_healthy_path_is_not_stamped(sample_pdf) -> None:
    assert DEGRADED_MARKER_PATTERN.search(_convert(sample_pdf).markdown) is None


def test_a_resource_failure_logs_at_warning_with_the_exception_type(
    monkeypatch, sample_pdf, caplog
) -> None:
    """At debug this was the only trace of a fallback that changes every character,
    and the TYPE is what separates a resource failure from a malformed PDF."""

    _inject(monkeypatch, MemoryError("simulated"))
    with caplog.at_level(logging.WARNING):
        _convert(sample_pdf)
    assert "resource failure" in caplog.text
    assert "MemoryError" in caplog.text


def test_a_document_verdict_stays_at_debug(monkeypatch, sample_pdf, caplog) -> None:
    _inject(monkeypatch, ValueError("unreadable"))
    with caplog.at_level(logging.WARNING):
        _convert(sample_pdf)
    assert "resource failure" not in caplog.text


# --------------------------------------------------- the frame C17's tests missed


def test_the_marker_survives_the_full_markitdown_plugin_loop(monkeypatch) -> None:
    """The end-to-end proof, and the reason the signal is in-band rather than a raise.

    likhit is only ever reached through this loop, so a test that calls
    `NepaliPdfConverter.convert` directly cannot see what the caller actually gets.
    """

    from markitdown import MarkItDown

    _inject(monkeypatch, MemoryError("simulated"))
    result = MarkItDown(enable_plugins=True).convert(SAMPLE, pages=PAGES)
    assert DEGRADED_MARKER_PATTERN.search(result.markdown) is not None


def test_re_raising_cannot_reach_the_caller(monkeypatch) -> None:
    """Pins the refutation, so the re-raise fix is not attempted a second time.

    MarkItDown catches Exception around every `converter.convert()` call and then
    tries the next converter. A MemoryError raised out of `convert()` therefore does
    not propagate; the caller gets the plain PdfConverter's output and exit 0.
    """

    from markitdown import MarkItDown

    def raising_convert(self, file_stream, stream_info, **kwargs):
        raise MemoryError("simulated")

    monkeypatch.setattr(NepaliPdfConverter, "convert", raising_convert)

    # No pytest.raises: the point is that nothing is raised.
    result = MarkItDown(enable_plugins=True).convert(SAMPLE, pages=PAGES)
    assert result.markdown, "expected the loop to fall through to another converter"
    # And the fallback it lands in is the unmarked, undecoded one -- which is exactly
    # why re-raising is worse than the in-band marker.
    assert DEGRADED_MARKER_PATTERN.search(result.markdown) is None


def test_a_transient_that_recovers_does_not_stamp_the_transcript(
    monkeypatch, sample_pdf
) -> None:
    """The false positive a mutation test caught, and the reason the reason is cleared.

    A MemoryError is exactly the kind of failure that can be transient, so the first
    likhit attempt can fail and the second succeed. The first draft kept the reason
    sticky, and stamped the resulting transcript -- one that likhit had produced, with
    every page anchor intact. A marker on a healthy transcript is worse than no
    marker: a corpus sweep would quarantine good documents on it.
    """

    real = nepali_pdf_module._convert_with_likhit
    attempts: list[int] = []

    def transient(raw: bytes):
        attempts.append(1)
        if len(attempts) == 1:
            raise MemoryError("transient: recovers on retry")
        return real(raw)

    monkeypatch.setattr(nepali_pdf_module, "_convert_with_likhit", transient)
    markdown = _convert(sample_pdf).markdown

    assert len(attempts) >= 2, (
        "the second likhit attempt was never reached, so this test proves nothing "
        "about clearing the reason"
    )
    from likhit.renderers.markdown import page_anchor_numbers

    assert page_anchor_numbers(markdown), "expected a likhit transcript with anchors"
    assert DEGRADED_MARKER_PATTERN.search(markdown) is None, (
        "a transcript likhit successfully produced was stamped as degraded"
    )


def test_a_failure_on_both_attempts_still_stamps(monkeypatch, sample_pdf) -> None:
    """The other side of the clearing rule: clearing must not lose a real failure."""

    _inject(monkeypatch, MemoryError("persistent"))
    assert DEGRADED_MARKER_PATTERN.search(_convert(sample_pdf).markdown) is not None


def test_the_degraded_transcript_is_not_shorter_than_the_healthy_one(
    monkeypatch, sample_pdf
) -> None:
    """The reason no downstream size or "did we get text" check could catch this.

    Asserted as an inequality rather than the measured figures, because those move
    with any decode change; the DIRECTION is the finding.
    """

    healthy = len(_convert(sample_pdf).markdown)
    _inject(monkeypatch, MemoryError("simulated"))
    degraded = len(_convert(sample_pdf).markdown)
    assert degraded >= healthy, (
        f"degraded={degraded} healthy={healthy}: if the degraded transcript is now "
        "shorter, a length heuristic could detect this and the module docstring is stale"
    )


def test_the_degraded_transcript_loses_every_page_anchor(
    monkeypatch, sample_pdf
) -> None:
    """Page anchors are a likhit feature, so the fallback cannot emit any. This is
    the property that made the damage recognisable after the fact -- but only after,
    which is what the marker fixes."""

    from likhit.renderers.markdown import page_anchor_numbers

    healthy = page_anchor_numbers(_convert(sample_pdf).markdown)
    assert healthy, "fixture must produce anchors, or this test proves nothing"

    _inject(monkeypatch, MemoryError("simulated"))
    assert page_anchor_numbers(_convert(sample_pdf).markdown) == []
