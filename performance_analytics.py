"""
performance_analytics.py 

Let

    • r_{i,t}  –  cumulative total return (price + income) of fund i
                   at discrete time index *t*  (monthly frequency)
    • b_{j,t}  –  corresponding benchmark return series j  (up to 3 per fund)
    • τ        –  «last date» of the current snapshot

For every look-back window W (e.g., 1 year, YTD, …) we need *aligned*
observations {t ∈ W : t ≤ τ} for BOTH the fund and the benchmark in order
to compute relative measures such as Tracking Error or Excess Return.
Hence:

    1. We **slice** each time series with identical bounds (start, end).  
    2. We enforce `len(slice) == theoretical_length` so that Jan–Dec gaps
       are not silently treated as zero.  
    3. For metric pairs (fund fₜ, benchmark bₜ) we first *align* using
       `pandas.Series.align(join="inner")` to guarantee co-monotone dates.

All metrics are then functions of these *strictly synchronous* vectors,
eliminating temporal look-ahead or mismatched trading calendars.

Implemented outputs:
Absolute metrics (fund only)
    • Cumulative performance                ΔP/P₀
    • Annualised volatility                 √12 · σ_m
    • Sharpe ratio                          (μ_m – r_f/12)/σ_m · √12
    • Max draw-down                         max((cummax – level) / cummax)
    • Downside risk (semi-σ)                √12 · σ(fₜ | fₜ < 0)
    • Positive months hit-rate              𝟙{fₜ > 0}

Benchmark metrics (per mapped b_j)
    Same set as above, calculated on {bₜ}.

Relative metrics (fund vs b_j)
    • Excess performance                    ∏(1+fₜ) – ∏(1+bₜ)
    • Tracking error                        √12 · σ(fₜ – bₜ)
    • Static beta                           Cov/Var
    • Rolling 12-month beta                 β̂₁₂(τ)
    • Hit-rate                              𝟙{fₜ > bₜ}
    • Linear correlation                    ρ(f, b)

Notation key:
    μ_m, σ_m : monthly mean / stdev        (unbiased np.std(ddof=0))
    √12      : annualisation factor for monthly data
    r_f      : risk-free rate              (set to 0 here)

The class is entirely *vectorised* (NumPy + pandas) no Python loops across
dates, only across «fund × feed» groups, keeping runtime ≈ O(N T).

"""


from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Union

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

__all__: list[str] = ["PerformanceAnalytics"]

_EPS: float = 1e-8                          # numerical guard against divisions by zero

# Vendor feed collapsing: TPRD/PPRD/HPRD ⇒ p,  HMRD/PMRD ⇒ m
_P_TAGS = ("TPRD", "PPRD", "HPRD")
_M_TAGS = ("HMRD", "PMRD")


def _collapse_feed(tag: str) -> str:
    """
    Map verbose feed tags to a *single-letter* code:

        'p' – total-return feeds (TPRD, PPRD, HPRD)  
        'm' – market-return (HMRD, PMRD)  
        else lower-cased original tag.
    """
    tag_u = str(tag or "").upper()
    if any(k in tag_u for k in _P_TAGS):
        return "p"
    if any(k in tag_u for k in _M_TAGS):
        return "m"
    return tag_u.lower()


