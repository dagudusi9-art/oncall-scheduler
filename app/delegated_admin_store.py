# -*- coding: utf-8 -*-
"""
月限定代理管理者(delegated admin)機能

- 通常管理者が、特定の年月だけに限定した代理管理者トークンを発行できる。
- このトークンは auth.py の member_tokens(メンバー入力用トークン)とは
  完全に別物として扱う。通常管理者よりは弱いが member よりは強い権限を
  持つため、rawトークンはどこにも永続化せず、hashのみを保存する。

保存先:
  - delegated_admin_tokens シート(+ ローカルJSONキャッシュ)
      1トークン=1行。key = token_hash。
  - delegated_admin_audit_log シート(+ ローカルJSONキャッシュ、append-only)
      代理管理者による操作の監査ログ。

このモジュールは data_store.py / sheets_backend.py / src/optimizer.py を
一切変更せず、sheets_backend.py が既に提供している汎用API
(upsert_keyed_row / append_row_safe / read_table)のみを呼び出す。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
if str(_APP_DIR) not in sys.path:
    sys.path.append(str(_APP_DIR))

import sheets_backend  # noqa: E402

DATA_DIR = _PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

TOKENS_LOCAL_PATH = DATA_DIR / "delegated_admin_tokens.json"
AUDIT_LOG_LOCAL_PATH = DATA_DIR / "delegated_admin_audit_log.json"

SHEET_TOKENS = "delegated_admin_tokens"
SHEET_AUDIT_LOG = "delegated_admin_audit_log"

TOKENS_HEADER = ["token_hash", "year", "month", "label", "issued_at", "revoked", "expires_at"]
AUDIT_HEADER = ["timestamp", "token_hash", "label", "year", "month", "action", "detail_json"]


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _read_local_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_local_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# トークンの発行・失効・検証
# ----------------------------------------------------------------------

def _load_all_tokens() -> List[dict]:
    """Sheetsが設定されていればそちらを正データとして読み込み、
    ローカルキャッシュを更新する。未設定・失敗時はローカルキャッシュにフォールバック。"""
    if sheets_backend.is_configured():
        remote = sheets_backend.read_table(SHEET_TOKENS)
        if remote is not None:
            _write_local_json(TOKENS_LOCAL_PATH, remote)
            return remote
    return _read_local_json(TOKENS_LOCAL_PATH, [])


def issue_token(year: int, month: int, label: str) -> str:
    """新しい代理管理者トークンを発行し、raw tokenを返す。
    保存されるのはhashのみで、raw tokenはこの戻り値以外どこにも残らない。"""
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    row = {
        "token_hash": token_hash,
        "year": str(int(year)),
        "month": str(int(month)),
        "label": label,
        "issued_at": datetime.now().isoformat(),
        "revoked": "False",
        "expires_at": "",
    }

    if sheets_backend.is_configured():
        sheets_backend.upsert_keyed_row(
            SHEET_TOKENS,
            TOKENS_HEADER,
            key_cols=["token_hash"],
            key_values={"token_hash": token_hash},
            row_dict=row,
        )

    # ローカルには必ず反映する(Sheets未設定 or 書き込み失敗時のフォールバック)
    rows = _read_local_json(TOKENS_LOCAL_PATH, [])
    rows = [r for r in rows if r.get("token_hash") != token_hash]
    rows.append(row)
    _write_local_json(TOKENS_LOCAL_PATH, rows)

    return raw_token


def revoke_token(token_hash: str) -> bool:
    """指定したtoken_hashのトークンをrevoke状態にする。存在しなければFalse。"""
    rows = _load_all_tokens()
    target = None
    for r in rows:
        if r.get("token_hash") == token_hash:
            r["revoked"] = "True"
            target = r
            break
    if target is None:
        return False

    _write_local_json(TOKENS_LOCAL_PATH, rows)
    if sheets_backend.is_configured():
        sheets_backend.upsert_keyed_row(
            SHEET_TOKENS,
            TOKENS_HEADER,
            key_cols=["token_hash"],
            key_values={"token_hash": token_hash},
            row_dict=target,
        )
    return True


def list_tokens() -> List[dict]:
    """通常管理者画面での一覧表示用。発行日時の新しい順。"""
    rows = _load_all_tokens()
    return sorted(rows, key=lambda r: r.get("issued_at", ""), reverse=True)


def validate_token(raw_token: str) -> Optional[dict]:
    """rawトークンを検証する。有効なら
    {"token_hash", "year", "month", "label"} を返す。
    存在しない・不正・revoked済みの場合はNoneを返す。

    対象年月はこの関数が返すレコードの値のみを信頼する。
    呼び出し側(delegated_admin.py)はURLやUIから年月を受け取ってはならない。
    """
    if not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    rows = _load_all_tokens()
    for r in rows:
        stored_hash = str(r.get("token_hash", ""))
        if not stored_hash or not hmac.compare_digest(stored_hash, token_hash):
            continue
        if str(r.get("revoked", "False")).strip().lower() in ("true", "1"):
            return None
        try:
            year = int(r["year"])
            month = int(r["month"])
        except (KeyError, ValueError, TypeError):
            return None
        return {
            "token_hash": token_hash,
            "year": year,
            "month": month,
            "label": r.get("label", ""),
        }
    return None


# ----------------------------------------------------------------------
# audit log(append-only)
# ----------------------------------------------------------------------

def _ensure_audit_log_header_once() -> None:
    """delegated_admin_audit_log シートに対して、まだ1件もデータが
    無いことを確認できた場合に限り、ヘッダー行だけを単独で先に確定させる。

    背景(原因調査): sheets_backend.append_row_safe() は「シートが
    空ならヘッダー行を追記し、続けてその場でデータ行を追記する」実装に
    なっている。真っさらなシートに対してこの2回の ws.append_row() が
    間隔なく連続実行されると、Google Sheets API側の反映タイミングに
    よっては2回目の追記(データ行)が1回目のヘッダー行を上書きして
    しまうことがある(append_row()を間隔を空けずに連続実行した際に
    知られている事象)。

    実際に本番で発生した不具合はこれが原因だった: 最初の login 用の
    append_row_safe() 呼び出しで、ヘッダー行がデータ行の値で上書き
    されてしまい、以降 sheets_backend.read_table()(内部で
    ws.get_all_records() を使う)がこの壊れた1行目を「列名」として
    扱ってしまうため、action/year/month等の列が正しく取得できず、
    管理者画面の「代理管理者 操作ログ」が空行や `None年None月` に
    見えていた。

    対策として、ヘッダーが未確定(=シートにまだデータが1件も無い)
    場合だけ、write_table()で先にヘッダー行のみを単独で書き込んで
    おく。write_table()はclear()+update()による範囲指定の書き込みで、
    append_row()のような「末尾行の自動検出」に依存しないため、この後に
    append_row_safe()を呼んでも(その時点で既にヘッダーが存在するため)
    append_row_safe()内部の「シートが空なら」の分岐そのものが発生せず、
    連続appendによる競合が起きない。

    既に1件でもデータが読み取れた場合は絶対に呼び出し元から
    実行しない(write_table()はシート全体を上書きするため、既存の
    監査ログを消してしまわないよう、必ず事前にread_table()で
    空であることを確認してから呼ぶこと)。
    """
    existing = sheets_backend.read_table(SHEET_AUDIT_LOG)
    if existing:
        return  # 既にデータがある場合は何もしない(既存ログを消さないため)
    sheets_backend.write_table(SHEET_AUDIT_LOG, AUDIT_HEADER, [])


def _looks_like_audit_row(r: dict) -> bool:
    """audit log の1行として最低限の形を満たしているかを判定する。

    ヘッダー行破損等、何らかの理由で期待した列が読み取れなかった行を
    「空行」としてそのまま画面に表示してしまわないよう、
    action・timestampの両方が読み取れる行だけを有効な行とみなす。
    """
    return bool(r.get("action")) and bool(r.get("timestamp"))


def log_action(token_info: dict, action: str, detail: Optional[dict] = None) -> None:
    """delegated_admin_audit_log へ1行追記する。
    Sheets未設定・書き込み失敗時はローカルJSONへ追記する(いずれの場合も
    追記のみで、既存のログ行は一切変更・削除しない)。"""
    row = {
        "timestamp": datetime.now().isoformat(),
        "token_hash": token_info.get("token_hash", ""),
        "label": token_info.get("label", ""),
        "year": str(token_info.get("year", "")),
        "month": str(token_info.get("month", "")),
        "action": action,
        "detail_json": json.dumps(detail or {}, ensure_ascii=False),
    }

    ok = False
    if sheets_backend.is_configured():
        _ensure_audit_log_header_once()
        ok = sheets_backend.append_row_safe(SHEET_AUDIT_LOG, AUDIT_HEADER, row)

    if not ok:
        rows = _read_local_json(AUDIT_LOG_LOCAL_PATH, [])
        rows.append(row)
        _write_local_json(AUDIT_LOG_LOCAL_PATH, rows)


def get_audit_log(year: Optional[int] = None, month: Optional[int] = None) -> List[dict]:
    """通常管理者画面での閲覧用。year/monthでフィルタ可能。新しい順。

    action・timestampが読み取れない行(ヘッダー破損等による欠損行)は
    ここで除外し、画面に空行やNoneを表示させない。
    """
    rows: Optional[List[dict]] = None
    if sheets_backend.is_configured():
        rows = sheets_backend.read_table(SHEET_AUDIT_LOG)
    if rows is None:
        rows = _read_local_json(AUDIT_LOG_LOCAL_PATH, [])

    rows = [r for r in rows if _looks_like_audit_row(r)]

    if year is not None:
        rows = [r for r in rows if str(r.get("year")) == str(int(year))]
    if month is not None:
        rows = [r for r in rows if str(r.get("month")) == str(int(month))]
    return sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True)
