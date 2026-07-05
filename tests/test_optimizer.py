# -*- coding: utf-8 -*-
"""
optimizer.py の単体テスト

実行方法:
    cd oncall_scheduler
    python -m pytest tests/ -v
"""
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.models import Member, Slot, Unavailability
from src.optimizer import OnCallOptimizer, OptimizerOptions


def test_basic_feasible_schedule():
    """基本的な条件で、絶対条件を満たす勤務表が生成できること"""
    members = [Member(name=n, target_count=2) for n in ["A", "B", "C", "D"]]
    optimizer = OnCallOptimizer(
        year=2026, month=2, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=10),
    )
    result = optimizer.solve()

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert len(result.entries) == 28  # 2026年2月は28日

    for entry in result.entries:
        day_name = entry.assignments[Slot.DAY]
        night_name = entry.assignments[Slot.NIGHT]
        assert day_name is not None
        assert night_name is not None
        # 絶対条件③: 同じ人が同日の日中・夜間を両方担当しない
        assert day_name != night_name


def test_unavailability_is_respected():
    """不都合日に指定した人が割り当てられないこと"""
    members = [Member(name=n, target_count=4) for n in ["A", "B", "C", "D"]]
    unavailabilities = [
        Unavailability(member_name="A", day=date(2026, 3, 1), day_unavailable=True, night_unavailable=True),
    ]
    optimizer = OnCallOptimizer(
        year=2026, month=3, members=members, unavailabilities=unavailabilities,
        options=OptimizerOptions(max_time_seconds=10),
    )
    result = optimizer.solve()

    assert result.status in ("OPTIMAL", "FEASIBLE")
    first_day_entry = result.entries[0]
    assert first_day_entry.assignments[Slot.DAY] != "A"
    assert first_day_entry.assignments[Slot.NIGHT] != "A"


def test_single_person_per_slot():
    """各枠(日中/夜間)に1人だけ割り当てられること(2重割当がないこと)"""
    members = [Member(name=n, target_count=3) for n in ["A", "B", "C"]]
    optimizer = OnCallOptimizer(
        year=2026, month=4, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=10),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")
    for entry in result.entries:
        assert entry.assignments[Slot.DAY] in [m.name for m in members]
        assert entry.assignments[Slot.NIGHT] in [m.name for m in members]


def test_gaikobu_assigned_only_to_eligible_members():
    """外部バイトは対象者(gaikobu_eligible=True)からのみ割り当てられること"""
    members = [
        Member(name="A", target_count=2, gaikobu_eligible=True),
        Member(name="B", target_count=2, gaikobu_eligible=True),
        Member(name="C", target_count=2, gaikobu_eligible=False),  # 対象外
        Member(name="D", target_count=2, gaikobu_eligible=False),  # 対象外
    ]
    gaikobu_days = {date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)}
    optimizer = OnCallOptimizer(
        year=2026, month=5, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=10, gaikobu_days=gaikobu_days),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")

    for entry in result.entries:
        if entry.day in gaikobu_days:
            assert entry.gaikobu in ("A", "B")
        else:
            assert entry.gaikobu is None


def test_gaikobu_excludes_own_hospital_same_day():
    """外部バイトに入った日は自院の日中・夜間どちらにも入らないこと"""
    members = [Member(name=n, target_count=3, gaikobu_eligible=True) for n in ["A", "B", "C"]]
    gaikobu_days = {date(2026, 6, 5)}
    optimizer = OnCallOptimizer(
        year=2026, month=6, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=10, gaikobu_days=gaikobu_days),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")

    target_entry = next(e for e in result.entries if e.day == date(2026, 6, 5))
    assert target_entry.gaikobu is not None
    assert target_entry.assignments[Slot.DAY] != target_entry.gaikobu
    assert target_entry.assignments[Slot.NIGHT] != target_entry.gaikobu


