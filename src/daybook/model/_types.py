"""The sole model gateway; screenshot builders run only after admission."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Generic, Literal, TypeVar

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
