"""daybook.model — the one door to the one model.

Only one large model fits in memory, so every model call in the system goes through one `ModelGateway`
with one call in flight at a time, ordered by priority. Callers describe *what* they want (a `Prompt`
with an output type) and get back a parsed domain value. They never see HTTP, OpenAI message dicts,
base64, `reasoning_effort` spellings, or JSON repair.

    reply = await gateway.call(Priority.INTERACTIVE, Prompt(system=..., user=..., output=RecallOut))
    # reply is a RecallOut instance, or None if the model was unreachable

    # Capture asks for a slot first and grabs the screen only when the slot is granted, so the frame is
    # fresh and never sits in a queue (and never touches disk):
    obs = await gateway.call(Priority.OBSERVE, build_observe_prompt)   # async () -> Prompt | None

Swapping Ollama qwen3.6:35b for vLLM Qwen3.8-Flash-Next is a config change ($DAYBOOK_HOME/daybook.toml):

    [model]
    base_url = "http://127.0.0.1:11434/v1"
    model = "qwen3.6:35b"
    api_key_file = ""                       # "~/.config/qwen38/api-key" for vLLM
    dialect = "ollama"                      # "vllm": how effort and JSON schema are spelled on the wire
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Priority(IntEnum):
    """Lower runs first. A queued INTERACTIVE call waits for at most the one call already in flight (~3 s)."""

    INTERACTIVE = 0  # chat: routing, recall, skill drafting
    TICK = 1  # rollup + skill signals
    OBSERVE = 2  # one screenshot description


Effort = Literal["none", "low", "medium"]


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    api_key_file: Path | None
    dialect: Literal["ollama", "vllm", "openai"]
    timeout_s: float = 60.0
    image_max_px: int = 1280
    jpeg_quality: int = 80


@dataclass(frozen=True)
class Prompt[T: BaseModel | str]:
    """One model request. `output=str` means free text; a pydantic model means JSON constrained by its schema.

    Output pydantic models are private to the calling module (e.g. mind._TickOut); callers convert them to
    domain types right after `call` returns.
    """

    system: str
    user: str
    output: type[T]
    images: tuple[bytes, ...] = ()  # raw JPEG bytes, in memory only; the gateway base64s them
    effort: Effort = "none"
    max_tokens: int = 400


type PromptBuilder[T: BaseModel | str] = Callable[[], Awaitable[Prompt[T] | None]]


class ModelUnavailable(Exception):
    """Endpoint down or timed out. Background loops log and skip; chat shows 'model is busy/offline'."""


class ModelOutputInvalid(Exception):
    """Output failed schema validation twice (the gateway retries once with the validation error appended)."""


@dataclass(frozen=True)
class GatewayStatus:
    model: str
    reachable: bool
    in_flight: Priority | None
    queued: dict[Priority, int]
    last_latency_s: dict[Priority, float]
    calls_last_minute: dict[Priority, int]  # shown in the UI footer; the live model-call budget


class ModelGateway:
    def __init__(self, config: ModelConfig) -> None:
        raise NotImplementedError

    async def call[T: BaseModel | str](self, priority: Priority, prompt: Prompt[T] | PromptBuilder[T]) -> T | None:
        """Wait for the single slot by priority, then run one request.

        Returns None if `prompt` is a builder that returned None (e.g. the screen locked while waiting).
        Raises ModelUnavailable / ModelOutputInvalid.
        TODO:
          ticket = heap push (priority, seq); await until ticket is head and slot free
          if callable(prompt): prompt = await prompt(); if None: release, return None
          body = _wire.encode(prompt, config)   # dialect: Ollama wants reasoning_effort 'none' + response_format json_schema;
                                                # vLLM wants guided_json / chat_template_kwargs enable_thinking=False
          resp = await httpx post /chat/completions (Authorization from api_key_file if set)
          text = strip <think>…</think> and ``` fences
          if output is str: return text
          try output.model_validate_json(text) except ValidationError as e: retry once with e appended; else raise
          finally: release slot, record latency
        """
        raise NotImplementedError

    def status(self) -> GatewayStatus:
        raise NotImplementedError


class ScriptedGateway(ModelGateway):
    """Test double: returns queued replies in order and records prompts. Lets every lane work without the GPU."""

    def __init__(self, replies: list[BaseModel | str]) -> None:
        raise NotImplementedError

    @property
    def prompts(self) -> list[Prompt]:
        raise NotImplementedError
