# -*- coding: utf-8 -*-
"""
認証機能

- 管理者: パスワードで管理者画面全体を保護する(従来通り)
- メンバー: 4桁PINコードでの本人確認は廃止。代わりに、メンバーごとに
  ランダムなトークンを含む専用URL(例: /member_input?token=otani_1a2b3c4d5e6f)
  を発行し、そのURLを知っている人だけが入力画面へアクセスできる方式にする。

  URLは名前を選ばせる必要がないため、管理者がLINE等で一度送るだけで、
  以後はそのメンバーがブックマークして毎月使い続けられる
  (年月はアプリ全体の設定なので、月が変わってもURLは変わらない)。

保存先: data/auth.json
  {
    "admin_password_hash": "...",
    "member_tokens": {"大谷": "otani_1a2b3c4d5e6f", ...}
  }

パスワードは平文では保存せず、salt付きハッシュで保存します。
トークンはURLの一部になるため、そのままでは検索されうる前提とし、
機密情報(パスワード等)とは別物として扱います
(トークンの漏洩が疑われる場合は管理者画面から再発行してください)。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Optional

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
DATA_DIR = _APP_DIR.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
AUTH_PATH = DATA_DIR / "auth.json"

try:
    import pykakasi

    _KKS = pykakasi.kakasi()
except Exception:  # pragma: no cover - pykakasiが無い環境でも動くようにする
    _KKS = None


def _load() -> dict:
    if not AUTH_PATH.exists():
        return {"admin_password_hash": None, "member_tokens": {}}
    with AUTH_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("member_tokens", {})
    return data


def _save(data: dict) -> None:
    with AUTH_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _hash(value: str, salt: str) -> str:
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# 管理者パスワード
# ----------------------------------------------------------------------

def has_admin_password() -> bool:
    return bool(_load().get("admin_password_hash"))


def set_admin_password(password: str) -> None:
    data = _load()
    salt = secrets.token_hex(8)
    data["admin_password_hash"] = f"{salt}${_hash(password, salt)}"
    _save(data)


def check_admin_password(password: str) -> bool:
    data = _load()
    stored = data.get("admin_password_hash")
    if not stored:
        return False
    salt, hashed = stored.split("$", 1)
    return _hash(password, salt) == hashed


# ----------------------------------------------------------------------
# メンバー専用URLトークン
# ----------------------------------------------------------------------

def _slugify(name: str) -> str:
    """名前からURLに使えるASCIIのスラッグを作る(日本語はローマ字化する)"""
    slug = ""
    if _KKS is not None:
        try:
            result = _KKS.convert(name)
            slug = "".join(item.get("hepburn", "") for item in result)
        except Exception:
            slug = ""
    if not slug:
        slug = name
    slug = re.sub(r"[^a-zA-Z0-9]", "", slug).lower()
    if not slug:
        slug = "member"
    return slug[:16]


def has_token(member_name: str) -> bool:
    return member_name in _load().get("member_tokens", {})


def get_token(member_name: str) -> Optional[str]:
    return _load().get("member_tokens", {}).get(member_name)


def issue_token(member_name: str) -> str:
    """新しいトークンを発行(既存のURLは無効化される)"""
    data = _load()
    token = f"{_slugify(member_name)}_{secrets.token_hex(6)}"
    data.setdefault("member_tokens", {})[member_name] = token
    _save(data)
    return token


def revoke_token(member_name: str) -> None:
    data = _load()
    data.get("member_tokens", {}).pop(member_name, None)
    _save(data)


def get_member_by_token(token: str) -> Optional[str]:
    """トークンから該当メンバー名を逆引きする。見つからなければNone。"""
    if not token:
        return None
    tokens = _load().get("member_tokens", {})
    for name, t in tokens.items():
        if t == token:
            return name
    return None


# ----------------------------------------------------------------------
# アプリの公開URL(専用URLの組み立てに使う)
# ----------------------------------------------------------------------

def get_app_base_url() -> str:
    data = _load()
    return data.get("app_base_url") or "http://localhost:8501"


def set_app_base_url(base_url: str) -> None:
    data = _load()
    data["app_base_url"] = base_url.rstrip("/")
    _save(data)


def build_member_url(member_name: str, member_page_url_path: str = "member_input") -> Optional[str]:
    token = get_token(member_name)
    if not token:
        return None
    return f"{get_app_base_url()}/{member_page_url_path}?token={token}"


# ----------------------------------------------------------------------
# Streamlit画面用の管理者ログインゲート
# ----------------------------------------------------------------------

def require_admin_login() -> None:
    """
    管理者画面の先頭で呼び出す。パスワード未設定なら初回設定を促し、
    設定済みならログインフォームを表示して未認証の場合は st.stop() する。
    """
    if st.session_state.get("is_admin_authenticated"):
        return

    if not has_admin_password():
        st.warning("管理者パスワードが未設定です。初回のみ、ここでパスワードを設定してください。")
        with st.form("set_admin_password_form"):
            pw1 = st.text_input("管理者パスワードを設定", type="password")
            pw2 = st.text_input("確認用にもう一度入力", type="password")
            submitted = st.form_submit_button("設定する")
            if submitted:
                if not pw1:
                    st.error("パスワードを入力してください")
                elif pw1 != pw2:
                    st.error("パスワードが一致しません")
                else:
                    set_admin_password(pw1)
                    st.session_state["is_admin_authenticated"] = True
                    st.success("管理者パスワードを設定しました")
                    st.rerun()
        st.stop()

    st.subheader("🔒 管理者ログイン")
    with st.form("admin_login_form"):
        pw = st.text_input("管理者パスワード", type="password")
        submitted = st.form_submit_button("ログイン")
        if submitted:
            if check_admin_password(pw):
                st.session_state["is_admin_authenticated"] = True
                st.rerun()
            else:
                st.error("パスワードが違います")
    st.stop()


def logout_admin() -> None:
    st.session_state.pop("is_admin_authenticated", None)
