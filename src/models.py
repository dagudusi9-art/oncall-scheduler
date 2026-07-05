# -*- coding: utf-8 -*-
"""
データモデル定義

このモジュールでは、オンコール自動割当システムで使用する
基本的なデータ構造(dataclass)を定義する。

- Member          : 医師1名の情報(名前, 目標オンコール回数)
- Unavailability   : 1名・1日分の不都合情報(日中不可/夜間不可)
- ScheduleEntry    : 割当結果1日分(日中担当/夜間担当)
- Slot             : 日中/夜間を表すEnum
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional


class Slot(str, Enum):
    """1日の勤務枠(日中 / 夜間)"""

    DAY = "day"
    NIGHT = "night"

    @property
    def label_ja(self) -> str:
        return {"day": "日中", "night": "夜間"}[self.value]


@dataclass
class Member:
    """オンコールに参加する医師1名の情報"""

    name: str
    target_count: int = 0  # 今月の目標オンコール回数(合計。日中+夜間。自院分のみ)
    gaikobu_eligible: bool = False  # 外部病院バイトの対象者かどうか

    def __post_init__(self) -> None:
        if self.target_count < 0:
            raise ValueError(f"target_count は0以上である必要があります: {self.name}")


@dataclass
class Unavailability:
    """
    ある医師の、ある1日における不都合情報。

    day_unavailable   : True なら日中オンコール不可
    night_unavailable : True なら夜間オンコール不可
    (両方Trueなら終日不可)
    """

    member_name: str
    day: date
    day_unavailable: bool = False
    night_unavailable: bool = False

    def is_unavailable(self, slot: Slot) -> bool:
        return self.day_unavailable if slot == Slot.DAY else self.night_unavailable


@dataclass
class ScheduleEntry:
    """割当結果1日分"""

    day: date
    assignments: Dict[Slot, Optional[str]] = field(
        default_factory=lambda: {Slot.DAY: None, Slot.NIGHT: None}
    )
    gaikobu: Optional[str] = None  # その日の外部病院バイト担当者(対象日でなければNone)


@dataclass
class ScheduleResult:
    """最適化結果全体"""

    year: int
    month: int
    entries: List[ScheduleEntry]
    # solverの状態: "OPTIMAL" / "FEASIBLE" / "INFEASIBLE" など
    status: str
    # 各メンバーの統計 {名前: {"day": n, "night": n, "total": n, "target": n, "diff": n,
    #                          "gaikobu": n, "grand_total": n}}
    # total = 自院オンコール合計(day+night), gaikobu = 外部バイト回数,
    # grand_total = total + gaikobu(総勤務)
    stats: Dict[str, Dict[str, int]]
    # 割当できなかった枠がある場合の理由メッセージ一覧
    warnings: List[str] = field(default_factory=list)
