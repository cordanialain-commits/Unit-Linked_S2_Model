"""Validation tests. Run with:  python tests/test_model.py   (or: python -m pytest tests)

1. The vectorised monthly engine is compared, model point by model point, with an independent
   single-policy projection written with plain Python loops (in-force: every scenario;
   new business: central scenario).
2. Internal consistency: fund value = BEL + PVFP in every scenario, BEL = PV of cash flow
   lines, revenue account = result by source, VNB + BEL = 0 for new business.
   Partial surrenders are included in all of these.
3. Roll-forward consistency of the business plan machinery: with a zero real-world premium,
   BEL(0) = PV(cash flows of the first h months) + DF(h) x BEL(h) valued on the forward curve.
4. A valuation date that is not a year end (31 March) runs and ends the plan on a 31 December.
5. Timing of a valuation.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ulmodel import valuation, vnb  # noqa: E402
from ulmodel.business_plan import state_at  # noqa: E402
from ulmodel.engine import BEL_SIGNS, L, combine, project, pv_lines  # noqa: E402
from ulmodel.inputs import (load_assumptions, load_config, load_in_force,  # noqa: E402
                            load_new_business, load_products)
from ulmodel.outputs import ra_result  # noqa: E402
from ulmodel.scenarios import (build_curves, build_scenarios, forward_spot,  # noqa: E402
                               real_world_basis)

CONFIG = ROOT / "inputs" / "run_config.xlsx"


def _setup(valuation_date=None):
    cfg = load_config(CONFIG)
    if valuation_date:
        cfg.valuation_date = pd.Timestamp(valuation_date)
    a = load_assumptions(cfg.assumptions_file, cfg.economic_file)
    products = load_products(cfg.products_file, a)
    inf = load_in_force(cfg.in_force_dir, products, a, cfg.valuation_date)
    nb = load_new_business(cfg.new_business_dir, products, a, cfg.valuation_date)
    curves = build_curves(a.spot, a, cfg.H)
    return cfg, a, products, inf, nb, curves, build_scenarios(a, curves)


def ref_df(spot, t):
    """Discount factor to month t from annual spot rates, constant forward within each year."""
    y, k = divmod(t, 12)
    d0 = (1 + spot[y - 1]) ** -y if y else 1.0
    if k == 0:
        return d0
    d1 = (1 + spot[y]) ** -(y + 1)
    return d0 * (d1 / d0) ** (k / 12)


def reference_bel(j, s, mp, prod, a, scen, spots, H):
    """BEL of model point j in scenario s - straightforward scalar monthly projection."""
    spot = spots[scen.curve[s]]
    k = int(mp.entry[j])                       # 0 = in force, else issue at start of month k
    fv = mp.fv[j] * scen.fund_factor[s, mp.fund_idx[j]] if k == 0 else 0.0
    lives = mp.count[j] if k == 0 else 0.0
    infl = a.expense_inflation + scen.infl_add[s]
    n = min(int(mp.rem[j]), H)
    guar = mp.prem_paid[j]                     # return-of-premium guarantee
    bel = 0.0

    def benefit(f):
        if prod.db_type == "FV_PCT":
            return max(prod.db_pct, 1.0) * f
        if prod.db_type == "ROP":
            return max(f, guar)
        return f + mp.sa[j]

    for t in range(1, n + 1):
        v0, v1 = ref_df(spot, t - 1), ref_df(spot, t)
        if t == k:                             # new business enters
            lives = mp.count[j]
            fv = mp.prem[j] * (1 - prod.alloc_charge)
            acq = prod.acq_per_policy * (1 + a.expense_inflation) ** ((t - 1) / 12) \
                + mp.prem[j] * prod.acq_pct_premium
            bel += v0 * lives * (mp.prem[j] * prod.comm_initial + acq - mp.prem[j])
        if lives == 0.0:
            continue
        age = min(max((mp.age_m[j] + t - 1) // 12, 0), a.qx.shape[1] - 1)
        py = (mp.dur_m[j] + t - 1) // 12 + 1                       # policy year

        fv_grown = fv * (v0 / v1)
        fv = fv_grown * (1 - prod.amc / 12)
        fv -= min(prod.policy_fee / 12, fv)
        q_be = min(a.qx[mp.sex[j], age] * prod.mort_factor, 1.0)
        fv -= min(prod.coi_pct * (1 - (1 - q_be) ** (1 / 12)) * (benefit(fv) - fv), fv)

        q_annual = min(q_be * scen.qx_mult[s] + (scen.qx_add_y1[s] if t <= 12 else 0.0), 1.0)
        q = 1 - (1 - q_annual) ** (1 / 12)
        w_annual = prod.lapse[min(py, prod.lapse.size) - 1]
        w_annual = min(max(w_annual * scen.lapse_mult[s], w_annual - scen.lapse_max_dec[s]), 1.0)
        w = 1 - (1 - w_annual) ** (1 / 12)
        sc = prod.surrender_charges[py - 1] if py <= prod.surrender_charges.size else 0.0

        deaths = lives * q
        if t == n:
            lapses, mats = 0.0, lives - deaths
        else:
            lapses, mats = (lives - deaths) * w, 0.0

        f_exp = scen.exp_mult[s] * (1 + infl) ** ((t - 1) / 12)
        out = deaths * benefit(fv) + lapses * fv * (1 - sc) + mats * fv
        out += lives * prod.exp_per_policy / 12 * f_exp
        out += lives * fv_grown * prod.exp_investment / 12 * scen.exp_mult[s]
        out += (deaths + lapses + mats) * prod.exp_claim * f_exp
        out += lives * fv_grown * (prod.comm_trail - prod.rebate) / 12
        lives -= deaths + lapses + mats
        # partial surrender of the policies still in force
        pw_annual = prod.partial[min(py, prod.partial.size) - 1]
        pw_annual = min(max(pw_annual * scen.lapse_mult[s], pw_annual - scen.lapse_max_dec[s]), 1.0)
        pw = 1 - (1 - pw_annual) ** (1 / 12)
        out += lives * fv * pw * (1 - sc)
        fv -= fv * pw
        if prod.db_type == "ROP":
            guar *= 1 - pw
        bel += v1 * out
    return bel


def test_engine_matches_scalar_reference():
    cfg, a, products, inf, nb, curves, scen = _setup()
    rng = np.random.default_rng(1)
    for code, prod in products.items():
        mp = inf[code].capped(cfg.H)
        proj = project(mp, prod, a, scen, curves, cfg.H)
        for j in rng.choice(mp.n, 6, replace=False):
            for s in range(scen.n):
                ref = reference_bel(j, s, mp, prod, a, scen, curves.spot, cfg.H)
                got = proj.bel[s, j]
                assert abs(got - ref) <= 1e-8 * max(1.0, abs(ref)), (code, j, scen.names[s], got, ref)
        new = nb[code].capped(cfg.H)
        base = scen.base_only()
        proj = project(new, prod, a, base, curves, cfg.H)
        for j in rng.choice(new.n, 6, replace=False):
            ref = reference_bel(j, 0, new, prod, a, base, curves.spot, cfg.H)
            assert abs(proj.bel[0, j] - ref) <= 1e-8 * max(1.0, abs(ref)), (code, "NB", j)
    print("ok  engine == independent scalar reference (in-force: all scenarios; new business: central)")


def test_internal_identities():
    cfg, a, products, inf, nb, curves, scen = _setup()
    for code, prod in products.items():
        mp = inf[code].capped(cfg.H)
        p = project(mp, prod, a, scen, curves, cfg.H, full_accounts=True)
        pv = pv_lines(p.acc, curves.df[scen.curve])
        fv0, bel = p.fv0.sum(axis=1), p.bel.sum(axis=1)
        # fund value = BEL + PVFP, and BEL = PV of the cash flow lines, in every scenario
        assert np.allclose(fv0, bel + pv[:, L["profit"]], rtol=1e-10), code
        assert np.allclose(bel, combine(lambda n: pv[:, L[n]], BEL_SIGNS), rtol=1e-10), code
        # revenue account result = result by source, every month and scenario
        assert np.allclose(ra_result(p.acc), p.acc[:, :, L["profit"]], rtol=0,
                           atol=1e-9 * fv0.max()), code
        # full run-off at the horizon; policy movements reconcile
        g = lambda n: p.acc[:, :, L[n]]  # noqa: E731
        assert np.allclose(g("lives_eop")[:, -1], 0.0) and np.allclose(g("unit_res_eop")[:, -1], 0.0)
        assert np.allclose(g("lives_bop") - g("deaths") - g("lapses") - g("maturities"), g("lives_eop"))
        # central-only run gives the same central lines and the same BEL
        q = project(mp, prod, a, scen, curves, cfg.H)
        assert np.allclose(q.acc[0], p.acc[0]) and np.array_equal(q.bel, p.bel)
        # new business: VNB = -BEL
        v = vnb.compute(prod, nb[code], a, curves, scen, cfg.H)
        assert np.allclose(v["check_vnb_plus_bel"], 0.0, atol=1e-9 * v["pv_premium"].max()), code
    print("ok  FV = BEL + PVFP; BEL = PV of lines; revenue account = by source; VNB = -BEL")


def test_roll_forward_consistency():
    cfg, a, products, inf, nb, curves, scen = _setup()
    h, K = 12, a.spot.size
    long = build_curves(a.spot, a, h + 12 * K)
    rw = real_world_basis(long, a)
    rw.grow = np.broadcast_to(long.grow[:1], rw.grow.shape)       # zero real-world premium
    base = scen.base_only()
    curves_h = build_curves(forward_spot(long.df[0], h, K), a, cfg.H)
    for code, prod in products.items():
        mp = inf[code]
        bel0 = project(mp.capped(cfg.H + h), prod, a, base, long, cfg.H + h).bel[0].sum()
        p = project(mp, prod, a, base, rw, h, snapshots={h})
        cash = combine(lambda n: p.acc[0, :, L[n]], BEL_SIGNS)
        st = state_at(mp, *p.snaps[h], h)
        bel_h = project(st.capped(cfg.H), prod, a, base, curves_h, cfg.H,
                        exp_scale=(1 + a.expense_inflation) ** (h / 12)).bel[0].sum()
        rolled = (cash * long.df[0, 1:h + 1]).sum() + long.df[0, h] * bel_h
        assert abs(rolled - bel0) <= 1e-9 * abs(bel0), (code, rolled, bel0)
    print("ok  BEL(0) = PV(first-year cash flows) + DF x BEL(year end, forward curve)")


def test_quarter_start_date():
    cfg, a, products, inf, nb, curves, scen = _setup("2026-03-31")
    assert cfg.bp_months == 69 and cfg.bp_offsets == [0, 9, 21, 33, 45, 57, 69]
    assert cfg.date(cfg.bp_months) == pd.Timestamp("2031-12-31")
    portfolio = [(products[c], m) for c, m in inf.items()]
    results, total = valuation.value(portfolio, a, curves, scen, cfg.H)
    assert abs(total.metrics["check"]) < 1e-6 * total.metrics["fv0"] and total.metrics["scr"] > 0
    assert all(m.entry.min() >= 1 for m in nb.values())           # past issue months dropped
    print("ok  valuation at 2026-03-31; plan of 69 months ends 2031-12-31")


def test_performance():
    cfg, a, products, inf, nb, curves, scen = _setup()
    portfolio = [(products[c], m) for c, m in inf.items()]
    n = sum(m.n for m in inf.values())
    t0 = time.perf_counter()
    valuation.value(portfolio, a, curves, scen, cfg.H)
    dt = time.perf_counter() - t0
    print(f"ok  valuation of {n:,} model points x {scen.n} scenarios x {cfg.H} months in {dt:.1f}s")
    assert dt < 120


if __name__ == "__main__":
    test_engine_matches_scalar_reference()
    test_internal_identities()
    test_roll_forward_consistency()
    test_quarter_start_date()
    test_performance()
    print("all tests passed")
