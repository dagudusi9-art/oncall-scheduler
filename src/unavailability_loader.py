# -*- coding: utf-8 -*-
"""
不都合日データの読み込み

CSV / Googleスプレッドシート(Googleフォームの回答シート)いずれの場合も、
最終的には以下の「ロング形式」のテーブルに変換してから使用する。

    member_name, date(YYYY-MM-DD), day_unavailable(0/1), night_unavailable(0/1)

Googleフォームの回答は通常「ワイド形式」(1行1回答、日付ごとに列がある、
または「不都合な日」を複数選択チェックボックスで回答する形式など)になる
ことが多いため、フォームの設計に応じてここで変換する。

本実装では2種類のフォーマットに対応する:

1. ロング形式CSV (推奨。手動編集・テスト用)
   member_name,date,day_unavailable,night_unavailable
   大谷,2026-08-01,1,0
   大谷,2026-08-02,0,1

2. Googleフォームの典型的な回答形式(ワイド形式)
   タイムスタンプ, 氏名, 日中に不可な日(複数選択), 夜間に不可な日(複数選択)
   2026/07/20 10:00:00, 大谷, "8/1, 8/3", "8/2"

   → forms_io.py 側でロング形式に変換してから本モジュールへ渡す想定。
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import List

from .models import Unavailability


def _parse_bool(value: str) -> bool:
    return str(value).strip() in ("1", "true", "True", "TRUE", "○", "×") and str(
        value
    ).strip() not in ("0", "false", "False", "FALSE", "")


def load_unavailability_from_csv(path: str | Path) -> List[Unavailability]:
    """
    ロング形式CSVから不都合日情報を読み込む。

    列: member_name, date, day_unavailable, night_unavailable
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"不都合日CSVが見つかりません: {path}")

    results: List[Unavailability] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required_cols = {"member_name", "date", "day_unavailable", "night_unavailable"}
        if reader.fieldnames is None or not required_cols.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"CSVの列名が不正です。必要な列: {required_cols} / 実際: {reader.fieldnames}"
            )

        for row_num, row in enumerate(reader, start=2):
            try:
                d = datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
            except ValueError as e:
                raise ValueError(f"{row_num}行目: 日付の形式が不正です ({row['date']})") from e

            results.append(
                Unavailability(
                    member_name=row["member_name"].strip(),
                    day=d,
                    day_unavailable=_parse_bool(row["day_unavailable"]),
                    night_unavailable=_parse_bool(row["night_unavailable"]),
                )
            )
    return results


def load_unavailability_from_records(records: List[dict]) -> List[Unavailability]:
    """
    辞書のリスト(gspreadのget_all_records()の戻り値など)から
    ロング形式で不都合日情報を読み込む。sheets_io.py から使用する。
    """
    results: List[Unavailability] = []
    for row_num, row in enumerate(records, start=2):
        raw_date = row.get("date") or row.get("日付")
        member_name = row.get("member_name") or row.get("氏名") or row.get("名前")
        if not raw_date or not member_name:
            continue  # 空行はスキップ

        if isinstance(raw_date, date):
            d = raw_date
        else:
            raw_date = str(raw_date).strip()
            d = None
            for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                try:
                    d = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            if d is None:
                raise ValueError(f"{row_num}行目: 日付の形式が不正です ({raw_date})")

        day_unavail = row.get("day_unavailable", row.get("日中不可", 0))
        night_unavail = row.get("night_unavailable", row.get("夜間不可", 0))

        results.append(
            Unavailability(
                member_name=str(member_name).strip(),
                day=d,
                day_unavailable=_parse_bool(day_unavail),
                night_unavailable=_parse_bool(night_unavail),
            )
        )
    return results
