"""The walls of a scan, found in its pixels, and the scan stood up in 3D on them.

Every image here is drawn by the test: no dataset file, no annotation, no model.
"""

import numpy as np
import pytest
from PIL import Image, ImageDraw
from shapely.geometry import Point, Polygon

from blueprints import config, mesh, scan_plan

W, H = 640, 480


def page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (W, H), 255)
    return image, ImageDraw.Draw(image)


def solid_plan() -> Image.Image:
    """Two rooms inside thick black walls; a table drawn in thin lines and some text stand in one."""
    image, draw = page()
    for box in ((60, 60, 580, 70), (60, 410, 580, 420), (60, 60, 70, 420), (570, 60, 580, 420), (300, 60, 310, 300)):
        draw.rectangle(box, fill=0)                       # outer walls, 11 px thick, and a partition
    draw.rectangle((130, 180, 230, 250), outline=0, width=2)  # a table: thin lines
    draw.line((330, 380, 520, 380), fill=0, width=1)      # a worktop's edge: one thin line
    draw.text((360, 150), "OH  24,5 m2", fill=0)
    draw.text((120, 120), "MH", fill=0)
    return image


def covered(plan: dict, x: float, y: float) -> bool:
    return any(Polygon(p["outer"], p["holes"]).buffer(0).contains(Point(x, y)) for p in plan["polygons"])


def test_thick_walls_are_found_and_thin_lines_and_text_are_not():
    plan = scan_plan.find_walls(np.asarray(solid_plan()))
    assert plan["ok"] and plan["quality"]["how"] == "solid"
    assert 8 <= plan["thickness_px"] <= 12 and (plan["width"], plan["height"]) == (W, H)
    for x, y in ((320, 65), (65, 240), (575, 240), (320, 415), (305, 180)):   # on each wall
        assert covered(plan, x, y), (x, y)
    for x, y in ((130, 215), (180, 181), (420, 380), (370, 155), (200, 300)):  # table, worktop, text, floor
        assert not covered(plan, x, y), (x, y)
    # The walls close round the rooms: one outline, with the rooms as its holes.
    assert len(plan["polygons"]) == 1 and len(plan["polygons"][0]["holes"]) == 1


def test_walls_drawn_as_two_thin_lines_are_the_strip_between_them():
    image, draw = page()
    for a, b in (((60, 60, 580, 60), (60, 68, 580, 68)), ((60, 412, 580, 412), (60, 420, 580, 420))):
        draw.line(a, fill=0, width=1)
        draw.line(b, fill=0, width=1)
    for a, b in (((60, 60, 60, 420), (68, 60, 68, 420)), ((572, 60, 572, 420), (580, 60, 580, 420))):
        draw.line(a, fill=0, width=1)
        draw.line(b, fill=0, width=1)
    draw.line((120, 240, 520, 240), fill=0, width=1)   # a single long line: a dimension line, not a wall
    plan = scan_plan.find_walls(np.asarray(image))
    assert plan["ok"] and plan["quality"]["how"] == "lines"
    assert covered(plan, 320, 64) and covered(plan, 64, 240) and covered(plan, 576, 240) and covered(plan, 320, 416)
    assert not covered(plan, 320, 240) and not covered(plan, 320, 150)


@pytest.mark.parametrize("fill, why", [(255, "almost empty"), (0, "mostly ink")])
def test_a_page_with_no_plan_on_it_is_refused(fill, why):
    plan = scan_plan.find_walls(np.full((H, W), fill, dtype=np.uint8))
    assert not plan["ok"] and why in plan["why"] and plan["polygons"] == []


def test_a_page_of_text_and_thin_lines_is_refused_and_so_is_one_filled_shape():
    image, draw = page()
    for row in range(40, 440, 22):
        draw.text((40, row), "Asunto Oy Esimerkki  3H+K+S  74,5 m2  kerros 2  ohjeellinen, ei mittakaavassa", fill=0)
    draw.rectangle((30, 30, 610, 450), outline=0, width=1)
    assert not scan_plan.find_walls(np.asarray(image))["ok"]

    image, draw = page()
    draw.rectangle((120, 90, 520, 390), fill=0)  # a dark block: an area, not walls
    blob = scan_plan.find_walls(np.asarray(image))
    assert not blob["ok"]


def test_a_scan_stands_up_in_3d_on_its_own_pixels(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(scan_plan, "_known", None)
    path = tmp_path / "plan.png"
    solid_plan().save(path)
    assert scan_plan.verdict_if_known(path) is None       # nothing worked out yet: a listing says nothing

    data = mesh.scan_mesh(path)
    header, positions, indices = mesh.unpack(data)       # the reader every mesh goes through
    assert data[:4] == b"BPM1" and header["kind"] == "scan" and header["scan"]["scale"] == "assumed"
    assert len(positions) == 3 * header["vertices"] and len(indices) == 3 * header["triangles"]
    assert max(indices) < header["vertices"]
    kinds = [e["cls"] for e in header["elements"]]
    assert kinds[0] == "IfcSlab" and kinds.count("wall") == 1   # the sheet, and the walls on it
    assert scan_plan.verdict_if_known(path) is True and mesh.scan_mesh(path) == data  # kept, and known to listings

    # A pixel of the picture maps into the model: a wall's pixel lies under a wall, a room's does not.
    unit, (ox, oy, oz) = header["scan"]["unit_per_px"], header["offset"]
    wall = next(e for e in header["elements"] if e["cls"] == "wall")
    outline = Polygon(mesh.footprint(wall, positions, indices))

    def model(px: float, py: float) -> Point:
        return Point(px * unit - ox, (H - py) * unit - oy)

    assert outline.buffer(0.01).contains(model(65, 240)) and outline.buffer(0.01).contains(model(320, 65))
    top = max(positions[2::3])
    assert top == pytest.approx(mesh.WALL_HEIGHT - oz, abs=1e-3)   # the walls stand on the sheet's top
    # The sheet spans the whole picture, so the scan laid on it lines up corner to corner.
    xs, ys = positions[0::3], positions[1::3]
    assert min(xs) == pytest.approx(-ox, abs=1e-3) and max(xs) == pytest.approx(W * unit - ox, abs=1e-3)
    assert min(ys) == pytest.approx(-oy, abs=1e-3) and max(ys) == pytest.approx(H * unit - oy, abs=1e-3)


def test_a_scan_whose_walls_cannot_be_found_has_no_3d_view(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(scan_plan, "_known", None)
    path = tmp_path / "empty.png"
    Image.new("L", (W, H), 255).save(path)
    with pytest.raises(mesh.NoScanWalls, match="almost empty"):
        mesh.scan_mesh(path)
    assert scan_plan.verdict_if_known(path) is False


def test_an_image_too_large_to_trace_is_refused_without_being_decoded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(scan_plan, "_known", None)
    monkeypatch.setattr(scan_plan, "MAX_PIXELS", 1000)
    path = tmp_path / "sheet.png"
    solid_plan().save(path)
    monkeypatch.setattr(scan_plan, "read_gray", lambda _path: pytest.fail("the image was decoded"))
    plan = scan_plan.load(path)
    assert not plan["ok"] and "too large" in plan["why"]
