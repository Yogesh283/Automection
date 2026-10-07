import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def _secret() -> str:
    secret = os.getenv("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET must be set in .env")
    return secret


def access_ttl_minutes() -> int:
    return int(os.getenv("JWT_ACCESS_TTL_MINUTES", "30") or "30")


def refresh_ttl_days() -> int:
    return int(os.getenv("JWT_REFRESH_TTL_DAYS", "14") or "14")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(*, user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=access_ttl_minutes())
    payload = {
        "sub": str(user_id),
        "username": username,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def create_refresh_token_value() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=refresh_ttl_days())


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    if payload.get("type") != "access":
        raise JWTError("Invalid token type")
    return payload
