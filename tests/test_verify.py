"""Verified thresholds: set from calibration answers, never handed out on thin evidence."""

import pytest

from blueprints import verify
from blueprints.answer import Answer
from blueprints.engines import router
from blueprints.scoring import CHECK, VERIFIED


def _rows(method, kind, score, n, wrong=0):
    return [{"method": method, "type": kind, "confidence": score,
             "status": "wrong" if i < wrong else "correct"} for i in range(n)]


def test_enough_clean_answers_earn_a_threshold():
    out = verify.calibrate(_rows("dxf", "count", 0.9, 400), alpha=0.01)
    fam = out["families"]["dxf/count"]
    assert fam["threshold"] == 0.9 and fam["errors_at_threshold"] == 0
    assert fam["upper_bound"] <= 0.01


def test_too_few_answers_earn_nothing_even_with_no_errors():
    out = verify.calibrate(_rows("ifc", "volume", 0.99, 30), alpha=0.01)
    assert out["families"]["ifc/volume"]["threshold"] is None   # 0 of 30 only proves "under 9.5%"


def test_errors_among_the_best_scored_block_the_family():
    out = verify.calibrate(_rows("ifc", "count", 0.99, 500, wrong=20), alpha=0.01)
    assert out["families"]["ifc/count"]["threshold"] is None


def test_threshold_stops_above_the_level_where_errors_start():
    rows = _rows("dxf", "area", 0.99, 600) + _rows("dxf", "area", 0.85, 600) + _rows("dxf", "area", 0.6, 60, wrong=30)
    fam = verify.calibrate(rows, alpha=0.01)["families"]["dxf/area"]
    assert fam["threshold"] == 0.85          # the 0.6 level would let the errors in
    assert fam["answers_at_threshold"] == 1200


def test_trying_several_levels_makes_each_bound_stricter():
    one = verify.calibrate(_rows("a", "count", 0.9, 320), alpha=0.01)["families"]["a/count"]
    rows = _rows("b", "count", 0.9, 320) + _rows("b", "count", 0.5, 5, wrong=5) + _rows("b", "count", 0.4, 5, wrong=5)
    three = verify.calibrate(rows, alpha=0.01)["families"]["b/count"]
    assert one["threshold"] == 0.9           # 0 of 320 at one level: bound 0.93%
    assert three["threshold"] is None        # the same data, three levels tried: bound 1.3%
    assert three["levels_tested"][0]["upper_bound"] > one["upper_bound"]


def test_families_are_calibrated_separately():
    out = verify.calibrate(_rows("dxf", "count", 0.9, 400) + _rows("ifc", "count", 0.95, 400, wrong=40), alpha=0.01)
    assert out["families"]["dxf/count"]["threshold"] == 0.9
    assert out["families"]["ifc/count"]["threshold"] is None


def _thresholds():
    return verify.calibrate(_rows("dxf-engine:v1", "count", 0.9, 400), alpha=0.01)


def test_an_answer_marked_never_verified_stays_out_of_the_bound_and_is_never_verified():
    """Right by the dataset, but not usable as it stands: no score can make it Verified."""
    box = [{"kind": "box", "bbox": [0, 0, 1, 1]}]
    flagged = Answer(answer=32, unit="count", confidence=0.99, method="dxf-engine:v1", evidence=box,
                     meta={"not_verifiable": "9 of the 32 outlines lie exactly on another: 23 distinct places"})
    assert verify.apply(flagged, _thresholds()).state == CHECK
    rows = _rows("dxf-engine:v1", "count", 0.9, 400) + [
        {"method": "dxf-engine:v1", "type": "count", "confidence": 0.9, "status": "correct", "meta": {"not_verifiable": "x"}}] * 300
    family = verify.calibrate(rows)["families"]["dxf-engine:v1/count"]
    assert family["calibration_answers"] == 400  # the 300 marked answers are not part of the measurement


def test_apply_marks_only_what_reaches_the_threshold():
    box = [{"kind": "box", "bbox": [0, 0, 1, 1]}]
    good = verify.apply(Answer(answer=7, unit="count", confidence=0.9, method="dxf-engine:v1", evidence=box), _thresholds())
    assert good.state == VERIFIED and good.meta["verified_because"]["calibration_errors"] == 0
    low = verify.apply(Answer(answer=7, unit="count", confidence=0.5, method="dxf-engine:v1", evidence=box), _thresholds())
    assert low.state == CHECK
    other_kind = verify.apply(Answer(answer=12.5, unit="m2", confidence=0.99, method="dxf-engine:v1", evidence=box), _thresholds())
    assert other_kind.state == CHECK         # areas have no threshold yet
    other_engine = verify.apply(Answer(answer=7, unit="count", confidence=0.99, method="ifc-engine:v1", evidence=box), _thresholds())
    assert other_engine.state == CHECK
    declined = verify.apply(Answer.decline("no DXF"), _thresholds())
    assert declined.answer is None


