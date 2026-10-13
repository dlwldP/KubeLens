"""도구 레이어(kubelens.tools)의 시그니처에서 LLM용 도구 정의(JSON Schema)를 만든다.

도구 목록의 단일 출처는 kubelens.tools.TOOLS이며, 여기서 따로 정의하지 않는다.
"""

from __future__ import annotations

import inspect
import types
import typing
from typing import Any

from kubelens.tools import TOOLS, Tool

_JSON_TYPES: dict[Any, str] = {str: "string", bool: "boolean", int: "integer"}


def _json_type(annotation: Any) -> str:
    if annotation in _JSON_TYPES:
        return _JSON_TYPES[annotation]
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        non_null = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_null) == 1:
            return _json_type(non_null[0])
    raise TypeError(f"지원하지 않는 도구 인자 타입: {annotation!r}")


def tool_spec(t: Tool) -> dict[str, Any]:
    hints = typing.get_type_hints(t.render)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for i, (name, param) in enumerate(t.signature.parameters.items()):
        if i == 0:  # 첫 인자는 ReadOnlyKube (LLM에 노출하지 않음)
            continue
        prop: dict[str, Any] = {"type": _json_type(hints[name])}
        if param.default is inspect.Parameter.empty:
            required.append(name)
        elif param.default is not None:
            prop["default"] = param.default
        properties[name] = prop
    return {
        "name": t.name,
        "description": t.description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def all_tool_specs() -> list[dict[str, Any]]:
    return [tool_spec(t) for t in TOOLS.values()]
