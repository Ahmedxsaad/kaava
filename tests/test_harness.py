import csv
import io
import json

import pytest
from PIL import Image

from blueprints import baseline, config
from blueprints.answer import Answer
from blueprints.harness import FatalEngineError, finalize_run, make_row, new_run_id, run_eval
from blueprints.render import can_render, fit_size, render, visual_tokens
from blueprints.scoring import CHECK, DECLINED, VERIFIED
from blueprints.split import make_split
from blueprints.vlm import MockVLM, VLMReply


def _run(tiny, tmp_path, engine, questions=None, **kwargs):
    split, _ = make_split(tiny, tmp_path / "split.json")
    questions = questions if questions is not None else tiny.questions
    return run_eval(method="test", answer_fn=engine, dataset=tiny, questions=questions,
                    split_of=split.of, run_dir=tmp_path / "run", progress=lambda _: None, **kwargs)


# ---- the answer structure ---------------------------------------------------------

def test_answer_states_are_enforced():
    with pytest.raises(ValueError):
        Answer(answer=None, state=CHECK)                 # no answer must be declined
    with pytest.raises(ValueError):
        Answer(answer=7, state=DECLINED)                 # declined carries no value
    with pytest.raises(ValueError):
        Answer(answer=7, state=VERIFIED)                 # verified needs evidence
    ok = Answer(answer=7, state=VERIFIED, evidence=[{"kind": "box", "bbox": [0, 0, 1, 1]}])
    assert ok.state == VERIFIED
    assert Answer.decline("unreadable scan").state == DECLINED


# ---- rendering ----------------------------------------------------------------------

@pytest.mark.parametrize("size", [(240, 180), (876, 600), (4500, 2667), (3024, 2160), (8000, 300)])
def test_fit_size_respects_both_model_limits_and_never_upscales(size):
    w, h = fit_size(*size, max_edge=2576, max_tokens=4784)
    assert max(w, h) <= 2576 and visual_tokens(w, h) <= 4784
    assert w <= size[0] and h <= size[1]
    assert w / h == pytest.approx(size[0] / size[1], rel=0.02)


def test_small_png_is_sent_untouched_and_big_one_is_downscaled(tmp_path):
    small, big = tmp_path / "s.png", tmp_path / "b.png"
    Image.new("RGB", (300, 200), "white").save(small)
    Image.new("RGBA", (5000, 3000), (0, 0, 0, 0)).save(big)
    a = render(small, max_edge=2576, max_tokens=4784)[0]
    assert a.data == small.read_bytes() and (a.width, a.height) == (300, 200)
    b = render(big, max_edge=2576, max_tokens=4784)[0]
    assert max(b.width, b.height) <= 2576 and b.visual_tokens <= 4784
    with Image.open(io.BytesIO(b.data)) as im:
        assert im.size == (b.width, b.height)
        assert im.convert("RGB").getpixel((0, 0)) == (255, 255, 255)  # transparency became paper


def test_pdf_page_is_rendered_at_the_model_limit(tiny):
    page = render(tiny.abspath("data/swiss_dwellings/site0_bldg0_floor1.pdf"),
                  max_edge=2576, max_tokens=4784)[0]
    assert page.media_type == "image/png"
    assert 0.9 * 4784 <= page.visual_tokens <= 4784   # uses the resolution it is allowed
    assert not can_render("model.ifc") and not can_render("plan.dxf") and can_render("plan.PDF")


# ---- baseline engine with the offline model -----------------------------------------

def test_baseline_answers_images_and_declines_ifc_at_no_cost(tiny, tmp_path):
    vlm = MockVLM()
    result = _run(tiny, tmp_path, baseline.make_engine(vlm))
    rows = {r["qid"]: r for r in result["rows"]}
    assert result["complete"] and len(rows) == len(tiny.questions)
    for q in tiny.questions:
        row = rows[q.qid]
        if q.path.endswith(".ifc"):
            assert row["state"] == DECLINED and row["status"] == "abstained"
            assert row["cost_usd"] == 0.0 and "no image" in row["decline_reason"]
        else:
            assert row["state"] == CHECK and row["pred"] is not None  # never Verified
            assert row["meta"]["images"][0]["visual_tokens"] > 0
    assert vlm.calls == sum(not q.path.endswith(".ifc") for q in tiny.questions)


