from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.llm.base import LLMRateLimitError, LLMToolCallMissingError, LLMTransientError
from src.llm.factory import make_llm_client
from src.llm.retry import with_retry

# ---------- Retry -----------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_returns_immediately_on_success() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    out = await with_retry(fn, base_delay=0.001, jitter=0)
    assert out == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_recovers_from_rate_limit_then_succeeds() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise LLMRateLimitError("slow down")
        return "recovered"

    out = await with_retry(fn, base_delay=0.001, jitter=0)
    assert out == "recovered"
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_retries_transient_errors() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LLMTransientError("upstream timeout")
        return "third time lucky"

    out = await with_retry(fn, max_attempts=3, base_delay=0.001, jitter=0)
    assert out == "third time lucky"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_gives_up_after_max_attempts() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise LLMRateLimitError("perm")

    with pytest.raises(LLMRateLimitError):
        await with_retry(fn, max_attempts=2, base_delay=0.001, jitter=0)
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_does_not_retry_non_retryable_errors() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise LLMToolCallMissingError("model returned text")

    with pytest.raises(LLMToolCallMissingError):
        await with_retry(fn, max_attempts=3, base_delay=0.001, jitter=0)
    assert calls == 1


# ---------- Anthropic client (mocked SDK) -----------------------------------


def _mk_anthropic_client() -> Any:
    from src.llm.anthropic_client import AnthropicClient

    client = AnthropicClient.__new__(AnthropicClient)  # bypass __init__ -> no SDK call
    client.model = "claude-test"
    return client


@pytest.mark.asyncio
async def test_anthropic_tool_call_returns_input_dict() -> None:
    client = _mk_anthropic_client()
    fake_block = MagicMock()
    fake_block.type = "tool_use"
    fake_block.input = {"intent": "lookup", "confidence": 0.9}
    fake_resp = MagicMock(content=[fake_block])

    client._client = MagicMock()  # noqa: SLF001
    client._client.messages.create = AsyncMock(return_value=fake_resp)  # noqa: SLF001

    out = await client.tool_call(
        "what's John's status?",
        {
            "name": "preprocess",
            "description": "x",
            "input_schema": {"type": "object", "properties": {}},
        },
    )
    assert out == {"intent": "lookup", "confidence": 0.9}


@pytest.mark.asyncio
async def test_anthropic_tool_call_raises_when_no_tool_use_block() -> None:
    client = _mk_anthropic_client()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I refuse"
    fake_resp = MagicMock(content=[text_block])

    client._client = MagicMock()  # noqa: SLF001
    client._client.messages.create = AsyncMock(return_value=fake_resp)  # noqa: SLF001

    with pytest.raises(LLMToolCallMissingError):
        await client.tool_call(
            "x",
            {
                "name": "t",
                "description": "x",
                "input_schema": {"type": "object", "properties": {}},
            },
        )


@pytest.mark.asyncio
async def test_anthropic_text_complete_concatenates_text_blocks() -> None:
    client = _mk_anthropic_client()
    b1 = MagicMock()
    b1.type = "text"
    b1.text = "Hello "
    b2 = MagicMock()
    b2.type = "text"
    b2.text = "world"
    fake_resp = MagicMock(content=[b1, b2])

    client._client = MagicMock()  # noqa: SLF001
    client._client.messages.create = AsyncMock(return_value=fake_resp)  # noqa: SLF001

    out = await client.text_complete("hi")
    assert out == "Hello world"


# ---------- OpenAI client (mocked SDK) --------------------------------------


def _mk_openai_client() -> Any:
    from src.llm.openai_client import OpenAIClient

    client = OpenAIClient.__new__(OpenAIClient)
    client.model = "gpt-test"
    return client


