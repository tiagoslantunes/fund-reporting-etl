"""
backfill_performance_metrics.py

High-level idea:
Let  

    R ∈ ℝ^{N×T}   be the panel of cumulative total returns  
                   (N funds, T month-end observations) extracted from
                   Performance.csv.

For every historical month-end τ < T we compute

    M_τ = g(R_{≤τ}) ,  τ = 1, …, T−1

where g(·) is the deterministic functional implemented by
:class:`performance_analytics.py`.

Hence no look-ahead bias is introduced—at each snapshot we feed g only the
information that was available at that time.

The resulting metric 
    {M_τ}_{τ=1}^{T}
is then partitioned by calendar year and written to disk:

    • current year ⇒ kept as a «hot» file that will continue to grow  
    • past years   ⇒ moved to an immutable Hist/ subfolder


Directory structure:
ROOT/
    01_MorningStar/
        Output/
            Performance.csv              ← source time-series (N×T)
            All_Perf_MetricsYYYY.csv      ← current year, keeps growing
            All Perf Metrics Hist/
                All_Perf_MetricsYYYY.csv  ← frozen snapshots (one per past-year)


Usage:
Run this script *ONCE* straight after the main ETL/automation pipeline has
produced an up-to-date Performance.csv.  After the back-fill, the daily
pipeline will carry on appending fresh rows only to the current-year file.

"""

from __future__ import annotations          # forward-compatible type hints

# Imports
import logging                              # lightweight observability
from pathlib import Path                    # OS-agnostic path handling
from typing import List                     # generic container hints

import pandas as pd                         # data wrangling powerhouse
from performance_analytics import (
    PerformanceAnalytics,                   # metric engine g(·)
)

# Configuration constants (user-adjustable knobs)

ROOT: Path = Path(
    r"C:/Users/Utilizador/Documents/PIC/test_data_structure"
)  # project root

OUTPUT_DIR: Path = ROOT / "01_MorningStar" / "Output"     # where CSVs live
HIST_DIR: Path = OUTPUT_DIR / "All Perf Metrics Hist"      # archive folder
HIST_DIR.mkdir(exist_ok=True)                              # idempotent

# Up-stream artefacts – MUST exist before we start
PERF_CSV: Path = OUTPUT_DIR / "Performance.csv"            # tidy returns
BM_XLSX:  Path = ROOT / "Reporting Docs" / "Benchmark_Mapping.xlsx"

# Logging 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log: logging.Logger = logging.getLogger("backfill")

# Load inputs
if not PERF_CSV.exists():
    raise FileNotFoundError(
        f"{PERF_CSV} not found – run the MorningStar pipeline first."
    )

ret_df: pd.DataFrame = pd.read_csv(      # full return history
    PERF_CSV, parse_dates=["date"]
)

bench_map_df:   pd.DataFrame = pd.read_excel(BM_XLSX, "Benchmark")
output_pref_df: pd.DataFrame = pd.read_excel(BM_XLSX, "Output Metric")

# Identify month-ends requiring back-fill

# Convert each observation date to its calendar month-end, then de-duplicate and sort ⇒ strictly increasing {τ₁, …, τ_T}.
month_ends: List[pd.Timestamp] = (
    ret_df["date"]
        .dt.to_period("M")         # Year-Month period (⟨YYYY-MM⟩)
        .dt.to_timestamp("M")      # final calendar day of that period
        .drop_duplicates()         # uniqueness
        .sort_values()
        .tolist()
)

if len(month_ends) < 2:            # need ≥ 2 observations to back-fill
    raise ValueError("Not enough historic data to back-fill.")

latest_me: pd.Timestamp       = month_ends[-1]   # τ_T – already current
backfill_dates: List[pd.Timestamp] = month_ends[:-1]  # τ₁…τ_{T−1}

log.info(
    "Back-filling %d historical month-ends (oldest = %s, newest = %s)",
    len(backfill_dates),
    backfill_dates[0].date(),
    backfill_dates[-1].date(),
)

# Main loop – compute M_τ  ∀ τ ∈ backfill_dates

frames: List[pd.DataFrame] = []      # accumulator for each snapshot

for τ in backfill_dates:

    # Restrict the return matrix to information available ≤ τ
    hist = ret_df[ret_df["date"] <= τ].copy()   # ℝ^{N × t}
    if hist.empty:                              # defensive guard
        continue

    # Instantiate the analytics engine g(·)
    pa = PerformanceAnalytics(
        hist,
        bench_map_df,
        output_metric=output_pref_df,
        rf=0.0,            # rf_t set to zero ⇒ Sharpe uses raw mean/σ
        rolling_window=12, # β̂ computed on trailing 12 months
        logger=log,
    )

    # Evaluate g at snapshot τ   (scale=100 ⇒ ratios→percentages)
    snapshot = (
        pa.analyse_all(scale=100)
          .round(3)                       # pretty-print; no effect on storage
          .assign(ReportDate=τ.date())    # label each row with τ
    )
    frames.append(snapshot)

    log.info("Computed metrics for %s — %d rows", τ.date(), len(snapshot))

# Sanity check: at least one τ must have succeeded
if not frames:
    raise RuntimeError("No metrics produced – please verify input paths.")

# Concatenate along row axis (⨁ over τ)
metrics_hist: pd.DataFrame = pd.concat(frames, ignore_index=True)

# Enforce correct dtypes and stable ordering
metrics_hist["ReportDate"] = pd.to_datetime(metrics_hist["ReportDate"])
metrics_hist.sort_values(["ReportDate", "instrument"], inplace=True)

# Persist one file per calendar year
current_year: int = metrics_hist["ReportDate"].dt.year.max()

for yr, grp in metrics_hist.groupby(metrics_hist["ReportDate"].dt.year):
    year_fname: str  = f"All_Perf_Metrics{yr}.csv"
    tmp_path:  Path  = OUTPUT_DIR / year_fname

    grp.to_csv(tmp_path, index=False)
    log.info("Wrote %s (%d rows)", year_fname, len(grp))

    # Move immutable years into the Hist/ vault
    if yr != current_year:
        final_path = HIST_DIR / year_fname
        if final_path.exists():
            final_path.unlink()        # make room for fresh copy
        tmp_path.rename(final_path)
        log.info("Archived %s → %s", year_fname, final_path.relative_to(ROOT))

# Done 
log.info(
    "Historical back-fill completed – %d distinct years (current = %d)",
    metrics_hist["ReportDate"].dt.year.nunique(),
    current_year,
)
