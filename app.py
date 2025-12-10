import streamlit as st
import pandas as pd
import glob
import os
import html
import re
from datetime import datetime, timedelta


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
        if (
            "商品管理番号（商品URL）" in df.columns
            and "商品画像パス1" in df.columns
        ):
            dfs.append(df[["商品管理番号（商品URL）", "商品画像パス1"]])

    if not dfs:
        return {}

    merged = pd.concat(dfs, ignore_index=True)
    merged["商品管理番号（商品URL）"] = (
        merged["商品管理番号（商品URL）"].astype(str).str.strip()
    )
    merged["商品画像パス1"] = merged["商品画像パス1"].astype(str).str.strip()

    return dict(zip(merged["商品管理番号（商品URL）"], merged["商品画像パス1"]))


# ==========================
# HTML テーブル生成（商品コードクリック対応）
# ==========================
def make_html_table(df):
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
                link = (
                    f"<a href='?sku={code}' "
                    f"style='color:#0073e6; text-decoration:none;'>{code}</a>"
                )
                tds.append(f"<td>{link}</td>")
            elif col == "画像":
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
# Main
# ==========================
def main():
    st.set_page_config(page_title="Tempostar 売上集計", layout="wide")
    st.title("Tempostar 在庫変動データ - SKU別集計")

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

    # ---------- 初期フィルタ（セッション） ----------
    default_start = max_date - timedelta(days=30)
    if default_start < min_date:
        default_start = min_date

    if "sku_filters" not in st.session_state:
        st.session_state["sku_filters"] = {
            "start_date": default_start,
            "end_date": max_date,
            "keyword": "",
            "min_total_sales": 0,
        }
        st.session_state["sku_applied"] = False

    if "restock_filters" not in st.session_state:
        st.session_state["restock_filters"] = {
            "keyword": "",
            "min_total_sales": 0,
            "restock_months": 1,
            "target_days": 30,
            "max_current_stock": 999999,  # ★現在庫フィルタ（初期は実質フィルタなし）
        }
        st.session_state["restock_applied"] = False

    # クエリパラメータ（グラフ用）
    params = st.experimental_get_query_params()
    selected_sku = params.get("sku", [None])[0]

    # ==========================
    # CSS（列幅・3行制限・ヘッダー固定）
    # ==========================
    st.markdown(
        """
<style>
.sku-table { border-collapse:collapse; font-size:13px; width:100%; }
.sku-table th { background:#f2f2f2; }
.sku-table td, .sku-table th {
    padding:4px 6px;
    border:1px solid #ccc;
    vertical-align:top;
}
.sku-table tbody tr:hover { background:#fafafa; }
.sku-table img { max-height:70px; width:auto; display:block; margin:auto; }

/* 1:画像 */
.sku-table td:nth-child(1), .sku-table th:nth-child(1) {
    width:72px; text-align:center;
}
/* 2,3:コード */
.sku-table td:nth-child(2), .sku-table th:nth-child(2),
.sku-table td:nth-child(3), .sku-table th:nth-child(3) {
    width:110px; white-space:nowrap;
}
/* 4:商品名 */
/* ヘッダーは普通のまま */
.sku-table th:nth-child(4) {
    max-width:420px;
}
/* データ側だけ3行制限 */
.sku-table td:nth-child(4) {
    max-width:420px;
    display:-webkit-box;
    -webkit-line-clamp:3;
    -webkit-box-orient:vertical;
    overflow:hidden;
}
/* 5,6:属性 */
.sku-table td:nth-child(5), .sku-table th:nth-child(5),
.sku-table td:nth-child(6), .sku-table th:nth-child(6) {
    width:110px; white-space:nowrap;
}
/* 7,8,9:数値列 */
.sku-table td:nth-child(7), .sku-table th:nth-child(7),
.sku-table td:nth-child(8), .sku-table th:nth-child(8),
.sku-table td:nth-child(9), .sku-table th:nth-child(9) {
    width:80px; text-align:right; white-space:nowrap;
}

/* ヘッダー固定 */
.sku-table thead th {
    position:sticky;
    top:3.2rem;
    z-index:2;
    background:#f2f2f2;
}

/* =======================
   表の文字サイズアップ
   ======================= */
.sku-table {
    font-size: 14px;   /* ← 今13px → 14pxに拡大 */
}

/* =======================
   発注推奨数を強調表示
   ======================= */
/* 発注推奨数列だけ強調 */
.sku-table td:has(span.order-col),
.sku-table th:has(span.order-col) {
    font-weight: bold;
    background: #FFE4E1;
    color: #C40000;
    text-align: center;
}
}
</style>
""",
        unsafe_allow_html=True,
    )

    # ==========================
    # タブ
    # ==========================
    tab1, tab2 = st.tabs(["SKU別売上集計", "在庫少商品（発注目安）"])

    # --------------------------------------------------
    # タブ1：SKU別売上集計
    # --------------------------------------------------
    with tab1:
        left, right = st.columns([1, 3])

        # ---- 左カラム：フィルタ ----
        with left:
            st.subheader("SKU別売上集計 - 条件")
            st.text(f"データ期間：{min_date} ～ {max_date}")

            f_sku = st.session_state["sku_filters"]

            with st.form("sku_form"):
                start_date = st.date_input(
                    "開始日",
                    f_sku["start_date"],
                    min_value=min_date,
                    max_value=max_date,
                )
                end_date = st.date_input(
                    "終了日",
                    f_sku["end_date"],
                    min_value=min_date,
                    max_value=max_date,
                )
                keyword = st.text_input(
                    "検索（商品コード / 商品基本コード / 商品名）",
                    f_sku["keyword"],
                )
                min_total_sales = st.number_input(
                    "売上個数の下限（プラス値）",
                    min_value=0,
                    value=int(f_sku["min_total_sales"]),
                )

                submit_sku = st.form_submit_button("この条件で表示")

            if submit_sku:
                if start_date > end_date:
                    start_date, end_date = end_date, start_date
                st.session_state["sku_filters"] = {
                    "start_date": start_date,
                    "end_date": end_date,
                    "keyword": keyword,
                    "min_total_sales": int(min_total_sales),
                }
                st.session_state["sku_applied"] = True

        # ---- 右カラム：結果 ----
        with right:
            if not st.session_state["sku_applied"]:
                st.info("左側で条件を設定して『この条件で表示』を押してください。")
            else:
                f_sku = st.session_state["sku_filters"]
                start_date = f_sku["start_date"]
                end_date = f_sku["end_date"]
                keyword = f_sku["keyword"]
                min_total_sales = f_sku["min_total_sales"]

                # ---------- DF 読み込み ----------
                main_files = [
                    fi for fi in file_infos if start_date <= fi["date"] <= end_date
                ]
                if not main_files:
                    st.error("選択範囲のCSVがありません。")
                else:
                    main_paths = [fi["path"] for fi in main_files]
                    df_main = load_tempostar_data(main_paths)

                    # キーワード絞り込み
                    if keyword:
                        cond = False
                        for col in ["商品コード", "商品基本コード", "商品名"]:
                            if col in df_main.columns:
                                cond |= df_main[col].astype(str).str.contains(
                                    keyword, case=False
                                )
                        df_main = df_main[cond]

                    # 必須列チェック
                    required = {"商品コード", "商品基本コード", "増減値"}
                    if not required.issubset(df_main.columns):
                        st.error(
                            "Tempostar CSV に『商品コード』『商品基本コード』『増減値』が必要です。"
                        )
                    else:
                        # --- 在庫推移グラフ ---
                        if selected_sku:
                            st.markdown(f"### 📈 在庫推移グラフ：{selected_sku}")

                            if "変動後" not in df_main.columns:
                                st.warning(
                                    "『変動後』列がないため在庫推移グラフを表示できません。"
                                )
                            else:
                                df_sku = df_main[
                                    df_main["商品コード"] == selected_sku
                                ].copy()
                                df_sku["日付"] = df_sku["元ファイル"].str.extract(
                                    r"(\d{8})"
                                )
                                df_sku["日付"] = pd.to_datetime(
                                    df_sku["日付"],
                                    format="%Y%m%d",
                                    errors="coerce",
                                )
                                df_plot = (
                                    df_sku[["日付", "変動後"]]
                                    .dropna()
                                    .sort_values("日付")
                                )

                                if df_plot.empty:
                                    st.warning(
                                        "選択したSKUの在庫データがありません。"
                                    )
                                else:
                                    st.line_chart(
                                        df_plot.set_index("日付")["変動後"]
                                    )

                            st.markdown("---")

                        # --- 売上集計 ---
                        if "更新理由" in df_main.columns:
                            df_sales_main = df_main[
                                df_main["更新理由"] == "受注取込"
                            ].copy()
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
                        sales_grouped = sales_grouped[
                            sales_grouped["売上個数合計"] > 0
                        ]

                        # 在庫（現在庫）
                        if "変動後" in df_main.columns:
                            stock_group = (
                                df_main.groupby(
                                    "商品コード", dropna=False
                                )["変動後"]
                                .last()
                                .reset_index()
                                .rename(columns={"変動後": "現在庫"})
                            )
                            stock_group["現在庫"] = (
                                pd.to_numeric(
                                    stock_group["現在庫"], errors="coerce"
                                )
                                .fillna(0)
                                .astype(int)
                            )
                            sales_grouped = sales_grouped.merge(
                                stock_group, on="商品コード", how="left"
                            )
                        else:
                            sales_grouped["現在庫"] = 0

                        sales_grouped["現在庫"] = (
                            pd.to_numeric(
                                sales_grouped["現在庫"], errors="coerce"
                            )
                            .fillna(0)
                            .astype(int)
                        )

                        if min_total_sales > 0:
                            sales_grouped = sales_grouped[
                                sales_grouped["売上個数合計"]
                                >= min_total_sales
                            ]

                        sales_grouped = sales_grouped.sort_values(
                            "売上個数合計", ascending=False
                        )

                        # 画像列
                        img_master = load_image_master()
                        base_url = "https://image.rakuten.co.jp/hype/cabinet"

                        def to_img(code):
                            key = str(code).strip()
                            rel = img_master.get(key, "")
                            if not rel:
                                return ""
                            return (
                                f'<img src="{base_url + rel}" width="70">'
                            )

                        sales_grouped["画像"] = sales_grouped[
                            "商品基本コード"
                        ].apply(to_img)

                        cols = sales_grouped.columns.tolist()
                        cols.insert(0, cols.pop(cols.index("画像")))
                        sales_grouped = sales_grouped[cols]

                        display_cols = [
                            "画像",
                            "商品コード",
                            "商品基本コード",
                            "商品名",
                            "属性1名",
                            "属性2名",
                            "売上個数合計",
                            "現在庫",
                        ]
                        df_view = sales_grouped[display_cols]

                        st.write(
                            f"📦 SKU数：{len(df_view):,} ｜ 集計期間：{start_date} ～ {end_date}"
                        )
                        st.markdown(
                            make_html_table(df_view),
                            unsafe_allow_html=True,
                        )

    # --------------------------------------------------
    # タブ2：在庫少商品（発注目安）
    # --------------------------------------------------
    with tab2:
        left, right = st.columns([1, 3])

        # ---- 左カラム：フィルタ ----
        with left:
            st.subheader("在庫少商品（発注目安） - 条件")
            st.text(f"データ最終日：{max_date}")

            f_r = st.session_state["restock_filters"]

            with st.form("restock_form"):
                keyword_r = st.text_input(
                    "検索（商品コード / 商品基本コード / 商品名）",
                    f_r["keyword"],
                )
                min_total_sales_r = st.number_input(
                    "売上個数の下限（プラス値）",
                    min_value=0,
                    value=int(f_r["min_total_sales"]),
                )

                months_choices = [1, 2, 3, 4, 5, 6]
                default_restock = int(f_r.get("restock_months", 1))
                if default_restock not in months_choices:
                    default_restock = 1

                restock_months = st.selectbox(
                    "在庫少商品の集計期間（直近◯ヶ月）",
                    months_choices,
                    index=months_choices.index(default_restock),
                )

                target_days = st.number_input(
                    "何日分の在庫を確保するか（発注目安）",
                    min_value=1,
                    max_value=365,
                    value=int(f_r["target_days"]),
                )

                # ★現在庫の最大値フィルタ
                max_current_stock = st.number_input(
                    "現在庫の上限（この数以下を抽出）",
                    min_value=0,
                    max_value=999999,
                    value=int(f_r.get("max_current_stock", 999999)),
                )

                submit_restock = st.form_submit_button("この条件で表示")

            if submit_restock:
                st.session_state["restock_filters"] = {
                    "keyword": keyword_r,
                    "min_total_sales": int(min_total_sales_r),
                    "restock_months": int(restock_months),
                    "target_days": int(target_days),
                    "max_current_stock": int(max_current_stock),
                }
                st.session_state["restock_applied"] = True

        # ---- 右カラム：結果 ----
        with right:
            if not st.session_state["restock_applied"]:
                st.info("左側で条件を設定して『この条件で表示』を押してください。")
            else:
                f_r = st.session_state["restock_filters"]
                keyword_r = f_r["keyword"]
                min_total_sales_r = f_r["min_total_sales"]
                restock_months = f_r["restock_months"]
                target_days = f_r["target_days"]
                max_current_stock = f_r["max_current_stock"]  # ★ここで読み出し

                # 直近 restock_months ヶ月
                end_r = max_date
                start_r = (
                    pd.Timestamp(max_date)
                    - pd.DateOffset(months=restock_months)
                ).date()
                if start_r < min_date:
                    start_r = min_date

                restock_files = [
                    fi for fi in file_infos if start_r <= fi["date"] <= end_r
                ]
                if not restock_files:
                    st.warning(
                        f"直近{restock_months}ヶ月（{start_r} ～ {end_r}）にCSVがありません。"
                    )
                else:
                    restock_paths = [fi["path"] for fi in restock_files]
                    df_restock = load_tempostar_data(restock_paths)

                    # キーワード適用
                    if keyword_r:
                        cond_r = False
                        for col in ["商品コード", "商品基本コード", "商品名"]:
                            if col in df_restock.columns:
                                cond_r |= df_restock[col].astype(str).str.contains(
                                    keyword_r, case=False
                                )
                        df_restock = df_restock[cond_r]

                    if "更新理由" in df_restock.columns:
                        df_sales_recent = df_restock[
                            df_restock["更新理由"] == "受注取込"
                        ].copy()
                    else:
                        df_sales_recent = df_restock.copy()

                    if df_sales_recent.empty:
                        st.warning(
                            f"直近{restock_months}ヶ月（{start_r} ～ {end_r}）に売上データがありません。"
                        )
                    else:
                        agg_sales = {
                            "商品基本コード": "last",
                            "商品名": "last",
                            "属性1名": "last",
                            "属性2名": "last",
                            "増減値": "sum",
                        }

                        sales_recent = (
                            df_sales_recent.groupby(
                                "商品コード", dropna=False
                            )
                            .agg(agg_sales)
                            .reset_index()
                            .rename(columns={"増減値": "増減値合計"})
                        )
                        sales_recent["売上個数合計"] = -sales_recent["増減値合計"]
                        sales_recent = sales_recent[
                            sales_recent["売上個数合計"] > 0
                        ]

                        # 売上下限
                        if min_total_sales_r > 0:
                            sales_recent = sales_recent[
                                sales_recent["売上個数合計"]
                                >= min_total_sales_r
                            ]

                        # 現在庫：直近期間内の最後の変動後
                        if "変動後" in df_restock.columns:
                            stock_group_r = (
                                df_restock.groupby(
                                    "商品コード", dropna=False
                                )["変動後"]
                                .last()
                                .reset_index()
                                .rename(columns={"変動後": "現在庫"})
                            )
                            stock_group_r["現在庫"] = (
                                pd.to_numeric(
                                    stock_group_r["現在庫"], errors="coerce"
                                )
                                .fillna(0)
                                .astype(int)
                            )
                            sales_recent = sales_recent.merge(
                                stock_group_r, on="商品コード", how="left"
                            )
                        else:
                            sales_recent["現在庫"] = 0

                        sales_recent["現在庫"] = (
                            pd.to_numeric(
                                sales_recent["現在庫"], errors="coerce"
                            )
                            .fillna(0)
                            .astype(int)
                        )

                        # ★現在庫フィルタをここで適用（この数以下だけ残す）
                        sales_recent = sales_recent[
                            sales_recent["現在庫"] <= max_current_stock
                        ]

                        # 画像列
                        img_master = load_image_master()
                        base_url = "https://image.rakuten.co.jp/hype/cabinet"

                        def to_img(code):
                            key = str(code).strip()
                            rel = img_master.get(key, "")
                            if not rel:
                                return ""
                            return (
                                f'<img src="{base_url + rel}" width="70">'
                            )

                        sales_recent["画像"] = sales_recent[
                            "商品基本コード"
                        ].apply(to_img)

                        # 表示順
                        display_cols = [
                            "画像",
                            "商品コード",
                            "商品基本コード",
                            "商品名",
                            "属性1名",
                            "属性2名",
                            "売上個数合計",
                            "現在庫",
                        ]
                        cols_r = ["画像"] + [
                            c for c in display_cols if c != "画像"
                        ]
                        sales_recent = sales_recent[cols_r]

                        # 発注推奨数計算
                        period_days = max((end_r - start_r).days + 1, 1)
                        sales_recent["1日平均売上"] = (
                            sales_recent["売上個数合計"] / period_days
                        )
                        sales_recent["目標在庫"] = (
                            sales_recent["1日平均売上"] * target_days
                        )

                        target_qty = pd.to_numeric(
                            sales_recent["目標在庫"], errors="coerce"
                        )
                        current_stock = pd.to_numeric(
                            sales_recent["現在庫"], errors="coerce"
                        )
                        diff = (target_qty - current_stock).fillna(0)
                        sales_recent["発注推奨数"] = (
                            diff.where(diff > 0, 0).round().astype(int)
                        )

                        restock_view = sales_recent[
                            sales_recent["発注推奨数"] > 0
                        ]
                        restock_view = restock_view.sort_values(
                            "発注推奨数", ascending=False
                        )

                        st.info(
                            f"発注目安は直近{restock_months}ヶ月（{start_r} ～ {end_r}）の売上から計算しています。"
                        )

                        if restock_view.empty:
                            st.success("発注推奨の商品はありません。")
                        else:
                            cols2 = display_cols + [
                                "1日平均売上",
                                "目標在庫",
                                "発注推奨数",
                            ]
                            restock_view = restock_view[cols2]
                            # 発注推奨数列をHTML生成用にクラス付与
                            restock_view.rename(columns={"発注推奨数": "<span class='order-col'>発注推奨数</span>"}, inplace=True)

                            # 小数点1桁表示
                            restock_view["1日平均売上"] = restock_view["1日平均売上"].map(
                                lambda x: f"{x:.1f}"
                            )
                            restock_view["目標在庫"] = restock_view["目標在庫"].map(
                                lambda x: f"{x:.1f}"
                            )

                            st.write(
                                f"⚠ 抽出SKU数：{len(restock_view):,} ｜ 目標在庫：平均 {target_days} 日分"
                            )
                            st.markdown(
                                make_html_table(restock_view),
                                unsafe_allow_html=True,
                            )


if __name__ == "__main__":
    main()
