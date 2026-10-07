import ctypes
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
import time
import mysql.connector
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InvalidElementStateException,
    InvalidSessionIdException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

load_dotenv(Path(__file__).resolve().parent / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_file = Path("frontend-settings.json")
FRONTEND_DATA = json.loads(_file.read_text(encoding="utf-8")) if _file.exists() else {}
print(FRONTEND_DATA)

MOBILE_NUMBER = os.getenv("DAMAN_PHONE", "")
PASSWORD = os.getenv("DAMAN_PASSWORD", "")
USER_ID = os.getenv("DAMAN_USER_ID", "").strip()


SERIAL_CLASS = "TimeLeft__C-id"
BIG_BUTTON_CLASS = "Betting__C-foot-b"
SMALL_BUTTON_CLASS = "Betting__C-foot-s"
AMOUNT_INPUT_CSS = 'input[type="number"]'
RESULT_CSS = ".winner_result"
RESULT_ROW_CSS = ".record-body .van-row"

POLL_INTERVAL_SECONDS = 1
BET_PREPARE_DELAY_SECONDS = 5
MIN_SECONDS_TO_BET = 3
BET_AMOUNT = 5
BET_TYPE = "big"
WIN_PAYOUT_RATE = 0.98

DRY_RUN = False
SESSION_STARTED_AT = None
SESSION_MAIN_AMOUNT = None
SESSION_TARGET_PROFIT = None
CHROME_DEBUG_HOST = os.getenv("CHROME_DEBUG_HOST", "").strip() or "127.0.0.1"
CHROME_DEBUG_PORT = int(os.getenv("CHROME_DEBUG_PORT", "").strip() or "9222")
CHROME_DEBUG_PROFILE = Path(
    os.getenv("CHROME_DEBUG_PROFILE", "").strip()
    or (Path(__file__).parent / "chrome-debug-profile")
)
GAME_SITE = os.getenv("GAME_SITE", "").strip()
LOGIN_URL = os.getenv("GAME_LOGIN_URL", "").strip()
WINGO_URL = os.getenv("GAME_WINGO_URL", "").strip()
if not GAME_SITE or not LOGIN_URL or not WINGO_URL:
    raise SystemExit(
        "Set GAME_SITE, GAME_LOGIN_URL, and GAME_WINGO_URL in .env."
    )


def chrome_debug_running():
    try:
        urllib.request.urlopen(
            f"http://{CHROME_DEBUG_HOST}:{CHROME_DEBUG_PORT}/json/version",
            timeout=1,
        )
        return True
    except Exception:
        return False


def start_debug_chrome(start_url=None):
    if chrome_debug_running():
        return

    if not start_url:
        start_url = (
            os.getenv("APP_URL", "").strip().rstrip("/") + "/settings"
            if os.getenv("APP_URL", "").strip()
            else "about:blank"
        )

    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    chrome_exe = None
    for path in chrome_paths:
        if os.path.exists(path):
            chrome_exe = path
            break

    if chrome_exe is None:
        raise Exception("Chrome not found.")

    CHROME_DEBUG_PROFILE.mkdir(exist_ok=True)
    subprocess.Popen(
        [
            chrome_exe,
            f"--remote-debugging-port={CHROME_DEBUG_PORT}",
            f"--user-data-dir={CHROME_DEBUG_PROFILE}",
            start_url,
        ]
    )

    for attempt in range(20):
        if chrome_debug_running():
            return
        time.sleep(0.5)

    raise Exception("Chrome debug port failed to start.")


def keep_system_awake():
    if os.name != "nt":
        return

    # Screen band ho sakti hai. System sleep nahi hoga, isliye automation chalti rahegi.
    es_continuous = 0x80000000
    es_system_required = 0x00000001
    es_awaymode_required = 0x00000040
    ctypes.windll.kernel32.SetThreadExecutionState(
        es_continuous | es_system_required | es_awaymode_required
    )


def allow_system_sleep():
    if os.name != "nt":
        return

    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


ALWAYS_VISIBLE_JS = """
Object.defineProperty(document, 'hidden', {get: function(){ return false; }, configurable: true});
Object.defineProperty(document, 'visibilityState', {get: function(){ return 'visible'; }, configurable: true});
Object.defineProperty(document, 'webkitHidden', {get: function(){ return false; }, configurable: true});
document.hasFocus = function(){ return true; };
window.onblur = null;
window.onfocus = null;
"""


def harden_browser_for_background(browser):
    try:
        browser.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": ALWAYS_VISIBLE_JS},
        )
    except Exception:
        pass

    try:
        browser.execute_cdp_cmd(
            "Emulation.setFocusEmulationEnabled",
            {"enabled": True},
        )
    except Exception:
        pass

    keep_browser_active(browser)


def keep_browser_active(browser):
    try:
        browser.execute_script(ALWAYS_VISIBLE_JS + "\ntry{window.focus();}catch(e){}")
    except Exception:
        pass

    try:
        browser.execute_cdp_cmd(
            "Emulation.setFocusEmulationEnabled",
            {"enabled": True},
        )
    except Exception:
        pass


def open_new_tab(browser, url):
    before = list(browser.window_handles)
    browser.execute_script("window.open('about:blank','_blank');")
    time.sleep(0.4)
    after = list(browser.window_handles)
    new_handles = [handle for handle in after if handle not in before]
    if new_handles:
        browser.switch_to.window(new_handles[-1])
    elif after:
        browser.switch_to.window(after[-1])
    browser.get(url)
    print("Opened new Chrome tab:", url)


