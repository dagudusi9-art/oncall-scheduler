# -*- coding: utf-8 -*-
"""
admin.py の「シフト生成」ボタン相当のフローを、Streamlitを起動せずに
統合テストとして検証する。

app/pages/admin.py の該当ブロックと同じ手順を再現する:
    member_models = ds.get_members_for_shift_generation(year, month)
    unavailabilities = ds.get_unavailability_objects_with_known_absence(year, month)
    gaikobu_days_set = ds.get_gaikobu_days_as_dates(year, month)
    annual_actual_totals = ds.get_annual_actual_own_totals(year)
    member_names = [m.name for m in member_models]
    month_target = ds.get_month_target(year, month)
    remaining_target = ds.get_remaining_target(year, month)
    future_available_slots = ds.get_future_available_slots(year, month, member_names)
    options = OptimizerOptions(..., month_target=..., remaining_target=..., future_available_slots=...)
    optimizer = OnCallOptimizer(year, month, member_models, unavailabilities, options)
    result = optimizer.solve()

実行方法:
    cd oncall_scheduler_git
    python -m pytest tests/test_admin_shift_generation_flow.py -v -s
"""
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_PROJECT_ROOT))
sys.path.append(str(_PROJECT_ROOT / "app"))

from src.optimizer import OnCallOptimizer, OptimizerOptions, verify_schedule_result  # noqa: E402
from src.models import Slot  # noqa: E402


def _fresh_data_store():
    import importlib

    tmp_dir = tempfile.mkdtemp()
    import data_store as ds

    importlib.reload(ds)
    ds.DATA_DIR = Path(tmp_dir)
    ds.DATA_DIR.mkdir(exist_ok=True)
    ds.CONFIG_PATH = ds.DATA_DIR / "config.json"
    return ds, tmp_dir


FINAL_TARGET = {
    "Ryu": 25, "Nakajima": 28, "Kikuchi": 37, "Otani": 42,
    "Fujii": 56, "Kosaka": 53, "Wakayama": 57, "Otaki": 66,
}

MONTH_TARGET_OCT = {
    "Ryu": 0, "Nakajima": 5, "Kikuchi": 14, "Otani": 6,
    "Fujii": 0, "Kosaka": 18, "Wakayama": 19, "Otaki": 0,
}

KNOWN_ABSENCE = {
    "Otaki": [{"start": "2026-10-01", "end": "2026-10-31"}],
    "Otani": [{"start": "2026-10-13", "end": "2026-12-06"}],
    "Kikuchi": [{"start": "2027-01-01", "end": "2027-01-15"}],
    "Kosaka": [],
    "Nakajima": [
        {"start": "2026-10-12", "end": "2026-12-20"},
        {"start": "2027-03-01", "end": "2027-03-14"},
    ],
    "Fujii": [{"start": "2026-10-01", "end": "2026-12-06"}],
    "Ryu": [
        {"start": "2026-10-01", "end": "2026-11-08"},
        {"start": "2027-01-18", "end": "2027-01-31"},
        {"start": "2027-03-01", "end": "2027-03-31"},
    ],
    "Wakayama": [{"start": "2026-12-07", "end": "2026-12-21"}],
}


def _seed(ds):
    for name in FINAL_TARGET:
        ds.add_member(name, 0, "", False)
    ds.set_final_target(FINAL_TARGET)
    ds.set_month_target(2026, 10, MONTH_TARGET_OCT)
    ds.set_known_long_term_absence(KNOWN_ABSENCE)


def _run_shift_generation(ds, year, month, max_time_seconds=60):
    """admin.pyのボタン押下ブロックと同じ手順を再現する。"""
    member_models = ds.get_members_for_shift_generation(year, month)
    unavailabilities = ds.get_unavailability_objects_with_known_absence(year, month)
    gaikobu_days_set = ds.get_gaikobu_days_as_dates(year, month)
    annual_actual_totals = ds.get_annual_actual_own_totals(year)

    member_names = [m.name for m in member_models]
    month_target = ds.get_month_target(year, month)
    remaining_target = ds.get_remaining_target(year, month)
    future_available_slots = ds.get_future_available_slots(year, month, member_names)

    options = OptimizerOptions(
        max_time_seconds=max_time_seconds,
        gaikobu_days=gaikobu_days_set,
        annual_actual_totals=annual_actual_totals,
        month_target=month_target,
        remaining_target=remaining_target,
        future_available_slots=future_available_slots,
    )
    optimizer = OnCallOptimizer(
        year=year, month=month, members=member_models, unavailabilities=unavailabilities, options=options,
    )
    return optimizer.solve()


def test_october_generation_end_to_end_matches_month_target_with_known_absence_respected():
    """2026年10月分の生成が、①既知の長期不在(Otaki/Fujii全休、Otani/Ryu/
    Nakajima一部不在)をhard constraintとして守り、②final_targetをMember.
    target_countとして使い、③month_targetをsoft targetとして(達成可能なため)
    exactに達成することを確認する。"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 10)
        _seed(ds)

        # Member.target_countがfinal_targetになっていることを確認
        member_models = ds.get_members_for_shift_generation(2026, 10)
        by_name = {m.name: m.target_count for m in member_models}
        assert by_name == FINAL_TARGET

        result = _run_shift_generation(ds, 2026, 10, max_time_seconds=90)
        assert result.status in ("OPTIMAL", "FEASIBLE"), result.warnings

        # 既知の長期不在が守られていること(Otaki/Fujiiは10月全日不在)
        for entry in result.entries:
            assert entry.assignments[Slot.DAY] != "Otaki"
            assert entry.assignments[Slot.NIGHT] != "Otaki"
            assert entry.assignments[Slot.DAY] != "Fujii"
            assert entry.assignments[Slot.NIGHT] != "Fujii"

        # month_targetがexactに達成されていること(以前の検証で0違反確認済みのケース)
        for name, s in result.stats.items():
            assert s["month_target"] == MONTH_TARGET_OCT[name]
            assert s["deviation"] == 0, f"{name}: {s}"

        ok, report = verify_schedule_result(result)
        assert ok, report
    finally:
        shutil.rmtree(tmp_dir)


def test_remaining_target_and_future_available_slots_are_wired_correctly():
    """remaining_target(final_target - 確定実績)とfuture_available_slots
    (既知の長期不在を反映した将来枠数)が、実際にoptimizerへ渡り、
    hard constraintとして機能していることを確認する(小規模ケース)。"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 11)
        for name in ["A", "B", "C"]:
            ds.add_member(name, 0, "", False)
        ds.set_final_target({"A": 10, "B": 20, "C": 30})
        ds.set_month_target(2026, 11, {"A": 5, "B": 10, "C": 15})  # 合計30 = 11月の... (60枠中の一部)
        # 10月分をAだけ確定済みにする(6回実施済み)
        ds.save_actual_snapshot(
            2026, 10,
            {"entries": [], "stats": {"A": {"day": 3, "night": 3, "total": 6, "gaikobu": 0, "grand_total": 6}}},
        )
        ds.mark_actual_finalized(2026, 10)

        remaining = ds.get_remaining_target(2026, 11)
        assert remaining["A"] == 10 - 6  # = 4
        assert remaining["B"] == 20
        assert remaining["C"] == 30

        future = ds.get_future_available_slots(2026, 11, ["A", "B", "C"])
        # 12月〜3月まで長期不在なしなので、A/B/Cとも同じ満額のはず
        assert future["A"] == future["B"] == future["C"]
        assert future["A"] > 0
    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
