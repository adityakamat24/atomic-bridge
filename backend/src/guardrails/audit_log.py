from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


class AuditLogTamperingError(Exception):
    """Raised by `verify` when the chain breaks."""


class AuditLog:
    """Append-only NDJSON log with sha256 hash chaining.

    Each entry stores `prev_hash` and `this_hash`. `this_hash` is the
    sha256 of (prev_hash + json(entry sans hashes)).

    Single-process safe (filesystem locking via threading.Lock). Production
    swap: WORM-storage + a separate verifier job.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, entry: dict[str, Any]) -> str:
        """Append a new entry. Returns this_hash."""
        with self._lock:
            prev_hash = self._last_hash_locked()
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            payload: dict[str, Any] = {"timestamp": ts, **entry}
            this_hash = _hash_entry(prev_hash, payload)
            payload["prev_hash"] = prev_hash
            payload["this_hash"] = this_hash
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")
            return this_hash

    def verify(self) -> int:
        """Walk the file from genesis and assert each entry hashes correctly.
        Returns the number of entries verified.
        """
        prev_hash = GENESIS_HASH
        count = 0
        for entry in self._iter_entries():
            stored_prev = entry.get("prev_hash")
            stored_this = entry.get("this_hash")
            inner = {k: v for k, v in entry.items() if k not in {"prev_hash", "this_hash"}}
            expected = _hash_entry(prev_hash, inner)
            if stored_prev != prev_hash:
                raise AuditLogTamperingError(
                    f"entry #{count}: prev_hash mismatch"
                )
            if stored_this != expected:
                raise AuditLogTamperingError(
                    f"entry #{count}: this_hash mismatch"
                )
            prev_hash = expected
            count += 1
        return count

    def entries(self) -> list[dict[str, Any]]:
        return list(self._iter_entries())

    # ----- internal -----

    def _last_hash_locked(self) -> str:
        if not self._path.exists() or self._path.stat().st_size == 0:
            return GENESIS_HASH
        last_line = ""
        with self._path.open("rb") as f:
            try:
                f.seek(-2, 2)
                while f.read(1) != b"\n":
                    f.seek(-2, 1)
            except OSError:
                f.seek(0)
            last_line = f.readline().decode("utf-8")
        if not last_line.strip():
            return GENESIS_HASH
        try:
            obj = json.loads(last_line)
        except json.JSONDecodeError:
            return GENESIS_HASH
        return str(obj.get("this_hash", GENESIS_HASH))

    def _iter_entries(self) -> Iterator[dict[str, Any]]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


def _hash_entry(prev_hash: str, entry: dict[str, Any]) -> str:
    serial = json.dumps(entry, sort_keys=True, default=str)
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\n")
    h.update(serial.encode("utf-8"))
    return h.hexdigest()
