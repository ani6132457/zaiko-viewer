import streamlit as st
import pandas as pd
import glob
import os
import html
import re
from datetime import datetime, date, timedelta


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

    # ---------- フィルタ初期値 ----------
    default_start = max_date - timedelta(days=30)
    if default_start < min_date:
        default_start = min_date

    if "filters" not in st.session_state:
        st.session_state["filters"] = {
            "start_date": default_start,
            "end_date": max_date,
            "keyword": "",
            "min_total_sales": 0,
            "target_days": 30,
            "restock_months": 1,   # 在庫少タブ用：直近◯ヶ月
            "submitted": False,
        }

    f = st.session_state["filters"]

    # ==========================
    # Sidebar（フォーム＋ボタン）
    # ==========================
    with st.sidebar:
        st.header("集計条件")
        st.caption(f"📅 データ期間：{min_date} ～ {max_date}")

        with st.form("filter_form"):
            start_date = st.date_input(
                "開始日", f["start_date"], min_value=min_date, max_value=max_date
            )
            end_date = st.date_input(
                "終了日", f["end_date"], min_value=min_date, max_value=max_date
            )

            keyword = st.text_input(
                "検索（商品コード / 商品基本コード / 商品名）",
                f["keyword"],
            )
            min_total_sales = st.number_input(
                "売上個数の下限（プラス値）",
                min_value=0,
                value=int(f["min_total_sales"]),
            )
            target_days = st.number_input(
                "何日分の在庫を確保するか（発注目安）",
                min_value=1,
                max_value=365,
                value=int(f["target_days"]),
            )
            restock_months = st.selectbox(
                "在庫少商品の集計期間（直近◯ヶ月）",
                [1, 2, 3, 4, 5, 6],
                index=[1, 2, 3, 4, 5, 6].index(int(f["restock_months"])),
            )

            submitted = st.form_submit_button("この条件で表示")

        if submitted:
            if start_date > end_date:
                start_date, end_date = end_date, start_date

            f["start_date"] = start_date
            f["end_date"] = end_date
            f["keyword"] = keyword
            f["min_total_sales"] = int(min_total_sales)
            f["target_days"] = int(target_days)
            f["restock_months"] = int(restock_months)
            f["submitted"] = True

        # 対象CSV一覧
        if f["submitted"]:
            target_files = [
                fi
                for fi in file_infos
                if f["start_date"] <= fi["date"] <= f["end_date"]
            ]
            st.markdown("---")
            st.caption("対象CSV：")
            for fi in target_files:
                st.caption(f"・{fi['date']} : {fi['name']}")

    if not f["submitted"]:
        st.info("左の条件を設定して『この条件で表示』ボタンを押してください。")
        return

    start_date = f["start_date"]
    end_date = f["end_date"]
    keyword = f["keyword"]
    min_total_sales = f["min_total_sales"]
    target_days = f["target_days"]
    restock_months = f["restock_months"]

    # ---------- 期間内 CSV 抽出（SKU集計用） ----------
    target_files = [fi for fi in file_infos if start_date <= fi["date"] <= end_date]
    if not target_files:
        st.error("選択範囲のCSVがありません。")
        return
    paths = [fi["path"] for fi in target_files]

    # ==========================
    # メインDF 読み込み
    # ==========================
    df = load_tempostar_data(paths)

    # 元ファイル名から日付列（_file_date）を作成（在庫少タブ用）
    if "_file_date" not in df.columns:
        date_str = df["元ファイル"].str.extract(r"(\d{8})")[0]
        df["_file_date"] = pd.to_datetime(
            date_str, format="%Y%m%d", errors="coerce"
        ).dt.date

    # キーワード絞り込み
    if keyword:
        cond = False
        for col in ["商品コード", "商品基本コード", "商品名"]:
            if col in df.columns:
                cond |= df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    # 必須列チェック
    required = {"商品コード", "商品基本コード", "増減値"}
    if not required.issubset(df.columns):
        st.error("Tempostar CSV に『商品コード』『商品基本コード』『増減値』が必要です。")
        return

    # ==========================
    # 商品コードクリック時の在庫推移グラフ
    # ==========================
    params = st.experimental_get_query_params()
    selected_sku = params.get("sku", [None])[0]

    if selected_sku:
        st.markdown(f"## 📈 在庫推移グラフ：{selected_sku}")

        if "変動後" not in df.columns:
            st.warning("『変動後』列がないため在庫推移グラフを表示できません。")
        else:
            df_sku = df[df["商品コード"] == selected_sku].copy()
            df_sku["日付"] = df_sku["元ファイル"].str.extract(r"(\d{8})")
            df_sku["日付"] = pd.to_datetime(
                df_sku["日付"], format="%Y%m%d", errors="coerce"
            )
            df_plot = df_sku[["日付", "変動後"]].dropna().sort_values("日付")

            if df_plot.empty:
                st.warning("選択したSKUの在庫データがありません。")
            else:
                st.line_chart(df_plot.set_index("日付")["変動後"])

        st.markdown("---")

    # ==========================
    # 売上集計（SKU別タブ用：サイドバー日付範囲）
    # ==========================
    if "更新理由" in df.columns:
        df_sales_all = df[df["更新理由"] == "受注取込"].copy()
    else:
        df_sales_all = df.copy()

    agg_sales = {
        "商品基本コード": "last",
        "商品名": "last",
        "属性1名": "last",
        "属性2名": "last",
        "増減値": "sum",
    }

    sales_grouped = (
        df_sales_all.groupby("商品コード", dropna=False)
        .agg(agg_sales)
        .reset_index()
        .rename(columns={"増減値": "増減値合計"})
    )

    sales_grouped["売上個数合計"] = -sales_grouped["増減値合計"]
    sales_grouped = sales_grouped[sales_grouped["売上個数合計"] > 0]

    # 在庫（現在庫）※全データから最新在庫を取得
    if "変動後" in df.columns:
        stock_group = (
            df.groupby("商品コード", dropna=False)["変動後"]
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

    # 売上個数の下限フィルタ
    if min_total_sales > 0:
        sales_grouped = sales_grouped[
            sales_grouped["売上個数合計"] >= min_total_sales
        ]

    sales_grouped = sales_grouped.sort_values("売上個数合計", ascending=False)

    # ==========================
    # 画像列の付与（共通）
    # ==========================
    img_master = load_image_master()
    base_url = "https://image.rakuten.co.jp/hype/cabinet"

    def to_img(code):
        key = str(code).strip()
        rel = img_master.get(key, "")
        if not rel:
            return ""
        return f'<img src="{base_url + rel}" width="70">'

    sales_grouped["画像"] = sales_grouped["商品基本コード"].apply(to_img)

    # 画像列を先頭へ
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
        "増減値合計",
    ]
    df_view = sales_grouped[display_cols]

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
</style>
""",
        unsafe_allow_html=True,
    )

    # ==========================
    # タブ表示
    # ==========================
    tab1, tab2 = st.tabs(["SKU別売上集計", "在庫少商品（発注目安）"])

    # ---- タブ1：SKU別売上集計（サイドバーの期間）----
    with tab1:
        st.write(
            f"📦 SKU数：{len(df_view):,} ｜ 集計期間：{start_date} ～ {end_date}"
        )
        st.markdown(make_html_table(df_view), unsafe_allow_html=True)

    # ---- タブ2：在庫少商品（直近◯ヶ月）----
    with tab2:
        # 直近 restock_months ヶ月分のデータだけで売上を再集計
        end_r = max_date
        start_r = (pd.Timestamp(max_date) - pd.DateOffset(months=restock_months)).date()
        if start_r < min_date:
            start_r = min_date

        period_days = max((end_r - start_r).days + 1, 1)

        df_recent = df[(df["_file_date"] >= start_r) & (df["_file_date"] <= end_r)]

        if df_recent.empty:
            st.warning(
                f"直近{restock_months}ヶ月（{start_r} ～ {end_r}）にデータがありません。"
            )
        else:
            if "更新理由" in df_recent.columns:
                df_sales_recent = df_recent[df_recent["更新理由"] == "受注取込"].copy()
            else:
                df_sales_recent = df_recent.copy()

            if df_sales_recent.empty:
                st.warning(
                    f"直近{restock_months}ヶ月（{start_r} ～ {end_r}）に売上データがありません。"
                )
            else:
                sales_recent = (
                    df_sales_recent.groupby("商品コード", dropna=False)
                    .agg(agg_sales)
                    .reset_index()
                    .rename(columns={"増減値": "増減値合計"})
                )
                sales_recent["売上個数合計"] = -sales_recent["増減値合計"]
                sales_recent = sales_recent[sales_recent["売上個数合計"] > 0]

                # 最新在庫（stock_group）をマージ
                if "現在庫" in sales_grouped.columns:
                    stock_for_merge = sales_grouped[["商品コード", "現在庫"]].copy()
                    sales_recent = sales_recent.merge(
                        stock_for_merge, on="商品コード", how="left"
                    )
                else:
                    sales_recent["現在庫"] = 0

                sales_recent["現在庫"] = (
                    pd.to_numeric(sales_recent["現在庫"], errors="coerce")
                    .fillna(0)
                    .astype(int)
                )

                # 画像列
                sales_recent["画像"] = sales_recent["商品基本コード"].apply(to_img)

                # 表示順に揃える
                cols_r = ["画像"] + [c for c in display_cols if c != "画像"]
                sales_recent = sales_recent[cols_r]

                # 1日平均売上・目標在庫・発注推奨数
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

                restock_view = sales_recent[sales_recent["発注推奨数"] > 0]
                restock_view = restock_view.sort_values(
                    "発注推奨数", ascending=False
                )

                st.info(
                    f"発注目安は直近{restock_months}ヶ月（{start_r} ～ {end_r}）の売上から計算しています。"
                )

                if restock_view.empty:
                    st.success("発注推奨の商品はありません。")
                else:
                    cols2 = display_cols + ["1日平均売上", "目標在庫", "発注推奨数"]
                    restock_view = restock_view[cols2]
                    st.write(
                        f"⚠ 抽出SKU数：{len(restock_view):,} ｜ 目標在庫：平均 {target_days} 日分"
                    )
                    st.markdown(
                        make_html_table(restock_view),
                        unsafe_allow_html=True,
                    )


if __name__ == "__main__":
    main()
