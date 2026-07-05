# -*- coding: utf-8 -*-
"""
管理者画面

- 管理者ログイン(パスワード)
- メンバー管理(追加・削除・メールアドレス・目標回数の自動計算)
- メンバー専用URLの発行(本人確認は専用URLのトークン方式)
- 入力状況一覧(入力済み/未入力/最終更新日時)
- 入力締切の設定・未入力者へのリマインダー(LINE共有 / メール)
- Googleスプレッドシートへの自動同期設定
- 勤務表作成ボタン(最適化を実行)
- 勤務表をカレンダー形式で色付き表示・集計表
- ワンタップでの勤務表確定 → Excel/PDF自動出力・LINE共有文言の生成
"""
import sys
import html
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

_APP_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _APP_DIR.parent
for p in (_APP_DIR, _PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.append(str(p))

import auth  # noqa: E402
import data_store as ds  # noqa: E402
import notify  # noqa: E402
import pdf_export  # noqa: E402
import sheets_sync as ssync  # noqa: E402
import ui_common as uc  # noqa: E402
from src.excel_export import export_to_excel  # noqa: E402
from src.optimizer import OnCallOptimizer, OptimizerOptions  # noqa: E402
from src.models import ScheduleEntry, ScheduleResult, Slot  # noqa: E402


def _format_sync_time(iso_str: str) -> str:
    try:
        return datetime.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_str


st.title("🛠️ 管理者画面")

# ======================================================================
# 0. ログイン
# ======================================================================
auth.require_admin_login()

col_logout, _ = st.columns([1, 4])
with col_logout:
    if st.button("🚪 管理者ログアウト"):
        auth.logout_admin()
        st.rerun()

config = ds.load_config()

# ======================================================================
# 1. 年月設定
# ======================================================================
st.header("1. 対象年月")
c1, c2, c3 = st.columns([1, 1, 3])
with c1:
    new_year = st.number_input("年", min_value=2020, max_value=2100, value=config["year"], step=1)
with c2:
    new_month = st.number_input("月", min_value=1, max_value=12, value=config["month"], step=1)
with c3:
    st.write("")
    st.write("")
    if st.button("年月を保存"):
        ds.set_year_month(int(new_year), int(new_month))
        st.success(f"{new_year}年{new_month}月に設定しました")
        st.rerun()

st.divider()

# ======================================================================
# 2. メンバー管理・目標回数(自動計算)
# ======================================================================
st.header("2. メンバー管理・目標回数")
st.caption(
    "目標回数は通常「自動計算」を利用します。メンバー入力画面で終日不可が連続7日以上入力された場合、"
    "その期間を長期不在相当として勤務可能日数から差し引き、その月のオンコール総数を自動配分します。"
    "長期不在の専用入力欄はありません。例外対応が必要な場合のみ「手動指定」をONにして目標回数を直接編集してください。"
)

members = config.get("members", [])
n_days_in_month = 0
try:
    import calendar as _cal

    n_days_in_month = _cal.monthrange(config["year"], config["month"])[1]
except Exception:
    pass

if members:
    auto_targets = ds.compute_auto_targets(config["year"], config["month"])
    display_rows = []
    for m in members:
        available_days = ds.compute_available_days(config["year"], config["month"], m)
        display_rows.append(
            {
                "名前": m["name"],
                "目標回数": m.get("target_count", 0),
                "手動指定": bool(m.get("manual_target", False)),
                "勤務可能日数": available_days,
                "自動計算目標回数": auto_targets.get(m["name"], 0),
                "メール(任意・リマインダー用)": m.get("email", ""),
                "外部バイト対象": bool(m.get("gaikobu_eligible", False)),
            }
        )
    df = pd.DataFrame(display_rows)
    edited = st.data_editor(
        df,
        num_rows="fixed",
        use_container_width=True,
        disabled=["勤務可能日数", "自動計算目標回数"],
        column_config={
            "目標回数": st.column_config.NumberColumn(
                min_value=0, max_value=200, step=1, help="手動指定がONのときのみ実際に使用されます"
            ),
            "手動指定": st.column_config.CheckboxColumn(help="ONにすると目標回数を手動で使用します"),
            "勤務可能日数": st.column_config.NumberColumn(help="月の日数-連続7日以上の終日不可日数(自動計算・編集不可)"),
            "自動計算目標回数": st.column_config.NumberColumn(help="勤務可能日数の比率に応じた自動配分結果(編集不可)"),
            "外部バイト対象": st.column_config.CheckboxColumn(),
        },
        key="member_editor",
    )
    if st.button("メンバー情報を保存"):
        for _, row in edited.iterrows():
            name = str(row["名前"])
            ds.update_target_count(name, int(row["目標回数"]))
            ds.update_member_email(name, str(row["メール(任意・リマインダー用)"] or ""))
            ds.update_member_gaikobu_eligible(name, bool(row["外部バイト対象"]))
            ds.update_member_manual_target(name, bool(row["手動指定"]))
        st.success("保存しました")
        st.rerun()

    n_gaikobu_eligible = int(edited["外部バイト対象"].sum())
    st.caption(f"外部バイト対象者: {n_gaikobu_eligible}人 / {len(edited)}人")

    effective_targets = [
        int(row["目標回数"]) if row["手動指定"] else int(row["自動計算目標回数"]) for _, row in edited.iterrows()
    ]
    total_target = sum(effective_targets)
    total_slots = n_days_in_month * 2
    st.caption(
        f"参考: {config['month']}月の総枠数は {total_slots} 枠(日中+夜間)。"
        f"現在の目標回数(自動+手動)の合計は {total_target} 回です。"
    )
else:
    st.info("メンバーがまだ登録されていません。下のフォームから追加してください。")

st.subheader("メンバーの追加")
with st.form("add_member_form", clear_on_submit=True):
    new_name = st.text_input("名前")
    new_email = st.text_input("メールアドレス(任意・リマインダー送信用)")
    new_gaikobu_eligible = st.checkbox("外部バイト対象にする")
    st.caption("目標回数は追加後、自動計算されます。終日不可が連続7日以上ある場合は、その期間を長期不在相当として自動反映します。")
    submitted = st.form_submit_button("追加")
    if submitted:
        if not new_name.strip():
            st.error("名前を入力してください")
        else:
            try:
                ds.add_member(new_name.strip(), 0, new_email.strip(), new_gaikobu_eligible)
                st.success(f"「{new_name}」を追加しました。続けて専用URLを発行してください。")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

if members:
    st.subheader("メンバーの削除")
    del_name = st.selectbox("削除するメンバー", [m["name"] for m in members], key="del_select")
    if st.button("削除する", type="secondary"):
        ds.remove_member(del_name)
        auth.revoke_token(del_name)
        st.success(f"「{del_name}」を削除しました")
        st.rerun()

st.divider()

# ======================================================================
# 3. 外部バイト日の設定
# ======================================================================
st.header("3. 外部バイト日の設定")
st.caption(
    "外部病院バイトが必要な日をカレンダーから選択してください(クリックでON/OFF切り替え)。"
    "希望制ではなく、対象者(外部バイト対象=ONのメンバー)の中から自動的に1人が割り当てられます。"
)

year_for_gaikobu, month_for_gaikobu = config["year"], config["month"]
gaikobu_day_set = set(ds.get_gaikobu_days(year_for_gaikobu, month_for_gaikobu))
weeks_gaikobu = uc.month_weeks(year_for_gaikobu, month_for_gaikobu)

# member_input.py の不都合日カレンダーと同じ理由(<a>リンク方式はスマホの
# タップで反応しないことがある)で、st.button方式(st-keyクラスで色付け)に
# 統一している。詳細は member_input.py 側のコメントを参照。
GAIKOBU_CAL_KEY = "gaikobu_calendar"

gaikobu_css = [
    f"""
    .st-key-{GAIKOBU_CAL_KEY} [data-testid="stHorizontalBlock"] {{
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 4px !important;
        width: 100% !important;
        max-width: 100% !important;
    }}
    .st-key-{GAIKOBU_CAL_KEY} [data-testid="stHorizontalBlock"] > div {{
        min-width: 0 !important;
        width: 14.2857% !important;
        flex: 1 1 0 !important;
    }}
    .st-key-{GAIKOBU_CAL_KEY} div.stButton > button {{
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
    .st-key-{GAIKOBU_CAL_KEY} div.stButton > button:active {{
        filter: brightness(0.94);
    }}
    .st-key-{GAIKOBU_CAL_KEY} .cal-weekday {{
        text-align: center;
        font-weight: 700;
        color: #4b5563;
        font-size: 0.88rem;
        padding: 0.15rem 0;
    }}
    .st-key-{GAIKOBU_CAL_KEY} .cal-empty {{
        height: 3.2rem;
        border: 1px solid #e5e7eb;
        border-radius: 0.65rem;
        background: #f8fafc;
        opacity: 0.5;
    }}
    """
]

gaikobu_day_keys: dict = {}
for week in weeks_gaikobu:
    for d in week:
        if d is None:
            continue
        day_str = d.isoformat()
        is_on = day_str in gaikobu_day_set
        state_code = "on" if is_on else "off"
        cell_key = f"gaikobu_{d.day:02d}_{state_code}"
        gaikobu_day_keys[day_str] = cell_key
        bg = "#F8CCCC" if is_on else "#F8FAFC"
        gaikobu_css.append(
            f".st-key-{GAIKOBU_CAL_KEY} .st-key-{cell_key} button "
            f"{{ background-color: {bg} !important; }}"
        )

with st.container(key=GAIKOBU_CAL_KEY):
    st.markdown(f"<style>{''.join(gaikobu_css)}</style>", unsafe_allow_html=True)

    gaikobu_header_cols = st.columns(7, gap="small")
    for col, wd in zip(gaikobu_header_cols, uc.WEEKDAY_JA):
        col.markdown(f"<div class='cal-weekday'>{html.escape(wd)}</div>", unsafe_allow_html=True)

    for week in weeks_gaikobu:
        row_cols = st.columns(7, gap="small")
        for col, d in zip(row_cols, week):
            with col:
                if d is None:
                    st.markdown("<div class='cal-empty'></div>", unsafe_allow_html=True)
                    continue
                day_str = d.isoformat()
                is_on = day_str in gaikobu_day_set
                state_text = "🚑" if is_on else "－"
                label = f"{d.day}\n{state_text}"
                if st.button(
                    label,
                    key=gaikobu_day_keys[day_str],
                    use_container_width=True,
                    help=f"{d.day}日: 外部バイト{'あり' if is_on else 'なし'}(タップで切替)",
                ):
                    ds.toggle_gaikobu_day(year_for_gaikobu, month_for_gaikobu, day_str)
                    st.rerun()

n_gaikobu_days = len(gaikobu_day_set)
n_eligible_now = len([m for m in ds.get_members() if m.get("gaikobu_eligible", False)])
st.caption(f"外部バイト対象日: {n_gaikobu_days}日 / 外部バイト対象者: {n_eligible_now}人")
if n_gaikobu_days > 0 and n_eligible_now == 0:
    st.warning("外部バイト対象日が設定されていますが、対象者が0人です。手順2で対象者を設定してください。")

st.divider()

# ======================================================================
# 4. メンバー専用URLの発行(本人確認)
# ======================================================================
st.header("4. メンバー専用URLの発行")
st.caption(
    "各メンバーに、本人専用のURL(トークン付き)を発行してください。"
    "そのURLを知っている人だけが、そのメンバーの入力画面にアクセスできます。"
    "URLはLINE等で1回だけ個別に送れば、毎月同じURLをそのまま使い続けられます"
    "(月が変わってもURLの再取得は不要です)。"
)

current_base_url = auth.get_app_base_url()
new_base_url = st.text_input(
    "アプリの公開URL(このWebアプリにアクセスするための基点URL)",
    value=current_base_url,
    help="例: https://hospital-oncall.example.com 。ローカルで試す場合は http://localhost:8501 のままで構いません。",
)
if st.button("公開URLを保存"):
    auth.set_app_base_url(new_base_url.strip() or "http://localhost:8501")
    st.success("公開URLを保存しました")
    st.rerun()

members = ds.get_members()
if members:
    for m in members:
        name = m["name"]
        col_name, col_status, col_button = st.columns([2, 2, 2])
        with col_name:
            st.write(f"**{name}**")
        with col_status:
            st.write("🔗 発行済み" if auth.has_token(name) else "⚪ 未発行")
        with col_button:
            btn_label = "URLを再発行" if auth.has_token(name) else "URLを発行"
            if st.button(btn_label, key=f"issue_token_{name}"):
                auth.issue_token(name)
                st.rerun()
        if auth.has_token(name):
            url = auth.build_member_url(name)
            st.code(url, language=None)
    st.caption(
        "上記の各URLをそれぞれのメンバーへ個別に送ってください。再発行すると古いURLは無効になります。"
    )

st.divider()

# ======================================================================
# 4. 入力締切・リマインダー
# ======================================================================
st.header("5. 入力締切・リマインダー")

current_deadline = ds.get_deadline()
c1, c2 = st.columns([2, 1])
with c1:
    deadline_input = st.date_input("入力締切日", value=current_deadline or date.today())
with c2:
    st.write("")
    st.write("")
    if st.button("締切を保存"):
        ds.set_deadline(deadline_input.isoformat())
        st.success(f"締切を{deadline_input.month}月{deadline_input.day}日に設定しました")
        st.rerun()

members = ds.get_members()
member_names_all = [m["name"] for m in members]
submission_stats = ds.get_submission_stats(config["year"], config["month"])
last_updated = ds.get_last_updated(config["year"], config["month"])
submitted_names = [n for n in member_names_all if submission_stats.get(n, 0) > 0]
not_submitted_names = [n for n in member_names_all if n not in submitted_names]

st.subheader("入力状況一覧")
if member_names_all:
    def _format_last_updated(name: str) -> str:
        raw = last_updated.get(name)
        if not raw:
            return "-"
        try:
            dt = datetime.fromisoformat(raw)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return raw

    status_rows = [
        {
            "名前": name,
            "状態": "✅ 入力済み" if name in submitted_names else "⚠️ 未入力",
            "入力日数": submission_stats.get(name, 0),
            "最終更新日時": _format_last_updated(name),
        }
        for name in member_names_all
    ]
    status_df = pd.DataFrame(status_rows)

    def _highlight_not_submitted(row):
        color = "background-color:#FFF3CD" if row["状態"] == "⚠️ 未入力" else ""
        return [color] * len(row)

    st.dataframe(
        status_df.style.apply(_highlight_not_submitted, axis=1),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("メンバーが登録されると、入力状況とリマインダーがここに表示されます。")

if not member_names_all:
    pass
elif not_submitted_names:
    st.warning("まだ入力していない可能性がある人: " + "、".join(not_submitted_names))

    reminder_text = notify.build_reminder_text(
        config["year"], config["month"], member_names_all, submitted_names, ds.get_deadline()
    )
    with st.expander("📣 リマインダーを送る"):
        st.text_area("リマインダー文章(コピーして使えます)", value=reminder_text, height=120)

        line_url = notify.build_line_share_url(reminder_text)
        st.link_button("📱 LINEで共有する", line_url)
        st.caption("LINEアプリが開き、共有先を選んで送信できます(追加の設定は不要です)。")

        with st.form("email_reminder_form"):
            st.write("メールでリマインダーを送る(任意・SMTP設定が必要)")
            smtp_host = st.text_input("SMTPホスト", placeholder="smtp.gmail.com")
            smtp_port = st.number_input("SMTPポート", value=587, step=1)
            smtp_user = st.text_input("SMTPユーザー名(メールアドレス)")
            smtp_password = st.text_input("SMTPパスワード", type="password")
            send_submitted = st.form_submit_button("メールを送信する")
            if send_submitted:
                to_addresses = [
                    m["email"] for m in members if m["name"] in not_submitted_names and m.get("email")
                ]
                if not to_addresses:
                    st.error("未入力メンバーにメールアドレスが登録されていません(手順2で登録してください)")
                elif not smtp_host or not smtp_user:
                    st.error("SMTP情報を入力してください")
                else:
                    try:
                        notify.send_email_reminders(
                            smtp_host=smtp_host,
                            smtp_port=int(smtp_port),
                            smtp_user=smtp_user,
                            smtp_password=smtp_password,
                            from_address=smtp_user,
                            to_addresses=to_addresses,
                            subject=f"【オンコール表】{config['year']}年{config['month']}月分入力のお願い",
                            body=reminder_text,
                        )
                        st.success(f"{len(to_addresses)}件にメールを送信しました")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"送信に失敗しました: {e}")
else:
    st.success("全員が入力済みです。")

st.divider()

# ======================================================================
# 5. Googleスプレッドシートへの自動同期
# ======================================================================
st.header("6. Googleスプレッドシートへの自動同期")

sync_settings = ds.get_auto_sync_settings()

if not ssync.is_configured():
    st.caption(
        "Googleスプレッドシート連携は設定されていません。ローカルでは "
        "credentials/service_account.json を、Streamlit Community Cloudでは "
        "st.secrets の [gcp_service_account] を設定すると使えるようになります。"
        "詳細はREADME「6. Google連携のセットアップ」を参照してください。"
    )
else:
    source_label = {"local": "ローカルファイル (credentials/service_account.json)", "secrets": "st.secrets"}
    st.caption(f"認証情報の取得元: {source_label.get(ssync.credential_source(), '不明')}")

    auto_sync_enabled = st.checkbox(
        "メンバーが入力を確定するたびに、Googleスプレッドシートへ自動的に同期する",
        value=sync_settings["enabled"],
    )
    sheet_key_input = st.text_input(
        "同期先スプレッドシートのID", value=sync_settings["spreadsheet_key"]
    )
    if st.button("同期設定を保存"):
        ds.set_auto_sync_settings(auto_sync_enabled, sheet_key_input.strip())
        st.success("同期設定を保存しました")
        st.rerun()

    if sync_settings["spreadsheet_key"]:
        st.markdown("**入力データ(不都合日)の一括保存・読み込み**")
        manual_col1, manual_col2 = st.columns(2)
        with manual_col1:
            if st.button("📤 全員分をスプレッドシートに保存", use_container_width=True):
                ok, message = ssync.save_all(config["year"], config["month"])
                (st.success if ok else st.error)(message)
        with manual_col2:
            confirm_load = st.checkbox(
                "Google Sheetsの内容でローカルデータを更新します。よろしいですか?",
                key="confirm_load_all_unavailability",
            )
            if st.button(
                "📥 全員分をスプレッドシートから読み込む",
                use_container_width=True,
                disabled=not confirm_load,
            ):
                ok, message = ssync.load_all(config["year"], config["month"])
                if ok:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)

        admin_sync_times = ds.get_admin_sheets_sync(config["year"], config["month"])
        if admin_sync_times.get("saved") or admin_sync_times.get("loaded"):
            parts = []
            if admin_sync_times.get("saved"):
                parts.append(f"保存: {_format_sync_time(admin_sync_times['saved'])}")
            if admin_sync_times.get("loaded"):
                parts.append(f"読み込み: {_format_sync_time(admin_sync_times['loaded'])}")
            st.caption("最終同期時刻 - " + " / ".join(parts))

