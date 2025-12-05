import streamlit as st
import pandas as pd
import glob
import os
import html


# ========= データ読み込み系 =========

@st.cache_data
def load_tempostar_data(file_paths):
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
def load_image_master():
    """
    商品画像URLマスタフォルダ内のCSVをすべて読み込んで
    商品番号 -> 商品画像パス の dict を返す
    フォルダ構成:
        app.py
        商品画像URLマスタ/
            master1.csv
            master2.csv
            ...
    CSVフォーマット:
        1列目: 商品番号（＝商品コードに対応）
        2列目: 商品画像パス（例: /shoes4/0623_1.jpg）
    """
    master_folder = "商品画像URLマスタ"
    pattern = os.path.join(master_folder, "*.csv")
    paths = glob.glob(pattern)

    if not paths:
        # マスタがなくてもアプリ自体は動くようにする（画像なし表示）
        return {}

    dfs = []
    for p in paths:
        # ヘッダー行ありを想定（1列目: 商品番号, 2列目: 商品画像パス）
        # ヘッダー名が違っても位置で拾うようにする
        df_raw = pd.read_csv(p, encoding="cp932", header=None)
        if df_raw.shape[1] < 2:
            continue
        df = df_raw.iloc[:, :2].copy()
        df.columns = ["商品番号", "商品画像パス"]
        dfs.append(df)

    if not dfs:
        return {}

    all_img = pd.concat(dfs, ignore_index=True)

    # 前後の空白をトリム
    all_img["商品番号"] = all_img["商品番号"].astype(str).str.strip()
    all_img["商品画像パス"] = all_img["商品画像パス"].astype(str).str.strip()

    # 後勝ちで dict 化（重複があれば後のCSVを優先）
    img_dict = dict(zip(all_img["商品番号"], all_img["商品画像パス"]))
    return img_dict


# ========= HTMLテーブル描画 =========

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


# ========= メインアプリ =========

def main():
    st.set_page_config(page_title="Tempostar SKU別売上集計（画像付き）", layout="wide")
    st.title("Tempostar 在庫変動データ - SKU別売上集計（商品画像付き）")

    # ================ 対象CSVファイルの取得 ================
    file_paths = sorted(glob.glob("tempostar_stock_*.csv"))

    if not file_paths:
        st.error("tempostar_stock_*.csv が見つかりません。\napp.py と同じフォルダに Tempostar の CSV を置いてください。")
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
        df_raw = load_tempostar_data(selected_paths)
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

    # ================ 商品画像マスタを利用して画像列を作成 ================
    img_master = load_image_master()
    base_url = "https://image.rakuten.co.jp/hype/cabinet"

    def code_to_img_tag(row):
        # 商品番号マスタと突き合わせるキーを決定
        # まず商品コード、なければ商品基本コードを使う
        code = None
        if "商品コード" in row and pd.notna(row["商品コード"]):
            code = str(row["商品コード"]).strip()
        elif "商品基本コード" in row and pd.notna(row["商品基本コード"]):
            code = str(row["商品基本コード"]).strip()

        if not code:
            return ""

        rel_path = img_master.get(code, "")
        if not rel_path:
            return ""

        # 画像URL生成: https://image.rakuten.co.jp/hype/cabinet + 商品画像パス
        # 商品画像パスが "/shoes4/0623_1.jpg" 形式前提
        rel_path = str(rel_path).strip()
        url = base_url + rel_path
        safe = html.escape(url, quote=True)
        return f'<img src="{safe}" width="120">'

    sales_grouped["画像"] = sales_grouped.apply(code_to_img_tag, axis=1)

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
    if not img_master:
        st.warning("商品画像URLマスタが見つからないか、列数が不足しています。画像は表示されません。")

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
