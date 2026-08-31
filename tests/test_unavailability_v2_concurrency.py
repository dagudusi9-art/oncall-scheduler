# -*- coding: utf-8 -*-
"""
不都合日 v2 保存方式の検証テスト。

Google Sheetsへの実アクセスは行わず、gspreadのWorksheetを模したインメモリの
フェイクに差し替えて、sheets_backend.py の upsert_keyed_row / append_row_safe /
read_table を通した挙動を検証する。これにより、実際のSheets API呼び出し
シーケンス(get_all_values→対象行だけをupdate、または末尾にappend)を
そのまま再現しつつ、ユーザーの要求した同時保存シナリオを再現できる。
"""
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_PROJECT_ROOT))
sys.path.append(str(_PROJECT_ROOT / "app"))


class FakeWorksheet:
    """gspreadのWorksheetの必要最小限を模したフェイク。
    実際のシートと同様、行は「配列のリスト」として保持し、
    ws.clear()が呼ばれたかどうかを記録できるようにする。"""

    def __init__(self):
        self.rows = []  # list[list[str]] (先頭行がヘッダー)
        self.clear_call_count = 0

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def get_all_records(self):
        if not self.rows:
            return []
        header = self.rows[0]
        records = []
        for row in self.rows[1:]:
            rec = {}
            for i, col in enumerate(header):
                rec[col] = row[i] if i < len(row) else ""
            records.append(rec)
        return records

    def update(self, values, range_name):
        # range_name例: "A1" (先頭全体書き込み) または "A5:D5" (1行だけ)
        if range_name == "A1":
            # 先頭からvaluesを書き込む(既存の行数より短い場合は残りは変更しない
            # という単純化はせず、A1形式のシンプルなfull-write系だけこの分岐を使う)
            for i, row_vals in enumerate(values):
                if i < len(self.rows):
                    self.rows[i] = list(row_vals)
                else:
                    self.rows.append(list(row_vals))
            return
        # "A{row}:{col}{row}" 形式 → 対象行番号を取り出して1行だけ書き換える
        import re

        m = re.match(r"^[A-Z]+(\d+):[A-Z]+(\d+)$", range_name)
        assert m, f"unexpected range_name: {range_name}"
        row_num = int(m.group(1))
        idx = row_num - 1  # 0-indexed
        while len(self.rows) <= idx:
            self.rows.append([])
        self.rows[idx] = list(values[0])

    def append_row(self, values, value_input_option=None):
        self.rows.append(list(values))

    def clear(self):
        self.clear_call_count += 1
        self.rows = []


class FakeSpreadsheet:
    def __init__(self):
        self.worksheets = {}

    def worksheet(self, name):
        if name not in self.worksheets:
            import gspread

            raise gspread.exceptions.WorksheetNotFound(name)
        return self.worksheets[name]

    def add_worksheet(self, title, rows=200, cols=20):
        ws = FakeWorksheet()
        self.worksheets[title] = ws
        return ws


class _FakeWorksheetNotFound(Exception):
    pass


def _install_fake_gspread(monkeypatch):
    """gspreadモジュール自体をインメモリのフェイクに差し替える。"""
    import types

    fake_gspread = types.ModuleType("gspread")

    class _Exceptions:
        WorksheetNotFound = _FakeWorksheetNotFound

    fake_gspread.exceptions = _Exceptions()

    def _authorize(creds):
        return None  # 使わない

    fake_gspread.authorize = _authorize
    monkeypatch.setitem(sys.modules, "gspread", fake_gspread)
    return fake_gspread


