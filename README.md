
# Streamlining Data Reporting Processes

An **end-to-end Extract → Transform → Analyse → Persist pipeline** that consolidates **Morningstar** data for ~200 investment funds into clean, reproducible datasets and **Power BI-ready** performance metrics.

The pipeline enforces **no look-ahead bias**, automates **quality assurance (QA)** and validation checks, and archives immutable historical records for **fully reproducible reporting**.

---

## 1. Overview

The goal of this project is to **automate and standardize** the reporting of fund performance, exposures, and holdings, replacing manual processes with a robust, reproducible, and scalable system.

It ingests multiple heterogeneous Morningstar data feeds, merges and cleans them, computes a comprehensive set of absolute, benchmark, and relative performance metrics, and outputs structured data ready for integration into **Power BI** dashboards and quantitative analysis workflows.

---

## 2. Architecture

```

Input (Morningstar Feeds)
↓
Extract (automation\_pipeline)
↓
Transform (merge, clean, validate)
↓
Analyse (performance\_analytics engine)
↓
Persist (snapshots + historical archive)

```

### Key Components

| Script | Responsibility |
|--------|----------------|
| **`automation_pipeline.py`** | Main orchestration script. Runs the full ETL + analytics process: ingestion of vendor data, consolidation, snapshotting, metrics calculation, QA checks, and archiving. |
| **`backfill_performance_metrics.py`** | Rebuilds historical performance metric files by backfilling past month-end data to maintain a consistent time-series baseline. |
| **`corr_threshold_validation.py`** | Detects fund name changes by computing Pearson correlation (ρ ≥ 0.90) between overlapping return series and automatically merging identities. |
| **`performance_analytics.py`** | Vectorized NumPy/pandas engine for calculating absolute, benchmark, and relative metrics across multiple time windows. |
| **`validate_exposure_header_classification.py`** | Regression test to ensure exposure headers are consistently classified across runs. |

---

## 3. Data Flow

### 3.1 Extraction
**Inputs:**
- `Performance` feed – monthly returns + volatility.
- `Characteristic` feed – exposures (e.g., country, sector, asset class).
- `Holdings` feed – top holdings and weights.
- `Reporting Docs/Benchmark_Mapping.xlsx`:
  - **Benchmark** sheet – maps instrument → up to 3 benchmarks (ordered).
  - **Output Metric** sheet – preferred feed per instrument (`p` = total return, `m` = market return).

Parsing features in `automation_pipeline.py`:
- Flexible date handling (YYYY-MM, MM/DD/YYYY, text month names).
- Column name normalization (e.g., “Std Dev”).
- Regex-based header classification for exposures.

---

### 3.2 Transformation
- **Outer merge** of performance and volatility feeds to ensure completeness.
- **Melt and classify** exposures and holdings:
  - Uses `parse_column_name()` to categorize by geography (US vs Non-US), sector, asset class, etc.
- De-duplication of overlapping vendor data.

---

### 3.3 Analysis – `performance_analytics.py`
The analytics engine computes:

**Absolute Metrics**
- Cumulative total return
- Annualised volatility
- Sharpe ratio
- Maximum drawdown
- Downside deviation
- Positive-month count

**Benchmark Metrics** (up to 3 benchmarks/instrument)
- Same set as absolute metrics, computed for each benchmark.

**Relative Metrics**
- Excess return
- Tracking error
- Beta
- Rolling 12-month beta
- Hit-rate
- Correlation

**Windows**
- MTD, last month, QTD, YTD, 1Y, 3Y, 5Y, since_2023 (configurable).

---

### 3.4 Persistence
- **Latest snapshots**:
  - `Performance.csv`
  - `Characteristic.csv`
  - `Holdings.csv`
- **Power BI metrics feed**:
  - `All_Perf_MetricsYYYY.csv` (one row per instrument × window × metric).
- **Historical archive**:
  - Frozen, immutable files stored in `All Perf Metrics Hist/`.

---

## 4. Validation & QA

- **Exposure header classification test** (`validate_exposure_header_classification.py`)  
  Ensures that exposure header parsing matches the regression baseline.

- **Fund auto-rename** (`corr_threshold_validation.py`)  
  Detects high-correlation (>0.90) overlapping return series and merges renamed funds.

- **Snapshot diffs** (in `automation_pipeline.py`)  
  Logs newly added or missing funds, exposures, and holdings vs the previous run.

- **No look-ahead enforcement**  
  All calculations use only data available at or before time τ.

---

## 5. Folder Structure

```

/Input/                                    # Raw Morningstar feeds
/Output/                                   # Latest tidy snapshots
/Hist/                                     # Frozen historical files
/Reporting Docs/                           # Benchmark mapping & configs
automation\_pipeline.py                     # Main ETL orchestration
backfill\_performance\_metrics.py            # Historical backfill
corr\_threshold\_validation.py               # Name-change detection & merge
performance\_analytics.py                   # Metrics calculation engine
validate\_exposure\_header\_classification.py # Exposure header regression test

````

---

## 6. Dependencies

* Python 3.10+
* pandas ≥ 2.0
* NumPy ≥ 1.24
* OpenPyXL (Excel parsing)
* SciPy (Pearson correlation)
* python-dateutil (date parsing)

Install via:

```bash
pip install pandas numpy openpyxl scipy python-dateutil
````

---

## 7. Future Enhancements

* Parallel ingestion for large datasets.
* Config-driven metric selection.
* Direct API ingestion from Morningstar.

---

