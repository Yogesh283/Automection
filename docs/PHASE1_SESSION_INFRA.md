# Phase 1 — Backend Session Infrastructure

## New API endpoints

| Method | Path | Auth |
|--------|------|------|
| POST | `/api/auth/login` | No |
| POST | `/api/auth/refresh` | Refresh token |
| POST | `/api/session/start` | Bearer access token |
| POST | `/api/session/stop` | Bearer access token |
| GET | `/api/session/status` | Bearer access token |
| GET | `/api/session/{sessionId}` | Bearer access token |
| WS | `/ws/webrtc/{sessionId}?access_token=...` | Access token query |

Existing web form endpoints remain unchanged:
- `GET /settings`
- `POST /settings`

## Create an app user

```bash
python scripts/create_app_user.py --username alice --password "strong-password"
```

App login credentials are separate from game mobile/password.

## Run tests

```bash
pip install -r requirements.txt
pytest -q
```

## What remains before live WebRTC video works

1. Implement real `BrowserScreenCapturer` (Xvfb grab or Chrome CDP screencast).
2. Implement real `WebRTCPeer` with `aiortc` + STUN/TURN from config.
3. Bridge capturer frames into the peer track.
4. Start/stop media when a viewer connects to `/ws/webrtc/{sessionId}`.
5. Allocate/start per-session Xvfb displays (`:100+`) instead of shared `:99` only.
6. End-to-end verify on a real Android device/emulator.
