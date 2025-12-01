import streamlit as st
import pandas as pd
import glob


@st.cache_data
def load_data():
    """フォルダ内の「在庫変動データ*.xlsm」の一番新しいファイルを読む"""
    files = sorted(glob.glob("在庫変動データ*.xlsm"))
    if not files:
        raise FileNotFoundError("在庫変動データ*.xlsm が見つかりません。")

    latest_file = files[-1]

    # シート名が日付付きで変わっても、最初のシートを使うようにする
    sheets_dict = pd.read_excel(latest_file, sheet_name=None)
    sheet_name = list(sheets_dict.keys())[0]
    df = sheets_dict[sheet_name]

    # 列名を少しだけ扱いやすくリネーム
    rename_map = {
        "商品番号.1": "SKU",
        "Unnamed: 9": "画像URL",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    return df, latest_file, sheet_name


def main():
    st.set_page_config(page_title="在庫変動データビューア", layout="wide")

    st.title("在庫変動データビューア")

    try:
        df, filename, sheet_name = load_data()
    except Exception as e:
        st.error(f"データ読み込みでエラーが発生しました: {e}")
        st.stop()

    st.caption(f"読み込みファイル: {filename}（シート: {sheet_name}）")
    st.write(f"件数: {len(df):,} 件")

    # ===== サイドバーでフィルタ =====
    with st.sidebar:
        st.header("絞り込み")

        keyword = st.text_input("商品番号 / SKU / 商品名で検索")

        # ランキングの範囲スライダー（列がない場合はスキップ）
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

        # 売上個数（絶対値）の下限
        min_sales = 0
        if "売上個数" in df.columns:
            min_sales = st.number_input(
                "売上個数（絶対値）の下限", min_value=0, value=0, step=1
            )

    # ===== データ絞り込み =====
    filtered = df.copy()

    # キーワード検索
    if keyword:
        cols = []
        for c in ["商品番号", "SKU", "商品名"]:
            if c in filtered.columns:
                cols.append(c)

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

    # ===== テーブル表示（商品画像を必須表示） =====
    st.subheader("一覧")

    # 表示したい主な列
    display_cols = []
    for c in ["ランキング", "商品番号", "SKU", "商品名", "属性1名", "属性2名", "現在庫", "売上個数"]:
        if c in filtered.columns:
            display_cols.append(c)

    # 画像列も必ず追加（あれば）
    if "画像URL" in filtered.columns:
        display_cols.append("画像URL")

    display_df = filtered[display_cols].copy()

    # 画像列を小さめサムネで表示
    if "画像URL" in display_df.columns:
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "画像URL": st.column_config.ImageColumn(
                    "画像",
                    help="商品画像サムネイル",
                    width="small",  # 小さめサイズ
                )
            },
        )
    else:
        # 画像URL列がない場合のフォールバック
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
