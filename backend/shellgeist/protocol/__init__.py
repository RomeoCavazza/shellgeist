"""JSON-RPC protocol layer: request routing, validation models, helpers."""

from shellgeist.protocol.handler import handle_request
from shellgeist.protocol.models import SGRequest, SGResult

__all__ = [
    "handle_request",
    "SGRequest",
    "SGResult",
]
