import streamlit as st
import pandas as pd
import glob
import os
import html


# ========= データ読み込み系 =========

@st.cache_data
def load_tempostar_data(file_paths):
    dfs = []
    for path in file_paths:
        df = pd.read_csv(path, encoding="cp932")
        df["元ファイル"] = os.path.basename(path)
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    for col in ["増減値", "変動後"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce").fillna(0).astype(int)

    return all_df


@st.cache_data
def load_image_master():
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
        sub = df[["商品管理番号（商品URL）", "商品画像パス1"]].copy()
        dfs.append(sub)

    if not dfs:
        return {}

    all_img = pd.concat(dfs, ignore_index=True)

    all_img["商品管理番号（商品URL）"] = all_img["商品管理番号（商品URL）"].astype(str).str.strip()
    all_img["商品画像パス1"] = all_img["商品画像パス1"].astype(str).str.strip()

    img_dict = dict(zip(all_img["商品管理番号（商品URL）"], all_img["商品画像パス1"]))
    return img_dict


# ========= HTMLテーブル描画 =========

def make_html_table(df: pd.DataFrame) -> str:
    thead_cells = "".join(f"<th>{html.escape(str(col))}</th>" for col in df.columns)
    thead = f"<thead><tr>{thead_cells}</tr></thead>"

    rows_html = []
    for _, row in df.iterrows():
        tds = []
        for col in df.columns:
            val = row[col]
            if col == "画像":
                tds.append(f"<td>{val}</td>")
            else:
                tds.append(f"<td>{html.escape(str(val))}</td>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")
    tbody = "<tbody>" + "".join(rows_html) + "</tbody>"

    return f"<table border='1' cellspacing='0' cellpadding='4'>{thead}{tbody}</table>"


# ========= メイン =========

def main():
    st.set_page_config(page_title="Tempostar SKU別売上集計（画像付き）", layout="wide")
    st.title("Tempostar 在庫変動データ - SKU別売上集計（商品画像付き）")

    # ===== CSV読み込み先をローカルパスに変更 =====
    BASE_DIR = r"C:\Users\ani\python_app\在庫ログ自動取得app"
    pattern = os.path.join(BASE_DIR, "tempostar_stock_*.csv")
    file_paths = sorted(glob.glob(pattern))

    if not file_paths:
        st.error(f"指定フォルダに tempostar_stock_*.csv が見つかりません。\nパス: {BASE_DIR}")
        st.stop()

    file_name_list = [os.path.basename(p) for p in file_paths]

    with st.sidebar:
        st.header("集計設定")
        st.code(BASE_DIR)

        selected_file_names = st.multiselect(
            "集計対象のCSVファイル（複数選択可）",
            file_name_list,
            default=file_name_list,
        )
        if not selected_file_names:
            st.warning("少なくとも1つCSVファイルを選択してください。")
            st.stop()

        selected_paths = [
            p for p in file_paths if os.path.basename(p) in selected_file_names
        ]

        for p in selected_paths:
            st.caption("・" + os.path.basename(p))

        keyword = st.text_input("商品コード / 商品基本コード / 商品名で検索")
        min_total_sales = st.number_input("売上個数合計（プラス）の下限", min_value=0, value=0, step=1)

    try:
        df_raw = load_tempostar_data(selected_paths)
    except Exception as e:
        st.error(f"CSV読み込みでエラーが発生: {e}")
        st.stop()

    st.write(f"読み込みファイル数: {len(selected_paths)} 件")
    st.write(f"明細行数: {len(df_raw):,} 行")

    df = df_raw.copy()

    if keyword:
        cond = False
        for col in ["商品コード", "商品基本コード", "商品名"]:
            if col in df.columns:
                cond = cond | df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    required = {"商品基本コード", "増減値"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error("必要列が不足: " + " / ".join(missing))
        st.stop()

    if "更新理由" in df.columns:
        df_sales = df[df["更新理由"] == "受注取込"].copy()
    else:
        df_sales = df.copy()

    keys = [c for c in ["商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"] if c in df_sales.columns]
    sales_grouped = df_sales.groupby(keys, dropna=False).agg({"増減値": "sum"}).reset_index()
    sales_grouped = sales_grouped.rename(columns={"増減値": "増減値合計"})
    sales_grouped["売上個数合計"] = -sales_grouped["増減値合計"]
    sales_grouped = sales_grouped[sales_grouped["売上個数合計"] > 0]

    if "変動後" in df.columns:
        stock = df.groupby(keys, dropna=False).agg({"変動後": "last"}).reset_index()
        stock = stock.rename(columns={"変動後": "現在庫"})
        sales_grouped = pd.merge(sales_grouped, stock, on=keys, how="left")

    if min_total_sales > 0:
        sales_grouped = sales_grouped[sales_grouped["売上個数合計"] >= min_total_sales]

    sales_grouped = sales_grouped.sort_values("売上個数合計", ascending=False)

    img_master = load_image_master()
    base_url = "https://image.rakuten.co.jp/hype/cabinet"

    def img_tag(row):
        code = str(row["商品基本コード"]).strip()
        rel = img_master.get(code, "")
        if not rel:
            return ""
        url = base_url + str(rel).strip()
        return f'<img src="{html.escape(url)}" width="120">'

    sales_grouped["画像"] = sales_grouped.apply(img_tag, axis=1)
    cols = sales_grouped.columns.tolist()
    cols.insert(0, cols.pop(cols.index("画像")))
    sales_grouped = sales_grouped[cols]

    view_cols = ["画像"] + [c for c in keys if c in sales_grouped.columns] + ["売上個数合計", "現在庫", "増減値合計"]
    df_view = sales_grouped[view_cols].copy()

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
