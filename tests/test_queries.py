"""Free-form questions read as queries: the language model selects, the engine counts and measures."""

import pytest

from blueprints import model_query, plan_query
from blueprints.engines import dxf, ifc
from blueprints.scoring import CHECK, DECLINED
from blueprints.vlm import MockVLM, VLMReply


class Selector(MockVLM):
    """A language model that returns the query it was built with, and remembers what it was shown."""

    def __init__(self, **query):
        super().__init__()
        self.query, self.seen = query, []

    def ask(self, system, images, text, schema):
        self.seen.append((system, images, text, schema))
        return VLMReply(data=self.query, model="mock", cost_usd=0.004, latency_ms=2.0)


def square(x, y, side=4.0):
    return [[x, y], [x + side, y], [x + side, y + side], [x, y + side]]


def room(number, name, x, y, **extra):
    return {"polygon": square(x, y), "area": 16.0, "gross_area": 16.0, "holes": 0, "hole_rings": [], "shaft": False,
            "number": number, "name": name, "printed_area": None, "apartment_tag": None, "unit": 0, "texts": [], **extra}


# Three rooms in a row: two Stanza and a Bagno. A door between the first two, a window on each Stanza's
# outer wall and one on the Bagno's; a toilet in the Bagno. One door outline is in the file twice.
PLAN = {
    "rooms": [room("1.01", "Stanza", 0, 0), room("1.02", "Stanza", 4, 0), room("1.03", "Bagno", 8, 0)],
    "units": [{"tag": "A", "tags": ["A"], "rooms": [0, 1, 2]}],
    "doors": [{"bbox": [3.9, 1.0, 4.1, 1.9]}, {"bbox": [3.9, 1.0, 4.1, 1.9]}, {"bbox": [7.9, 1.0, 8.1, 1.8]}],
    "windows": [{"bbox": [1.0, -0.1, 2.5, 0.1]}, {"bbox": [5.0, -0.1, 6.5, 0.1]}, {"bbox": [9.0, -0.1, 10.0, 0.1]}],
    "fixtures": [{"kind": "toilet", "bbox": [9.0, 2.0, 9.5, 2.6]}],
}
COMMON = dict(method=dxf.METHOD, cost_usd=0.0)


def test_a_question_that_names_a_room_is_not_a_count_over_the_whole_floor():
    """"The windows in Stanza" answered with every window of the floor would be wrong, and it was once Verified."""
    assert dxf.part_named("what is the number of windows in stanza", PLAN) == "'Stanza'"
    assert dxf.part_named("How many windows in room 1.02?", PLAN) == "room 1.02"
    assert dxf.part_named("how many doors does apartment A have", PLAN) == "apartment A"
    assert dxf.part_named("How many windows are in 'Bagno'?", PLAN) == "'Bagno'"
    # The dataset's own questions about the whole floor name no room: a kind's own words are not a room.
    assert dxf.part_named("How many windows are drawn on this floor plan?", PLAN) is None
    assert dxf.part_named("Quante vasche da bagno sono disegnate in questa pianta?", PLAN) is None


def test_the_language_model_is_shown_the_plans_rooms_and_gives_no_number():
    llm = Selector(measure="area", rooms=[0, 1, 9], item="", combine="sum", cannot=False, reading="all Stanza rooms")
    query = plan_query.ask("the area of the stanza", PLAN, llm, dxf.is_outdoor)
    assert query.rooms == [0, 1]                                    # an id that is not a room of the plan is dropped
    system, images, text, schema = llm.seen[0]
    assert images == [] and "0 | 1.01 | Stanza | A | no | 16.00" in text
    assert "answer" not in schema["properties"] and "never compute or state a number" in system


def test_rooms_are_added_up_compared_and_measured_around():
    area = plan_query.run(plan_query.Query(measure="area", rooms=[0, 1]), PLAN, dxf, dict(COMMON))
    assert area.answer == 32.0 and area.unit == "m2" and area.state == CHECK and len(area.evidence) == 3
    largest = plan_query.run(plan_query.Query(measure="area", rooms=[0, 1, 2], combine="largest"), PLAN, dxf, dict(COMMON))
    assert largest.answer == 16.0 and len(largest.evidence) == 1
    around = plan_query.run(plan_query.Query(measure="perimeter", rooms=[2]), PLAN, dxf, dict(COMMON))
    assert around.answer == 16.0 and around.unit == "m"
    skirting = plan_query.run(plan_query.Query(measure="baseboard", rooms=[1]), PLAN, dxf, dict(COMMON))
    # Stanza 1.02 has two doors on its outline (0.9 m and 0.8 m); the outline held twice is one door.
    assert skirting.answer == pytest.approx(16.0 - 0.9 - 0.8) and "2 door openings" in skirting.explanation
    assert sum(1 for e in skirting.evidence if e.get("role") == "door opening, left out") == 2


