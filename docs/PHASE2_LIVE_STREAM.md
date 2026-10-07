# Phase 2 — Chrome Live Streaming (manual E2E)

> Do **not** claim live streaming works until a WebRTC client has received a real video frame.
> On limited-RAM servers: test **one session only** first.

## 1. Required packages

Python (pip):

```bash
pip install -r requirements.txt
# includes: aiortc, av, numpy, pillow
```

Ubuntu host:

```bash
sudo apt update
sudo apt install -y xvfb ffmpeg imagemagick x11-utils
# Chrome + Selenium deps remain as documented in requirements.txt
```

## 2. Required environment variables

```bash
# auth / sessions (Phase 1)
JWT_SECRET=...
CHROME_PROFILES_ROOT=/home/updowanfx/chrome-profiles
SESSION_DISPLAY_BASE=100
SESSION_DISPLAY_WIDTH=1920
SESSION_DISPLAY_HEIGHT=1080

# WebRTC
WEBRTC_BACKEND=aiortc
WEBRTC_STUN_URL=stun:stun.l.google.com:19302
# Optional TURN (do not hardcode secrets in code):
WEBRTC_TURN_URL=
WEBRTC_TURN_USERNAME=
WEBRTC_TURN_PASSWORD=

CAPTURE_FPS=8
```

`:99` remains reserved for the existing web `POST /settings` bot path.

## 3. Start backend

```bash
cd /path/to/AutoMection
set -a; source .env; set +a
# Keep legacy :99 Xvfb for old frontend if you still use it:
pgrep -a 'Xvfb :99' || Xvfb :99 -screen 0 1920x1080x24 -ac -nolisten tcp >/tmp/xvfb99.log 2>&1 &

.venv-server/bin/uvicorn settings_server:app --host 127.0.0.1 --port 8090
```

## 4. Create app user + start ONE test session

```bash
python scripts/create_app_user.py --username viewer --password 'strong-pass'

TOKEN=$(curl -s -X POST http://127.0.0.1:8090/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"viewer","password":"strong-pass"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["accessToken"])')

# Start ONE session (game mobile/password are for the bot process only)
curl -s -X POST http://127.0.0.1:8090/api/session/start \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"mobileNumber":"YOUR_GAME_MOBILE","password":"YOUR_GAME_PASSWORD"}'
# Note sessionId + displayName from the JSON response
```

Verify Xvfb for that session (example `:100`):

```bash
xdpyinfo -display :100 | head
ps aux | grep '[X]vfb :100'
ls -la "$CHROME_PROFILES_ROOT"/user-*/session-*
```

## 5. Connect a WebRTC test client

Open `docs/webrtc_test_client.html` in a desktop browser (or serve it locally).

1. Paste API base URL (`https://updowanfx.com` or `http://127.0.0.1:8090`)
2. Paste access token and sessionId
3. Click **Connect**

The page:

- Opens `WSS /ws/webrtc/{sessionId}?access_token=...`
- Creates an SDP offer
- Applies the server answer
- Exchanges ICE candidates
- Renders `<video>` when a track arrives

## 6. Verify Chrome frames are actually received

Success criteria (all must be true):

1. Browser console shows `ontrack` fired
2. `<video>` element plays non-black / changing pixels (Chrome UI visible)
3. Server logs include `capturer_started`, `webrtc_track_attached`, `webrtc_answer_created`
4. Frame counter in the test client increases

If only PeerConnection exists but video stays black / no `ontrack`, live streaming is **not** confirmed.

## 7. Stop the session

```bash
curl -s -X POST http://127.0.0.1:8090/api/session/stop \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"sessionId":"SESSION_ID"}'
```

Confirm cleanup:

```bash
xdpyinfo -display :100   # should fail
ps aux | grep '[c]hrome' # session chrome gone
```

## 8. Inspect resource usage

```bash
free -h
ps -o pid,rss,cmd -C Xvfb
ps -o pid,rss,cmd -C chrome | head
ps -o pid,rss,cmd -C python | head
```

Only after **one** session is confirmed end-to-end should a second concurrent session be attempted.

## Automated tests

```bash
pytest -q
# Real Xvfb/ffmpeg integration test auto-skips on Windows / missing tools:
pytest -q tests/test_phase2_streaming.py -k integration
```
