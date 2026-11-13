"""Reading questions by rule: quoted names, the measure, the element kind."""

import pytest

from blueprints.questions import (asks_about_whole_model, kinds_in, measure_of, norm,
                                  quoted_strings, strip_quoted)


@pytest.mark.parametrize("question,names", [
    ("What is the net floor area of the room 'KORIDOR (119)' on '1.korrus', in m²?", ["KORIDOR (119)", "1.korrus"]),
    ("Kui suur on ruumi „ELUTUBA+KÖÖK (3-1)“ netopõrandapind korrusel „1.korrus“ (m²)?", ["ELUTUBA+KÖÖK (3-1)", "1.korrus"]),
    ("Wie viele Räume mit der Bezeichnung «Schlafzimmer» gibt es in diesem Geschoss?", ["Schlafzimmer"]),
    ("How many rooms labelled 'Cage d'escalier' are there on this floor?", ["Cage d'escalier"]),
    ("Combien de pièces portant la désignation « Salle d'eau » y a-t-il ?", ["Salle d'eau"]),
    ("How many windows are shown on this floor plan?", []),
])
def test_quoted_names(question, names):
    assert quoted_strings(question) == names


def test_names_are_removed_before_reading_the_question():
    q = "What is the net floor area of the room 'Door store 2 m3' on 'Level 1', in m²?"
    assert "Door" not in strip_quoted(q)
    assert measure_of(q) == "area"       # the m3 inside the room name is ignored
    assert kinds_in(q) == ["space"]      # and so is the word Door


@pytest.mark.parametrize("question,measure", [
    ("How many doors are shown on the floor plan of '1.korrus'?", "count"),
    ("Mitu ust on näidatud korruse „1.korrus“ plaanil?", "count"),
    ("Hvor mange døre er vist på plantegningen for 'Stueplan'?", "count"),
    ("Kuinka monta ikkunaa pohjapiirustuksessa on?", "count"),
    ("What is the total net floor area of all rooms (spaces) on '1.korrus', in m²?", "area"),
    ("Kui suur on kõigi ruumide netopõrandapind kokku korrusel „1.korrus“ (m²)?", "area"),
    ("What is the net floor area of apartment A in m2?", "area"),
    ("What is the total net volume of all slabs on 'Level 1', in m³?", "volume"),
    ("What is the total length of all walls on 'Level 1', in m?", "length"),
    ("What software was used to create this model?", None),
])
def test_measure(question, measure):
    assert measure_of(question) == measure


@pytest.mark.parametrize("question,kind", [
    ("How many doors are shown on the floor plan of 'X'?", "door"),
    ("Mitu ust on mudelis 'X'?", "door"),
    ("How many windows are there in the model 'X'?", "window"),
    ("Mitu akent on mudelis 'X'?", "window"),
    ("How many rooms (spaces) are shown on the floor plan of 'X'?", "space"),
    ("Mitu ruumi on näidatud korruse 'X' plaanil?", "space"),
    ("How many walls are shown on the floor plan of 'X'?", "wall"),   # "floor plan" is not a slab
    ("Mitu seina on mudelis 'X'?", "wall"),
    ("How many stairs are there in the model 'X'?", "stair"),
    ("Mitu treppi on näidatud korruse 'X' plaanil?", "stair"),
    ("How many columns are there in the model 'X'?", "column"),
    ("Mitu posti on mudelis 'X'?", "column"),
    ("How many beams are there in the model 'X'?", "beam"),
    ("Mitu tala on mudelis 'X'?", "beam"),
    ("How many sanitary fixtures are there in the model 'X'?", "sanitary"),
    ("What is the total net volume of all slabs on 'X', in m³?", "slab"),
])
def test_kind(question, kind):
    assert kinds_in(question) == [kind]


def test_unknown_or_mixed_kinds_are_not_guessed():
    assert kinds_in("How many lifts are there in the model 'X'?") == []
    assert set(kinds_in("How many doors and windows are there in the model 'X'?")) == {"door", "window"}


def test_whole_model_scope_needs_the_word():
    assert asks_about_whole_model("How many doors are there in the model 'Duplex'?")
    assert asks_about_whole_model("Mitu ust on mudelis „X“?")
    assert not asks_about_whole_model("How many doors are shown on the floor plan of 'Model room'?")


def test_norm_makes_names_comparable():
    assert norm("  KORIDOR   (119) ") == norm("koridor (119)")
    assert norm("m²") == "m2"
