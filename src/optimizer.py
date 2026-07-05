# -*- coding: utf-8 -*-
"""
OR-Tools CP-SAT による勤務表最適化エンジン

■ 絶対条件 (Hard constraints)
  1. 不都合日には割り当てない(メンバーが入力した不都合日に加えて、
     長期不在期間も終日の不都合日として自動的に扱われる)
  2. 各枠(日中/夜間)には1人だけ割り当てる
  3. 同じ人が同日の日中・夜間を両方担当しない
  4. (外部バイト対象日) 外部バイト対象者の中から必ず1人を割り当てる
  5. (外部バイト対象日) 不都合日(日中または夜間どちらか)がある人は割り当てない
  6. 外部バイトに入った日は、自院の日中・夜間どちらにも入らない

■ できるだけ守りたい条件 (Soft constraints / 目的関数で最小化)
  目的関数の優先順位(重みの大小で表現):
  A. 自院オンコールの年間実績(actual_assignments)を均等化 ← 最優先
  B. その月の自院オンコール目標回数からのズレを最小化
  C. 日中/夜間の担当回数の偏りを最小化 (個人ごと)
  D. 夜間→翌日日中のような連続勤務を避ける
  E. 月内での勤務日の偏り(集中)を避ける
  F. 土日・祝日担当回数の偏りを最小化
  G. 外部バイトの回数を対象者間でなるべく均等にする(義務ではないため優先度は低い)

自院オンコールは義務勤務であるのに対し、外部病院バイトは義務ではないため、
外部バイトの均等化は他の条件よりも優先度を下げている。

各ソフト制約には重み(weight)を持たせ、重要度に応じて調整できるようにしている。
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from ortools.sat.python import cp_model

from .models import Member, ScheduleEntry, ScheduleResult, Slot, Unavailability

logger = logging.getLogger(__name__)


@dataclass
class OptimizerWeights:
    """ソフト制約の重み。値が大きいほどその条件を強く重視する。"""

    annual_actual_balance: int = 300  # 自院オンコールの年間実績均等化(最優先)
    target_count_deviation: int = 50
    day_night_balance: int = 15
    consecutive_shift: int = 12
    spread_clustering: int = 5
    weekend_holiday_balance: int = 8
    gaikobu_balance: int = 6  # 外部バイト回数を対象者間で均等にする重み(義務ではないため低め)


@dataclass
class OptimizerOptions:
    weights: OptimizerWeights = None
    holidays: Optional[Set[date]] = None  # 祝日リスト(任意)
    fixed_assignments: Optional[Dict[Tuple[date, Slot], str]] = None  # 手動固定枠(将来拡張)
    gaikobu_days: Optional[Set[date]] = None  # 外部病院バイトが必要な日
    annual_actual_totals: Optional[Dict[str, int]] = None  # 自院オンコールの年間実績(この月より前の確定分)
    max_time_seconds: float = 30.0

    def __post_init__(self):
        if self.weights is None:
            self.weights = OptimizerWeights()
        if self.holidays is None:
            self.holidays = set()
        if self.fixed_assignments is None:
            self.fixed_assignments = {}
        if self.gaikobu_days is None:
            self.gaikobu_days = set()
        if self.annual_actual_totals is None:
            self.annual_actual_totals = {}


class OnCallOptimizer:
    def __init__(
        self,
        year: int,
        month: int,
        members: List[Member],
        unavailabilities: List[Unavailability],
        options: Optional[OptimizerOptions] = None,
    ):
        self.year = year
        self.month = month
        self.members = members
        self.member_names = [m.name for m in members]
        self.target_count = {m.name: m.target_count for m in members}
        self.options = options or OptimizerOptions()

        self.days: List[date] = self._month_days(year, month)

        # 外部バイト対象者・対象日(月の範囲内のみ)
        self.gaikobu_eligible_names: List[str] = [
            m.name for m in members if getattr(m, "gaikobu_eligible", False)
        ]
        self.gaikobu_days: List[date] = sorted(
            d for d in self.options.gaikobu_days if d in set(self.days)
        )

        # (member_name, day) -> Unavailability
        self.unavail_map: Dict[Tuple[str, date], Unavailability] = {
            (u.member_name, u.day): u for u in unavailabilities
        }

        self.model = cp_model.CpModel()
        self.x: Dict[Tuple[date, Slot, str], cp_model.IntVar] = {}
        self.g: Dict[Tuple[date, str], cp_model.IntVar] = {}  # 外部バイト割当変数
        self.warnings: List[str] = []

    @staticmethod
    def _month_days(year: int, month: int) -> List[date]:
        n_days = calendar.monthrange(year, month)[1]
        return [date(year, month, d) for d in range(1, n_days + 1)]

    def _is_weekend_or_holiday(self, d: date) -> bool:
        return d.weekday() >= 5 or d in self.options.holidays

    def _is_unavailable(self, member: str, day: date, slot: Slot) -> bool:
        u = self.unavail_map.get((member, day))
        return u.is_unavailable(slot) if u else False

    def _is_unavailable_any(self, member: str, day: date) -> bool:
        """外部バイトは終日の勤務とみなすため、日中・夜間いずれかの
        不都合があればその日は外部バイトにも割り当てない。"""
        u = self.unavail_map.get((member, day))
        return bool(u and (u.day_unavailable or u.night_unavailable))

    # ------------------------------------------------------------------
    # モデル構築
    # ------------------------------------------------------------------
    def build(self) -> None:
        model = self.model

        # --- 変数定義: x[day, slot, member] ---
        for d in self.days:
            for slot in (Slot.DAY, Slot.NIGHT):
                for name in self.member_names:
                    self.x[(d, slot, name)] = model.NewBoolVar(
                        f"x_{d.isoformat()}_{slot.value}_{name}"
                    )

        # --- 絶対条件① 不都合日には割り当てない ---
        for d in self.days:
            for slot in (Slot.DAY, Slot.NIGHT):
                for name in self.member_names:
                    if self._is_unavailable(name, d, slot):
                        model.Add(self.x[(d, slot, name)] == 0)

        # --- 絶対条件② 各枠は1人のみ ---
        for d in self.days:
            for slot in (Slot.DAY, Slot.NIGHT):
                model.Add(
                    sum(self.x[(d, slot, name)] for name in self.member_names) == 1
                )

        # --- 絶対条件③ 同日の日中・夜間を同じ人が担当しない ---
        for d in self.days:
            for name in self.member_names:
                model.Add(
                    self.x[(d, Slot.DAY, name)] + self.x[(d, Slot.NIGHT, name)] <= 1
                )

        # --- 外部病院バイト: 変数定義 ---
        # 対象日 × 対象者(gaikobu_eligible=Trueのメンバー)のみ変数を作る
        for d in self.gaikobu_days:
            for name in self.gaikobu_eligible_names:
                self.g[(d, name)] = model.NewBoolVar(f"g_{d.isoformat()}_{name}")

        # --- 絶対条件④ 外部バイト対象日は対象者の中から必ず1人を割り当てる ---
        for d in self.gaikobu_days:
            if self.gaikobu_eligible_names:
                model.Add(sum(self.g[(d, name)] for name in self.gaikobu_eligible_names) == 1)

        # --- 絶対条件⑤ 不都合日(日中または夜間)がある対象者は外部バイトに割り当てない ---
        for d in self.gaikobu_days:
            for name in self.gaikobu_eligible_names:
                if self._is_unavailable_any(name, d):
                    model.Add(self.g[(d, name)] == 0)

        # --- 絶対条件⑥ 外部バイトに入った日は自院の日中・夜間どちらにも入らない ---
        for d in self.gaikobu_days:
            for name in self.gaikobu_eligible_names:
                model.Add(
                    self.x[(d, Slot.DAY, name)] + self.x[(d, Slot.NIGHT, name)] + self.g[(d, name)] <= 1
                )

        # --- 手動固定枠(将来拡張): 指定があれば強制的に割り当てる ---
        for (fd, fslot), fname in self.options.fixed_assignments.items():
            if (fd, fslot, fname) in self.x:
                model.Add(self.x[(fd, fslot, fname)] == 1)

        penalty_terms: List[cp_model.LinearExpr] = []
        w = self.options.weights

        # --- ソフトA: 自院オンコールの年間実績を均等化(最優先) ---
        # 「この月より前の確定済み年間実績」+「この月の割当予定数」の合計が、
        # メンバー間でなるべく均等になるようにする(予定ではなく実績を基準にする)。
        annual_prior = self.options.annual_actual_totals
        if annual_prior:
            max_upper = max(annual_prior.values(), default=0) + len(self.days) * 2 + 1
            combined_vars = {}
            for name in self.member_names:
                month_total = sum(
                    self.x[(d, slot, name)] for d in self.days for slot in (Slot.DAY, Slot.NIGHT)
                )
                prior = int(annual_prior.get(name, 0))
                combined = model.NewIntVar(0, max_upper, f"annual_combined_{name}")
                model.Add(combined == month_total + prior)
                combined_vars[name] = combined

            annual_max = model.NewIntVar(0, max_upper, "annual_max")
            annual_min = model.NewIntVar(0, max_upper, "annual_min")
            for name in self.member_names:
                model.Add(combined_vars[name] <= annual_max)
                model.Add(combined_vars[name] >= annual_min)
            penalty_terms.append((annual_max - annual_min) * w.annual_actual_balance)

        # --- ソフトB: 目標回数からのズレを最小化 ---
        for name in self.member_names:
            total = sum(
                self.x[(d, slot, name)] for d in self.days for slot in (Slot.DAY, Slot.NIGHT)
            )
            target = self.target_count.get(name, 0)
            diff = model.NewIntVar(-len(self.days) * 2, len(self.days) * 2, f"diff_{name}")
            model.Add(diff == total - target)
            abs_diff = model.NewIntVar(0, len(self.days) * 2, f"absdiff_{name}")
            model.AddAbsEquality(abs_diff, diff)
            penalty_terms.append(abs_diff * w.target_count_deviation)

        # --- ソフトC: 日中/夜間の担当回数の偏りを最小化(個人ごと) ---
        for name in self.member_names:
            day_total = sum(self.x[(d, Slot.DAY, name)] for d in self.days)
            night_total = sum(self.x[(d, Slot.NIGHT, name)] for d in self.days)
            bdiff = model.NewIntVar(-len(self.days), len(self.days), f"bdiff_{name}")
            model.Add(bdiff == day_total - night_total)
            babs = model.NewIntVar(0, len(self.days), f"babs_{name}")
            model.AddAbsEquality(babs, bdiff)
            penalty_terms.append(babs * w.day_night_balance)

        # --- ソフトD: 夜間→翌日日中の連続勤務を避ける ---
        for i in range(len(self.days) - 1):
            d_today = self.days[i]
            d_tomorrow = self.days[i + 1]
            for name in self.member_names:
                # 同じ人が今日の夜間と明日の日中を両方担当したらペナルティ
                consec = model.NewBoolVar(f"consec_{d_today.isoformat()}_{name}")
                model.AddMultiplicationEquality(
                    consec,
                    [self.x[(d_today, Slot.NIGHT, name)], self.x[(d_tomorrow, Slot.DAY, name)]],
                )
                penalty_terms.append(consec * w.consecutive_shift)

        # --- ソフトE: 勤務日の偏り(近接日への集中)を避ける ---
        # まず「その日に(日中/夜間どちらかで)割り当てられているか」を表す
        # Bool変数を用意する(同日の日中・夜間は排他なので0/1で表現できる)。
        assigned_on_day: Dict[Tuple, cp_model.IntVar] = {}
        for d in self.days:
            for name in self.member_names:
                var = model.NewBoolVar(f"assigned_{d.isoformat()}_{name}")
                model.Add(var == self.x[(d, Slot.DAY, name)] + self.x[(d, Slot.NIGHT, name)])
                assigned_on_day[(d, name)] = var

        # 同じ人が近い日(3日以内)に複数回入るとペナルティを与える(ペアごとに判定)
        window = 3
        for name in self.member_names:
            for i, d in enumerate(self.days):
                nearby_days = self.days[i + 1 : i + 1 + window]
                for nd in nearby_days:
                    both = model.NewBoolVar(f"cluster_{d.isoformat()}_{nd.isoformat()}_{name}")
                    model.AddMultiplicationEquality(
                        both, [assigned_on_day[(d, name)], assigned_on_day[(nd, name)]]
                    )
                    penalty_terms.append(both * w.spread_clustering)

        # --- ソフトF: 土日・祝日の偏りを最小化 ---
        weekend_days = [d for d in self.days if self._is_weekend_or_holiday(d)]
        if weekend_days:
            avg_weekend = len(weekend_days) * 2 / max(len(self.member_names), 1)
            for name in self.member_names:
                weekend_total = sum(
                    self.x[(d, slot, name)] for d in weekend_days for slot in (Slot.DAY, Slot.NIGHT)
                )
                scaled_avg = round(avg_weekend * 100)
                wdiff = model.NewIntVar(-100000, 100000, f"wdiff_{name}")
                model.Add(wdiff == weekend_total * 100 - scaled_avg)
                wabs = model.NewIntVar(0, 100000, f"wabs_{name}")
                model.AddAbsEquality(wabs, wdiff)
                # weekend_totalを100倍したスケールで比較しているため、
                # 重みも同スケールに合わせて弱める(最低1)
                scaled_weight = max(1, w.weekend_holiday_balance // 20)
                penalty_terms.append(wabs * scaled_weight)

        # --- ソフトG: 外部バイトの回数を対象者間でなるべく均等にする ---
        if self.gaikobu_days and self.gaikobu_eligible_names:
            total_gaikobu: Dict[str, cp_model.LinearExpr] = {
                name: sum(self.g[(d, name)] for d in self.gaikobu_days)
                for name in self.gaikobu_eligible_names
            }
            max_g = model.NewIntVar(0, len(self.gaikobu_days), "gaikobu_max")
            min_g = model.NewIntVar(0, len(self.gaikobu_days), "gaikobu_min")
            for name in self.gaikobu_eligible_names:
                model.Add(total_gaikobu[name] <= max_g)
                model.Add(total_gaikobu[name] >= min_g)
            penalty_terms.append((max_g - min_g) * w.gaikobu_balance)

        model.Minimize(sum(penalty_terms))

    # ------------------------------------------------------------------
    # 求解
    # ------------------------------------------------------------------
    def solve(self) -> ScheduleResult:
        self.build()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.options.max_time_seconds
        solver.parameters.num_search_workers = 8
        status = solver.Solve(self.model)

        status_name = solver.StatusName(status)
        entries: List[ScheduleEntry] = []
        stats: Dict[str, Dict[str, int]] = {
            name: {
                "day": 0,
                "night": 0,
                "total": 0,
                "target": self.target_count.get(name, 0),
                "diff": 0,
                "gaikobu": 0,
                "grand_total": 0,
            }
            for name in self.member_names
        }

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.warnings.append(
                "最適化に失敗しました(INFEASIBLE)。不都合日の条件が厳しすぎる可能性があります。"
                "目標回数や不都合日の設定を見直してください。"
            )
            return ScheduleResult(
                year=self.year,
                month=self.month,
                entries=[],
                status=status_name,
                stats=stats,
                warnings=self.warnings,
            )

        for d in self.days:
            entry = ScheduleEntry(day=d)
            for slot in (Slot.DAY, Slot.NIGHT):
                assigned_name = None
                for name in self.member_names:
                    if solver.Value(self.x[(d, slot, name)]) == 1:
                        assigned_name = name
                        break
                entry.assignments[slot] = assigned_name
                if assigned_name is None:
                    self.warnings.append(f"{d.isoformat()} {slot.label_ja}: 割当できませんでした")
                else:
                    stats[assigned_name]["total"] += 1
                    stats[assigned_name][slot.value] += 1

            if d in self.gaikobu_days:
                assigned_gaikobu = None
                for name in self.gaikobu_eligible_names:
                    if solver.Value(self.g[(d, name)]) == 1:
                        assigned_gaikobu = name
                        break
                entry.gaikobu = assigned_gaikobu
                if assigned_gaikobu is None:
                    self.warnings.append(f"{d.isoformat()} 外部バイト: 割当できませんでした")
                else:
                    stats[assigned_gaikobu]["gaikobu"] += 1

            entries.append(entry)

        for name in self.member_names:
            stats[name]["diff"] = stats[name]["total"] - stats[name]["target"]
            stats[name]["grand_total"] = stats[name]["total"] + stats[name]["gaikobu"]

        return ScheduleResult(
            year=self.year,
            month=self.month,
            entries=entries,
            status=status_name,
            stats=stats,
            warnings=self.warnings,
        )
