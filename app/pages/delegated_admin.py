# -*- coding: utf-8 -*-
"""
月限定代理管理者(delegated admin)専用ページ

通常管理者画面(admin.py)の権限制限版ではなく、完全に独立した専用ページ。
通常管理者が発行した、特定の年月だけに限定されたトークンを持つ人だけが
アクセスでき、その年月に関する最小限の操作
(入力状況確認・目標回数の閲覧・勤務表生成・確定・Excel/PDF出力)
のみを行える。

セキュリティ上の要点:
- 対象年月はこのページ自身がトークンレコードから取得した値のみを使う。
  URLのクエリパラメータや画面上のUIから年月を受け取る要素は存在しない
  (admin.pyのような年月selectbox・年月保存ボタンは一切無い)。
- トークンの有効性(存在する/revokedでない)は、このページの先頭で
  毎回このページ自身が検証する。サイドバーに表示しない・リンクを
  作らないといった「UIで隠す」対策だけに頼らない。
- 外部バイト設定・実績編集・メンバー管理・member/delegated token管理・
  final target変更・month target変更を行うコードはこのファイルの
  どこにも存在しない(admin.pyをimportして一部を隠す、という実装には
  していない)。
- 勤務表生成・snapshot保存・確定・Excel/PDF出力は、admin.pyと同じ
  data_store.py / src.optimizer / src.excel_export / pdf_export の
  既存関数をそのまま呼び出す(独自の並行実装は行わない)。
"""
import calendar as _cal
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

_APP_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _APP_DIR.parent
for p in reversed((_APP_DIR, _PROJECT_ROOT)):
    if str(p) in sys.path:
        sys.path.remove(str(p))
    sys.path.insert(0, str(p))

import data_store as ds  # noqa: E402
import delegated_admin_store as das  # noqa: E402
import pdf_export  # noqa: E402
import ui_common as uc  # noqa: E402
from src.excel_export import export_to_excel  # noqa: E402
from src.optimizer import OnCallOptimizer, OptimizerOptions  # noqa: E402
from src.models import Slot  # noqa: E402


st.title("🛠️ 月限定代理管理者")

# ======================================================================
# 0. トークン検証(この画面自身が毎回検証する。対象年月もここでのみ確定する)
# ======================================================================
raw_token = st.query_params.get("dtoken")
token_info = das.validate_token(raw_token) if raw_token else None

if token_info is None:
    st.error("このリンクは無効か、失効しています。発行した管理者にご確認ください。")
    st.stop()

year: int = token_info["year"]
month: int = token_info["month"]
label: str = token_info["label"]
token_hash: str = token_info["token_hash"]

# 初回アクセス(このブラウザセッションで初めて有効なトークンを確認できた
# タイミング)のみ access ログを残す。Streamlitはボタン操作等のたびに
# スクリプト全体が再実行される(rerun)ため、session_stateでガードしないと
# 操作のたびにログが増え続けてしまう。
_access_logged_key = f"delegated_admin_access_logged_{token_hash}"
if not st.session_state.get(_access_logged_key):
    das.log_action(token_info, "login", {})
    st.session_state[_access_logged_key] = True

st.info(f"対象：**{year}年{month}月 代理管理者**（{label}）")
st.caption("この画面では対象年月を変更できません。他の年月の作業が必要な場合は通常管理者にご連絡ください。")

st.divider()

# ======================================================================
# 1. 入力状況確認
# ======================================================================
st.header("1. 入力状況確認")

members = ds.get_members()
member_names_all = [m["name"] for m in members]
submission_stats = ds.get_submission_stats(year, month)
last_updated = ds.get_last_updated(year, month)


def _format_last_updated(name: str) -> str:
    raw = last_updated.get(name)
    if not raw:
        return "-"
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


