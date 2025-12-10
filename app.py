import streamlit as st
import pandas as pd
import glob
import os
import html
import re
from datetime import datetime, date


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

    # ★ ここを .strip() ではなく .str.strip() にする
    merged["商品管理番号（商品URL）"] = (
        merged["商品管理番号（商品URL）"].astype(str).str.strip()
    )
    merged["商品画像パス1"] = (
        merged["商品画像パス1"].astype(str).str.strip()
    )

    return dict(zip(merged["商品管理番号（商品URL）"], merged["商品画像パス1"]))


# ==========================
# HTML テーブル生成（商品コードクリック）
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
        m = pat.search(os.path.basename(path))
        if m:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
            file_infos.append({"date": d, "path": path, "name": os.path.basename(path)})

    if not file_infos:
        st.error("tempostar_stock_YYYYMMDD.csv 形式のファイルがありません。")
        return

    all_dates = sorted({fi["date"] for fi in file_infos})
    min_date, max_date = min(all_dates), max(all_dates)
    years = sorted({d.year for d in all_dates})

    # ---------- フィルタ初期値 ----------
    one_month_ago = (pd.Timestamp(max_date) - pd.DateOffset(months=1)).date()
    if one_month_ago < min_date:
        one_month_ago = min_date

    if "filters" not in st.session_state:
        st.session_state["filters"] = {
            "start_date": one_month_ago,
            "end_date": max_date,
            "keyword": "",
            "min_total_sales": 0,
            "target_days": 30,
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
            st.markdown("##### 開始日")
            c1, c2, c3 = st.columns(3)
            with c1:
                s_y = st.selectbox("開始年", years,
                                   index=years.index(f["start_date"].year),
                                   label_visibility="collapsed")
            with c2:
                s_m = st.selectbox("開始月", sorted({d.month for d in all_dates}),
                                   index=f["start_date"].month - 1,
                                   label_visibility="collapsed")
            with c3:
                s_d = st.selectbox("開始日", sorted({d.day for d in all_dates}),
                                   index=f["start_date"].day - 1,
                                   label_visibility="collapsed")

            st.markdown("##### 終了日")
            c4, c5, c6 = st.columns(3)
            with c4:
                e_y = st.selectbox("終了年", years,
                                   index=years.index(f["end_date"].year),
                                   label_visibility="collapsed")
            with c5:
                e_m = st.selectbox("終了月", sorted({d.month for d in all_dates}),
                                   index=f["end_date"].month - 1,
                                   label_visibility="collapsed")
            with c6:
                e_d = st.selectbox("終了日", sorted({d.day for d in all_dates}),
                                   index=f["end_date"].day - 1,
                                   label_visibility="collapsed")

            keyword = st.text_input("検索（商品コード / 商品名）", f["keyword"])
            min_total_sales = st.number_input("売上個数の下限", min_value=0, value=f["min_total_sales"])
            target_days = st.number_input("何日分の在庫を確保するか", min_value=1, max_value=365, value=f["target_days"])

            submitted = st.form_submit_button("この条件で表示")

        if submitted:
            start_date = date(s_y, s_m, s_d)
            end_date = date(e_y, e_m, e_d)
            if start_date > end_date:
                start_date, end_date = end_date, start_date

            f["start_date"] = start_date
            f["end_date"] = end_date
            f["keyword"] = keyword
            f["min_total_sales"] = int(min_total_sales)
            f["target_days"] = int(target_days)
            f["submitted"] = True

        # 対象CSVもサイドバーに表示
        if f["submitted"]:
            target_files = [fi for fi in file_infos if f["start_date"] <= fi["date"] <= f["end_date"]]
            st.markdown("---")
            st.caption("対象CSV：")
            for fi in target_files:
                st.caption(f"・{fi['date']} : {fi['name']}")

    if not f["submitted"]:
        st.info("左の条件を設定して『この条件で表示』ボタンを押してください。")
        return

    start_date, end_date = f["start_date"], f["end_date"]
    keyword, min_total_sales, target_days = f["keyword"], f["min_total_sales"], f["target_days"]

    target_files = [fi for fi in file_infos if start_date <= fi["date"] <= end_date]
    if not target_files:
        st.error("選択範囲のCSVがありません。")
        return

    paths = [fi["path"] for fi in target_files]
    df = load_tempostar_data(paths)

    # ==========================
    # キーワード絞り込み
    # ==========================
    if keyword:
        cond = False
        for col in ["商品コード", "商品名"]:
            if col in df.columns:
                cond |= df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    # ==========================
    # 在庫推移グラフ（商品コードクリック）
    # ==========================
    params = st.experimental_get_query_params()
    selected_sku = params.get("sku", [None])[0]

    if selected_sku:
        st.markdown(f"## 📈 在庫推移グラフ：{selected_sku}")

        if "変動後" not in df.columns:
            st.warning("『変動後』列がないため、在庫推移グラフを表示できません。")
        else:
            df_sku = df[df["商品コード"] == selected_sku].copy()
            df_sku["日付"] = df_sku["元ファイル"].str.extract(r"(\d{8})")
            df_sku["日付"] = pd.to_datetime(df_sku["日付"], format="%Y%m%d", errors="coerce")
            df_plot = df_sku[["日付", "変動後"]].dropna().sort_values("日付")

            if df_plot.empty:
                st.warning("選択したSKUの在庫データがありません。")
            else:
                st.line_chart(df_plot.set_index("日付")["変動後"])
        st.markdown("---")

    # ==========================
    # 売上集計
    # ==========================
    if "更新理由" in df.columns:
        df_sales = df[df["更新理由"] == "受注取込"]
    else:
        df_sales = df.copy()

    agg_sales = df_sales.groupby("商品コード", dropna=False)["増減値"].sum()
    tbl = pd.DataFrame({
        "商品コード": agg_sales.index,
        "増減値合計": agg_sales.values,
    })
    tbl["売上個数合計"] = -tbl["増減値合計"]

# 現在庫（NaNや文字列をすべて0に正規化してから整数化）
if "変動後" in df.columns:
    stock = df.groupby("商品コード")["変動後"].last()
    stock = pd.to_numeric(stock, errors="coerce").fillna(0).astype(int)
    tbl["現在庫"] = stock.reindex(tbl["商品コード"]).fillna(0).astype(int)
else:
    tbl["現在庫"] = 0

    info_cols = ["商品基本コード", "商品名", "属性1名", "属性2名"]
    info = df_sales.groupby("商品コード", dropna=False)[info_cols].last().reset_index()
    merged = tbl.merge(info, on="商品コード", how="left")

    # 売上下限
    merged = merged[merged["売上個数合計"] >= min_total_sales]

    # 画像列
    img_master = load_image_master()
    base_url = "https://image.rakuten.co.jp/hype/cabinet"

    def to_img(code):
        key = str(code).strip()
        rel = img_master.get(key, "")
        if not rel:
            return ""
        return f'<img src="{base_url + rel}" width="70">'

    merged.insert(0, "画像", merged["商品基本コード"].apply(to_img))

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
    df_view = merged[display_cols].sort_values("売上個数合計", ascending=False)

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
/* 4:商品名（3行制限） */
.sku-table td:nth-child(4), .sku-table th:nth-child(4) {
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

    with tab1:
        st.write(
            f"📦 SKU数：{len(df_view):,} ｜ 集計期間：{start_date} ～ {end_date}"
        )
        st.markdown(make_html_table(df_view), unsafe_allow_html=True)

    with tab2:
        days = max((end_date - start_date).days + 1, 1)
        restock = merged.copy()
        restock["1日平均売上"] = (restock["売上個数合計"] / days).round(2)
        restock["目標在庫"] = (restock["1日平均売上"] * target_days).round()
        restock["発注推奨数"] = (restock["目標在庫"] - restock["現在庫"]).apply(
            lambda x: max(int(x), 0)
        )
        restock = restock[restock["発注推奨数"] > 0]
        restock = restock.sort_values("発注推奨数", ascending=False)

        if restock.empty:
            st.success("発注推奨の商品はありません。")
        else:
            cols2 = display_cols + ["1日平均売上", "目標在庫", "発注推奨数"]
            st.markdown(make_html_table(restock[cols2]), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
