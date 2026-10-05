import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
import time
import mysql.connector
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InvalidSessionIdException,
    NoSuchElementException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_file = Path("frontend-settings.json")
HOSTED_SETTINGS_URL = os.getenv(
    "HOSTED_SETTINGS_URL",
    "https://updowanfx.com/frontend-settings.json",
)
_last_hosted_load = 0
FRONTEND_DATA = {}


def load_local_settings():
    if not _file.exists():
        return {}

    try:
        saved = json.loads(_file.read_text(encoding="utf-8"))
        return saved if isinstance(saved, dict) else {}
    except Exception:
        return {}


def load_hosted_settings():
    global _last_hosted_load

    try:
        with urllib.request.urlopen(HOSTED_SETTINGS_URL, timeout=8) as response:
            hosted = json.loads(response.read().decode("utf-8"))
        if isinstance(hosted, dict) and isinstance(hosted.get("data"), dict):
            hosted = hosted["data"]
        if not isinstance(hosted, dict) or not hosted:
            return {}

        _file.write_text(
            json.dumps(hosted, indent=2),
            encoding="utf-8",
        )
        _last_hosted_load = time.time()
        print("Hosting settings load हो गई:", hosted)
        return hosted
    except Exception as error:
        print("Hosting settings load नहीं हुई, local file use होगी:", error)
        return {}


def refresh_frontend_data(force=False):
    global FRONTEND_DATA

    if not force and FRONTEND_DATA and time.time() - _last_hosted_load < 15:
        return FRONTEND_DATA

    hosted = load_hosted_settings()
    if hosted:
        FRONTEND_DATA = hosted
        return FRONTEND_DATA

    FRONTEND_DATA = load_local_settings()
    return FRONTEND_DATA


FRONTEND_DATA = refresh_frontend_data(force=True)
print(FRONTEND_DATA)

MOBILE_NUMBER = os.getenv("DAMAN_PHONE", "")
PASSWORD = os.getenv("DAMAN_PASSWORD", "")


SERIAL_CLASS = "TimeLeft__C-id"
BIG_BUTTON_CLASS = "Betting__C-foot-b"
SMALL_BUTTON_CLASS = "Betting__C-foot-s"
AMOUNT_INPUT_CSS = 'input[type="number"]'
RESULT_CSS = ".winner_result"
RESULT_ROW_CSS = ".record-body .van-row"

POLL_INTERVAL_SECONDS = 1
BET_PREPARE_DELAY_SECONDS = 10
BET_AMOUNT = 5
BET_TYPE = "big"
WIN_PAYOUT_RATE = 0.98

DRY_RUN = False
SESSION_STARTED_AT = None
CHROME_DEBUG_PORT = 9222
CHROME_DEBUG_PROFILE = Path(__file__).parent / "chrome-debug-profile"
GAME_SITE = "damanvipgames.com"
LOGIN_URL = "https://damanvipgames.com/#/login"
WINGO_URL = "https://damanvipgames.com/#/saas/Lottery/WinGo?gameCode=WinGo_30S&lottery=WinGo"


def chrome_debug_running():
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version",
            timeout=1,
        )
        return True
    except Exception:
        return False


def start_debug_chrome(start_url="http://127.0.0.1:8000/settings"):
    if chrome_debug_running():
        return

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
        raise Exception("Chrome नहीं मिला।")

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

    raise Exception("Chrome debug port start नहीं हुआ।")


def connect_chrome():
    return webdriver.Chrome()


def open_in_same_tab(browser, url):
    browser.get(url)


def connect_database():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        database="automection",
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

    db.commit()


