# -*- coding: utf-8 -*-
"""
絶対条件⑨(3日以上連続Callが終了した直後は2日連続OFF必須)の検証用単体テスト。

【確定した解釈】
  - 3日連続Call → その直後にもう1日Callを重ねて4日連続へ延長することは
    絶対条件⑦(最大4日連続まで)の範囲内として許容する。
  - 5日連続Callは絶対条件⑦により不可。
  - 連続Call(3日または4日)が実際に終了した直後の2日間は必ずOFFにする。

fixed_assignments で特定メンバーの特定日を Call/OFF に強制固定し、
各パターンが期待通り feasible/infeasible になることを確認する。
Night Call(絶対条件⑧)との混同を避けるため、固定するCallはすべて
Day枠を使用する(Night枠は絡めない)。

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

YEAR, MONTH = 2026, 8  # 31日ある月。境界日を確保しやすいので使用
MEMBERS = [Member(name=n, target_count=6) for n in ["A", "B", "C", "D", "E"]]
BASE_DAY = date(YEAR, MONTH, 10)  # 前後に十分な日数を確保できる基準日


def _d(offset: int) -> date:
    return BASE_DAY + timedelta(days=offset)


def _run(call_offsets, off_offsets=None):
    """
    call_offsets: "A" の Day枠を強制的に Call にする offset のリスト
    off_offsets : "A" の Day/Night枠を強制的に OFF(不可)にする offset のリスト
    戻り値: solver の status 文字列 ("OPTIMAL"/"FEASIBLE"/"INFEASIBLE" など)
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

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    status = solver.Solve(opt.model)
    return solver.StatusName(status)


def test_3day_streak_can_extend_to_4day():
    """3日連続Call(0,1,2)の翌日(3)にもCall → 4日連続への延長として許容(feasible)"""
    status = _run(call_offsets=[0, 1, 2, 3])
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"


def test_5day_consecutive_call_is_infeasible():
    """5日連続Call(0,1,2,3,4) → 絶対条件⑦(最大4日連続)によりinfeasible"""
    status = _run(call_offsets=[0, 1, 2, 3, 4])
    assert status == "INFEASIBLE", f"expected INFEASIBLE, got {status}"


def test_3day_streak_end_second_off_day_cannot_be_call():
    """3日連続Call(0,1,2)で終了(3はOFF)した場合、翌々日(4)にCallは不可 → infeasible"""
    status = _run(call_offsets=[0, 1, 2, 4], off_offsets=[3])
    assert status == "INFEASIBLE", f"expected INFEASIBLE, got {status}"


def test_3day_streak_end_two_days_off_is_feasible():
    """3日連続Call(0,1,2)で終了し、直後の2日間(3,4)がOFF → feasible"""
    status = _run(call_offsets=[0, 1, 2], off_offsets=[3, 4])
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"


def test_4day_streak_end_second_off_day_cannot_be_call():
    """4日連続Call(0,1,2,3)で終了(4はOFF)した場合、翌々日(5)にCallは不可 → infeasible"""
    status = _run(call_offsets=[0, 1, 2, 3, 5], off_offsets=[4])
    assert status == "INFEASIBLE", f"expected INFEASIBLE, got {status}"


def test_4day_streak_end_two_days_off_is_feasible():
    """4日連続Call(0,1,2,3)で終了し、直後の2日間(4,5)がOFF → feasible"""
    status = _run(call_offsets=[0, 1, 2, 3], off_offsets=[4, 5])
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"


def test_2day_streak_plus_call_on_next_day_is_feasible():
    """2日連続Call(0,1)の翌日(2)にCall → このルール単独では許容(=3日連続になるだけ)"""
    status = _run(call_offsets=[0, 1, 2])
    assert status in ("OPTIMAL", "FEASIBLE"), f"expected feasible, got {status}"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
