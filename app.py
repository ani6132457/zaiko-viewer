import streamlit as st
import pandas as pd
import glob
import os
import html


@st.cache_data
def load_tempostar_data(file_paths):
    """選択されたCSVファイルをすべて読み込んで結合"""
    dfs = []
    for path in file_paths:
        df = pd.read_csv(path, encoding="cp932")
        df["元ファイル"] = os.path.basename(path)
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    # 数値列の変換
    for col in ["増減値", "変動後"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce").fillna(0).astype(int)

    return all_df


@st.cache_data
def load_image_master():
    """同一フォルダ内の「商品画像URLマスタ」から画像マップを作成"""
    master_folder = "商品画像URLマスタ"
    pattern = os.path.join(master_folder, "*.csv")
    paths = glob.glob(pattern)

    if not paths:
        return {}

    dfs = []
    for p in paths:
        df = pd.read_csv(p, encoding="cp932")
        if "商品管理番号（商品URL）" not in df.columns or "商品画像パス1" not in df.columns:
            continue
        dfs.append(df)

    if not dfs:
        return {}

    img_df = pd.concat(dfs, ignore_index=True)

    # トリム
    img_df["商品管理番号（商品URL）"] = img_df["商品管理番号（商品URL）"].astype(str).str.strip()
    img_df["商品画像パス1"] = img_df["商品画像パス1"].astype(str).str.strip()

    return dict(zip(img_df["商品管理番号（商品URL）"], img_df["商品画像パス1"]))


def make_html_table(df):
    thead = "<thead><tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns) + "</tr></thead>"
    tbody_rows = []
    for _, row in df.iterrows():
        tds = []
        for c in df.columns:
            val = row[c]
            if c == "画像":
                tds.append(f"<td>{val}</td>")
            else:
                tds.append(f"<td>{html.escape(str(val))}</td>")
        tbody_rows.append("<tr>" + "".join(tds) + "</tr>")
    tbody = "<tbody>" + "".join(tbody_rows) + "</tbody>"
    return f"<table border='1' cellspacing='0' cellpadding='4'>{thead}{tbody}</table>"


def main():
    st.set_page_config(page_title="Tempostar 在庫ビューア", layout="wide")
    st.title("Tempostar 在庫変動集計（画像付き）")

    # ←★ ここが変更点：同一フォルダ内を探索
    BASE_DIR = os.getcwd()
    pattern = os.path.join(BASE_DIR, "tempostar_stock_*.csv")
    file_paths = sorted(glob.glob(pattern))

    if not file_paths:
        st.error("tempostar_stock_*.csv をこのフォルダに置いてください。")
        st.stop()

    file_names = [os.path.basename(p) for p in file_paths]

    with st.sidebar:
        st.header("集計設定")
        st.caption("CSV検索パス：")
        st.code(BASE_DIR)

        selected_names = st.multiselect(
            "集計対象CSV（複数選択可）",
            file_names,
            default=file_names,  # ←全部合算が標準
        )
        if not selected_names:
            st.stop()

        target_paths = [p for p in file_paths if os.path.basename(p) in selected_names]

        keyword = st.text_input("キーワード検索（商品名等）")
        min_sales = st.number_input("売上個数合計の下限", min_value=0, value=0)

    df = load_tempostar_data(target_paths)

    st.write(f"読み込みファイル数: {len(target_paths)} 件")
    st.write(f"明細行数: {len(df):,}")

    if keyword:
        col_ok = ["商品コード", "商品基本コード", "商品名"]
        cond = False
        for c in col_ok:
            if c in df.columns:
                cond |= df[c].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    if "更新理由" in df.columns:
        df_sales = df[df["更新理由"] == "受注取込"].copy()
    else:
        df_sales = df.copy()

    keys = [c for c in ["商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"] if c in df_sales.columns]
    grouped = df_sales.groupby(keys).agg({"増減値": "sum"}).reset_index()
    grouped = grouped.rename(columns={"増減値": "増減値合計"})
    grouped["売上個数合計"] = -grouped["増減値合計"]
    grouped = grouped[grouped["売上個数合計"] > 0]

    if "変動後" in df.columns:
        stock = df.groupby(keys).agg({"変動後": "last"}).reset_index()
        stock = stock.rename(columns={"変動後": "現在庫"})
        grouped = pd.merge(grouped, stock, on=keys, how="left")

    if min_sales > 0:
        grouped = grouped[grouped["売上個数合計"] >= min_sales]

    grouped = grouped.sort_values("売上個数合計", ascending=False)

    img_master = load_image_master()
    BASE_IMG = "https://image.rakuten.co.jp/hype/cabinet"

    def img(row):
        code = str(row["商品基本コード"]).strip()
        path = img_master.get(code, "")
        if not path:
            return ""
        return f'<img src="{BASE_IMG}{path}" width="120">'

    grouped["画像"] = grouped.apply(img, axis=1)
    cols = ["画像"] + keys + ["売上個数合計", "現在庫", "増減値合計"]
    df_view = grouped[cols]

    st.write(f"SKU数: {len(df_view):,}")

    html_table = make_html_table(df_view)
    st.markdown("""
        <style>
        table {border-collapse: collapse; font-size: 14px;}
        th {background: #f2f2f2;}
        td, th {border: 1px solid #ccc; padding: 4px 6px;}
        tr:hover {background: #fafafa;}
        img {display: block;}
        </style>
    """, unsafe_allow_html=True)
    st.markdown(html_table, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
