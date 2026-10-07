import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from infra.db import db_cursor
from infra.logging_safe import log_event


@dataclass
class SessionRecord:
    sessionId: str
    userId: int
    processId: Optional[int]
    status: str
    profilePath: str
    displayName: Optional[str]
    createdAt: Optional[str]
    mobileNumber: Optional[str] = None


class SessionManager:
    """Persistent session registry with profile + display allocation interfaces."""

    STATUS_STARTING = "starting"
    STATUS_RUNNING = "running"
    STATUS_STOPPED = "stopped"
    STATUS_ERROR = "error"

    def __init__(self, profiles_root: Optional[str] = None):
        self.profiles_root = Path(
            profiles_root
            or os.getenv("CHROME_PROFILES_ROOT", "").strip()
            or str(Path(__file__).resolve().parent.parent / "chrome-profiles")
        )
        self.display_base = int(os.getenv("SESSION_DISPLAY_BASE", "100") or "100")

    def _new_session_id(self, user_id: int) -> str:
        return f"user_{user_id}_{uuid.uuid4().hex[:16]}"

    def allocate_display(self, user_id: int) -> str:
        """
        Phase 1: allocate a display name for the session data model.
        Does not start Xvfb yet — orchestration comes later.
        """
        with db_cursor(dictionary=True) as (db, cursor):
            cursor.execute(
                """
                SELECT `display_name`
                FROM `app_sessions`
                WHERE `status` IN ('starting', 'running')
                  AND `display_name` IS NOT NULL
                """
            )
            used = set()
            for row in cursor.fetchall() or []:
                name = row.get("display_name") or ""
                if name.startswith(":"):
                    try:
                        used.add(int(name[1:]))
                    except ValueError:
                        pass

        candidate = self.display_base
        while candidate in used:
            candidate += 1
        return f":{candidate}"

    def profile_path_for(self, user_id: int, session_id: str) -> Path:
        path = self.profiles_root / f"user-{user_id}" / f"session-{session_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_session(
        self,
        *,
        user_id: int,
        mobile_number: str,
        process_id: Optional[int] = None,
        status: str = STATUS_STARTING,
        display_name: Optional[str] = None,
        profile_path: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> SessionRecord:
        session_id = session_id or self._new_session_id(user_id)
        display_name = display_name or self.allocate_display(user_id)
        if profile_path:
            path = Path(profile_path)
            path.mkdir(parents=True, exist_ok=True)
            profile_path = str(path)
        else:
            profile_path = str(self.profile_path_for(user_id, session_id))

        with db_cursor() as (db, cursor):
            cursor.execute(
                """
                INSERT INTO `app_sessions` (
                    `session_id`, `user_id`, `process_id`, `status`,
                    `profile_path`, `display_name`, `mobile_number`
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    user_id,
                    process_id,
                    status,
                    str(profile_path),
                    display_name,
                    mobile_number,
                ),
            )

        log_event(
            "session_created",
            sessionId=session_id,
            userId=user_id,
            display=display_name,
        )
        return self.get_session(session_id)

    def update_session(
        self,
        session_id: str,
        *,
        process_id: Optional[int] = None,
        status: Optional[str] = None,
        stopped: bool = False,
    ) -> None:
        fields = []
        values = []
        if process_id is not None:
            fields.append("`process_id` = %s")
            values.append(process_id)
        if status is not None:
            fields.append("`status` = %s")
            values.append(status)
        if stopped:
            fields.append("`stopped_at` = %s")
            values.append(datetime.utcnow())
        if not fields:
            return
        values.append(session_id)
        with db_cursor() as (db, cursor):
            cursor.execute(
                f"UPDATE `app_sessions` SET {', '.join(fields)} WHERE `session_id` = %s",
                tuple(values),
            )

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with db_cursor(dictionary=True) as (db, cursor):
            cursor.execute(
                """
                SELECT `session_id`, `user_id`, `process_id`, `status`,
                       `profile_path`, `display_name`, `mobile_number`, `created_at`
                FROM `app_sessions`
                WHERE `session_id` = %s
                LIMIT 1
                """,
                (session_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        created = row.get("created_at")
        return SessionRecord(
            sessionId=row["session_id"],
            userId=int(row["user_id"]),
            processId=row.get("process_id"),
            status=row["status"],
            profilePath=row["profile_path"],
            displayName=row.get("display_name"),
            createdAt=created.isoformat(sep=" ", timespec="seconds") if created else None,
            mobileNumber=row.get("mobile_number"),
        )

    def get_owned_session(self, session_id: str, user_id: int) -> Optional[SessionRecord]:
        session = self.get_session(session_id)
        if session is None or session.userId != user_id:
            return None
        return session

    def list_user_sessions(self, user_id: int, active_only: bool = True):
        query = """
            SELECT `session_id`, `user_id`, `process_id`, `status`,
                   `profile_path`, `display_name`, `mobile_number`, `created_at`
            FROM `app_sessions`
            WHERE `user_id` = %s
        """
        params = [user_id]
        if active_only:
            query += " AND `status` IN ('starting', 'running')"
        query += " ORDER BY `created_at` DESC"
        with db_cursor(dictionary=True) as (db, cursor):
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall() or []
        sessions = []
        for row in rows:
            created = row.get("created_at")
            sessions.append(
                SessionRecord(
                    sessionId=row["session_id"],
                    userId=int(row["user_id"]),
                    processId=row.get("process_id"),
                    status=row["status"],
                    profilePath=row["profile_path"],
                    displayName=row.get("display_name"),
                    createdAt=(
                        created.isoformat(sep=" ", timespec="seconds") if created else None
                    ),
                    mobileNumber=row.get("mobile_number"),
                )
            )
        return sessions

    def to_dict(self, session: SessionRecord) -> dict:
        return asdict(session)


session_manager = SessionManager()
