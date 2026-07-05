# -*- coding: utf-8 -*-
"""
data_store.py の目標回数自動計算・勤務可能日数計算に関する単体テスト

実行方法:
    cd oncall_scheduler
    python -m pytest tests/ -v
    または
    python tests/test_target_calc.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_PROJECT_ROOT))
sys.path.append(str(_PROJECT_ROOT / "app"))


def _fresh_data_store():
    """テストごとに独立した data/ ディレクトリを使うようにモジュールを再読込する"""
    import importlib

    tmp_dir = tempfile.mkdtemp()
    import data_store as ds

    importlib.reload(ds)
    ds.DATA_DIR = Path(tmp_dir)
    ds.DATA_DIR.mkdir(exist_ok=True)
    ds.CONFIG_PATH = ds.DATA_DIR / "config.json"
    return ds, tmp_dir


def test_state_cycle_order_full_off_second():
    """不可日タップは ○→×終日不可→▲昼→▲夜→○ の順に切り替わる"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        ds.add_member("A", 0, "", False)
        day = "2026-08-01"
        assert ds.get_member_day_state(2026, 8, "A", day) == ds.STATE_OK
        assert ds.cycle_member_day_state(2026, 8, "A", day) == ds.STATE_FULL_OFF
        assert ds.cycle_member_day_state(2026, 8, "A", day) == ds.STATE_DAY_OFF
        assert ds.cycle_member_day_state(2026, 8, "A", day) == ds.STATE_NIGHT_OFF
        assert ds.cycle_member_day_state(2026, 8, "A", day) == ds.STATE_OK
    finally:
        shutil.rmtree(tmp_dir)


def test_available_days_without_long_full_off_run():
    """連続7日以上の終日不可が無ければ勤務可能日数は月の日数と一致する"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        ds.add_member("A", 0, "", False)
        assert ds.compute_available_days(2026, 8, {"name": "A"}) == 31
    finally:
        shutil.rmtree(tmp_dir)


def test_six_consecutive_full_off_days_do_not_reduce_available_days():
    """終日不可が連続6日なら長期不在相当にはしない"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        ds.add_member("A", 0, "", False)
        for day_num in range(1, 7):
            ds.set_member_day_state(2026, 8, "A", f"2026-08-{day_num:02d}", ds.STATE_FULL_OFF)
        assert ds.compute_available_days(2026, 8, {"name": "A"}) == 31
    finally:
        shutil.rmtree(tmp_dir)


def test_seven_consecutive_full_off_days_reduce_available_days():
    """終日不可が連続7日以上なら、その連続期間を勤務可能日数から差し引く"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        ds.add_member("A", 0, "", False)
        for day_num in range(5, 12):
            ds.set_member_day_state(2026, 8, "A", f"2026-08-{day_num:02d}", ds.STATE_FULL_OFF)
        assert ds.compute_available_days(2026, 8, {"name": "A"}) == 31 - 7
        assert {d.isoformat() for d in ds.get_auto_absence_days(2026, 8, "A")} == {
            f"2026-08-{day_num:02d}" for day_num in range(5, 12)
        }
    finally:
        shutil.rmtree(tmp_dir)


def test_day_only_or_night_only_does_not_count_as_auto_absence():
    """日中のみ不可・夜間のみ不可は、7日続いても長期不在相当にはしない"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        ds.add_member("A", 0, "", False)
        for day_num in range(1, 8):
            ds.set_member_day_state(2026, 8, "A", f"2026-08-{day_num:02d}", ds.STATE_DAY_OFF)
        assert ds.compute_available_days(2026, 8, {"name": "A"}) == 31
    finally:
        shutil.rmtree(tmp_dir)


def test_auto_targets_sum_matches_total_slots_with_auto_absence():
    """自動計算された目標回数の合計は、必ず総枠数(日数×2)と一致する"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        for name in ["A", "B", "C", "D"]:
            ds.add_member(name, 0, "", False)
        for day_num in range(1, 16):
            ds.set_member_day_state(2026, 8, "B", f"2026-08-{day_num:02d}", ds.STATE_FULL_OFF)

        targets = ds.compute_auto_targets(2026, 8)
        assert sum(targets.values()) == 31 * 2
        assert targets["B"] < targets["A"]
    finally:
        shutil.rmtree(tmp_dir)


def test_manual_target_is_respected():
    """手動指定されたメンバーの目標回数は自動計算で上書きされない"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        for name in ["A", "B", "C"]:
            ds.add_member(name, 0, "", False)
        ds.update_member_manual_target("A", True)
        ds.update_target_count("A", 2)

        targets = ds.compute_auto_targets(2026, 8)
        assert targets["A"] == 2
        assert targets["B"] + targets["C"] == 31 * 2 - 2
    finally:
        shutil.rmtree(tmp_dir)


def test_get_members_as_models_uses_auto_targets():
    """get_members_as_models() が自動計算結果を反映していること"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        for name in ["A", "B"]:
            ds.add_member(name, 0, "", False)

        models = ds.get_members_as_models()
        total = sum(m.target_count for m in models)
        assert total == 31 * 2
    finally:
        shutil.rmtree(tmp_dir)


def test_get_unavailability_objects_uses_entered_full_off_as_hard_constraint():
    """入力された終日不可は、実際の割当から除外するための制約として反映される"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 8)
        ds.add_member("A", 0, "", False)
        ds.set_member_day_state(2026, 8, "A", "2026-08-05", ds.STATE_FULL_OFF)

        objs = ds.get_unavailability_objects(2026, 8)
        a_entries = [u for u in objs if u.member_name == "A" and u.day.isoformat() == "2026-08-05"]
        assert a_entries[0].day_unavailable and a_entries[0].night_unavailable
    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    test_state_cycle_order_full_off_second()
    test_available_days_without_long_full_off_run()
    test_six_consecutive_full_off_days_do_not_reduce_available_days()
    test_seven_consecutive_full_off_days_reduce_available_days()
    test_day_only_or_night_only_does_not_count_as_auto_absence()
    test_auto_targets_sum_matches_total_slots_with_auto_absence()
    test_manual_target_is_respected()
    test_get_members_as_models_uses_auto_targets()
    test_get_unavailability_objects_uses_entered_full_off_as_hard_constraint()
    print("全テスト成功")
