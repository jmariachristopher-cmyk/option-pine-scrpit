"""
Run this once per day (after you have a fresh Upstox access token -- see
get_token.py) to generate .pine files for every symbol you list.

Example:
    python run_daily.py \
        --access-token "$UPSTOX_ACCESS_TOKEN" \
        --expiry 2026-09-09 \
        --symbols NIFTY BANKNIFTY RELIANCE HDFCBANK TCS \
        --n-each-side 10

Each symbol produces its own data/<SYMBOL>.pine and data/<SYMBOL>.csv --
open the .pine file for the chart you're on and paste it into TradingView's
Pine Editor.
"""

import argparse
import os

from upstox_generator import run_pipeline


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--access-token", default=os.environ.get("UPSTOX_ACCESS_TOKEN"),
                    help="Or set the UPSTOX_ACCESS_TOKEN environment variable")
    p.add_argument("--expiry", required=True, help="YYYY-MM-DD, exact Upstox format")
    p.add_argument("--n-each-side", type=int, default=10, help="Strikes above/below ATM")
    p.add_argument("--symbols", nargs="+", default=["NIFTY", "BANKNIFTY"],
                    help="e.g. NIFTY BANKNIFTY RELIANCE HDFCBANK TCS ...")
    p.add_argument("--outdir", default="data")
    args = p.parse_args()

    if not args.access_token:
        raise SystemExit(
            "No access token. Run get_token.py first, then pass --access-token "
            "or set UPSTOX_ACCESS_TOKEN."
        )

    os.makedirs(args.outdir, exist_ok=True)

    for symbol in args.symbols:
        try:
            result = run_pipeline(args.access_token, symbol, args.expiry, args.n_each_side)
        except Exception as e:
            print(f"[{symbol}] FAILED: {e}")
            continue

        pine_path = os.path.join(args.outdir, f"{symbol}.pine")
        csv_path = os.path.join(args.outdir, f"{symbol}.csv")
        with open(pine_path, "w") as f:
            f.write(result["pine_text"])
        with open(csv_path, "w") as f:
            f.write(result["csv_text"])

        print(
            f"[{symbol}] OK -- spot {result['spot']}, ATM {result['atm']}, "
            f"strike spacing {result['step']} -> {pine_path}"
        )


if __name__ == "__main__":
    main()
