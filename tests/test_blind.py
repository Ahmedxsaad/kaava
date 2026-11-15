"""The blind-trial rig (blind.py): the key is locked first, marks are paired with evidence, checks are timed.

Nothing here calls a model: the system and the model alone are stand-ins that return fixed answers.
"""

from types import SimpleNamespace

import pytest

from blueprints import blind

BOX = lambda n, x0, y0, x1, y1: {"kind": "box", "n": n, "bbox": [x0, y0, x1, y1]}  # noqa: E731
GOOD = {"by": "AB", "kind": "count", "quantity": 2, "marks": [[10, 10], [50, 50]], "unseen": True}


def key(**changes):
    return blind.clean_key({**GOOD, **changes}, 1000, 800)


def kaava(answer, evidence, unit="count"):
    return blind.kaava_part({"answer": answer, "unit": unit, "state": "check" if answer is not None else "declined",
                             "evidence": evidence, "cost_eur": 0.02, "latency_ms": 9000.0}, key(quantity=2))


# ---------------------------------------------------------------- marks against evidence


def test_marks_pair_with_the_items_they_lie_in():
    evidence = [BOX(1, 0, 0, 20, 20), BOX(2, 40, 40, 60, 60), BOX(3, 500, 500, 520, 520)]
    out = blind.match_marks([[10, 10], [50, 50], [900, 100]], evidence, long_edge=1000)
    assert out["matched"] == [[0, 1], [1, 2]]
    assert out["missed"] == [2]  # a mark with no item: the system missed it
    assert out["extra"] == [3]  # an item with no mark: the system added it


def test_a_mark_just_outside_an_item_still_belongs_to_it_and_one_far_off_does_not():
    evidence = [BOX(1, 100, 100, 120, 120)]
    near = blind.match_marks([[130, 110]], evidence, long_edge=1000)  # 10 px out, tolerance 15 px
    far = blind.match_marks([[140, 110]], evidence, long_edge=1000)  # 20 px out
    assert near["tolerance_px"] == 15.0 and near["matched"] == [[0, 1]] and not near["missed"]
    assert far["missed"] == [0] and far["extra"] == [1]


def test_a_mark_beside_a_small_item_reaches_it_by_half_its_size_at_most():
    # On a sheet 6,000 px wide the share of the long edge alone would be 90 px: wider than a door is drawn.
    door = [BOX(1, 1000, 1000, 1080, 1060)]  # 80 px wide: a mark may lie 40 px beside it, not 60
    assert blind.match_marks([[1118, 1030]], door, long_edge=6000)["matched"] == [[0, 1]]
    assert blind.match_marks([[1142, 1030]], door, long_edge=6000)["missed"] == [0]
    assert blind.match_marks([[1142, 1030]], door, long_edge=6000)["tolerance_px"] == 90.0


def test_a_crowded_corner_is_paired_as_well_as_it_can_be():
    # Both marks reach item 1; only the second also reaches item 2. Nearest-first alone would give
    # item 1 to the second mark and leave the first without one.
    evidence = [BOX(1, 0, 0, 40, 40), BOX(2, 30, 0, 70, 40)]
    out = blind.match_marks([[5, 20], [33, 20]], evidence, long_edge=100)
    assert sorted(out["matched"]) == [[0, 1], [1, 2]] and not out["missed"] and not out["extra"]


def test_an_item_takes_one_mark_only():
    out = blind.match_marks([[10, 10], [12, 12]], [BOX(1, 0, 0, 20, 20)], long_edge=1000)
    assert len(out["matched"]) == 1 and len(out["missed"]) == 1 and not out["extra"]


def test_outlines_and_points_have_places_too():
    evidence = [{"kind": "polygon", "n": 1, "points": [[0, 0], [100, 0], [100, 100], [0, 100]]},
                {"kind": "point", "n": 2, "x": 300.0, "y": 300.0},
                {"kind": "element", "n": 3, "id": "x", "x": 600.0, "y": 600.0}]
    out = blind.match_marks([[50, 50], [305, 300], [600, 610]], evidence, long_edge=1000)
    assert out["matched"] == [[0, 1], [1, 2], [2, 3]]


