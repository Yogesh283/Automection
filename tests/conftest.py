import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault("JWT_ACCESS_TTL_MINUTES", "30")
os.environ.setdefault("JWT_REFRESH_TTL_DAYS", "14")
os.environ.setdefault("CHROME_PROFILES_ROOT", str(ROOT / "chrome-profiles-test"))
os.environ.setdefault("WEBRTC_STUN_URL", "stun:stun.l.google.com:19302")
os.environ.setdefault("WEBRTC_BACKEND", "placeholder")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Avoid real MySQL / real bot process during unit tests.
    users = {}
    refresh_rows = {}
    sessions = {}
    processes = {}

    def fake_ensure():
        return None

    def fake_create_user(username, password):
        from infra.security import hash_password

        user_id = len(users) + 1
        users[user_id] = {
            "id": user_id,
            "username": username,
            "password_hash": hash_password(password),
            "is_active": 1,
        }
        return user_id

    def fake_get_by_username(username):
        for user in users.values():
            if user["username"] == username:
                return user
        return None

    def fake_get_by_id(user_id):
        return users.get(int(user_id))

    def fake_authenticate(username, password):
        from infra.security import verify_password

        user = fake_get_by_username(username)
        if not user or not verify_password(password, user["password_hash"]):
            return None
        return user

    def fake_store_refresh(user_id, token):
        from infra.security import hash_token, refresh_expiry

        refresh_rows[hash_token(token)] = {
            "user_id": user_id,
            "expires_at": refresh_expiry().replace(tzinfo=None),
            "revoked": 0,
            "username": users[user_id]["username"],
            "is_active": 1,
        }

    def fake_revoke(token):
        from infra.security import hash_token

        row = refresh_rows.get(hash_token(token))
        if row:
            row["revoked"] = 1

    def fake_valid_refresh(token):
        from infra.security import hash_token
        import time

        row = refresh_rows.get(hash_token(token))
        if not row or row["revoked"] or not row["is_active"]:
            return None
        if row["expires_at"].timestamp() < time.time():
            return None
        return row

    class FakeSessionManager:
        STATUS_STARTING = "starting"
        STATUS_RUNNING = "running"
        STATUS_STOPPED = "stopped"
        STATUS_ERROR = "error"

        def _new_session_id(self, user_id):
            import uuid

            return f"user_{user_id}_{uuid.uuid4().hex[:8]}"

        def profile_path_for(self, user_id, session_id):
            path = tmp_path / f"user-{user_id}" / f"session-{session_id}"
            path.mkdir(parents=True, exist_ok=True)
            return path

        def create_session(
            self,
            *,
            user_id,
            mobile_number,
            process_id=None,
            status="starting",
            display_name=None,
            profile_path=None,
            session_id=None,
        ):
            from session.manager import SessionRecord

            sid = session_id or self._new_session_id(user_id)
            profile = Path(profile_path) if profile_path else self.profile_path_for(user_id, sid)
            profile.mkdir(parents=True, exist_ok=True)
            record = SessionRecord(
                sessionId=sid,
                userId=user_id,
                processId=process_id,
                status=status,
                profilePath=str(profile),
                displayName=display_name or ":100",
                createdAt="2026-01-01 00:00:00",
                mobileNumber=mobile_number,
            )
            sessions[sid] = record
            return record

        def update_session(self, session_id, *, process_id=None, status=None, stopped=False):
            session = sessions[session_id]
            if process_id is not None:
                session.processId = process_id
            if status is not None:
                session.status = status

        def get_session(self, session_id):
            return sessions.get(session_id)

        def get_owned_session(self, session_id, user_id):
            session = sessions.get(session_id)
            if session is None or session.userId != user_id:
                return None
            return session

        def list_user_sessions(self, user_id, active_only=True):
            items = [s for s in sessions.values() if s.userId == user_id]
            if active_only:
                items = [s for s in items if s.status in ("starting", "running")]
            return items

        def to_dict(self, session):
            from dataclasses import asdict

            return asdict(session)

    fake_manager = FakeSessionManager()

    class FakeProc:
        def __init__(self):
            self.pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_start_main_app(mobile, password, user_id, **kwargs):
        processes[mobile] = FakeProc()
        processes[mobile]._env = kwargs
        return True

    def fake_stop_main_app(mobile):
        processes.pop(mobile, None)

    def fake_get_main_process(mobile):
        return processes.get(mobile)

    def fake_save_settings(data, mobile):
        return 99

    # Force simulated displays in API tests (no real Xvfb).
    from session.display_manager import DisplayManager

    test_displays = DisplayManager(base=100, allow_simulate=True)

    monkeypatch.setattr("infra.db.ensure_auth_session_schema", fake_ensure)
    monkeypatch.setattr("infra.users.create_user", fake_create_user)
    monkeypatch.setattr("infra.users.get_user_by_username", fake_get_by_username)
    monkeypatch.setattr("infra.users.get_user_by_id", fake_get_by_id)
    monkeypatch.setattr("infra.users.authenticate_user", fake_authenticate)
    monkeypatch.setattr("infra.users.store_refresh_token", fake_store_refresh)
    monkeypatch.setattr("infra.users.revoke_refresh_token", fake_revoke)
    monkeypatch.setattr("infra.users.get_valid_refresh_row", fake_valid_refresh)
    monkeypatch.setattr("session.manager.session_manager", fake_manager)
    monkeypatch.setattr("api.session_routes.session_manager", fake_manager)
    monkeypatch.setattr("api.webrtc_signaling.session_manager", fake_manager)
    import session.display_manager as display_mod

    monkeypatch.setattr(display_mod, "display_manager", test_displays)
    monkeypatch.setattr("api.session_routes.display_manager", test_displays)

    import settings_server

    monkeypatch.setattr(settings_server, "start_main_app", fake_start_main_app)
    monkeypatch.setattr(settings_server, "stop_main_app", fake_stop_main_app)
    monkeypatch.setattr(settings_server, "get_main_process", fake_get_main_process)
    monkeypatch.setattr(settings_server, "save_settings_to_database", fake_save_settings)

    fake_create_user("alice", "alice-pass")
    fake_create_user("bob", "bob-pass")

    with TestClient(settings_server.app) as test_client:
        yield test_client, fake_manager, users, test_displays, processes
