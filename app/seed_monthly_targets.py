# -*- coding: utf-8 -*-
"""
final_target / month_target_* / known_long_term_absence の初期値投入スクリプト。

各値は data_store.py の set_final_target() / set_month_target() /
set_known_long_term_absence() をそれぞれ個別に呼び出すだけであり、
app_stateの他のキーには一切触れない(既存のgaikobu_days・finalized・
schedule等のキーはそのまま残る)。

実行方法:
    cd oncall_scheduler_git
    python -m app.seed_monthly_targets   (または直接 python app/seed_monthly_targets.py)

Sheets連携が設定されていればそちらにも反映され、未設定ならローカルの
data/*.json にのみ保存される(通常のdata_store.pyの挙動と同じ)。
"""
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
for p in (_APP_DIR, _PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import data_store as ds  # noqa: E402


FINAL_TARGET = {
    "Ryu": 25,
    "Nakajima": 28,
    "Kikuchi": 37,
    "Otani": 42,
    "Fujii": 56,
    "Kosaka": 53,
    "Wakayama": 57,
    "Otaki": 66,
}

# {month_number: {name: target}}
MONTH_TARGETS = {
    10: {"Ryu": 0, "Nakajima": 5, "Kikuchi": 14, "Otani": 6, "Fujii": 0, "Kosaka": 18, "Wakayama": 19, "Otaki": 0},
    11: {"Ryu": 8, "Nakajima": 0, "Kikuchi": 8, "Otani": 0, "Fujii": 0, "Kosaka": 11, "Wakayama": 13, "Otaki": 20},
    12: {"Ryu": 8, "Nakajima": 3, "Kikuchi": 5, "Otani": 9, "Fujii": 13, "Kosaka": 7, "Wakayama": 4, "Otaki": 13},
    1: {"Ryu": 4, "Nakajima": 8, "Kikuchi": 2, "Otani": 9, "Fujii": 15, "Kosaka": 6, "Wakayama": 7, "Otaki": 11},
    2: {"Ryu": 5, "Nakajima": 7, "Kikuchi": 3, "Otani": 8, "Fujii": 12, "Kosaka": 5, "Wakayama": 6, "Otaki": 10},
    3: {"Ryu": 0, "Nakajima": 5, "Kikuchi": 5, "Otani": 10, "Fujii": 16, "Kosaka": 6, "Wakayama": 8, "Otaki": 12},
}

# 2026年10月〜2027年3月の月番号 -> 実際の年
MONTH_TO_YEAR = {10: 2026, 11: 2026, 12: 2026, 1: 2027, 2: 2027, 3: 2027}

KNOWN_LONG_TERM_ABSENCE = {
    "Otaki": [{"start": "2026-10-01", "end": "2026-10-31"}],
    "Otani": [{"start": "2026-10-13", "end": "2026-12-06"}],
    "Kikuchi": [{"start": "2027-01-01", "end": "2027-01-15"}],
    "Kosaka": [],
    "Nakajima": [
        {"start": "2026-10-12", "end": "2026-12-20"},
        {"start": "2027-03-01", "end": "2027-03-14"},
    ],
    "Fujii": [{"start": "2026-10-01", "end": "2026-12-06"}],
    "Ryu": [
        {"start": "2026-10-01", "end": "2026-11-08"},
        {"start": "2027-01-18", "end": "2027-01-31"},
        {"start": "2027-03-01", "end": "2027-03-31"},
    ],
    "Wakayama": [{"start": "2026-12-07", "end": "2026-12-21"}],
}


def main() -> None:
    ds.set_final_target(FINAL_TARGET)
    print(f"final_target を保存しました: {FINAL_TARGET}")

    for month_num, targets in MONTH_TARGETS.items():
        year = MONTH_TO_YEAR[month_num]
        ds.set_month_target(year, month_num, targets)
        print(f"month_target_{year}_{month_num:02d} を保存しました: {targets}")

    ds.set_known_long_term_absence(KNOWN_LONG_TERM_ABSENCE)
    print(f"known_long_term_absence を保存しました: {KNOWN_LONG_TERM_ABSENCE}")


if __name__ == "__main__":
    main()
