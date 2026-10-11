import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from daybook.model import (
    ModelConfig,
    ModelGateway,
    ModelUnavailable,
    ObserveReplaced,
    Priority,
    Prompt,
)
from daybook.model._gateway import request_body


class Reply(BaseModel):
    seen: str


def prompt(text: str = "hello") -> Prompt[Reply]:
    return Prompt("describe", text, Reply)


def gateway(handler) -> ModelGateway:
    gw = ModelGateway(ModelConfig())
    gw._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://mock/v1/"
    )
    return gw


def response(seen="hello"):
    return httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps({"seen": seen})}}]}
    )


async def test_slot_priority_starvation_and_admission():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    active = 0

    async def transport(request):
        nonlocal active
        active += 1
        assert active == 1
        text = json.loads(request.content)["messages"][1]["content"]
        calls.append(text)
        if text == "first":
            entered.set()
            await release.wait()
        active -= 1
        return response(text)

    gw = gateway(transport)
    first = asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt("first")))
    await entered.wait()
    built = []

    async def builder():
        built.append(True)
        return prompt("observe")

    observe = asyncio.create_task(gw.call(Priority.OBSERVE, builder))
    tick = asyncio.create_task(gw.call(Priority.TICK, prompt("tick")))
    chats = [
        asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt(f"chat{i}")))
        for i in range(3)
    ]
    await asyncio.sleep(0)
    assert built == []
    assert gw.status().in_flight == "interactive"
    assert gw.status().queue_depth == 5
    release.set()
    await asyncio.gather(first, observe, tick, *chats)
    assert calls == ["first", "chat0", "observe", "chat1", "chat2", "tick"]
    assert gw.status().calls_per_minute == 6
    assert gw.status().background_calls_per_minute == 2
    await gw.close()


async def test_tick_beats_observe_before_starvation_guard():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def transport(request):
        text = json.loads(request.content)["messages"][1]["content"]
        calls.append(text)
        if text == "first":
            entered.set()
            await release.wait()
        return response()

    gw = gateway(transport)
    first = asyncio.create_task(gw.call(Priority.OBSERVE, prompt("first")))
    await entered.wait()
    observe = asyncio.create_task(gw.call(Priority.OBSERVE, prompt("observe")))
    tick = asyncio.create_task(gw.call(Priority.TICK, prompt("tick")))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, observe, tick)
    assert calls == ["first", "tick", "observe"]
    await gw.close()


async def test_replacement_and_none_builder():
    entered = asyncio.Event()
    release = asyncio.Event()
    built = []

    async def transport(request):
        entered.set()
        await release.wait()
        return response()

    async def builder():
        built.append("old")
        return prompt()

    async def skip():
        built.append("new")
        return None

    gw = gateway(transport)
    first = asyncio.create_task(gw.call(Priority.TICK, prompt()))
    await entered.wait()
    old = asyncio.create_task(gw.call(Priority.OBSERVE, builder))
    await asyncio.sleep(0)
    new = asyncio.create_task(gw.call(Priority.OBSERVE, skip))
    await asyncio.sleep(0)
    with pytest.raises(ObserveReplaced):
        await old
    assert built == []
    release.set()
    await first
    assert await new is None
    assert built == ["new"]
    assert gw.status().calls_per_minute == 1
    await gw.close()


async def test_repair_once_and_sanitized_validation():
    bodies = []

    def transport(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "secret bad json"}}]}
            )
        return response("repaired")

    gw = gateway(transport)
    assert await gw.call(Priority.TICK, prompt()) == Reply(seen="repaired")
    assert len(bodies) == 2
    assert "json_invalid" in bodies[1]["messages"][0]["content"]
    assert "secret bad json" not in json.dumps(bodies[1])
    assert gw.status().background_calls_per_minute == 2
    await gw.close()


async def test_invalid_twice_and_timeout(monkeypatch):
    calls = []

    def invalid(request):
        calls.append(True)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    gw = gateway(invalid)
    with pytest.raises(ModelUnavailable, match="ValidationError"):
        await gw.call(Priority.TICK, prompt())
    assert len(calls) == 2
    await gw.close()

    async def slow(request):
        await asyncio.Event().wait()

    monkeypatch.setattr("daybook.model._gateway._TIMEOUT", 0.03)
    gw = gateway(slow)
    with pytest.raises(ModelUnavailable, match="TimeoutError"):
        await asyncio.wait_for(gw.call(Priority.TICK, prompt()), 0.2)
    assert not gw.status().reachable
    await gw.close()


async def test_cancellation_drop_and_close():
    started = asyncio.Event()

    async def transport(request):
        started.set()
        await asyncio.Event().wait()

    gw = gateway(transport)
    first = asyncio.create_task(gw.call(Priority.TICK, prompt()))
    await started.wait()
    observe = asyncio.create_task(gw.call(Priority.OBSERVE, prompt()))
    await asyncio.sleep(0)
    gw.drop_observe()
    with pytest.raises(ObserveReplaced):
        await observe
    await gw.close()
    with pytest.raises(ModelUnavailable):
        await first
    with pytest.raises(ModelUnavailable):
        await gw.call(Priority.TICK, prompt())


@pytest.mark.parametrize("dialect", ["ollama", "vllm", "openai"])
@pytest.mark.parametrize("effort", ["none", "low"])
def test_dialect_body(dialect, effort):
    config = ModelConfig(model="qwen", dialect=dialect)
    body = request_body(
        config, Prompt("system", "screen", Reply, (b"jpeg",), effort, 400)
    )
    expected = {
        "model": "qwen",
        "messages": [
            {
                "role": "system",
                "content": "system\nReturn only a JSON object matching this schema: "
                + json.dumps(Reply.model_json_schema()),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "screen"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,anBlZw=="},
                    },
                ],
            },
        ],
        "stream": False,
        "max_tokens": 400,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "Reply", "schema": Reply.model_json_schema()},
        },
    }
    if dialect == "vllm":
        expected["chat_template_kwargs"] = {"enable_thinking": effort != "none"}
        if effort != "none":
            expected["reasoning_effort"] = effort
    else:
        expected["reasoning_effort"] = effort
    if dialect == "openai":
        expected["response_format"]["json_schema"]["strict"] = True
        expected["response_format"]["json_schema"]["schema"]["additionalProperties"] = (
            False
        )
    assert body == expected


async def test_status_rolling_window_and_plain_output(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("daybook.model._gateway.time.monotonic", lambda: now[0])
    gw = gateway(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
    )
    assert await gw.call(Priority.INTERACTIVE, Prompt("", "", str)) == "ok"
    assert gw.status().reachable
    assert gw.status().calls_per_minute == 1
    now[0] = 61.0
    assert gw.status().calls_per_minute == 0
    await gw.close()


async def test_key_file_expands_home_path():
    from pathlib import Path
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix=".daybook-key-test-", dir=Path.home()) as folder:
        key_file = Path(folder) / "key"
        key_file.write_text("test-only-token\n")
        relative = "~/" + str(key_file.relative_to(Path.home()))
        gw = ModelGateway(ModelConfig(api_key_file=relative))
        assert gw._client.headers["Authorization"] == "Bearer test-only-token"
        await gw.close()
