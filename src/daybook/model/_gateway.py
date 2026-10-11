"""Serial admission, request dialects, and bounded structured-output repair."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, cast

import httpx
from pydantic import BaseModel, ValidationError

from ._types import (
    GatewayStatus,
    ModelConfig,
    ModelUnavailable,
    ObserveReplaced,
    Priority,
    Prompt,
    T,
)

_TIMEOUT = 40.0


@dataclass
class _Request:
    priority: Priority
    prompt: Prompt | Callable[[], Awaitable[Prompt | None]]
    future: asyncio.Future[BaseModel | str | None]


def request_body(config: ModelConfig, prompt: Prompt, repair: str = "") -> dict:
    """Keep backend spelling here; callers own their output schemas."""
    content: str | list[dict] = prompt.user
    if prompt.images:
        content = [{"type": "text", "text": prompt.user}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(frame).decode()
                },
            }
            for frame in prompt.images
        )
    system = prompt.system
    if issubclass(prompt.output, BaseModel):
        system += "\nReturn only a JSON object matching this schema: " + json.dumps(
            prompt.output.model_json_schema()
        )
    if repair:
        system += "\nReturn corrected output. Validation errors: " + repair
    body: dict = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "stream": False,
        "max_tokens": prompt.max_tokens,
    }
    if config.dialect == "vllm":
        body["chat_template_kwargs"] = {"enable_thinking": prompt.effort != "none"}
        if prompt.effort != "none":
            body["reasoning_effort"] = prompt.effort
    else:
        body["reasoning_effort"] = prompt.effort
    if issubclass(prompt.output, BaseModel):
        schema = prompt.output.model_json_schema()
        json_schema: dict = {"name": prompt.output.__name__, "schema": schema}
        if config.dialect == "openai":
            _strict_schema(schema)
            json_schema["strict"] = True
        body["response_format"] = {"type": "json_schema", "json_schema": json_schema}
    return body


def _strict_schema(schema: dict) -> None:
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
    schema.pop("default", None)
    for value in schema.values():
        if isinstance(value, dict):
            _strict_schema(value)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    _strict_schema(entry)


class ModelGateway:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        headers = {}
        if config.api_key_file:
            headers["Authorization"] = (
                "Bearer " + Path(config.api_key_file).read_text().strip()
            )
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=_TIMEOUT,
            trust_env=False,
        )
        self._queue: list[_Request] = []
        self._worker: asyncio.Task[None] | None = None
        self._active: _Request | None = None
        self._interactive_streak = 0
        self._closed = False
        self._reachable = False
        self._latency: float | None = None
        self._calls: deque[tuple[float, Priority]] = deque()

    async def call(
        self,
        priority: Priority,
        prompt: Prompt[T] | Callable[[], Awaitable[Prompt[T] | None]],
    ) -> T | None:
        if self._closed:
            raise ModelUnavailable("gateway_closed")
        if priority == Priority.OBSERVE:
            self.drop_observe()
        future: asyncio.Future[BaseModel | str | None] = (
            asyncio.get_running_loop().create_future()
        )
        request = _Request(priority, prompt, future)
        self._queue.append(request)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._serve())
        try:
            return cast(T | None, await future)
        finally:
            if request in self._queue and future.cancelled():
                self._queue.remove(request)

    def drop_observe(self) -> None:
        for request in tuple(self._queue):
            if request.priority == Priority.OBSERVE:
                self._queue.remove(request)
                if not request.future.done():
                    request.future.set_exception(ObserveReplaced("observe_replaced"))

    def _next(self) -> _Request:
        waiting = [r for r in self._queue if not r.future.done()]
        self._queue = waiting
        background = [r for r in waiting if r.priority != Priority.INTERACTIVE]
        if self._interactive_streak >= 2 and background:
            request = background[0]
        else:
            request = min(waiting, key=lambda r: r.priority)
        self._queue.remove(request)
        self._interactive_streak = (
            self._interactive_streak + 1
            if request.priority == Priority.INTERACTIVE
            else 0
        )
        return request

    async def _serve(self) -> None:
        try:
            while any(not r.future.done() for r in self._queue):
                request = self._next()
                self._active = request
                try:
                    async with asyncio.timeout(_TIMEOUT):
                        built = (
                            await request.prompt()
                            if callable(request.prompt)
                            else request.prompt
                        )
                        result = (
                            None
                            if built is None
                            else await self._complete(request.priority, built)
                        )
                    if not request.future.done():
                        request.future.set_result(result)
                except asyncio.CancelledError:
                    if not request.future.done():
                        request.future.set_exception(ModelUnavailable("gateway_closed"))
                    raise
                except Exception as exc:
                    self._reachable = (
                        False
                        if isinstance(exc, (httpx.HTTPError, TimeoutError))
                        else self._reachable
                    )
                    # Never expose server error bodies, prompts, or validation values.
                    if not request.future.done():
                        request.future.set_exception(
                            ModelUnavailable(type(exc).__name__)
                        )
                finally:
                    self._active = None
        finally:
            self._worker = None

    async def _complete(self, priority: Priority, prompt: Prompt) -> BaseModel | str:
        repair = ""
        for attempt in range(2):
            started = time.monotonic()
            self._calls.append((started, priority))
            response = await self._client.post(
                "chat/completions", json=request_body(self.config, prompt, repair)
            )
            response.raise_for_status()
            self._latency = time.monotonic() - started
            self._reachable = True
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("missing_content")
            if issubclass(prompt.output, str):
                return prompt.output(content)
            try:
                return prompt.output.model_validate_json(content)
            except ValidationError as exc:
                if attempt:
                    raise
                # ValidationError.__str__ contains input text. Use locations and kinds only.
                repair = json.dumps(
                    [
                        {"loc": error["loc"], "type": error["type"]}
                        for error in exc.errors()
                    ]
                )
        raise AssertionError("unreachable")

    def status(self) -> GatewayStatus:
        cutoff = time.monotonic() - 60
        while self._calls and self._calls[0][0] <= cutoff:
            self._calls.popleft()
        return GatewayStatus(
            self.config.model,
            self._reachable,
            self._active.priority.name.lower() if self._active else None,
            sum(not r.future.done() for r in self._queue),
            self._latency,
            len(self._calls),
            sum(priority != Priority.INTERACTIVE for _, priority in self._calls),
        )

    async def close(self) -> None:
        self._closed = True
        for request in self._queue:
            if not request.future.done():
                request.future.set_exception(ModelUnavailable("gateway_closed"))
        self._queue.clear()
        worker = self._worker
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        await self._client.aclose()
