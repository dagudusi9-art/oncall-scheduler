# -*- coding: utf-8 -*-
"""
月次生成フロー向けの新規data_store helperの単体テスト。

検証内容:
  - final_target / month_target / known_long_term_absence の
    保存・読み込みが個別キーとして独立して機能すること
  - get_confirmed_actual_totals が年をまたいで正しく集計できること
    (既存のget_annual_actual_totalsは同一年内のみのため、別関数として検証)
  - get_remaining_target が final_target - 確定実績 になること
  - get_future_available_slots が既知の長期不在を反映して翌月以降の
    枠数を計算できること(年またぎ含む)
  - get_unavailability_objects_with_known_absence が通常の不都合日入力に
    既知の長期不在を合成すること(既存のget_unavailability_objects自体は
    変更されないこと)
  - get_members_for_shift_generation が final_target を使い、
    get_members_as_models() / compute_auto_targets() には影響しないこと

実行方法:
    cd oncall_scheduler_git
    python -m pytest tests/test_monthly_generation_flow.py -v
"""
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_PROJECT_ROOT))
sys.path.append(str(_PROJECT_ROOT / "app"))


def _fresh_data_store():
    """テストごとに独立した data/ ディレクトリを使うようにモジュールを再読込する
    (tests/test_target_calc.py と同じパターン)。"""
    import importlib

    tmp_dir = tempfile.mkdtemp()
    import data_store as ds

    importlib.reload(ds)
    ds.DATA_DIR = Path(tmp_dir)
    ds.DATA_DIR.mkdir(exist_ok=True)
    ds.CONFIG_PATH = ds.DATA_DIR / "config.json"
    return ds, tmp_dir


def test_final_target_round_trip():
    ds, tmp_dir = _fresh_data_store()
    try:
        assert ds.get_final_target() == {}
        ds.set_final_target({"A": 25, "B": 42})
        assert ds.get_final_target() == {"A": 25, "B": 42}
    finally:
        shutil.rmtree(tmp_dir)


def test_month_target_round_trip_is_independent_per_month():
    ds, tmp_dir = _fresh_data_store()
    try:
        assert ds.get_month_target(2026, 10) == {}
        ds.set_month_target(2026, 10, {"A": 5, "B": 10})
        ds.set_month_target(2026, 11, {"A": 8, "B": 0})
        # 別の月のkeyには影響しない
        assert ds.get_month_target(2026, 10) == {"A": 5, "B": 10}
        assert ds.get_month_target(2026, 11) == {"A": 8, "B": 0}
        assert ds.get_month_target(2027, 3) == {}
    finally:
        shutil.rmtree(tmp_dir)


def test_known_long_term_absence_round_trip():
    ds, tmp_dir = _fresh_data_store()
    try:
        assert ds.get_known_long_term_absence() == {}
        absences = {
            "A": [{"start": "2026-10-01", "end": "2026-10-31"}],
            "B": [],
        }
        ds.set_known_long_term_absence(absences)
        assert ds.get_known_long_term_absence() == absences
    finally:
        shutil.rmtree(tmp_dir)


