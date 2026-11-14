"""Vectorised monthly projection engine.

All model points of a product are projected together with numpy; the only Python loop is over
the monthly time steps (at most 480).

Optimisations
-------------
* Scenario factorisation. A scenario is an *economic* state (curve + instantaneous fund shock)
  combined with a *decrement* state (mortality / lapse basis). Market shocks share the central
  decrements and life shocks share the central fund path, so scenarios are projected in two
  groups in which the shared dimension is computed once and broadcast.
* Only the BEL is accumulated per model point for the shocked scenarios; the detailed monthly
  lines are computed for the central scenario only (unless full_accounts=True).
* Decrement rates are converted to monthly tables once, then looked up by index.
* The loop stops when the last policy has left; model points are processed in chunks.

Timing within month t (from time t-1 to time t)
  start of month : new business issued in month t enters: single premium, allocation to units,
                   initial commission and acquisition expenses
  during month   : fund growth of the basis
  end of month   : charges (AMC, policy fee, cost of insurance), expenses, trail commission,
                   rebates, then decrements: deaths -> surrenders or maturity
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .inputs import Assumptions, ModelPoints, Product
from .scenarios import Basis, ScenarioSet

LINES = [
    "lives_bop", "new_policies", "deaths", "lapses", "maturities", "lives_eop",
    "premium", "allocated_premium", "init_commission", "acq_expense",
    "investment_return", "rebate",
    "death_claims", "surrender_claims", "partial_claims", "maturity_claims",
    "unit_res_bop", "unit_res_eop",
    "exp_maintenance", "exp_investment", "exp_claim", "comm_trail",
    "amc", "policy_fee", "coi", "surrender_penalty", "death_strain", "profit",
]
L = {name: i for i, name in enumerate(LINES)}
# cash flows occurring at the start of the month (discounted with DF(t-1))
START_LINES = ("premium", "allocated_premium", "init_commission", "acq_expense")
# result by source: profit = sum(sign x line)
PROFIT_SIGNS = {
    "premium": 1, "allocated_premium": -1, "init_commission": -1, "acq_expense": -1,
    "amc": 1, "policy_fee": 1, "coi": 1, "surrender_penalty": 1, "rebate": 1,
    "exp_maintenance": -1, "exp_investment": -1, "exp_claim": -1, "comm_trail": -1,
    "death_strain": -1,
}
# best estimate liability: BEL = sum(sign x PV(line))
BEL_SIGNS = {
    "death_claims": 1, "surrender_claims": 1, "partial_claims": 1, "maturity_claims": 1,
    "exp_maintenance": 1,
    "exp_investment": 1, "exp_claim": 1, "comm_trail": 1, "rebate": -1,
    "init_commission": 1, "acq_expense": 1, "premium": -1,
}


def combine(get, signs: dict):
    """sum(sign x get(line)) - used for profit and BEL on any array layout."""
    return sum(s * get(name) for name, s in signs.items())


def pv_lines(acc: np.ndarray, df: np.ndarray) -> np.ndarray:
    """Present value of every line. acc: (S, H, lines); df: (S, >=H+1). Returns (S, lines)."""
    H = acc.shape[1]
    pv = np.einsum("shl,sh->sl", acc, df[:, 1:H + 1])
    for name in START_LINES:
        pv[:, L[name]] = (acc[:, :, L[name]] * df[:, :H]).sum(axis=1)
    pv[:, L["profit"]] = combine(lambda n: pv[:, L[n]], PROFIT_SIGNS)
    return pv


@dataclass
class Projection:
    bel: np.ndarray       # (S, N) best estimate liability per model point (all policies)
    fv0: np.ndarray       # (S, N) fund value at t=0 after instantaneous shock (all policies)
    sv0: np.ndarray       # (N,)   surrender value at t=0, central scenario (all policies)
    acc: np.ndarray       # (S, H, len(LINES)) monthly aggregates (central only unless full)
    has_acc: np.ndarray   # (S,) scenarios for which acc is filled
    # month -> (policies in force, fund value per policy, premium guarantee per policy)
    snaps: dict = field(default_factory=dict)
    pv_mp: np.ndarray | None = None             # (lines, N) central PV per model point


def _groups(scen: ScenarioSet) -> list:
    """Split scenario indices into [central + market shocks], [life shocks], [others]."""
    s = np.arange(scen.n)
    base_dec = ((scen.qx_mult == 1) & (scen.qx_add_y1 == 0) & (scen.lapse_mult == 1)
                & (scen.exp_mult == 1) & (scen.infl_add == 0))
    base_econ = (scen.curve == scen.curve[0]) & (scen.fund_factor == scen.fund_factor[0]).all(axis=1)
    market = s[base_dec]
    life = s[~base_dec & base_econ]
    other = s[~base_dec & ~base_econ]
    return [g for g in (market, life, other) if g.size]


def project(mp: ModelPoints, prod: Product, a: Assumptions, scen: ScenarioSet, basis: Basis,
            H: int, full_accounts: bool = False, snapshots=(), pv_by_mp: bool = False,
            exp_scale: float = 1.0) -> Projection:
    """Project `mp` for H months.

    snapshots : months at which the central policies in force and fund values are returned
    pv_by_mp  : also return the central present value of every line per model point
    exp_scale : scaling of per-policy / per-claim expenses (inflation to the projection start)
    """
    S, N = scen.n, mp.n
    out = Projection(
        bel=np.empty((S, N)), fv0=scen.fund_factor[:, mp.fund_idx] * (mp.fv * mp.count),
        sv0=np.zeros(N), acc=np.zeros((S, H, len(LINES))), has_acc=np.zeros(S, dtype=bool),
        pv_mp=np.zeros((len(LINES), N)) if pv_by_mp else None)
    for rows in _groups(scen):
        _project_group(rows, mp, prod, a, scen, basis, H, full_accounts, snapshots, exp_scale, out)

    py0 = np.maximum(mp.dur_m, 0) // 12
    sc_now = np.zeros(int(py0.max()) + 1)
    k = min(sc_now.size, prod.surrender_charges.size)
    sc_now[:k] = prod.surrender_charges[:k]
    out.sv0 = mp.fv * mp.count * (1.0 - sc_now[py0])
    if pv_by_mp:
        out.pv_mp[L["profit"]] = combine(lambda n: out.pv_mp[L[n]], PROFIT_SIGNS)
    return out


def _monthly(rate):
    """Monthly rate equivalent to an annual decrement rate."""
    return 1.0 - (1.0 - rate) ** (1.0 / 12.0)


def _project_group(rows, mp, prod, a, scen, basis, H, full, snapshots, exp_scale, out):
    R, N = rows.size, mp.n

    def same(*arrays):
        return all((x[rows] == x[rows[0]]).all() for x in arrays)

    # rows carrying the economic state (E), the decrement state (D) and the expense state (X);
    # each collapses to the first row of the group when identical across the group
    e = rows[:1] if same(scen.curve, scen.fund_factor) else rows
    d = rows[:1] if same(scen.qx_mult, scen.qx_add_y1, scen.lapse_mult, scen.lapse_max_dec) else rows
    x = rows[:1] if same(scen.exp_mult, scen.infl_add) else rows

    central = rows[0] == 0
    want_lines = full or central
    if want_lines:
        out.has_acc[rows if full else rows[:1]] = True
    n_lines = R if full else 1

    def pk(v):
        """Rows used for the detailed lines: all (full) or the first row (central)."""
        if full or np.ndim(v) < 2 or v.shape[0] == 1:
            return v
        return v[:1]

    grow = basis.grow[scen.curve[e]]                            # (E, 1 or F, M)
    by_fund = grow.shape[1] > 1
    df = basis.df[scen.curve[rows]]                             # (R, M+1)

    # ---- monthly decrement tables (annual rate shocked first, then converted to monthly)
    max_age = a.qx.shape[1] - 1
    q_be = np.minimum(a.qx * prod.mort_factor, 1.0)             # (2, ages) annual
    qm_be = _monthly(q_be)
    q_s = q_be[None] * scen.qx_mult[d, None, None]              # (D, 2, ages)
    qm_s = _monthly(np.minimum(q_s, 1.0))
    has_cat = bool(scen.qx_add_y1[d].any())
    qm_cat = _monthly(np.minimum(q_s + scen.qx_add_y1[d, None, None], 1.0)) if has_cat else qm_s

    n_py = (int(max(mp.dur_m.max(), 0)) + H) // 12 + 2          # policy years needed
    lapse_tab = prod.lapse[np.minimum(np.arange(n_py), prod.lapse.size - 1)]
    wm_s = _monthly(np.minimum(
        np.maximum(lapse_tab * scen.lapse_mult[d, None], lapse_tab - scen.lapse_max_dec[d, None]),
        1.0))                                                   # (D, n_py)
    sc_tab = np.zeros(n_py)
    k = min(n_py, prod.surrender_charges.size)
    sc_tab[:k] = prod.surrender_charges[:k]
    # partial surrenders: share of the fund withdrawn; shocked like the lapse rates
    has_pw = bool(prod.partial.any())
    pw_tab = prod.partial[np.minimum(np.arange(n_py), prod.partial.size - 1)]
    pwm_s = _monthly(np.minimum(
        np.maximum(pw_tab * scen.lapse_mult[d, None], pw_tab - scen.lapse_max_dec[d, None]),
        1.0))                                                   # (D, n_py)

    # ---- product features (annual rates applied as one twelfth per month)
    db_pct = max(prod.db_pct, 1.0) if prod.db_type == "FV_PCT" else 1.0
    is_rop = prod.db_type == "ROP"
    sa = mp.sa if prod.db_type == "FV_PLUS_SA" else None
    guar = mp.prem_paid                                         # (N,), or (D, N) once reduced
    exp_mult = scen.exp_mult[x, None]                           # (X, 1)
    infl = (1.0 + a.expense_inflation + scen.infl_add[x])[:, None]
    fv_cost = (prod.exp_investment * exp_mult + (prod.comm_trail - prod.rebate)) / 12.0
    net_amc = 1.0 - prod.amc / 12.0
    fee_m = prod.policy_fee / 12.0
    maint_m = prod.exp_per_policy / 12.0

    def death_benefit(f):
        if is_rop:
            return np.maximum(f, guar)
        return f + sa if sa is not None else f * db_pct

    # ---- state
    in0 = mp.entry == 0
    has_nb = bool((mp.entry > 0).any())
    fv = (mp.fv * in0)[None, :] * scen.fund_factor[e][:, mp.fund_idx]   # (E, N) per policy
    lives = np.broadcast_to(mp.count * in0, (d.size, N)).copy()         # (D, N)
    bel = np.zeros((R, N))
    zero = np.zeros(N)
    last = min(H, int(np.minimum(mp.rem, H).max()))             # nothing in force afterwards

    for t in range(1, last + 1):
        i = t - 1
        age = np.clip((mp.age_m + i) // 12, 0, max_age)         # attained age (years)
        py = np.clip((mp.dur_m + i) // 12, 0, n_py - 1)         # policy year - 1

        # -- start of month: new business issued this month
        new = mp.entry == t if has_nb else None
        if has_nb and new.any():
            n_new = mp.count * new                              # (N,)
            prem = mp.prem * new
            alloc = prem * (1.0 - prod.alloc_charge)
            icomm = prem * prod.comm_initial
            acq = new * (prod.acq_per_policy * exp_scale * (1.0 + a.expense_inflation) ** (i / 12)) \
                + prem * prod.acq_pct_premium
            fv_start = fv
            lives = lives + n_new
            fv_bop = fv + alloc
            bel += df[:, i, None] * (n_new * (icomm + acq - prem))
        else:
            n_new = prem = alloc = icomm = acq = zero
            fv_start = fv_bop = fv

        g = 1.0 + (grow[:, mp.fund_idx, i] if by_fund else grow[:, 0, i, None])   # (E, N | 1)
        fv1 = fv_bop * g

        # -- end of month: charges taken from the fund (never below zero)
        after_amc = fv1 * net_amc
        fv2 = np.maximum(after_amc - fee_m, 0.0) if fee_m else after_amc
        if prod.coi_pct:
            coi = np.minimum((prod.coi_pct * qm_be[mp.sex, age]) * (death_benefit(fv2) - fv2), fv2)
            fv3 = fv2 - coi
        else:
            coi = 0.0
            fv3 = fv2
        db = death_benefit(fv3)

        # -- decrements: deaths, then exits (surrenders, or maturity of all survivors)
        qx = (qm_cat if t <= 12 else qm_s)[:, mp.sex, age]      # (D, N)
        is_mat = mp.rem == t                                    # (N,)
        w = np.where(is_mat, 1.0, wm_s[:, py])                  # (D, N)
        sc = np.where(is_mat, 0.0, sc_tab[py])                  # (N,)
        deaths = lives * qx
        exits = (lives - deaths) * w
        lives_eop = lives - deaths - exits

        # -- best estimate cash flows at end of month
        infl_t = exp_scale * exp_mult * infl ** (i / 12)        # (X, 1)
        claim_exp = prod.exp_claim * infl_t
        exit_value = fv3 * (1.0 - sc) if sc.any() else fv3
        cost_pp = fv1 * fv_cost + maint_m * infl_t              # per policy in force
        cf = deaths * (db + claim_exp) + exits * (exit_value + claim_exp) + lives * cost_pp

        # -- partial surrenders of the policies still in force (after decrements)
        if has_pw:
            pw = pwm_s[:, py] * (mp.entry <= t) if has_nb else pwm_s[:, py]    # (D, N)
            part = fv3 * pw                                     # withdrawn per policy
            sc_pw = sc_tab[py]
            cf = cf + lives_eop * (part * (1.0 - sc_pw))
            fv_end = fv3 - part
        else:
            part, sc_pw, fv_end = 0.0, 0.0, fv3
        bel += df[:, t, None] * cf

        # -- detailed lines (central scenario only unless full accounts are requested)
        if want_lines:
            lv, de, ex, le = pk(lives), pk(deaths), pk(exits), pk(lives_eop)
            f0, fb, f1, f3, dbb = pk(fv_start), pk(fv_bop), pk(fv1), pk(fv3), pk(db)
            it, em, gg = pk(infl_t), pk(exp_mult), pk(g)
            lap, mat = ex * ~is_mat, ex * is_mat
            lv_f1 = lv * f1
            part_tot = le * pk(part)                            # partial surrenders, gross
            vals = {
                "lives_bop": lv, "new_policies": n_new, "deaths": de, "lapses": lap,
                "maturities": mat, "lives_eop": le,
                "premium": n_new * prem, "allocated_premium": n_new * alloc,
                "init_commission": n_new * icomm, "acq_expense": n_new * acq,
                "investment_return": lv * (fb * (gg - 1.0)), "rebate": lv_f1 * (prod.rebate / 12),
                "death_claims": de * dbb, "surrender_claims": lap * (f3 * (1.0 - sc)),
                "partial_claims": part_tot * (1.0 - sc_pw), "maturity_claims": mat * f3,
                "unit_res_bop": lv * f0, "unit_res_eop": le * pk(fv_end),
                "exp_maintenance": lv * (maint_m * it),
                "exp_investment": lv_f1 * (prod.exp_investment / 12 * em),
                "exp_claim": (de + ex) * (prod.exp_claim * it),
                "comm_trail": lv_f1 * (prod.comm_trail / 12),
                "amc": lv_f1 * (prod.amc / 12), "policy_fee": lv * (pk(after_amc) - pk(fv2)),
                "coi": lv * pk(coi), "surrender_penalty": lap * (f3 * sc) + part_tot * sc_pw,
                "death_strain": de * (dbb - f3),
            }
            row = np.empty((n_lines, len(LINES)))
            for name, arr in vals.items():
                arr = np.broadcast_to(arr, (n_lines, N))
                row[:, L[name]] = arr.sum(axis=1)
                if central and out.pv_mp is not None:
                    out.pv_mp[L[name]] += arr[0] * (df[0, i] if name in START_LINES else df[0, t])
            row[:, L["profit"]] = combine(lambda n: row[:, L[n]], PROFIT_SIGNS)
            out.acc[rows if full else rows[:1], i, :] = row

        fv, lives = fv_end, lives_eop
        if has_pw and is_rop:                                   # guarantee reduced pro rata
            guar = guar * (1.0 - pw)
        if central and t in snapshots:
            row0 = lambda v: (v[0] if np.ndim(v) == 2 else v).copy()  # noqa: E731
            out.snaps[t] = (row0(lives), row0(fv), row0(guar))

    out.bel[rows] = bel
