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


def first_non_null(series: pd.Series):
    """最初の非NaN要素を返す（画像URLなどに使用）"""
    s = series.dropna()
    if len(s) == 0:
        return None
    return s.iloc[0]


def main():
    st.set_page_config(page_title="在庫変動データビューア", layout="wide")
    st.title("在庫変動データビューア")

    # ================ フォルダ単位でファイルを探す ================
    # app.py と同じ階層のサブフォルダ配下にある「在庫変動データ*.xlsm」を全部拾う
    # 例: 202511/在庫変動データ20251101.xlsm など
    file_paths = sorted(glob.glob("*/在庫変動データ*.xlsm"))

    # フォルダ名 -> そのフォルダ内のファイル一覧
    folder_files = {}
    for path in file_paths:
        folder = os.path.basename(os.path.dirname(path))  # 例: "202511"
        folder_files.setdefault(folder, []).append(path)

    if not folder_files:
        st.error("サブフォルダ内に 在庫変動データ*.xlsm が見つかりません。フォルダ構成を確認してください。")
        st.stop()

    # フォルダ名（=月）一覧をソート
    months = sorted(folder_files.keys())

    # ================ サイドバー：月フォルダを複数選択 ================
    with st.sidebar:
        st.header("月の選択")
        default_month = [months[-1]]  # デフォルトは最新月だけ
        selected_months = st.multiselect(
            "表示・集計する月フォルダ（複数選択可）",
            months,
            default=default_month,
        )

    if not selected_months:
        st.warning("少なくとも1つ月フォルダを選択してください。")
        st.stop()

    # ================ 選択された複数月のデータを読み込み＆合算 ================
    all_dfs = []
    sheet_info = []

    for month in selected_months:
        files_in_month = sorted(folder_files[month])
        selected_file = files_in_month[-1]  # その月フォルダ内の最新ファイルを使用

        try:
            df_month, sheet_name = load_data(selected_file)
        except Exception as e:
            st.error(f"データ読み込みでエラー（フォルダ: {month} / ファイル: {selected_file}）: {e}")
            st.stop()

        df_month = df_month.copy()
        df_month["月フォルダ"] = month  # どの月か分かるように列追加

        all_dfs.append(df_month)
        sheet_info.append(f"{month}: {os.path.basename(selected_file)}（シート: {sheet_name}）")

    df = pd.concat(all_dfs, ignore_index=True)

    st.caption("読み込みファイル一覧")
    for info in sheet_info:
        st.caption("・" + info)
    st.write(f"合算件数: {len(df):,} 件（{len(selected_months)}か月分）")

    # ================ フィルタ設定（明細・集計共通） ================
    with st.sidebar:
        st.header("絞り込み")
        keyword = st.text_input("商品番号 / SKU / 商品名で検索")

        rank_range = None
        if "ランキング" in df.columns:
            try:
                min_rank = int(df["ランキング"].min())
                max_rank = int(df["ランキング"].max())
                rank_range = st.slider(
                    "ランキング範囲",
                    min_rank,
                    max_rank,
                    (min_rank, min(min_rank + 49, max_rank)),
                )
            except ValueError:
                rank_range = None

        min_sales = 0
        if "売上個数" in df.columns:
            min_sales = st.number_input(
                "売上個数（絶対値）の下限", min_value=0, value=0, step=1
            )

    # ================ データ絞り込み（明細ベース） ================
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

    # 売上個数（絶対値）フィルタ
    if "売上個数" in filtered.columns:
        filtered = filtered[filtered["売上個数"].abs() >= min_sales]

    # ================ SKU別集計（ここが今回のメイン） ================
    df_agg = None
    if {"SKU", "売上個数"}.issubset(filtered.columns):
        # グループキー（SKU＋商品情報）
        group_keys = []
        for c in ["SKU", "商品番号", "商品名", "属性1名", "属性2名"]:
            if c in filtered.columns:
                group_keys.append(c)

        agg_dict = {
            "売上個数": "sum",  # ネットの合計個数（返品がマイナスで入る場合は差し引き後）
        }
        if "現在庫" in filtered.columns:
            agg_dict["現在庫"] = "last"
        if "画像URL" in filtered.columns:
            agg_dict["画像URL"] = first_non_null
        if "月フォルダ" in filtered.columns:
            agg_dict["月フォルダ"] = pd.Series.nunique  # 何か月分か

        grouped = filtered.groupby(group_keys, dropna=False).agg(agg_dict).reset_index()

        # 列名調整
        grouped = grouped.rename(
            columns={
                "売上個数": "売上個数合計",
                "月フォルダ": "集計月数",
            }
        )

        # 表示用列の順番
        agg_cols = []

        # 画像URL を先頭に変換
        if "画像URL" in grouped.columns:
            grouped.insert(0, "画像", grouped["画像URL"].apply(lambda u: make_img_tag(u, width=120)))
            grouped = grouped.drop(columns=["画像URL"])
            agg_cols.append("画像")

        for c in ["SKU", "商品番号", "商品名", "属性1名", "属性2名"]:
            if c in grouped.columns:
                agg_cols.append(c)

        for c in ["売上個数合計", "現在庫", "集計月数"]:
            if c in grouped.columns:
                agg_cols.append(c)

        df_agg = grouped[agg_cols].copy()

    # ================ 表示タブ：明細一覧 / SKU別集計 ================
    tab_meisai, tab_agg = st.tabs(["明細一覧", "SKU別集計（SKUごとのトータル個数）"])

    # ---- 明細一覧 ----
    with tab_meisai:
        st.subheader("明細一覧")

        base_cols = [
            c
            for c in [
                "月フォルダ",
                "ランキング",
                "商品番号",
                "SKU",
                "商品名",
                "属性1名",
                "属性2名",
                "現在庫",
                "売上個数",
            ]
            if c in filtered.columns
        ]

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

    # ---- SKU別集計 ----
    with tab_agg:
        st.subheader("SKU別集計（合算）")

        if df_agg is None or df_agg.empty:
            st.info("SKU または 売上個数 の列がないため、SKU別集計を表示できません。")
        else:
            html_table_agg = df_to_html_table(df_agg)

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
            st.markdown(html_table_agg, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
