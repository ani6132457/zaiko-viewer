import streamlit as st
import pandas as pd
import glob
import os
import html
import re
from datetime import datetime


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

    # 数値列
    for col in ["増減値", "変動後"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce").fillna(0).astype(int)

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
    merged["商品管理番号（商品URL）"] = merged["商品管理番号（商品URL）"].astype(str).str.strip()
    merged["商品画像パス1"] = merged["商品画像パス1"].astype(str).str.strip()

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
            v = row[col]
            if col == "画像":
                tds.append(f"<td>{v}</td>")
            else:
                tds.append(f"<td>{html.escape(str(v))}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    tbody = "<tbody>" + "".join(body_rows) + "</tbody>"

    return f"""
    <table border="1" cellspacing="0" cellpadding="4">
        {thead}{tbody}
    </table>
    """


# ==========================
# Main
# ==========================
def main():
    st.set_page_config(page_title="Tempostar 売上集計（画像付き）", layout="wide")

    st.title("Tempostar 在庫変動データ - SKU別売上集計（商品画像付き）")

    # ---------- CSV一覧 ----------
    raw_paths = sorted(glob.glob("tempostar_stock_*.csv"))
    if not raw_paths:
        st.error("tempostar_stock_*.csv がありません。")
        return

    # ファイル名から日付抽出
    file_infos = []
    pat = re.compile(r"tempostar_stock_(\d{8})")

    for path in raw_paths:
        name = os.path.basename(path)
        m = pat.search(name)
        if m:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
            file_infos.append({"date": d, "path": path, "name": name})

    if not file_infos:
        st.error("tempostar_stock_YYYYMMDD.csv の形式が見つかりません。")
        return

    all_dates = sorted({fi["date"] for fi in file_infos})
    min_date, max_date = min(all_dates), max(all_dates)

    # ---------- サイドバー ----------
    with st.sidebar:
        st.header("集計条件")

        st.write(f"📅 データ期間： **{min_date} 〜 {max_date}**")

        # ★ カレンダーの代わりに「日付プルダウン（数字のみ）」を使う ★
        start_date = st.selectbox(
            "集計開始日",
            options=all_dates,
            index=len(all_dates) - 1,
            format_func=lambda d: d.strftime("%Y/%m/%d"),
        )

        end_date = st.selectbox(
            "集計終了日",
            options=all_dates,
            index=len(all_dates) - 1,
            format_func=lambda d: d.strftime("%Y/%m/%d"),
        )

        if start_date > end_date:
            st.warning("開始日が終了日より後になっています。自動で並び替えます。")
            start_date, end_date = end_date, start_date

        # 期間内 CSV
        target = [fi for fi in file_infos if start_date <= fi["date"] <= end_date]
        if not target:
            st.error("選択範囲のCSVがありません。")
            return

        paths = [fi["path"] for fi in target]

        st.caption("対象CSV：")
        for fi in target:
            st.caption(f"・{fi['date']} : {fi['name']}")

        keyword = st.text_input("検索（商品コード / 商品基本コード / 商品名）")
        min_total_sales = st.number_input(
            "売上個数の下限（プラス値）", min_value=0, value=0
        )

    # ---------- CSV読込 ----------
    df = load_tempostar_data(paths)

    if keyword:
        cond = False
        for col in ["商品コード", "商品基本コード", "商品名"]:
            if col in df.columns:
                cond |= df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    # 必須列
    required = {"商品コード", "商品基本コード", "増減値"}
    if not required.issubset(df.columns):
        st.error("Tempostar CSV に必要な列が不足しています。")
        return

    # ---------- 売上（受注取込のみ） ----------
    if "更新理由" in df.columns:
        df_sales = df[df["更新理由"] == "受注取込"]
    else:
        df_sales = df.copy()

    agg_sales = {
        "商品基本コード": "last",
        "商品名": "last",
        "属性1名": "last",
        "属性2名": "last",
        "増減値": "sum",
    }

    sales_grouped = (
        df_sales.groupby("商品コード", dropna=False)
        .agg(agg_sales)
        .reset_index()
        .rename(columns={"増減値": "増減値合計"})
    )

    sales_grouped["売上個数合計"] = -sales_grouped["増減値合計"]
    sales_grouped = sales_grouped[sales_grouped["売上個数合計"] > 0]

    # ---------- 在庫 ----------
    if "変動後" in df.columns:
        stock_group = (
            df.groupby("商品コード")
            .agg({"変動後": "last"})
            .reset_index()
            .rename(columns={"変動後": "現在庫"})
        )
        sales_grouped = sales_grouped.merge(stock_group, on="商品コード", how="left")

    # ---------- フィルタ ----------
    if min_total_sales > 0:
        sales_grouped = sales_grouped[sales_grouped["売上個数合計"] >= min_total_sales]

    sales_grouped = sales_grouped.sort_values("売上個数合計", ascending=False)

    # ---------- 画像 ----------
    img_master = load_image_master()
    base_url = "https://image.rakuten.co.jp/hype/cabinet"

    def to_img(row):
        code = str(row["商品基本コード"]).strip()
        rel = img_master.get(code, "")
        if not rel:
            return ""
        return f'<img src="{base_url + rel}" width="120">'

    sales_grouped["画像"] = sales_grouped.apply(to_img, axis=1)
    cols = sales_grouped.columns.tolist()
    cols.insert(0, cols.pop(cols.index("画像")))
    sales_grouped = sales_grouped[cols]

    # ---------- 表示 ----------
    display = [
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

    df_view = sales_grouped[display]

    st.write(
        f"📦 SKU数：{len(df_view):,}　｜　集計期間：{start_date.strftime('%Y/%m/%d')} 〜 {end_date.strftime('%Y/%m/%d')}"
    )

    # テーブル表示（HTML）
    table_html = make_html_table(df_view)

    st.markdown(
        """
    <style>
    table { border-collapse: collapse; font-size: 14px; }
    th { background:#f2f2f2; }
    td, th { padding:6px 8px; border:1px solid #ccc; }
    tr:hover { background:#fafafa; }
    img { display:block; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    st.markdown(table_html, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
