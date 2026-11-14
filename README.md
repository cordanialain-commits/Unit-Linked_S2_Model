# ULModel — unit-linked Solvency II projection model

Deterministic, monthly cash-flow model for single premium unit-linked business, with three
run types sharing one projection engine:

| Run type | What it does | Output folder |
|---|---|---|
| `VALUATION` | Solvency II valuation of the in-force portfolio at any month end: BEL, PVFP, all standard formula shocks, SCR, risk margin | `outputs/valuation/` |
| `VNB` | Value of the new business issued in the months after the valuation date, discounted to the valuation date, by product and month of issue | `outputs/vnb/` |
| `BUSINESS_PLAN` | Real-world projection of in-force + new business to the last 31 December of the plan, with a full Solvency II valuation at the start and at every 31 December | `outputs/business_plan/` |

Python + numpy, Excel in / Excel out. The full formula specification is in
[docs/technical_documentation.md](docs/technical_documentation.md).

## Run it

```
python run_model.py                        uses inputs/run_config.xlsx (or double-click run_model.bat)
python run_model.py --run-type VNB         override the run type (VALUATION, VNB, BUSINESS_PLAN, ALL)
python run_model.py --config <file>        another configuration workbook
python tests/test_model.py                 validation tests
python tools/create_sample_inputs.py --force   re-create the sample input workbooks
```

Requires Python 3.10+ with `numpy`, `pandas`, `openpyxl`.

## Inputs

| File | Content |
|---|---|
| `inputs/run_config.xlsx` | Run type, valuation date (any month end), horizons, paths, options |
| `inputs/economic_<date>.xlsx` | Yield curve and interest shocks, expense inflation, equity symmetric adjustment, real-world risk premia by asset class — one file per valuation date |
| `inputs/assumptions.xlsx` | Mortality, full and partial surrender rates, expenses (per policy and % of fund value), claim and acquisition expenses, fund asset mixes, S2 shocks, correlations, tax, cost of capital |
| `inputs/products.xlsx` | Product features (one column per product) and surrender charge scales |
| `inputs/in_force/<PRODUCT>.xlsx` | In-force model points at the valuation date: birth, issue and maturity dates, fund, fund value, sum assured, premiums paid |
| `inputs/new_business/<PRODUCT>.xlsx` | Future new business: issue month, policies, age, term, fund, single premium, sum assured |

Yellow cells are inputs; each workbook has a README sheet and header comments. All rates are
annual; the model converts them to monthly. **The delivered figures are illustrative sample
data**, not calibrated assumptions (10,000 in-force model points, 804 new business model points
per year over 5 years).

To value another quarter: copy the economic workbook, update the curve, point
`run_config.xlsx` to it, change `valuation_date`, and replace the in-force model points.
To value the new business of a quarter, set the valuation date to the previous quarter end and
`vnb_months = 3`.

## Outputs

In every file the total of all products comes first (column B, first rows or first sheet),
followed by the products in the column order of `products.xlsx`, so its position does not
depend on the number of products.

**Valuation**
- `results_summary.xlsx` — key metrics per product and total, scenario detail, curves.
- `valuation_variables.xlsx` — present value of every cash flow, PVFP by source, undiscounted
  totals, four reconciliation checks, risk margin and all SCR components (optionally the
  present values under every shock with `scenario_detail = Y`).
- `revenue_account.xlsx` — central run-off P&L by calendar year and by month.

**VNB**
- `vnb_results.xlsx` — VNB and its decomposition by product, by issue month, and one sheet per
  product with issue months in columns; premiums, margin, and the check `VNB + BEL = 0`.

**Business plan**
- `business_plan.xlsx` — Solvency II position at each 31 December (fund value, BEL, risk
  margin, SCR by module), real-world P&L by calendar year and by month, new business volumes
  and value by year of issue, real-world fund returns and the forward curves used.

## Method in brief

- **Monthly steps** from any month end, up to 480 months. New business enters at the start of
  its issue month. The yield curve is annual with a constant forward rate within each year.
- **Risk-neutral runs** (valuation, VNB): funds earn the forward rate of the scenario curve and
  cash flows are discounted on the same curve, so `fund value = BEL + PVFP` exactly.
- **Business plan**: funds earn the forward rate plus the risk premium of their asset mix. At
  each 31 December the projected portfolio is revalued with all shocks on the forward curve
  implied by the starting curve, with unit costs inflated to that date.
- **SCR**: market (interest, equity, property, spread, currency) and life (mortality,
  longevity, lapse up/down/mass, expense, catastrophe) modules, standard correlations,
  operational risk; risk margin by cost of capital. The `TOTAL` column sums shock losses across
  products before aggregation, so it includes diversification between products.

## Validation

`tests/test_model.py` checks: the engine against an independent single-policy monthly
projection (in-force under every scenario, new business in the central scenario, to 1e-8
relative); `FV = BEL + PVFP`, `BEL = PV of cash flow lines`, revenue account = result by source
and `VNB = −BEL`; roll-forward consistency of the year-end valuations; a 31 March start date.

## Simplifications to be aware of

- Deterministic: guarantees (return of premium, sum assured) are valued at intrinsic cost only — no TVOG.
- Single premium only; no fund switches or top-ups. Partial surrenders are a deterministic
  share of the fund value; the lapse up/down shocks apply to them, the mass lapse does not.
- Not modelled: counterparty default and concentration risk, loss-absorbing capacity of deferred
  taxes, disability and revision risk.
- VNB is before tax, risk margin and cost of capital.
- Business plan: fund mixes are constant, future curves are the forwards of the starting curve,
  the equity symmetric adjustment and all non-economic assumptions are unchanged over the plan.
- Spread stress is a fund-level input factor; currency is a single downward shock.
- Operational risk uses the first 12 projected months of expenses as a proxy for the last 12.
- Policy-level flooring is applied at model-point level.

