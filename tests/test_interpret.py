"""Restating a question for the engines: the model words the question, the engine computes the answer."""

from types import SimpleNamespace

from blueprints import interpret


class FakeLLM:
    """Stands in for a language model: returns what the test tells it to."""

    model = "fake-model"

    def __init__(self, data=None, error=None):
        self.data, self.error, self.prompts = data, error, []

    def ask(self, system, images, text, schema):
        assert images == [] and "restated" in schema["properties"]
        self.prompts.append(text)
        return SimpleNamespace(data=self.data, error=self.error, cost_usd=0.002, latency_ms=40.0,
                               model=self.model, no_answer="")


EXAMPLES = ["How many doors (interior doors and entrance doors together) are drawn on this floor plan?",
            "What is the net floor area of room 2.21 ('Pièce') in m²?"]
NAMES = {"Room numbers on the plan": ["2.20", "2.21"], "Apartments on the plan": ["A", "B"]}


def test_the_prompt_carries_the_examples_and_the_names_but_no_answer():
    llm = FakeLLM({"restated": EXAMPLES[0], "matches_example": True, "reason": "asks for the door count"})
    out = interpret.restate("combien de portes au total ?", EXAMPLES, NAMES, llm)
    assert out.question == EXAMPLES[0] and out.cost_usd == 0.002 and out.model == "fake-model"
    prompt = llm.prompts[0]
    assert "combien de portes au total ?" in prompt and EXAMPLES[1] in prompt and "2.21" in prompt and "B" in prompt


def test_no_restatement_is_a_valid_outcome():
    llm = FakeLLM({"restated": None, "matches_example": False, "reason": "a 2D plan holds no heights"})
    out = interpret.restate("how high is the ceiling?", EXAMPLES, NAMES, llm)
    assert out.question is None and "heights" in out.reason


def test_an_unchanged_or_failed_reply_is_not_a_restatement():
    same = FakeLLM({"restated": "how  many doors?", "matches_example": False, "reason": ""})
    assert interpret.restate("how many doors?", EXAMPLES, NAMES, same).question is None
    down = FakeLLM(error="timeout")
    out = interpret.restate("how many doors?", EXAMPLES, NAMES, down)
    assert out.question is None and out.error == "timeout"


def test_names_come_from_the_drawing():
    dxf = {"rooms": [{"name": "Zimmer", "number": "2.01"}, {"name": "", "number": "2.16"}, {"name": "x", "number": "9", "shaft": True}],
           "units": [{"tag": "B"}, {"tag": None}]}
    names = interpret.names_on_dxf(dxf)
    assert names["Room labels on the plan"] == ["Zimmer"] and names["Room numbers on the plan"] == ["2.01", "2.16"]
    assert names["Apartments on the plan"] == ["B"] and names["Rooms, as number ('label')"] == ["2.01 ('Zimmer')"]
    ifc = {"storeys": [{"name": "Level 1"}], "kinds": {"door": [1], "ramp": []},
           "spaces": [{"name": "101", "long_name": "Office", "storey": 0}]}
    names = interpret.names_in_ifc(ifc)
    assert names["Storeys of the model"] == ["'Level 1'"] and names["Element kinds in the model"] == ["door"]
    assert names["Rooms of the model"] == ["'Office (101)' on 'Level 1'"]
