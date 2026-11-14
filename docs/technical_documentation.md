# ULModel — Technical documentation

This document specifies every formula used by the model, in the order in which the
calculation is performed. It describes the code as implemented in `ulmodel/` (version 2.1.0:
monthly time step, three run types, partial surrenders). Section 17 maps each section to the source file.

Formulas are written in plain notation: `x_t` is the value of `x` for projection month `t`,
`1[condition]` equals 1 when the condition is true and 0 otherwise, `floor(x)` is the integer
part of `x`.

---

## 1. Conventions

| Item | Convention |
|---|---|
| Time step | Monthly. `t = 0` is the valuation date `V` (any month end). Month `t` runs from time `t-1` to time `t`; time `t` is the end of the `t`-th calendar month after `V` |
| Horizon | Risk-neutral projections: `H = 12 × projection_years` months (maximum 480). Business plan: section 15 |
| Business | Single premium products only. In-force policies are projected in run-off (no future premium). New business pays one premium at issue |
| Unit of calculation | One model point. Fund quantities are per policy; cash flows are per model point (per-policy amount × policies in force) |
| Annual inputs | All rates and annual amounts in the input workbooks are annual. Decrement rates are converted with `1 − (1 − q)^(1/12)`; charges, fees and running expenses are applied as one twelfth per month |
| Sign | BEL cash flows: outgo positive, income negative. Revenue account: income and outgo both positive under their heading |

**Timing inside month `t`**

1. Start of month (time `t-1`): new business with issue month `t` enters — single premium received, allocated part invested in units, initial commission and acquisition expenses paid.
2. During the month: the unit fund earns the monthly growth rate of the economic basis.
3. End of month (time `t`), in this order: management charge, policy fee, cost-of-insurance charge; expenses, trail commission and rebates; then decrements — deaths first, full surrenders on the survivors, or maturity of all survivors; finally partial surrenders of the policies still in force.

---

## 2. Model point data

Monetary inputs are totals for the model point and are converted to per-policy amounts.

### 2.1 In-force model points (`in_force/<PRODUCT>.xlsx`)

```
N     = policy_count
FV_0  = fund_value    / N          per-policy fund value at V
SA    = sum_assured   / N
G_0   = premiums_paid / N          guaranteed amount for the return-of-premium benefit

a0 = complete months between date_of_birth and V        attained age in months
d0 = complete months between issue_date and V           elapsed policy duration in months
     complete months(D, V) = 12 × (V.year − D.year) + (V.month − D.month) − 1[D.day > V.day]

n  = WHOLE_LIFE                                         if maturity_date is blank
   = max( 12 × (M.year − V.year) + (M.month − V.month) , 1 )     otherwise (M = maturity_date)

k  = 0                                                  entry month: already in force
```

`n` is the month at the end of which all surviving policies mature: a policy maturing during
calendar month `n` after `V` is paid at the end of that month.

### 2.2 New business model points (`new_business/<PRODUCT>.xlsx`)

```
k  = 12 × (I.year − V.year) + (I.month − V.month)       I = issue_month; rows with k < 1 are ignored
N  = policy_count
P  = single_premium / N
SA = sum_assured / N
G_t = P until issue (then reduced by partial surrenders, 6.7)
FV_0 = 0

a0 = 12 × age_at_issue − (k − 1)                        so that the age at issue is reached at time k−1
d0 = −(k − 1)                                           so that the duration is 0 at issue
n  = WHOLE_LIFE                                         if term_years is blank or <= 0
   = (k − 1) + 12 × term_years                          otherwise
```

Policies with issue month `k` are issued at time `k − 1` (start of month `k`); `k = 1` is the
month following the valuation date.

### 2.3 Horizon cap

In risk-neutral projections of horizon `H`, `n` is replaced by `min(n, H)`: the fund value
remaining at the horizon is paid out and reported with maturities. No cap is applied in the
real-world business plan projection.

### 2.4 Age and policy year in month `t`

```
age_t = min( max( floor( (a0 + t − 1) / 12 ) , 0 ) , omega )     attained age in years
py_t  = floor( (d0 + t − 1) / 12 ) + 1                           policy year (>= 1)
```

Rates change at birthdays and policy anniversaries. `omega` is the last age of the mortality table.

---

