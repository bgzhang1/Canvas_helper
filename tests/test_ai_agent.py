from __future__ import annotations

import json
import shutil

import httpx
import pytest

from agent import (
    AgentConfig,
    OpenAICompatAgent,
    SkillRegistry,
    build_shell_agent_tools,
    build_course_agent_input,
    build_course_agent_tools,
)


def course_payload() -> dict:
    return {
        "course": {"id": 1, "name": "Software Engineering", "course_code": "CS101", "term_name": "2026 Spring"},
        "announcements": [
            {
                "title": "Deadline reminder",
                "message": "<p>Lab 1 is due next week.</p>",
                "posted_at": "2026-05-28T00:00:00+00:00",
                "author_name": "Teacher",
            }
        ],
        "assignments": [
            {
                "name": "Lab 1",
                "due_at": "2026-06-04T00:00:00+00:00",
                "unlock_at": None,
                "lock_at": None,
                "points_possible": 10,
            }
        ],
        "pages": [{"title": "Home", "body": "<h1>Welcome</h1>", "updated_at": "2026-05-28T00:00:00+00:00"}],
        "files": [
            {
                "display_name": "slides.txt",
                "updated_at": "2026-05-28T00:00:00+00:00",
                "outline": [{"title": "Intro"}],
                "text_excerpt": "Intro slides\nDeadline: next week",
            }
        ],
    }


@pytest.mark.asyncio
async def test_agent_runs_local_tool_call_and_injects_skills() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert "tools" in body
            assert "Active skills" in body["messages"][0]["content"]
            assert "deadline_focus" in body["messages"][0]["content"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "search_course_materials",
                                            "arguments": json.dumps({"query": "deadline", "limit": 3}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "summary": "Found the lab deadline.",
                                    "timeline": [],
                                    "course_outline": [],
                                    "risks": [],
                                    "confidence_notes": [],
                                }
                            ),
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            skills=SkillRegistry.from_text("deadline_focus: Prioritize deadlines and due dates."),
            client=client,
        )
        result = await agent.run(
            system_prompt="Return JSON.",
            user_payload=build_course_agent_input(course_payload()),
            tools=build_course_agent_tools(course_payload()),
            response_format_json=True,
        )

    assert json.loads(result.content)["summary"] == "Found the lab deadline."
    assert result.tools_used == ["search_course_materials"]
    tool_message = next(message for message in requests[1]["messages"] if message["role"] == "tool")
    assert "Deadline reminder" in tool_message["content"]


@pytest.mark.asyncio
async def test_agent_falls_back_when_provider_rejects_tools() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "tools are not supported"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"summary": "No tools.", "timeline": [], "course_outline": [], "risks": [], "confidence_notes": []}),
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            client=client,
        )
        result = await agent.run(
            system_prompt="Return JSON.",
            user_payload=build_course_agent_input(course_payload()),
            tools=build_course_agent_tools(course_payload()),
            response_format_json=True,
        )

    assert result.fallback_without_tools is True
    assert "tools" in requests[0]
    assert "tools" not in requests[1]


@pytest.mark.asyncio
async def test_agent_drops_reasoning_but_keeps_tools_when_rejected() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'reasoning_effort'"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"summary": "ok", "timeline": [], "course_outline": [], "risks": [], "confidence_notes": []}),
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model", reasoning_effort="medium"),
            client=client,
        )
        result = await agent.run(
            system_prompt="Return JSON.",
            user_payload=build_course_agent_input(course_payload()),
            tools=build_course_agent_tools(course_payload()),
            response_format_json=True,
        )

    assert result.fallback_without_tools is False
    assert "reasoning_effort" in requests[0]
    assert "reasoning_effort" not in requests[1]
    assert "tools" in requests[1]


@pytest.mark.asyncio
async def test_agent_falls_back_to_tool_results_when_final_response_is_empty() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "search_course_materials",
                                            "arguments": json.dumps({"query": "deadline", "limit": 3}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": ""}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            client=client,
        )
        result = await agent.run(
            system_prompt="Answer.",
            user_payload=build_course_agent_input(course_payload()),
            tools=build_course_agent_tools(course_payload()),
        )

    assert result.tools_used == ["search_course_materials"]
    assert "(empty response)" not in result.content
    assert "Tool results:" in result.content
    assert "Deadline reminder" in result.content


