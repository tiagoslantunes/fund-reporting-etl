"""
automation_pipeline.py 

This end-to-end *Extract → Tidy → Analyse → Persist* pipeline digests the full
Morningstar data dump—returns, volatility, exposures, holdings—into three
canonical tidy tables plus a daily refresh of performance analytics.

Mathematically, let

    • ℱ ≔ {f₁, …, f_N}            be the universe of funds  
    • t                           index discrete month-ends / snapshots  
    • r_{i,t} ∈ ℝ                cumulative total return of fund i at t  
    • σ_{i,t} ∈ ℝ⁺               annualised volatility estimate  
    • χ_{i,t,k}                  kth exposure characteristic of fund i at t  
    • ω_{i,t,n}                  nth constituent holding weight

This script performs

    1.  Ingestion      –  parse messy vendor layouts → tidy long form  
    2.  Consolidation  –  outer-merge multiple feeds, deduplicate clashes  
    3.  Snapshotting   –  write *current* CSVs + compare against previous run  
    4.  Metrics g(·)   –  call :class:`performance_analytics.py`
                          to compute risk/return tensors and append them to
                          All_Perf_MetricsYYYY.csv
    5.  Archiving      –  rotate completed-year metric files to a write-once
                          «Hist/» vault.

Throughout, statistical integrity is preserved (no look-ahead, outer joins
retain NA evidence, Pearson-ρ-driven auto-rename controlled at ρ>0.90, etc.).
"""

# Imports & Configuration 
from __future__ import annotations  # postpone evaluation of type hints

import argparse                         # command-line configuration
import logging                          # lightweight structured logging
import os                               # environment-based configuration
import time                             # high-resolution wall-clock
import re                               # regex for filename parsing
from datetime import datetime           # timestamp helpers
from pathlib import Path                # OS-agnostic paths
from typing import Callable, Dict, List # generic type hints

import numpy as np                      # numerical ops (ρ, etc.)
import pandas as pd                     # data-wrangling work-horse
from scipy.stats import pearsonr        # Pearson correlation coefficient

from performance_analytics import PerformanceAnalytics  # g(·) – metric engine

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
    force=True        
)

# Create a module-level logger for use throughout the script
logger = logging.getLogger(__name__)

# Global Constants & Helpers
FREQ: int = 12  # months/year – used for annualisation factors

# Morningstar files use inconsistent headers for the first column, define a canonical header and map known aliases to it
REQUIRED_KEY = "Group/Investment"
ALIAS_MAP: Dict[str, str] = {
    "instrument": REQUIRED_KEY,
    "fund": REQUIRED_KEY,
    "fund_name": REQUIRED_KEY,
}

# Keep track of any exposure headers we fail to parse, so we only warn once per header
logged_headers: set[str] = set()

