# -*- coding: utf-8 -*-
"""
Webアプリ用データストア

CLI版(config.yaml + CSV)とは別に、Webアプリでは編集のしやすさを優先して
JSONファイルにデータを保存する。

data/
  config.json                  # {"year":2026,"month":8,"members":[{"name":..,"target_count":..}]}
  unavailability_2026_08.json  # {"大谷": {"2026-08-01": {"day": true, "night": false}, ...}, ...}
  schedule_2026_08.json        # 直近の最適化結果のキャッシュ(表示の高速化用、任意)

本番でメンバー数・アクセス数が増える場合は、このモジュールの内部実装だけを
SQLite等に置き換えれば、UI側(pages/)のコードは変更不要になるように
関数インターフェースを薄く保っている。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))

from src.models import Member, Unavailability  # noqa: E402
import sheets_backend  # noqa: E402

DATA_DIR = _PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"

# ----------------------------------------------------------------------
# Googleスプレッドシートのシート(タブ)名
#
# app_config / members / tokens は sheets_backend.is_configured() の場合、
# ローカルの data/*.json より常に優先して読み込まれる(Sheetsを正データとする)。
# 「不都合日入力」は旧形式(1日1行)のシート。過去データのバックアップ・
# 後方互換読み込み専用として残し、このシートへは二度と書き込まない。
# 新しい保存方式は「不都合日入力_v2」(1行=1メンバー×1ヶ月)を使う。
# assignments / actual_assignments / app_state は生成・派生データの保存先で、
# 年月やキーごとにJSON1行として保存する(人間が直接編集する想定ではない)。
# ----------------------------------------------------------------------
SHEET_APP_CONFIG = "app_config"
SHEET_MEMBERS = "members"
SHEET_UNAVAILABILITY = "不都合日入力"  # 旧形式。読み取り専用バックアップとして保持する
SHEET_UNAVAILABILITY_V2 = "不都合日入力_v2"  # 新形式。1行=1メンバー×1ヶ月
SHEET_UNAVAILABILITY_HISTORY = "不都合日入力_history"  # 保存成功時のみappend-only
SHEET_ASSIGNMENTS = "assignments"
SHEET_ACTUAL_ASSIGNMENTS = "actual_assignments"
SHEET_APP_STATE = "app_state"

MEMBERS_HEADER = [
    "name",
    "target_count",
    "email",
    "gaikobu_eligible",
    "manual_target",
    "absence_start",
    "absence_end",
]
# 旧形式(読み取り専用)のヘッダー。書き込みには使わない。
UNAVAILABILITY_HEADER = ["member_name", "date", "day_unavailable", "night_unavailable"]

# 新形式(v2)のヘッダーとキー列。1行=1メンバー×1ヶ月(day_mapをJSONで保持)。
UNAVAILABILITY_V2_HEADER = ["member_name", "year_month", "days_json", "updated_at"]
UNAVAILABILITY_V2_KEY_COLS = ["member_name", "year_month"]

# 履歴シートのヘッダー。保存ボタン押下による保存成功時のみ1行追記する。
UNAVAILABILITY_HISTORY_HEADER = ["member_name", "year_month", "days_json", "saved_at"]


def _write_local_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_local_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _state_load(key: str, local_path: Path, default):
    """app_state シートからキーで読み込み、成功したらローカルにもキャッシュする。
    Sheets未設定・読み込み失敗時はローカルキャッシュにフォールバックする。"""
    if sheets_backend.is_configured():
        remote = sheets_backend.read_blob(SHEET_APP_STATE, key)
        if remote is not None:
            _write_local_json(local_path, remote)
            return remote
    return _read_local_json(local_path, default)


def _state_save(key: str, local_path: Path, data) -> None:
    """ローカルへは必ず保存し、Sheetsへの反映はbest-effortで行う。"""
    _write_local_json(local_path, data)
    if sheets_backend.is_configured():
        sheets_backend.write_blob(SHEET_APP_STATE, key, data)


def _state_clear(key: str, local_path: Path) -> None:
    if local_path.exists():
        local_path.unlink()
    if sheets_backend.is_configured():
        sheets_backend.delete_blob(SHEET_APP_STATE, key)

# 状態の定義(メンバー入力画面でワンタップで巡回させる4状態)
STATE_OK = "ok"  # 終日OK
STATE_DAY_OFF = "day_off"  # 日中不可
STATE_NIGHT_OFF = "night_off"  # 夜間不可
STATE_FULL_OFF = "full_off"  # 終日不可

STATE_ORDER = [STATE_OK, STATE_FULL_OFF, STATE_DAY_OFF, STATE_NIGHT_OFF]

STATE_LABEL = {
    STATE_OK: "○",
    STATE_DAY_OFF: "▲昼",
    STATE_NIGHT_OFF: "▲夜",
    STATE_FULL_OFF: "×",
}

STATE_COLOR = {
    STATE_OK: "#DFF5E1",  # 薄緑
    STATE_DAY_OFF: "#FFE8B3",  # 薄オレンジ
    STATE_NIGHT_OFF: "#CFE3FA",  # 薄青
    STATE_FULL_OFF: "#F8CCCC",  # 薄赤
}


def _month_key(year: int, month: int) -> str:
    return f"{year}_{month:02d}"


def _unavailability_path(year: int, month: int) -> Path:
    return DATA_DIR / f"unavailability_{_month_key(year, month)}.json"


# ----------------------------------------------------------------------
# 設定(年月・メンバー・目標回数)
# ----------------------------------------------------------------------

DEFAULT_CONFIG = {
    "year": date.today().year,
    "month": date.today().month,
    "members": [],
    "submission_deadline": None,  # "YYYY-MM-DD" 形式
    "auto_sync_sheets": False,
    "sheets_spreadsheet_key": "",
}


def _member_row_to_dict(row: dict) -> dict:
    def _flag(v) -> bool:
        return str(v).strip().lower() in ("1", "true", "yes")

    return {
        "name": str(row.get("name", "")).strip(),
        "target_count": int(row["target_count"]) if str(row.get("target_count", "")).strip() else 0,
        "email": str(row.get("email", "") or ""),
        "gaikobu_eligible": _flag(row.get("gaikobu_eligible", "")),
        "manual_target": _flag(row.get("manual_target", "")),
        "absence_start": str(row.get("absence_start") or "") or None,
        "absence_end": str(row.get("absence_end") or "") or None,
    }


def _fetch_remote_config() -> Optional[dict]:
    """app_config / members シートから設定を組み立てる。
    どちらかの読み込みに失敗した場合はNoneを返す(呼び出し側でローカルに
    フォールバックする)。"""
    if not sheets_backend.is_configured():
        return None
    kv = sheets_backend.read_kv(SHEET_APP_CONFIG)
    members_rows = sheets_backend.read_table(SHEET_MEMBERS)
    if kv is None or members_rows is None:
        return None

    config = dict(DEFAULT_CONFIG)
    if kv.get("year", "").strip():
        config["year"] = int(kv["year"])
    if kv.get("month", "").strip():
        config["month"] = int(kv["month"])
    config["submission_deadline"] = kv.get("submission_deadline") or None
    config["auto_sync_sheets"] = kv.get("auto_sync_sheets", "").strip().lower() == "true"
    config["sheets_spreadsheet_key"] = kv.get("sheets_spreadsheet_key", "")
    config["members"] = [_member_row_to_dict(r) for r in members_rows if str(r.get("name", "")).strip()]
    return config


def _push_config_to_sheets(config: dict) -> None:
    """best-effort。失敗してもローカル保存済みのため呼び出し側は落とさない。"""
    if not sheets_backend.is_configured():
        return
    kv = {
        "year": config.get("year"),
        "month": config.get("month"),
        "submission_deadline": config.get("submission_deadline") or "",
        "auto_sync_sheets": bool(config.get("auto_sync_sheets", False)),
        "sheets_spreadsheet_key": config.get("sheets_spreadsheet_key", ""),
    }
    sheets_backend.write_kv(SHEET_APP_CONFIG, kv)
    sheets_backend.write_table(SHEET_MEMBERS, MEMBERS_HEADER, config.get("members", []))
    # 管理者画面でst.secrets未設定のままスプレッドシートIDを入力した場合でも、
    # 次回起動までは接続を維持できるようにローカルにもキャッシュしておく。
    key = config.get("sheets_spreadsheet_key", "")
    if key:
        sheets_backend.cache_spreadsheet_key_locally(key)


def load_config() -> dict:
    remote = _fetch_remote_config()
    if remote is not None:
        _write_local_json(CONFIG_PATH, remote)
        return remote

    # Sheets未設定、または読み込み失敗時はローカルキャッシュを使う
    # (ローカル開発時やCloudでの一時的な接続断への対応)
    if not CONFIG_PATH.exists():
        config = dict(DEFAULT_CONFIG)
        _write_local_json(CONFIG_PATH, config)
        return config
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)
    # 旧バージョンのconfig.jsonに無いキーはデフォルト値で補完する(後方互換性)
    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            changed = True
    if changed:
        _write_local_json(CONFIG_PATH, config)
    return config


def save_config(config: dict) -> None:
    """ローカルには必ず保存し(高速なキャッシュ・オフライン用)、
    Google Sheetsが設定されていればそちらにも反映する(正データ)。"""
    _write_local_json(CONFIG_PATH, config)
    _push_config_to_sheets(config)


def get_members() -> List[dict]:
    return load_config().get("members", [])


def add_member(
    name: str,
    target_count: int = 0,
    email: str = "",
    gaikobu_eligible: bool = False,
    manual_target: bool = False,
    absence_start: Optional[str] = None,
    absence_end: Optional[str] = None,
) -> None:
    config = load_config()
    names = [m["name"] for m in config["members"]]
    if name in names:
        raise ValueError(f"「{name}」はすでに登録されています")
    config["members"].append(
        {
            "name": name,
            "target_count": target_count,
            "email": email,
            "gaikobu_eligible": gaikobu_eligible,
            "manual_target": manual_target,
            "absence_start": absence_start,
            "absence_end": absence_end,
        }
    )
    save_config(config)


def remove_member(name: str) -> None:
    config = load_config()
    config["members"] = [m for m in config["members"] if m["name"] != name]
    save_config(config)


def update_target_count(name: str, target_count: int) -> None:
    config = load_config()
    for m in config["members"]:
        if m["name"] == name:
            m["target_count"] = target_count
    save_config(config)


def update_member_email(name: str, email: str) -> None:
    config = load_config()
    for m in config["members"]:
        if m["name"] == name:
            m["email"] = email
    save_config(config)


def update_member_gaikobu_eligible(name: str, eligible: bool) -> None:
    config = load_config()
    for m in config["members"]:
        if m["name"] == name:
            m["gaikobu_eligible"] = bool(eligible)
    save_config(config)


def update_member_manual_target(name: str, manual: bool) -> None:
    config = load_config()
    for m in config["members"]:
        if m["name"] == name:
            m["manual_target"] = bool(manual)
    save_config(config)


def update_member_absence(name: str, absence_start: Optional[str], absence_end: Optional[str]) -> None:
    """長期不在の開始日・終了日を設定する('YYYY-MM-DD' または None)"""
    config = load_config()
    for m in config["members"]:
        if m["name"] == name:
            m["absence_start"] = absence_start or None
            m["absence_end"] = absence_end or None
    save_config(config)


def set_year_month(year: int, month: int) -> None:
    config = load_config()
    config["year"] = year
    config["month"] = month
    save_config(config)


# ----------------------------------------------------------------------
# 目標回数の自動計算(終日不可7日以上を長期不在相当として反映)
# ----------------------------------------------------------------------

def _days_in_month(year: int, month: int) -> int:
    import calendar as _cal

    return _cal.monthrange(year, month)[1]


def get_auto_absence_days(year: int, month: int, member_name: str, min_consecutive_days: int = 7) -> set[date]:
    """
    メンバー入力の「終日不可」が連続 min_consecutive_days 日以上続く期間を
    長期不在相当とみなし、その日付集合を返す。

    例: 7日連続の終日不可なら7日すべてを勤務可能日数から差し引く。
    6日以下の終日不可や、日中のみ/夜間のみ不可は通常の不都合日として扱い、
    目標回数の自動計算には反映しない。
    """
    n_days = _days_in_month(year, month)
    raw = load_unavailability_raw(year, month).get(member_name, {})
    full_off_days = {
        date.fromisoformat(day_str)
        for day_str, flags in raw.items()
        if bool(flags.get("day", False)) and bool(flags.get("night", False))
    }

    result: set[date] = set()
    current_run: list[date] = []

    for day_num in range(1, n_days + 1):
        d = date(year, month, day_num)
        if d in full_off_days:
            current_run.append(d)
        else:
            if len(current_run) >= min_consecutive_days:
                result.update(current_run)
            current_run = []

    if len(current_run) >= min_consecutive_days:
        result.update(current_run)

    return result


def compute_available_days(year: int, month: int, member: dict) -> int:
    """
    その月の勤務可能日数を計算する。

    ルール:
    - メンバーが入力した「終日不可」が連続7日以上の場合、その連続期間を
      長期不在相当として勤務可能日数から差し引く。
    - 6日以下の終日不可、日中のみ不可、夜間のみ不可は、通常の不都合日として
      割当制約には使うが、目標回数の自動計算には反映しない。
    - 管理者による長期不在の個別入力欄は廃止し、ここでは参照しない。
    """
    n_days = _days_in_month(year, month)
    member_name = member.get("name", "")
    absence_days = get_auto_absence_days(year, month, member_name) if member_name else set()
    return max(n_days - len(absence_days), 0)


def compute_auto_targets(year: int, month: int) -> Dict[str, int]:
    """
    各メンバーの自院オンコール目標回数を、勤務可能日数の比率に応じて
    自動配分する(最大剰余方式で合計が総枠数に一致するように調整する)。

    「目標回数を手動指定」がONのメンバーは、その人の現在の target_count を
    そのまま使い、残りの枠を自動計算対象のメンバーで比率配分する。
    """
    members = get_members()
    n_days = _days_in_month(year, month)
    total_slots = n_days * 2  # 日中+夜間

    manual_members = [m for m in members if m.get("manual_target")]
    auto_members = [m for m in members if not m.get("manual_target")]

    manual_sum = sum(int(m.get("target_count", 0)) for m in manual_members)
    remaining = max(total_slots - manual_sum, 0)

    result: Dict[str, int] = {m["name"]: int(m.get("target_count", 0)) for m in manual_members}

    if not auto_members:
        return result

    available = {m["name"]: compute_available_days(year, month, m) for m in auto_members}
    total_available = sum(available.values())

    if total_available <= 0:
        for m in auto_members:
            result[m["name"]] = 0
        return result

    # 最大剰余方式(Hamilton法): まず切り捨てで配分し、余りを小数部が大きい順に配る
    raw = {name: remaining * avail / total_available for name, avail in available.items()}
    floor_vals = {name: int(raw[name]) for name in raw}
    assigned = sum(floor_vals.values())
    leftover = remaining - assigned

    order = sorted(raw.keys(), key=lambda n: raw[n] - floor_vals[n], reverse=True)
    for i in range(leftover):
        floor_vals[order[i % len(order)]] += 1

    result.update(floor_vals)
    return result


def get_members_as_models() -> List[Member]:
    config = load_config()
    auto_targets = compute_auto_targets(config["year"], config["month"])
    return [
        Member(
            name=m["name"],
            target_count=int(auto_targets.get(m["name"], m.get("target_count", 0))),
            gaikobu_eligible=bool(m.get("gaikobu_eligible", False)),
        )
        for m in get_members()
    ]


# ----------------------------------------------------------------------
# 不都合日データ
# ----------------------------------------------------------------------

def _month_key_dash(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _clean_day_map(day_map: Dict[str, dict]) -> Dict[str, dict]:
    """STATE_OK相当(day=False, night=False)の日は保存しない形へ正規化する。"""
    return {
        day_str: {"day": bool(flags.get("day", False)), "night": bool(flags.get("night", False))}
        for day_str, flags in day_map.items()
        if flags.get("day", False) or flags.get("night", False)
    }


# ----------------------------------------------------------------------
# 読み込み: 新形式(v2) + 旧形式(バックアップ・後方互換)の統合
# ----------------------------------------------------------------------

def _fetch_v2_month_records(year: int, month: int) -> Optional[Dict[str, Dict[str, dict]]]:
    """「不都合日入力_v2」シートから対象年月の全メンバー分を読み込む。
    読み取り専用(このシートを書き換えない)。Sheets未接続・読み込み失敗時はNone。"""
    if not sheets_backend.is_configured():
        return None
    rows = sheets_backend.read_table(SHEET_UNAVAILABILITY_V2)
    if rows is None:
        return None

    ym = _month_key_dash(year, month)
    result: Dict[str, Dict[str, dict]] = {}
    for r in rows:
        if str(r.get("year_month", "")) != ym:
            continue
        member_name = str(r.get("member_name", "")).strip()
        if not member_name:
            continue
        raw_json = r.get("days_json", "")
        try:
            day_map = json.loads(raw_json) if raw_json else {}
        except (json.JSONDecodeError, TypeError):
            day_map = {}
        result[member_name] = _clean_day_map(day_map)
    return result


def _fetch_legacy_month_records(year: int, month: int) -> Optional[Dict[str, Dict[str, dict]]]:
    """旧形式「不都合日入力」シート(1日1行)から対象年月の行を読み込む。
    読み取り専用(このシートには二度と書き込まない)。
    v2にまだ記録の無いメンバーのための後方互換フォールバック用。"""
    if not sheets_backend.is_configured():
        return None
    rows = sheets_backend.read_table(SHEET_UNAVAILABILITY)
    if rows is None:
        return None

    prefix = f"{year:04d}-{month:02d}-"
    result: Dict[str, Dict[str, dict]] = {}
    for r in rows:
        date_str = str(r.get("date", ""))
        if not date_str.startswith(prefix):
            continue
        member_name = str(r.get("member_name", "")).strip()
        if not member_name:
            continue
        d = str(r.get("day_unavailable", "")).strip().lower() in ("1", "true")
        n = str(r.get("night_unavailable", "")).strip().lower() in ("1", "true")
        if d or n:
            result.setdefault(member_name, {})[date_str] = {"day": d, "night": n}
    return result


def load_unavailability_raw(year: int, month: int) -> Dict[str, Dict[str, dict]]:
    """対象年月の全メンバー分の不都合日を読み込む(読み取り専用)。

    v2シートとレガシーシートを統合するが、統合はメンバー単位で行う
    (同一メンバーの新旧データを日付レベルで混ぜない)。あるメンバーが
    v2に記録を持っていれば、そのメンバーについては常にv2の内容のみを
    採用する(=v2の新しいデータがレガシーの古いデータで上書きされたり、
    逆にレガシーの古いデータがv2より優先されたりすることはない)。
    v2に記録の無いメンバーだけ、レガシーシートの内容で補完する。
    """
    v2 = _fetch_v2_month_records(year, month)
    if v2 is not None:
        legacy = _fetch_legacy_month_records(year, month) or {}
        merged: Dict[str, Dict[str, dict]] = dict(legacy)
        merged.update(v2)  # メンバー単位でv2が常に優先(新しい保存方式が正)
        _write_local_json(_unavailability_path(year, month), merged)
        return merged

    # Sheets未接続、または読み込み失敗時はローカルキャッシュにフォールバック
    path = _unavailability_path(year, month)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_local_unavailability(year: int, month: int) -> Dict[str, Dict[str, dict]]:
    """ローカルJSONキャッシュのみを読む(Sheetsへはアクセスしない)。
    管理者による「ローカルの内容を一括でSheetsへ反映する」用途向け。"""
    return _read_local_json(_unavailability_path(year, month), {})


# ----------------------------------------------------------------------
# 保存: member × year_month を単位とした安全な保存(v2)
# ----------------------------------------------------------------------

def _save_member_month_to_sheets(year: int, month: int, member_name: str, day_map: Dict[str, dict]) -> bool:
    """1名・1ヶ月分をv2シートへ安全にupsertする。ws.clear()は使わない。
    他メンバー・他月の行には一切触れない。"""
    if not sheets_backend.is_configured():
        return False
    from datetime import datetime as _dt

    ym = _month_key_dash(year, month)
    row = {
        "member_name": member_name,
        "year_month": ym,
        "days_json": json.dumps(day_map, ensure_ascii=False),
        "updated_at": _dt.now().isoformat(),
    }
    return sheets_backend.upsert_keyed_row(
        SHEET_UNAVAILABILITY_V2,
        UNAVAILABILITY_V2_HEADER,
        UNAVAILABILITY_V2_KEY_COLS,
        {"member_name": member_name, "year_month": ym},
        row,
    )


def _append_unavailability_history(year: int, month: int, member_name: str, day_map: Dict[str, dict]) -> None:
    """保存成功時のみ呼ばれる。append-onlyで履歴を1行追加する(失敗は無視)。"""
    if not sheets_backend.is_configured():
        return
    from datetime import datetime as _dt

    row = {
        "member_name": member_name,
        "year_month": _month_key_dash(year, month),
        "days_json": json.dumps(day_map, ensure_ascii=False),
        "saved_at": _dt.now().isoformat(),
    }
    sheets_backend.append_row_safe(SHEET_UNAVAILABILITY_HISTORY, UNAVAILABILITY_HISTORY_HEADER, row)


def save_member_unavailability(
    year: int, month: int, member_name: str, day_map: Dict[str, dict]
) -> Tuple[bool, str]:
    """member_name 1名・1ヶ月分の不都合日を保存する唯一の入口。

    - ローカルJSONキャッシュ(その月の全メンバー分)を更新する(他メンバー分は保持)
    - Google Sheetsが設定されていれば「不都合日入力_v2」へ対象行だけを
      安全にupsertする(ws.clear()は一切使わない。他メンバー・他月の行は
      絶対に変更しない)
    - Sheetsへの保存に成功した場合のみ、履歴シートへ1行追記する
    - タップ操作からは呼ばない(呼び出しは「保存」ボタン押下時のみ)

    仕様(同一member×year_monthを複数セッションが同時編集した場合):
    内部でsheets_backend.upsert_keyed_row()を使うため、
    同一メンバー・同一年月を2つのブラウザセッション(同じ医師の複数タブ、
    または管理者操作とメンバー操作が重なった場合など)がほぼ同時に保存
    すると last-write-wins になる。すなわち、後からGoogle Sheetsへの
    書き込みが完了した方の内容がそのまま残り、2つの入力内容がマージ
    されることはない。異なるメンバー・異なる年月同士の保存は、
    タイミングに関わらず互いに影響しない(対象行が異なるため)。
    詳細は sheets_backend.upsert_keyed_row() のdocstringを参照。

    戻り値: (成功したか, 表示用メッセージ)
    """
    cleaned = _clean_day_map(day_map)

    try:
        local_data = get_local_unavailability(year, month)
        if cleaned:
            local_data[member_name] = cleaned
        else:
            local_data.pop(member_name, None)
        _write_local_json(_unavailability_path(year, month), local_data)
    except Exception as e:  # noqa: BLE001
        return False, f"ローカル保存に失敗しました: {e}"

    touch_last_updated(year, month, member_name)

    if not sheets_backend.is_configured():
        return True, "保存しました(ローカル)。"

    ok = _save_member_month_to_sheets(year, month, member_name, cleaned)
    if ok:
        _append_unavailability_history(year, month, member_name, cleaned)
        set_member_sheets_sync(year, month, member_name, kind="saved")
        return True, "保存しました(Googleスプレッドシートにも反映済みです)。"

    return False, "Googleスプレッドシートへの反映に失敗しました。画面上の入力内容は保持されています。しばらくしてから再度保存してください。"


# ----------------------------------------------------------------------
# タップ操作用の純粋関数(Sheets/ローカルには一切アクセスしない)
# ----------------------------------------------------------------------

def state_from_flags(flags: Optional[dict]) -> str:
    """{"day": bool, "night": bool} から状態文字列を求める(副作用なし)。"""
    if not flags:
        return STATE_OK
    d, n = bool(flags.get("day", False)), bool(flags.get("night", False))
    if d and n:
        return STATE_FULL_OFF
    if d:
        return STATE_DAY_OFF
    if n:
        return STATE_NIGHT_OFF
    return STATE_OK


def next_cycle_state(state: str) -> str:
    """状態を次の状態に進める(副作用なし)。"""
    return STATE_ORDER[(STATE_ORDER.index(state) + 1) % len(STATE_ORDER)]


def flags_from_state(state: str) -> Optional[dict]:
    """状態文字列から {"day": bool, "night": bool} を求める。
    STATE_OKの場合はNone(=保存しない)を返す(副作用なし)。"""
    if state == STATE_OK:
        return None
    return {
        "day": state in (STATE_DAY_OFF, STATE_FULL_OFF),
        "night": state in (STATE_NIGHT_OFF, STATE_FULL_OFF),
    }


# ----------------------------------------------------------------------
# 後方互換API(1日単位の即時保存)。テスト・スクリプト等から使う場合のみ。
# タップ操作の経路としては使わない(保存はセッション状態に留め、保存ボタン
# 押下時にsave_member_unavailability()を1回だけ呼ぶ)。
# 内部的にはsave_member_unavailability()(=v2への安全なupsert)を経由する
# ため、ここを呼んでもシート全体のclearは発生しない。
# ----------------------------------------------------------------------

def get_member_day_state(year: int, month: int, member_name: str, day_str: str) -> str:
    data = load_unavailability_raw(year, month)
    return state_from_flags(data.get(member_name, {}).get(day_str))


def set_member_day_state(year: int, month: int, member_name: str, day_str: str, state: str) -> None:
    data = load_unavailability_raw(year, month)
    member_data = dict(data.get(member_name, {}))
    flags = flags_from_state(state)
    if flags is None:
        member_data.pop(day_str, None)
    else:
        member_data[day_str] = flags
    save_member_unavailability(year, month, member_name, member_data)


def cycle_member_day_state(year: int, month: int, member_name: str, day_str: str) -> str:
    """状態を次の状態に巡回させ、保存後の新しい状態を返す"""
    current = get_member_day_state(year, month, member_name, day_str)
    nxt = next_cycle_state(current)
    set_member_day_state(year, month, member_name, day_str, nxt)
    return nxt


# ----------------------------------------------------------------------
# 旧形式 → v2 への移行(手動実行のみ。自動実行はしない)
# ----------------------------------------------------------------------

def _build_legacy_month_groups() -> Dict[Tuple[str, str], Dict[str, dict]]:
    """旧シート「不都合日入力」全体を読み込み、(member_name, year_month) ごとに
    day_mapへグルーピングする。読み取り専用(旧シートは一切変更しない)。"""
    rows = sheets_backend.read_table(SHEET_UNAVAILABILITY)
    if rows is None:
        return {}
    groups: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for r in rows:
        date_str = str(r.get("date", "")).strip()
        if len(date_str) < 7:
            continue
        ym = date_str[:7]
        member_name = str(r.get("member_name", "")).strip()
        if not member_name:
            continue
        d = str(r.get("day_unavailable", "")).strip().lower() in ("1", "true")
        n = str(r.get("night_unavailable", "")).strip().lower() in ("1", "true")
        if not (d or n):
            continue
        groups.setdefault((member_name, ym), {})[date_str] = {"day": d, "night": n}
    return groups


def _read_v2_all_keys_and_data() -> Dict[Tuple[str, str], Dict[str, dict]]:
    """v2シートの全行を (member_name, year_month) -> day_map の形で読み込む(読み取り専用)。"""
    rows = sheets_backend.read_table(SHEET_UNAVAILABILITY_V2) or []
    result: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for r in rows:
        key = (str(r.get("member_name", "")), str(r.get("year_month", "")))
        raw_json = r.get("days_json", "")
        try:
            result[key] = json.loads(raw_json) if raw_json else {}
        except (json.JSONDecodeError, TypeError):
            result[key] = {}
    return result


def migrate_unavailability_to_v2(skip_existing: bool = True) -> dict:
    """
    旧シート「不都合日入力」(1日1行)の全データを (member_name, year_month)
    ごとにグルーピングし、v2シート「不都合日入力_v2」へ安全にupsertする。

    - 対象行の更新・追記にのみ ws.clear() を使わない upsert_keyed_row() を使う
      (シート全体のclearは一切発生しない)
    - skip_existing=True(既定)の場合、v2に既に存在する
      (member_name, year_month) の組は上書きしない。これにより、
      メンバーが移行後に保存した新しいデータが、移行処理の再実行によって
      古いレガシーデータで上書きされることはない
    - 旧シートは一切変更しない(バックアップとしてそのまま残る)
    - 何度実行しても結果は同じになる(冪等)

    戻り値: {"total": 対象組数, "migrated": 新規作成数, "skipped": 既存のためスキップした数,
             "failed": 失敗数, "errors": [失敗したキーのリスト]}
    """
    empty_result = {"total": 0, "migrated": 0, "skipped": 0, "failed": 0, "errors": []}
    if not sheets_backend.is_configured():
        return dict(empty_result, errors=["Googleスプレッドシート連携が設定されていません。"])

    groups = _build_legacy_month_groups()
    existing_keys = set(_read_v2_all_keys_and_data().keys()) if skip_existing else set()

    result = dict(empty_result)
    result["total"] = len(groups)
    from datetime import datetime as _dt

    for (member_name, ym), day_map in sorted(groups.items()):
        if skip_existing and (member_name, ym) in existing_keys:
            result["skipped"] += 1
            continue
        row = {
            "member_name": member_name,
            "year_month": ym,
            "days_json": json.dumps(day_map, ensure_ascii=False),
            "updated_at": _dt.now().isoformat(),
        }
        ok = sheets_backend.upsert_keyed_row(
            SHEET_UNAVAILABILITY_V2,
            UNAVAILABILITY_V2_HEADER,
            UNAVAILABILITY_V2_KEY_COLS,
            {"member_name": member_name, "year_month": ym},
            row,
        )
        if ok:
            result["migrated"] += 1
        else:
            result["failed"] += 1
            result["errors"].append(f"{member_name} / {ym}")
    return result


def verify_unavailability_migration() -> dict:
    """
    旧シートとv2シートの内容を (member_name, year_month) ごとに比較検証する
    (読み取り専用。どちらのシートも一切変更しない)。

    戻り値: {
        "total_legacy_keys": 旧シート側に存在する組の数,
        "matched": 一致した組の数,
        "mismatched": [(member_name, year_month), ...],  # 内容が一致しない組
        "missing_in_v2": [(member_name, year_month), ...],  # v2にまだ存在しない組
    }
    """
    empty_result = {"total_legacy_keys": 0, "matched": 0, "mismatched": [], "missing_in_v2": []}
    if not sheets_backend.is_configured():
        return empty_result

    legacy_groups = _build_legacy_month_groups()
    v2_data = _read_v2_all_keys_and_data()

    matched = 0
    mismatched: List[Tuple[str, str]] = []
    missing: List[Tuple[str, str]] = []

    for key, legacy_map in sorted(legacy_groups.items()):
        if key not in v2_data:
            missing.append(key)
            continue
        norm_legacy = _clean_day_map(legacy_map)
        norm_v2 = _clean_day_map(v2_data[key])
        if norm_legacy == norm_v2:
            matched += 1
        else:
            mismatched.append(key)

    return {
        "total_legacy_keys": len(legacy_groups),
        "matched": matched,
        "mismatched": mismatched,
        "missing_in_v2": missing,
    }


def get_unavailability_objects(year: int, month: int) -> List[Unavailability]:
    """
    メンバーが入力した不都合日を、最適化エンジン用の Unavailability に変換する。

    長期不在は独立した入力項目としては持たず、終日不可が連続7日以上続く場合に
    目標回数の自動計算でのみ長期不在相当として扱う。割当制約としては、入力済みの
    終日不可そのものがそのまま使われる。
    """
    from datetime import datetime

    data = load_unavailability_raw(year, month)
    results: List[Unavailability] = []

    for member_name, days in data.items():
        for day_str, flags in days.items():
            d = datetime.strptime(day_str, "%Y-%m-%d").date()
            results.append(
                Unavailability(
                    member_name=member_name,
                    day=d,
                    day_unavailable=bool(flags.get("day", False)),
                    night_unavailable=bool(flags.get("night", False)),
                )
            )

    return results


def get_submission_stats(year: int, month: int) -> Dict[str, int]:
    """メンバーごとの「不都合日として入力した日数」の統計(将来の集計機能用)"""
    data = load_unavailability_raw(year, month)
    return {name: len(days) for name, days in data.items()}


def replace_member_unavailability(year: int, month: int, member_name: str, day_map: Dict[str, dict]) -> None:
    """
    member_name 1名分の不都合日データで「ローカルキャッシュ」だけを置き換える。
    day_map は {"YYYY-MM-DD": {"day": bool, "night": bool}, ...} の形式。
    他メンバーのローカルデータは変更しない。

    注意: この関数はSheetsへは一切書き込まない(読み込んだ内容をローカルに
    反映するだけの用途)。Sheetsへ保存したい場合は save_member_unavailability()
    を使うこと。
    """
    data = get_local_unavailability(year, month)
    cleaned = _clean_day_map(day_map)
    if cleaned:
        data[member_name] = cleaned
    else:
        data.pop(member_name, None)
    _write_local_json(_unavailability_path(year, month), data)


def replace_all_unavailability(year: int, month: int, by_member: Dict[str, Dict[str, dict]]) -> None:
    """
    全メンバー分の不都合日データで「ローカルキャッシュ」だけを置き換える
    (Sheetsの最新内容をローカルへ反映する用途)。
    by_member は {"名前": {"YYYY-MM-DD": {"day": bool, "night": bool}, ...}, ...} の形式。

    注意: この関数はSheetsへは一切書き込まない。
    """
    cleaned: Dict[str, Dict[str, dict]] = {
        member_name: _clean_day_map(day_map) for member_name, day_map in by_member.items()
    }
    _write_local_json(_unavailability_path(year, month), cleaned)


# ----------------------------------------------------------------------
# 最終更新日時(入力状況の可視化用)
# ----------------------------------------------------------------------

def _last_updated_path(year: int, month: int) -> Path:
    return DATA_DIR / f"last_updated_{_month_key(year, month)}.json"


def touch_last_updated(year: int, month: int, member_name: str) -> None:
    from datetime import datetime as _dt

    key = f"last_updated_{_month_key(year, month)}"
    data = _state_load(key, _last_updated_path(year, month), {})
    data[member_name] = _dt.now().isoformat()
    _state_save(key, _last_updated_path(year, month), data)


def get_last_updated(year: int, month: int) -> Dict[str, str]:
    """{名前: ISO形式のタイムスタンプ文字列} を返す"""
    return _state_load(f"last_updated_{_month_key(year, month)}", _last_updated_path(year, month), {})


# ----------------------------------------------------------------------
# Googleスプレッドシートとの最終同期時刻
# ----------------------------------------------------------------------


def _sheets_sync_path(year: int, month: int) -> Path:
    return DATA_DIR / f"sheets_sync_{_month_key(year, month)}.json"


def _load_sheets_sync(year: int, month: int) -> dict:
    data = _state_load(
        f"sheets_sync_{_month_key(year, month)}", _sheets_sync_path(year, month), {"members": {}, "admin": {}}
    )
    data.setdefault("members", {})
    data.setdefault("admin", {})
    return data


def _save_sheets_sync(year: int, month: int, data: dict) -> None:
    _state_save(f"sheets_sync_{_month_key(year, month)}", _sheets_sync_path(year, month), data)


def set_member_sheets_sync(year: int, month: int, member_name: str, kind: str) -> None:
    """kind は 'saved' または 'loaded'。医師本人の最終同期時刻を記録する。"""
    from datetime import datetime as _dt

    data = _load_sheets_sync(year, month)
    member_record = data["members"].setdefault(member_name, {})
    member_record[kind] = _dt.now().isoformat()
    _save_sheets_sync(year, month, data)


def get_member_sheets_sync(year: int, month: int, member_name: str) -> Dict[str, str]:
    """{"saved": ISO文字列, "loaded": ISO文字列} を返す(無ければキー無し)"""
    data = _load_sheets_sync(year, month)
    return data["members"].get(member_name, {})


def set_admin_sheets_sync(year: int, month: int, kind: str) -> None:
    """kind は 'saved' または 'loaded'。管理者による一括同期の最終時刻を記録する。"""
    from datetime import datetime as _dt

    data = _load_sheets_sync(year, month)
    data["admin"][kind] = _dt.now().isoformat()
    _save_sheets_sync(year, month, data)


def get_admin_sheets_sync(year: int, month: int) -> Dict[str, str]:
    """{"saved": ISO文字列, "loaded": ISO文字列} を返す(無ければキー無し)"""
    data = _load_sheets_sync(year, month)
    return data["admin"]


# ----------------------------------------------------------------------
# 入力締切
# ----------------------------------------------------------------------

def set_deadline(deadline_str: Optional[str]) -> None:
    """deadline_str は 'YYYY-MM-DD' 形式。None または空文字で締切を解除する。"""
    config = load_config()
    config["submission_deadline"] = deadline_str or None
    save_config(config)


def get_deadline() -> Optional[date]:
    from datetime import datetime as _dt

    config = load_config()
    raw = config.get("submission_deadline")
    if not raw:
        return None
    try:
        return _dt.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


# ----------------------------------------------------------------------
# Googleスプレッドシートへの自動同期設定
# ----------------------------------------------------------------------

def set_auto_sync_settings(enabled: bool, spreadsheet_key: str) -> None:
    config = load_config()
    config["auto_sync_sheets"] = bool(enabled)
    config["sheets_spreadsheet_key"] = spreadsheet_key or ""
    save_config(config)


def get_auto_sync_settings() -> Dict[str, object]:
    config = load_config()
    return {
        "enabled": bool(config.get("auto_sync_sheets", False)),
        "spreadsheet_key": config.get("sheets_spreadsheet_key", ""),
    }


# ----------------------------------------------------------------------
# 勤務表の確定状態
# ----------------------------------------------------------------------

def _finalized_path(year: int, month: int) -> Path:
    return DATA_DIR / f"finalized_{_month_key(year, month)}.json"


def mark_finalized(year: int, month: int) -> None:
    """
    予定勤務表(scheduled_assignments)を確定する。
    確定した瞬間の予定をそのまま実績(actual_assignments)の初期値としてコピーする
    (まだ実績スナップショットが無い場合のみ。既にある場合は上書きしない)。
    """
    from datetime import datetime as _dt

    _state_save(
        f"finalized_{_month_key(year, month)}",
        _finalized_path(year, month),
        {"finalized_at": _dt.now().isoformat()},
    )

    if load_actual_snapshot(year, month) is None:
        scheduled = load_schedule_snapshot(year, month)
        if scheduled is not None:
            save_actual_snapshot(year, month, scheduled)


def get_finalized_info(year: int, month: int) -> Optional[dict]:
    return _state_load(f"finalized_{_month_key(year, month)}", _finalized_path(year, month), None)


def is_finalized(year: int, month: int) -> bool:
    return get_finalized_info(year, month) is not None


def clear_finalized(year: int, month: int) -> None:
    _state_clear(f"finalized_{_month_key(year, month)}", _finalized_path(year, month))


# ----------------------------------------------------------------------
# 予定勤務表(scheduled_assignments)のスナップショット
# (セッションをまたいで結果を表示するための保存。年間集計には使わない)
# ----------------------------------------------------------------------

def _schedule_snapshot_path(year: int, month: int) -> Path:
    return DATA_DIR / f"schedule_{_month_key(year, month)}.json"


def save_schedule_snapshot(year: int, month: int, snapshot: dict) -> None:
    _write_local_json(_schedule_snapshot_path(year, month), snapshot)
    if sheets_backend.is_configured():
        sheets_backend.write_blob(SHEET_ASSIGNMENTS, _month_key(year, month), snapshot)


def load_schedule_snapshot(year: int, month: int) -> Optional[dict]:
    if sheets_backend.is_configured():
        remote = sheets_backend.read_blob(SHEET_ASSIGNMENTS, _month_key(year, month))
        if remote is not None:
            _write_local_json(_schedule_snapshot_path(year, month), remote)
            return remote

    path = _schedule_snapshot_path(year, month)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def update_schedule_entries(year: int, month: int, updated_entries: List[dict]) -> Optional[dict]:
    """
    予定勤務表(scheduled_assignments)の担当者を管理者が直接修正する。

    updated_entries: [{"date": "YYYY-MM-DD", "day": 名前 or None,
                        "night": 名前 or None, "gaikobu": 名前 or None}, ...]
    既存スナップショットの entries を丸ごと置き換え、statsを再計算して保存する
    (target/diffは既存statsの目標値を維持する)。
    確定前・確定後のどちらでも呼び出せる。この関数はactual_assignmentsには
    一切影響しない。
    保存後のスナップショットを返す(該当月のスナップショットが無ければ None)。
    """
    snapshot = load_schedule_snapshot(year, month)
    if snapshot is None:
        return None
    by_date = {e["date"]: e for e in updated_entries}
    for e in snapshot["entries"]:
        u = by_date.get(e["date"])
        if u is not None:
            e["day"] = u.get("day")
            e["night"] = u.get("night")
            e["gaikobu"] = u.get("gaikobu")
    snapshot["stats"] = _recompute_actual_stats(snapshot)
    save_schedule_snapshot(year, month, snapshot)
    return snapshot


def copy_schedule_to_actual(year: int, month: int) -> bool:
    """
    予定勤務表(scheduled_assignments)の現在の内容を、実績勤務表
    (actual_assignments)へ上書きコピーする(管理者が明示的に実行した場合のみ)。
    実績側のtarget(目標)は既存の実績statsの値を維持する。
    成功したら True、コピー元の予定がまだ無ければ False を返す。
    """
    schedule_snapshot = load_schedule_snapshot(year, month)
    if schedule_snapshot is None:
        return False

    actual_snapshot = load_actual_snapshot(year, month)
    if actual_snapshot is None:
        # 実績がまだ無い場合は、予定をそのままコピーして初期化する
        save_actual_snapshot(year, month, schedule_snapshot)
    else:
        by_date = {e["date"]: e for e in schedule_snapshot["entries"]}
        for e in actual_snapshot["entries"]:
            u = by_date.get(e["date"])
            if u is not None:
                e["day"] = u.get("day")
                e["night"] = u.get("night")
                e["gaikobu"] = u.get("gaikobu")
        actual_snapshot["stats"] = _recompute_actual_stats(actual_snapshot)
        save_actual_snapshot(year, month, actual_snapshot)

    from datetime import datetime as _dt

    history = _load_actual_edit_history()
    history.append(
        {
            "year": year,
            "month": month,
            "date": "(全日)",
            "slot_type": "bulk_copy",
            "old_member": None,
            "new_member": None,
            "reason": "予定を実績へ反映(一括コピー)",
            "edited_by": "admin",
            "edited_at": _dt.now().isoformat(),
        }
    )
    _save_actual_edit_history(history)
    return True


# ----------------------------------------------------------------------
# 外部病院バイト対象日
# ----------------------------------------------------------------------

def _gaikobu_days_path(year: int, month: int) -> Path:
    return DATA_DIR / f"gaikobu_days_{_month_key(year, month)}.json"


def get_gaikobu_days(year: int, month: int) -> List[str]:
    """'YYYY-MM-DD' 形式の文字列リストを返す"""
    return _state_load(f"gaikobu_days_{_month_key(year, month)}", _gaikobu_days_path(year, month), [])


def set_gaikobu_days(year: int, month: int, day_strs: List[str]) -> None:
    _state_save(
        f"gaikobu_days_{_month_key(year, month)}",
        _gaikobu_days_path(year, month),
        sorted(set(day_strs)),
    )


def toggle_gaikobu_day(year: int, month: int, day_str: str) -> bool:
    """指定日をON/OFF切り替える。切り替え後の状態(True=対象日)を返す"""
    days = set(get_gaikobu_days(year, month))
    if day_str in days:
        days.remove(day_str)
        is_on = False
    else:
        days.add(day_str)
        is_on = True
    set_gaikobu_days(year, month, list(days))
    return is_on


def get_gaikobu_days_as_dates(year: int, month: int) -> set:
    from datetime import datetime as _dt

    return {_dt.strptime(s, "%Y-%m-%d").date() for s in get_gaikobu_days(year, month)}


# ----------------------------------------------------------------------
# 実績勤務表(actual_assignments)
#
# 自動生成直後は予定(scheduled_assignments)と同じ内容で初期化されるが、
# その後は勤務交代(swap)や管理者による手動修正を経て予定から独立して
# 更新されていく。年間集計・翌月以降の均等化には必ずこちらを使用する。
# ----------------------------------------------------------------------

def _actual_snapshot_path(year: int, month: int) -> Path:
    return DATA_DIR / f"actual_{_month_key(year, month)}.json"


def save_actual_snapshot(year: int, month: int, snapshot: dict) -> None:
    _write_local_json(_actual_snapshot_path(year, month), snapshot)
    if sheets_backend.is_configured():
        sheets_backend.write_blob(SHEET_ACTUAL_ASSIGNMENTS, _month_key(year, month), snapshot)


def load_actual_snapshot(year: int, month: int) -> Optional[dict]:
    if sheets_backend.is_configured():
        remote = sheets_backend.read_blob(SHEET_ACTUAL_ASSIGNMENTS, _month_key(year, month))
        if remote is not None:
            _write_local_json(_actual_snapshot_path(year, month), remote)
            return remote

    path = _actual_snapshot_path(year, month)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_actual_slot(year: int, month: int, day_str: str, slot_type: str) -> Optional[str]:
    """slot_type は 'day' / 'night' / 'gaikobu'。該当日の実績担当者名を返す。"""
    snapshot = load_actual_snapshot(year, month)
    if not snapshot:
        return None
    for e in snapshot.get("entries", []):
        if e["date"] == day_str:
            return e.get(slot_type)
    return None


def set_actual_slot(year: int, month: int, day_str: str, slot_type: str, new_member: Optional[str]) -> bool:
    """
    実績のある1枠(日付+slot_type)の担当者を書き換える。
    成功したら True、該当日が実績スナップショットに存在しなければ False を返す。
    """
    snapshot = load_actual_snapshot(year, month)
    if not snapshot:
        return False
    found = False
    for e in snapshot.get("entries", []):
        if e["date"] == day_str:
            e[slot_type] = new_member
            found = True
            break
    if not found:
        return False
    # statsを再計算する
    snapshot["stats"] = _recompute_actual_stats(snapshot)
    save_actual_snapshot(year, month, snapshot)
    return True


def _recompute_actual_stats(snapshot: dict) -> Dict[str, Dict[str, int]]:
    """entries(実績)から集計(day/night/total/gaikobu/grand_total)を再計算する。
    目標(target)・差(diff)は元のstatsの値をそのまま維持する(目標回数は
    予定作成時のものを表示用に引き続き使う)。"""
    old_stats = snapshot.get("stats", {})
    names = set(old_stats.keys())
    for e in snapshot.get("entries", []):
        for key in ("day", "night", "gaikobu"):
            if e.get(key):
                names.add(e[key])

    new_stats: Dict[str, Dict[str, int]] = {
        name: {
            "day": 0,
            "night": 0,
            "total": 0,
            "target": old_stats.get(name, {}).get("target", 0),
            "diff": 0,
            "gaikobu": 0,
            "grand_total": 0,
        }
        for name in names
    }
    for e in snapshot.get("entries", []):
        if e.get("day"):
            new_stats[e["day"]]["day"] += 1
            new_stats[e["day"]]["total"] += 1
        if e.get("night"):
            new_stats[e["night"]]["night"] += 1
            new_stats[e["night"]]["total"] += 1
        if e.get("gaikobu"):
            new_stats[e["gaikobu"]]["gaikobu"] += 1
    for name in new_stats:
        new_stats[name]["diff"] = new_stats[name]["total"] - new_stats[name]["target"]
        new_stats[name]["grand_total"] = new_stats[name]["total"] + new_stats[name]["gaikobu"]
    return new_stats


def check_actual_conflict(year: int, month: int, day_str: str, slot_type: str, member_name: str) -> Optional[str]:
    """
    その日に member_name を新たに割り当てた場合、既存の実績と矛盾(同日に
    日中・夜間・外部バイトのうち複数を担当することになる等)が起きないかを
    確認する。問題があればその理由の文字列を返し、問題無ければ None を返す。
    """
    snapshot = load_actual_snapshot(year, month)
    if not snapshot:
        return None
    for e in snapshot.get("entries", []):
        if e["date"] != day_str:
            continue
        occupied_slots = [s for s in ("day", "night", "gaikobu") if e.get(s) == member_name and s != slot_type]
        if occupied_slots:
            return f"{member_name}さんは{day_str}に既に別の枠({occupied_slots[0]})を担当しています"
    return None


# ----------------------------------------------------------------------
# 実績の手動修正履歴
# ----------------------------------------------------------------------

_ACTUAL_EDIT_HISTORY_PATH = DATA_DIR / "actual_edit_history.json"

SLOT_TYPE_LABEL = {"day": "日中", "night": "夜間", "gaikobu": "外部バイト"}


def _load_actual_edit_history() -> List[dict]:
    return _state_load("actual_edit_history", _ACTUAL_EDIT_HISTORY_PATH, [])


def _save_actual_edit_history(records: List[dict]) -> None:
    _state_save("actual_edit_history", _ACTUAL_EDIT_HISTORY_PATH, records)


def edit_actual_assignment(
    year: int,
    month: int,
    day_str: str,
    slot_type: str,
    new_member: Optional[str],
    reason: str,
    edited_by: str = "admin",
) -> bool:
    """
    管理者による実績の手動修正(急な交代・病欠・LINE上での交代済み・
    外部バイトキャンセルなど)。修正履歴も保存する。
    """
    from datetime import datetime as _dt

    old_member = get_actual_slot(year, month, day_str, slot_type)
    success = set_actual_slot(year, month, day_str, slot_type, new_member)
    if not success:
        return False

    history = _load_actual_edit_history()
    history.append(
        {
            "year": year,
            "month": month,
            "date": day_str,
            "slot_type": slot_type,
            "old_member": old_member,
            "new_member": new_member,
            "reason": reason,
            "edited_by": edited_by,
            "edited_at": _dt.now().isoformat(),
        }
    )
    _save_actual_edit_history(history)
    return True


def get_actual_edit_history(year: Optional[int] = None, month: Optional[int] = None) -> List[dict]:
    history = _load_actual_edit_history()
    if year is not None:
        history = [h for h in history if h["year"] == year]
    if month is not None:
        history = [h for h in history if h["month"] == month]
    return sorted(history, key=lambda h: h["edited_at"], reverse=True)


# ----------------------------------------------------------------------
# 勤務交代(swap)
# ----------------------------------------------------------------------

_SWAP_REQUESTS_PATH = DATA_DIR / "swap_requests.json"

SWAP_STATUS_PENDING = "pending"
SWAP_STATUS_APPROVED = "approved"
SWAP_STATUS_REJECTED = "rejected"

SWAP_STATUS_LABEL = {
    SWAP_STATUS_PENDING: "保留中",
    SWAP_STATUS_APPROVED: "承認済み",
    SWAP_STATUS_REJECTED: "却下",
}


def _load_swap_requests() -> List[dict]:
    return _state_load("swap_requests", _SWAP_REQUESTS_PATH, [])


def _save_swap_requests(records: List[dict]) -> None:
    _state_save("swap_requests", _SWAP_REQUESTS_PATH, records)


def create_swap_request(
    year: int, month: int, day_str: str, slot_type: str, from_member: str, to_member: str
) -> str:
    """
    予定担当者(from_member)が、指定の勤務(日付+勤務種別)を to_member に
    交代してほしいという依頼を作成する。まだ実績には反映されない
    (to_member が承認した時点で反映される)。
    """
    import uuid
    from datetime import datetime as _dt

    requests = _load_swap_requests()
    request_id = uuid.uuid4().hex[:12]
    requests.append(
        {
            "id": request_id,
            "year": year,
            "month": month,
            "date": day_str,
            "slot_type": slot_type,
            "from_member": from_member,
            "to_member": to_member,
            "requested_at": _dt.now().isoformat(),
            "approved_at": None,
            "status": SWAP_STATUS_PENDING,
        }
    )
    _save_swap_requests(requests)
    return request_id


def get_swap_requests(
    year: Optional[int] = None,
    month: Optional[int] = None,
    status: Optional[str] = None,
    member_name: Optional[str] = None,
) -> List[dict]:
    requests = _load_swap_requests()
    if year is not None:
        requests = [r for r in requests if r["year"] == year]
    if month is not None:
        requests = [r for r in requests if r["month"] == month]
    if status is not None:
        requests = [r for r in requests if r["status"] == status]
    if member_name is not None:
        requests = [r for r in requests if r["from_member"] == member_name or r["to_member"] == member_name]
    return sorted(requests, key=lambda r: r["requested_at"], reverse=True)


def get_swap_request(request_id: str) -> Optional[dict]:
    for r in _load_swap_requests():
        if r["id"] == request_id:
            return r
    return None


def respond_to_swap_request(request_id: str, approve: bool) -> str:
    """
    交代相手(to_member)が依頼を承認/却下する。
    承認された場合は、その場で actual_assignments を更新する
    (元の担当者(from_member)を外し、交代後の担当者(to_member)に置き換える)。
    戻り値: "approved" / "rejected" / エラーメッセージ
    """
    from datetime import datetime as _dt

    requests = _load_swap_requests()
    target = None
    for r in requests:
        if r["id"] == request_id:
            target = r
            break
    if target is None:
        return "交代依頼が見つかりません"
    if target["status"] != SWAP_STATUS_PENDING:
        return f"この依頼は既に処理済みです({SWAP_STATUS_LABEL.get(target['status'], target['status'])})"

    if not approve:
        target["status"] = SWAP_STATUS_REJECTED
        target["approved_at"] = _dt.now().isoformat()
        _save_swap_requests(requests)
        return "rejected"

    year, month = target["year"], target["month"]
    day_str, slot_type = target["date"], target["slot_type"]
    to_member = target["to_member"]

    conflict = check_actual_conflict(year, month, day_str, slot_type, to_member)
    if conflict:
        return f"承認できません: {conflict}"

    success = set_actual_slot(year, month, day_str, slot_type, to_member)
    if not success:
        return "実績データが見つからないため反映できませんでした"

    target["status"] = SWAP_STATUS_APPROVED
    target["approved_at"] = _dt.now().isoformat()
    _save_swap_requests(requests)
    return "approved"


# ----------------------------------------------------------------------
# 月末の実績確定・年間実績集計
# ----------------------------------------------------------------------

def _actual_finalized_path(year: int, month: int) -> Path:
    return DATA_DIR / f"actual_finalized_{_month_key(year, month)}.json"


def mark_actual_finalized(year: int, month: int) -> None:
    """
    月末に実績を確定する。確定した月のactual_assignmentsが年間集計に
    反映されるようになる。確定後も管理者は手動修正できる
    (実績修正機能自体がもともと管理者専用のため)。
    """
    from datetime import datetime as _dt

    _state_save(
        f"actual_finalized_{_month_key(year, month)}",
        _actual_finalized_path(year, month),
        {"actual_finalized_at": _dt.now().isoformat()},
    )


def get_actual_finalized_info(year: int, month: int) -> Optional[dict]:
    return _state_load(
        f"actual_finalized_{_month_key(year, month)}", _actual_finalized_path(year, month), None
    )


def is_actual_finalized(year: int, month: int) -> bool:
    return get_actual_finalized_info(year, month) is not None


def clear_actual_finalized(year: int, month: int) -> None:
    _state_clear(f"actual_finalized_{_month_key(year, month)}", _actual_finalized_path(year, month))


def get_annual_actual_totals(year: int, upto_month: int = 12) -> Dict[str, Dict[str, int]]:
    """
    指定年のうち、実績確定済み(is_actual_finalized)の月だけを対象に、
    メンバーごとの実績を年間で合計する。
    戻り値: {名前: {"day":n, "night":n, "total":n, "gaikobu":n, "grand_total":n}}
    """
    totals: Dict[str, Dict[str, int]] = {
        m["name"]: {"day": 0, "night": 0, "total": 0, "gaikobu": 0, "grand_total": 0} for m in get_members()
    }
    for month in range(1, upto_month + 1):
        if not is_actual_finalized(year, month):
            continue
        snapshot = load_actual_snapshot(year, month)
        if not snapshot:
            continue
        for name, s in snapshot.get("stats", {}).items():
            if name not in totals:
                totals[name] = {"day": 0, "night": 0, "total": 0, "gaikobu": 0, "grand_total": 0}
            totals[name]["day"] += s.get("day", 0)
            totals[name]["night"] += s.get("night", 0)
            totals[name]["total"] += s.get("total", 0)
            totals[name]["gaikobu"] += s.get("gaikobu", 0)
            totals[name]["grand_total"] += s.get("grand_total", 0)
    return totals


def get_annual_actual_own_totals(year: int, upto_month: int = 12) -> Dict[str, int]:
    """自院オンコール(日中+夜間)の年間実績のみを {名前: 回数} で返す
    (翌月以降の自動割当で参照する年間均等化の入力に使う)。"""
    totals = get_annual_actual_totals(year, upto_month=upto_month)
    return {name: s["total"] for name, s in totals.items()}
