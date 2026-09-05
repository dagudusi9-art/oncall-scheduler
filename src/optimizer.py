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
  7. 各メンバーの自院オンコール合計(total_calls)は、以下の範囲を超えない
     (月次生成フロー向けのhard constraint。3月末などの最終目標(final target)
     を絶対に動かさないための「先読み」制約):

       total_calls[name] <= remaining_target[name]
       remaining_target[name] - total_calls[name] <= future_available_slots[name]

     remaining_target = 最終目標(final target) - これまでの確定実績。
     future_available_slots = 来月以降(将来)に既知の長期不在を反映した
     割当可能枠数。

     この2本の不等式だけで、以下が自動的に保証される:
       - 今月だけでfinal targetを超えて使い切ることはない(1本目)
       - 今月の割当後、残りを将来枠数以内で必ず消化できる、すなわち
         「今月の割当によってfinal target達成が将来的に不可能になる」
         ことは絶対に起こらない(2本目)
       - 最終月(future_available_slots=0の月)では自動的に
         total_calls == remaining_target の厳密一致に収束する(2本目の
         不等式が total_calls >= remaining_target を強制するため)。
     remaining_target / future_available_slots を明示的に指定しない場合は、
     それぞれ target_count 自身 / 0 がデフォルト値として使われ、これは
     従来どおりの「today's target_countに厳密一致」という単月hard
     equalityと完全に等価になる(後方互換)。

■ できるだけ守りたい条件 (Soft constraints)
  以下の4段階で最適化する(疑似的な重み付けではなく、実際に複数回solveする
  厳密な辞書式(lexicographic)最適化)。優先順位は上から順:

  [第1段階] 月別目標(month_target)からの「最大ズレ」を最小化する。
    deviation[name] = |actual - month_target[name]|
    max_deviation = max(deviation[name] for all name)
    を最小化する。これにより「特定の1人だけが大きく崩れる」ことを避け、
    まず全員±0を目指し、それが無理なら全員±1以内を目指す、という
    挙動になる。

  [第2段階] 第1段階の最小max_deviationに対し、休息ルール改善のために
    必要な場合のみ最大+1まで緩和を許した上で、休息ルール違反の総数を
    最小化する。
      月〜金Night → 翌日は原則完全OFF(Day/Nightとも不可)
      土曜Night → 日曜Dayは原則不可(日曜Nightは通常どおり許可)
      日曜Night → 月曜は原則完全OFF
      5日以上の連続Callは原則禁止
      3日以上連続Callが終了した直後は原則2日連続OFF
    +1の緩和が実際には不要な場合(緩和してもしなくても休息ルール違反数が
    変わらない場合)は、第3段階で自動的に緩和なし(元のmax_deviation)に
    引き戻される。

  [第3段階] 休息ルール違反数を第2段階の最小値に固定した上で、月別目標
    からの「総ズレ」(sum of |actual - month_target|)を最小化する。

  [第4段階] 総ズレを第3段階の最小値に固定した上で、既存の公平性指標を
    最適化する:
      A. 自院オンコールの年間実績を均等化
      C. 日中/夜間の担当回数の偏りを最小化 (個人ごと)
      E. 月内での勤務日の偏り(集中)を避ける
      F. 土日・祝日担当回数の偏りを最小化
      G. 外部バイトの回数を対象者間でなるべく均等にする
      H. 土曜・日曜のオンコールは同じ週末にまとめる(分断を避ける)

  各段階は前段の最小値を「これ以上悪化させない」制約として引き継ぐため、
  「月別目標を守るために休息ルールを1件でも余分に破る」「休息ルールの
  ために月別目標を無制限にずらす」といったことは構造的に起こり得ない。
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

# 休息ルール違反の内訳を表すカテゴリ名
VIOLATION_CATEGORIES = (
    "weekday_night_next_day",  # 月〜金Night → 翌日勤務
    "saturday_night_sunday_day",  # 土曜Night → 日曜Day
    "sunday_night_monday",  # 日曜Night → 月曜勤務
    "five_day_streak",  # 5日以上連続Call
    "streak_missing_two_days_off",  # 3日以上連続Call後、2連休が取れていない
)


