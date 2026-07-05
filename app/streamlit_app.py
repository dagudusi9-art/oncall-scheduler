# -*- coding: utf-8 -*-
"""
オンコール自動割当システム Webアプリ エントリポイント

実行方法:
    cd oncall_scheduler
    streamlit run app/streamlit_app.py

ファイル名はWindowsでのダウンロード・展開時の文字化けを避けるため
すべてASCII(半角英数字)にしている。画面に表示される名前(タイトル)は
st.Page(..., title="...") で日本語を指定しており、見た目には影響しない。
"""
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="オンコール自動割当システム", page_icon="🏥", layout="wide")


def inject_mobile_css() -> None:
    """スマホでもカレンダーの7列グリッドが崩れないようにする。

    Streamlit の st.columns は狭い画面では縦積みになるため、
    不都合日入力カレンダーの曜日と日付が縦一列に崩れる。
    スマホ運用を優先し、横スクロールなしで列幅を縮めて7列を維持する。
    """
    st.markdown(
        """
        <style>
        @media (max-width: 768px) {
            /* Streamlit標準のスマホ時カラム縦積みを抑止する */
            div[data-testid="stHorizontalBlock"] {
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                gap: 0.18rem !important;
                width: 100% !important;
            }

            div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                flex: 1 1 0 !important;
                width: 0 !important;
                min-width: 0 !important;
                padding-left: 0 !important;
                padding-right: 0 !important;
            }

            /* スマホでは余白と文字を少し詰める */
            section.main > div.block-container {
                padding-left: 0.55rem !important;
                padding-right: 0.55rem !important;
                padding-top: 1rem !important;
            }

            h1 {
                font-size: 1.85rem !important;
                line-height: 1.2 !important;
            }

            h2, h3 {
                line-height: 1.25 !important;
            }

            p, li, label, div, span {
                word-break: keep-all;
            }

            /* カレンダーのボタンを7列内に収める */
            div.stButton > button {
                min-height: 2.75rem !important;
                padding: 0.15rem 0.05rem !important;
                font-size: 0.82rem !important;
                line-height: 1.1 !important;
                white-space: pre-line !important;
                overflow-wrap: anywhere !important;
            }

            /* 凡例・曜日ヘッダーのラベルも小さめにする */
            div[data-testid="stMarkdownContainer"] {
                font-size: 0.92rem;
            }

            /* サイドバーが開いた時に本文を圧迫しすぎないようにする */
            section[data-testid="stSidebar"] {
                max-width: 82vw !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_mobile_css()

PAGES_DIR = Path(__file__).resolve().parent / "pages"


def home() -> None:
    st.title("🏥 オンコール自動割当システム")
    st.caption("外科オンコール表の入力・自動作成システム")

    st.markdown(
        """
        左のサイドバー、またはこの画面のボタンから利用したい画面を選んでください。

        - **メンバー入力**: 自分の名前を選んで、不都合な日をタップで入力します(スマホ対応)
        - **管理者**: メンバー・目標回数の設定、勤務表の自動作成、Excel/Googleスプレッドシート出力
        """
    )

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("👤 メンバーの方はこちら")
        st.write("不都合な日を入力します。")
        st.page_link(member_page, label="メンバー入力画面を開く", icon="👤")

    with col2:
        st.subheader("🛠️ 管理者の方はこちら")
        st.write("メンバー管理・目標回数設定・勤務表作成を行います。")
        st.page_link(admin_page, label="管理者画面を開く", icon="🛠️")

    st.divider()
    st.caption("運用担当者向け: サーバーで常時起動しておくと、スマホからブラウザでアクセスして入力できます。")


home_page = st.Page(home, title="ホーム", icon="🏥", default=True, url_path="home")
member_page = st.Page(str(PAGES_DIR / "member_input.py"), title="メンバー入力", icon="👤", url_path="member_input")
admin_page = st.Page(str(PAGES_DIR / "admin.py"), title="管理者", icon="🛠️", url_path="admin")

pg = st.navigation([home_page, member_page, admin_page])
pg.run()
