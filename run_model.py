"""ULModel command line.

    python run_model.py                           # uses inputs/run_config.xlsx
    python run_model.py --run-type VNB            # override the run type of the config
    python run_model.py --config path/to/run_config.xlsx

Run types: VALUATION, VNB, BUSINESS_PLAN, ALL
"""

import argparse
import sys
from pathlib import Path

from ulmodel.inputs import RUN_TYPES, InputError
from ulmodel.runner import run

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Unit-linked Solvency II projection model")
    ap.add_argument("--config", default=str(ROOT / "inputs" / "run_config.xlsx"),
                    help="run configuration workbook")
    ap.add_argument("--run-type", choices=[*RUN_TYPES, *[r.lower() for r in RUN_TYPES]],
                    help="override run_type of the configuration workbook")
    args = ap.parse_args()
    try:
        run(args.config, args.run_type)
    except InputError as exc:
        print(f"\nINPUT ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