@pytest.mark.asyncio
async def test_json_agent_fallback_remains_valid_json_when_final_response_is_empty() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "search_course_materials",
                                            "arguments": json.dumps({"query": "deadline", "limit": 3}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": ""}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            client=client,
        )
        result = await agent.run(
            system_prompt="Return JSON.",
            user_payload=build_course_agent_input(course_payload()),
            tools=build_course_agent_tools(course_payload()),
            response_format_json=True,
        )

    parsed = json.loads(result.content)
    assert parsed["summary"].startswith("The model returned an empty final response")
    assert parsed["tool_results"][0]["name"] == "search_course_materials"
    assert "Deadline reminder" in parsed["tool_results"][0]["result_preview"]


@pytest.mark.asyncio
async def test_streaming_agent_falls_back_to_tool_results_when_final_response_is_empty() -> None:
    requests: list[dict] = []

    def sse(*payloads: dict) -> str:
        lines = [f"data: {json.dumps(payload)}\n" for payload in payloads]
        lines.append("data: [DONE]\n")
        return "\n".join(lines)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
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
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "search_course_materials",
                                                "arguments": json.dumps({"query": "deadline", "limit": 3}),
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
            )
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": ""}}]}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            client=client,
        )
        events = [
            event
            async for event in agent.run_stream(
                system_prompt="Answer.",
                user_payload=build_course_agent_input(course_payload()),
                tools=build_course_agent_tools(course_payload()),
            )
        ]

    done = events[-1]
    assert done["type"] == "done"
    assert done["tools_used"] == ["search_course_materials"]
    assert "Tool results:" in done["content"]
    assert "Deadline reminder" in done["content"]


@pytest.mark.asyncio
async def test_agent_recovers_final_answer_when_first_final_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not [m for m in body["messages"] if m.get("tool_calls")]:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "search_course_materials",
                                            "arguments": json.dumps({"query": "deadline", "limit": 3}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        nudged = any("no content" in (m.get("content") or "") for m in body["messages"])
        content = "Lab 1 is due 2026-06-04." if nudged else ""
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            client=client,
        )
        result = await agent.run(
            system_prompt="Answer.",
            user_payload=build_course_agent_input(course_payload()),
            tools=build_course_agent_tools(course_payload()),
        )

    assert result.tools_used == ["search_course_materials"]
    assert result.content == "Lab 1 is due 2026-06-04."
    assert "Tool results:" not in result.content


@pytest.mark.asyncio
async def test_streaming_agent_recovers_final_answer_when_first_final_is_empty() -> None:
    def sse(*payloads: dict) -> str:
        lines = [f"data: {json.dumps(payload)}\n" for payload in payloads]
        lines.append("data: [DONE]\n")
        return "\n".join(lines)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not [m for m in body["messages"] if m.get("tool_calls")]:
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
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "search_course_materials",
                                                "arguments": json.dumps({"query": "deadline", "limit": 3}),
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
            )
        nudged = any("no content" in (m.get("content") or "") for m in body["messages"])
        content = "Lab 1 is due 2026-06-04." if nudged else ""
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": content}}]}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            client=client,
        )
        events = [
            event
            async for event in agent.run_stream(
                system_prompt="Answer.",
                user_payload=build_course_agent_input(course_payload()),
                tools=build_course_agent_tools(course_payload()),
            )
        ]

    done = events[-1]
    assert done["type"] == "done"
    assert done["tools_used"] == ["search_course_materials"]
    assert done["content"] == "Lab 1 is due 2026-06-04."
    assert any(event.get("type") == "delta" and "Lab 1" in event.get("content", "") for event in events)


def test_grep_tool_searches_project_sandbox(tmp_path) -> None:
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "slides.txt").write_text("Intro\nDeadline: next week\n", encoding="utf-8")
    (tmp_path / "course.json").write_text('{"name":"Software Engineering"}', encoding="utf-8")

    grep_tool = next(tool for tool in build_shell_agent_tools(tmp_path) if tool.name == "grep")
    result = grep_tool.handler({"pattern": "deadline", "path": ".", "include": "*.txt"})

    assert result["matches"] == [
        {
            "file": "files/slides.txt",
            "line_number": 2,
            "line": "Deadline: next week",
        }
    ]


