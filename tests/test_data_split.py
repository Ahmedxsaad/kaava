import json
from collections import Counter

import pytest

from blueprints.data import Dataset, drawing_of, question_id, question_template, source_of
from blueprints.sample import allocate, load_sample, save_sample, stratified_sample
from blueprints.split import (FRACTIONS, SPLITS, Split, SplitLocked, find_links, make_split)

from conftest import build_dataset


# ---- loader ----------------------------------------------------------------------

def test_question_ids_are_stable_and_unique(tiny):
    qs = tiny.questions
    assert len({q.qid for q in qs}) == len(qs)
    q = qs[0]
    assert q.qid == question_id(q.path, q.question)
    assert Dataset(tiny.root).questions[0].qid == q.qid  # same on reload


def test_source_and_drawing():
    assert source_of("data/cubicasa5k/colorful-10052.png") == "cubicasa5k"
    assert source_of("data/opening_design/CTR/Most Current Set/A700 - SCHEDULES.pdf") == "opening_design"
    # Same name, different extension: the same drawing.
    assert drawing_of("data/swiss_dwellings/site1_bldg2_floor3.pdf") == drawing_of(
        "data/swiss_dwellings/site1_bldg2_floor3.dxf")


def test_formats_lists_every_format_of_a_drawing(tiny):
    files = tiny.formats("data/swiss_dwellings/site0_bldg0_floor1.pdf")
    assert sorted(f.rsplit(".", 1)[1] for f in files) == ["dxf", "pdf"]


def test_group_rules(tiny):
    g = tiny.group_of
    # Floors of one Swiss site stay together.
    assert g("data/swiss_dwellings/site3387_bldg5238_floor23498.pdf") == "swiss_dwellings/site3387"
    assert g("data/swiss_dwellings/site3387_bldg9999_floor1.dxf") == "swiss_dwellings/site3387"
    # A rendered plan stays with its model; models of one project stay together.
    assert g("data/ifc_models/bsc-duplex_duplex-a-20110907.ifc") == "ifc_models/bsc-duplex"
    assert g("data/ifc_models/bsc-duplex_duplex-a-20110907_plan-01-level-1.pdf") == "ifc_models/bsc-duplex"
    assert g("data/ifc_models/bsc-duplex_duplex-m-20110907.ifc") == "ifc_models/bsc-duplex"
    # ifc_bench project names can contain underscores; the last token is the discipline.
    assert g("data/ifc_bench/fantasy_office_building_3_arc.ifc") == "ifc_bench/fantasy_office_building_3"
    assert g("data/ifc_bench/fantasy_office_building_3_str.ifc") == "ifc_bench/fantasy_office_building_3"
    # Sheets of one project folder stay together.
    assert g("data/opening_design/Sports_Complex/Sheets - Current Set/A-801 - Doors.pdf") == \
        g("data/opening_design/Sports_Complex/Sheets - Current Set/A-701 - Restrooms.pdf")
    # Each CubiCasa plan is its own unit.
    assert g("data/cubicasa5k/colorful-1.png") != g("data/cubicasa5k/colorful-2.png")


def test_render_is_mapped_to_its_ifc_model(tiny):
    render = "data/ifc_models/col-duplex_duplex-a_plan-01-level-1.pdf"
    assert tiny.ifc_model_of(render) == "data/ifc_models/col-duplex_duplex-a"
    assert tiny.renders_of("data/ifc_models/col-duplex_duplex-a.ifc") == [render]


def test_template_blanks_names_and_numbers():
    a = question_template("What is the net floor area of room 12 ('Bad') in m²?")
    b = question_template("What is the net floor area of room 3 ('Küche') in m²?")
    assert a == b
    assert question_template("Kui suur on ruumi „ESIK (47-3)“ netopõrandapind?") == \
        question_template("Kui suur on ruumi „KORIDOR (12)“ netopõrandapind?")
    # An apostrophe inside the quoted name does not end it.
    assert question_template("How many rooms labelled 'Cage d'escalier' are there on this floor?") == \
        question_template("How many rooms labelled 'Bad' are there on this floor?")
    assert question_template("Combien de pièces portant la désignation 'Salle d'eau' y a-t-il à cet étage ?") == \
        question_template("Combien de pièces portant la désignation 'Cuisine' y a-t-il à cet étage ?")


# ---- split -----------------------------------------------------------------------

def test_every_group_is_in_exactly_one_split(tiny, tmp_path):
    split, _ = make_split(tiny, tmp_path / "split.json")
    qs = tiny.questions
    assert all(split.of(q) in SPLITS for q in qs)
    by_group = {}
    for q in qs:
        by_group.setdefault(q.group, set()).add(split.of(q))
    assert all(len(s) == 1 for s in by_group.values())
    # No drawing is shared between splits either.
    by_drawing = {}
    for q in qs:
        by_drawing.setdefault(q.drawing, set()).add(split.of(q))
    assert all(len(s) == 1 for s in by_drawing.values())


