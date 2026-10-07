import os
from contextlib import contextmanager

import mysql.connector


def connect_database():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "automection"),
    )


@contextmanager
def db_cursor(dictionary=False):
    db = connect_database()
    cursor = db.cursor(dictionary=dictionary)
    try:
        yield db, cursor
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cursor.close()
        db.close()


def ensure_auth_session_schema() -> None:
    with db_cursor() as (db, cursor):
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS `app_users` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `username` VARCHAR(64) NOT NULL UNIQUE,
                `password_hash` VARCHAR(255) NOT NULL,
                `is_active` TINYINT(1) NOT NULL DEFAULT 1,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS `app_refresh_tokens` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `user_id` INT NOT NULL,
                `token_hash` VARCHAR(64) NOT NULL UNIQUE,
                `expires_at` DATETIME NOT NULL,
                `revoked` TINYINT(1) NOT NULL DEFAULT 0,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX (`user_id`),
                CONSTRAINT `fk_refresh_user`
                    FOREIGN KEY (`user_id`) REFERENCES `app_users`(`id`)
                    ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS `app_sessions` (
                `session_id` VARCHAR(64) PRIMARY KEY,
                `user_id` INT NOT NULL,
                `process_id` INT NULL,
                `status` VARCHAR(32) NOT NULL,
                `profile_path` VARCHAR(512) NOT NULL,
                `display_name` VARCHAR(16) NULL,
                `mobile_number` VARCHAR(32) NULL,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                `stopped_at` DATETIME NULL,
                INDEX (`user_id`),
                INDEX (`status`),
                CONSTRAINT `fk_session_user`
                    FOREIGN KEY (`user_id`) REFERENCES `app_users`(`id`)
                    ON DELETE CASCADE
            )
            """
        )
