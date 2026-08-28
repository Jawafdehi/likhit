from likhit.quality.axes import check_matra_damage


def test_one_malformed_conjunct_ra_cannot_be_diluted_by_document_length():
    verdict, evidence = check_matra_damage(("क" * 100_000) + "कर्ि")

    assert verdict == "suspect"
    assert evidence["malformed_conjunct_ra"] == 1


def test_a_short_document_with_the_shape_is_not_exempt():
    verdict, evidence = check_matra_damage("कर्ि")

    assert verdict == "suspect"
    assert evidence["malformed_conjunct_ra"] == 1


def test_conjunct_ra_screen_reads_beyond_the_damage_sample():
    text = ("क" * 300_000) + "कर्ि" + ("क" * 600_000)

    verdict, evidence = check_matra_damage(text)

    assert verdict == "suspect"
    assert evidence["malformed_conjunct_ra"] == 1


def test_clean_long_text_stays_clean():
    verdict, evidence = check_matra_damage("क" * 100_000)

    assert verdict == "clean"
    assert evidence["malformed_conjunct_ra"] == 0
