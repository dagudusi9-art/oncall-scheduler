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
from typing import Dict, List, Optional

import sys

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))

from src.models import Member, Unavailability  # noqa: E402

DATA_DIR = _PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"

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


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)
    # 旧バージョンのconfig.jsonに無いキーはデフォルト値で補完する(後方互換性)
    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            changed = True
    if changed:
        save_config(config)
    return config


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


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

def load_unavailability_raw(year: int, month: int) -> Dict[str, Dict[str, dict]]:
    path = _unavailability_path(year, month)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_unavailability_raw(year: int, month: int, data: Dict[str, Dict[str, dict]]) -> None:
    path = _unavailability_path(year, month)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_member_day_state(year: int, month: int, member_name: str, day_str: str) -> str:
    data = load_unavailability_raw(year, month)
    member_data = data.get(member_name, {})
    entry = member_data.get(day_str)
    if not entry:
        return STATE_OK
    d, n = entry.get("day", False), entry.get("night", False)
    if d and n:
        return STATE_FULL_OFF
    if d:
        return STATE_DAY_OFF
    if n:
        return STATE_NIGHT_OFF
    return STATE_OK


def set_member_day_state(year: int, month: int, member_name: str, day_str: str, state: str) -> None:
    data = load_unavailability_raw(year, month)
    member_data = data.setdefault(member_name, {})
    if state == STATE_OK:
        member_data.pop(day_str, None)
    else:
        member_data[day_str] = {
            "day": state in (STATE_DAY_OFF, STATE_FULL_OFF),
            "night": state in (STATE_NIGHT_OFF, STATE_FULL_OFF),
        }
    save_unavailability_raw(year, month, data)
    touch_last_updated(year, month, member_name)


def cycle_member_day_state(year: int, month: int, member_name: str, day_str: str) -> str:
    """状態を次の状態に巡回させ、保存後の新しい状態を返す"""
    current = get_member_day_state(year, month, member_name, day_str)
    next_state = STATE_ORDER[(STATE_ORDER.index(current) + 1) % len(STATE_ORDER)]
    set_member_day_state(year, month, member_name, day_str, next_state)
    return next_state


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


# ----------------------------------------------------------------------
# 最終更新日時(入力状況の可視化用)
# ----------------------------------------------------------------------

def _last_updated_path(year: int, month: int) -> Path:
    return DATA_DIR / f"last_updated_{_month_key(year, month)}.json"


def touch_last_updated(year: int, month: int, member_name: str) -> None:
    from datetime import datetime as _dt

    path = _last_updated_path(year, month)
    data = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    data[member_name] = _dt.now().isoformat()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_last_updated(year: int, month: int) -> Dict[str, str]:
    """{名前: ISO形式のタイムスタンプ文字列} を返す"""
    path = _last_updated_path(year, month)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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

    path = _finalized_path(year, month)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"finalized_at": _dt.now().isoformat()}, f, ensure_ascii=False, indent=2)

    if load_actual_snapshot(year, month) is None:
        scheduled = load_schedule_snapshot(year, month)
        if scheduled is not None:
            save_actual_snapshot(year, month, scheduled)


def is_finalized(year: int, month: int) -> bool:
    return _finalized_path(year, month).exists()


def get_finalized_info(year: int, month: int) -> Optional[dict]:
    path = _finalized_path(year, month)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def clear_finalized(year: int, month: int) -> None:
    path = _finalized_path(year, month)
    if path.exists():
        path.unlink()


# ----------------------------------------------------------------------
# 予定勤務表(scheduled_assignments)のスナップショット
# (セッションをまたいで結果を表示するための保存。年間集計には使わない)
# ----------------------------------------------------------------------

def _schedule_snapshot_path(year: int, month: int) -> Path:
    return DATA_DIR / f"schedule_{_month_key(year, month)}.json"


def save_schedule_snapshot(year: int, month: int, snapshot: dict) -> None:
    path = _schedule_snapshot_path(year, month)
    with path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def load_schedule_snapshot(year: int, month: int) -> Optional[dict]:
    path = _schedule_snapshot_path(year, month)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------
# 外部病院バイト対象日
# ----------------------------------------------------------------------

def _gaikobu_days_path(year: int, month: int) -> Path:
    return DATA_DIR / f"gaikobu_days_{_month_key(year, month)}.json"


def get_gaikobu_days(year: int, month: int) -> List[str]:
    """'YYYY-MM-DD' 形式の文字列リストを返す"""
    path = _gaikobu_days_path(year, month)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def set_gaikobu_days(year: int, month: int, day_strs: List[str]) -> None:
    path = _gaikobu_days_path(year, month)
    with path.open("w", encoding="utf-8") as f:
        json.dump(sorted(set(day_strs)), f, ensure_ascii=False, indent=2)


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
    path = _actual_snapshot_path(year, month)
    with path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def load_actual_snapshot(year: int, month: int) -> Optional[dict]:
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
    if not _ACTUAL_EDIT_HISTORY_PATH.exists():
        return []
    with _ACTUAL_EDIT_HISTORY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_actual_edit_history(records: List[dict]) -> None:
    with _ACTUAL_EDIT_HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


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
    if not _SWAP_REQUESTS_PATH.exists():
        return []
    with _SWAP_REQUESTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_swap_requests(records: List[dict]) -> None:
    with _SWAP_REQUESTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


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

    path = _actual_finalized_path(year, month)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"actual_finalized_at": _dt.now().isoformat()}, f, ensure_ascii=False, indent=2)


def is_actual_finalized(year: int, month: int) -> bool:
    return _actual_finalized_path(year, month).exists()


def get_actual_finalized_info(year: int, month: int) -> Optional[dict]:
    path = _actual_finalized_path(year, month)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def clear_actual_finalized(year: int, month: int) -> None:
    path = _actual_finalized_path(year, month)
    if path.exists():
        path.unlink()


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