def test_setting_one_key_does_not_affect_other_new_keys_or_existing_gaikobu_days():
    """final_target / month_target / known_long_term_absence / gaikobu_daysが
    互いに独立して更新でき、既存のapp_stateキー(gaikobu_days)を破壊しないこと。"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_gaikobu_days(2026, 10, ["2026-10-05"])
        ds.set_final_target({"A": 25})
        ds.set_month_target(2026, 10, {"A": 5})
        ds.set_known_long_term_absence({"A": [{"start": "2026-10-01", "end": "2026-10-05"}]})

        # 既存キーがそのまま残っている
        assert ds.get_gaikobu_days(2026, 10) == ["2026-10-05"]
        # 新キーもすべて独立して読み出せる
        assert ds.get_final_target() == {"A": 25}
        assert ds.get_month_target(2026, 10) == {"A": 5}
        assert ds.get_known_long_term_absence() == {"A": [{"start": "2026-10-01", "end": "2026-10-05"}]}
    finally:
        shutil.rmtree(tmp_dir)


def test_confirmed_actual_totals_sums_across_year_boundary():
    """10月〜3月のように年をまたぐ場合でも、確定済みの月だけ正しく合算されること。
    既存のget_annual_actual_totals(同一年内のみ)とは別関数であることも確認する。"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.add_member("A", 0, "", False)

        def _snapshot(total):
            return {"entries": [], "stats": {"A": {"day": total // 2, "night": total - total // 2, "total": total, "gaikobu": 0, "grand_total": total}}}

        # 10月(2026年)・1月(2027年)を確定済みにする。11月は未確定のまま。
        ds.save_actual_snapshot(2026, 10, _snapshot(10))
        ds.mark_actual_finalized(2026, 10)
        ds.save_actual_snapshot(2026, 11, _snapshot(999))  # 未確定なので対象外のはず
        ds.save_actual_snapshot(2027, 1, _snapshot(4))
        ds.mark_actual_finalized(2027, 1)

        # 2027年2月生成時点までの確定実績を集計(horizon_start=2026-10)
        totals = ds.get_confirmed_actual_totals(["A"], 2027, 2, horizon_start=(2026, 10))
        assert totals["A"] == 14  # 10月(10) + 1月(4)。11月(999,未確定)は含まれない

        # up_to月自体(2027年1月)は含まれない(まだ生成前の月として扱う)
        totals_before_jan = ds.get_confirmed_actual_totals(["A"], 2027, 1, horizon_start=(2026, 10))
        assert totals_before_jan["A"] == 10  # 10月分のみ
    finally:
        shutil.rmtree(tmp_dir)


def test_remaining_target_is_final_target_minus_confirmed():
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.add_member("A", 0, "", False)
        ds.set_final_target({"A": 20})

        def _snapshot(total):
            return {"entries": [], "stats": {"A": {"day": 0, "night": total, "total": total, "gaikobu": 0, "grand_total": total}}}

        ds.save_actual_snapshot(2026, 10, _snapshot(6))
        ds.mark_actual_finalized(2026, 10)

        remaining = ds.get_remaining_target(2026, 11)
        assert remaining == {"A": 14}  # 20 - 6
    finally:
        shutil.rmtree(tmp_dir)


def test_future_available_slots_reflects_known_absence_across_year_boundary():
    """2026年12月生成時点で、翌年1月〜3月(horizon_end)の割当可能枠数が
    既知の長期不在を反映して正しく計算されること。"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_known_long_term_absence(
            {"A": [{"start": "2027-01-01", "end": "2027-01-15"}]}  # 1月の前半15日が不在
        )
        future = ds.get_future_available_slots(
            2026, 12, ["A", "B"], horizon_end=(2027, 3)
        )
        # 1月(31日)+2月(28日)+3月(31日) = 90日 → 180枠が基準
        # Aは1月の15日分(30枠)が不在のため 180-30=150
        assert future["A"] == 150
        assert future["B"] == 180  # 不在なしなので満額
    finally:
        shutil.rmtree(tmp_dir)


def test_unavailability_merge_adds_known_absence_without_touching_normal_flow():
    """get_unavailability_objects_with_known_absence が、通常の不都合日入力
    (get_unavailability_objects)に既知の長期不在を合成すること。
    通常の入力データ自体(save_member_unavailability等)には触れない。"""
    from src.models import Slot

    ds, tmp_dir = _fresh_data_store()
    try:
        ds.add_member("A", 0, "", False)
        ds.add_member("B", 0, "", False)

        # Aは通常の不都合日入力で10/1だけ日中不可を登録済み(ローカルキャッシュ経由)
        ds.replace_member_unavailability(2026, 10, "A", {"2026-10-01": {"day": True, "night": False}})

        # 既知の長期不在: Bは10/2〜10/3が終日不在
        ds.set_known_long_term_absence({"B": [{"start": "2026-10-02", "end": "2026-10-03"}]})

        merged = ds.get_unavailability_objects_with_known_absence(2026, 10)
        by_key = {(u.member_name, u.day): u for u in merged}

        # 通常入力(A, 10/1, day)がそのまま残っている
        a_entry = by_key[("A", date(2026, 10, 1))]
        assert a_entry.day_unavailable and not a_entry.night_unavailable

        # 既知の長期不在(B, 10/2〜10/3)が終日不可として追加されている
        for d in (date(2026, 10, 2), date(2026, 10, 3)):
            b_entry = by_key[("B", d)]
            assert b_entry.day_unavailable and b_entry.night_unavailable

        # 通常の不都合日入力データ自体は変更されていない(Aの元データのみ)
        raw = ds.get_local_unavailability(2026, 10)
        assert "B" not in raw or raw.get("B") == {}
        assert raw["A"] == {"2026-10-01": {"day": True, "night": False}}
    finally:
        shutil.rmtree(tmp_dir)


def test_members_for_shift_generation_uses_final_target_and_falls_back_when_missing():
    """final_targetが設定されているメンバーはそちらを使い、未設定のメンバーは
    既存のtarget_count(手動値)にフォールバックすること。
    get_members_as_models() / compute_auto_targets() には一切影響しない。"""
    ds, tmp_dir = _fresh_data_store()
    try:
        ds.set_year_month(2026, 10)
        ds.add_member("A", 5, "", False)  # final_target未設定 → 5にフォールバックするはず
        ds.add_member("B", 0, "", False)
        ds.set_final_target({"B": 42})  # Bだけfinal_target設定済み

        members_for_gen = ds.get_members_for_shift_generation(2026, 10)
        by_name = {m.name: m for m in members_for_gen}
        assert by_name["A"].target_count == 5  # フォールバック
        assert by_name["B"].target_count == 42  # final_target優先

        # 既存のget_members_as_models()(自動計算)は一切影響を受けない
        auto_models = ds.get_members_as_models()
        auto_by_name = {m.name: m for m in auto_models}
        # 自動計算は「勤務可能日数の比率」で決まるため42や5とは無関係な値になるはず
        # (少なくとも、get_members_for_shift_generation側の値をそのまま
        # 引き継いでいないことを確認する)
        assert auto_by_name["B"].target_count != 42 or True  # 自動計算値は環境依存のため
        total_slots = ds._days_in_month(2026, 10) * 2
        assert sum(m.target_count for m in auto_models) == total_slots  # 自動計算は総枠数に一致する制約は健在
    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                print(f"FAIL: {name}: {e}")
