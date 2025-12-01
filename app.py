import streamlit as st
import pandas as pd
import glob
import os
import html


@st.cache_data
def load_data(file_path: str):
    """指定されたファイルを読み込む"""
    sheets_dict = pd.read_excel(file_path, sheet_name=None)
    sheet_name = list(sheets_dict.keys())[0]
    df = sheets_dict[sheet_name]

    # 列名を少しだけ扱いやすくリネーム
    rename_map = {
        "商品番号.1": "SKU",
        "Unnamed: 9": "画像URL",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    return df, sheet_name


def make_img_tag(url: str, width: int = 120) -> str:
    """画像URLから <img> タグを生成（URLがなければ空文字）"""
    if isinstance(url, str) and url.startswith("http"):
        safe_url = html.escape(url, quote=True)
        return f'<img src="{safe_url}" width="{width}">'
    return ""


def df_to_html_table(df: pd.DataFrame) -> str:
    """DataFrame を HTMLテーブル文字列に変換（画像列はすでに <img> タグ前提）"""
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
                # 画像列はHTMLをそのまま
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
    st.set_page_config(page_title="在庫変動データビューア", layout="wide")
    st.title("在庫変動データビューア")

    # ================ フォルダ単位でファイルを探す ================
    # 直下のフォルダ配下にある「在庫変動データ*.xlsm」を全部拾う
    file_paths = sorted(glob.glob("*/在庫変動データ*.xlsm"))

    # フォルダ名 -> そのフォルダ内のファイル一覧
    folder_files = {}
    for path in file_paths:
        folder = os.path.basename(os.path.dirname(path))  # 例: "202511"
        folder_files.setdefault(folder, []).append(path)

    if not folder_files:
        st.error("サブフォルダ内に 在庫変動データ*.xlsm が見つかりません。構成を確認してください。")
        st.stop()

    # フォルダ名（=月）一覧をソート
    months = sorted(folder_files.keys())

    # ================ サイドバーで「月フォルダ」を選択 ================
    with st.sidebar:
        st.header("月の選択")
        selected_month = st.selectbox("表示する月フォルダ", months, index=len(months) - 1)

    # 選択されたフォルダの中で、一番新しいファイルを使う
    files_in_month = sorted(folder_files[selected_month])
    selected_file = files_in_month[-1]

    # ================ データ読み込み ================
    try:
        df, sheet_name = load_data(selected_file)
    except Exception as e:
        st.error(f"データ読み込みでエラー: {e}")
        st.stop()

    st.caption(f"フォルダ: {selected_month} / ファイル: {selected_file}（シート: {sheet_name}）")
    st.write(f"件数: {len(df):,} 件")

    # ================ フィルタ設定 ================
    with st.sidebar:
        st.header("絞り込み")
        keyword = st.text_input("商品番号 / SKU / 商品名で検索")

        rank_range = None
        if "ランキング" in df.columns:
            min_rank = int(df["ランキング"].min())
            max_rank = int(df["ランキング"].max())
            rank_range = st.slider(
                "ランキング範囲",
                min_rank,
                max_rank,
                (min_rank, min(min_rank + 49, max_rank)),
            )

        min_sales = 0
        if "売上個数" in df.columns:
            min_sales = st.number_input(
                "売上個数（絶対値）の下限", min_value=0, value=0, step=1
            )

    # ================ データ絞り込み ================
    filtered = df.copy()

    # キーワード検索
    if keyword:
        cols = [c for c in ["商品番号", "SKU", "商品名"] if c in filtered.columns]
        if cols:
            cond = False
            for c in cols:
                cond = cond | filtered[c].astype(str).str.contains(keyword, case=False)
            filtered = filtered[cond]

    # ランキング範囲
    if rank_range and "ランキング" in filtered.columns:
        filtered = filtered[
            (filtered["ランキング"] >= rank_range[0])
            & (filtered["ランキング"] <= rank_range[1])
        ]

    # 売上個数（絶対値）
    if "売上個数" in filtered.columns:
        filtered = filtered[filtered["売上個数"].abs() >= min_sales]

    # ================ テーブル表示（画像先頭 & 文字大きめ） ================
    st.subheader("一覧")

    # 表示したい基本列
    base_cols = [
        c for c in ["ランキング", "商品番号", "SKU", "商品名", "属性1名", "属性2名", "現在庫", "売上個数"]
        if c in filtered.columns
    ]

    # 画像URL列があれば画像列を先頭に
    if "画像URL" in filtered.columns:
        display_df = filtered[["画像URL"] + base_cols].copy()
        display_df.insert(0, "画像", display_df["画像URL"].apply(lambda u: make_img_tag(u, width=120)))
        display_df = display_df.drop(columns=["画像URL"])
    else:
        display_df = filtered[base_cols].copy()

    html_table = df_to_html_table(display_df)

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
