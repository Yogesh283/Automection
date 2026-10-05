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
_main_process = None


def start_main_app(mobile_number, password_text):
    global _main_process

    if _main_process is not None and _main_process.poll() is None:
        return False

    env = os.environ.copy()
    env["DAMAN_PHONE"] = str(mobile_number)
    env["DAMAN_PASSWORD"] = str(password_text)

    _main_process = subprocess.Popen(
        [sys.executable, str(MAIN_FILE)],
        cwd=str(Path(__file__).parent),
        env=env,
    )
    return True


def connect_database():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        database="automection",
    )


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def save_settings_to_database(data):
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
        ORDER BY `id` DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()

    if row:
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
                row[0],
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO `settings` (
                `starting_amount`,
                `max_levels`,
                `level_amounts`,
                `choice`,
                `stop_loss`,
                `target_profit`
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                starting_amount,
                max_levels,
                level_amounts,
                choice,
                stop_loss,
                target_profit,
            ),
        )

    db.commit()
    cursor.close()
    db.close()


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
        save_settings_to_database(save_data)
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

    started = start_main_app(mobile_number, password_text)
    if started:
        message = "System start हो गया। Login अपने आप भरेगा।"
    else:
        message = "System पहले से चल रहा है।"

    return {
        "success": True,
        "started": started,
        "message": message,
        "data": save_data
    }
