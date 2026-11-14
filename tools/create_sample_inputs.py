"""Create the input workbooks (templates filled with ILLUSTRATIVE sample data).

    python tools/create_sample_inputs.py            # refuses to overwrite existing files
    python tools/create_sample_inputs.py --force    # overwrite

Outputs (in inputs/):
    run_config.xlsx, economic_2025Q4.xlsx, assumptions.xlsx, products.xlsx,
    in_force/<PRODUCT>.xlsx, new_business/<PRODUCT>.xlsx
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
VAL_DATE = dt.date(2025, 12, 31)
PRODUCTS = ["UL_SAVINGS", "UL_ROP", "UL_PROTECT"]

HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(bold=True, color="FFFFFF")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
PCT, PCT4, NUM, INT, DATE = "0.00%", "0.0000%", "#,##0.00", "#,##0", "yyyy-mm-dd"


# --------------------------------------------------------------------------- sheet helpers
def header(ws, cols, widths=None, comments=None):
    ws.append(cols)
    for i, _ in enumerate(cols, 1):
        c = ws.cell(row=1, column=i)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, 16)
        if comments and cols[i - 1] in comments:
            c.comment = Comment(comments[cols[i - 1]], "ULModel")
    ws.freeze_panes = "A2"


def fmt_cols(ws, formats: dict, fill=True):
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if c.column in formats:
                c.number_format = formats[c.column]
                if fill:
                    c.fill, c.border = INPUT_FILL, BORDER


def param_sheet(wb, title, rows):
    """rows: (parameter, value, number format or None, description)."""
    ws = wb.create_sheet(title)
    header(ws, ["Parameter", "Value", "Description"], {1: 30, 2: 26, 3: 100})
    for name, value, fmt, desc in rows:
        ws.append([name, value, desc])
        c = ws.cell(row=ws.max_row, column=2)
        c.fill, c.border = INPUT_FILL, BORDER
        c.alignment = Alignment(horizontal="right")
        if fmt:
            c.number_format = fmt
    return ws


def readme_sheet(wb, title, lines):
    ws = wb.active
    ws.title = "README"
    ws.column_dimensions["A"].width = 125
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14, color="1F3864")
    for i, line in enumerate(lines, 3):
        ws.cell(row=i, column=1, value=line).alignment = Alignment(wrap_text=True, vertical="top")
    ws.sheet_view.showGridLines = False


def corr_sheet(wb, title, labels, pairs, note):
    ws = wb.create_sheet(title)
    header(ws, ["", *labels], {1: 14})
    n = len(labels)
    for i, a in enumerate(labels):
        ws.append([a, *[1.0 if i == j else pairs.get((a, b), pairs.get((b, a), 0.0))
                        for j, b in enumerate(labels)]])
        ws.cell(row=i + 2, column=1).font = Font(bold=True)
    fmt_cols(ws, {j: "0.00" for j in range(2, n + 2)})
    ws.cell(row=n + 4, column=1, value=note)


# --------------------------------------------------------------------------- run config
def build_config(path: Path):
    wb = Workbook()
    readme_sheet(wb, "ULModel - run configuration", [
        "This workbook drives a run: python run_model.py  (or double-click run_model.bat).",
        "Yellow cells are inputs. Paths are relative to the folder of this workbook.",
        "",
        "Run types:",
        "   VALUATION     : Solvency II valuation of the in-force portfolio at the valuation date "
        "(run-off, risk-neutral, all standard formula shocks).",
        "   VNB           : value of the new business issued in the months following the valuation date, "
        "discounted to the valuation date, by product and month of issue.",
        "   BUSINESS_PLAN : real-world projection of in-force + new business to the last 31 December of the "
        "plan, with a full Solvency II valuation at the start and at each 31 December.",
        "   ALL           : the three runs.",
        "",
        "The valuation date can be any month end. Month 1 of every projection is the month following it; "
        "new business with issue month = month 1 is issued at the valuation date + 1 day.",
    ])
    ws = param_sheet(wb, "Run", [
        ("run_type", "ALL", None, "VALUATION, VNB, BUSINESS_PLAN or ALL."),
        ("valuation_date", VAL_DATE, DATE, "Start date of the model. Must be a month end (e.g. any quarter end)."),
        ("projection_years", 40, INT, "Horizon of the risk-neutral projections, in years (max 40, monthly steps). Remaining fund value is released at the horizon."),
        ("vnb_months", 12, INT, "VNB run: number of issue months included, starting the month after the valuation date (3 = next quarter, 12 = next year)."),
        ("bp_years", 5, INT, "BUSINESS_PLAN run: number of years. If the valuation date is not a 31 December, the plan is extended to the next 31 December."),
        ("economic_file", "economic_2025Q4.xlsx", None, "Economic assumptions at the valuation date (one file per quarter)."),
        ("assumptions_file", "assumptions.xlsx", None, "Non-economic assumptions."),
        ("products_file", "products.xlsx", None, "Product features."),
        ("in_force_folder", "in_force", None, "Folder with one workbook per product: in-force model points at the valuation date."),
        ("new_business_folder", "new_business", None, "Folder with one workbook per product: future new business model points."),
        ("output_folder", "../outputs", None, "Outputs are written to <output_folder>/valuation, /vnb and /business_plan."),
        ("scenario_detail", "N", None, "Y: VALUATION also reports the present value of every cash flow under each shock (slower on large portfolios)."),
        ("mp_detail", "N", None, "Y: VALUATION also writes the BEL per model point and scenario."),
        ("chunk_size", 5000, INT, "Number of model points projected per batch. 5,000-10,000 keeps the arrays in the CPU cache."),
    ])
    rt = DataValidation(type="list", formula1='"VALUATION,VNB,BUSINESS_PLAN,ALL"', allow_blank=False)
    yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=False)
    ws.add_data_validation(rt)
    ws.add_data_validation(yn)
    rt.add("B2")
    yn.add("B13")
    yn.add("B14")
    wb.save(path)


# --------------------------------------------------------------------------- economic
def ir_shock(m):
    up = [70, 70, 64, 59, 55, 52, 49, 47, 44, 42, 39, 37, 35, 34, 33, 31, 30, 29, 27, 26]
    dn = [75, 65, 56, 50, 46, 42, 39, 36, 33, 31, 30, 29, 28, 28, 27, 28, 28, 28, 29, 29]
    if m <= 20:
        return up[m - 1] / 100, dn[m - 1] / 100
    w = (m - 20) / 70.0                       # linear interpolation to 20% at maturity 90
    return (26 + (20 - 26) * w) / 100, (29 + (20 - 29) * w) / 100


def build_economic(path: Path):
    wb = Workbook()
    readme_sheet(wb, "ULModel - economic assumptions at the valuation date", [
        "One workbook per valuation date (referenced by run_config.xlsx). Yellow cells are inputs.",
        "",
        "Parameters  : expense inflation and the equity symmetric adjustment published by EIOPA.",
        "Yield_Curve : risk-free spot rates (annual compounding) for maturities 1..40 years from the valuation "
        "date, with the Solvency II relative interest rate shocks. The model derives monthly discount factors "
        "(constant forward rate within each year) and the shocked curves.",
        "Real_World  : annual return in excess of the risk-free forward rate, per asset class, used by the "
        "BUSINESS_PLAN run only. A fund earns the risk-free rate plus the premium of its asset mix (Funds sheet).",
        "",
        "IMPORTANT: illustrative sample data. Replace the curve with the EIOPA risk-free term structure.",
    ])
    param_sheet(wb, "Parameters", [
        ("expense_inflation", 0.02, PCT, "Annual inflation applied to per-policy and per-claim expenses."),
        ("equity_symmetric_adj", 0.0, PCT, "Equity symmetric adjustment at the valuation date (between -10% and +10%)."),
    ])
    ws = wb.create_sheet("Yield_Curve")
    header(ws, ["maturity", "spot_rate", "shock_up_rel", "shock_down_rel"], comments={
        "maturity": "Years from the valuation date.",
        "spot_rate": "Risk-free zero-coupon rate, annual compounding.",
        "shock_up_rel": "Relative upward shock (art. 166). Minimum absolute shock set in assumptions.xlsx / S2_Shocks.",
        "shock_down_rel": "Relative downward shock (art. 167). Negative rates are not shocked."})
    for m in range(1, 41):
        up, dn = ir_shock(m)
        ws.append([m, round(0.033 - 0.011 * np.exp(-m / 15.0), 5), round(up, 5), round(dn, 5)])
    fmt_cols(ws, {2: "0.000%", 3: PCT, 4: PCT})
    ws = wb.create_sheet("Real_World")
    header(ws, ["asset_class", "risk_premium"], {1: 18, 2: 16}, comments={
        "risk_premium": "Annual expected return in excess of the risk-free forward rate."})
    for name, rp in [("equity_type1", 0.035), ("equity_type2", 0.045), ("property", 0.025),
                     ("govt_bonds", 0.0), ("corp_bonds", 0.01), ("cash", 0.0)]:
        ws.append([name, rp])
    fmt_cols(ws, {2: PCT})
    wb.save(path)


# --------------------------------------------------------------------------- assumptions
def build_assumptions(path: Path):
    wb = Workbook()
    readme_sheet(wb, "ULModel - non-economic assumptions", [
        "Yellow cells are inputs. Do not rename sheets or column headers (the model reads them by name).",
        "All rates are ANNUAL; the model converts them to monthly rates (1 - (1 - q)^(1/12) for decrements, "
        "one twelfth for charges and expenses).",
        "",
        "General             : tax, cost of capital, Solvency II options.",
        "Mortality           : best-estimate base table qx by age and sex (scaled per product in Product_Assumptions).",
        "Lapse               : best-estimate annual FULL surrender rates by policy year, one column per product "
        "(the last row applies to all later policy years).",
        "Partial_Surrender   : best-estimate annual PARTIAL surrender rates (share of fund value withdrawn), same layout.",
        "Product_Assumptions : one row per product: mortality scaling, the two expense types (per policy and "
        "% of fund value), claim and acquisition expenses.",
        "Funds               : asset mix of each unit-linked fund, used for the market shocks and the real-world returns.",
        "S2_Shocks           : Solvency II standard formula stress parameters.",
        "Corr_Market / Corr_Life : standard formula correlation matrices.",
        "",
        "IMPORTANT: illustrative sample data, not calibrated assumptions.",
    ])
    param_sheet(wb, "General", [
        ("tax_rate", 0.25, PCT, "Corporate tax rate used in the revenue accounts and for PVFP / VNB net of tax."),
        ("cost_of_capital_rate", 0.06, PCT, "Cost-of-capital rate for the risk margin."),
        ("risk_margin_driver", "PVFP", None, "Run-off pattern of the SCR for the risk margin: PVFP (remaining value of future profits), FUND_VALUE or POLICIES."),
        ("policy_level_flooring", "Y", None, "Y: mortality / longevity / lapse / CAT shocks only count model points whose BEL increases (Delegated Reg. art. 137-143). N: net at product level."),
    ])

    ws = wb.create_sheet("Mortality")
    header(ws, ["age", "qx_male", "qx_female"])
    for age in range(0, 121):
        qm = 1 - np.exp(-(0.0003 + 0.000025 * 1.098 ** age))
        qf = 1 - np.exp(-(0.0002 + 0.000015 * 1.100 ** age))
        ws.append([age, 1.0 if age == 120 else round(min(qm, 1), 6), 1.0 if age == 120 else round(min(qf, 1), 6)])
    fmt_cols(ws, {2: PCT4, 3: PCT4})

    ws = wb.create_sheet("Lapse")
    header(ws, ["policy_year", *PRODUCTS], comments={
        "policy_year": "1 = first policy year. The last row is used for all later years."})
    lapse = {
        "UL_SAVINGS": [0.03, 0.04, 0.05, 0.06, 0.06, 0.06, 0.06, 0.07, 0.07, 0.06, 0.05, 0.05, 0.05, 0.05, 0.05],
        "UL_ROP": [0.03, 0.035, 0.04, 0.045, 0.05, 0.05, 0.045, 0.045, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04],
        "UL_PROTECT": [0.04, 0.04, 0.04, 0.04, 0.04, 0.035, 0.035, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03],
    }
    for y in range(15):
        ws.append([y + 1, *[lapse[p][y] for p in PRODUCTS]])
    fmt_cols(ws, {2: PCT, 3: PCT, 4: PCT})

    ws = wb.create_sheet("Partial_Surrender")
    header(ws, ["policy_year", *PRODUCTS], comments={
        "policy_year": "1 = first policy year. The last row is used for all later years. "
                       "Annual share of the fund value withdrawn by policies remaining in force. "
                       "The surrender charge scale applies to the amount withdrawn; the "
                       "return-of-premium guarantee is reduced pro rata. Set 0% for no partial surrender."})
    partial = {"UL_SAVINGS": [0.0, 0.01, 0.02, 0.025, 0.03], "UL_ROP": [0.0, 0.005, 0.01, 0.01, 0.01],
               "UL_PROTECT": [0.0, 0.0, 0.005, 0.005, 0.005]}
    for y in range(5):
        ws.append([y + 1, *[partial[p][y] for p in PRODUCTS]])
    fmt_cols(ws, {2: PCT, 3: PCT, 4: PCT})

    ws = wb.create_sheet("Product_Assumptions")
    cols = ["product_code", "mortality_factor", "expense_per_policy", "expense_pct_fund_value",
            "claim_expense", "acquisition_expense_per_policy", "acquisition_expense_pct_premium"]
    header(ws, cols, {1: 18, 2: 18, 3: 20, 4: 24, 5: 16, 6: 30, 7: 32}, comments={
        "mortality_factor": "Best-estimate mortality as a % of the base table.",
        "expense_per_policy": "Expense type 1 - ANNUAL cost per policy in force, in money of the valuation date (inflated afterwards).",
        "expense_pct_fund_value": "Expense type 2 - ANNUAL cost as a % of fund value (not inflated: it follows the fund).",
        "claim_expense": "Cost per death / surrender / maturity, in money of the valuation date (inflated afterwards).",
        "acquisition_expense_per_policy": "New business: cost per policy issued, in money of the valuation date (inflated to the issue month).",
        "acquisition_expense_pct_premium": "New business: acquisition cost as % of the single premium (in addition to commission)."})
    ws.append(["UL_SAVINGS", 0.85, 45.0, 0.0008, 80.0, 150.0, 0.003])
    ws.append(["UL_ROP", 0.80, 55.0, 0.0008, 80.0, 180.0, 0.003])
    ws.append(["UL_PROTECT", 0.75, 70.0, 0.0008, 120.0, 250.0, 0.003])
    fmt_cols(ws, {2: PCT, 3: NUM, 4: "0.000%", 5: NUM, 6: NUM, 7: PCT})

    ws = wb.create_sheet("Funds")
    cols = ["fund_code", "fund_name", "equity_type1", "equity_type2", "property", "govt_bonds",
            "corp_bonds", "cash", "bond_duration", "spread_stress_pct", "fx_exposure_pct"]
    header(ws, cols, {1: 14, 2: 28, 9: 15, 10: 18, 11: 17}, comments={
        "equity_type1": "Share of the fund in type 1 equities (EEA / OECD listed). The six asset classes must sum to 100%.",
        "equity_type2": "Share in type 2 equities (other listed, unlisted, commodities, alternatives).",
        "govt_bonds": "EEA government bonds: interest rate risk only (no spread stress).",
        "corp_bonds": "Corporate bonds: interest rate + spread risk.",
        "bond_duration": "Modified duration (years) of the bond holdings, used to reprice them under the interest rate shocks.",
        "spread_stress_pct": "Spread stress factor applied to the corporate bonds (art. 176 factor for the portfolio's rating and duration).",
        "fx_exposure_pct": "Share of the whole fund denominated in foreign currency (unhedged)."})
    for f in [
        ("EQ_GLOBAL", "Global equity", 1.00, 0, 0, 0, 0, 0.00, 0, 0, 0.60),
        ("EQ_EMERGING", "Emerging markets equity", 0, 1.00, 0, 0, 0, 0.00, 0, 0, 1.00),
        ("BALANCED", "Balanced multi-asset", 0.45, 0.05, 0.05, 0.25, 0.15, 0.05, 6.0, 0.085, 0.25),
        ("BOND_EUR", "Euro aggregate bonds", 0, 0, 0, 0.50, 0.45, 0.05, 7.0, 0.09, 0.00),
        ("PROPERTY", "European property", 0, 0, 0.90, 0, 0, 0.10, 0, 0, 0.10),
        ("MONEY", "Money market", 0, 0, 0, 0, 0, 1.00, 0, 0, 0.00),
    ]:
        ws.append(list(f))
    fmt_cols(ws, {**{j: PCT for j in range(3, 9)}, 9: "0.0", 10: PCT, 11: PCT})

    param_sheet(wb, "S2_Shocks", [
        ("mortality_up", 0.15, PCT, "Permanent relative increase in (annual) mortality rates (art. 137)."),
        ("longevity_down", 0.20, PCT, "Permanent relative decrease in mortality rates (art. 138)."),
        ("lapse_up", 0.50, PCT, "Permanent relative increase in (annual) lapse rates, capped at 100% (art. 142)."),
        ("lapse_down", 0.50, PCT, "Permanent relative decrease in lapse rates (art. 142)."),
        ("lapse_down_max_abs", 0.20, PCT, "Maximum absolute decrease of the annual lapse rate in the lapse-down shock."),
        ("lapse_mass", 0.40, PCT, "Mass lapse: share of policies surrendering immediately (40% retail, 70% for the business of art. 142(6)(b))."),
        ("expense_up", 0.10, PCT, "Relative increase in expenses (art. 140)."),
        ("expense_inflation_add", 0.01, PCT, "Absolute increase in the expense inflation rate (art. 140)."),
        ("life_cat_add", 0.0015, PCT, "Absolute increase in annual mortality rates over the next 12 months (art. 143)."),
        ("equity_type1", 0.39, PCT, "Type 1 equity stress before symmetric adjustment (art. 169)."),
        ("equity_type2", 0.49, PCT, "Type 2 equity stress before symmetric adjustment (art. 169)."),
        ("equity_corr_t1_t2", 0.75, "0.00", "Correlation between type 1 and type 2 equity capital requirements."),
        ("property", 0.25, PCT, "Property stress (art. 174)."),
        ("currency", 0.25, PCT, "Foreign currency stress (art. 188) applied to the unhedged foreign exposure of the funds."),
        ("ir_up_min_abs", 0.01, PCT, "Minimum absolute increase of rates in the interest-up shock (art. 166)."),
        ("corr_market_life", 0.25, "0.00", "Correlation between the market and life modules in the BSCR."),
        ("corr_interest_a_up", 0.0, "0.00", "Parameter A (interest vs equity/property/spread) when the interest-UP shock bites. Corr_Market holds the DOWN case (0.5)."),
        ("op_risk_expense_factor", 0.25, PCT, "Operational risk: share of annual unit-linked expenses (art. 204)."),
    ])
    corr_sheet(wb, "Corr_Market", ["interest", "equity", "property", "spread", "currency"], {
        ("interest", "equity"): 0.5, ("interest", "property"): 0.5, ("interest", "spread"): 0.5,
        ("interest", "currency"): 0.25, ("equity", "property"): 0.75, ("equity", "spread"): 0.75,
        ("equity", "currency"): 0.25, ("property", "spread"): 0.5, ("property", "currency"): 0.25,
        ("spread", "currency"): 0.25,
    }, "Matrix for the interest-DOWN case (A = 0.5). When the interest-UP shock bites, A is replaced by S2_Shocks!corr_interest_a_up.")
    corr_sheet(wb, "Corr_Life", ["mortality", "longevity", "lapse", "expense", "cat"], {
        ("mortality", "longevity"): -0.25, ("mortality", "lapse"): 0.0, ("mortality", "expense"): 0.25,
        ("mortality", "cat"): 0.25, ("longevity", "lapse"): 0.25, ("longevity", "expense"): 0.25,
        ("longevity", "cat"): 0.0, ("lapse", "expense"): 0.5, ("lapse", "cat"): 0.25,
        ("expense", "cat"): 0.25,
    }, "Life underwriting correlation matrix (disability and revision risks are not applicable to these products).")
    wb.save(path)


# --------------------------------------------------------------------------- products
FEATURES = [
    # parameter, description, format, values per product
    ("product_code", "Unique code. Must match the model point file names and the assumptions workbook.", None, PRODUCTS),
    ("product_name", "Free description.", None, ["Single premium unit-linked savings bond",
                                                "Single premium unit-linked with return-of-premium guarantee",
                                                "Single premium unit-linked whole of life with sum assured"]),
    ("amc_pct", "Annual management charge, % of fund value per year, deducted monthly from the fund.", PCT, [0.0085, 0.012, 0.010]),
    ("policy_fee", "Fixed ANNUAL policy fee per policy, deducted monthly from the fund.", NUM, [0.0, 36.0, 48.0]),
    ("allocation_charge_pct", "Premium loading: share of the single premium NOT invested in units.", PCT, [0.01, 0.03, 0.03]),
    ("death_benefit_type", "FV_PCT: % of fund value | ROP: max(fund value, premiums paid) | FV_PLUS_SA: fund value + sum assured.", None, ["FV_PCT", "ROP", "FV_PLUS_SA"]),
    ("death_benefit_pct", "Death benefit as % of fund value (FV_PCT only).", PCT, [1.01, 1.0, 1.0]),
    ("coi_pct_of_qx", "Cost-of-insurance charge = this % x best-estimate mortality x sum at risk, deducted monthly (0% = none).", PCT, [0.0, 0.0, 1.30]),
    ("fund_rebate_pct", "Rebates received from fund managers, % of fund value per year.", PCT, [0.0030, 0.0025, 0.0020]),
    ("commission_initial_pct", "Initial commission, % of the single premium (new business).", PCT, [0.02, 0.03, 0.03]),
    ("commission_trail_pct", "Trail commission, % of fund value per year.", PCT, [0.0025, 0.002, 0.001]),
]
SURRENDER = {"UL_SAVINGS": [0.03, 0.02, 0.01, 0, 0], "UL_ROP": [0.05, 0.04, 0.03, 0.02, 0.01],
             "UL_PROTECT": [0.04, 0.03, 0.02, 0.01, 0]}


def build_products(path: Path):
    wb = Workbook()
    readme_sheet(wb, "ULModel - product features", [
        "Products           : contractual features, one column per product (add a column to add a product).",
        "Surrender_Charges  : surrender penalty (% of fund value) by policy year, one column per product; "
        "0% after the last row.",
        "",
        "All products are single premium. In-force business is projected in run-off (no future premium); "
        "new business pays its single premium at issue.",
        "A product also needs: a Lapse column and a Product_Assumptions row in assumptions.xlsx, and model "
        "point workbooks named <product_code>.xlsx in the in_force and/or new_business folders.",
    ])
    ws = wb.create_sheet("Products")
    header(ws, ["Parameter", "Description", *PRODUCTS], {1: 26, 2: 95, 3: 22, 4: 22, 5: 22})
    for name, desc, fmt, values in FEATURES:
        ws.append([name, desc, *values])
        for j in range(3, 3 + len(PRODUCTS)):
            c = ws.cell(row=ws.max_row, column=j)
            c.fill, c.border = INPUT_FILL, BORDER
            c.alignment = Alignment(horizontal="right", wrap_text=True)
            if fmt:
                c.number_format = fmt
    dv = DataValidation(type="list", formula1='"FV_PCT,ROP,FV_PLUS_SA"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("C7:E7")
    ws.freeze_panes = "C2"

    ws = wb.create_sheet("Surrender_Charges")
    header(ws, ["policy_year", *PRODUCTS])
    for y in range(5):
        ws.append([y + 1, *[SURRENDER[p][y] for p in PRODUCTS]])
    fmt_cols(ws, {2: PCT, 3: PCT, 4: PCT})
    wb.save(path)


# --------------------------------------------------------------------------- model points
IF_COLS = ["mp_id", "policy_count", "date_of_birth", "sex", "issue_date", "maturity_date",
           "fund_code", "fund_value", "sum_assured", "premiums_paid"]
IF_COMMENTS = {
    "policy_count": "Number of policies grouped in the model point.",
    "date_of_birth": "Date of birth of the insured (attained age is tracked in months).",
    "sex": "M or F.",
    "issue_date": "Policy inception date (drives the policy year for lapses and surrender charges).",
    "maturity_date": "Maturity date. Blank = whole of life.",
    "fund_code": "Fund code as defined in assumptions.xlsx, sheet Funds.",
    "fund_value": "TOTAL unit fund value of the model point at the valuation date.",
    "sum_assured": "TOTAL additional death sum assured (death_benefit_type = FV_PLUS_SA).",
    "premiums_paid": "TOTAL premiums paid (death_benefit_type = ROP).",
}
NB_COLS = ["mp_id", "issue_month", "policy_count", "age_at_issue", "sex", "term_years",
           "fund_code", "single_premium", "sum_assured"]
NB_COMMENTS = {
    "issue_month": "Month in which the policies enter the portfolio (any date in the month). They are issued at the start of that month. Months on or before the valuation date are ignored.",
    "policy_count": "Number of policies sold in the model point.",
    "age_at_issue": "Age at issue (years).",
    "term_years": "Policy term in years. Blank or 0 = whole of life.",
    "single_premium": "TOTAL single premium of the model point.",
    "sum_assured": "TOTAL additional death sum assured (death_benefit_type = FV_PLUS_SA).",
}
FUNDS = np.array(["EQ_GLOBAL", "EQ_EMERGING", "BALANCED", "BOND_EUR", "PROPERTY", "MONEY"])
FUND_P = [0.30, 0.07, 0.38, 0.13, 0.07, 0.05]


def mp_workbook(path: Path, title: str, notes: list, cols, comments, rows, formats):
    wb = Workbook()
    readme_sheet(wb, title, notes)
    ws = wb.create_sheet("ModelPoints")
    header(ws, cols, {1: 14}, comments)
    for r in rows:
        ws.append(r)
    fmt_cols(ws, formats, fill=False)
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return dt.date(y, m + 1, min(d.day, 28))


def in_force_rows(rng, kind: str, n: int) -> list:
    rows = []
    for i in range(n):
        count = int(rng.integers(1, 9))
        dur_m = int(rng.integers(0, 15 * 12))
        if kind == "UL_SAVINGS":
            age_m, term = int(rng.integers(30 * 12, 80 * 12)), None
        elif kind == "UL_ROP":
            term = int(rng.integers(10, 31))
            dur_m = int(rng.integers(0, min(term, 12) * 12))
            age_m = int(rng.integers(30 * 12, 70 * 12))
        else:
            age_m, term = int(rng.integers(30 * 12, 65 * 12)), None
        issue = add_months(VAL_DATE, -dur_m).replace(day=int(rng.integers(1, 29)))
        if issue > VAL_DATE:
            issue = VAL_DATE
        dob = add_months(VAL_DATE, -age_m).replace(day=int(rng.integers(1, 29)))
        maturity = add_months(issue, term * 12) if term else None
        paid = float(np.round(rng.lognormal(10.5, 0.7), -2))
        fv = paid * max(1.0 + rng.normal(0.03, 0.03), 0.9) ** (dur_m / 12)
        sa = float(rng.choice([10000, 20000, 30000, 50000, 75000])) if kind == "UL_PROTECT" else 0.0
        rows.append([f"{kind[3:6]}{i + 1:05d}", count, dob, str(rng.choice(["M", "F"])), issue, maturity,
                     str(rng.choice(FUNDS, p=FUND_P)), round(fv * count, 2), round(sa * count, 2),
                     round(paid * count, 2)])
    return rows


def new_business_rows(rng, kind: str, per_month: int, months: int) -> list:
    rows = []
    for k in range(1, months + 1):
        month = add_months(VAL_DATE, k).replace(day=1)
        growth = 1.03 ** ((k - 1) // 12)                      # sales plan grows 3% a year
        for j in range(per_month):
            count = int(rng.integers(5, 41))
            if kind == "UL_SAVINGS":
                age, term = int(rng.integers(35, 76)), None
            elif kind == "UL_ROP":
                age, term = int(rng.integers(30, 66)), int(rng.choice([10, 15, 20, 25]))
            else:
                age, term = int(rng.integers(30, 61)), None
            prem = float(np.round(rng.lognormal(10.4, 0.6) * growth, -2))
            sa = float(rng.choice([10000, 20000, 30000, 50000, 75000])) if kind == "UL_PROTECT" else 0.0
            count = max(1, count // 6)                        # keeps plan volumes realistic
            rows.append([f"N{kind[3:6]}{month:%y%m}{j + 1:02d}", month, count, age,
                         str(rng.choice(["M", "F"])), term, str(rng.choice(FUNDS, p=FUND_P)),
                         round(prem * count, 2), round(sa * count, 2)])
    return rows


IN_FORCE_N = {"UL_SAVINGS": 4500, "UL_ROP": 3300, "UL_PROTECT": 2200}   # 10,000 model points
NB_PER_MONTH = {"UL_SAVINGS": 30, "UL_ROP": 22, "UL_PROTECT": 15}       # 804 per year
NB_MONTHS = 60


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", default=str(ROOT / "inputs"))
    ap.add_argument("--force", action="store_true", help="overwrite existing workbooks")
    args = ap.parse_args()

    inputs = Path(args.inputs)
    single = {"run_config.xlsx": build_config, "economic_2025Q4.xlsx": build_economic,
              "assumptions.xlsx": build_assumptions, "products.xlsx": build_products}
    targets = [inputs / n for n in single] + [inputs / d / f"{p}.xlsx" for d in ("in_force", "new_business")
                                              for p in PRODUCTS]
    existing = [t for t in targets if t.exists()]
    if existing and not args.force:
        sys.exit("Refusing to overwrite existing inputs (use --force):\n  " + "\n  ".join(map(str, existing)))
    for d in ("in_force", "new_business"):
        (inputs / d).mkdir(parents=True, exist_ok=True)

    for name, fn in single.items():
        fn(inputs / name)
        print(f"written {inputs / name}")
    rng = np.random.default_rng(2026)
    for p in PRODUCTS:
        mp_workbook(inputs / "in_force" / f"{p}.xlsx", f"ULModel - in-force model points: {p}", [
            f"In-force model points at the valuation date ({VAL_DATE}). The file name is the product code.",
            "One row per model point. Monetary amounts are TOTALS for the model point (all its policies).",
            "In-force business is projected in run-off: no future premium.",
            "Paste your policy extract in sheet ModelPoints (values only, keep the header row).",
        ], IF_COLS, IF_COMMENTS, in_force_rows(rng, p, IN_FORCE_N[p]),
            {2: INT, 3: DATE, 5: DATE, 6: DATE, 8: NUM, 9: NUM, 10: NUM})
        print(f"written {inputs / 'in_force' / (p + '.xlsx')}  ({IN_FORCE_N[p]:,} model points)")
    for p in PRODUCTS:
        rows = new_business_rows(rng, p, NB_PER_MONTH[p], NB_MONTHS)
        mp_workbook(inputs / "new_business" / f"{p}.xlsx", f"ULModel - new business model points: {p}", [
            "Future new business (sales plan). The file name is the product code.",
            "One row per model point and issue month. Monetary amounts are TOTALS for the model point.",
            "The VNB run uses the issue months within run_config!vnb_months after the valuation date; "
            "the BUSINESS_PLAN run uses all issue months within the plan horizon.",
            "Policies are issued at the start of their issue month and pay a single premium at issue.",
        ], NB_COLS, NB_COMMENTS, rows, {2: "yyyy-mm", 3: INT, 8: NUM, 9: NUM})
        print(f"written {inputs / 'new_business' / (p + '.xlsx')}  ({len(rows):,} model points)")


if __name__ == "__main__":
    main()
