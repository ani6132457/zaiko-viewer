import streamlit as st
import pandas as pd
import glob
import os
import html
import re
import json
import requests
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit.components.v1 as components
from datetime import datetime, timedelta
from pandas.tseries.offsets import DateOffset

# 追加（オーバーレイ表示用）
import base64
import io
import plotly.graph_objects as go

# 追加（ZOZO在庫チェック用。外部ライブラリを増やさないよう標準ライブラリのみで実装。
#         ※ html モジュールは冒頭で import 済みのものを流用）


# ==========================
# Tempostar CSV 読み込み
# ==========================
@st.cache_data
def load_tempostar_data(file_paths):
    dfs = []
    for path in file_paths:
        df = pd.read_csv(path, encoding="cp932")
        df["元ファイル"] = os.path.basename(path)
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    # 数値列を明示的に変換
    for col in ["増減値", "変動後"]:
        if col in all_df.columns:
            all_df[col] = (
                pd.to_numeric(all_df[col], errors="coerce")
                .fillna(0)
                .astype(int)
            )
    return all_df


# ==========================
# 商品画像マスタ読み込み
# ==========================
@st.cache_data
def load_image_master():
    folder = "商品画像URLマスタ"
    paths = glob.glob(os.path.join(folder, "*.csv"))

    if not paths:
        return {}

    dfs = []
    for p in paths:
        df = pd.read_csv(p, encoding="cp932")
        if "商品管理番号（商品URL）" in df.columns and "商品画像パス1" in df.columns:
            dfs.append(df[["商品管理番号（商品URL）", "商品画像パス1"]])

    if not dfs:
        return {}

    merged = pd.concat(dfs, ignore_index=True)
    merged["商品管理番号（商品URL）"] = merged["商品管理番号（商品URL）"].astype(str).str.strip()
    merged["商品画像パス1"] = merged["商品画像パス1"].astype(str).str.strip()

    return dict(zip(merged["商品管理番号（商品URL）"], merged["商品画像パス1"]))


# ==========================
# SKUマスター自動読み込み（CS品番 ⇔ 弊社SKU）
# ==========================
@st.cache_data
def load_sku_master():
    """
    「SKUマスター」フォルダ内のCSV（列: CS品番, SKU）を自動読み込みし、
    納品推奨数システムに渡すための [{"cs_no":..., "sku":...}, ...] を返す。
    """
    folder = "SKUマスター"
    paths = glob.glob(os.path.join(folder, "*.csv"))
    if not paths:
        return []

    dfs = []
    for p in paths:
        try:
            df = pd.read_csv(p, encoding="cp932")
        except UnicodeDecodeError:
            df = pd.read_csv(p, encoding="utf-8-sig")
        if "CS品番" in df.columns and "SKU" in df.columns:
            dfs.append(df[["CS品番", "SKU"]])

    if not dfs:
        return []

    merged = pd.concat(dfs, ignore_index=True)
    merged["CS品番"] = merged["CS品番"].astype(str).str.strip()
    merged["SKU"] = merged["SKU"].astype(str).str.strip()
    merged = merged[(merged["CS品番"] != "") & (merged["SKU"] != "")]
    merged = merged.drop_duplicates(subset=["CS品番"], keep="last")

    return [
        {"cs_no": row["CS品番"], "sku": row["SKU"]}
        for _, row in merged.iterrows()
    ]


# ==========================
# アップロードCSVの柔軟読み込み（エンコーディング自動判定）
# ==========================
def read_csv_flexible(uploaded_file):
    """
    Streamlitのアップロードファイル（cp932 / utf-8-sig どちらもあり得る）を
    順番に試して読み込む。全部失敗したらNoneを返す。
    """
    raw = uploaded_file.getvalue()
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc)
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return None


# ==========================
# テンポスター現在庫マップ（商品コード＝SKU → 現在庫）
# ==========================
@st.cache_data
def get_tempostar_stock_map(file_paths):
    """
    全期間のTempostar CSVから、商品コードごとの最新の「変動後」在庫数を計算する。
    （フィルター期間に関わらず、常に最新の在庫を反映するため全ファイルを対象にする）
    """
    if not file_paths:
        return {}

    df_all = load_tempostar_data(tuple(sorted(file_paths)))

    if "商品コード" not in df_all.columns or "変動後" not in df_all.columns:
        return {}

    df_all = df_all.copy()
    df_all["商品コード"] = df_all["商品コード"].astype(str).str.strip()

    # ファイル名の日付順に並べ替えてから、SKUごとに最後の値（＝最新在庫）を取る
    df_all["_日付"] = df_all["元ファイル"].astype(str).str.extract(r"(\d{8})")
    df_all = df_all.sort_values("_日付")

    stock = (
        df_all.groupby("商品コード", dropna=False)["変動後"]
        .last()
        .to_dict()
    )
    return {k: int(v) for k, v in stock.items()}


@st.cache_data
def get_all_sku_snapshot(file_paths):
    """
    全期間のTempostar CSVから、商品コードごとに最新の商品情報・在庫を1行にまとめる。
    絞り込み期間内に一度もCSVに登場しない（＝その期間は動きが無かった）商品も含め、
    「現時点で存在する全SKU」を常に拾えるようにするためのもの。
    """
    if not file_paths:
        return pd.DataFrame()

    df_all = load_tempostar_data(tuple(sorted(file_paths)))
    if "商品コード" not in df_all.columns:
        return pd.DataFrame()

    df_all = df_all.copy()
    df_all["商品コード"] = df_all["商品コード"].astype(str).str.strip()
    df_all["_日付"] = df_all["元ファイル"].astype(str).str.extract(r"(\d{8})")
    df_all = df_all.sort_values("_日付")

    agg = {}
    for col in ["商品基本コード", "商品名", "属性1名", "属性2名", "変動後"]:
        if col in df_all.columns:
            agg[col] = "last"

    snap = df_all.groupby("商品コード", dropna=False).agg(agg).reset_index()

    if "変動後" in snap.columns:
        snap = snap.rename(columns={"変動後": "現在庫"})
        snap["現在庫"] = pd.to_numeric(snap["現在庫"], errors="coerce").fillna(0).astype(int)
    else:
        snap["現在庫"] = 0

    return snap


# ==========================
# テンポスター日別売上マップ（SKU → [{d:'YYYYMMDD', q:数量}, ...]）
# ==========================
@st.cache_data
def get_tempostar_sales_map(file_paths):
    """
    全期間のTempostar CSVから、商品コード（SKU）×日付ごとの売上個数を集計する。
    納品推奨数システム側で任意の期間を選んで自社売上個数を合算できるようにするため、
    日別の時系列データとして返す。
    """
    if not file_paths:
        return {}

    df_all = load_tempostar_data(tuple(sorted(file_paths)))

    required = {"商品コード", "増減値"}
    if not required.issubset(df_all.columns):
        return {}

    df_all = df_all.copy()
    df_all["商品コード"] = df_all["商品コード"].astype(str).str.strip()
    df_all["_日付"] = df_all["元ファイル"].astype(str).str.extract(r"(\d{8})")

    if "更新理由" in df_all.columns:
        df_sales = df_all[df_all["更新理由"].astype(str).str.contains("受注取込", na=False)].copy()
    else:
        df_sales = df_all.copy()

    df_sales["売上個数"] = -df_sales["増減値"]
    df_sales = df_sales[df_sales["売上個数"] > 0]

    grouped = (
        df_sales.groupby(["商品コード", "_日付"], dropna=False)["売上個数"]
        .sum()
        .reset_index()
    )

    sales_map = {}
    for sku, sub in grouped.groupby("商品コード"):
        sub = sub.sort_values("_日付")
        sales_map[sku] = [
            {"d": row["_日付"], "q": int(row["売上個数"])}
            for _, row in sub.iterrows()
            if pd.notna(row["_日付"])
        ]
    return sales_map


# ==========================
# 日別在庫履歴（納品推奨数システムの在庫推移グラフ用）
# ==========================
@st.cache_data
def get_tempostar_stock_history_map(file_paths):
    """
    全期間のTempostar CSVから、商品コードごとの日別在庫（変動後）推移を作る。
    戻り値: { 商品コード: [{"d": "YYYYMMDD", "stock": 在庫数}, ...] }（日付昇順）
    """
    if not file_paths:
        return {}

    df_all = load_tempostar_data(tuple(sorted(file_paths)))
    required = {"商品コード", "変動後"}
    if not required.issubset(df_all.columns):
        return {}

    df_all = df_all.copy()
    df_all["商品コード"] = df_all["商品コード"].astype(str).str.strip()
    df_all["_日付"] = df_all["元ファイル"].astype(str).str.extract(r"(\d{8})")
    df_all = df_all.dropna(subset=["_日付", "変動後"])

    grouped = (
        df_all.sort_values("_日付")
        .groupby(["商品コード", "_日付"], dropna=False)["変動後"]
        .last()
        .reset_index()
    )

    history_map = {}
    for sku, sub in grouped.groupby("商品コード"):
        sub = sub.sort_values("_日付")
        history_map[sku] = [
            {"d": row["_日付"], "stock": int(row["変動後"])}
            for _, row in sub.iterrows()
        ]
    return history_map


# ==========================
# 売上個数予想（去年の「翌日」を起点にした期間集計）
# ==========================
def default_forecast_range():
    """
    「去年の翌日」を起点にしたデフォルト期間（1ヶ月分）を返す。
    例）今日が2026/08/08なら、去年の同日は2025/08/08、その翌日である
    2025/08/09を起点日とし、そこから1ヶ月後までをデフォルト範囲にする。
    """
    today = datetime.now().date()
    try:
        last_year_today = today.replace(year=today.year - 1)
    except ValueError:
        # うるう年の2/29対応
        last_year_today = today.replace(year=today.year - 1, day=28)
    start = last_year_today + timedelta(days=1)
    end = (pd.Timestamp(start) + DateOffset(months=1)).date()
    return start, end


def compute_forecast_map(sales_map, start_date, end_date):
    """
    get_tempostar_sales_map() が返す日別売上データから、
    指定期間内の合計を商品コードごとに集計する。
    戻り値: { 商品コード: 期間内合計売上個数 }
    """
    if not sales_map or start_date is None or end_date is None or start_date > end_date:
        return {}
    s = start_date.strftime("%Y%m%d")
    e = end_date.strftime("%Y%m%d")
    result = {}
    for sku, entries in sales_map.items():
        total = sum(item["q"] for item in entries if s <= item["d"] <= e)
        if total > 0:
            result[sku] = total
    return result


# ==========================
# 楽天RMS 在庫API 2.0 連携
# ==========================
# ※ manageNumber（商品管理番号）＝ Tempostarの「商品基本コード」
#    variantId（楽天SKU）＝ Tempostarの「商品コード」 と同一の運用であることを前提にしています。
# ※ エンドポイント/リクエスト形式は在庫更新用の bulk-upsert と対になる一括取得APIを想定しています。
#    実際にRMSの管理画面（WEB APIサービス配下のAPIドキュメント）で最終確認のうえご利用ください。
RAKUTEN_INVENTORY_BULK_GET_URL = "https://api.rms.rakuten.co.jp/es/2.0/inventories/bulk-get"
RAKUTEN_BATCH_SIZE = 100
RAKUTEN_MAX_WORKERS = 8  # 並列で投げるバッチ数（楽天側のレート制限に応じて調整可）
RAKUTEN_MAX_RETRIES = 4  # 429（レート制限）時の最大リトライ回数
RAKUTEN_RETRY_BASE_WAIT = 2.0  # リトライ時の待機秒数の基準（指数バックオフ: 2s, 4s, 8s, 16s...）


