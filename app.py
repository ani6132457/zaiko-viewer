import streamlit as st
import pandas as pd
import glob
import os
import html


@st.cache_data
def load_data(file_paths):
    """指定されたTempostar CSVファイル群を読み込んで1つのDataFrameに結合"""
    dfs = []
    for path in file_paths:
        df = pd.read_csv(path, encoding="cp932")
        df["元ファイル"] = os.path.basename(path)
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    # 念のため数値型に変換（増減値が文字列になっても壊れないように）
    all_df["増減値"] = pd.to_numeric(all_df["増減値"], errors="coerce").fillna(0).astype(int)
    all_df["変動後"] = pd.to_numeric(all_df["変動後"], errors="coerce").fillna(0).astype(int)

    return all_df


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
    st.set_page_config(page_title="Tempostar SKU別売上集計", layout="wide")
    st.title("Tempostar 在庫変動データ - SKU別売上集計")

    # ================ 対象CSVファイルの取得 ================
    file_paths = sorted(glob.glob("tempostar_stock_*.csv"))

    if not file_paths:
        st.error("tempostar_stock_*.csv が見つかりません。\napp.py と同じフォルダに CSV を置いてください。")
        st.stop()

    file_name_list = [os.path.basename(p) for p in file_paths]

    # ================ サイドバー：対象ファイル選択 & フィルタ設定 ================
    with st.sidebar:
        st.header("集計設定")

        # 対象とするCSVファイル（複数選択可、デフォルトは一番新しいファイルだけ）
        default_files = [file_name_list[-1]]
        selected_file_names = st.multiselect(
            "集計対象のCSVファイル（複数選択可）",
            file_name_list,
            default=default_files,
        )

        if not selected_file_names:
            st.warning("少なくとも1つCSVファイルを選択してください。")
            st.stop()

        # 選択されたファイル名からパスを復元
        selected_paths = [
            p for p in file_paths if os.path.basename(p) in selected_file_names
        ]

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

    # ================ 明細レベルでの絞り込み（キーワード） ================
    df = df_raw.copy()

    if keyword:
        cond = False
        for col in ["商品コード", "商品基本コード", "商品名"]:
            if col in df.columns:
                cond = cond | df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    # ================ SKU別集計（ここがメイン） ================
    # 必須列チェック
    if not {"商品コード", "増減値"}.issubset(df.columns):
        st.error("商品コード または 増減値 の列がCSVにありません。項目名を確認してください。")
        st.stop()

    # グループキー（SKU＋商品情報）
    group_keys = []
    for c in ["商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"]:
        if c in df.columns:
            group_keys.append(c)

    # 増減値は「マイナス = 出荷（売れている）」前提
    # まず単純に増減値を合計し、その後符号を反転して「売上個数合計（プラス）」を作る
    agg_dict = {
        "増減値": "sum",        # 合計（マイナスが大きいほど売れている）
        "変動後": "last",       # 最後の変動後 在庫数
    }
    if "元ファイル" in df.columns:
        agg_dict["元ファイル"] = pd.Series.nunique  # 何ファイル分か（＝日数などの目安）

    grouped = df.groupby(group_keys, dropna=False).agg(agg_dict).reset_index()

    # 元の合計値（マイナス）の列名調整
    grouped = grouped.rename(columns={"増減値": "増減値合計", "変動後": "現在庫"})

    # 表示用の「売上個数合計」はマイナスを反転してプラスにする
    grouped["売上個数合計"] = -grouped["増減値合計"]

    # 売れていない（合計0以下）は基本除外
    grouped = grouped[grouped["売上個数合計"] > 0]

    # 下限フィルタ（プラスの数値で指定）
    if min_total_sales > 0:
        grouped = grouped[grouped["売上個数合計"] >= min_total_sales]

    # 売上個数合計の降順に並べ替え（よく売れている順）
    grouped = grouped.sort_values("売上個数合計", ascending=False)

    # 表示用列の順番
    display_cols = []

    for c in ["商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"]:
        if c in grouped.columns:
            display_cols.append(c)

    for c in ["売上個数合計", "現在庫", "増減値合計", "元ファイル"]:
        if c in grouped.columns:
            display_cols.append(c)

    df_view = grouped[display_cols].copy()

    st.write(f"SKU数（売上個数合計 > 0）: {len(df_view):,} 件")

    # ================ HTMLテーブルで表示（文字大きめ） ================
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
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(html_table, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