def test_the_windows_doors_and_fixtures_of_named_rooms_are_the_ones_that_belong_to_them():
    windows = plan_query.run(plan_query.Query(measure="count_items", item="window", rooms=[0, 1]), PLAN, dxf, dict(COMMON))
    assert windows.answer == 2 and "The plan holds 3 on the whole floor" in windows.explanation
    doors = plan_query.run(plan_query.Query(measure="count_items", item="door", rooms=[0, 1]), PLAN, dxf, dict(COMMON))
    assert doors.answer == 2                                         # the door between the two rooms is counted once
    toilets = plan_query.run(plan_query.Query(measure="count_items", item="toilet", rooms=[0]), PLAN, dxf, dict(COMMON))
    assert toilets.answer == 0 and toilets.evidence[0]["kind"] == "formula"
    none = plan_query.run(plan_query.Query(measure="area", rooms=[]), PLAN, dxf, dict(COMMON))
    assert none.state == DECLINED


MODEL = {
    "storeys": [{"name": "Ground"}, {"name": "First"}],
    "kinds": {"door": [{"id": "d1", "cls": "IfcDoor", "name": "D", "storey": 0}, {"id": "d2", "cls": "IfcDoor", "name": "D", "storey": 1},
                       {"id": "d3", "cls": "IfcDoor", "name": "D", "storey": 1}], "window": []},
    "spaces": [{"id": "s1", "name": "101", "long_name": "CORRIDOR", "storey": 0, "area_geometry": 20.0, "area_outline": 20.0, "area_qto_net": 20.0, "area_qto_gross": None},
               {"id": "s2", "name": "102", "long_name": "WC", "storey": 0, "area_geometry": 3.0, "area_outline": 3.0, "area_qto_net": None, "area_qto_gross": None},
               {"id": "s3", "name": "201", "long_name": "OFFICE", "storey": 1, "area_geometry": None, "area_outline": None, "area_qto_net": 12.5, "area_qto_gross": None}],
}
IFC_COMMON = dict(method=ifc.METHOD, cost_usd=0.0)


def test_a_model_question_is_run_on_the_models_storeys_elements_and_rooms():
    doors = model_query.run(model_query.Query(measure="count_elements", kind="door", storeys=[1]), MODEL, ifc._element, dict(IFC_COMMON))
    assert doors.answer == 2 and "on 'First'" in doors.explanation and len(doors.evidence) == 2
    everywhere = model_query.run(model_query.Query(measure="count_elements", kind="door", storeys=[0, 1]), MODEL, ifc._element, dict(IFC_COMMON))
    assert everywhere.answer == 3 and "in the whole model" in everywhere.explanation
    total = model_query.run(model_query.Query(measure="area", rooms=[0, 1, 2]), MODEL, ifc._element, dict(IFC_COMMON))
    assert total.answer == 35.5 and total.unit == "m2"              # a room without geometry uses the model's own quantity
    biggest = model_query.run(model_query.Query(measure="area", rooms=[0, 1, 2], combine="largest"), MODEL, ifc._element, dict(IFC_COMMON))
    assert biggest.answer == 20.0 and "CORRIDOR (101)" in biggest.explanation
    storeys = model_query.run(model_query.Query(measure="count_storeys"), MODEL, ifc._element, dict(IFC_COMMON))
    assert storeys.answer == 2 and "Ground, First" in storeys.explanation
    unknown = model_query.run(model_query.Query(measure="count_elements", kind="pool"), MODEL, ifc._element, dict(IFC_COMMON))
    assert unknown.state == DECLINED and "holds no element kind called 'pool'" in unknown.reason


def test_a_model_question_that_names_a_room_is_not_a_count_over_the_storey():
    assert ifc.room_named("how many doors does the corridor have on 'Ground'", MODEL) == "CORRIDOR"
    assert ifc.room_named("How many doors are shown on the floor plan of 'Ground'?", MODEL) is None
