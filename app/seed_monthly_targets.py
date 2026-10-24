# -*- coding: utf-8 -*-
"""2026年12月～2027年3月の月別目標を管理画面から明示的に適用する。"""
import calendar
import sys
from pathlib import Path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
import data_store as ds

# Day/Nightそれぞれ1枠。メンバー名は実アプリのAtsushiを使用。
MONTH_TARGETS_BY_DATE = {
    (2026, 12): {"Fujii":13, "Ryu":19, "Nakajima":6, "Kosaka":4, "Atsushi":10, "Kikuchi":6, "Wakayama":4, "Otaki":0},
    (2027, 1): {"Fujii":15, "Ryu":8, "Nakajima":14, "Kosaka":4, "Atsushi":12, "Kikuchi":2, "Wakayama":7, "Otaki":0},
    (2027, 2): {"Fujii":7, "Ryu":12, "Nakajima":13, "Kosaka":4, "Atsushi":10, "Kikuchi":4, "Wakayama":6, "Otaki":0},
    (2027, 3): {"Fujii":22, "Ryu":0, "Nakajima":8, "Kosaka":5, "Atsushi":13, "Kikuchi":6, "Wakayama":8, "Otaki":0},
}
# 画像で確認された2026年10月・11月の回数。過去4～9月はfinal targetに含めない。
OCT_NOV_TOTALS = {"Fujii":0, "Ryu":10, "Nakajima":4, "Kosaka":36, "Atsushi":6, "Kikuchi":31, "Wakayama":35, "Otaki":0}

def future_totals():
    names = set(OCT_NOV_TOTALS)
    return {name:sum(targets[name] for targets in MONTH_TARGETS_BY_DATE.values()) for name in names}

def proposed_final_targets():
    fut = future_totals()
    return {name:OCT_NOV_TOTALS[name]+fut[name] for name in OCT_NOV_TOTALS}

def validate_targets():
    names = set(OCT_NOV_TOTALS)
    for (year,month), targets in MONTH_TARGETS_BY_DATE.items():
        assert set(targets) == names, f"{year}/{month}: メンバー名が不一致"
        assert sum(targets.values()) == 2*calendar.monthrange(year,month)[1], f"{year}/{month}: 枠数不一致"
        assert targets['Otaki']==0
    assert sum(OCT_NOV_TOTALS.values()) == 122

def update_future_targets():
    """管理者の明示的な操作でのみ、月別目標4件とfinal targetを保存する。"""
    validate_targets()
    active = {m['name'] for m in ds.load_config().get('members',[])}
    if not set(OCT_NOV_TOTALS).issubset(active):
        raise RuntimeError('登録メンバー名が目標と一致しません。未登録: '+', '.join(sorted(set(OCT_NOV_TOTALS)-active)))
    for (year,month), targets in MONTH_TARGETS_BY_DATE.items():
        ds.set_month_target(year,month,targets)
    final = proposed_final_targets()
    ds.set_final_target(final)
    return final

if __name__ == '__main__':
    print('この更新は管理者画面のボタンから実行してください。')