st.divider()

# ======================================================================
# 6. 勤務表の作成・確定
# ======================================================================
st.header("7. 勤務表の作成・確定")

members = ds.get_members()
if not members:
    st.warning("メンバーを登録すると勤務表を作成できます。")
    st.stop()

year, month = config["year"], config["month"]
finalized_info = ds.get_finalized_info(year, month)


def _render_stats_table(stats: dict) -> None:
    stats_rows = [
        {
            "名前": name,
            "日中": s["day"],
            "夜間": s["night"],
            "自院合計": s["total"],
            "外部バイト": s.get("gaikobu", 0),
            "総勤務": s.get("grand_total", s["total"]),
            "目標": s["target"],
            "差": s["diff"],
        }
        for name, s in stats.items()
    ]
    stats_df = pd.DataFrame(stats_rows)

    def _highlight_diff(val):
        return "background-color:#F8CCCC" if val != 0 else ""

    st.dataframe(
        stats_df.style.map(_highlight_diff, subset=["差"]),
        use_container_width=True,
        hide_index=True,
    )


def _result_to_snapshot(result: ScheduleResult) -> dict:
    return {
        "year": result.year,
        "month": result.month,
        "status": result.status,
        "warnings": result.warnings,
        "entries": [
            {
                "date": e.day.isoformat(),
                "day": e.assignments.get(Slot.DAY),
                "night": e.assignments.get(Slot.NIGHT),
                "gaikobu": e.gaikobu,
            }
            for e in result.entries
        ],
        "stats": result.stats,
    }


