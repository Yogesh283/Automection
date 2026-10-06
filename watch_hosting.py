import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"
STATE_FILE = ROOT / "last-hosting-start.json"
MAIN_FILE = ROOT / "main.py"
HOSTED_SETTINGS_URL = os.getenv(
    "HOSTED_SETTINGS_URL",
    "https://updowanfx.com/frontend-settings.json",
)
POLL_SECONDS = 3
_main_processes = {}


def load_env():
    values = {}
    if not ENV_FILE.exists():
        return values

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_state():
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_state(data):
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fetch_hosted_settings():
    with urllib.request.urlopen(HOSTED_SETTINGS_URL, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return data if isinstance(data, dict) else {}


def bot_running(mobile_number):
    process = _main_processes.get(mobile_number)
    return process is not None and process.poll() is None


def start_bot(mobile_number, password_text):
    if bot_running(mobile_number):
        print("Bot पहले से चल रहा है:", mobile_number)
        return False

    if not mobile_number or not password_text:
        print("Mobile/password नहीं मिला। D:\\AutoMection\\.env में DAMAN_PASSWORD डालो।")
        return False

    env = os.environ.copy()
    env.update(load_env())
    env["DAMAN_PHONE"] = str(mobile_number)
    env["DAMAN_PASSWORD"] = str(password_text)
    env["PYTHONUNBUFFERED"] = "1"

    _main_processes[mobile_number] = subprocess.Popen(
        [sys.executable, "-u", str(MAIN_FILE)],
        cwd=str(ROOT),
        env=env,
    )
    print("Hosting Submit मिला। Bot start हो गया:", mobile_number)
    return True


def main():
    print("Hosting watcher चालू है।")
    print("Form यहाँ Submit करो: https://updowanfx.com/")
    print("ये window खुली रहने दो।")

    while True:
        env = load_env()
        try:
            settings = fetch_hosted_settings()
        except Exception as error:
            print("Hosting settings नहीं मिली:", error)
            time.sleep(POLL_SECONDS)
            continue

        requested_at = str(settings.get("requestedAt") or "")
        start_bot_flag = bool(settings.get("startBot"))
        state = read_state()

        if start_bot_flag and requested_at and requested_at != state.get("requestedAt"):
            mobile_number = str(
                settings.get("mobileNumber") or env.get("DAMAN_PHONE") or ""
            ).strip()
            password_text = str(env.get("DAMAN_PASSWORD") or "").strip()
            started = start_bot(mobile_number, password_text)
            if started:
                local_file = ROOT / "frontend-settings.json"
                local_file.write_text(
                    json.dumps(settings, indent=2),
                    encoding="utf-8",
                )
                write_state({"requestedAt": requested_at})

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
