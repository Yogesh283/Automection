from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import get_current_user
from infra.logging_safe import log_event
from session.display_manager import DisplayManager, display_manager
from session.manager import SessionManager, session_manager
from streaming.registry import media_registry

router = APIRouter(prefix="/api/session", tags=["session"])


class StartSessionRequest(BaseModel):
    mobileNumber: str = Field(min_length=5, max_length=20)
    password: str = Field(min_length=1, max_length=128)
    startingAmount: Optional[str] = None
    maxLevels: Optional[str] = None
    levelAmounts: Optional[list] = None
    choice: Optional[str] = None
    stopLoss: Optional[str] = None
    targetProfit: Optional[str] = None


def _bot_bridge():
    import settings_server as bot_host

    return bot_host


def _cleanup_partial(
    *,
    session_id: Optional[str],
    display_name: Optional[str],
    mobile: Optional[str],
    manager: SessionManager,
    displays: DisplayManager,
):
    if session_id:
        try:
            media_registry.stop_session_sync(session_id)
        except Exception:
            pass
        try:
            manager.update_session(
                session_id,
                status=SessionManager.STATUS_ERROR,
                stopped=True,
            )
        except Exception:
            pass
    if mobile:
        try:
            _bot_bridge().stop_main_app(mobile)
        except Exception:
            pass
    if display_name:
        try:
            displays.stop_and_release(display_name)
        except Exception:
            pass


@router.post("/start")
def start_session(
    body: StartSessionRequest,
    user=Depends(get_current_user),
    manager: SessionManager = Depends(lambda: session_manager),
    displays: DisplayManager = Depends(lambda: display_manager),
):
    bot_host = _bot_bridge()
    mobile = body.mobileNumber.strip()
    user_id = int(user["id"])

    save_data = {
        "startingAmount": body.startingAmount,
        "maxLevels": body.maxLevels,
        "levelAmounts": body.levelAmounts or [],
        "choice": body.choice,
        "stopLoss": body.stopLoss,
        "targetProfit": body.targetProfit,
    }
    try:
        settings_user_id = bot_host.save_settings_to_database(save_data, mobile)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save bot settings",
        )

    display_handle = None
    session = None
    try:
        # 1-2. Session id + profile path prepared via manager helpers
        session_id = manager._new_session_id(user_id)
        profile_path = str(manager.profile_path_for(user_id, session_id))

        # 3-4. Allocate and start Xvfb (never :99)
        display_handle = displays.allocate_and_start(session_id=session_id)

        # 5-7. Persist session + start existing bot process
        session = manager.create_session(
            user_id=user_id,
            mobile_number=mobile,
            status=SessionManager.STATUS_STARTING,
            display_name=display_handle.display,
            profile_path=profile_path,
            session_id=session_id,
        )
        media_registry.register_session(
            session_id=session.sessionId,
            display_name=session.displayName,
            profile_path=session.profilePath,
        )

        bot_host.start_main_app(
            mobile,
            body.password,
            settings_user_id,
            chrome_user_data_dir=session.profilePath,
            display=session.displayName,
            session_id=session.sessionId,
        )
        process = bot_host.get_main_process(mobile)
        pid = process.pid if process is not None else None
        manager.update_session(
            session.sessionId,
            process_id=pid,
            status=SessionManager.STATUS_RUNNING,
        )
    except Exception as error:
        _cleanup_partial(
            session_id=session.sessionId if session else (
                display_handle.session_id if display_handle else None
            ),
            display_name=display_handle.display if display_handle else None,
            mobile=mobile,
            manager=manager,
            displays=displays,
        )
        log_event(
            "session_start_failed",
            userId=user_id,
            error=type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start session",
        )

    session = manager.get_owned_session(session.sessionId, user_id)
    log_event(
        "session_started",
        sessionId=session.sessionId,
        userId=user_id,
        processId=session.processId,
        display=session.displayName,
    )
    return {
        "success": True,
        "started": True,
        "sessionId": session.sessionId,
        "userId": user_id,
        "message": "Session started",
        "session": manager.to_dict(session),
    }


class StopSessionRequest(BaseModel):
    sessionId: Optional[str] = None


@router.post("/stop")
def stop_session(
    body: StopSessionRequest = StopSessionRequest(),
    user=Depends(get_current_user),
    manager: SessionManager = Depends(lambda: session_manager),
    displays: DisplayManager = Depends(lambda: display_manager),
):
    sid = body.sessionId
    if not sid:
        active = manager.list_user_sessions(int(user["id"]), active_only=True)
        if not active:
            raise HTTPException(status_code=404, detail="No active session")
        sid = active[0].sessionId

    session = manager.get_owned_session(sid, int(user["id"]))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    bot_host = _bot_bridge()

    # 2. Stop bot
    if session.mobileNumber:
        try:
            bot_host.stop_main_app(session.mobileNumber)
        except Exception as error:
            log_event(
                "session_stop_bot_error",
                sessionId=session.sessionId,
                error=type(error).__name__,
            )

    # 3-4. Stop WebRTC peers + screen capture
    try:
        media_registry.stop_session_sync(session.sessionId)
    except Exception as error:
        log_event(
            "session_stop_media_error",
            sessionId=session.sessionId,
            error=type(error).__name__,
        )

    # 5-6. Stop Xvfb and release display
    try:
        displays.stop_and_release(session.displayName)
    except Exception as error:
        log_event(
            "session_stop_display_error",
            sessionId=session.sessionId,
            error=type(error).__name__,
        )

    # 7. Update DB
    manager.update_session(
        session.sessionId,
        status=SessionManager.STATUS_STOPPED,
        stopped=True,
    )
    log_event("session_stopped", sessionId=session.sessionId, userId=user["id"])
    updated = manager.get_owned_session(session.sessionId, int(user["id"]))
    return {
        "success": True,
        "stopped": True,
        "sessionId": session.sessionId,
        "session": manager.to_dict(updated) if updated else None,
    }


@router.get("/status")
def session_status(
    user=Depends(get_current_user),
    manager: SessionManager = Depends(lambda: session_manager),
):
    active = manager.list_user_sessions(int(user["id"]), active_only=True)
    return {
        "success": True,
        "userId": user["id"],
        "activeCount": len(active),
        "sessions": [manager.to_dict(item) for item in active],
    }


@router.get("/{sessionId}")
def get_session(
    sessionId: str,
    user=Depends(get_current_user),
    manager: SessionManager = Depends(lambda: session_manager),
):
    session = manager.get_owned_session(sessionId, int(user["id"]))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": True, "session": manager.to_dict(session)}
