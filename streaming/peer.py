"""
WebRTC peer abstraction backed by aiortc.

Frames from BrowserScreenCapturer become a VideoStreamTrack.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any, Callable, List, Optional

import numpy as np

from infra.logging_safe import log_event
from streaming.capturer import BrowserScreenCapturer


def ice_servers_from_env() -> List[dict]:
    servers: List[dict] = []
    stun = (os.getenv("WEBRTC_STUN_URL") or "stun:stun.l.google.com:19302").strip()
    if stun:
        servers.append({"urls": [stun]})
    turn = (os.getenv("WEBRTC_TURN_URL") or "").strip()
    if turn:
        entry: dict = {"urls": [turn]}
        username = (os.getenv("WEBRTC_TURN_USERNAME") or "").strip()
        credential = (os.getenv("WEBRTC_TURN_PASSWORD") or "").strip()
        if username:
            entry["username"] = username
        if credential:
            entry["credential"] = credential
        servers.append(entry)
    return servers


class WebRTCPeer(ABC):
    @abstractmethod
    async def attach_capturer(self, capturer: BrowserScreenCapturer) -> None:
        raise NotImplementedError

    @abstractmethod
    async def handle_offer(self, offer: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def create_offer(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def handle_answer(self, answer: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    async def add_ice_candidate(self, candidate: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_ice_candidate(self, callback: Callable[[dict], Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def connection_state(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def has_video_track(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class CapturerVideoTrack:
    """Lazy wrapper so aiortc import failure does not break non-media tests."""

    def __new__(cls, capturer: BrowserScreenCapturer):
        from aiortc import VideoStreamTrack
        from av import VideoFrame

        class _Track(VideoStreamTrack):
            kind = "video"

            def __init__(self, cap: BrowserScreenCapturer):
                super().__init__()
                self._capturer = cap
                self._fallback = np.zeros((480, 640, 3), dtype=np.uint8)

            async def recv(self):
                pts, time_base = await self.next_timestamp()
                frame_np = await asyncio.to_thread(self._capturer.latest_frame)
                if frame_np is None:
                    frame_np = self._fallback
                if frame_np.dtype != np.uint8:
                    frame_np = frame_np.astype(np.uint8)
                if frame_np.ndim != 3 or frame_np.shape[2] != 3:
                    raise RuntimeError("Capturer must produce HxWx3 RGB frames")
                video_frame = VideoFrame.from_ndarray(frame_np, format="rgb24")
                video_frame.pts = pts
                video_frame.time_base = time_base
                return video_frame

        return _Track(capturer)


class AiortcWebRTCPeer(WebRTCPeer):
    def __init__(self, session_id: str, ice_servers: Optional[List[dict]] = None):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

        self.session_id = session_id
        self.capturer: Optional[BrowserScreenCapturer] = None
        self._track = None
        self._closed = False
        self._ice_callback: Optional[Callable[[dict], Any]] = None
        self._has_video = False

        config_servers = []
        for entry in ice_servers if ice_servers is not None else ice_servers_from_env():
            urls = entry.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]
            kwargs = {"urls": urls}
            if entry.get("username"):
                kwargs["username"] = entry["username"]
            if entry.get("credential"):
                kwargs["credential"] = entry["credential"]
            config_servers.append(RTCIceServer(**kwargs))

        self.pc = RTCPeerConnection(
            configuration=RTCConfiguration(iceServers=config_servers)
        )

        @self.pc.on("icecandidate")
        def _on_ice(candidate):
            if candidate is None or self._ice_callback is None:
                return
            payload = {
                "candidate": candidate.candidate,
                "sdpMid": candidate.sdpMid,
                "sdpMLineIndex": candidate.sdpMLineIndex,
            }
            try:
                result = self._ice_callback(payload)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as error:
                log_event(
                    "webrtc_ice_callback_error",
                    sessionId=self.session_id,
                    error=type(error).__name__,
                )

        @self.pc.on("connectionstatechange")
        async def _on_state():
            log_event(
                "webrtc_connection_state",
                sessionId=self.session_id,
                state=self.pc.connectionState,
            )

    async def attach_capturer(self, capturer: BrowserScreenCapturer) -> None:
        self.capturer = capturer
        if self._track is None:
            self._track = CapturerVideoTrack(capturer)
            self.pc.addTrack(self._track)
            self._has_video = True
            log_event("webrtc_track_attached", sessionId=self.session_id)

    async def handle_offer(self, offer: dict) -> dict:
        from aiortc import RTCSessionDescription

        sdp = offer.get("sdp") or offer.get("payload", {}).get("sdp")
        if not sdp:
            raise ValueError("offer missing sdp")
        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type=offer.get("type") or "offer")
        )
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        local = self.pc.localDescription
        log_event("webrtc_answer_created", sessionId=self.session_id)
        return {"type": local.type, "sdp": local.sdp}

    async def create_offer(self) -> dict:
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        local = self.pc.localDescription
        return {"type": local.type, "sdp": local.sdp}

    async def handle_answer(self, answer: dict) -> None:
        from aiortc import RTCSessionDescription

        sdp = answer.get("sdp") or answer.get("payload", {}).get("sdp")
        if not sdp:
            raise ValueError("answer missing sdp")
        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type=answer.get("type") or "answer")
        )

    async def add_ice_candidate(self, candidate: dict) -> None:
        from aiortc.sdp import candidate_from_sdp

        payload = candidate.get("candidate") if isinstance(candidate.get("candidate"), dict) else candidate
        if payload is None:
            return
        cand_str = payload.get("candidate") if isinstance(payload, dict) else None
        if not cand_str:
            # End-of-candidates
            await self.pc.addIceCandidate(None)
            return
        # Browser may send "candidate:..." — strip prefix for candidate_from_sdp.
        sdp_line = cand_str
        if sdp_line.startswith("candidate:"):
            sdp_line = sdp_line[len("candidate:") :]
        ice = candidate_from_sdp(sdp_line)
        ice.sdpMid = payload.get("sdpMid")
        ice.sdpMLineIndex = payload.get("sdpMLineIndex")
        await self.pc.addIceCandidate(ice)

    def on_ice_candidate(self, callback: Callable[[dict], Any]) -> None:
        self._ice_callback = callback

    def connection_state(self) -> str:
        return getattr(self.pc, "connectionState", "unknown")

    def has_video_track(self) -> bool:
        return self._has_video

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.pc.close()
        except Exception as error:
            log_event(
                "webrtc_close_error",
                sessionId=self.session_id,
                error=type(error).__name__,
            )
        log_event("webrtc_peer_closed", sessionId=self.session_id)


class PlaceholderWebRTCPeer(WebRTCPeer):
    """Fallback when aiortc is unavailable."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.capturer: Optional[BrowserScreenCapturer] = None
        self._closed = False
        self._ice_callback = None
        self._has_video = False

    async def attach_capturer(self, capturer: BrowserScreenCapturer) -> None:
        self.capturer = capturer
        self._has_video = True

    async def handle_offer(self, offer: dict) -> dict:
        return {
            "type": "answer",
            "sdp": None,
            "note": "Placeholder peer — aiortc unavailable",
            "sessionId": self.session_id,
        }

    async def create_offer(self) -> dict:
        return {"type": "offer", "sdp": None, "sessionId": self.session_id}

    async def handle_answer(self, answer: dict) -> None:
        return None

    async def add_ice_candidate(self, candidate: dict) -> None:
        return None

    def on_ice_candidate(self, callback: Callable[[dict], Any]) -> None:
        self._ice_callback = callback

    def connection_state(self) -> str:
        return "closed" if self._closed else "new"

    def has_video_track(self) -> bool:
        return self._has_video

    async def close(self) -> None:
        self._closed = True
        # Capturer lifecycle is owned by SessionMediaRegistry, not the peer.


def build_peer(session_id: str, backend: Optional[str] = None) -> WebRTCPeer:
    choice = (backend or os.getenv("WEBRTC_BACKEND") or "aiortc").strip().lower()
    if choice == "placeholder":
        return PlaceholderWebRTCPeer(session_id)
    try:
        import aiortc  # noqa: F401

        return AiortcWebRTCPeer(session_id)
    except Exception as error:
        log_event(
            "webrtc_peer_fallback",
            sessionId=session_id,
            error=type(error).__name__,
        )
        return PlaceholderWebRTCPeer(session_id)
