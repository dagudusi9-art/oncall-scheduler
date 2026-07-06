# -*- coding: utf-8 -*-
"""
Googleスプレッドシートをアプリの正データストアとして扱うための低レベル層。

Streamlit Community Cloudはファイルシステムをエフェメラルにしか保持しない
(再起動・再デプロイで data/ 以下が初期化される)ため、主要なデータは
このモジュールを介してGoogleスプレッドシートへ保存し、起動時にはまず
スプレッドシートから読み込む。ローカルの data/*.json は、Sheets未設定時の
動作(ローカル開発・テスト実行)や、Sheets接続に失敗した場合の
フォールバック用キャッシュとしてのみ使う。

シート構成(1つのスプレッドシート内の複数タブ):
    app_config          key-value形式。year, month, submission_deadline,
                         auto_sync_sheets, sheets_spreadsheet_key,
                         app_base_url, admin_password_hash など
    members             メンバー一覧(1行1名)
    tokens               メンバー個別URLトークン(1行1名)
    不都合日入力          不都合日(既存のシート名をそのまま使用。
                         src/sheets_io.py・app/sheets_sync.py と共有)
    assignments          予定勤務表スナップショット(年月ごとにJSON1行)
    actual_assignments    実績勤務表スナップショット(年月ごとにJSON1行)
    app_state             上記以外の運用データ(外部バイト対象日・交代依頼・
                         確定フラグ・入力履歴など)をキーごとにJSONで保存する
                         汎用バケット

スプレッドシートIDの解決順序(get_spreadsheet_key):
    1. st.secrets["sheets"]["spreadsheet_key"]
       (推奨。Streamlit Cloudの再起動・再デプロイでも消えない唯一の場所)
    2. data/sheets_key.json のローカルキャッシュ
       (管理者画面でsecrets未設定のまま入力した場合の簡易キャッシュ。
        Streamlit Cloudでは他のローカルファイルと同様に再起動で消えるため、
        本番運用では1を設定することを強く推奨する)

認証情報(サービスアカウント)は既存の app/sheets_sync.py と同じ方式:
    st.secrets["gcp_service_account"](Streamlit Cloud)、無ければ
    credentials/service_account.json(ローカル実行)。

この層の関数は例外を投げない。読み込みは失敗時にNoneを返し、書き込みは
失敗時にFalseを返す。呼び出し側は
    - 読み込み: Noneならローカルキャッシュにフォールバックする
    - 書き込み: ローカル保存は必ず行い、Sheetsへの反映はbest-effortとする
という方針で使うこと(このモジュール自体は方針を強制しない)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
DATA_DIR = _PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

CREDENTIALS_PATH = _PROJECT_ROOT / "credentials" / "service_account.json"
_KEY_CACHE_PATH = DATA_DIR / "sheets_key.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


# ----------------------------------------------------------------------
# 認証情報・スプレッドシートIDの解決
# ----------------------------------------------------------------------

def _get_secrets_credentials_info() -> Optional[dict]:
    try:
        import streamlit as st

        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:  # noqa: BLE001  st.secrets未設定時は例外になることがある
        pass
    return None


def _get_secrets_spreadsheet_key() -> Optional[str]:
    try:
        import streamlit as st

        if "sheets" in st.secrets and st.secrets["sheets"].get("spreadsheet_key"):
            return str(st.secrets["sheets"]["spreadsheet_key"])
    except Exception:  # noqa: BLE001
        pass
    return None


def is_available() -> bool:
    """gspreadライブラリが使えるかどうか"""
    try:
        import gspread  # noqa: F401

        return True
    except ImportError:
        return False


def credential_source() -> Optional[str]:
    """現在使える認証情報のソースを返す。"secrets" / "local" / None"""
    if _get_secrets_credentials_info() is not None:
        return "secrets"
    if CREDENTIALS_PATH.exists():
        return "local"
    return None


def get_spreadsheet_key() -> str:
    """スプレッドシートIDを解決する(優先: st.secrets > ローカルキャッシュ)"""
    from_secrets = _get_secrets_spreadsheet_key()
    if from_secrets:
        return from_secrets
    if _KEY_CACHE_PATH.exists():
        try:
            with _KEY_CACHE_PATH.open("r", encoding="utf-8") as f:
                return json.load(f).get("spreadsheet_key", "") or ""
        except Exception:  # noqa: BLE001
            return ""
    return ""


def cache_spreadsheet_key_locally(key: str) -> None:
    """管理者画面でst.secrets未設定のままスプレッドシートIDを入力した場合の
    簡易キャッシュ。Streamlit Cloudでは再起動で消えるため、恒久的な設定と
    しては st.secrets["sheets"]["spreadsheet_key"] を使うことを推奨する。"""
    try:
        with _KEY_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump({"spreadsheet_key": key or ""}, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def spreadsheet_key_from_secrets() -> bool:
    """スプレッドシートIDがst.secretsで設定されているか
    (再起動後も確実に残る設定かどうかの判定に使う)"""
    return bool(_get_secrets_spreadsheet_key())


def is_configured() -> bool:
    """Google Sheetsをデータストアとして使える状態かどうか"""
    return is_available() and credential_source() is not None and bool(get_spreadsheet_key())


_spreadsheet_cache: dict = {}


def get_spreadsheet():
    """gspreadのSpreadsheetオブジェクトを返す。失敗時はNone(例外は投げない)。"""
    if not is_configured():
        return None
    key = get_spreadsheet_key()
    cache_key = (credential_source(), key)
    if cache_key in _spreadsheet_cache:
        return _spreadsheet_cache[cache_key]
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        secrets_info = _get_secrets_credentials_info()
        if secrets_info is not None:
            creds = Credentials.from_service_account_info(secrets_info, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(str(CREDENTIALS_PATH), scopes=SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(key)
    except Exception:  # noqa: BLE001
        return None
    _spreadsheet_cache[cache_key] = sh
    return sh


def _get_or_create_ws(sh, name: str, rows: int = 200, cols: int = 20):
    import gspread

    try:
        return sh.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=name, rows=rows, cols=cols)


# ----------------------------------------------------------------------
# 読み込みキャッシュ(Streamlitは操作ごとにスクリプト全体を再実行するため、
# キャッシュ無しでは1回の画面表示で同じシートに何度もAPIアクセスしてしまう。
# 短いTTLでキャッシュし、書き込み成功時に明示的にクリアする)
# ----------------------------------------------------------------------

def _cached_get_all_records_impl(sheet_name: str, spreadsheet_key: str) -> Optional[List[dict]]:
    sh = get_spreadsheet()
    if sh is None:
        return None
    try:
        ws = _get_or_create_ws(sh, sheet_name)
        return ws.get_all_records()
    except Exception:  # noqa: BLE001
        return None


try:
    import streamlit as _st

    _cached_get_all_records = _st.cache_data(ttl=15, show_spinner=False)(_cached_get_all_records_impl)
except Exception:  # noqa: BLE001  streamlit未初期化の環境でも動くようにする
    _cached_get_all_records = _cached_get_all_records_impl


def _invalidate_cache() -> None:
    try:
        _cached_get_all_records.clear()
    except Exception:  # noqa: BLE001
        pass


def _get_all_records(sheet_name: str) -> Optional[List[dict]]:
    return _cached_get_all_records(sheet_name, get_spreadsheet_key())


# ----------------------------------------------------------------------
# key-value形式シート (app_config用)
# ----------------------------------------------------------------------

def read_kv(sheet_name: str) -> Optional[Dict[str, str]]:
    records = _get_all_records(sheet_name)
    if records is None:
        return None
    return {str(r.get("key", "")): str(r.get("value", "")) for r in records if r.get("key")}


def write_kv(sheet_name: str, data: Dict[str, object]) -> bool:
    sh = get_spreadsheet()
    if sh is None:
        return False
    try:
        existing = read_kv(sheet_name) or {}
        merged = dict(existing)
        merged.update(data)
        ws = _get_or_create_ws(sh, sheet_name)
        rows = [["key", "value"]]
        for k in sorted(merged.keys()):
            rows.append([k, _to_cell(merged[k])])
        ws.clear()
        ws.update(values=rows, range_name="A1")
    except Exception:  # noqa: BLE001
        return False
    _invalidate_cache()
    return True


def _to_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ----------------------------------------------------------------------
# テーブル形式シート (members / tokens / unavailability 用)
# ----------------------------------------------------------------------

def read_table(sheet_name: str) -> Optional[List[dict]]:
    return _get_all_records(sheet_name)


def write_table(sheet_name: str, header: List[str], rows: List[dict]) -> bool:
    sh = get_spreadsheet()
    if sh is None:
        return False
    try:
        ws = _get_or_create_ws(sh, sheet_name)
        values = [header]
        for row in rows:
            values.append([_to_row_cell(row.get(col)) for col in header])
        ws.clear()
        ws.update(values=values, range_name="A1")
    except Exception:  # noqa: BLE001
        return False
    _invalidate_cache()
    return True


def _to_row_cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    return v


# ----------------------------------------------------------------------
# JSONブロブ形式シート (assignments / actual_assignments / app_state 用)
# ----------------------------------------------------------------------

def read_blob(sheet_name: str, key: str) -> Optional[dict]:
    records = _get_all_records(sheet_name)
    if records is None:
        return None
    for r in records:
        if str(r.get("key", "")) == key:
            raw = r.get("data_json", "")
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
    return None


def write_blob(sheet_name: str, key: str, data: dict) -> bool:
    from datetime import datetime as _dt

    sh = get_spreadsheet()
    if sh is None:
        return False
    try:
        ws = _get_or_create_ws(sh, sheet_name)
        records = ws.get_all_records()
        rows = [
            [str(r.get("key", "")), str(r.get("updated_at", "")), str(r.get("data_json", ""))]
            for r in records
            if str(r.get("key", "")) != key
        ]
        rows.append([key, _dt.now().isoformat(), json.dumps(data, ensure_ascii=False)])
        values = [["key", "updated_at", "data_json"]] + sorted(rows, key=lambda r: r[0])
        ws.clear()
        ws.update(values=values, range_name="A1")
    except Exception:  # noqa: BLE001
        return False
    _invalidate_cache()
    return True


def delete_blob(sheet_name: str, key: str) -> bool:
    sh = get_spreadsheet()
    if sh is None:
        return False
    try:
        ws = _get_or_create_ws(sh, sheet_name)
        records = ws.get_all_records()
        rows = [
            [str(r.get("key", "")), str(r.get("updated_at", "")), str(r.get("data_json", ""))]
            for r in records
            if str(r.get("key", "")) != key
        ]
        values = [["key", "updated_at", "data_json"]] + rows
        ws.clear()
        ws.update(values=values, range_name="A1")
    except Exception:  # noqa: BLE001
        return False
    _invalidate_cache()
    return True
