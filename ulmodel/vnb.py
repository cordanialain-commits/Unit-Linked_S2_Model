"""Value of new business: risk-neutral projection of future new business cohorts,
discounted to the valuation date, by product and month of issue."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import L, LINES, project

# (column, line, sign) - VNB = sum of the signed components
COMPONENTS = [
    ("pv_premium_loading", None, 1),          # premium - allocated premium
    ("pv_amc", "amc", 1), ("pv_policy_fees", "policy_fee", 1), ("pv_coi_charges", "coi", 1),
    ("pv_surrender_penalties", "surrender_penalty", 1), ("pv_rebates", "rebate", 1),
    ("pv_death_strain", "death_strain", -1),
    ("pv_expenses_per_policy", "exp_maintenance", -1),
    ("pv_expenses_pct_fund", "exp_investment", -1),
    ("pv_claim_expenses", "exp_claim", -1),
    ("pv_trail_commission", "comm_trail", -1),
    ("pv_initial_commission", "init_commission", -1),
    ("pv_acquisition_expenses", "acq_expense", -1),
]
FLOWS = [("pv_death_claims", "death_claims"), ("pv_surrender_claims", "surrender_claims"),
         ("pv_partial_surrenders", "partial_claims"),
         ("pv_maturity_claims", "maturity_claims")]


def compute(prod, nb, a, curves, scen, H: int, chunk_size: int = 5000) -> pd.DataFrame:
    """One row per issue month (column 'month' = k, issue at the start of month k).

    Monetary columns are present values at the valuation date on the central curve, except
    'premium' (undiscounted). VNB is before tax and before risk margin.
    """
    base = scen.base_only()
    nb = nb.capped(H)
    pv = np.zeros((len(LINES), nb.n))
    bel = np.zeros(nb.n)
    for lo in range(0, nb.n, chunk_size):
        chunk = nb.slice(lo, lo + chunk_size)
        p = project(chunk, prod, a, base, curves, H, pv_by_mp=True)
        pv[:, lo:lo + chunk_size] = p.pv_mp
        bel[lo:lo + chunk_size] = p.bel[0]

    g = lambda name: pv[L[name]]  # noqa: E731
    d = {"month": nb.entry, "model_points": np.ones(nb.n), "policies": nb.count,
         "premium": nb.count * nb.prem, "pv_premium": g("premium")}
    for col, line, sign in COMPONENTS:
        d[col] = sign * (g(line) if line else g("premium") - g("allocated_premium"))
    d["vnb"] = sum(d[col] for col, _, _ in COMPONENTS)
    d["vnb_net_of_tax"] = d["vnb"] * (1.0 - a.tax_rate)
    for col, line in FLOWS:
        d[col] = g(line)
    d["bel"] = bel
    d["check_vnb_plus_bel"] = d["vnb"] + bel       # risk-neutral identity: must be 0
    out = pd.DataFrame(d).groupby("month", as_index=False).sum()
    out.insert(0, "product", prod.code)
    out["vnb_margin"] = out["vnb"] / out["pv_premium"]
    return out
