# Streamlining Data Reporting Processes

An end-to-end **Extract → Transform → Analyse → Persist** pipeline that consolidates Morningstar data for ~200 funds into tidy tables and **Power BI-ready** performance metrics. It enforces **no look-ahead**, automates **QA/validation**, and archives immutable history for reproducible reporting.

## Key capabilities
- **Centralized ETL** from heterogeneous Morningstar feeds:
  - Performance (total/market returns), **volatility**, **exposures**, **holdings**.
- **Tidy consolidation** with outer merges and de-duplication.
- **Metric engine** (`performance_analytics.py`) computes:
  - Absolute: cumulative perf, annualized volatility, Sharpe, max drawdown, downside risk, positive-months.
  - Benchmark: same set for up to **3** mapped benchmarks per instrument.
  - Relative: excess return, tracking error, beta, **rolling 12m beta**, hit-rate, correlation.
- **Windows**: MTD, last month, QTD, YTD, 1Y, 3Y, 5Y, since_2023 (configurable).
- **No look-ahead**: each snapshot uses only information ≤ τ.
- **Back-fill** of historical month-ends to build a consistent time-series baseline.
- **Validation & QA**:
  - Header parser for exposures with regression test (`validate_exposure_header_classification.py`).
  - **Auto-rename** of funds via Pearson ρ ≥ 0.90 to stitch name changes.
  - Snapshot diffs: new/missing funds, exposures, holdings.
- **Archiving**: current-year metrics kept “hot”; prior years frozen under `Hist/`.

## Repository layout (key paths)
ROOT/
01_MorningStar/
01_Daily/
01_Performance Data/
02_Volatility Data/TPRD/
02_Monthly/02_Exposures Data/
01_THRD/ 02_CVRD/ 03_ESRD/ 04_EGRD/ 05_FIRD/
Output/
Performance.csv
Characteristic.csv
Holdings.csv
All_Perf_MetricsYYYY.csv
All Perf Metrics Hist/
Reporting Docs/Benchmark_Mapping.xlsx
Code/
automation_pipeline.py
backfill_performance_metrics.py
performance_analytics.py
validate_exposure_header_classification.py

## How it works
1. **Ingest** vendor CSV/XLSX → tidy long form (robust parsers for dates, “Std Dev”, headers).
2. **Consolidate** returns + volatility (outer merge), de-dup per feed.
3. **Exposures/holdings** melted, classified (`parse_column_name`), special handling for US vs Non-US.
4. **Snapshot** latest `Performance.csv`, `Characteristic.csv`, `Holdings.csv`; compare with previous run.
5. **Analyse** with `PerformanceAnalytics` (vectorized NumPy/pandas) and **append** to `All_Perf_MetricsYYYY.csv`.
6. **Archive** completed years to `All Perf Metrics Hist/`.

## Inputs
- `Output/Performance.csv` – tidy monthly returns (+ volatility when available).
- `Reporting Docs/Benchmark_Mapping.xlsx`
  - Sheet **Benchmark**: instrument → up to 3 benchmarks (ordered).
  - Sheet **Output Metric**: preferred feed per instrument (`p` total return vs `m` market return).

## Outputs
- `Performance.csv`, `Characteristic.csv`, `Holdings.csv` (latest snapshots).
- `All_Perf_MetricsYYYY.csv` (Power BI feed; one row per instrument × window × metric).
- Archived historical metric files under `All Perf Metrics Hist/`.
