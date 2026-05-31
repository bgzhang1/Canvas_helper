"""Extreme-case coverage for the hardened OpenAI-compatible agent.

All network is simulated with httpx.MockTransport (no real provider is configured in
the real .env: OPENAI_COMPAT_* are empty), and the unconfigured "real config" path is
verified end-to-end via the local-fallback analysis.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent import (
    AgentCancelled,
    AgentConfig,
    AgentToolEvent,
    AIAnalysisService,
    AIConfig,
    OpenAICompatAgent,
    parse_model_json,
)
from agent.agent import _completions_url, _compact_messages, _validate_tool_arguments
from backend.app.db import Database, utc_now


FAST_RETRY = dict(max_retries=2, retry_base_delay=0.0, retry_max_delay=0.0)


def _msg(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _tool_call_msg(name: str, arguments: dict, call_id: str = "c1") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
                    ],
                }
            }
        ]
    }


def _agent(handler, **config) -> tuple[OpenAICompatAgent, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = {"base_url": "https://llm.example/v1", "api_key": "test", "model": "m", **config}
    return OpenAICompatAgent(AgentConfig(**cfg), client=client), client


def _course_tool():
    from agent.agent import AgentTool

    return AgentTool(
        name="lookup",
        description="lookup",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "section": {"type": "string", "enum": ["a", "b"]},
                "tags": {"type": "array"},
            },
            "required": ["query"],
        },
        handler=lambda args: {"echo": args.get("query"), "section": args.get("section")},
    )


# --------------------------------------------------------------------------- retries


@pytest.mark.asyncio
async def test_retry_exhausted_raises_after_max_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "down"})

    agent, client = _agent(handler, **FAST_RETRY)
    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            await agent.run(system_prompt="x", user_payload={"m": "hi"})
    assert calls["n"] == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_retry_after_header_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=_msg("ok"))

    agent, client = _agent(handler, **FAST_RETRY)
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"})
    assert result.content == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_network_error_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=_msg("recovered"))

    agent, client = _agent(handler, **FAST_RETRY)
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"})
    assert result.content == "recovered"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_provider_returns_no_choices_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    agent, client = _agent(handler, **FAST_RETRY)
    async with client:
        with pytest.raises(httpx.HTTPError):
            await agent.run(system_prompt="x", user_payload={"m": "hi"})


# --------------------------------------------------------------------------- cancellation


@pytest.mark.asyncio
async def test_cancel_before_first_round_makes_no_requests() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_msg("never"))

    def cancel() -> None:
        raise RuntimeError("user cancelled")

    agent, client = _agent(handler)
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"}, cancel_check=cancel)
    assert result.cancelled is True
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_cancel_between_rounds_returns_partial_with_tool_events() -> None:
    state = {"round": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["round"] += 1
        return httpx.Response(200, json=_tool_call_msg("lookup", {"query": "x"}, "c1"))

    checks = {"n": 0}

    def cancel() -> bool:
        checks["n"] += 1
        return checks["n"] > 2  # allow first round, cancel afterwards

    agent, client = _agent(handler, max_tool_rounds=5)
    async with client:
        result = await agent.run(
            system_prompt="x", user_payload={"m": "hi"}, tools=[_course_tool()], cancel_check=cancel
        )
    assert result.cancelled is True
    assert result.tools_used == ["lookup"]


# --------------------------------------------------------------------------- validation


@pytest.mark.asyncio
async def test_missing_required_argument_is_rejected_without_calling_handler() -> None:
    called = {"hit": False}
    tool = _course_tool()
    original = tool.handler
    tool = tool.__class__(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        handler=lambda a: called.__setitem__("hit", True) or original(a),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if len(body["messages"]) <= 2:
            return httpx.Response(200, json=_tool_call_msg("lookup", {"section": "a"}))  # no query
        return httpx.Response(200, json=_msg("done"))

    agent, client = _agent(handler)
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"}, tools=[tool])
    assert called["hit"] is False
    event = result.tool_events[0]
    assert event.ok is False
    assert "missing required field 'query'" in event.error


@pytest.mark.asyncio
async def test_bad_enum_argument_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if len(body["messages"]) <= 2:
            return httpx.Response(200, json=_tool_call_msg("lookup", {"query": "q", "section": "zzz"}))
        return httpx.Response(200, json=_msg("done"))

    agent, client = _agent(handler)
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"}, tools=[_course_tool()])
    assert result.tool_events[0].ok is False
    assert "must be one of" in result.tool_events[0].error


def test_validate_tool_arguments_unit() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}, "n": {"type": "integer"}, "items": {"type": "array"}},
        "required": ["q"],
    }
    assert _validate_tool_arguments(schema, {"q": "x", "n": 3, "items": []}) is None
    assert _validate_tool_arguments(schema, {"n": 3}) == "missing required field 'q'"
    assert "must be an array" in _validate_tool_arguments(schema, {"q": "x", "items": "no"})
    # lenient on coercible scalar types
    assert _validate_tool_arguments(schema, {"q": "x", "n": "5"}) is None


# --------------------------------------------------------------------------- hooks


@pytest.mark.asyncio
async def test_before_tool_call_can_block() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if len(body["messages"]) <= 2:
            return httpx.Response(200, json=_tool_call_msg("lookup", {"query": "q"}))
        return httpx.Response(200, json=_msg("done"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = OpenAICompatAgent(
        AgentConfig(base_url="https://llm.example/v1", api_key="t", model="m"),
        client=client,
        before_tool_call=lambda name, args: {"block": True, "reason": "policy: lookup disabled"},
    )
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"}, tools=[_course_tool()])
    assert result.tool_events[0].ok is False
    assert "policy: lookup disabled" in result.tool_events[0].error


@pytest.mark.asyncio
async def test_after_tool_call_can_override_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if len(body["messages"]) <= 2:
            return httpx.Response(200, json=_tool_call_msg("lookup", {"query": "q"}))
        return httpx.Response(200, json=_msg("done"))

    def after(event: AgentToolEvent) -> AgentToolEvent:
        return AgentToolEvent(name=event.name, arguments=event.arguments, ok=True, result={"redacted": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = OpenAICompatAgent(
        AgentConfig(base_url="https://llm.example/v1", api_key="t", model="m"),
        client=client,
        after_tool_call=after,
    )
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"}, tools=[_course_tool()])
    assert result.tool_events[0].result == {"redacted": True}


# --------------------------------------------------------------------------- usage


@pytest.mark.asyncio
async def test_usage_is_aggregated_across_rounds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if len(body["messages"]) <= 2:
            msg = _tool_call_msg("lookup", {"query": "q"})
            msg["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            return httpx.Response(200, json=msg)
        return httpx.Response(
            200,
            json={**_msg("done"), "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22}},
        )

    agent, client = _agent(handler)
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"}, tools=[_course_tool()])
    assert result.usage == {"prompt_tokens": 30, "completion_tokens": 7, "total_tokens": 37}


# --------------------------------------------------------------------------- degradation


@pytest.mark.asyncio
async def test_temperature_dropped_when_provider_rejects_it() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "temperature is not supported by this model"}})
        return httpx.Response(200, json=_msg("ok"))

    agent, client = _agent(handler, reasoning_effort="")
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"})
    assert result.content == "ok"
    assert "temperature" in requests[0]
    assert "temperature" not in requests[1]


@pytest.mark.asyncio
async def test_response_format_dropped_when_provider_rejects_json_mode() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "response_format json_object is not supported"}})
        return httpx.Response(200, json=_msg('{"summary": "ok"}'))

    agent, client = _agent(handler, reasoning_effort="")
    async with client:
        result = await agent.run(system_prompt="x", user_payload={"m": "hi"}, response_format_json=True)

    assert result.content == '{"summary": "ok"}'
    assert "response_format" in requests[0]
    assert "response_format" not in requests[1]
    assert "temperature" in requests[1]


@pytest.mark.asyncio
async def test_degradation_retries_do_not_consume_single_tool_round() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert "tools" in body
            assert "response_format" in body
            assert "reasoning_effort" in body
            return httpx.Response(400, json={"error": {"message": "response_format json_object is not supported"}})
        if len(requests) == 2:
            assert "tools" in body
            assert "response_format" not in body
            assert "reasoning_effort" in body
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: reasoning_effort"}})
        if len(requests) == 3:
            assert "tools" in body
            assert "response_format" not in body
            assert "reasoning_effort" not in body
            return httpx.Response(200, json=_tool_call_msg("lookup", {"query": "q"}))
        return httpx.Response(200, json=_msg("done"))

    agent, client = _agent(handler, max_tool_rounds=1, reasoning_effort="high")
    async with client:
        result = await agent.run(
            system_prompt="x",
            user_payload={"m": "hi"},
            tools=[_course_tool()],
            response_format_json=True,
        )

    assert result.content == "done"
    assert result.tools_used == ["lookup"]
    assert len(requests) == 4


# --------------------------------------------------------------------------- url / auth


def test_completions_url_variants() -> None:
    assert _completions_url("https://h/v1") == "https://h/v1/chat/completions"
    assert _completions_url("https://h/v1/") == "https://h/v1/chat/completions"
    assert _completions_url("https://h") == "https://h/v1/chat/completions"
    assert _completions_url("https://h/chat/completions") == "https://h/chat/completions"
    assert _completions_url("https://h/openai/v1") == "https://h/openai/v1/chat/completions"


@pytest.mark.asyncio
async def test_bearer_prefix_in_key_is_not_doubled() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=_msg("ok"))

    agent, client = _agent(handler, api_key="Bearer sk-abc")
    async with client:
        await agent.run(system_prompt="x", user_payload={"m": "hi"})
    assert seen[0] == "Bearer sk-abc"


# --------------------------------------------------------------------------- compaction


def test_compaction_drops_old_tool_bodies_only() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "tool", "content": "X" * 5000},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "Y" * 5000},
        {"role": "assistant", "content": "b"},
        {"role": "tool", "content": "Z" * 50},
        {"role": "assistant", "content": "recent"},
    ]
    _compact_messages(messages, budget_chars=2000)
    assert messages[0]["content"] == "s"
    assert messages[1]["content"] == "u"
    assert messages[2]["content"].startswith('{"note"')  # oldest tool body dropped
    assert messages[-1]["content"] == "recent"  # recent tail protected


# --------------------------------------------------------------------------- json parsing


def test_parse_model_json_handles_fences_and_prose() -> None:
    assert parse_model_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_model_json('Here is the result: {"a": [1, 2], "b": "}"} trailing') == {"a": [1, 2], "b": "}"}
    assert parse_model_json("[1, 2, 3]") == [1, 2, 3]
    with pytest.raises(json.JSONDecodeError):
        parse_model_json("not json at all")


# --------------------------------------------------------------------------- malformed SSE


@pytest.mark.asyncio
async def test_stream_tolerates_noise_keepalive_and_usage_only_chunk() -> None:
    def sse(*payloads) -> str:
        lines = [": keep-alive\n", "\n", "garbage line without prefix\n"]
        lines += [f"data: {json.dumps(p)}\n" for p in payloads]
        lines.append("data: [DONE]\n")
        return "\n".join(lines)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=sse(
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
                {"choices": [], "usage": {"total_tokens": 7}},
            ),
        )

    agent, client = _agent(handler)
    async with client:
        events = [e async for e in agent.run_stream(system_prompt="x", user_payload={"m": "hi"})]
    done = events[-1]
    assert done["type"] == "done"
    assert done["content"] == "Hello"
    assert done["usage"] == {"total_tokens": 7}
    assert done["cancelled"] is False


@pytest.mark.asyncio
async def test_stream_returns_partial_done_when_provider_disconnects_after_content() -> None:
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial answer"}}]}\n\n'
            raise httpx.ReadError("peer closed")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BrokenStream())

    agent, client = _agent(handler, **FAST_RETRY)
    async with client:
        events = [event async for event in agent.run_stream(system_prompt="x", user_payload={"m": "hi"})]

    assert events[0] == {"type": "delta", "content": "Partial answer"}
    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "Partial answer"
    assert events[-1]["cancelled"] is False


@pytest.mark.asyncio
async def test_stream_uses_complete_tool_call_when_provider_disconnects_before_done() -> None:
    requests: list[dict] = []

    class BrokenToolStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            payload = {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": json.dumps({"query": "q"})},
                                }
                            ]
                        }
                    }
                ]
            }
            yield f"data: {json.dumps(payload)}\n\n".encode()
            raise httpx.ReadError("peer closed")

    def sse(*payloads) -> str:
        lines = [f"data: {json.dumps(p)}\n" for p in payloads]
        lines.append("data: [DONE]\n")
        return "\n".join(lines)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, stream=BrokenToolStream())
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "Used lookup"}}]}))

    agent, client = _agent(handler, **FAST_RETRY)
    async with client:
        events = [event async for event in agent.run_stream(system_prompt="x", user_payload={"m": "hi"}, tools=[_course_tool()])]

    assert any(event.get("type") == "tool" and event.get("phase") == "end" and event.get("ok") for event in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "Used lookup"
    assert events[-1]["tools_used"] == ["lookup"]
    assert [message["role"] for message in requests[1]["messages"][-2:]] == ["assistant", "tool"]


@pytest.mark.asyncio
async def test_stream_degradation_retries_do_not_consume_single_tool_round() -> None:
    requests: list[dict] = []

    def sse(*payloads) -> str:
        lines = [f"data: {json.dumps(p)}\n" for p in payloads]
        lines.append("data: [DONE]\n")
        return "\n".join(lines)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert "stream" in body
            assert "tools" in body
            assert "response_format" in body
            return httpx.Response(400, json={"error": {"message": "response_format is not supported for streaming"}})
        if len(requests) == 2:
            assert "tools" in body
            assert "response_format" not in body
            return httpx.Response(
                200,
                text=sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "c1",
                                            "type": "function",
                                            "function": {"name": "lookup", "arguments": json.dumps({"query": "q"})},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
            )
        assert "tools" not in body
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "done"}}]}))

    agent, client = _agent(handler, max_tool_rounds=1, reasoning_effort="")
    async with client:
        events = [
            event
            async for event in agent.run_stream(
                system_prompt="x",
                user_payload={"m": "hi"},
                tools=[_course_tool()],
                response_format_json=True,
            )
        ]

    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "done"
    assert events[-1]["tools_used"] == ["lookup"]
    assert len(requests) == 3


# --------------------------------------------------------------------------- real-config fallback


@pytest.mark.asyncio
async def test_unconfigured_provider_uses_local_fallback(tmp_path) -> None:
    db = Database(tmp_path / "fallback.db")
    db.init()
    now = utc_now()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO courses(id, name, course_code, workflow_state, term_name, raw_json, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "Software Engineering", "CS101", "available", "2026 Spring", "{}", now),
        )
        conn.execute(
            "INSERT INTO assignments(id, course_id, name, due_at, unlock_at, lock_at, workflow_state, points_possible, raw_json, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (10, 1, "Lab 1", "2026-06-04T00:00:00+00:00", None, None, "published", 10.0, "{}", now),
        )

    service = AIAnalysisService(db, AIConfig(base_url=None, api_key=None, model="local-fallback"))
    result = await service.analyze_course(1)

    assert result["model"] == "local-fallback"
    assert any(item["title"] == "Lab 1" for item in result["timeline"])
    assert result["timeline"][0]["confidence"] == "high"
