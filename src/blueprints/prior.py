"""Floor baseline: answer without looking at the drawing.

For each question it gives the most common answer that the same question got on
OTHER dev drawings (median for areas, lengths and volumes). It costs nothing and
knows nothing about the drawing, so it measures how far guessing alone goes: many
counts are 0 or 1. Any method that reads the drawing has to clear this floor, and
the vision baseline's accuracy should be read against it, not against zero.

Honesty rules: the prior is built from dev answers only, and never from the
question's own group (leave-one-group-out), so it cannot see the answer it predicts.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Callable, Sequence

from .answer import Answer
from .data import Dataset, Question
from .scoring import CHECK

METHOD = "floor:no-drawing-prior"
MIN_EXACT = 5  # other drawings needed before trusting the exact wording over the template


def make_engine(dev_questions: Sequence[Question]) -> Callable[[Question, Dataset], Answer]:
    by_text: dict[tuple, list[tuple[str, object]]] = defaultdict(list)
    by_template: dict[tuple, list[tuple[str, object]]] = defaultdict(list)
    for q in dev_questions:
        if isinstance(q.answer, (int, float)) and not isinstance(q.answer, bool):
            by_text[(q.source, q.type, q.question)].append((q.group, q.answer))
            by_template[(q.source, q.type, q.template)].append((q.group, q.answer))

    def answer(q: Question, dataset: Dataset) -> Answer:
        common = dict(method=METHOD, cost_usd=0.0, latency_ms=0.0)
        if q.type == "text":
            return Answer.decline("no prior for a free-text question", **common)
        exact = [a for g, a in by_text[(q.source, q.type, q.question)] if g != q.group]
        pool = exact if len(exact) >= MIN_EXACT else [
            a for g, a in by_template[(q.source, q.type, q.template)] if g != q.group
        ]
        if not pool:
            return Answer.decline("this question was not asked about any other dev drawing", **common)
        if q.type == "count":
            counts = Counter(pool)
            top = max(counts.values())
            value = min(v for v, n in counts.items() if n == top)  # ties: the smaller count
            share = top / len(pool)
            note = f"most common answer on {len(pool)} other dev drawings ({share:.0%} of them)"
        else:
            value, share = statistics.median(pool), None
            note = f"median answer on {len(pool)} other dev drawings"
        return Answer(answer=value, state=CHECK, confidence=share, explanation=note, **common)

    return answer
