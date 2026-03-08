"""Tool registry: schema-driven tool definition and lookup."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    input_model: type[Any] | None = None

    def execute(self, **kwargs: Any) -> Any:
        if self.input_model:
            try:
                model_fields = set(self.input_model.model_fields.keys())
                model_kwargs = {k: v for k, v in kwargs.items() if k in model_fields}
                extra_kwargs = {k: v for k, v in kwargs.items() if k not in model_fields}
                validated = self.input_model.model_validate(model_kwargs)
                return self.func(**validated.model_dump(), **extra_kwargs)
            except Exception as e:
                return f"Error: Validation failed for {self.name}. {e}"
        return self.func(**kwargs)


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def register(self, description: str, input_model: type[Any] | None = None) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            name = func.__name__
            self.tools[name] = Tool(
                name=name,
                description=description,
                func=func,
                input_model=input_model
            )
            return func
        return decorator

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        from pydantic import BaseModel
        schemas = []
        for tool in self.tools.values():
            schema = {}
            if tool.input_model is not None and tool.input_model is not BaseModel:
                schema = tool.input_model.model_json_schema()
                schema = _clean_schema(schema)

            schemas.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": schema
            })
        return schemas


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip noise from Pydantic JSON schemas for 7B models.

    Removes: title, $defs, default, anyOf (simplified to first type).
    """
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("title", "$defs", "default"):
            continue
        if k == "anyOf" and isinstance(v, list):
            # Simplify Optional[X] → X
            non_null = [t for t in v if t != {"type": "null"}]
            if len(non_null) == 1:
                out.update(_clean_schema(non_null[0]))
            else:
                out[k] = v
            continue
        if isinstance(v, dict):
            out[k] = _clean_schema(v)
        elif isinstance(v, list):
            out[k] = [_clean_schema(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


# Global registry for the engine
registry = ToolRegistry()
