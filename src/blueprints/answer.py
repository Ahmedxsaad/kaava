"""The one structure every engine returns, and the UI and the scorer consume."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .scoring import CHECK, DECLINED, STATES, VERIFIED


@dataclass
class Answer:
    """An answer with its state, evidence and cost.

    state: "verified" (no need to double-check), "check" (answer + evidence, one
    check is weak) or "declined" (can't answer reliably; `reason` says why).

    Numbers in `answer` must be computed from enumerated objects or geometry. A
    language model may produce `explanation` and parse the question, nothing else.
    The image-only baseline is the one deliberate exception: it exists to be beaten.
    """

    answer: Any | None = None
    unit: str | None = None
    state: str = CHECK
    confidence: float | None = None
    method: str = ""
    evidence: list[dict] = field(default_factory=list)
    explanation: str = ""
    reason: str = ""
    cost_usd: float | None = 0.0
    latency_ms: float | None = None
    meta: dict = field(default_factory=dict)
    # Infrastructure failure (network, rate limit, bad key): not an answer at all.
    # Rows with an error are retried when a run is resumed.
    error: str | None = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown state {self.state!r}")
        if self.answer is None and self.state != DECLINED:
            raise ValueError("no answer means the state must be 'declined'")
        if self.answer is not None and self.state == DECLINED:
            raise ValueError("a declined answer must not carry a value")
        if self.state == VERIFIED and not self.evidence:
            raise ValueError("a verified answer must carry its evidence")

    @classmethod
    def decline(cls, reason: str, *, method: str = "", **kwargs: Any) -> "Answer":
        return cls(answer=None, state=DECLINED, reason=reason, method=method, **kwargs)

    @classmethod
    def failed(cls, error: str, *, method: str = "", **kwargs: Any) -> "Answer":
        return cls(answer=None, state=DECLINED, reason="engine error", error=error,
                   method=method, **kwargs)

    def to_dict(self) -> dict:
        return asdict(self)