def test_grep_tool_can_read_outside_project_sandbox(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    external = tmp_path / "external"
    sandbox.mkdir()
    external.mkdir()
    (external / "outside.txt").write_text("External deadline\n", encoding="utf-8")

    grep_tool = next(tool for tool in build_shell_agent_tools(sandbox) if tool.name == "grep")
    result = grep_tool.handler({"pattern": "deadline", "path": "../external", "include": "*.txt"})

    assert len(result["matches"]) == 1
    assert result["matches"][0]["file"].endswith("external\\outside.txt") or result["matches"][0]["file"].endswith("external/outside.txt")


def test_bash_tool_allows_read_escape_but_blocks_external_writes(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (tmp_path / "outside.txt").write_text("external read\n", encoding="utf-8")
    bash_tool = next(tool for tool in build_shell_agent_tools(tmp_path) if tool.name == "bash")
    sandbox_tool = next(tool for tool in build_shell_agent_tools(sandbox) if tool.name == "bash")

    if shutil.which("bash"):
        sandbox_tool.handler({"command": "cat ../outside.txt"})

    with pytest.raises(ValueError):
        sandbox_tool.handler({"command": "printf hi > ../outside.txt"})

    with pytest.raises(ValueError):
        bash_tool.handler({"command": "rm ../outside.txt"})


def test_bash_tool_can_write_inside_project_sandbox(tmp_path) -> None:
    if not shutil.which("bash"):
        pytest.skip("bash is not available on PATH")
    bash_tool = next(tool for tool in build_shell_agent_tools(tmp_path) if tool.name == "bash")
    if bash_tool.handler({"command": "true"})["exit_code"] != 0:
        pytest.skip("bash is present but not usable")

    result = bash_tool.handler({"command": "mkdir -p scratch && printf hi > scratch/out.txt && cat scratch/out.txt"})

    assert result["exit_code"] == 0
    assert result["stdout"] == "hi"
    assert (tmp_path / "scratch" / "out.txt").read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_agent_sends_reasoning_effort_in_request_body() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model", reasoning_effort="high"),
            client=client,
        )
        result = await agent.run(system_prompt="Answer.", user_payload={"message": "hi"}, tools=None)

    assert result.content == "ok"
    assert requests[0]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_agent_drops_reasoning_effort_when_provider_rejects_it() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "unknown field: reasoning_effort"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model", reasoning_effort="high"),
            client=client,
        )
        result = await agent.run(system_prompt="Answer.", user_payload={"message": "hi"}, tools=None)

    assert result.content == "ok"
    assert requests[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in requests[1]



@pytest.mark.asyncio
async def test_agent_retries_transient_5xx_then_succeeds() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"message": "service unavailable"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(
                base_url="https://llm.example/v1",
                api_key="test",
                model="model",
                max_retries=2,
                retry_base_delay=0.0,
                retry_max_delay=0.0,
            ),
            client=client,
        )
        result = await agent.run(system_prompt="Answer.", user_payload={"message": "hi"}, tools=None)

    assert result.content == "ok"
    assert len(requests) == 2  # one transient failure + one success


@pytest.mark.asyncio
async def test_agent_runs_multiple_tool_calls_in_one_round_preserving_order() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_a",
                                        "type": "function",
                                        "function": {"name": "list_course_materials", "arguments": json.dumps({"section": "assignments"})},
                                    },
                                    {
                                        "id": "call_b",
                                        "type": "function",
                                        "function": {"name": "search_course_materials", "arguments": json.dumps({"query": "deadline"})},
                                    },
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = OpenAICompatAgent(
            AgentConfig(base_url="https://llm.example/v1", api_key="test", model="model"),
            client=client,
        )
        result = await agent.run(
            system_prompt="Answer.",
            user_payload=build_course_agent_input(course_payload()),
            tools=build_course_agent_tools(course_payload()),
        )

    assert result.content == "done"
    assert result.tools_used == ["list_course_materials", "search_course_materials"]
    # Tool result messages are appended in assistant call order, matching tool_call_id order.
    tool_messages = [m for m in requests[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_a", "call_b"]
