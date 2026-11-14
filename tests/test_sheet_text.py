"""The texts printed on a sheet: a PDF's own text layer, and the rules for OCR readings."""

import pymupdf

from blueprints import sheet_text


def test_a_pdf_s_texts_come_from_its_text_layer_with_their_places(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet_text.config, "REPO_ROOT", tmp_path)
    path = tmp_path / "schedule.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    for row in range(14):
        page.insert_text((60, 40 + 22 * row), f"10{row}.A", fontsize=10)
        page.insert_text((200, 40 + 22 * row), "3'-0\"", fontsize=10)
    doc.save(path)
    index = sheet_text.read(path)
    assert index["source"] == "pdf" and (index["width"], index["height"]) == (600, 400)
    texts = [item["text"] for item in index["items"]]
    assert texts[:4] == ["100.A", "3'-0\"", "101.A", "3'-0\""]                 # in reading order
    assert [item["id"] for item in index["items"]] == list(range(len(texts)))
    first = index["items"][0]["box"]
    assert 55 < first[0] < 65 and 25 < first[1] < 45
    assert sheet_text.at(index, [50, 25, 120, 45])[0]["text"] == "100.A"
    assert sheet_text.read(path) == index                                      # the second time from the cache


def test_a_figure_is_not_read_upside_down():
    # 600 read the wrong way up is 009, and the OCR is as sure of one as of the other.
    reads = [{"turn": 90, "text": "009", "score": 0.99}, {"turn": 270, "text": "600", "score": 0.97}]
    assert sheet_text._best_reading(reads)["text"] == "600"
    clear = [{"turn": 180, "text": "ENTRANCE", "score": 0.98}, {"turn": 0, "text": "3ONVHLN3", "score": 0.4}]
    assert sheet_text._best_reading(clear)["text"] == "ENTRANCE"               # a clearly better reading still wins


def test_a_text_read_in_two_tiles_is_kept_once():
    twice = [{"text": "27000", "score": 0.99, "turn": 0, "box": [100, 100, 220, 140]},
             {"text": "27000", "score": 0.95, "turn": 0, "box": [101, 101, 221, 141]},
             {"text": "7200", "score": 0.99, "turn": 0, "box": [600, 100, 700, 140]}]
    assert sorted(item["text"] for item in sheet_text._without_repeats(twice)) == ["27000", "7200"]
