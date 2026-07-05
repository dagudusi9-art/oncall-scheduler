# -*- coding: utf-8 -*-
"""
オンコール自動割当システム CLIエントリポイント

■ 使い方

ローカルCSVで試す場合:
    python -m src.main \
        --config config/config.yaml \
        --unavailability sample_data/unavailability.csv \
        --output output/schedule_2026_08.xlsx

Google Sheets連携で実行する場合:
    python -m src.main \
        --sheets \
        --credentials credentials/service_account.json \
        --spreadsheet-key <スプレッドシートのID> \
        --output output/schedule_2026_08.xlsx \
        --write-back

--write-back を付けると、最適化結果をスプレッドシートの
「勤務表」「集計表」シートにも書き込む(Excel出力に加えて)。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# パッケージ実行(python -m src.main)・スクリプト直接実行の両対応
try:
    from .config_loader import load_config_from_yaml
    from .excel_export import export_to_excel
    from .optimizer import OnCallOptimizer, OptimizerOptions
    from .unavailability_loader import load_unavailability_from_csv
except ImportError:  # スクリプト直接実行時
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from src.config_loader import load_config_from_yaml
    from src.excel_export import export_to_excel
    from src.optimizer import OnCallOptimizer, OptimizerOptions
    from src.unavailability_loader import load_unavailability_from_csv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="オンコール自動割当システム")
    p.add_argument("--config", type=str, default="config/config.yaml", help="設定YAMLファイルのパス")
    p.add_argument("--unavailability", type=str, default=None, help="不都合日CSVファイルのパス")
    p.add_argument("--output", type=str, default="output/schedule.xlsx", help="出力Excelファイルのパス")

    p.add_argument("--sheets", action="store_true", help="Google Sheets連携モードで実行する")
    p.add_argument("--credentials", type=str, default="credentials/service_account.json")
    p.add_argument("--spreadsheet-key", type=str, default=None, help="対象スプレッドシートのID")
    p.add_argument("--write-back", action="store_true", help="結果をスプレッドシートにも書き込む")

    p.add_argument("--max-time", type=float, default=30.0, help="最適化の最大計算時間(秒)")
    return p


def run_local(args: argparse.Namespace) -> int:
    config = load_config_from_yaml(args.config)
    if not args.unavailability:
        logger.error("--unavailability にCSVファイルのパスを指定してください")
        return 1
    unavailabilities = load_unavailability_from_csv(args.unavailability)

    return _optimize_and_export(config, unavailabilities, args)


def run_sheets(args: argparse.Namespace) -> int:
    from .sheets_io import SheetsClient

    if not args.spreadsheet_key:
        logger.error("--spreadsheet-key を指定してください")
        return 1

    client = SheetsClient(credentials_path=args.credentials, spreadsheet_key=args.spreadsheet_key)
    config = client.load_config()
    unavailabilities = client.load_unavailability()

    exit_code = _optimize_and_export(config, unavailabilities, args, sheets_client=client if args.write_back else None)
    return exit_code


def _optimize_and_export(config, unavailabilities, args, sheets_client=None) -> int:
    logger.info(f"{config.year}年{config.month}月分の勤務表を最適化します(メンバー数: {len(config.members)})")

    options = OptimizerOptions(max_time_seconds=args.max_time)
    optimizer = OnCallOptimizer(
        year=config.year,
        month=config.month,
        members=config.members,
        unavailabilities=unavailabilities,
        options=options,
    )
    result = optimizer.solve()

    if result.status not in ("OPTIMAL", "FEASIBLE"):
        logger.error(f"最適化に失敗しました: status={result.status}")
        for w in result.warnings:
            logger.error(f"  - {w}")
        return 2

    logger.info(f"最適化完了 (status={result.status})")
    for w in result.warnings:
        logger.warning(w)

    output_path = export_to_excel(result, args.output)
    logger.info(f"Excelファイルを出力しました: {output_path}")

    if sheets_client is not None:
        sheets_client.write_schedule(result)
        logger.info("スプレッドシートへの書き込みが完了しました")

    _print_summary(result)
    return 0


def _print_summary(result) -> None:
    print("\n=== 集計サマリー ===")
    print(f"{'名前':<10}{'日中':>6}{'夜間':>6}{'合計':>6}{'目標':>6}{'差':>6}")
    for name, s in result.stats.items():
        print(f"{name:<10}{s['day']:>6}{s['night']:>6}{s['total']:>6}{s['target']:>6}{s['diff']:>6}")


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.sheets:
        return run_sheets(args)
    return run_local(args)


if __name__ == "__main__":
    sys.exit(main())