def test_baseline_never_sends_the_reference_answer_or_type(tiny):
    seen = []

    class Spy(MockVLM):
        def ask(self, system, images, text, schema):
            seen.append(system + "\n" + text)
            return super().ask(system, images, text, schema)

    q = next(q for q in tiny.questions if q.type == "area" and q.path.endswith(".pdf"))
    baseline.make_engine(Spy())(q, tiny)
    assert q.question in seen[0]
    assert str(q.answer) not in seen[0].replace(q.question, "")
    assert f"type: {q.type}" not in seen[0].lower()


def test_model_reply_without_an_answer_is_a_decline(tiny):
    class Unsure(MockVLM):
        def ask(self, system, images, text, schema):
            return VLMReply(data={"answer_number": None, "answer_text": None, "confidence": 0.2,
                                  "basis": "too blurry"}, model="mock", cost_usd=0.01)

    q = next(q for q in tiny.questions if q.path.endswith(".png"))
    ans = baseline.make_engine(Unsure())(q, tiny)
    assert ans.state == DECLINED and ans.answer is None and "too blurry" in ans.reason
    assert ans.cost_usd == 0.01  # a paid call that produced no answer still costs


# ---- harness ------------------------------------------------------------------------

def test_rows_are_written_as_they_land_and_scored(tiny, tmp_path):
    def oracle(q, ds):
        return Answer(answer=q.answer, cost_usd=0.011052)  # exactly EUR 0.01

    result = _run(tiny, tmp_path, oracle)
    lines = (tmp_path / "run" / "predictions.jsonl").read_text().splitlines()
    assert len(lines) == len(tiny.questions)
    assert all(r["status"] == "correct" for r in result["rows"])
    assert result["rows"][0]["cost_eur"] == pytest.approx(0.01)
    row = json.loads(lines[0])
    assert "question" not in row              # question text stays in the dataset
    text_row = next(r for r in result["rows"] if r["type"] == "text")
    assert "gold" not in text_row             # free-text references are not copied to logs


def test_resume_keeps_good_rows_and_retries_failed_ones(tiny, tmp_path):
    calls = []

    def flaky(q, ds):
        calls.append(q.qid)
        if len(calls) == 3:
            return Answer.failed("network error")
        return Answer(answer=q.answer)

    first = _run(tiny, tmp_path, flaky, workers=1)
    assert not first["complete"]
    failed = [r["qid"] for r in first["rows"] if r["error"]]
    assert len(failed) == 1

    calls.clear()
    second = _run(tiny, tmp_path, lambda q, ds: (calls.append(q.qid), Answer(answer=q.answer))[1], workers=1)
    assert calls == failed                      # only the failed question is asked again
    assert second["complete"] and all(not r["error"] for r in second["rows"])


def test_budget_stops_the_run(tiny, tmp_path):
    result = _run(tiny, tmp_path, lambda q, ds: Answer(answer=1, cost_usd=1.1052),
                  workers=1, max_cost_eur=3.0)
    assert not result["complete"] and "budget" in result["stopped"]
    assert 3 <= len(result["rows"]) < len(tiny.questions)


def test_repeated_engine_errors_stop_the_run(tiny, tmp_path):
    result = _run(tiny, tmp_path, lambda q, ds: Answer.failed("rate limited"), workers=1)
    assert "errors in a row" in result["stopped"]
    assert len(result["rows"]) == 3


def test_fatal_error_stops_at_once(tiny, tmp_path):
    def bad_key(q, ds):
        raise FatalEngineError("credentials rejected")

    result = _run(tiny, tmp_path, bad_key, workers=1)
    assert result["stopped"].startswith("fatal") and result["rows"] == []


def test_engine_crash_is_recorded_not_raised(tiny, tmp_path):
    def crash(q, ds):
        raise ZeroDivisionError("bug in engine")

    result = _run(tiny, tmp_path, crash, workers=1, max_consecutive_errors=10**6)
    assert all("ZeroDivisionError" in r["error"] for r in result["rows"])


