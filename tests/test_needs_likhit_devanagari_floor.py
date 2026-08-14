"""`_default_pdf_result_needs_likhit` must not let a Devanagari *count* silence the
garble evidence.

Before this file the predicate had no test at all, and an absolute floor
(`devanagari_chars >= 20`) returned False before any ratio term was computed. On
`markdown-quality-v14` document `2997__1612859754Arnama Gaupalika` that floor cleared on 56
Devanagari characters -- 0.12% of the document's 45,325 non-space characters -- so 25,217
characters of raw Preeti shipped as a transcript. likhit does not raise on that PDF; it
returns 40,481 Devanagari characters and outscores the default 130,202 to -25,795. It was
never asked.

The fixtures below are built from the *measured* term values of that document rather than
from round numbers, so they exercise the branch the corpus actually took.
"""

from likhit.converters.nepali_pdf import _default_pdf_result_needs_likhit

#: One line of `2997`'s shipped transcript. Raw Preeti keystrokes read as Latin; under the
#: Preeti map this is `महालेखापरीक्षकको कार्यालय काठमाडौँ, नेपाल`.
PREETI_LINE = "dxfn]vfk/LIfssf] sfof{no sf7df8f}F, g]kfn"

#: A pipe row, because `pipe_heavy_lines` was 319 on the real document.
PREETI_ROW = "| dxfn]vfk/LIfssf] | sfof{no | @)&& | #=$! |"

#: 56 Devanagari characters: exactly what cleared the old floor of 20 on the real document.
#: Not 20 and not 100 -- the point is that the measured value was well past the threshold.
DEVANAGARI_56 = "क" * 56


def _preeti_document(devanagari: str = "") -> str:
    return "\n".join([devanagari, *([PREETI_LINE, PREETI_ROW] * 20)])


def test_raw_preeti_is_suspected_when_a_little_devanagari_is_present() -> None:
    """The regression case: garble evidence must outrank a Devanagari count.

    Delete the removal of the `devanagari_chars >= 20` short-circuit and this fails.
    """
    document = _preeti_document(DEVANAGARI_56)
    assert len([c for c in document if "ऀ" <= c <= "ॿ"]) >= 20
    assert _default_pdf_result_needs_likhit(document) is True


def test_raw_preeti_is_suspected_with_no_devanagari_at_all() -> None:
    """The same document without the Devanagari must behave identically.

    This is the control for the test above: it passed before the fix too, so on its own it
    proves nothing. Together they show the verdict no longer depends on the count.
    """
    assert _default_pdf_result_needs_likhit(_preeti_document()) is True


def test_devanagari_count_does_not_change_the_verdict() -> None:
    """Sweep the count across the old threshold. The verdict must not move."""
    verdicts = {
        count: _default_pdf_result_needs_likhit(_preeti_document("क" * count))
        for count in (0, 1, 19, 20, 21, 56, 500)
    }
    assert set(verdicts.values()) == {True}, verdicts


def test_clean_nepali_prose_is_still_not_suspected() -> None:
    """The floor's legitimate job -- not re-extracting a document that decoded -- has to
    survive without it. Properly decoded Devanagari trips no ratio term, so the ratio
    disjunction returns False on its own and the floor was never what protected it."""
    prose = "\n".join(
        ["महालेखापरीक्षकको कार्यालय काठमाडौँ नेपाल लेखापरीक्षण प्रतिवेदन आर्थिक वर्ष"] * 20
        + ["Office of the Auditor General Babar Mahal Kathmandu Nepal annual report"]
        * 20
    )
    assert _default_pdf_result_needs_likhit(prose) is False


def test_english_prose_is_still_not_suspected() -> None:
    """v14's 11 genuinely English anchor-free transcripts scored `suspicious_ratio`
    0.0010-0.0095. They must stay unsuspected: re-extracting them costs a likhit run per
    document and gains nothing."""
    english = "\n".join(
        ["The Office of the Auditor General audited the local level reports this year."]
        * 40
    )
    assert _default_pdf_result_needs_likhit(english) is False


def test_too_few_latin_tokens_is_still_short_circuited() -> None:
    """The other half of the early return is untouched and still fires."""
    assert _default_pdf_result_needs_likhit("dxfn] vfk/LIfssf] sfof{no") is False


def test_cid_garbage_still_wins_before_any_ratio() -> None:
    """`cid_garbage_count >= 2` is above the removed floor and is unaffected. `2997` itself
    had exactly 1 and missed this gate by one."""
    assert _default_pdf_result_needs_likhit("(cid:12) hello (cid:13) world") is True
    assert _default_pdf_result_needs_likhit("(cid:12) " + "hello world " * 40) is False
