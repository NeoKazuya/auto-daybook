"""The sole model gateway; screenshot builders run only after admission."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Awaitable, Callable, Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel | str)


class Priority(IntEnum):
    INTERACTIVE = 0
    TICK = 1
    OBSERVE = 2


@dataclass(frozen=True)
class Prompt(Generic[T]):
    system: str
    user: str
    output: type[T]
    images: tuple[bytes, ...] = ()
    effort: Literal["none", "low"] = "none"
    max_tokens: int = 1024


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen3.6:35b"
    api_key_file: str = ""
    dialect: Literal["ollama", "vllm", "openai"] = "ollama"


@dataclass(frozen=True)
class GatewayStatus:
    model: str
    reachable: bool
    in_flight: str | None
    queue_depth: int
    latency: float | None
    calls_per_minute: int
    background_calls_per_minute: int


class ModelUnavailable(Exception):
    pass


class ObserveReplaced(Exception):
    pass


class ModelGateway:
    def __init__(self, config: ModelConfig) -> None:
        raise NotImplementedError

    async def call(
        self,
        priority: Priority,
        prompt: Prompt[T] | Callable[[], Awaitable[Prompt[T] | None]],
    ) -> T | None:
        raise NotImplementedError

    def drop_observe(self) -> None:
        raise NotImplementedError

    def status(self) -> GatewayStatus:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class ScriptedGateway(ModelGateway):
    def __init__(self, replies: list[BaseModel | str | Exception]) -> None:
        self.replies = deque(replies)
        self.prompts: list[Prompt] = []
        self.priorities: list[Priority] = []

    async def call(
        self,
        priority: Priority,
        prompt: Prompt[T] | Callable[[], Awaitable[Prompt[T] | None]],
    ) -> T | None:
        built = await prompt() if callable(prompt) else prompt
        if built is None:
            return None
        self.prompts.append(built)
        self.priorities.append(priority)
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        if issubclass(built.output, str):
            return built.output(str(reply))
        if issubclass(built.output, BaseModel):
            return (
                built.output.model_validate_json(reply)
                if isinstance(reply, str)
                else built.output.model_validate(reply.model_dump())
            )
        raise TypeError("unsupported output type")

    def drop_observe(self) -> None:
        pass

    def status(self) -> GatewayStatus:
        return GatewayStatus(
            "scripted",
            True,
            None,
            0,
            0,
            len(self.prompts),
            sum(p != Priority.INTERACTIVE for p in self.priorities),
        )

    async def close(self) -> None:
        pass
