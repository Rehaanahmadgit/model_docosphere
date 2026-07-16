"""
scheduler/ws_listener.py — WebSocket listener for on-demand start/stop.

Stub — implementation pending.
Connects to the backend WebSocket at /ws/agent/{agent_id} and listens for
"start_session" / "stop_session" commands from the dashboard, allowing
admins to trigger immediate attendance sessions without waiting for the
scheduled task window.
"""
from __future__ import annotations

import asyncio


class AgentWSListener:
    """
    Maintains a persistent WebSocket connection to the backend and dispatches
    start/stop commands to the service layer.

    Args:
        ws_url: Full WebSocket URL, e.g. wss://backend.railway.app/ws/agent/42
        token: Raw agent token (sent as query param or header on connect)
        on_start: Callable[] — called when server sends start_session
        on_stop: Callable[] — called when server sends stop_session
    """

    def __init__(self, ws_url: str, token: str, on_start=None, on_stop=None):
        self.ws_url = ws_url
        self.token = token
        self.on_start = on_start
        self.on_stop = on_stop
        self._running = False

    async def run(self) -> None:
        # TODO: connect with websockets lib, reconnect with exponential backoff
        raise NotImplementedError

    def stop(self) -> None:
        self._running = False
