"""The commands end to end, on the synthetic dataset, with the offline model."""

import csv
import json

import pytest

from blueprints import cli, config


@pytest.fixture
def project(tiny_root, tmp_path, monkeypatch):
    """Point the CLI's output folders at a temp project so nothing real is touched."""
    monkeypatch.setattr(config, "SPLITS_DIR", tmp_path / "splits")
    monkeypatch.setattr(config, "SPLIT_FILE", tmp_path / "splits" / "split_v1.json")
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def run(*args):
        return cli.main(["--dataset", str(tiny_root), *args])

    assert run("split") == 0
    return run, tmp_path


def test_split_sample_floor_score_errors(project, capsys):
    run, root = project
    assert run("info") == 0
    assert run("sample", "--split", "dev", "--n", "12", "--name", "s12") == 0
    sample = root / "splits" / "s12.json"
    assert json.loads(sample.read_text())["split"] == "dev"

    # A sample is fixed: it is not silently replaced.
    with pytest.raises(SystemExit):
        run("sample", "--split", "dev", "--n", "12", "--name", "s12")

    assert run("floor", "--sample", str(sample)) == 0
    board = list(csv.DictReader((root / "results" / "leaderboard.csv").open()))
    assert {r["method"] for r in board} == {"floor:no-drawing-prior"}
    (run_dir,) = (root / "results" / "runs").iterdir()

    # Re-scoring the logged answers through `score` reproduces the same numbers.
    preds = root / "preds.jsonl"
    with preds.open("w") as f:
        for line in (run_dir / "predictions.jsonl").read_text().splitlines():
            row = json.loads(line)
            f.write(json.dumps({"qid": row["qid"], "answer": row["pred"]}) + "\n")
    assert run("score", str(preds), "--method", "replay", "--sample", str(sample)) == 0
    board = list(csv.DictReader((root / "results" / "leaderboard.csv").open()))
    floor, replay = (next(r for r in board if r["method"] == m and r["scope"] == "overall")
                     for m in ("floor:no-drawing-prior", "replay"))
    for column in ("n", "answered", "correct", "wrong", "accuracy"):
        assert floor[column] == replay[column]

    capsys.readouterr()
    assert run("errors", run_dir.name, "--status", "all", "-n", "3") == 0
    assert "Q: " in capsys.readouterr().out


def test_mock_baseline_runs_but_is_never_logged_as_a_result(project, capsys):
    run, root = project
    run("sample", "--split", "dev", "--n", "10", "--need-image", "--name", "img")
    sample = str(root / "splits" / "img.json")
    assert run("baseline", "--sample", sample, "--dry-run") == 0
    assert "nothing sent" in capsys.readouterr().out
    assert run("baseline", "--sample", sample, "--provider", "mock", "--workers", "2") == 0
    assert not (root / "results").exists()   # offline runs stay out of results/


def test_test_split_needs_final_and_is_logged(project):
    run, root = project
    with pytest.raises(SystemExit):
        run("sample", "--split", "test", "--n", "5", "--name", "t")
    assert not (root / "splits" / "t.json").exists()
    assert run("sample", "--split", "test", "--n", "5", "--name", "t", "--final") == 0
    assert "sample t" in (root / "results" / "test_access.log").read_text()

    # Evaluating on it is refused without --final, even with a ready sample file.
    with pytest.raises(SystemExit):
        run("baseline", "--sample", str(root / "splits" / "t.json"), "--provider", "mock")


def test_predictions_for_another_split_are_refused(project):
    run, root = project
    run("sample", "--split", "calibration", "--n", "5", "--name", "cal")
    qid = json.loads((root / "splits" / "cal.json").read_text())["qids"][0]
    preds = root / "p.jsonl"
    preds.write_text(json.dumps({"qid": qid, "answer": 1}) + "\n")
    with pytest.raises(SystemExit):   # claimed as dev, but the question is in calibration
        run("score", str(preds), "--method", "m", "--split", "dev")
