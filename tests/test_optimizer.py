# -*- coding: utf-8 -*-
"""
optimizer.py の単体テスト

【新仕様(hard target + soft rest rules + 2段階最適化)を前提とした変更点】
  - target_count は絶対条件(hard constraint)になったため、各テストで
    sum(target_count) をその月の総Call枠数(日数×2)に一致させている。
  - 「自院オンコールの年間実績均等化(ソフトA)」は、月内合計が
    hard constraintで固定されるようになったため、月内の実際の割当数を
    変える効果を持たなくなった(=目標回数を年間実績のために自動調整する
    ことは絶対に起きない、という新仕様どおりの挙動)。これを検証する
    テストに置き換えている。

実行方法:
    cd oncall_scheduler
    python -m pytest tests/ -v
"""
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.models import Member, Slot, Unavailability
from src.optimizer import OnCallOptimizer, OptimizerOptions, OptimizerWeights


def test_basic_feasible_schedule():
    """基本的な条件で、絶対条件を満たす勤務表が生成できること
    (2026年2月=28日=56枠。target合計を56に一致させる)"""
    members = [Member(name=n, target_count=14) for n in ["A", "B", "C", "D"]]
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

    # 絶対条件⑦: 目標回数は必ず厳密に達成される(diff=0)
    for name, s in result.stats.items():
        assert s["diff"] == 0, f"{name}: target={s['target']} actual={s['total']}"


def test_unavailability_is_respected():
    """不都合日に指定した人が割り当てられないこと
    (2026年3月=31日=62枠。target合計を62に一致させる)"""
    members = [
        Member(name="A", target_count=16),
        Member(name="B", target_count=16),
        Member(name="C", target_count=15),
        Member(name="D", target_count=15),
    ]
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
    for name, s in result.stats.items():
        assert s["diff"] == 0


