import streamlit as st
import pandas as pd
import glob
import os
import html
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


@st.cache_data
def load_data(file_paths):
    """指定されたTempostar CSVファイル群を読み込んで1つのDataFrameに結合"""
    dfs = []
    for path in file_paths:
        df = pd.read_csv(path, encoding="cp932")
        df["元ファイル"] = os.path.basename(path)
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    # 数値列を明示的に変換
    for col in ["増減値", "変動後"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce").fillna(0).astype(int)

    return all_df


@st.cache_data
def fetch_image_map(basic_codes):
    """
    商品基本コードのリストから
    code -> 画像URL の辞書をまとめて取得する（並列で高速化＆キャッシュ）
    """
    # ユニーク化して無駄なリクエスト削減
    unique_codes = sorted(
        set(str(c) for c in basic_codes if isinstance(c, str) and c.strip() != "")
    )

    def fetch_one(code):
        page_url = f"https://item.rakuten.co.jp/hype/{code}/"
        try:
            resp = requests.get(page_url, timeout=5)
        except Exception:
            return code, ""

        if resp.status_code != 200:
            return code, ""

        html_text = resp.text

        marker = '<span class="sale_desc">'
        start_pos = html_text.find(marker)
        if start_pos == -1:
            return code, ""

        img_pos = html_text.find("<img", start_pos)
        if img_pos == -1:
            return code, ""

        src_marker = 'src="'
        src_start = html_text.find(src_marker, img_pos)
        if src_start == -1:
            return code, ""
        src_start += len(src_marker)
        src_end = html_text.find('"', src_start)
        if src_end == -1:
            return code, ""

        img_url = html_text[src_start:src_end]
        return code, img_url or ""

    results = {}
    # 並列で一気に取得（ワーカー数は環境に合わせて調整可能）
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_one, code): code for code in unique_codes}
        for fut in as_completed(futures):
            code, url = fut.result()
            if url:
                results[code] = url

    return results


def make_html_table(df: pd.DataFrame) -> str:
    """DataFrame をシンプルな HTML テーブル文字列に変換"""
    # ヘッダー
    thead_cells = "".join(f"<th>{html.escape(str(col))}</th>" for col in df.columns)
    thead = f"<thead><tr>{thead_cells}</tr></thead>"

    # 本体
    rows_html = []
    for _, row in df.iterrows():
        tds = []
        for col in df.columns:
            val = row[col]
            if col == "画像":
                # 画像列はHTMLそのまま
                tds.append(f"<td>{val}</td>")
            else:
                tds.append(f"<td>{html.escape(str(val))}</td>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")
    tbody = "<tbody>" + "".join(rows_html) + "</tbody>"

    table = f"""
    <table border="1" cellspacing="0" cellpadding="4">
        {thead}
        {tbody}
    </table>
    """
    return table


