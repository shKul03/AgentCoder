"""WebSocket bridge — streams pipeline events to connected clients.

Channel naming convention (Phase 10e):
  terminal:prompt_generator   — Prompt Generator CLI output
  terminal:code_generator     — Code Generator CLI output
  terminal:code_reviewer      — Code Reviewer CLI output
  pipeline                    — pipeline state-change events
  review                      — review JSON events

Clients may send ``{"subscribe": ["terminal:code_generator", "pipeline"]}``
to opt into only those channels.  Omitting the subscribe message (or
subscribing to ``["*"]``) delivers all channels.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections with optional per-channel filtering."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        # None means "all channels"; a set means "only these channels"
        self._subscriptions: dict[WebSocket, set[str] | None] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        self._subscriptions[ws] = None  # receive all channels by default

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        self._subscriptions.pop(ws, None)

    def set_filter(self, ws: WebSocket, channels: list[str]) -> None:
        """Subscribe *ws* to only the given *channels*.

        Passing ``["*"]`` resets to receive-all behaviour.
        """
        if "*" in channels:
            self._subscriptions[ws] = None
        else:
            self._subscriptions[ws] = set(channels)

    def _should_deliver(self, ws: WebSocket, channel: str) -> bool:
        """Return True if *ws* has opted into *channel* (or has no filter)."""
        subs = self._subscriptions.get(ws)
        return subs is None or channel in subs

    async def broadcast(self, message: dict[str, Any]) -> None:
        channel: str = message.get("channel", "")
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            if not self._should_deliver(ws, channel):
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()

# Asyncio queue — will be used by future pipeline events
_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


def _setup_bus_subscriptions() -> None:
    """Placeholder — comm bus removed in Phase 1. Will be rewired in Phase 2."""
    pass


async def _broadcast_worker() -> None:
    """Background task: drains the queue and broadcasts to WebSocket clients."""
    while True:
        msg = await _queue.get()
        await manager.broadcast(msg)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                parsed = json.loads(data)
                if "subscribe" in parsed and isinstance(parsed["subscribe"], list):
                    manager.set_filter(ws, parsed["subscribe"])
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        manager.disconnect(ws)
