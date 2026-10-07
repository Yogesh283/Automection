import json
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError

from infra.logging_safe import log_event
from infra.security import decode_access_token
from infra.users import get_user_by_id
from session.manager import session_manager
from streaming.registry import media_registry

router = APIRouter(tags=["webrtc"])


async def _authenticate_ws(websocket: WebSocket, token: Optional[str]) -> dict:
    if not token:
        await websocket.close(code=4401)
        raise PermissionError("missing token")
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        await websocket.close(code=4401)
        raise PermissionError("invalid token")

    user = get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        await websocket.close(code=4401)
        raise PermissionError("inactive user")
    return {"id": int(user["id"]), "username": user["username"]}


@router.websocket("/ws/webrtc/{sessionId}")
async def webrtc_signaling(
    websocket: WebSocket,
    sessionId: str,
    access_token: Optional[str] = Query(default=None),
):
    """
    Authenticated WebRTC signaling for a single owned session.
    Attaches a real video track from the session capturer when available.
    """
    await websocket.accept()
    peer = None
    user = None
    try:
        user = await _authenticate_ws(websocket, access_token)
    except PermissionError:
        return

    session = session_manager.get_owned_session(sessionId, int(user["id"]))
    if session is None:
        log_event(
            "webrtc_unauthorized_session",
            sessionId=sessionId,
            userId=user["id"],
        )
        await websocket.send_json(
            {"type": "error", "error": "Session not found or not owned by user"}
        )
        await websocket.close(code=4403)
        return

    # Ensure media runtime exists even if start registered it already.
    media_registry.register_session(
        session_id=session.sessionId,
        display_name=session.displayName,
        profile_path=session.profilePath,
    )

    try:
        peer = await media_registry.create_peer(session.sessionId)
    except Exception as error:
        log_event(
            "webrtc_peer_create_error",
            sessionId=sessionId,
            userId=user["id"],
            error=type(error).__name__,
        )
        await websocket.send_json(
            {"type": "error", "error": "Failed to create WebRTC peer"}
        )
        await websocket.close(code=1011)
        return

    async def _send_ice(candidate: dict):
        try:
            await websocket.send_json(
                {
                    "type": "ice-candidate",
                    "sessionId": sessionId,
                    "candidate": candidate,
                }
            )
        except Exception:
            pass

    peer.on_ice_candidate(_send_ice)

    log_event(
        "webrtc_ws_connected",
        sessionId=sessionId,
        userId=user["id"],
        hasVideo=peer.has_video_track(),
    )
    await websocket.send_json(
        {
            "type": "ready",
            "sessionId": sessionId,
            "hasVideoTrack": peer.has_video_track(),
            "message": "Signaling connected. Send an SDP offer to receive an answer.",
        }
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "error": "Invalid JSON"})
                continue

            msg_type = str(message.get("type") or "")
            try:
                if msg_type == "offer":
                    answer = await peer.handle_offer(message)
                    await websocket.send_json(
                        {
                            "type": "answer",
                            "sessionId": sessionId,
                            "sdp": answer.get("sdp"),
                            "payload": answer,
                        }
                    )
                elif msg_type == "answer":
                    await peer.handle_answer(message)
                    await websocket.send_json({"type": "ack", "for": "answer"})
                elif msg_type in ("ice-candidate", "ice"):
                    candidate = message.get("candidate") or message.get("payload") or message
                    await peer.add_ice_candidate(
                        candidate if isinstance(candidate, dict) else message
                    )
                    await websocket.send_json({"type": "ack", "for": "ice-candidate"})
                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json(
                        {"type": "error", "error": f"Unsupported message type: {msg_type}"}
                    )
            except Exception as error:
                log_event(
                    "webrtc_signaling_error",
                    sessionId=sessionId,
                    userId=user["id"],
                    error=type(error).__name__,
                )
                await websocket.send_json(
                    {"type": "error", "error": f"Signaling failure: {type(error).__name__}"}
                )
    except WebSocketDisconnect:
        log_event(
            "webrtc_ws_disconnected",
            sessionId=sessionId,
            userId=user["id"] if user else None,
        )
    except Exception as error:
        log_event(
            "webrtc_signaling_error",
            sessionId=sessionId,
            userId=user["id"] if user else None,
            error=type(error).__name__,
        )
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if peer is not None:
            try:
                await media_registry.close_peer(sessionId, peer)
            except Exception:
                pass
