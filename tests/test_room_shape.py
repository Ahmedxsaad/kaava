"""A room measured as the free space inside its walls: any shape, holes cut out (measure.room_shape)."""

import pytest
from PIL import Image, ImageDraw

from blueprints import measure

PPM = 50.0  # pixels per metre in every drawing below
WALL = 10   # a wall 0.2 m thick


def draw(tmp_path, name, walls, clear=(), thin=(), solid=()):
    """A sheet: `walls` are rectangles' outlines drawn thick; `clear` cuts openings; `thin` lines are furniture."""
    im = Image.new("L", (1400, 1100), 255)
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in walls:
        d.rectangle([x0, y0, x1, y1], outline=0, width=WALL)
    for box in solid:
        d.rectangle(box, fill=0)
    for box in clear:
        d.rectangle(box, fill=255)
    for line in thin:
        d.line(line, fill=0, width=2)
    path = tmp_path / name
    im.save(path)
    measure._grey.cache_clear(); measure._masks.cache_clear()
    return path


def test_a_plain_room_measures_as_its_rectangle(tmp_path):
    # inner faces at 210..790 and 210..590: 11.6 m by 7.6 m
    path = draw(tmp_path, "plain.png", walls=[(200, 200, 800, 600)], thin=[(300, 300, 500, 300), (300, 300, 300, 400)])
    shape = measure.room_shape(path, [215, 215, 785, 585], PPM)
    assert shape["ok"] and not shape["holes"]
    assert shape["area_m2"] == pytest.approx(11.6 * 7.6, rel=0.01)       # thin lines (a table) are floor
    assert shape["perimeter_m"] == pytest.approx(2 * (11.6 + 7.6), rel=0.01)


def test_a_column_and_a_core_standing_in_the_room_are_taken_out_and_returned(tmp_path):
    # a 0.6 m square column, and a lift core 2 m by 2 m with its own walls and a door, inside the room
    path = draw(tmp_path, "holes.png", walls=[(200, 200, 800, 600), (500, 350, 600, 450)],
                solid=[(300, 300, 329, 329)], clear=[(540, 440, 570, 452)])
    shape = measure.room_shape(path, [215, 215, 785, 585], PPM)
    assert shape["ok"] and len(shape["holes"]) == 2
    areas = sorted(h["area_m2"] for h in shape["holes"])
    assert areas[0] == pytest.approx(0.36, rel=0.08) and areas[1] == pytest.approx(4.0, rel=0.05)
    assert shape["gross_m2"] == pytest.approx(11.6 * 7.6, rel=0.01)
    assert shape["area_m2"] == pytest.approx(11.6 * 7.6 - 0.36 - 4.0, rel=0.01)
    assert shape["hole_m2"] == pytest.approx(4.36, rel=0.05)


def test_an_l_shaped_room_is_measured_by_its_own_outline_and_stops_at_its_doors(tmp_path):
    # Two rooms share a wall with a 0.9 m door in it; the left one is L-shaped (a block is walled off in its corner).
    path = draw(tmp_path, "ell.png", walls=[(200, 200, 800, 600), (800, 200, 1100, 600), (200, 200, 500, 400)],
                clear=[(788, 450, 812, 495)])
    shape = measure.room_shape(path, [215, 215, 785, 585], PPM)
    assert shape["ok"]
    whole, block = 11.6 * 7.6, (500 - 210) / PPM * (400 - 210) / PPM   # the walled-off block, with its walls
    assert shape["area_m2"] == pytest.approx(whole - block, rel=0.02)   # and nothing of the room next door
    assert [round(o["width_m"], 1) for o in shape["openings"]] == [0.9]


def test_a_room_open_to_the_next_one_is_refused_not_guessed(tmp_path):
    # the wall between the two rooms has a 4 m gap: where the room ends cannot be told
    path = draw(tmp_path, "open.png", walls=[(200, 200, 800, 600), (800, 200, 1100, 600)], clear=[(788, 300, 812, 500)])
    shape = measure.room_shape(path, [215, 215, 785, 585], PPM)
    assert not shape["ok"] and "open to the space next to it" in shape["why"]


def test_bold_text_printed_in_the_room_is_not_a_column(tmp_path):
    path = draw(tmp_path, "text.png", walls=[(200, 200, 800, 600)], solid=[(400, 380, 440, 396)])
    with_text = measure.room_shape(path, [215, 215, 785, 585], PPM, texts=[{"box": [395, 375, 445, 400], "text": "HALL"}])
    assert with_text["ok"] and not with_text["holes"]
    assert with_text["area_m2"] == pytest.approx(11.6 * 7.6, rel=0.01)


def test_a_closet_behind_a_door_is_not_part_of_the_room_but_an_alcove_is(tmp_path):
    # Left: a closet 1.5 m wide behind a 0.7 m door. Right: an alcove 1.5 m wide, open across its whole width.
    path = draw(tmp_path, "nooks.png", walls=[(200, 200, 800, 600), (300, 600, 395, 660), (600, 600, 695, 660)],
                clear=[(330, 588, 365, 612), (611, 588, 684, 612)])
    shape = measure.room_shape(path, [215, 215, 785, 585], PPM)
    assert shape["ok"]
    alcove = (684 - 611 + 1) / PPM * (649 - 591 + 1) / PPM          # the alcove's floor, through the wall's thickness
    assert shape["area_m2"] == pytest.approx(11.6 * 7.6 + alcove, rel=0.01)
    assert [round(o["width_m"], 1) for o in shape["openings"]] == [0.7]   # the closet's door is a door opening


def test_a_window_is_wall_drawn_thin_and_does_not_let_the_room_out(tmp_path):
    # A 3 m window in the top wall: the solid wall is cut away and three thin lines are drawn along the gap.
    path = draw(tmp_path, "window.png", walls=[(200, 200, 800, 600)], clear=[(400, 198, 550, 212)],
                thin=[(400, 201, 550, 201), (400, 205, 550, 205), (400, 209, 550, 209)])
    shape = measure.room_shape(path, [215, 215, 785, 585], PPM)
    assert shape["ok"] and shape["area_m2"] == pytest.approx(11.6 * 7.6, rel=0.01)
    assert shape["openings"] == []           # a window is not a door opening: the baseboard runs under it
