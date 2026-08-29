"""Direct coverage for ``normalize_devanagari_spacing``'s ``preserve_marker_spaces``.

🛑 Why this file exists. The flag shipped with **no production caller on this branch and no
test reference at all** — its callers arrive in the branch stacked on top of this one. Review
demonstrated that it was not merely uncovered but undetectable: forcing it off at function
entry, which deletes *both* behaviours it gates, left the whole suite green. A documented
behaviour change that no shipped path reaches and no test observes is the shape of a defect
that survives review, so the flag is pinned here, on this branch, in both directions.

The rule these tests defend is asymmetric on purpose, and the asymmetry is the point:

- a space **before a marker** must survive, because at that moment it is the only record of
  where the marker's base ended;
- a space **after a virama** must survive too, because nothing local distinguishes
  ``सञ् चालन`` (one word a span split) from ``छन् तथा`` (two words, authored space).

An earlier form of this function deleted the second one whenever markers were being resolved.
That is the defect these tests exist to keep out; see the comment at the branch.
"""

from __future__ import annotations

import pytest

from likhit.extractors import kalimati as kalimati_module
from likhit.extractors.kalimati import normalize_devanagari_spacing

# The deletion `protected_boundary` guards against targets exactly these two markers.
_DELETED_WITHOUT_THE_FLAG = (
    kalimati_module._PUA_REPH,
    kalimati_module._PUA_IKAR,
)

# ⚠️ The protected set names four more, and for these the protection is inert: they are not
# Devanagari combining characters and the deletion's own set does not list them, so their
# space survives whether the flag is set or not. Measured, not assumed — writing this file
# first asserted all six behaved alike and four cases failed. Kept as a test rather than a
# comment so that widening the deletion set silently makes this file fail instead of
# silently changing output.
_INERT_IN_THE_PROTECTED_SET = (
    kalimati_module._PUA_KOKILA_IKAR,
    kalimati_module._PUA_KOKILA_TA,
    kalimati_module._PUA_KOKILA_HALF_SA,
    kalimati_module._PUA_KOKILA_HALF_THA,
)


@pytest.mark.parametrize("marker", _DELETED_WITHOUT_THE_FLAG)
def test_a_space_before_a_marker_survives_only_while_markers_resolve(
    marker: str,
) -> None:
    """Both directions, so neither branch can be deleted without a failure."""

    text = "कारोबारको " + marker

    assert normalize_devanagari_spacing(text, preserve_marker_spaces=True) == text
    assert normalize_devanagari_spacing(text) == "कारोबारको" + marker


@pytest.mark.parametrize("marker", _INERT_IN_THE_PROTECTED_SET)
def test_the_four_kokila_markers_need_no_protection_to_keep_their_space(
    marker: str,
) -> None:
    """Documents the inert half of the protected set rather than implying it bites."""

    text = "कारोबारको " + marker

    assert normalize_devanagari_spacing(text, preserve_marker_spaces=True) == text
    assert normalize_devanagari_spacing(text) == text


def test_the_contextual_ne_marker_is_not_in_the_protected_set() -> None:
    """It is excluded deliberately: its space records where its base ended.

    It is absent from the protected set *and* from the deletion set, so its space survives
    either way — the same observable behaviour as the four inert markers above, reached for a
    different reason. Both are asserted so a refactor that merges the two sets fails here.
    """

    text = "कारोबारको " + kalimati_module._PUA_CONTEXTUAL_NE

    assert normalize_devanagari_spacing(text, preserve_marker_spaces=True) == text
    assert normalize_devanagari_spacing(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "छन् तथा",
        "सम्वत् २०७४",
        "एवम् अन्य",
        "गर्नुपर्ने छन् तथा भएका",
    ],
)
def test_a_post_virama_space_survives_in_either_pass(text: str) -> None:
    """🛑 The regression this file was written for.

    A blanket post-virama space deletion joins real word boundaries, so every case here
    keeps its space in both passes.

    ⚠️ ``सञ् चालन`` USED TO BE in this list, on the stated ground that "nothing local
    separates it from ``छन् तथा``, and a rule that gets one right gets the other wrong."
    That premise is false and the reconciliation is what showed it: what separates them is
    the consonant BEFORE the virama, not the virama. ``त् म् न्`` are word-final in Nepali
    (``सम्वत्``, ``एवम्``, ``छन्``); ``ञ्`` never is -- it occurs only as the first member of
    a conjunct (``सञ्चालन``, ``पञ्च``, ``अञ्चल``). So the ञ case is decidable locally and is
    now asserted joined, in
    :func:`test_a_post_nya_space_is_joined_because_nya_is_never_word_final`. Every case
    remaining in this list is untouched by that rule, which is why they still pass.
    """

    assert normalize_devanagari_spacing(text) == text
    assert normalize_devanagari_spacing(text, preserve_marker_spaces=True) == text


def test_a_post_nya_space_is_joined_because_nya_is_never_word_final() -> None:
    """``सञ् चालन`` joins, and that is a narrowing of the blanket rule, not a return to it.

    The discriminator is the consonant preceding the virama, so this bites exactly where a
    word-final consonant cannot occur. The four cases in the parametrized test above are the
    negative control: they share the post-virama space and are all preserved.
    """

    assert normalize_devanagari_spacing("सञ् चालन विविध") == "सञ्चालन विविध"
    assert (
        normalize_devanagari_spacing("सञ् चालन विविध", preserve_marker_spaces=True)
        == "सञ्चालन विविध"
    )


def test_a_real_combining_character_still_closes_the_gap() -> None:
    """The protection covers this module's markers, not every following character."""

    assert (
        normalize_devanagari_spacing(
            "क " + kalimati_module._IKAR, preserve_marker_spaces=True
        )
        == "क" + kalimati_module._IKAR
    )


def test_the_purya_stem_is_still_repaired_in_either_pass() -> None:
    """The one post-virama join that is lexically pinned rather than guessed."""

    assert normalize_devanagari_spacing("पुर् याउनु") == "पुर्याउनु"
    assert (
        normalize_devanagari_spacing("पुर् याउनु", preserve_marker_spaces=True) == "पुर्याउनु"
    )
