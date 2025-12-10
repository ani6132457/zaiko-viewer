import streamlit as st
import pandas as pd
import glob
import os
import html
import re
import math
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

    merged = pd.concat(dfs, ignore_index=True)
    merged["商品管理番号（商品URL）"] = merged["商品管理番号（商品URL）"].astype(str).strip()
    merged["商品画像パス1"] = merged["商品画像パス1"].astype(str).strip()

    return dict(zip(merged["商品管理番号（商品URL）"], merged["商品画像パス1"]))


# ==========================
# HTML テーブル生成
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
                tds.append(
                    f"<td><a href='?sku={code}' style='color:#0073e6; text-decoration:none;'>{code}</a></td>"
                )
            elif col == "画像":
                tds.append(f"<td>{val}</td>")
            else:
                tds.append(f"<td>{html.escape(str(val))}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    return f"""
    <table class="sku-table">
        {thead}<tbody>{"".join(body_rows)}</tbody>
    </table>
    """


# ==========================
# Main
# ==========================
def main():
    st.set_page_config(page_title="Tempostar 売上集計", layout="wide")
    st.title("Tempostar 在庫変動データ - SKU集計")

    # CSV取得
    raw_paths = sorted(glob.glob("tempostar_stock_*.csv"))
    if not raw_paths:
        st.error("tempostar_stock_*.csv がありません")
        return

    file_infos = []
    pat = re.compile(r"tempostar_stock_(\d{8})")
    for path in raw_paths:
        m = pat.search(os.path.basename(path))
        if m:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
            file_infos.append({"date": d, "path": path})

    all_dates = sorted({fi["date"] for fi in file_infos})
    min_date, max_date = min(all_dates), max(all_dates)
    years = sorted({d.year for d in all_dates})

    # 初期値
    one_month_ago = max_date - pd.DateOffset(months=1)
    one_month_ago = max(min_date, one_month_ago.date())

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
    # Sidebar
    # ==========================
    with st.sidebar:
        st.header("集計条件")
        st.caption(f"📅データ期間：{min_date} ～ {max_date}")

        with st.form("filter_form"):
            # 日付入力
            y1, m1, d1 = st.columns(3)
            s_y = y1.selectbox("開始年", years, index=years.index(f["start_date"].year))
            s_m = m1.selectbox("開始月", sorted({d.month for d in all_dates}), index=f["start_date"].month-1)
            s_d = d1.selectbox("開始日", sorted({d.day for d in all_dates}), index=0)

            y2, m2, d2 = st.columns(3)
            e_y = y2.selectbox("終了年", years, index=years.index(f["end_date"].year))
            e_m = m2.selectbox("終了月", sorted({d.month for d in all_dates}), index=f["end_date"].month-1)
            e_d = d2.selectbox("終了日", sorted({d.day for d in all_dates}), index=len(sorted({d.day for d in all_dates}))-1)

            keyword = st.text_input("検索ワード", f["keyword"])
            min_total = st.number_input("売上個数下限", 0, value=f["min_total_sales"])
            target_days = st.number_input("安全在庫（日数）", 1, 365, value=f["target_days"])
            submitted = st.form_submit_button("この条件で表示")

        if submitted:
            f.update({
                "start_date": date(s_y, s_m, s_d),
                "end_date": date(e_y, e_m, e_d),
                "keyword": keyword,
                "min_total_sales": min_total,
                "target_days": target_days,
                "submitted": True,
            })

    if not f["submitted"]:
        st.info("条件を設定して『この条件で表示』を押してください")
        return

    start_date, end_date = f["start_date"], f["end_date"]
    keyword, min_total_sales, target_days = f["keyword"], f["min_total_sales"], f["target_days"]

    paths = [fi["path"] for fi in file_infos if start_date <= fi["date"] <= end_date]
    df = load_tempostar_data(paths)

    # ==========================
    # キーワード絞り込み他
    # ==========================
    if keyword:
        cond = False
        for c in ["商品コード", "商品名"]:
            cond |= df[c].astype(str).str.contains(keyword)
        df = df[cond]

    df_sales = df[df["更新理由"] == "受注取込"] if "更新理由" in df else df
    agg = df_sales.groupby("商品コード")["増減値"].sum()
    tbl = pd.DataFrame({
        "商品コード": agg.index,
        "売上個数合計": -agg.values,
    })
    # 最新在庫
    stock = df.groupby("商品コード")["変動後"].last().fillna(0).astype(int)
    tbl["現在庫"] = stock.reindex(tbl["商品コード"]).fillna(0).astype(int)

    # 商品情報付与
    info_cols = ["商品基本コード", "商品名", "属性1名", "属性2名"]
    info = df_sales.groupby("商品コード")[info_cols].last()
    merged = tbl.merge(info, on="商品コード", how="left")
    merged = merged[merged["売上個数合計"] >= min_total_sales]

    # 画像列付与
    img_master = load_image_master()
    base_url = "https://image.rakuten.co.jp/hype/cabinet"
    merged.insert(0, "画像", merged["商品基本コード"].apply(lambda c: f'<img src="{base_url + img_master.get(str(c), "")}" width="70">'))

    display = [
        "画像", "商品コード", "商品基本コード",
        "商品名", "属性1名", "属性2名",
        "売上個数合計", "現在庫"
    ]

    df_view = merged[display].sort_values("売上個数合計", ascending=False)

    # ==========================
    # CSS（列幅＆3行制限）
    # ==========================
    st.markdown("""
<style>
.sku-table { border-collapse:collapse; font-size:13px; width:100%; }
.sku-table th { background:#f2f2f2; }
.sku-table td, .sku-table th { padding:4px 6px; border:1px solid #ccc; vertical-align:top; }
.sku-table tbody tr:hover { background:#fafafa; }
.sku-table img { max-height:70px; width:auto; display:block; margin:auto; }

/* 画像 */
.sku-table td:nth-child(1), .sku-table th:nth-child(1) {
    width:72px; text-align:center;
}
/* 商品コード/基本コード */
.sku-table td:nth-child(2), .sku-table th:nth-child(2),
.sku-table td:nth-child(3), .sku-table th:nth-child(3) {
    width:110px; white-space:nowrap;
}
/* 商品名（横広く＋3行制限） */
.sku-table td:nth-child(4), .sku-table th:nth-child(4) {
    max-width:420px;
    display:-webkit-box;
    -webkit-line-clamp:3;
    -webkit-box-orient:vertical;
    overflow:hidden;
}
/* 属性 */
.sku-table td:nth-child(5), .sku-table th:nth-child(5),
.sku-table td:nth-child(6), .sku-table th:nth-child(6) {
    width:110px; white-space:nowrap;
}
/* 数値列 */
.sku-table td:nth-child(7), .sku-table th:nth-child(7),
.sku-table td:nth-child(8), .sku-table th:nth-child(8) {
    width:80px; text-align:right; white-space:nowrap;
}

/* Sticky header */
.sku-table thead th {
    position:sticky;
    top:3.2rem;
    z-index:2;
    background:#f2f2f2;
}
</style>
""", unsafe_allow_html=True)

    # ==========================
    # タブ表示
    # ==========================
    tab1, tab2 = st.tabs(["SKU別売上集計", "在庫少商品（発注目安）"])

    with tab1:
        st.write(f"📦 SKU数:{len(df_view)} ｜ {start_date}〜{end_date}")
        st.markdown(make_html_table(df_view), unsafe_allow_html=True)

    with tab2:
        days = max((end_date-start_date).days+1, 1)
        restock = merged.copy()
        restock["1日平均売上"] = (restock["売上個数合計"]/days).round(2)
        restock["目標在庫"] = (restock["1日平均売上"]*target_days).round()
        restock["発注推奨数"] = (restock["目標在庫"]-restock["現在庫"]).apply(lambda x:max(int(x),0))
        restock = restock[restock["発注推奨数"]>0]
        restock = restock.sort_values("発注推奨数",ascending=False)

        if restock.empty:
            st.success("発注推奨商品はありません！")
        else:
            cols2 = display + ["1日平均売上","目標在庫","発注推奨数"]
            st.markdown(make_html_table(restock[cols2]), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
