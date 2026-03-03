"""LLM provider layer: client discovery, prompt building, streaming."""

from shellgeist.llm.client import get_client
from shellgeist.llm.stream import run_llm_stream_with_retry
from shellgeist.llm.prompt import build_system_prompt

__all__ = [
    "get_client",
    "run_llm_stream_with_retry",
    "build_system_prompt",
]
