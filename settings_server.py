import json
import os
import subprocess
import sys
from pathlib import Path

import mysql.connector
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
HTML_FILE = Path(__file__).parent / "bet-settings.html"
SETTINGS_FILE = Path(__file__).parent / "frontend-settings.json"
MAIN_FILE = Path(__file__).parent / "main.py"
_main_processes = {}


def start_main_app(mobile_number, password_text, user_id):
    process = _main_processes.get(mobile_number)
    if process is not None and process.poll() is None:
        return False

    env = os.environ.copy()
    env["DAMAN_PHONE"] = str(mobile_number)
    env["DAMAN_PASSWORD"] = str(password_text)
    env["DAMAN_USER_ID"] = str(user_id)
    env["PYTHONUNBUFFERED"] = "1"

    _main_processes[mobile_number] = subprocess.Popen(
        [sys.executable, "-u", str(MAIN_FILE)],
        cwd=str(Path(__file__).parent),
        env=env,
    )
    return True


def connect_database():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "automection"),
    )


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def save_settings_to_database(data, mobile_number):
    db = connect_database()
    cursor = db.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS `settings` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `mobile_number` VARCHAR(20),
            `starting_amount` INT,
            `max_levels` INT,
            `level_amounts` TEXT,
            `choice` VARCHAR(20),
            `stop_loss` INT,
            `target_profit` INT,
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute("SHOW COLUMNS FROM `settings`")
    columns = [row[0] for row in cursor.fetchall()]
    if "mobile_number" not in columns:
        cursor.execute("ALTER TABLE `settings` ADD COLUMN `mobile_number` VARCHAR(20) NULL")

    starting_amount = to_int(data.get("startingAmount"))
    max_levels = to_int(data.get("maxLevels"), 1)
    level_amounts = json.dumps(data.get("levelAmounts") or [])
    choice = str(data.get("choice") or "")
    stop_loss = to_int(data.get("stopLoss"))
    target_profit = to_int(data.get("targetProfit"))

    cursor.execute(
        """
        SELECT `id`
        FROM `settings`
        WHERE `mobile_number` = %s
        ORDER BY `id` DESC
        LIMIT 1
        """,
        (mobile_number,),
    )
    row = cursor.fetchone()

    if row:
        user_id = row[0]
        cursor.execute(
            """
            UPDATE `settings`
            SET `starting_amount` = %s,
                `max_levels` = %s,
                `level_amounts` = %s,
                `choice` = %s,
                `stop_loss` = %s,
                `target_profit` = %s
            WHERE `id` = %s
            """,
            (
                starting_amount,
                max_levels,
                level_amounts,
                choice,
                stop_loss,
                target_profit,
                user_id,
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO `settings` (
                `mobile_number`,
                `starting_amount`,
                `max_levels`,
                `level_amounts`,
                `choice`,
                `stop_loss`,
                `target_profit`
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                mobile_number,
                starting_amount,
                max_levels,
                level_amounts,
                choice,
                stop_loss,
                target_profit,
            ),
        )
        user_id = cursor.lastrowid

    db.commit()
    cursor.close()
    db.close()
    return user_id


@app.get("/")
@app.get("/settings")
def settings_page():
    return FileResponse(HTML_FILE)


@app.post("/settings")
def settings(data: dict):
    for key, value in data.items():
        if key not in ("password", "mobileNumber"):
            print(key, value)

    mobile_number = str(data.get("mobileNumber") or "").strip()
    password_text = str(data.get("password") or "")

    save_data = dict(data)
    save_data.pop("password", None)
    save_data.pop("mobileNumber", None)

    try:
        user_id = save_settings_to_database(save_data, mobile_number)
    except Exception as error:
        print("Database save failed:", error)
        return {
            "success": False,
            "error": "Database में save नहीं हुआ।",
        }

    SETTINGS_FILE.write_text(
        json.dumps(save_data, indent=2),
        encoding="utf-8",
    )

    if not mobile_number or not password_text:
        return {
            "success": False,
            "error": "Mobile number और password दोनों चाहिए।",
        }

    started = start_main_app(mobile_number, password_text, user_id)
    if started:
        message = f"User ID {user_id} start हो गया। Login अपने आप भरेगा।"
    else:
        message = f"User ID {user_id} पहले से चल रहा है।"

    return {
        "success": True,
        "started": started,
        "userId": user_id,
        "message": message,
        "data": save_data
    }