def test_finalize_writes_summary_and_leaderboard(tiny, tmp_path):
    result = _run(tiny, tmp_path, lambda q, ds: Answer(answer=q.answer, cost_usd=0.002))
    board = tmp_path / "leaderboard.csv"
    weights = tiny.family_weights()
    for run_id in ("run-a", "run-b"):
        summary = finalize_run(run_id=run_id, run_dir=tmp_path / "run", method="oracle", result=result,
                               dataset=tiny, split_name="dev", sample_name="all", weights=weights,
                               leaderboard=board)
    assert summary["metrics"]["overall"]["accuracy"] == 1.0
    assert summary["commit"] and summary["code_sha"] and summary["questions_sha"]
    assert json.loads((tmp_path / "run" / "summary.json").read_text())["method"] == "oracle"

    rows = list(csv.DictReader(board.open()))
    assert [r["run_id"] for r in rows if r["scope"] == "overall"] == ["run-a", "run-b"]  # one header, appended
    scopes = {r["scope"] for r in rows}
    assert scopes == {"overall", "weighted_to_dataset_mix", "family"}
    families = {(r["source"], r["type"]) for r in rows if r["scope"] == "family"}
    assert ("swiss_dwellings", "area") in families and ("cubicasa5k", "count") in families
    overall = next(r for r in rows if r["scope"] == "overall")
    for column in ("commit", "method", "accuracy", "coverage", "eur_per_question"):
        assert overall[column] != ""


# ---- cost ------------------------------------------------------------------------------

def test_token_cost_uses_list_prices():
    usd = config.token_cost_usd("claude-opus-5-5", input_tokens=1_000_000, output_tokens=100_000)
    assert usd == pytest.approx(4.00 + 2.00)
    assert config.token_cost_usd("some-unpriced-model", 10, 10) is None  # never a guess
    assert config.usd_to_eur(1.1052) == pytest.approx(1.0)


def test_run_ids_do_not_collide_within_a_second():
    ids = {new_run_id("floor:no-drawing-prior") for _ in range(50)}
    assert len(ids) == 50
    assert all(":" not in i and "/" not in i for i in ids)  # safe as a folder name


def test_interrupted_run_is_not_put_on_the_leaderboard(tiny, tmp_path):
    result = _run(tiny, tmp_path, lambda q, ds: Answer(answer=1, cost_usd=1.1052), workers=1, max_cost_eur=2.0)
    board = tmp_path / "leaderboard.csv"
    finalize_run(run_id="partial", run_dir=tmp_path / "run", method="m", result=result, dataset=tiny,
                 split_name="dev", sample_name="all", leaderboard=board, log=result["complete"])
    assert not board.exists()
    assert json.loads((tmp_path / "run" / "summary.json").read_text())["complete"] is False


# ---- the no-drawing floor ------------------------------------------------------------

def test_floor_never_uses_the_answer_of_its_own_group(tiny):
    from blueprints import prior
    from blueprints.data import Question

    def q(group, answer, text="How many windows are shown on this floor plan?", qtype="count"):
        return Question(qid=f"{group}-{answer}-{qtype}", path=f"data/cubicasa5k/{group}.png", question=text,
                        answer=answer, unit="count", type=qtype, source="cubicasa5k",
                        drawing=f"data/cubicasa5k/{group}", group=f"cubicasa5k/{group}")

    dev = [q("a", 9), q("b", 2), q("c", 2), q("d", 3)]
    engine = prior.make_engine(dev)
    assert engine(dev[0], tiny).answer == 2          # its own 9 is not in the pool
    assert engine(dev[1], tiny).answer in (2, 3, 9)  # built from a, c, d only
    lonely = [q("a", 9)]
    assert prior.make_engine(lonely)(lonely[0], tiny).answer is None   # nothing else to learn from
    text_q = q("z", "Revit", text="What software?", qtype="text")
    assert prior.make_engine(dev)(text_q, tiny).state == DECLINED
