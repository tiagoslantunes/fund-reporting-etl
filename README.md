
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
Extract (parsers)
↓
Transform (tidy consolidation)
↓
Analyse (PerformanceAnalytics engine)
↓
Persist (snapshots + historical archive)

````

### Key Components

| Module | Responsibility |
|--------|----------------|
| **`ingestion.py`** | Parses CSV/XLSX Morningstar feeds into tidy long format, handling performance, volatility, exposures, and holdings. |
| **`consolidation.py`** | Outer-merge multiple feeds, de-duplicate records, and classify exposures/holdings. |
| **`performance_analytics.py`** | Computes vectorized performance metrics using NumPy/pandas. |
| **`validate_exposure_header_classification.py`** | Regression test for exposure header classification. |
| **`auto_rename.py`** | Detects fund name changes using Pearson correlation (ρ ≥ 0.90) and merges identities. |
| **`archiver.py`** | Archives completed years into the immutable `Hist/` folder. |

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

Parsing features:
- Flexible date parsing (YYYY-MM, MM/DD/YYYY, text month).
- Normalisation of column names (e.g., “Std Dev”).
- Regex-based header classification for exposures.

---

### 3.2 Transformation
- **Outer merge** of performance and volatility to avoid record loss.
- **Melt and classify** exposures/holdings:
  - `parse_column_name()` detects exposure category (US vs Non-US, Sector, Asset Class).
- De-duplication of overlapping vendor data.

---

### 3.3 Analysis – `PerformanceAnalytics`
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
- **Snapshots**:
  - `Performance.csv`
  - `Characteristic.csv`
  - `Holdings.csv`
- **Metrics feed**:
  - `All_Perf_MetricsYYYY.csv` (one row per instrument × window × metric).
- **Archival**:
  - Frozen historical files stored in `All Perf Metrics Hist/`.

---

## 4. Validation & QA

- **Exposure header classification test** (`validate_exposure_header_classification.py`)  
  Ensures exposure parsing output matches regression baseline.
  
- **Fund auto-rename** (`auto_rename.py`)  
  Detects >0.90 Pearson correlation in overlapping returns and merges identities under new names.
  
- **Snapshot diffs**  
  Reports newly added or missing funds, exposures, and holdings vs previous run.

- **No look-ahead enforcement**  
  Every snapshot uses only data available up to time τ.

---

## 5. Example Execution Flow

1. **Ingest**  
   ```bash
   python ingestion.py
   ```

Loads and tidies raw Morningstar data.

2. **Consolidate**

   ```bash
   python consolidation.py
   ```

   Merges feeds into canonical tidy tables.

3. **Analyse**

   ```bash
   python performance_analytics.py
   ```

   Computes metrics for all instruments.

4. **Validate**

   ```bash
   python validate_exposure_header_classification.py
   ```

   Runs QA checks.

5. **Rename funds** (if needed)

   ```bash
   python auto_rename.py
   ```

6. **Archive**

   ```bash
   python archiver.py
   ```

---

## 6. Folder Structure

```
/Input/                         # Raw Morningstar feeds
/Output/                        # Latest tidy snapshots
/Hist/                          # Frozen historical files
/Reporting Docs/                # Benchmark mapping & configs
ingestion.py                    # Parsing logic
consolidation.py                # Data merging
performance_analytics.py        # Metric calculation engine
validate_exposure_header_classification.py
auto_rename.py
archiver.py
```

---

## 7. Dependencies

* Python 3.10+
* pandas ≥ 2.0
* NumPy ≥ 1.24
* OpenPyXL (Excel parsing)
* SciPy (Pearson correlation)
* python-dateutil (date parsing)

Install via:

```bash
pip install pandas numpy openpyxl scipy python-dateutil
```

---

## 8. Future Enhancements

* Parallel ingestion for large datasets.
* Config-driven metric selection.
* Direct API ingestion from Morningstar.

---