def test_gaikobu_respects_unavailability():
    """不都合日(日中または夜間)がある対象者には外部バイトを割り当てないこと"""
    # 外部バイト対象日は「日中・夜間・外部バイト」の3つの役割が必要になるため、
    # 十分な人数(4人)を用意する。うち2人を外部バイト対象とする。
    members = [
        Member(name="A", target_count=3, gaikobu_eligible=True),
        Member(name="B", target_count=3, gaikobu_eligible=True),
        Member(name="C", target_count=3, gaikobu_eligible=False),
        Member(name="D", target_count=3, gaikobu_eligible=False),
    ]
    gaikobu_days = {date(2026, 9, 10)}
    unavailabilities = [
        Unavailability(member_name="A", day=date(2026, 9, 10), day_unavailable=True, night_unavailable=False),
    ]
    optimizer = OnCallOptimizer(
        year=2026, month=9, members=members, unavailabilities=unavailabilities,
        options=OptimizerOptions(max_time_seconds=10, gaikobu_days=gaikobu_days),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")

    target_entry = next(e for e in result.entries if e.day == date(2026, 9, 10))
    assert target_entry.gaikobu == "B"  # Aは不都合日のため割当不可


def test_gaikobu_stats_totals():
    """統計に外部バイト回数・総勤務(自院合計+外部バイト)が正しく反映されること"""
    members = [Member(name=n, target_count=4, gaikobu_eligible=True) for n in ["A", "B", "C"]]
    gaikobu_days = {date(2026, 10, d) for d in range(1, 6)}
    optimizer = OnCallOptimizer(
        year=2026, month=10, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=10, gaikobu_days=gaikobu_days),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")

    total_gaikobu_assigned = sum(s["gaikobu"] for s in result.stats.values())
    assert total_gaikobu_assigned == len(gaikobu_days)  # 対象日数と一致するはず

    for name, s in result.stats.items():
        assert s["grand_total"] == s["total"] + s["gaikobu"]


def test_annual_actual_balance_favors_deficit_member():
    """年間実績が少ないメンバーに、その月の割当が優先的に多く回ること
    (2人構成では日中/夜間の排他制約により必ず1日1回ずつ均等に割り当たって
    しまうため、この検証には3人以上のメンバーが必要)"""
    members = [Member(name=n, target_count=8) for n in ["A", "B", "C"]]
    annual_totals = {"A": 0, "B": 20, "C": 20}  # Aは年間実績ゼロ、B・Cは既に20回
    optimizer = OnCallOptimizer(
        year=2026, month=6, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=20, annual_actual_totals=annual_totals),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")
    # 年間実績が少ないAの方が、この月にたくさん割り当てられるはず
    assert result.stats["A"]["total"] > result.stats["B"]["total"]
    assert result.stats["A"]["total"] > result.stats["C"]["total"]


def test_month_weeks_starts_on_sunday():
    """month_weeks()が日曜始まりで週を生成すること"""
    import sys as _sys
    from pathlib import Path as _Path

    app_dir = _Path(__file__).resolve().parent.parent / "app"
    if str(app_dir) not in _sys.path:
        _sys.path.append(str(app_dir))
    import ui_common as uc

    assert uc.WEEKDAY_JA == ["日", "月", "火", "水", "木", "金", "土"]

    # 2026年8月1日は土曜日 -> 最初の週は日〜金がNoneで、土曜(末尾)に1日が入る
    weeks = uc.month_weeks(2026, 8)
    first_week = weeks[0]
    assert len(first_week) == 7
    assert first_week[:6] == [None] * 6
    assert first_week[6].day == 1
    assert first_week[6].weekday() == 5  # 土曜(date.weekday()は月曜=0基準のまま)

    # 2番目の週は日曜(8/2)始まり
    second_week = weeks[1]
    assert second_week[0].day == 2
    assert second_week[0].weekday() == 6  # 日曜


if __name__ == "__main__":
    test_basic_feasible_schedule()
    test_unavailability_is_respected()
    test_single_person_per_slot()
    test_gaikobu_assigned_only_to_eligible_members()
    test_gaikobu_excludes_own_hospital_same_day()
    test_gaikobu_respects_unavailability()
    test_gaikobu_stats_totals()
    test_annual_actual_balance_favors_deficit_member()
    test_month_weeks_starts_on_sunday()
    print("全テスト成功")
