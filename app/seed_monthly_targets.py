# -*- coding: utf-8 -*-
"""Target definitions, and safe refresh of Dec 2026–Mar 2027 targets."""
import calendar
import sys
from pathlib import Path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
import data_store as ds

MONTH_TARGETS_BY_DATE = {(2026, 12): {'Fujii': 13, 'Ryu': 19, 'Nakajima': 6, 'Kosaka': 4, 'Otani': 10, 'Kikuchi': 6, 'Wakayama': 4, 'Otaki': 0}, (2027, 1): {'Fujii': 15, 'Ryu': 8, 'Nakajima': 14, 'Kosaka': 4, 'Otani': 12, 'Kikuchi': 2, 'Wakayama': 7, 'Otaki': 0}, (2027, 2): {'Fujii': 7, 'Ryu': 12, 'Nakajima': 13, 'Kosaka': 4, 'Otani': 10, 'Kikuchi': 4, 'Wakayama': 6, 'Otaki': 0}, (2027, 3): {'Fujii': 22, 'Ryu': 0, 'Nakajima': 8, 'Kosaka': 5, 'Otani': 13, 'Kikuchi': 6, 'Wakayama': 8, 'Otaki': 0}}
MONTH_TO_YEAR = {10: 2026, 11: 2026, 12: 2026, 1: 2027, 2: 2027, 3: 2027}
MONTH_TARGETS = {m: targets for (y, m), targets in MONTH_TARGETS_BY_DATE.items()}
KNOWN_LONG_TERM_ABSENCE = {}  # The previously saved absence data must not be overwritten.
FINAL_TARGET = {}  # Calculated using confirmed actual Oct–Nov counts when applying.

def update_future_targets():
    """Write Dec–Mar goals and final_target; requires confirmed Oct and Nov data."""
    for (y,m), targets in MONTH_TARGETS_BY_DATE.items():
        expected = calendar.monthrange(y,m)[1]*2
        if sum(targets.values()) != expected:
            raise ValueError(f"{y}/{m}: expected {expected} slots")
    for y,m in [(2026,10),(2026,11)]:
        if not ds.is_actual_finalized(y,m) or not ds.load_actual_snapshot(y,m):
            raise RuntimeError(f"{y}/{m} の確定済み実績が必要です。目標値は変更していません。")
    names = list(next(iter(MONTH_TARGETS_BY_DATE.values())))
    previous = ds.get_confirmed_actual_totals(names, 2026, 12)
    future = {n:sum(v[n] for v in MONTH_TARGETS_BY_DATE.values()) for n in names}
    final = {n:previous[n]+future[n] for n in names}
    for (y,m), targets in MONTH_TARGETS_BY_DATE.items():
        ds.set_month_target(y,m,targets)
    ds.set_final_target(final)
    return final

if __name__ == "__main__":
    print(update_future_targets())
