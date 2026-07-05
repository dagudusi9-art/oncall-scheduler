# -*- coding: utf-8 -*-
"""
Google Sheets連携(Webアプリ層)

ローカル実行時は credentials/service_account.json を、
Streamlit Community Cloud上では st.secrets["gcp_service_account"] を使って
認証する。どちらも無い場合は「未設定」として扱い、例外を投げずに
Falseや案内メッセージを返す(呼び出し側でアプリが落ちないようにするため)。

この層は admin.py / member_input.py から使う想定で、
実際のGoogle Sheets APIの読み書きは src/sheets_io.py の SheetsClient に委譲する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
for _p in (_APP_DIR, _PROJECT_ROOT):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import data_store as ds  # noqa: E402

CREDENTIALS_PATH = _PROJECT_ROOT / "credentials" / "service_account.json"


def _get_secrets_credentials_info() -> Optional[dict]:
    """Streamlit Community Cloud の st.secrets からサービスアカウント情報を取得する。
    st.secrets が未設定/該当キーが無い場合は None を返す(例外を出さない)。
    """
    try:
        import streamlit as st

        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:  # noqa: BLE001  st.secrets未設定時は例外になることがある
        pass
    return None


def credential_source() -> Optional[str]:
    """現在使える認証情報のソースを返す。"local" / "secrets" / None"""
    if _get_secrets_credentials_info() is not None:
        return "secrets"
    if CREDENTIALS_PATH.exists():
        return "local"
    return None


def is_available() -> bool:
    """gspreadライブラリが使えるかどうか"""
    try:
        import gspread  # noqa: F401

        return True
    except ImportError:
        return False


def is_configured() -> bool:
    """Google Sheets連携が使える状態(ライブラリ・認証情報とも揃っている)かどうか"""
    return is_available() and credential_source() is not None


def get_spreadsheet_key() -> str:
    return str(ds.get_auto_sync_settings().get("spreadsheet_key", "") or "")


def get_client(spreadsheet_key: Optional[str] = None):
    """SheetsClient を構築する。未設定の場合は None を返す(例外は投げない)。"""
    if not is_available():
        return None
    key = spreadsheet_key or get_spreadsheet_key()
    if not key:
        return None

    from src.sheets_io import SheetsClient

    secrets_info = _get_secrets_credentials_info()
    try:
        if secrets_info is not None:
            return SheetsClient(credentials_info=secrets_info, spreadsheet_key=key)
        if CREDENTIALS_PATH.exists():
            return SheetsClient(credentials_path=CREDENTIALS_PATH, spreadsheet_key=key)
    except Exception:  # noqa: BLE001
        return None
    return None


# ----------------------------------------------------------------------
# 医師1名分の保存・読み込み(メンバー入力画面用)
# ----------------------------------------------------------------------

def save_member(year: int, month: int, member_name: str) -> Tuple[bool, str]:
    """member_name 1名分の不都合日データをスプレッドシートへ保存する。
    他メンバーのデータは変更しない。
    """
    if not is_configured():
        return False, "Googleスプレッドシート連携が設定されていません。"

    client = get_client()
    if client is None:
        return False, "スプレッドシートに接続できませんでした。設定を確認してください。"

    try:
        all_unavailabilities = ds.get_unavailability_objects(year, month)
        own = [u for u in all_unavailabilities if u.member_name == member_name]
        client.write_unavailability_for_member(member_name, own)
    except Exception as e:  # noqa: BLE001
        return False, f"保存に失敗しました: {e}"

    ds.set_member_sheets_sync(year, month, member_name, kind="saved")
    return True, "Googleスプレッドシートに保存しました。"


def load_member(year: int, month: int, member_name: str) -> Tuple[bool, str]:
    """スプレッドシート上の member_name 1名分のデータをローカルに反映する。
    他メンバーのローカルデータは変更しない。
    """
    if not is_configured():
        return False, "Googleスプレッドシート連携が設定されていません。"

    client = get_client()
    if client is None:
        return False, "スプレッドシートに接続できませんでした。設定を確認してください。"

    try:
        remote = client.load_unavailability_for_member(member_name)
    except Exception as e:  # noqa: BLE001
        return False, f"読み込みに失敗しました: {e}"

    day_map = {
        u.day.isoformat(): {"day": u.day_unavailable, "night": u.night_unavailable} for u in remote
    }
    ds.replace_member_unavailability(year, month, member_name, day_map)
    ds.set_member_sheets_sync(year, month, member_name, kind="loaded")
    return True, "Googleスプレッドシートから読み込みました。"


# ----------------------------------------------------------------------
# 全メンバー一括の保存・読み込み(管理者画面用)
# ----------------------------------------------------------------------

def save_all(year: int, month: int) -> Tuple[bool, str]:
    """ローカルの全メンバー分の不都合日データでスプレッドシート全体を上書きする。"""
    if not is_configured():
        return False, "Googleスプレッドシート連携が設定されていません。"

    client = get_client()
    if client is None:
        return False, "スプレッドシートに接続できませんでした。設定を確認してください。"

    try:
        all_unavailabilities = ds.get_unavailability_objects(year, month)
        client.write_unavailability(all_unavailabilities)
    except Exception as e:  # noqa: BLE001
        return False, f"保存に失敗しました: {e}"

    ds.set_admin_sheets_sync(year, month, kind="saved")
    return True, "全員分のデータをGoogleスプレッドシートに保存しました。"


def load_all(year: int, month: int) -> Tuple[bool, str]:
    """スプレッドシート全体の内容でローカルの不都合日データを上書きする
    (Google Sheets優先)。呼び出し前に呼び出し側で確認ダイアログを出すこと。
    """
    if not is_configured():
        return False, "Googleスプレッドシート連携が設定されていません。"

    client = get_client()
    if client is None:
        return False, "スプレッドシートに接続できませんでした。設定を確認してください。"

    try:
        remote = client.load_unavailability()
    except Exception as e:  # noqa: BLE001
        return False, f"読み込みに失敗しました: {e}"

    by_member: dict = {}
    for u in remote:
        member_map = by_member.setdefault(u.member_name, {})
        member_map[u.day.isoformat()] = {"day": u.day_unavailable, "night": u.night_unavailable}

    ds.replace_all_unavailability(year, month, by_member)
    ds.set_admin_sheets_sync(year, month, kind="loaded")
    return True, "Googleスプレッドシートの内容でローカルデータを更新しました。"
