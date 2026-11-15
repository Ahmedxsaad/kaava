"""A blind trial: one question on one sheet, keyed by a person before the system answers.

The answer key of our own benchmark (examples/stoa/questions.jsonl) covers what a person can read
off a sheet in a minute. It leaves out the tasks the demo shows off: a count of every door on a
sheet, an area traced on the sheet, a length of baseboard. A blind trial tests one of those:

  1. A marker who has not seen the system's answer marks each item on the sheet, or enters what
     they measured, and locks that as the key. A locked key cannot be changed.
  2. The system and the vision model alone answer the same question, once each.
  3. The record holds the key's quantity, the marks the system missed and the marks it added,
     refusals, what each answer cost when it was worked out, and how long it took.
  4. Three people who do not know the system check its answer from the evidence on the sheet,
     and are timed.

The page is blind_page.html, served at /blind. One JSON file per trial in cache/blind/. Nothing here
makes up a key or a verdict: every figure in a record was entered by a person or measured by the run.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import statistics
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse

from . import config
from .scoring import score_answer

STORE = config.REPO_ROOT / "cache" / "blind"
PAGE = Path(__file__).with_name("blind_page.html")
VERSION = 1
# What a key can be: the question's type for the scorer, and the unit the quantity is in.
KINDS = {"count": "count", "length": "m", "area": "m2", "volume": "m3"}
# A mark belongs to an item when it lies inside it, or beside it by less than half the item's own size
# and never by more than this share of the sheet's long edge. (On a sheet 6,000 px wide that share alone
# would be 90 px, wider than a door is drawn: a mark could then be taken for the door next to its own.)
TOLERANCE = 0.015
MIN_SECONDS, MAX_SECONDS = 1.0, 3600.0
VERDICTS = ("right", "wrong", "cannot_tell")
MAX_MARKS = 3000
WAIT_S = 40.0  # one request waits this long for the run, then says "still working" (as /api/ask does)
KAAVA_LIMIT_S = 20 * 60.0
ID = re.compile(r"^[A-Za-z0-9_-]{8,24}$")

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_starts: dict[str, float] = {}  # a verifier's start, by token: the server's own clock on the check


class Refused(Exception):
    """A request the rig turns down, with the HTTP status to answer with."""

    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status, self.why = status, why


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ----------------------------------------------------------------- the key


def clean_key(body: dict, width: int, height: int) -> dict:
    """The key as it is stored, from what the marker sent. Raises Refused when it is not a usable key."""
    by = " ".join(str(body.get("by") or "").split())[:60]
    if not by:
        raise Refused(422, "say who made this key (a name or initials)")
    kind = body.get("kind")
    if kind not in KINDS:
        raise Refused(422, f"a key is one of: {', '.join(KINDS)}")
    if body.get("unseen") is not True:
        raise Refused(422, "the marker has to confirm they have not seen Kaava's answer to this question on this sheet")
    marks = body.get("marks") or []
    if not isinstance(marks, list) or len(marks) > MAX_MARKS:
        raise Refused(422, "the marks are not a list of points")
    points = []
    for mark in marks:
        try:
            x, y = float(mark[0]), float(mark[1])
        except (TypeError, ValueError, IndexError):
            raise Refused(422, "a mark is a pair of numbers") from None
        if not (math.isfinite(x) and math.isfinite(y)) or not (0 <= x <= width and 0 <= y <= height):
            raise Refused(422, "a mark lies outside the sheet")
        points.append([round(x, 1), round(y, 1)])
    quantity = body.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity < 0:
        raise Refused(422, "the key needs its quantity: a number that is not negative")
    if kind == "count":
        if float(quantity) != int(quantity):
            raise Refused(422, "a count is a whole number")
        quantity = int(quantity)
    else:
        quantity = float(quantity)
    return {"by": by, "kind": kind, "quantity": quantity, "unit": KINDS[kind],
            "how": " ".join(str(body.get("how") or "").split())[:600], "marks": points,
            "unseen": True, "rig_test": bool(body.get("rig_test")), "locked_at": now()}


def lock_key(record: dict, key: dict) -> dict:
    """Put the key on a trial. Once: a key is never replaced, and never set after the system has run."""
    if record.get("run"):
        raise Refused(409, "Kaava has already answered in this trial: a key made now would not be blind")
    if record.get("key"):
        raise Refused(409, "this trial's key is locked and cannot be changed")
    record["key"] = key
    return record


# ----------------------------------------------------------------- marks against evidence


def placed(evidence: list[dict]) -> tuple[list[dict], int]:
    """The counted items of an answer that have a place on the sheet, and how many have none.

    Evidence is in the pixels of the image the page shows (server.Service._evidence). Context items
    and formulas are not counted items. An outline that lies exactly on an earlier one is one place.
    """
    items, unplaced = [], 0
    for item in evidence or []:
        kind = item.get("kind")
        if kind not in ("box", "polygon", "point", "element") or item.get("same_as"):
            continue
        n = item.get("n")
        if item.get("bbox") and len(item["bbox"]) == 4:
            x0, y0, x1, y1 = item["bbox"]
            items.append({"n": n, "shape": "box", "bbox": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]})
        elif item.get("points") and len(item["points"]) >= 3:
            items.append({"n": n, "shape": "polygon", "points": [[float(x), float(y)] for x, y in item["points"]]})
        elif item.get("x") is not None and item.get("y") is not None:
            items.append({"n": n, "shape": "point", "x": float(item["x"]), "y": float(item["y"])})
        else:
            unplaced += 1
    return items, unplaced


def _centre(item: dict) -> tuple[float, float]:
    if item["shape"] == "box":
        x0, y0, x1, y1 = item["bbox"]
        return (x0 + x1) / 2, (y0 + y1) / 2
    if item["shape"] == "polygon":
        return (sum(x for x, _ in item["points"]) / len(item["points"]),
                sum(y for _, y in item["points"]) / len(item["points"]))
    return item["x"], item["y"]


def _to_segment(x: float, y: float, a: list[float], b: list[float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / length))
    return math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy))


def _inside(x: float, y: float, ring: list[list[float]]) -> bool:
    hit = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def _reaches(x: float, y: float, item: dict, tol: float) -> bool:
    """Whether a mark at (x, y) can be a mark of this item: on it, or beside it by at most half its size
    (and at most `tol`). An item shown as a point has no size, so `tol` alone bounds it."""
    if item["shape"] == "box":
        x0, y0, x1, y1 = item["bbox"]
        tol = min(tol, 0.5 * max(x1 - x0, y1 - y0))
        return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
    if item["shape"] == "polygon":
        ring = item["points"]
        xs, ys = [px for px, _ in ring], [py for _, py in ring]
        tol = min(tol, 0.5 * max(max(xs) - min(xs), max(ys) - min(ys)))
        return _inside(x, y, ring) or min(_to_segment(x, y, a, b) for a, b in zip(ring, ring[1:] + ring[:1])) <= tol
    return math.hypot(x - item["x"], y - item["y"]) <= tol


def match_marks(marks: list[list[float]], evidence: list[dict], long_edge: float, tolerance: float = TOLERANCE) -> dict:
    """Pair the marker's marks with the items the answer shows, one to one, as many as can be paired.

    A mark with no item is one the system missed; an item with no mark is one it added. The pairing is
    a maximum matching (each mark tries its nearest free item first, and an item gives way when its
    mark has another), so a crowded corner is not scored worse than it is.
    """
    items, unplaced = placed(evidence)
    tol = tolerance * long_edge
    reach: list[list[int]] = []
    for x, y in marks:
        near = [j for j, item in enumerate(items) if _reaches(x, y, item, tol)]
        near.sort(key=lambda j: math.hypot(x - _centre(items[j])[0], y - _centre(items[j])[1]))
        reach.append(near)
    owner: dict[int, int] = {}  # item -> mark

    def place(i: int, seen: set[int]) -> bool:
        for j in reach[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or place(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in sorted(range(len(marks)), key=lambda i: len(reach[i])):  # the marks with fewest choices first
        place(i, set())
    paired = {i: j for j, i in owner.items()}
    return {"tolerance_px": round(tol, 1), "unplaced": unplaced,
            "matched": [[i, items[j]["n"]] for i, j in sorted(paired.items())],
            "missed": [i for i in range(len(marks)) if i not in paired],
            "extra": [items[j]["n"] for j in range(len(items)) if j not in owner]}


# ----------------------------------------------------------------- the record


def kaava_part(out: dict, key: dict, wall_s: float | None = None) -> dict:
    """What is kept of the system's answer (the response of /api/ask), scored against the key."""
    answer = out.get("answer")
    refused = answer is None
    status = "declined" if refused else score_answer(answer, key["quantity"], key["kind"], gold_unit=key["unit"],
                                                      pred_unit=out.get("unit")).status
    return {"answer": answer, "unit": out.get("unit"), "state": out.get("state"), "refused": refused,
            "reason": out.get("reason") or "", "explanation": out.get("explanation") or "",
            "engine": out.get("engine"), "method": out.get("method"),
            # What the answer cost when it was worked out. A replay of an answer already on file adds nothing now.
            "cost_eur": out.get("cost_eur"), "spent_now_eur": out.get("spent_now_eur"),
            "replayed": bool(out.get("replayed") or out.get("replayed_from")), "replayed_from": out.get("replayed_from"),
            "latency_ms": out.get("latency_ms"), "wall_s": None if wall_s is None else round(wall_s, 1),
            "estimate": out.get("estimate"), "caveat": out.get("caveat"), "evidence": out.get("evidence") or [],
            "trace": out.get("trace") or [], "asked_as": "as the app asks by default (auto)", "status": status}


