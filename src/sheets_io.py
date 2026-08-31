# -*- coding: utf-8 -*-
"""
Google Sheets連携モジュール

gspread + サービスアカウントを使い、以下を行う。

  1. 「不都合日入力シート」(Googleフォームの回答が飛ぶシート、または
     メンバーが直接入力するシート)からロング形式データを読み込む
  2. 「管理者設定シート」から年月・メンバー・目標回数を読み込む(任意)
  3. 最適化結果(勤務表・集計表)を「勤務表出力シート」へ書き込む

セットアップ方法は README.md の「Google連携のセットアップ」を参照。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:  # pragma: no cover
    gspread = None
    Credentials = None

from .config_loader import AppConfig
from .models import Member, ScheduleResult, Slot, Unavailability
from .unavailability_loader import load_unavailability_from_records

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


class SheetsClient:
    """Google Sheets との読み書きをまとめて扱うクラス

    認証情報は次のいずれかの方法で渡す(両方渡された場合は credentials_info を優先する):

      - credentials_path : ローカルのサービスアカウントJSONファイルへのパス
                            (ローカル実行・CLI実行時に使用)
      - credentials_info : サービスアカウントJSONの内容を辞書化したもの
                            (Streamlit Community Cloud の st.secrets 経由で使用)
    """

    def __init__(
        self,
        credentials_path: Optional[str | Path] = None,
        spreadsheet_key: str = "",
        credentials_info: Optional[dict] = None,
    ):
        if gspread is None:
            raise ImportError(
                "gspread / google-auth がインストールされていません。"
                "`pip install gspread google-auth` を実行してください。"
            )
        if not spreadsheet_key:
            raise ValueError("spreadsheet_key が指定されていません。")
        if credentials_info is not None:
            creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
        elif credentials_path is not None:
            creds = Credentials.from_service_account_file(str(credentials_path), scopes=SCOPES)
        else:
            raise ValueError("credentials_path または credentials_info のいずれかが必要です。")
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_key)

    # ------------------------------------------------------------------
    # 読み込み
    # ------------------------------------------------------------------
    def load_unavailability(self, worksheet_name: str = "不都合日入力") -> List[Unavailability]:
        """
        不都合日シートを読み込む。

        期待する列(ヘッダー行): member_name, date, day_unavailable, night_unavailable
        (Googleフォームの回答シートを直接使う場合は forms_io.py で
        このロング形式に変換してから同シートに書き出す運用を推奨)
        """
        ws = self.sh.worksheet(worksheet_name)
        records = ws.get_all_records()
        return load_unavailability_from_records(records)

    def load_config(self, worksheet_name: str = "管理者設定") -> AppConfig:
        """
        管理者設定シートを読み込む。

        期待するレイアウト:
            A1: year   B1: <西暦>
            A2: month  B2: <月>
            A4: name   B4: target_count   (ヘッダー行)
            A5: 大谷    B5: 4
            A6: 中島    B6: 8
            ...
        """
        ws = self.sh.worksheet(worksheet_name)
        values = ws.get_all_values()
        year = int(values[0][1])
        month = int(values[1][1])

        members: List[Member] = []
        header_row_idx = None
        for i, row in enumerate(values):
            if row and row[0].strip().lower() in ("name", "名前"):
                header_row_idx = i
                break
        if header_row_idx is None:
            raise ValueError("管理者設定シートにメンバー一覧のヘッダー行(name/名前)が見つかりません")

        for row in values[header_row_idx + 1 :]:
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            target = int(row[1]) if len(row) > 1 and row[1].strip() else 0
            members.append(Member(name=name, target_count=target))

        return AppConfig(year=year, month=month, members=members)

    # ------------------------------------------------------------------
    # 書き込み
    # ------------------------------------------------------------------
    def write_schedule(
        self,
        result: ScheduleResult,
        schedule_sheet_name: str = "勤務表",
        stats_sheet_name: str = "集計表",
    ) -> None:
        self._write_schedule_sheet(result, schedule_sheet_name)
        self._write_stats_sheet(result, stats_sheet_name)

    def write_unavailability(
        self,
        unavailabilities: List[Unavailability],
        worksheet_name: str = "不都合日入力",
    ) -> None:
        """
        Webアプリの不都合日データ(ロング形式)をスプレッドシートへ書き込む。
        渡された unavailabilities がその時点の全メンバー分のデータであることを
        前提に、シート全体を上書きする(管理者による一括保存用)。

        複数人が同時に入力している状況で1人分だけを保存したい場合は
        write_unavailability_for_member() を使うこと。
        """
        ws = self._get_or_create_worksheet(worksheet_name)
        ws.clear()
        rows = [["member_name", "date", "day_unavailable", "night_unavailable"]]
        for u in sorted(unavailabilities, key=lambda x: (x.day, x.member_name)):
            rows.append(
                [u.member_name, u.day.isoformat(), int(u.day_unavailable), int(u.night_unavailable)]
            )
        ws.update(values=rows, range_name="A1")

    def write_unavailability_for_member(
        self,
        member_name: str,
        unavailabilities: List[Unavailability],
        worksheet_name: str = "不都合日入力",
    ) -> None:
        """
        1名分の不都合日データだけをスプレッドシートに反映する。

        シート上の他メンバーの行はそのまま残し、member_name の行だけを
        削除してから渡された unavailabilities で置き換える。
        複数の医師が同時期にそれぞれ自分の入力を保存する運用でも、
        互いのデータを消してしまわないようにするための書き込み方法。
        """
        ws = self._get_or_create_worksheet(worksheet_name)
        existing_records = ws.get_all_records()

        other_rows = [
            [
                r.get("member_name", ""),
                str(r.get("date", "")),
                int(bool(r.get("day_unavailable", 0))),
                int(bool(r.get("night_unavailable", 0))),
            ]
            for r in existing_records
            if str(r.get("member_name", "")) != member_name
        ]

        own_rows = [
            [member_name, u.day.isoformat(), int(u.day_unavailable), int(u.night_unavailable)]
            for u in sorted(unavailabilities, key=lambda x: x.day)
        ]

        rows = [["member_name", "date", "day_unavailable", "night_unavailable"]]
        rows.extend(sorted(other_rows + own_rows, key=lambda r: (r[1], r[0])))

        ws.clear()
        ws.update(values=rows, range_name="A1")

    def load_unavailability_for_member(
        self,
        member_name: str,
        worksheet_name: str = "不都合日入力",
    ) -> List[Unavailability]:
        """指定した1名分の不都合日データだけをシートから読み込む"""
        all_records = self.load_unavailability(worksheet_name=worksheet_name)
        return [u for u in all_records if u.member_name == member_name]

    def _get_or_create_worksheet(self, name: str, rows: int = 100, cols: int = 10):
        try:
            return self.sh.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            return self.sh.add_worksheet(title=name, rows=rows, cols=cols)

    def _write_schedule_sheet(self, result: ScheduleResult, sheet_name: str) -> None:
        ws = self._get_or_create_worksheet(sheet_name)
        ws.clear()
        rows = [["日付", "曜日", "日中", "夜間", "外部バイト"]]
        for entry in result.entries:
            weekday = WEEKDAY_JA[entry.day.weekday()]
            rows.append(
                [
                    f"{entry.day.month}/{entry.day.day}",
                    weekday,
                    entry.assignments.get(Slot.DAY) or "(未割当)",
                    entry.assignments.get(Slot.NIGHT) or "(未割当)",
                    entry.gaikobu or "",
                ]
            )
        ws.update(values=rows, range_name="A1")

    def _write_stats_sheet(self, result: ScheduleResult, sheet_name: str) -> None:
        ws = self._get_or_create_worksheet(sheet_name)
        ws.clear()
        rows = [["名前", "日中", "夜間", "自院合計", "外部バイト", "総勤務", "目標", "差"]]
        for name, s in result.stats.items():
            rows.append(
                [
                    name,
                    s["day"],
                    s["night"],
                    s["total"],
                    s.get("gaikobu", 0),
                    s.get("grand_total", s["total"]),
                    s["target"],
                    s["diff"],
                ]
            )
        ws.update(values=rows, range_name="A1")
