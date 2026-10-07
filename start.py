"""
One command: start frontend (settings UI) + FastAPI backend.
The bot starts automatically when you click Start Bot.

  python start.py
  or:  run.bat
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise SystemExit(f"Set {name} in .env.")
    return value


def main() -> None:
    host = require_env("APP_HOST")
    port = int(require_env("APP_PORT"))
    app_url = require_env("APP_URL").rstrip("/")
    settings_url = f"{app_url}/settings"
    game_site = require_env("GAME_SITE")

    print("=" * 50)
    print("AutoMection local server")
    print(f"Frontend + API : {settings_url}")
    print(f"Game site      : {game_site}")
    print("Open the form and click Start Bot to launch Chrome.")
    print("=" * 50)

    def open_browser() -> None:
        time.sleep(1.2)
        try:
            webbrowser.open(settings_url)
        except Exception:
            pass

    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn

    uvicorn.run(
        "settings_server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
