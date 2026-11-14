"""Orchestration of the three run types: VALUATION, VNB, BUSINESS_PLAN (or ALL)."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd

from . import __version__, business_plan, outputs, valuation, vnb
from .inputs import (InputError, load_assumptions, load_config, load_in_force,
                     load_new_business, load_products)
from .scenarios import build_curves, build_scenarios


def run(config_path, run_type: str | None = None, verbose: bool = True) -> dict:
    say = print if verbose else (lambda *x, **k: None)
    t0 = time.perf_counter()
    cfg = load_config(Path(config_path))
    if run_type:
        cfg.run_type = run_type.upper()
    a = load_assumptions(cfg.assumptions_file, cfg.economic_file)
    if a.spot.size < cfg.proj_years:
        raise InputError(f"Yield curve has {a.spot.size} maturities; projection_years = "
                         f"{cfg.proj_years} requires at least as many")
    products = load_products(cfg.products_file, a)
    todo = ["VALUATION", "VNB", "BUSINESS_PLAN"] if cfg.run_type == "ALL" else [cfg.run_type]
    val = cfg.valuation_date

    in_force = load_in_force(cfg.in_force_dir, products, a, val) \
        if {"VALUATION", "BUSINESS_PLAN"} & set(todo) else {}
    new_bus = load_new_business(cfg.new_business_dir, products, a, val) \
        if {"VNB", "BUSINESS_PLAN"} & set(todo) else {}
    curves = build_curves(a.spot, a, cfg.H)
    scen = build_scenarios(a, curves)
    t_read = time.perf_counter() - t0
    say(f"ULModel {__version__} - valuation date {val.date()} - run type {cfg.run_type} - "
        f"monthly steps, {cfg.proj_years} years")

    info = {
        "Model": f"ULModel {__version__}",
        "Run date": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Valuation date": str(val.date()),
        "Projection": f"monthly, {cfg.H} months",
        "Economic file": str(cfg.economic_file),
        "Assumptions file": str(cfg.assumptions_file),
        "Products file": str(cfg.products_file),
        "In-force model points": ", ".join(f"{c} ({m.n:,})" for c, m in in_force.items()) or "-",
        "New business model points": ", ".join(f"{c} ({m.n:,})" for c, m in new_bus.items()) or "-",
        "Policy-level flooring": "Y" if a.policy_level_flooring else "N",
        "Risk margin driver": a.rm_driver,
    }
    out = {"config": cfg, "assumptions": a, "products": products, "files": []}

    for rt in todo:
        t1 = time.perf_counter()
        say(f"\n[{rt}]")
        run_info = {"Run type": rt, **info}
        if rt == "VALUATION":
            if not in_force:
                raise InputError(f"No in-force model points found in {cfg.in_force_dir}")
            portfolio = [(products[c], in_force[c]) for c in products if c in in_force]
            results, total = valuation.value(portfolio, a, curves, scen, cfg.H, cfg.chunk_size,
                                             cfg.scenario_detail, mp_detail=cfg.mp_detail)
            for r in [total, *results]:
                say(f"  {r.code:<12} {r.n_mp:>8,} MPs   fund value {r.metrics['fv0']:>16,.0f}   "
                    f"BEL {r.metrics['bel']:>16,.0f}   SCR {r.metrics['scr']:>14,.0f}")
            files = outputs.write_valuation(cfg, results, total, curves, scen, a, run_info)
            out["valuation"] = (results, total)
        elif rt == "VNB":
            frames = []
            for code in [c for c in products if c in new_bus]:
                nb = new_bus[code].take(new_bus[code].entry <= cfg.vnb_months)
                if nb.n:
                    frames.append(vnb.compute(products[code], nb, a, curves, scen, cfg.H,
                                              cfg.chunk_size))
            if not frames:
                raise InputError(f"No new business issued in the {cfg.vnb_months} months after "
                                 f"{val.date()} found in {cfg.new_business_dir}")
            df = pd.concat(frames, ignore_index=True)
            run_info["New business window"] = (f"{cfg.date(1).strftime('%Y-%m')} to "
                                               f"{cfg.date(cfg.vnb_months).strftime('%Y-%m')}")
            for code, g in [("TOTAL", df), *df.groupby("product", sort=False)]:
                say(f"  {code:<12} premium {g['premium'].sum():>14,.0f}   VNB {g['vnb'].sum():>12,.0f}"
                    f"   margin {g['vnb'].sum() / g['pv_premium'].sum():6.2%}")
            files = outputs.write_vnb(cfg, df, run_info)
            out["vnb"] = df
        else:
            if not in_force and not new_bus:
                raise InputError("Business plan needs in-force and/or new business model points")
            run_info["Plan horizon"] = f"{cfg.bp_months} months, to {cfg.date(cfg.bp_months).date()}"
            bp = business_plan.run(cfg, a, products, in_force, new_bus, say)
            files = outputs.write_business_plan(cfg, bp, products, a, run_info)
            out["business_plan"] = bp
        out["files"] += files
        say(f"  done in {time.perf_counter() - t1:.1f}s -> " + ", ".join(f.name for f in files))

    say(f"\nInputs read in {t_read:.1f}s, total {time.perf_counter() - t0:.1f}s. "
        f"Outputs in {cfg.output_dir}")
    return out
