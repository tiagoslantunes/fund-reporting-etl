"""
corr_threshold_validation.py


Purpose:
Demonstrate quantitatively that using a ρ ≥ 0.90 cut-off in the
auto-rename step (see automation_pipeline.py) yields an extremely low
Type-I error probability—i.e. the risk of wrongly concluding that two
distinct funds are actually the same instrument is virtually zero once
we have ≥ 24 months of overlapping data.

Methodology:
1.  Analytic p-value  
    For a sample correlation r computed from `n` i.i.d. observations drawn
    from a bivariate Normal with true correlation ρ₀ = 0, the test statistic

        t = r √(n−2) / √(1−r²)      ∼  t₍ν=n−2₎

    A two-sided false-alarm probability is therefore

        p = 2 · (1 − Fₜ(|t|, ν)).

2.  Monte-Carlo sanity check
    Draw 20 000 pairs of independent `𝒩(0,1)` vectors of length n and
    record how often |r| ≥ 0.90.  This empirical rate should agree with the
    analytic p-value.

Results on 2 Jul 2025:
``analytic_df`` prints

    Months (n)   Pr(|r| ≥ 0.90):
           12    6.64 × 10⁻⁵
           24    2.16 × 10⁻⁹
           36    8.22 × 10⁻¹⁴
           60    < 10⁻¹⁶  (machine zero)
          120    < 10⁻¹⁶

Monte-Carlo for *n* = 36 gave `0 / 20 000 = 0`, fully consistent with the
8 × 10⁻¹⁴ analytic probability.

Conclusion:
A 0.90 threshold provides protection even at 24 months; by 36 months
the false-positive rate is astronomically small.  Thus the heuristic is safe
for production use.
"""

import math
import numpy as np
import pandas as pd
from scipy.stats import t


def analytic_pvalue(r: float, n: int) -> float:
    """
    Two-sided p-value  P(|R| ≥ r | ρ₀ = 0) using the exact t distribution.

    Parameters:
    r : float
        Observed sample correlation coefficient.
    n : int
        Sample size (number of paired observations).

    Returns:
    float
        Probability under the null that |R| is at least as extreme as r.
    """
    df = n - 2                                                # degrees of freedom
    t0 = r * math.sqrt(df) / math.sqrt(1 - r**2)             # Fisher t-statistic
    return 2 * (1 - t.cdf(t0, df))                           # two-sided tail


# Analytic probabilities for several window lengths 
n_vals = [12, 24, 36, 60, 120]
rows = [
    {"Months (n)": n, "Pr(|r| ≥ 0.90)": analytic_pvalue(0.9, n)}
    for n in n_vals
]
analytic_df = pd.DataFrame(rows)

# Monte-Carlo check (n = 36 months) 
def monte_carlo_prob(r: float, n: int, trials: int = 20_000) -> float:
    """
    Empirical estimate of P(|R| ≥ r) by brute-force simulation.

    Fixes RNG seed for reproducibility.
    """
    rng = np.random.default_rng(42)
    cnt = 0
    for _ in range(trials):
        a = rng.normal(size=n)
        b = rng.normal(size=n)
        if abs(np.corrcoef(a, b)[0, 1]) >= r:
            cnt += 1
    return cnt / trials


mc_prob_36 = monte_carlo_prob(0.9, 36)

# Display outputs (when run interactively) 
if __name__ == "__main__":
    pd.set_option("display.float_format", "{:.3e}".format)
    print("\nAnalytic tail probabilities for ρ ≥ 0.90 under H₀: ρ = 0\n")
    print(analytic_df.to_string(index=False))
    print(f"\nMonte-Carlo estimate for n = 36:  {mc_prob_36:.3e}\n")