def test_same_ifc_project_in_two_sources_shares_a_split(tiny, tmp_path):
    links = find_links(tiny, tiny.questions)
    assert links == [["ifc_bench/duplex", "ifc_models/col-duplex"]]
    split, _ = make_split(tiny, tmp_path / "split.json")
    assert split.groups["ifc_bench/duplex"] == split.groups["ifc_models/col-duplex"]
    assert "ifc_models/col-other" in split.groups  # a different GlobalId is not linked


def test_split_is_deterministic(tiny, tmp_path):
    a, _ = make_split(tiny, tmp_path / "a.json")
    b, _ = make_split(tiny, tmp_path / "b.json")
    assert a.groups == b.groups


def test_split_is_frozen_when_the_dataset_grows(tmp_path):
    small = tmp_path / "small" / "junction-dataset-preview"
    big = tmp_path / "big" / "junction-dataset"
    small.mkdir(parents=True)
    big.mkdir(parents=True)
    build_dataset(small, n_plans=12, n_sites=8)
    build_dataset(big, n_plans=60, n_sites=30)  # a superset: same names plus new ones
    path = tmp_path / "split.json"

    first, _ = make_split(Dataset(small), path)
    second, report = make_split(Dataset(big), path)

    assert report["kept"] == len(first.groups) and report["added"] > 0
    for group, name in first.groups.items():
        assert second.groups[group] == name, f"{group} moved"
    assert len(json.loads(path.read_text())["history"]) == 2


def test_split_is_balanced_per_type_with_enough_groups(tmp_path):
    root = tmp_path / "junction-dataset"
    root.mkdir()
    build_dataset(root, n_plans=200, n_sites=80)
    ds = Dataset(root)
    split, _ = make_split(ds, tmp_path / "split.json")
    for family in [("cubicasa5k", "count"), ("swiss_dwellings", "area"), ("swiss_dwellings", "count")]:
        qs = [q for q in ds.questions if q.family == family]
        share = Counter(split.of(q) for q in qs)
        for name in SPLITS:
            assert share[name] / len(qs) == pytest.approx(FRACTIONS[name], abs=0.03)


def test_test_split_is_locked_by_default(tiny, tmp_path):
    split, _ = make_split(tiny, tmp_path / "split.json")
    with pytest.raises(SplitLocked):
        split.select(tiny.questions, "test")
    assert split.select(tiny.questions, "test", allow_test=True) is not None
    assert split.select(tiny.questions, "dev")


def test_saved_split_round_trips(tiny, tmp_path):
    path = tmp_path / "split.json"
    made, _ = make_split(tiny, path)
    assert Split.load(path).groups == made.groups


# ---- sample ----------------------------------------------------------------------

def test_allocate_sums_and_respects_sizes():
    sizes = {("a", "count"): 2000, ("b", "area"): 400, ("c", "text"): 7, ("d", "count"): 3}
    quota = allocate(sizes, 200)
    assert sum(quota.values()) == 200
    assert all(quota[f] <= sizes[f] for f in sizes)
    assert quota[("d", "count")] == 3            # small family taken whole
    assert quota[("a", "count")] > quota[("b", "area")] > quota[("c", "text")]
    # Square-root allocation: 5x the questions gets far less than 5x the sample.
    assert quota[("a", "count")] < 3 * quota[("b", "area")]


def test_allocate_when_n_exceeds_everything():
    sizes = {("a", "count"): 4, ("b", "area"): 2}
    assert allocate(sizes, 100) == sizes


def test_sample_is_deterministic_and_spread_over_drawings(tiny, tmp_path):
    split, _ = make_split(tiny, tmp_path / "split.json")
    dev = split.select(tiny.questions, "dev")
    a = stratified_sample(dev, 10, seed="s")
    b = stratified_sample(dev, 10, seed="s")
    assert [q.qid for q in a] == [q.qid for q in b]
    cubi = [q for q in a if q.source == "cubicasa5k"]
    dev_plans = {q.drawing for q in dev if q.source == "cubicasa5k"}
    # One question per drawing before any drawing gets a second one.
    assert len({q.drawing for q in cubi}) == min(len(cubi), len(dev_plans))


def test_sample_file_round_trips_and_rejects_another_dataset(tiny, tmp_path):
    split, _ = make_split(tiny, tmp_path / "split.json")
    picked = stratified_sample(split.select(tiny.questions, "dev"), 8, seed="s")
    path = tmp_path / "sample.json"
    save_sample(path, "s", "dev", "s", picked)
    meta, loaded = load_sample(path, tiny)
    assert [q.qid for q in loaded] == [q.qid for q in picked] and meta["split"] == "dev"

    data = json.loads(path.read_text())
    data["qids"].append("not-a-question")
    path.write_text(json.dumps(data))
    with pytest.raises(KeyError):
        load_sample(path, tiny)
