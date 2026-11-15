"""The log of questions asked: written without waiting, read back as live figures, off when not set up."""

import datetime

from blueprints import ledger


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def run(self, sql, **params):
        self.store.append((sql.split()[0], params))
        if sql.startswith("SELECT"):
            when = datetime.datetime(2026, 11, 15, 1, 0, tzinfo=datetime.timezone.utc)
            return [[3, 1, 1, 1, 0.07, 0.016, 2, 0.0, 2, when, when, 2, 1]]
        return []

    def close(self):
        self.store.append(("CLOSE", {}))


def test_without_an_address_nothing_is_logged_and_there_are_no_live_figures(monkeypatch):
    monkeypatch.delenv(ledger.URL_VAR, raising=False)
    monkeypatch.setenv("BLUEPRINTS_OFFLINE", "1")
    ledger.record(drawing="d", question="q", state="check")       # returns at once, does nothing
    assert not ledger.enabled() and ledger.summary() is None


def test_a_question_is_written_by_the_writer_thread_and_the_figures_are_read_back(monkeypatch):
    store = []
    monkeypatch.setenv(ledger.URL_VAR, "postgres://user:secret@host:5432/db")
    monkeypatch.delenv("BLUEPRINTS_OFFLINE", raising=False)
    monkeypatch.setenv("KAAVA_BUILD", "abc1234")
    monkeypatch.setattr(ledger, "_connect", lambda: FakeConnection(store))
    monkeypatch.setattr(ledger, "_summary", (0.0, None))
    ledger.record(drawing="plans/a", source="swiss_dwellings", question="How many doors? " + "x" * 600, state="verified", answer=23,
                  unit="count", engine="DXF geometry", method="dxf-engine:v1", cost_eur=0.0, first_cost_eur=0.0, alone_eur=0.008, latency_ms=4,
                  replayed=False)
    ledger._rows.join()
    insert = next(params for kind, params in store if kind == "INSERT")
    assert insert["state"] == "verified" and insert["answer"] == "23" and insert["app_build"] == "abc1234"
    assert len(insert["question"]) == 500                           # a question is kept to a sensible length
    assert set(insert) == set(ledger.FIELDS) and "ip" not in insert and "user" not in insert   # what was asked, never who asked
    live = ledger.summary()
    assert live["asked"] == 3 and live["verified"] == 1 and live["spent_eur"] == 0.07 and live["kept_in"] == "Aiven for PostgreSQL"
    assert live["shown_again"] == 1                                 # answers shown again are counted apart: they spent nothing
    assert "first_cost_eur" in insert                               # what the inference cost, beside what this request spent
    assert ledger.summary() is live                                 # read from the database at most every 30 seconds


def test_a_database_that_cannot_be_reached_loses_the_row_not_the_answer(monkeypatch):
    def refuse():
        raise OSError("connection refused")

    monkeypatch.setenv(ledger.URL_VAR, "postgres://user:secret@host:5432/db")
    monkeypatch.delenv("BLUEPRINTS_OFFLINE", raising=False)
    monkeypatch.setattr(ledger, "_connect", refuse)
    monkeypatch.setattr(ledger, "_summary", (0.0, None))
    ledger.record(drawing="plans/a", question="q", state="check")   # does not raise
    ledger._rows.join()
    assert ledger.summary() is None and "connection refused" in ledger.last_error
