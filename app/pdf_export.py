# -*- coding: utf-8 -*-
"""
最適化結果・実績・年間集計・交代履歴をPDFファイルへ出力する。

reportlab組み込みのCID(Adobe-Japan1)フォントを使うため、外部フォント
ファイルを用意する必要がない(HeiseiKakuGo-W5)。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import ParagraphStyle

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))

from src.models import ScheduleEntry, ScheduleResult, Slot  # noqa: E402

_FONT_NAME = "HeiseiKakuGo-W5"
pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]
SLOT_TYPE_LABEL = {"day": "日中", "night": "夜間", "gaikobu": "外部バイト"}
SWAP_STATUS_LABEL = {"pending": "保留中", "approved": "承認済み", "rejected": "却下"}

_HEADER_BG = colors.HexColor("#4472C4")
_WEEKEND_BG = colors.HexColor("#FCE4D6")
_WARN_BG = colors.HexColor("#F8CCCC")

_TABLE_STYLE_BASE = [
    ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
    ("FONTSIZE", (0, 0), (-1, -1), 8),
    ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
]


def _normalize_entries(entries: List[Union[ScheduleEntry, dict]]) -> List[dict]:
    normalized = []
    for e in entries:
        if isinstance(e, ScheduleEntry):
            normalized.append(
                {"date": e.day, "day": e.assignments.get(Slot.DAY), "night": e.assignments.get(Slot.NIGHT), "gaikobu": e.gaikobu}
            )
        else:
            d = e["date"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            normalized.append({"date": d, "day": e.get("day"), "night": e.get("night"), "gaikobu": e.get("gaikobu")})
    return normalized


def _build_calendar_table(entries: List[Union[ScheduleEntry, dict]]) -> Table:
    data = [["日付", "曜日", "日中", "夜間", "外部バイト"]]
    weekend_rows = []
    for row_idx, e in enumerate(_normalize_entries(entries), start=1):
        d = e["date"]
        weekday_idx = d.weekday()
        data.append(
            [
                f"{d.month}/{d.day}",
                WEEKDAY_JA[weekday_idx],
                e.get("day") or "(未割当)",
                e.get("night") or "(未割当)",
                e.get("gaikobu") or "",
            ]
        )
        if weekday_idx >= 5:
            weekend_rows.append(row_idx)

    table = Table(data, colWidths=[22 * mm, 16 * mm, 48 * mm, 48 * mm, 40 * mm], repeatRows=1)
    style = list(_TABLE_STYLE_BASE)
    for row_idx in weekend_rows:
        style.append(("BACKGROUND", (0, row_idx), (1, row_idx), _WEEKEND_BG))
    table.setStyle(TableStyle(style))
    return table


def _build_stats_table(stats: Dict[str, Dict[str, int]]) -> Table:
    data = [["名前", "日中", "夜間", "自院合計", "外部バイト", "総勤務", "目標", "差"]]
    diff_rows = []
    for row_idx, (name, s) in enumerate(stats.items(), start=1):
        data.append(
            [
                name,
                s.get("day", 0),
                s.get("night", 0),
                s.get("total", 0),
                s.get("gaikobu", 0),
                s.get("grand_total", s.get("total", 0)),
                s.get("target", 0),
                s.get("diff", 0),
            ]
        )
        if s.get("diff", 0) != 0:
            diff_rows.append(row_idx)

    table = Table(data, colWidths=[24 * mm, 14 * mm, 14 * mm, 18 * mm, 18 * mm, 16 * mm, 14 * mm, 12 * mm])
    style = list(_TABLE_STYLE_BASE)
    style[1] = ("FONTSIZE", (0, 0), (-1, -1), 9)
    for row_idx in diff_rows:
        style.append(("BACKGROUND", (7, row_idx), (7, row_idx), _WARN_BG))
    table.setStyle(TableStyle(style))
    return table


def _build_annual_table(annual_totals: Dict[str, Dict[str, int]]) -> Table:
    data = [["名前", "自院日中\n実績", "自院夜間\n実績", "自院合計\n実績", "外部バイト\n実績", "総勤務\n実績"]]
    for name, s in annual_totals.items():
        data.append([name, s.get("day", 0), s.get("night", 0), s.get("total", 0), s.get("gaikobu", 0), s.get("grand_total", 0)])

    table = Table(data, colWidths=[26 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 20 * mm])
    style = list(_TABLE_STYLE_BASE)
    style[1] = ("FONTSIZE", (0, 0), (-1, -1), 9)
    table.setStyle(TableStyle(style))
    return table


def _build_swap_history_table(swap_history: List[dict]) -> Table:
    data = [["日付", "勤務種別", "元の担当者", "交代後", "申請日時", "承認日時", "状態"]]
    for r in swap_history:
        data.append(
            [
                r.get("date", ""),
                SLOT_TYPE_LABEL.get(r.get("slot_type"), r.get("slot_type", "")),
                r.get("from_member", ""),
                r.get("to_member", ""),
                (r.get("requested_at") or "")[:16].replace("T", " "),
                (r.get("approved_at") or "")[:16].replace("T", " ") if r.get("approved_at") else "",
                SWAP_STATUS_LABEL.get(r.get("status"), r.get("status", "")),
            ]
        )
    table = Table(data, colWidths=[20 * mm, 20 * mm, 22 * mm, 22 * mm, 32 * mm, 32 * mm, 18 * mm], repeatRows=1)
    table.setStyle(TableStyle(_TABLE_STYLE_BASE))
    return table


def export_to_pdf(
    scheduled_result: ScheduleResult,
    output_path: str | Path,
    actual_entries: Optional[List[dict]] = None,
    actual_stats: Optional[Dict[str, Dict[str, int]]] = None,
    annual_totals: Optional[Dict[str, Dict[str, int]]] = None,
    swap_history: Optional[List[dict]] = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
    )

    title_style = ParagraphStyle("title", fontName=_FONT_NAME, fontSize=16, leading=20, spaceAfter=8)
    heading_style = ParagraphStyle("heading", fontName=_FONT_NAME, fontSize=12, leading=16, spaceAfter=4)
    normal_style = ParagraphStyle("normal", fontName=_FONT_NAME, fontSize=9, leading=12)

    elements = []
    elements.append(Paragraph(f"{scheduled_result.year}年{scheduled_result.month}月 オンコール勤務表", title_style))
    elements.append(Spacer(1, 4 * mm))

    monthly_stats = actual_stats if actual_stats is not None else scheduled_result.stats
    elements.append(Paragraph("月間集計" + ("(実績ベース)" if actual_stats is not None else "(予定ベース)"), heading_style))
    elements.append(_build_stats_table(monthly_stats))
    elements.append(Spacer(1, 8 * mm))

    elements.append(Paragraph("予定勤務表", heading_style))
    elements.append(_build_calendar_table(scheduled_result.entries))

    if actual_entries is not None:
        elements.append(PageBreak())
        elements.append(Paragraph("実績勤務表", heading_style))
        elements.append(_build_calendar_table(actual_entries))

    if annual_totals:
        elements.append(PageBreak())
        elements.append(Paragraph("年間実績集計", heading_style))
        elements.append(Paragraph("(評価の中心は自院日中+自院夜間。外部バイトは義務ではないため別枠で表示)", normal_style))
        elements.append(Spacer(1, 2 * mm))
        elements.append(_build_annual_table(annual_totals))

    if swap_history:
        elements.append(PageBreak())
        elements.append(Paragraph("交代履歴", heading_style))
        elements.append(_build_swap_history_table(swap_history))

    if scheduled_result.warnings:
        elements.append(Spacer(1, 6 * mm))
        elements.append(Paragraph("警告・注意事項:", normal_style))
        for w in scheduled_result.warnings:
            elements.append(Paragraph(f"- {w}", normal_style))

    doc.build(elements)
    return output_path