def connect_chrome():
    # Local Windows: visible Chrome + remote debug (new tab with settings).
    # AWS/Linux: headless (no Chrome window on the server).
    use_debug = os.getenv("CHROME_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if os.name == "nt" and os.getenv("CHROME_DEBUG", "1").strip() != "0":
        use_debug = True

    if use_debug:
        app_url = os.getenv("APP_URL", "").strip().rstrip("/")
        start_debug_chrome(f"{app_url}/settings" if app_url else "about:blank")
        options = Options()
        options.add_experimental_option(
            "debuggerAddress",
            f"{CHROME_DEBUG_HOST}:{CHROME_DEBUG_PORT}",
        )
        browser = webdriver.Chrome(options=options)
        harden_browser_for_background(browser)
        print("Attached to visible Chrome (debug) — automation will run in a new tab.")
        return browser

    options = Options()
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,BackForwardCache")
    options.add_argument("--disable-hang-monitor")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-size=1200,900")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Infrastructure-only: isolated Chrome profile per app session (no game-logic change).
    chrome_user_data_dir = os.getenv("CHROME_USER_DATA_DIR", "").strip()
    if chrome_user_data_dir:
        Path(chrome_user_data_dir).mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={chrome_user_data_dir}")

    headless_env = os.getenv("HEADLESS", "").strip().lower()
    use_headless = headless_env in ("1", "true", "yes")
    if headless_env in ("0", "false", "no"):
        use_headless = False
    elif os.name != "nt" and not os.environ.get("DISPLAY"):
        use_headless = True
    elif os.name != "nt" and headless_env == "":
        # Server default: headless
        use_headless = True

    if use_headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--remote-allow-origins=*")
        print("Chrome headless mode (AWS/server) is on.")
    else:
        print("Chrome visible mode is on.")

    browser = webdriver.Chrome(options=options)
    try:
        browser.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            },
        )
    except Exception:
        pass
    harden_browser_for_background(browser)
    return browser


def open_in_same_tab(browser, url):
    browser.get(url)


def connect_database():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "automection"),
    )


def prepare_database(cursor, db):
    cursor.execute("SHOW COLUMNS FROM `data`")
    columns = [row[0] for row in cursor.fetchall()]

    if "win_amount" not in columns:
        cursor.execute("ALTER TABLE `data` ADD COLUMN `win_amount` DECIMAL(10,2) NULL")
    else:
        cursor.execute("ALTER TABLE `data` MODIFY `win_amount` DECIMAL(10,2) NULL")

    if "loss_amount" not in columns:
        cursor.execute("ALTER TABLE `data` ADD COLUMN `loss_amount` DECIMAL(10,2) NULL")
    else:
        cursor.execute("ALTER TABLE `data` MODIFY `loss_amount` DECIMAL(10,2) NULL")

    if "user_id" not in columns:
        cursor.execute("ALTER TABLE `data` ADD COLUMN `user_id` INT NULL")

    db.commit()


def current_user_id():
    if not USER_ID:
        return None
    return int(USER_ID)


def user_sql(params):
    if not USER_ID:
        return "", tuple(params)
    return " AND `user_id` = %s", tuple(params) + (int(USER_ID),)


