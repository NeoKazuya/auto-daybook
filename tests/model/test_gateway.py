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


def gateway(handler, max_concurrency: int = 1) -> ModelGateway:
    gw = ModelGateway(ModelConfig(max_concurrency=max_concurrency))
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


async def test_interactive_overlaps_background_and_replaces_only_queued_observe():
    background_started = asyncio.Event()
    release_background = asyncio.Event()
    chat_started = asyncio.Event()
    release_chat = asyncio.Event()
    calls = []
    built = []

    async def transport(request):
        text = json.loads(request.content)["messages"][1]["content"]
        calls.append(text)
        if text == "background":
            background_started.set()
            await release_background.wait()
        if text == "chat":
            chat_started.set()
            await release_chat.wait()
        return response(text)

    def builder(text):
        async def build():
            built.append(text)
            return prompt(text)

        return build

    gw = gateway(transport, max_concurrency=2)
    tasks = []
    try:
        background = asyncio.create_task(gw.call(Priority.TICK, prompt("background")))
        tasks.append(background)
        await background_started.wait()
        old = asyncio.create_task(gw.call(Priority.OBSERVE, builder("old")))
        tasks.append(old)
        await asyncio.sleep(0)
        new = asyncio.create_task(gw.call(Priority.OBSERVE, builder("new")))
        tasks.append(new)
        chat = asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt("chat")))
        tasks.append(chat)
        with pytest.raises(ObserveReplaced):
            await old
        await asyncio.wait_for(chat_started.wait(), 0.3)
        assert built == []
        assert calls == ["background", "chat"]
        assert gw.status().in_flight == "interactive"
        assert gw.status().queue_depth == 1
        release_chat.set()
        await chat
        assert gw.status().in_flight == "tick"
        assert built == []
        release_background.set()
        await asyncio.gather(background, new)
        assert built == ["new"]
        assert calls == ["background", "chat", "new"]
    finally:
        await gw.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_close_awaits_all_active_http_and_rejects_queued_requests():
    started = [asyncio.Event(), asyncio.Event()]
    cancelled = []
    never = asyncio.Event()

    async def transport(request):
        text = json.loads(request.content)["messages"][1]["content"]
        index = int(text)
        started[index].set()
        try:
            await never.wait()
        finally:
            # Cleanup must finish before the HTTP client is closed.
            await asyncio.sleep(0)
            cancelled.append(index)

    gw = gateway(transport, max_concurrency=2)
    calls = [
        asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt(str(i))))
        for i in range(2)
    ]
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started)), 0.3
        )
        queued = asyncio.create_task(gw.call(Priority.TICK, prompt("queued")))
        calls.append(queued)
        await asyncio.sleep(0)
        assert gw.status().queue_depth == 1
        await gw.close()
        assert sorted(cancelled) == [0, 1]
        assert gw._client.is_closed
        assert gw.status().in_flight is None
        assert gw.status().queue_depth == 0
        for call in calls:
            with pytest.raises(ModelUnavailable, match="gateway_closed"):
                await call
    finally:
        await gw.close()
        await asyncio.gather(*calls, return_exceptions=True)


@pytest.mark.parametrize("limit", [0, -1, True, False, 1.5, "2", None])
def test_concurrency_limit_must_be_a_positive_integer(limit):
    with pytest.raises(ValueError, match="max_concurrency must be a positive integer"):
        ModelConfig(max_concurrency=limit)


