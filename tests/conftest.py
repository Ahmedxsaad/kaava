"""A tiny synthetic dataset with the same layout and naming as Stoa's."""

import json

import pytest

from blueprints.data import Dataset

GUID_A = "0AbCdEfGhIjKlMnOpQrStU"  # 22 characters, like an IFC GlobalId
GUID_B = "1ZyXwVuTsRqPoNmLkJiHgF"


def _ifc(guid: str) -> str:
    return (
        "ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
        f"#1=IFCPROJECT('{guid}',#2,'Test project',$,$,$,$,(#3),#4);\nENDSEC;\nEND-ISO-10303-21;\n"
    )


def build_dataset(root, n_plans=12, n_sites=8):
    """Write the dataset to `root` and return the list of question records."""
    from PIL import Image
    import pymupdf

    data = root / "data"
    records = []

    def ask(path, question, answer, unit, qtype):
        records.append({"path": path, "question": question, "answer": answer,
                        "unit": unit, "type": qtype})

    (data / "cubicasa5k").mkdir(parents=True)
    for i in range(n_plans):
        rel = f"data/cubicasa5k/colorful-{100 + i}.png"
        Image.new("RGB", (120, 90), "white").save(root / rel)
        ask(rel, "How many windows are shown on this floor plan?", i % 5, "count", "count")
        ask(rel, "Kuinka monta ikkunaa pohjapiirustuksessa on?", i % 5, "count", "count")

    (data / "swiss_dwellings").mkdir()
    for i in range(n_sites):
        for floor in (1, 2):  # two floors of the same site: one group
            stem = f"data/swiss_dwellings/site{i}_bldg{i}_floor{i * 10 + floor}"
            doc = pymupdf.open()
            page = doc.new_page(width=400, height=300)
            page.insert_text((50, 50), f"Room 1.01  {12.5 + i:.2f} m2")
            doc.save(root / f"{stem}.pdf")
            doc.close()
            (root / f"{stem}.dxf").write_text("0\nEOF\n")
            ask(f"{stem}.pdf", "How many rooms labelled 'Bad' are there on this floor?", 2, "count", "count")
            ask(f"{stem}.pdf", "What is the net floor area of room 1 ('Bad') in m²?", 12.5 + i, "m2", "area")

    (data / "ifc_models").mkdir()
    (data / "ifc_bench").mkdir()
    (root / "data/ifc_models/col-duplex_duplex-a.ifc").write_text(_ifc(GUID_A))
    doc = pymupdf.open()
    doc.new_page(width=300, height=200)
    doc.save(root / "data/ifc_models/col-duplex_duplex-a_plan-01-level-1.pdf")
    doc.close()
    (root / "data/ifc_models/col-other_tower.ifc").write_text(_ifc(GUID_B))
    # The same building as col-duplex, under another name in another source.
    (root / "data/ifc_bench/duplex_arc.ifc").write_text(_ifc(GUID_A))

    ask("data/ifc_models/col-duplex_duplex-a.ifc", "How many doors are there in the model 'Duplex'?", 14, "count", "count")
    ask("data/ifc_models/col-duplex_duplex-a_plan-01-level-1.pdf", "How many doors are shown on the floor plan of 'Level 1'?", 6, "count", "count")
    ask("data/ifc_models/col-other_tower.ifc", "What is the net floor area of the room 'A' on 'L1', in m²?", 33.2, "m2", "area")
    ask("data/ifc_bench/duplex_arc.ifc", "What software was used to create this model?", "Revit 2011", None, "text")

    (data / "opening_design/Sports_Complex/Sheets").mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page(width=600, height=400)
    doc.save(root / "data/opening_design/Sports_Complex/Sheets/A-801 - Door Schedule.pdf")
    doc.close()
    ask("data/opening_design/Sports_Complex/Sheets/A-801 - Door Schedule.pdf",
        "On sheet A-801, how many doors are listed in the door schedule?", 38, "count", "count")

    with open(root / "questions.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return records


@pytest.fixture
def tiny_root(tmp_path):
    root = tmp_path / "junction-dataset-preview"
    root.mkdir()
    build_dataset(root)
    return root


@pytest.fixture
def tiny(tiny_root):
    return Dataset(tiny_root)
