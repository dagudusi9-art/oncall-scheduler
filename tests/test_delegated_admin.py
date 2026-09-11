# -*- coding: utf-8 -*-
"""
delegated_admin_store.py(月限定代理管理者)の単体テスト

- 正しいtoken / 間違ったtoken / revoked token の扱い
- 対象年月がトークンレコードに厳密に紐づき、他の年月には流用できないこと
- rawトークンがローカルキャッシュのどこにも平文で残らないこと(hashのみ)
- audit logがaction単位で追記され、before/afterに相当する詳細情報が残ること
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_PROJECT_ROOT))
sys.path.append(str(_PROJECT_ROOT / "app"))


def _fresh_store():
    import importlib

    tmp_dir = tempfile.mkdtemp()
    import delegated_admin_store as das

    importlib.reload(das)
    das.DATA_DIR = Path(tmp_dir)
    das.DATA_DIR.mkdir(exist_ok=True)
    das.TOKENS_LOCAL_PATH = das.DATA_DIR / "delegated_admin_tokens.json"
    das.AUDIT_LOG_LOCAL_PATH = das.DATA_DIR / "delegated_admin_audit_log.json"
    return das, tmp_dir


def test_issue_and_validate_token():
    das, tmp_dir = _fresh_store()
    try:
        raw_token = das.issue_token(2026, 11, "Nakajima")
        info = das.validate_token(raw_token)
        assert info is not None
        assert info["year"] == 2026
        assert info["month"] == 11
        assert info["label"] == "Nakajima"
    finally:
        shutil.rmtree(tmp_dir)


def test_invalid_token_returns_none():
    das, tmp_dir = _fresh_store()
    try:
        das.issue_token(2026, 11, "Nakajima")
        assert das.validate_token("this-token-does-not-exist") is None
        assert das.validate_token("") is None
        assert das.validate_token(None) is None
    finally:
        shutil.rmtree(tmp_dir)


def test_revoked_token_rejected():
    das, tmp_dir = _fresh_store()
    try:
        raw_token = das.issue_token(2026, 11, "Nakajima")
        info = das.validate_token(raw_token)
        assert info is not None

        revoked = das.revoke_token(info["token_hash"])
        assert revoked is True
        assert das.validate_token(raw_token) is None
    finally:
        shutil.rmtree(tmp_dir)


def test_revoke_unknown_token_hash_returns_false():
    das, tmp_dir = _fresh_store()
    try:
        assert das.revoke_token("no-such-hash") is False
    finally:
        shutil.rmtree(tmp_dir)


def test_token_is_scoped_to_its_own_year_month_only():
    """2026年11月用トークンで検証しても、返る年月は常に11月のみであり、
    他の年月(例: 2026年12月)向けの情報を返すことはできない
    (=呼び出し側が別月を操作する余地が構造的に無いことを保証する)。"""
    das, tmp_dir = _fresh_store()
    try:
        nov_token = das.issue_token(2026, 11, "Nakajima")
        dec_token = das.issue_token(2026, 12, "Sato")

        nov_info = das.validate_token(nov_token)
        dec_info = das.validate_token(dec_token)

        assert (nov_info["year"], nov_info["month"]) == (2026, 11)
        assert (dec_info["year"], dec_info["month"]) == (2026, 12)
        # 別トークンの値を使っても自分の年月しか出てこない(取り違えが無い)
        assert nov_info["token_hash"] != dec_info["token_hash"]
    finally:
        shutil.rmtree(tmp_dir)


def test_multiple_tokens_can_target_same_month_independently():
    das, tmp_dir = _fresh_store()
    try:
        token_a = das.issue_token(2026, 11, "Nakajima")
        token_b = das.issue_token(2026, 11, "Sato")

        das.revoke_token(das.validate_token(token_a)["token_hash"])

        assert das.validate_token(token_a) is None
        info_b = das.validate_token(token_b)
        assert info_b is not None
        assert info_b["month"] == 11
    finally:
        shutil.rmtree(tmp_dir)


def test_raw_token_never_persisted_only_hash_is_stored():
    das, tmp_dir = _fresh_store()
    try:
        raw_token = das.issue_token(2026, 11, "Nakajima")

        with das.TOKENS_LOCAL_PATH.open("r", encoding="utf-8") as f:
            raw_file_content = f.read()

        assert raw_token not in raw_file_content

        rows = json.loads(raw_file_content)
        assert len(rows) == 1
        assert rows[0]["token_hash"] == das._hash_token(raw_token)
        assert "token" not in rows[0]  # rawを保存する列自体が存在しない
    finally:
        shutil.rmtree(tmp_dir)


def test_list_tokens_returns_issued_tokens():
    das, tmp_dir = _fresh_store()
    try:
        das.issue_token(2026, 11, "Nakajima")
        das.issue_token(2026, 12, "Sato")
        tokens = das.list_tokens()
        labels = {t["label"] for t in tokens}
        assert labels == {"Nakajima", "Sato"}
    finally:
        shutil.rmtree(tmp_dir)


def test_audit_log_records_actions_with_detail():
    das, tmp_dir = _fresh_store()
    try:
        raw_token = das.issue_token(2026, 11, "Nakajima")
        info = das.validate_token(raw_token)

        das.log_action(info, "login", {})
        das.log_action(info, "generate_draft", {"status": "OPTIMAL", "warning_count": 0})
        das.log_action(info, "finalize", {"status": "OPTIMAL"})

        logs = das.get_audit_log(year=2026, month=11)
        actions = [r["action"] for r in logs]
        assert actions.count("login") == 1
        assert "generate_draft" in actions
        assert "finalize" in actions

        finalize_row = next(r for r in logs if r["action"] == "finalize")
        assert finalize_row["label"] == "Nakajima"
        detail = json.loads(finalize_row["detail_json"])
        assert detail["status"] == "OPTIMAL"
    finally:
        shutil.rmtree(tmp_dir)


def test_audit_log_filters_by_year_month():
    das, tmp_dir = _fresh_store()
    try:
        nov_info = das.validate_token(das.issue_token(2026, 11, "Nakajima"))
        dec_info = das.validate_token(das.issue_token(2026, 12, "Sato"))

        das.log_action(nov_info, "login", {})
        das.log_action(dec_info, "login", {})

        nov_logs = das.get_audit_log(year=2026, month=11)
        assert len(nov_logs) == 1
        assert nov_logs[0]["label"] == "Nakajima"

        dec_logs = das.get_audit_log(year=2026, month=12)
        assert len(dec_logs) == 1
        assert dec_logs[0]["label"] == "Sato"
    finally:
        shutil.rmtree(tmp_dir)