async def test_configurable_capacity_three_and_single_background_admission():
    started = {
        name: asyncio.Event() for name in ("tick", "chat1", "chat2", "observe", "chat3")
    }
    release = {name: asyncio.Event() for name in started}
    active = []
    peak = 0
    background_peak = 0
    built = []

    async def transport(request):
        nonlocal peak, background_peak
        text = json.loads(request.content)["messages"][1]["content"]
        active.append(text)
        peak = max(peak, len(active))
        background_peak = max(
            background_peak, sum(n in ("tick", "observe") for n in active)
        )
        started[text].set()
        try:
            await release[text].wait()
            return response(text)
        finally:
            active.remove(text)

    async def observe_builder():
        built.append("observe")
        return prompt("observe")

    gw = gateway(transport, max_concurrency=3)
    calls = []
    try:
        calls.append(asyncio.create_task(gw.call(Priority.TICK, prompt("tick"))))
        await started["tick"].wait()
        calls.append(asyncio.create_task(gw.call(Priority.OBSERVE, observe_builder)))
        calls.extend(
            asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt(name)))
            for name in ("chat1", "chat2", "chat3")
        )
        await asyncio.wait_for(
            asyncio.gather(started["chat1"].wait(), started["chat2"].wait()), 0.3
        )
        assert not started["chat3"].is_set()
        assert built == []
        assert gw.status().queue_depth == 2
        release["chat1"].set()
        await asyncio.wait_for(started["chat3"].wait(), 0.3)
        assert built == []
        release["tick"].set()
        await asyncio.wait_for(started["observe"].wait(), 0.3)
        assert built == ["observe"]
        assert peak == 3
        assert background_peak == 1
        for event in release.values():
            event.set()
        await asyncio.gather(*calls)
    finally:
        await gw.close()
        await asyncio.gather(*calls, return_exceptions=True)


