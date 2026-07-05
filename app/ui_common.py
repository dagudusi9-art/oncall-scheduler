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

import streamlit as st

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


def render_schedule_calendar(entries: list, year: int, month: int) -> None:
    """
    勤務表(予定/実績どちらでも可)をGoogleカレンダー風に表示する、閲覧専用の共通コンポーネント。

    entries: [{"date": "YYYY-MM-DD", "day": 名前 or None, "night": 名前 or None, "gaikobu": 名前 or None}, ...]

    表示は st.markdown による静的HTMLのみで構成されているため、クリックや編集は一切できない
    (ボタン等のインタラクティブ要素を含まない)。管理者画面・メンバー画面のどちらから呼んでも
    見た目・配色が完全に一致するよう、この関数だけを両画面で共有すること
    (画面ごとに描画コードをコピーしないこと。修正はここ1か所で済む)。
    """
    by_day = {e["date"]: e for e in entries}
    weeks = month_weeks(year, month)

    header_cols = st.columns(7)
    for col, wd in zip(header_cols, WEEKDAY_JA):
        col.markdown(f"<div style='text-align:center;font-weight:bold'>{wd}</div>", unsafe_allow_html=True)

    for week in weeks:
        row_cols = st.columns(7)
        for col, d in zip(row_cols, week):
            with col:
                if d is None:
                    st.write("")
                    continue
                e = by_day.get(d.isoformat(), {})
                day_name = e.get("day") or "-"
                night_name = e.get("night") or "-"
                gaikobu_name = e.get("gaikobu")
                day_color = member_color(day_name) if day_name != "-" else "#EEEEEE"
                night_color = member_color(night_name) if night_name != "-" else "#EEEEEE"
                weekend_marker = " (土日)" if is_weekend(d) else ""
                gaikobu_html = ""
                if gaikobu_name:
                    gaikobu_color = member_color(gaikobu_name)
                    gaikobu_html = (
                        f"<div style='background-color:{gaikobu_color};border-radius:4px;padding:2px;"
                        f"font-size:0.8em;text-align:center'>🚑{gaikobu_name}</div>"
                    )
                st.markdown(
                    f"""
                    <div style='border:1px solid #ddd;border-radius:6px;padding:4px;margin-bottom:4px;'>
                      <div style='font-size:0.75em;color:#555'>{d.day}{weekend_marker}</div>
                      <div style='background-color:{day_color};border-radius:4px;padding:2px;margin:2px 0;font-size:0.8em;text-align:center'>☀️{day_name}</div>
                      <div style='background-color:{night_color};border-radius:4px;padding:2px;font-size:0.8em;text-align:center'>🌙{night_name}</div>
                      {gaikobu_html}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