def _snapshot_to_result(snapshot: dict) -> ScheduleResult:
    """保存済みのスナップショット(JSON)からScheduleResultを再構築する。
    セッションが切れていて session_state に元のオブジェクトが無い場合でも、
    確定・Excel/PDF出力ができるようにするための変換。"""
    entries = []
    for e in snapshot["entries"]:
        entry = ScheduleEntry(day=datetime.strptime(e["date"], "%Y-%m-%d").date())
        entry.assignments[Slot.DAY] = e.get("day")
        entry.assignments[Slot.NIGHT] = e.get("night")
        entry.gaikobu = e.get("gaikobu")
        entries.append(entry)
    return ScheduleResult(
        year=snapshot["year"],
        month=snapshot["month"],
        entries=entries,
        status=snapshot["status"],
        stats=snapshot["stats"],
        warnings=snapshot.get("warnings", []),
    )


def _get_export_extras(year: int, month: int):
    """Excel/PDF出力に添える実績・年間集計・交代履歴を取得する。"""
    actual_snapshot = ds.load_actual_snapshot(year, month)
    actual_entries = actual_snapshot["entries"] if actual_snapshot else None
    actual_stats = actual_snapshot["stats"] if actual_snapshot else None
    annual_totals = ds.get_annual_actual_totals(year)
    swap_history = ds.get_swap_requests(year=year, month=month)
    return actual_entries, actual_stats, annual_totals, swap_history