def baseline_part(answer, unit, key: dict, *, model, cost_eur, latency_ms, basis: str = "", reason: str = "",
                  logged: str | None = None) -> dict:
    """What is kept of the vision model's own answer from the image alone, scored against the key."""
    refused = answer is None
    status = "declined" if refused else score_answer(answer, key["quantity"], key["kind"], gold_unit=key["unit"],
                                                      pred_unit=unit).status
    return {"answer": answer, "unit": unit, "refused": refused, "reason": reason, "basis": basis, "model": model,
            "cost_eur": cost_eur, "latency_ms": latency_ms, "logged_run": logged, "status": status}


def compare(key: dict, kaava: dict, image: dict) -> dict:
    """The system's answer beside the key: the difference, and for a count the marks missed and added."""
    out: dict = {"kind": key["kind"], "key": key["quantity"], "kaava": kaava["answer"], "unit": key["unit"]}
    numeric = isinstance(kaava["answer"], (int, float)) and not isinstance(kaava["answer"], bool)
    if numeric:
        out["difference"] = round(kaava["answer"] - key["quantity"], 4)
        if key["kind"] != "count" and key["quantity"]:
            out["relative"] = round((kaava["answer"] - key["quantity"]) / key["quantity"], 5)
            out["within_2_percent"] = kaava["status"] == "correct"
    if key["kind"] != "count":
        return out
    marks, why = key["marks"], ""
    items, unplaced = placed(kaava["evidence"])
    if not marks:
        why = "the marker gave a count without marking the items"
    elif len(marks) != key["quantity"]:
        why = f"the marker placed {len(marks)} marks for a count of {key['quantity']}, so the marks are not the whole key"
    elif kaava["refused"]:
        why = "Kaava gave no number, so there is nothing to compare mark by mark"
    elif not items:
        why = "Kaava's evidence for this answer has no places on the sheet"
    elif unplaced:
        why = f"{unplaced} of the items Kaava counted have no place on this sheet"
    elif numeric and len(items) != kaava["answer"]:
        why = (f"Kaava's evidence shows {len(items)} places for an answer of {kaava['answer']:g}: "
               "the answer was computed from what is printed, not by boxing each item")
    if why:
        out["marks"] = {"comparable": False, "why": why}
    else:
        out["marks"] = {"comparable": True, **match_marks(marks, kaava["evidence"], max(image["width"], image["height"]))}
    return out