def _setup_env(monkeypatch, tmp_dir, shared_spreadsheet):
    """data_store / sheets_backend を、フェイクSheetsに向けて初期化する。"""
    import importlib

    _install_fake_gspread(monkeypatch)

    import sheets_backend as sb

    importlib.reload(sb)
    sb.DATA_DIR = Path(tmp_dir)
    sb.DATA_DIR.mkdir(exist_ok=True)

    monkeypatch.setattr(sb, "is_available", lambda: True)
    monkeypatch.setattr(sb, "credential_source", lambda: "local")
    monkeypatch.setattr(sb, "get_spreadsheet_key", lambda: "FAKE_KEY")
    monkeypatch.setattr(sb, "get_spreadsheet", lambda: shared_spreadsheet)

    # st.cache_data相当のTTLキャッシュはstreamlit無しでは素通しの_cached_get_all_records_implが
    # そのまま使われる(sheets_backend.py側でstreamlit未インストール時のフォールバックが働く)
    def _no_cache_get_all_records(sheet_name, spreadsheet_key):
        return sb._cached_get_all_records_impl(sheet_name, spreadsheet_key)

    monkeypatch.setattr(sb, "_cached_get_all_records", _no_cache_get_all_records)

    import data_store as ds

    importlib.reload(ds)
    ds.DATA_DIR = Path(tmp_dir)
    ds.DATA_DIR.mkdir(exist_ok=True)
    ds.CONFIG_PATH = ds.DATA_DIR / "config.json"
    ds.sheets_backend = sb
    return ds, sb


def _two_user_env(monkeypatch_a, monkeypatch_b, shared_spreadsheet, tmp_root):
    """2ユーザー分の独立したdata_store/sheets_backendモジュールインスタンスを用意する
    (各ユーザーが別プロセス/別セッションであることを模す)。ただしSpreadsheetは共有する。
    """
    dir_a = tmp_root / "user_a"
    dir_b = tmp_root / "user_b"
    dir_a.mkdir()
    dir_b.mkdir()
    ds_a, sb_a = _setup_env(monkeypatch_a, dir_a, shared_spreadsheet)
    ds_b, sb_b = _setup_env(monkeypatch_b, dir_b, shared_spreadsheet)
    return ds_a, ds_b


def test_ws_clear_never_called_by_unavailability_save(monkeypatch, tmp_path):
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    ok, msg = ds.save_member_unavailability(2026, 10, "Kikuchi", {"2026-10-03": {"day": True, "night": False}})
    assert ok

    v2_ws = shared_sh.worksheets[ds.SHEET_UNAVAILABILITY_V2]
    hist_ws = shared_sh.worksheets[ds.SHEET_UNAVAILABILITY_HISTORY]
    assert v2_ws.clear_call_count == 0
    assert hist_ws.clear_call_count == 0


def test_scenario_a_then_b_same_month(monkeypatch, tmp_path):
    """Aが2026-10を入力・保存 → Bが2026-10を入力・保存 → 両者のデータが残る"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    ok_a, _ = ds.save_member_unavailability(
        2026, 10, "Otani", {"2026-10-05": {"day": True, "night": False}}
    )
    ok_b, _ = ds.save_member_unavailability(
        2026, 10, "Ryu", {"2026-10-06": {"day": False, "night": True}}
    )
    assert ok_a and ok_b

    data = ds.load_unavailability_raw(2026, 10)
    assert data["Otani"] == {"2026-10-05": {"day": True, "night": False}}
    assert data["Ryu"] == {"2026-10-06": {"day": False, "night": True}}


def test_scenario_b_saves_november_a_october_still_intact(monkeypatch, tmp_path):
    """Bが2026-11を入力・保存 → A/Bの2026-10が残っていること"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    ds.save_member_unavailability(2026, 10, "Otani", {"2026-10-05": {"day": True, "night": False}})
    ds.save_member_unavailability(2026, 10, "Ryu", {"2026-10-06": {"day": False, "night": True}})
    ok, _ = ds.save_member_unavailability(2026, 11, "Ryu", {"2026-11-02": {"day": True, "night": True}})
    assert ok

    oct_data = ds.load_unavailability_raw(2026, 10)
    nov_data = ds.load_unavailability_raw(2026, 11)
    assert oct_data["Otani"] == {"2026-10-05": {"day": True, "night": False}}
    assert oct_data["Ryu"] == {"2026-10-06": {"day": False, "night": True}}
    assert nov_data["Ryu"] == {"2026-11-02": {"day": True, "night": True}}
    assert "Otani" not in nov_data