def to_money(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_risk_limits():
    stop_loss = to_money(FRONTEND_DATA.get("stopLoss"))
    target_profit = to_money(FRONTEND_DATA.get("targetProfit"))

    if _file.exists():
        saved = json.loads(_file.read_text(encoding="utf-8"))
        stop_loss = to_money(saved.get("stopLoss"), stop_loss)
        target_profit = to_money(saved.get("targetProfit"), target_profit)

    try:
        db = connect_database()
        cursor = db.cursor()
        if USER_ID:
            cursor.execute(
                """
                SELECT `stop_loss`, `target_profit`
                FROM `settings`
                WHERE `id` = %s
                """,
                (int(USER_ID),),
            )
        else:
            cursor.execute(
                """
                SELECT `stop_loss`, `target_profit`
                FROM `settings`
                ORDER BY `id` DESC
                LIMIT 1
                """
            )
        row = cursor.fetchone()
        cursor.close()
        db.close()

        if row:
            if row[0] is not None:
                stop_loss = to_money(row[0], stop_loss)
            if row[1] is not None:
                target_profit = to_money(row[1], target_profit)
    except Exception:
        pass

    return stop_loss, target_profit


def get_session_totals(cursor):
    if SESSION_STARTED_AT is None:
        return 0.0, 0.0

    clause, params = user_sql([SESSION_STARTED_AT])
    cursor.execute(
        f"""
        SELECT COALESCE(SUM(`win_amount`), 0), COALESCE(SUM(`loss_amount`), 0)
        FROM `data`
        WHERE `user_bet` IS NOT NULL
          AND `time` >= %s
          {clause}
        """,
        params,
    )
    win_total, loss_total = cursor.fetchone()
    return to_money(win_total), to_money(loss_total)


def session_should_stop(cursor):
    global SESSION_MAIN_AMOUNT, SESSION_TARGET_PROFIT

    if SESSION_MAIN_AMOUNT is None or SESSION_TARGET_PROFIT is None:
        SESSION_MAIN_AMOUNT, SESSION_TARGET_PROFIT = get_risk_limits()

    main_amount = to_money(SESSION_MAIN_AMOUNT)
    target_add = to_money(SESSION_TARGET_PROFIT)
    win_total, loss_total = get_session_totals(cursor)
    net = win_total - loss_total
    current_amount = main_amount + net
    target_line = main_amount + target_add
    print(
        "Main amount:",
        main_amount,
        "Current amount:",
        round(current_amount, 2),
        "Target off:",
        target_line,
    )

    if target_add and current_amount >= target_line:
        print("Target profit reached. Shutting system down.")
        return True

    if main_amount and loss_total - win_total >= main_amount:
        print("Main amount loss complete. Stopping at stop loss.")
        return True

    return False


def get_user_level_amounts():
    starting = to_money(FRONTEND_DATA.get("startingAmount"), BET_AMOUNT)
    levels = []

    if _file.exists():
        saved = json.loads(_file.read_text(encoding="utf-8"))
        starting = to_money(saved.get("startingAmount"), starting)
        for item in saved.get("levelAmounts") or []:
            amount = to_money(item)
            if amount > 0:
                levels.append(amount)

    try:
        db = connect_database()
        cursor = db.cursor()
        if USER_ID:
            cursor.execute(
                """
                SELECT `starting_amount`, `level_amounts`
                FROM `settings`
                WHERE `id` = %s
                """,
                (int(USER_ID),),
            )
        else:
            cursor.execute(
                """
                SELECT `starting_amount`, `level_amounts`
                FROM `settings`
                ORDER BY `id` DESC
                LIMIT 1
                """
            )
        row = cursor.fetchone()
        cursor.close()
        db.close()

        if row:
            if row[0]:
                starting = to_money(row[0], starting)
            if row[1]:
                parsed = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                table_levels = []
                for item in parsed or []:
                    amount = to_money(item)
                    if amount > 0:
                        table_levels.append(amount)
                if table_levels:
                    levels = table_levels
    except Exception:
        pass

    if not starting:
        starting = BET_AMOUNT

    return starting, levels


def next_amount_after_loss(last_amount, starting, levels):
    last_amount = to_money(last_amount)
    if not levels:
        return starting, 0

    if abs(last_amount - to_money(starting)) < 0.001:
        return levels[0], 1

    for index, level_amount in enumerate(levels):
        if abs(last_amount - to_money(level_amount)) < 0.001:
            if index + 1 < len(levels):
                return levels[index + 1], index + 2
            return starting, 0

    return levels[0], 1


def get_next_bet_amount(cursor):
    starting, levels = get_user_level_amounts()
    last_amount = None
    last_status = None

    if SESSION_STARTED_AT is not None:
        clause, params = user_sql([SESSION_STARTED_AT])
        cursor.execute(
            f"""
            SELECT `amount`, `status`
            FROM `data`
            WHERE `user_bet` IS NOT NULL
              AND `status` IN (1, -1)
              AND `time` >= %s
              {clause}
            ORDER BY `id` DESC
            LIMIT 1
            """,
            params,
        )
        row = cursor.fetchone()
        if row:
            last_amount = row[0]
            last_status = row[1]

    # Use starting amount on WIN and the first bet.
    # Move to the next level only after the last LOSS. Use 300 only after a 100 loss.
    if last_status != -1:
        amount = starting
        print("Next amount: Start bet =", amount)
    else:
        amount, level_number = next_amount_after_loss(last_amount, starting, levels)
        if level_number == 0:
            print("Last level loss booked. Restarting from starting bet.")
            print("Next amount: Start bet =", amount)
        else:
            print("Next amount: Level", level_number, "=", amount)

    if amount == int(amount):
        amount = int(amount)
    return amount


def dismiss_popups(browser):
    for count in range(8):
        announcement_buttons = browser.find_elements(
            By.CLASS_NAME, "announcement-dialog__button"
        )
        reward_buttons = browser.find_elements(
            By.CLASS_NAME, "reward-amount-dialog__button"
        )
        # Only Cancel inside dialog/popup — otherwise login page may go back.
        cancel_buttons = browser.find_elements(
            By.XPATH,
            "//*[contains(@class,'van-dialog') or contains(@class,'van-popup')]"
            "//*[self::button or contains(@class,'van-button')]"
            "[normalize-space()='Cancel' or normalize-space()='Close'"
            " or normalize-space()='OK' or normalize-space()='Got it']",
        )

        if announcement_buttons:
            try:
                announcement_buttons[0].click()
            except Exception:
                browser.execute_script("arguments[0].click();", announcement_buttons[0])
            time.sleep(0.3)
        elif reward_buttons:
            try:
                reward_buttons[0].click()
            except Exception:
                browser.execute_script("arguments[0].click();", reward_buttons[0])
            time.sleep(0.3)
        elif cancel_buttons:
            try:
                if cancel_buttons[0].is_displayed():
                    cancel_buttons[0].click()
                    print("Closed popup/dialog with Cancel.")
                    time.sleep(0.4)
                else:
                    break
            except Exception:
                try:
                    browser.execute_script("arguments[0].click();", cancel_buttons[0])
                    print("Closed popup/dialog with Cancel.")
                    time.sleep(0.4)
                except Exception:
                    break
        else:
            break


def wingo_hash():
    if "#" in WINGO_URL:
        hash_part = WINGO_URL.split("#", 1)[1]
    else:
        hash_part = "/saas/Lottery/WinGo?gameCode=WinGo_30S&lottery=WinGo"
    hash_part = hash_part.lstrip("#")
    if not hash_part.startswith("/"):
        hash_part = "/" + hash_part
    return "#" + hash_part


def site_home_url():
    return f"https://{GAME_SITE}/"


def is_win_go_open(browser):
    url = ""
    try:
        url = (browser.current_url or "").lower()
    except Exception:
        url = ""

    # Home with ?gameCode=WinGo is NOT the game page.
    if "#/saas/lottery/wingo" in url:
        return True

    serials = browser.find_elements(By.CLASS_NAME, SERIAL_CLASS)
    for serial in serials:
        try:
            if serial.is_displayed() and (serial.text or "").strip():
                return True
        except Exception:
            pass

    big_buttons = browser.find_elements(By.CLASS_NAME, BIG_BUTTON_CLASS)
    small_buttons = browser.find_elements(By.CLASS_NAME, SMALL_BUTTON_CLASS)
    for button in list(big_buttons) + list(small_buttons):
        try:
            if button.is_displayed():
                return True
        except Exception:
            pass

    return False


def navigate_to_wingo(browser):
    print("Navigating to Win Go game page...")
    home = site_home_url()
    target_hash = wingo_hash()

    try:
        current = browser.current_url or ""
    except Exception:
        current = ""

    if GAME_SITE.lower() not in current.lower():
        browser.get(home)
        time.sleep(2)

    # Hash routing keeps SPA on the game route (browser.get drops /saas/Lottery/WinGo).
    browser.execute_script("window.location.hash = arguments[0];", target_hash)
    time.sleep(3)
    dismiss_popups(browser)

    if not is_win_go_open(browser):
        browser.execute_script(
            "window.location.href = arguments[0];",
            home.rstrip("/") + "/" + target_hash,
        )
        time.sleep(3)
        dismiss_popups(browser)

    for attempt in range(25):
        if is_win_go_open(browser):
            print("Win Go game page ready:", browser.current_url)
            return True
        time.sleep(0.4)

    debug_page_state(browser, "wingo-navigate-fail")
    print("Win Go page failed to open. URL:", browser.current_url)
    return False


def find_win_go_card(browser):
    xpaths = [
        "//div[contains(@class,'daman_img')][.//h3[normalize-space()='Win Go']]",
        "//h3[normalize-space()='Win Go']/ancestor::div[contains(@class,'daman_img')]",
        "//h3[normalize-space()='Win Go']",
        "//*[normalize-space()='Win Go']",
    ]

    for xpath in xpaths:
        cards = browser.find_elements(By.XPATH, xpath)
        for card in cards:
            try:
                if card.is_displayed():
                    return card
            except Exception:
                pass

    return None


def open_win_go(browser):
    print("5. Scrolling page to open Win Go card...")
    dismiss_popups(browser)
    time.sleep(1)

    for attempt in range(12):
        if is_win_go_open(browser):
            print("Already on Win Go game page.")
            return

        browser.execute_script("window.scrollBy(0, 350);")
        time.sleep(0.5)

        win_go = find_win_go_card(browser)
        if win_go is None:
            print("Waiting for Win Go card...")
            dismiss_popups(browser)
            time.sleep(0.5)
            continue

        browser.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            win_go,
        )
        time.sleep(0.6)

        try:
            win_go.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            print("Popup blocked click. Using JavaScript click...")
            dismiss_popups(browser)
            browser.execute_script("arguments[0].click();", win_go)

        time.sleep(3)
        if is_win_go_open(browser):
            print("Opened from Win Go card.")
            return

    print("Win Go card did not open game page. Using hash URL...")
    navigate_to_wingo(browser)