def clean_verification(body: dict, record: dict, server_seconds: float | None = None) -> dict:
    """One person's check of the system's answer, as it is stored."""
    kaava = (record.get("run") or {}).get("kaava")
    if kaava is None:
        raise Refused(409, "Kaava has not answered in this trial yet: there is nothing to check")
    if kaava["refused"]:
        raise Refused(409, "Kaava gave no number in this trial: there is no answer to check")
    by = " ".join(str(body.get("by") or "").split())[:60]
    if not by:
        raise Refused(422, "say who is checking (a name or initials)")
    verdict = body.get("verdict")
    if verdict not in VERDICTS:
        raise Refused(422, f"a verdict is one of: {', '.join(VERDICTS)}")
    seconds = body.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) \
            or not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise Refused(422, "the time taken has to be between one second and one hour")
    agrees = None if verdict == "cannot_tell" or kaava["status"] not in ("correct", "wrong") \
        else (verdict == "right") == (kaava["status"] == "correct")
    return {"by": by, "verdict": verdict, "seconds": round(float(seconds), 1),
            "server_seconds": None if server_seconds is None else round(server_seconds, 1),
            "note": " ".join(str(body.get("note") or "").split())[:400], "agrees_with_key": agrees, "at": now()}


def summary(record: dict) -> dict:
    """The trial in a few figures, worked out from the record each time it is read."""
    run, checks = record.get("run") or {}, record.get("verifications") or []
    kaava, alone = run.get("kaava"), run.get("baseline")
    marks = ((run.get("comparison") or {}).get("marks") or {})
    return {"stage": "verified" if checks else "run" if kaava else "key locked",
            "kaava_status": kaava and kaava["status"], "baseline_status": alone and alone["status"],
            "missed": len(marks["missed"]) if marks.get("comparable") else None,
            "extra": len(marks["extra"]) if marks.get("comparable") else None,
            "verifiers": len(checks),
            "median_seconds": round(statistics.median(c["seconds"] for c in checks), 1) if checks else None,
            "agree_with_key": sum(c["agrees_with_key"] is True for c in checks),
            "disagree_with_key": sum(c["agrees_with_key"] is False for c in checks),
            "could_not_tell": sum(c["verdict"] == "cannot_tell" for c in checks)}


