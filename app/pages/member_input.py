# -*- coding: utf-8 -*-
"""
メンバー入力画面

- 名前選択やPINコードは廃止。代わりに、管理者が発行した専用URL
  (例: .../member_input?token=otani_1a2b3c4d5e6f)でアクセスすることで
  本人確認を行う。トークンを知っている人だけがこの画面に入力できる。
- カレンダーが表示される
- 各日をタップすると 「○(終日OK)→×(終日不可)→▲昼(日中不可)→▲夜(夜間不可)→○...」
  の順に状態が切り替わる(タップごとに自動保存)
- 入力締切が設定されていれば、締切までの残り日数を表示する
- 「入力内容を確定する」を押すと、(自動同期が有効な場合)
  Googleスプレッドシートへも同期される
- 毎月同じURLを使い続けられる(年月はアプリ全体の設定なので、
  月が変わってもURLを再取得する必要はない)
"""
import sys
import html
from urllib.parse import urlencode
from datetime import date
from pathlib import Path

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.append(str(_APP_DIR))

import auth  # noqa: E402
import data_store as ds  # noqa: E402
import ui_common as uc  # noqa: E402

st.title("👤 不都合日入力")

# --- トークンによる本人確認 ---
token = st.query_params.get("token")
selected = auth.get_member_by_token(token) if token else None

if not selected:
    st.error(
        "有効な個別URLでアクセスしてください。URLが分からない場合は管理者に発行を依頼してください。"
    )
    st.caption("このページは、管理者から発行された専用URL(トークン付き)でのみ利用できます。")
    st.stop()

config = ds.load_config()
members = config.get("members", [])
member_names = [m["name"] for m in members]

if selected not in member_names:
    st.error("このURLに対応するメンバーが見つかりません。管理者にURLの再発行を依頼してください。")
    st.stop()

year, month = config["year"], config["month"]

# URLリンク型カレンダー用: ?set_day=YYYY-MM-DD が来たら状態を切り替えて、
# すぐに通常URLへ戻す。st.columns/st.buttonを使わないため、スマホでも7列が崩れない。
set_day = st.query_params.get("set_day")
if set_day:
    valid_days = {d.isoformat() for week in uc.month_weeks(year, month) for d in week if d is not None}
    if set_day in valid_days:
        ds.cycle_member_day_state(year, month, selected, set_day)
    st.query_params.clear()
    st.query_params["token"] = token
    st.rerun()

st.info(f"ログイン中: **{selected}** さん")

st.subheader(f"{year}年{month}月 の不都合日")

# --- 締切表示 ---
deadline = ds.get_deadline()
if deadline:
    days_left = (deadline - date.today()).days
    if days_left < 0:
        st.error(f"⏰ 入力締切({deadline.month}月{deadline.day}日)は過ぎています。至急入力してください。")
    elif days_left == 0:
        st.warning(f"⏰ 本日({deadline.month}月{deadline.day}日)が入力締切です。")
    elif days_left <= 3:
        st.warning(f"⏰ 入力締切まであと{days_left}日です({deadline.month}月{deadline.day}日まで)。")
    else:
        st.info(f"⏰ 入力締切: {deadline.month}月{deadline.day}日(あと{days_left}日)")

if ds.is_finalized(year, month):
    st.info("この月の勤務表は既に確定されています。修正が必要な場合は管理者に連絡してください。")

st.markdown(
    "各日をタップすると状態が切り替わります: "
    "**○(終日OK) → ×(終日不可) → ▲昼(日中不可) → ▲夜(夜間不可) → ○...** "
    "タップした瞬間に自動保存されます。"
)

legend_items = [
    (ds.STATE_OK, "終日OK"),
    (ds.STATE_FULL_OFF, "終日不可"),
    (ds.STATE_DAY_OFF, "日中不可"),
    (ds.STATE_NIGHT_OFF, "夜間不可"),
]
legend_html = ["<div class='mobile-legend'>"]
for state, text in legend_items:
    legend_html.append(
        "<div class='mobile-legend-item' "
        f"style='background-color:{ds.STATE_COLOR[state]};'>"
        f"{html.escape(ds.STATE_LABEL[state])} {html.escape(text)}</div>"
    )
