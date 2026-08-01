# -*- coding: utf-8 -*-
"""
絶対条件⑧(Night Callの翌日ルール)の検証用単体テスト。

【仕様】
  - 月〜金のNight Call翌日は完全OFF(Day Call/Night Callともに不可)
  - 土曜NightのみOK翌日(日曜)のNight Callを例外的に許可
      (日曜のDay Callは不可、日曜のNight Callは可)
  - 日曜Nightの翌日(月曜)は完全OFF
  - 祝日は考慮せず、曜日のみで判定(月〜金はすべて平日扱い)

fixed_assignments で特定メンバーの特定枠を Call に強制固定し、
各パターンが期待通り feasible/infeasible になることを確認する。

2026年8月の曜日:
  8/10(月) 8/11(火) 8/12(水) 8/13(木) 8/14(金) 8/15(土) 8/16(日) 8/17(月)

実行方法:
    python -m pytest tests/test_rule8_night_next_day.py -v
"""
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.models import Member, Slot
from src.optimizer import OnCallOptimizer, OptimizerOptions
from ortools.sat.python import cp_model

YEAR, MONTH = 2026, 8
MEMBERS = [Member(name=n, target_count=6) for n in ["A", "B", "C", "D", "E"]]


def _run(fixed):
    """fixed: {(date, Slot): "A"} を強制固定して solve し、statusを返す"""
    opt = OnCallOptimizer(
        YEAR, MONTH, MEMBERS, [],
        OptimizerOptions(max_time_seconds=15, fixed_assignments=fixed),
    )
    opt.build()
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    status = solver.Solve(opt.model)
    return solver.StatusName(status)


def test_tuesday_night_to_wednesday_night_is_infeasible():
    """火曜(8/11)Night → 水曜(8/12)Night: 平日なのでinfeasible"""
    fixed = {
        (date(2026, 8, 11), Slot.NIGHT): "A",
        (date(2026, 8, 12), Slot.NIGHT): "A",
    }
    assert _run(fixed) == "INFEASIBLE"


def test_friday_night_to_saturday_night_is_infeasible():
    """金曜(8/14)Night → 土曜(8/15)Night: 平日なのでinfeasible"""
    fixed = {
        (date(2026, 8, 14), Slot.NIGHT): "A",
        (date(2026, 8, 15), Slot.NIGHT): "A",
    }
    assert _run(fixed) == "INFEASIBLE"


def test_saturday_night_to_sunday_night_is_feasible():
    """土曜(8/15)Night → 日曜(8/16)Night: 例外的に許可されfeasible"""
    fixed = {
        (date(2026, 8, 15), Slot.NIGHT): "A",
        (date(2026, 8, 16), Slot.NIGHT): "A",
    }
    status = _run(fixed)
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"


def test_sunday_night_to_monday_night_is_infeasible():
    """日曜(8/16)Night → 月曜(8/17)Night: 完全休みなのでinfeasible"""
    fixed = {
        (date(2026, 8, 16), Slot.NIGHT): "A",
        (date(2026, 8, 17), Slot.NIGHT): "A",
    }
    assert _run(fixed) == "INFEASIBLE"


def test_night_to_next_day_day_call_is_always_infeasible():
    """Night翌日のDay Callは曜日を問わず常にinfeasible(土曜Night→日曜Dayも含む)"""
    cases = [
        (date(2026, 8, 10), date(2026, 8, 11)),  # 月Night → 火Day
        (date(2026, 8, 11), date(2026, 8, 12)),  # 火Night → 水Day
        (date(2026, 8, 14), date(2026, 8, 15)),  # 金Night → 土Day
        (date(2026, 8, 15), date(2026, 8, 16)),  # 土Night → 日Day(Nightは可だがDayは不可)
        (date(2026, 8, 16), date(2026, 8, 17)),  # 日Night → 月Day
    ]
    for night_day, day_day in cases:
        fixed = {
            (night_day, Slot.NIGHT): "A",
            (day_day, Slot.DAY): "A",
        }
        status = _run(fixed)
        assert status == "INFEASIBLE", (
            f"{night_day}(Night) -> {day_day}(Day) expected INFEASIBLE, got {status}"
        )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