class PerformanceAnalytics:
    """
    Metric engine *(·) –  exposes a single public method 

    Workflow
    --------
    Initialise with tidy returns `returns_df` and benchmark map.
    Call :py:meth:`analyse_all`  →  long DataFrame of metrics.
    """

    _META_COLS: List[str] = ["Base Currency", "ISIN", "Ticker"]

    # Definition of rolling windows (start, end) as λ(τ) ↦ (t₀, τ)
    _WINDOWS = {
        "mtd": lambda τ: (τ.replace(day=1), τ),
        "last_month": lambda τ: (
            τ.replace(day=1) - relativedelta(months=1),
            τ.replace(day=1) - timedelta(days=1),
        ),
        "qtd": lambda τ: (
            datetime(τ.year, ((τ.month - 1) // 3) * 3 + 1, 1),
            τ,
        ),
        "ytd": lambda τ: (datetime(τ.year, 1, 1), τ),
        "1y": lambda τ: (τ - relativedelta(months=11), τ),
        "3y": lambda τ: (τ - relativedelta(months=35), τ),
        "5y": lambda τ: (τ - relativedelta(months=59), τ),
        "since_2023": lambda τ: (datetime(2023, 12, 31) + timedelta(days=1), τ),
    }

    # Construction
    def __init__(
        self,
        returns_df: pd.DataFrame,
        bench_map: Union[pd.DataFrame, Dict[str, Union[str, List[str]]]],
        *,
        output_metric: Union[pd.DataFrame, Dict[str, str], None] = None,
        rf: float = 0.0,
        rolling_window: int = 12,
        logger: logging.Logger | None = None,
    ) -> None:
        """
        Parameters
        ----------
        returns_df : tidy DataFrame with monthly cumulative returns  
                     expected columns = ['instrument','date','return', 'type', …]
        bench_map  : mapping «fund → list ≤3 benchmarks»  
        output_metric : preferred feed tag per instrument 
        rf : risk-free rate (annual, decimal) included in Sharpe  
        rolling_window : length (months) for rolling β estimation  
        logger : optional external logger (falls back to module logger)
        """
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        if not self.logger.handlers:
            h = logging.StreamHandler()
            h.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                  "%Y-%m-%d %H:%M:%S")
            )
            self.logger.addHandler(h)
        self.logger.setLevel(logging.INFO)

        # Data cleansing 
        df = returns_df.copy()
        df.rename(
            columns={
                "Group/Investment": "instrument",
                "return (cumulative)": "return",
            },
            inplace=True,
            errors="ignore",
        )

        needed = {"instrument", "date", "return"} - set(df.columns)
        if needed:
            raise ValueError(f"returns_df missing columns: {needed}")

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["return"] = pd.to_numeric(df["return"], errors="coerce") / 100.0

        if "type" not in df.columns:
            df["type"] = ""
        df["type"] = df["type"].apply(_collapse_feed)

        before = len(df)
        df.dropna(subset=["instrument", "date", "return"], inplace=True)
        self.logger.info("Cleaned %d invalid rows", before - len(df))

        df = (
            df.sort_values(["instrument", "type", "date"])
              .groupby(["instrument", "type", "date"], as_index=False)
              .first()
        )

        # Metadata lookup table 
        self.meta = (
            df.groupby(["instrument", "type"])[self._META_COLS]
              .first()
              .fillna("")
        )

        self.returns = df[["instrument", "type", "date", "return"]]
        self.rf = float(rf)
        self.rolling_window = int(rolling_window)

        # Wide pivot (debug convenience)
        self._wide = (
            self.returns
              .pivot(index="date", columns=["instrument", "type"], values="return")
              .sort_index()
        )

        # Benchmark mapping table 
        if isinstance(bench_map, pd.DataFrame):
            fund_col, *bmk_cols = bench_map.columns[:4]
            self.bench_map = {
                str(row[fund_col]): [
                    str(row[c]).strip()
                    for c in bmk_cols
                    if c in row and pd.notna(row[c]) and str(row[c]).strip()
                ][:3]
                for _, row in bench_map.iterrows()
                if pd.notna(row[fund_col]) and str(row[fund_col]).strip()
            }
        else:  # dict
            self.bench_map = {
                str(k): (
                    [str(v).strip()] if isinstance(v, str)
                    else [str(x).strip() for x in v if str(x).strip()]
                )[:3]
                for k, v in bench_map.items()
            }

        # Feed preference (p vs m) 
        if output_metric is None:
            self.output_pref: Dict[str, str] = {}
        elif isinstance(output_metric, pd.DataFrame):
            inst_col, out_col = output_metric.columns[:2]
            self.output_pref = {
                str(r[inst_col]): str(r[out_col]).lower().strip()
                for _, r in output_metric.iterrows()
                if pd.notna(r[inst_col]) and pd.notna(r[out_col])
            }
        else:
            self.output_pref = {
                str(k): str(v).lower().strip() for k, v in output_metric.items()
            }

    # Internal helpers
    @staticmethod
    def _months_between(a: datetime, b: datetime) -> int:
        """Inclusive #months between two dates a ≤ b."""
        return (b.year - a.year) * 12 + b.month - a.month + 1

    @staticmethod
    def _safe_div(num: float, den: float) -> float:
        """Return `num/den` or NaN if den ≈ 0."""
        return np.nan if den == 0 or np.isnan(den) or abs(den) < _EPS else num / den

    def _rolling_beta(self, f: pd.Series, b: pd.Series) -> float:
        """
        β̂₁₂  –  trailing *self.rolling_window* months:

            β = Cov(f, b) / Var(b)

        Returns NaN if insufficient history or Var(b) ~ 0.
        """
        if len(f) < self.rolling_window:
            return np.nan
        cov = f.rolling(self.rolling_window).cov(b).iloc[-1]
        var = b.rolling(self.rolling_window).var().iloc[-1]
        return np.nan if var < _EPS else cov / var

    # Public API
    def analyse_all(
        self,
        *,
        scale: float = 1.0,
        percent_metrics: set[str] | None = None,
    ) -> pd.DataFrame:
        """
        Vectorised computation of all metrics for every
        (instrument, feed-type) across all defined windows.

        Returns
        -------
        DataFrame
            Long/table: one row = (instrument, window, metric, value, …)
        """
        if percent_metrics is None:
            percent_metrics = {
                "AbsPerf", "Volatility", "MaxDrawdown", "DownsideRisk",
                "TrackingError", "RelPerf",
            }

        rows: List[Tuple] = []
        grouped = (
            self.returns.set_index("date")
                        .groupby(["instrument", "type"])["return"]
        )
        sqrt12 = np.sqrt(12.0)

        # Iterate over fund × feed pairs 
        for (fund, feed), s_f in grouped:
            s_f = s_f.sort_index()
            τ = s_f.index.max()                    # last obs
            base, isin, tick = self.meta.loc[(fund, feed)]

            # Locate up to 3 benchmarks (order preserved)
            bmks: List[Tuple[int, pd.Series]] = []
            for lvl, bname in enumerate(self.bench_map.get(fund, []), start=1):
                if (bname, feed) in grouped.groups:
                    bmks.append((lvl, grouped.get_group((bname, feed)).sort_index()))
                elif bname in grouped.groups:
                    bmks.append((lvl, grouped.get_group(bname).sort_index()))

            # Windows loop 
            for win, bounds in self._WINDOWS.items():
                t0, t1 = bounds(τ)
                needed = self._months_between(t0, t1)
                f_win = s_f.loc[t0:t1]
                if len(f_win) < needed or t1 not in f_win.index:
                    continue  # skip incomplete window

                # Absolute metrics 
                μ_m, σ_m = f_win.mean(), f_win.std(ddof=0)
                vol = σ_m * sqrt12
                sharpe = (
                    self._safe_div(μ_m - self.rf / 12.0, σ_m) * sqrt12
                    if σ_m >= _EPS else np.nan
                )
                pos = (f_win > 0).mean()
                cpath = (1 + f_win).cumprod()
                mdd = ((cpath.cummax() - cpath) / cpath.cummax()).max()
                downside = f_win[f_win < 0].std(ddof=0) * sqrt12

                abs_metrics = {
                    "AbsPerf": cpath.iloc[-1] - 1,
                    "Volatility": vol,
                    "PositiveMonths": pos,
                    "MaxDrawdown": mdd,
                    "Sharpe": sharpe,
                    "DownsideRisk": downside,
                }
                for m, v0 in abs_metrics.items():
                    v = v0 * scale if m in percent_metrics else v0
                    rows.append((fund, base, isin, tick, feed, τ.date(), win, m, v))

                # Benchmarks + relative metrics 
                for lvl, s_b in bmks:
                    b_win = s_b.loc[t0:t1]
                    if len(b_win) < needed or t1 not in b_win.index:
                        continue
                    tag = str(lvl)

                    μ_b, σ_b = b_win.mean(), b_win.std(ddof=0)
                    vol_b = σ_b * sqrt12
                    sharpe_b = (
                        self._safe_div(μ_b - self.rf / 12.0, σ_b) * sqrt12
                        if σ_b >= _EPS else np.nan
                    )
                    pos_b = (b_win > 0).mean()
                    cpath_b = (1 + b_win).cumprod()
                    mdd_b = ((cpath_b.cummax() - cpath_b) / cpath_b.cummax()).max()
                    disadv = b_win[b_win < 0].std(ddof=0) * sqrt12

                    bmk_metrics = {
                        f"BmkAbsPerf{tag}": cpath_b.iloc[-1] - 1,
                        f"BmkVolatility{tag}": vol_b,
                        f"BmkPositiveMonths{tag}": pos_b,
                        f"BmkMaxDrawdown{tag}": mdd_b,
                        f"BmkSharpe{tag}": sharpe_b,
                        f"BmkDownsideRisk{tag}": disadv,
                    }
                    for m, v0 in bmk_metrics.items():
                        raw = re.sub(r"\d+$", "", m.replace("Bmk", ""))
                        v = v0 * scale if raw in percent_metrics else v0
                        rows.append((fund, base, isin, tick, feed, τ.date(), win, m, v))

                    # — Relative metrics (aligned inner join) —
                    f_rel, b_rel = f_win.align(b_win, join="inner")
                    if len(f_rel) < needed or t1 not in f_rel.index:
                        continue

                    rel_perf = (1 + f_rel).prod() - 1 - ((1 + b_rel).prod() - 1)
                    diff = f_rel - b_rel
                    te = diff.std(ddof=0) * sqrt12
                    var_b = b_rel.var(ddof=0)
                    beta = np.nan if var_b < _EPS else f_rel.cov(b_rel) / var_b
                    rbeta = self._rolling_beta(f_rel, b_rel)
                    hit = (f_rel > b_rel).mean()
                    corr = (
                        f_rel.corr(b_rel)
                        if f_rel.std(ddof=0) >= _EPS else np.nan
                    )

                    rel_metrics = {
                        f"RelPerf{tag}": rel_perf,
                        f"TrackingError{tag}": te,
                        f"Beta{tag}": beta,
                        f"RollingBeta{tag}": rbeta,
                        f"HitRate{tag}": hit,
                        f"Correlation{tag}": corr,
                    }
                    for m, v0 in rel_metrics.items():
                        raw = re.sub(r"\d+$", "", m)
                        v = v0 * scale if raw in percent_metrics else v0
                        rows.append((fund, base, isin, tick, feed, t1.date(), win, m, v))

        # Assemble long table 
        cols = [
            "instrument", "Base Currency", "ISIN", "Ticker", "type",
            "LastDate", "Window", "Metric", "Value",
        ]
        out = pd.DataFrame(rows, columns=cols)

        # Feed-type flag for reporting
        out["OutputReport"] = np.where(
            out.apply(lambda r: self.output_pref.get(r["instrument"], "") == r["type"],
                      axis=1),
            "Y", "",
        )
        out = out[cols + ["OutputReport"]]

        self.logger.info("analyse_all finished – %d rows", len(out))
        return out

    # Convenience constructor
    @classmethod
    def from_performance_csv(
        cls,
        filepath: Union[str, Path],
        bench_map: Union[pd.DataFrame, Dict[str, Union[str, List[str]]]],
        *,
        output_metric: Union[pd.DataFrame, Dict[str, str], None] = None,
        **kwargs,
    ) -> "PerformanceAnalytics":
        """
        Helper for one-liner instantiation directly from «Performance.csv».
        """
        df = pd.read_csv(filepath, parse_dates=["date"])
        return cls(df, bench_map, output_metric=output_metric, **kwargs)
