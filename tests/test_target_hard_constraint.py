# -*- coding: utf-8 -*-
"""
目標Call回数のhard constraint化と、休息ルールとの優先順位に関する
end-to-endのテスト。

検証内容:
  1. 休息ルール違反なしでは目標回数を達成できないケースでは、
     最小限の違反(このケースでは1件)だけを許容して目標回数を
     達成すること。
  2. 目標回数自体が物理的に不可能な設定(sum(target) != 総枠数、
     または個人のtargetが割当可能枠数を超える)では、目標回数を
     自動調整せずINFEASIBLEになり、理由がwarningsに明示されること。

実行方法:
    python -m pytest tests/test_target_hard_constraint.py -v -s
"""
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.models import Member, Slot, Unavailability
from src.optimizer import OnCallOptimizer, OptimizerOptions


def test_minimal_rest_violation_allowed_when_required_to_hit_target():
    """
    2026年2月(28日=56枠)。
    Aは2/9(月)・2/10(火)のDay枠が使えない(不都合日)よう設定し、
    かつAのtarget=2とすることで、Aは2/9・2/10のNight枠2つでしか
    targetを達成できないようにする(他に利用可能な枠が無い)。
    2/9(月)Night → 2/10(火)Nightは「月〜金Night→翌日完全OFF」に
    抵触するため、Aのtargetを達成するには休息ルール違反が
    最低1件必要になる。B〜Eの4人は制約なしで残り54枠を自由に
    埋められる(1人当たり月28日中13〜14回程度で済み、毎日勤務する
    必要はない)ため、5日以上連続Call等の副作用を起こさずに済み、
    全体の最小違反数はちょうど1件になるはずである。
    (B・Cの2人だけに残り54枠を担わせると、26日間毎日どちらかが
    働き続けることになり5日連続Callルールに必ず抵触してしまうため、
    ここでは4人に分散させている)
    """
    members = [
        Member(name="A", target_count=2),
        Member(name="B", target_count=14),
        Member(name="C", target_count=14),
        Member(name="D", target_count=13),
        Member(name="E", target_count=13),
    ]
    unavailabilities = [
        Unavailability(member_name="A", day=date(2026, 2, 9), day_unavailable=True, night_unavailable=False),
        Unavailability(member_name="A", day=date(2026, 2, 10), day_unavailable=True, night_unavailable=False),
    ]
    # Aをこの2日間以外すべて不都合日にして、targetの2回がこの2日の
    # Night枠だけで達成されるように強制する。
    for d in [date(2026, 2, day) for day in range(1, 29) if day not in (9, 10)]:
        unavailabilities.append(
            Unavailability(member_name="A", day=d, day_unavailable=True, night_unavailable=True)
        )

    optimizer = OnCallOptimizer(
        year=2026, month=2, members=members, unavailabilities=unavailabilities,
        options=OptimizerOptions(max_time_seconds=25),
    )
    result = optimizer.solve()

    assert result.status in ("OPTIMAL", "FEASIBLE"), result.warnings
    for name, s in result.stats.items():
        assert s["diff"] == 0, f"{name}: {s}"

    a_stats = result.stats["A"]
    assert a_stats["night"] == 2  # 2/9, 2/10ともNightで達成しているはず
    assert a_stats["weekday_night_violation"] == 1, a_stats
    assert a_stats["sunday_night_monday_violation"] == 0
    assert a_stats["consecutive_rule_violation"] == 0

    total_violations = sum(
        s["weekday_night_violation"] + s["sunday_night_monday_violation"] + s["consecutive_rule_violation"]
        for s in result.stats.values()
    )
    assert total_violations == 1, "目標達成に必要な最小限(1件)のみ違反を許容するはず"


def test_infeasible_when_target_sum_does_not_match_total_slots():
    """sum(target_count) != 総枠数 の場合、目標回数を自動調整せず
    INFEASIBLEを返し、理由をwarningsに明示すること。
    (2026年4月=30日=60枠に対し、target合計をわざと50にする)"""
    members = [Member(name=n, target_count=10) for n in ["A", "B", "C", "D", "E"]]  # 合計50 != 60
    optimizer = OnCallOptimizer(
        year=2026, month=4, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=5),
    )
    result = optimizer.solve()

    assert result.status == "INFEASIBLE"
    assert result.entries == []
    # target自体は変更されていないことを確認
    for name, s in result.stats.items():
        assert s["target"] == 10
    assert any("許容範囲の合計" in w for w in result.warnings)
    assert any("final targetは変更していません" in w for w in result.warnings)


def test_infeasible_when_individual_target_exceeds_availability():
    """個人のtargetが、不都合日を除いた割当可能枠数を超える場合も
    INFEASIBLEとなり、該当メンバー名が理由に明示されること。
    (2026年4月=30日=60枠。Aは1日を除いて終日不都合にし、
    target_countだけ大きく設定する)"""
    members = [
        Member(name="A", target_count=10),  # 実際の割当可能枠は2枠しかない
        Member(name="B", target_count=50),
    ]
    unavailabilities = [
        Unavailability(member_name="A", day=date(2026, 4, d), day_unavailable=True, night_unavailable=True)
        for d in range(2, 31)
    ]
    optimizer = OnCallOptimizer(
        year=2026, month=4, members=members, unavailabilities=unavailabilities,
        options=OptimizerOptions(max_time_seconds=5),
    )
    result = optimizer.solve()

    assert result.status == "INFEASIBLE"
    assert any("A" in w and "割当可能枠数" in w for w in result.warnings)
    for name, s in result.stats.items():
        # targetは自動調整されていない
        assert s["target"] == (10 if name == "A" else 50)


def test_remaining_target_exceeding_this_months_availability_is_not_flagged_infeasible():
    """remaining_target(=final target)が今月の物理的な割当可能枠数を超えて
    いても、それ自体は問題ではないこと(過去に「upperがavailableを超えたら
    常にINFEASIBLE」という誤ったチェックが入っていたことへの回帰テスト)。
    今月中に最低限消化すべき量(lower = max(0, R-F))さえ今月の割当可能枠数に
    収まっていれば、構造的な事前診断でINFEASIBLEと判定されてはならない。
    (2026年4月=30日=60枠。Aのremaining_target=50は今月の物理上限
    (30日×1人1日1枠=最大30)を上回るが、lower=max(0,50-45)=5は
    十分に達成可能なので事前診断はパスするはず)"""
    remaining = {"A": 50, "B": 5, "C": 5}
    future = {"A": 45, "B": 0, "C": 0}
    members = [Member(name=n, target_count=t) for n, t in remaining.items()]
    optimizer = OnCallOptimizer(
        year=2026, month=4, members=members, unavailabilities=[],
        options=OptimizerOptions(
            max_time_seconds=5,
            remaining_target=remaining,
            future_available_slots=future,
        ),
    )
    optimizer.build()
    reasons = optimizer._diagnose_infeasibility_precheck()
    assert reasons == [], f"upperがavailableを超えるだけで誤ってINFEASIBLE判定されている: {reasons}"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