def test_what_is_not_a_counted_item_has_no_place():
    evidence = [{"kind": "formula", "text": "1 + 1"}, {"kind": "context", "role": "not counted", "bbox": [0, 0, 5, 5]},
                BOX(1, 0, 0, 20, 20), {**BOX(2, 0, 0, 20, 20), "same_as": 1},  # the same outline twice in the file
                {"kind": "element", "n": 3, "id": "on another storey"}]
    items, unplaced = blind.placed(evidence)
    assert [item["n"] for item in items] == [1] and unplaced == 1


# ---------------------------------------------------------------- the key, and the order


def test_a_key_is_cleaned_and_stamped():
    made = key(by="  Anna   B ", how=" counted  each leaf ")
    assert made["by"] == "Anna B" and made["quantity"] == 2 and made["unit"] == "count" and made["how"] == "counted each leaf"
    assert made["marks"] == [[10.0, 10.0], [50.0, 50.0]] and made["locked_at"].endswith("Z") and made["rig_test"] is False
    assert key(kind="area", quantity=245.04, marks=[])["unit"] == "m2"


@pytest.mark.parametrize("changes, why", [
    ({"by": " "}, "who made"),
    ({"unseen": False}, "not seen"),
    ({"kind": "guess"}, "one of"),
    ({"quantity": 2.5}, "whole number"),
    ({"quantity": -1}, "not negative"),
    ({"quantity": None}, "quantity"),
    ({"quantity": True}, "quantity"),
    ({"marks": [[10, 10], [1200, 5]]}, "outside the sheet"),
    ({"marks": [["a", 1]]}, "pair of numbers"),
])
def test_a_key_that_cannot_be_used_is_turned_down(changes, why):
    with pytest.raises(blind.Refused) as refusal:
        key(**changes)
    assert refusal.value.status == 422 and why in refusal.value.why


def test_the_key_comes_first_and_is_locked_once():
    record = blind.lock_key({"id": "t"}, key())
    with pytest.raises(blind.Refused) as again:
        blind.lock_key(record, key(quantity=3, marks=[]))
    assert again.value.status == 409 and "locked" in again.value.why
    with pytest.raises(blind.Refused) as late:  # the system has answered: a key made now would not be blind
        blind.lock_key({"id": "t", "run": {"kaava": {}}}, key())
    assert late.value.status == 409 and "blind" in late.value.why


# ---------------------------------------------------------------- the comparison


def test_a_count_is_compared_mark_by_mark():
    said = kaava(3, [BOX(1, 0, 0, 20, 20), BOX(2, 40, 40, 60, 60), BOX(3, 700, 700, 720, 720)])
    out = blind.compare(key(), said, {"width": 1000, "height": 800})
    assert said["status"] == "wrong" and out["difference"] == 1
    assert out["marks"]["comparable"] and out["marks"]["extra"] == [3] and out["marks"]["missed"] == []


def test_marks_are_not_compared_when_that_would_mislead():
    image = {"width": 1000, "height": 800}
    boxes = [BOX(1, 0, 0, 20, 20), BOX(2, 40, 40, 60, 60)]
    partial = blind.compare(key(quantity=5), kaava(2, boxes), image)  # two marks for a count of five
    assert not partial["marks"]["comparable"] and "not the whole key" in partial["marks"]["why"]
    computed = blind.compare(key(), kaava(40, boxes), image)  # two figures boxed, the answer worked out from them
    assert not computed["marks"]["comparable"] and "computed from what is printed" in computed["marks"]["why"]
    declined = blind.compare(key(), kaava(None, []), image)
    assert not declined["marks"]["comparable"] and "no number" in declined["marks"]["why"]
    unmarked = blind.compare(key(marks=[]), kaava(2, boxes), image)
    assert not unmarked["marks"]["comparable"] and "without marking" in unmarked["marks"]["why"]


def test_a_measure_is_scored_by_the_two_percent_rule():
    area = key(kind="area", quantity=245.0, marks=[])
    close = blind.kaava_part({"answer": 244.14, "unit": "m2", "state": "check", "evidence": []}, area)
    off = blind.kaava_part({"answer": 238.0, "unit": "m2", "state": "check", "evidence": []}, area)
    assert close["status"] == "correct" and off["status"] == "wrong"
    out = blind.compare(area, close, {"width": 10, "height": 10})
    assert out["within_2_percent"] and out["difference"] == pytest.approx(-0.86) and "marks" not in out
    assert not blind.compare(area, off, {"width": 10, "height": 10})["within_2_percent"]


