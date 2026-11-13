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


# ---- floor-plan questions in the four languages of the Swiss plans -------------------

from blueprints.questions import apartment_in, asks_about_numbered_rooms, bare_numbers


@pytest.mark.parametrize("question,kind", [
    ("How many doors (interior doors and entrance doors together) are drawn on this floor plan?", "door"),
    ("Combien de portes (portes intérieures et portes d'entrée confondues) sont dessinées sur ce plan ?", "door"),
    ("Wie viele Türen (Innentüren und Eingangstüren zusammen) sind auf diesem Grundriss eingezeichnet?", "door"),
    ("Quante porte (porte interne e porte d'ingresso insieme) sono disegnate in questa pianta?", "door"),
    ("How many windows are drawn on this floor plan?", "window"),
    ("Combien de fenêtres sont dessinées sur ce plan ?", "window"),
    ("Wie viele Fenster sind auf diesem Grundriss eingezeichnet?", "window"),
    ("Quante finestre sono disegnate in questa pianta?", "window"),
    ("How many apartments (residential units) are shown on this floor plan?", "apartment"),
    ("Combien d'appartements (logements) figurent sur ce plan d'étage ?", "apartment"),
    ("Wie viele Wohnungen sind auf diesem Grundriss dargestellt?", "apartment"),
    ("Quanti appartamenti (unità abitative) sono rappresentati in questa pianta?", "apartment"),
    ("How many toilets (WC) are drawn on this floor plan?", "toilet"),
    ("Combien de WC sont dessinés sur ce plan ?", "toilet"),
    ("Wie viele WCs sind auf diesem Grundriss eingezeichnet?", "toilet"),
    ("Quanti WC sono disegnati in questa pianta?", "toilet"),
    ("How many washbasins are drawn on this floor plan?", "washbasin"),
    ("Combien de lavabos sont dessinés sur ce plan ?", "washbasin"),
    ("Quanti lavabi sono disegnati in questa pianta?", "washbasin"),
    ("How many bathtubs are drawn on this floor plan?", "bathtub"),
    ("Wie viele Badewannen sind auf diesem Grundriss eingezeichnet?", "bathtub"),
    ("Quante vasche da bagno sono disegnate in questa pianta?", "bathtub"),
    ("How many showers are drawn on this floor plan?", "shower"),
    ("Combien de douches sont dessinées sur ce plan ?", "shower"),
    ("Quante docce sono disegnate in questa pianta?", "shower"),
])
def test_floor_plan_kinds_in_four_languages(question, kind):
    assert kinds_in(question) == [kind]
    assert measure_of(question) == "count"
    assert quoted_strings(question) == []   # the apostrophes in d'entrée, d'étage are not quotes


@pytest.mark.parametrize("question,apartment", [
    ("What is the net floor area of apartment B in m², summing all its rooms except balconies?", "B"),
    ("Quelle est la surface nette de l'appartement B en m² (somme de toutes ses pièces) ?", "B"),
    ("Wie gross ist die Nettowohnfläche von Wohnung C in m² (Summe aller Räume)?", "C"),
    ("Qual è la superficie netta dell'appartamento A in m² (somma di tutti i locali)?", "A"),
    ("How many apartments (residential units) are shown on this floor plan?", None),
    ("What is the net floor area of the apartment in the north wing, in m²?", None),
])
def test_apartment_reference(question, apartment):
    assert apartment_in(question) == apartment


def test_room_number_and_numbered_rooms():
    assert bare_numbers("What is the net floor area of room 2.01 ('Zimmer') in m²?") == ["2.01"]
    assert bare_numbers("Quelle est la surface nette de la pièce 0.13 ('Couloir') en m² ?") == ["0.13"]
    assert bare_numbers("What is the total net area in m² of all rooms labelled 'Bad 2' on this floor?") == []
    assert asks_about_numbered_rooms("What is the total net floor area in m² of all numbered indoor rooms on this floor?")
    assert asks_about_numbered_rooms("Qual è la superficie netta totale in m² di tutti i locali interni numerati di questo piano?")
    assert not asks_about_numbered_rooms("What is the total net area in m² of all rooms labelled 'X' on this floor?")


def test_room_reference_is_read_by_structure_whatever_the_numbering():
    from blueprints.questions import room_ref_in

    assert room_ref_in("What is the net floor area of room 2.01 ('Zimmer') in m²?") == "2.01"
    assert room_ref_in("What is the net floor area of room U1.13 ('Reduit') in m²?") == "U1.13"
    assert room_ref_in("Wie gross ist die Nettofläche von Raum U1.07 («Bad») in m²?") == "U1.07"
    assert room_ref_in("Quelle est la surface nette de la pièce 0.13 ('Couloir') en m² ?") == "0.13"
    assert room_ref_in("What is the total net area in m² of all rooms labelled 'Bad' on this floor?") is None
    assert room_ref_in("How many rooms labelled 'Zimmer' are there on this floor?") is None