def test_scenario_a_reedits_october_b_unaffected(monkeypatch, tmp_path):
    """Aが再度2026-10を編集・保存 → Bの2026-10/11が一切変化しないこと"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    ds.save_member_unavailability(2026, 10, "Otani", {"2026-10-05": {"day": True, "night": False}})
    ds.save_member_unavailability(2026, 10, "Ryu", {"2026-10-06": {"day": False, "night": True}})
    ds.save_member_unavailability(2026, 11, "Ryu", {"2026-11-02": {"day": True, "night": True}})

    ok, _ = ds.save_member_unavailability(
        2026, 10, "Otani",
        {"2026-10-05": {"day": True, "night": False}, "2026-10-20": {"day": True, "night": True}},
    )
    assert ok

    oct_data = ds.load_unavailability_raw(2026, 10)
    nov_data = ds.load_unavailability_raw(2026, 11)
    assert oct_data["Otani"] == {
        "2026-10-05": {"day": True, "night": False},
        "2026-10-20": {"day": True, "night": True},
    }
    # Bのデータは一切変化していないこと
    assert oct_data["Ryu"] == {"2026-10-06": {"day": False, "night": True}}
    assert nov_data["Ryu"] == {"2026-11-02": {"day": True, "night": True}}


def test_admin_target_month_switch_preserves_each_month(monkeypatch, tmp_path):
    """管理者の対象年月を9月→10月→11月→9月と変更しても各月の不都合日が保持される"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    ds.save_member_unavailability(2026, 9, "Kikuchi", {"2026-09-01": {"day": True, "night": False}})
    ds.save_member_unavailability(2026, 10, "Kikuchi", {"2026-10-01": {"day": False, "night": True}})
    ds.save_member_unavailability(2026, 11, "Kikuchi", {"2026-11-01": {"day": True, "night": True}})

    ds.set_year_month(2026, 10)
    ds.set_year_month(2026, 11)
    ds.set_year_month(2026, 9)

    assert ds.load_unavailability_raw(2026, 9)["Kikuchi"] == {"2026-09-01": {"day": True, "night": False}}
    assert ds.load_unavailability_raw(2026, 10)["Kikuchi"] == {"2026-10-01": {"day": False, "night": True}}
    assert ds.load_unavailability_raw(2026, 11)["Kikuchi"] == {"2026-11-01": {"day": True, "night": True}}