# Generic Helpers
def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardise the *identity* column header to «Group/Investment».

    Vendor CSVs/XLSX label that first column variously as *Instrument*,
    *Fund*, *Fund Name*…  We canonicalise so downstream group-bys are stable.
    """
    # Strip whitespace from column names and build a map from stripped to original
    clean_map = {c.strip(): c for c in df.columns}
    # For each alias we know, check if any column matches (case-insensitive)
    for alias, tgt in ALIAS_MAP.items():
        for col in list(clean_map):
            if col.lower() == alias.lower() and clean_map[col] != tgt:
                # Rename the found alias to the target canonical key
                df = df.rename(columns={clean_map[col]: tgt})
                # Remove from our map so we don't rename repeatedly
                del clean_map[col]
                break
    return df

def extract_date_from_filename(fp: Path) -> str | None:
    """
    Pull «YYYYMMDD» token from *fp.name* and return «YYYY-MM».

    Used to tag exposures / holdings snapshots with the month of observation.
    """
    m = re.search(r"(\d{4})(\d{2})(\d{2})", fp.name)
    return f"{m.group(1)}-{m.group(2)}" if m else None

def extract_full_date_from_filename(fp: Path) -> datetime:
    """
    Find an 8-digit date (YYYYMMDD) in the filename and parse it into a datetime.
    If none found, return datetime.min so that sorting by date still works.
    """
    m = re.search(r"(\d{8})", fp.name)
    return datetime.strptime(m.group(1), "%Y%m%d") if m else datetime.min

def find_most_recent_file(folder: Path, pattern: str) -> Path | None:
    """
    Within the given folder, glob for files matching pattern (e.g. 'TPRD_*.csv'),
    sort them by the date embedded in their names (newest first), and return
    the most recent one. Logs a warning if none are found.
    """
    files = sorted(
        folder.glob(pattern),
        key=extract_full_date_from_filename,
        reverse=True
    )
    if not files:
        logger.warning("No files %s in %s", pattern, folder)
        return None
    logger.info("Most recent %s → %s", pattern, files[0].name)
    return files[0]
    
def infer_report_date(files: list[Path]) -> datetime:
    """
    From a list of performance feed files pick the latest YYYYMMDD date
    embedded in any filename.  
    Fallback to `datetime.now()` if nothing is found.
    """
    dates = [
        extract_full_date_from_filename(f)
        for f in files
        if extract_full_date_from_filename(f) != datetime.min
    ]
    return max(dates) if dates else datetime.now()

def dedup_by_source(df: pd.DataFrame, subset: List[str], _source: str) -> pd.DataFrame:
    """
    Drop rows that are perfect duplicates based on the given subset of columns.
    Logs how many duplicates were removed. Useful for merging multiple data feeds
    where exact duplicates may occur.
    """
    before = len(df)
    # Keep first occurrence, drop the rest
    out = df.drop_duplicates(subset=subset, keep="first").reset_index(drop=True)
    removed = before - len(out)
    if removed:
        logger.info(
            "dedup_by_source › %s exact dupes removed on %s",
            removed, subset
        )
    return out

# Snapshot Helpers (Previous Runs)
def read_prev_csv(path: Path) -> pd.DataFrame | None:
    """
    Load a previous snapshot CSV (from the Hist folder). Handles missing files,
    different encodings (UTF-8, Latin-1), and ensures the required key column
    exists. Returns None if the file is absent or unusable.
    """
    if not path.exists():
        logger.info("%s not found – first run or snapshots deleted", path.name)
        return None

    # Try multiple encodings in case the file isn't UTF-8
    for enc in ("utf-8", "latin-1"):
        try:
            # Skip commented header rows (starting with '#')
            df = pd.read_csv(path, comment="#", encoding=enc)
            # Normalize columns (in case the canonical column has changed)
            df = _normalise_columns(df)
            # Ensure the DataFrame is not empty and has our required key
            if df.empty or REQUIRED_KEY not in df.columns:
                logger.warning(
                    "%s present but unusable (missing '%s')",
                    path.name, REQUIRED_KEY
                )
                return None
            return df
        except (UnicodeDecodeError, pd.errors.EmptyDataError):
            continue
    logger.error("Cannot decode %s", path)
    return None

def write_prev_csv(df: pd.DataFrame, path: Path, tag: str) -> None:
    """
    Save the given DataFrame to CSV at 'path', and prefix the file with a
    '#DATE=<tag>' line so future runs know when this snapshot was taken.
    """
    with open(path, "w", encoding="utf-8", newline="") as f:
        # Embed the snapshot date as a commented line
        f.write(f"#DATE={tag}\n")
        df.to_csv(f, index=False)
    logger.info("Snapshot written › %s (%d rows)", path.name, len(df))

# Exposure-Sheet Helpers 
def parse_column_name(col: str) -> tuple[str, str, str]:
    """
    Take a raw exposure column header string (e.g. 'Equity Style Large Cap (%)')
    and break it into three components (the triple (category,type,description)):
      1. category    (e.g. 'Equity' or 'Fixed Income')
      2. type        (e.g. 'Market Capitalization Breakdown')
      3. description (e.g. 'Large Cap')

    Heuristics rely on regex patterns + keyword dictionaries.  Unknown
    patterns emit a one-time warning so that the mapping table can evolve.
    """
    try:
        # Lowercase version for keyword matching
        cl = col.lower()
        # Initialize outputs as 'Unknown'
        cat, typ, desc = 'Unknown', 'Unknown', ''
        
        # Identify the broad asset class:
        if 'equity' in cl:
            cat = 'Equity'
        elif any(x in cl for x in [
            'fixd-inc', 'fixed income', 'coupon', 'maturity',
            'credit quality survey', 'average eff duration survey',
            'average mod duration survey', 'average ytm survey',
            'average eff maturity survey',
        ]):
            cat = 'Fixed Income'

        # For Equity exposures, look for specific breakdowns:
        if cat == 'Equity':
            # Economic sector (e.g. "Econ Sector Financials (%)")
            if 'econ sector' in cl:
                typ = 'Economic Sector'
                m = re.search(r'econ sector\s+(.*?)\s*%', col, re.I)
                desc = m.group(1).strip() if m else ''

            # Style (large/mid/small cap, or detailed style)
            elif 'style' in cl:
                # Big-cap vs small-cap
                if any(x in cl for x in ['large cap','mid cap','small cap']):
                    typ = 'Market Capitalization Breakdown'
                    m = re.search(r'Equity Style\s+(Large Cap|Mid Cap|Small Cap)', col, re.I)
                    desc = m.group(1).strip() if m else ''
                # Other style categories (e.g. "Value", "Growth")
                else:
                    typ = 'Detailed Style Breakdown'
                    m = re.search(r'Equity Style\s+(.*?)(?=\s*%|\s*\(Long)', col, re.I)
                    desc = m.group(1).strip() if m else ''

            # Country exposures (aggregated US vs Non-US, or detailed list)
            elif 'country' in cl:
                is_agg = any(x in cl for x in ['non-us','united states'])
                typ = 'Country (Aggregated)' if is_agg else 'Detailed Country Breakdown'
                if is_agg:
                    desc = 'US' if 'united states' in cl else 'Non-US'
                else:
                    m = re.search(r'Country\s+(.*?)\s*%', col, re.I)
                    desc = m.group(1).strip() if m else ''

        # For Fixed Income exposures, detect duration, yield, credit, etc.
        elif cat == 'Fixed Income':
            if cl.startswith('average eff duration survey'):
                typ, desc = 'Duration', 'Average Eff Duration Survey'
            elif cl.startswith('average mod duration survey'):
                typ, desc = 'Duration', 'Average Mod Duration Survey'
            elif cl.startswith('average ytm survey'):
                typ, desc = 'Yield to Maturity – Aggregated Metrics', 'Average YTM Survey'
            elif cl.startswith('average eff maturity survey'):
                typ, desc = 'Maturity (Years)', 'Average Eff Maturity Survey'
            elif 'credit quality survey' in cl:
                typ = 'Credit Quality Survey'
                m = re.search(r'credit quality survey\s+(.*?)\s*(%|\(|$)', col, re.I)
                desc = m.group(1).strip() if m else ''
            elif 'credit rtg' in cl:
                # Aggregated vs detailed credit rating
                if 'avg' in cl:
                    typ, desc = 'Credit Rating – Aggregated Metrics', 'Average'
                elif 'cvrg' in cl:
                    typ, desc = 'Credit Rating – Aggregated Metrics', 'Coverage'
                else:
                    typ = 'Credit Rating – Detailed Breakdown' if 'detail' in cl else 'Credit Rating – Simple Breakdown'
                    m = re.search(r'brkdwn\s+(\S+)', col, re.I)
                    desc = m.group(1).strip() if m else ''
            elif 'ytm' in cl:
                if 'avg' in cl:
                    typ, desc = 'Yield to Maturity – Aggregated Metrics', 'Average'
                else:
                    typ = 'Yield to Maturity Distribution'
                    m = re.search(r'(?:brkdwn\s+(\S+))|(?:detail\s*-\s*(.*?)(?:\(|$))', col, re.I)
                    desc = (m.group(1) or m.group(2)).strip() if m else ''
            elif 'duration' in cl:
                typ = 'Duration'
                m = re.search(r'duration\s+(.*)', col, re.I)
                desc = m.group(1).strip() if m else ''
            elif 'maturity' in cl:
                # Distinguish days vs years
                if 'day' in cl:
                    typ = 'Maturity (Days)'
                elif 'yr' in cl:
                    typ = 'Maturity (Years)'
                else:
                    typ = 'Maturity'
                m = re.search(r'maturity\s+(.*)', col, re.I)
                desc = m.group(1).strip() if m else ''
            elif 'coupon' in cl:
                typ = 'Coupon Distribution'
                m = re.search(r'coupon\s+(.*?)\s*(\(|$)', col, re.I)
                desc = f'Coupon {m.group(1).strip()}' if m else ''

        # Fallback / cleanup: strip trailing '%' and common suffixes
        if not desc:
            desc = re.sub(r'%.*$', '', col).strip()
        desc = (desc
                .replace('(net)', '')
                .replace('(fi%)', '')
                .replace('(calc)', '')
                .replace('(long rescaled)', '')
                .strip()
        )

        # Warn once if still unparsed
        if ('Unknown' in (cat, typ)) and col not in logged_headers:
            logger.warning("Unparsed header '%s' → (%s, %s, %s)", col, cat, typ, desc)
            logged_headers.add(col)

        return cat, typ, desc

    except Exception:
        # On any unexpected error, log the exception and return safe defaults
        logger.exception("parse_column_name '%s' failed", col)
        return 'Unknown', 'Unknown', 'Unknown'

def melt_and_transform(fp: Path) -> pd.DataFrame:
    """
    Tidy-fy one Exposure workbook:

        • Wide Excel → long pandas  
        • Parse headers via :func:`parse_column_name`  
        • Duplicate US aggregated rows into detailed proxies (empirical fix)  
        • Tag with «date» inferred from filename.

    Returns DataFrame  ~  [fund IDs, date, category, type, description, value].
    """
    try:
        # Capture date tag from filename
        date_str = extract_date_from_filename(fp) or ''

        # Read Excel skipping extraneous header rows
        df = pd.read_excel(fp, header=1, skiprows=[2, 3])

        # First 4 columns are metadata IDs
        ids = list(df.columns[:4])
        logger.info(
            "melt_and_transform › %s – rows:%d headers:%d",
            fp.name,
            len(df),
            len(df.columns) - 4,
        )

        # Unpivot the wide table into long form
        m = df.melt(
            id_vars=ids,
            var_name='original_col',
            value_name='value'
        )

        # Ensure the 'value' column is numeric
        m['value'] = pd.to_numeric(m['value'], errors='coerce')

        # Parse each original header into structured fields
        m[['category','type','description']] = (
            m['original_col']
             .apply(parse_column_name)
             .apply(pd.Series)
        )

        # Disaggregate aggregated 'US' entries if present
        us_agg = m[
            (m['category']=='Equity') &
            (m['type']=='Country (Aggregated)') &
            (m['description']=='US')
        ].copy()
        if not us_agg.empty:
            # Convert to detailed breakdown and append
            us_agg['type'] = 'Detailed Country Breakdown'
            us_agg['description'] = us_agg['original_col'].str.extract(
                r'Country\s+(.*?)\s*%', expand=False
            ).str.strip()
            m = pd.concat([m, us_agg], ignore_index=True)
            logger.info("Added %d duplicated US-detail rows", len(us_agg))

        # Tag and select final columns
        m['date'] = date_str
        return m[ ids + ['date','category','type','description','value'] ]

    except Exception:
        logger.exception("melt_and_transform failed for %s", fp)
        return pd.DataFrame()

# Performance & Volatility Parsers 
def _transform_timeseries_generic(fp: Path, date_row: int, data_row0: int) -> pd.DataFrame:
    """
    Generic loader that unpivots a wide monthly-return feed (CSV or XLSX)
    into tidy long format with columns:
      [Group/Investment, Base Currency, ISIN, Ticker, date, return (cumulative)]

    Steps:
      1) Read file via pandas, choosing CSV vs. Excel by extension.
      2) Look at the given `date_row` to extract the date headers.
      3) Normalize those to datetime, drop any that failed parsing, 
         and warn if any were bad.
      4) Build a “block” consisting of the first 4 metadata columns 
         plus only the good date columns.
      5) Rename the first 4 columns to our standard keys.
      6) Stack the wide block into long form using `.stack()`.
      7) Convert the “return (cumulative)” column to numeric.
      8) QC: warn if any fund has a missing return at the very last date.
      9) Drop any rows still missing a return before returning.
    """
    try:
        # Load the file
        if fp.suffix.lower() == ".csv":
            df = pd.read_csv(fp, header=None)
        else:
            df = pd.read_excel(fp, header=None)

        # Parse the date headers from the specified row, starting at column 4
        raw = df.iloc[date_row, 4:]
        dates = pd.to_datetime(raw, errors="coerce").dt.normalize()
        good = ~dates.isna()
        if not good.any():
            logger.error("No valid date headers in %s", fp)
            return pd.DataFrame()
        if good.sum() != len(raw):
            logger.warning(
                "%s: %d bad date headers dropped",
                fp.name,
                (~good).sum()
            )
        col_idx = raw.index[good]
        dates = dates[good]
        last_dt = dates.iloc[-1]
        
        # Build the tidy block: first 4 meta cols + good date cols
        meta = df.iloc[data_row0:, :4]
        data = df.iloc[data_row0:, col_idx].set_axis(dates, axis=1)
        block = meta.join(data)

        # Rename the first four columns to our canonical keys
        block = block.rename(columns={
            0: REQUIRED_KEY,
            1: "Base Currency",
            2: "ISIN",
            3: "Ticker",
        })

        # Stack into long form
        tidy = (
            block
            .set_index([REQUIRED_KEY, "Base Currency", "ISIN", "Ticker"])
            .stack()
            .reset_index()
        )
        tidy.columns = [
            REQUIRED_KEY,
            "Base Currency",
            "ISIN",
            "Ticker",
            "date",
            "return (cumulative)",
        ]

        # Ensure returns are numeric
        tidy["return (cumulative)"] = pd.to_numeric(
            tidy["return (cumulative)"], errors="coerce"
        )

        # QC: warn if any fund missing a return on the last date
        nan_last = tidy[
            (tidy["date"] == last_dt)
            & tidy["return (cumulative)"].isna()
        ]
        for inst in nan_last[REQUIRED_KEY].unique():
            logger.warning("Missing return for %s at %s", inst, last_dt.date())

        # Log summary and return cleaned data
        logger.info(
            "Parsed %s – %d instruments × %d months",
            fp.name,
            tidy[REQUIRED_KEY].nunique(),
            len(dates),
        )
        return tidy.dropna(subset=["return (cumulative)"])

    except Exception:
        logger.exception("_transform_timeseries_generic failed for %s", fp)
        return pd.DataFrame()

# Shorthand lambdas for each specific performance feed
transform_tprd_timeseries = lambda fp: _transform_timeseries_generic(fp, 8, 12)
transform_pprd_timeseries = lambda fp: _transform_timeseries_generic(fp, 8, 12)
transform_pmrd_timeseries = lambda fp: _transform_timeseries_generic(fp, 8, 12)
transform_hprd_timeseries = lambda fp: _transform_timeseries_generic(fp, 1, 5)
transform_hmrd_timeseries = lambda fp: _transform_timeseries_generic(fp, 1, 5)

def transform_volatility_timeseries(fp: Path) -> pd.DataFrame:
    """
    Parse a volatility ("Std Dev") feed into tidy long form.

    - Reads CSV vs. Excel by suffix.
    - Looks at row 8 for dates, row 9 for the "Std Dev" label.
    - Forward-fills the date series so each Std Dev column lines up with
      its return neighbor.
    - Stacks into:
        [Group/Investment, Base Currency, ISIN, Ticker, date, volatility]
    - Warns if any instrument has missing volatility on the last date.
    """
    try:
        # Load file
        if fp.suffix.lower() == ".csv":
            df = pd.read_csv(fp, header=None)
        else:
            df = pd.read_excel(fp, header=None)

        # Layout constants
        DATE_ROW = 8
        KIND_ROW = 9
        DATA_ROW0 = 12
        META_END = 4

        # Extract and normalize dates, forward-fill blanks
        raw_dates = df.iloc[DATE_ROW, META_END:]
        dates = (
            pd.to_datetime(raw_dates, errors="coerce")
            .dt.normalize()
            .ffill()
        )

        # Identify which of those columns are Std Dev
        kind_row = df.iloc[KIND_ROW, META_END:].astype(str).str.lower()
        vol_mask = kind_row.str.contains(r"std\s*dev")
        if not vol_mask.any():
            logger.error("No 'Std Dev' columns detected in %s", fp)
            return pd.DataFrame()

        # Keep only the Std Dev columns
        idx = raw_dates.index[vol_mask]
        dates = dates[vol_mask]
        last_dt = dates.iloc[-1]

        # Build block: meta cols + volatility cols
        meta = df.iloc[DATA_ROW0:, :META_END]
        data = df.iloc[DATA_ROW0:, idx].set_axis(dates, axis=1)
        block = meta.join(data)

        # Standardize column names
        block = block.rename(columns={
            0: REQUIRED_KEY,
            1: "Base Currency",
            2: "ISIN",
            3: "Ticker",
        })

        # Stack into long form
        tidy = (
            block
            .set_index([REQUIRED_KEY, "Base Currency", "ISIN", "Ticker"])
            .stack()
            .reset_index()
        )
        tidy.columns = [
            REQUIRED_KEY,
            "Base Currency",
            "ISIN",
            "Ticker",
            "date",
            "volatility",
        ]

        # Warn if latest volatility is missing
        nan_last = tidy[
            (tidy["date"] == last_dt)
            & tidy["volatility"].isna()
        ]
        for inst in nan_last[REQUIRED_KEY].unique():
            logger.warning("Missing vol for %s at %s", inst, last_dt.date())

        # Return only valid rows
        return tidy.dropna(subset=["volatility"])

    except Exception:
        logger.exception("transform_volatility_timeseries failed for %s", fp)
        return pd.DataFrame()

# Folder-Level Consolidators 
def consolidate_exposures(folder: Path) -> pd.DataFrame:
    """
    Read all Exposure workbooks (*.xlsx) in `folder`, parse each with
    melt_and_transform(), and concatenate them into a single DataFrame.
    """
    files = list(folder.glob("*.xlsx"))
    if not files:
        logger.warning("No exposure files in %s", folder)
        return pd.DataFrame()
    logger.info("consolidate_exposures › %d files", len(files))
    return pd.concat([melt_and_transform(f) for f in files], ignore_index=True)

def consolidate_code_tables_in_folder(folder: Path) -> pd.DataFrame:
    """
    Read all code-table Excel files (*.xlsx) in `folder`, rename columns
    to a standard set, tag each with its date (from filename), and
    concatenate into one holdings DataFrame.
    """
    files = list(folder.glob("*.xlsx"))
    if not files:
        logger.warning("No code tables in %s", folder)
        return pd.DataFrame()

    logger.info("consolidate_code_tables › %d files", len(files))
    frames: List[pd.DataFrame] = []

    for f in files:
        df = pd.read_excel(f)
        # Normalize column names
        df.rename(
            columns={
                "Code": "code",
                "Name": "name",
                "weight (%)": "weight"
            },
            inplace=True,
            errors="ignore",
        )
        # Extract date tag
        df["date"] = extract_date_from_filename(f)
        frames.append(df)

    return pd.concat(frames, ignore_index=True)


# Main Execution Block: Orchestrates the entire pipeline 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Consolidate Morningstar-style fund files and calculate reporting metrics."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.getenv("FUND_REPORTING_ROOT", ".")),
        help="Project data root. Defaults to FUND_REPORTING_ROOT or the current directory.",
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    log_file = root / "automation_process.log"
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)

    start_ts = time.perf_counter()     # start stopwatch
    try:
        # Generate a run tag (YYYYMMDD) for this session’s snapshots
        run_tag = datetime.now().strftime("%Y%m%d")
        logger.info("Script started – run tag %s", run_tag)

        # Define key folders and ensure outputs exist
        base     = root / "01_MorningStar"
        daily    = base / "01_Daily"
        output   = base / "Output"
        output.mkdir(parents=True, exist_ok=True)  # create if missing
        hist_dir = output / "Hist"
        hist_dir.mkdir(exist_ok=True)            # for previous-run snapshots
        bm_path  = root / "Reporting Docs" / "Benchmark_Mapping.xlsx"

        # Locate Performance & Volatility source folders
        perf_root = daily / "01_Performance Data"
        vol_root  = daily / "02_Volatility Data" / "TPRD"

        # Process each performance feed in turn
        # List of tuples: (feed tag, filename pattern, transformer function)
        ret_sources: List[tuple[str, str, Callable[[Path], pd.DataFrame]]] = [
            ("01_TPRD", "TPRD_*.csv",          transform_tprd_timeseries),
            ("02_HPRD", "HPRD_COMBINED_*.xlsx", transform_hprd_timeseries),
            ("03_PPRD", "PPRD_*.csv",          transform_pprd_timeseries),
            ("04_HMRD", "HMRD_COMBINED_*.xlsx", transform_hmrd_timeseries),
            ("05_PMRD", "PMRD_*.csv",          transform_pmrd_timeseries),
        ]

        ret_frames: List[pd.DataFrame] = []
        report_files: List[Path] = []
        # For each feed: find the latest file, transform, tag with feed, collect
        for tag, pattern, fn in ret_sources:
            fp = find_most_recent_file(perf_root / tag, pattern)
            if not fp:
                continue
            report_files.append(fp)
            df = fn(fp)
            if df.empty:
                continue
            df["type"] = tag      # record which feed produced these rows
            ret_frames.append(df)
            logger.info("%s › %d rows", tag, len(df))
        if not report_files:
            logger.error("No performance feeds found – aborting run.")
            raise SystemExit(1)        
        report_date_dt  = infer_report_date(report_files)
        report_date_str = report_date_dt.strftime("%Y-%m-%d")
        logger.info("Reporting date inferred as %s", report_date_str)


        # Combine all feeds into one DataFrame
        df_ret = pd.concat(ret_frames, ignore_index=True) if ret_frames else pd.DataFrame()
        logger.info("df_ret shape %s", df_ret.shape)

        # Process volatility feed (TPRD_VOL_*.csv)
        vol_fp = find_most_recent_file(vol_root, "TPRD_VOL_*.csv")
        if not vol_fp:
            logger.warning("Volatility file missing – proceeding without vol data")
            df_vol = pd.DataFrame()
        else:
            df_vol = transform_volatility_timeseries(vol_fp)
        logger.info("df_vol shape %s", df_vol.shape)

        # Merge performance + volatility on the key columns, outer join
        if not df_ret.empty or not df_vol.empty:
            df_daily = pd.merge(
                df_ret,
                df_vol,
                on=[REQUIRED_KEY, "Base Currency", "ISIN", "Ticker", "date"],
                how="outer",
            )
            # Remove any exact duplicates across feeds
            df_daily = dedup_by_source(df_daily, [REQUIRED_KEY, "date", "type"], "type")
        else:
            df_daily = pd.DataFrame()
        logger.info("df_daily shape %s", df_daily.shape)

        # Monthly exposures and holdings
        exp_root = base / "02_Monthly" / "02_Exposures Data"
        # Exposures: three subfolders
        if exp_root.exists():
            df_exposures = pd.concat(
                [
                    consolidate_exposures(exp_root / sub).assign(source=sub)
                    for sub in ["03_ESRD", "04_EGRD", "05_FIRD"]
                ],
                ignore_index=True,
            )
        else:
            df_exposures = pd.DataFrame()
        logger.info("df_exposures shape %s", df_exposures.shape)

        # Holdings: two subfolders of code tables
        if exp_root.exists():
            df_codes = pd.concat(
                [
                    consolidate_code_tables_in_folder(exp_root / sub).assign(source=sub)
                    for sub in ["01_THRD", "02_CVRD"]
                ],
                ignore_index=True,
            )
            # Deduplicate holdings by name, date, source, code
            df_codes = dedup_by_source(df_codes, ["name", "date", "source","code"], "source")
        else:
            df_codes = pd.DataFrame()
        logger.info("df_codes shape %s", df_codes.shape)

        # Write out the latest clean CSVs
        df_daily.to_csv(output / "Performance.csv", index=False)
        df_exposures.to_csv(output / "Characteristic.csv", index=False)
        df_codes.to_csv(output / "Holdings.csv", index=False)
        logger.info("Latest CSVs written to %s", output)

        # Load previous-run snapshots for change detection
        prev_perf = hist_dir / "Performance_prev.csv"
        prev_exp  = hist_dir / "Characteristic_prev.csv"
        prev_hold = hist_dir / "Holdings_prev.csv"

        prev_perf_df = read_prev_csv(prev_perf)
        prev_exp_df  = read_prev_csv(prev_exp)
        prev_hold_df = read_prev_csv(prev_hold)

        # Compare NEW vs. OLD snapshots to log structural changes from MorningStar

        # Performance: detect new or removed funds, attempt auto-rename
        if prev_perf_df is not None and not df_daily.empty:
            new_set = set(df_daily[REQUIRED_KEY])
            old_set = set(prev_perf_df[REQUIRED_KEY])
            added, removed = new_set - old_set, old_set - new_set

            if added:
                logger.warning("New funds detected: %s", sorted(added))
            if removed:
                logger.warning("Funds removed: %s", sorted(removed))

            # Auto-rename logic: correlate return paths ρ > 0.9
            try:
                pn = df_daily.pivot_table(
                    index="date", columns=REQUIRED_KEY,
                    values="return (cumulative)", aggfunc="first"
                )
                pp = prev_perf_df.pivot_table(
                    index="date", columns=REQUIRED_KEY,
                    values="return (cumulative)", aggfunc="first"
                )
                corr_renames: Dict[str,str] = {}
                for new_name in added.copy():
                    if new_name not in pn: continue
                    sn = pn[new_name].dropna()
                    best_corr, best_old = 0.0, None
                    for old_name in removed.copy():
                        if old_name not in pp: continue
                        so = pp[old_name].dropna()
                        common = sn.index.intersection(so.index)
                        if len(common) >= 12:
                            c, _ = pearsonr(sn.loc[common], so.loc[common])
                            if c > best_corr:
                                best_corr, best_old = c, old_name
                    if best_corr > 0.9 and best_old:
                        logger.info(
                            "Auto-rename › '%s' → '%s' (ρ=%.2f)",
                            best_old, new_name, best_corr
                        )
                        corr_renames[best_old] = new_name
                        added.remove(new_name)
                        removed.remove(best_old)
                if corr_renames:
                    logger.info("Rename mapping: %s", corr_renames)
            except Exception:
                logger.exception("Correlation-based rename step failed")

        # Exposures: new/missing funds
        if prev_exp_df is not None and not df_exposures.empty:
            curr_keys = df_exposures[REQUIRED_KEY].dropna().astype(str)
            prev_keys = prev_exp_df[REQUIRED_KEY].dropna().astype(str)
            add_e = set(curr_keys) - set(prev_keys)
            miss_e = set(prev_keys) - set(curr_keys)
            if add_e:
                logger.warning("New exposure funds: %s", sorted(add_e))
            if miss_e:
                logger.warning("Missing exposure funds: %s", sorted(miss_e))

        # Holdings: new/missing codes
        if prev_hold_df is not None and not df_codes.empty and "name" in df_codes.columns:
            add_c  = set(df_codes["name"]) - set(prev_hold_df["name"])
            miss_c = set(prev_hold_df["name"]) - set(df_codes["name"])
            if add_c:
                logger.warning("New codes: %s", sorted(add_c))
            if miss_c:
                logger.warning("Codes removed: %s", sorted(miss_c))

        # Save snapshots for the next run
        write_prev_csv(df_daily,    prev_perf, run_tag)
        write_prev_csv(df_exposures, prev_exp,  run_tag)
        write_prev_csv(df_codes,     prev_hold, run_tag)

        # PERFORMANCE-METRICS GENERATION & ARCHIVING 
        try:
            # prep & inputs 
            bench_map_df = pd.read_excel(bm_path, "Benchmark")
            outpref_df   = pd.read_excel(bm_path, "Output Metric")

            pa = PerformanceAnalytics.from_performance_csv(
                output / "Performance.csv",
                bench_map_df,
                output_metric=outpref_df,
                rf=0.0,
                rolling_window=12,
            )

            # per-year file names 
            current_year      = report_date_dt.year
            metrics_fname     = f"All_Perf_Metrics{current_year}.csv"
            metrics_path      = output / metrics_fname

            hist_metrics_dir  = output / "All Perf Metrics Hist"
            hist_metrics_dir.mkdir(exist_ok=True)

            # Move any completed-year files (≠ current_year) to Hist/
            for fp in output.glob("All_Perf_Metrics*.csv"):
                m = re.match(r"All_Perf_Metrics(\d{4})\.csv", fp.name)
                if m and int(m.group(1)) != current_year:
                    dest = hist_metrics_dir / fp.name
                    if dest.exists():
                        dest.unlink()          # replace if old copy there
                    fp.rename(dest)
                    logger.info("Archived %s → Hist", fp.name)

            # build/append this run’s rows 
            metrics_new = (
                pa.analyse_all(scale=100)
                  .round(3)
                  .assign(ReportDate=report_date_str)
            )

            if metrics_path.exists():
                prev = pd.read_csv(metrics_path, parse_dates=["ReportDate"])
                # keep rows that are *not* from today’s report, then append
                prev = prev[prev["ReportDate"] != report_date_str]
                metrics_all = pd.concat([prev, metrics_new], ignore_index=True)
            else:
                metrics_all = metrics_new

            metrics_all.sort_values(["ReportDate", "instrument"], inplace=True)
            metrics_all.to_csv(metrics_path, index=False, date_format="%Y-%m-%d")

            logger.info(
                "Performance metrics updated → %s  (rows added: %d; file spans %d dates)",
                metrics_fname,
                len(metrics_new),
                metrics_all["ReportDate"].nunique(),
            )

        except Exception:
            logger.exception("PerformanceAnalytics failed")

        # Completion message
        elapsed = time.perf_counter() - start_ts
        logger.info("Run completed successfully ✔ (%.2f s)", elapsed)
        # Write a one-liner summary 
        with open(output / "run_time.txt", "w", encoding="utf-8") as f:
            f.write(f"Automation pipeline run on {run_tag} finished in {elapsed:,.2f} seconds.\n")
            
        print("Done running!")

    except Exception:
        # Catch any critical error that aborts the run
        logger.exception("Critical error – run aborted")
        print(f"Error – check {log_file}")