def test_a_refusal_is_recorded_as_one_and_is_not_wrong():
    said = kaava(None, [])
    assert said["refused"] and said["status"] == "declined"
    alone = blind.baseline_part(None, None, key(), model="m", cost_eur=0.01, latency_ms=5.0, reason="cannot tell")
    assert alone["refused"] and alone["status"] == "declined"


# ---------------------------------------------------------------- the checks


def trial_with(answer=2):
    return {"id": "t", "key": key(), "run": {"kaava": kaava(answer, [BOX(1, 0, 0, 20, 20), BOX(2, 40, 40, 60, 60)])},
            "verifications": []}


@pytest.mark.parametrize("seconds", [0.5, 0, -3, 3601, None, "12", True, float("nan")])
def test_a_check_must_have_taken_between_a_second_and_an_hour(seconds):
    with pytest.raises(blind.Refused) as refusal:
        blind.clean_verification({"by": "V1", "verdict": "right", "seconds": seconds}, trial_with())
    assert refusal.value.status == 422


def test_a_check_says_whether_it_agrees_with_the_key():
    right, wrong = trial_with(2), trial_with(3)
    check = lambda record, verdict: blind.clean_verification({"by": "V", "verdict": verdict, "seconds": 41.26}, record, 43.0)  # noqa: E731
    assert check(right, "right")["agrees_with_key"] is True and check(right, "wrong")["agrees_with_key"] is False
    assert check(wrong, "wrong")["agrees_with_key"] is True and check(wrong, "right")["agrees_with_key"] is False
    assert check(right, "cannot_tell")["agrees_with_key"] is None
    assert check(right, "right")["seconds"] == 41.3 and check(right, "right")["server_seconds"] == 43.0


def test_there_is_nothing_to_check_before_the_run_or_after_a_refusal():
    for record in ({"id": "t", "key": key()}, trial_with(None)):
        with pytest.raises(blind.Refused) as refusal:
            blind.clean_verification({"by": "V", "verdict": "right", "seconds": 10}, record)
        assert refusal.value.status == 409


def test_the_key_stays_hidden_until_the_system_has_answered():
    before = blind.public({"id": "t", "key": key()})
    assert "quantity" not in before["key"] and "marks" not in before["key"] and before["summary"]["stage"] == "key locked"
    after = blind.public(trial_with())
    assert after["key"]["quantity"] == 2 and after["summary"]["stage"] == "run"
    seen = blind.public({**trial_with(), "title": "A sheet", "question": "How many?"}, "verify")
    assert "key" not in seen and "run" not in seen and seen["kaava"]["answer"] == 2 and "status" not in seen["kaava"]


# ---------------------------------------------------------------- the API, with stand-ins for both answers


class Stub:
    """A service that knows one drawing and answers it with two boxed items."""

    budget_eur, spent_eur = 1.0, 0.0

    def __init__(self):
        self.asked = 0

    def allowed(self, drawing):
        return drawing == "uploads/abc"

    def view(self, drawing):
        return SimpleNamespace(kind="image", width=1000, height=800)

    def summary(self, drawing):
        return {"title": "A fresh sheet"}

    def ask(self, request):
        self.asked += 1
        assert request.auto and request.question == "How many doors?"
        return {"question": request.question, "answer": 2, "unit": "count", "state": "check", "engine": "stand-in",
                "method": "stand-in", "cost_eur": 0.021, "spent_now_eur": 0.021, "replayed": False, "replayed_from": None,
                "latency_ms": 8200.0, "evidence": [BOX(1, 0, 0, 20, 20), BOX(2, 40, 40, 60, 60)], "baseline": None}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    alone = {"calls": 0}

    def ask_alone(service, drawing, question, key, out):
        alone["calls"] += 1
        return blind.baseline_part(3, "count", key, model="stand-in", cost_eur=0.008, latency_ms=6100.0)

    monkeypatch.setattr(blind, "STORE", tmp_path / "blind")
    monkeypatch.setattr(blind, "ask_alone", ask_alone)
    service, app = Stub(), FastAPI()
    blind.mount(app, service)
    return TestClient(app), service, alone


def lock(client, **changes):
    body = {"drawing": "uploads/abc", "question": "How many  doors?", **GOOD, **changes}
    return client.post("/api/blind/key", json=body)


