from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx


logger = logging.getLogger("canvas_ai_agent")

# Transient HTTP statuses worth retrying (rate limit + transient upstream faults).
# 4xx codes handled by `_degrade_request` (400/404/422) are intentionally excluded.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Replacement marker for older tool results dropped during context compaction.
_COMPACTED_TOOL_CONTENT = '{"note":"[older tool result omitted to fit the context budget]"}'

ToolHandler = Callable[[dict[str, Any]], Any]


class AgentCancelled(RuntimeError):
    """Raised cooperatively when a caller requests cancellation of an agent run."""


@dataclass(frozen=True)
class AgentConfig:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str = "medium"
    max_tool_rounds: int = 4
    timeout_seconds: float = 120.0
    tool_timeout_seconds: float = 30.0
    max_retries: int = 2
    max_empty_retries: int = 1
    retry_base_delay: float = 0.5
    retry_max_delay: float = 8.0
    max_context_chars: int = 400_000


@dataclass
class _RequestFlags:
    """Optional request features that may be dropped progressively on 4xx rejection."""

    tools: bool
    reasoning: bool
    response_format: bool = False
    temperature: bool = True


@dataclass(frozen=True)
class AgentSkill:
    name: str
    instructions: str


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentToolEvent:
    name: str
    arguments: dict[str, Any]
    ok: bool
    result: Any = None
    error: str | None = None


@dataclass
class AgentRunResult:
    content: str
    tool_events: list[AgentToolEvent] = field(default_factory=list)
    fallback_without_tools: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    cancelled: bool = False

    @property
    def tools_used(self) -> list[str]:
        return [event.name for event in self.tool_events if event.ok]