## 3. Economic basis

### 3.1 Monthly discount factors

Input: annual spot rates `s_j` (annual compounding) for maturities `j = 1 … K` years from the
valuation date (`Yield_Curve` sheet of the economic workbook). Let `D(j) = (1 + s_j)^(−j)` and
`D(0) = 1`. The forward rate is constant within each year, i.e. `ln D` is linear in time:

```
For t = 12 y + m months (0 <= m < 12, y < K):
    DF_t = D(y) × ( D(y+1) / D(y) ) ^ (m / 12)

Beyond the last maturity (t > 12 K), the last annual forward rate is kept:
    DF_t = D(K) × ( D(K) / D(K−1) ) ^ (t/12 − K)
```

### 3.2 Shocked curves

```
Up     s_j^up   = s_j + max( s_j × up_j , ir_up_min_abs )
Down   s_j^down = s_j − max( s_j , 0 ) × down_j            (negative rates are not shocked)
```

`up_j`, `down_j` are the relative shocks of the `Yield_Curve` sheet. Monthly discount factors
of the shocked curves follow 3.1.

### 3.3 Risk-neutral growth

In a risk-neutral projection every fund earns the monthly forward rate of the scenario curve:

```
g_t = DF_(t−1) / DF_t − 1
```

### 3.4 Real-world growth (business plan only)

```
rf_t   = (1 + g_t)^12 − 1                                  annualised base forward rate of month t
rp_F   = SUM over asset classes c of weight_(F,c) × risk_premium_c
g_t^F  = ( 1 + rf_t + rp_F ) ^ (1/12) − 1                  monthly growth of fund F
```

`weight_(F,c)` is the asset allocation of fund `F` (`Funds` sheet, held constant) and
`risk_premium_c` the annual excess return of the asset class (`Real_World` sheet).

### 3.5 Forward curve at a future date

The curve used for a valuation `h` months after the valuation date is the one implied by the
base curve:

```
s_j^(h) = ( DF_h / DF_(h + 12 j) ) ^ (1/j) − 1             j = 1 … K
```

Sections 3.1 and 3.2 are then applied to `s^(h)` (the relative shocks apply by maturity from
the new date).

---

## 4. Scenarios

Each scenario is defined by the parameters below. The central scenario uses the neutral value
in every column.

| Parameter | Meaning | Neutral value |
|---|---|---|
| curve | Yield curve used (base / up / down) | base |
| `phi_F` | Instantaneous multiplier applied at `t = 0` to the value of fund `F` | 1 |
| `mq` | Multiplier on annual mortality rates | 1 |
| `aq` | Absolute addition to the annual mortality rate during months 1 to 12 | 0 |
| `mw` | Multiplier on annual lapse rates | 1 |
| `dw` | Maximum absolute decrease of the annual lapse rate | no limit |
| `me` | Multiplier on expenses | 1 |
| `dj` | Addition to the annual expense inflation rate | 0 |

| Scenario | Definition (parameter names refer to `S2_Shocks`) |
|---|---|
| BASE | all neutral |
| IR_UP / IR_DOWN | curve = up / down; `phi_F` = interest-rate fund factor (4.1) |
| EQUITY_T1 | `phi_F = 1 − equity_type1_F × (equity_type1 + equity_symmetric_adj)` |
| EQUITY_T2 | `phi_F = 1 − equity_type2_F × (equity_type2 + equity_symmetric_adj)` |
| PROPERTY | `phi_F = 1 − property_F × property` |
| SPREAD | `phi_F = 1 − corp_bonds_F × spread_stress_pct_F` |
| CURRENCY | `phi_F = 1 − fx_exposure_pct_F × currency` |
| MORTALITY | `mq = 1 + mortality_up` |
| LONGEVITY | `mq = 1 − longevity_down` |
| LIFE_CAT | `aq = life_cat_add` |
| LAPSE_UP | `mw = 1 + lapse_up` (applied to full and partial surrender rates) |
| LAPSE_DOWN | `mw = 1 − lapse_down`, `dw = lapse_down_max_abs` (full and partial surrender rates) |
| EXPENSE | `me = 1 + expense_up`, `dj = expense_inflation_add` |
| LAPSE_MASS | not projected; closed form (section 11.2) |