if member_names_all:
    status_rows = [
        {
            "名前": name,
            "状態": "✅ 入力済み" if submission_stats.get(name, 0) > 0 else "⚠️ 未入力",
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
    st.caption("メンバーが登録されていません。")

st.divider()

# ======================================================================
# 2. 各メンバーの不都合日(閲覧のみ・変更は不可)
# ======================================================================
st.header("2. 各メンバーの不都合日(閲覧のみ)")
st.caption(
    "内容の確認のみ行えます。この画面から他人の不都合日を書き換えることはできません。"
    "誰かに不都合日を変更してもらう必要がある場合は、本人に自分専用の入力URLから"
    "修正してもらった上で、下記「4. 勤務表の作成・確定」から再生成してください。"
)

if member_names_all:
    raw_unavail = ds.load_unavailability_raw(year, month)
    selected_member = st.selectbox("メンバーを選択", member_names_all, key="delegated_unavail_member")
    day_map = raw_unavail.get(selected_member, {})
    n_days = _cal.monthrange(year, month)[1]
    unavail_rows = []
    for day_num in range(1, n_days + 1):
        d_str = f"{year:04d}-{month:02d}-{day_num:02d}"
        state = ds.state_from_flags(day_map.get(d_str))
        unavail_rows.append({"日付": f"{month}/{day_num}", "状態": ds.STATE_LABEL[state]})
    st.dataframe(pd.DataFrame(unavail_rows), use_container_width=True, hide_index=True, height=320)
else:
    st.caption("メンバーが登録されていません。")

st.divider()

# ======================================================================
# 3. 割当目標回数(閲覧のみ・final target / month target とも変更不可)
# ======================================================================
st.header("3. 割当目標回数(閲覧のみ)")
st.caption(
    "final target(3月末までの最終目標)・月別目標はいずれも通常管理者が"
    "別途設定した値です。この画面から変更することはできません。"
)

final_target = ds.get_final_target()
month_target = ds.get_month_target(year, month)
if final_target or month_target:
    target_names = sorted(set(final_target.keys()) | set(month_target.keys()))
    target_rows = [
        {
            "名前": name,
            "final target(3月末まで)": final_target.get(name, "-"),
            f"{year}年{month}月の月別目標": month_target.get(name, "-"),
        }
        for name in target_names
    ]
    st.dataframe(pd.DataFrame(target_rows), use_container_width=True, hide_index=True)
else:
    st.info("final target・月別目標はまだ設定されていません(未設定でも通常の目標回数で勤務表生成は動作します)。")

st.divider()

# ======================================================================
# 4. 勤務表の作成・確定
#    (admin.pyと同一の data_store / optimizer / export ロジックを再利用)
# ======================================================================
st.header("4. 勤務表の作成・確定")

if not members:
    st.warning("メンバーが登録されていません。通常管理者にご連絡ください。")
    st.stop()

finalized_info = ds.get_finalized_info(year, month)


def _get_export_extras(target_year: int, target_month: int):
    actual_snapshot = ds.load_actual_snapshot(target_year, target_month)
    actual_entries = actual_snapshot["entries"] if actual_snapshot else None
    actual_stats = actual_snapshot["stats"] if actual_snapshot else None
    annual_totals = ds.get_annual_actual_totals(target_year)
    swap_history = ds.get_swap_requests(year=target_year, month=target_month)
    return actual_entries, actual_stats, annual_totals, swap_history


if finalized_info:
    # ------------------------------------------------------------
    # 確定済み: 保存済みのスナップショット・出力ファイルを表示するのみ
    # ------------------------------------------------------------
    finalized_at = finalized_info.get("finalized_at", "")
    st.success(f"✅ この月の勤務表は確定済みです(確定日時: {finalized_at[:16].replace('T', ' ')})")

    snapshot = ds.load_schedule_snapshot(year, month)
    if snapshot:
        st.subheader("📅 予定勤務表(カレンダー表示)")
        uc.render_schedule_calendar(snapshot["entries"], year, month)
        st.subheader("📊 月間集計(予定ベース)")
        uc.render_stats_table(snapshot["stats"])

    excel_path = _PROJECT_ROOT / "output" / f"schedule_{year}_{month:02d}.xlsx"
    pdf_path = _PROJECT_ROOT / "output" / f"schedule_{year}_{month:02d}.pdf"

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        if excel_path.exists():
            with open(excel_path, "rb") as f:
                st.download_button(
                    "⬇️ Excelをダウンロード",
                    data=f.read(),
                    file_name=excel_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="delegated_excel_download_finalized",
                )
    with dl_col2:
        if pdf_path.exists():
            with open(pdf_path, "rb") as f:
                st.download_button(
                    "⬇️ PDFをダウンロード",
                    data=f.read(),
                    file_name=pdf_path.name,
                    mime="application/pdf",
                    key="delegated_pdf_download_finalized",
                )

else:
    # ------------------------------------------------------------
    # 未確定: 作成・見直し・確定のフロー
    # ------------------------------------------------------------
    st.caption(
        "入力状況: "
        + " / ".join(f"{m['name']}: {submission_stats.get(m['name'], 0)}日入力済み" for m in members)
    )

    max_time = st.slider(
        "最適化の計算時間(秒)", min_value=5, max_value=180, value=150, step=5, key="delegated_max_time"
    )

    if st.button("🚀 勤務表を作成する(下書き)", type="primary", key="delegated_generate"):
        with st.spinner("最適化を実行中です..."):
            member_models = ds.get_members_for_shift_generation(year, month)
            unavailabilities = ds.get_unavailability_objects_with_known_absence(year, month)
            gaikobu_days_set = ds.get_gaikobu_days_as_dates(year, month)
            annual_actual_totals = ds.get_annual_actual_own_totals(year)

            member_names = [m.name for m in member_models]
            month_target_now = ds.get_month_target(year, month)
            remaining_target = ds.get_remaining_target(year, month)
            future_available_slots = ds.get_future_available_slots(year, month, member_names)

            options = OptimizerOptions(
                max_time_seconds=float(max_time),
                gaikobu_days=gaikobu_days_set,
                annual_actual_totals=annual_actual_totals,
                month_target=month_target_now,
                remaining_target=remaining_target,
                future_available_slots=future_available_slots,
            )
            optimizer = OnCallOptimizer(
                year=year, month=month, members=member_models, unavailabilities=unavailabilities, options=options,
            )
            result = optimizer.solve()

            # 完全な勤務表が物理的に作れない場合だけ、未割当枠を最小限許した
            # 部分勤務表を作る(admin.pyの下書き作成ロジックと同一)。
            if result.status not in ("OPTIMAL", "FEASIBLE"):
                partial_options = OptimizerOptions(
                    max_time_seconds=float(max_time),
                    gaikobu_days=gaikobu_days_set,
                    annual_actual_totals=annual_actual_totals,
                    month_target=month_target_now,
                    remaining_target=remaining_target,
                    future_available_slots=future_available_slots,
                    allow_unassigned=True,
                )
                partial_optimizer = OnCallOptimizer(
                    year=year, month=month, members=member_models,
                    unavailabilities=unavailabilities, options=partial_options,
                )
                partial_result = partial_optimizer.solve()
                if partial_result.status in ("OPTIMAL", "FEASIBLE"):
                    partial_result.warnings.insert(
                        0, "完全な勤務表を作成できなかったため、埋められない枠だけ未割当とした下書きを作成しました。"
                    )
                    result = partial_result

            st.session_state["delegated_schedule_result"] = result
            if result.status in ("OPTIMAL", "FEASIBLE"):
                ds.save_schedule_snapshot(year, month, uc.result_to_snapshot(result))

            das.log_action(
                token_info,
                "generate_draft",
                {"status": result.status, "warning_count": len(result.warnings)},
            )

    result = st.session_state.get("delegated_schedule_result")
    snapshot = ds.load_schedule_snapshot(year, month) if result is None else None

    effective_result = None
    if result is not None:
        effective_result = result
    elif snapshot is not None and snapshot["status"] in ("OPTIMAL", "FEASIBLE"):
        effective_result = uc.snapshot_to_result(snapshot)

    display_entries, display_stats, display_status, display_warnings = None, None, None, []
    if result is not None:
        display_status = result.status
        display_warnings = result.warnings
        if result.status in ("OPTIMAL", "FEASIBLE"):
            snap = uc.result_to_snapshot(result)
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

            has_unassigned = any(e.get("day") is None or e.get("night") is None for e in display_entries)
            if has_unassigned:
                st.warning(
                    "この枠を担当できるメンバーが不足しています。"
                    "該当メンバーに不都合日の変更を依頼してから再生成してください。"
                )

            st.subheader("📅 勤務表(カレンダー表示・下書き)")
            uc.render_schedule_calendar(display_entries, year, month)

            st.subheader("📊 集計表")
            uc.render_stats_table(display_stats)

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
                        key="delegated_excel_download_draft",
                    )

            st.divider()
            st.subheader("✅ 勤務表の確定")
            st.caption("内容を確認し、問題なければ確定してください。確定するとExcel・PDFが自動生成されます。")
            if st.button("✅ この勤務表を確定する", type="primary", key="delegated_finalize"):
                if effective_result is None:
                    st.error("下書きの再作成が必要です。「勤務表を作成する」を押してください。")
                elif any(
                    e.assignments.get(Slot.DAY) is None or e.assignments.get(Slot.NIGHT) is None
                    for e in effective_result.entries
                ):
                    st.error("未割当のDay/Night枠が残っています。メンバーに不都合日の変更を依頼してから再生成してください。")
                else:
                    ds.save_schedule_snapshot(year, month, uc.result_to_snapshot(effective_result))
                    ds.mark_finalized(year, month)  # 実績(actual_assignments)が予定からコピーされる

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

                    das.log_action(token_info, "finalize", {"status": effective_result.status})

                    st.success("勤務表を確定しました。Excel・PDFを出力しました。")
                    st.rerun()
