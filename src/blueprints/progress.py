"""What a long answer is doing right now, for whoever is waiting for it.

Reading a large sheet for the first time takes half a minute; boxing a hundred doors
tile by tile takes a minute. An engine says where it is (`say`), keyed by the question's
id, and the app shows that line while it waits (`get`). Nothing here affects an answer.
"""

from __future__ import annotations

import threading

_notes: dict[str, str] = {}
_lock = threading.Lock()


def say(key: str | None, text: str) -> None:
    if key:
        with _lock:
            _notes[key] = text


def get(key: str) -> str | None:
    with _lock:
        return _notes.get(key)


def clear(key: str | None) -> None:
    if key:
        with _lock:
            _notes.pop(key, None)
