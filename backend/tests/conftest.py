from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.deps import build_container
from src.api.main import app
from src.config import Settings
from src.llm.base import BaseLLMClient

REPO_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# ---------- Scripted LLM (re-used by every API/integration test) ----------


class ScriptedLLM(BaseLLMClient):
    """Returns canned responses in queue order. Push expected responses
    onto the queue from the test body."""

    def __init__(self, model: str = "scripted") -> None:
        super().__init__(model)
        self._tool_q: list[dict[str, Any]] = []
        self._text_q: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []

    def queue_tool(self, *responses: dict[str, Any]) -> None:
        self._tool_q.extend(responses)

    def queue_text(self, *responses: str) -> None:
        self._text_q.extend(responses)

    async def tool_call(  # type: ignore[override]
        self,
        prompt: str,
        tool_schema: dict[str, Any],
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        self.tool_calls.append(
            {"prompt": prompt, "schema": tool_schema["name"], "system": system}
        )
        if self._tool_q:
            return self._tool_q.pop(0)
        return {}

    async def text_complete(  # type: ignore[override]
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        self.text_calls.append({"prompt": prompt, "system": system})
        if self._text_q:
            return self._text_q.pop(0)
        return "(no scripted response)"


# ---------- Deterministic embedder (no model download) -------------------


class _KeywordEmb:
    KEYWORDS = ("vpn", "outlook", "sharepoint", "badge", "laptop", "network", "windows", "drive")

    @property
    def dimension(self) -> int:
        return len(self.KEYWORDS)

    def embed(self, texts: list[str]) -> Any:
        import numpy as np

        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, t in enumerate(texts):
            tl = t.lower()
            for j, kw in enumerate(self.KEYWORDS):
                if kw in tl:
                    out[i, j] = 1.0
            n = float(np.linalg.norm(out[i]))
            if n > 0:
                out[i] /= n
        return out


# ---------- Fixtures -----------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(REPO_DATA_DIR, target)
    return target


@pytest.fixture
def llms() -> dict[str, ScriptedLLM]:
    return {
        "planner": ScriptedLLM("scripted-planner"),
        "preprocessor": ScriptedLLM("scripted-preprocessor"),
        "response": ScriptedLLM("scripted-response"),
    }


@pytest.fixture
def client(
    isolated_data_dir: Path,
    llms: dict[str, ScriptedLLM],
    tmp_path: Path,
) -> Iterator[TestClient]:
    """Builds a test AppContainer with scripted LLMs + keyword embedder so
    no LLM keys / model downloads are needed in tests."""
    settings = Settings(
        DATA_DIR=isolated_data_dir,
        AUDIT_LOG_PATH=tmp_path / "audit.ndjson",
        RATE_LIMIT_PER_MIN=60,
        ANTHROPIC_API_KEY="sk-ant-fake",
        OPENAI_API_KEY="sk-oai-fake",
    )
    container = build_container(
        settings,
        embedding=_KeywordEmb(),
        planner_llm=llms["planner"],
        preprocessor_llm=llms["preprocessor"],
        response_llm=llms["response"],
    )
    app.state.container = container
    with TestClient(app) as c:
        yield c
    app.state.container = None