@dataclass
class OptimizerWeights:
    """公平性(第2段階)ソフト制約の重み。値が大きいほどその条件を強く重視する。
    休息ルール(第1段階)は重みではなく、別途「違反件数の最小化」という
    独立した目的関数で扱うため、ここには含まれない。"""

    annual_actual_balance: int = 300  # 自院オンコールの年間実績均等化(最優先)
    weekend_pairing: int = 20  # 土日オンコールを同じ週末にまとめる
    day_night_balance: int = 15
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

    # --- 月次生成フロー向け(すべて任意。未指定時はMember.target_countを
    #     そのまま使う単月hard equalityにフォールバックし、従来動作と
    #     完全に後方互換になる) ---
    month_target: Optional[Dict[str, int]] = None  # 今月の月別目標(soft・最優先で維持)
    remaining_target: Optional[Dict[str, int]] = None  # final target - これまでの確定実績(hard上限)
    future_available_slots: Optional[Dict[str, int]] = None  # 来月以降の割当可能枠数(hard、先読み)
    max_deviation_relaxation: int = 1  # 休息ルール改善のために許す最大ズレの追加緩和幅

    max_time_seconds: float = 150.0
    # 4段階最適化(①最大ズレ最小化 → ②休息ルール違反最小化(+1まで緩和可)
    # → ③総ズレ最小化 → ④公平性最適化)にそれぞれ割り当てる最大計算時間。
    # 未指定の場合は max_time_seconds を 15%/15%/15%/55% の比率で配分する
    # (①②③は0になりやすく短時間で確定することが多いため、④公平性側に
    # 多くの時間を残す設計)。OPTIMALになれば各段階ともその時点で終了し、
    # 時間切れの場合はその時点のbest FEASIBLE解を採用する(CP-SATの標準動作)。
    max_dev_time_seconds: Optional[float] = None
    rest_rule_time_seconds: Optional[float] = None
    total_dev_time_seconds: Optional[float] = None
    fairness_time_seconds: Optional[float] = None

    # 通常solveがINFEASIBLEだった場合の部分勤務表フォールバック専用。
    # Trueのときだけ自院Day/Night枠に「未割当」を許可し、その数を最優先で最小化する。
    allow_unassigned: bool = False

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
        if self.month_target is None:
            self.month_target = {}
        if self.remaining_target is None:
            self.remaining_target = {}
        if self.future_available_slots is None:
            self.future_available_slots = {}
        if self.max_dev_time_seconds is None:
            self.max_dev_time_seconds = max(3.0, self.max_time_seconds * 0.15)
        if self.rest_rule_time_seconds is None:
            self.rest_rule_time_seconds = max(3.0, self.max_time_seconds * 0.15)
        if self.total_dev_time_seconds is None:
            self.total_dev_time_seconds = max(3.0, self.max_time_seconds * 0.15)
        if self.fairness_time_seconds is None:
            self.fairness_time_seconds = max(3.0, self.max_time_seconds * 0.55)


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

        # 月次生成フロー向けの3つの値。未指定の場合は target_count を使い、
        # 「今月だけでtarget_countに厳密一致」という従来のhard equalityと
        # 完全に等価な挙動にフォールバックする(remaining=target, future=0
        # なら total<=target かつ target-total<=0 で total==target と同義)。
        self.month_target: Dict[str, int] = {
            name: self.options.month_target.get(name, self.target_count[name])
            for name in self.member_names
        }
        self.remaining_target: Dict[str, int] = {
            name: self.options.remaining_target.get(name, self.target_count[name])
            for name in self.member_names
        }
        self.future_available_slots: Dict[str, int] = {
            name: self.options.future_available_slots.get(name, 0)
            for name in self.member_names
        }

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
        self.assigned_on_day: Dict[Tuple[date, str], cp_model.IntVar] = {}
        # 休息ルール違反変数: {member_name: {category: [IntVar, ...]}}
        self.violation_vars: Dict[str, Dict[str, List[cp_model.IntVar]]] = {
            name: {cat: [] for cat in VIOLATION_CATEGORIES} for name in self.member_names
        }
        self.fairness_terms: List[cp_model.LinearExpr] = []
        self.deviation_vars: Dict[str, cp_model.IntVar] = {}  # |actual - month_target| (メンバーごと)
        self.max_dev_var: Optional[cp_model.IntVar] = None  # 上記の最大値
        self.unassigned_vars: Dict[Tuple[date, Slot], cp_model.IntVar] = {}
        self.warnings: List[str] = []

    @staticmethod
    def _month_days(year: int, month: int) -> List[date]:
        n_days = calendar.monthrange(year, month)[1]
        return [date(year, month, d) for d in range(1, n_days + 1)]

    def _is_weekend_or_holiday(self, d: date) -> bool:
        return d.weekday() >= 5 or d in self.options.holidays

    def _weekend_pairs(self) -> List[Tuple[date, date]]:
        """
        月内で連続する(土曜, 日曜)のペアを返す。
        月をまたぐペアは扱えないため対象外とする。
        """
        day_set = set(self.days)
        pairs: List[Tuple[date, date]] = []
        for d in self.days:
            if d.weekday() == 5:  # 土曜
                nxt = d + timedelta(days=1)
                if nxt in day_set and nxt.weekday() == 6:
                    pairs.append((d, nxt))
        return pairs

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
        # 通常は必ず実在メンバー1人。部分勤務表フォールバック時だけ
        # 「未割当」変数を1つ追加し、どうしても埋められない枠を空欄で返せるようにする。
        for d in self.days:
            for slot in (Slot.DAY, Slot.NIGHT):
                assigned = sum(self.x[(d, slot, name)] for name in self.member_names)
                if self.options.allow_unassigned:
                    u = model.NewBoolVar(f"unassigned_{d.isoformat()}_{slot.value}")
                    self.unassigned_vars[(d, slot)] = u
                    model.Add(assigned + u == 1)
                else:
                    model.Add(assigned == 1)

        # --- 絶対条件③ 同日の日中・夜間を同じ人が担当しない ---
        for d in self.days:
            for name in self.member_names:
                model.Add(
                    self.x[(d, Slot.DAY, name)] + self.x[(d, Slot.NIGHT, name)] <= 1
                )

        # --- 外部病院バイト: 変数定義 ---
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

        # --- 絶対条件⑦ final target達成可能性の先読み(hard constraint) ---
        # optimizer側でfinal target自体を±1などで自動調整することは一切
        # 行わない。remaining_target/future_available_slotsを指定しない
        # 場合は target_count に厳密一致する従来動作と等価になる。
        self.total_calls: Dict[str, cp_model.LinearExpr] = {}
        for name in self.member_names:
            total = sum(
                self.x[(d, slot, name)] for d in self.days for slot in (Slot.DAY, Slot.NIGHT)
            )
            self.total_calls[name] = total
            R = self.remaining_target[name]
            F = self.future_available_slots[name]
            model.Add(total <= R)
            # 部分勤務表は未割当枠を人間が後から調整するための「下書き」。
            # その時点では未割当分の担当者が未確定なので、将来達成可能性の下限だけは
            # 一時的に適用しない。final targetそのものや上限は変更しない。
            if not self.options.allow_unassigned:
                model.Add(R - total <= F)

        # --- 「その日に(日中/夜間どちらかで)割り当てられているか」を表すBool変数 ---
        # (同日の日中・夜間は排他なので0/1で表現できる。休息ルールとソフトEの両方で使う)
        for d in self.days:
            for name in self.member_names:
                var = model.NewBoolVar(f"assigned_{d.isoformat()}_{name}")
                model.Add(var == self.x[(d, Slot.DAY, name)] + self.x[(d, Slot.NIGHT, name)])
                self.assigned_on_day[(d, name)] = var

        # ==================================================================
        # 月別目標からのズレ(第1段階・第3段階で最小化するsoft constraint)
        # ==================================================================
        self._build_deviation_terms()

        # ==================================================================
        # 休息ルール(第2段階で違反件数を最小化するsoft constraint)
        # ==================================================================
        self._build_rest_rule_violations()

        # ==================================================================
        # 公平性(第4段階で最適化するsoft constraint)
        # ==================================================================
        self._build_fairness_terms()

    def _build_deviation_terms(self) -> None:
        """各メンバーについて deviation[name] = |total_calls - month_target|
        を定義し、その最大値 max_dev_var も用意する。"""
        model = self.model
        upper = len(self.days) * 2 + 1
        for name in self.member_names:
            mt = self.month_target[name]
            diff = model.NewIntVar(-upper, upper, f"monthdiff_{name}")
            model.Add(diff == self.total_calls[name] - mt)
            dev = model.NewIntVar(0, upper, f"monthdev_{name}")
            model.AddAbsEquality(dev, diff)
            self.deviation_vars[name] = dev

        self.max_dev_var = model.NewIntVar(0, upper, "max_month_deviation")
        for name in self.member_names:
            model.Add(self.max_dev_var >= self.deviation_vars[name])

    def _add_and_violation(self, category: str, name: str, literals: List, threshold_offset: int) -> None:
        """literals(0/1変数、または (1 - var) の形の否定リストも可)の
        AND条件が成立するときに1以上になる違反変数を作り、記録する。
        「解が不必要にviolationを1にする」ことは目的関数の最小化により
        起こらないため、下限制約(v >= sum(literals) - threshold_offset)
        だけで十分に「AND条件が真のとき、かつそのときに限りv=1」を表現できる。
        """
        idx = len(self.violation_vars[name][category])
        v = self.model.NewIntVar(0, 1, f"viol_{category}_{name}_{idx}")
        self.model.Add(v >= sum(literals) - threshold_offset)
        self.violation_vars[name][category].append(v)

    def _build_rest_rule_violations(self) -> None:
        model = self.model

        # --- 月〜金Night → 翌日は原則完全OFF ---
        # --- 土曜Night → 日曜Dayは原則不可(日曜Nightは許可) ---
        # --- 日曜Night → 月曜は原則完全OFF ---
        for i in range(len(self.days) - 1):
            d_today = self.days[i]
            d_tomorrow = self.days[i + 1]
            weekday = d_today.weekday()  # 月=0 ... 日=6
            for name in self.member_names:
                night_today = self.x[(d_today, Slot.NIGHT, name)]
                if weekday <= 4:  # 月〜金
                    self._add_and_violation(
                        "weekday_night_next_day",
                        name,
                        [night_today, self.assigned_on_day[(d_tomorrow, name)]],
                        threshold_offset=1,
                    )
                elif weekday == 5:  # 土曜: 翌日Dayのみ違反(翌日Nightは許可)
                    day_tomorrow = self.x[(d_tomorrow, Slot.DAY, name)]
                    self._add_and_violation(
                        "saturday_night_sunday_day",
                        name,
                        [night_today, day_tomorrow],
                        threshold_offset=1,
                    )
                else:  # weekday == 6, 日曜
                    self._add_and_violation(
                        "sunday_night_monday",
                        name,
                        [night_today, self.assigned_on_day[(d_tomorrow, name)]],
                        threshold_offset=1,
                    )

        # --- 5日以上の連続Callは原則禁止 ---
        # 任意の連続5日間で、assigned_on_dayの合計が4を超えた分を違反とする。
        window = 5
        for name in self.member_names:
            for i in range(len(self.days) - window + 1):
                days_window = self.days[i : i + window]
                excess = model.NewIntVar(0, window, f"excess5_{name}_{i}")
                model.Add(
                    excess >= sum(self.assigned_on_day[(d, name)] for d in days_window) - (window - 1)
                )
                self.violation_vars[name]["five_day_streak"].append(excess)

        # --- 3日以上連続Callが終了した直後は原則2日連続OFF ---
        # d-2,d-1,d の3日連続Callで、d+1が(既に)OFFであれば、そこが
        # 連続勤務の切れ目にあたるため、続くd+2もOFFであるべき。破って
        # いれば違反1件とする(4日連続の場合も末尾3日分で同様に判定される)。
        for i in range(2, len(self.days) - 2):
            d_m2 = self.days[i - 2]
            d_m1 = self.days[i - 1]
            d = self.days[i]
            d_p1 = self.days[i + 1]
            d_p2 = self.days[i + 2]
            for name in self.member_names:
                literals = [
                    self.assigned_on_day[(d_m2, name)],
                    self.assigned_on_day[(d_m1, name)],
                    self.assigned_on_day[(d, name)],
                    1 - self.assigned_on_day[(d_p1, name)],
                    self.assigned_on_day[(d_p2, name)],
                ]
                self._add_and_violation(
                    "streak_missing_two_days_off", name, literals, threshold_offset=4
                )

    def _build_fairness_terms(self) -> None:
        model = self.model
        w = self.options.weights
        penalty_terms: List[cp_model.LinearExpr] = self.fairness_terms

        # --- A: 自院オンコールの年間実績を均等化(最優先) ---
        annual_prior = self.options.annual_actual_totals
        if annual_prior:
            max_upper = max(annual_prior.values(), default=0) + len(self.days) * 2 + 1
            combined_vars = {}
            for name in self.member_names:
                month_total = self.total_calls[name]
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

        # --- C: 日中/夜間の担当回数の偏りを最小化(個人ごと) ---
        for name in self.member_names:
            day_total = sum(self.x[(d, Slot.DAY, name)] for d in self.days)
            night_total = sum(self.x[(d, Slot.NIGHT, name)] for d in self.days)
            bdiff = model.NewIntVar(-len(self.days), len(self.days), f"bdiff_{name}")
            model.Add(bdiff == day_total - night_total)
            babs = model.NewIntVar(0, len(self.days), f"babs_{name}")
            model.AddAbsEquality(babs, bdiff)
            penalty_terms.append(babs * w.day_night_balance)

        # --- E: 勤務日の偏り(近接日への集中)を避ける ---
        cluster_window = 3
        for name in self.member_names:
            for i, d in enumerate(self.days):
                nearby_days = self.days[i + 1 : i + 1 + cluster_window]
                for nd in nearby_days:
                    both = model.NewBoolVar(f"cluster_{d.isoformat()}_{nd.isoformat()}_{name}")
                    model.AddMultiplicationEquality(
                        both, [self.assigned_on_day[(d, name)], self.assigned_on_day[(nd, name)]]
                    )
                    penalty_terms.append(both * w.spread_clustering)

        # --- H: 土曜・日曜のオンコールは同じ週末にまとめる(分断を避ける) ---
        weekend_pairs = self._weekend_pairs()
        for sat, sun in weekend_pairs:
            for name in self.member_names:
                a = self.assigned_on_day[(sat, name)]
                b = self.assigned_on_day[(sun, name)]
                split = model.NewBoolVar(f"weekend_split_{sat.isoformat()}_{name}")
                model.Add(split >= a - b)
                model.Add(split >= b - a)
                model.Add(split <= a + b)
                model.Add(split <= 2 - a - b)
                penalty_terms.append(split * w.weekend_pairing)

        # --- F: 土日・祝日の偏りを最小化 ---
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
                scaled_weight = max(1, w.weekend_holiday_balance // 20)
                penalty_terms.append(wabs * scaled_weight)

        # --- G: 外部バイトの回数を対象者間でなるべく均等にする ---
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

    def _all_violation_vars(self) -> List[cp_model.IntVar]:
        result = []
        for name in self.member_names:
            for cat in VIOLATION_CATEGORIES:
                result.extend(self.violation_vars[name][cat])
        return result

    # ------------------------------------------------------------------
    # INFEASIBLE時の診断(目標回数は一切変更せず、原因を報告するのみ)
    # ------------------------------------------------------------------
    def _diagnose_infeasibility(self) -> List[str]:
        reasons = self._diagnose_infeasibility_precheck()
        if not reasons:
            reasons.append(
                "総枠数・個人ごとの割当可能枠数には矛盾が見当たりませんが、"
                "目標回数・不都合日・同日日中夜間排他などの組み合わせにより"
                "全体として実行可能な割当が存在しません(詳細な原因特定には"
                "個別の目標回数・不都合日の組み合わせを見直してください)。"
                "final targetは自動調整していません。"
            )
        return reasons

    def _diagnose_infeasibility_precheck(self) -> List[str]:
        """モデルをsolveする前でも判定できる、構造的に確実な矛盾を検出する。

        各メンバーのtotal_callsは [max(0, R-F), R] の範囲に収まる必要がある
        (R=remaining_target, F=future_available_slots)。この範囲の下限・
        上限の合計が、今月の総枠数を挟んでいなければ構造的にINFEASIBLE
        (②の「各枠1人」制約から、sum(total_calls)は必ず総枠数と一致する
        ため)。remaining_target/future_available_slotsを指定しない場合は
        R=target_count, F=0 となり、従来の「sum(target)==総枠数」チェックと
        完全に等価になる。
        """
        reasons: List[str] = []
        total_slots = len(self.days) * 2
        lower_sum = 0
        upper_sum = 0
        for name in self.member_names:
            R = self.remaining_target[name]
            F = self.future_available_slots[name]
            if R < 0:
                reasons.append(
                    f"{name}: remaining_target({R})が負の値です。確定実績が"
                    "final targetを超えている可能性があります(final targetは"
                    "変更していません)。"
                )
                R = 0
            lower = max(0, R - F)
            upper = R
            lower_sum += lower
            upper_sum += upper

            available = sum(
                1
                for d in self.days
                for slot in (Slot.DAY, Slot.NIGHT)
                if not self._is_unavailable(name, d, slot)
            )
            # 「今月中に少なくともこれだけは消化しないと将来枠だけでは
            # final targetに届かない」という下限(lower)が、今月の物理的な
            # 割当可能枠数を超えていれば構造的にINFEASIBLE。
            # 上限(upper=R)は「今月使い切ってよい最大値」であって
            # 「今月中に必ず消化する量」ではないため、upperがavailableを
            # 超えていること自体は問題ではない(単に今月はavailableの
            # 範囲内までしか使われず、残りは来月以降に回るだけ)。
            if lower > available:
                reasons.append(
                    f"{name}: 今月中に最低でも{lower}回消化しないとfinal target達成が"
                    f"将来的に不可能になりますが、今月の不都合日を除いた割当可能枠数は"
                    f"{available}回しかありません。final targetは変更していません。"
                )

        if not (lower_sum <= total_slots <= upper_sum):
            reasons.append(
                f"今月の総Call枠数({total_slots} = {len(self.days)}日×2枠)が、"
                f"全員のremaining_target許容範囲の合計(下限{lower_sum}〜上限{upper_sum})"
                "の外にあります。final targetは変更していません。"
            )

        for d in self.days:
            for slot in (Slot.DAY, Slot.NIGHT):
                available_members = [
                    name for name in self.member_names if not self._is_unavailable(name, d, slot)
                ]
                if not available_members:
                    reasons.append(
                        f"{d.isoformat()} {slot.label_ja}: 割当可能なメンバーが1人もいません。"
                    )
        return reasons

    # ------------------------------------------------------------------
    # 求解: 4段階の辞書式(lexicographic)最適化
    #   ①最大ズレ最小化 → ②休息ルール違反最小化(+1まで緩和可)
    #   → ③総ズレ最小化 → ④公平性最適化
    # ------------------------------------------------------------------
    def _empty_stats(self) -> Dict[str, Dict[str, int]]:
        return {
            name: {
                "day": 0, "night": 0, "total": 0,
                "target": self.remaining_target.get(name, self.target_count.get(name, 0)),
                "diff": 0, "gaikobu": 0, "grand_total": 0, "weekend": 0,
                "weekday_night_violation": 0,
                "sunday_night_monday_violation": 0,
                "consecutive_rule_violation": 0,
                "month_target": self.month_target.get(name, 0),
                "deviation": 0,
                "max_deviation": 0,
            }
            for name in self.member_names
        }

    def _infeasible_result(self, reasons_header: str, reasons: List[str]) -> ScheduleResult:
        self.warnings.append(reasons_header)
        self.warnings.extend(reasons)
        return ScheduleResult(
            year=self.year, month=self.month, entries=[], status="INFEASIBLE",
            stats=self._empty_stats(), warnings=self.warnings,
        )

    @staticmethod
    def _add_solution_hint(model: cp_model.CpModel, solver: cp_model.CpSolver, variables) -> None:
        """前段階の解を次段階solveのヒントとして与え、再探索を高速化する
        (制約は前段より厳しくなるだけなので、前段の解は常にヒントとして有効)。"""
        try:
            model.ClearHints()
        except AttributeError:
            pass
        for v in variables:
            model.AddHint(v, solver.Value(v))

    def solve(self) -> ScheduleResult:
        self.build()

        # --- 事前診断: 構造的に確実なINFEASIBLEは、solverを回すまでもなく
        #     理由を明示して返す(final targetは変更しない) ---
        pre_reasons = [] if self.options.allow_unassigned else self._diagnose_infeasibility_precheck()
        if pre_reasons:
            return self._infeasible_result(
                "最適化に失敗しました(INFEASIBLE)。final targetは変更していません。", pre_reasons
            )

        all_violations = self._all_violation_vars()
        dev_vars = list(self.deviation_vars.values())
        x_vars = list(self.x.values())

        # 部分勤務表フォールバックでは、何より先に未割当枠数を最小化する。
        # 最小値を固定してから通常の月別目標・休息・公平性最適化へ進む。
        if self.options.allow_unassigned and self.unassigned_vars:
            self.model.Minimize(sum(self.unassigned_vars.values()))
            solver0 = cp_model.CpSolver()
            solver0.parameters.max_time_in_seconds = self.options.max_dev_time_seconds
            solver0.parameters.num_search_workers = 8
            status0 = solver0.Solve(self.model)
            if status0 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return self._infeasible_result(
                    "部分勤務表も作成できませんでした(INFEASIBLE)。",
                    self._diagnose_infeasibility(),
                )
            min_unassigned = sum(solver0.Value(v) for v in self.unassigned_vars.values())
            self.model.Add(sum(self.unassigned_vars.values()) <= min_unassigned)
            self._add_solution_hint(self.model, solver0, x_vars)

        # ================================================================
        # 第1段階: 月別目標からの「最大ズレ」を最小化
        # ================================================================
        self.model.Minimize(self.max_dev_var)
        solver1 = cp_model.CpSolver()
        solver1.parameters.max_time_in_seconds = self.options.max_dev_time_seconds
        solver1.parameters.num_search_workers = 8
        status1 = solver1.Solve(self.model)

        if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return self._infeasible_result(
                "最適化に失敗しました(INFEASIBLE)。final targetは変更していません。",
                self._diagnose_infeasibility(),
            )

        min_max_dev = solver1.Value(self.max_dev_var)
        self._add_solution_hint(self.model, solver1, x_vars)

        # ================================================================
        # 第2段階: 最大ズレを (min_max_dev + relaxation) まで緩和可能とした上で、
        #          休息ルール違反件数を最小化
        # ================================================================
        relaxation = max(0, int(self.options.max_deviation_relaxation))
        relaxed_bound = min_max_dev + relaxation
        self.model.Add(self.max_dev_var <= relaxed_bound)

        stage2_objective = sum(all_violations) if all_violations else 0
        self.model.Minimize(stage2_objective)
        solver2 = cp_model.CpSolver()
        solver2.parameters.max_time_in_seconds = self.options.rest_rule_time_seconds
        solver2.parameters.num_search_workers = 8
        status2 = solver2.Solve(self.model)

        if status2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # 第1段階の解にフォールバック(理論上は起こらないはずだが念のため)
            self.warnings.append(
                "第2段階(休息ルール最小化)の求解に失敗したため、第1段階の解を採用しました。"
            )
            return self._extract_result(solver1, solver1.StatusName(status1))

        min_violation_count = sum(solver2.Value(v) for v in all_violations) if all_violations else 0
        self._add_solution_hint(self.model, solver2, x_vars)

        # ================================================================
        # 第3段階: 休息ルール違反数を第2段階の最小値以下に固定した上で、
        #          月別目標からの「総ズレ」を最小化
        #          (これにより、第2段階で不必要に使われたmax_deviationの
        #          緩和は、休息ルールの改善に寄与しない限り自動的に
        #          元の最小値へ引き戻される)
        # ================================================================
        if all_violations:
            self.model.Add(sum(all_violations) <= min_violation_count)

        stage3_objective = sum(dev_vars) if dev_vars else 0
        self.model.Minimize(stage3_objective)
        solver3 = cp_model.CpSolver()
        solver3.parameters.max_time_in_seconds = self.options.total_dev_time_seconds
        solver3.parameters.num_search_workers = 8
        status3 = solver3.Solve(self.model)

        if status3 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.warnings.append(
                "第3段階(総ズレ最小化)の求解に失敗したため、第2段階の解を採用しました。"
            )
            return self._extract_result(solver2, solver2.StatusName(status2))

        min_total_dev = sum(solver3.Value(v) for v in dev_vars) if dev_vars else 0
        self._add_solution_hint(self.model, solver3, x_vars)

        # ================================================================
        # 第4段階: 総ズレを第3段階の最小値以下に固定した上で、公平性を最適化
        # ================================================================
        if dev_vars:
            self.model.Add(sum(dev_vars) <= min_total_dev)

        fairness_objective = sum(self.fairness_terms) if self.fairness_terms else 0
        self.model.Minimize(fairness_objective)
        solver4 = cp_model.CpSolver()
        solver4.parameters.max_time_in_seconds = self.options.fairness_time_seconds
        solver4.parameters.num_search_workers = 8
        status4 = solver4.Solve(self.model)

        if status4 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            solver, status_name = solver4, solver4.StatusName(status4)
        else:
            self.warnings.append(
                "第4段階(公平性最適化)の求解に失敗したため、第3段階の解を採用しました。"
            )
            solver, status_name = solver3, solver3.StatusName(status3)

        return self._extract_result(solver, status_name)

    def _extract_result(self, solver: cp_model.CpSolver, status_name: str) -> ScheduleResult:
        entries: List[ScheduleEntry] = []
        stats: Dict[str, Dict[str, int]] = self._empty_stats()

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
                    if self._is_weekend_or_holiday(d):
                        stats[assigned_name]["weekend"] += 1

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

        max_dev_value = solver.Value(self.max_dev_var) if self.max_dev_var is not None else 0

        for name in self.member_names:
            stats[name]["diff"] = stats[name]["total"] - stats[name]["target"]
            stats[name]["grand_total"] = stats[name]["total"] + stats[name]["gaikobu"]
            stats[name]["deviation"] = stats[name]["total"] - stats[name]["month_target"]
            stats[name]["max_deviation"] = max_dev_value

            weekday_night = sum(
                solver.Value(v) for v in self.violation_vars[name]["weekday_night_next_day"]
            ) + sum(
                solver.Value(v) for v in self.violation_vars[name]["saturday_night_sunday_day"]
            )
            sunday_monday = sum(
                solver.Value(v) for v in self.violation_vars[name]["sunday_night_monday"]
            )
            consecutive = sum(
                solver.Value(v) for v in self.violation_vars[name]["five_day_streak"]
            ) + sum(
                solver.Value(v) for v in self.violation_vars[name]["streak_missing_two_days_off"]
            )
            stats[name]["weekday_night_violation"] = weekday_night
            stats[name]["sunday_night_monday_violation"] = sunday_monday
            stats[name]["consecutive_rule_violation"] = consecutive

        if self.options.allow_unassigned:
            missing = [
                (d, slot) for (d, slot), v in self.unassigned_vars.items()
                if solver.Value(v) == 1
            ]
            if missing:
                self.warnings.insert(0,
                    f"勤務可能者不足のため {len(missing)} 枠を未割当のまま作成しました。メンバー間で調整してください。"
                )
                for d, slot in missing:
                    self.warnings.append(
                        f"{d.isoformat()} {slot.label_ja}: 未割当（勤務可能者不足・要調整）"
                    )

        return ScheduleResult(
            year=self.year, month=self.month, entries=entries, status=status_name,
            stats=stats, warnings=self.warnings,
        )


def verify_schedule_result(result: ScheduleResult) -> Tuple[bool, str]:
    """生成後の検証レポートを返す。各メンバーについて
      month_target(月別目標) / actual(total) / deviation(月別目標からのズレ) /
      day / night / weekend / weekday-night-next-day violation /
      sunday-night-monday violation / consecutive-call violation /
      max_deviation(全員共通の最大ズレ) / solver status
    を確認できる。

    月別目標は絶対条件⑦(final target先読み)の範囲内で調整され得る
    soft constraintであるため、deviation != 0 自体は失敗ではない
    (むしろ休息ルール改善や将来の割当可能枠数の都合で意図的に生じ得る)。
    ここでの success 判定は「solverがFEASIBLE/OPTIMALな解を返せたか」
    のみを基準にする。deviationが大きい場合は別途「月別目標から変更された
    メンバー」として一覧表示する。
    """
    if result.status not in ("OPTIMAL", "FEASIBLE"):
        return False, f"status={result.status} のため検証不可(INFEASIBLE等)"

    lines = [f"solver status = {result.status}", ""]
    lines.append(
        f"{'name':<10}{'month_tgt':>10}{'actual':>8}{'deviation':>10}{'day':>6}{'night':>6}"
        f"{'weekend':>9}{'wd_night':>10}{'su_mon':>8}{'consec':>8}"
    )
    changed: List[Tuple[str, int]] = []
    for name, s in result.stats.items():
        if s["deviation"] != 0:
            changed.append((name, s["deviation"]))
        lines.append(
            f"{name:<10}{s['month_target']:>10}{s['total']:>8}{s['deviation']:>+10}{s['day']:>6}{s['night']:>6}"
            f"{s['weekend']:>9}{s['weekday_night_violation']:>10}"
            f"{s['sunday_night_monday_violation']:>8}{s['consecutive_rule_violation']:>8}"
        )

    max_dev = next(iter(result.stats.values()))["max_deviation"] if result.stats else 0
    lines.append(f"\nmax_deviation(全員の最大ズレ) = {max_dev}")

    if changed:
        lines.append("\n月別目標から変更されたメンバー(安全網が発動):")
        for name, dev in changed:
            lines.append(f"  {name}: {dev:+d}({'超過' if dev > 0 else '不足'})")
    else:
        lines.append("\n月別目標からの変更: なし(全員 exact 達成)")

    return True, "\n".join(lines)
