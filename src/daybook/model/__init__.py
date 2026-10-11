"""The sole model gateway; screenshot builders run only after admission."""

from __future__ import annotations

from collections import deque
from typing import Awaitable, Callable

from pydantic import BaseModel

from ._gateway import ModelGateway as ModelGateway
from ._types import (
    GatewayStatus as GatewayStatus,
)
from ._types import (
    ModelConfig as ModelConfig,
)
from ._types import (
    ModelUnavailable as ModelUnavailable,
)
from ._types import (
    ObserveReplaced as ObserveReplaced,
)
from ._types import (
    Priority as Priority,
)
from ._types import (
    Prompt as Prompt,
)
from ._types import (
    T,
)


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

    def check_health(self) -> None:
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
