"""Event emission, result builders, transport helpers, and telemetry."""

from shellgeist.io.events import UIEventEmitter
from shellgeist.io.results import completed_result, failed_result, stopped_result
from shellgeist.io.telemetry import TelemetryEmitter
from shellgeist.io.transport import safe_drain, send_json

__all__ = [
    "UIEventEmitter",
    "TelemetryEmitter",
    "completed_result",
    "failed_result",
    "safe_drain",
    "send_json",
    "stopped_result",
]
