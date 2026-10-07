from infra.db import db_cursor
from infra.security import hash_password, hash_token, refresh_expiry, verify_password


def create_user(username: str, password: str) -> int:
    password_hash = hash_password(password)
    with db_cursor() as (db, cursor):
        cursor.execute(
            """
            INSERT INTO `app_users` (`username`, `password_hash`)
            VALUES (%s, %s)
            """,
            (username, password_hash),
        )
        return cursor.lastrowid


def get_user_by_username(username: str):
    with db_cursor(dictionary=True) as (db, cursor):
        cursor.execute(
            """
            SELECT `id`, `username`, `password_hash`, `is_active`
            FROM `app_users`
            WHERE `username` = %s
            LIMIT 1
            """,
            (username,),
        )
        return cursor.fetchone()


def get_user_by_id(user_id: int):
    with db_cursor(dictionary=True) as (db, cursor):
        cursor.execute(
            """
            SELECT `id`, `username`, `password_hash`, `is_active`
            FROM `app_users`
            WHERE `id` = %s
            LIMIT 1
            """,
            (user_id,),
        )
        return cursor.fetchone()


def authenticate_user(username: str, password: str):
    user = get_user_by_username(username)
    if not user or not user.get("is_active"):
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def store_refresh_token(user_id: int, refresh_token: str) -> None:
    token_hash = hash_token(refresh_token)
    expires_at = refresh_expiry().replace(tzinfo=None)
    with db_cursor() as (db, cursor):
        cursor.execute(
            """
            INSERT INTO `app_refresh_tokens` (`user_id`, `token_hash`, `expires_at`)
            VALUES (%s, %s, %s)
            """,
            (user_id, token_hash, expires_at),
        )


def revoke_refresh_token(refresh_token: str) -> None:
    token_hash = hash_token(refresh_token)
    with db_cursor() as (db, cursor):
        cursor.execute(
            """
            UPDATE `app_refresh_tokens`
            SET `revoked` = 1
            WHERE `token_hash` = %s
            """,
            (token_hash,),
        )


def get_valid_refresh_row(refresh_token: str):
    token_hash = hash_token(refresh_token)
    with db_cursor(dictionary=True) as (db, cursor):
        cursor.execute(
            """
            SELECT t.`id`, t.`user_id`, t.`expires_at`, t.`revoked`,
                   u.`username`, u.`is_active`
            FROM `app_refresh_tokens` t
            JOIN `app_users` u ON u.`id` = t.`user_id`
            WHERE t.`token_hash` = %s
            LIMIT 1
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    if row["revoked"] or not row["is_active"]:
        return None
    if row["expires_at"] and row["expires_at"].timestamp() < __import__("time").time():
        return None
    return row