def to_money(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_risk_limits():
    settings = refresh_frontend_data()
    stop_loss = to_money(settings.get("stopLoss"))
    target_profit = to_money(settings.get("targetProfit"))

    if _file.exists():
        saved = json.loads(_file.read_text(encoding="utf-8"))
        stop_loss = to_money(saved.get("stopLoss"), stop_loss)
        target_profit = to_money(saved.get("targetProfit"), target_profit)

    try:
        db = connect_database()
        cursor = db.cursor()
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

    cursor.execute(
        """
        SELECT COALESCE(SUM(`win_amount`), 0), COALESCE(SUM(`loss_amount`), 0)
        FROM `data`
        WHERE `user_bet` IS NOT NULL
          AND `time` >= %s
        """,
        (SESSION_STARTED_AT,),
    )
    win_total, loss_total = cursor.fetchone()
    return to_money(win_total), to_money(loss_total)


def session_should_stop(cursor):
    stop_loss, target_profit = get_risk_limits()
    win_total, loss_total = get_session_totals(cursor)
    print(
        "Session profit:",
        win_total,
        "/",
        target_profit,
        "Session loss:",
        loss_total,
        "/",
        stop_loss,
    )

    if target_profit and win_total >= target_profit:
        print("Target profit पूरा हो गया। System stop हो रहा है।")
        return True

    if stop_loss and loss_total >= stop_loss:
        print("Stop loss पूरा हो गया। System stop हो रहा है।")
        return True

    return False


def get_user_level_amounts():
    settings = refresh_frontend_data()
    starting = to_money(settings.get("startingAmount"), BET_AMOUNT)
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


def get_next_bet_amount(cursor):
    starting, levels = get_user_level_amounts()

    consecutive_losses = 0
    if SESSION_STARTED_AT is not None:
        cursor.execute(
            """
            SELECT `status`
            FROM `data`
            WHERE `user_bet` IS NOT NULL
              AND `status` IN (1, -1)
              AND `time` >= %s
            ORDER BY `id` DESC
            """,
            (SESSION_STARTED_AT,),
        )
        rows = cursor.fetchall()

        for status_row in rows:
            if status_row[0] == -1:
                consecutive_losses = consecutive_losses + 1
            else:
                break

    # पहली bet और WIN के बाद starting amount।
    # LOSS पर level 1, 2, 3...। सारे level पूरे हों तो फिर starting से शुरू।
    if not levels:
        amount = starting
        print("Next amount: Start bet =", amount)
        return int(amount) if amount == int(amount) else amount

    cycle = consecutive_losses % (len(levels) + 1)
    if cycle == 0:
        amount = starting
        print("Next amount: Start bet =", amount)
    else:
        amount = levels[cycle - 1]
        print("Next amount: Level", cycle, "=", amount)

    if amount == int(amount):
        amount = int(amount)
    return amount


def dismiss_popups(browser):
    for count in range(10):
        announcement_buttons = browser.find_elements(
            By.CLASS_NAME, "announcement-dialog__button"
        )
        reward_buttons = browser.find_elements(
            By.CLASS_NAME, "reward-amount-dialog__button"
        )

        if announcement_buttons:
            announcement_buttons[0].click()
            time.sleep(0.3)
        elif reward_buttons:
            reward_buttons[0].click()
            time.sleep(0.3)
        else:
            break


def is_win_go_open(browser):
    url = ""
    try:
        url = (browser.current_url or "").lower()
    except Exception:
        url = ""

    if "wingo" in url:
        return True

    serials = browser.find_elements(By.CLASS_NAME, SERIAL_CLASS)
    if serials:
        return True

    big_buttons = browser.find_elements(By.CLASS_NAME, BIG_BUTTON_CLASS)
    small_buttons = browser.find_elements(By.CLASS_NAME, SMALL_BUTTON_CLASS)
    return bool(big_buttons and small_buttons)


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
    print("5. Page scroll करके Win Go card खोला जा रहा है...")
    dismiss_popups(browser)
    time.sleep(1)

    for attempt in range(12):
        browser.execute_script("window.scrollBy(0, 350);")
        time.sleep(0.5)

        win_go = find_win_go_card(browser)
        if win_go is None:
            print("Win Go card का इंतजार हो रहा है...")
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
            print("Popup click रोक रहा था। JavaScript click हो रहा है...")
            dismiss_popups(browser)
            browser.execute_script("arguments[0].click();", win_go)

        time.sleep(2)
        if is_win_go_open(browser):
            print("Win Go card से खुल गया।")
            return

    print("Win Go card नहीं मिला। Win Go URL खुल रहा है...")
    browser.get(WINGO_URL)
    time.sleep(2)


def reload_win_go_page(browser):
    print("6. Win Go reload हो रहा है...")
    time.sleep(3)
    browser.refresh()
    time.sleep(2)
    dismiss_popups(browser)
    print("Win Go reload हो गया। अब amount popup खुलेगा।")


def get_serial_from_history(browser):
    records = get_result_records(browser)
    if not records:
        return None

    last_period = records[0][0]
    if last_period.isdigit():
        return str(int(last_period) + 1)
    return last_period


def get_serial_number(browser):
    selectors = [
        (By.CLASS_NAME, SERIAL_CLASS),
        (By.CSS_SELECTOR, ".TimeLeft__C-id"),
        (By.CSS_SELECTOR, "[class*='TimeLeft__C-id']"),
        (By.CSS_SELECTOR, "[class*='TimeLeft']"),
    ]

    for by, value in selectors:
        elements = browser.find_elements(by, value)
        for element in elements:
            text = element.text.strip()
            digits = "".join(character for character in text if character.isdigit())
            if len(digits) >= 10:
                return digits

    history_serial = get_serial_from_history(browser)
    if history_serial:
        return history_serial

    return None


def get_latest_result(browser):
    # Latest result element मौजूद न हो तो खाली values लौटाता है।
    result_elements = browser.find_elements(By.CSS_SELECTOR, RESULT_CSS)

    if not result_elements:
        return None, None

    # Example text: Red, 6, Big
    result_text = result_elements[0].text.strip()
    result_type = None

    # Result की हर line में BIG या SMALL खोजता है।
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

    return records


def get_last_big_small(browser):
    # Game history की पहली row latest completed serial होती है।
    records = get_result_records(browser)

    if records:
        last_result = records[0][1]

        if last_result == "big" or last_result == "small":
            return last_result

    # Result उपलब्ध न हो तो कुछ नहीं लौटाता।
    return None


def save_serial(cursor, db, serial_number):
    query = """
        INSERT INTO `data` (`number`, `status`)
        VALUES (%s, %s)
    """
    cursor.execute(query, (serial_number, 0))
    db.commit()
    print(f"New serial database में save हुआ: {serial_number}")


def save_user_bet(cursor, db, serial_number, bet_type, amount):
    cursor.execute(
        """
        SELECT `id`
        FROM `data`
        WHERE `number` = %s
        ORDER BY `id` DESC
        LIMIT 1
        """,
        (serial_number,),
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
                `number`, `user_bet`, `amount`, `status`, `win_amount`, `loss_amount`
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (serial_number, bet_type.lower(), amount, 0, 0, 0),
        )

    db.commit()
    print("User bet database में save हुई:", bet_type.upper(), "Amount:", amount)


def get_bet_status():
    # किसी argument की जरूरत नहीं। सिर्फ last bet database से पढ़ी जाती है।
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
    # उसी serial की saved bet और amount से WIN/LOSS amount निकाली जाती है।
    cursor.execute(
        """
        SELECT `id`, `user_bet`, `amount`
        FROM `data`
        WHERE `number` = %s
        ORDER BY `id` DESC
        LIMIT 1
        """,
        (serial_number,),
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
        "Result database में save हुआ:",
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
        print("Type केवल big या small हो सकती है।")
        return False

    button = find_bet_button(browser, bet_type)
    if button is None:
        print(bet_type.upper(), "button नहीं मिला।")
        return False

    browser.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
    time.sleep(0.3)

    try:
        button.click()
    except (ElementClickInterceptedException, ElementNotInteractableException):
        print(bet_type.upper(), "normal click नहीं हुआ। JavaScript click हो रहा है...")
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
        print(bet_type.upper(), "amount popup नहीं खुला।")
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
            print(bet_type.upper(), "confirm overlay रोक रहा था। JavaScript click हो रहा है...")
            browser.execute_script("arguments[0].click();", element)
    except Exception as error:
        print(bet_type.upper(), "confirm click नहीं हुआ:", error)
        print(bet_type.upper(), "amount popup खुला। Amount:", amount)
        return True

    print(bet_type.upper(), "amount popup खुला। Amount:", amount)
    print("Confirmation खुद click करें।")
    return True



def bet_result(browser):

    for attempt in range(20):
        result_elements = browser.find_elements(By.CSS_SELECTOR, RESULT_CSS)

        if result_elements:
            result_text = result_elements[0].text.strip()
            return result_text

        print("Result element का इंतजार हो रहा है...")
        time.sleep(1)














def prepare_bet(browser, cursor, db, serial_number):
    # Dry-run में website पर form नहीं खोला जाता; केवल जानकारी print होती है।
    if DRY_RUN:
        print(
            f"[DRY RUN] Serial {serial_number} के लिए "
            f"amount {BET_AMOUNT} की bet तैयार होती।"
        )
        return
        # not change
    try:
        last_big_small = get_last_big_small(browser)
        if not last_big_small:
            result_text, latest_result = get_latest_result(browser)
            last_big_small = latest_result

        if not last_big_small:
            print("Last BIG/SMALL result नहीं मिला।")
            return

        print("Last result:", last_big_small.upper(), "इसका amount popup खुलेगा।")
        amount = get_next_bet_amount(cursor)
        form_is_ready = bet_created(browser, last_big_small, amount)
        save_user_bet(cursor, db, serial_number, last_big_small, amount)

        if form_is_ready:
            print(f"Serial {serial_number} के लिए form तैयार है।")
        else:
            print(f"Serial {serial_number} के लिए popup नहीं खुला।")
    except Exception as error:
        print("Bet prepare error, system चलता रहेगा:", error)
















def watch_serial_numbers(browser, cursor, db):
    global SESSION_STARTED_AT
    SESSION_STARTED_AT = datetime.now()

    old_serial = None
    for attempt in range(40):
        old_serial = get_serial_number(browser)
        if old_serial:
            break
        print("Serial number का इंतजार हो रहा है...")
        time.sleep(1)

    print("Initial serial:", old_serial)
    if old_serial:
        prepare_bet(browser, cursor, db, old_serial)

    # Page खुलते समय मौजूद result को पुराना result माना जाता है।
    old_result_text, old_result_type = get_latest_result(browser)

    if old_result_type:
        print("Initial result:", old_result_type.upper())

    # एक result को बार-बार database में update होने से रोकता है।
    saved_results = set()

    # Program बंद होने तक हर एक सेकंड में serial check होता है।
    while True:
        new_serial = get_serial_number(browser)
        if not new_serial:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        # Latest BIG/SMALL result इस आसान variable में store होता है।
        new_result_text, latest_result = get_latest_result(browser)

        # Game history से मिले नए period results database में save करता है।
        result_records = get_result_records(browser)

        new_result_saved = False

        for result_serial, result_type in result_records:
            result_key = (result_serial, result_type)

            if result_key not in saved_results:
                save_game_result(
                    cursor,
                    db,
                    result_serial,
                    result_type,
                )
                saved_results.add(result_key)
                new_result_saved = True

        if new_result_saved and session_should_stop(cursor):
            print("Monitoring बंद हो गई।")
            return

        # नया result आने पर केवल console में BIG/SMALL दिखाता है।
        if new_result_text and new_result_text != old_result_text:
            old_result_text = new_result_text

            if latest_result:
                print("Latest result:", latest_result.upper())

        # Serial बदलने पर उसे database में save करता है।
        if new_serial != old_serial:
            print("Serial बदला:", old_serial, "से", new_serial)
            old_serial = new_serial
            save_serial(cursor, db, new_serial)

            # नया serial आने के बाद तय समय तक इंतजार करता है।
            time.sleep(BET_PREPARE_DELAY_SECONDS)

            if session_should_stop(cursor):
                print("Monitoring बंद हो गई।")
                return

            # वही serial active हो तो form तैयार करता है।
            if get_serial_number(browser) == new_serial:
                prepare_bet(browser, cursor, db, new_serial)

        time.sleep(POLL_INTERVAL_SECONDS)


def userdata(phone_number, password_text):
    # Login credentials न मिलने पर program स्पष्ट error के साथ रुकता है।
    if not phone_number or not password_text:
        raise ValueError(
            "Mobile number और password settings form से भेजो।"
        )

    # Database cursor query चलाता है और Chrome website automation संभालता है।
    db = connect_database()
    cursor = db.cursor()
    prepare_database(cursor, db)
    browser = connect_chrome()

    try:
        print("1. Login page load हो रहा है...")
        browser.get(LOGIN_URL)
        time.sleep(2)

        phone = None
        for attempt in range(20):
            fields = browser.find_elements(By.NAME, "userNumber")
            if fields and fields[0].is_displayed():
                phone = fields[0]
                break
            time.sleep(0.5)

        if phone is None:
            raise Exception("Login form नहीं मिला।")

        print("2. Login data fill हो रहा है...")
        phone.clear()
        phone.send_keys(phone_number)

        password = browser.find_element(
            By.CSS_SELECTOR, 'input[placeholder="Password"]'
        )
        password.clear()
        password.send_keys(password_text)

        print("3. Login हो रहा है...")
        password.send_keys(Keys.ENTER)
        time.sleep(3)

        print("4. Popups बंद हो रहे हैं...")
        dismiss_popups(browser)
        time.sleep(1)

        open_win_go(browser)
        time.sleep(2)

        reload_win_go_page(browser)
        watch_serial_numbers(browser, cursor, db)
    except KeyboardInterrupt:
        print("Monitoring बंद किया गया।")
    except InvalidSessionIdException:
        print("Chrome connection टूट गई। Settings से दोबारा Submit करो।")
    finally:
        cursor.close()
        db.close()


if __name__ == "__main__":
    userdata(
        MOBILE_NUMBER,
        PASSWORD,
    )
