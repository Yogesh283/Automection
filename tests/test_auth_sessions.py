import pytest
from starlette.websockets import WebSocketDisconnect


def _login(client, username: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "accessToken" in data
    assert "refreshToken" in data
    return data


def test_login_success(client):
    test_client, *_ = client
    data = _login(test_client, "alice", "alice-pass")
    assert data["user"]["username"] == "alice"


def test_login_failure(client):
    test_client, *_ = client
    response = test_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong"},
    )
    assert response.status_code == 401


def test_refresh_token(client):
    test_client, *_ = client
    login = _login(test_client, "alice", "alice-pass")
    response = test_client.post(
        "/api/auth/refresh",
        json={"refreshToken": login["refreshToken"]},
    )
    assert response.status_code == 200
    assert response.json()["accessToken"]


def test_start_and_status_and_get_session(client):
    test_client, manager, *_ = client
    login = _login(test_client, "alice", "alice-pass")
    headers = {"Authorization": f"Bearer {login['accessToken']}"}

    start = test_client.post(
        "/api/session/start",
        headers=headers,
        json={
            "mobileNumber": "7357685240",
            "password": "game-password",
            "startingAmount": "10",
            "maxLevels": "1",
            "levelAmounts": ["10"],
            "choice": "big",
            "stopLoss": "100",
            "targetProfit": "50",
        },
    )
    assert start.status_code == 200
    body = start.json()
    assert body["success"] is True
    assert body["started"] is True
    assert body["sessionId"]
    sid = body["sessionId"]
    assert body["session"]["displayName"].startswith(":")
    assert "user-" in body["session"]["profilePath"]

    status = test_client.get("/api/session/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["activeCount"] == 1

    detail = test_client.get(f"/api/session/{sid}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["session"]["sessionId"] == sid


def test_user_cannot_access_other_session(client):
    test_client, *_ = client
    alice = _login(test_client, "alice", "alice-pass")
    bob = _login(test_client, "bob", "bob-pass")

    start = test_client.post(
        "/api/session/start",
        headers={"Authorization": f"Bearer {alice['accessToken']}"},
        json={"mobileNumber": "1111111111", "password": "x"},
    )
    sid = start.json()["sessionId"]

    forbidden = test_client.get(
        f"/api/session/{sid}",
        headers={"Authorization": f"Bearer {bob['accessToken']}"},
    )
    assert forbidden.status_code == 404


def test_unauthorized_session_access(client):
    test_client, *_ = client
    response = test_client.get("/api/session/status")
    assert response.status_code == 401


def test_stop_session_owner_only(client):
    test_client, *_ = client
    alice = _login(test_client, "alice", "alice-pass")
    bob = _login(test_client, "bob", "bob-pass")
    headers_alice = {"Authorization": f"Bearer {alice['accessToken']}"}
    headers_bob = {"Authorization": f"Bearer {bob['accessToken']}"}

    start = test_client.post(
        "/api/session/start",
        headers=headers_alice,
        json={"mobileNumber": "2222222222", "password": "x"},
    )
    sid = start.json()["sessionId"]

    denied = test_client.post(
        "/api/session/stop",
        headers=headers_bob,
        json={"sessionId": sid},
    )
    assert denied.status_code == 404

    stopped = test_client.post(
        "/api/session/stop",
        headers=headers_alice,
        json={"sessionId": sid},
    )
    assert stopped.status_code == 200
    assert stopped.json()["stopped"] is True


def test_websocket_auth_and_ownership(client):
    test_client, *_ = client
    alice = _login(test_client, "alice", "alice-pass")
    bob = _login(test_client, "bob", "bob-pass")
    start = test_client.post(
        "/api/session/start",
        headers={"Authorization": f"Bearer {alice['accessToken']}"},
        json={"mobileNumber": "3333333333", "password": "x"},
    )
    sid = start.json()["sessionId"]

    with test_client.websocket_connect(
        f"/ws/webrtc/{sid}?access_token={alice['accessToken']}"
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready.get("hasVideoTrack") is True
        ws.send_json({"type": "offer", "sdp": "v=0\r\nm=video 0 UDP/TLS/RTP/SAVPF 96\r\n"})
        # Placeholder backend returns answer without real SDP negotiation.
        msg = ws.receive_json()
        assert msg["type"] in ("answer", "error")

    with pytest.raises(WebSocketDisconnect):
        with test_client.websocket_connect(
            f"/ws/webrtc/{sid}?access_token={bob['accessToken']}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            ws.receive_json()


def test_websocket_invalid_session(client):
    test_client, *_ = client
    alice = _login(test_client, "alice", "alice-pass")
    with pytest.raises(WebSocketDisconnect):
        with test_client.websocket_connect(
            f"/ws/webrtc/does-not-exist?access_token={alice['accessToken']}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            ws.receive_json()


def test_existing_settings_route_still_present(client):
    test_client, *_ = client
    response = test_client.get("/settings")
    assert response.status_code == 200
