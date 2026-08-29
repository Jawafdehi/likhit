"""D4: `numeric_damage` must not read an account number as a merged cell.

⚠️ Four tests from the original file are NOT here. They cover the merge oracle -- a corpus
artifact keyed to a particular conversion -- and `audit_one`, the file-level entry point.
Neither has moved into the package yet, so porting their tests would mean porting assertions
about code that is not here. They are listed in the deferred set beside this file.

Ground truth for every expectation here is `verify_numeric_merges.py`, which put
all 14,608 flagged runs in V8 to the page geometry: 12,540 were single cells, so
the `>= 15 digits` rule alone ran at precision 0.142.
"""

from likhit.quality.axes import check_numeric_damage

#: A payment table row as V8 renders it: the account number is 16 digits and sits
#: alone in its column. The old rule called every one of these a merged cell.
ACCOUNT_ROWS = "\n".join(
    f"| {n} | SITA GIRI | 2.99 | 2017-07-20 |"
    for n in (
        "2370100000277119",
        "2370100002570119",
        "2080100002740119",
        "01211100137512000001",
        "660083801800018",
        "790118603700001",
    )
)

#: Beruju figures that really did run together, from `11792__हेटौँडा उप महानगरपालिका`
#: page 8, where geometry puts a ruled column edge inside each token.
MERGED_AMOUNTS = "\n".join(
    f"| {n} |"
    for n in (
        "९,८२,८४,२८८१,९८,११,८००",
        "४,०७,००,७९२१,१२,७५,५००",
        "५,२३,९६,९०८४,२०,६१५२५,५१,४५०३,८१,४९,३४३",
        "534,000.00534,000.00",
        "3,00,000.003,00,000.00",
        "१०३५,७०,१५,७१७५,९२,३००",
    )
)


def test_account_numbers_are_not_merged_cells():
    verdict, evidence = check_numeric_damage(ACCOUNT_ROWS)

    assert verdict == "clean"
    assert evidence["merged_cell_runs"] == 0
    # The population is unchanged -- all six are still long runs. Only the shape
    # test rejects them, which is what makes the improvement attributable.
    assert evidence["long_runs"] == 6


def test_amounts_that_really_ran_together_are_still_counted():
    verdict, evidence = check_numeric_damage(MERGED_AMOUNTS)

    assert evidence["merged_cell_runs"] == 6
    assert verdict == "garbled"  # more than five


def test_one_merged_run_among_account_numbers_is_still_found():
    verdict, evidence = check_numeric_damage(
        ACCOUNT_ROWS + "\n| ९,८२,८४,२८८१,९८,११,८०० |"
    )

    assert verdict == "suspect"
    assert evidence["merged_cell_runs"] == 1
    assert evidence["long_runs"] == 7


def test_a_damaged_amount_is_counted_once_not_twice():
    # `fffd_in_number` owns U+FFFD; counting it as a merge as well would double
    # the reported damage for one figure.
    verdict, evidence = check_numeric_damage("| 1,23,45,678�1,23,45,678 |")

    assert evidence["fffd_in_number"] == 1
    assert evidence["merged_cell_runs"] == 0
    assert verdict == "suspect"


def test_a_short_merge_is_left_alone_by_design():
    # Below the digit floor the shape test measured precision 0.14, so the floor
    # stays and short merges are knowingly out of scope -- recorded, not hidden.
    _verdict, evidence = check_numeric_damage("| ५९,०९,९५११,१०,००० |")

    assert evidence["long_runs"] == 0
    assert evidence["merged_cell_runs"] == 0


def test_evidence_says_where_the_count_came_from():
    _verdict, text_only = check_numeric_damage(MERGED_AMOUNTS)
    _verdict, from_geometry = check_numeric_damage(MERGED_AMOUNTS, confirmed_merges=2)

    assert text_only["merge_source"] == "text"
    assert from_geometry["merge_source"] == "geometry"
    # The oracle overrides the estimate, and the estimate stays visible beside it.
    assert from_geometry["merged_cell_runs"] == 2
    assert from_geometry["merge_shaped_runs"] == 6


def test_the_oracle_can_clear_a_file_the_text_rule_flags():
    # 157 runs in `3442__1613972298Lalitpur` are one amount written twice inside a
    # single cell; 106 more in the same document are real merges. Only geometry
    # separates them, so a zero from the oracle has to win.
    verdict, evidence = check_numeric_damage(MERGED_AMOUNTS, confirmed_merges=0)

    assert verdict == "clean"
    assert evidence["merged_cell_runs"] == 0


def test_a_transcript_with_no_numbers_is_clean():
    assert check_numeric_damage("कार्यालयको प्रतिवेदन")[0] == "clean"


# --- the oracle artifact ---------------------------------------------------


def test_the_geometry_oracle_path_is_reachable_across_every_band() -> None:
    """The geometry-oracle branch at every band, and a note on what no gate can see.

    `confirmed_merges` lets a caller substitute a count measured from page geometry for the
    text rule's estimate. This migration annotated it `set | None`, which is wrong -- it has
    always been a COUNT -- so anyone following the signature got
    `TypeError: unsupported operand type(s) for +: 'int' and 'set'`, including through the
    public `audit_text`.

    ⚠️ **Why neither gate caught that, measured rather than assumed.** Two tests above
    (`test_evidence_says_where_the_count_came_from`, `test_the_oracle_can_clear_a_file_the_
    text_rule_flags`) already pass integers here, so the branch WAS exercised -- pytest was
    green because the runtime is correct. And with the wrong annotation restored, `ty` reports
    the same 8 diagnostics and never names this parameter, even with int literals at the call
    site. So the defect was unfalsifiable by either gate and only review could find it.

    This test does not change that; it is not a guard against a bad annotation. What it adds
    is the branch asserted across all three bands with `merge_source` pinned, so a change that
    silently stops taking the oracle path fails here instead of passing quietly.
    """

    text = "| १२३४५६७८९०१२३४५६ |\n| 534,000.00534,000.00 |\n" * 3

    for count, expected in ((0, "clean"), (3, "suspect"), (7, "garbled")):
        verdict, evidence = check_numeric_damage(text, confirmed_merges=count)

        assert verdict == expected, (count, evidence)
        assert evidence["merged_cell_runs"] == count
        assert evidence["merge_source"] == "geometry", (
            "the oracle branch was not taken, so this test is measuring the text rule"
        )

    # And the default really is the other branch, or the assertions above prove nothing.
    assert check_numeric_damage(text)[1]["merge_source"] == "text"
