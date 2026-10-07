# UpdowanFX Android APK (client-only)

Client APK for app login, session start/stop, and WebRTC live view.
**No Selenium, no game automation, no VNC inside the APK.**

## Build

```bat
cd android
set JAVA_HOME=C:\Program Files\Android\Android Studio\jbr
gradlew.bat assembleDebug
```

APK output:

```
android/app/build/outputs/apk/debug/app-debug.apk
```

Install:

```bat
adb install -r android\app\build\outputs\apk\debug\app-debug.apk
```

## App flow

1. **Sign in** — app username/password → `POST /api/auth/login`
2. **Start session** — game mobile/password → `POST /api/session/start`
3. **Live view** — WebView WebRTC → `WSS /ws/webrtc/{sessionId}?access_token=...`
4. **Stop session** — `POST /api/session/stop`

Default API base: `https://updowanfx.com` (editable on login screen).

## Server prerequisites

- Phase 1 + Phase 2 backend deployed
- App user created: `python scripts/create_app_user.py --username ... --password ...`
- One session only on low-RAM hosts until verified
