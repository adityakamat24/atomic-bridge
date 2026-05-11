from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock

from src.write_path.proposal import WriteProposal


class InMemoryApprovalStore:
    """Token -> WriteProposal, with TTL-based expiry. Single-process safe.

    Production swap: Redis with TTL (named in design doc).
    """

    def __init__(self) -> None:
        self._proposals: dict[str, WriteProposal] = {}
        self._lock = Lock()

    def put(self, proposal: WriteProposal) -> None:
        with self._lock:
            self._proposals[proposal.token] = proposal

    def get(self, token: str) -> WriteProposal | None:
        with self._lock:
            p = self._proposals.get(token)
            if p is None:
                return None
            if datetime.now(UTC) > p.expires_at:
                self._proposals.pop(token, None)
                return None
            return p

    def delete(self, token: str) -> None:
        with self._lock:
            self._proposals.pop(token, None)

    def cleanup_expired(self) -> int:
        with self._lock:
            now = datetime.now(UTC)
            expired = [t for t, p in self._proposals.items() if now > p.expires_at]
            for t in expired:
                self._proposals.pop(t, None)
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._proposals)
