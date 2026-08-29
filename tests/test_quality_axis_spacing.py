"""The `spacing` check must read text spacing, not table markup."""

from likhit.quality.axes import check_spacing


def _tokens(count: int, word: str = "कार्यालय") -> str:
    return " ".join([word] * count)


def test_per_character_spacing_is_still_garbled():
    # The damage this check exists for: "म ह ा ल े ख ा".
    verdict, evidence = check_spacing(" ".join("महालेखापरीक्षकको" * 20))

    assert verdict == "garbled"
    assert evidence["single_char_share"] > 0.55


def test_a_wide_table_of_blank_cells_is_not_spacing_damage():
    # Explicit blank cells render as bare `|`. Without excluding pipes this
    # scored 0.551 and came back `garbled` on real transcripts.
    row = "| " * 40 + "|"
    text = _tokens(120) + "\n" + "\n".join([row] * 60)

    verdict, evidence = check_spacing(text)

    assert verdict == "clean", evidence


def test_pipes_do_not_count_toward_the_single_char_share():
    prose = _tokens(200)
    piped = prose + "\n" + "| " * 500

    assert (
        check_spacing(prose)[1]["single_char_share"]
        == (check_spacing(piped)[1]["single_char_share"])
    )


def _fenced(body: str, filler: int = 200) -> str:
    return _tokens(filler) + "\n```text\n" + body + "\n```\n"


def test_a_fenced_table_of_blank_cells_is_not_spacing_damage():
    # This is what excluding fences was FOR: likhit wraps tables in ```text
    # fences, and a wide table of blank cells is all single-char `|` tokens.
    # Stripping the pipes is what makes it clean -- deleting the fenced content
    # is not needed, and cost the check the page. See `strip_fences`.
    row = "| " * 40 + "|"
    text = _fenced("\n".join([row] * 60))

    verdict, evidence = check_spacing(text)

    assert verdict == "clean", evidence


def test_per_character_garble_inside_a_fence_is_still_scored():
    # REVERSED 2026-08-12, deliberately: this asserted `clean`, because
    # `FENCE_RE.sub` deleted each fence's contents along with its delimiters.
    # likhit fences whole *pages*, so that discarded the page -- a median
    # document was scored on 14.07% of itself. Fenced text is document text.
    verdict, evidence = check_spacing(_fenced(" ".join("क" * 400)))

    assert verdict == "garbled", evidence


def test_the_verdict_does_not_depend_on_where_the_sample_cuts():
    # `check_spacing` used to sample before stripping, which is the only reason
    # it escaped the fence-deletion bug: a sample that cut mid-fence left an
    # unbalanced ``` that `FENCE_RE` could not match, so the garble survived --
    # but only for documents long enough to be sampled. Stripping first makes
    # the two agree.
    small = _fenced(" ".join("क" * 400))
    large = _fenced(" ".join("क" * 400_000))

    assert len(small) < 600_000 < len(large)
    assert check_spacing(small)[0] == check_spacing(large)[0] == "garbled"


def test_genuine_single_character_words_stay_below_the_threshold():
    # `र` ("and") is a legitimate one-character Nepali word and appears often.
    text = " ".join(["कार्यालय", "र"] * 150)

    verdict, evidence = check_spacing(text)

    assert evidence["single_char_share"] == 0.5
    # Half the tokens being a real one-letter word is suspect, not garbled.
    assert verdict == "suspect"


def test_short_documents_are_not_scored():
    assert check_spacing("कार्यालय " * 10) == ("clean", {})


def test_very_long_tokens_still_flag_as_suspect():
    text = _tokens(200) + " " + "क" * 60

    assert check_spacing(text)[1]["very_long_tokens"] == 1
