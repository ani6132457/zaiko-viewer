import streamlit as st
import pandas as pd
import glob
import os


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

        show_image = st.checkbox("商品画像も表示する（上位50件のみ）", value=False)

    # ===== データ絞り込み =====
    filtered = df.copy()

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

    if rank_range and "ランキング" in filtered.columns:
        filtered = filtered[
            (filtered["ランキング"] >= rank_range[0])
            & (filtered["ランキング"] <= rank_range[1])
        ]

    if "売上個数" in filtered.columns:
        filtered = filtered[filtered["売上個数"].abs() >= min_sales]

    # ===== テーブル表示 =====
    display_cols = []
    for c in ["ランキング", "商品番号", "SKU", "商品名", "属性1名", "属性2名", "現在庫", "売上個数"]:
        if c in filtered.columns:
            display_cols.append(c)

    st.subheader("一覧")
    st.dataframe(
        filtered[display_cols],
        use_container_width=True,
        hide_index=True,
    )

    # ===== 画像表示（任意） =====
    if show_image and "画像URL" in filtered.columns:
        st.subheader("商品画像（上位50件）")
        for _, row in filtered.head(50).iterrows():
            st.markdown(f"**{row.get('ランキング', '')}: {row.get('商品名', '')}**")
            url = row.get("画像URL")
            if isinstance(url, str) and url.startswith("http"):
                st.image(url, width=200)
            st.markdown("---")


if __name__ == "__main__":
    main()
