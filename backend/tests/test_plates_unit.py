import pytest

from app.services.plates import REVIEW_CONFIDENCE, clean_plate, normalize, strict


@pytest.mark.parametrize("raw", ["GJ14AW1013", "gj 14-aw 1013", " GJ14 AW 1013\n", "MH12AB1234", "DL1CAB1234", "KA01A1234", "TN09C1234", "RJ14G7712"])
def test_valid_plates_pass_untouched(raw):
    r = clean_plate(raw)
    assert r.valid and r.corrections == 0 and r.text == normalize(raw)


@pytest.mark.parametrize("raw,fixed", [
    ("GJ14AW1O13", "GJ14AW1013"),          # letter O read for digit 0
    ("GJ14AW10I3", "GJ14AW1013"),          # I for 1
    ("GJ14AWI013", "GJ14AW1013"),          # I for 1 at the start of the number
    ("6J14AW1013", "GJ14AW1013"),          # 6 for G in the state code
    ("GJB4AW1013", "GJ84AW1013"),          # B for 8 in the district digits
    ("MH12A81234", "MH12AB1234"),          # 8 for B in the series
])
def test_one_look_alike_mistake_is_fixed(raw, fixed):
    r = clean_plate(raw)
    assert r.valid and r.text == fixed and r.corrections == 1
    assert not r.needs_review(95)           # one fix with good confidence is trusted


def test_two_or_more_corrections_go_to_a_person():
    r = clean_plate("GJ14AWIO13")
    assert r.valid and r.corrections == 2 and r.needs_review(99)


@pytest.mark.parametrize("raw", ["", "????", "GJ1013", "GJ14ABCD1013", "XX14AW1013", "GJ14AW10", "GJ14AW10134", "1234567890", "GJ-AW-1013", "GJ14AW1013EXTRA",
                                 "MH120B1234"])      # a 0 would have to become O in the series, and series skip I and O: send to a person
def test_impossible_reads_are_invalid(raw):
    r = clean_plate(raw)
    assert not r.valid and r.needs_review(99)


def test_bharat_series_is_valid():
    r = clean_plate("21 BH 1234 AA")
    assert r.valid and r.text == "21BH1234AA" and r.corrections == 0


def test_low_confidence_always_needs_review_even_for_a_perfect_read():
    r = clean_plate("GJ14AW1013")
    assert r.needs_review(REVIEW_CONFIDENCE - 1) and not r.needs_review(REVIEW_CONFIDENCE)


def test_strict_accepts_only_exact_valid_plates():
    assert strict("gj14aw1013") == "GJ14AW1013"
    assert strict("GJ14AW1O13") is None                     # a human must type the right plate, no guessing
    assert strict("nonsense") is None and strict("") is None


def test_a_fix_never_invents_the_letters_i_or_o_in_the_series():
    assert not clean_plate("GJ14AW10134").valid       # extra digit: was being "fixed" into GJ14AWI0134
    assert not clean_plate("MH12101234").valid        # 1 would have to become I in the series
    assert clean_plate("MH12AB1234").valid


def test_a_valid_shape_is_not_second_guessed():
    r = clean_plate("GJ1BAW1013")                      # district 1, series BAW: a legal plate
    assert r.valid and r.corrections == 0 and r.text == "GJ1BAW1013"
