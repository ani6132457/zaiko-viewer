import streamlit as st
import pandas as pd
import glob
import os
import html
import re
from datetime import datetime, timedelta
from pandas.tseries.offsets import DateOffset

# 追加（オーバーレイ表示用）
import base64
import io
import matplotlib.pyplot as plt


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
def show_stock_drawer(selected_sku: str, df_main: pd.DataFrame):
    msg = ""
    img_html = ""

    if "変動後" not in df_main.columns:
        msg = "『変動後』列がないため在庫推移グラフを表示できません。"
    else:
        df_sku = df_main[df_main["商品コード"] == selected_sku].copy()
        df_sku["日付"] = df_sku["元ファイル"].astype(str).str.extract(r"(\d{8})")
        df_sku["日付"] = pd.to_datetime(df_sku["日付"], format="%Y%m%d", errors="coerce")
        df_plot = df_sku[["日付", "変動後"]].dropna().sort_values("日付")

        if df_plot.empty:
            msg = "選択したSKUの在庫データがありません。"
        else:
            fig, ax = plt.subplots(figsize=(7.4, 3.4))
            ax.plot(df_plot["日付"], df_plot["変動後"])
            ax.set_title(f"在庫推移（SKU: {selected_sku}）")
            ax.set_ylabel("在庫")
            ax.grid(True, alpha=0.25)
            fig.autofmt_xdate()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
            plt.close(fig)

            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            img_html = f"<img src='data:image/png;base64,{b64}' style='width:100%;height:auto;display:block;' />"

            drawer_html = f"""
            <style>
            .drawer {{
                position: fixed;
                top: 3.6rem;
                right: 0;
                width: 560px;
                max-width: 94vw;
                height: calc(100vh - 3.6rem);
                background: #fff;
                border-left: 1px solid #ddd;
                box-shadow: -10px 0 26px rgba(0,0,0,0.18);
                z-index: 9999;
                padding: 14px 14px 18px 14px;
                overflow: auto;
                animation: slideIn 180ms ease-out;
            }}
            @keyframes slideIn {{
                from {{ transform: translateX(16px); opacity: 0.6; }}
                to   {{ transform: translateX(0); opacity: 1; }}
            }}
            .drawer-head {{
                display:flex;
                align-items:center;
                justify-content:space-between;
                gap:10px;
                margin-bottom: 10px;
            }}
            .drawer-title {{
                font-weight: 700;
                font-size: 15px;
                margin: 0;
            }}
            .drawer-close-btn {{
                padding: 6px 10px;
                border: 1px solid #ddd;
                border-radius: 10px;
                background: #fafafa;
                font-size: 13px;
                cursor: pointer;
                white-space: nowrap;
            }}
            .drawer-close-btn:hover {{ background: #f0f0f0; }}
            .drawer-msg {{
                margin: 6px 0 0 0;
                color:#444;
                font-size: 13px;
            }}
            </style>

            <div class="drawer" id="stock-drawer">
            <div class="drawer-head">
                <p class="drawer-title">📈 在庫推移（{html.escape(str(selected_sku))}）</p>
                <button class="drawer-close-btn" onclick="
                const el = window.parent.document.getElementById('stock-drawer');
                if (el) el.style.display='none';
                ">閉じる</button>
            </div>

            {f"<p class='drawer-msg'>{html.escape(msg)}</p>" if msg else ""}
            {img_html}
            </div>
            """
            st.markdown(drawer_html, unsafe_allow_html=True)

    # 閉じる（sessionで制御）
    if st.button("閉じる", key=f"close_drawer_{selected_sku}"):
        st.session_state["selected_sku"] = None
        st.rerun()