`equity_symmetric_adj` is read from the economic workbook. The shocked scenarios are applied to
in-force business only (valuations); VNB and the real-world projection use the central
parameters.

### 4.1 Interest-rate fund factor

```
bonds_F  = govt_bonds_F + corp_bonds_F
s(D)     = annual spot rate at maturity D, linearly interpolated between annual maturities
           (flat below maturity 1 and above maturity K)

price_F  = ( (1 + s_base(D_F)) / (1 + s_shock(D_F)) ) ^ D_F          D_F = bond_duration
phi_F    = 1 + bonds_F × ( price_F − 1 )
```

### 4.2 Initial fund value in a scenario

```
FV_0^scenario = FV_0 × phi_F
```

---

## 5. Monthly decrement and charge rates

### 5.1 Mortality

`q_table(sex, age)` is the annual table, `mort` the product's `mortality_factor`.

```
Annual best estimate    qA_t  = min( q_table(sex, age_t) × mort , 1 )
Monthly best estimate   qBE_t = 1 − (1 − qA_t)^(1/12)

Annual scenario rate    qS_t  = min( qA_t × mq + aq × 1[t <= 12] , 1 )
Monthly scenario rate   q_t   = 1 − (1 − qS_t)^(1/12)
```

The shock is applied to the annual rate, which is then converted to a monthly rate.

### 5.2 Lapse

`w_table(py)` is the product's column of the `Lapse` sheet (annual rates, `Kw` rows).

```
Annual best estimate   wA_t = w_table( min(py_t, Kw) )
Annual scenario rate   wS_t = min( max( wA_t × mw , wA_t − dw ) , 1 )
Monthly rate           w_t  = 1 − (1 − wS_t)^(1/12)
```

No lapse is applied in the maturity month (`t = n`): all survivors mature.

### 5.2b Partial surrender

`p_table(py)` is the product's column of the `Partial_Surrender` sheet: annual share of the
fund value withdrawn by policies remaining in force (`Kp` rows). The lapse shocks apply to it
in the same way as to the full surrender rate:

```
Annual best estimate   pA_t = p_table( min(py_t, Kp) )
Annual scenario rate   pS_t = min( max( pA_t × mw , pA_t − dw ) , 1 )
Monthly rate           pw_t = 1 − (1 − pS_t)^(1/12)
```

`pw_t = 0` for a new business model point before its issue month.

### 5.3 Surrender charge

```
sc_t = charge_pct(py_t)   if py_t <= number of rows of Surrender_Charges
     = 0                  otherwise
```

---

## 6. Unit fund projection (per policy)

Product parameters (`Products` sheet): `amc` = `amc_pct`, `fee` = `policy_fee`,
`al` = `allocation_charge_pct`, `coi` = `coi_pct_of_qx`.

### 6.1 Entry of new business — start of month

For a new business model point, in its issue month `t = k` only:

```
Prem_t  = P
Alloc_t = P × (1 − al)                         invested in units
```

`Prem_t = Alloc_t = 0` in every other month and for in-force model points.

```
FVbop_t = FVe_(t−1) + Alloc_t                  FVe = fund after partial surrender (6.7); 0 before issue
```

### 6.2 Investment return

```
Inv_t = FVbop_t × g_t
FV1_t = FVbop_t × (1 + g_t)
```

`g_t` is the risk-neutral rate of the scenario curve (3.3) or the real-world rate of the
model point's fund (3.4).

### 6.3 Management charge and policy fee — end of month

```
AMC_t = (amc / 12) × FV1_t
FV2_t = max( FV1_t × (1 − amc/12) − fee/12 , 0 )
Fee_t = FV1_t × (1 − amc/12) − FV2_t           equals fee/12, or the remaining fund if smaller
```

The policy fee is a fixed amount (not inflated).

### 6.4 Death benefit function

| `death_benefit_type` | `DB(F)` |
|---|---|
| FV_PCT | `max(death_benefit_pct, 1) × F` |
| ROP | `max(F, G_(t−1))` |
| FV_PLUS_SA | `F + SA` |

### 6.5 Cost-of-insurance charge

```
SAR_t = DB(FV2_t) − FV2_t
COI_t = min( coi × qBE_t × SAR_t , FV2_t )
```