if finalized_info:
    # ------------------------------------------------------------
    # 確定済み: 保存済みのスナップショット・出力ファイルを表示
    # ------------------------------------------------------------
    finalized_at = finalized_info.get("finalized_at", "")
    st.success(f"✅ この月の勤務表は確定済みです(確定日時: {finalized_at[:16].replace('T', ' ')})")

    snapshot = ds.load_schedule_snapshot(year, month)
    if snapshot:
        st.subheader("📅 予定勤務表(カレンダー表示)")
        uc.render_schedule_calendar(snapshot["entries"], year, month)
        st.subheader("📊 月間集計(予定ベース)")
        _render_stats_table(snapshot["stats"])

    excel_path = _PROJECT_ROOT / "output" / f"schedule_{year}_{month:02d}.xlsx"
    pdf_path = _PROJECT_ROOT / "output" / f"schedule_{year}_{month:02d}.pdf"

    if st.button("🔄 実績・年間集計・交代履歴の最新状態でExcel/PDFを再生成"):
        if snapshot is not None:
            actual_entries, actual_stats, annual_totals, swap_history = _get_export_extras(year, month)
            scheduled_for_export = _snapshot_to_result(snapshot)
            export_to_excel(
                scheduled_for_export, excel_path,
                actual_entries=actual_entries, actual_stats=actual_stats,
                annual_totals=annual_totals, swap_history=swap_history,
            )
            pdf_export.export_to_pdf(
                scheduled_for_export, pdf_path,
                actual_entries=actual_entries, actual_stats=actual_stats,
                annual_totals=annual_totals, swap_history=swap_history,
            )
            st.success("最新の実績・年間集計・交代履歴を反映してExcel/PDFを再生成しました")
            st.rerun()

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        if excel_path.exists():
            with open(excel_path, "rb") as f:
                st.download_button(
                    "⬇️ Excelをダウンロード",
                    data=f.read(),
                    file_name=excel_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
    with dl_col2:
        if pdf_path.exists():
            with open(pdf_path, "rb") as f:
                st.download_button(
                    "⬇️ PDFをダウンロード", data=f.read(), file_name=pdf_path.name, mime="application/pdf"
                )

    if snapshot:
        share_text = notify.build_schedule_share_text(year, month, snapshot["stats"])
        st.link_button("📱 LINEで勤務表確定をお知らせする", notify.build_line_share_url(share_text))

    st.divider()
    if st.button("🔓 確定を解除して再作成する(内容を修正したい場合)", type="secondary"):
        ds.clear_finalized(year, month)
        st.success("確定を解除しました。勤務表を再作成できます。")
        st.rerun()

else:
    # ------------------------------------------------------------
    # 未確定: 作成・見直し・確定のフロー
    # ------------------------------------------------------------
    st.caption(
        "入力状況: "
        + " / ".join(f"{m['name']}: {submission_stats.get(m['name'], 0)}日入力済み" for m in members)
    )

    max_time = st.slider("最適化の計算時間(秒)", min_value=5, max_value=120, value=30, step=5)

    if st.button("🚀 勤務表を作成する(下書き)", type="primary"):
        with st.spinner("最適化を実行中です..."):
            member_models = ds.get_members_as_models()
            unavailabilities = ds.get_unavailability_objects(year, month)
            gaikobu_days_set = ds.get_gaikobu_days_as_dates(year, month)
            annual_actual_totals = ds.get_annual_actual_own_totals(year)
            options = OptimizerOptions(
                max_time_seconds=float(max_time),
                gaikobu_days=gaikobu_days_set,
                annual_actual_totals=annual_actual_totals,
            )
            optimizer = OnCallOptimizer(
                year=year, month=month, members=member_models, unavailabilities=unavailabilities, options=options,
            )
            result = optimizer.solve()
            st.session_state["schedule_result"] = result
            if result.status in ("OPTIMAL", "FEASIBLE"):
                ds.save_schedule_snapshot(year, month, _result_to_snapshot(result))

    result = st.session_state.get("schedule_result")
    snapshot = ds.load_schedule_snapshot(year, month) if result is None else None

    # 確定・Excel/PDF出力に使う実体(セッションに無ければスナップショットから再構築)
    effective_result: Optional[ScheduleResult] = None
    if result is not None:
        effective_result = result
    elif snapshot is not None and snapshot["status"] in ("OPTIMAL", "FEASIBLE"):
        effective_result = _snapshot_to_result(snapshot)

    display_entries, display_stats, display_status, display_warnings = None, None, None, []
    if result is not None:
        display_status = result.status
        display_warnings = result.warnings
        if result.status in ("OPTIMAL", "FEASIBLE"):
            snap = _result_to_snapshot(result)
            display_entries, display_stats = snap["entries"], snap["stats"]
    elif snapshot is not None:
        display_status = snapshot["status"]
        display_warnings = snapshot.get("warnings", [])
        display_entries, display_stats = snapshot["entries"], snapshot["stats"]

    if display_status is not None:
        if display_status not in ("OPTIMAL", "FEASIBLE"):
            st.error("勤務表を作成できませんでした(条件が厳しすぎる可能性があります)。")
            for w in display_warnings:
                st.write(f"- {w}")
        else:
            st.success(f"勤務表(下書き)を作成しました(status: {display_status})")
            if display_warnings:
                with st.expander("⚠️ 警告"):
                    for w in display_warnings:
                        st.write(f"- {w}")

            st.subheader("📅 勤務表(カレンダー表示・下書き)")
            uc.render_schedule_calendar(display_entries, year, month)

            st.subheader("📊 集計表")
            _render_stats_table(display_stats)

            st.subheader("⬇️ Excel出力(下書き)")
            tmp_path = _PROJECT_ROOT / "output" / f"schedule_{year}_{month:02d}.xlsx"
            if effective_result is not None:
                export_to_excel(effective_result, tmp_path)
            if tmp_path.exists():
                with open(tmp_path, "rb") as f:
                    st.download_button(
                        label="勤務表(Excel)をダウンロード",
                        data=f.read(),
                        file_name=f"oncall_schedule_{year}_{month:02d}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

            st.divider()
            st.subheader("✅ 勤務表の確定")
            st.caption(
                "内容を確認し、問題なければ確定してください。確定するとExcel・PDFが自動生成され、"
                "LINEでお知らせするための文章も作成されます。"
            )
            if st.button("✅ この勤務表を確定する", type="primary"):
                if effective_result is None:
                    st.error("下書きの再作成が必要です。「勤務表を作成する」を押してください。")
                else:
                    ds.save_schedule_snapshot(year, month, _result_to_snapshot(effective_result))
                    ds.mark_finalized(year, month)  # ここで実績(actual_assignments)が予定からコピーされる

                    excel_path = _PROJECT_ROOT / "output" / f"schedule_{year}_{month:02d}.xlsx"
                    pdf_path = _PROJECT_ROOT / "output" / f"schedule_{year}_{month:02d}.pdf"
                    actual_entries, actual_stats, annual_totals, swap_history = _get_export_extras(year, month)
                    export_to_excel(
                        effective_result, excel_path,
                        actual_entries=actual_entries, actual_stats=actual_stats,
                        annual_totals=annual_totals, swap_history=swap_history,
                    )
                    pdf_export.export_to_pdf(
                        effective_result, pdf_path,
                        actual_entries=actual_entries, actual_stats=actual_stats,
                        annual_totals=annual_totals, swap_history=swap_history,
                    )

                    sync = ds.get_auto_sync_settings()
                    if sync["enabled"] and sync["spreadsheet_key"] and ssync.is_configured():
                        try:
                            client = ssync.get_client(sync["spreadsheet_key"])
                            if client is None:
                                st.warning("スプレッドシートへの自動同期に失敗しました: 接続できませんでした")
                            else:
                                client.write_schedule(effective_result)
                        except Exception as e:  # noqa: BLE001
                            st.warning(f"スプレッドシートへの自動同期に失敗しました: {e}")

                    st.success("勤務表を確定しました。Excel・PDFを出力しました。")
                    st.rerun()

            st.subheader("🔗 Googleスプレッドシートへ手動で書き込み")
            if not ssync.is_configured():
                st.caption("Googleスプレッドシート連携が設定されていません。")
            else:
                spreadsheet_key = st.text_input(
                    "書き込み先スプレッドシートのID",
                    value=ds.get_auto_sync_settings()["spreadsheet_key"],
                    key="manual_sheet_key_input",
                )
                if st.button("スプレッドシートへ書き込む"):
                    if effective_result is None:
                        st.error("下書きの再作成が必要です。「勤務表を作成する」を押してください。")
                    else:
                        try:
                            client = ssync.get_client(spreadsheet_key)
                            if client is None:
                                st.error("書き込みに失敗しました: 接続できませんでした")
                            else:
                                client.write_schedule(effective_result)
                                st.success("スプレッドシートに書き込みました")
                        except Exception as e:  # noqa: BLE001
                            st.error(f"書き込みに失敗しました: {e}")

st.divider()

# ======================================================================
# 8. 実績勤務表・実績の手動修正
# ======================================================================
st.header("8. 実績勤務表・実績の手動修正")

actual_snapshot = ds.load_actual_snapshot(year, month)

if not ds.is_finalized(year, month) or actual_snapshot is None:
    st.info("この月の勤務表がまだ確定されていません。「7. 勤務表の作成・確定」で確定すると、実績(actual_assignments)が予定と同じ内容で作成されます。")
else:
    st.caption(
        "実績は、勤務交代の承認や下記の手動修正によって、予定(scheduled_assignments)とは"
        "独立して更新されます。年間集計・翌月以降の均等化には必ずこの実績が使われます。"
    )
    st.subheader("📅 実績勤務表(カレンダー表示)")
    uc.render_schedule_calendar(actual_snapshot["entries"], year, month)
    st.subheader("📊 月間集計(実績ベース)")
    _render_stats_table(actual_snapshot["stats"])

    st.subheader("✏️ 実績の手動修正")
    st.caption(
        "急な交代・病欠・LINE上での交代済み・外部バイトキャンセルなど、"
        "個人間の交代機能を使わずに実績を直接修正したい場合はこちらを使ってください。"
        "修正内容は履歴として記録されます。"
    )
    day_options = [e["date"] for e in actual_snapshot["entries"]]
    member_names_for_edit = [m["name"] for m in ds.get_members()]
    with st.form("manual_actual_edit_form"):
        edit_col1, edit_col2, edit_col3 = st.columns(3)
        with edit_col1:
            edit_date = st.selectbox("日付", day_options)
        with edit_col2:
            edit_slot_label = st.selectbox("勤務種別", ["日中", "夜間", "外部バイト"])
        with edit_col3:
            edit_new_member = st.selectbox("修正後の担当者", ["(未割当にする)"] + member_names_for_edit)
        edit_reason = st.text_input("修正理由(例: 病欠のため交代、LINE上で交代済みなど)")
        edit_submitted = st.form_submit_button("実績を修正する")
        if edit_submitted:
            slot_type_map = {"日中": "day", "夜間": "night", "外部バイト": "gaikobu"}
            slot_type = slot_type_map[edit_slot_label]
            new_member_value = None if edit_new_member == "(未割当にする)" else edit_new_member
            if not edit_reason.strip():
                st.error("修正理由を入力してください")
            else:
                conflict = None
                if new_member_value:
                    conflict = ds.check_actual_conflict(year, month, edit_date, slot_type, new_member_value)
                if conflict:
                    st.error(f"修正できません: {conflict}")
                else:
                    success = ds.edit_actual_assignment(
                        year, month, edit_date, slot_type, new_member_value, edit_reason.strip(), edited_by="admin"
                    )
                    if success:
                        st.success("実績を修正しました")
                        st.rerun()
                    else:
                        st.error("修正に失敗しました(該当日が見つかりません)")

    edit_history_this_month = ds.get_actual_edit_history(year, month)
    if edit_history_this_month:
        with st.expander(f"📝 この月の修正履歴({len(edit_history_this_month)}件)"):
            hist_rows = [
                {
                    "日付": h["date"],
                    "勤務種別": ds.SLOT_TYPE_LABEL.get(h["slot_type"], h["slot_type"]),
                    "修正前": h["old_member"] or "(未割当)",
                    "修正後": h["new_member"] or "(未割当)",
                    "理由": h["reason"],
                    "修正者": h["edited_by"],
                    "修正日時": h["edited_at"][:16].replace("T", " "),
                }
                for h in edit_history_this_month
            ]
            st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)

st.divider()

# ======================================================================
# 9. 年間実績集計
# ======================================================================
st.header("9. 年間実績集計")
st.caption(
    "実績確定済みの月だけを対象に、自院オンコール(日中+夜間)を中心とした年間実績を集計します。"
    "外部バイトは義務ではないため、別枠(参考値)として表示しています。"
)

annual_year = st.number_input("集計対象の年", min_value=2020, max_value=2100, value=year, step=1, key="annual_year_input")
annual_totals_display = ds.get_annual_actual_totals(int(annual_year))
if annual_totals_display:
    annual_rows = [
        {
            "名前": name,
            "自院日中 実績": s["day"],
            "自院夜間 実績": s["night"],
            "自院合計 実績": s["total"],
            "外部バイト 実績": s["gaikobu"],
            "総勤務 実績": s["grand_total"],
        }
        for name, s in annual_totals_display.items()
    ]
    st.dataframe(pd.DataFrame(annual_rows), use_container_width=True, hide_index=True)

    finalized_months = [m for m in range(1, 13) if ds.is_actual_finalized(int(annual_year), m)]
    st.caption(f"実績確定済みの月: {', '.join(f'{m}月' for m in finalized_months) if finalized_months else '(まだありません)'}")
else:
    st.info("メンバーが登録されていません。")

st.divider()

# ======================================================================
# 10. 交代履歴・未処理の交代申請
# ======================================================================
st.header("10. 交代履歴・未処理の交代申請")
st.caption("勤務交代は当事者2人(交代依頼者・交代相手)の間で承認/却下されます。管理者の承認操作は不要で、ここでは履歴の確認のみ行います。")

pending_requests = ds.get_swap_requests(status=ds.SWAP_STATUS_PENDING)
st.subheader(f"⏳ 未処理の交代申請({len(pending_requests)}件)")
if pending_requests:
    pending_rows = [
        {
            "日付": r["date"],
            "勤務種別": ds.SLOT_TYPE_LABEL.get(r["slot_type"], r["slot_type"]),
            "元の担当者": r["from_member"],
            "交代相手": r["to_member"],
            "申請日時": r["requested_at"][:16].replace("T", " "),
        }
        for r in pending_requests
    ]
    st.dataframe(pd.DataFrame(pending_rows), use_container_width=True, hide_index=True)
else:
    st.caption("現在、未処理の交代申請はありません。")

st.subheader("📜 交代履歴(全体)")
all_swap_requests = ds.get_swap_requests()
if all_swap_requests:
    history_rows = [
        {
            "日付": r["date"],
            "勤務種別": ds.SLOT_TYPE_LABEL.get(r["slot_type"], r["slot_type"]),
            "元の担当者": r["from_member"],
            "交代後の担当者": r["to_member"],
            "申請日時": r["requested_at"][:16].replace("T", " "),
            "承認日時": r["approved_at"][:16].replace("T", " ") if r.get("approved_at") else "-",
            "状態": ds.SWAP_STATUS_LABEL.get(r["status"], r["status"]),
        }
        for r in all_swap_requests
    ]
    st.dataframe(pd.DataFrame(history_rows), use_container_width=True, hide_index=True)
else:
    st.caption("交代履歴はまだありません。")

st.divider()

# ======================================================================
# 11. 月末の実績確定
# ======================================================================
st.header("11. 月末の実績確定")
st.caption(
    "月末に実績を確定すると、この月のactual_assignmentsが年間実績集計に反映されるようになります。"
    "確定後も管理者は実績を修正できます(手順8の修正機能は管理者専用のため)。"
)

if actual_snapshot is None:
    st.info("この月の勤務表がまだ確定されていないため、実績確定はできません。先に「7. 勤務表の作成・確定」を行ってください。")
elif ds.is_actual_finalized(year, month):
    info = ds.get_actual_finalized_info(year, month)
    finalized_at_str = (info.get("actual_finalized_at", "") if info else "")[:16].replace("T", " ")
    st.success(f"✅ この月の実績は確定済みです(確定日時: {finalized_at_str})")
    if st.button("🔓 実績確定を解除する", type="secondary"):
        ds.clear_actual_finalized(year, month)
        st.success("実績確定を解除しました")
        st.rerun()
else:
    if st.button("✅ この月の実績を確定する", type="primary"):
        ds.mark_actual_finalized(year, month)
        st.success("この月の実績を確定しました。年間実績集計に反映されます。")
        st.rerun()
