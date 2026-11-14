"""Solvency II standard formula: losses per shock and aggregation into SCR / risk margin."""

from __future__ import annotations

import numpy as np

from .engine import L, Projection
from .inputs import Assumptions, LIFE_RISKS, MARKET_RISKS, ModelPoints, Product
from .scenarios import BASE, FLOORED, MASS_LAPSE, ScenarioSet


def scenario_table(proj: Projection, mp: ModelPoints, prod: Product, a: Assumptions,
                   scen: ScenarioSet, exp_scale: float = 1.0) -> dict:
    """Per scenario: fund value, BEL, net asset value and loss of own funds for one chunk.

    Net asset value = unit-linked assets (fund value) - BEL.
    'loss' is the loss of basic own funds used for the SCR; for the life shocks listed in
    FLOORED it is the sum over model points of max(loss, 0) when policy-level flooring is on.
    """
    nav = proj.fv0 - proj.bel                       # (S, N)
    dnav = nav[0] - nav                             # loss per model point
    out = {}
    for s, name in enumerate(scen.names):
        floor = a.policy_level_flooring and name in FLOORED
        out[name] = {
            "fv0": proj.fv0[s].sum(), "bel": proj.bel[s].sum(),
            "nav": nav[s].sum(), "delta_nav": dnav[s].sum(),
            "loss": np.maximum(dnav[s], 0.0).sum() if floor else dnav[s].sum(),
        }
    # mass lapse: a proportion m of the policies surrenders immediately
    m = a.shocks["lapse_mass"]
    bel0 = proj.bel[0]
    loss_mp = m * (proj.sv0 + mp.count * prod.exp_claim * exp_scale - bel0)
    bel_mass = bel0 + loss_mp
    out[MASS_LAPSE] = {
        "fv0": proj.fv0[0].sum(), "bel": bel_mass.sum(),
        "nav": (proj.fv0[0] - bel_mass).sum(), "delta_nav": loss_mp.sum(),
        "loss": np.maximum(loss_mp, 0.0).sum() if a.policy_level_flooring else loss_mp.sum(),
    }
    return out


def add_tables(t1: dict | None, t2: dict) -> dict:
    if t1 is None:
        return {k: dict(v) for k, v in t2.items()}
    return {k: {m: t1[k][m] + t2[k][m] for m in t2[k]} for k in t2}


def _agg(values: np.ndarray, corr: np.ndarray) -> float:
    return float(np.sqrt(max(values @ corr @ values, 0.0)))


def aggregate(table: dict, a: Assumptions, opex_12m: float, runoff: np.ndarray,
              df_annual: np.ndarray) -> dict:
    """Aggregate shock losses into SCR market / life / BSCR / SCR and the risk margin.

    runoff    : (Y,) SCR run-off pattern at the start of each projection year (1 at t = 0)
    df_annual : (Y+1,) central discount factors at annual points
    """
    sh = a.shocks
    loss = {k: max(v["loss"], 0.0) for k, v in table.items() if k != BASE}
    r = {}

    # ---- market
    r["ir_up"], r["ir_down"] = loss["IR_UP"], loss["IR_DOWN"]
    r["interest"] = max(r["ir_up"], r["ir_down"])
    down_bites = r["ir_down"] > r["ir_up"]
    r["ir_scenario"] = "down" if down_bites else ("up" if r["ir_up"] > 0 else "none")
    r["eq_t1"], r["eq_t2"] = loss["EQUITY_T1"], loss["EQUITY_T2"]
    c = sh["equity_corr_t1_t2"]
    r["equity"] = float(np.sqrt(r["eq_t1"] ** 2 + 2 * c * r["eq_t1"] * r["eq_t2"] + r["eq_t2"] ** 2))
    r["property"], r["spread"], r["currency"] = loss["PROPERTY"], loss["SPREAD"], loss["CURRENCY"]
    corr = a.corr_market.copy()
    if not down_bites:      # Corr_Market holds the 'down' matrix; replace parameter A
        i = MARKET_RISKS.index("interest")
        for name in ("equity", "property", "spread"):
            j = MARKET_RISKS.index(name)
            corr[i, j] = corr[j, i] = sh["corr_interest_a_up"]
    mkt = np.array([r[k] for k in MARKET_RISKS])
    r["scr_market"] = _agg(mkt, corr)
    r["div_market"] = r["scr_market"] - mkt.sum()

    # ---- life underwriting
    r["mortality"], r["longevity"] = loss["MORTALITY"], loss["LONGEVITY"]
    r["lapse_up"], r["lapse_down"], r["lapse_mass"] = (
        loss["LAPSE_UP"], loss["LAPSE_DOWN"], loss[MASS_LAPSE])
    r["lapse"] = max(r["lapse_up"], r["lapse_down"], r["lapse_mass"])
    r["lapse_scenario"] = max(("up", "down", "mass"), key=lambda k: r[f"lapse_{k}"]) \
        if r["lapse"] > 0 else "none"
    r["expense"], r["cat"] = loss["EXPENSE"], loss["LIFE_CAT"]
    life = np.array([r[k] for k in LIFE_RISKS])
    r["scr_life"] = _agg(life, a.corr_life)
    r["div_life"] = r["scr_life"] - life.sum()

    # ---- BSCR, operational risk, SCR
    m, l_, c = r["scr_market"], r["scr_life"], sh["corr_market_life"]
    r["bscr"] = float(np.sqrt(m * m + 2 * c * m * l_ + l_ * l_))
    r["div_bscr"] = r["bscr"] - m - l_
    r["scr_op"] = sh["op_risk_expense_factor"] * opex_12m
    r["scr"] = r["bscr"] + r["scr_op"]

    # ---- risk margin: cost of capital on the non-hedgeable SCR (life + operational),
    #      projected with the run-off pattern
    r["scr_rm0"] = r["scr_life"] + r["scr_op"]
    r["risk_margin"] = float(a.coc_rate * (r["scr_rm0"] * runoff * df_annual[1:]).sum())
    return r


def opex_12m(acc0: np.ndarray) -> float:
    """Central administrative expenses of the first 12 months (proxy for the last 12)."""
    return float(acc0[:12][:, [L["exp_maintenance"], L["exp_investment"], L["exp_claim"]]].sum())


def runoff_pattern(acc0: np.ndarray, fv0: float, df: np.ndarray, driver: str) -> np.ndarray:
    """SCR(t) / SCR(0) at t = 0, 12, 24, ... months, from the central projection acc0 (H, lines).

    PVFP       : value of future profits remaining at t (the SCR of unit-linked business is
                 mostly a loss of future margins). Falls back to FUND_VALUE if PVFP(0) <= 0.
    FUND_VALUE : projected unit reserves.
    POLICIES   : policies in force.
    """
    H = acc0.shape[0]
    at = np.arange(0, H, 12)
    if driver == "PVFP":
        tail = (acc0[:, L["profit"]] * df[1:H + 1])[::-1].cumsum()[::-1]     # sum over u > t
        pv = tail[at] / df[at]
        if pv[0] > 0:
            return np.maximum(pv, 0.0) / pv[0]
        driver = "FUND_VALUE"
    if driver == "POLICIES":
        v = acc0[at, L["lives_bop"]]
    else:
        v = np.concatenate([[fv0], acc0[:, L["unit_res_eop"]]])[at]
    return v / v[0] if v[0] > 0 else np.zeros(at.size)
