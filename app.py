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
    st.set_page_config(page_title="在庫変動データ SKU集計", layout="wide")
    st.title("在庫変動データ SKU別集計ビューア")

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
            "集計対象の月フォルダ（複数選択可）",
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
    st.write(f"明細行数: {len(df):,} 行（{len(selected_months)}か月分）")

    # ================ フィルタ設定（集計前の前処理用） ================
    with st.sidebar:
        st.header("絞り込み（集計対象）")
        keyword = st.text_input("商品番号 / SKU / 商品名で検索")

        min_total_sales = st.number_input(
            "売上個数合計（絶対値）の下限",
            min_value=0,
            value=0,
            step=1,
        )

    # ================ 明細レベルでの絞り込み（キーワード） ================
    filtered = df.copy()

    # キーワード検索（集計前）
    if keyword:
        cols = [c for c in ["商品番号", "SKU", "商品名"] if c in filtered.columns]
        if cols:
            cond = False
            for c in cols:
                cond = cond | filtered[c].astype(str).str.contains(keyword, case=False)
            filtered = filtered[cond]

    # ================ SKU別集計（合算） ================
    if not {"SKU", "売上個数"}.issubset(filtered.columns):
        st.error("SKU または 売上個数 の列が見つからないため、集計できません。")
        st.stop()

    # グループキー（SKU＋商品情報）
    group_keys = []
    for c in ["SKU", "商品番号", "商品名", "属性1名", "属性2名"]:
        if c in filtered.columns:
            group_keys.append(c)

    agg_dict = {
        "売上個数": "sum",  # 合計売上個数（返品がマイナスなら差し引き後）
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

    # 売上個数合計の絶対値フィルタ
    grouped["売上個数合計_abs"] = grouped["売上個数合計"].abs()
    if min_total_sales > 0:
        grouped = grouped[grouped["売上個数合計_abs"] >= min_total_sales]

    # 売上個数合計の降順に並べ替え
    grouped = grouped.sort_values("売上個数合計", ascending=False)

    # 表示用列の順番
    agg_cols = []

    # 画像列を先頭に
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

    st.write(f"SKU数: {len(df_agg):,} 件")

    # ================ 集計テーブル表示（1画面のみ） ================
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
