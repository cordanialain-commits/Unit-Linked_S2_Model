"""Economic bases (monthly) and definition of the central scenario + Solvency II shocks.

Every scenario is described by a handful of vectors (one entry per scenario) so that the
engine can project all scenarios at once with numpy broadcasting.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .inputs import Assumptions, FUND_COLS

BASE = "BASE"
SCENARIOS = [
    BASE,
    "IR_UP", "IR_DOWN", "EQUITY_T1", "EQUITY_T2", "PROPERTY", "SPREAD", "CURRENCY",
    "MORTALITY", "LONGEVITY", "LAPSE_UP", "LAPSE_DOWN", "EXPENSE", "LIFE_CAT",
]
MASS_LAPSE = "LAPSE_MASS"  # instantaneous shock: evaluated analytically, no projection
# Shocks that only apply to policies for which they increase technical provisions
FLOORED = {"MORTALITY", "LONGEVITY", "LAPSE_UP", "LAPSE_DOWN", "LIFE_CAT", MASS_LAPSE}
NO_LIMIT = 1e9


@dataclass
class Basis:
    """Monthly economic basis.

    df   : (C, M+1) discount factor to the end of month t (df[:, 0] = 1)
    grow : (C, 1 or F, M) fund growth rate in month t (index t-1); the middle axis is the
           fund when growth is fund-specific (real world), 1 otherwise (risk neutral)
    spot : (C, K) annual spot rates the basis was built from (rows: base, up, down)
    """
    df: np.ndarray
    grow: np.ndarray
    spot: np.ndarray


def monthly_df(spot: np.ndarray, months: int) -> np.ndarray:
    """Monthly discount factors from annual spot rates: constant forward rate within each
    year, last annual forward rate kept beyond the last maturity."""
    K = spot.size
    log_df = np.concatenate([[0.0], -np.arange(1, K + 1) * np.log1p(spot)])
    t = np.arange(months + 1) / 12.0
    out = np.interp(t, np.arange(K + 1), log_df)
    beyond = t > K
    out[beyond] = log_df[K] + (log_df[K] - log_df[K - 1]) * (t[beyond] - K)
    return np.exp(out)


def build_curves(spot: np.ndarray, a: Assumptions, months: int) -> Basis:
    """Risk-neutral basis: base, interest-up and interest-down curves."""
    up = spot + np.maximum(spot * a.ir_up_rel, a.shocks["ir_up_min_abs"])
    down = spot - np.maximum(spot, 0.0) * a.ir_down_rel     # negative rates are not shocked
    spots = np.vstack([spot, up, down])
    df = np.vstack([monthly_df(s, months) for s in spots])
    return Basis(df=df, grow=(df[:, :-1] / df[:, 1:] - 1.0)[:, None, :], spot=spots)


def forward_spot(df_base: np.ndarray, h: int, K: int) -> np.ndarray:
    """Annual spot rates (maturities 1..K) of the curve implied at month h by df_base."""
    k = np.arange(1, K + 1)
    return (df_base[h] / df_base[h + 12 * k]) ** (1.0 / k) - 1.0


def real_world_basis(curves: Basis, a: Assumptions) -> Basis:
    """Fund growth = risk-free forward rate + annual risk premium of the fund's asset mix."""
    rf_annual = (1.0 + curves.grow[0, 0]) ** 12 - 1.0                          # (M,)
    grow = (1.0 + rf_annual[None, :] + a.fund_rw_premium[:, None]) ** (1 / 12) - 1.0
    return Basis(df=curves.df[:1], grow=grow[None, :, :], spot=curves.spot[:1])


@dataclass
class ScenarioSet:
    names: list
    curve: np.ndarray        # (S,) row of the basis used by the scenario
    qx_mult: np.ndarray      # (S,)
    qx_add_y1: np.ndarray    # (S,) absolute addition to annual qx during the first 12 months
    lapse_mult: np.ndarray   # (S,)
    lapse_max_dec: np.ndarray  # (S,) maximum absolute decrease of the annual lapse rate
    exp_mult: np.ndarray     # (S,)
    infl_add: np.ndarray     # (S,)
    fund_factor: np.ndarray  # (S, F) instantaneous multiplier applied to fund values at t=0

    @property
    def n(self) -> int:
        return len(self.names)

    def index(self, name: str) -> int:
        return self.names.index(name)

    def base_only(self) -> "ScenarioSet":
        return ScenarioSet([BASE], *[getattr(self, k)[:1] for k in (
            "curve", "qx_mult", "qx_add_y1", "lapse_mult", "lapse_max_dec", "exp_mult",
            "infl_add", "fund_factor")])


def build_scenarios(a: Assumptions, curves: Basis) -> ScenarioSet:
    sh = a.shocks
    S, F = len(SCENARIOS), len(a.fund_codes)
    ix = {n: i for i, n in enumerate(SCENARIOS)}

    curve = np.zeros(S, dtype=np.int64)
    qx_mult = np.ones(S)
    qx_add = np.zeros(S)
    lapse_mult = np.ones(S)
    lapse_dec = np.full(S, NO_LIMIT)
    exp_mult = np.ones(S)
    infl_add = np.zeros(S)
    factor = np.ones((S, F))

    col = {c: i for i, c in enumerate(FUND_COLS)}
    alloc = a.fund_alloc
    bonds = alloc[:, col["govt_bonds"]] + alloc[:, col["corp_bonds"]]

    # --- interest rate: curve shift + repricing of the bond holdings at their duration
    mats = np.arange(1, curves.spot.shape[1] + 1)
    dur = a.fund_duration
    r_base = np.interp(dur, mats, curves.spot[0])
    for name, row in (("IR_UP", 1), ("IR_DOWN", 2)):
        r_shock = np.interp(dur, mats, curves.spot[row])
        price = ((1.0 + r_base) / (1.0 + r_shock)) ** dur
        curve[ix[name]] = row
        factor[ix[name]] = 1.0 + bonds * (price - 1.0)

    # --- other market shocks: instantaneous fall in the value of the exposed assets
    sa = sh["equity_symmetric_adj"]
    factor[ix["EQUITY_T1"]] = 1.0 - alloc[:, col["equity_type1"]] * (sh["equity_type1"] + sa)
    factor[ix["EQUITY_T2"]] = 1.0 - alloc[:, col["equity_type2"]] * (sh["equity_type2"] + sa)
    factor[ix["PROPERTY"]] = 1.0 - alloc[:, col["property"]] * sh["property"]
    factor[ix["SPREAD"]] = 1.0 - alloc[:, col["corp_bonds"]] * a.fund_spread
    factor[ix["CURRENCY"]] = 1.0 - a.fund_fx * sh["currency"]

    # --- life underwriting shocks
    qx_mult[ix["MORTALITY"]] = 1.0 + sh["mortality_up"]
    qx_mult[ix["LONGEVITY"]] = 1.0 - sh["longevity_down"]
    qx_add[ix["LIFE_CAT"]] = sh["life_cat_add"]
    lapse_mult[ix["LAPSE_UP"]] = 1.0 + sh["lapse_up"]
    lapse_mult[ix["LAPSE_DOWN"]] = 1.0 - sh["lapse_down"]
    lapse_dec[ix["LAPSE_DOWN"]] = sh["lapse_down_max_abs"]
    exp_mult[ix["EXPENSE"]] = 1.0 + sh["expense_up"]
    infl_add[ix["EXPENSE"]] = sh["expense_inflation_add"]

    return ScenarioSet(list(SCENARIOS), curve, qx_mult, qx_add, lapse_mult, lapse_dec,
                       exp_mult, infl_add, factor)
