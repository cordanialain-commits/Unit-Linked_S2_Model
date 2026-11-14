"""Solvency II valuation of a portfolio at a given date: BEL, PVFP, all shocks, SCR, RM.

Used directly by the VALUATION run and, at each 31 December, by the BUSINESS_PLAN run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import scr
from .engine import BEL_SIGNS, L, LINES, combine, project, pv_lines
from .scenarios import BASE


@dataclass
class Result:
    code: str
    name: str
    n_mp: int = 0
    policies: float = 0.0
    table: dict | None = None        # scenario -> fv0 / bel / nav / loss
    acc: np.ndarray | None = None    # (S, H, lines) monthly lines
    pv: np.ndarray | None = None     # (S, lines) present values (nan where not computed)
    metrics: dict = field(default_factory=dict)
    detail: pd.DataFrame | None = None


def _finalise(r: Result, a, curves, scen) -> None:
    df = curves.df[scen.curve]
    r.pv = pv_lines(r.acc, df)
    r.pv[~r.has_acc] = np.nan
    base = r.table[BASE]
    fv0, H = base["fv0"], r.acc.shape[1]
    runoff = scr.runoff_pattern(r.acc[0], fv0, curves.df[0], a.rm_driver)
    m = scr.aggregate(r.table, a, scr.opex_12m(r.acc[0]), runoff, curves.df[0, 0:H + 1:12])
    pvfp = float(r.pv[0, L["profit"]])
    bel_cf = float(combine(lambda n: r.pv[0, L[n]], BEL_SIGNS))
    m.update(
        n_mp=r.n_mp, policies=r.policies, fv0=fv0, bel=base["bel"], pvfp=pvfp,
        pvfp_net=pvfp * (1.0 - a.tax_rate),
        check=fv0 - base["bel"] - pvfp, check_bel=base["bel"] - bel_cf,
        bel_ratio=base["bel"] / fv0 if fv0 else 0.0,
    )
    m["tp"] = m["bel"] + m["risk_margin"]
    m["of"] = fv0 - m["tp"]
    m["scr_ratio"] = m["scr"] / fv0 if fv0 else 0.0
    m["of_ratio"] = m["of"] / m["scr"] if m["scr"] else 0.0
    r.metrics = m


def value(portfolio: list, a, curves, scen, H: int, chunk_size: int = 5000,
          full_accounts: bool = False, exp_scale: float = 1.0, mp_detail: bool = False):
    """portfolio: list of (Product, ModelPoints in force). Returns (results, total)."""
    results = []
    total = Result("TOTAL", "All products")
    for prod, mp in portfolio:
        mp = mp.capped(H)
        r = Result(prod.code, prod.name, n_mp=mp.n, policies=float(mp.count.sum()))
        r.has_acc = np.zeros(scen.n, dtype=bool)
        det = []
        for lo in range(0, mp.n, chunk_size):
            chunk = mp.slice(lo, lo + chunk_size)
            proj = project(chunk, prod, a, scen, curves, H, full_accounts, exp_scale=exp_scale)
            r.table = scr.add_tables(r.table, scr.scenario_table(proj, chunk, prod, a, scen, exp_scale))
            r.acc = proj.acc if r.acc is None else r.acc + proj.acc
            r.has_acc |= proj.has_acc
            if mp_detail:
                det.append(pd.DataFrame({
                    "mp_id": chunk.ids, "policy_count": chunk.count, "fund_value": proj.fv0[0],
                    **{f"bel_{n}": proj.bel[s] for s, n in enumerate(scen.names)}}))
        _finalise(r, a, curves, scen)
        if mp_detail:
            r.detail = pd.concat(det, ignore_index=True)
        results.append(r)

        total.n_mp += r.n_mp
        total.policies += r.policies
        total.table = scr.add_tables(total.table, r.table)
        total.acc = r.acc.copy() if total.acc is None else total.acc + r.acc
        total.has_acc = r.has_acc.copy()
    if results:
        _finalise(total, a, curves, scen)
    return results, total


__all__ = ["Result", "value", "LINES"]
