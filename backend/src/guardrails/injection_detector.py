from __future__ import annotations

import re

# Advisory-only. The Dual LLM defense is the actual protection (05-guardrails.md
# §Defense 3). This detector exists so the audit log can record a heuristic
# score per request — useful for debugging and post-hoc analysis.

_PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
    (
        re.compile(
            r"\bignore\b.{0,20}(previous|prior|above|all|earlier)\b.{0,20}\binstructions?\b",
            re.IGNORECASE,
        ),
        0.85,
        "ignore-previous-instructions",
    ),
    (
        re.compile(
            r"\b(disregard|override)\b.{0,30}\b(rules|constraints?|guidelines?)\b",
            re.IGNORECASE,
        ),
        0.7,
        "disregard-rules",
    ),
    (
        re.compile(r"\byou are now\b", re.IGNORECASE),
        0.55,
        "you-are-now",
    ),
    (
        re.compile(r"\b(reveal|expose|leak|show me)\b.{0,40}\b(prompt|system|instructions?)\b", re.IGNORECASE),
        0.75,
        "reveal-system-prompt",
    ),
    (
        re.compile(r"\b(act|behave) as (a|an) [a-z]+", re.IGNORECASE),
        0.55,
        "act-as-other",
    ),
    (
        re.compile(r"<\|[a-z_]+\|>", re.IGNORECASE),
        0.5,
        "special-token-mimic",
    ),
)


class InjectionDetector:
    """Returns a 0..1 score and the list of matching pattern names. Never
    raises, never blocks — just observes."""

    def score(self, text: str) -> float:
        max_score = 0.0
        for pat, weight, _ in _PATTERNS:
            if pat.search(text):
                max_score = max(max_score, weight)
        return min(1.0, max_score)

    def matches(self, text: str) -> list[str]:
        names: list[str] = []
        for pat, _, name in _PATTERNS:
            if pat.search(text):
                names.append(name)
        return names
