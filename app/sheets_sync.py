# -*- coding: utf-8 -*-
"""
Google Sheets連携(Webアプリ層)

ローカル実行時は credentials/service_account.json を、
Streamlit Community Cloud上では st.secrets["gcp_service_account"] を使って
認証する。どちらも無い場合は「未設定」として扱い、例外を投げずに
Falseや案内メッセージを返す(呼び出し側でアプリが落ちないようにするため)。

この層は admin.py / member_input.py から使う想定。

不都合日の保存・読み込みについては、シート全体を ws.clear() して書き戻す
旧方式(src/sheets_io.py の SheetsClient.write_unavailability* 系)による
データ消失事故が過去に発生したため、現在は data_store.py 側の安全な
v2保存経路(member×year_month単位のupsert。ws.clear()を一切使わない)に
統一している。SheetsClient は勤務表・集計表の書き込み(write_schedule等)や
CLI(src/main.py)での不都合日読み込みには引き続き使われる。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
for _p in reversed((_APP_DIR, _PROJECT_ROOT)):
    if str(_p) in sys.path:
        sys.path.remove(str(_p))
    sys.path.insert(0, str(_p))

import data_store as ds  # noqa: E402
import sheets_backend  # noqa: E402

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
    """スプレッドシートIDを解決する。

    sheets_backend.get_spreadsheet_key()(st.secrets優先、次にローカル
    キャッシュ)を第一候補とし、そこで解決できない場合のみ、管理者が
    admin.py で入力したdata_store側の設定値にフォールバックする。
    """
    key = sheets_backend.get_spreadsheet_key()
    if key:
        return key
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
#
# 不都合日の保存・読み込みは、他ユーザー・他月のデータを壊す事故が
# 過去に起きたため、シート全体を ws.clear() して書き戻す旧方式
# (SheetsClient.write_unavailability* 系)はもう使わない。
# data_store.py 側の安全なv2保存経路(member×year_month単位でのupsert、
# ws.clear()を一切使わない)に統一する。
# ----------------------------------------------------------------------

def save_member(year: int, month: int, member_name: str, day_map: Optional[dict] = None) -> Tuple[bool, str]:
    """member_name 1名・対象年月分の不都合日データを保存する。
    他メンバー・他月のデータには一切触れない(v2の安全なupsート経路を使用)。

    day_map を渡さない場合は、現在ローカルにキャッシュされている
    その人のデータをそのまま保存する。
    """
    if day_map is None:
        day_map = ds.get_local_unavailability(year, month).get(member_name, {})
    return ds.save_member_unavailability(year, month, member_name, day_map)


def load_member(year: int, month: int, member_name: str) -> Tuple[bool, str]:
    """Googleスプレッドシート(v2優先、無ければ旧シート)上の member_name
    1名分のデータを読み直し、ローカルキャッシュへ反映する。
    他メンバーのローカルデータは変更しない。Sheetsへは書き込まない。
    """
    if not is_configured() and not sheets_backend.is_configured():
        return False, "Googleスプレッドシート連携が設定されていません。"

    remote_all = ds.load_unavailability_raw(year, month)
    day_map = remote_all.get(member_name, {})
    ds.replace_member_unavailability(year, month, member_name, day_map)
    ds.set_member_sheets_sync(year, month, member_name, kind="loaded")
    return True, "Googleスプレッドシートの内容でローカルデータを更新しました。"


# ----------------------------------------------------------------------
# 全メンバー一括の読み込み(管理者画面用)
#
# 「ローカルの全員分をSheetsへ一括で上書き保存する」機能(旧 save_all())は
# 意図的に廃止した。ローカルキャッシュは各メンバーの「保存」ボタン操作や
# 直近の読み込みタイミングに依存するため、既にメンバーがv2へ直接保存した
# 最新データより古い可能性があり、それをローカルの古い内容で一括上書きすると
# 最新データを消してしまう危険がある。Google Sheets(v2)を常にsource of
# truthとし、ローカルはあくまで表示用キャッシュとして扱う方針に統一する。
# 個別メンバーの保存は member_input.py の「保存する」ボタン
# (ds.save_member_unavailability、対象1行のみの安全なupsert)のみを使う。
# ----------------------------------------------------------------------

def load_all(year: int, month: int) -> Tuple[bool, str]:
    """Googleスプレッドシート(v2優先、無ければ旧シート)の対象年月の内容で
    ローカルの不都合日データを上書きする。Sheetsへは書き込まない
    (読み込みのみ)。呼び出し前に呼び出し側で確認ダイアログを出すこと。
    """
    if not sheets_backend.is_configured():
        return False, "Googleスプレッドシート連携が設定されていません。"

    by_member = ds.load_unavailability_raw(year, month)
    ds.replace_all_unavailability(year, month, by_member)
    ds.set_admin_sheets_sync(year, month, kind="loaded")
    return True, "Googleスプレッドシートの内容でローカルデータを更新しました。"
