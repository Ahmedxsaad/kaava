"""The log of questions asked of the app, kept in PostgreSQL (Aiven for PostgreSQL on the public demo).

Each question asked is one row: which drawing, the question, how it ended (verified, check,
declined), the answer, the method, what it cost, how long it took. No one's name, address or
browser is recorded: who asked is not known to the app at all. The Numbers page reads the log
back as live figures: how many questions were asked, how they ended, what was spent.

The log is optional. It is on when KAAVA_PG_URL holds a PostgreSQL address, and never in the
way: a row is handed to a writer thread and the answer goes out without waiting for it; a
database that cannot be reached loses rows, not answers.
"""

from __future__ import annotations

import os
import queue
import ssl
import threading
import time
from urllib.parse import unquote, urlparse

URL_VAR = "KAAVA_PG_URL"
# `cost_eur`: what this request spent (nothing when an answer is shown again). `first_cost_eur`: what working
# the answer out cost, whenever that was; this is the figure to set beside another system's cost.
FIELDS = ("drawing", "source", "question", "state", "answer", "unit", "engine", "method", "cost_eur", "first_cost_eur",
          "alone_eur", "latency_ms", "replayed", "app_build")
TABLE_SQL = """CREATE TABLE IF NOT EXISTS kaava.answers (
    id bigserial PRIMARY KEY,
    asked_at timestamptz NOT NULL DEFAULT now(),
    drawing text NOT NULL, source text, question text NOT NULL,
    state text NOT NULL, answer text, unit text, engine text, method text,
    cost_eur numeric(10,6) NOT NULL DEFAULT 0, alone_eur numeric(10,6),
    latency_ms integer, replayed boolean NOT NULL DEFAULT false, app_build text)"""
# Added after the table was first made; rows from before it have no figure here.
COLUMN_SQL = "ALTER TABLE kaava.answers ADD COLUMN IF NOT EXISTS first_cost_eur numeric(10,6)"
INSERT_SQL = (f"INSERT INTO kaava.answers ({', '.join(FIELDS)}) VALUES ({', '.join(':' + f for f in FIELDS)})")
SUMMARY_SQL = """SELECT count(*), count(*) FILTER (WHERE state = 'verified'), count(*) FILTER (WHERE state = 'check'),
    count(*) FILTER (WHERE state = 'declined'), coalesce(sum(cost_eur) FILTER (WHERE NOT replayed), 0),
    coalesce(sum(alone_eur), 0), count(*) FILTER (WHERE alone_eur IS NOT NULL),
    coalesce(sum(coalesce(first_cost_eur, cost_eur)) FILTER (WHERE alone_eur IS NOT NULL), 0),
    count(DISTINCT drawing), min(asked_at), max(asked_at), count(*) FILTER (WHERE replayed OR cost_eur = 0),
    count(*) FILTER (WHERE replayed)
    FROM kaava.answers"""
SUMMARY_FOR_S = 30.0  # the live figures are read at most this often

_rows: "queue.Queue[dict]" = queue.Queue(maxsize=500)
_lock = threading.Lock()
_writer: threading.Thread | None = None
_summary: tuple[float, dict | None] = (0.0, None)
last_error: str | None = None


def enabled() -> bool:
    if URL_VAR not in os.environ:  # the app may have been started without its .env read yet
        from dotenv import load_dotenv

        from . import config

        load_dotenv(config.REPO_ROOT / ".env")
    return bool(os.environ.get(URL_VAR)) and not os.environ.get("BLUEPRINTS_OFFLINE")


def _connect():
    import pg8000.native

    address = urlparse(os.environ[URL_VAR])
    # As sslmode=require does: the connection is encrypted; the server's certificate (signed by the
    # project's own authority at Aiven) is not checked against the public authorities.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return pg8000.native.Connection(
        user=unquote(address.username or ""), password=unquote(address.password or ""), host=address.hostname,
        port=address.port or 5432, database=(address.path or "/defaultdb").lstrip("/"), ssl_context=context, timeout=10)


def _write_forever() -> None:
    global last_error
    connection = None
    while True:
        row = _rows.get()
        try:
            if connection is None:
                connection = _connect()
                connection.run(TABLE_SQL)
                connection.run(COLUMN_SQL)
            connection.run(INSERT_SQL, **{field: row.get(field) for field in FIELDS})
            last_error = None
        except Exception as exc:  # the log must never take the app down with it
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass
            connection = None
        finally:
            _rows.task_done()


def record(**row) -> None:
    """Log one question asked. Returns at once; never raises."""
    global _writer
    if not enabled():
        return
    with _lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_write_forever, name="ledger", daemon=True)
            _writer.start()
    row["question"] = str(row.get("question") or "")[:500]
    if row.get("answer") is not None:
        row["answer"] = str(row["answer"])[:120]
    row.setdefault("app_build", os.environ.get("KAAVA_BUILD") or None)
    try:
        _rows.put_nowait(row)
    except queue.Full:
        pass


def summary() -> dict | None:
    """The live figures, or None when there is no log. Read from the database at most every 30 seconds."""
    global _summary, last_error
    if not enabled():
        return None
    at, kept = _summary
    if time.time() - at < SUMMARY_FOR_S:
        return kept
    try:
        connection = _connect()
        try:
            connection.run(TABLE_SQL)
            connection.run(COLUMN_SQL)
            (asked, verified, check, declined, spent, alone, compared, spent_compared, drawings, first, last, free,
             again), = connection.run(SUMMARY_SQL)
        finally:
            connection.close()
        kept = {"asked": asked, "verified": verified, "check": check, "declined": declined, "free": free,
                # Money this app spent: an answer shown again spends nothing and is not counted here.
                "spent_eur": round(float(spent), 4), "drawings": drawings, "shown_again": again,
                # Only where the same question was also put to the vision model alone: what the inference of each cost.
                "compared": compared, "spent_compared_eur": round(float(spent_compared), 4), "alone_eur": round(float(alone), 4),
                "since": first.isoformat() if first else None, "latest": last.isoformat() if last else None,
                "kept_in": "Aiven for PostgreSQL"}
        last_error = None
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"[:200]
        kept = None
    _summary = (time.time(), kept)
    return kept
