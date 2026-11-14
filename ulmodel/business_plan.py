"""Business plan: real-world monthly projection of in-force + new business to the last
31 December of the plan, with a full Solvency II valuation at the start and at every 31 December.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import valuation, vnb
from .engine import project
from .inputs import ModelPoints
from .scenarios import build_curves, build_scenarios, forward_spot, real_world_basis


@dataclass
class BusinessPlan:
    months: int
    acc: dict            # product code -> (months, lines) real-world monthly lines
    solvency: list       # [(offset in months, results, total)] one entry per valuation date
    forward_spots: dict  # offset -> annual spot curve used for the valuation
    rw_growth: np.ndarray  # (F, months) real-world monthly fund growth
    nb: pd.DataFrame     # value of the plan's new business by product and issue month


def state_at(mp: ModelPoints, lives: np.ndarray, fv: np.ndarray, guar: np.ndarray,
             h: int) -> ModelPoints:
    """Model points in force h months after the start, from the projected state."""
    keep = (mp.entry <= h) & (lives > 1e-9) & (mp.rem > h)
    st = mp.take(keep)
    st.count, st.fv, st.prem_paid = lives[keep], fv[keep], guar[keep]
    st.age_m, st.dur_m, st.rem = st.age_m + h, st.dur_m + h, st.rem - h
    st.entry = np.zeros(st.n, dtype=np.int64)
    return st


def run(cfg, a, products: dict, in_force: dict, new_business: dict, say=print) -> BusinessPlan:
    H, Hbp, offsets = cfg.H, cfg.bp_months, cfg.bp_offsets
    K = a.spot.size
    curves0 = build_curves(a.spot, a, Hbp + 12 * K)
    scen0 = build_scenarios(a, curves0)
    base = scen0.base_only()
    rw = real_world_basis(curves0, a)

    # ---- real-world projection of in-force + new business
    acc, states, nb_frames = {}, {h: [] for h in offsets}, []
    for code, prod in products.items():
        parts = []
        if code in in_force:
            parts.append(in_force[code])
            states[0].append((prod, in_force[code]))
        if code in new_business:
            nb = new_business[code]
            nb = nb.take(nb.entry <= Hbp)
            if nb.n:
                parts.append(nb)
                nb_frames.append(vnb.compute(prod, nb, a, curves0, scen0, H, cfg.chunk_size))
        if not parts:
            continue
        mp = ModelPoints.concat(parts)
        proj = project(mp, prod, a, base, rw, Hbp, snapshots=set(offsets[1:]))
        acc[code] = proj.acc[0]
        for h in offsets[1:]:
            st = state_at(mp, *proj.snaps.get(h, (np.zeros(mp.n),) * 3), h)
            if st.n:
                states[h].append((prod, st))

    # ---- Solvency II valuation at the start and at each 31 December, on the forward curve
    solvency, spots = [], {}
    for h in offsets:
        spot_h = a.spot if h == 0 else forward_spot(curves0.df[0], h, K)
        curves_h = build_curves(spot_h, a, H)
        scen_h = build_scenarios(a, curves_h)
        exp_scale = (1.0 + a.expense_inflation) ** (h / 12)
        results, total = valuation.value(states[h], a, curves_h, scen_h, H, cfg.chunk_size,
                                         exp_scale=exp_scale)
        solvency.append((h, results, total))
        spots[h] = spot_h
        if results:
            say(f"  valuation at {cfg.date(h).date()}   fund value {total.metrics['fv0']:>16,.0f}"
                f"   BEL {total.metrics['bel']:>16,.0f}   SCR {total.metrics['scr']:>14,.0f}")

    nb_df = pd.concat(nb_frames, ignore_index=True) if nb_frames else pd.DataFrame()
    return BusinessPlan(Hbp, acc, solvency, spots, rw.grow[0][:, :Hbp], nb_df)
