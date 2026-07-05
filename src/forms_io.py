# -*- coding: utf-8 -*-
"""
Googleフォーム回答の変換モジュール

推奨するフォーム設計:

  質問1: 氏名                         (プルダウン、メンバー一覧から選択)
  質問2: 日中に不都合な日を選択してください  (チェックボックス、月内の全日付)
  質問3: 夜間に不都合な日を選択してください  (チェックボックス、月内の全日付)

この形式でGoogleフォームを作ると、回答スプレッドシートは次のような
「ワイド形式」になる。

  タイムスタンプ | 氏名 | 日中に不都合な日を選択してください | 夜間に不都合な日を選択してください
  2026/7/20 10:00 | 大谷 | 8/1, 8/3                        | 8/2

本モジュールは、この回答シートの生データ(gspreadのget_all_records()の
戻り値)を受け取り、モデル・最適化エンジンが扱う「ロング形式」
(1人×1日×1行)の Unavailability リストへ変換する。

なお、同じ人が複数回フォームに回答した場合は「最新の回答のみ有効」とし、
古い回答は上書きされる(タイムスタンプ列で判定)。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Dict, List, Optional

from .models import Unavailability

_DATE_TOKEN_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")


def _parse_date_tokens(text: str, year: int) -> List[date]:
    """'8/1, 8/3' のような文字列から date のリストを作る"""
    if not text:
        return []
    result = []
    for m in _DATE_TOKEN_RE.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        try:
            result.append(date(year, month, day))
        except ValueError:
            continue  # 不正な日付は無視
    return result


def convert_form_responses(
    records: List[dict],
    year: int,
    name_column: str = "氏名",
    day_unavailable_column: str = "日中に不都合な日を選択してください",
    night_unavailable_column: str = "夜間に不都合な日を選択してください",
    timestamp_column: str = "タイムスタンプ",
) -> List[Unavailability]:
    """
    Googleフォームのワイド形式回答(get_all_records()の戻り値)を
    ロング形式のUnavailabilityリストに変換する。

    同一メンバーから複数回答があった場合は、timestamp_column が
    最も新しい回答のみを採用する。
    """
    # まず「メンバー名 -> 最新の回答行」に絞り込む
    latest_by_member: Dict[str, dict] = {}
    latest_ts_by_member: Dict[str, str] = {}

    for row in records:
        name = str(row.get(name_column, "")).strip()
        if not name:
            continue
        ts = str(row.get(timestamp_column, ""))
        if name not in latest_by_member or ts >= latest_ts_by_member.get(name, ""):
            latest_by_member[name] = row
            latest_ts_by_member[name] = ts

    # 全ての不都合日を(member, date) -> {day_unavail, night_unavail} に集約
    combined: Dict[tuple, Dict[str, bool]] = {}
    for name, row in latest_by_member.items():
        day_unavail_dates = _parse_date_tokens(str(row.get(day_unavailable_column, "")), year)
        night_unavail_dates = _parse_date_tokens(str(row.get(night_unavailable_column, "")), year)

        for d in day_unavail_dates:
            combined.setdefault((name, d), {"day": False, "night": False})["day"] = True
        for d in night_unavail_dates:
            combined.setdefault((name, d), {"day": False, "night": False})["night"] = True

    results = [
        Unavailability(
            member_name=name,
            day=d,
            day_unavailable=flags["day"],
            night_unavailable=flags["night"],
        )
        for (name, d), flags in combined.items()
    ]
    return results