@st.cache_data
def get_rakuten_sku_pairs(file_paths):
    """
    Tempostar CSV全体から (商品基本コード=manageNumber, 商品コード=variantId) の
    重複なしペア一覧を作る。楽天APIへ問い合わせる対象リストとして使う。
    """
    if not file_paths:
        return tuple()

    df_all = load_tempostar_data(tuple(sorted(file_paths)))
    required = {"商品コード", "商品基本コード"}
    if not required.issubset(df_all.columns):
        return tuple()

    pairs = (
        df_all[["商品基本コード", "商品コード"]]
        .astype(str)
        .apply(lambda s: s.str.strip())
        .drop_duplicates()
    )
    pairs = pairs[(pairs["商品基本コード"] != "") & (pairs["商品コード"] != "")]
    return tuple(
        (row["商品基本コード"], row["商品コード"]) for _, row in pairs.iterrows()
    )


def _rakuten_auth_header():
    try:
        rakuten_secrets = st.secrets.get("rakuten", {})
    except Exception:
        rakuten_secrets = {}
    service_secret = rakuten_secrets.get("service_secret")
    license_key = rakuten_secrets.get("license_key")
    if not service_secret or not license_key:
        return None
    token = base64.b64encode(f"{service_secret}:{license_key}".encode("utf-8")).decode("utf-8")
    return f"ESA {token}"


# ---- バックグラウンド更新用の共有キャッシュ（プロセス内メモリ上に保持） ----
# st.cache_data（同期・画面ブロック型）ではなく、別スレッドで取得して
# 終わったものから順に反映する方式にすることで、取得中も画面操作をブロックしない。
#
# 注意: 通常のモジュール直下の変数（例: _state = {...}）は、Streamlitが
# スクリプトを再実行するたびに再代入されて中身が消えてしまう。
# st.cache_resource を使うことで、rerunをまたいでプロセス内に持続させている。


@st.cache_resource
def _get_rakuten_bg_container():
    return {
        "lock": threading.Lock(),
        "map": {},
        "errors": [],
        "fetched_at": None,   # 表示用の "HH:MM:SS"
        "fetched_ts": 0.0,    # TTL判定用のUNIX時刻
        "fetching": False,
        "fetching_started_ts": 0.0,
    }


