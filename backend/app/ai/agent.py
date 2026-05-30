from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class AgentConfig:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str = "medium"
    max_tool_rounds: int = 4
    timeout_seconds: float = 120.0


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

    def call(self, name: str, arguments: dict[str, Any]) -> AgentToolEvent:
        tool = self._tools.get(name)
        if not tool:
            return AgentToolEvent(name=name, arguments=arguments, ok=False, error=f"Unknown tool: {name}")
        try:
            return AgentToolEvent(name=name, arguments=arguments, ok=True, result=tool.handler(arguments))
        except Exception as exc:
            return AgentToolEvent(name=name, arguments=arguments, ok=False, error=f"{exc.__class__.__name__}: {exc}")


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
    ):
        self.config = config
        self.skills = skills or SkillRegistry()
        self._client = client

    async def run(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[AgentTool] | None = None,
        response_format_json: bool = False,
        temperature: float = 0.2,
    ) -> AgentRunResult:
        registry = ToolRegistry(tools)
        skill_prompt = self.skills.render_prompt()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(part for part in [system_prompt, skill_prompt] if part)},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        events: list[AgentToolEvent] = []
        tools_enabled = bool(tools)
        fallback_without_tools = False
        reasoning_enabled = bool(self.config.reasoning_effort)

        for _round in range(max(1, self.config.max_tool_rounds)):
            try:
                message = await self._chat_completion(
                    messages,
                    registry.openai_tools() if tools_enabled else None,
                    response_format_json=response_format_json,
                    temperature=temperature,
                    reasoning_effort=self.config.reasoning_effort if reasoning_enabled else None,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {400, 404, 422}:
                    # Degrade in order: drop tools first (most common incompatibility),
                    # then drop reasoning_effort, before giving up.
                    if tools_enabled:
                        tools_enabled = False
                        fallback_without_tools = True
                        continue
                    if reasoning_enabled:
                        reasoning_enabled = False
                        continue
                raise

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return AgentRunResult(
                    content=_message_content(message),
                    tool_events=events,
                    fallback_without_tools=fallback_without_tools,
                )

            messages.append(_assistant_tool_message(message, tool_calls))
            for tool_call in tool_calls:
                if tool_call.get("type") != "function":
                    continue
                function = tool_call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = _parse_tool_arguments(function.get("arguments"))
                event = registry.call(name, arguments)
                events.append(event)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": name,
                        "content": _truncate_json(
                            {
                                "ok": event.ok,
                                "result": event.result,
                                "error": event.error,
                            }
                        ),
                    }
                )

        messages.append(
            {
                "role": "system",
                "content": "Tool budget exhausted. Produce the final answer now without requesting more tools.",
            }
        )
        message = await self._chat_completion(
            messages,
            None,
            response_format_json=response_format_json,
            temperature=temperature,
            reasoning_effort=self.config.reasoning_effort if reasoning_enabled else None,
        )
        return AgentRunResult(
            content=_message_content(message),
            tool_events=events,
            fallback_without_tools=fallback_without_tools,
        )

    async def run_stream(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[AgentTool] | None = None,
        response_format_json: bool = False,
        temperature: float = 0.2,
    ) -> AsyncIterator[dict[str, Any]]:
        registry = ToolRegistry(tools)
        skill_prompt = self.skills.render_prompt()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(part for part in [system_prompt, skill_prompt] if part)},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        events: list[AgentToolEvent] = []
        tools_enabled = bool(tools)
        fallback_without_tools = False
        reasoning_enabled = bool(self.config.reasoning_effort)

        for _round in range(max(1, self.config.max_tool_rounds)):
            content_parts: list[str] = []
            tool_call_parts: dict[int, dict[str, Any]] = {}
            try:
                async for chunk in self._chat_completion_stream(
                    messages,
                    registry.openai_tools() if tools_enabled else None,
                    response_format_json=response_format_json,
                    temperature=temperature,
                    reasoning_effort=self.config.reasoning_effort if reasoning_enabled else None,
                ):
                    delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}) if isinstance(chunk, dict) else {}
                    content_delta = _message_content(delta)
                    if content_delta:
                        content_parts.append(content_delta)
                        yield {"type": "delta", "content": content_delta}
                    _merge_stream_tool_call_parts(tool_call_parts, delta.get("tool_calls") or [])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {400, 404, 422}:
                    # Degrade in order: drop tools first (most common incompatibility),
                    # then drop reasoning_effort, before giving up.
                    if tools_enabled:
                        tools_enabled = False
                        fallback_without_tools = True
                        continue
                    if reasoning_enabled:
                        reasoning_enabled = False
                        continue
                raise

            content = "".join(content_parts)
            tool_calls = _finalize_stream_tool_calls(tool_call_parts)
            if not tool_calls:
                result = AgentRunResult(content=content, tool_events=events, fallback_without_tools=fallback_without_tools)
                yield {
                    "type": "done",
                    "content": result.content,
                    "tools_used": result.tools_used,
                    "tool_events": [_tool_event_payload(event) for event in result.tool_events],
                    "fallback_without_tools": result.fallback_without_tools,
                }
                return

            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tool_call in tool_calls:
                if tool_call.get("type") != "function":
                    continue
                function = tool_call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = _parse_tool_arguments(function.get("arguments"))
                event = registry.call(name, arguments)
                events.append(event)
                yield {"type": "tool", **_tool_event_payload(event)}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": name,
                        "content": _truncate_json(
                            {
                                "ok": event.ok,
                                "result": event.result,
                                "error": event.error,
                            }
                        ),
                    }
                )

        messages.append(
            {
                "role": "system",
                "content": "Tool budget exhausted. Produce the final answer now without requesting more tools.",
            }
        )
        content_parts = []
        async for chunk in self._chat_completion_stream(
            messages,
            None,
            response_format_json=response_format_json,
            temperature=temperature,
            reasoning_effort=self.config.reasoning_effort if reasoning_enabled else None,
        ):
            delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}) if isinstance(chunk, dict) else {}
            content_delta = _message_content(delta)
            if content_delta:
                content_parts.append(content_delta)
                yield {"type": "delta", "content": content_delta}
        result = AgentRunResult(content="".join(content_parts), tool_events=events, fallback_without_tools=fallback_without_tools)
        yield {
            "type": "done",
            "content": result.content,
            "tools_used": result.tools_used,
            "tool_events": [_tool_event_payload(event) for event in result.tool_events],
            "fallback_without_tools": result.fallback_without_tools,
        }

    async def _chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        response_format_json: bool,
        temperature: float,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        base = self.config.base_url.rstrip("/")
        completions_url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format_json:
            body["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort

        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        if self._client:
            response = await self._client.post(completions_url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.post(completions_url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]

    async def _chat_completion_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        response_format_json: bool,
        temperature: float,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        base = self.config.base_url.rstrip("/")
        completions_url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format_json:
            body["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort

        headers = {"Authorization": f"Bearer {self.config.api_key}"}

        if self._client:
            async with self._client.stream("POST", completions_url, headers=headers, json=body) as response:
                response.raise_for_status()
                async for chunk in _iter_sse_json(response):
                    yield chunk
            return

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            async with client.stream("POST", completions_url, headers=headers, json=body) as response:
                response.raise_for_status()
                async for chunk in _iter_sse_json(response):
                    yield chunk


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


def build_course_agent_tools(payload: dict[str, Any], tool_root: str | Path | None = None) -> list[AgentTool]:
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
            handler=lambda args: _search_course_materials(payload, args),
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
    files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    for file_path in files:
        if file_path.name.startswith("."):
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