The charge uses the monthly best-estimate rate `qBE_t` in every scenario (contractual charge,
unaffected by the mortality shocks).

### 6.6 Fund value at decrement and death benefit

```
FV_t = FV2_t − COI_t
DB_t = DB(FV_t)
```

`FV_t` is the fund value on which deaths, full surrenders and maturities are paid.

### 6.7 Partial surrender and end-of-month fund value

Policies still in force after the decrements withdraw a share `pw_t` of their fund:

```
PW_t  = FV_t × pw_t                           amount withdrawn per policy (gross of penalty)
FVe_t = FV_t − PW_t                           fund value carried to month t + 1
G_t   = G_(t−1) × (1 − pw_t)                  ROP guarantee reduced pro rata (ROP products only)
```

The surrender charge `sc_t` applies to the amount withdrawn. The sum assured of FV_PLUS_SA
products and the fixed policy fee are not reduced. No claim handling expense is charged on
partial surrenders.

---

## 7. Policies in force

```
l_0 = N for in-force model points, 0 for new business
New_t = N × 1[t = k]                                 new policies (issue month only)
lb_t  = l_(t−1) + New_t                              policies exposed during month t

D_t = lb_t × q_t                                     deaths
W_t = ( lb_t − D_t ) × w_t        if t < n           surrenders
    = 0                           if t = n
M_t = 0                           if t < n           maturities
    = lb_t − D_t                  if t = n

l_t = lb_t − D_t − W_t − M_t
```

---

## 8. Cash flows (per model point)

Assumptions (`Product_Assumptions`): `E` = `expense_per_policy` (annual, expense type 1), `ei` =
`expense_pct_fund_value` (annual, expense type 2), `C` = `claim_expense`, `Ap` = `acquisition_expense_per_policy`,
`Aq` = `acquisition_expense_pct_premium`; `j` = `expense_inflation` (economic workbook).
Product parameters: `ci` = `commission_initial_pct`, `ct` = `commission_trail_pct`,
`rb` = `fund_rebate_pct`.

`X` is the expense scale of the run: 1 for a projection starting at the valuation date,
`(1 + j)^(h/12)` for a valuation performed `h` months later (section 15.3).

### 8.1 Start of month (time `t − 1`) — new business only

```
Premium_t        = New_t × Prem_t
AllocPremium_t   = New_t × Alloc_t
InitCommission_t = New_t × Prem_t × ci
AcqExpense_t     = New_t × ( Ap × X × (1 + j)^((t−1)/12) + Prem_t × Aq )
```

### 8.2 End of month (time `t`)

```
Expense inflation factor   I_t = X × me × (1 + j + dj) ^ ((t − 1)/12)

Claims
  DeathClaims_t     = D_t × DB_t
  SurrenderClaims_t = W_t × FV_t × (1 − sc_t)
  PartialClaims_t   = l_t × PW_t × (1 − sc_t)
  MaturityClaims_t  = M_t × FV_t

Expenses
  MaintExp_t  = lb_t × (E / 12) × I_t                expenses per policy
  InvExp_t    = lb_t × FV1_t × (ei / 12) × me        expenses in % of fund value
  ClaimExp_t  = ( D_t + W_t + M_t ) × C × I_t        claim handling expenses

Commission and rebates
  TrailComm_t = lb_t × FV1_t × (ct / 12)
  Rebate_t    = lb_t × FV1_t × (rb / 12)             income
```

Unit costs are entered in money of the valuation date. Per-policy and per-claim expenses are
inflated; expenses in % of fund value are not (they follow the fund). Both expense types are
increased by the expense shock multiplier `me`. Fund-based expenses, trail commission and
rebates are based on the fund value after investment return and before charges.

### 8.3 Charges and margins

```
PremLoading_t  = Premium_t − AllocPremium_t
AMCIncome_t    = lb_t × AMC_t
FeeIncome_t    = lb_t × Fee_t
COIIncome_t    = lb_t × COI_t
SurrPenalty_t  = ( W_t × FV_t + l_t × PW_t ) × sc_t
DeathStrain_t  = D_t × ( DB_t − FV_t )
```

### 8.4 Unit reserves

```
Ubop_t = l_(t−1) × FVe_(t−1)                         before the entry of new business
Ueop_t = l_t × FVe_t
UnitInvReturn_t = lb_t × Inv_t
```