def test_new_member_addition_does_not_affect_existing_data(monkeypatch, tmp_path):
    """新しいメンバーを追加して保存しても既存メンバーのデータが変化しないこと"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    ds.save_member_unavailability(2026, 10, "Otani", {"2026-10-05": {"day": True, "night": False}})
    ok, _ = ds.save_member_unavailability(2026, 10, "NewMember", {"2026-10-10": {"day": True, "night": True}})
    assert ok

    data = ds.load_unavailability_raw(2026, 10)
    assert data["Otani"] == {"2026-10-05": {"day": True, "night": False}}
    assert data["NewMember"] == {"2026-10-10": {"day": True, "night": True}}


def test_two_users_sequential_saves_do_not_clobber_each_other(monkeypatch, tmp_path):
    """[逐次実行のテスト] 2ユーザーがそれぞれ別のメンバーとして交互に保存を
    実行しても、互いのデータを消さないこと。

    注意: このテストはA→B→A→Bという逐次呼び出しであり、read/writeが
    実際にインターリーブする競合状態そのものは再現していない
    (2つの独立したdata_store/sheets_backendインスタンスを使い、同じ
    フェイクSpreadsheetへ交互に書き込むことで、各回の保存が最新の
    シート状態を見て動作することだけを確認するテスト)。
    実際の読み込みと書き込みの間に他セッションの書き込みが割り込む
    ケースは test_interleaved_write_race_* 系のテストで検証する。
    """
    import pytest

    shared_sh = FakeSpreadsheet()
    mp_a = pytest.MonkeyPatch()
    mp_b = pytest.MonkeyPatch()
    try:
        ds_a, ds_b = _two_user_env(mp_a, mp_b, shared_sh, tmp_path)

        ds_a.save_member_unavailability(2026, 10, "Otani", {"2026-10-01": {"day": True, "night": False}})
        ds_b.save_member_unavailability(2026, 10, "Ryu", {"2026-10-02": {"day": False, "night": True}})
        ds_a.save_member_unavailability(2026, 10, "Otani", {"2026-10-01": {"day": True, "night": True}})
        ds_b.save_member_unavailability(2026, 10, "Ryu", {"2026-10-03": {"day": True, "night": False}})

        merged = ds_a.load_unavailability_raw(2026, 10)
        assert merged["Otani"] == {"2026-10-01": {"day": True, "night": True}}
        assert merged["Ryu"] == {"2026-10-03": {"day": True, "night": False}}

        v2_ws = shared_sh.worksheets[ds_a.SHEET_UNAVAILABILITY_V2]
        assert v2_ws.clear_call_count == 0
        # 1行=1メンバー×1ヶ月なので、Otani/Ryuの2行だけになっているはず
        records = v2_ws.get_all_records()
        assert len(records) == 2
    finally:
        mp_a.undo()
        mp_b.undo()


def test_interleaved_write_race_same_existing_key_last_write_wins(monkeypatch, tmp_path):
    """[実際のインターリーブを再現] upsert_keyed_row() が get_all_values() で
    行位置を特定した直後・ws.update()実行前に、別セッションの保存一式
    (read→write)がまるごと割り込むケースを再現する。

    対象は既に1回保存済みの同一 member_name × year_month(=同じ行が
    既に存在する状態)。この場合、後からGoogle Sheetsへの書き込みを
    完了させたセッションの内容がそのまま残る(last-write-wins)。
    2つのセッションの入力内容がマージされることはなく、また行が
    重複することもない(対象行が既に存在するため、双方とも
    ws.update()による同一行の上書きになる)。
    """
    import pytest

    shared_sh = FakeSpreadsheet()
    mp_a = pytest.MonkeyPatch()
    mp_b = pytest.MonkeyPatch()
    try:
        ds_a, ds_b = _two_user_env(mp_a, mp_b, shared_sh, tmp_path)

        # 既存行を用意しておく(通常運用: 既にその月の保存済みデータがある状態)
        ds_a.save_member_unavailability(2026, 10, "Otani", {"2026-10-01": {"day": True, "night": False}})

        v2_ws = shared_sh.worksheets[ds_a.SHEET_UNAVAILABILITY_V2]
        original_get_all_values = v2_ws.get_all_values
        triggered = {"done": False}

        def _interleaving_get_all_values():
            # Aのupsert_keyed_row内、get_all_values()の直後(=行位置を
            # 特定するための読み込みの直後)・ws.update()実行前に割り込む
            # 形で、1回だけBの保存(read→write一式)を先に完了させる。
            values = original_get_all_values()
            if not triggered["done"]:
                triggered["done"] = True
                ds_b.save_member_unavailability(
                    2026, 10, "Otani", {"2026-10-02": {"day": False, "night": True}}
                )
            return values

        monkeypatch.setattr(v2_ws, "get_all_values", _interleaving_get_all_values)

        ok, _ = ds_a.save_member_unavailability(
            2026, 10, "Otani", {"2026-10-03": {"day": True, "night": True}}
        )
        assert ok

        # Aの書き込みがBの後に実行されるため、Aの内容がlast-write-winsで残る
        data = ds_a.load_unavailability_raw(2026, 10)
        assert data["Otani"] == {"2026-10-03": {"day": True, "night": True}}

        # 行が重複せず1行のままであること(対象行が既に存在するケースのため)
        records = v2_ws.get_all_records()
        matching = [r for r in records if r["member_name"] == "Otani" and r["year_month"] == "2026-10"]
        assert len(matching) == 1
        assert v2_ws.clear_call_count == 0
    finally:
        mp_a.undo()
        mp_b.undo()


def test_interleaved_write_race_new_key_documented_limitation(monkeypatch, tmp_path):
    """[既知の制約・ドキュメント化] まだ一度も保存されたことのない
    member_name × year_month に対して、2セッションがほぼ同時に
    「該当行なし」と判定した直後にそれぞれ ws.append_row() を実行すると、
    理論上は同一keyの行が2行できる可能性がある(同一医師が同じ月を
    複数タブ・複数端末からほぼ同時に初回保存した場合のみ起こりうる、
    極めて稀なケース)。

    この場合でも、シート全体の破壊(ws.clear())や他メンバー・他月の
    レコードへの影響は無い。読み込み側(_fetch_v2_month_records)は
    シート内で最後に出現する行の内容を採用するため、実害としては
    last-write-winsと同等になる(重複行自体は残るが、データの消失や
    シート破壊は起きない)。
    """
    import pytest

    shared_sh = FakeSpreadsheet()
    mp_a = pytest.MonkeyPatch()
    mp_b = pytest.MonkeyPatch()
    try:
        ds_a, ds_b = _two_user_env(mp_a, mp_b, shared_sh, tmp_path)

        # v2シート自体は存在するが、対象キー(Otani/2026-10)の行はまだ無い状態を作る
        ds_a.save_member_unavailability(2026, 10, "Someone", {"2026-10-01": {"day": True, "night": False}})
        v2_ws = shared_sh.worksheets[ds_a.SHEET_UNAVAILABILITY_V2]

        original_get_all_values = v2_ws.get_all_values
        triggered = {"done": False}

        def _interleaving_get_all_values():
            values = original_get_all_values()
            if not triggered["done"]:
                triggered["done"] = True
                ds_b.save_member_unavailability(
                    2026, 10, "Otani", {"2026-10-02": {"day": False, "night": True}}
                )
            return values

        monkeypatch.setattr(v2_ws, "get_all_values", _interleaving_get_all_values)

        ds_a.save_member_unavailability(2026, 10, "Otani", {"2026-10-03": {"day": True, "night": True}})

        records = v2_ws.get_all_records()
        matching = [r for r in records if r["member_name"] == "Otani" and r["year_month"] == "2026-10"]
        # このテストの割り込みタイミングでは、AとBが共に「該当行なし」と
        # 判定してそれぞれappendするため、実際に2行できることを確認する
        # (=既知の制約が実際に再現されていることの確認。1行だった場合は
        # このテストの割り込み条件が成立していないことを意味するため失敗させる)
        assert len(matching) == 2

        # 他メンバー(Someone)の行は無事であり、シート全体のclearも起きていない
        someone_rows = [r for r in records if r["member_name"] == "Someone"]
        assert len(someone_rows) == 1
        assert v2_ws.clear_call_count == 0

        # 読み込み時は最後に出現する行(=最後に書き込まれた内容)が採用される
        data = ds_a.load_unavailability_raw(2026, 10)
        assert data["Otani"] == {"2026-10-03": {"day": True, "night": True}}
    finally:
        mp_a.undo()
        mp_b.undo()


def test_shift_creation_reads_all_members_for_target_month(monkeypatch, tmp_path):
    """シフト作成時に対象月の全メンバーの不都合日が正しくOR-Toolsへ渡されること
    (get_unavailability_objects が v2 に保存した全員分を統合できているかを確認)"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    ds.save_member_unavailability(2026, 10, "Otani", {"2026-10-05": {"day": True, "night": False}})
    ds.save_member_unavailability(2026, 10, "Ryu", {"2026-10-06": {"day": False, "night": True}})
    ds.save_member_unavailability(2026, 10, "Kikuchi", {"2026-10-07": {"day": True, "night": True}})

    objs = ds.get_unavailability_objects(2026, 10)
    names = {u.member_name for u in objs}
    assert names == {"Otani", "Ryu", "Kikuchi"}

    by_name = {u.member_name: u for u in objs}
    assert by_name["Otani"].day.isoformat() == "2026-10-05"
    assert by_name["Otani"].day_unavailable and not by_name["Otani"].night_unavailable
    assert by_name["Ryu"].day.isoformat() == "2026-10-06"
    assert not by_name["Ryu"].day_unavailable and by_name["Ryu"].night_unavailable
    assert by_name["Kikuchi"].day_unavailable and by_name["Kikuchi"].night_unavailable


