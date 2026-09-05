# -*- coding: utf-8 -*-
"""
月次生成フロー(3月末final targetを維持しながら、毎月の残り必要回数を
更新していく設計)の単体テスト。

検証内容:
  1. remaining_target/future_available_slots を指定しない場合、
     従来どおり target_count に厳密一致するhard equalityとして動作すること
     (後方互換)。
  2. month_target(月別目標)を指定した通常のケースでは、それがexactに
     達成され、month_targetからの変更は発生しないこと。
  3. remaining_target/future_available_slotsによる「先読み」hard
     constraintにより、今月の割当だけでfinal targetを超えて使い切ったり、
     将来消化不能な量を残したりすることがないこと。
  4. month_target exactでは休息ルール違反0にできないが、最大ズレを+1
     まで緩和すれば違反0にできるケースで、実際に「安全網」として
     +1までの緩和が使われ、かつそれが最小限(特定の1人だけ)に
     留まること。
  5. 最終月(future_available_slots=0)では、month_target無関係に
     remaining_targetちょうどが厳密に達成されること。

実行方法:
    python -m pytest tests/test_monthly_flow.py -v -s
"""
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.models import Member, Slot, Unavailability
from src.optimizer import OnCallOptimizer, OptimizerOptions, verify_schedule_result


def test_defaults_are_backward_compatible_hard_equality():
    """remaining_target/future_available_slots/month_targetを指定しない場合、
    target_countに厳密一致する従来のhard equalityと完全に等価であること。
    (2026年2月=28日=56枠)"""
    members = [Member(name=n, target_count=14) for n in ["A", "B", "C", "D"]]
    optimizer = OnCallOptimizer(
        year=2026, month=2, members=members, unavailabilities=[],
        options=OptimizerOptions(max_time_seconds=20),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")
    for name, s in result.stats.items():
        assert s["total"] == 14
        assert s["diff"] == 0
        assert s["deviation"] == 0  # month_targetも同じ値にフォールバックしている


def test_month_target_is_achieved_exactly_when_feasible():
    """month_targetを明示指定した通常のケースで、休息ルール違反0のまま
    月別目標がexactに達成され、'月別目標から変更されたメンバー'が
    いないこと。(2026年8月=31日=62枠)"""
    targets = {"A": 13, "B": 13, "C": 12, "D": 12, "E": 12}
    members = [Member(name=n, target_count=t) for n, t in targets.items()]
    optimizer = OnCallOptimizer(
        year=2026, month=8, members=members, unavailabilities=[],
        options=OptimizerOptions(
            max_time_seconds=40,
            month_target=targets,
            remaining_target=targets,
            future_available_slots={n: 0 for n in targets},
        ),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")

    ok, report = verify_schedule_result(result)
    assert ok, report
    for name, s in result.stats.items():
        assert s["deviation"] == 0, report
        assert s["weekday_night_violation"] == 0
        assert s["sunday_night_monday_violation"] == 0
        assert s["consecutive_rule_violation"] == 0
    assert "月別目標からの変更: なし" in report


def test_lookahead_hard_constraint_prevents_overuse_and_underuse():
    """remaining_target/future_available_slotsのhard constraintにより、
    今月の割当が [remaining_target - future_available_slots, remaining_target]
    の範囲に収まること(final targetを超えて使い切らない/将来消化不能な
    量を残さない)。(2026年10月=31日=62枠, 5人)"""
    remaining = {"A": 20, "B": 15, "C": 12, "D": 8, "E": 7}  # 合計62
    future_avail = {"A": 10, "B": 5, "C": 0, "D": 20, "E": 30}
    month_target = {"A": 15, "B": 15, "C": 12, "D": 10, "E": 10}  # 合計62(soft)
    members = [Member(name=n, target_count=t) for n, t in remaining.items()]
    optimizer = OnCallOptimizer(
        year=2026, month=10, members=members, unavailabilities=[],
        options=OptimizerOptions(
            max_time_seconds=60,
            month_target=month_target,
            remaining_target=remaining,
            future_available_slots=future_avail,
        ),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")
    for name, s in result.stats.items():
        R = remaining[name]
        F = future_avail[name]
        assert s["total"] <= R, f"{name}: {s['total']} > remaining_target={R}"
        assert R - s["total"] <= F, (
            f"{name}: 今月の割当後、残り{R - s['total']}が将来枠{F}を超えている"
            "(final target達成が将来的に不可能になっている)"
        )


def test_final_month_forces_exact_remaining_target_regardless_of_month_target():
    """最終月(future_available_slots=0)では、month_targetの値に関わらず
    total_calls == remaining_target が厳密に強制されること。
    (2026年3月=31日=62枠。month_targetをわざとremaining_targetと
    ずらして、それでも実際の割当はremaining_target側に一致することを確認)"""
    remaining = {"A": 20, "B": 20, "C": 12, "D": 10}  # 合計62
    wrong_month_target = {"A": 10, "B": 10, "C": 22, "D": 20}  # 合計62だが値が違う
    members = [Member(name=n, target_count=t) for n, t in remaining.items()]
    optimizer = OnCallOptimizer(
        year=2026, month=3, members=members, unavailabilities=[],
        options=OptimizerOptions(
            max_time_seconds=40,
            month_target=wrong_month_target,
            remaining_target=remaining,
            future_available_slots={n: 0 for n in remaining},  # 最終月
        ),
    )
    result = optimizer.solve()
    assert result.status in ("OPTIMAL", "FEASIBLE")
    for name, s in result.stats.items():
        assert s["total"] == remaining[name], (
            f"{name}: 最終月はremaining_target({remaining[name]})に厳密一致すべきだが"
            f"actual={s['total']}"
        )


def _relaxation_scenario(relaxation: int):
    """安全網シナリオ: 2026年2月(28日=56枠)。
    Aは2/9(月)・2/10(火)のDay枠が使えず、かつそれ以外全日不都合日なので、
    Aが2回働くには2/9 Night・2/10 Night の両方を使うしかなく、これは
    「月〜金Night→翌日完全OFF」に抵触して休息ルール違反1件になる。

    ここでAのremaining_target=2・future_available_slots=1とすることで、
    「今月2回のうち1回を来月以降に繰り越す(今月は1回だけ働く)」ことが
    hardに許容されるようにする。その代わり、その1回分の穴を埋めるため
    Bのremaining_targetに+1のバッファ(15)を持たせておく。

    これにより、月別目標どおりの2回(exact)を選べば休息ルール違反が
    避けられない一方、Aが1回・Bが+1回、という月別目標から見て±1の
    乖離を許容すれば休息ルール違反0が実現できる、という状況を作れる。
    relaxationの値によって、この乖離が実際に選ばれるかどうかが変わる。
    """
    month_target = {"A": 2, "B": 14, "C": 14, "D": 13, "E": 13}  # 合計56
    remaining = {"A": 2, "B": 15, "C": 14, "D": 13, "E": 13}  # Bだけ+1のバッファ
    future = {"A": 1, "B": 1, "C": 0, "D": 0, "E": 0}  # A・Bとも1回分の増減が許容される
    members = [Member(name=n, target_count=remaining[n]) for n in month_target]
    unavailabilities = [
        Unavailability(member_name="A", day=date(2026, 2, 9), day_unavailable=True, night_unavailable=False),
        Unavailability(member_name="A", day=date(2026, 2, 10), day_unavailable=True, night_unavailable=False),
    ]
    for d in [date(2026, 2, day) for day in range(1, 29) if day not in (9, 10)]:
        unavailabilities.append(
            Unavailability(member_name="A", day=d, day_unavailable=True, night_unavailable=True)
        )

    optimizer = OnCallOptimizer(
        year=2026, month=2, members=members, unavailabilities=unavailabilities,
        options=OptimizerOptions(
            max_time_seconds=60,
            month_target=month_target,
            remaining_target=remaining,
            future_available_slots=future,
            max_deviation_relaxation=relaxation,
        ),
    )
    return optimizer.solve()


def test_relaxation_safety_net_used_minimally_when_beneficial():
    """max_deviation_relaxation=1(デフォルト)の場合: 休息ルール違反0を
    実現するために、Aだけ月別目標から-1、Bだけ+1(バッファの範囲内)の
    最小限の乖離が安全網として使われること。"""
    result = _relaxation_scenario(relaxation=1)
    assert result.status in ("OPTIMAL", "FEASIBLE"), result.warnings

    total_violations = sum(
        s["weekday_night_violation"] + s["sunday_night_monday_violation"] + s["consecutive_rule_violation"]
        for s in result.stats.values()
    )
    assert total_violations == 0, "休息ルール違反0が優先されるはず(安全網が使われるべきケース)"

    deviated = {name: s["deviation"] for name, s in result.stats.items() if s["deviation"] != 0}
    assert set(deviated.keys()) == {"A", "B"}, f"想定外のメンバーで月別目標が変更された: {deviated}"
    assert deviated["A"] == -1 and deviated["B"] == 1, f"想定外の乖離量: {deviated}"
    for name, s in result.stats.items():
        assert s["max_deviation"] <= 1


def test_relaxation_disabled_leaves_rest_rule_violation():
    """max_deviation_relaxation=0にした場合、月別目標からの乖離が一切
    許されないため、同じシナリオでも休息ルール違反(1件)が残ること
    (=安全網が無効なら目標達成のために休息ルールが破られることの確認)。"""
    result = _relaxation_scenario(relaxation=0)
    assert result.status in ("OPTIMAL", "FEASIBLE"), result.warnings
    for name, s in result.stats.items():
        assert s["deviation"] == 0, "緩和禁止なので月別目標どおりのはず"
    total_violations = sum(
        s["weekday_night_violation"] + s["sunday_night_monday_violation"] + s["consecutive_rule_violation"]
        for s in result.stats.values()
    )
    assert total_violations >= 1, "緩和を禁止した場合は休息ルール違反が残るはず"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