legend_html.append("</div>")
st.markdown("".join(legend_html), unsafe_allow_html=True)

weeks = uc.month_weeks(year, month)

# 重要: HTMLリンクだと環境によってタップ時にPython側へイベントが届かないことがあるため、
# Streamlitのネイティブボタンで描画する。CSSでスマホでも7列を維持する。
weekday_cols = st.columns(7, gap="small")
for col, wd in zip(weekday_cols, uc.WEEKDAY_JA):
    col.markdown(f"<div class='native-cal-weekday'>{html.escape(wd)}</div>", unsafe_allow_html=True)

for week_index, week in enumerate(weeks):
    cols = st.columns(7, gap="small")
    for col_index, d in enumerate(week):
        with cols[col_index]:
            if d is None:
                st.markdown("<div class='native-cal-empty'>&nbsp;</div>", unsafe_allow_html=True)
                continue

            day_str = d.isoformat()
            state = ds.get_member_day_state(year, month, selected, day_str)
            label = f"{d.day}\n{ds.STATE_LABEL[state]}"
            if st.button(
                label,
                key=f"cal_{selected}_{year}_{month}_{day_str}",
                use_container_width=True,
                help=f"{d.day}日: {ds.STATE_LABEL[state]}",
            ):
                ds.cycle_member_day_state(year, month, selected, day_str)
                st.rerun()

st.caption("スマホでも横スクロールなしで7列固定表示にしています。日付ボタンをタップすると即時保存されます。")

st.divider()

if st.button("✅ 入力内容を確定する", type="primary"):
    st.success(f"{selected}さんの{year}年{month}月分の入力内容を確認しました。タップした内容は自動保存済みです。")

    sync_settings = ds.get_auto_sync_settings()
    if sync_settings["enabled"] and sync_settings["spreadsheet_key"]:
        cred_path = _APP_DIR.parent / "credentials" / "service_account.json"
        if cred_path.exists():
            try:
                from src.sheets_io import SheetsClient

                client = SheetsClient(
                    credentials_path=cred_path, spreadsheet_key=sync_settings["spreadsheet_key"]
                )
                unavailabilities = ds.get_unavailability_objects(year, month)
                client.write_unavailability(unavailabilities)
                st.success("Googleスプレッドシートにも同期しました")
            except Exception as e:  # noqa: BLE001
                st.warning(f"スプレッドシートへの同期に失敗しました(入力内容自体は保存済みです): {e}")

st.caption("入力後、内容の変更が必要な場合は再度タップして状態を切り替えてください。このURLは毎月そのまま使えます。")

st.divider()

# ======================================================================
# 自分の予定勤務・実績勤務
# ======================================================================
st.header("📋 自分の勤務(予定・実績)")

scheduled_snapshot = ds.load_schedule_snapshot(year, month)
actual_snapshot = ds.load_actual_snapshot(year, month)


def _my_shifts(entries: list, name: str) -> list:
    rows = []
    for e in entries:
        parts = []
        if e.get("day") == name:
            parts.append("日中")
        if e.get("night") == name:
            parts.append("夜間")
        if e.get("gaikobu") == name:
            parts.append("外部バイト")
        if parts:
            rows.append({"日付": e["date"], "勤務": "・".join(parts)})
    return rows


tab_sched, tab_actual = st.tabs(["予定", "実績"])
with tab_sched:
    if scheduled_snapshot:
        my_scheduled = _my_shifts(scheduled_snapshot["entries"], selected)
        if my_scheduled:
            st.dataframe(my_scheduled, use_container_width=True, hide_index=True)
        else:
            st.caption("この月の予定勤務はありません。")
    else:
        st.caption("まだ勤務表が確定されていません。")