class ToolRegistry:
    def __init__(self, tools: list[AgentTool] | None = None):
        self._tools: dict[str, AgentTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.name):
            raise ValueError(f"Invalid tool name: {tool.name}")
        self._tools[tool.name] = tool

    def openai_tools(self) -> list[dict[str, Any]]:
        return [tool.to_openai_tool() for tool in self._tools.values()]

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def call(self, name: str, arguments: dict[str, Any]) -> AgentToolEvent:
        tool = self._tools.get(name)
        if not tool:
            return AgentToolEvent(name=name, arguments=arguments, ok=False, error=f"Unknown tool: {name}")
        try:
            return AgentToolEvent(name=name, arguments=arguments, ok=True, result=tool.handler(arguments))
        except Exception as exc:
            return AgentToolEvent(name=name, arguments=arguments, ok=False, error=f"{exc.__class__.__name__}: {exc}")

    async def call_async(self, name: str, arguments: dict[str, Any], timeout: float = 30.0) -> AgentToolEvent:
        if name not in self._tools:
            return AgentToolEvent(name=name, arguments=arguments, ok=False, error=f"Unknown tool: {name}")
        try:
            return await asyncio.wait_for(asyncio.to_thread(self.call, name, arguments), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return AgentToolEvent(name=name, arguments=arguments, ok=False, error=f"Tool '{name}' timed out after {timeout:.0f}s")


class SkillRegistry:
    def __init__(self, skills: list[AgentSkill] | None = None):
        self.skills = skills or []

    @classmethod
    def from_text(cls, raw: str | None) -> "SkillRegistry":
        if not raw or not raw.strip():
            return cls()
        blocks = [block.strip() for block in re.split(r"\n\s*\n|(?:^|\n)\s*---+\s*(?:\n|$)", raw) if block.strip()]
        skills: list[AgentSkill] = []
        for index, block in enumerate(blocks, start=1):
            first_line, _, rest = block.partition("\n")
            if ":" in first_line and len(first_line) <= 80:
                name, _, first_instructions = first_line.partition(":")
                instructions = "\n".join(part for part in [first_instructions.strip(), rest.strip()] if part)
                skills.append(AgentSkill(name=_slug_skill_name(name) or f"skill_{index}", instructions=instructions or block))
            else:
                skills.append(AgentSkill(name=f"skill_{index}", instructions=block))
        return cls(skills)

    def render_prompt(self) -> str:
        if not self.skills:
            return ""
        lines = ["Active skills:"]
        for skill in self.skills:
            lines.append(f"- {skill.name}: {skill.instructions}")
        return "\n".join(lines)


class OpenAICompatAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        skills: SkillRegistry | None = None,
        client: httpx.AsyncClient | None = None,
        before_tool_call: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
        after_tool_call: Callable[[AgentToolEvent], AgentToolEvent | None] | None = None,
        transform_context: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ):
        self.config = config
        self.skills = skills or SkillRegistry()
        self._client = client
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call
        self.transform_context = transform_context

    async def run(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[AgentTool] | None = None,
        response_format_json: bool | dict[str, Any] = False,
        temperature: float = 0.2,
        cancel_check: Callable[[], None] | None = None,
    ) -> AgentRunResult:
        registry = ToolRegistry(tools)
        skill_prompt = self.skills.render_prompt()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(part for part in [system_prompt, skill_prompt] if part)},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        events: list[AgentToolEvent] = []
        usage: dict[str, int] = {}
        flags = _RequestFlags(
            tools=bool(tools),
            reasoning=bool(self.config.reasoning_effort),
            response_format=bool(response_format_json),
        )
        fallback_without_tools = False

        try:
            async with self._session_client() as client:
                round_index = 0
                max_tool_rounds = max(1, self.config.max_tool_rounds)
                while round_index < max_tool_rounds:
                    _raise_if_cancelled(cancel_check)
                    logger.debug("agent run round=%d flags=%s", round_index + 1, flags)
                    try:
                        message, round_usage = await self._chat_completion(
                            client,
                            self._prepare_context(messages),
                            registry.openai_tools() if flags.tools else None,
                            response_format_json=response_format_json and flags.response_format,
                            temperature=temperature if flags.temperature else None,
                            reasoning_effort=self.config.reasoning_effort if flags.reasoning else None,
                        )
                    except httpx.HTTPStatusError as exc:
                        degraded = _degrade_request(exc, flags)
                        if degraded is None:
                            logger.error("agent run failed with non-degradable error: %s", exc)
                            raise
                        if flags.tools and not degraded.tools:
                            fallback_without_tools = True
                        logger.warning("agent degrading after status=%s %s->%s", exc.response.status_code, flags, degraded)
                        flags = degraded
                        continue

                    _merge_usage(usage, round_usage)
                    tool_calls = message.get("tool_calls") or []
                    if not tool_calls:
                        content = _message_content(message)
                        if not content.strip():
                            recovered, recovered_usage = await self._recover_empty_final(
                                client, messages, flags,
                                response_format_json=response_format_json,
                                temperature=temperature,
                            )
                            _merge_usage(usage, recovered_usage)
                            content = recovered or _fallback_tool_response(events, response_format_json=response_format_json)
                        return AgentRunResult(content, events, fallback_without_tools, usage)

                    round_index += 1
                    messages.append(_assistant_tool_message(message, tool_calls))
                    _raise_if_cancelled(cancel_check)
                    for tool_call, name, event in await self._dispatch_tool_calls(registry, tool_calls):
                        events.append(event)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id"),
                                "name": name,
                                "content": _truncate_json({"ok": event.ok, "result": event.result, "error": event.error}),
                            }
                        )

                logger.info("agent tool budget exhausted after %d round(s)", max(1, self.config.max_tool_rounds))
                _raise_if_cancelled(cancel_check)
                messages.append(
                    {
                        "role": "system",
                        "content": "Tool budget exhausted. Produce the final answer now without requesting more tools.",
                    }
                )
                message, round_usage = await self._chat_completion(
                    client,
                    self._prepare_context(messages),
                    None,
                    response_format_json=response_format_json and flags.response_format,
                    temperature=temperature if flags.temperature else None,
                    reasoning_effort=self.config.reasoning_effort if flags.reasoning else None,
                )
                _merge_usage(usage, round_usage)
                content = _message_content(message)
                if not content.strip():
                    recovered, recovered_usage = await self._recover_empty_final(
                        client, messages, flags,
                        response_format_json=response_format_json,
                        temperature=temperature,
                    )
                    _merge_usage(usage, recovered_usage)
                    if recovered.strip():
                        content = recovered
        except AgentCancelled:
            logger.info("agent run cancelled by caller")
            return AgentRunResult(
                _fallback_tool_response(events, response_format_json=response_format_json),
                events,
                fallback_without_tools,
                usage,
                cancelled=True,
            )
        return AgentRunResult(
            content if content.strip() else _fallback_tool_response(events, response_format_json=response_format_json),
            events,
            fallback_without_tools,
            usage,
        )

    async def run_stream(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[AgentTool] | None = None,
        response_format_json: bool | dict[str, Any] = False,
        temperature: float = 0.2,
        cancel_check: Callable[[], None] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        registry = ToolRegistry(tools)
        skill_prompt = self.skills.render_prompt()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(part for part in [system_prompt, skill_prompt] if part)},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        events: list[AgentToolEvent] = []
        usage: dict[str, int] = {}
        flags = _RequestFlags(
            tools=bool(tools),
            reasoning=bool(self.config.reasoning_effort),
            response_format=bool(response_format_json),
        )
        fallback_without_tools = False
        content_parts: list[str] = []

        def done_event(content: str, *, cancelled: bool = False) -> dict[str, Any]:
            result = AgentRunResult(
                content if content.strip() else _fallback_tool_response(events, response_format_json=response_format_json),
                events,
                fallback_without_tools,
                usage,
                cancelled=cancelled,
            )
            return {
                "type": "done",
                "content": result.content,
                "tools_used": result.tools_used,
                "tool_events": [_tool_event_payload(event) for event in result.tool_events],
                "fallback_without_tools": result.fallback_without_tools,
                "usage": result.usage,
                "cancelled": result.cancelled,
            }

        try:
            async with self._session_client() as client:
                round_index = 0
                max_tool_rounds = max(1, self.config.max_tool_rounds)
                while round_index < max_tool_rounds:
                    _raise_if_cancelled(cancel_check)
                    content_parts = []
                    tool_call_parts: dict[int, dict[str, Any]] = {}
                    stream_interrupted = False
                    try:
                        async for chunk in self._chat_completion_stream(
                            client,
                            self._prepare_context(messages),
                            registry.openai_tools() if flags.tools else None,
                            response_format_json=response_format_json and flags.response_format,
                            temperature=temperature if flags.temperature else None,
                            reasoning_effort=self.config.reasoning_effort if flags.reasoning else None,
                        ):
                            if isinstance(chunk, dict) and chunk.get("usage"):
                                _merge_usage(usage, chunk["usage"])
                            delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}) if isinstance(chunk, dict) else {}
                            reasoning_delta = _message_reasoning(delta)
                            if reasoning_delta:
                                yield {"type": "thinking", "content": reasoning_delta}
                            content_delta = _message_content(delta)
                            if content_delta:
                                content_parts.append(content_delta)
                                yield {"type": "delta", "content": content_delta}
                            _merge_stream_tool_call_parts(tool_call_parts, delta.get("tool_calls") or [])
                    except httpx.HTTPStatusError as exc:
                        degraded = _degrade_request(exc, flags)
                        if degraded is None:
                            logger.error("agent stream failed with non-degradable error: %s", exc)
                            raise
                        if flags.tools and not degraded.tools:
                            fallback_without_tools = True
                        logger.warning("agent stream degrading after status=%s %s->%s", exc.response.status_code, flags, degraded)
                        flags = degraded
                        continue
                    except (httpx.TransportError, httpx.TimeoutException) as exc:
                        if tool_call_parts:
                            stream_interrupted = True
                            logger.warning("agent stream interrupted after tool call deltas; using received tool calls: %s", exc)
                        elif content_parts or events:
                            logger.warning("agent stream interrupted after partial output; returning partial result: %s", exc)
                            yield done_event("".join(content_parts))
                            return
                        else:
                            raise

                    content = "".join(content_parts)
                    tool_calls = _finalize_stream_tool_calls(tool_call_parts)
                    if not tool_calls:
                        if stream_interrupted and not content.strip():
                            yield done_event("The model stream was interrupted before a complete answer was received.")
                            return
                        if not content.strip():
                            recovered: list[str] = []
                            async for event in self._recover_empty_final_stream(
                                client, messages, flags,
                                response_format_json=response_format_json,
                                temperature=temperature,
                                usage=usage,
                                out=recovered,
                            ):
                                yield event
                            if recovered:
                                yield done_event(recovered[0])
                                return
                        yield done_event(content)
                        return

                    round_index += 1
                    messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                    for tool_call in tool_calls:
                        if tool_call.get("type") != "function":
                            continue
                        function = tool_call.get("function") or {}
                        yield {
                            "type": "tool",
                            "phase": "start",
                            "name": str(function.get("name") or ""),
                            "arguments": _parse_tool_arguments(function.get("arguments")),
                        }
                    _raise_if_cancelled(cancel_check)
                    for tool_call, name, event in await self._dispatch_tool_calls(registry, tool_calls):
                        events.append(event)
                        yield {"type": "tool", "phase": "end", **_tool_event_payload(event)}
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id"),
                                "name": name,
                                "content": _truncate_json({"ok": event.ok, "result": event.result, "error": event.error}),
                            }
                        )

                logger.info("agent stream tool budget exhausted after %d round(s)", max(1, self.config.max_tool_rounds))
                _raise_if_cancelled(cancel_check)
                messages.append(
                    {
                        "role": "system",
                        "content": "Tool budget exhausted. Produce the final answer now without requesting more tools.",
                    }
                )
                content_parts = []
                try:
                    async for chunk in self._chat_completion_stream(
                        client,
                        self._prepare_context(messages),
                        None,
                        response_format_json=response_format_json and flags.response_format,
                        temperature=temperature if flags.temperature else None,
                        reasoning_effort=self.config.reasoning_effort if flags.reasoning else None,
                    ):
                        if isinstance(chunk, dict) and chunk.get("usage"):
                            _merge_usage(usage, chunk["usage"])
                        delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}) if isinstance(chunk, dict) else {}
                        reasoning_delta = _message_reasoning(delta)
                        if reasoning_delta:
                            yield {"type": "thinking", "content": reasoning_delta}
                        content_delta = _message_content(delta)
                        if content_delta:
                            content_parts.append(content_delta)
                            yield {"type": "delta", "content": content_delta}
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    if content_parts or events:
                        logger.warning("agent stream interrupted during final answer; returning partial result: %s", exc)
                        yield done_event("".join(content_parts))
                        return
                    raise
                if not "".join(content_parts).strip():
                    recovered_tail: list[str] = []
                    async for event in self._recover_empty_final_stream(
                        client, messages, flags,
                        response_format_json=response_format_json,
                        temperature=temperature,
                        usage=usage,
                        out=recovered_tail,
                    ):
                        yield event
                    if recovered_tail:
                        yield done_event(recovered_tail[0])
                        return
        except AgentCancelled:
            logger.info("agent stream cancelled by caller")
            yield done_event("".join(content_parts), cancelled=True)
            return
        yield done_event("".join(content_parts))

    @asynccontextmanager
    async def _session_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield a client reused across all rounds of one run to avoid per-round TCP/TLS setup."""
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            yield client

    def _prepare_context(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply an optional context transform, then size-bounded compaction, before sending."""
        if self.transform_context:
            try:
                transformed = self.transform_context(messages)
                if isinstance(transformed, list):
                    messages = transformed
            except Exception as exc:  # a hook must never break the run
                logger.warning("transform_context hook failed, using untransformed context: %s", exc)
        return _compact_messages(messages, self.config.max_context_chars)

    async def _dispatch_tool_calls(
        self, registry: "ToolRegistry", tool_calls: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], str, AgentToolEvent]]:
        """Run a round's function tool calls concurrently, preserving assistant call order.

        Applies (in order): the optional before-hook gate, JSON-schema argument validation,
        execution with timeout, then the optional after-hook override.
        """
        functions = [call for call in tool_calls if call.get("type") == "function"]

        async def run_one(call: dict[str, Any]) -> tuple[dict[str, Any], str, AgentToolEvent]:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = _parse_tool_arguments(function.get("arguments"))
            event = await self._run_single_tool(registry, name, arguments)
            if self.after_tool_call:
                try:
                    overridden = self.after_tool_call(event)
                    if isinstance(overridden, AgentToolEvent):
                        event = overridden
                except Exception as exc:
                    logger.warning("after_tool_call hook failed for %s: %s", name, exc)
            logger.info("agent tool name=%s ok=%s error=%s", name or "?", event.ok, event.error or "-")
            return call, name, event

        return list(await asyncio.gather(*(run_one(call) for call in functions)))

    async def _run_single_tool(self, registry: "ToolRegistry", name: str, arguments: dict[str, Any]) -> AgentToolEvent:
        if self.before_tool_call:
            try:
                decision = self.before_tool_call(name, arguments)
            except Exception as exc:
                logger.warning("before_tool_call hook failed for %s: %s", name, exc)
                decision = None
            if decision and decision.get("block"):
                return AgentToolEvent(name=name, arguments=arguments, ok=False, error=decision.get("reason") or f"Tool '{name}' blocked by policy")
        tool = registry.get(name)
        if tool is not None:
            schema_error = _validate_tool_arguments(tool.parameters, arguments)
            if schema_error:
                return AgentToolEvent(name=name, arguments=arguments, ok=False, error=f"Invalid arguments: {schema_error}")
        return await registry.call_async(name, arguments, timeout=self.config.tool_timeout_seconds)

    def _build_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        response_format_json: bool | dict[str, Any],
        temperature: float | None,
        reasoning_effort: str | None,
        stream: bool,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        url = _completions_url(self.config.base_url)
        body: dict[str, Any] = {"model": self.config.model, "messages": messages}
        if temperature is not None:
            body["temperature"] = temperature
        if stream:
            body["stream"] = True
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format_json:
            if isinstance(response_format_json, dict):
                body["response_format"] = response_format_json
            else:
                body["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        key = (self.config.api_key or "").strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        return url, body, {"Authorization": f"Bearer {key}"}

    async def _sleep_backoff(self, attempt: int, response: httpx.Response | None, *, reason: str) -> None:
        delay = min(self.config.retry_base_delay * (2**attempt), self.config.retry_max_delay)
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            delay = min(max(delay, retry_after), self.config.retry_max_delay)
        delay += random.uniform(0, self.config.retry_base_delay)
        logger.warning("agent retrying chat completion attempt=%d delay=%.2fs reason=%s", attempt + 1, delay, reason)
        await asyncio.sleep(delay)

    async def _chat_completion(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        response_format_json: bool | dict[str, Any],
        temperature: float | None,
        reasoning_effort: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        url, body, headers = self._build_request(
            messages,
            tools,
            response_format_json=response_format_json,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            stream=False,
        )
        attempt = 0
        while True:
            try:
                response = await client.post(url, headers=headers, json=body)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt >= self.config.max_retries:
                    logger.error("agent chat completion network error, giving up after %d retries: %s", attempt, exc)
                    raise
                await self._sleep_backoff(attempt, None, reason=exc.__class__.__name__)
                attempt += 1
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt < self.config.max_retries:
                await response.aread()
                await self._sleep_backoff(attempt, response, reason=f"status {response.status_code}")
                attempt += 1
                continue
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise httpx.HTTPError("Provider returned no choices in chat completion response")
            return choices[0].get("message") or {}, data.get("usage") or {}

    async def _recover_empty_final(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        flags: _RequestFlags,
        *,
        response_format_json: bool | dict[str, Any],
        temperature: float | None,
    ) -> tuple[str, dict[str, Any]]:
        """Re-prompt (without tools) to recover a final answer when the model returns empty content."""
        usage: dict[str, int] = {}
        nudged = list(messages)
        for _ in range(max(0, self.config.max_empty_retries)):
            nudged.append(
                {
                    "role": "system",
                    "content": "Your previous reply had no content. Write the complete final answer now using the tool results above; do not call any tools.",
                }
            )
            logger.warning("agent recovering empty final response with a re-prompt")
            message, round_usage = await self._chat_completion(
                client,
                self._prepare_context(nudged),
                None,
                response_format_json=response_format_json and flags.response_format,
                temperature=temperature if flags.temperature else None,
                reasoning_effort=self.config.reasoning_effort if flags.reasoning else None,
            )
            _merge_usage(usage, round_usage)
            content = _message_content(message)
            if content.strip():
                return content, usage
        return "", usage

    async def _recover_empty_final_stream(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        flags: _RequestFlags,
        *,
        response_format_json: bool | dict[str, Any],
        temperature: float | None,
        usage: dict[str, int],
        out: list[str],
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a no-tool re-prompt to recover a final answer; append recovered text to ``out``."""
        nudged = list(messages)
        for _ in range(max(0, self.config.max_empty_retries)):
            nudged.append(
                {
                    "role": "system",
                    "content": "Your previous reply had no content. Write the complete final answer now using the tool results above; do not call any tools.",
                }
            )
            logger.warning("agent recovering empty final response with a streamed re-prompt")
            parts: list[str] = []
            async for chunk in self._chat_completion_stream(
                client,
                self._prepare_context(nudged),
                None,
                response_format_json=response_format_json and flags.response_format,
                temperature=temperature if flags.temperature else None,
                reasoning_effort=self.config.reasoning_effort if flags.reasoning else None,
            ):
                if isinstance(chunk, dict) and chunk.get("usage"):
                    _merge_usage(usage, chunk["usage"])
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}) if isinstance(chunk, dict) else {}
                reasoning_delta = _message_reasoning(delta)
                if reasoning_delta:
                    yield {"type": "thinking", "content": reasoning_delta}
                content_delta = _message_content(delta)
                if content_delta:
                    parts.append(content_delta)
                    yield {"type": "delta", "content": content_delta}
            if "".join(parts).strip():
                out.append("".join(parts))
                return

    async def _chat_completion_stream(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        response_format_json: bool | dict[str, Any],
        temperature: float | None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        url, body, headers = self._build_request(
            messages,
            tools,
            response_format_json=response_format_json,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            stream=True,
        )
        attempt = 0
        while True:
            yielded = False
            try:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        if response.status_code in _RETRYABLE_STATUS and attempt < self.config.max_retries:
                            await self._sleep_backoff(attempt, response, reason=f"status {response.status_code}")
                            attempt += 1
                            continue
                        response.raise_for_status()
                    async for chunk in _iter_sse_json(response):
                        yielded = True
                        yield chunk
                    return
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                # Only retry before any chunk was emitted; retrying mid-stream would duplicate output.
                if yielded or attempt >= self.config.max_retries:
                    raise
                await self._sleep_backoff(attempt, None, reason=exc.__class__.__name__)
                attempt += 1
                continue


def build_course_agent_input(payload: dict[str, Any], tool_root: str | Path | None = None) -> dict[str, Any]:
    data = {
        "course": payload["course"],
        "inventory": {
            "announcements": len(payload.get("announcements") or []),
            "assignments": len(payload.get("assignments") or []),
            "pages": len(payload.get("pages") or []),
            "files": len(payload.get("files") or []),
        },
        "assignments": payload.get("assignments") or [],
        "recent_announcements": [
            {
                "title": item.get("title"),
                "posted_at": item.get("posted_at"),
                "author_name": item.get("author_name"),
            }
            for item in (payload.get("announcements") or [])[:20]
        ],
        "pages": [
            {
                "title": item.get("title"),
                "updated_at": item.get("updated_at"),
            }
            for item in payload.get("pages") or []
        ],
        "files": [
            {
                "display_name": item.get("display_name"),
                "updated_at": item.get("updated_at"),
                "outline": item.get("outline") or [],
            }
            for item in payload.get("files") or []
        ],
        "tool_guidance": "Use local tools when you need announcement bodies, page bodies, or extracted file text.",
    }
    if tool_root:
        course_workspace = payload.get("agent_workspace_path")
        data["local_tool_workspace"] = {
            "cwd": ".",
            "scope": "project sandbox",
            "outside_sandbox": "read-only filesystem access",
            "tools": ["bash", "grep"],
        }
        if course_workspace:
            try:
                data["local_tool_workspace"]["course_material_path"] = Path(course_workspace).resolve().relative_to(Path(tool_root).resolve()).as_posix()
            except ValueError:
                data["local_tool_workspace"]["course_material_path"] = "."
    return data


def build_course_agent_tools(
    payload: dict[str, Any],
    tool_root: str | Path | None = None,
    search_handler: ToolHandler | None = None,
) -> list[AgentTool]:
    tools = [
        AgentTool(
            name="list_course_materials",
            description="List synced local course materials and counts.",
            parameters={
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["all", "announcements", "assignments", "pages", "files"],
                        "default": "all",
                    }
                },
            },
            handler=lambda args: _list_course_materials(payload, args),
        ),
        AgentTool(
            name="search_course_materials",
            description="Search synced local course text, announcements, pages, assignments, and file excerpts.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text."},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["announcements", "assignments", "pages", "files"]},
                        "description": "Optional source filters.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                "required": ["query"],
            },
            handler=lambda args: search_handler(args) if search_handler else _search_course_materials(payload, args),
        ),
        AgentTool(
            name="get_file_excerpt",
            description="Return the outline and extracted text excerpt for a synced local file by display name.",
            parameters={
                "type": "object",
                "properties": {
                    "display_name": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 200, "maximum": 12000, "default": 4000},
                },
                "required": ["display_name"],
            },
            handler=lambda args: _get_file_excerpt(payload, args),
        ),
        AgentTool(
            name="get_page_body",
            description="Return a locally cached Canvas page body by title.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 200, "maximum": 8000, "default": 3000},
                },
                "required": ["title"],
            },
            handler=lambda args: _get_page_body(payload, args),
        ),
    ]
    if tool_root:
        tools.extend(build_shell_agent_tools(tool_root))
    return tools


def build_shell_agent_tools(root: str | Path) -> list[AgentTool]:
    root_path = Path(root)
    return [
        AgentTool(
            name="bash",
            description=(
                "Run a short bash command inside the project sandbox. "
                "Commands may read any filesystem path but may only write, delete, move, or chmod paths inside the sandbox. "
                "Network commands, nested shells, interpreters, and shell variable expansion are blocked because their side effects cannot be path-checked."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Project-internal bash command to run."},
                    "cwd": {"type": "string", "description": "Optional project-relative working directory.", "default": "."},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "max_output_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 12000},
                },
                "required": ["command"],
            },
            handler=lambda args: _run_bash(root_path, args),
        ),
        AgentTool(
            name="grep",
            description="Search text files. Project sandbox paths and external read-only paths are both allowed.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Text or regular expression to search for."},
                    "path": {"type": "string", "description": "Workspace-relative file or directory to search.", "default": "."},
                    "include": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "Glob pattern(s), for example *.txt or [*.txt, *.json].",
                    },
                    "case_sensitive": {"type": "boolean", "default": False},
                    "regex": {"type": "boolean", "default": False},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "required": ["pattern"],
            },
            handler=lambda args: _grep_workspace(root_path, args),
        ),
    ]


def _slug_skill_name(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_").lower()
    return slug[:64]


def _message_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return ""


def _message_reasoning(message: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _fallback_tool_response(events: list[AgentToolEvent], *, response_format_json: bool | dict[str, Any]) -> str:
    if not events:
        return ""
    successes = [event for event in events if event.ok]
    failures = [event for event in events if not event.ok]
    if response_format_json:
        return json.dumps(
            {
                "summary": "The model returned an empty final response after using local tools.",
                "timeline": [],
                "course_outline": [],
                "risks": [f"{event.name}: {event.error or 'tool failed'}" for event in failures],
                "confidence_notes": [
                    "Fallback response generated from completed tool calls.",
                    *[f"{event.name}: {_preview_tool_result(event.result, 500)}" for event in successes[:5]],
                ],
                "tool_results": [
                    {
                        "name": event.name,
                        "arguments": event.arguments,
                        "result_preview": _preview_tool_result(event.result, 1000),
                    }
                    for event in successes[:8]
                ],
            },
            ensure_ascii=False,
        )

    lines: list[str] = ["I ran the available tools, but the model returned an empty final response."]
    if successes:
        lines.append("")
        lines.append("Tool results:")
        for event in successes[:8]:
            lines.append(f"- {event.name}: {_summarize_tool_result(event.result)}")
        if len(successes) > 8:
            lines.append(f"- {len(successes) - 8} more successful tool call(s) omitted.")
    if failures:
        lines.append("")
        lines.append("Tool errors:")
        for event in failures[:5]:
            lines.append(f"- {event.name}: {event.error or 'tool failed'}")
        if len(failures) > 5:
            lines.append(f"- {len(failures) - 5} more failed tool call(s) omitted.")
    return "\n".join(lines)


def _summarize_tool_result(result: Any) -> str:
    if isinstance(result, list):
        if not result:
            return "No results."
        lines = [f"{len(result)} result(s)."]
        for item in result[:3]:
            lines.append(f"  - {_summarize_tool_item(item)}")
        if len(result) > 3:
            lines.append(f"  - {len(result) - 3} more result(s).")
        return "\n".join(lines)
    return _summarize_tool_item(result)


def _summarize_tool_item(item: Any) -> str:
    if isinstance(item, dict):
        title = item.get("title") or item.get("name") or item.get("display_name") or item.get("file") or item.get("file_id")
        labels = []
        for key in ("course", "source", "status", "exit_code", "due_at", "updated_at"):
            value = item.get(key)
            if value not in (None, ""):
                labels.append(f"{key}={value}")
        prefix = str(title) if title not in (None, "") else "result"
        if labels:
            prefix = f"{prefix} ({', '.join(labels)})"
        text = item.get("snippet") or item.get("text") or item.get("stdout") or item.get("note") or item.get("message")
        if text not in (None, ""):
            return f"{prefix}: {_clean_preview(str(text), 280)}"
        return _clean_preview(_preview_tool_result(item, 500), 500)
    return _clean_preview(str(item), 500)


def _preview_tool_result(result: Any, limit: int) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    return _clean_preview(text, limit)


def _clean_preview(value: str, limit: int) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...[truncated]"


def _assistant_tool_message(message: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": _message_content(message),
        "tool_calls": tool_calls,
    }
    return assistant


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {"raw": str(raw)}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _merge_stream_tool_call_parts(target: dict[int, dict[str, Any]], parts: list[dict[str, Any]]) -> None:
    for part in parts:
        try:
            index = int(part.get("index", len(target)))
        except (TypeError, ValueError):
            index = len(target)
        current = target.setdefault(index, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
        if part.get("id"):
            current["id"] = part["id"]
        if part.get("type"):
            current["type"] = part["type"]
        function = part.get("function") or {}
        current_function = current.setdefault("function", {"name": "", "arguments": ""})
        if function.get("name"):
            current_function["name"] = f"{current_function.get('name') or ''}{function['name']}"
        if function.get("arguments"):
            current_function["arguments"] = f"{current_function.get('arguments') or ''}{function['arguments']}"


def _finalize_stream_tool_calls(parts: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index in sorted(parts):
        item = parts[index]
        function = item.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        calls.append(
            {
                "id": item.get("id") or f"tool_call_{index}",
                "type": item.get("type") or "function",
                "function": {
                    "name": name,
                    "arguments": function.get("arguments") or "{}",
                },
            }
        )
    return calls


def _tool_event_payload(event: AgentToolEvent) -> dict[str, Any]:
    return {
        "name": event.name,
        "arguments": event.arguments,
        "ok": event.ok,
        "result": event.result,
        "error": event.error,
    }


def _degrade_request(exc: httpx.HTTPStatusError, flags: _RequestFlags) -> _RequestFlags | None:
    """Choose which optional request feature to drop after a 4xx rejection.

    Returns the updated flags or ``None`` to re-raise. Prefers the feature named in
    the error body; otherwise drops ``reasoning_effort`` (reasoning-models only),
    then ``response_format``, then ``tools``, then a non-default ``temperature``
    (rejected by some reasoning models), since tools are broadly supported and
    essential to this agent.
    """
    if exc.response.status_code not in {400, 404, 422}:
        return None
    try:
        body = exc.response.text.lower()
    except Exception:
        body = ""
    blames_tools = "tool" in body or "function" in body
    blames_reasoning = "reasoning" in body
    blames_response_format = "response_format" in body or "json" in body or "schema" in body
    blames_temperature = "temperature" in body
    if flags.temperature and blames_temperature:
        return replace(flags, temperature=False)
    if flags.response_format and blames_response_format:
        return replace(flags, response_format=False)
    if flags.reasoning and blames_reasoning:
        return replace(flags, reasoning=False)
    if flags.tools and blames_tools:
        return replace(flags, tools=False)
    if flags.reasoning:
        return replace(flags, reasoning=False)
    if flags.response_format:
        return replace(flags, response_format=False)
    if flags.tools:
        return replace(flags, tools=False)
    if flags.temperature:
        return replace(flags, temperature=False)
    return None


def _raise_if_cancelled(cancel_check: Callable[[], None] | None) -> None:
    """Invoke a cooperative cancel check; normalize truthy/raising signals to AgentCancelled."""
    if cancel_check is None:
        return
    try:
        signalled = cancel_check()
    except AgentCancelled:
        raise
    except Exception as exc:  # treat any raised signal (e.g. SyncCancelled) as cancellation
        raise AgentCancelled(str(exc) or "cancelled") from exc
    if signalled:
        raise AgentCancelled("cancelled")


def _merge_usage(total: dict[str, int], usage: dict[str, Any] | None) -> None:
    """Accumulate OpenAI-style token counts across rounds, ignoring non-numeric fields."""
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total[key] = int(total.get(key, 0) + value)


def _completions_url(base_url: str) -> str:
    """Build a chat-completions URL tolerant of base URLs with/without /v1 or a full path."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return "/v1/chat/completions"
    low = base.lower()
    if low.endswith("/chat/completions"):
        return base
    if low.endswith("/v1") or low.endswith("/openai/v1") or low.endswith("/v1/openai"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _compact_messages(messages: list[dict[str, Any]], budget_chars: int) -> list[dict[str, Any]]:
    """Drop the bodies of older tool results in place when the transcript exceeds the budget.

    Keeps the system prompt, the first user message, and the most recent few messages
    intact so the model retains task framing plus recent evidence.
    """
    if budget_chars <= 0:
        return messages

    def total() -> int:
        return sum(len(message.get("content") or "") for message in messages)

    if total() <= budget_chars:
        return messages
    protected_tail = 4
    upper = max(2, len(messages) - protected_tail)
    for index in range(2, upper):
        message = messages[index]
        if message.get("role") == "tool" and message.get("content") != _COMPACTED_TOOL_CONTENT:
            message["content"] = _COMPACTED_TOOL_CONTENT
            if total() <= budget_chars:
                break
    logger.info("agent compacted context to ~%d chars (budget=%d)", total(), budget_chars)
    return messages


def _validate_tool_arguments(schema: Any, arguments: dict[str, Any]) -> str | None:
    """Lightweight JSON-schema check: required presence, enum membership, structural types.

    Returns an error string, or None when arguments are acceptable. String/number
    types are intentionally lenient because tool handlers coerce them defensively.
    """
    if not isinstance(schema, dict):
        return None
    for key in schema.get("required") or []:
        if key not in arguments or arguments[key] in (None, ""):
            return f"missing required field '{key}'"
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return None
    for key, spec in properties.items():
        if not isinstance(spec, dict) or key not in arguments or arguments[key] is None:
            continue
        value = arguments[key]
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            return f"field '{key}' must be one of {enum}"
        message = _structural_type_error(key, value, spec.get("type"))
        if message:
            return message
    return None


def _structural_type_error(key: str, value: Any, expected: Any) -> str | None:
    if expected == "array" and not isinstance(value, list):
        return f"field '{key}' must be an array"
    if expected == "object" and not isinstance(value, dict):
        return f"field '{key}' must be an object"
    if expected == "boolean" and not isinstance(value, bool):
        return f"field '{key}' must be a boolean"
    return None


def parse_model_json(raw: str | None) -> Any:
    """Parse model output as JSON, tolerating markdown code fences and surrounding prose.

    Raises ``json.JSONDecodeError`` when no JSON object/array can be recovered.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((pos for pos in (text.find("{"), text.find("[")) if pos >= 0), default=-1)
    if start < 0:
        raise json.JSONDecodeError("no JSON value found", text, 0)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])
    raise json.JSONDecodeError("unbalanced JSON value", text, start)


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    """Parse a numeric ``Retry-After`` header (seconds) when the upstream provides one."""
    if response is None:
        return None
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


async def _iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        text = line.strip()
        if not text or not text.startswith("data:"):
            continue
        data = text[5:].strip()
        if data == "[DONE]":
            break
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield parsed


def _truncate_json(value: Any, limit: int = 20_000) -> str:
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _truncate_text(value: str | None, limit: int) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _list_course_materials(payload: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    section = args.get("section") or "all"
    data = {
        "course": payload.get("course"),
        "announcements": [
            {"title": item.get("title"), "posted_at": item.get("posted_at")}
            for item in payload.get("announcements") or []
        ],
        "assignments": payload.get("assignments") or [],
        "pages": [{"title": item.get("title"), "updated_at": item.get("updated_at")} for item in payload.get("pages") or []],
        "files": [
            {
                "display_name": item.get("display_name"),
                "updated_at": item.get("updated_at"),
                "outline": item.get("outline") or [],
            }
            for item in payload.get("files") or []
        ],
    }
    if section == "all":
        return data
    return {section: data.get(section, [])}


def _search_course_materials(payload: dict[str, Any], args: dict[str, Any]) -> list[dict[str, Any]]:
    query = str(args.get("query") or "").strip().lower()
    if not query:
        return []
    sources = set(args.get("sources") or ["announcements", "assignments", "pages", "files"])
    limit = _bounded_int(args.get("limit"), default=8, minimum=1, maximum=20)
    matches: list[dict[str, Any]] = []

    def add(source: str, title: str, body: str, metadata: dict[str, Any] | None = None) -> None:
        if source not in sources or query not in f"{title}\n{body}".lower():
            return
        matches.append(
            {
                "source": source,
                "title": title,
                "snippet": _snippet(body or title, query, 700),
                "metadata": metadata or {},
            }
        )

    for item in payload.get("assignments") or []:
        add("assignments", str(item.get("name") or ""), json.dumps(item, ensure_ascii=False), {"due_at": item.get("due_at")})
    for item in payload.get("announcements") or []:
        add("announcements", str(item.get("title") or ""), str(item.get("message") or ""), {"posted_at": item.get("posted_at")})
    for item in payload.get("pages") or []:
        add("pages", str(item.get("title") or ""), str(item.get("body") or ""), {"updated_at": item.get("updated_at")})
    for item in payload.get("files") or []:
        outline = json.dumps(item.get("outline") or [], ensure_ascii=False)
        body = "\n".join([outline, str(item.get("text_excerpt") or "")])
        add("files", str(item.get("display_name") or ""), body, {"updated_at": item.get("updated_at")})
    return matches[:limit]


def _get_file_excerpt(payload: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    display_name = str(args.get("display_name") or "").strip().lower()
    limit = _bounded_int(args.get("limit"), default=4000, minimum=200, maximum=12000)
    if not display_name:
        raise ValueError("display_name is required")
    for item in payload.get("files") or []:
        name = str(item.get("display_name") or "")
        if name.lower() == display_name or display_name in name.lower():
            return {
                "display_name": name,
                "updated_at": item.get("updated_at"),
                "outline": item.get("outline") or [],
                "text_excerpt": _truncate_text(item.get("text_excerpt"), limit),
            }
    raise ValueError(f"File not found: {display_name}")


def _get_page_body(payload: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip().lower()
    limit = _bounded_int(args.get("limit"), default=3000, minimum=200, maximum=8000)
    if not title:
        raise ValueError("title is required")
    for item in payload.get("pages") or []:
        page_title = str(item.get("title") or "")
        if page_title.lower() == title or title in page_title.lower():
            return {
                "title": page_title,
                "updated_at": item.get("updated_at"),
                "body": _truncate_text(item.get("body"), limit),
            }
    raise ValueError(f"Page not found: {title}")


def _run_bash(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    cwd = _resolve_workspace_path(root, str(args.get("cwd") or "."))
    _validate_bash_command(command, root, cwd)
    bash_path = shutil.which("bash")
    if not bash_path:
        raise RuntimeError("bash executable was not found on PATH")
    timeout = _bounded_int(args.get("timeout_seconds"), default=5, minimum=1, maximum=10)
    max_output = _bounded_int(args.get("max_output_chars"), default=12000, minimum=200, maximum=20000)

    completed = subprocess.run(
        [bash_path, "-lc", command],
        cwd=str(cwd),
        env=_minimal_shell_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "cwd": _workspace_relative(root, cwd),
        "stdout": _truncate_text(completed.stdout, max_output),
        "stderr": _truncate_text(completed.stderr, max_output),
    }


def _grep_workspace(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise ValueError("pattern is required")
    search_path = _resolve_read_path(root, str(args.get("path") or "."))
    if _is_sensitive_read_path(search_path):
        raise ValueError("refusing to read a sensitive path")
    include = _normalize_include_patterns(args.get("include"))
    max_matches = _bounded_int(args.get("max_matches"), default=20, minimum=1, maximum=100)
    case_sensitive = bool(args.get("case_sensitive", False))
    regex = bool(args.get("regex", False))
    flags = 0 if case_sensitive else re.IGNORECASE
    expression = re.compile(pattern if regex else re.escape(pattern), flags)
    matches: list[dict[str, Any]] = []

    for file_path in _iter_workspace_text_files(search_path, include):
        display_path = _display_path(root, file_path)
        try:
            for line_number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if expression.search(line):
                    matches.append(
                        {
                            "file": display_path,
                            "line_number": line_number,
                            "line": _truncate_text(line, 1000),
                        }
                    )
                    if len(matches) >= max_matches:
                        return {"matches": matches, "truncated": True}
        except OSError as exc:
            matches.append({"file": display_path, "line_number": None, "line": f"[read failed: {exc.__class__.__name__}]"})
            if len(matches) >= max_matches:
                return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


_SENSITIVE_READ_MARKERS = (
    "/.ssh", "/.aws", "/.gnupg", "/.gcloud", "/.azure", "/.kube", "/.config/gh",
    "/.docker", "/.npmrc", "/.netrc", "/.git-credentials", "/.pypirc",
    "id_rsa", "id_ed25519", "id_dsa", "credentials", "secret", ".pem", ".env",
)


def _is_sensitive_read_path(value: Any) -> bool:
    """Best-effort guard so prompt-injected content cannot read credential/secret files."""
    text = str(value).replace("\\", "/").lower()
    return any(marker in text for marker in _SENSITIVE_READ_MARKERS)


def _resolve_workspace_path(root: Path, value: str) -> Path:
    root_path = root.resolve()
    candidate = (root_path / (value or ".")).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("path must stay inside the project sandbox") from exc
    return candidate


def _resolve_read_path(root: Path, value: str) -> Path:
    raw = value or "."
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    return (root.resolve() / raw).resolve()


def _workspace_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return "."


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return str(path.resolve())


def _minimal_shell_env() -> dict[str, str]:
    allowed = {"PATH", "SystemRoot", "WINDIR", "COMSPEC", "TMP", "TEMP"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _validate_bash_command(command: str, root: Path, cwd: Path) -> None:
    lowered = command.lower()
    if "$" in command or "`" in command or "<(" in command or ">(" in command:
        raise ValueError("bash command cannot use shell expansion because writes cannot be path-checked")

    blocked_commands = {
        "dd",
        "eval",
        "exec",
        "source",
        "mkfs",
        "mount",
        "sudo",
        "su",
        "curl",
        "wget",
        "ssh",
        "scp",
        "ftp",
        "nc",
        "ncat",
        "telnet",
        "bash",
        "sh",
        "zsh",
        "fish",
        "python",
        "python3",
        "py",
        "pip",
        "node",
        "npm",
        "npx",
        "perl",
        "ruby",
        "php",
        "powershell",
        "pwsh",
        "cmd",
        "ln",
        "tar",
        "unzip",
        "7z",
        "sqlite3",
        "env",
        "printenv",
        "export",
        "set",
    }
    if re.search(r"(^|[\s;&|()])\.($|[\s;&|()])", lowered):
        raise ValueError("bash command uses blocked command: .")
    for name in blocked_commands:
        if re.search(rf"(^|[\s;&|()]){re.escape(name)}($|[\s;&|()])", lowered):
            raise ValueError(f"bash command uses blocked command: {name}")

    tokens = _shell_tokens(command)
    for token in tokens:
        if _is_sensitive_read_path(token):
            raise ValueError(f"bash command may not access a sensitive path: {token}")
    _validate_redirections(tokens, root, cwd)
    _validate_mutating_commands(tokens, root, cwd)


def _shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _validate_redirections(tokens: list[str], root: Path, cwd: Path) -> None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {">", ">>", ">|", "&>"}:
            if index + 1 >= len(tokens):
                raise ValueError("bash redirection is missing a target path")
            _require_sandbox_write_path(tokens[index + 1], root, cwd)
            index += 2
            continue
        for operator in (">>", ">|", "&>", ">"):
            if token.startswith(operator) and len(token) > len(operator):
                _require_sandbox_write_path(token[len(operator) :], root, cwd)
                break
        index += 1


def _validate_mutating_commands(tokens: list[str], root: Path, cwd: Path) -> None:
    for segment in _command_segments(tokens):
        if not segment:
            continue
        command_index = _first_command_index(segment)
        if command_index is None:
            continue
        command = Path(segment[command_index]).name.lower()
        args = segment[command_index + 1 :]
        if command in {"rm", "rmdir", "mkdir", "touch"}:
            for operand in _path_operands(args):
                _require_sandbox_write_path(operand, root, cwd)
        elif command == "mv":
            for operand in _path_operands(args):
                _require_sandbox_write_path(operand, root, cwd)
        elif command == "cp":
            operands = _path_operands(args)
            if operands:
                _require_sandbox_write_path(operands[-1], root, cwd)
        elif command in {"chmod", "chown"}:
            for operand in _path_operands(args[1:]):
                _require_sandbox_write_path(operand, root, cwd)
        elif command == "tee":
            for operand in _path_operands(args):
                _require_sandbox_write_path(operand, root, cwd)
        elif command == "sed" and any(arg == "-i" or arg.startswith("-i") for arg in args):
            for operand in _path_operands(args):
                _require_sandbox_write_path(operand, root, cwd)
        elif command == "find" and any(arg in {"-delete", "-exec", "-execdir"} for arg in args):
            for operand in _path_operands(args[:1]):
                _require_sandbox_write_path(operand, root, cwd)


def _command_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {";", "&&", "||", "|"}:
            if current:
                segments.append(current)
                current = []
            index += 1
            continue
        if token in {">", ">>", ">|", "&>"}:
            index += 2
            continue
        if any(token.startswith(operator) and len(token) > len(operator) for operator in (">>", ">|", "&>", ">")):
            index += 1
            continue
        current.append(token)
        index += 1
    if current:
        segments.append(current)
    return segments


def _first_command_index(segment: list[str]) -> int | None:
    for index, token in enumerate(segment):
        if "=" in token and not token.startswith("=") and "/" not in token and "\\" not in token:
            continue
        return index
    return None


def _path_operands(args: list[str]) -> list[str]:
    operands: list[str] = []
    skip_next = False
    options_with_values = {"-o", "-g", "-m", "-t", "-S", "-D"}
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        if arg in {"<", ">", ">>", ">|", "&>", "&&", "||", "|", ";"}:
            continue
        operands.append(arg)
    return operands


def _require_sandbox_write_path(value: str, root: Path, cwd: Path) -> None:
    resolved = _resolve_command_path(value, root, cwd)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"bash write target must stay inside the project sandbox: {value}") from exc


def _resolve_command_path(value: str, root: Path, cwd: Path) -> Path:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("empty path is not allowed")
    path = Path(cleaned)
    if path.is_absolute():
        return path.resolve()
    return (cwd / path).resolve()


def _normalize_include_patterns(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        patterns = [str(item).strip() for item in value if str(item).strip()]
        if patterns:
            return patterns
    return ["*.txt", "*.json", "*.md", "*.html", "*.htm", "*.csv"]


def _iter_workspace_text_files(path: Path, include: list[str]):
    for file_path in _walk_text_files(path):
        if file_path.name.startswith("."):
            continue
        if _is_sensitive_read_path(file_path):
            continue
        if file_path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".bin", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip"}:
            continue
        if not any(fnmatch.fnmatch(file_path.name, pattern) for pattern in include):
            continue
        try:
            if file_path.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        yield file_path


def _walk_text_files(path: Path):
    if path.is_file():
        yield path
        return
    skip_dirs = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".pytest_cache", ".cache", ".mypy_cache"}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [name for name in dirnames if name not in skip_dirs and not name.startswith(".")]
        for filename in filenames:
            yield Path(dirpath) / filename


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _snippet(text: str, query: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    index = clean.lower().find(query)
    if index < 0:
        return _truncate_text(clean, limit)
    start = max(0, index - limit // 3)
    end = min(len(clean), start + limit)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end] + suffix
