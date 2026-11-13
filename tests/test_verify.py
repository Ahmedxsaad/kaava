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


def test_router_picks_the_engine_by_what_the_drawing_comes_with(tiny):
    by_path = {q.path: q for q in tiny.questions}
    assert router.route(by_path["data/ifc_models/col-duplex_duplex-a.ifc"], tiny) == "ifc"
    assert router.route(by_path["data/ifc_models/col-duplex_duplex-a_plan-01-level-1.pdf"], tiny) == "ifc"
    assert router.route(by_path["data/swiss_dwellings/site0_bldg0_floor1.pdf"], tiny) == "dxf"
    assert router.route(next(q for q in tiny.questions if q.source == "cubicasa5k"), tiny) is None
