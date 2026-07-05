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
