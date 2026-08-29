"""Ported from the OAG run directory, where these 14 tests were never gated by CI.

The only change is how the module is loaded. The original resolved it through
``importlib.util.spec_from_file_location`` with a ``TABLE_REDACTOR_UNDER_TEST`` environment
override, because it lived beside the tool in a run directory and had to survive being
mutation-staged. In-package it is a plain import, so the override -- and the risk of a test
run silently measuring a different file than the one under review -- is gone.

Every fixture is synthetic: ``राम``/``सीता`` and obviously-invented numbers. That is what
makes them publishable, and it is deliberate rather than incidental.
"""

import pytest

from likhit.privacy import redact_tables as redactor


def redact(text):
    output, targets, stats = redactor.redact_table_text(text)
    return output, targets, stats


def test_header_column_redacts_values_but_not_amounts():
    source = (
        "| नाम | जन्म मिति | रकम |\n"
        "| राम | २०५०/०१/०२ | १२३४५ |\n"
        "| सीता | २०५१/०२/०३ | ९९९९९ |\n"
    )

    output, targets, _ = redact(source)

    assert output == (
        "| नाम | जन्म मिति | रकम |\n"
        "| राम | [REDACTED:TABLE-DATE-OF-BIRTH] | १२३४५ |\n"
        "| सीता | [REDACTED:TABLE-DATE-OF-BIRTH] | ९९९९९ |\n"
    )
    assert len(targets) == 2
    assert {target.classification for target in targets} == {"date_of_birth"}


def test_header_column_supports_citizenship_abbreviation():
    source = "| ना. प्र. नं. | नाम |\n| १२-३४-५६७८९ | राम |\n"

    output, targets, _ = redact(source)

    assert "[REDACTED:TABLE-CITIZENSHIP-NO]" in output
    assert len(targets) == 1
    assert targets[0].evidence[0].mechanism == "header_column"


def test_same_row_prefers_one_adjacent_candidate():
    source = "| नागरिकता प्रमाणपत्र नं. | १२-३४-५६७८९ | १२३४५६७ |\n"

    output, targets, _ = redact(source)

    assert output == (
        "| नागरिकता प्रमाणपत्र नं. | [REDACTED:TABLE-CITIZENSHIP-NO] | १२३४५६७ |\n"
    )
    assert len(targets) == 1
    assert targets[0].evidence[0].mechanism == "same_row_adjacent"


def test_rerun_does_not_promote_a_refused_ambiguous_value():
    source = "| नागरिकता प्रमाणपत्र नं. | १२-३४-५६७८९ | १२३४५६७ |\n"

    first_output, first_targets, _ = redact(source)
    second_output, second_targets, stats = redact(first_output)

    assert len(first_targets) == 1
    assert second_output == first_output
    assert not second_targets
    assert stats["citizenship_refused_already_redacted_row"] == 1


def test_inline_placeholder_does_not_hide_a_separate_table_value():
    source = "| जन्म मिति [REDACTED:DATE-OF-BIRTH] | २०५०/०१/०२ |\n"

    output, targets, _ = redact(source)

    assert output == (
        "| जन्म मिति [REDACTED:DATE-OF-BIRTH] | [REDACTED:TABLE-DATE-OF-BIRTH] |\n"
    )
    assert len(targets) == 1


def test_same_row_accepts_one_nonadjacent_candidate():
    source = "| नागरिकता प्रमाणपत्र नं. | विवरण | १२-३४-५६७८९ |\n"

    output, targets, _ = redact(source)

    assert output.endswith("| विवरण | [REDACTED:TABLE-CITIZENSHIP-NO] |\n")
    assert len(targets) == 1
    assert targets[0].evidence[0].mechanism == "same_row_unique"


def test_same_row_refuses_multiple_nonadjacent_candidates():
    source = "| नागरिकता प्रमाणपत्र नं. | विवरण | १२-३४-५६७८९ | ९८-७६-५४३२१ |\n"

    output, targets, stats = redact(source)

    assert output == source
    assert not targets
    assert stats["citizenship_refused_ambiguous_row"] == 1


def test_citizenship_recommendation_count_is_not_an_identifier():
    source = "| नागरिकता सिफारिस नं. | १२३४५६७ |\n"

    output, targets, _ = redact(source)

    assert output == source
    assert not targets


