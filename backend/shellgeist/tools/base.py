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

            schemas.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": schema
            })
        return schemas


# Global registry for the engine
registry = ToolRegistry()
