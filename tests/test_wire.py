from dataclasses import fields
from typing import get_args

import pytest

from daybook import events
from daybook.act import OverlayRequest
from daybook.web import wire as w


@pytest.mark.parametrize("model", list(w.EXAMPLES))
def test_every_example_validates(model):
    value = w.example(model)
    assert model.model_validate_json(value.model_dump_json(by_alias=True)) == value
    assert model.model_json_schema()["examples"]


def test_every_wire_model_has_example():
    def descendants(cls):
        return {
            child for sub in cls.__subclasses__() for child in (sub, *descendants(sub))
        }

    assert descendants(w.WireModel) == set(w.EXAMPLES)


def test_event_registry_is_complete():
    types = get_args(events.Event)
    assert {t.__name__ for t in types} == set(w.EVENT_MODELS)
    for event_type in types:
        data = w.example(w.EVENT_MODELS[event_type.__name__]).model_dump()
        if event_type is events.OverlayShown:
            data["request"] = OverlayRequest(**data["request"])
        event = event_type(**{f.name: data[f.name] for f in fields(event_type)})
        name, encoded = w.serialize_event(event)
        assert w.EVENT_MODELS[name].model_validate_json(encoded)
