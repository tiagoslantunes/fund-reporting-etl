"""Rebuild historical performance metrics without look-ahead bias.

For each historical month-end, the analytics engine receives only observations
available up to that date. Results are written to one file per calendar year;
completed years are moved to the historical archive.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import pandas as pd

from performance_analytics import PerformanceAnalytics


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backfill")


def backfill(root: Path) -> None:
    """Calculate and persist metrics for every non-current month-end."""
    root = root.expanduser().resolve()
    output_dir = root / "01_MorningStar" / "Output"
    hist_dir = output_dir / "All Perf Metrics Hist"
    hist_dir.mkdir(parents=True, exist_ok=True)

    performance_csv = output_dir / "Performance.csv"
    benchmark_xlsx = root / "Reporting Docs" / "Benchmark_Mapping.xlsx"

    if not performance_csv.exists():
        raise FileNotFoundError(
            f"{performance_csv} not found; run automation_pipeline.py first."
        )
    if not benchmark_xlsx.exists():
        raise FileNotFoundError(f"Benchmark mapping not found: {benchmark_xlsx}")

    returns = pd.read_csv(performance_csv, parse_dates=["date"])
    benchmark_map = pd.read_excel(benchmark_xlsx, "Benchmark")
    output_preference = pd.read_excel(benchmark_xlsx, "Output Metric")

    month_ends = (
        returns["date"]
        .dt.to_period("M")
        .dt.to_timestamp("M")
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    if len(month_ends) < 2:
        raise ValueError("At least two month-end observations are required.")

    frames: list[pd.DataFrame] = []
    for snapshot_date in month_ends[:-1]:
        history = returns[returns["date"] <= snapshot_date].copy()
        if history.empty:
            continue

        analytics = PerformanceAnalytics(
            history,
            benchmark_map,
            output_metric=output_preference,
            rf=0.0,
            rolling_window=12,
            logger=log,
        )
        snapshot = (
            analytics.analyse_all(scale=100)
            .round(3)
            .assign(ReportDate=snapshot_date.date())
        )
        frames.append(snapshot)
        log.info("Computed metrics for %s (%d rows)", snapshot_date.date(), len(snapshot))

    if not frames:
        raise RuntimeError("No historical metrics were produced.")

    metrics = pd.concat(frames, ignore_index=True)
    metrics["ReportDate"] = pd.to_datetime(metrics["ReportDate"])
    metrics.sort_values(["ReportDate", "instrument"], inplace=True)
    current_year = int(metrics["ReportDate"].dt.year.max())

    for year, group in metrics.groupby(metrics["ReportDate"].dt.year):
        filename = f"All_Perf_Metrics{year}.csv"
        destination = output_dir / filename
        group.to_csv(destination, index=False)
        log.info("Wrote %s (%d rows)", filename, len(group))

        if int(year) != current_year:
            archived = hist_dir / filename
            destination.replace(archived)
            log.info("Archived %s", archived.relative_to(root))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill historical fund metrics with no look-ahead."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.getenv("FUND_REPORTING_ROOT", ".")),
        help="Project data root. Defaults to FUND_REPORTING_ROOT or the current directory.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    backfill(parse_args().root)
