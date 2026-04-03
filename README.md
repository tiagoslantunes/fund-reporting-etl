# Streamlining Data Reporting Processes

An end-to-end **Extract → Transform → Analyse → Persist** pipeline that consolidates Morningstar data for ~200 investment funds into clean, reproducible datasets and Power BI-ready performance metrics.

The pipeline enforces **no look-ahead bias**, automates quality assurance and validation checks, and archives immutable historical records for fully reproducible reporting.

---

## Overview

Manual fund reporting is slow, error-prone, and hard to audit. This project replaces those processes with a robust, automated system that:

- Ingests multiple heterogeneous Morningstar data feeds (performance, characteristics, holdings)
- Merges, cleans, and validates data across ~200 instruments
- Computes a comprehensive set of absolute, benchmark, and relative performance metrics
- Outputs structured data ready for **Power BI** dashboards and quantitative analysis

---

## Architecture

```
Input (Morningstar Feeds)
        ↓
   Extract (automation_pipeline)
        ↓
   Transform (merge, clean, validate)
        ↓
   Analyse (performance_analytics engine)
        ↓
   Persist (snapshots + historical archive)
```

### Key Scripts

| Script | Responsibility |
|--------|----------------|
| `automation_pipeline.py` | Main orchestration — runs the full ETL + analytics process: ingestion, consolidation, snapshotting, metrics calculation, QA checks, and archiving |
| `performance_analytics.py` | Vectorized NumPy/pandas engine for calculating absolute, benchmark, and relative metrics across multiple time windows |
| `backfill_performance_metrics.py` | Rebuilds historical performance metric files by backfilling past month-end data to maintain a consistent time-series baseline |
| `corr_threshold_validation.py` | Detects fund name changes via Pearson correlation (ρ ≥ 0.90) between overlapping return series and automatically merges identities |
| `validate_exposure_header_classification.py` | Regression test ensuring exposure headers are consistently classified across pipeline runs |

---

## Data Flow

### 1. Extraction

**Input feeds:**
- **Performance** — monthly returns and volatility
- **Characteristic** — exposures (country, sector, asset class, etc.)
- **Holdings** — top holdings and weights
- **`Reporting Docs/Benchmark_Mapping.xlsx`**
  - *Benchmark* sheet — maps each instrument to up to 3 benchmarks (ordered by priority)
  - *Output Metric* sheet — preferred feed per instrument (`p` = total return, `m` = market return)

Parsing features in `automation_pipeline.py`:
- Flexible date handling (YYYY-MM, MM/DD/YYYY, text month names)
- Column name normalization (e.g., `"Std Dev"` variants)
- Regex-based header classification for exposure columns

### 2. Transformation

- Outer merge of performance and volatility feeds to ensure completeness
- Melt and classify exposures and holdings using `parse_column_name()` — categorizes by geography (US vs Non-US), sector, asset class, etc.
- De-duplication of overlapping vendor records

### 3. Analysis

`performance_analytics.py` computes metrics across configurable time windows (MTD, last month, QTD, YTD, 1Y, 3Y, 5Y, since-2023):

| Category | Metrics |
|----------|---------|
| **Absolute** | Cumulative return, annualised volatility, Sharpe ratio, max drawdown, downside deviation, positive-month count |
| **Benchmark** (up to 3) | Same set as absolute, computed per benchmark |
| **Relative** | Excess return, tracking error, beta, rolling 12M beta, hit-rate, correlation |

### 4. Persistence

| Output | Description |
|--------|-------------|
| `Output/Performance.csv` | Latest performance snapshot |
| `Output/Characteristic.csv` | Latest exposure snapshot |
| `Output/Holdings.csv` | Latest holdings snapshot |
| `All_Perf_MetricsYYYY.csv` | Power BI metrics feed — one row per instrument × window × metric |
| `All Perf Metrics Hist/` | Frozen, immutable historical archive |

---

## Validation & QA

| Check | How |
|-------|-----|
| **Exposure header regression** | `validate_exposure_header_classification.py` asserts header parsing matches a known baseline |
| **Fund auto-rename detection** | `corr_threshold_validation.py` merges fund identities with correlation > 0.90 over overlapping windows |
| **Snapshot diffs** | `automation_pipeline.py` logs newly added or missing funds, exposures, and holdings vs the previous run |
| **No look-ahead enforcement** | All calculations use only data available at or before time τ |

---

## Folder Structure

```
/Input/                                      # Raw Morningstar feeds
/Output/                                     # Latest tidy snapshots
/Hist/                                       # Frozen historical files
/Reporting Docs/                             # Benchmark mapping & config files
automation_pipeline.py                       # Main ETL orchestration
performance_analytics.py                     # Metrics calculation engine
backfill_performance_metrics.py              # Historical backfill
corr_threshold_validation.py                 # Name-change detection & merge
validate_exposure_header_classification.py   # Exposure header regression test
```

---

## Getting Started

**Requirements:** Python 3.10+

```bash
pip install pandas numpy openpyxl scipy python-dateutil
```

**Run the full pipeline:**

```bash
python automation_pipeline.py
```

**Backfill historical metrics:**

```bash
python backfill_performance_metrics.py
```

**Run QA checks:**

```bash
python validate_exposure_header_classification.py
python corr_threshold_validation.py
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `pandas >= 2.0` | Data manipulation and I/O |
| `numpy >= 1.24` | Vectorized metric calculations |
| `openpyxl` | Excel file parsing |
| `scipy` | Pearson correlation |
| `python-dateutil` | Flexible date parsing |

---

## Roadmap

- Parallel ingestion for large datasets
- Config-driven metric selection (YAML/TOML)
- Direct API ingestion from Morningstar
