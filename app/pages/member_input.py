# -*- coding: utf-8 -*-
"""
メンバー入力画面

- 名前選択やPINコードは廃止。代わりに、管理者が発行した専用URL
  (例: .../member_input?token=otani_1a2b3c4d5e6f)でアクセスすることで
  本人確認を行う。トークンを知っている人だけがこの画面に入力できる。
- 初期表示は管理者が設定した対象年月だが、◀▶ボタンで表示月を
  前後に切り替えられる(過去・未来の月も閲覧・修正できる)。
  管理者設定の対象年月そのものは変更されず、あくまで「今このページで
  どの月を見ているか」という表示上の状態(ブラウザのセッション内でのみ保持)
- カレンダーが表示される
- 各日をタップすると 「○(終日OK)→×(終日不可)→▲昼(日中不可)→▲夜(夜間不可)→○...」
  の順に状態が切り替わる。タップ中の変更は st.session_state (ブラウザの
  セッション内)だけに保持され、Google Sheetsへは一切アクセスしない。
- 入力締切が設定されていれば、締切までの残り日数を表示する
- 「💾 保存する」を押した時点で初めて、その人・その月分のデータだけを
  Googleスプレッドシート(不都合日入力_v2)へ安全に保存する
  (member×year_month単位のupsertで、他メンバー・他月のデータには触れない)
- 毎月同じURLを使い続けられる(年月はアプリ全体の設定なので、
  月が変わってもURLを再取得する必要はない)
"""
import sys
import html
from datetime import date
from pathlib import Path
from typing import Dict

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent.parent
# Always prefer modules from this deployed app directory.  Streamlit can rerun a
# page inside a long-lived process, so a stale top-level module with the same name
# must never win over app/data_store.py.
if str(_APP_DIR) in sys.path:
    sys.path.remove(str(_APP_DIR))
sys.path.insert(0, str(_APP_DIR))

import importlib  # noqa: E402
import auth  # noqa: E402
import data_store as ds  # noqa: E402
import ui_common as uc  # noqa: E402

# Deployment safety guard: the previous rollout showed a new member_input.py
# running against a stale data_store module.  If that happens, force one fresh
# import from app/data_store.py and fail closed rather than exposing the old
# destructive save path.
_EXPECTED_DATA_STORE = (_APP_DIR / "data_store.py").resolve()
_REQUIRED_DATA_STORE_API = (
    "state_from_flags",
    "next_cycle_state",
    "flags_from_state",
    "save_member_unavailability",
)
_ds_file = Path(getattr(ds, "__file__", "")).resolve() if getattr(ds, "__file__", None) else None
if _ds_file != _EXPECTED_DATA_STORE or any(not hasattr(ds, name) for name in _REQUIRED_DATA_STORE_API):
    sys.modules.pop("data_store", None)
    ds = importlib.import_module("data_store")

_missing_ds_api = [name for name in _REQUIRED_DATA_STORE_API if not hasattr(ds, name)]
if _missing_ds_api:
    st.error("アプリの更新がまだ完了していません。数十秒後にページを再読み込みしてください。")
    st.stop()


def _format_sync_time(iso_str: str) -> str:
    from datetime import datetime as _dt

    try:
        return _dt.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_str


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

admin_year, admin_month = config["year"], config["month"]

st.info(f"ログイン中: **{selected}** さん")

# --- 表示月の切り替え(初期値は管理者設定の対象年月。ここでの変更は
#     このブラウザのセッション内だけの表示上の状態で、管理者設定や
#     他メンバーの表示には影響しない) ---


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


view_state_key = f"member_view_ym_{selected}"
if view_state_key not in st.session_state:
    st.session_state[view_state_key] = (admin_year, admin_month)

nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])
with nav_col1:
    if st.button("◀", key="view_month_prev", use_container_width=True):
        cur_y, cur_m = st.session_state[view_state_key]
        st.session_state[view_state_key] = _add_months(cur_y, cur_m, -1)
        st.rerun()
with nav_col2:
    _vy, _vm = st.session_state[view_state_key]
    st.markdown(
        f"<div style='text-align:center; font-weight:700; font-size:1.1rem; padding-top:0.35rem;'>"
        f"{_vy}年{_vm}月</div>",
        unsafe_allow_html=True,
    )
with nav_col3:
    if st.button("▶", key="view_month_next", use_container_width=True):
        cur_y, cur_m = st.session_state[view_state_key]
        st.session_state[view_state_key] = _add_months(cur_y, cur_m, 1)
        st.rerun()

year, month = st.session_state[view_state_key]

if (year, month) != (admin_year, admin_month):
    st.caption(f"📅 現在、{year}年{month}月を表示中です(入力対象月: {admin_year}年{admin_month}月)")
    if st.button("入力対象月に戻る", use_container_width=True):
        st.session_state[view_state_key] = (admin_year, admin_month)
        st.rerun()

st.subheader(f"{year}年{month}月 の不都合日")

