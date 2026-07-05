# -*- coding: utf-8 -*-
"""
auth.py のトークン発行・逆引き・失効に関する単体テスト
"""
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_PROJECT_ROOT))
sys.path.append(str(_PROJECT_ROOT / "app"))


def _fresh_auth():
    import importlib

    tmp_dir = tempfile.mkdtemp()
    import auth

    importlib.reload(auth)
    auth.DATA_DIR = Path(tmp_dir)
    auth.DATA_DIR.mkdir(exist_ok=True)
    auth.AUTH_PATH = auth.DATA_DIR / "auth.json"
    return auth, tmp_dir


def test_issue_and_lookup_token():
    auth, tmp_dir = _fresh_auth()
    try:
        token = auth.issue_token("大谷")
        assert token.startswith("ootani_") or "_" in token
        assert auth.get_member_by_token(token) == "大谷"
    finally:
        shutil.rmtree(tmp_dir)


def test_invalid_token_returns_none():
    auth, tmp_dir = _fresh_auth()
    try:
        assert auth.get_member_by_token("no-such-token") is None
        assert auth.get_member_by_token("") is None
        assert auth.get_member_by_token(None) is None
    finally:
        shutil.rmtree(tmp_dir)


def test_reissue_invalidates_old_token():
    auth, tmp_dir = _fresh_auth()
    try:
        old_token = auth.issue_token("大谷")
        new_token = auth.issue_token("大谷")
        assert old_token != new_token
        assert auth.get_member_by_token(old_token) is None
        assert auth.get_member_by_token(new_token) == "大谷"
    finally:
        shutil.rmtree(tmp_dir)


def test_revoke_token():
    auth, tmp_dir = _fresh_auth()
    try:
        token = auth.issue_token("大谷")
        auth.revoke_token("大谷")
        assert not auth.has_token("大谷")
        assert auth.get_member_by_token(token) is None
    finally:
        shutil.rmtree(tmp_dir)


def test_build_member_url():
    auth, tmp_dir = _fresh_auth()
    try:
        auth.set_app_base_url("https://hospital-oncall.example.com")
        token = auth.issue_token("若山")
        url = auth.build_member_url("若山")
        assert url == f"https://hospital-oncall.example.com/member_input?token={token}"
    finally:
        shutil.rmtree(tmp_dir)


def test_admin_password_flow():
    auth, tmp_dir = _fresh_auth()
    try:
        assert not auth.has_admin_password()
        auth.set_admin_password("secret123")
        assert auth.has_admin_password()
        assert auth.check_admin_password("secret123")
        assert not auth.check_admin_password("wrong")
    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    test_issue_and_lookup_token()
    test_invalid_token_returns_none()
    test_reissue_invalidates_old_token()
    test_revoke_token()
    test_build_member_url()
    test_admin_password_flow()
    print("全テスト成功")