def test_a_trial_from_key_to_checks(rig):
    client, service, alone = rig
    made = lock(client)
    assert made.status_code == 200, made.text
    trial = made.json()["id"]
    assert made.json()["question"] == "How many doors?" and "quantity" not in made.json()["key"]
    assert "quantity" not in client.get(f"/api/blind/{trial}").json()["key"]  # still hidden: nothing has run
    assert client.post(f"/api/blind/{trial}/verify", json={"by": "V", "verdict": "right", "seconds": 9}).status_code == 409

    ran = client.post(f"/api/blind/{trial}/run")
    assert ran.status_code == 200, ran.text
    run = ran.json()["run"]
    assert run["kaava"]["status"] == "correct" and run["kaava"]["cost_eur"] == 0.021 and run["kaava"]["latency_ms"] == 8200.0
    assert run["baseline"]["status"] == "wrong" and run["baseline"]["answer"] == 3
    assert run["comparison"]["marks"] == {"comparable": True, "tolerance_px": 15.0, "unplaced": 0,
                                          "matched": [[0, 1], [1, 2]], "missed": [], "extra": []}
    assert ran.json()["key"]["quantity"] == 2  # shown now that the system has answered

    again = client.post(f"/api/blind/{trial}/run")  # asking again returns the record: nothing is asked twice
    assert again.status_code == 200 and again.json()["run"] == run and service.asked == 1 and alone["calls"] == 1

    seen = client.get(f"/api/blind/{trial}?view=verify").json()
    assert "key" not in seen and "run" not in seen and seen["kaava"]["answer"] == 2
    token = client.post(f"/api/blind/{trial}/verify/start").json()["token"]
    for by, verdict, seconds in (("V1", "right", 30), ("V2", "right", 50), ("V3", "wrong", 41.5)):
        sent = client.post(f"/api/blind/{trial}/verify", json={"by": by, "verdict": verdict, "seconds": seconds, "token": token})
        assert sent.status_code == 200, sent.text
    record = client.get(f"/api/blind/{trial}").json()
    assert [c["agrees_with_key"] for c in record["verifications"]] == [True, True, False]
    assert record["verifications"][0]["server_seconds"] is not None and record["verifications"][1]["server_seconds"] is None
    assert record["summary"] == {"stage": "verified", "kaava_status": "correct", "baseline_status": "wrong", "missed": 0,
                                 "extra": 0, "verifiers": 3, "median_seconds": 41.5, "agree_with_key": 2,
                                 "disagree_with_key": 1, "could_not_tell": 0}
    assert client.post(f"/api/blind/{trial}/verify", json={"by": "V4", "verdict": "right", "seconds": 0.2}).status_code == 422
    assert [t["id"] for t in blind.trials()] == [trial]


def test_what_the_rig_turns_down(rig):
    client, _, _ = rig
    assert lock(client, drawing="uploads/other").status_code == 404
    assert lock(client, question=" ").status_code == 422
    assert lock(client, unseen=False).status_code == 422
    for bad in ("nope", "../../etc/passwd", "a" * 40):
        assert client.get(f"/api/blind/{bad}").status_code == 404
    assert client.post("/api/blind/abcdefghij/run").status_code == 404
    assert client.get("/blind").status_code == 200 and "Blind trial" in client.get("/blind").text


def test_a_failed_call_to_the_model_alone_is_tried_again_and_the_system_is_not(rig, monkeypatch):
    client, service, _ = rig
    trial = lock(client).json()["id"]
    calls = {"n": 0}

    def flaky(service, drawing, question, key, out):
        calls["n"] += 1
        if calls["n"] == 1:
            raise blind.Refused(503, "the model alone could not be asked: timeout")
        return blind.baseline_part(2, "count", key, model="stand-in", cost_eur=0.008, latency_ms=6000.0)

    monkeypatch.setattr(blind, "ask_alone", flaky)
    first = client.post(f"/api/blind/{trial}/run")
    assert first.status_code == 503 and "timeout" in first.text
    half = client.get(f"/api/blind/{trial}").json()
    assert half["run"]["kaava"]["answer"] == 2 and "baseline" not in half["run"]
    second = client.post(f"/api/blind/{trial}/run")
    assert second.status_code == 200 and second.json()["run"]["baseline"]["status"] == "correct"
    assert service.asked == 1 and calls["n"] == 2