---

## 9. Present values and BEL

Start-of-month cash flows (8.1) are discounted with `DF_(t−1)`, all others with `DF_t`:

```
PV(X)      = SUM over t of DF_t × X_t                for end-of-month lines
PV_start(X)= SUM over t of DF_(t−1) × X_t            for Premium, AllocPremium, InitCommission, AcqExpense

BEL = PV(DeathClaims) + PV(SurrenderClaims) + PV(PartialClaims) + PV(MaturityClaims)
    + PV(MaintExp) + PV(InvExp) + PV(ClaimExp) + PV(TrailComm) − PV(Rebate)
    + PV_start(InitCommission) + PV_start(AcqExpense) − PV_start(Premium)
```

The BEL is accumulated per model point and per scenario with the scenario's discount factors.
It is also rebuilt from the present values of the aggregated lines; the difference is reported
as a check.

---

## 10. Revenue account and PVFP

### 10.1 Revenue account (month `t`)

```
Income_t   = Premium_t + UnitInvReturn_t + Rebate_t
Claims_t   = DeathClaims_t + SurrenderClaims_t + PartialClaims_t + MaturityClaims_t
dReserve_t = Ueop_t − Ubop_t
Costs_t    = AcqExpense_t + MaintExp_t + InvExp_t + ClaimExp_t + InitCommission_t + TrailComm_t

Result_t   = Income_t − Claims_t − dReserve_t − Costs_t
Tax_t      = tax_rate × Result_t                     negative results give a credit
```

### 10.2 Result by source

```
Profit_t = PremLoading_t − InitCommission_t − AcqExpense_t
         + AMCIncome_t + FeeIncome_t + COIIncome_t + SurrPenalty_t + Rebate_t
         − MaintExp_t − InvExp_t − ClaimExp_t − TrailComm_t
         − DeathStrain_t
```

`Profit_t = Result_t` in every month; the difference is reported as a check.

### 10.3 PVFP

```
PVFP = PV_start(PremLoading) − PV_start(InitCommission) − PV_start(AcqExpense)
     + PV(AMCIncome) + PV(FeeIncome) + PV(COIIncome) + PV(SurrPenalty) + PV(Rebate)
     − PV(MaintExp) − PV(InvExp) − PV(ClaimExp) − PV(TrailComm) − PV(DeathStrain)

PVFP_net = PVFP × (1 − tax_rate)
```

For in-force business the start-of-month terms are zero and `PVFP = SUM of DF_t × Profit_t`.

### 10.4 Identities (risk-neutral projections)

```
(a)  FV_total_0 = BEL + PVFP                         in every scenario
(b)  FV_total_0 = PV(AMCIncome) + PV(FeeIncome) + PV(COIIncome) + PV(fund released on claims)
     fund released on claims = (D_t + W_t + M_t) × FV_t + l_t × PW_t
                             = DeathClaims − DeathStrain + SurrenderClaims + PartialClaims
                               + SurrPenalty + MaturityClaims
```

with `FV_total_0 = N × FV_0 × phi_F` summed over model points (0 for new business, so that
`PVFP = −BEL`). Both hold because the fund earns `g_t` and `DF_(t−1) = DF_t × (1 + g_t)`; both
are reported as checks.

### 10.5 Calendar-year reporting

Monthly lines are aggregated by calendar year: flows are summed; opening stocks take the first
month of the year and closing stocks the last. The first year is partial when the valuation
date is not a 31 December (labelled with its number of months).

---

## 11. Loss of own funds per shock

### 11.1 Projected scenarios

Per model point:

```
NAV^s  = FV_total_0^s − BEL^s
Loss^s = NAV^BASE − NAV^s
```

```
Flooring on (policy_level_flooring = Y), scenarios MORTALITY, LONGEVITY, LIFE_CAT,
LAPSE_UP, LAPSE_DOWN, LAPSE_MASS:
        Loss_product^s = SUM over model points of max( Loss_mp^s , 0 )
All other cases:
        Loss_product^s = SUM over model points of Loss_mp^s
```

### 11.2 Mass lapse (closed form)

