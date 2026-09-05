# -*- coding: utf-8 -*-
"""
休息ルール(Night Callの翌日ルール)の検証用単体テスト。

【新仕様(soft constraint)】
  - 月〜金Night → 翌日は原則完全OFF(Day/Nightとも不可)
  - 土曜NightのみOK: 翌日(日曜)のNight Callを例外的に許可
      (日曜のDay Callは不可、日曜のNight Callは可)
  - 日曜Nightの翌日(月曜)は原則完全OFF
  - 祝日は考慮せず、曜日のみで判定する(月〜金はすべて平日扱い)
  - これらは目標回数達成のためにやむを得ない場合のみ許容されるsoft
    constraintであり、hard constraintとしてINFEASIBLEにはならない。
    違反した場合は違反変数(weekday_night_next_day /
    saturday_night_sunday_day / sunday_night_monday)に1件カウントされる。

fixed_assignments で特定メンバーの特定枠をCallに強制固定し、
各パターンが実際にfeasibleであること、および強制した違反が
きちんとカウントされることを確認する。

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

YEAR, MONTH = 2026, 8  # 31日 = 62枠
# target合計を62に一致させる(hard constraint)。fixed_assignmentsで
# 強制する枠は各テストごとに高々2つなので、target値はどのメンバーの
# 割当可能枠数も超えない適当な値にしておく。
MEMBERS = [
    Member(name="A", target_count=13),
    Member(name="B", target_count=13),
    Member(name="C", target_count=12),
    Member(name="D", target_count=12),
    Member(name="E", target_count=12),
]


def _build_and_solve(fixed, minimize_category=None):
    """fixed: {(date, Slot): "A"} を強制固定してbuildし、
    (status, optimizer) を返す。minimize_categoryを指定した場合は、
    そのカテゴリの違反変数の合計を目的関数として最小化した上でsolveする
    (「強制された違反が実際に検出されるか」を確認するため)。"""
    opt = OnCallOptimizer(
        YEAR, MONTH, MEMBERS, [],
        OptimizerOptions(max_time_seconds=15, fixed_assignments=fixed),
    )
    opt.build()
    if minimize_category is not None:
        all_vars = []
        for name in opt.member_names:
            all_vars.extend(opt.violation_vars[name][minimize_category])
        opt.model.Minimize(sum(all_vars))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    status = solver.Solve(opt.model)
    return solver.StatusName(status), opt, solver


def _violation_total(opt, solver, category: str) -> int:
    return sum(solver.Value(v) for name in opt.member_names for v in opt.violation_vars[name][category])


def test_weekday_night_to_next_day_is_feasible_but_counted_as_violation():
    """火曜(8/11)Night → 水曜(8/12)Night: 平日ルールなのでfeasibleだが、
    weekday_night_next_dayが少なくとも1件カウントされる"""
    fixed = {
        (date(2026, 8, 11), Slot.NIGHT): "A",
        (date(2026, 8, 12), Slot.NIGHT): "A",
    }
    status, opt, solver = _build_and_solve(fixed, minimize_category="weekday_night_next_day")
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "weekday_night_next_day") >= 1


def test_friday_night_to_saturday_night_is_feasible_but_counted_as_violation():
    """金曜(8/14)Night → 土曜(8/15)Night: 平日ルールなのでfeasibleだが、違反1件以上"""
    fixed = {
        (date(2026, 8, 14), Slot.NIGHT): "A",
        (date(2026, 8, 15), Slot.NIGHT): "A",
    }
    status, opt, solver = _build_and_solve(fixed, minimize_category="weekday_night_next_day")
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "weekday_night_next_day") >= 1


def test_saturday_night_to_sunday_night_has_no_penalty():
    """土曜(8/15)Night → 日曜(8/16)Night: 例外的に許可され、
    saturday_night_sunday_day / sunday_night_monday いずれの違反にも
    カウントされない(ペナルティなし)"""
    fixed = {
        (date(2026, 8, 15), Slot.NIGHT): "A",
        (date(2026, 8, 16), Slot.NIGHT): "A",
    }
    status, opt, solver = _build_and_solve(
        fixed, minimize_category="saturday_night_sunday_day"
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    # 日曜Nightを使っただけでは、土曜ルール(翌日Day禁止)には抵触しない
    assert _violation_total(opt, solver, "saturday_night_sunday_day") == 0


def test_saturday_night_to_sunday_day_is_feasible_but_a_rest_violation():
    """土曜(8/15)Night → 日曜(8/16)Day: soft化されたのでfeasibleだが、
    saturday_night_sunday_day違反として少なくとも1件カウントされる"""
    fixed = {
        (date(2026, 8, 15), Slot.NIGHT): "A",
        (date(2026, 8, 16), Slot.DAY): "A",
    }
    status, opt, solver = _build_and_solve(
        fixed, minimize_category="saturday_night_sunday_day"
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "saturday_night_sunday_day") >= 1


def test_sunday_night_to_monday_is_feasible_but_a_rest_violation():
    """日曜(8/16)Night → 月曜(8/17)Night: soft化されたのでfeasibleだが、
    sunday_night_monday違反として少なくとも1件カウントされる"""
    fixed = {
        (date(2026, 8, 16), Slot.NIGHT): "A",
        (date(2026, 8, 17), Slot.NIGHT): "A",
    }
    status, opt, solver = _build_and_solve(fixed, minimize_category="sunday_night_monday")
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "sunday_night_monday") >= 1


def test_night_to_next_day_day_call_is_now_feasible_for_every_weekday_pattern():
    """Night翌日のDay Callは、以前はhard constraintで常にinfeasibleだったが、
    soft化された現在はどの曜日パターンでもfeasibleになること
    (土曜Night→日曜Dayのみ専用カテゴリ、それ以外はweekday側カテゴリで違反計上)"""
    cases = [
        (date(2026, 8, 10), date(2026, 8, 11)),  # 月Night → 火Day
        (date(2026, 8, 11), date(2026, 8, 12)),  # 火Night → 水Day
        (date(2026, 8, 14), date(2026, 8, 15)),  # 金Night → 土Day
        (date(2026, 8, 15), date(2026, 8, 16)),  # 土Night → 日Day
        (date(2026, 8, 16), date(2026, 8, 17)),  # 日Night → 月Day
    ]
    for night_day, day_day in cases:
        fixed = {
            (night_day, Slot.NIGHT): "A",
            (day_day, Slot.DAY): "A",
        }
        status, opt, solver = _build_and_solve(fixed)
        assert status in ("OPTIMAL", "FEASIBLE"), (
            f"{night_day}(Night) -> {day_day}(Day) expected feasible, got {status}"
        )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