def reload_win_go_page(browser):
    print("6. Reloading Win Go...")
    time.sleep(2)

    if is_win_go_open(browser):
        browser.refresh()
        time.sleep(3)
        dismiss_popups(browser)
    else:
        navigate_to_wingo(browser)

    if not is_win_go_open(browser):
        navigate_to_wingo(browser)

    debug_page_state(browser, "after-wingo-reload")
    if not is_win_go_open(browser):
        raise Exception(
            "Stuck on home page instead of Win Go. Check GAME_WINGO_URL in .env."
        )
    print("Win Go ready. Amount popup will open after serial detect.")


def get_serial_from_history(browser):
    records = get_result_records(browser)
    if not records:
        return None

    last_period = records[0][0]
    if last_period.isdigit():
        return str(int(last_period) + 1)
    return last_period


def get_remaining_seconds(browser):
    boxes = browser.find_elements(By.CSS_SELECTOR, "[class*='TimeLeft__C-time']")
    if not boxes:
        boxes = browser.find_elements(By.CSS_SELECTOR, "[class*='TimeLeft__C-num']")

    for box in boxes:
        raw = box.text.replace("\n", "").replace(" ", "")
        if ":" not in raw:
            continue
        left, right = raw.split(":", 1)
        left = "".join(character for character in left if character.isdigit())
        right = "".join(character for character in right if character.isdigit())
        if left.isdigit() and right.isdigit() and len(right) <= 2:
            return int(left) * 60 + int(right)

    return None


def get_serial_number(browser):
    selectors = [
        (By.CLASS_NAME, SERIAL_CLASS),
        (By.CSS_SELECTOR, ".TimeLeft__C-id"),
        (By.CSS_SELECTOR, "[class*='TimeLeft__C-id']"),
        (By.CSS_SELECTOR, "[class*='TimeLeft__C']"),
        (By.CSS_SELECTOR, "[class*='TimeLeft']"),
        (By.XPATH, "//*[contains(@class,'TimeLeft') and string-length(normalize-space())>=10]"),
    ]

    try:
        for by, value in selectors:
            elements = browser.find_elements(by, value)
            for element in elements:
                try:
                    text = element.text.strip()
                except StaleElementReferenceException:
                    continue
                digits = "".join(character for character in text if character.isdigit())
                if len(digits) >= 10:
                    return digits

        history_serial = get_serial_from_history(browser)
        if history_serial:
            return history_serial
    except Exception as error:
        print("Serial read error, will retry:", error)

    return None


def get_latest_result(browser):
    # Return empty values if the latest result element is missing.
    result_elements = browser.find_elements(By.CSS_SELECTOR, RESULT_CSS)

    if not result_elements:
        return None, None

    # Example text: Red, 6, Big
    result_text = result_elements[0].text.strip()
    result_type = None

    # Search each result line for BIG or SMALL.
    for line in result_text.splitlines():
        word = line.strip().lower()

        if word == "big" or word == "small":
            result_type = word

    return result_text, result_type


def get_result_records(browser):
    records = []
    rows = browser.find_elements(By.CSS_SELECTOR, RESULT_ROW_CSS)
    if not rows:
        rows = browser.find_elements(By.CSS_SELECTOR, ".van-row")

    for row in rows:
        try:
            columns = row.find_elements(
                By.XPATH, "./div[contains(@class,'van-col')]"
            )
            if len(columns) < 3:
                columns = row.find_elements(By.XPATH, "./div")

            if len(columns) >= 3:
                period = columns[0].text.strip()
                result = columns[2].text.strip().lower()

                if "small" in result:
                    result = "small"
                elif "big" in result:
                    result = "big"

                if period.isdigit() and result in ("big", "small"):
                    records.append((period, result))
        except StaleElementReferenceException:
            continue

    return records


def get_last_big_small(browser):
    # The first game-history row is the latest completed serial.
    records = get_result_records(browser)

    if records:
        last_result = records[0][1]

        if last_result == "big" or last_result == "small":
            return last_result

    # Return nothing if no result is available.
    return None


