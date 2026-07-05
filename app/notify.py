# -*- coding: utf-8 -*-
"""
リマインダー・共有機能

- 未入力メンバーの一覧からリマインダー文章を自動生成
- LINEでそのまま共有できるURL(LINEの「共有」機能。個人のLINEアプリを
  開いて送信するだけなので、LINE Developerアカウントや料金は不要)
- (任意) SMTP設定がある場合、メールでリマインダーを送信

■ 参考: LINE Notifyについて
  LINE Notifyは2025年3月31日にサービスを終了しており、現在は利用できません。
  自動プッシュ通知をLINEで行いたい場合は、LINE公式アカウント + Messaging API
  の契約が必要です(本システムの範囲外)。ここでは追加の契約不要な
  「共有リンク」方式のみを提供しています。
"""
from __future__ import annotations

import smtplib
import ssl
from datetime import date
from email.mime.text import MIMEText
from typing import List
from urllib.parse import quote


def build_reminder_text(
    year: int,
    month: int,
    all_members: List[str],
    submitted_members: List[str],
    deadline: date | None = None,
) -> str:
    """未入力メンバーへのリマインダー文章を作る"""
    not_submitted = [m for m in all_members if m not in submitted_members]

    lines = [f"【オンコール表】{year}年{month}月分の不都合日入力のお願い"]
    if deadline:
        lines.append(f"入力締切: {deadline.month}月{deadline.day}日")
    if not_submitted:
        lines.append("まだ入力がお済みでない方: " + "、".join(not_submitted))
    else:
        lines.append("全員の入力が完了しています。ご協力ありがとうございます。")
    lines.append("入力はWebアプリからお願いします。")
    return "\n".join(lines)


def build_schedule_share_text(year: int, month: int, stats: dict) -> str:
    """勤務表確定後にLINE等で共有するための簡潔なテキストを作る"""
    lines = [f"【オンコール表】{year}年{month}月分が確定しました。"]
    for name, s in stats.items():
        lines.append(f"{name}: 日中{s['day']}回 夜間{s['night']}回 (合計{s['total']}回)")
    return "\n".join(lines)


def build_line_share_url(text: str) -> str:
    """
    LINEの公式「共有」用URLスキームを使い、テキストを埋め込んだ状態で
    LINEアプリ(または線友だち選択画面)を開くリンクを作る。
    LINE Developerアカウントや料金は不要(個人のLINEで送信するだけ)。
    """
    return f"https://line.me/R/msg/text/?{quote(text)}"


def send_email_reminders(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_address: str,
    to_addresses: List[str],
    subject: str,
    body: str,
    use_tls: bool = True,
) -> None:
    """
    シンプルなSMTP経由のメール送信(Gmail等のSMTPサーバーを利用する想定)。
    to_addresses が空の場合は何もしない。
    """
    if not to_addresses:
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = ", ".join(to_addresses)

    if use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls(context=context)
            if smtp_user:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_address, to_addresses, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_address, to_addresses, msg.as_string())