def _rakuten_fetch_worker(state, pairs, auth_header):
    stock_map = {}
    errors = []
    try:
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json; charset=utf-8",
        }
        batches = [pairs[i:i + RAKUTEN_BATCH_SIZE] for i in range(0, len(pairs), RAKUTEN_BATCH_SIZE)]

        def fetch_batch(batch):
            body = {"inventories": [{"manageNumber": mn, "variantId": vid} for mn, vid in batch]}
            last_exc = None
            for attempt in range(RAKUTEN_MAX_RETRIES + 1):
                resp = requests.post(RAKUTEN_INVENTORY_BULK_GET_URL, headers=headers, json=body, timeout=15)
                if resp.status_code == 429:
                    last_exc = requests.exceptions.HTTPError(
                        f"429 Too Many Requests（{attempt + 1}回目）", response=resp
                    )
                    if attempt < RAKUTEN_MAX_RETRIES:
                        # Retry-Afterヘッダーがあればそれを優先、なければ指数バックオフ＋ランダムな揺らぎ
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait_sec = float(retry_after)
                            except ValueError:
                                wait_sec = RAKUTEN_RETRY_BASE_WAIT * (2 ** attempt)
                        else:
                            wait_sec = RAKUTEN_RETRY_BASE_WAIT * (2 ** attempt)
                        wait_sec += random.uniform(0, 0.5)  # 複数バッチが同時に再送されるのを避ける
                        time.sleep(wait_sec)
                        continue
                    break
                resp.raise_for_status()
                return resp.json()
            raise last_exc

        with ThreadPoolExecutor(max_workers=RAKUTEN_MAX_WORKERS) as executor:
            future_to_idx = {executor.submit(fetch_batch, b): i for i, b in enumerate(batches)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    res_json = future.result()
                    for item in res_json.get("inventories", []):
                        vid = item.get("variantId")
                        qty = item.get("quantity")
                        if vid is not None and qty is not None:
                            stock_map[str(vid)] = int(qty)
                except Exception as e:
                    errors.append(f"{idx + 1}件目バッチ: {e}")
    except Exception as e:
        # 想定外のエラーが起きても「取得中」のまま固まらないよう、必ずここを通す
        errors.append(f"予期しないエラー: {e}")
    finally:
        with state["lock"]:
            state["map"] = stock_map
            state["errors"] = errors
            state["fetched_at"] = datetime.now().strftime("%H:%M:%S")
            state["fetched_ts"] = time.time()
            state["fetching"] = False


def get_rakuten_stock_state(pairs, force=False):
    """
    楽天RMS 在庫API 2.0 の在庫データを取得する（非ブロッキング）。
    ・裏側のスレッドで取得し、取得中もその場では待たずに現在キャッシュされている値を返す
    ・取得が完了すると、次にこの関数が呼ばれた（＝次の画面操作でrerunされた）タイミングで新しい値に切り替わる
    ・時間経過による自動再取得は行わない。force=Trueの時だけ取得を開始する
      （呼び出し側で「このセッションでまだ取得していない＝ページ再読み込み直後」または
      「手動更新ボタンが押された」場合にforce=Trueを渡す想定）
    戻り値: (stock_map, errors, fetched_at表示文字列, fetching中かどうか)

    認証情報は .streamlit/secrets.toml に以下の形式で設定してください。
        [rakuten]
        service_secret = "..."
        license_key = "..."
    """
    auth_header = _rakuten_auth_header()
    if not auth_header or not pairs:
        return {}, [], None, False

    state = _get_rakuten_bg_container()

    with state["lock"]:
        # 何らかの理由でスレッドが完了せず「取得中」のまま固まった場合の自己回復
        # （429リトライ時の待機（最大30秒程度×複数バッチ）も考慮し、5分を大きく超えたら異常とみなす）
        if state["fetching"] and (time.time() - state["fetching_started_ts"]) > 300:
            state["fetching"] = False
            state["errors"] = ["前回の取得が完了しないまま長時間経過したため、状態をリセットしました。"]

        already_fetching = state["fetching"]
        should_start = force and not already_fetching

        if should_start:
            state["fetching"] = True
            state["fetching_started_ts"] = time.time()

        result = (
            dict(state["map"]),
            list(state["errors"]),
            state["fetched_at"],
            state["fetching"],
        )

    if should_start:
        t = threading.Thread(target=_rakuten_fetch_worker, args=(state, pairs, auth_header), daemon=True)
        t.start()

    return result


# ==========================
# Amazon SP-API（FBA在庫）連携
# ==========================
# ※ Amazonの出品者SKU ＝ Tempostarの「商品コード」と同一の運用であることを前提にしています。
# ※ Amazon公式のベストプラクティスとして、getInventorySummariesは1日に複数回呼ぶことは
#    推奨されていない（1日1回程度のスナップショット取得が想定されている）ため、
#    楽天と異なり自動更新の間隔を長め（20時間）にしています。手動更新ボタンはいつでも押せます。
# ※ FBA在庫APIは楽天よりレート制限が厳しめのため、並列数を低めに設定しています。
AMAZON_LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
AMAZON_SPAPI_ENDPOINT = "https://sellingpartnerapi-fe.amazon.com"  # 日本を含む極東リージョン
AMAZON_MARKETPLACE_ID_JP = "A1VC38T7YXB528"
AMAZON_INVENTORY_BATCH_SIZE = 50
AMAZON_MAX_WORKERS = 2
AMAZON_MAX_RETRIES = 4
AMAZON_RETRY_BASE_WAIT = 3.0
AMAZON_TTL_SECONDS = 20 * 60 * 60  # 20時間


@st.cache_data
def get_amazon_sku_list(file_paths):
    """Tempostar CSV全体から商品コード（＝Amazon出品者SKU）の重複なし一覧を作る。"""
    if not file_paths:
        return tuple()
    df_all = load_tempostar_data(tuple(sorted(file_paths)))
    if "商品コード" not in df_all.columns:
        return tuple()
    skus = df_all["商品コード"].astype(str).str.strip()
    skus = skus[skus != ""].drop_duplicates()
    return tuple(sorted(skus.tolist()))


def _amazon_credentials():
    try:
        amazon_secrets = st.secrets.get("amazon", {})
    except Exception:
        amazon_secrets = {}
    client_id = amazon_secrets.get("client_id")
    client_secret = amazon_secrets.get("client_secret")
    refresh_token = amazon_secrets.get("refresh_token")
    if not client_id or not client_secret or not refresh_token:
        return None
    return {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}


def _amazon_credentials_diagnosis():
    """
    認証情報が読めない場合に、具体的に何が原因かを切り分けるための診断メッセージを返す。
    問題なければ None を返す。
    """
    try:
        top_level_keys = list(st.secrets.keys())
    except Exception as e:
        return f"st.secrets自体の読み込みに失敗しています（TOMLの書式エラーの可能性）: {e}"

    if "amazon" not in top_level_keys:
        return (
            f"Secretsに [amazon] セクションが見つかりません。"
            f"実際に読み込めているセクション名: {top_level_keys}"
        )

    try:
        amazon_secrets = st.secrets.get("amazon", {})
    except Exception as e:
        return f"[amazon] セクションの読み込みに失敗しています: {e}"

    missing = [
        name for name, val in [
            ("client_id", amazon_secrets.get("client_id")),
            ("client_secret", amazon_secrets.get("client_secret")),
            ("refresh_token", amazon_secrets.get("refresh_token")),
        ] if not val
    ]
    if missing:
        return f"[amazon] セクションはありますが、次の項目が空または未設定です: {', '.join(missing)}"

    return None


def _amazon_get_access_token(creds):
    """リフレッシュトークンからアクセストークン（有効期限約1時間）を取得する。"""
    resp = requests.post(
        AMAZON_LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@st.cache_resource
def _get_amazon_bg_container():
    return {
        "lock": threading.Lock(),
        "map": {},  # { SKU: {"fulfillable":n, "inbound":n, "reserved":n, "unfulfillable":n} }
        "errors": [],
        "fetched_at": None,
        "fetched_ts": 0.0,
        "fetching": False,
        "fetching_started_ts": 0.0,
    }


def _amazon_fetch_worker(state, skus, creds):
    stock_map = {}
    errors = []
    try:
        access_token = _amazon_get_access_token(creds)
        headers = {
            "x-amz-access-token": access_token,
            "Content-Type": "application/json",
            "User-Agent": "InventoryDashboard/1.0 (Language=Python)",
        }
        batches = [skus[i:i + AMAZON_INVENTORY_BATCH_SIZE] for i in range(0, len(skus), AMAZON_INVENTORY_BATCH_SIZE)]

        def fetch_batch(batch):
            params = {
                "details": "true",
                "granularityType": "Marketplace",
                "granularityId": AMAZON_MARKETPLACE_ID_JP,
                "marketplaceIds": AMAZON_MARKETPLACE_ID_JP,
                "sellerSkus": ",".join(batch),
            }
            last_exc = None
            for attempt in range(AMAZON_MAX_RETRIES + 1):
                resp = requests.get(
                    f"{AMAZON_SPAPI_ENDPOINT}/fba/inventory/v1/summaries",
                    headers=headers, params=params, timeout=15,
                )
                if resp.status_code == 429:
                    last_exc = requests.exceptions.HTTPError(
                        f"429 Too Many Requests（{attempt + 1}回目）", response=resp
                    )
                    if attempt < AMAZON_MAX_RETRIES:
                        wait_sec = AMAZON_RETRY_BASE_WAIT * (2 ** attempt) + random.uniform(0, 0.5)
                        time.sleep(wait_sec)
                        continue
                    break
                resp.raise_for_status()
                return resp.json()
            raise last_exc

        with ThreadPoolExecutor(max_workers=AMAZON_MAX_WORKERS) as executor:
            future_to_idx = {executor.submit(fetch_batch, b): i for i, b in enumerate(batches)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    res_json = future.result()
                    summaries = res_json.get("payload", {}).get("inventorySummaries", [])
                    for item in summaries:
                        sku = item.get("sellerSku")
                        if not sku:
                            continue
                        detail = item.get("inventoryDetails", {}) or {}
                        reserved = detail.get("reservedQuantity", {})
                        stock_map[str(sku)] = {
                            "fulfillable": detail.get("fulfillableQuantity", 0),
                            "inbound": (
                                detail.get("inboundWorkingQuantity", 0)
                                + detail.get("inboundShippedQuantity", 0)
                                + detail.get("inboundReceivingQuantity", 0)
                            ),
                            "reserved": reserved.get("totalReservedQuantity", 0) if isinstance(reserved, dict) else 0,
                            "unfulfillable": detail.get("totalUnfulfillableQuantity", 0),
                        }
                except Exception as e:
                    errors.append(f"{idx + 1}件目バッチ: {e}")
    except Exception as e:
        # LWAトークン取得失敗など、想定外のエラーが起きても「取得中」のまま固まらないようにする
        errors.append(f"予期しないエラー: {e}")
    finally:
        with state["lock"]:
            state["map"] = stock_map
            state["errors"] = errors
            state["fetched_at"] = datetime.now().strftime("%H:%M:%S")
            state["fetched_ts"] = time.time()
            state["fetching"] = False


def get_amazon_fba_stock_state(skus, force=False):
    """
    Amazon FBA在庫（getInventorySummaries）を取得する（非ブロッキング）。
    Amazon公式ベストプラクティスに従い、自動更新は約20時間に1回。手動更新ボタンでいつでも即時取得できる。
    戻り値: (stock_map, errors, fetched_at表示文字列, fetching中かどうか)

    認証情報は .streamlit/secrets.toml に以下の形式で設定してください。
        [amazon]
        client_id = "..."
        client_secret = "..."
        refresh_token = "..."
    """
    creds = _amazon_credentials()
    if not creds or not skus:
        return {}, [], None, False

    state = _get_amazon_bg_container()

    with state["lock"]:
        if state["fetching"] and (time.time() - state["fetching_started_ts"]) > 300:
            state["fetching"] = False
            state["errors"] = ["前回の取得が完了しないまま長時間経過したため、状態をリセットしました。"]

        is_stale = (time.time() - state["fetched_ts"]) > AMAZON_TTL_SECONDS
        already_fetching = state["fetching"]
        should_start = (force or is_stale) and not already_fetching

        if should_start:
            state["fetching"] = True
            state["fetching_started_ts"] = time.time()

        result = (
            dict(state["map"]),
            list(state["errors"]),
            state["fetched_at"],
            state["fetching"],
        )

    if should_start:
        t = threading.Thread(target=_amazon_fetch_worker, args=(state, skus, creds), daemon=True)
        t.start()

    return result



def render_rakuten_refresh_control(fetched_at, fetching, errors, key):
    """
    検索条件などの近くに置く、楽天在庫の状態表示＋手動更新ボタン。
    非ブロッキング取得なので、押してもすぐ手が離せる（裏で取得が進み、
    完了すると次の画面操作で自動的に新しい値に切り替わる）。
    """
    c1, c2 = st.columns([5, 2])
    with c1:
        if fetching:
            st.caption("📦 楽天在庫を裏で取得中…（そのまま操作を続けられます。完了後、次の操作で反映されます）")
        elif fetched_at:
            st.caption(f"📦 楽天在庫 最終取得: {fetched_at}（ページ再読み込みまで自動更新しません。最新を見たい時は右のボタンを押してください）")
        else:
            st.caption("📦 楽天在庫：未取得")
        if errors:
            st.caption(f"⚠️ 一部取得エラー: {errors[0]}")
    with c2:
        if st.button("🔄 楽天在庫を更新", key=f"rakuten_refresh_{key}", disabled=fetching, use_container_width=True):
            st.session_state["rakuten_force_refresh"] = True
            st.rerun()


def render_amazon_refresh_control(fetched_at, fetching, errors, key):
    """
    Amazon FBA在庫の状態表示＋手動更新ボタン。楽天と同じく非ブロッキング。
    Amazon公式の推奨（1日1回程度）に沿って、自動更新は約20時間間隔にしている。
    """
    c1, c2 = st.columns([5, 2])
    with c1:
        if fetching:
            st.caption("📦 Amazon FBA在庫を裏で取得中…（そのまま操作を続けられます。完了後、次の操作で反映されます）")
        elif fetched_at:
            st.caption(f"📦 Amazon FBA在庫 最終取得: {fetched_at}（自動更新は約20時間おき。Amazon推奨により頻繁な手動更新は控えめに）")
        else:
            st.caption("📦 Amazon FBA在庫：未取得")
        if errors:
            st.caption(f"⚠️ 一部取得エラー: {errors[0]}")
    with c2:
        if st.button("🔄 Amazon在庫を更新", key=f"amazon_refresh_{key}", disabled=fetching, use_container_width=True):
            st.session_state["amazon_force_refresh"] = True
            st.rerun()


# ==========================
# HTML テーブル生成（商品コードクリック対応）
# ==========================
def make_html_table(df: pd.DataFrame) -> str:
    thead = "<thead><tr>" + "".join(
        f"<th>{html.escape(str(c))}</th>" for c in df.columns
    ) + "</tr></thead>"

    body_rows = []
    for _, row in df.iterrows():
        tds = []
        for col in df.columns:
            val = row[col]

            if col == "商品コード":
                code = html.escape(str(val))
                # ★同じタブで開く（新規タブにならないように）
                link = (
                    f"<a href='?sku={code}' target='_self' "
                    f"style='color:#0073e6; text-decoration:none;'>{code}</a>"
                )
                tds.append(f"<td>{link}</td>")

            elif col == "画像":
                tds.append(f"<td>{val}</td>")

            # ★HTMLをそのまま表示する列（ここに「現在庫」も追加）
            elif col in ["発注推奨数", "指定日売上個数(昨年売上個数)", "現在庫"]:
                tds.append(f"<td>{val}</td>")

            else:
                tds.append(f"<td>{html.escape(str(val))}</td>")

        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    return f"""
    <table class="sku-table">
      {thead}
      <tbody>{"".join(body_rows)}</tbody>
    </table>
    """


# ==========================
# オーバーレイ（右ドロワー）表示：matplotlib→PNG→HTML埋め込み
# ==========================
@st.dialog("📈 在庫推移")
def _render_stock_dialog(selected_sku: str, df_main: pd.DataFrame):
    """
    st.dialog（Streamlit標準のモーダルポップアップ機能）で在庫推移グラフを表示する。
    自作のposition:fixedなHTML/JSに頼らないため、フリーズなどの不具合が起きない。
    標準機能により、✕ボタン・画面外クリック・ESCキーのいずれでも安全に閉じられる。
    """
    st.markdown(f"**SKU: {selected_sku}**")

    if "変動後" not in df_main.columns:
        st.caption("『変動後』列がないため在庫推移グラフを表示できません。")
    else:
        df_sku = df_main[df_main["商品コード"] == selected_sku].copy()
        df_sku["日付"] = df_sku["元ファイル"].astype(str).str.extract(r"(\d{8})")
        df_sku["日付"] = pd.to_datetime(df_sku["日付"], format="%Y%m%d", errors="coerce")
        df_plot = df_sku[["日付", "変動後"]].dropna().sort_values("日付")

        if df_plot.empty:
            st.caption("選択したSKUの在庫データがありません。")
        else:
            df_plot = df_plot.reset_index(drop=True)

            def _fmt_diff(x):
                if pd.isna(x):
                    return "—"
                x = int(x)
                if x > 0:
                    return f"+{x}"
                if x < 0:
                    return str(x)
                return "±0"

            diff_text = df_plot["変動後"].diff().apply(_fmt_diff)

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_plot["日付"],
                y=df_plot["変動後"],
                mode="lines+markers",
                line=dict(color="#4C78A8"),
                marker=dict(size=5),
                customdata=diff_text,
                hovertemplate="%{x|%Y/%m/%d}<br>在庫: %{y}（前回比 %{customdata}）<extra></extra>",
            ))
            fig.update_layout(
                title=f"在庫推移（SKU: {selected_sku}）",
                yaxis_title="在庫",
                height=340,
                margin=dict(l=40, r=20, t=40, b=40),
                hovermode="x unified",
            )
            st.plotly_chart(fig, use_container_width=True)

    if st.button("✕ 閉じる", key=f"close_dialog_{selected_sku}", use_container_width=True):
        st.session_state["drawer_dismissed_sku"] = selected_sku
        st.session_state["selected_sku"] = None
        st.rerun()


def show_stock_drawer(selected_sku: str, df_main: pd.DataFrame):
    _render_stock_dialog(selected_sku, df_main)


def handle_row_selection_for_drawer(event, df_view, sku_col="商品コード"):
    """
    st.dataframe(selection_mode='single-row')の選択結果から、表示すべきSKUを確定する共通ロジック。
    ・行選択が外れたら閉じる
    ・閉じるボタン（backdropクリック含む）で閉じた直後は、同じ行が選択されたままでも再度開かない
    """
    sel = event.selection.get("rows", [])
    if sel:
        clicked_sku = str(df_view.iloc[sel[0]][sku_col]).strip()
        if clicked_sku != st.session_state.get("drawer_dismissed_sku"):
            st.session_state["selected_sku"] = clicked_sku
        else:
            st.session_state["selected_sku"] = None
    else:
        st.session_state["selected_sku"] = None
        st.session_state["drawer_dismissed_sku"] = None


# ==========================
# Main
# ==========================
def inject_scroll_preserver():
    """
    全タブ・全操作共通：rerun（画面全体の再実行）が起きても、
    直前のスクロール位置をできるだけ早いタイミングで復元する。
    ・sessionStorage（ブラウザタブを閉じるまで保持）に現在位置を保存
    ・rerun後、DOMの再描画をMutationObserverで検知し、即座に位置を書き戻す
    ・追加パッケージ不要（st.components.v1.htmlのiframe経由でwindow.parentを操作）
    """
    scroll_js = """
    <script>
    (function() {
      try {
        var win = window.parent;
        var doc = win.document;
        var STORAGE_KEY = "app_scroll_y";

        function getScrollY() {
          return win.scrollY || doc.documentElement.scrollTop || doc.body.scrollTop || 0;
        }
        function setScrollY(y) {
          win.scrollTo(0, y);
          doc.documentElement.scrollTop = y;
          doc.body.scrollTop = y;
        }

        // --- 復元 ---
        var savedY = parseInt(win.sessionStorage.getItem(STORAGE_KEY) || "0", 10);
        if (savedY > 0) {
          var start = Date.now();
          var applied = false;

          var observer = new MutationObserver(function() {
            var maxScroll = Math.max(
              doc.documentElement.scrollHeight,
              doc.body.scrollHeight
            ) - win.innerHeight;
            if (maxScroll >= savedY - 5 || Date.now() - start > 1500) {
              setScrollY(savedY);
              applied = true;
            }
            if (Date.now() - start > 1500) {
              observer.disconnect();
            }
          });
          observer.observe(doc.body, { childList: true, subtree: true });

          // 念のため即時にも一度試す
          setScrollY(savedY);

          // 保険：一定時間後に必ず監視を止める
          setTimeout(function() { observer.disconnect(); }, 1500);
        }

        // --- 保存（ユーザーがスクロールするたびに更新） ---
        var saveTimer = null;
        win.addEventListener("scroll", function() {
          clearTimeout(saveTimer);
          saveTimer = setTimeout(function() {
            win.sessionStorage.setItem(STORAGE_KEY, String(getScrollY()));
          }, 120);
        }, true);
      } catch (e) {
        // クロスオリジン等で失敗しても、アプリ本体には影響させない
      }
    })();
    </script>
    """
    components.html(scroll_js, height=0)


def main():
    st.set_page_config(page_title="Tempostar 売上集計", layout="wide")
    inject_scroll_preserver()
    st.title("Tempostar 在庫変動データ")

    # ---------- CSV 一覧 ----------
    raw_paths = sorted(glob.glob("tempostar_stock_*.csv"))
    if not raw_paths:
        st.error("tempostar_stock_*.csv がありません。")
        return

    file_infos = []
    pat = re.compile(r"tempostar_stock_(\d{8})")

    for path in raw_paths:
        name = os.path.basename(path)
        m = pat.search(name)
        if m:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
            file_infos.append({"date": d, "path": path, "name": name})

    if not file_infos:
        st.error("tempostar_stock_YYYYMMDD.csv 形式のファイルがありません。")
        return

    all_dates = sorted({fi["date"] for fi in file_infos})
    min_date, max_date = min(all_dates), max(all_dates)

    if "selected_sku" not in st.session_state:
        st.session_state["selected_sku"] = None
    if "drawer_dismissed_sku" not in st.session_state:
        st.session_state["drawer_dismissed_sku"] = None

    # ---------- 楽天在庫（RMS 在庫API 2.0・非ブロッキング背景取得） ----------
    # 自動取得は「このブラウザセッションでまだ取得していない時（＝開いた直後やF5直後）」のみ。
    # それ以外は手動更新ボタンを押さない限り、取得済みの値をそのまま使い続ける。
    all_paths_for_rakuten = tuple(sorted(fi["path"] for fi in file_infos))
    manual_refresh = st.session_state.pop("rakuten_force_refresh", False)
    need_session_fetch = not st.session_state.get("rakuten_session_fetched", False)
    st.session_state["rakuten_session_fetched"] = True

    if _rakuten_auth_header() is None:
        rakuten_stock_map, rakuten_errors, rakuten_fetched_at, rakuten_fetching = {}, [], None, False
    else:
        rakuten_pairs = get_rakuten_sku_pairs(all_paths_for_rakuten)
        rakuten_stock_map, rakuten_errors, rakuten_fetched_at, rakuten_fetching = get_rakuten_stock_state(
            rakuten_pairs, force=(manual_refresh or need_session_fetch)
        )

    # ---------- Amazon FBA在庫（SP-API・非ブロッキング背景取得） ----------
    # Amazon推奨（1日1回程度）に沿って、自動更新は約20時間おき。手動更新ボタンはいつでも押せる。
    amazon_manual_refresh = st.session_state.pop("amazon_force_refresh", False)
    if _amazon_credentials() is None:
        diag = _amazon_credentials_diagnosis()
        amazon_stock_map, amazon_errors, amazon_fetched_at, amazon_fetching = (
            {}, [diag] if diag else ["認証情報が読み込めませんでした（原因不明）"], None, False
        )
    else:
        amazon_skus = get_amazon_sku_list(all_paths_for_rakuten)
        if not amazon_skus:
            amazon_stock_map, amazon_errors, amazon_fetched_at, amazon_fetching = (
                {}, ["Tempostarデータに「商品コード」列が無いか、対象SKUが0件のため取得を開始していません。"], None, False
            )
        else:
            amazon_stock_map, amazon_errors, amazon_fetched_at, amazon_fetching = get_amazon_fba_stock_state(
                amazon_skus, force=amazon_manual_refresh
            )


    # ---------- 売上個数予想用：全期間の日別売上マップ（1回だけ計算） ----------
    all_sales_map = get_tempostar_sales_map(all_paths_for_rakuten)

    # ---------- 初期フィルタ（セッション） ----------
    default_forecast_start, default_forecast_end = default_forecast_range()

    default_start = max_date - timedelta(days=30)
    if default_start < min_date:
        default_start = min_date

    # フィルター入力値を session_state のフラットなキーで管理
    # （st.form 内で key= に渡すことで value= の上書き問題を回避）
    if "sku_applied" not in st.session_state:
        st.session_state["sku_applied"] = False
    if "restock_applied" not in st.session_state:
        st.session_state["restock_applied"] = True

    # 売上個数タブ用デフォルト
    if "sku_keyword" not in st.session_state:
        st.session_state["sku_keyword"] = ""
    if "sku_start_date" not in st.session_state:
        st.session_state["sku_start_date"] = default_start
    if "sku_end_date" not in st.session_state:
        st.session_state["sku_end_date"] = max_date
    if "sku_min_sales" not in st.session_state:
        st.session_state["sku_min_sales"] = 0
    if "sku_forecast_start" not in st.session_state:
        st.session_state["sku_forecast_start"] = default_forecast_start
    if "sku_forecast_end" not in st.session_state:
        st.session_state["sku_forecast_end"] = default_forecast_end

    # 発注推奨タブ用デフォルト
    if "rs_keyword" not in st.session_state:
        st.session_state["rs_keyword"] = ""
    if "rs_min_sales" not in st.session_state:
        st.session_state["rs_min_sales"] = 0
    if "rs_months" not in st.session_state:
        st.session_state["rs_months"] = 1
    if "rs_target_days" not in st.session_state:
        st.session_state["rs_target_days"] = 30
    if "rs_max_stock" not in st.session_state:
        st.session_state["rs_max_stock"] = 999999
    if "rs_forecast_start" not in st.session_state:
        st.session_state["rs_forecast_start"] = default_forecast_start
    if "rs_forecast_end" not in st.session_state:
        st.session_state["rs_forecast_end"] = default_forecast_end


    # ==========================
    # CSS
    # ==========================
    st.markdown(
        """
<style>
/* ===== ページ全体 ===== */
[data-testid="stAppViewContainer"] { background: #f7f8fa; }
[data-testid="stHeader"] { background: #ffffff; border-bottom: 1px solid #e0e4ea; }

/* ===== サイドパネル（フィルター列） ===== */
.filter-card {
    background: #ffffff;
    border: 1px solid #e0e4ea;
    border-radius: 12px;
    padding: 20px 18px 24px 18px;
    margin-bottom: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.filter-card h3 {
    margin: 0 0 14px 0;
    font-size: 15px;
    font-weight: 700;
    color: #1a1d23;
    letter-spacing: 0.01em;
}

/* ===== テーブル共通 ===== */
.sku-table {
    border-collapse: collapse;
    font-size: 13px;
    width: 100%;
    background: #ffffff;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 6px rgba(0,0,0,0.07);
}
.sku-table th {
    background: #f0f2f7;
    color: #444;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 10px 10px;
    border-bottom: 2px solid #d8dde8;
    white-space: nowrap;
}
.sku-table td {
    padding: 9px 10px;
    border-bottom: 1px solid #eef0f5;
    vertical-align: middle;
    color: #222;
}
.sku-table tbody tr:hover { background: #f5f7fc; }
.sku-table img { max-height: 64px; width: auto; display: block; margin: auto; border-radius: 4px; }

/* 列幅 */
.sku-table td:nth-child(1), .sku-table th:nth-child(1) { width: 76px; text-align: center; }
.sku-table td:nth-child(2), .sku-table th:nth-child(2),
.sku-table td:nth-child(3), .sku-table th:nth-child(3) { width: 120px; white-space: nowrap; }
.sku-table td:nth-child(4) {
    max-width: 380px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.sku-table td:nth-child(5), .sku-table th:nth-child(5),
.sku-table td:nth-child(6), .sku-table th:nth-child(6) { width: 100px; }
.sku-table td:nth-child(7), .sku-table th:nth-child(7),
.sku-table td:nth-child(8), .sku-table th:nth-child(8),
.sku-table td:nth-child(9), .sku-table th:nth-child(9),
.sku-table td:nth-child(10), .sku-table th:nth-child(10) {
    width: 100px; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
}

/* 数値強調 */
.sku-table td:nth-child(3)  { font-weight: 600; font-size: 13px; color: #1a1d23; }
.sku-table td:nth-child(7),
.sku-table td:nth-child(8)  { font-weight: 700; font-size: 15px; color: #1a1d23; }
.sku-table td:nth-child(9)  { font-weight: 700; font-size: 15px; }

/* 商品コードリンク */
.sku-table a { color: #3b7de9; text-decoration: none; font-weight: 500; }
.sku-table a:hover { text-decoration: underline; }

/* ヘッダー固定 */
.sku-table thead th { position: sticky; top: 0; z-index: 2; }

/* 発注推奨バッジ */
.sku-table td .order-col {
    display: inline-block;
    font-weight: 700;
    background: #fff0ee;
    color: #c0392b;
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid #f5c6c2;
    font-size: 14px;
    min-width: 48px;
    text-align: center;
}

/* 在庫ステータスラベル */
.sku-table .stock-danger { color: #c0392b; font-size: 11px; font-weight: 700; }
.sku-table .stock-warn   { color: #d35400; font-size: 11px; font-weight: 700; }

/* ===== タブ ===== */
[data-testid="stTab"] p {
    font-size: 14px !important;
    font-weight: 600 !important;
}
[data-testid="stTab"] {
    padding: 8px 20px !important;
}

/* Streamlit標準ヘッダーを非表示にして、タブの固定表示と被らないようにする */
[data-testid="stHeader"] {
    display: none;
}
[data-testid="stAppViewContainer"] > .main {
    padding-top: 1rem;
}

/* タブの見出し部分を画面上部に完全固定 */
[data-testid="stTabs"] [role="tablist"] {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 9999;
    background: #ffffff;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08);
    padding: 6px 3rem;
}
/* fixedにした分、タブの中身が隠れないよう本来あった位置に空白を確保 */
[data-testid="stTabs"] {
    margin-top: 56px;
}

/* ===== メトリクスバー ===== */
.metric-bar {
    display: flex;
    gap: 12px;
    margin-bottom: 14px;
    flex-wrap: wrap;
}
.metric-chip {
    background: #ffffff;
    border: 1px solid #e0e4ea;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    color: #444;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.metric-chip strong { color: #1a1d23; font-size: 16px; margin-left: 4px; }
</style>
""",
        unsafe_allow_html=True,
    )

        # ==========================
    # タブ（タブ名と中身を一致させる）
    # ==========================
    def render_restock_tab(file_infos, min_date, max_date, rakuten_stock_map, rakuten_fetching, rakuten_errors, rakuten_fetched_at, all_sales_map, amazon_stock_map, amazon_fetching, amazon_errors, amazon_fetched_at):
        # --- 発注推奨一覧タブ ---
        left, right = st.columns([1, 3])

        with left:
            st.markdown('<div class="filter-card"><h3>🔍 絞り込み条件</h3>', unsafe_allow_html=True)
            st.caption(f"データ最終日：{max_date}")

            with st.form("restock_form"):
                st.text_input(
                    "キーワード（商品コード / 商品基本コード / 商品名）",
                    key="rs_keyword",
                )
                st.number_input(
                    "売上個数（この数以上）",
                    min_value=0,
                    key="rs_min_sales",
                )

                months_choices = [1, 2, 3, 4, 5, 6]
                cur_months = st.session_state["rs_months"]
                if cur_months not in months_choices:
                    cur_months = 1
                st.selectbox(
                    "集計期間（直近◯ヶ月）",
                    months_choices,
                    index=months_choices.index(cur_months),
                    key="rs_months",
                )

                st.number_input(
                    "確保したい在庫日数",
                    min_value=1,
                    max_value=365,
                    key="rs_target_days",
                )

                st.number_input(
                    "現在庫フィルター（この数以下）",
                    min_value=0,
                    max_value=999999,
                    key="rs_max_stock",
                )

                st.markdown("**売上個数予想の集計期間**（デフォルト：去年翌日から1ヶ月）")
                fc1, fc2 = st.columns(2)
                with fc1:
                    st.date_input("開始日", key="rs_forecast_start")
                with fc2:
                    st.date_input("終了日", key="rs_forecast_end")

                submit_restock = st.form_submit_button("🔎 この条件で表示", use_container_width=True)

            st.markdown('</div>', unsafe_allow_html=True)

            render_rakuten_refresh_control(
                rakuten_fetched_at, rakuten_fetching, rakuten_errors, key="restock"
            )
            render_amazon_refresh_control(
                amazon_fetched_at, amazon_fetching, amazon_errors, key="restock"
            )

            if submit_restock:
                st.session_state["restock_applied"] = True

        with right:
            if not st.session_state["restock_applied"]:
                st.info("左側で条件を設定して『この条件で表示』を押してください。")
            else:
                keyword_r       = st.session_state["rs_keyword"]
                min_total_sales_r = int(st.session_state["rs_min_sales"])
                restock_months  = int(st.session_state["rs_months"])
                target_days     = int(st.session_state["rs_target_days"])
                max_current_stock = int(st.session_state["rs_max_stock"])
                forecast_start_r = st.session_state["rs_forecast_start"]
                forecast_end_r   = st.session_state["rs_forecast_end"]
                forecast_map_r   = compute_forecast_map(all_sales_map, forecast_start_r, forecast_end_r)

                end_r = max_date
                start_r = (pd.Timestamp(max_date) - pd.DateOffset(months=restock_months)).date()
                if start_r < min_date:
                    start_r = min_date

                restock_files = [fi for fi in file_infos if start_r <= fi["date"] <= end_r]
                if not restock_files:
                    st.warning(f"直近{restock_months}ヶ月（{start_r} ～ {end_r}）にCSVがありません。")
                else:
                    restock_paths = [fi["path"] for fi in restock_files]
                    df_restock = load_tempostar_data(restock_paths)

                    if keyword_r:
                        cond_r = False
                        for col in ["商品コード", "商品基本コード", "商品名"]:
                            if col in df_restock.columns:
                                cond_r |= df_restock[col].astype(str).str.contains(keyword_r, case=False, na=False)
                        df_restock = df_restock[cond_r]

                    if "更新理由" in df_restock.columns:
                        df_sales_recent = df_restock[df_restock["更新理由"] == "受注取込"].copy()
                    else:
                        df_sales_recent = df_restock.copy()

                    if df_sales_recent.empty:
                        st.warning(f"直近{restock_months}ヶ月（{start_r} ～ {end_r}）に売上データがありません。")
                    else:
                        agg_sales = {
                            "商品基本コード": "last",
                            "商品名": "last",
                            "属性1名": "last",
                            "属性2名": "last",
                            "増減値": "sum",
                        }

                        sales_recent = (
                            df_sales_recent.groupby("商品コード", dropna=False)
                            .agg(agg_sales)
                            .reset_index()
                            .rename(columns={"増減値": "増減値合計"})
                        )
                        sales_recent["売上個数合計"] = -sales_recent["増減値合計"]
                        sales_recent = sales_recent[sales_recent["売上個数合計"] > 0]

                        if min_total_sales_r > 0:
                            sales_recent = sales_recent[sales_recent["売上個数合計"] >= min_total_sales_r]

                        if "変動後" in df_restock.columns:
                            stock_group_r = (
                                df_restock.groupby("商品コード", dropna=False)["変動後"]
                                .last()
                                .reset_index()
                                .rename(columns={"変動後": "現在庫"})
                            )
                            stock_group_r["現在庫"] = (
                                pd.to_numeric(stock_group_r["現在庫"], errors="coerce")
                                .fillna(0)
                                .astype(int)
                            )
                            sales_recent = sales_recent.merge(stock_group_r, on="商品コード", how="left")
                        else:
                            sales_recent["現在庫"] = 0

                        sales_recent["現在庫"] = (
                            pd.to_numeric(sales_recent["現在庫"], errors="coerce")
                            .fillna(0)
                            .astype(int)
                        )

                        sales_recent = sales_recent[sales_recent["現在庫"] <= max_current_stock]

                        # 楽天在庫（RMS 在庫API 2.0・リアルタイム取得）
                        sales_recent["楽天在庫"] = (
                            sales_recent["商品コード"].astype(str).str.strip().map(rakuten_stock_map)
                        )

                        # Amazon FBA在庫（出荷可能数量・SP-APIより取得）
                        sales_recent["Amazon FBA在庫"] = (
                            sales_recent["商品コード"].astype(str).str.strip()
                            .map(lambda k: amazon_stock_map.get(k, {}).get("fulfillable"))
                        )

                        # 売上個数予想（去年翌日を起点にした期間集計）
                        sales_recent["売上個数予想"] = (
                            sales_recent["商品コード"].astype(str).str.strip().map(forecast_map_r).fillna(0).astype(int)
                        )

                        img_master = load_image_master()
                        base_url = "https://image.rakuten.co.jp/hype/cabinet"

                        def to_img_url(code):
                            key = str(code).strip()
                            rel = img_master.get(key, "")
                            return (base_url + rel) if rel else ""

                        sales_recent["画像"] = sales_recent["商品基本コード"].apply(to_img_url)

                        # 発注推奨数計算
                        period_days = max((end_r - start_r).days + 1, 1)
                        one_day_avg = sales_recent["売上個数合計"] / period_days
                        target_stock = one_day_avg * target_days
                        target_qty = pd.to_numeric(target_stock, errors="coerce")
                        current_stock = pd.to_numeric(sales_recent["現在庫"], errors="coerce")
                        diff = (target_qty - current_stock).fillna(0)
                        sales_recent["発注推奨数"] = diff.where(diff > 0, 0).round().astype(int)

                        restock_view = sales_recent[sales_recent["発注推奨数"] > 0].copy()
                        restock_view = restock_view.sort_values("発注推奨数", ascending=False)

                        st.info(f"発注目安：直近 {restock_months} ヶ月（{start_r} ～ {end_r}）の売上から計算 ｜ 目標在庫 {target_days} 日分")

                        if restock_view.empty:
                            st.success("✅ 発注推奨の商品はありません。")
                        else:
                            display_cols = [
                                "画像", "商品コード", "商品基本コード", "商品名",
                                "属性1名", "属性2名", "売上個数合計", "売上個数予想", "現在庫", "楽天在庫", "Amazon FBA在庫", "発注推奨数",
                            ]
                            display_cols = [c for c in display_cols if c in restock_view.columns]
                            df_view_r = restock_view[display_cols].copy()

                            # 在庫ステータス列を追加
                            stock_num = pd.to_numeric(df_view_r["現在庫"], errors="coerce").fillna(0).astype(int)
                            sales_num = pd.to_numeric(df_view_r["売上個数合計"], errors="coerce").fillna(0).astype(int)
                            def _status(s, v):
                                if s <= 0: return "🔴 在庫切れ"
                                if s <= 10 or s < v: return "🟡 在庫少"
                                return ""
                            df_view_r.insert(
                                df_view_r.columns.tolist().index("楽天在庫") + 1,
                                "状態",
                                [_status(s, v) for s, v in zip(stock_num, sales_num)]
                            )

                            st.markdown(
                                f'<div class="metric-bar">'
                                f'<div class="metric-chip">抽出SKU数<strong>{len(df_view_r):,}</strong></div>'
                                f'<div class="metric-chip">集計期間<strong>{start_r} ～ {end_r}</strong></div>'
                                f'<div class="metric-chip">売上個数予想の期間<strong>{forecast_start_r} ～ {forecast_end_r}</strong></div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

                            col_cfg = {}
                            if "画像" in df_view_r.columns:
                                col_cfg["画像"] = st.column_config.ImageColumn("画像", width="small")
                            if "商品名" in df_view_r.columns:
                                col_cfg["商品名"] = st.column_config.TextColumn("商品名", width="medium")
                            if "楽天在庫" in df_view_r.columns:
                                col_cfg["楽天在庫"] = st.column_config.NumberColumn("楽天在庫", format="%d")
                            if "Amazon FBA在庫" in df_view_r.columns:
                                col_cfg["Amazon FBA在庫"] = st.column_config.NumberColumn("Amazon FBA在庫", format="%d")
                            if "売上個数予想" in df_view_r.columns:
                                col_cfg["売上個数予想"] = st.column_config.NumberColumn("売上個数予想", format="%d")
                            if not rakuten_stock_map and not rakuten_fetching:
                                st.caption("ℹ️ 楽天在庫が空欄の場合は、`.streamlit/secrets.toml` に楽天APIの認証情報が未設定か、取得エラーが発生しています。")
                            if not amazon_stock_map and not amazon_fetching:
                                st.caption("ℹ️ Amazon FBA在庫が空欄の場合は、`.streamlit/secrets.toml` にAmazon APIの認証情報が未設定か、取得エラーが発生しています。")

                            event_r = st.dataframe(
                                df_view_r,
                                hide_index=True,
                                use_container_width=True,
                                selection_mode="single-row",
                                on_select="rerun",
                                column_config=col_cfg if col_cfg else None,
                            )

                            # 行クリックでSKU取得 → ドロワー表示
                            handle_row_selection_for_drawer(event_r, df_view_r)
                            if st.session_state["selected_sku"]:
                                show_stock_drawer(st.session_state["selected_sku"], df_restock)

    def render_sales_tab(file_infos, min_date, max_date, rakuten_stock_map, rakuten_fetching, rakuten_errors, rakuten_fetched_at, all_sales_map, amazon_stock_map, amazon_fetching, amazon_errors, amazon_fetched_at):
        # --- 売上個数一覧タブ ---
        left, right = st.columns([1, 3])

        with left:
            st.markdown('<div class="filter-card"><h3>🔍 絞り込み条件</h3>', unsafe_allow_html=True)
            st.caption(f"データ期間：{min_date} ～ {max_date}")

            with st.form("sku_form"):
                st.date_input(
                    "開始日",
                    key="sku_start_date",
                    min_value=min_date,
                    max_value=max_date,
                )
                st.date_input(
                    "終了日",
                    key="sku_end_date",
                    min_value=min_date,
                    max_value=max_date,
                )
                st.text_input(
                    "キーワード（商品コード / 商品基本コード / 商品名）",
                    key="sku_keyword",
                )
                st.number_input(
                    "売上個数（この数以上）",
                    min_value=0,
                    key="sku_min_sales",
                )

                st.markdown("**売上個数予想の集計期間**（デフォルト：去年翌日から1ヶ月）")
                fc1, fc2 = st.columns(2)
                with fc1:
                    st.date_input("予想 開始日", key="sku_forecast_start")
                with fc2:
                    st.date_input("予想 終了日", key="sku_forecast_end")

                submit_sku = st.form_submit_button("🔎 この条件で表示", use_container_width=True)

            st.markdown('</div>', unsafe_allow_html=True)

            render_rakuten_refresh_control(
                rakuten_fetched_at, rakuten_fetching, rakuten_errors, key="sales"
            )
            render_amazon_refresh_control(
                amazon_fetched_at, amazon_fetching, amazon_errors, key="sales"
            )

            if submit_sku:
                # 開始・終了日の順序を自動補正
                if st.session_state["sku_start_date"] > st.session_state["sku_end_date"]:
                    st.session_state["sku_start_date"], st.session_state["sku_end_date"] = (
                        st.session_state["sku_end_date"], st.session_state["sku_start_date"]
                    )
                st.session_state["sku_applied"] = True

        with right:
            if not st.session_state["sku_applied"]:
                st.info("左側で条件を設定して『この条件で表示』を押してください。")
            else:
                start_date     = st.session_state["sku_start_date"]
                end_date       = st.session_state["sku_end_date"]
                keyword        = st.session_state["sku_keyword"]
                min_total_sales = int(st.session_state["sku_min_sales"])
                forecast_start_s = st.session_state["sku_forecast_start"]
                forecast_end_s   = st.session_state["sku_forecast_end"]
                forecast_map_s   = compute_forecast_map(all_sales_map, forecast_start_s, forecast_end_s)

                # 今年ファイル
                main_files = [fi for fi in file_infos if start_date <= fi["date"] <= end_date]
                if not main_files:
                    st.error("選択範囲のCSVがありません。")
                    return

                main_paths = [fi["path"] for fi in main_files]
                df_main = load_tempostar_data(main_paths)

                # SKU正規化
                if "商品コード" in df_main.columns:
                    df_main["商品コード"] = df_main["商品コード"].astype(str).str.strip()

                # 昨年同期間ファイル
                last_start = (pd.Timestamp(start_date) - DateOffset(years=1)).date()
                last_end = (pd.Timestamp(end_date) - DateOffset(years=1)).date()
                last_files = [fi for fi in file_infos if last_start <= fi["date"] <= last_end]

                df_last = None
                if last_files:
                    last_paths = [fi["path"] for fi in last_files]
                    df_last = load_tempostar_data(last_paths)
                    if "商品コード" in df_last.columns:
                        df_last["商品コード"] = df_last["商品コード"].astype(str).str.strip()

                # ---- デバッグ表示 ----
                st.caption(f"集計期間：{start_date} ～ {end_date} ｜ 昨年同期間：{last_start} ～ {last_end}")
                st.caption(f"今年CSV件数：{len(main_files)} ｜ 昨年CSV件数：{len(last_files)}")
                if len(last_files) == 0:
                    st.warning("昨年同期間のCSVが見つかりません。tempostar_stock_YYYYMMDD.csv の昨年分も同じフォルダに必要です。")

                # キーワード絞り込み（今年）
                if keyword:
                    cond = False
                    for col in ["商品コード", "商品基本コード", "商品名"]:
                        if col in df_main.columns:
                            cond |= df_main[col].astype(str).str.contains(keyword, case=False, na=False)
                    df_main = df_main[cond]

                # キーワード絞り込み（昨年）
                if df_last is not None and keyword:
                    cond_last = False
                    for col in ["商品コード", "商品基本コード", "商品名"]:
                        if col in df_last.columns:
                            cond_last |= df_last[col].astype(str).str.contains(keyword, case=False, na=False)
                    df_last = df_last[cond_last]

                required = {"商品コード", "商品基本コード", "増減値"}
                if not required.issubset(df_main.columns):
                    st.error("Tempostar CSV に『商品コード』『商品基本コード』『増減値』が必要です。")
                    return

                # --- 全SKUのベース（売上が0件のSKUや、期間内に動きが無い商品も含める）---
                # 集計期間内のdf_mainではなく、常に全期間の最新スナップショットを使うことで、
                # 「その期間は全く動きが無かった商品」も現在庫つきで表示できるようにする。
                all_paths_for_snapshot = [fi["path"] for fi in file_infos]
                sales_grouped = get_all_sku_snapshot(all_paths_for_snapshot).copy()

                if sales_grouped.empty:
                    st.error("Tempostar CSV から商品情報を読み取れませんでした。")
                    return

                # キーワード絞り込み（全SKUベース側にも適用）
                if keyword:
                    cond_base = False
                    for col in ["商品コード", "商品基本コード", "商品名"]:
                        if col in sales_grouped.columns:
                            cond_base |= sales_grouped[col].astype(str).str.contains(keyword, case=False, na=False)
                    sales_grouped = sales_grouped[cond_base]

                # --- 売上集計（今年）---
                if "更新理由" in df_main.columns:
                    df_sales_main = df_main[df_main["更新理由"].astype(str).str.contains("受注取込", na=False)].copy()
                else:
                    df_sales_main = df_main.copy()

                sales_sum_main = (
                    df_sales_main.groupby("商品コード", dropna=False)["増減値"]
                    .sum()
                    .reset_index()
                    .rename(columns={"増減値": "増減値合計"})
                )
                sales_grouped = sales_grouped.merge(sales_sum_main, on="商品コード", how="left")
                sales_grouped["増減値合計"] = sales_grouped["増減値合計"].fillna(0)
                sales_grouped["売上個数合計"] = (-sales_grouped["増減値合計"]).astype(int)

                # --- 売上集計（昨年）---
                if df_last is not None and {"商品コード", "増減値"}.issubset(df_last.columns):
                    if "更新理由" in df_last.columns:
                        df_sales_last = df_last[
                            df_last["更新理由"].astype(str).str.contains("受注取込", na=False)
                        ].copy()
                    else:
                        df_sales_last = df_last.copy()

                    df_sales_last["商品コード"] = df_sales_last["商品コード"].astype(str).str.strip()

                    last_grouped = (
                        df_sales_last.groupby("商品コード", dropna=False)["増減値"]
                        .sum()
                        .reset_index()
                    )
                    last_grouped["昨年売上個数"] = -last_grouped["増減値"]
                    last_grouped = last_grouped.drop(columns=["増減値"])

                    sales_grouped = sales_grouped.merge(last_grouped, on="商品コード", how="left")

                sales_grouped["昨年売上個数"] = (
                    pd.to_numeric(
                        sales_grouped["昨年売上個数"]
                        if "昨年売上個数" in sales_grouped.columns
                        else pd.Series(0, index=sales_grouped.index),
                        errors="coerce"
                    )
                    .fillna(0)
                    .astype(int)
                )

                sales_grouped["現在庫"] = (
                    pd.to_numeric(sales_grouped["現在庫"], errors="coerce")
                    .fillna(0)
                    .astype(int)
                )

                # 楽天在庫（RMS 在庫API 2.0・リアルタイム取得）
                sales_grouped["楽天在庫"] = (
                    sales_grouped["商品コード"].astype(str).str.strip().map(rakuten_stock_map)
                )

                # Amazon FBA在庫（出荷可能数量・SP-APIより取得）
                sales_grouped["Amazon FBA在庫"] = (
                    sales_grouped["商品コード"].astype(str).str.strip()
                    .map(lambda k: amazon_stock_map.get(k, {}).get("fulfillable"))
                )

                # 売上個数予想（去年翌日を起点にした期間集計）
                sales_grouped["売上個数予想"] = (
                    sales_grouped["商品コード"].astype(str).str.strip().map(forecast_map_s).fillna(0).astype(int)
                )

                if min_total_sales > 0:
                    sales_grouped = sales_grouped[sales_grouped["売上個数合計"] >= min_total_sales]

                sales_grouped = sales_grouped.sort_values("売上個数合計", ascending=False)

                # 画像列（URL形式で直接返す）
                img_master = load_image_master()
                base_url = "https://image.rakuten.co.jp/hype/cabinet"

                def to_img_url_s(code):
                    key = str(code).strip()
                    rel = img_master.get(key, "")
                    return (base_url + rel) if rel else ""

                sales_grouped["画像"] = sales_grouped["商品基本コード"].apply(to_img_url_s)

                # 今年・前年を別列で保持
                sales_grouped["今年売上"] = sales_grouped["売上個数合計"].astype(int)
                sales_grouped["前年売上"] = sales_grouped["昨年売上個数"].astype(int)

                # 不要列を落とす
                sales_grouped = sales_grouped.drop(
                    columns=["売上個数合計", "昨年売上個数", "増減値合計",
                             "指定日売上個数(昨年売上個数)"], errors="ignore"
                )

                display_cols = [
                    "画像", "商品コード", "商品基本コード", "商品名",
                    "属性1名", "属性2名", "今年売上", "前年売上", "売上個数予想", "現在庫", "楽天在庫", "Amazon FBA在庫",
                ]
                display_cols = [c for c in display_cols if c in sales_grouped.columns]
                df_view = sales_grouped[display_cols]

                st.markdown(
                    f'<div class="metric-bar">'
                    f'<div class="metric-chip">SKU数<strong>{len(df_view):,}</strong></div>'
                    f'<div class="metric-chip">集計期間<strong>{start_date} ～ {end_date}</strong></div>'
                    f'<div class="metric-chip">売上個数予想の期間<strong>{forecast_start_s} ～ {forecast_end_s}</strong></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                if not rakuten_stock_map and not rakuten_fetching:
                    st.caption("ℹ️ 楽天在庫が空欄の場合は、`.streamlit/secrets.toml` に楽天APIの認証情報が未設定か、取得エラーが発生しています。")
                if not amazon_stock_map and not amazon_fetching:
                    st.caption("ℹ️ Amazon FBA在庫が空欄の場合は、`.streamlit/secrets.toml` にAmazon APIの認証情報が未設定か、取得エラーが発生しています。")

                event = st.dataframe(
                    df_view,
                    hide_index=True,
                    use_container_width=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    column_config={
                        "画像":    st.column_config.ImageColumn("画像", width="small"),
                        "商品名":  st.column_config.TextColumn("商品名", width="medium"),
                        "今年売上": st.column_config.NumberColumn("今年売上", format="%d"),
                        "前年売上": st.column_config.NumberColumn("前年売上", format="%d"),
                        "売上個数予想": st.column_config.NumberColumn("売上個数予想", format="%d"),
                        "現在庫":  st.column_config.NumberColumn("現在庫",   format="%d"),
                        "楽天在庫": st.column_config.NumberColumn("楽天在庫", format="%d"),
                        "Amazon FBA在庫": st.column_config.NumberColumn("Amazon FBA在庫", format="%d"),
                    } if "画像" in df_view.columns else None,
                )

                # 行クリックでSKU取得 → ドロワー表示
                handle_row_selection_for_drawer(event, df_view)

                # 右ドロワー（選択されている時だけ）
                if st.session_state["selected_sku"]:
                    show_stock_drawer(st.session_state["selected_sku"], df_main)

        # --------------------------------------------------
        # タブ2：在庫少商品（発注目安）
        # --------------------------------------------------

    def render_delivery_tab(file_infos, rakuten_stock_map, rakuten_errors, rakuten_fetching, rakuten_fetched_at):
        # --- 納品推奨数システム（HTMLツール埋め込み＋テンポスター在庫連携）---
        html_path = "納品推奨数システムv5.html"
        if not os.path.exists(html_path):
            st.error(
                f"『{html_path}』が見つかりません。app.py と同じフォルダに配置してください。"
            )
            return

        render_rakuten_refresh_control(
            rakuten_fetched_at, rakuten_fetching, rakuten_errors, key="delivery"
        )

        sku_master = load_sku_master()
        all_paths = [fi["path"] for fi in file_infos]
        sales_map = get_tempostar_sales_map(all_paths)
        tempostar_stock_map = get_tempostar_stock_map(all_paths)
        stock_history_map = get_tempostar_stock_history_map(all_paths)

        # 売上個数予想（発注推奨一覧タブと同じ期間設定を流用。デフォルトは去年翌日から1ヶ月）
        forecast_default_start, forecast_default_end = default_forecast_range()
        forecast_start_d = st.session_state.get("rs_forecast_start", forecast_default_start)
        forecast_end_d = st.session_state.get("rs_forecast_end", forecast_default_end)
        forecast_map_d = compute_forecast_map(sales_map, forecast_start_d, forecast_end_d)

        if rakuten_stock_map:
            # SKUごとに楽天在庫を優先し、楽天側にデータがないSKUだけテンポスター在庫で補完する
            stock_map = dict(tempostar_stock_map)
            stock_map.update(rakuten_stock_map)
            fallback_count = len(set(tempostar_stock_map) - set(rakuten_stock_map))
            stock_source_label = "楽天(リアルタイム)"
            if fallback_count > 0:
                st.caption(
                    f"ℹ️ 楽天側に在庫データがない{fallback_count}SKUは、テンポスターの在庫で補完しています。"
                )
        else:
            stock_map = tempostar_stock_map
            stock_source_label = "テンポスター(フォールバック)"
            if rakuten_fetching:
                st.info("📦 楽天在庫を裏で取得中です。取得完了までテンポスターの在庫データで表示しています。")
            elif rakuten_errors:
                st.warning(
                    "楽天在庫の取得に失敗したため、テンポスターの在庫データで表示しています。"
                    f"（エラー: {rakuten_errors[0]}）"
                )
            elif _rakuten_auth_header() is None:
                st.info(
                    "楽天APIの認証情報（`.streamlit/secrets.toml`）が未設定のため、"
                    "テンポスターの在庫データで表示しています。"
                )

        if not sku_master:
            st.warning(
                "『SKUマスター』フォルダにCSV（列名: CS品番, SKU）が見つからないため、"
                "在庫連携なしで表示します。"
            )

        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        injected_script = (
            "<script>"
            f"window.__SKU_MASTER__ = {json.dumps(sku_master, ensure_ascii=False)};"
            f"window.__TEMPOSTAR_STOCK__ = {json.dumps(stock_map, ensure_ascii=False)};"
            f"window.__STOCK_SOURCE_LABEL__ = {json.dumps(stock_source_label, ensure_ascii=False)};"
            f"window.__TEMPOSTAR_SALES__ = {json.dumps(sales_map, ensure_ascii=False)};"
            f"window.__STOCK_HISTORY__ = {json.dumps(stock_history_map, ensure_ascii=False)};"
            f"window.__FORECAST_SALES__ = {json.dumps(forecast_map_d, ensure_ascii=False)};"
            "</script>"
        )
        # 本体スクリプトが動く前に注入データを読み込ませるため、<script>タグの直前に挿入
        html_content = html_content.replace("<script>", injected_script + "<script>", 1)

        st.caption(
            f"在庫データソース：{stock_source_label} ｜ SKUマスター {len(sku_master)}件 ｜ "
            f"在庫データ {len(stock_map)}SKU分 ｜ "
            f"売上データ {len(sales_map)}SKU分（期間は画面上部で指定可） ｜ "
            f"売上個数予想：{forecast_start_d} ～ {forecast_end_d}"
        )
        # iframe自体のスクロールバーは出さず、アプリ全体のスクロールと
        # テーブル内（商品行）のスクロールの2つだけになるようにする
        components.html(html_content, height=1700, scrolling=False)

    def render_stock_check_tab():
        # --- 在庫下げチェックタブ ---
        # 納品書CSV（複数可）と変動ログCSV（複数可）をアップロードし、
        # 選択したユーザーの在庫操作で、納品数どおりに在庫が減っているかを確認する。
        st.caption(
            "納品書CSVと変動ログCSVをアップロードすると、SKUマスターでCS品番⇔SKUを紐付けたうえで、"
            "選択したユーザーの在庫操作が納品数どおりに反映されているかをチェックします。"
            "（納品日と在庫操作日が異なる前提のため、日付は比較に使いません）"
        )

        up_col1, up_col2 = st.columns(2)
        with up_col1:
            delivery_uploads = st.file_uploader(
                "① 納品書CSV（複数選択可）",
                type="csv",
                accept_multiple_files=True,
                key="stockcheck_delivery_files",
            )
        with up_col2:
            log_uploads = st.file_uploader(
                "② 変動ログCSV（複数選択可）",
                type="csv",
                accept_multiple_files=True,
                key="stockcheck_log_files",
            )

        if not delivery_uploads or not log_uploads:
            st.info("納品書CSVと変動ログCSVの両方を1ファイル以上アップロードしてください。")
            return

        # ---------- 読み込み ----------
        delivery_dfs, delivery_fail = [], []
        for f in delivery_uploads:
            df_f = read_csv_flexible(f)
            if df_f is None:
                delivery_fail.append(f.name)
            else:
                delivery_dfs.append(df_f)

        log_dfs, log_fail = [], []
        for f in log_uploads:
            df_f = read_csv_flexible(f)
            if df_f is None:
                log_fail.append(f.name)
            else:
                log_dfs.append(df_f)

        if delivery_fail:
            st.error(f"納品書CSVの読み込みに失敗しました（文字コード不明）：{', '.join(delivery_fail)}")
        if log_fail:
            st.error(f"変動ログCSVの読み込みに失敗しました（文字コード不明）：{', '.join(log_fail)}")
        if not delivery_dfs or not log_dfs:
            return

        df_delivery = pd.concat(delivery_dfs, ignore_index=True)
        df_log = pd.concat(log_dfs, ignore_index=True)

        required_delivery_cols = {"CS品番", "納品数"}
        required_log_cols = {"商品コード", "増減値", "ユーザー"}
        if not required_delivery_cols.issubset(df_delivery.columns):
            st.error(
                f"納品書CSVに必要な列（{', '.join(required_delivery_cols)}）が見つかりません。"
                f"検出された列：{', '.join(df_delivery.columns)}"
            )
            return
        if not required_log_cols.issubset(df_log.columns):
            st.error(
                f"変動ログCSVに必要な列（{', '.join(required_log_cols)}）が見つかりません。"
                f"検出された列：{', '.join(df_log.columns)}"
            )
            return

        # ---------- SKUマスターでCS品番→SKUを変換 ----------
        sku_master = load_sku_master()
        if not sku_master:
            st.warning(
                "『SKUマスター』フォルダにCSV（列名: CS品番, SKU）が見つからないため、"
                "在庫下げチェックを実行できません。"
            )
            return
        cs_to_sku = {m["cs_no"]: m["sku"] for m in sku_master}

        df_delivery = df_delivery.copy()
        df_delivery["CS品番"] = df_delivery["CS品番"].astype(str).str.strip()
        df_delivery["納品数"] = pd.to_numeric(df_delivery["納品数"], errors="coerce").fillna(0).astype(int)
        df_delivery["SKU"] = df_delivery["CS品番"].map(cs_to_sku)

        unmapped = (
            df_delivery[df_delivery["SKU"].isna()][["CS品番", "商品名"]]
            .drop_duplicates()
            if "商品名" in df_delivery.columns
            else df_delivery[df_delivery["SKU"].isna()][["CS品番"]].drop_duplicates()
        )
        df_delivery_mapped = df_delivery.dropna(subset=["SKU"]).copy()

        if df_delivery_mapped.empty:
            st.warning("SKUマスターに一致するCS品番が1件もありませんでした。")
            return

        # ---------- 納品数をSKUごとに合算 ----------
        agg_dict = {"納品数": "sum"}
        if "商品名" in df_delivery_mapped.columns:
            agg_dict["商品名"] = "last"
        if "納品書番号" in df_delivery_mapped.columns:
            agg_dict["納品書番号"] = lambda s: "、".join(sorted(set(s.astype(str))))

        delivered = df_delivery_mapped.groupby("SKU", dropna=False).agg(agg_dict).reset_index()
        delivered = delivered.rename(columns={"納品数": "納品数合計"})

        # CS品番一覧（同じSKUに複数CS品番が紐づくケースの確認用）
        cs_list = (
            df_delivery_mapped.groupby("SKU")["CS品番"]
            .apply(lambda s: "、".join(sorted(set(s))))
            .reset_index()
            .rename(columns={"CS品番": "CS品番"})
        )
        delivered = delivered.merge(cs_list, on="SKU", how="left")

        # ---------- ユーザー選択（チェックボックス・複数選択可） ----------
        df_log = df_log.copy()
        df_log["ユーザー"] = df_log["ユーザー"].astype(str).str.strip()
        df_log.loc[df_log["ユーザー"].isin(["nan", "None", ""]), "ユーザー"] = None
        users = sorted(df_log["ユーザー"].dropna().unique().tolist())

        if not users:
            st.warning("変動ログCSVに『ユーザー』が入っている行がありません（手動操作の記録がありません）。")
            return

        st.markdown("**③ チェック対象にするユーザー（在庫を下げた人）を選択**")
        btn_col1, btn_col2, _ = st.columns([1, 1, 6])
        if btn_col1.button("全選択", key="stockcheck_select_all"):
            for u in users:
                st.session_state[f"stockcheck_user_{u}"] = True
        if btn_col2.button("全解除", key="stockcheck_select_none"):
            for u in users:
                st.session_state[f"stockcheck_user_{u}"] = False

        checkbox_cols = st.columns(4)
        selected_users = []
        for i, u in enumerate(users):
            with checkbox_cols[i % 4]:
                checked = st.checkbox(u, value=st.session_state.get(f"stockcheck_user_{u}", False), key=f"stockcheck_user_{u}")
            if checked:
                selected_users.append(u)

        if not selected_users:
            st.info("ユーザーを1人以上選択してください。")
            return

        # ---------- 選択ユーザー分の増減値をSKUごとに合算 ----------
        df_log["商品コード"] = df_log["商品コード"].astype(str).str.strip()
        df_log["増減値"] = pd.to_numeric(df_log["増減値"], errors="coerce").fillna(0).astype(int)
        df_log_f = df_log[df_log["ユーザー"].isin(selected_users)]

        log_sum = (
            df_log_f.groupby("商品コード")["増減値"]
            .sum()
            .reset_index()
            .rename(columns={"商品コード": "SKU", "増減値": "増減値合計"})
        )
        log_sum["減少数"] = -log_sum["増減値合計"]

        # ---------- 突合 ----------
        result = delivered.merge(log_sum[["SKU", "減少数"]], on="SKU", how="left")
        result["減少数"] = result["減少数"].fillna(0).astype(int)
        result["差分（納品数−減少数）"] = result["納品数合計"] - result["減少数"]
        result["正常"] = result["差分（納品数−減少数）"] == 0

        normal_count = int(result["正常"].sum())
        abnormal_view = result[~result["正常"]].copy().sort_values(
            "差分（納品数−減少数）", key=lambda s: s.abs(), ascending=False
        )

        # ---------- サマリー ----------
        st.markdown(
            f'<div class="metric-bar">'
            f'<div class="metric-chip">対象SKU数<strong>{len(result):,}</strong></div>'
            f'<div class="metric-chip">正常<strong>{normal_count:,}</strong></div>'
            f'<div class="metric-chip">異常<strong>{len(abnormal_view):,}</strong></div>'
            f'<div class="metric-chip">SKUマスター未登録CS品番<strong>{len(unmapped):,}</strong></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        display_cols = ["SKU", "CS品番"]
        if "商品名" in abnormal_view.columns:
            display_cols.append("商品名")
        display_cols += ["納品数合計", "減少数", "差分（納品数−減少数）"]
        if "納品書番号" in abnormal_view.columns:
            display_cols.append("納品書番号")
        display_cols = [c for c in display_cols if c in abnormal_view.columns]

        st.markdown("#### ⚠️ 在庫が正常に下がっていないSKU")
        if abnormal_view.empty:
            st.success("✅ 選択したユーザーの操作で、対象SKUはすべて納品数どおりに在庫が減少しています。")
        else:
            st.dataframe(
                abnormal_view[display_cols],
                hide_index=True,
                use_container_width=True,
            )

            st.markdown("##### SKUごとの変動ログ詳細")
            st.caption("参考として、選択ユーザーに関わらずそのSKUの変動ログを全件表示しています（受注取込による自動減少も含む）。")

            log_detail_cols = [c for c in ["更新日時", "更新理由", "変動前", "変動後", "増減値", "ユーザー"] if c in df_log.columns]

            for _, row in abnormal_view.iterrows():
                sku_i = row["SKU"]
                label = f"{sku_i}"
                if "商品名" in row and pd.notna(row.get("商品名")):
                    label += f"｜{row['商品名']}"
                label += f"｜納品数{row['納品数合計']} / 減少数{row['減少数']} / 差分{row['差分（納品数−減少数）']}"

                with st.expander(label):
                    log_rows = df_log[df_log["商品コード"] == sku_i].copy()
                    if "更新日時" in log_rows.columns:
                        log_rows = log_rows.sort_values("更新日時")
                    if log_rows.empty:
                        st.caption("このSKUに該当する変動ログはありません。")
                    else:
                        st.dataframe(
                            log_rows[log_detail_cols],
                            hide_index=True,
                            use_container_width=True,
                        )

        if not unmapped.empty:
            with st.expander(f"SKUマスターに一致するCS品番が見つからなかった行（{len(unmapped)}件・チェック対象外）"):
                st.dataframe(unmapped, hide_index=True, use_container_width=True)

        with st.expander(f"正常に減少していたSKU一覧（{normal_count}件）"):
            normal_view = result[result["正常"]][display_cols] if not result.empty else result
            st.dataframe(normal_view, hide_index=True, use_container_width=True)

    def make_zozo_html_table(df: pd.DataFrame) -> str:
        """ZOZO在庫チェック用のHTMLテーブル（既存のsku-tableスタイルを流用）。"""
        stock_class_map = {"在庫なし": "stock-danger", "残り": "stock-warn"}

        def stock_cell(v):
            v = str(v)
            if v == "在庫なし":
                return f'<span class="stock-danger">{html.escape(v)}</span>'
            if v.startswith("残り"):
                return f'<span class="stock-warn">{html.escape(v)}</span>'
            return html.escape(v)

        thead = "<thead><tr>" + "".join(
            f"<th>{html.escape(str(c))}</th>" for c in df.columns if c != "URL"
        ) + "</tr></thead>"

        body_rows = []
        for _, row in df.iterrows():
            tds = []
            for col in df.columns:
                if col == "URL":
                    continue
                if col == "商品名":
                    url = row.get("URL", "")
                    label = html.escape(str(row[col]))
                    if url:
                        tds.append(
                            f"<td><a href='{html.escape(str(url))}' target='_blank' "
                            f"style='color:#0073e6; text-decoration:none;'>{label}</a></td>"
                        )
                    else:
                        tds.append(f"<td>{label}</td>")
                elif col == "在庫状況":
                    tds.append(f"<td>{stock_cell(row[col])}</td>")
                else:
                    tds.append(f"<td>{html.escape(str(row[col]))}</td>")
            body_rows.append("<tr>" + "".join(tds) + "</tr>")

        return f"""
        <table class="sku-table">
          {thead}
          <tbody>{"".join(body_rows)}</tbody>
        </table>
        """

    def render_zozo_tab():
        st.markdown(
            "Excelマクロ（ZozoStockChecker）で取得したZOZOTOWN「perky room」のCSVを読み込んで、"
            "カラー×サイズごとの在庫状況（在庫あり／残りN点／在庫なし）を確認するための画面です。"
        )
        st.caption(
            "先にExcel側で「① 在庫取得実行」→「② CSV出力」を実行し、出力されたCSVをここにドラッグ&ドロップしてください。"
        )

        uploaded = st.file_uploader(
            "ZOZO在庫CSVをアップロード",
            type="csv",
            key="zozo_csv_uploader",
        )
        if not uploaded:
            return

        df = read_csv_flexible(uploaded)
        if df is None:
            st.error("CSVの読み込みに失敗しました（文字コード不明）。Excelマクロが出力したCSVをそのまま使ってください。")
            return

        required_cols = {"ブランド", "商品名", "ZOZO品番", "店舗品番", "カラー", "サイズ", "在庫状況", "URL"}
        if not required_cols.issubset(df.columns):
            st.error(
                f"必要な列（{', '.join(required_cols)}）が見つかりません。"
                f"検出された列：{', '.join(df.columns)}"
            )
            return

        for col in required_cols:
            df[col] = df[col].fillna("").astype(str)

        fetched_at = ""
        if "取得日時" in df.columns and len(df) > 0:
            fetched_at = str(df["取得日時"].iloc[0])
        if fetched_at:
            st.caption(f"📦 このCSVの取得日時: {fetched_at}（{uploaded.name}）")

        # ---------- サマリー ----------
        total = len(df)
        n_none = int((df["在庫状況"] == "在庫なし").sum())
        n_low = int(df["在庫状況"].astype(str).str.startswith("残り").sum())
        n_ok = total - n_none - n_low
        st.markdown(
            f'<div class="metric-bar">'
            f'<div class="metric-chip">SKU行数<strong>{total:,}</strong></div>'
            f'<div class="metric-chip">在庫あり<strong>{n_ok:,}</strong></div>'
            f'<div class="metric-chip">残りわずか<strong>{n_low:,}</strong></div>'
            f'<div class="metric-chip">在庫なし<strong>{n_none:,}</strong></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ---------- フィルター ----------
        fc1, fc2, fc3 = st.columns([2, 2, 3])
        with fc1:
            stock_filter = st.selectbox(
                "在庫状況で絞り込み",
                ["すべて", "在庫あり", "残りわずか（残りN点）", "在庫なし"],
                key="zozo_stock_filter",
            )
        with fc2:
            brand_options = ["すべて"] + sorted(df["ブランド"].replace("", pd.NA).dropna().unique().tolist())
            brand_filter = st.selectbox("ブランドで絞り込み", brand_options, key="zozo_brand_filter")
        with fc3:
            keyword = st.text_input("商品名・カラーで検索", key="zozo_keyword_filter", placeholder="例：ハット、ブラック")

        view = df.copy()
        if stock_filter == "在庫あり":
            view = view[view["在庫状況"] == "在庫あり"]
        elif stock_filter == "残りわずか（残りN点）":
            view = view[view["在庫状況"].astype(str).str.startswith("残り")]
        elif stock_filter == "在庫なし":
            view = view[view["在庫状況"] == "在庫なし"]

        if brand_filter != "すべて":
            view = view[view["ブランド"] == brand_filter]

        if keyword:
            mask = (
                view["商品名"].astype(str).str.contains(keyword, case=False, na=False)
                | view["カラー"].astype(str).str.contains(keyword, case=False, na=False)
            )
            view = view[mask]

        # 欠品・残りわずかを上に表示（納品数を考える際に見やすいように）
        stock_priority = {"在庫なし": 0}
        view = view.copy()
        view["_並び順"] = view["在庫状況"].apply(
            lambda v: 0 if v == "在庫なし" else (1 if str(v).startswith("残り") else 2)
        )
        view = view.sort_values("_並び順").drop(columns="_並び順")

        st.caption(f"表示中: {len(view):,} / {total:,} 行")
        st.markdown(
            make_zozo_html_table(
                view[["ブランド", "商品名", "ZOZO品番", "店舗品番", "カラー", "サイズ", "在庫状況", "URL"]]
            ),
            unsafe_allow_html=True,
        )

        # ---------- SKUマスターとの紐づけ確認（検証用） ----------
        with st.expander("🔗 SKUマスター（CS品番）との紐づけ確認（検証用）"):
            st.caption(
                "ZOZOの「店舗品番」と、SKUマスターの「CS品番」が一致するかを確認します。"
                "一致すれば、木曜時点の在庫日報CSV（販売可能数合計）と今のZOZO在庫を比較できます。"
                "※ 在庫日報CSVは現状「納品推奨数システム」タブ（ブラウザ内のみで処理）に読み込まれており、"
                "Python側からはまだ参照できません。差分表示を作る場合は、このタブにも"
                "在庫日報CSVのアップロード欄を別途追加する必要があります。"
            )
            sku_master = load_sku_master()
            if not sku_master:
                st.info("SKUマスターが読み込めていません（『SKUマスター』フォルダにCSVが必要です）。")
            else:
                cs_no_set = {m["cs_no"] for m in sku_master}
                zozo_codes = df["店舗品番"].replace("", pd.NA).dropna().unique().tolist()
                matched = [c for c in zozo_codes if c in cs_no_set]
                unmatched = [c for c in zozo_codes if c not in cs_no_set]
                st.markdown(
                    f'<div class="metric-bar">'
                    f'<div class="metric-chip">ZOZO店舗品番（ユニーク）<strong>{len(zozo_codes):,}</strong></div>'
                    f'<div class="metric-chip">CS品番と一致<strong>{len(matched):,}</strong></div>'
                    f'<div class="metric-chip">不一致<strong>{len(unmatched):,}</strong></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if unmatched:
                    st.caption("一致しなかった店舗品番の例（先頭20件）：")
                    st.write(unmatched[:20])
                if matched:
                    st.success(
                        f"{len(matched)}件が一致しました。この形式で紐づけが可能そうです。"
                        "次のステップとして、Tempostarの現在庫との差分表示を追加できます。"
                    )

    # タブ順：最初に「在庫少商品（発注目安）」を開く
    tab_restock, tab_sales, tab_delivery, tab_stockcheck, tab_zozo = st.tabs(
        ["発注推奨一覧", "売上個数一覧", "納品推奨数システム", "在庫下げチェック", "ZOZO在庫チェック"]
    )

    with tab_restock:
        render_restock_tab(file_infos, min_date, max_date, rakuten_stock_map, rakuten_fetching, rakuten_errors, rakuten_fetched_at, all_sales_map, amazon_stock_map, amazon_fetching, amazon_errors, amazon_fetched_at)

    with tab_sales:
        render_sales_tab(file_infos, min_date, max_date, rakuten_stock_map, rakuten_fetching, rakuten_errors, rakuten_fetched_at, all_sales_map, amazon_stock_map, amazon_fetching, amazon_errors, amazon_fetched_at)

    with tab_delivery:
        render_delivery_tab(file_infos, rakuten_stock_map, rakuten_errors, rakuten_fetching, rakuten_fetched_at)

    with tab_stockcheck:
        render_stock_check_tab()

    with tab_zozo:
        render_zozo_tab()


if __name__ == "__main__":
    main()