async def test_parallel_admissions_preserve_priority_and_background_fairness():
    initial_started = [asyncio.Event(), asyncio.Event()]
    release_initial = asyncio.Event()
    calls = []

    async def transport(request):
        text = json.loads(request.content)["messages"][1]["content"]
        calls.append(text)
        if text.startswith("initial"):
            initial_started[int(text[-1])].set()
            await release_initial.wait()
        return response(text)

    gw = gateway(transport, max_concurrency=2)
    tasks = [
        asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt(f"initial{i}")))
        for i in range(2)
    ]
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in initial_started)), 0.3
        )
        # Both interactive admissions count toward the shared fairness rule.
        tasks.append(
            asyncio.create_task(gw.call(Priority.OBSERVE, prompt("old_observe")))
        )
        tasks.append(asyncio.create_task(gw.call(Priority.TICK, prompt("tick"))))
        tasks.extend(
            asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt(f"chat{i}")))
            for i in range(3)
        )
        await asyncio.sleep(0)
        release_initial.set()
        await asyncio.gather(*tasks)
        assert calls[2] == "old_observe"
        assert calls.index("tick") <= calls.index("chat2")
    finally:
        await gw.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_caller_cancellation_keeps_active_http_slot_until_completion():
    started = {name: asyncio.Event() for name in ("first", "second", "third")}
    release = {name: asyncio.Event() for name in started}
    cancelled_http = []

    async def transport(request):
        text = json.loads(request.content)["messages"][1]["content"]
        started[text].set()
        try:
            await release[text].wait()
            return response(text)
        except asyncio.CancelledError:
            cancelled_http.append(text)
            raise

    gw = gateway(transport, max_concurrency=2)
    tasks = []
    try:
        first = asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt("first")))
        second = asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt("second")))
        tasks.extend((first, second))
        await asyncio.wait_for(
            asyncio.gather(started["first"].wait(), started["second"].wait()), 0.3
        )
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        third = asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt("third")))
        tasks.append(third)
        await asyncio.sleep(0)
        assert not started["third"].is_set()
        assert cancelled_http == []
        assert gw.status().queue_depth == 1
        release["first"].set()
        await asyncio.wait_for(started["third"].wait(), 0.3)
        release["second"].set()
        release["third"].set()
        await asyncio.gather(second, third)
        assert cancelled_http == []
    finally:
        await gw.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_repair_keeps_background_slot_while_interactive_runs():
    repair_started = asyncio.Event()
    release_repair = asyncio.Event()
    calls = []
    built = []

    async def transport(request):
        body = json.loads(request.content)
        text = body["messages"][1]["content"]
        calls.append(text)
        if text == "repair":
            if calls.count("repair") == 1:
                return httpx.Response(
                    200, json={"choices": [{"message": {"content": "{}"}}]}
                )
            repair_started.set()
            await release_repair.wait()
        return response(text)

    async def waiting_background():
        built.append("later")
        return prompt("later")

    gw = gateway(transport, max_concurrency=2)
    tasks = []
    try:
        repair = asyncio.create_task(gw.call(Priority.TICK, prompt("repair")))
        tasks.append(repair)
        await repair_started.wait()
        later = asyncio.create_task(gw.call(Priority.OBSERVE, waiting_background))
        chat = asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt("chat")))
        tasks.extend((later, chat))
        assert await asyncio.wait_for(chat, 0.3) == Reply(seen="chat")
        assert built == []
        assert calls == ["repair", "repair", "chat"]
        release_repair.set()
        await asyncio.gather(repair, later)
        assert built == ["later"]
        assert calls == ["repair", "repair", "chat", "later"]
        assert gw.status().calls_per_minute == 4
    finally:
        await gw.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_parallel_timeout_frees_background_for_fresh_lazy_builder(monkeypatch):
    entered = asyncio.Event()
    release_chat = asyncio.Event()
    built = []

    async def transport(request):
        text = json.loads(request.content)["messages"][1]["content"]
        if text == "slow":
            entered.set()
            await asyncio.Event().wait()
        if text == "chat":
            await release_chat.wait()
        return response(text)

    async def fresh():
        built.append("fresh")
        return prompt("fresh")

    monkeypatch.setattr("daybook.model._gateway._TIMEOUT", 0.1)
    gw = gateway(transport, max_concurrency=2)
    tasks = []
    try:
        slow = asyncio.create_task(gw.call(Priority.TICK, prompt("slow")))
        tasks.append(slow)
        await entered.wait()
        observe = asyncio.create_task(gw.call(Priority.OBSERVE, fresh))
        chat = asyncio.create_task(gw.call(Priority.INTERACTIVE, prompt("chat")))
        tasks.extend((observe, chat))
        await asyncio.sleep(0)
        assert built == []
        release_chat.set()
        assert await chat == Reply(seen="chat")
        with pytest.raises(ModelUnavailable, match="TimeoutError"):
            await slow
        assert await asyncio.wait_for(observe, 0.3) == Reply(seen="fresh")
        assert built == ["fresh"]
    finally:
        await gw.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_queued_cancel_and_none_builder_do_not_use_http_capacity():
    entered = asyncio.Event()
    release = asyncio.Event()
    built = []
    calls = []

    async def transport(request):
        text = json.loads(request.content)["messages"][1]["content"]
        calls.append(text)
        entered.set()
        await release.wait()
        return response(text)

    async def cancelled_builder():
        built.append("cancelled")
        return prompt("cancelled")

    async def skip_builder():
        built.append("skip")
        return None

    gw = gateway(transport, max_concurrency=2)
    tasks = []
    try:
        active = asyncio.create_task(gw.call(Priority.TICK, prompt("active")))
        tasks.append(active)
        await entered.wait()
        cancelled = asyncio.create_task(gw.call(Priority.OBSERVE, cancelled_builder))
        tasks.append(cancelled)
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        skip = asyncio.create_task(gw.call(Priority.OBSERVE, skip_builder))
        tasks.append(skip)
        await asyncio.sleep(0)
        assert built == []
        release.set()
        await active
        assert await skip is None
        assert built == ["skip"]
        assert calls == ["active"]
        assert gw.status().queue_depth == 0
    finally:
        await gw.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_close_before_admission_does_not_run_lazy_builders():
    built = []

    async def builder():
        built.append(True)
        return prompt()

    gw = gateway(lambda _: response(), max_concurrency=3)
    call = asyncio.create_task(gw.call(Priority.OBSERVE, builder))
    await asyncio.sleep(0)
    await gw.close()
    with pytest.raises(ModelUnavailable, match="gateway_closed"):
        await call
    assert built == []
    assert gw._client.is_closed
    assert gw.status().in_flight is None