def save_serial(cursor, db, serial_number):
    if serial_has_row(cursor, serial_number):
        return

    query = """
        INSERT INTO `data` (`number`, `status`, `user_id`)
        VALUES (%s, %s, %s)
    """
    cursor.execute(query, (serial_number, 0, current_user_id()))
    db.commit()
    print(f"New serial saved to database: {serial_number}")


def serial_has_row(cursor, serial_number):
    clause, params = user_sql([serial_number])
    cursor.execute(
        f"""
        SELECT `id`
        FROM `data`
        WHERE `number` = %s
          {clause}
        ORDER BY `id` DESC
        LIMIT 1
        """,
        params,
    )
    return cursor.fetchone() is not None


def save_user_bet(cursor, db, serial_number, bet_type, amount):
    clause, params = user_sql([serial_number])
    cursor.execute(
        f"""
        SELECT `id`
        FROM `data`
        WHERE `number` = %s
          {clause}
        ORDER BY `id` DESC
        LIMIT 1
        """,
        params,
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            """
            UPDATE `data`
            SET `user_bet` = %s,
                `amount` = %s,
                `win_amount` = %s,
                `loss_amount` = %s
            WHERE `id` = %s
            """,
            (bet_type.lower(), amount, 0, 0, row[0]),
        )
    else:
        cursor.execute(
            """
            INSERT INTO `data` (
                `number`, `user_bet`, `amount`, `status`, `win_amount`, `loss_amount`, `user_id`
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (serial_number, bet_type.lower(), amount, 0, 0, 0, current_user_id()),
        )

    db.commit()
    print("User bet saved to database:", bet_type.upper(), "Amount:", amount)


def get_bet_status():
    # No arguments needed. Reads the last bet from the database only.
    db = connect_database()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT `user_bet`, `result`, `amount`
        FROM `data`
        WHERE `user_bet` IS NOT NULL
        ORDER BY `id` DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    cursor.close()
    db.close()

    if not row:
        return {
            "amount": 0,
            "status": 0
        }

    user_bet, game_result, last_amount = row
    print("Last bet:", user_bet, "Result:", game_result, "Amount:", last_amount)

    if not user_bet or not game_result:
        status = 0
    elif user_bet.lower() == game_result.lower():
        status = 1
    else:
        status = -1

    return {
        "amount": last_amount or 0,
        "status": status
    }



def recovery_simulator():
    result = get_bet_status()
    amount = result["amount"]
    status = result["status"]
    if status == -1:
        new_amount = BET_AMOUNT + 1
        return new_amount
    return amount



def save_game_result(cursor, db, serial_number, result_type):
    # Compute WIN/LOSS amount from the saved bet and amount for that serial.
    clause, params = user_sql([serial_number])
    cursor.execute(
        f"""
        SELECT `id`, `user_bet`, `amount`
        FROM `data`
        WHERE `number` = %s
          {clause}
        ORDER BY `id` DESC
        LIMIT 1
        """,
        params,
    )
    row = cursor.fetchone()

    if not row:
        return

    row_id, user_bet, bet_amount = row
    bet_amount = bet_amount or 0
    result_type = result_type.lower()
    win_amount = 0
    loss_amount = 0
    status = 0

    if user_bet and user_bet.lower() == result_type:
        status = 1
        win_amount = round(to_money(bet_amount) * WIN_PAYOUT_RATE, 2)
    elif user_bet:
        status = -1
        loss_amount = round(to_money(bet_amount), 2)

    cursor.execute(
        """
        UPDATE `data`
        SET `result` = %s,
            `status` = %s,
            `win_amount` = %s,
            `loss_amount` = %s
        WHERE `id` = %s
        """,
        (result_type, status, win_amount, loss_amount, row_id),
    )
    db.commit()

    status_text = "PENDING"
    if status == 1:
        status_text = "WIN"
    elif status == -1:
        status_text = "LOSS"

    print(
        "Result saved to database:",
        serial_number,
        result_type.upper(),
        status_text,
        "Win:",
        win_amount,
        "Loss:",
        loss_amount,
    )


def find_bet_button(browser, bet_type):
    if bet_type == "big":
        buttons = browser.find_elements(By.CLASS_NAME, BIG_BUTTON_CLASS)
        if not buttons:
            buttons = browser.find_elements(
                By.XPATH,
                "//*[normalize-space()='Big' or normalize-space()='BIG']",
            )
    else:
        buttons = browser.find_elements(By.CLASS_NAME, SMALL_BUTTON_CLASS)
        if not buttons:
            buttons = browser.find_elements(
                By.XPATH,
                "//*[normalize-space()='Small' or normalize-space()='SMALL']",
            )

    for button in buttons:
        try:
            if button.is_displayed():
                return button
        except Exception:
            pass

    return None


def bet_created(browser, bet_type, amount):
    bet_type = bet_type.lower()

    if bet_type not in ("big", "small"):
        print("Type must be big or small only.")
        return False

    button = find_bet_button(browser, bet_type)
    if button is None:
        print(bet_type.upper(), "button not found.")
        return False

    browser.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
    time.sleep(0.2)
    keep_browser_active(browser)

    try:
        browser.execute_script("arguments[0].click();", button)
    except Exception:
        try:
            button.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            print(bet_type.upper(), "Normal click failed. Using JavaScript click...")
            browser.execute_script("arguments[0].click();", button)

    number = None
    for attempt in range(15):
        inputs = browser.find_elements(By.CSS_SELECTOR, AMOUNT_INPUT_CSS)
        for item in inputs:
            try:
                if item.is_displayed():
                    number = item
                    break
            except Exception:
                pass

        if number:
            break
        time.sleep(0.3)

    if number is None:
        print(bet_type.upper(), "amount popup did not open.")
        return False

    try:
        number.clear()
        number.send_keys(str(amount))
    except (ElementNotInteractableException, ElementClickInterceptedException):
        browser.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
            number,
            str(amount),
        )

    if bet_type == "big":
        confirmation_css = (
            ".van-button.van-button--default.van-button--normal"
            ".bet-amount.n_big"
        )
    else:
        confirmation_css = (
            ".van-button.van-button--default.van-button--normal"
            ".bet-amount.n_small"
        )

    for attempt in range(20):
        overlays = browser.find_elements(By.CSS_SELECTOR, ".winning")
        visible_win_overlay = False
        for overlay in overlays:
            try:
                if overlay.is_displayed():
                    visible_win_overlay = True
            except Exception:
                pass
        if not visible_win_overlay:
            break
        time.sleep(0.4)

    try:
        element = browser.find_element(By.CSS_SELECTOR, confirmation_css)
        try:
            element.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            print(bet_type.upper(), "Confirm overlay blocked click. Using JavaScript click...")
            browser.execute_script("arguments[0].click();", element)
    except Exception as error:
        print(bet_type.upper(), "confirm click failed:", error)
        print(bet_type.upper(), "amount popup opened. Amount:", amount)
        return True

    print(bet_type.upper(), "amount popup opened. Amount:", amount)
    print("Click confirmation manually.")
    return True



def bet_result(browser):

    for attempt in range(20):
        result_elements = browser.find_elements(By.CSS_SELECTOR, RESULT_CSS)

        if result_elements:
            result_text = result_elements[0].text.strip()
            return result_text

        print("Waiting for result element...")
        time.sleep(1)














def prepare_bet(browser, cursor, db, serial_number):
    # In dry-run the website form is not opened; only info is printed.
    if DRY_RUN:
        print(
            f"[DRY RUN] Serial {serial_number}: "
            f"amount {BET_AMOUNT} bet would be prepared."
        )
        return True

    try:
        if get_serial_number(browser) != serial_number:
            print("Serial changed. Bet will be placed on the next round.")
            return False

        remaining = get_remaining_seconds(browser)
        print("Bet time left:", remaining)
        if remaining is not None and remaining < MIN_SECONDS_TO_BET:
            print("Bet window too short. Waiting for next round.")
            return False

        last_big_small = None
        for attempt in range(8):
            last_big_small = get_last_big_small(browser)
            if not last_big_small:
                result_text, latest_result = get_latest_result(browser)
                last_big_small = latest_result
            if last_big_small:
                break
            time.sleep(0.2)

        if not last_big_small:
            print("Last BIG/SMALL result not found. Will retry.")
            return False

        print("Last result:", last_big_small.upper(), "- opening its amount popup.")
        amount = get_next_bet_amount(cursor)
        form_is_ready = bet_created(browser, last_big_small, amount)
        save_user_bet(cursor, db, serial_number, last_big_small, amount)

        if form_is_ready:
            print(f"Serial {serial_number} form is ready.")
            return True

        print(f"Serial {serial_number} popup did not open. Will retry.")
        return False
    except Exception as error:
        print("Bet prepare error, system will continue:", error)
        return False


def serial_has_bet(cursor, serial_number):
    clause, params = user_sql([serial_number])
    cursor.execute(
        f"""
        SELECT `user_bet`, `amount`
        FROM `data`
        WHERE `number` = %s
          {clause}
        ORDER BY `id` DESC
        LIMIT 1
        """,
        params,
    )
    row = cursor.fetchone()
    return bool(row and row[0] and row[1] is not None)


def ensure_bet_for_serial(browser, cursor, db, serial_number):
    if serial_has_bet(cursor, serial_number):
        return True

    for attempt in range(4):
        current = get_serial_number(browser)
        if current != serial_number:
            print("Round changed. Bet will use current serial:", current)
            return False

        remaining = get_remaining_seconds(browser)
        if remaining is not None and remaining < MIN_SECONDS_TO_BET:
            return False

        if prepare_bet(browser, cursor, db, serial_number):
            return True
        if serial_has_bet(cursor, serial_number):
            return True
        time.sleep(0.3)

    return serial_has_bet(cursor, serial_number)
















def save_pending_results(browser, cursor, db, saved_results):
    result_records = get_result_records(browser)
    for result_serial, result_type in result_records:
        result_key = (result_serial, result_type)
        if result_key not in saved_results:
            save_game_result(cursor, db, result_serial, result_type)
            saved_results.add(result_key)


def wait_for_bet_result(browser, cursor, db, saved_results, serial_number):
    if not serial_number:
        return

    for attempt in range(4):
        remaining = get_remaining_seconds(browser)
        if remaining is not None and remaining >= 20:
            save_pending_results(browser, cursor, db, saved_results)
            return

        save_pending_results(browser, cursor, db, saved_results)
        clause, params = user_sql([serial_number])
        cursor.execute(
            f"""
            SELECT `status`
            FROM `data`
            WHERE `number` = %s
              AND `user_bet` IS NOT NULL
              {clause}
            ORDER BY `id` DESC
            LIMIT 1
            """,
            params,
        )
        row = cursor.fetchone()
        if row and row[0] in (1, -1):
            return
        time.sleep(0.3)


def watch_serial_numbers(browser, cursor, db):
    global SESSION_STARTED_AT, SESSION_MAIN_AMOUNT, SESSION_TARGET_PROFIT
    SESSION_STARTED_AT = datetime.now()
    SESSION_MAIN_AMOUNT, SESSION_TARGET_PROFIT = get_risk_limits()

    old_serial = None
    for attempt in range(40):
        old_serial = get_serial_number(browser)
        if old_serial:
            break
        print("Waiting for serial number...")
        if attempt in (0, 10, 20, 30):
            debug_page_state(browser, f"serial-wait-{attempt}")
            dismiss_popups(browser)
            if not is_win_go_open(browser):
                navigate_to_wingo(browser)
        time.sleep(1)

    print("Initial serial:", old_serial)
    serial_seen_at = {}
    if old_serial:
        serial_seen_at[old_serial] = time.time()
        save_serial(cursor, db, old_serial)

    # Treat the result present when the page opens as the old result.
    old_result_text, old_result_type = get_latest_result(browser)

    if old_result_type:
        print("Initial result:", old_result_type.upper())

    # Prevents the same result from being updated repeatedly in the database.
    saved_results = set()

    # Checks every round until the program stops.
    # The 5-second wait is counted from serial-first-seen so mid rounds are not missed.
    while True:
        try:
            keep_system_awake()
            keep_browser_active(browser)
            new_serial = get_serial_number(browser)
            if not new_serial:
                time.sleep(0.3)
                continue

            new_result_text, latest_result = get_latest_result(browser)
            save_pending_results(browser, cursor, db, saved_results)

            if session_should_stop(cursor):
                print("Monitoring stopped.")
                close_browser(browser)
                return

            if new_result_text and new_result_text != old_result_text:
                old_result_text = new_result_text
                if latest_result:
                    print("Latest result:", latest_result.upper())

            if new_serial not in serial_seen_at:
                if old_serial and old_serial.isdigit() and new_serial.isdigit():
                    gap = int(new_serial) - int(old_serial)
                    if gap > 1:
                        print(
                            "Gap found:",
                            gap - 1,
                            "serial(s) missed.",
                            old_serial,
                            "to",
                            new_serial,
                        )
                print("Serial changed:", old_serial, "to", new_serial)
                remaining_now = get_remaining_seconds(browser)
                # If the round has already run more than 5 seconds, do not wait more.
                if remaining_now is not None and remaining_now <= 25:
                    serial_seen_at[new_serial] = time.time() - BET_PREPARE_DELAY_SECONDS
                else:
                    serial_seen_at[new_serial] = time.time()
                old_serial = new_serial
                save_serial(cursor, db, new_serial)

            elapsed = time.time() - serial_seen_at[new_serial]
            if elapsed < BET_PREPARE_DELAY_SECONDS:
                time.sleep(0.2)
                continue

            if not serial_has_bet(cursor, new_serial):
                remaining = get_remaining_seconds(browser)
                if remaining is None or remaining >= MIN_SECONDS_TO_BET:
                    print(
                        "Bet create:",
                        new_serial,
                        "wait=",
                        round(elapsed, 1),
                        "sec",
                    )
                    ensure_bet_for_serial(browser, cursor, db, new_serial)

                    # If the serial advanced during the bet, switch to the current serial immediately.
                    current = get_serial_number(browser)
                    if current and current != new_serial:
                        if current not in serial_seen_at:
                            print("New serial arrived while busy:", current)
                            remaining_now = get_remaining_seconds(browser)
                            if remaining_now is not None and remaining_now <= 25:
                                serial_seen_at[current] = (
                                    time.time() - BET_PREPARE_DELAY_SECONDS
                                )
                            else:
                                serial_seen_at[current] = time.time()
                            old_serial = current
                            save_serial(cursor, db, current)

            time.sleep(0.2)
        except StaleElementReferenceException as error:
            print("Page changed after loss book. Starting bet continues:", error)
            time.sleep(0.5)


def debug_page_state(browser, label):
    try:
        url = browser.current_url or ""
        title = browser.title or ""
        print(f"[{label}] URL: {url} | title: {title}")
        shot = Path(__file__).parent / f"debug-{label.replace(' ', '_')}.png"
        try:
            browser.save_screenshot(str(shot))
            print(f"[{label}] screenshot: {shot.name}")
        except Exception:
            pass
    except Exception as error:
        print(f"[{label}] page state error:", error)


def login_still_open(browser):
    try:
        fields = browser.find_elements(By.NAME, "userNumber")
        for field in fields:
            try:
                if field.is_displayed():
                    return True
            except StaleElementReferenceException:
                continue
    except Exception:
        pass
    return False


def page_toast_text(browser):
    selectors = [
        ".van-toast",
        ".van-notify",
        ".van-dialog__message",
        ".van-loading__text",
        "[class*='van-toast']",
    ]
    texts = []
    for css in selectors:
        try:
            for el in browser.find_elements(By.CSS_SELECTOR, css):
                try:
                    if el.is_displayed():
                        text = (el.text or "").strip()
                        if text and text not in texts and len(text) < 120:
                            texts.append(text)
                except StaleElementReferenceException:
                    continue
        except Exception:
            continue
    return " | ".join(texts[:5])


def is_login_loading(browser):
    try:
        loaders = browser.find_elements(
            By.CSS_SELECTOR,
            ".van-toast--loading, .van-loading--circular",
        )
        for el in loaders:
            try:
                if not el.is_displayed():
                    continue
                classes = el.get_attribute("class") or ""
                text = (el.text or "").strip().lower()
                if "van-toast--loading" in classes or "van-loading" in classes:
                    return True
                if "loading" in text:
                    return True
            except StaleElementReferenceException:
                continue

        toast = page_toast_text(browser).lower().strip()
        return toast == "loading..." or toast.startswith("loading")
    except Exception:
        return False


def fill_input(browser, element, text):
    text = str(text)
    try:
        element.click()
        time.sleep(0.1)
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
    except Exception:
        pass

    # Vue/React controlled inputs: native value setter is required.
    browser.execute_script(
        """
        const el = arguments[0];
        const value = String(arguments[1]);
        el.focus();
        const proto = window.HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) {
          desc.set.call(el, '');
          el.dispatchEvent(new Event('input', { bubbles: true }));
          desc.set.call(el, value);
        } else {
          el.value = value;
        }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        element,
        text,
    )

    try:
        current = element.get_attribute("value") or ""
        if current != text:
            element.clear()
            element.send_keys(text)
    except Exception:
        pass


