from __future__ import annotations

import math
import re
from typing import Mapping

from . import ActionSpec


def duration(value: object) -> float:
    match = re.fullmatch(r"(0|[1-9][0-9]*)([smh])", str(value))
    if not match:
        raise ValueError("invalid duration")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


def parameters(spec: ActionSpec, args: Mapping[str, object]) -> dict[str, object]:
    if args.keys() - spec.parameters.keys():
        raise ValueError("unknown parameters")
    result: dict[str, object] = {}
    for name, param in spec.parameters.items():
        if name not in args and param.required:
            raise ValueError("missing parameter")
        value = args.get(name, param.default)
        valid = False
        if param.type == "str":
            valid = isinstance(value, str)
        elif param.type in ("enum", "music.mood"):
            valid = isinstance(value, str) and value in param.values
        elif param.type == "number":
            valid = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and float(value) > 0
            )
        elif param.type == "duration":
            duration(value)
            valid = isinstance(value, str)
        if not valid:
            raise ValueError("invalid parameter")
        result[name] = value
    return result