def test_single_person_per_slot():
    """各枠(日中/夜間)に1人だけ割り当てられること(2重割当がないこと)
    (2026年4月=30日=60枠。target合計を60に一致させる)"""
    members = [Member(name=n, target_count=20) for n in ["A", "B", "C"]]
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
    """外部バイトは対象者(gaikobu_eligible=True)からのみ割り当てられること
    (2026年5月=31日=62枠。target合計を62に一致させる。外部バイトは
    自院枠とは独立の変数なのでtarget合計には影響しない)"""
    members = [
        Member(name="A", target_count=16, gaikobu_eligible=True),
        Member(name="B", target_count=16, gaikobu_eligible=True),
        Member(name="C", target_count=15, gaikobu_eligible=False),
        Member(name="D", target_count=15, gaikobu_eligible=False),
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
    for name, s in result.stats.items():
        assert s["diff"] == 0


def test_gaikobu_excludes_own_hospital_same_day():
    """外部バイトに入った日は自院の日中・夜間どちらにも入らないこと
    (2026年6月=30日=60枠)"""
    members = [Member(name=n, target_count=20, gaikobu_eligible=True) for n in ["A", "B", "C"]]
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
    """不都合日(日中または夜間)がある対象者には外部バイトを割り当てないこと
    (2026年9月=30日=60枠)"""
    members = [
        Member(name="A", target_count=15, gaikobu_eligible=True),
        Member(name="B", target_count=15, gaikobu_eligible=True),
        Member(name="C", target_count=15, gaikobu_eligible=False),
        Member(name="D", target_count=15, gaikobu_eligible=False),
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
    """統計に外部バイト回数・総勤務(自院合計+外部バイト)が正しく反映されること
    (2026年10月=31日=62枠)"""
    members = [
        Member(name="A", target_count=21, gaikobu_eligible=True),
        Member(name="B", target_count=21, gaikobu_eligible=True),
        Member(name="C", target_count=20, gaikobu_eligible=True),
    ]
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


def test_annual_actual_balance_does_not_override_hard_target():
    """目標回数がhard constraintになったため、年間実績の偏り
    (annual_actual_totals)がどれだけ大きくても、各メンバーの当月実績は
    設定されたtarget_countと厳密に一致し、それ以外の値には決して
    調整されないこと(=optimizerがtargetを勝手に動かさないことの確認)。
    (2026年6月=30日=60枠)"""
    members = [Member(name=n, target_count=20) for n in ["A", "B", "C"]]
    annual_totals = {"A": 0, "B": 20, "C": 20}  # Aは年間実績ゼロ、B・Cは既に20回
    optimizer = OnCallOptimizer(
        year=2026, month=6, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=15, annual_actual_totals=annual_totals),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")
    # 年間実績に関わらず、当月の実績は全員target(20)ちょうどになる
    for name, s in result.stats.items():
        assert s["total"] == 20
        assert s["diff"] == 0


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


def test_weekend_pairs_detects_same_weekend_saturday_sunday():
    """_weekend_pairs() が月内の連続する(土曜,日曜)を正しく検出すること
    (2026年8月は8/1が土曜始まりのため、8/1-8/2, 8/8-8/9, ... が対になる)"""
    members = [
        Member(name="A", target_count=16),
        Member(name="B", target_count=16),
        Member(name="C", target_count=15),
        Member(name="D", target_count=15),
    ]
    optimizer = OnCallOptimizer(
        year=2026, month=8, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=5),
    )
    pairs = optimizer._weekend_pairs()
    assert (date(2026, 8, 1), date(2026, 8, 2)) in pairs
    assert (date(2026, 8, 8), date(2026, 8, 9)) in pairs
    # 月末(8/29が土曜)は8/30が日曜として月内に存在するのでペアになる
    assert (date(2026, 8, 29), date(2026, 8, 30)) in pairs


def test_weekend_pairing_reduces_split_weekends():
    """週末ペア化(公平性側ソフトH)により、同じ人の土日オンコールが
    同じ週末にまとまりやすくなること(重み0との比較で分断が減ること)。
    (2026年8月=31日=62枠)"""
    members = [
        Member(name="A", target_count=16),
        Member(name="B", target_count=16),
        Member(name="C", target_count=15),
        Member(name="D", target_count=15),
    ]

    def _count_splits(weight: int) -> int:
        weights = OptimizerWeights(weekend_pairing=weight)
        optimizer = OnCallOptimizer(
            year=2026, month=8, members=members, unavailabilities=[],
            options=OptimizerOptions(max_time_seconds=20, weights=weights),
        )
        result = optimizer.solve()
        assert result.status in ("OPTIMAL", "FEASIBLE")
        by_day = {e.day: e for e in result.entries}
        splits = 0
        for sat, sun in optimizer._weekend_pairs():
            sat_names = {n for n in [by_day[sat].assignments[Slot.DAY], by_day[sat].assignments[Slot.NIGHT]] if n}
            sun_names = {n for n in [by_day[sun].assignments[Slot.DAY], by_day[sun].assignments[Slot.NIGHT]] if n}
            for name in sat_names ^ sun_names:  # 片方だけに現れる人=分断
                if (name in sat_names) != (name in sun_names):
                    splits += 1
        return splits

    splits_without_pairing = _count_splits(weight=0)
    splits_with_pairing = _count_splits(weight=20)
    assert splits_with_pairing <= splits_without_pairing


def test_rest_rule_violations_are_zero_when_avoidable():
    """不都合日などの制約が特に無い通常のケースでは、休息ルール違反
    (平日Night翌日勤務・土曜Night→日曜Day・日曜Night→月曜勤務・
    5日以上連続Call・3日以上連続Call後の2連休不足)は回避可能であり、
    実際に0件で目標回数を達成できること。
    (2026年8月=31日=62枠、5人)"""
    members = [
        Member(name="A", target_count=13),
        Member(name="B", target_count=13),
        Member(name="C", target_count=12),
        Member(name="D", target_count=12),
        Member(name="E", target_count=12),
    ]
    optimizer = OnCallOptimizer(
        year=2026, month=8, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=25),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")

    for name, s in result.stats.items():
        assert s["diff"] == 0
        assert s["weekday_night_violation"] == 0, f"{name}: {s}"
        assert s["sunday_night_monday_violation"] == 0, f"{name}: {s}"
        assert s["consecutive_rule_violation"] == 0, f"{name}: {s}"


if __name__ == "__main__":
    test_basic_feasible_schedule()
    test_unavailability_is_respected()
    test_single_person_per_slot()
    test_gaikobu_assigned_only_to_eligible_members()
    test_gaikobu_excludes_own_hospital_same_day()
    test_gaikobu_respects_unavailability()
    test_gaikobu_stats_totals()
    test_annual_actual_balance_does_not_override_hard_target()
    test_month_weeks_starts_on_sunday()
    test_weekend_pairs_detects_same_weekend_saturday_sunday()
    test_weekend_pairing_reduces_split_weekends()
    test_rest_rule_violations_are_zero_when_avoidable()
    print("全テスト成功")