def main():
    st.set_page_config(page_title="Tempostar SKU別売上集計（画像付き）", layout="wide")
    st.title("Tempostar 在庫変動データ - SKU別売上集計（商品画像付き）")

    # ================ 対象CSVファイルの取得 ================
    file_paths = sorted(glob.glob("tempostar_stock_*.csv"))

    if not file_paths:
        st.error("tempostar_stock_*.csv が見つかりません。\napp.py と同じフォルダに CSV を置いてください。")
        st.stop()

    file_name_list = [os.path.basename(p) for p in file_paths]

    # ================ サイドバー：対象ファイル選択 & フィルタ ================
    with st.sidebar:
        st.header("集計設定")

        default_files = [file_name_list[-1]]  # デフォルトは最新CSVだけ
        selected_file_names = st.multiselect(
            "集計対象のCSVファイル（複数選択可）",
            file_name_list,
            default=default_files,
        )

        if not selected_file_names:
            st.warning("少なくとも1つCSVファイルを選択してください。")
            st.stop()

        selected_paths = [p for p in file_paths if os.path.basename(p) in selected_file_names]

        # キーワード絞り込み（集計前）
        keyword = st.text_input("商品コード / 商品基本コード / 商品名で検索")

        # 売上個数合計の下限（プラス表示）
        min_total_sales = st.number_input(
            "売上個数合計（プラス値）の下限",
            min_value=0,
            value=0,
            step=1,
        )

    # ================ データ読み込み ================
    try:
        df_raw = load_data(selected_paths)
    except Exception as e:
        st.error(f"CSV読み込みでエラーが発生しました: {e}")
        st.stop()

    st.caption("読み込みファイル")
    for name in selected_file_names:
        st.caption(f"・{name}")
    st.write(f"明細行数: {len(df_raw):,} 行")

    df = df_raw.copy()

    # ================ 明細レベルでのキーワード絞り込み ================
    if keyword:
        cond = False
        for col in ["商品コード", "商品基本コード", "商品名"]:
            if col in df.columns:
                cond = cond | df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    # 必須列チェック
    if not {"商品コード", "商品基本コード", "増減値"}.issubset(df.columns):
        st.error("商品コード / 商品基本コード / 増減値 のいずれかの列がCSVにありません。項目名を確認してください。")
        st.stop()

    # ================ 売上用データ（更新理由＝受注取込のみ） ================
    if "更新理由" in df.columns:
        df_sales = df[df["更新理由"] == "受注取込"].copy()
    else:
        # 更新理由列がない場合は全行を売上として扱う（保険）
        df_sales = df.copy()

    # ================ SKU別売上集計 ================
    sales_group_keys = []
    for c in ["商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"]:
        if c in df_sales.columns:
            sales_group_keys.append(c)

    agg_sales = {
        "増減値": "sum",  # マイナスが大きいほど売れている
    }

    sales_grouped = df_sales.groupby(sales_group_keys, dropna=False).agg(agg_sales).reset_index()
    sales_grouped = sales_grouped.rename(columns={"増減値": "増減値合計"})

    # 表示用「売上個数合計」＝ マイナスを反転してプラスに
    sales_grouped["売上個数合計"] = -sales_grouped["増減値合計"]

    # 売れていない（0以下）は除外
    sales_grouped = sales_grouped[sales_grouped["売上個数合計"] > 0]

    # ================ 在庫情報（現在庫）を別途集計（全更新理由対象） ================
    if "変動後" in df.columns:
        stock_group_keys = []
        for c in ["商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"]:
            if c in df.columns:
                stock_group_keys.append(c)

        agg_stock = {
            "変動後": "last",  # 最後の変動後在庫
        }
        stock_group = df.groupby(stock_group_keys, dropna=False).agg(agg_stock).reset_index()
        stock_group = stock_group.rename(columns={"変動後": "現在庫"})

        sales_grouped = pd.merge(
            sales_grouped,
            stock_group,
            on=stock_group_keys,
            how="left",
        )

    # ================ 売上個数合計の下限フィルタ & 並べ替え ================
    if min_total_sales > 0:
        sales_grouped = sales_grouped[sales_grouped["売上個数合計"] >= min_total_sales]

    sales_grouped = sales_grouped.sort_values("売上個数合計", ascending=False)

    # ================ 画像URLをまとめて取得 → 画像列を先頭に ================
    # 商品基本コード一覧から画像URLマップを一括取得（並列＋キャッシュ）
    code_list = sales_grouped["商品基本コード"].astype(str).tolist()
    image_map = fetch_image_map(code_list)

    # HTML <img> タグに変換
    def code_to_img_tag(code):
        url = image_map.get(str(code), "")
        if not url:
            return ""
        safe = html.escape(url, quote=True)
        return f'<img src="{safe}" width="120">'

    sales_grouped["画像"] = sales_grouped["商品基本コード"].apply(code_to_img_tag)

    # 画像列を先頭へ
    cols = sales_grouped.columns.tolist()
    cols.insert(0, cols.pop(cols.index("画像")))
    sales_grouped = sales_grouped[cols]

    # ================ 表示列の並び ================
    display_cols = ["画像"]
    for c in ["商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"]:
        if c in sales_grouped.columns:
            display_cols.append(c)
    for c in ["売上個数合計", "現在庫", "増減値合計"]:
        if c in sales_grouped.columns:
            display_cols.append(c)

    df_view = sales_grouped[display_cols].copy()

    st.write(f"SKU数（売上個数合計 > 0）: {len(df_view):,} 件")
    st.caption("※ 画像URLは商品基本コードごとに並列取得し、キャッシュしています")

    # ================ HTMLテーブルで表示 ================
    html_table = make_html_table(df_view)

    st.markdown(
        """
        <style>
        table {
            border-collapse: collapse;
            font-size: 14px;
        }
        th {
            background-color: #f2f2f2;
            font-size: 14px;
        }
        td, th {
            padding: 6px 8px;
            border: 1px solid #ccc;
        }
        tr:hover {
            background-color: #f9f9f9;
        }
        img {
            display: block;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(html_table, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