@pytest.mark.parametrize(
    "value",
    [
        "मिति २०५०/०१/०२",
        "२०५०/०१/०२ अनुमानित",
        "१२३४",
        "१२३४५६७८९०१",
    ],
)
def test_dob_requires_a_whole_cell_and_plausible_length(value):
    source = f"| जन्म मिति | {value} |\n"

    output, targets, _ = redact(source)

    assert output == source
    assert not targets


def test_dual_label_uses_an_explicit_ambiguous_placeholder_once():
    source = "| नागरिकता प्रमाणपत्र नं. / जन्म मिति | रकम |\n| २०५०/०१/०२ | १२३४५ |\n"

    output, targets, _ = redact(source)

    assert output == (
        "| नागरिकता प्रमाणपत्र नं. / जन्म मिति | रकम |\n"
        "| [REDACTED:TABLE-PERSONAL-VALUE] | १२३४५ |\n"
    )
    assert len(targets) == 1
    assert targets[0].classification == "ambiguous_personal_value"
    assert {item.kind for item in targets[0].evidence} == {
        "citizenship",
        "date_of_birth",
    }


def test_one_selected_kind_protects_ambiguous_dual_label_context_on_rerun():
    source = "| २०५०/०१/०२ | १२३४५ | विवरण | नागरिकता प्रमाणपत्र नं. / जन्म मिति |\n"

    first_output, first_targets, _ = redact(source)
    second_output, second_targets, _ = redact(first_output)

    assert first_output == (
        "| [REDACTED:TABLE-PERSONAL-VALUE] | १२३४५ | विवरण "
        "| नागरिकता प्रमाणपत्र नं. / जन्म मिति |\n"
    )
    assert len(first_targets) == 1
    assert first_targets[0].classification == "ambiguous_personal_value"
    assert first_targets[0].protected_kinds == (
        "citizenship",
        "date_of_birth",
    )
    assert second_output == first_output
    assert not second_targets


def test_one_kind_redacted_protects_a_different_labels_ambiguous_row():
    """The shape that aborted the whole v18 run, reduced to one row.

    A dual-kind label at column 0 sees two candidates and refuses both kinds as
    ambiguous. A citizenship label at column 5 has exactly one adjacent candidate
    and takes it. That removal shrinks column 0's candidate set to one, so keying
    row safety on `kind` promoted the survivor on rerun and `main`'s rescan found
    a target in bytes the previous pass had written. Measured on the v18 tree as
    one document (11862) and one cell; `local-level-report/11862` collapses a whole
    table onto a single line, which is how one row comes to carry both labels.
    """
    source = (
        "| नागरिकता प्रमाणपत्र नं. / जन्म मिति | विवरण | २०५०/०१/०२ "
        "| विवरण | २०५१/०२/०३ | ना. प्र. नं. |\n"
    )

    first_output, first_targets, first_stats = redact(source)
    second_output, second_targets, second_stats = redact(first_output)

    # Pass one is unchanged: the adjacent citizenship candidate, and nothing else.
    assert len(first_targets) == 1
    assert first_targets[0].ref.column_index == 4
    assert first_targets[0].evidence[0].mechanism == "same_row_adjacent"
    assert first_output == (
        "| नागरिकता प्रमाणपत्र नं. / जन्म मिति | विवरण | २०५०/०१/०२ "
        "| विवरण | [REDACTED:TABLE-CITIZENSHIP-NO] | ना. प्र. नं. |\n"
    )
    assert first_stats["citizenship_refused_ambiguous_row"] == 1
    assert first_stats["date_of_birth_refused_ambiguous_row"] == 1

    # Pass two must be a no-op. Before the fix it selected column 2 as
    # `same_row_unique`, which is what `main`'s post-redaction rescan rejected.
    assert not second_targets
    assert second_output == first_output
    assert second_stats["date_of_birth_refused_already_redacted_row"] == 1
    assert second_stats["citizenship_refused_already_redacted_row"] == 2


def test_changed_document_preserves_non_target_unicode_and_crlf():
    decomposed = "क\u093c"
    source = f"{decomposed}\r\n| जन्म मिति | रकम |\r\n| २०५०/०१/०२ | १२३४५ |\r\n"

    output, targets, _ = redact(source)

    assert output.startswith(f"{decomposed}\r\n")
    assert decomposed in output
    assert output.count("\r\n") == source.count("\r\n")
    assert len(targets) == 1


def test_no_target_returns_the_identical_string():
    source = "क\u093c\n| विवरण | रकम |\n| परीक्षण | १२३४५ |\n"

    output, targets, _ = redact(source)

    assert output == source
    assert not targets
