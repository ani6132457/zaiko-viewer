import streamlit as st
import pandas as pd
import glob
import os
import html
import re
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

    # 日本語カレンダー設定
    st.markdown("""
        <script>
            const lang = document.documentElement.lang;
            document.documentElement.lang = "ja";
        </script>
    """, unsafe_allow_html=True)

    st.title("Tempostar 在庫変動データ - SKU別売上集計（商品画像付き）")

    # ================ 対象CSVファイルの取得（ファイル名から日付を抽出） ================
    raw_paths = sorted(glob.glob("tempostar_stock_*.csv"))

    if not raw_paths:
        st.error("tempostar_stock_*.csv が見つかりません。\napp.py と同じフォルダに Tempostar の CSV を置いてください。")
        st.stop()

    file_infos = []
    date_pattern = re.compile(r"tempostar_stock_(\d{8})")

    for path in raw_paths:
        name = os.path.basename(path)
        m = date_pattern.search(name)
        if not m:
            # 日付が取れないファイルは一旦無視（必要なら別扱いにしてもOK）
            continue
        d = datetime.strptime(m.group(1), "%Y%m%d").date()
        file_infos.append({"date": d, "path": path, "name": name})

    if not file_infos:
        st.error("tempostar_stock_YYYYMMDD.csv 形式のファイルが見つかりません。")
        st.stop()

    # 利用可能な日付の最小・最大
    all_dates = [fi["date"] for fi in file_infos]
    min_date = min(all_dates)
    max_date = max(all_dates)

    # ================ サイドバー：カレンダーで期間を選択 ================
    with st.sidebar:
        st.header("集計設定")

        st.write(f"利用可能なデータ期間：{min_date} 〜 {max_date}")

        # デフォルトは最新日のみ
        default_range = (max_date, max_date)
        selected_range = st.date_input(
            "集計期間（開始日〜終了日）",
            value=default_range,
        )

        # Streamlitのバージョンによって返り値形式が違う場合があるので吸収
        if isinstance(selected_range, (list, tuple)) and len(selected_range) == 2:
            start_date, end_date = selected_range
        else:
            # 単一日指定になっていた場合はその日だけにする
            start_date = end_date = selected_range

        if start_date > end_date:
            start_date, end_date = end_date, start_date

        # 選択期間内のファイルを抽出
        selected_infos = [
            fi for fi in file_infos if start_date <= fi["date"] <= end_date
        ]
        selected_paths = [fi["path"] for fi in selected_infos]

        if not selected_paths:
            st.error("選択した期間に対応するCSVファイルがありません。")
            st.stop()

        st.write("集計対象日数:", len(selected_infos), "日")
        # 確認用に日付リストを表示（必要なければ消してもOK）
        st.caption("対象日:")
        st.caption("、".join(str(fi["date"]) for fi in selected_infos))

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
    for fi in selected_infos:
        st.caption(f"・{fi['date']} : {os.path.basename(fi['path'])}")
    st.write(f"明細行数: {len(df_raw):,} 行")

    df = df_raw.copy()

    # ================ 明細レベルでのキーワード絞り込み ================
    if keyword:
        cond = False
        for col in ["商品コード", "商品基本コード", "商品名"]:
            if col in df.columns:
                cond = cond | df[col].astype(str).str.contains(keyword, case=False)
        df = df[cond]

    # 必須列チェック（Tempostar側は 商品コード＋商品基本コード を使う）
    required_cols = {"商品コード", "商品基本コード", "増減値"}
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        st.error("CSVに以下の列が必要です: " + " / ".join(missing))
        st.stop()

    # =========================
    # 売上用データ（更新理由＝受注取込のみ）
    # =========================
    if "更新理由" in df.columns:
        df_sales = df[df["更新理由"] == "受注取込"].copy()
    else:
        df_sales = df.copy()

    # ================ SKU別売上集計（商品コード単位で合算） ================
    # 商品コードごとに増減値を合算し、その他の情報は最後のものを採用
    agg_sales = {
        "商品基本コード": "last",
        "商品名": "last",
        "属性1名": "last",
        "属性2名": "last",
        "増減値": "sum",
    }
    sales_grouped = (
        df_sales
        .groupby("商品コード", dropna=False)
        .agg(agg_sales)
        .reset_index()
        .rename(columns={"増減値": "増減値合計"})
    )

    # 表示用「売上個数合計」＝ マイナスを反転してプラスに
    sales_grouped["売上個数合計"] = -sales_grouped["増減値合計"]

    # 売れていない（0以下）は除外
    sales_grouped = sales_grouped[sales_grouped["売上個数合計"] > 0]

    # ================ 在庫情報（現在庫：商品コード単位） ================
    if "変動後" in df.columns:
        agg_stock = {
            "商品基本コード": "last",
            "商品名": "last",
            "属性1名": "last",
            "属性2名": "last",
            "変動後": "last",
        }
        stock_group = (
            df
            .groupby("商品コード", dropna=False)
            .agg(agg_stock)
            .reset_index()
            .rename(columns={"変動後": "現在庫"})
        )

        # 売上集計とマージ（商品コードで結合）
        sales_grouped = pd.merge(
            sales_grouped,
            stock_group[["商品コード", "現在庫"]],
            on="商品コード",
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
    display_cols = ["画像", "商品コード", "商品基本コード", "商品名", "属性1名", "属性2名"]
    for c in ["売上個数合計", "現在庫", "増減値合計"]:
        if c in sales_grouped.columns:
            display_cols.append(c)

    df_view = sales_grouped[display_cols].copy()

    st.write(
        f"SKU数（売上個数合計 > 0）: {len(df_view):,} 件"
        f"　/　集計期間: {start_date} 〜 {end_date}"
    )
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