def public(record: dict, view: str = "") -> dict:
    """The record as it may be read. Before the system has answered, the key's content stays hidden;
    a verifier's view never holds the key, the model alone, or what other verifiers said."""
    out = {**record, "summary": summary(record)}
    ran = bool((record.get("run") or {}).get("kaava"))
    if view == "verify":
        kaava = (record.get("run") or {}).get("kaava")
        out = {k: record[k] for k in ("id", "version", "drawing", "title", "question", "image", "created") if k in record}
        out["kaava"] = kaava and {k: kaava[k] for k in ("answer", "unit", "state", "refused", "reason", "explanation",
                                                         "engine", "estimate", "caveat", "evidence", "trace")}
        out["verifiers"] = len(record.get("verifications") or [])
        return out
    if not ran and record.get("key"):
        key = record["key"]
        out["key"] = {"by": key["by"], "kind": key["kind"], "unit": key["unit"], "locked_at": key["locked_at"],
                      "rig_test": key.get("rig_test", False), "withheld": "the quantity and the marks are shown once Kaava has answered"}
    return out


# ----------------------------------------------------------------- storage


def _file(trial: str) -> Path:
    if not ID.match(trial or ""):
        raise Refused(404, "no such trial")
    return STORE / f"{trial}.json"


def load(trial: str) -> dict:
    path = _file(trial)
    if not path.is_file():
        raise Refused(404, "no such trial")
    return json.loads(path.read_text(encoding="utf-8"))