# --- 締切表示(締切は入力対象月に対するものなので、対象月を表示中のときだけ出す) ---
deadline = ds.get_deadline()
if deadline and (year, month) == (admin_year, admin_month):
    days_left = (deadline - date.today()).days
    if days_left < 0:
        st.error(f"⏰ 入力締切({deadline.month}月{deadline.day}日)は過ぎています。至急入力してください。")
    elif days_left == 0:
        st.warning(f"⏰ 本日({deadline.month}月{deadline.day}日)が入力締切です。")
    elif days_left <= 3:
        st.warning(f"⏰ 入力締切まであと{days_left}日です({deadline.month}月{deadline.day}日まで)。")
    else:
        st.info(f"⏰ 入力締切: {deadline.month}月{deadline.day}日(あと{days_left}日)")
elif deadline:
    st.caption(f"⏰ 入力締切({deadline.month}月{deadline.day}日)は入力対象月({admin_month}月分)のものです。")

if ds.is_finalized(year, month):
    st.info("この月の勤務表は既に確定されています。修正が必要な場合は管理者に連絡してください。")

st.markdown(
    "各日をタップすると状態が切り替わります: "
    "**○(終日OK) → ×(終日不可) → ▲昼(日中不可) → ▲夜(夜間不可) → ○...** "
    "タップ中の変更はこの画面上だけに一時保存され、"
    "下の「💾 保存する」を押した時点でGoogleスプレッドシートに反映されます。"
)

# --- タップ中の編集内容はセッション状態(ブラウザのセッション内)だけに保持する。
#     タップのたびにGoogleスプレッドシートへ読み書きすることはない。
#     (year, month, selected)の組み合わせごとに独立したキーを持つため、
#     表示月を切り替えても他の月の未保存編集は保持される。
pending_key = f"unavail_pending_{selected}_{year}_{month}"
if pending_key not in st.session_state:
    st.session_state[pending_key] = dict(ds.load_unavailability_raw(year, month).get(selected, {}))
pending_map: Dict[str, dict] = st.session_state[pending_key]

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

# 状態ごとの短縮表示(ボックスの大きさが変わらないよう、常に2文字程度に固定)
STATE_SHORT_LABEL = {
    ds.STATE_OK: "○",
    ds.STATE_FULL_OFF: "×終",
    ds.STATE_DAY_OFF: "▲昼",
    ds.STATE_NIGHT_OFF: "▲夜",
}

# --- カレンダー本体: st.button方式(Streamlit公式のwidgetイベント経路) ---
#
# 以前は <a href="?..."> のHTMLリンク方式にしていたが、スマホ(タッチ操作)で
# タップしても反応しないことがあった。原因は主に2つ:
#   1. st.navigation/st.Page環境では、同一オリジンの<a>タグのクリックが
#      StreamlitフロントエンドのSPA的なルーティング処理に横取りされ、
#      正規のwidgetイベント経路(WebSocket経由でPythonに通知→rerun)を
#      通らない場合がある
#   2. ':active { transform: scale(0.98) }' のCSSがタップ中に要素の見た目を
#      変化させ、iOS Safari等がこれを「指が動いた=スクロール」と誤認識し、
#      クリックイベント自体をキャンセルしてしまう(マウス操作では発生しない)
#
# そのため、このアプリの他の全ボタン(承認/却下ボタン等)と同じ、
# 確実に動作するst.buttonに統一した。
#
# 色付けは、以前使っていた「aria-labelの文字列マッチ」(aria-labelは
# デフォルトでは付与されず実質機能していなかった)ではなく、Streamlit公式の
# 「keyを指定すると要素に `st-key-<key>` というCSSクラスが付与される」仕組み
# (Streamlit 1.31以降)を使う。keyに状態を含めることで、状態ごとに確実な
# CSSクラスで背景色を指定できる。
#
# CSSは st.container(key=...) でスコープを絞り、カレンダー以外のボタンに
# 副作用が及ばないようにしている。
CAL_CONTAINER_KEY = "unavail_calendar"

css_rules = [
    f"""
    .st-key-{CAL_CONTAINER_KEY} [data-testid="stHorizontalBlock"] {{
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 4px !important;
        width: 100% !important;
        max-width: 100% !important;
    }}
    .st-key-{CAL_CONTAINER_KEY} [data-testid="stHorizontalBlock"] > div {{
        min-width: 0 !important;
        width: 14.2857% !important;
        flex: 1 1 0 !important;
    }}
    .st-key-{CAL_CONTAINER_KEY} div.stButton > button {{
        width: 100% !important;
        min-width: 0 !important;
        height: 3.2rem !important;
        min-height: 3.2rem !important;
        max-height: 3.2rem !important;
        padding: 0.1rem 0.05rem !important;
        white-space: pre-line !important;
        line-height: 1.05 !important;
        font-weight: 700 !important;
        border-radius: 0.65rem !important;
        border: 1px solid #cfd6e4 !important;
        color: #1f2937 !important;
        box-shadow: none !important;
        overflow: hidden !important;
        touch-action: manipulation;
        -webkit-tap-highlight-color: rgba(0,0,0,0.08);
    }}
    .st-key-{CAL_CONTAINER_KEY} div.stButton > button:active {{
        filter: brightness(0.94);
    }}
    .st-key-{CAL_CONTAINER_KEY} .cal-weekday {{
        text-align: center;
        font-weight: 700;
        color: #4b5563;
        font-size: 0.88rem;
        padding: 0.15rem 0;
    }}
    .st-key-{CAL_CONTAINER_KEY} .cal-empty {{
        height: 3.2rem;
        border: 1px solid #e5e7eb;
        border-radius: 0.65rem;
        background: #f8fafc;
        opacity: 0.5;
    }}
    """
]