```
SV_0     = N × FV_0 × ( 1 − sc(py_1) )                      surrender value at the valuation date
Loss_mp  = lapse_mass × ( SV_0 + N × C × X − BEL_mp^BASE )
BEL^MASS = BEL^BASE + Loss_mp
```

### 11.3 Total column

```
Loss_total^s = SUM over products of Loss_product^s           before the aggregation of section 12
```

---

## 12. Solvency capital requirement

All losses are floored at zero before use: `Ls(s) = max(Loss^s, 0)`.

```
Interest   = max( Ls(IR_UP) , Ls(IR_DOWN) )
             binding scenario = down if Ls(IR_DOWN) > Ls(IR_UP); otherwise up (none if both are 0)
Equity     = sqrt( E1² + 2 × equity_corr_t1_t2 × E1 × E2 + E2² )     E1 = Ls(EQUITY_T1), E2 = Ls(EQUITY_T2)
Property   = Ls(PROPERTY);  Spread = Ls(SPREAD);  Currency = Ls(CURRENCY)

SCR_market = sqrt( SUM over i,j of CorrMkt(i,j) × Mkt_i × Mkt_j )
```

`CorrMkt` (`Corr_Market` sheet) holds the interest-down case; when the binding interest
scenario is not "down", the correlations between interest and equity, property and spread are
replaced by `corr_interest_a_up`.

```
Mortality = Ls(MORTALITY);  Longevity = Ls(LONGEVITY)
Lapse     = max( Ls(LAPSE_UP) , Ls(LAPSE_DOWN) , Ls(LAPSE_MASS) )
Expense   = Ls(EXPENSE);  Cat = Ls(LIFE_CAT)

SCR_life  = sqrt( SUM over i,j of CorrLife(i,j) × Life_i × Life_j )

BSCR   = sqrt( SCR_market² + 2 × corr_market_life × SCR_market × SCR_life + SCR_life² )
SCR_op = op_risk_expense_factor × SUM over t = 1..12 of ( MaintExp_t + InvExp_t + ClaimExp_t )   central
SCR    = BSCR + SCR_op
```

No adjustment for the loss-absorbing capacity of deferred taxes is made.

---

## 13. Risk margin and summary metrics

```
SCR_RU(0) = SCR_life + SCR_op

RM = cost_of_capital_rate × SUM over y = 0..Y−1 of [ SCR_RU(0) × r_y × DF_(12 (y+1)) ]     Y = H / 12
```

`r_y` is the run-off pattern at time `12 y` months (`r_0 = 1`), on the central projection,
selected by `risk_margin_driver`:

```
PVFP         V_y = ( SUM over u > 12 y of DF_u × Profit_u ) / DF_(12 y)
             r_y = max( V_y , 0 ) / V_0          (FUND_VALUE is used instead if V_0 <= 0)
FUND_VALUE   r_y = U_(12 y) / U_0                U_0 = initial fund value, U_t = Ueop_t
POLICIES     r_y = l_(12 y) / l_0
```

```
Technical provisions   TP = BEL + RM
Fund value − TP           = FV_total_0 − TP
```

---

## 13b. Output layout

In every output, the total of all products comes first: column B when products are in columns,
the first rows or the first block when they are in rows, and the first sheet when there is one
sheet per product. Products follow in the column order of `products.xlsx`. The position of the
total therefore does not depend on the number of products.

---

## 14. Run type VALUATION

In-force model points at the valuation date, horizon cap `H` (2.3), risk-neutral basis built
from the input curve, all scenarios of section 4, sections 5 to 13. Outputs: results summary,
valuation variables (present value of every line, PVFP components, checks, SCR components),
revenue account (central scenario, by calendar year and monthly).

---

## 15. Run type VNB

New business model points with issue month `k <= vnb_months`, central scenario, risk-neutral
basis of the valuation date, horizon cap `H` counted from the valuation date. Everything is
discounted to the valuation date (issue cash flows with `DF_(k−1)`).

Present values are computed per model point (section 9) and summed by product and issue month:

```
VNB = PV_start(PremLoading)
    + PV(AMCIncome) + PV(FeeIncome) + PV(COIIncome) + PV(SurrPenalty) + PV(Rebate)
    − PV(DeathStrain) − PV(MaintExp) − PV(InvExp) − PV(ClaimExp) − PV(TrailComm)
    − PV_start(InitCommission) − PV_start(AcqExpense)

VNB_net    = VNB × (1 − tax_rate)
VNB margin = VNB / PV_start(Premium)
Check      : VNB + BEL = 0                     (identity 10.4 with zero initial fund value)
```

