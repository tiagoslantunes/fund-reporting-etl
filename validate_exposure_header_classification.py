"""
validate_exposure_header_classification.py  –  Sanity test for the
`parse_column_name()` heuristic


Rationale:
Morningstar’s Exposure workbooks arrive with a free-text zoo of column
headers (≈175 unique strings across ESRD / EGRD / FIRD).  Our parser
`parse_column_name()` uses regex + keyword heuristics to map each raw header
→ (asset_class, breakdown_type, description).

This validation script acts as an early-warning:

    • It scans all workbooks under  
          «ROOT/01_MorningStar/02_Monthly/02_Exposures Data/».  
    • Extracts the header row only (cheap: nrows=0).  
    • Feeds each header through `parse_column_name`.  
    • Counts how many return "Unknown" for either category or type.

If the unknown proportion exceeds `max_unknown_frac` (default 1 %), we raise
`RuntimeError`, forcing the developer to tighten the heuristic before bad
taxonomy leaks downstream.

Empirically on 2 July 2025 we observe  

    0 / 174  → 0 % unknown < 1 % threshold 

which justifies the heuristic’s adequacy.
"""

import argparse
import logging
from pathlib import Path
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("header-validator")


def validate_exposure_header_classification(
    root: Path,
    max_unknown_frac: float = 0.01,
) -> None:
    """
    Assert that ≤ `max_unknown_frac` of raw exposure headers remain
    unclassified by :func:`parse_column_name`.

    Parameters
    ----------
    root : Path
        Project root (folder that contains *01_MorningStar/* tree).
    max_unknown_frac : float, default 0.01
        Permitted share of headers that may still map to
        ("Unknown", "Unknown", …).

    Raises
    ------
    RuntimeError
        If the unknown-header fraction breaches the threshold.
    """

    # Resolve exposure file locations 
    exposure_root = (
        root
        / "01_MorningStar"
        / "02_Monthly"
        / "02_Exposures Data"
    )

    # Harvest *all* raw header strings (skip 4 meta columns) 
    raw_headers: set[str] = set()
    for sub in ("03_ESRD", "04_EGRD", "05_FIRD"):
        folder = exposure_root / sub
        if not folder.exists():
            continue

        for fp in folder.glob("*.xlsx"):
            # Read header row only – keeps I/O + memory minimal.
            df = pd.read_excel(fp, header=1, nrows=0)
            raw_headers.update(df.columns[4:])  # ignore instrument / ccy / ISIN / ticker

    raw_headers = sorted(raw_headers)

    # Pipe through the heuristic parser 
    from automation_pipeline import parse_column_name

    parsed = [parse_column_name(h) for h in raw_headers]
    unknown = [
        hdr for hdr, (cat, typ, _) in zip(raw_headers, parsed)
        if "Unknown" in (cat, typ)
    ]
    frac_unknown = len(unknown) / len(raw_headers) if raw_headers else 0.0

    logger.info(
        "Header-classification check: %d of %d headers (%.2f %%) are 'Unknown'.",
        len(unknown), len(raw_headers), frac_unknown * 100,
    )

    # Hard fail if heuristic drifts beyond tolerance 
    if frac_unknown > max_unknown_frac:
        logger.error(
            "Unknown-header fraction (%.2f %%) exceeds the limit of %.2f %% — "
            "please refine `parse_column_name()`.",
            frac_unknown * 100, max_unknown_frac * 100,
        )
        raise RuntimeError("Exposure header classification validation failed.")
    else:
        logger.info(
            "Validation passed — unknown-header fraction ≤ %.2f %%.",
            max_unknown_frac * 100,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate exposure header classification.")
    parser.add_argument("--root", type=Path, required=True, help="Project data root.")
    parser.add_argument("--max-unknown-frac", type=float, default=0.01)
    args = parser.parse_args()
    validate_exposure_header_classification(args.root, args.max_unknown_frac)

