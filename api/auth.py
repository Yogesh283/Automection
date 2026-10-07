from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from infra.logging_safe import log_event
from infra.security import (
    create_access_token,
    create_refresh_token_value,
)
from infra.users import (
    authenticate_user,
    get_user_by_id,
    get_valid_refresh_row,
    revoke_refresh_token,
    store_refresh_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refreshToken: str = Field(min_length=10)


@router.post("/login")
def login(body: LoginRequest):
    user = authenticate_user(body.username.strip(), body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    access = create_access_token(user_id=int(user["id"]), username=user["username"])
    refresh = create_refresh_token_value()
    store_refresh_token(int(user["id"]), refresh)

    log_event("auth_login", userId=int(user["id"]), username=user["username"])
    return {
        "success": True,
        "accessToken": access,
        "refreshToken": refresh,
        "tokenType": "bearer",
        "user": {"id": int(user["id"]), "username": user["username"]},
    }


@router.post("/refresh")
def refresh(body: RefreshRequest):
    row = get_valid_refresh_row(body.refreshToken)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user = get_user_by_id(int(row["user_id"]))
    if not user or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User inactive or not found",
        )

    # Rotate refresh token.
    revoke_refresh_token(body.refreshToken)
    access = create_access_token(user_id=int(user["id"]), username=user["username"])
    new_refresh = create_refresh_token_value()
    store_refresh_token(int(user["id"]), new_refresh)

    log_event("auth_refresh", userId=int(user["id"]))
    return {
        "success": True,
        "accessToken": access,
        "refreshToken": new_refresh,
        "tokenType": "bearer",
        "user": {"id": int(user["id"]), "username": user["username"]},
    }