def login_button_enabled(browser):
    buttons = browser.find_elements(
        By.XPATH,
        "//button[normalize-space()='Log in' or normalize-space()='Login']"
        " | //div[contains(@class,'van-button')][normalize-space()='Log in' or normalize-space()='Login']",
    )
    for button in buttons:
        try:
            if not button.is_displayed():
                continue
            classes = button.get_attribute("class") or ""
            disabled_attr = button.get_attribute("disabled")
            aria = button.get_attribute("aria-disabled")
            if disabled_attr or aria == "true" or "disabled" in classes:
                return False, button
            return True, button
        except StaleElementReferenceException:
            continue
    return False, None


def dump_login_controls(browser):
    try:
        phone_vals = []
        for el in browser.find_elements(By.NAME, "userNumber"):
            phone_vals.append(el.get_attribute("value") or "")
        print("Phone field value:", phone_vals)

        buttons = browser.execute_script(
            """
            return Array.from(document.querySelectorAll('button, .van-button, [role="button"]'))
              .filter(el => !!(el.offsetWidth || el.offsetHeight))
              .map(el => (el.innerText || el.textContent || '').trim().slice(0, 40));
            """
        )
        print("Visible buttons:", buttons)
    except Exception as error:
        print("Login controls dump failed:", error)


def click_login_submit(browser, password_element):
    dump_login_controls(browser)

    enabled, button = login_button_enabled(browser)
    print("Login button enabled:", enabled)
    if not enabled:
        raise Exception(
            "Log in button is disabled — phone/password not bound in Vue."
        )

    if button is not None:
        label = (button.text or "").strip()
        print("Login click:", label or "Log in")
        try:
            button.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            browser.execute_script("arguments[0].click();", button)
        return True

    print("Login button not found, trying Enter...")
    try:
        password_element.send_keys(Keys.ENTER)
        return True
    except (ElementNotInteractableException, InvalidElementStateException):
        browser.execute_script(
            """
            const form = arguments[0].closest('form');
            if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }
            else { arguments[0].dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); }
            """,
            password_element,
        )
        return True


