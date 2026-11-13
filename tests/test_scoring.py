import math

import pytest

from blueprints.scoring import (
    CHECK,
    VERIFIED,
    clopper_pearson_upper,
    normalize_unit,
    parse_number,
    score_answer,
    summarize,
)


# ---- Stoa's rule: counts exact -------------------------------------------------

@pytest.mark.parametrize("pred", [7, 7.0, "7", " 7 ", "7.0"])
def test_count_exact_match_is_correct(pred):
    assert score_answer(pred, 7, "count").status == "correct"


@pytest.mark.parametrize("pred", [6, 8, 7.4, 6.99, "seven", "7 windows", -7])
def test_count_off_by_anything_is_wrong(pred):
    s = score_answer(pred, 7, "count")
    assert s.status == "wrong" and s.correct is False


def test_count_zero():
    assert score_answer(0, 0, "count").status == "correct"
    assert score_answer(1, 0, "count").status == "wrong"


def test_count_gets_no_two_percent_slack():
    # 101 is within 2% of 100, but a count must be exact.
    assert score_answer(101, 100, "count").status == "wrong"


# ---- Stoa's rule: areas, lengths, volumes within 2% ----------------------------

@pytest.mark.parametrize("qtype", ["area", "length", "volume"])
def test_tolerance_edges(qtype):
    assert score_answer(102.0, 100.0, qtype).status == "correct"   # exactly +2%
    assert score_answer(98.0, 100.0, qtype).status == "correct"    # exactly -2%
    assert score_answer(102.01, 100.0, qtype).status == "wrong"
    assert score_answer(97.99, 100.0, qtype).status == "wrong"


def test_tolerance_is_relative_to_the_reference():
    assert score_answer(12.74, 12.5, "area").status == "correct"   # 1.92%
    assert score_answer(12.76, 12.5, "area").status == "wrong"     # 2.08%
    s = score_answer(12.76, 12.5, "area")
    assert math.isclose(s.rel_err, 0.0208, rel_tol=1e-3)


def test_tolerance_edge_survives_float_rounding():
    # 0.1 * 1.02 is 0.10200000000000001 in binary; still exactly +2%.
    assert score_answer(0.1 * 1.02, 0.1, "area").status == "correct"


def test_zero_reference_needs_zero():
    assert score_answer(0.0, 0.0, "area").status == "correct"
    assert score_answer(0.01, 0.0, "area").status == "wrong"


def test_sign_matters():
    assert score_answer(-50.0, 50.0, "area").status == "wrong"


# ---- strictness on top of the rule ---------------------------------------------

@pytest.mark.parametrize("pred", [None, "", "   "])
def test_abstention_is_never_correct_and_never_wrong(pred):
    s = score_answer(pred, 7, "count")
    assert s.status == "abstained" and s.correct is False


def test_wrong_unit_is_wrong_even_if_the_number_would_convert():
    # 1076.39 ft2 is 100 m2, but the question asked for m2.
    s = score_answer(1076.39, 100.0, "area", gold_unit="m²", pred_unit="ft²")
    assert s.status == "wrong" and "unit" in s.reason


def test_same_unit_spelled_differently_is_fine():
    assert score_answer(100.0, 100.0, "area", gold_unit="m²", pred_unit="m2").status == "correct"
    assert score_answer(100.0, 100.0, "area", gold_unit="m²", pred_unit="sqm").status == "correct"
    assert normalize_unit("M^2") == normalize_unit("m²")
    assert normalize_unit("m³") != normalize_unit("m²")


@pytest.mark.parametrize("bad", ["12,5", "1,234", "about 12", "12 m2", "12-13", True, float("nan"), float("inf")])
def test_ambiguous_numbers_do_not_parse(bad):
    assert parse_number(bad) is None


@pytest.mark.parametrize("good,expected", [("12.5", 12.5), ("-3", -3.0), ("1e3", 1000.0), (".5", 0.5), ("−2", -2.0)])
def test_plain_numbers_parse(good, expected):
    assert parse_number(good) == expected


def test_other_type_follows_its_unit():
    assert score_answer(101, 100, "other", gold_unit="count").status == "wrong"
    assert score_answer(101, 100, "other", gold_unit="m").status == "correct"