day_keys: Dict[str, str] = {}
for week in weeks:
    for d in week:
        if d is None:
            continue
        day_str = d.isoformat()
        state = ds.state_from_flags(pending_map.get(day_str))
        cell_key = f"cal_{d.day:02d}_{state}"
        day_keys[day_str] = cell_key
        border_extra = "border-color:#94a3b8 !important;" if uc.is_weekend(d) else ""
        css_rules.append(
            f".st-key-{CAL_CONTAINER_KEY} .st-key-{cell_key} button "
            f"{{ background-color: {ds.STATE_COLOR[state]} !important; {border_extra} }}"
        )

with st.container(key=CAL_CONTAINER_KEY):
    st.markdown(f"<style>{''.join(css_rules)}</style>", unsafe_allow_html=True)

    header_cols = st.columns(7, gap="small")
    for col, wd in zip(header_cols, uc.WEEKDAY_JA):
        col.markdown(f"<div class='cal-weekday'>{html.escape(wd)}</div>", unsafe_allow_html=True)

    for week in weeks:
        row_cols = st.columns(7, gap="small")
        for col, d in zip(row_cols, week):
            with col:
                if d is None:
                    st.markdown("<div class='cal-empty'></div>", unsafe_allow_html=True)
                    continue
                day_str = d.isoformat()
                state = ds.state_from_flags(pending_map.get(day_str))
                label = f"{d.day}\n{STATE_SHORT_LABEL[state]}"
                if st.button(
                    label,
                    key=day_keys[day_str],
                    use_container_width=True,
                    help=f"{d.day}日: {ds.STATE_LABEL[state]}",
                ):
                    # ここではセッション状態(pending_map)だけを書き換える。
                    # Google Sheets・ローカルファイルへは一切アクセスしない。
                    nxt = ds.next_cycle_state(state)
                    flags = ds.flags_from_state(nxt)
                    if flags is None:
                        pending_map.pop(day_str, None)
                    else:
                        pending_map[day_str] = flags
                    st.rerun()

st.caption("色で状態を判別できます。表示: ○=終日OK、×終=終日不可、▲昼=日中不可、▲夜=夜間不可")

st.divider()

if st.button("💾 保存する", type="primary"):
    ok, message = ds.save_member_unavailability(year, month, selected, dict(pending_map))
    if ok:
        st.success(f"{selected}さんの{year}年{month}月分を保存しました。{message}")
    else:
        st.error(f"保存に失敗しました。入力内容はこの画面に残っています。再度お試しください: {message}")

st.caption("入力後、内容の変更が必要な場合は再度タップして状態を切り替え、「保存する」を押してください。このURLは毎月そのまま使えます。")

# ------------------------------------------------------------------
# 「Googleスプレッドシートと同期」の手動ボタンはメンバー側には表示しない。
# 上の「💾 保存する」を押した時点で既にGoogleスプレッドシート(v2)へ
# 保存済みのため、メンバーが別途同期を意識する必要はない。
# 管理者向けの一括保存・読み込みボタンは管理者画面(admin.py)のみに残している。
# ------------------------------------------------------------------

st.divider()

# ======================================================================
# 全体勤務表(閲覧専用)
# ======================================================================
st.header("🗂️ 全体勤務表(閲覧専用)")

scheduled_snapshot = ds.load_schedule_snapshot(year, month)

if scheduled_snapshot:
    from datetime import datetime as _dt

    overview_rows = []
    for e in scheduled_snapshot["entries"]:
        d = _dt.strptime(e["date"], "%Y-%m-%d").date()
        overview_rows.append(
            {
                "日付": f"{d.month}/{d.day}({uc.WEEKDAY_JA_BY_PYTHON_INDEX[d.weekday()]})",
                "日中": e.get("day") or "-",
                "夜間": e.get("night") or "-",
                "外部バイト": e.get("gaikobu") or "-",
            }
        )
    st.dataframe(overview_rows, use_container_width=True, hide_index=True)
    st.caption("表示のみです。予定の変更が必要な場合は管理者にご連絡ください。")
else:
    st.caption("まだこの月の勤務表は確定されていません。")

st.divider()

# ======================================================================
# 自分の予定勤務・実績勤務
# ======================================================================
st.header("📋 自分の勤務(予定・実績)")

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