with tab_actual:
    if actual_snapshot:
        my_actual = _my_shifts(actual_snapshot["entries"], selected)
        if my_actual:
            st.dataframe(my_actual, use_container_width=True, hide_index=True)
        else:
            st.caption("この月の実績勤務はありません。")
    else:
        st.caption("まだ勤務表が確定されていません。")

st.divider()

# ======================================================================
# 勤務交代
# ======================================================================
st.header("🔄 勤務交代")

if actual_snapshot is None:
    st.info("勤務表が確定されると、ここから勤務交代の依頼ができるようになります。")
else:
    st.subheader("交代依頼を作成する")
    st.caption("自分の勤務のうち、交代してほしい日を選び、交代相手を指定してください。相手が承認すると実績に反映されます。")

    my_shift_options = []
    shift_label_to_choice = {}
    for e in actual_snapshot["entries"]:
        if e.get("day") == selected:
            label = f"{e['date']} 日中"
            my_shift_options.append(label)
            shift_label_to_choice[label] = (e["date"], "day")
        if e.get("night") == selected:
            label = f"{e['date']} 夜間"
            my_shift_options.append(label)
            shift_label_to_choice[label] = (e["date"], "night")
        if e.get("gaikobu") == selected:
            label = f"{e['date']} 外部バイト"
            my_shift_options.append(label)
            shift_label_to_choice[label] = (e["date"], "gaikobu")

    if not my_shift_options:
        st.caption("現在、交代を依頼できる自分の勤務がありません。")
    else:
        other_members = [n for n in member_names if n != selected]
        with st.form("swap_request_form"):
            shift_label = st.selectbox("交代してほしい勤務", my_shift_options)
            partner = st.selectbox("交代相手", other_members)
            submitted = st.form_submit_button("交代依頼を送る")
            if submitted:
                day_str, slot_type = shift_label_to_choice[shift_label]
                if slot_type == "gaikobu":
                    partner_info = next((m for m in members if m["name"] == partner), None)
                    if not partner_info or not partner_info.get("gaikobu_eligible"):
                        st.error(f"{partner}さんは外部バイト対象者ではないため、この勤務は依頼できません。")
                        submitted = False
                if submitted:
                    ds.create_swap_request(year, month, day_str, slot_type, selected, partner)
                    st.success(f"{partner}さんに交代依頼を送りました。承認されると実績に反映されます。")
                    st.rerun()

    st.subheader("自分宛の交代依頼")
    incoming_requests = [
        r for r in ds.get_swap_requests(year=year, month=month, status=ds.SWAP_STATUS_PENDING)
        if r["to_member"] == selected
    ]
    if not incoming_requests:
        st.caption("現在、承認待ちの交代依頼はありません。")
    else:
        for r in incoming_requests:
            slot_label = ds.SLOT_TYPE_LABEL.get(r["slot_type"], r["slot_type"])
            st.write(f"**{r['date']} {slot_label}** を {r['from_member']}さんから引き受ける依頼")
            col_approve, col_reject = st.columns(2)
            with col_approve:
                if st.button("✅ 承認する", key=f"approve_{r['id']}"):
                    result_msg = ds.respond_to_swap_request(r["id"], approve=True)
                    if result_msg == "approved":
                        st.success("承認しました。実績を更新しました。")
                    else:
                        st.error(result_msg)
                    st.rerun()
            with col_reject:
                if st.button("❌ 却下する", key=f"reject_{r['id']}"):
                    ds.respond_to_swap_request(r["id"], approve=False)
                    st.info("却下しました。")
                    st.rerun()

    st.subheader("自分が出した交代依頼")
    outgoing_requests = ds.get_swap_requests(year=year, month=month, member_name=selected)
    outgoing_requests = [r for r in outgoing_requests if r["from_member"] == selected]
    if outgoing_requests:
        rows = [
            {
                "日付": r["date"],
                "勤務種別": ds.SLOT_TYPE_LABEL.get(r["slot_type"], r["slot_type"]),
                "交代相手": r["to_member"],
                "状態": ds.SWAP_STATUS_LABEL.get(r["status"], r["status"]),
            }
            for r in outgoing_requests
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("これまでに出した交代依頼はありません。")
