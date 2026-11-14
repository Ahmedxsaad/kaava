"""Small symbols on a large sheet: boxed tile by tile, merged and counted by the code."""

from PIL import Image, ImageDraw

from blueprints import tiles
from blueprints.vlm import MockVLM, VLMReply


def test_tiles_cover_the_region_and_overlap():
    plan = tiles.plan(6000, 4238, [300, 600, 4100, 3800])
    assert 4 <= len(plan) <= tiles.MAX_TILES
    assert min(t[0] for t in plan) == 300 and min(t[1] for t in plan) == 600
    assert max(t[2] for t in plan) >= 4100 and max(t[3] for t in plan) >= 3800
    columns = sorted({t[0] for t in plan})
    assert all(b - a < tiles.TILE for a, b in zip(columns, columns[1:]))  # neighbours share a strip
    assert len(tiles.plan(14000, 9902, None)) <= tiles.MAX_TILES             # a huge sheet gets larger tiles, not more
    assert tiles.plan(900, 700, None) == [(0, 0, 900, 700)]


def test_a_symbol_found_in_two_tiles_is_counted_once():
    a = {"bbox": [1300, 100, 1360, 160], "margin": 140, "label": "door"}   # seen whole in one tile
    b = {"bbox": [1302, 102, 1361, 158], "margin": 2, "label": "door"}     # the same door at the other tile's edge
    c = {"bbox": [200, 900, 260, 960], "margin": 200, "label": "door"}
    merged = tiles.merge([a, b, c])
    assert len(merged) == 2 and a in merged and c in merged


class TileBoxer(MockVLM):
    """Boxes every dark square it is shown: one box in the middle of each tile that holds ink."""

    native_boxes = True

    def ask(self, system, images, text, schema):
        self.calls += 1
        assert "one part of a larger sheet" in system
        return VLMReply(data={"items": [{"box_2d": [450, 450, 550, 550], "label": "mark"}], "cannot_tell": False,
                              "confidence": 0.9, "basis": ""}, model="mock", cost_usd=0.01, latency_ms=1.0)


def test_empty_paper_is_not_sent_and_boxes_land_in_sheet_pixels(tmp_path):
    sheet = Image.new("RGB", (3000, 1500), "white")
    ImageDraw.Draw(sheet).rectangle([200, 600, 500, 900], fill="black")   # ink only in the first tile
    path = tmp_path / "sheet.png"
    sheet.save(path)
    vlm = TileBoxer()
    result = tiles.count(vlm, path, "How many marks?")
    assert result["tiles"] >= 2 and result["sent"] == vlm.calls == 1          # the blank tiles cost nothing
    assert result["cost_usd"] == 0.01 and len(result["boxes"]) == 1
    assert result["boxes"][0]["bbox"] == [675.0, 675.0, 825.0, 825.0]         # 45-55% of a 1500-pixel tile at the origin


def test_two_passes_agree_on_a_symbol_or_it_is_set_aside():
    door = lambda x: {"kind": "box", "bbox": [x, 100, x + 50, 150], "label": "door"}
    first, second = [door(100), door(400), door(900)], [door(104), door(398), door(1500)]
    both, once = tiles.agree(first, second)
    assert [b["bbox"][0] for b in both] == [100, 400]                   # boxed in both passes: counted
    assert sorted(b["bbox"][0] for b in once) == [900, 1500]            # boxed in one pass only: set aside
    assert tiles.agree([], [door(1)]) == ([], [door(1)])
