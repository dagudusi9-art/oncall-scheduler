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
    """スマホ優先のCSS。

    カレンダー(不都合日入力・外部バイト日設定)は st.button + st.columns(7) で
    描画し、各ページ側で st.container(key=...) を使って `.st-key-<key>` の
    CSSクラスにスコープしたスタイル(7列固定・ボックスサイズ固定・状態別の
    背景色)を個別に注入している(member_input.py, admin.py を参照)。
    ここでは、それ以外の全体の余白、文字サイズ、カード、表の見やすさを調整する。
    """
    st.markdown(
        """
        <style>
        :root {
            --cal-border: #d7dce5;
            --cal-text: #313443;
            --cal-muted: #6b7280;
            --cal-bg: #ffffff;
            --cal-empty: #f8fafc;
        }

        .mobile-legend {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 6px;
            margin: 0.5rem 0 1rem 0;
        }
        .mobile-legend-item {
            border-radius: 10px;
            padding: 8px 4px;
            text-align: center;
            font-size: 0.9rem;
            font-weight: 600;
            border: 1px solid rgba(49,52,67,0.08);
        }
        .mobile-card-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 1rem;
        }
        .mobile-card {
            border: 1px solid #e5e7eb;
            border-radius: 14px;
            padding: 1rem;
            background: #fff;
        }

        @media (max-width: 768px) {
            section.main > div.block-container {
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
                padding-top: 1rem !important;
                max-width: 100% !important;
            }

            h1 {
                font-size: 1.75rem !important;
                line-height: 1.2 !important;
            }
            h2 {
                font-size: 1.35rem !important;
                line-height: 1.25 !important;
            }
            h3 {
                font-size: 1.15rem !important;
                line-height: 1.25 !important;
            }

            p, li, label, div, span {
                word-break: keep-all;
            }

            /* 通常ボタンはスマホでタップしやすく */
            div.stButton > button {
                min-height: 2.6rem !important;
                padding: 0.35rem 0.5rem !important;
                white-space: normal !important;
                line-height: 1.2 !important;
            }

            .mobile-legend {
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 6px;
            }
            .mobile-legend-item {
                font-size: 0.86rem;
                padding: 7px 3px;
            }
            .mobile-card-grid {
                grid-template-columns: 1fr;
                gap: 0.75rem;
            }

            /* サイドバーが本文を圧迫しすぎないようにする */
            section[data-testid="stSidebar"] {
                max-width: 82vw !important;
            }

            /* DataFrame / data_editor はスマホでは横にはみ出しやすいので枠内に抑える */
            div[data-testid="stDataFrame"],
            div[data-testid="stDataEditor"] {
                max-width: 100% !important;
            }
        }

        @media (max-width: 640px) {
            .block-container {
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
            }
            [data-testid="stHorizontalBlock"] {
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                gap: 0.22rem !important;
                width: 100% !important;
                max-width: 100% !important;
                overflow: visible !important;
            }
            [data-testid="stHorizontalBlock"] > div {
                min-width: 0 !important;
                width: 14.2857% !important;
                flex: 1 1 0 !important;
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
    st.caption("海軍病院のオンコール表の入力・自動作成システム")

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
