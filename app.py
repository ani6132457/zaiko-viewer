import streamlit as st
import pandas as pd
import glob
import os
import html
from datetime import datetime, date


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
    商品管理番号（商品URL） -> 商品画像パス1 の dict を返す

    フォルダ構成:
        app.py
        商品画像URLマスタ/
            master1.csv
            master2.csv
            ...

    CSVフォーマット（想定ヘッダー）:
        商品管理番号（商品URL）, 商品画像パス1, ...
    """
    master_folder = "商品画像URLマスタ"
    pattern = os.path.join(master_folder, "*.csv")
    paths = glob.glob(pattern)

    if not paths:
        # マスタがなくてもアプリ自体は動くようにする（画像なし表示）
        return {}

    dfs = []
    for p in paths:
        df = pd.read_csv(p, encoding="cp932")
        if "商品管理番号（商品URL）" not in df.columns or "商品画像パス1" not in df.columns:
            # 想定列がないファイルはスキップ
            continue
        sub = df[["商品管理番号（商品URL）", "商品画像パス1"]].copy()
        dfs.append(sub)

    if not dfs:
        return {}

    all_img = pd.concat(dfs, ignore_index=True)

    # 前後の空白をトリム
    all_img["商品管理番号（商品URL）"] = (
        all_img["商品管理番号（商品URL）"].astype(str).str.strip()
    )
    all_img["商品画像パス1"] = all_img["商品画像パス1"].astype(str).str.strip()

    # 後勝ちで dict 化（重複があれば後のCSVを優先）
    img_dict = dict(
        zip(all_img["商品管理番号（商品URL）"], all_img["商品画像パス1"])
    )
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

    # ================ ローカルパスからCSV一覧を取得 ================
    # ここを UNC ではなくローカルパスに変更
    BASE_DIR = r"C:\Users\ani\github\zaiko-viewer"
    pattern = os.path.join(BASE_DIR, "tempostar_stock_*.csv")

    paths = sorted(glob.glob(pattern))

    if not paths:
        st.error(
            "指定フォルダに tempostar_stock_*.csv が見つかりません。\n"
            f"パス: {BASE_DIR}"
        )
        st.stop()

    # ファイル名から日付を解析してリスト化
    file_infos = []
    for p in paths:
        name = os.path.basename(p)  # 例: tempostar_stock_20251204.csv
        stem, _ = os.path.splitext(name)  # tempostar_stock_20251204
        date_part = stem.replace("tempostar_stock_", "")
        try:
            d = datetime.strptime(date_part, "%Y%m%d").date()
        except Exception:
            # もし想定外の形式ならスキップ
            continue
        file_infos.append({"path": p, "name": name, "date": d})

    if not file_infos:
        st.error("ファイル名から日付を解析できませんでした。tempostar_stock_YYYYMMDD.csv 形式か確認してください。")
        st.stop()

    all_dates = [info["date"] for info in file_infos]
    min_date = min(all_dates)
    max_date = max(all_dates)

    # ================ サイドバー：カレンダーで日付範囲選択 & フィルタ ================
    with st.sidebar:
        st.header("集計設定")

        st.caption("CSV保存先パス")
        st.code(BASE_DIR)

        st.caption("集計対象日付（カレンダーから期間を選択）")
        date_range = st.date_input(
            "日付範囲",
            (min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            format="YYYY-MM-DD",
        )

        # st.date_input は単日 or タプルどちらも返る可能性がある
        if isinstance(date_range, tuple):
            if len(date_range) == 2:
                start_date, end_date = date_range
            elif len(date_range) == 1:
                start_date = end_date = date_range[0]
            else:
                start_date = end_date = min_date
        else:
            start_date = end_date = date_range

        if start_date is None or end_date is None:
            start_date, end_date = min_date, max_date

        # 選択された日付範囲に含まれるファイルだけを対象に
        selected_infos = [
            info for info in file_infos if start_date <= info["date"] <= end_date
        ]

        if not selected_infos:
            st.warning("この日付範囲に該当するCSVがありません。日付範囲を見直してください。")
            st.stop()

        selected_paths = [info["path"] for info in selected_infos]

        st.caption("選択中の日付とファイル")
        for info in selected_infos:
            st.caption(f"・{info['date']}  ({info['name']})")

        # キーワード絞り込み（集計前）
        keyword = st.text_input("商品コード / 商品基本コード / 商品名で検索")

        # 売上個数合計の下限（プラス表示）
        min_total_sales = st.number_input(
            "売上個数合計（プラス値）の下限",
            min_value=0,
            value=0,
            step=1,
        )

    # ================ データ読み込み（選択CSVを合算） ================
    try:
        df_raw = load_tempostar_data(selected_paths)
    except Exception as e:
        st.error(f"CSV読み込みでエラーが発生しました: {e}")
        st.stop()

    st.write(f"読み込みCSVファイル数: {len(selected_paths)} 件")
    st.write(f"明細行数合計: {len(df_raw):,} 行")

    df = df_raw.copy()

    # ================ 明細レベルでのキーワード絞り込み ================
    if keyword:
        cond = False
        for col in ["商品コード", "商品基本コード", "商品名"]:
            if col in df.columns:
                cond = cond | df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    # 必須列チェック（Tempostar側は 商品基本コード をキーに使う）
    required_cols = {"商品基本コード", "増減値"}
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        st.error("CSVに以下の列が必要です: " + " / ".join(missing))
        st.stop()

    # ================ 売上用データ（更新理由＝受注取込のみ） ================
    if "更新理由" in df.columns:
        df_sales = df[df["更新理由"] == "受注取込"].copy()
    else:
        # 更新理由列がない場合は全行を売上として扱う（保険）
        df_sales = df.copy()

    # ================ SKU別売上集計 ================
    sales_group_keys = []
    for c in [
        "商品コード",
        "商品基本コード",
        "商品名",
        "属性1名",
        "属性2名",
    ]:
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
        for c in [
            "商品コード",
            "商品基本コード",
            "商品名",
            "属性1名",
            "属性2名",
        ]:
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

    def row_to_img_tag(row):
        """
        Tempostar側の 商品基本コード を
        マスタ側の 商品管理番号（商品URL） とみなして紐付け
        """
        code = str(row["商品基本コード"]).strip()
        if not code:
            return ""
        rel_path = img_master.get(code, "")
        if not rel_path:
            return ""
        rel_path = str(rel_path).strip()
        url = base_url + rel_path
        safe = html.escape(url, quote=True)
        return f'<img src="{safe}" width="120">'

    sales_grouped["画像"] = sales_grouped.apply(row_to_img_tag, axis=1)

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
        st.warning(
            "商品画像URLマスタが見つからないか、"
            "『商品管理番号（商品URL）』『商品画像パス1』列がありません。画像は表示されません。"
        )

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
