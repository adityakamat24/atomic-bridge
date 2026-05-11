from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.data_store import DataStore


@dataclass(frozen=True)
class NameCandidate:
    sys_id: str
    full_name: str
    confidence: float  # 0..1
    match_type: str  # "exact" | "substring" | "tokens" | "fuzzy"


_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def _normalize(s: str) -> str:
    """Lowercase and strip every char that's not [a-z0-9 ]."""
    return _PUNCT_RE.sub("", s.lower())


class NameResolver:
    """Deterministic candidate generator for user name mentions.

    Match tiers, in confidence order:

      1. exact      (normalised equality)                 -> 1.00
      2. substring  (mention appears verbatim in name)    -> 0.85
      3. tokens     (every mention token is a prefix of
                     a distinct name token, in order)     -> 0.75
      4. fuzzy      (Levenshtein distance <= 2 on a part) -> 0.55

    Tier `tokens` is what makes "John D." match "John Doe".
    Tier `fuzzy` catches typos like "Jhon" -> "John".
    """

    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._records: list[dict[str, object]] = []
        self._build_index()

    def _build_index(self) -> None:
        self._records = self._store.find("sys_user", filters=[], limit=10_000)

    def resolve(self, mention: str, top_k: int = 5) -> list[NameCandidate]:
        m = _normalize(mention)
        if not m:
            return []
        m_tokens = m.split()

        candidates: dict[str, NameCandidate] = {}
        for rec in self._records:
            full = str(rec.get("name", ""))
            if not full:
                continue
            sys_id = str(rec["sys_id"])
            full_norm = _normalize(full)
            name_tokens = full_norm.split()

            if full_norm == m:
                candidates[sys_id] = NameCandidate(sys_id, full, 1.0, "exact")
                continue
            if m in full_norm:
                candidates.setdefault(
                    sys_id, NameCandidate(sys_id, full, 0.85, "substring")
                )
                continue
            if _all_tokens_prefix_of_distinct_name_tokens(m_tokens, name_tokens):
                candidates.setdefault(
                    sys_id, NameCandidate(sys_id, full, 0.75, "tokens")
                )
                continue
            # Fuzzy fallback: any mention token within Levenshtein 2 of any name token
            if any(
                _levenshtein(mt, nt) <= 2
                for mt in m_tokens
                for nt in name_tokens
            ):
                candidates.setdefault(
                    sys_id, NameCandidate(sys_id, full, 0.55, "fuzzy")
                )

        ranked = sorted(
            candidates.values(),
            key=lambda c: (-c.confidence, c.full_name),
        )
        return ranked[:top_k]


def _all_tokens_prefix_of_distinct_name_tokens(
    mention_tokens: list[str], name_tokens: list[str]
) -> bool:
    """Greedy left-to-right: each mention token must be a prefix of some
    not-yet-consumed name token. Order matters (so "John D" matches "John Doe"
    but "D John" doesn't match "John Doe")."""
    if not mention_tokens or not name_tokens:
        return False
    used: set[int] = set()
    for mt in mention_tokens:
        matched = False
        for i, nt in enumerate(name_tokens):
            if i in used:
                continue
            if nt.startswith(mt):
                used.add(i)
                matched = True
                break
        if not matched:
            return False
    return True


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            insert = curr[j - 1] + 1
            delete = prev[j] + 1
            subst = prev[j - 1] + (0 if ca == cb else 1)
            curr[j] = min(insert, delete, subst)
        prev = curr
    return prev[-1]
