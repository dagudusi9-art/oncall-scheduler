# -*- coding: utf-8 -*-
"""
休息ルール(3日以上連続Call後の2連休、および5日以上連続Call)の
検証用単体テスト。

【新仕様(soft constraint)】
  - 5日以上の連続Callは原則禁止(five_day_streak違反)
  - 3日以上連続Callが終了した直後は原則2日連続OFF
    (streak_missing_two_days_off違反)
  - いずれも目標回数達成のためにやむを得ない場合のみ許容されるsoft
    constraintであり、hard constraintとしてINFEASIBLEにはならない。

fixed_assignments で特定メンバーの特定日をCall/OFFに強制固定し、
各パターンが実際にfeasibleであること、および想定した違反が
きちんとカウントされる(または、されない)ことを確認する。
Night Call(休息ルールのNight系)との混同を避けるため、固定するCallは
すべてDay枠を使用する(Night枠は絡めない)。

実行方法:
    python -m pytest tests/test_rule9_consecutive_off.py -v -s
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.models import Member, Slot
from src.optimizer import OnCallOptimizer, OptimizerOptions
from ortools.sat.python import cp_model

YEAR, MONTH = 2026, 8  # 31日=62枠。境界日を確保しやすいので使用
MEMBERS = [
    Member(name="A", target_count=13),
    Member(name="B", target_count=13),
    Member(name="C", target_count=12),
    Member(name="D", target_count=12),
    Member(name="E", target_count=12),
]
BASE_DAY = date(YEAR, MONTH, 10)  # 前後に十分な日数を確保できる基準日


def _d(offset: int) -> date:
    return BASE_DAY + timedelta(days=offset)


def _build_and_solve(call_offsets, off_offsets=None, minimize_category=None):
    """
    call_offsets: "A" のDay枠を強制的にCallにするoffsetのリスト
    off_offsets : "A" のDay/Night枠を強制的にOFF(不可)にするoffsetのリスト
    minimize_category を指定すると、そのカテゴリの違反変数合計を
    最小化した上でsolveし、実際にカウントされる違反数を確認できるようにする。
    戻り値: (status, optimizer, solver)
    """
    off_offsets = off_offsets or []
    fixed_assignments = {(_d(o), Slot.DAY): "A" for o in call_offsets}

    opt = OnCallOptimizer(
        YEAR, MONTH, MEMBERS, [],
        OptimizerOptions(max_time_seconds=15, fixed_assignments=fixed_assignments),
    )
    opt.build()

    for o in off_offsets:
        d = _d(o)
        opt.model.Add(opt.x[(d, Slot.DAY, "A")] == 0)
        opt.model.Add(opt.x[(d, Slot.NIGHT, "A")] == 0)

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


def test_3day_streak_can_extend_to_4day_without_penalty():
    """3日連続Call(0,1,2)の翌日(3)にもCall → 4日連続への延長は
    5日未満なのでfive_day_streak違反にはならず、streak_missing違反も
    「streakがまだ終わっていない」ため発生しない"""
    status, opt, solver = _build_and_solve(
        call_offsets=[0, 1, 2, 3], minimize_category="streak_missing_two_days_off"
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "five_day_streak") == 0
    assert _violation_total(opt, solver, "streak_missing_two_days_off") == 0


def test_5day_consecutive_call_is_feasible_but_a_rest_violation():
    """5日連続Call(0,1,2,3,4) → soft化されたのでfeasibleだが、
    five_day_streak違反が少なくとも1件カウントされる"""
    status, opt, solver = _build_and_solve(
        call_offsets=[0, 1, 2, 3, 4], minimize_category="five_day_streak"
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "five_day_streak") >= 1


def test_3day_streak_end_second_off_day_cannot_be_call_is_a_violation():
    """3日連続Call(0,1,2)で終了(3はOFF)した場合、翌々日(4)にCall →
    soft化されたのでfeasibleだが、streak_missing_two_days_off違反が
    少なくとも1件カウントされる"""
    status, opt, solver = _build_and_solve(
        call_offsets=[0, 1, 2, 4], off_offsets=[3],
        minimize_category="streak_missing_two_days_off",
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "streak_missing_two_days_off") >= 1


def test_3day_streak_end_two_days_off_has_no_penalty():
    """3日連続Call(0,1,2)で終了し、直後の2日間(3,4)がOFF →
    ルールを満たしているのでstreak_missing_two_days_off違反は0件"""
    status, opt, solver = _build_and_solve(
        call_offsets=[0, 1, 2], off_offsets=[3, 4],
        minimize_category="streak_missing_two_days_off",
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "streak_missing_two_days_off") == 0


def test_4day_streak_end_second_off_day_cannot_be_call_is_a_violation():
    """4日連続Call(0,1,2,3)で終了(4はOFF)した場合、翌々日(5)にCall →
    streak_missing_two_days_off違反が少なくとも1件カウントされる"""
    status, opt, solver = _build_and_solve(
        call_offsets=[0, 1, 2, 3, 5], off_offsets=[4],
        minimize_category="streak_missing_two_days_off",
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "streak_missing_two_days_off") >= 1


def test_4day_streak_end_two_days_off_has_no_penalty():
    """4日連続Call(0,1,2,3)で終了し、直後の2日間(4,5)がOFF → 違反0件"""
    status, opt, solver = _build_and_solve(
        call_offsets=[0, 1, 2, 3], off_offsets=[4, 5],
        minimize_category="streak_missing_two_days_off",
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "streak_missing_two_days_off") == 0


def test_2day_streak_plus_call_on_next_day_has_no_penalty():
    """2日連続Call(0,1)の翌日(2)にCall → 3日連続になるだけで、
    まだstreakが終了していないため違反にはならない"""
    status, opt, solver = _build_and_solve(
        call_offsets=[0, 1, 2], minimize_category="streak_missing_two_days_off"
    )
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"
    assert _violation_total(opt, solver, "streak_missing_two_days_off") == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