def test_v2_takes_priority_over_stale_legacy_data(monkeypatch, tmp_path):
    """新旧形式のフォールバックで、v2の新しいデータより旧シートの古いデータが
    優先されることがないことを確認する"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    # 旧シートに古いデータを直接投入(移行前の状態を模す)
    legacy_ws = shared_sh.add_worksheet(ds.SHEET_UNAVAILABILITY)
    legacy_ws.append_row(ds.UNAVAILABILITY_HEADER)
    legacy_ws.append_row(["Otani", "2026-10-05", 1, 0])  # 古いデータ: 日中不可のみ

    # 新方式で新しい内容を保存(夜間不可も追加)
    ds.save_member_unavailability(
        2026, 10, "Otani",
        {"2026-10-05": {"day": True, "night": True}, "2026-10-06": {"day": False, "night": True}},
    )

    data = ds.load_unavailability_raw(2026, 10)
    # v2の内容がそのまま採用されており、旧シートの古い値(nightがFalse)に
    # 引きずられていないこと
    assert data["Otani"] == {
        "2026-10-05": {"day": True, "night": True},
        "2026-10-06": {"day": False, "night": True},
    }


def test_legacy_member_without_v2_record_is_still_readable(monkeypatch, tmp_path):
    """v2にまだ記録の無いメンバーは、旧シートの内容で補完されること(後方互換)"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    legacy_ws = shared_sh.add_worksheet(ds.SHEET_UNAVAILABILITY)
    legacy_ws.append_row(ds.UNAVAILABILITY_HEADER)
    legacy_ws.append_row(["LegacyOnly", "2026-10-11", 1, 1])

    # v2には何も保存していない状態で、まだ移行もしていない
    data = ds.load_unavailability_raw(2026, 10)
    assert data["LegacyOnly"] == {"2026-10-11": {"day": True, "night": True}}

    v2_ws = shared_sh.worksheets.get(ds.SHEET_UNAVAILABILITY_V2)
    assert v2_ws is None or v2_ws.clear_call_count == 0