def test_a_verified_zero_still_carries_evidence():
    zero = verify.apply(Answer(answer=0, unit="count", confidence=0.9, method="dxf-engine:v1"), _thresholds())
    assert zero.state == VERIFIED and zero.evidence


def test_model_fallback_answers_are_never_verified(tiny, tmp_path):
    """Whatever the vision model's confidence, its answers stay 'check'."""
    import json

    q = next(q for q in tiny.questions if q.source == "cubicasa5k")
    log = tmp_path / "predictions.jsonl"
    log.write_text(json.dumps({"qid": q.qid, "pred": 3, "confidence": 1.0, "method": "vlm-image-only:x",
                               "explanation": "three", "cost_usd": 0.01, "latency_ms": 5.0}) + "\n")
    route = router.make(_thresholds(), fallback=router.replay(log))
    ans = route(q, tiny)
    assert ans.answer == 3 and ans.state == CHECK and ans.cost_usd == 0.01
    # Without a fallback the same question is declined, not guessed.
    assert router.make(_thresholds())(q, tiny).answer is None


def test_of_several_replayed_runs_the_first_that_holds_the_question_answers(tiny, tmp_path):
    import json

    scans = [q for q in tiny.questions if q.source == "cubicasa5k"][:2]
    marked, plain = tmp_path / "marked.jsonl", tmp_path / "plain.jsonl"
    marked.write_text(json.dumps({"qid": scans[0].qid, "pred": 5, "confidence": 0.9, "method": "vlm-pointing:x"}) + "\n")
    plain.write_text("".join(json.dumps({"qid": q.qid, "pred": 3, "confidence": 0.9, "method": "vlm-image-only:x"}) + "\n"
                             for q in scans))
    route = router.make(_thresholds(), fallback=router.replay(marked, plain))
    first, second = route(scans[0], tiny), route(scans[1], tiny)
    assert (first.answer, first.method) == (5, "vlm-pointing:x")        # held by the first run listed
    assert (second.answer, second.method) == (3, "vlm-image-only:x")    # only the second run has it


def test_router_picks_the_engine_by_what_the_drawing_comes_with(tiny):
    by_path = {q.path: q for q in tiny.questions}
    assert router.route(by_path["data/ifc_models/col-duplex_duplex-a.ifc"], tiny) == "ifc"
    assert router.route(by_path["data/ifc_models/col-duplex_duplex-a_plan-01-level-1.pdf"], tiny) == "ifc"
    assert router.route(by_path["data/swiss_dwellings/site0_bldg0_floor1.pdf"], tiny) == "dxf"
    assert router.route(next(q for q in tiny.questions if q.source == "cubicasa5k"), tiny) is None


# ---------------------------------------------------------------- label engine in the router


def _room_question(tiny):
    from dataclasses import replace

    q = next(q for q in tiny.questions if q.source == "cubicasa5k")
    return replace(q, question="How many bedrooms (makuuhuone, usually marked MH) are on this floor plan?")


def _labels(monkeypatch, answer, score):
    """Stand in for the OCR: the label engine returns this answer with this score."""
    from blueprints.answer import Answer
    from blueprints.engines import raster_labels

    def fake(q, dataset):
        if answer is None:
            return Answer.decline("no readable labels", method=raster_labels.METHOD, cost_usd=0.0)
        return Answer(answer=answer, unit="count", confidence=score, method=raster_labels.METHOD, cost_usd=0.0,
                      evidence=[{"kind": "box", "space": "image_px", "bbox": [1, 2, 3, 4], "label": "MH"}])

    monkeypatch.setattr(raster_labels, "answer", fake)


def _model(answer):
    from blueprints.answer import Answer

    calls = []

    def fallback(q, dataset):
        calls.append(q.qid)
        if answer is None:
            return Answer.decline("could not tell", method="vlm-pointing:x", cost_usd=0.004)
        return Answer(answer=answer, unit="count", confidence=0.7, method="vlm-pointing:x", cost_usd=0.004)

    return fallback, calls


def test_scans_route_to_the_label_engine_only_for_labelled_rooms(tiny):
    from dataclasses import replace

    q = _room_question(tiny)
    assert router.route(q, tiny) == "labels"
    assert router.route(replace(q, question="How many windows are shown on this floor plan?"), tiny) is None
    assert router.route(replace(q, question="Kuinka monta saunaa pohjapiirustuksessa on?"), tiny) == "labels"


def test_agreeing_labels_stand_and_cost_nothing(tiny, monkeypatch):
    _labels(monkeypatch, 3, 0.9)
    fallback, calls = _model(2)
    ans = router.make(_thresholds(), fallback=fallback)(_room_question(tiny), tiny)
    assert ans.answer == 3 and ans.cost_usd == 0.0 and ans.state == CHECK
    assert calls == []  # the paid model is not asked


