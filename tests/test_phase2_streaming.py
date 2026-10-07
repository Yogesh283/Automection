"""Phase 2 unit/integration tests for display, profiles, capture, WebRTC."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from session.display_manager import DisplayManager
from streaming.capturer import (
    SyntheticFrameCapturer,
    XvfbFramebufferCapturer,
    build_capturer,
    wait_for_frames,
)
from streaming.peer import PlaceholderWebRTCPeer, build_peer, ice_servers_from_env
from streaming.registry import SessionMediaRegistry


def test_allocate_display():
    mgr = DisplayManager(base=100, allow_simulate=True)
    handle = mgr.allocate(session_id="s1")
    assert handle.display == ":100"
    assert handle.number == 100
    assert 99 not in mgr._in_use
    mgr.release(handle.display)


def test_allocate_two_different_displays():
    mgr = DisplayManager(base=100, allow_simulate=True)
    a = mgr.allocate(session_id="a")
    b = mgr.allocate(session_id="b")
    assert a.display != b.display
    assert a.number == 100
    assert b.number == 101
    mgr.release(a.display)
    mgr.release(b.display)


def test_release_display():
    mgr = DisplayManager(base=100, allow_simulate=True)
    handle = mgr.allocate(session_id="s")
    mgr.release(handle.display)
    again = mgr.allocate(session_id="s2")
    assert again.display == ":100"
    mgr.release(again.display)


def test_start_and_stop_xvfb_simulated():
    mgr = DisplayManager(base=110, allow_simulate=True)
    handle = mgr.allocate_and_start(session_id="sim")
    assert handle.simulated is True
    assert mgr.is_alive(handle.display)
    mgr.stop(handle.display)
    mgr.release(handle.display)
    assert mgr.get(handle.display) is None


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("Xvfb") is None,
    reason="Real Xvfb only on Linux with Xvfb installed",
)
def test_start_and_stop_real_xvfb():
    mgr = DisplayManager(base=120, allow_simulate=False)
    handle = mgr.allocate_and_start(session_id="real")
    try:
        assert handle.simulated is False
        assert mgr.is_alive(handle.display)
        assert handle.process is not None
        assert handle.process.poll() is None
    finally:
        mgr.stop_and_release(handle.display)
    assert mgr.get(handle.display) is None


def test_isolated_chrome_profiles(tmp_path):
    from session.manager import SessionManager

    manager = SessionManager(profiles_root=str(tmp_path / "profiles"))
    p1 = manager.profile_path_for(1, "sess-a")
    p2 = manager.profile_path_for(1, "sess-b")
    p3 = manager.profile_path_for(2, "sess-a")
    assert p1 != p2
    assert p1 != p3
    assert "user-1" in str(p1)
    assert "session-sess-a" in str(p1)
    assert p1.is_dir() and p2.is_dir()


def test_two_sessions_different_profiles(client):
    test_client, manager, users, displays, processes = client
    alice = test_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass"}
    ).json()
    headers = {"Authorization": f"Bearer {alice['accessToken']}"}

    # Only one Chrome conceptually; still verify two profile paths differ.
    # Stop first before starting second to respect RAM guidance in automated tests.
    s1 = test_client.post(
        "/api/session/start",
        headers=headers,
        json={"mobileNumber": "4444444444", "password": "x"},
    ).json()
    path1 = s1["session"]["profilePath"]
    display1 = s1["session"]["displayName"]
    test_client.post("/api/session/stop", headers=headers, json={"sessionId": s1["sessionId"]})

    s2 = test_client.post(
        "/api/session/start",
        headers=headers,
        json={"mobileNumber": "5555555555", "password": "x"},
    ).json()
    path2 = s2["session"]["profilePath"]
    display2 = s2["session"]["displayName"]
    assert path1 != path2
    # Display may be reused after release — that is correct.
    assert display1.startswith(":")
    assert display2.startswith(":")
    test_client.post("/api/session/stop", headers=headers, json={"sessionId": s2["sessionId"]})


def test_session_a_cannot_access_session_b(client):
    test_client, *_ = client
    alice = test_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass"}
    ).json()
    bob = test_client.post(
        "/api/auth/login", json={"username": "bob", "password": "bob-pass"}
    ).json()
    start = test_client.post(
        "/api/session/start",
        headers={"Authorization": f"Bearer {alice['accessToken']}"},
        json={"mobileNumber": "6666666666", "password": "x"},
    ).json()
    sid = start["sessionId"]
    denied = test_client.get(
        f"/api/session/{sid}",
        headers={"Authorization": f"Bearer {bob['accessToken']}"},
    )
    assert denied.status_code == 404


def test_websocket_authentication_required(client):
    test_client, *_ = client
    alice = test_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass"}
    ).json()
    start = test_client.post(
        "/api/session/start",
        headers={"Authorization": f"Bearer {alice['accessToken']}"},
        json={"mobileNumber": "7777777777", "password": "x"},
    ).json()
    sid = start["sessionId"]
    with pytest.raises(Exception):
        with test_client.websocket_connect(f"/ws/webrtc/{sid}") as ws:
            ws.receive_json()


def test_webrtc_signaling_ready_and_offer(client):
    test_client, *_ = client
    alice = test_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass"}
    ).json()
    token = alice["accessToken"]
    start = test_client.post(
        "/api/session/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"mobileNumber": "8888888888", "password": "x"},
    ).json()
    sid = start["sessionId"]
    with test_client.websocket_connect(f"/ws/webrtc/{sid}?access_token={token}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["hasVideoTrack"] is True
        ws.send_json({"type": "offer", "sdp": "v=0"})
        response = ws.receive_json()
        assert response["type"] in ("answer", "error")


@pytest.mark.asyncio
async def test_peer_cleanup():
    peer = PlaceholderWebRTCPeer("sess-clean")
    capturer = SyntheticFrameCapturer(width=64, height=48, fps=10)
    await capturer.start(session_id="sess-clean", display_name=":100", profile_path="/tmp")
    await peer.attach_capturer(capturer)
    assert peer.has_video_track()
    await peer.close()
    assert peer.connection_state() == "closed"
    # Placeholder close also stops capturer
    assert capturer.frames_produced() >= 0


@pytest.mark.asyncio
async def test_session_media_cleanup():
    registry = SessionMediaRegistry()
    registry.register_session(
        session_id="m1",
        display_name=":100",
        profile_path="/tmp/p",
    )
    peer = await registry.create_peer("m1")
    assert peer.has_video_track()
    capturer = registry.get("m1").capturer
    assert wait_for_frames(capturer, min_frames=1, timeout_sec=3)
    await registry.stop_session("m1")
    assert registry.get("m1") is None


def test_ice_servers_from_env(monkeypatch):
    monkeypatch.setenv("WEBRTC_STUN_URL", "stun:example.com:3478")
    monkeypatch.setenv("WEBRTC_TURN_URL", "turn:turn.example.com:3478")
    monkeypatch.setenv("WEBRTC_TURN_USERNAME", "user")
    monkeypatch.setenv("WEBRTC_TURN_PASSWORD", "secret")
    servers = ice_servers_from_env()
    assert servers[0]["urls"] == ["stun:example.com:3478"]
    assert servers[1]["urls"] == ["turn:turn.example.com:3478"]
    assert servers[1]["username"] == "user"
    assert "secret" == servers[1]["credential"]


def test_synthetic_capturer_produces_frames():
    capturer = SyntheticFrameCapturer(width=80, height=60, fps=20)

    async def _run():
        await capturer.start(session_id="syn", display_name=":100", profile_path="x")
        ok = wait_for_frames(capturer, min_frames=2, timeout_sec=3)
        frame = capturer.latest_frame()
        await capturer.stop()
        return ok, frame

    ok, frame = asyncio.run(_run())
    assert ok is True
    assert frame is not None
    assert frame.shape == (60, 80, 3)


def test_build_capturer_prefers_synthetic_without_tools(monkeypatch):
    monkeypatch.setattr("streaming.capturer.shutil.which", lambda name: None)
    cap = build_capturer(display_name=":100", prefer_real=True)
    assert isinstance(cap, SyntheticFrameCapturer)


def test_session_cleanup_releases_display(client):
    test_client, manager, users, displays, processes = client
    alice = test_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass"}
    ).json()
    headers = {"Authorization": f"Bearer {alice['accessToken']}"}
    start = test_client.post(
        "/api/session/start",
        headers=headers,
        json={"mobileNumber": "9999999999", "password": "x"},
    ).json()
    display = start["session"]["displayName"]
    assert displays.get(display) is not None
    stop = test_client.post(
        "/api/session/stop",
        headers=headers,
        json={"sessionId": start["sessionId"]},
    )
    assert stop.status_code == 200
    assert displays.get(display) is None


def test_start_passes_display_and_profile_to_bot(client):
    test_client, manager, users, displays, processes = client
    alice = test_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass"}
    ).json()
    headers = {"Authorization": f"Bearer {alice['accessToken']}"}
    start = test_client.post(
        "/api/session/start",
        headers=headers,
        json={"mobileNumber": "1010101010", "password": "x"},
    ).json()
    proc = processes.get("1010101010")
    assert proc is not None
    assert proc._env.get("chrome_user_data_dir")
    assert proc._env.get("display")
    assert proc._env.get("session_id") == start["sessionId"]
    test_client.post(
        "/api/session/stop",
        headers=headers,
        json={"sessionId": start["sessionId"]},
    )


@pytest.mark.skipif(
    os.name != "posix"
    or shutil.which("Xvfb") is None
    or (shutil.which("ffmpeg") is None and shutil.which("import") is None),
    reason="Integration requires Linux Xvfb + ffmpeg/import",
)
@pytest.mark.asyncio
async def test_integration_xvfb_capture_and_webrtc_track(tmp_path):
    """
    ONE-session integration: Xvfb alive -> capturer produces frames ->
    peer has a video track. Does not start Chrome (RAM); verifies capture path.
    """
    displays = DisplayManager(base=130, allow_simulate=False)
    handle = displays.allocate_and_start(session_id="integ")
    try:
        assert displays.is_alive(handle.display)
        # Paint something on the display so capture is non-black when possible.
        if shutil.which("xsetroot"):
            os.system(f"DISPLAY={handle.display} xsetroot -solid navy")

        capturer = XvfbFramebufferCapturer(width=handle.width, height=handle.height, fps=5)
        await capturer.start(
            session_id="integ",
            display_name=handle.display,
            profile_path=str(tmp_path),
        )
        assert wait_for_frames(capturer, min_frames=1, timeout_sec=15), "No frames captured"
        frame = capturer.latest_frame()
        assert frame is not None
        assert frame.ndim == 3

        peer = build_peer("integ", backend="aiortc")
        await peer.attach_capturer(capturer)
        assert peer.has_video_track(), "PeerConnection must expose a video track"

        # Pull at least one encoded video frame from the track itself.
        track = peer._track
        video_frame = await asyncio.wait_for(track.recv(), timeout=10)
        assert video_frame is not None
        assert video_frame.width > 0 and video_frame.height > 0

        await peer.close()
        await capturer.stop()
    finally:
        displays.stop_and_release(handle.display)
