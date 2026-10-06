"""
एक कमांड: frontend (settings UI) + FastAPI backend start.
Bot Start Bot बटन से अपने आप चालू होगा।

  python start.py
  या:  run.bat
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


def main() -> None:
    host = os.getenv("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("APP_PORT", "8000") or "8000")
    app_url = (
        os.getenv("APP_URL", "").strip()
        or f"http://{host}:{port}"
    ).rstrip("/")
    settings_url = f"{app_url}/settings"

    # Local Windows defaults for visible Chrome bot
    if os.name == "nt":
        os.environ.setdefault("HEADLESS", "0")
        os.environ.setdefault("CHROME_DEBUG", "1")

    print("=" * 50)
    print("AutoMection local server")
    print(f"Frontend + API : {settings_url}")
    print(f"Game site      : {os.getenv('GAME_SITE', '')}")
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