def test_weak_labels_give_way_to_the_model_and_are_kept_as_a_second_opinion(tiny, monkeypatch):
    _labels(monkeypatch, 3, 0.6)
    fallback, calls = _model(2)
    ans = router.make(_thresholds(), fallback=fallback)(_room_question(tiny), tiny)
    assert ans.answer == 2 and ans.method == "vlm-pointing:x" and len(calls) == 1
    assert ans.meta["labels_said"] == 3 and ans.meta["labels_score"] == 0.6


def test_weak_labels_still_answer_when_there_is_no_model_or_it_declines(tiny, monkeypatch):
    _labels(monkeypatch, 3, 0.6)
    assert router.make(_thresholds())(_room_question(tiny), tiny).answer == 3
    fallback, _ = _model(None)
    ans = router.make(_thresholds(), fallback=fallback)(_room_question(tiny), tiny)
    assert ans.answer == 3 and ans.state == CHECK


def test_unreadable_labels_fall_through_to_the_model(tiny, monkeypatch):
    _labels(monkeypatch, None, None)
    fallback, _ = _model(1)
    assert router.make(_thresholds(), fallback=fallback)(_room_question(tiny), tiny).answer == 1
    assert router.make(_thresholds())(_room_question(tiny), tiny).answer is None


# ---------------------------------------------------------------- two readings of one scan


def _agreed_thresholds(threshold):
    family = {"threshold": threshold, "answers_at_threshold": 700, "errors_at_threshold": 1, "upper_bound": 0.0068}
    return {"alpha": 0.01, "families": {"labels+model:v1/count": family}}


def test_labels_and_model_with_the_same_count_can_be_verified(tiny, monkeypatch):
    """Verified only when calibration allowed the family; the model's marks are shown, not counted."""
    from blueprints.answer import Answer

    _labels(monkeypatch, 3, 0.9)

    def model(q, dataset):
        return Answer(answer=3, unit="count", confidence=0.6, method="vlm-pointing:x:boxes", cost_usd=0.004,
                      evidence=[{"kind": "box", "space": "image_px", "bbox": [10, 10, 20, 20], "label": "bedroom"}] * 3)

    q = _room_question(tiny)
    ans = router.make(_agreed_thresholds(0.95), confirm=model)(q, tiny)
    assert ans.answer == 3 and ans.state == VERIFIED and ans.method == router.AGREED and ans.cost_usd == 0.004
    assert ans.meta["verified_because"]["calibration_errors"] == 1
    counted = [e for e in ans.evidence if not e.get("role")]
    assert len(counted) == 1 and counted[0]["label"] == "MH"                  # the label read by OCR
    assert sum(e.get("role") == "marked by the vision model" for e in ans.evidence) == 3
    # No threshold for the family (calibration did not allow it): the same answer, for checking.
    unproven = router.make(_agreed_thresholds(None), confirm=model)(q, tiny)
    assert unproven.answer == 3 and unproven.state == CHECK and unproven.method == router.AGREED


def test_the_strictest_label_answers_stand_alone_and_free(tiny, monkeypatch):
    """At 0.99 the labels were right 97% of the time on calibration drawings: no second reading is paid for."""
    _labels(monkeypatch, 3, 0.99)
    model, calls = _model(2)
    ans = router.make(_agreed_thresholds(0.95), fallback=model, confirm=model)(_room_question(tiny), tiny)
    assert ans.answer == 3 and ans.method == "raster-labels:v1" and ans.cost_usd == 0.0 and calls == []


def test_readings_that_differ_are_never_verified(tiny, monkeypatch):
    _labels(monkeypatch, 3, 0.9)
    model, calls = _model(2)
    ans = router.make(_agreed_thresholds(0.95), confirm=model)(_room_question(tiny), tiny)
    # The model's count is the one shown (it was right far more often on calibration drawings), flagged as differing.
    assert ans.state == CHECK and ans.answer == 2 and ans.confidence == router.DIFFER_SCORE and len(calls) == 1
    assert ans.meta["model_said"] == 2 and ans.meta["labels_said"] == 3 and "differ" in ans.explanation
    assert any(e.get("role") == "label read by OCR" for e in ans.evidence)  # the labels are drawn, not counted
    # The model gives nothing: the labels' answer stands as it was, the call is still paid for.
    silent, _ = _model(None)
    alone = router.make(_agreed_thresholds(0.95), confirm=silent)(_room_question(tiny), tiny)
    assert alone.answer == 3 and alone.state == CHECK and alone.confidence == 0.9 and alone.cost_usd == 0.004


def test_weak_labels_are_not_confirmed_they_give_way(tiny, monkeypatch):
    _labels(monkeypatch, 3, 0.6)
    model, calls = _model(3)
    ans = router.make(_agreed_thresholds(0.95), fallback=model, confirm=model)(_room_question(tiny), tiny)
    assert ans.method == "vlm-pointing:x" and ans.state == CHECK and len(calls) == 1