def save(record: dict) -> None:
    path = _file(record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    part.replace(path)


def trials() -> list[dict]:
    """Every trial kept on this machine, the newest first."""
    found = [json.loads(p.read_text(encoding="utf-8")) for p in STORE.glob("*.json")] if STORE.is_dir() else []
    return sorted(found, key=lambda r: r.get("created", ""), reverse=True)


# ----------------------------------------------------------------- the two answers


def _on_file(service, drawing: str, question: str) -> bool | None:
    """Whether a model answer to this very question was already kept when the key was locked."""
    try:
        from . import server

        asked, _ = service._question_for(drawing, question)
        return bool(service.logged["pointing"].get(asked.qid)) or server._kept_answer(asked) is not None
    except Exception:  # the check is a note on the record, never a reason to turn a key down
        return None


def ask_kaava(service, drawing: str, question: str) -> tuple[dict, float]:
    """The system's answer, asked the way the app asks. A long answer is waited for, up to a limit."""
    from .server import AskRequest, StillWorking

    started = time.time()
    while True:
        try:
            return service.ask(AskRequest(drawing=drawing, question=question, auto=True)), time.time() - started
        except StillWorking:
            if time.time() - started > KAAVA_LIMIT_S:
                raise Refused(504, "Kaava took longer than twenty minutes on this question") from None
        except HTTPException as exc:
            raise Refused(exc.status_code, str(exc.detail)) from None


def ask_alone(service, drawing: str, question: str, key: dict, out: dict) -> dict:
    """The vision model's answer from the image alone: the same model the system calls, one call.

    A dataset question whose image-only answer is already in the run log is taken from there (it was
    paid for once and is the same call); anything else is asked now, within the server's spending cap.
    """
    logged = out.get("baseline")
    if logged:
        return baseline_part(logged.get("answer"), None, key, model=logged.get("model"), cost_eur=logged.get("cost_eur"),
                             latency_ms=logged.get("latency_ms"), logged=logged.get("run_id"))
    if os.environ.get("BLUEPRINTS_OFFLINE"):
        raise Refused(503, "this server is offline: the model alone cannot be asked")
    if service.spent_eur >= service.budget_eur:
        raise Refused(402, f"the demo's model budget of EUR {service.budget_eur:.2f} is used up")
    from dotenv import load_dotenv

    from . import baseline
    from .vlm import make_vlm

    load_dotenv(config.REPO_ROOT / ".env")
    if not os.environ.get("GEMINI_API_KEY"):
        raise Refused(503, "no vision-model key is configured on the server")
    vlm = make_vlm("gemini", "gemini-4-carbon", "low")  # the model and setting the system itself calls
    asked, _ = service._question_for(drawing, question)
    ans = baseline.make_engine(vlm)(asked, service.ds)
    service.spent_eur += config.usd_to_eur(ans.cost_usd or 0.0)
    if ans.error:  # the call failed: that is not an answer, and it may be tried again
        raise Refused(503, f"the model alone could not be asked: {str(ans.error)[:160]}")
    return baseline_part(ans.answer, ans.unit, key, model=vlm.label, cost_eur=round(config.usd_to_eur(ans.cost_usd or 0.0), 6),
                         latency_ms=ans.latency_ms, basis=ans.explanation, reason=ans.reason)


def work(service, trial: str, job: dict) -> None:
    """Run what the trial still lacks: the system first, then the model alone. Each is kept as soon
    as it has answered, so neither is ever asked twice."""
    try:
        record = load(trial)
        key = record["key"]
        run = record.get("run") or {}
        out = None
        if "kaava" not in run:
            job["doing"] = "Kaava is answering"
            out, wall = ask_kaava(service, record["drawing"], record["question"])
            run = {"started": job["started"], "kaava": kaava_part(out, key, wall), "asked": out.get("question")}
            run["comparison"] = compare(key, run["kaava"], record["image"])
            run["logged_baseline"] = out.get("baseline")
            record["run"] = run
            save(record)
        if "baseline" not in run:
            job["doing"] = "The vision model alone is answering"
            run["baseline"] = ask_alone(service, record["drawing"], record["question"], key,
                                        out or {"baseline": run.get("logged_baseline")})
            run["finished"] = now()
            record["run"] = run
            save(record)
    except Refused as exc:
        job["failed"] = (exc.status, exc.why)
    except Exception as exc:  # anything else is reported, not swallowed: the page shows it
        job["failed"] = (500, f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        job["done"].set()


# ----------------------------------------------------------------- the API


def mount(app, service) -> None:
    """Register the page and its API on the app (server.create_app)."""

    def guard(call):
        try:
            return call()
        except Refused as exc:
            raise HTTPException(exc.status, exc.why) from None

    @app.get("/blind", include_in_schema=False)
    def blind_page():
        return FileResponse(PAGE, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.post("/api/blind/key")
    async def blind_key(request: Request) -> dict:
        """Lock a key: a new trial. The marker's quantity and marks, before the system is asked."""
        body = await request.json()
        drawing = str(body.get("drawing") or "")
        question = " ".join(str(body.get("question") or "").split())[:300]
        if not service.allowed(drawing):
            raise HTTPException(404, "unknown drawing")
        if len(question) < 3:
            raise HTTPException(422, "the trial needs its question")
        view = service.view(drawing)
        if view.kind == "none":
            raise HTTPException(409, "this drawing has no sheet to mark: a blind trial needs one")

        def make() -> dict:
            key = clean_key(body, view.width, view.height)
            key["kaava_answer_on_file"] = _on_file(service, drawing, question)
            record = {"id": secrets.token_urlsafe(8)[:10], "version": VERSION, "created": now(), "drawing": drawing,
                      "title": service.summary(drawing).get("title", drawing), "question": question,
                      "image": {"width": view.width, "height": view.height,
                                "url": f"/api/image?drawing={quote(drawing, safe='')}"},
                      "verifications": []}
            lock_key(record, key)
            with _lock:
                save(record)
            return public(record)

        return guard(make)

    @app.get("/api/blind/{trial}")
    def blind_record(trial: str, view: str = "") -> dict:
        return guard(lambda: public(load(trial), view))

    @app.post("/api/blind/{trial}/run")
    def blind_run(trial: str, response: Response) -> dict:
        """Ask the system and the model alone, once each. Asking again returns what is on record."""
        record = guard(lambda: load(trial))
        run = record.get("run") or {}
        if "kaava" in run and "baseline" in run:
            return public(record)
        with _lock:
            job = _jobs.get(trial)
            if job is None:
                job = _jobs[trial] = {"done": threading.Event(), "failed": None, "doing": "Starting", "started": now()}
                threading.Thread(target=work, args=(service, trial, job), daemon=True).start()
        if not job["done"].wait(WAIT_S):
            response.status_code = 202
            return {"pending": True, "doing": job["doing"]}
        with _lock:
            _jobs.pop(trial, None)
        if job["failed"] is not None:
            raise HTTPException(*job["failed"])
        return guard(lambda: public(load(trial)))

    @app.post("/api/blind/{trial}/verify/start")
    def blind_verify_start(trial: str) -> dict:
        """A verifier starts looking: the server notes the time, so the check is timed on its clock too."""
        guard(lambda: load(trial))
        token = secrets.token_urlsafe(12)
        with _lock:
            for old in [t for t, at in _starts.items() if time.time() - at > 2 * MAX_SECONDS]:
                del _starts[old]
            _starts[token] = time.time()
        return {"token": token}

    @app.post("/api/blind/{trial}/verify")
    async def blind_verify(trial: str, request: Request) -> dict:
        body = await request.json()

        def add() -> dict:
            with _lock:
                started = _starts.pop(str(body.get("token") or ""), None)
                record = load(trial)
                check = clean_verification(body, record, None if started is None else time.time() - started)
                record.setdefault("verifications", []).append(check)
                save(record)
            return public(record)

        return guard(add)