def test_migration_is_idempotent_and_skips_existing_v2(monkeypatch, tmp_path):
    """migrate_unavailability_to_v2 は既存v2データを上書きせず、複数回実行しても
    結果が変わらないこと(冪等性)"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    legacy_ws = shared_sh.add_worksheet(ds.SHEET_UNAVAILABILITY)
    legacy_ws.append_row(ds.UNAVAILABILITY_HEADER)
    legacy_ws.append_row(["Otani", "2026-10-05", 1, 0])
    legacy_ws.append_row(["Otani", "2026-10-06", 0, 1])
    legacy_ws.append_row(["Ryu", "2026-10-07", 1, 1])

    result1 = ds.migrate_unavailability_to_v2(skip_existing=True)
    assert result1["migrated"] == 2  # (Otani,2026-10) と (Ryu,2026-10) の2組
    assert result1["skipped"] == 0

    # 移行後、メンバーが新しいデータを保存(移行データより新しい状態)
    ds.save_member_unavailability(2026, 10, "Otani", {"2026-10-20": {"day": True, "night": True}})

    # 移行を再実行しても、Otaniの新しいデータが古い移行内容で上書きされないこと
    result2 = ds.migrate_unavailability_to_v2(skip_existing=True)
    assert result2["migrated"] == 0
    assert result2["skipped"] == 2

    data = ds.load_unavailability_raw(2026, 10)
    assert data["Otani"] == {"2026-10-20": {"day": True, "night": True}}  # 上書きされていない
    assert data["Ryu"] == {"2026-10-07": {"day": True, "night": True}}

    legacy_ws_after = shared_sh.worksheets[ds.SHEET_UNAVAILABILITY]
    assert legacy_ws_after.clear_call_count == 0  # 旧シートは一切変更されない


def test_verify_migration_reports_mismatch_after_new_save(monkeypatch, tmp_path):
    """移行前後で既存データが一致するかを検証できること"""
    shared_sh = FakeSpreadsheet()
    ds, sb = _setup_env(monkeypatch, tmp_path, shared_sh)

    legacy_ws = shared_sh.add_worksheet(ds.SHEET_UNAVAILABILITY)
    legacy_ws.append_row(ds.UNAVAILABILITY_HEADER)
    legacy_ws.append_row(["Otani", "2026-10-05", 1, 0])

    before = ds.verify_unavailability_migration()
    assert before["total_legacy_keys"] == 1
    assert before["matched"] == 0
    assert len(before["missing_in_v2"]) == 1

    ds.migrate_unavailability_to_v2(skip_existing=True)
    after = ds.verify_unavailability_migration()
    assert after["matched"] == 1
    assert after["mismatched"] == []
    assert after["missing_in_v2"] == []
