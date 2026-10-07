"""
Per-session media runtime: capturer + WebRTC peers.

Tied to session lifecycle (start/stop) and WebSocket viewers.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from infra.logging_safe import log_event
from streaming.capturer import BrowserScreenCapturer, build_capturer
from streaming.peer import WebRTCPeer, build_peer


@dataclass
class SessionMediaRuntime:
    session_id: str
    display_name: Optional[str]
    profile_path: str
    capturer: Optional[BrowserScreenCapturer] = None
    peers: Set[WebRTCPeer] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)


class SessionMediaRegistry:
    def __init__(self):
        self._runtimes: Dict[str, SessionMediaRuntime] = {}
        self._lock = threading.Lock()

    def register_session(
        self,
        *,
        session_id: str,
        display_name: Optional[str],
        profile_path: str,
    ) -> SessionMediaRuntime:
        with self._lock:
            existing = self._runtimes.get(session_id)
            if existing:
                return existing
            runtime = SessionMediaRuntime(
                session_id=session_id,
                display_name=display_name,
                profile_path=profile_path,
            )
            self._runtimes[session_id] = runtime
            log_event(
                "media_runtime_registered",
                sessionId=session_id,
                display=display_name,
            )
            return runtime

    def get(self, session_id: str) -> Optional[SessionMediaRuntime]:
        with self._lock:
            return self._runtimes.get(session_id)

    async def ensure_capturer(self, session_id: str) -> BrowserScreenCapturer:
        runtime = self.get(session_id)
        if runtime is None:
            raise KeyError(f"No media runtime for session {session_id}")
        with runtime._lock:
            if runtime.capturer is None:
                runtime.capturer = build_capturer(display_name=runtime.display_name)
                await runtime.capturer.start(
                    session_id=session_id,
                    display_name=runtime.display_name,
                    profile_path=runtime.profile_path,
                )
            return runtime.capturer

    async def create_peer(self, session_id: str) -> WebRTCPeer:
        capturer = await self.ensure_capturer(session_id)
        peer = build_peer(session_id)
        await peer.attach_capturer(capturer)
        runtime = self.get(session_id)
        assert runtime is not None
        with runtime._lock:
            runtime.peers.add(peer)
        log_event("media_peer_created", sessionId=session_id)
        return peer

    async def close_peer(self, session_id: str, peer: WebRTCPeer) -> None:
        runtime = self.get(session_id)
        try:
            await peer.close()
        finally:
            if runtime is not None:
                with runtime._lock:
                    runtime.peers.discard(peer)
            log_event("media_peer_closed", sessionId=session_id)

    async def stop_session(self, session_id: str) -> None:
        with self._lock:
            runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            return
        peers = list(runtime.peers)
        for peer in peers:
            try:
                await peer.close()
            except Exception as error:
                log_event(
                    "media_peer_stop_error",
                    sessionId=session_id,
                    error=type(error).__name__,
                )
        if runtime.capturer is not None:
            try:
                await runtime.capturer.stop()
            except Exception as error:
                log_event(
                    "media_capturer_stop_error",
                    sessionId=session_id,
                    error=type(error).__name__,
                )
        log_event("media_runtime_stopped", sessionId=session_id)

    def stop_session_sync(self, session_id: str) -> None:
        """Sync wrapper for FastAPI sync route handlers."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.stop_session(session_id))
            return

        future = asyncio.run_coroutine_threadsafe(self.stop_session(session_id), loop)
        try:
            future.result(timeout=15)
        except Exception as error:
            log_event(
                "media_stop_sync_error",
                sessionId=session_id,
                error=type(error).__name__,
            )


media_registry = SessionMediaRegistry()