@pytest.mark.asyncio
async def test_openai_tool_call_returns_parsed_arguments() -> None:
    client = _mk_openai_client()
    tc = MagicMock()
    tc.function.name = "preprocess"
    tc.function.arguments = '{"intent": "lookup", "confidence": 0.9}'
    msg = MagicMock(tool_calls=[tc])
    choice = MagicMock(message=msg)
    fake_resp = MagicMock(choices=[choice])

    client._client = MagicMock()  # noqa: SLF001
    client._client.chat.completions.create = AsyncMock(return_value=fake_resp)  # noqa: SLF001

    out = await client.tool_call(
        "x",
        {
            "name": "preprocess",
            "description": "x",
            "input_schema": {"type": "object", "properties": {}},
        },
    )
    assert out == {"intent": "lookup", "confidence": 0.9}


@pytest.mark.asyncio
async def test_openai_tool_call_raises_when_no_function_call() -> None:
    client = _mk_openai_client()
    msg = MagicMock(tool_calls=[])
    choice = MagicMock(message=msg)
    fake_resp = MagicMock(choices=[choice])

    client._client = MagicMock()  # noqa: SLF001
    client._client.chat.completions.create = AsyncMock(return_value=fake_resp)  # noqa: SLF001

    with pytest.raises(LLMToolCallMissingError):
        await client.tool_call(
            "x",
            {
                "name": "t",
                "description": "x",
                "input_schema": {"type": "object", "properties": {}},
            },
        )


@pytest.mark.asyncio
async def test_openai_text_complete_returns_message_content() -> None:
    client = _mk_openai_client()
    msg = MagicMock(content="hello world")
    choice = MagicMock(message=msg)
    fake_resp = MagicMock(choices=[choice])

    client._client = MagicMock()  # noqa: SLF001
    client._client.chat.completions.create = AsyncMock(return_value=fake_resp)  # noqa: SLF001

    out = await client.text_complete("hi")
    assert out == "hello world"


# ---------- Same-shape contract -------------------------------------------


@pytest.mark.asyncio
async def test_both_providers_return_same_dict_shape_for_same_tool_schema() -> None:
    """The planner relies on this. Both providers must produce a plain
    dict[str, Any] for the same tool schema."""
    a = _mk_anthropic_client()
    a_block = MagicMock()
    a_block.type = "tool_use"
    a_block.input = {"x": 1, "y": [1, 2]}
    a._client = MagicMock()  # noqa: SLF001
    a._client.messages.create = AsyncMock(return_value=MagicMock(content=[a_block]))  # noqa: SLF001

    o = _mk_openai_client()
    tc = MagicMock()
    tc.function.name = "f"
    tc.function.arguments = '{"x": 1, "y": [1, 2]}'
    o._client = MagicMock()  # noqa: SLF001
    o._client.chat.completions.create = AsyncMock(  # noqa: SLF001
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(tool_calls=[tc]))])
    )

    schema = {"name": "f", "description": "", "input_schema": {"type": "object"}}
    a_out = await a.tool_call("p", schema)
    o_out = await o.tool_call("p", schema)
    assert a_out == o_out == {"x": 1, "y": [1, 2]}


# ---------- Factory --------------------------------------------------------


def _settings(provider: str, *, fallback: bool = False) -> Settings:
    return Settings(
        LLM_PROVIDER=provider,  # type: ignore[arg-type]
        ANTHROPIC_API_KEY="sk-ant-fake",
        OPENAI_API_KEY="sk-oai-fake",
        LLM_ENABLE_FALLBACK=fallback,
    )


def test_factory_returns_anthropic_client_for_anthropic_provider() -> None:
    from src.llm.anthropic_client import AnthropicClient

    client = make_llm_client("planner", _settings("anthropic"))
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-sonnet-4-6"


def test_factory_returns_openai_client_for_openai_provider() -> None:
    from src.llm.openai_client import OpenAIClient

    client = make_llm_client("planner", _settings("openai"))
    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4.1"


def test_factory_role_picks_correct_model() -> None:
    from src.llm.anthropic_client import AnthropicClient

    s = _settings("anthropic")
    p = make_llm_client("planner", s)
    r = make_llm_client("response", s)
    pp = make_llm_client("preprocessor", s)
    assert isinstance(p, AnthropicClient)
    assert p.model == s.MODEL_PLANNER
    assert r.model == s.MODEL_RESPONSE
    assert pp.model == s.MODEL_PREPROCESSOR


