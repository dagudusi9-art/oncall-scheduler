# -*- coding: utf-8 -*-
"""
設定ファイル(YAML)の読み込み

config/config.yaml に以下の形式で年月・メンバー・目標回数を記述する。

year: 2026
month: 8
members:
  - name: 大谷
    target_count: 4
  - name: 中島
    target_count: 8
  ...

将来的にはこの設定もGoogleスプレッドシート(管理者用シート)から
読み込むように差し替え可能。sheets_io.load_config_from_sheet() を参照。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import yaml

from .models import Member


class AppConfig:
    def __init__(self, year: int, month: int, members: List[Member]):
        self.year = year
        self.month = month
        self.members = members

    def member_names(self) -> List[str]:
        return [m.name for m in self.members]


def load_config_from_yaml(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"設定ファイルが空です: {path}")

    year = int(raw["year"])
    month = int(raw["month"])
    if not (1 <= month <= 12):
        raise ValueError(f"month は1〜12である必要があります: {month}")

    members_raw = raw.get("members", [])
    if not members_raw:
        raise ValueError("members が設定されていません")

    members = [
        Member(name=str(m["name"]), target_count=int(m.get("target_count", 0)))
        for m in members_raw
    ]

    # 重複名チェック
    names = [m.name for m in members]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ValueError(f"メンバー名が重複しています: {dup}")

    return AppConfig(year=year, month=month, members=members)