VNB is before tax and before risk margin or any cost of capital.

---

## 16. Run type BUSINESS_PLAN

### 16.1 Horizon

```
Hbp = 12 × bp_years + (12 − V.month) mod 12        months; the plan ends on a 31 December
Valuation dates: h = 0 and every h <= Hbp such that time h is a 31 December
```

Example: `V` = 31/12/2025, 5 years → 60 months, valuations at 0, 12, …, 60.
`V` = 31/03/2026, 5 years → 69 months to 31/12/2031, valuations at 0, 9, 21, …, 69.

### 16.2 Real-world projection

In-force model points and new business model points with `k <= Hbp` are projected together
for `Hbp` months with:

- central decrements and expenses (section 5, neutral parameters, `X = 1`);
- real-world fund growth `g_t^F` (3.4);
- no horizon cap: nothing is released at the end of the plan.

The monthly lines of sections 7, 8 and 10 give the plan P&L (no discounting is involved).

### 16.3 Valuation at each date `h`

The projected state at time `h` defines an in-force portfolio:

```
For every model point with k <= h, l_h > 0 and n > h:
    N' = l_h           FV_0' = FVe_h         SA' = SA        G_0' = G_h
    a0' = a0 + h       d0' = d0 + h          n' = n − h      k' = 0
```

This portfolio is valued exactly as in section 14, with:

- the forward curve at `h` (3.5), from which the shocked curves and the interest-rate fund
  factors are recomputed;
- expense scale `X = (1 + j)^(h/12)`;
- all other assumptions and shock parameters unchanged (including the symmetric adjustment).

All shocks are re-run in full at each date. The first valuation (`h = 0`) is identical to the
VALUATION run.

### 16.4 Value of the plan's new business

The VNB of section 15 is computed for all issue months `k <= Hbp`, on the curve of the
valuation date, and reported by product and calendar year of issue.

---

## 17. Mapping to the source code

| Section | File | Function |
|---|---|---|
| 2 | `ulmodel/inputs.py` | `load_in_force`, `load_new_business`, `ModelPoints.capped` |
| 3 | `ulmodel/scenarios.py` | `monthly_df`, `build_curves`, `real_world_basis`, `forward_spot` |
| 4 | `ulmodel/scenarios.py` | `build_scenarios` |
| 5 – 8, 10.2 | `ulmodel/engine.py` | `_project_group` (lines `exp_maintenance` = expenses per policy, `exp_investment` = expenses in % of fund value) |
| 9, 10.3 | `ulmodel/engine.py` | `pv_lines`, `BEL_SIGNS`, `PROFIT_SIGNS` |
| 10.1, 10.5 | `ulmodel/outputs.py` | `ra_result`, `_account_sheet`, `calendar_years` |
| 11 | `ulmodel/scr.py` | `scenario_table`, `add_tables` |
| 12, 13 | `ulmodel/scr.py`, `ulmodel/valuation.py` | `aggregate`, `opex_12m`, `runoff_pattern`, `_finalise` |
| 14 | `ulmodel/valuation.py` | `value` |
| 15 | `ulmodel/vnb.py` | `compute` |
| 16 | `ulmodel/business_plan.py` | `run`, `state_at` |

**Implementation notes that do not change the results**

- Surrenders and maturities are computed as a single "exit" decrement (monthly lapse rate, or 1
  in the maturity month with a zero surrender charge) and split afterwards for reporting.
- Market-shock scenarios reuse the central policies in force. Life-shock scenarios reuse the
  central fund path, except that the lapse shocks change the partial surrenders and therefore
  the fund path of those scenarios.
- The detailed lines are computed for the central scenario only, unless `scenario_detail = Y`.
- `tests/test_model.py` contains an independent single-policy implementation of sections 2 to 9
  and compares it with the engine; it also verifies the identities of 10.4 and that
  `BEL(0) = PV(cash flows of the first 12 months) + DF_12 × BEL(12)` when the BEL at month 12
  is obtained through the machinery of section 16.3 with a zero real-world premium.