# ---------- Fallback wrapping ----------------------------------------------


def test_factory_wraps_with_fallback_when_enabled_and_both_keys_set() -> None:
    from src.llm.fallback import FallbackLLMClient

    client = make_llm_client("planner", _settings("anthropic", fallback=True))
    assert isinstance(client, FallbackLLMClient)


def test_factory_skips_fallback_when_secondary_key_missing() -> None:
    from src.llm.anthropic_client import AnthropicClient

    s = Settings(
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="sk-ant-fake",
        OPENAI_API_KEY=None,
        LLM_ENABLE_FALLBACK=True,
    )
    client = make_llm_client("planner", s)
    assert isinstance(client, AnthropicClient)


@pytest.mark.asyncio
async def test_fallback_routes_to_secondary_on_primary_failure() -> None:
    from src.llm.base import LLMTransientError
    from src.llm.fallback import FallbackLLMClient

    primary = AsyncMock()
    primary.model = "primary-x"
    primary.tool_call.side_effect = LLMTransientError("primary down")
    fallback = AsyncMock()
    fallback.model = "fallback-y"
    fallback.tool_call.return_value = {"answered": "by_fallback"}

    client = FallbackLLMClient(primary=primary, fallback=fallback)
    out = await client.tool_call("hi", {"name": "f", "input_schema": {}})
    assert out == {"answered": "by_fallback"}
    primary.tool_call.assert_awaited_once()
    fallback.tool_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_does_not_trigger_on_tool_call_missing() -> None:
    """LLMToolCallMissingError signals a planner-prompt bug (the model wrote
    text instead of calling the tool). A different model is unlikely to help
    and would mask the real cause. Don't fail over."""
    from src.llm.fallback import FallbackLLMClient

    primary = AsyncMock()
    primary.model = "primary-x"
    primary.tool_call.side_effect = LLMToolCallMissingError("no tool use block")
    fallback = AsyncMock()
    fallback.model = "fallback-y"

    client = FallbackLLMClient(primary=primary, fallback=fallback)
    with pytest.raises(LLMToolCallMissingError):
        await client.tool_call("hi", {"name": "f", "input_schema": {}})
    fallback.tool_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_succeeds_when_primary_works() -> None:
    from src.llm.fallback import FallbackLLMClient

    primary = AsyncMock()
    primary.model = "primary-x"
    primary.tool_call.return_value = {"answered": "by_primary"}
    fallback = AsyncMock()
    fallback.model = "fallback-y"

    client = FallbackLLMClient(primary=primary, fallback=fallback)
    out = await client.tool_call("hi", {"name": "f", "input_schema": {}})
    assert out == {"answered": "by_primary"}
    fallback.tool_call.assert_not_awaited()


# ---------- Real-LLM smoke (env-gated) -------------------------------------


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_real_anthropic_tool_call() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    from src.llm.anthropic_client import AnthropicClient

    client = AnthropicClient(
        model=os.environ.get("MODEL_PREPROCESSOR", "claude-haiku-4-5"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    schema = {
        "name": "echo",
        "description": "Echo the message back",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }
    out = await client.tool_call(
        "Call the echo tool with message='ping'", schema
    )
    assert isinstance(out, dict)
    assert "message" in out


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_real_openai_tool_call() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    from src.llm.openai_client import OpenAIClient

    client = OpenAIClient(
        model=os.environ.get("OPENAI_MODEL_PREPROCESSOR", "gpt-4.1-mini"),
        api_key=os.environ["OPENAI_API_KEY"],
    )
    schema = {
        "name": "echo",
        "description": "Echo the message back",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }
    out = await client.tool_call(
        "Call the echo tool with message='ping'", schema
    )
    assert isinstance(out, dict)
    assert "message" in out