# ---- text is free-form: never auto-marked wrong ---------------------------------

def test_text_exact_match_ignores_case_and_spacing():
    assert score_answer("  autodesk REVIT 2011 ", "Autodesk Revit 2011", "text").status == "correct"


def test_text_mismatch_is_ungraded_with_soft_metrics():
    gold = "Kitchen: 33.2 m²\nSpace 4: 279.7 m²\nTotal: 312.9 m²"
    s = score_answer("Kitchen 33.2, Space 4 is 279.7, total 400", gold, "text")
    assert s.status == "ungraded" and s.correct is None
    assert 0 < s.token_f1 < 1
    # 33.2, 4 and 279.7 are found; 312.9 is not (the unit's "2" is not a number).
    assert s.number_recall == pytest.approx(3 / 4)


# ---- error bounds ----------------------------------------------------------------

def test_clopper_pearson_matches_rule_of_three():
    # 0 errors in n: exact bound is 1 - 0.05**(1/n), close to 3/n.
    for n in (30, 300, 1904):
        exact = 1 - 0.05 ** (1 / n)
        assert clopper_pearson_upper(0, n) == pytest.approx(exact, rel=1e-6)
        assert clopper_pearson_upper(0, n) == pytest.approx(3 / n, rel=0.06)


def test_clopper_pearson_known_value_and_edges():
    # Textbook value: 1 error in 100, one-sided 95% upper bound = 0.04656.
    assert clopper_pearson_upper(1, 100) == pytest.approx(0.04656, abs=1e-4)
    assert clopper_pearson_upper(5, 5) == 1.0
    assert clopper_pearson_upper(0, 0) is None
    assert clopper_pearson_upper(3, 100) > clopper_pearson_upper(2, 100)


# ---- aggregates ------------------------------------------------------------------

def _row(source, qtype, status, state=CHECK, cost=None, latency=None):
    return {"source": source, "type": qtype, "status": status, "state": state,
            "cost_eur": cost, "latency_ms": latency}


def test_summarize_counts_abstentions_against_accuracy():
    rows = [
        _row("a", "count", "correct", cost=0.01, latency=100),
        _row("a", "count", "wrong", cost=0.01, latency=300),
        _row("a", "count", "abstained", cost=0.0, latency=0),
        _row("a", "count", "abstained", cost=0.0, latency=0),
    ]
    o = summarize(rows)["overall"]
    assert o["n"] == 4 and o["answered"] == 2
    assert o["coverage"] == 0.5
    assert o["accuracy"] == 0.25              # 1 correct of 4 asked
    assert o["accuracy_when_answered"] == 0.5  # 1 correct of 2 answered
    assert o["eur_per_question"] == pytest.approx(0.005)  # spread over all 4


def test_summarize_leaves_ungraded_text_out_of_accuracy():
    rows = [_row("b", "text", "ungraded"), _row("b", "text", "correct"), _row("a", "count", "correct")]
    s = summarize(rows)
    assert s["by_family"]["b/text"]["accuracy"] == 1.0  # 1 of 1 gradable
    assert s["by_family"]["b/text"]["ungraded"] == 1
    assert s["overall"]["accuracy"] == 1.0


def test_summarize_tracks_errors_among_verified():
    rows = [_row("a", "count", "correct", state=VERIFIED) for _ in range(99)]
    rows.append(_row("a", "count", "wrong", state=VERIFIED))
    rows.append(_row("a", "count", "wrong", state=CHECK))
    o = summarize(rows)["overall"]
    assert o["verified"] == 100 and o["verified_wrong"] == 1
    assert o["verified_error_rate"] == 0.01
    assert o["verified_error_upper95"] == pytest.approx(0.04656, abs=1e-4)


def test_summarize_weighted_reflects_the_real_mix():
    rows = [_row("a", "count", "correct")] * 10 + [_row("b", "area", "wrong")] * 10
    # Sample is 50/50 but the real mix is 90/10.
    s = summarize(rows, weights={("a", "count"): 0.9, ("b", "area"): 0.1})
    assert s["overall"]["accuracy"] == 0.5
    assert s["weighted"]["accuracy"] == pytest.approx(0.9)
