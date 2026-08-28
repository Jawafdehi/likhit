"""`legacy_ascii` must not read Nepali numerics as legacy-font garble."""

import re

from likhit.quality.axes import FENCE_DELIMITER_RE, FISCAL_SPAN_RE, check_legacy_ascii


def _doc(body: str, devanagari_filler: int = 4000) -> str:
    return ("कार्यालयको लेखापरीक्षण प्रतिवेदन " * (devanagari_filler // 32)) + body


def test_fiscal_span_matches_two_and_three_component_forms():
    for span in ("2070/71", "२०८०/८१", "2013/14", "076/3/23", "०७६/३/२३", "76/3/2"):
        assert FISCAL_SPAN_RE.fullmatch(span), span


def test_fiscal_span_does_not_swallow_legacy_garble():
    # Legacy Preeti garble has letters around its slashes, not digits.
    assert FISCAL_SPAN_RE.sub("", "dxfn]vfk/LIfssf]") == "dxfn]vfk/LIfssf]"


def test_bikram_sambat_dates_do_not_make_a_document_suspect():
    # The regression this fixes: V8 recovered date columns V7 dropped, and every
    # recovered date counted as a legacy-punctuation run.
    dates = " ".join(["076/3/23", "076/3/27", "076/3/29"] * 40)
    text = _doc(dates)

    verdict, evidence = check_legacy_ascii(text, dev_n=4000, lat_n=0)

    assert verdict == "clean", evidence
    assert evidence["legacy_runs"] == 0


def test_fiscal_years_still_do_not_make_a_document_suspect():
    text = _doc(" ".join(["2070/71", "२०८०/८१"] * 60))

    assert check_legacy_ascii(text, dev_n=4000, lat_n=0)[0] == "clean"


def test_real_legacy_encoding_is_still_caught():
    # A document that is mostly legacy bytes must stay garbled.
    text = "dxfn]vfk/LIfssf] jflif{s n]vfk/LIf0f k|ltj]bg " * 200

    verdict, evidence = check_legacy_ascii(text, dev_n=0, lat_n=len(text) // 2)

    assert verdict == "garbled", evidence
    assert evidence["legacy_frac_of_doc"] > 0.15


def test_a_legacy_letterhead_over_clean_nepali_is_suspect_not_clean():
    text = _doc("dxfn]vfk/LIfssf] jflif{s n]vfk/LIf0f " * 12)

    assert check_legacy_ascii(text, dev_n=4000, lat_n=400)[0] in {"suspect", "garbled"}


def test_the_fix_uses_the_pattern_the_denominator_was_measured_with():
    """The calibration this axis was sized against used one specific fence pattern.

    ⚠️ **Weaker than it was, and the reason is worth stating.** The original compared
    `FENCE_DELIMITER_RE` directly against `measure_legacy_denominator.FENCE_DELIMITER`, so
    the two could not drift. That module is a corpus measurement tool and stayed behind, so
    the comparison is now against a COPY of its value recorded here. This still catches a
    change to the shipped pattern -- which is the likely direction -- but it can no longer
    catch the corpus tool changing underneath. Re-pointing this at the real constant is
    worth doing if that tool ever moves too.

    `runs/v10/legacy-denominator-v10.json` sized this fix with that pattern. If they drift,
    the published 658/138 transition counts stop describing the shipped check.
    """

    measured_with = re.compile(r"^\s*```[a-zA-Z]*\s*$", re.M)

    assert FENCE_DELIMITER_RE.pattern == measured_with.pattern
    assert FENCE_DELIMITER_RE.flags == measured_with.flags


def test_legacy_garble_inside_a_fence_is_scored():
    # likhit wraps whole pages in ```text fences, and a legacy-font page is
    # exactly the page that gets wrapped. Deleting fenced content hid it.
    garble = "dxfn]vfk/LIfssf] jflif{s n]vfk/LIf0f k|ltj]bg " * 200
    text = "```text\n" + garble + "\n```\n"

    verdict, evidence = check_legacy_ascii(text, dev_n=0, lat_n=len(garble) // 2)

    assert verdict == "garbled", evidence


def test_a_clean_fenced_body_is_in_the_denominator():
    # The 3.5x inflation, in one document: a 3-line legacy letterhead outside
    # the fence over a clean fenced body. The old denominator excluded the body,
    # so the letterhead was the whole document and scored garbled; 658 documents
    # left the `suspect` band when the body was counted.
    letterhead = "dxfn]vfk/LIfssf] jflif{s n]vfk/LIf0f "
    body = "कार्यालयको लेखापरीक्षण प्रतिवेदन " * 400
    text = letterhead + "\n```text\n" + body + "\n```\n"

    verdict, evidence = check_legacy_ascii(text, dev_n=len(body), lat_n=len(letterhead))

    assert verdict == "clean", evidence
    assert evidence["legacy_frac_of_doc"] < 0.05