# ==========================
# Main
# ==========================
def main():
    st.set_page_config(page_title="Tempostar 売上集計", layout="wide")
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


    # ---------- 初期フィルタ（セッション） ----------
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
[data-testid="stTabs"] button {
    font-size: 14px !important;
    font-weight: 600 !important;
    padding: 8px 20px !important;
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
    def render_restock_tab(file_infos, min_date, max_date):
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

                submit_restock = st.form_submit_button("🔎 この条件で表示", use_container_width=True)

            st.markdown('</div>', unsafe_allow_html=True)

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
                                "属性1名", "属性2名", "売上個数合計", "現在庫", "発注推奨数",
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
                                df_view_r.columns.tolist().index("現在庫") + 1,
                                "状態",
                                [_status(s, v) for s, v in zip(stock_num, sales_num)]
                            )

                            st.markdown(
                                f'<div class="metric-bar">'
                                f'<div class="metric-chip">抽出SKU数<strong>{len(df_view_r):,}</strong></div>'
                                f'<div class="metric-chip">集計期間<strong>{start_r} ～ {end_r}</strong></div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

                            col_cfg = {}
                            if "画像" in df_view_r.columns:
                                col_cfg["画像"] = st.column_config.ImageColumn("画像", width="small")

                            st.dataframe(
                                df_view_r,
                                hide_index=True,
                                use_container_width=True,
                                column_config=col_cfg if col_cfg else None,
                            )

    def render_sales_tab(file_infos, min_date, max_date):
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

                submit_sku = st.form_submit_button("🔎 この条件で表示", use_container_width=True)

            st.markdown('</div>', unsafe_allow_html=True)

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

                # --- 売上集計（今年）---
                if "更新理由" in df_main.columns:
                    df_sales_main = df_main[df_main["更新理由"].astype(str).str.contains("受注取込", na=False)].copy()
                else:
                    df_sales_main = df_main.copy()

                agg_sales = {
                    "商品基本コード": "last",
                    "商品名": "last",
                    "属性1名": "last",
                    "属性2名": "last",
                    "増減値": "sum",
                }

                sales_grouped = (
                    df_sales_main.groupby("商品コード", dropna=False)
                    .agg(agg_sales)
                    .reset_index()
                    .rename(columns={"増減値": "増減値合計"})
                )
                sales_grouped["売上個数合計"] = -sales_grouped["増減値合計"]
                sales_grouped = sales_grouped[sales_grouped["売上個数合計"] > 0]

                # --- 売上集計（昨年）---
                if df_last is not None and {"商品コード", "増減値"}.issubset(df_last.columns):
                    if "更新理由" in df_last.columns:
                        df_sales_last = df_last[
                            df_last["更新理由"].astype(str).str.contains("受注取込", na=False)
                        ].copy()
                    else:
                        df_sales_last = df_last.copy()

                    df_sales_last["商品コード"] = df_sales_last["商品コード"].astype(str).str.strip()
                    df_sales_main["商品コード"] = df_sales_main["商品コード"].astype(str).str.strip()

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

                # 在庫（現在庫）
                if "変動後" in df_main.columns:
                    stock_group = (
                        df_main.groupby("商品コード", dropna=False)["変動後"]
                        .last()
                        .reset_index()
                        .rename(columns={"変動後": "現在庫"})
                    )
                    stock_group["現在庫"] = (
                        pd.to_numeric(stock_group["現在庫"], errors="coerce")
                        .fillna(0)
                        .astype(int)
                    )
                    sales_grouped = sales_grouped.merge(stock_group, on="商品コード", how="left")
                else:
                    sales_grouped["現在庫"] = 0

                sales_grouped["現在庫"] = (
                    pd.to_numeric(sales_grouped["現在庫"], errors="coerce")
                    .fillna(0)
                    .astype(int)
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
                    "属性1名", "属性2名", "今年売上", "前年売上", "現在庫",
                ]
                display_cols = [c for c in display_cols if c in sales_grouped.columns]
                df_view = sales_grouped[display_cols]

                st.markdown(
                    f'<div class="metric-bar">'
                    f'<div class="metric-chip">SKU数<strong>{len(df_view):,}</strong></div>'
                    f'<div class="metric-chip">集計期間<strong>{start_date} ～ {end_date}</strong></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                event = st.dataframe(
                    df_view,
                    hide_index=True,
                    use_container_width=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    column_config={
                        "画像":    st.column_config.ImageColumn("画像", width="small"),
                        "今年売上": st.column_config.NumberColumn("今年売上", format="%d"),
                        "前年売上": st.column_config.NumberColumn("前年売上", format="%d"),
                        "現在庫":  st.column_config.NumberColumn("現在庫",   format="%d"),
                    } if "画像" in df_view.columns else None,
                )

                # 行クリックでSKU取得 → ドロワー表示
                sel = event.selection.get("rows", [])
                if sel:
                    clicked_row = df_view.iloc[sel[0]]
                    st.session_state["selected_sku"] = str(clicked_row["商品コード"]).strip()

                # 右ドロワー（選択されている時だけ）
                if st.session_state["selected_sku"]:
                    show_stock_drawer(st.session_state["selected_sku"], df_main)

        # --------------------------------------------------
        # タブ2：在庫少商品（発注目安）
        # --------------------------------------------------

    # タブ順：最初に「在庫少商品（発注目安）」を開く
    tab_restock, tab_sales = st.tabs(["発注推奨一覧", "売上個数一覧"])

    with tab_restock:
        render_restock_tab(file_infos, min_date, max_date)

    with tab_sales:
        render_sales_tab(file_infos, min_date, max_date)


if __name__ == "__main__":
    main()