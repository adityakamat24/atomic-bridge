from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from src.api.types import SessionState


class InMemorySessionStore:
    """Single-process session store with TTL-based expiry.

    Production swap: Redis. Stated in design doc.
    """

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = asyncio.Lock()

    async def create(self) -> SessionState:
        async with self._lock:
            now = datetime.now(UTC)
            sid = str(uuid.uuid4())
            state = SessionState(
                session_id=sid,
                created_at=now,
                last_active=now,
            )
            self._sessions[sid] = state
            return state

    async def get(self, session_id: str) -> SessionState | None:
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None
            if datetime.now(UTC) - state.last_active > self._ttl:
                self._sessions.pop(session_id, None)
                return None
            return state

    async def update(self, session_id: str, **fields: object) -> SessionState | None:
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None
            updated = state.model_copy(
                update={**fields, "last_active": datetime.now(UTC)}
            )
            self._sessions[session_id] = updated
            return updated

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def cleanup_expired(self) -> int:
        async with self._lock:
            now = datetime.now(UTC)
            expired = [
                sid
                for sid, st in self._sessions.items()
                if now - st.last_active > self._ttl
            ]
            for sid in expired:
                self._sessions.pop(sid, None)
            return len(expired)

    def __len__(self) -> int:
        return len(self._sessions)
