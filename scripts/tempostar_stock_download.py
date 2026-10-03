"""
Tempostar 在庫変動ログ自動取得（GitHub Actions / ローカル兼用）

- 取得する日付は --dates で指定（YYYY-MM-DD、カンマ区切り）。空なら前日（日本時間）。
- ログイン情報は環境変数から読む（コードには書かない）:
    TEMPOSTAR_COMPANY_ID / TEMPOSTAR_USER_ID / TEMPOSTAR_PASSWORD
- 取得したCSVは  <out-dir>/tempostar_stock_YYYYMMDD.csv  として保存する。
- 1日ぶんでも失敗したら、最後に終了コード1で終わる（成功した日のCSVは残る）。

使い方の例:
    python scripts/tempostar_stock_download.py --headless --dates 2026-09-25,2026-09-27
    python scripts/tempostar_stock_download.py --dry-run          # ブラウザを起動せず対象日だけ確認
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 日本時間（GitHubのサーバーはUTCで動くため、日付判定は必ずJSTで行う）
JST = timezone(timedelta(hours=9))

# =========================================================
# 設定（画面のIDなどが変わったらここを直す）
# =========================================================
STOCK_HISTORY_URL = "https://app.tempostar.net/stock/stockhistory/index.nhn"
LOG_DOWNLOAD_URL = "https://my.tempostar.net/admin/log/download.nhn"

DATE_FROM_ID = "stockupdatedtfrom"  # 更新日(自)
DATE_TO_ID = "stockupdatedtto"      # 更新日(至)

TEMP_SUFFIXES = {".tmp", ".crdownload", ".part"}

MAX_POLL_ROUNDS = 24        # ログ一覧の再確認回数（1回あたり約8〜10秒 → 最大およそ3〜4分）
DOWNLOAD_WAIT_SECONDS = 120  # クリック後、ファイルが落ちてくるのを待つ最大秒数


def now_jst_naive() -> datetime:
    """日本時間の現在時刻（タイムゾーン情報なし）。テンポスターの画面表示と比較するため。"""
    return datetime.now(JST).replace(tzinfo=None)


def log(msg: str) -> None:
    print(f"[{now_jst_naive().strftime('%H:%M:%S')}] {msg}", flush=True)


# =========================================================
# 引数・日付
# =========================================================
def parse_dates(raw: str) -> list:
    """'2026-09-25,2026-09-27' → [date, date]。空なら前日。今日以降は不可。"""
    today = now_jst_naive().date()
    raw = (raw or "").strip()
    if not raw:
        return [today - timedelta(days=1)]

    result = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
            raise ValueError(f"日付の形式が正しくありません: {token!r}（YYYY-MM-DD で指定してください）")
        d = datetime.strptime(token, "%Y-%m-%d").date()
        if d >= today:
            raise ValueError(f"{token} は取得できません（当日以降のログは未確定のため、前日までを指定してください）")
        if d not in result:
            result.append(d)

    if not result:
        raise ValueError("取得する日付が指定されていません")
    return sorted(result)


def env_required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"環境変数 {name} が設定されていません（GitHubのSecretsを確認してください）")
    return value


# =========================================================
# ブラウザ操作
# =========================================================
def setup_driver(download_dir: Path, headless: bool):
    from selenium import webdriver

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=ja-JP")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )
    driver = webdriver.Chrome(options=options)  # ドライバはSelenium Managerが自動解決
    driver.implicitly_wait(10)
    return driver


def login_if_needed(driver) -> None:
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException

    time.sleep(2)
    try:
        company_input = driver.find_element(By.ID, "companyid")
    except NoSuchElementException:
        return  # 既にログイン済み

    user_input = driver.find_element(By.ID, "userid")
    pw_input = driver.find_element(By.ID, "password")

    company_input.clear()
    company_input.send_keys(env_required("TEMPOSTAR_COMPANY_ID"))
    user_input.clear()
    user_input.send_keys(env_required("TEMPOSTAR_USER_ID"))
    pw_input.clear()
    pw_input.send_keys(env_required("TEMPOSTAR_PASSWORD"))

    driver.find_element(
        By.XPATH, "//input[@type='submit' and contains(@value,'ログイン')]"
    ).click()
    time.sleep(5)

    # まだログイン画面のままなら失敗（ID/パスワード違い、IP制限、追加認証など）
    if driver.find_elements(By.ID, "companyid"):
        raise RuntimeError(
            "ログインに失敗しました。ID/パスワードの誤り、または実行元（GitHubのサーバー）からの"
            "ログインが制限されている可能性があります。"
        )


def set_date_range_and_search(driver, display_date: str) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    wait = WebDriverWait(driver, 20)
    date_from = wait.until(EC.presence_of_element_located((By.ID, DATE_FROM_ID)))
    date_to = wait.until(EC.presence_of_element_located((By.ID, DATE_TO_ID)))

    date_from.clear()
    date_from.send_keys(display_date)
    date_to.clear()
    date_to.send_keys(display_date)

    driver.find_element(By.XPATH, "//input[@type='button' and @value='検索']").click()
    time.sleep(5)


def click_create_log_button(driver) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    btn = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable(
            (By.XPATH, "//input[@type='button' and @value='在庫変動ログファイル作成']")
        )
    )
    btn.click()
    time.sleep(5)


def click_log_search_button(driver) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    btn = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='submit' and @value='検索']"))
    )
    btn.click()
    time.sleep(3)


def _is_chrome_temp_file(p: Path) -> bool:
    """Chrome(Linux)がダウンロード中に作る隠しの一時ファイル（例: .com.google.Chrome.55uGza）かどうか。
    拡張子が .tmp / .crdownload / .part と違う形式なので、別途ここで弾く。"""
    return p.name.startswith(".")


def wait_for_download(download_dir: Path, click_time: float):
    """ダウンロード専用フォルダに、完成したファイルが現れるのを待つ。"""
    deadline = time.time() + DOWNLOAD_WAIT_SECONDS
    while time.time() < deadline:
        try:
            files = [p for p in download_dir.iterdir() if p.is_file()]
        except FileNotFoundError:
            # 列挙している間にファイルが消えた（＝まだ書き込み中）→ 少し待って再試行
            time.sleep(2)
            continue

        in_progress = [
            p for p in files
            if p.suffix.lower() in TEMP_SUFFIXES or _is_chrome_temp_file(p)
        ]
        finished = []
        for p in files:
            if p in in_progress:
                continue
            try:
                if p.stat().st_mtime >= click_time - 1:
                    finished.append(p)
            except FileNotFoundError:
                continue  # 消えた（一時ファイルが完成前に掴まれた）→ 無視して次へ

        if finished and not in_progress:
            try:
                newest = max(finished, key=lambda p: p.stat().st_mtime)
                # サイズが安定するまで少し待つ（書き込み途中のファイルを掴まないため）
                size1 = newest.stat().st_size
                time.sleep(1.5)
                if newest.stat().st_size == size1:
                    return newest
            except FileNotFoundError:
                pass  # 消えた → 次のループでやり直す
        time.sleep(2)
    return None


def download_newest_log(driver, download_dir: Path, request_time: datetime):
    """ログダウンロード画面で今回依頼した行を探し、ダウンロードして保存ファイルのパスを返す。"""
    from selenium.webdriver.common.by import By

    driver.get(LOG_DOWNLOAD_URL)

    for _ in range(MAX_POLL_ROUNDS):
        click_log_search_button(driver)

        rows = driver.find_elements(By.CSS_SELECTOR, "table.m-table tbody tr")
        candidates = []
        for row in rows:
            tds = row.find_elements(By.CSS_SELECTOR, "td")
            if len(tds) < 9:
                continue
            req_text = tds[0].text.strip()      # 依頼日時
            status_text = tds[4].text.strip()   # 状態
            result_text = tds[5].text.strip()   # 処理結果
            content_text = tds[6].text.strip()  # 処理内容
            try:
                req_dt = datetime.strptime(req_text, "%Y/%m/%d %H:%M:%S")
            except ValueError:
                continue
            if (
                req_dt >= request_time - timedelta(minutes=2)
                and "在庫変動履歴ファイル" in content_text
                and "完了" in status_text
                and "正常" in result_text
            ):
                candidates.append((req_dt, row))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            link = candidates[0][1].find_element(By.CSS_SELECTOR, "a.download__lnk")
            click_time = time.time()
            link.click()
            return wait_for_download(download_dir, click_time)

        time.sleep(5)
        driver.refresh()

    return None


def save_debug(driver, debug_dir, label: str) -> None:
    """失敗時の原因調査用に、画面のスクリーンショットとHTMLを保存する。"""
    if not debug_dir or driver is None:
        return
    try:
        debug_dir = Path(debug_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_jst_naive().strftime("%H%M%S")
        driver.save_screenshot(str(debug_dir / f"{label}_{stamp}.png"))
        (debug_dir / f"{label}_{stamp}.html").write_text(driver.page_source, encoding="utf-8")
        (debug_dir / f"{label}_{stamp}.txt").write_text(
            f"url: {driver.current_url}\ntitle: {driver.title}\n", encoding="utf-8"
        )
    except Exception as e:  # デバッグ保存の失敗で本処理を止めない
        log(f"デバッグ情報の保存に失敗: {e}")


def fetch_one_day(driver, target: date, download_dir: Path, out_dir: Path) -> Path:
    display = target.strftime("%Y/%m/%d")
    file_stamp = target.strftime("%Y%m%d")

    # 前回の残りファイルを掃除（別の日のファイルを誤って拾わないため）
    for p in download_dir.iterdir():
        if p.is_file():
            p.unlink()

    driver.get(STOCK_HISTORY_URL)
    login_if_needed(driver)
    driver.get(STOCK_HISTORY_URL)
    time.sleep(3)

    set_date_range_and_search(driver, display)

    request_time = now_jst_naive()
    click_create_log_button(driver)

    downloaded = download_newest_log(driver, download_dir, request_time)
    if not downloaded:
        raise RuntimeError("在庫変動ログのダウンロードファイルが見つかりませんでした（時間切れ）")

    target_path = out_dir / f"tempostar_stock_{file_stamp}.csv"
    shutil.move(str(downloaded), str(target_path))
    return target_path


# =========================================================
# main
# =========================================================
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tempostar在庫変動ログを取得する")
    parser.add_argument("--dates", default="", help="取得日（YYYY-MM-DD、カンマ区切り）。空なら前日")
    parser.add_argument("--out-dir", default=".", help="CSVの保存先フォルダ")
    parser.add_argument("--headless", action="store_true", help="ブラウザ画面を出さずに実行")
    parser.add_argument("--debug-dir", default="", help="失敗時のスクリーンショット等の保存先")
    parser.add_argument("--dry-run", action="store_true", help="ブラウザを起動せず、対象日だけ表示して終了")
    args = parser.parse_args(argv)

    try:
        targets = parse_dates(args.dates)
    except ValueError as e:
        print(f"引数エラー: {e}", file=sys.stderr)
        return 2

    log("取得対象: " + ", ".join(d.strftime("%Y-%m-%d") for d in targets))
    if args.dry_run:
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 認証情報が無い場合はブラウザを起動する前に止める
    env_required("TEMPOSTAR_COMPANY_ID")
    env_required("TEMPOSTAR_USER_ID")
    env_required("TEMPOSTAR_PASSWORD")

    download_dir = Path(tempfile.mkdtemp(prefix="tempostar_dl_"))
    driver = None
    succeeded, failed = [], []
    try:
        driver = setup_driver(download_dir, args.headless)
        for target in targets:
            label = target.strftime("%Y-%m-%d")
            log(f"--- {label} の取得を開始 ---")
            try:
                path = fetch_one_day(driver, target, download_dir, out_dir)
                log(f"保存完了: {path}")
                succeeded.append(label)
            except Exception as e:
                log(f"ERROR ({label}): {e}")
                traceback.print_exc()
                save_debug(driver, args.debug_dir, f"error_{label}")
                failed.append(label)
    finally:
        if driver is not None:
            driver.quit()
        shutil.rmtree(download_dir, ignore_errors=True)

    log(f"結果: 成功 {len(succeeded)} 件 / 失敗 {len(failed)} 件")
    if failed:
        log("失敗した日付: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
