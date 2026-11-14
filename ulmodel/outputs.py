"""Excel output writers for the three run types."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .engine import L, LINES, PROFIT_SIGNS, combine
from .scenarios import BASE, MASS_LAPSE
from .vnb import COMPONENTS, FLOWS

HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(bold=True, color="FFFFFF")
SECT_FILL = PatternFill("solid", fgColor="D9E1F2")
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
BOLD = Font(bold=True)
TOP = Border(top=Side(style="thin", color="7F7F7F"))
NUM = "#,##0;[Red]-#,##0"
NUM2 = "#,##0.00;[Red]-#,##0.00"
PCT = "0.00%"


# --------------------------------------------------------------------------- helpers
def _save(wb: Workbook, path: Path) -> Path:
    """Save; if the target is open in Excel, fall back to a time-stamped file name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{dt.datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        wb.save(alt)
        print(f"  ! {path.name} is open in another program - saved as {alt.name}")
        return alt


def _header(ws, cols, first_width=44, width=16):
    ws.append(list(cols))
    for i in range(1, len(cols) + 1):
        c = ws.cell(row=ws.max_row, column=i)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center" if i > 1 else "left", vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = first_width if i == 1 else width


def _section(ws, title, ncols):
    ws.append([title])
    for i in range(1, ncols + 1):
        c = ws.cell(row=ws.max_row, column=i)
        c.fill, c.font = SECT_FILL, BOLD


def _row(ws, label, values, fmt=NUM, bold=False):
    values = [v.item() if isinstance(v, np.generic) else v for v in values]
    ws.append([label, *values])
    r = ws.max_row
    ws.cell(row=r, column=1).alignment = Alignment(indent=0 if bold else 1)
    for i in range(1, len(values) + 2):
        c = ws.cell(row=r, column=i)
        if i > 1:
            if isinstance(values[i - 2], str):
                c.alignment = Alignment(horizontal="right")
            else:
                c.number_format = fmt
        if bold:
            c.font, c.fill, c.border = BOLD, TOTAL_FILL, TOP


def _layout(ws, layout, cols, get):
    """layout: (label, key[, fmt[, bold]]) with key None for a section header."""
    for item in layout:
        label, key = item[0], item[1]
        if key is None:
            _section(ws, label, len(cols) + 1)
            continue
        _row(ws, label, [get(c, key) for c in cols], item[2] if len(item) > 2 else NUM,
             item[3] if len(item) > 3 else False)


def _table(ws, df: pd.DataFrame, formats: dict | None = None, first_width=16, width=18):
    _header(ws, list(df.columns), first_width, width)
    formats = formats or {}
    for rec in df.itertuples(index=False):
        ws.append([v.item() if isinstance(v, np.generic) else v for v in rec])
        for i, col in enumerate(df.columns, 1):
            if not isinstance(rec[i - 1], str):
                ws.cell(row=ws.max_row, column=i).number_format = formats.get(col, NUM)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def _info_sheet(wb, info: dict):
    ws = wb.create_sheet("Run_Info")
    _header(ws, ["Item", "Value"], first_width=30, width=90)
    for k, v in info.items():
        ws.append([k, v])


# --------------------------------------------------------------------------- periods
def month_labels(cfg, n: int) -> list:
    return [cfg.date(t).strftime("%Y-%m") for t in range(1, n + 1)]


def calendar_years(acc: np.ndarray, cfg, extra: np.ndarray | None = None):
    """Aggregate monthly lines (H, lines) by calendar year: flows are summed, start-of-period
    stocks take the first month and end-of-period stocks the last month of the year."""
    H = acc.shape[0]
    years = np.array([cfg.date(t).year for t in range(1, H + 1)])
    uniq = np.unique(years)
    out = np.zeros((uniq.size, acc.shape[1]))
    ex = np.zeros(uniq.size) if extra is not None else None
    labels = []
    for j, y in enumerate(uniq):
        idx = np.flatnonzero(years == y)
        out[j] = acc[idx].sum(axis=0)
        for name in ("lives_bop", "unit_res_bop"):
            out[j, L[name]] = acc[idx[0], L[name]]
        for name in ("lives_eop", "unit_res_eop"):
            out[j, L[name]] = acc[idx[-1], L[name]]
        # opening policies exclude the new business of the first month of the year
        out[j, L["lives_bop"]] -= acc[idx[0], L["new_policies"]]
        if ex is not None:
            ex[j] = extra[idx].sum()
        labels.append(str(y) if idx.size == 12 else f"{y} ({idx.size}m)")
    return labels, out, ex


def ra_result(acc: np.ndarray) -> np.ndarray:
    """Revenue account result before tax from the accounting lines (last axis = lines)."""
    g = lambda n: acc[..., L[n]]  # noqa: E731
    income = g("premium") + g("investment_return") + g("rebate")
    claims = (g("death_claims") + g("surrender_claims") + g("partial_claims")
              + g("maturity_claims"))
    costs = (g("acq_expense") + g("exp_maintenance") + g("exp_investment") + g("exp_claim")
             + g("init_commission") + g("comm_trail"))
    return income - claims - (g("unit_res_eop") - g("unit_res_bop")) - costs


def _account_sheet(ws, title: str, labels: list, acc: np.ndarray, tax_rate: float,
                   pv_result: np.ndarray | None = None, opening_adjusted: bool = False):
    """acc: (periods, lines). pv_result: optional present value of the result per period."""
    g = lambda name: acc[:, L[name]]  # noqa: E731
    ncols = len(labels) + 2

    ws.append([title])
    ws["A1"].font = Font(bold=True, size=13, color="1F3864")
    _header(ws, ["Period", "Total", *labels], first_width=48, width=15)

    def flow(label, v, bold=False):
        _row(ws, label, [float(v.sum()), *v.tolist()], NUM, bold)

    def stock(label, v):
        _row(ws, label, ["", *v.tolist()], NUM)

    _section(ws, "POLICIES", ncols)
    stock("Policies in force - start of period", g("lives_bop") if opening_adjusted
          else g("lives_bop") - g("new_policies"))
    flow("New policies", g("new_policies"))
    flow("Deaths", g("deaths"))
    flow("Full surrenders", g("lapses"))
    flow("Maturities (incl. run-off at horizon)", g("maturities"))
    stock("Policies in force - end of period", g("lives_eop"))

    _section(ws, "INCOME", ncols)
    income = g("premium") + g("investment_return") + g("rebate")
    flow("Premiums", g("premium"))
    flow("Investment return on unit-linked assets", g("investment_return"))
    flow("Fund manager rebates", g("rebate"))
    flow("Total income", income, True)

    _section(ws, "CLAIMS AND CHANGE IN RESERVES", ncols)
    claims = (g("death_claims") + g("surrender_claims") + g("partial_claims")
              + g("maturity_claims"))
    d_res = g("unit_res_eop") - g("unit_res_bop")
    flow("Death claims", g("death_claims"))
    flow("Surrender claims (net of penalties)", g("surrender_claims"))
    flow("Partial surrenders (net of penalties)", g("partial_claims"))
    flow("Maturity claims (incl. run-off at horizon)", g("maturity_claims"))
    flow("Total claims", claims, True)
    flow("Change in unit-linked reserves", d_res, True)

    _section(ws, "EXPENSES AND COMMISSIONS", ncols)
    costs = (g("acq_expense") + g("exp_maintenance") + g("exp_investment") + g("exp_claim")
             + g("init_commission") + g("comm_trail"))
    flow("Acquisition expenses", g("acq_expense"))
    flow("Expenses per policy", g("exp_maintenance"))
    flow("Expenses in % of fund value", g("exp_investment"))
    flow("Claim handling expenses", g("exp_claim"))
    flow("Initial commissions", g("init_commission"))
    flow("Trail commissions", g("comm_trail"))
    flow("Total expenses and commissions", costs, True)

    _section(ws, "RESULT", ncols)
    result = income - claims - d_res - costs
    tax = result * tax_rate
    flow("Result before tax", result, True)
    flow("Tax", tax)
    flow("Result after tax", result - tax, True)

    _section(ws, "ANALYSIS OF RESULT BY SOURCE", ncols)
    flow("Premium loadings (unallocated premium)", g("premium") - g("allocated_premium"))
    flow("Annual management charges", g("amc"))
    flow("Policy fees", g("policy_fee"))
    flow("Cost-of-insurance charges", g("coi"))
    flow("Surrender penalties (full and partial)", g("surrender_penalty"))
    flow("Fund manager rebates", g("rebate"))
    flow("Death benefits in excess of fund value", -g("death_strain"))
    flow("Acquisition expenses", -g("acq_expense"))
    flow("Initial commissions", -g("init_commission"))
    flow("Expenses per policy", -g("exp_maintenance"))
    flow("Expenses in % of fund value", -g("exp_investment"))
    flow("Claim handling expenses", -g("exp_claim"))
    flow("Trail commissions", -g("comm_trail"))
    flow("Result before tax (by source)", g("profit"), True)
    flow("Check: difference with revenue account (must be 0)", result - g("profit"))

    _section(ws, "MEMORANDUM", ncols)
    stock("Unit-linked reserves - start of period", g("unit_res_bop"))
    stock("Unit-linked reserves - end of period", g("unit_res_eop"))
    if pv_result is not None:
        flow("Present value of result before tax (total = PVFP)", pv_result, True)
        flow("Present value of result after tax", pv_result * (1 - tax_rate), True)
    ws.freeze_panes = "C3"


def _accounts(wb, entities: list, cfg, tax_rate: float, what: str, df: np.ndarray | None = None):
    """entities: [(code, name, acc (H, lines))]. Annual sheet + monthly sheet per entity."""
    for code, name, acc in entities:
        pv = acc[:, L["profit"]] * df[1:acc.shape[0] + 1] if df is not None else None
        labels, acc_y, pv_y = calendar_years(acc, cfg, pv)
        ws = wb.create_sheet(code[:31])
        _account_sheet(ws, f"{what} - {name} - by calendar year", labels, acc_y, tax_rate, pv_y,
                       opening_adjusted=True)
    for code, name, acc in entities:
        active = np.flatnonzero(acc[:, L["lives_bop"]] > 0)
        last = int(active.max()) + 1 if active.size else 1
        pv = acc[:last, L["profit"]] * df[1:last + 1] if df is not None else None
        ws = wb.create_sheet(f"{code}_monthly"[:31])
        _account_sheet(ws, f"{what} - {name} - monthly", month_labels(cfg, last), acc[:last],
                       tax_rate, pv)


# --------------------------------------------------------------------------- valuation
SCR_LAYOUT = [
    ("SCR - MARKET RISK", None),
    ("Interest rate - up shock", "ir_up"),
    ("Interest rate - down shock", "ir_down"),
    ("Interest rate risk", "interest", NUM, True),
    ("   binding scenario", "ir_scenario"),
    ("Equity - type 1", "eq_t1"),
    ("Equity - type 2", "eq_t2"),
    ("Equity risk", "equity", NUM, True),
    ("Property risk", "property", NUM, True),
    ("Spread risk", "spread", NUM, True),
    ("Currency risk", "currency", NUM, True),
    ("Diversification within market", "div_market"),
    ("SCR Market", "scr_market", NUM, True),
    ("SCR - LIFE UNDERWRITING RISK", None),
    ("Mortality risk", "mortality", NUM, True),
    ("Longevity risk", "longevity", NUM, True),
    ("Lapse - up shock", "lapse_up"),
    ("Lapse - down shock", "lapse_down"),
    ("Lapse - mass lapse", "lapse_mass"),
    ("Lapse risk", "lapse", NUM, True),
    ("   binding scenario", "lapse_scenario"),
    ("Expense risk", "expense", NUM, True),
    ("Life catastrophe risk", "cat", NUM, True),
    ("Diversification within life", "div_life"),
    ("SCR Life", "scr_life", NUM, True),
    ("SOLVENCY CAPITAL REQUIREMENT", None),
    ("Diversification market / life", "div_bscr"),
    ("Basic SCR (BSCR)", "bscr", NUM, True),
    ("Operational risk", "scr_op"),
    ("SCR", "scr", NUM, True),
    ("SCR / fund value", "scr_ratio", PCT),
]
SUMMARY_LAYOUT = [
    ("PORTFOLIO", None),
    ("Number of model points", "n_mp"),
    ("Number of policies", "policies"),
    ("Initial fund value", "fv0"),
    ("CENTRAL VALUATION", None),
    ("Best estimate liability (BEL)", "bel"),
    ("PVFP - present value of future profits, pre-tax", "pvfp"),
    ("PVFP net of tax", "pvfp_net"),
    ("Check: fund value - BEL - PVFP (must be 0)", "check", NUM2),
    ("BEL / fund value", "bel_ratio", PCT),
    ("Risk margin", "risk_margin"),
    ("Technical provisions (BEL + risk margin)", "tp"),
    *SCR_LAYOUT,
    ("OWN FUNDS CONTRIBUTION", None),
    ("Fund value - technical provisions", "of", NUM, True),
    ("(Fund value - technical provisions) / SCR", "of_ratio", PCT),
]
# valuation variables: ("pv", line, sign) = sign x present value of the line (central)
VARIABLES_LAYOUT = [
    ("PORTFOLIO", None),
    ("Number of model points", "n_mp"),
    ("Number of policies", "policies"),
    ("Initial fund value", "fv0"),
    ("PRESENT VALUE OF BEST ESTIMATE CASH FLOWS (CENTRAL)", None),
    ("PV death claims", ("pv", "death_claims", 1)),
    ("PV surrender claims (net of penalties)", ("pv", "surrender_claims", 1)),
    ("PV partial surrenders (net of penalties)", ("pv", "partial_claims", 1)),
    ("PV maturity claims (incl. run-off at horizon)", ("pv", "maturity_claims", 1)),
    ("PV expenses per policy", ("pv", "exp_maintenance", 1)),
    ("PV expenses in % of fund value", ("pv", "exp_investment", 1)),
    ("PV claim handling expenses", ("pv", "exp_claim", 1)),
    ("PV trail commissions", ("pv", "comm_trail", 1)),
    ("PV fund manager rebates (income)", ("pv", "rebate", -1)),
    ("BEL = sum of the above", "bel", NUM, True),
    ("PRESENT VALUE OF RESULT BY SOURCE (CENTRAL)", None),
    ("PV annual management charges", ("pv", "amc", 1)),
    ("PV policy fees", ("pv", "policy_fee", 1)),
    ("PV cost-of-insurance charges", ("pv", "coi", 1)),
    ("PV surrender penalties (full and partial)", ("pv", "surrender_penalty", 1)),
    ("PV fund manager rebates", ("pv", "rebate", 1)),
    ("PV death benefits in excess of fund value", ("pv", "death_strain", -1)),
    ("PV expenses per policy", ("pv", "exp_maintenance", -1)),
    ("PV expenses in % of fund value", ("pv", "exp_investment", -1)),
    ("PV claim handling expenses", ("pv", "exp_claim", -1)),
    ("PV trail commissions", ("pv", "comm_trail", -1)),
    ("PVFP pre-tax = sum of the above", "pvfp", NUM, True),
    ("PVFP net of tax", "pvfp_net"),
    ("PV fund value released (deaths, full and partial surrenders, maturities)", "pv_fv_released"),
    ("UNDISCOUNTED TOTALS (CENTRAL)", None),
    ("Total claims paid", "u_claims"),
    ("Total expenses and commissions", "u_costs"),
    ("Total charges and rebates", "u_charges"),
    ("Total result before tax", "u_result"),
    ("CHECKS (MUST BE 0)", None),
    ("Fund value - BEL - PVFP", "check", NUM2),
    ("BEL (per model point) - BEL (from PV of cash flow lines)", "check_bel", NUM2),
    ("Fund value - PV charges - PV fund value released", "check_fund", NUM2),
    ("Revenue account result - result by source (max abs, monthly)", "check_ra", NUM2),
    ("RISK MARGIN AND TECHNICAL PROVISIONS", None),
    ("Non-hedgeable SCR at t=0 (life + operational)", "scr_rm0"),
    ("Risk margin", "risk_margin"),
    ("Technical provisions (BEL + risk margin)", "tp"),
    ("Fund value - technical provisions", "of", NUM, True),
    *SCR_LAYOUT,
]


def _var(r, key):
    if isinstance(key, tuple):
        return key[2] * r.pv[0, L[key[1]]]
    if key in r.metrics:
        return r.metrics[key]
    a0 = r.acc[0]
    s = lambda *names: float(sum(a0[:, L[n]].sum() for n in names))  # noqa: E731
    if key == "u_claims":
        return s("death_claims", "surrender_claims", "partial_claims", "maturity_claims")
    if key == "u_costs":
        return s("exp_maintenance", "exp_investment", "exp_claim", "comm_trail")
    if key == "u_charges":
        return s("amc", "policy_fee", "coi", "surrender_penalty", "rebate")
    if key == "u_result":
        return s("profit")
    if key == "check_ra":
        return float(np.abs(ra_result(a0) - a0[:, L["profit"]]).max())
    released = (r.pv[0, L["death_claims"]] - r.pv[0, L["death_strain"]]
                + r.pv[0, L["surrender_claims"]] + r.pv[0, L["surrender_penalty"]]
                + r.pv[0, L["partial_claims"]] + r.pv[0, L["maturity_claims"]])
    if key == "pv_fv_released":
        return released
    if key == "check_fund":
        return (r.metrics["fv0"] - released
                - sum(r.pv[0, L[n]] for n in ("amc", "policy_fee", "coi")))
    raise KeyError(key)


def write_valuation(cfg, results, total, curves, scen, a, info) -> list:
    out = cfg.output_dir / "valuation"
    cols = [total, *results]                       # TOTAL always first
    heads = ["Metric", *[r.code for r in cols]]

    # ---- results summary
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    _header(ws, heads, first_width=50, width=18)
    _layout(ws, SUMMARY_LAYOUT, cols, lambda r, k: r.metrics[k])
    ws.append([])
    ws.append(["TOTAL column (always column B): shocks are summed across products before aggregation, so it includes "
               "diversification between products (it is not the sum of the product SCRs)."])
    ws.freeze_panes = "B2"

    ws = wb.create_sheet("Scenarios")
    rows = []
    for r in cols:
        for name in [*scen.names, MASS_LAPSE]:
            t = r.table[name]
            rows.append({"Product": r.code, "Scenario": name, "Fund value t=0": t["fv0"],
                         "BEL": t["bel"], "Net asset value": t["nav"],
                         "Change in NAV vs central": -t["delta_nav"],
                         "Loss used for SCR": 0.0 if name == BASE else max(t["loss"], 0.0)})
    _table(ws, pd.DataFrame(rows), width=24)

    ws = wb.create_sheet("Curves")
    _header(ws, ["Maturity (years)", "Spot base", "Spot up", "Spot down", "DF base", "DF up",
                 "DF down"], first_width=18, width=14)
    for k in range(cfg.proj_years):
        ws.append([k + 1, *curves.spot[:, k].tolist(), *curves.df[:, 12 * (k + 1)].tolist()])
        for i in range(2, 5):
            ws.cell(row=ws.max_row, column=i).number_format = "0.000%"
        for i in range(5, 8):
            ws.cell(row=ws.max_row, column=i).number_format = "0.000000"
    for r in results:
        if r.detail is not None:
            _table(wb.create_sheet(f"MP_{r.code}"[:31]), r.detail)
    _info_sheet(wb, info)
    files = [_save(wb, out / "results_summary.xlsx")]

    # ---- variables
    wb = Workbook()
    ws = wb.active
    ws.title = "Variables"
    _header(ws, heads, first_width=62, width=18)
    _layout(ws, VARIABLES_LAYOUT, cols, _var)
    ws.freeze_panes = "B2"
    if cfg.scenario_detail:
        skip = {"lives_bop", "lives_eop", "unit_res_bop", "unit_res_eop", "new_policies",
                "deaths", "lapses", "maturities", "premium", "allocated_premium",
                "init_commission", "acq_expense"}
        rows = []
        for r in cols:
            for s, name in enumerate(scen.names):
                rows.append({"Product": r.code, "Scenario": name, "fund_value": r.table[name]["fv0"],
                             "bel": r.table[name]["bel"],
                             **{f"pv_{n}": r.pv[s, L[n]] for n in LINES if n not in skip}})
        _table(wb.create_sheet("By_Scenario"), pd.DataFrame(rows), width=20)
    _info_sheet(wb, info)
    files.append(_save(wb, out / "valuation_variables.xlsx"))

    # ---- revenue account
    wb = Workbook()
    wb.remove(wb.active)
    _accounts(wb, [(r.code, r.name, r.acc[0]) for r in [total, *results]], cfg, a.tax_rate,
              "Revenue account (central, risk-neutral run-off)", curves.df[0])
    _info_sheet(wb, info)
    files.append(_save(wb, out / "revenue_account.xlsx"))
    return files


# --------------------------------------------------------------------------- VNB
VNB_SUM_COLS = ["model_points", "policies", "premium", "pv_premium",
                *[c for c, _, _ in COMPONENTS], "vnb", "vnb_net_of_tax",
                *[c for c, _ in FLOWS], "bel", "check_vnb_plus_bel"]
VNB_LABELS = {
    "model_points": "Number of model points", "policies": "Number of policies",
    "premium": "Single premiums (undiscounted)", "pv_premium": "PV premiums",
    "pv_premium_loading": "PV premium loadings (unallocated premium)",
    "pv_amc": "PV annual management charges", "pv_policy_fees": "PV policy fees",
    "pv_coi_charges": "PV cost-of-insurance charges",
    "pv_surrender_penalties": "PV surrender penalties (full and partial)", "pv_rebates": "PV fund manager rebates",
    "pv_death_strain": "PV death benefits in excess of fund value",
    "pv_expenses_per_policy": "PV expenses per policy",
    "pv_expenses_pct_fund": "PV expenses in % of fund value",
    "pv_partial_surrenders": "PV partial surrenders",
    "pv_claim_expenses": "PV claim handling expenses",
    "pv_trail_commission": "PV trail commissions",
    "pv_initial_commission": "PV initial commissions",
    "pv_acquisition_expenses": "PV acquisition expenses",
    "vnb": "VNB (pre-tax, before risk margin)", "vnb_net_of_tax": "VNB net of tax",
    "pv_death_claims": "PV death claims", "pv_surrender_claims": "PV surrender claims",
    "pv_maturity_claims": "PV maturity claims", "bel": "BEL of new business",
    "check_vnb_plus_bel": "Check: VNB + BEL (must be 0)", "vnb_margin": "VNB / PV premiums",
}


def _vnb_summary(ws, groups: dict, first="Metric"):
    """groups: column label -> Series of summed VNB columns."""
    heads = list(groups)
    _header(ws, [first, *heads], first_width=46, width=18)

    def line(col, fmt=NUM, bold=False):
        _row(ws, VNB_LABELS[col], [groups[h][col] for h in heads], fmt, bold)

    n = len(heads) + 1
    _section(ws, "VOLUMES", n)
    for c in ("model_points", "policies", "premium", "pv_premium"):
        line(c)
    _section(ws, "VNB DECOMPOSITION (present values at the valuation date)", n)
    for c, _, _ in COMPONENTS:
        line(c)
    line("vnb", NUM, True)
    line("vnb_net_of_tax")
    _row(ws, VNB_LABELS["vnb_margin"],
         [groups[h]["vnb"] / groups[h]["pv_premium"] if groups[h]["pv_premium"] else 0.0
          for h in heads], PCT, True)
    _section(ws, "BEST ESTIMATE CASH FLOWS AND CHECK", n)
    for c, _ in FLOWS:
        line(c)
    line("bel")
    line("check_vnb_plus_bel", NUM2)
    ws.freeze_panes = "B2"


def _with_total(df: pd.DataFrame, keys: list) -> pd.DataFrame:
    """Add TOTAL rows (sum over products per key) in front of the product rows."""
    tot = df.groupby(keys, as_index=False)[VNB_SUM_COLS].sum()
    tot.insert(0, "product", "TOTAL")
    out = pd.concat([tot, df[["product", *keys, *VNB_SUM_COLS]]], ignore_index=True)
    out["vnb_margin"] = out["vnb"] / out["pv_premium"]
    return out


def write_vnb(cfg, df: pd.DataFrame, info) -> list:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    df = _with_total(df, ["month"])                  # TOTAL rows first
    codes = list(dict.fromkeys(df["product"]))       # TOTAL, then products in input order
    by_prod = df.groupby("product")[VNB_SUM_COLS].sum()
    _vnb_summary(ws, {code: by_prod.loc[code] for code in codes})

    df.insert(1, "issue_month", [cfg.date(k).strftime("%Y-%m") for k in df["month"]])
    df = df.drop(columns="month")
    ws = wb.create_sheet("By_Issue_Month")
    _table(ws, df, {"vnb_margin": PCT, "check_vnb_plus_bel": NUM2}, width=20)

    for code in codes:               # one sheet per entity: metric x (all months, each month)
        sub = df[df["product"] == code]
        ws = wb.create_sheet(code[:31])
        _vnb_summary(ws, {"All months": by_prod.loc[code],
                          **{m: row for m, (_, row) in zip(sub["issue_month"], sub.iterrows())}},
                     first="Issue month")
    _info_sheet(wb, info)
    return [_save(wb, cfg.output_dir / "vnb" / "vnb_results.xlsx")]


# --------------------------------------------------------------------------- business plan
BP_SOLVENCY_LAYOUT = [
    ("Number of policies", "policies"),
    ("Fund value", "fv0"),
    ("Best estimate liability (BEL)", "bel"),
    ("PVFP pre-tax", "pvfp"),
    ("Risk margin", "risk_margin"),
    ("Technical provisions", "tp"),
    ("SCR Market", "scr_market"),
    ("SCR Life", "scr_life"),
    ("Basic SCR", "bscr"),
    ("Operational risk", "scr_op"),
    ("SCR", "scr", NUM, True),
    ("Fund value - technical provisions", "of", NUM, True),
    ("(Fund value - technical provisions) / SCR", "of_ratio", PCT),
    ("SCR / fund value", "scr_ratio", PCT),
]


def write_business_plan(cfg, bp, products: dict, a, info) -> list:
    wb = Workbook()
    ws = wb.active
    ws.title = "Solvency"
    dates = [cfg.date(h).strftime("%Y-%m-%d") for h, _, _ in bp.solvency]
    _header(ws, ["Solvency II position at", *dates], first_width=46, width=18)
    codes = ["TOTAL", *[c for c in products if c in bp.acc]]
    for code in codes:
        _section(ws, "TOTAL - all products" if code == "TOTAL" else f"{code} - {products[code].name}",
                 len(dates) + 1)
        for item in BP_SOLVENCY_LAYOUT:
            vals = []
            for _, results, total in bp.solvency:
                r = total if code == "TOTAL" else next((x for x in results if x.code == code), None)
                vals.append(r.metrics[item[1]] if r is not None and r.metrics else 0.0)
            _row(ws, item[0], vals, item[2] if len(item) > 2 else NUM,
                 item[3] if len(item) > 3 else False)
    ws.append([])
    ws.append(["Each valuation is a full recalculation (all shocks) of the portfolio projected on the "
               "real-world basis, using the forward curve implied by the starting yield curve."])
    ws.freeze_panes = "B2"

    # ---- real-world P&L
    acc_total = sum(bp.acc.values())
    entities = [("PnL_TOTAL", "All products", acc_total)]
    entities += [(f"PnL_{c}", products[c].name, bp.acc[c]) for c in bp.acc]
    _accounts(wb, entities, cfg, a.tax_rate, "Business plan P&L (real world)")

    # ---- new business by calendar year
    if len(bp.nb):
        nb = bp.nb.copy()
        nb["year"] = [cfg.date(k).year for k in nb["month"]]
        agg = _with_total(nb.groupby(["product", "year"], as_index=False, sort=False)
                          [VNB_SUM_COLS].sum(), ["year"])
        _table(wb.create_sheet("New_Business"), agg, {"vnb_margin": PCT, "year": "0",
                                                      "check_vnb_plus_bel": NUM2}, width=20)

    # ---- economic basis
    ws = wb.create_sheet("Real_World_Returns")
    years = np.array([cfg.date(t).year for t in range(1, bp.months + 1)])
    uniq = np.unique(years)
    _header(ws, ["Fund return over the period", *[str(y) if (years == y).sum() == 12
                                                 else f"{y} ({(years == y).sum()}m)" for y in uniq]],
            first_width=30, width=14)
    for f, code in enumerate(a.fund_codes):
        _row(ws, code, [float(np.prod(1.0 + bp.rw_growth[f, years == y]) - 1.0) for y in uniq], PCT)
    ws = wb.create_sheet("Forward_Curves")
    _header(ws, ["Maturity (years)", *dates], first_width=18, width=14)
    for k in range(a.spot.size):
        _row(ws, k + 1, [float(bp.forward_spots[h][k]) for h, _, _ in bp.solvency], "0.000%")
    _info_sheet(wb, info)
    return [_save(wb, cfg.output_dir / "business_plan" / "business_plan.xlsx")]
