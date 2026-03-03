"""Session repair: deduplication, normalization, and pruning of chat history."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class SessionRepairReport:
    input_count: int
    output_count: int
    dropped_count: int
    deduped_count: int
    normalized_count: int

    def changed(self) -> bool:
        return (
            self.input_count != self.output_count
            or self.dropped_count > 0
            or self.deduped_count > 0
            or self.normalized_count > 0
        )


def _normalize_content(content: Any) -> tuple[str, bool]:
    if isinstance(content, str):
        return content, False
    if content is None:
        return "", True
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False), True
    return str(content), True


def repair_conversation_history(
    messages: list[dict[str, Any]],
    *,
    max_non_system: int = 80,
) -> tuple[list[dict[str, str]], SessionRepairReport]:
    allowed_roles = {"system", "user", "assistant", "tool"}

    dropped_count = 0
    deduped_count = 0
    normalized_count = 0
    normalized: list[dict[str, str]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            dropped_count += 1
            continue

        raw_role = str(msg.get("role") or "").strip().lower()
        if raw_role not in allowed_roles:
            dropped_count += 1
            continue

        content, content_changed = _normalize_content(msg.get("content"))
        if content_changed:
            normalized_count += 1

        if raw_role != "system":
            content = content.strip()
            if not content:
                dropped_count += 1
                continue

        entry = {"role": raw_role, "content": content}
        if normalized and normalized[-1] == entry:
            deduped_count += 1
            continue

        normalized.append(entry)

    system_msgs = [m for m in normalized if m["role"] == "system"]
    non_system_msgs = [m for m in normalized if m["role"] != "system"]

    if len(non_system_msgs) > max_non_system:
        dropped_count += len(non_system_msgs) - max_non_system
        non_system_msgs = non_system_msgs[-max_non_system:]

    repaired: list[dict[str, str]] = []
    if system_msgs:
        repaired.append(system_msgs[0])
    repaired.extend(non_system_msgs)

    report = SessionRepairReport(
        input_count=len(messages),
        output_count=len(repaired),
        dropped_count=dropped_count,
        deduped_count=deduped_count,
        normalized_count=normalized_count,
    )
    return repaired, report