def wait_for_interactable(browser, by, value, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            for el in browser.find_elements(by, value):
                try:
                    if el.is_displayed() and el.is_enabled():
                        return el
                except StaleElementReferenceException:
                    continue
        except Exception:
            pass
        time.sleep(0.3)
    return None


def close_browser(browser):
    try:
        browser.quit()
    except Exception:
        pass


def userdata(phone_number, password_text):
    # Stop with a clear error if login credentials are missing.
    if not phone_number or not password_text:
        raise ValueError(
            "Send mobile number and password from the settings form."
        )

    # Runs database queries and Chrome website automation.
    db = connect_database()
    cursor = db.cursor()
    prepare_database(cursor, db)
    browser = connect_chrome()

    try:
        keep_system_awake()
        print("Bot keeps running even if screen is off or another tab is active.")
        if USER_ID:
            print("User ID:", USER_ID, "Mobile:", phone_number)
        print("1. Opening login page in a new tab...")
        open_new_tab(browser, LOGIN_URL)
        harden_browser_for_background(browser)
        time.sleep(3)
        # Stay on the login URL; do not leave to home via a bad Cancel click.
        if "login" not in (browser.current_url or "").lower():
            browser.get(LOGIN_URL)
            time.sleep(2)
        dismiss_popups(browser)
        if "login" not in (browser.current_url or "").lower():
            browser.get(LOGIN_URL)
            time.sleep(2)

        print("2. Filling login data...")
        password = None
        for fill_try in range(5):
            dismiss_popups(browser)
            phone = wait_for_interactable(browser, By.NAME, "userNumber", timeout=10)
            if phone is None:
                raise Exception("Login form not found.")
            fill_input(browser, phone, phone_number)

            password = wait_for_interactable(
                browser, By.CSS_SELECTOR, 'input[type="password"]', timeout=10
            )
            if password is None:
                raise Exception("Password field not found.")
            fill_input(browser, password, password_text)

            phone_val = phone.get_attribute("value") or ""
            enabled, _ = login_button_enabled(browser)
            print(
                f"Fill try {fill_try + 1}: phone={phone_val!r} login_enabled={enabled}"
            )
            if phone_val == str(phone_number) and enabled:
                break
            time.sleep(0.5)
        else:
            debug_page_state(browser, "fill-failed")
            raise Exception(
                "Phone/password not filled in UI. Vue bind failed — check screenshot."
            )

        print("3. Logging in...")
        dismiss_popups(browser)
        click_login_submit(browser, password)

        for attempt in range(45):
            if not login_still_open(browser):
                break
            if is_login_loading(browser):
                if attempt % 5 == 0:
                    print("Login API loading... wait", attempt)
                time.sleep(1)
                continue
            toast = page_toast_text(browser)
            if toast:
                print("Login message:", toast)
            time.sleep(0.5)
        else:
            toast = page_toast_text(browser)
            debug_page_state(browser, "login-failed")
            if is_login_loading(browser) or "loading" in (toast or "").lower():
                raise Exception(
                    "Login loading stuck. AWS IP may be blocked or password is wrong. "
                    "Verify login on the game site in a browser first."
                )
            extra = f" Site message: {toast}" if toast else ""
            raise Exception(
                "Login failed. Check mobile/password." + extra
            )

        debug_page_state(browser, "login-ok")
        print("Game site:", GAME_SITE)

        print("4. Dismissing popups...")
        dismiss_popups(browser)
        time.sleep(1)

        open_win_go(browser)
        time.sleep(2)

        reload_win_go_page(browser)
        harden_browser_for_background(browser)
        keep_browser_active(browser)
        watch_serial_numbers(browser, cursor, db)
    except KeyboardInterrupt:
        print("Monitoring stopped.")
    except InvalidSessionIdException:
        print("Chrome connection lost. Submit again from Settings.")
    except Exception as error:
        print("Bot error:", error)
        try:
            debug_page_state(browser, "bot-error")
        except Exception:
            pass
    finally:
        allow_system_sleep()
        cursor.close()
        db.close()


if __name__ == "__main__":
    userdata(
        MOBILE_NUMBER,
        PASSWORD,
    )
