# -*- coding: utf-8 -*-
"""
Webアプリ共通のUIヘルパー

カレンダーの週表示(month_weeks)はすべて日曜始まりで統一している。
"""
from __future__ import annotations

import calendar
import hashlib
from datetime import date
from typing import List, Optional

# カレンダーグリッドのヘッダー表示用(日曜始まり)。
# WEEKDAY_JA[0]が週の最初の列(日曜)に対応する。
# 注意: これは表示順のリストであり、Python の date.weekday()(月曜=0)を
# そのままインデックスとして使うものではない。日付ごとの曜日名を
# python の weekday() から直接引きたい場合は WEEKDAY_JA_BY_PYTHON_INDEX を使うこと
# (Excel/PDF出力の日付一覧など、グリッド表示ではない箇所で使用)。
WEEKDAY_JA = ["日", "月", "火", "水", "木", "金", "土"]

# Excel/PDF出力など、date.weekday()(月曜=0,...,日曜=6)の値でそのまま
# インデックスして曜日名を得るための、月曜始まり順のリスト。
WEEKDAY_JA_BY_PYTHON_INDEX = ["月", "火", "水", "木", "金", "土", "日"]

# メンバー割当色のパレット(見やすい落ち着いた色合いを採用)
MEMBER_PALETTE = [
    "#A8D8EA", "#F6C7B6", "#C9E4C5", "#F7D488", "#D8BFD8",
    "#B0C4DE", "#FFB6A3", "#B5EAD7", "#FFDAC1", "#C7CEEA",
]


def month_weeks(year: int, month: int) -> List[List[Optional[date]]]:
    """
    指定された年月について、週ごとのdateリストを返す(日曜始まり)。
    月内に存在しない日は None で埋める。
    """
    cal = calendar.Calendar(firstweekday=calendar.SUNDAY)  # 6=日曜
    weeks: List[List[Optional[date]]] = []
    current_week: List[Optional[date]] = []

    for d in cal.itermonthdates(year, month):
        if d.month != month:
            current_week.append(None)
        else:
            current_week.append(d)
        if len(current_week) == 7:
            weeks.append(current_week)
            current_week = []

    # 月の最終週が7日未満で終わっている場合の保険(通常は発生しない)
    if current_week:
        while len(current_week) < 7:
            current_week.append(None)
        weeks.append(current_week)

    return weeks


def member_color(name: str) -> str:
    """メンバー名から一貫した色を割り当てる(名前が変わらない限り常に同じ色)"""
    idx = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % len(MEMBER_PALETTE)
    return MEMBER_PALETTE[idx]


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5
