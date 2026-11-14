"""Reading and validation of the Excel inputs.

    run_config.xlsx            run type, valuation date, horizons, paths
    economic_<date>.xlsx       yield curve, inflation, symmetric adjustment, real-world premia
    assumptions.xlsx           mortality, lapse, expenses, funds, S2 shocks, correlations
    products.xlsx              product features and surrender charge scales
    in_force/<PRODUCT>.xlsx    in-force model points at the valuation date
    new_business/<PRODUCT>.xlsx  future new business model points (by issue month)
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

MARKET_RISKS = ["interest", "equity", "property", "spread", "currency"]
LIFE_RISKS = ["mortality", "longevity", "lapse", "expense", "cat"]
DB_TYPES = ("FV_PCT", "ROP", "FV_PLUS_SA")
RUN_TYPES = ("VALUATION", "VNB", "BUSINESS_PLAN", "ALL")
FUND_COLS = ["equity_type1", "equity_type2", "property", "govt_bonds", "corp_bonds", "cash"]
IF_COLS = ["mp_id", "policy_count", "date_of_birth", "sex", "issue_date", "maturity_date",
           "fund_code", "fund_value", "sum_assured", "premiums_paid"]
NB_COLS = ["mp_id", "issue_month", "policy_count", "age_at_issue", "sex", "term_years",
           "fund_code", "single_premium", "sum_assured"]
WHOLE_LIFE = 10 ** 6       # remaining term (months) used for whole-of-life policies


class InputError(Exception):
    """Raised when an input workbook is missing data or contains invalid data."""


# --------------------------------------------------------------------------- helpers
def _read(path: Path, sheet: str, **kw) -> pd.DataFrame:
    if not Path(path).exists():
        raise InputError(f"File not found: {path}")
    try:
        df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl", **kw)
    except ValueError as exc:
        raise InputError(f"{Path(path).name}: sheet '{sheet}' not found") from exc
    df.columns = [str(c).strip() for c in df.columns]
    return df.dropna(how="all")


def _params(path: Path, sheet: str) -> dict:
    df = _read(path, sheet)
    if df.shape[1] < 2:
        raise InputError(f"{path.name}/{sheet}: expected columns Parameter | Value")
    return {k.strip().lower(): v for k, v in zip(df.iloc[:, 0], df.iloc[:, 1])
            if isinstance(k, str) and k.strip()}


def _get(params: dict, key: str, where: str, default=None):
    if key in params and not pd.isna(params[key]):
        return params[key]
    if default is not None:
        return default
    raise InputError(f"{where}: parameter '{key}' is missing")


def _num(params: dict, key: str, where: str, default=None) -> float:
    val = _get(params, key, where, default)
    try:
        return float(val)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{where}: parameter '{key}' must be numeric (got {val!r})") from exc


def _flag(params: dict, key: str, where: str, default=None) -> bool:
    val = str(_get(params, key, where, default)).strip().upper()
    if val in ("Y", "YES", "TRUE", "1", "1.0"):
        return True
    if val in ("N", "NO", "FALSE", "0", "0.0"):
        return False
    raise InputError(f"{where}: parameter '{key}' must be Y or N (got {val!r})")


def _need_cols(df: pd.DataFrame, cols, where: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise InputError(f"{where}: missing column(s) {missing}")


def _corr(path: Path, sheet: str, labels: list) -> np.ndarray:
    df = _read(path, sheet, index_col=0)
    df.index = [str(i).strip().lower() for i in df.index]
    df.columns = [c.lower() for c in df.columns]
    try:
        mat = df.loc[labels, labels].to_numpy(dtype=float)
    except KeyError as exc:
        raise InputError(f"{path.name}/{sheet}: rows/columns must be {labels}") from exc
    if not np.allclose(mat, mat.T) or not np.allclose(np.diag(mat), 1.0):
        raise InputError(f"{path.name}/{sheet}: matrix must be symmetric with 1 on the diagonal")
    return mat


def _dates(col: pd.Series, where: str, name: str, allow_blank=False) -> pd.Series:
    out = pd.to_datetime(col, errors="coerce")
    if not allow_blank and out.isna().any():
        raise InputError(f"{where}: column '{name}' contains blanks or invalid dates")
    return out


# --------------------------------------------------------------------------- containers
@dataclass
class Config:
    run_type: str
    valuation_date: pd.Timestamp
    proj_years: int
    vnb_months: int
    bp_years: int
    economic_file: Path
    assumptions_file: Path
    products_file: Path
    in_force_dir: Path
    new_business_dir: Path
    output_dir: Path
    scenario_detail: bool
    mp_detail: bool
    chunk_size: int

    @property
    def H(self) -> int:
        """Projection horizon in months."""
        return self.proj_years * 12

    @property
    def bp_months(self) -> int:
        """Business plan horizon in months: bp_years, extended to the next 31 December."""
        return self.bp_years * 12 + (12 - self.valuation_date.month) % 12

    @property
    def bp_offsets(self) -> list:
        """Months from the valuation date to each 31 December of the plan (0 = start)."""
        first = (12 - self.valuation_date.month) % 12 or 12
        return [0, *range(first, self.bp_months + 1, 12)]

    def date(self, months: int) -> pd.Timestamp:
        return self.valuation_date + pd.offsets.MonthEnd(months)


@dataclass
class Assumptions:
    tax_rate: float
    coc_rate: float
    policy_level_flooring: bool
    rm_driver: str
    expense_inflation: float
    spot: np.ndarray            # (K,) base annual spot rates, maturities 1..K years
    ir_up_rel: np.ndarray       # (K,)
    ir_down_rel: np.ndarray     # (K,)
    qx: np.ndarray              # (2, n_ages) annual rates, rows M / F, index = age
    lapse: pd.DataFrame         # annual rates, index policy year (1..), one column per product
    partial: pd.DataFrame       # annual partial surrender rates (% of fund value), same layout
    product_assumptions: pd.DataFrame
    fund_codes: list
    fund_alloc: np.ndarray      # (F, 6) columns as FUND_COLS
    fund_duration: np.ndarray   # (F,)
    fund_spread: np.ndarray     # (F,)
    fund_fx: np.ndarray         # (F,)
    fund_rw_premium: np.ndarray  # (F,) annual real-world risk premium over risk-free
    shocks: dict
    corr_market: np.ndarray
    corr_life: np.ndarray


@dataclass
class Product:
    code: str
    name: str
    amc: float
    policy_fee: float
    alloc_charge: float
    db_type: str
    db_pct: float
    coi_pct: float
    rebate: float
    comm_initial: float
    comm_trail: float
    surrender_charges: np.ndarray       # by policy year (index 0 = policy year 1)
    mort_factor: float = 1.0
    exp_per_policy: float = 0.0
    exp_investment: float = 0.0
    exp_claim: float = 0.0
    acq_per_policy: float = 0.0
    acq_pct_premium: float = 0.0
    lapse: np.ndarray = field(default_factory=lambda: np.zeros(1))
    partial: np.ndarray = field(default_factory=lambda: np.zeros(1))   # partial surrender rates


@dataclass
class ModelPoints:
    """Per-policy amounts; counts are the number of policies of the model point.

    age_m / dur_m are attained age and elapsed policy duration in complete months at the
    projection start (negative for new business not yet issued). `entry` is 0 for policies
    in force at the start and k >= 1 for new business issued at the start of month k.
    """
    ids: np.ndarray
    count: np.ndarray
    age_m: np.ndarray
    sex: np.ndarray          # 0 = M, 1 = F
    dur_m: np.ndarray
    rem: np.ndarray          # months from projection start to maturity (WHOLE_LIFE if none)
    entry: np.ndarray
    fund_idx: np.ndarray
    fv: np.ndarray
    prem: np.ndarray         # single premium paid at entry (new business)
    sa: np.ndarray
    prem_paid: np.ndarray    # premiums paid (return-of-premium guarantee)

    @property
    def n(self) -> int:
        return self.count.size

    def take(self, idx) -> "ModelPoints":
        return ModelPoints(**{k: v[idx] for k, v in self.__dict__.items()})

    def slice(self, lo: int, hi: int) -> "ModelPoints":
        return self.take(slice(lo, hi))

    def capped(self, horizon: int) -> "ModelPoints":
        """Same model points with maturity no later than the horizon (run-off at horizon)."""
        out = self.take(slice(None))
        out.rem = np.minimum(self.rem, horizon)
        return out

    @staticmethod
    def concat(parts: list) -> "ModelPoints":
        keys = parts[0].__dict__.keys()
        return ModelPoints(**{k: np.concatenate([getattr(p, k) for p in parts]) for k in keys})


# --------------------------------------------------------------------------- config
def load_config(path: Path) -> Config:
    path = Path(path)
    p = _params(path, "Run")
    w = f"{path.name}/Run"
    base = path.resolve().parent

    def loc(key):
        return (base / str(_get(p, key, w))).resolve()

    run_type = str(_get(p, "run_type", w)).strip().upper()
    if run_type not in RUN_TYPES:
        raise InputError(f"{w}: run_type must be one of {RUN_TYPES}")
    val = pd.to_datetime(_get(p, "valuation_date", w), errors="coerce")
    if pd.isna(val):
        raise InputError(f"{w}: valuation_date is not a valid date")
    val = pd.Timestamp(val).normalize()
    if val.day != calendar.monthrange(val.year, val.month)[1]:
        raise InputError(f"{w}: valuation_date must be a month end (got {val.date()})")
    years = int(_num(p, "projection_years", w))
    if not 1 <= years <= 40:
        raise InputError(f"{w}: projection_years must be between 1 and 40")
    return Config(
        run_type=run_type, valuation_date=val, proj_years=years,
        vnb_months=int(_num(p, "vnb_months", w, 12)),
        bp_years=int(_num(p, "bp_years", w, 5)),
        economic_file=loc("economic_file"), assumptions_file=loc("assumptions_file"),
        products_file=loc("products_file"), in_force_dir=loc("in_force_folder"),
        new_business_dir=loc("new_business_folder"), output_dir=loc("output_folder"),
        scenario_detail=_flag(p, "scenario_detail", w, "N"),
        mp_detail=_flag(p, "mp_detail", w, "N"),
        chunk_size=max(1, int(_num(p, "chunk_size", w, 5000))),
    )


# --------------------------------------------------------------------------- assumptions
def load_assumptions(path: Path, economic_path: Path) -> Assumptions:
    path, eco = Path(path), Path(economic_path)
    w, we = path.name, eco.name

    gen = _params(path, "General")
    rm_driver = str(_get(gen, "risk_margin_driver", f"{w}/General", "PVFP")).strip().upper()
    if rm_driver not in ("PVFP", "FUND_VALUE", "POLICIES"):
        raise InputError(f"{w}/General: risk_margin_driver must be PVFP, FUND_VALUE or POLICIES")

    # ---- economic workbook
    ep = _params(eco, "Parameters")
    yc = _read(eco, "Yield_Curve")
    _need_cols(yc, ["maturity", "spot_rate", "shock_up_rel", "shock_down_rel"], f"{we}/Yield_Curve")
    yc = yc.sort_values("maturity")
    if not np.array_equal(yc["maturity"].to_numpy(), np.arange(1, len(yc) + 1)):
        raise InputError(f"{we}/Yield_Curve: maturities must run 1,2,3,... (years) without gaps")
    rw = _read(eco, "Real_World")
    _need_cols(rw, ["asset_class", "risk_premium"], f"{we}/Real_World")
    rw = dict(zip(rw["asset_class"].astype(str).str.strip().str.lower(), rw["risk_premium"]))
    miss = [c for c in FUND_COLS if c not in rw]
    if miss:
        raise InputError(f"{we}/Real_World: missing asset class(es) {miss}")

    mort = _read(path, "Mortality")
    _need_cols(mort, ["age", "qx_male", "qx_female"], f"{w}/Mortality")
    mort = mort.sort_values("age")
    if not np.array_equal(mort["age"].to_numpy(), np.arange(0, len(mort))):
        raise InputError(f"{w}/Mortality: ages must run 0,1,2,... without gaps")
    qx = np.vstack([mort["qx_male"].to_numpy(float), mort["qx_female"].to_numpy(float)])
    if np.isnan(qx).any() or (qx < 0).any() or (qx > 1).any():
        raise InputError(f"{w}/Mortality: qx must be between 0 and 1")

    tables = {}
    for sheet in ("Lapse", "Partial_Surrender"):
        tab = _read(path, sheet)
        _need_cols(tab, ["policy_year"], f"{w}/{sheet}")
        tab = tab.sort_values("policy_year").set_index("policy_year")
        if not np.array_equal(tab.index.to_numpy(), np.arange(1, len(tab) + 1)):
            raise InputError(f"{w}/{sheet}: policy_year must run 1,2,3,... without gaps")
        tables[sheet] = tab
    lapse, partial = tables["Lapse"], tables["Partial_Surrender"]

    pa = _read(path, "Product_Assumptions")
    _need_cols(pa, ["product_code", "mortality_factor", "expense_per_policy",
                    "expense_pct_fund_value", "claim_expense", "acquisition_expense_per_policy",
                    "acquisition_expense_pct_premium"], f"{w}/Product_Assumptions")
    pa["product_code"] = pa["product_code"].astype(str).str.strip()
    pa = pa.set_index("product_code")

    funds = _read(path, "Funds")
    _need_cols(funds, ["fund_code", *FUND_COLS, "bond_duration", "spread_stress_pct",
                       "fx_exposure_pct"], f"{w}/Funds")
    funds["fund_code"] = funds["fund_code"].astype(str).str.strip()
    alloc = funds[FUND_COLS].fillna(0.0).to_numpy(float)
    bad = funds["fund_code"][np.abs(alloc.sum(axis=1) - 1.0) > 1e-6].tolist()
    if bad:
        raise InputError(f"{w}/Funds: asset allocation must sum to 100% for fund(s) {bad}")

    shocks = {k: float(v) for k, v in _params(path, "S2_Shocks").items()}
    shocks["equity_symmetric_adj"] = _num(ep, "equity_symmetric_adj", f"{we}/Parameters")
    required = ["mortality_up", "longevity_down", "lapse_up", "lapse_down",
                "lapse_down_max_abs", "lapse_mass", "expense_up", "expense_inflation_add",
                "life_cat_add", "equity_type1", "equity_type2", "equity_corr_t1_t2",
                "property", "currency", "ir_up_min_abs", "corr_market_life",
                "corr_interest_a_up", "op_risk_expense_factor"]
    miss = [k for k in required if k not in shocks]
    if miss:
        raise InputError(f"{w}/S2_Shocks: missing parameter(s) {miss}")

    return Assumptions(
        tax_rate=_num(gen, "tax_rate", f"{w}/General"),
        coc_rate=_num(gen, "cost_of_capital_rate", f"{w}/General"),
        policy_level_flooring=_flag(gen, "policy_level_flooring", f"{w}/General", "Y"),
        rm_driver=rm_driver,
        expense_inflation=_num(ep, "expense_inflation", f"{we}/Parameters"),
        spot=yc["spot_rate"].to_numpy(float),
        ir_up_rel=yc["shock_up_rel"].to_numpy(float),
        ir_down_rel=yc["shock_down_rel"].to_numpy(float),
        qx=qx, lapse=lapse, partial=partial, product_assumptions=pa,
        fund_codes=funds["fund_code"].tolist(), fund_alloc=alloc,
        fund_duration=funds["bond_duration"].fillna(0.0).to_numpy(float),
        fund_spread=funds["spread_stress_pct"].fillna(0.0).to_numpy(float),
        fund_fx=funds["fx_exposure_pct"].fillna(0.0).to_numpy(float),
        fund_rw_premium=alloc @ np.array([float(rw[c]) for c in FUND_COLS]),
        shocks=shocks,
        corr_market=_corr(path, "Corr_Market", MARKET_RISKS),
        corr_life=_corr(path, "Corr_Life", LIFE_RISKS),
    )


# --------------------------------------------------------------------------- products
def load_products(path: Path, a: Assumptions) -> dict:
    """Product features: parameters in rows, one column per product."""
    path = Path(path)
    w = path.name
    df = _read(path, "Products")
    _need_cols(df, ["Parameter"], f"{w}/Products")
    df["Parameter"] = df["Parameter"].astype(str).str.strip().str.lower()
    df = df.set_index("Parameter")
    sc = _read(path, "Surrender_Charges")
    _need_cols(sc, ["policy_year"], f"{w}/Surrender_Charges")
    sc = sc.sort_values("policy_year")
    if len(sc) and not np.array_equal(sc["policy_year"].to_numpy(), np.arange(1, len(sc) + 1)):
        raise InputError(f"{w}/Surrender_Charges: policy_year must run 1,2,3,... without gaps")

    products = {}
    for col in [c for c in df.columns if c.lower() != "description"]:
        p = {k: v for k, v in df[col].items()}
        where = f"{w}/Products[{col}]"
        code = str(_get(p, "product_code", where)).strip()
        db_type = str(_get(p, "death_benefit_type", where)).strip().upper()
        if db_type not in DB_TYPES:
            raise InputError(f"{where}: death_benefit_type must be one of {DB_TYPES}")
        if code not in a.product_assumptions.index:
            raise InputError(f"Product '{code}' has no row in assumptions sheet Product_Assumptions")
        for sheet, tab in (("Lapse", a.lapse), ("Partial_Surrender", a.partial)):
            if code not in tab.columns:
                raise InputError(f"Product '{code}' has no column in assumptions sheet {sheet}")
            v = tab[code].to_numpy(float)
            if np.isnan(v).any() or (v < 0).any() or (v > 1).any():
                raise InputError(f"{sheet} rates for '{code}' must be between 0 and 1 (no blanks)")
        if code not in sc.columns:
            raise InputError(f"Product '{code}' has no column in {w}/Surrender_Charges")
        pa = a.product_assumptions.loc[code]
        products[code] = Product(
            code=code, name=str(_get(p, "product_name", where, code)),
            amc=_num(p, "amc_pct", where), policy_fee=_num(p, "policy_fee", where, 0.0),
            alloc_charge=_num(p, "allocation_charge_pct", where, 0.0),
            db_type=db_type, db_pct=_num(p, "death_benefit_pct", where, 1.0),
            coi_pct=_num(p, "coi_pct_of_qx", where, 0.0),
            rebate=_num(p, "fund_rebate_pct", where, 0.0),
            comm_initial=_num(p, "commission_initial_pct", where, 0.0),
            comm_trail=_num(p, "commission_trail_pct", where, 0.0),
            surrender_charges=sc[code].fillna(0.0).to_numpy(float),
            mort_factor=float(pa["mortality_factor"]),
            exp_per_policy=float(pa["expense_per_policy"]),
            exp_investment=float(pa["expense_pct_fund_value"]),
            exp_claim=float(pa["claim_expense"]),
            acq_per_policy=float(pa["acquisition_expense_per_policy"]),
            acq_pct_premium=float(pa["acquisition_expense_pct_premium"]),
            lapse=a.lapse[code].to_numpy(float), partial=a.partial[code].to_numpy(float),
        )
    if not products:
        raise InputError(f"{w}/Products: no product column found")
    return products


# --------------------------------------------------------------------------- model points
def _common(mp: pd.DataFrame, a: Assumptions, where: str):
    sex = mp["sex"].astype(str).str.strip().str.upper()
    if not sex.isin(["M", "F"]).all():
        raise InputError(f"{where}: sex must be M or F")
    fund_map = {c: i for i, c in enumerate(a.fund_codes)}
    fcode = mp["fund_code"].astype(str).str.strip()
    unknown = sorted(set(fcode) - set(fund_map))
    if unknown:
        raise InputError(f"{where}: fund_code(s) {unknown} not defined in the Funds sheet")
    return (sex == "F").to_numpy(np.int64), fcode.map(fund_map).to_numpy(np.int64)


def _files(folder: Path, products: dict) -> dict:
    folder = Path(folder)
    out = {}
    if not folder.exists():
        return out
    for f in sorted(folder.glob("*.xlsx")):
        if f.name.startswith("~$"):
            continue
        if f.stem not in products:
            raise InputError(f"{f}: file name must be a product code defined in the products "
                             f"workbook ({sorted(products)})")
        out[f.stem] = f
    return out


def load_in_force(folder: Path, products: dict, a: Assumptions, val: pd.Timestamp) -> dict:
    out = {}
    for code, f in _files(folder, products).items():
        where = f"in_force/{f.name}"
        mp = _read(f, "ModelPoints")
        _need_cols(mp, IF_COLS, where)
        mp = mp[mp["policy_count"].fillna(0) > 0].reset_index(drop=True)
        if mp.empty:
            continue
        if mp[["fund_value"]].isna().any().any() or (mp["fund_value"] < 0).any():
            raise InputError(f"{where}: fund_value must be filled and not negative")
        dob = _dates(mp["date_of_birth"], where, "date_of_birth")
        iss = _dates(mp["issue_date"], where, "issue_date")
        mat = _dates(mp["maturity_date"], where, "maturity_date", allow_blank=True)
        if (dob > val).any() or (iss > val).any():
            raise InputError(f"{where}: date_of_birth / issue_date after the valuation date")

        def complete_months(d):
            return ((val.year - d.dt.year) * 12 + val.month - d.dt.month
                    - (d.dt.day > val.day)).to_numpy(np.int64)

        rem = ((mat.dt.year - val.year) * 12 + mat.dt.month - val.month)
        rem = np.where(mat.isna(), WHOLE_LIFE, np.maximum(rem.fillna(0), 1)).astype(np.int64)
        sex, fund = _common(mp, a, where)
        count = mp["policy_count"].to_numpy(float)
        out[code] = ModelPoints(
            ids=mp["mp_id"].astype(str).to_numpy(), count=count,
            age_m=complete_months(dob), sex=sex, dur_m=complete_months(iss), rem=rem,
            entry=np.zeros(len(mp), dtype=np.int64), fund_idx=fund,
            fv=mp["fund_value"].to_numpy(float) / count, prem=np.zeros(len(mp)),
            sa=mp["sum_assured"].fillna(0.0).to_numpy(float) / count,
            prem_paid=mp["premiums_paid"].fillna(0.0).to_numpy(float) / count,
        )
    return out


def load_new_business(folder: Path, products: dict, a: Assumptions, val: pd.Timestamp) -> dict:
    """New business issued at the start of month k (k = 1 is the month after the valuation
    date). Rows with an issue month on or before the valuation date are ignored."""
    out = {}
    for code, f in _files(folder, products).items():
        where = f"new_business/{f.name}"
        mp = _read(f, "ModelPoints")
        _need_cols(mp, NB_COLS, where)
        mp = mp[mp["policy_count"].fillna(0) > 0].reset_index(drop=True)
        iss = _dates(mp["issue_month"], where, "issue_month")
        k = ((iss.dt.year - val.year) * 12 + iss.dt.month - val.month).to_numpy(np.int64)
        mp, k = mp[k >= 1].reset_index(drop=True), k[k >= 1]
        if mp.empty:
            continue
        if mp[["age_at_issue", "single_premium"]].isna().any().any():
            raise InputError(f"{where}: blanks in age_at_issue or single_premium")
        sex, fund = _common(mp, a, where)
        count = mp["policy_count"].to_numpy(float)
        term = mp["term_years"].fillna(0).to_numpy(float)
        prem = mp["single_premium"].to_numpy(float) / count
        out[code] = ModelPoints(
            ids=mp["mp_id"].astype(str).to_numpy(), count=count,
            age_m=(mp["age_at_issue"].to_numpy(float) * 12).astype(np.int64) - (k - 1),
            sex=sex, dur_m=-(k - 1),
            rem=np.where(term <= 0, WHOLE_LIFE, (k - 1) + np.rint(term * 12)).astype(np.int64),
            entry=k, fund_idx=fund, fv=np.zeros(len(mp)), prem=prem,
            sa=mp["sum_assured"].fillna(0.0).to_numpy(float) / count, prem_paid=prem.copy(),
        )
    return out
