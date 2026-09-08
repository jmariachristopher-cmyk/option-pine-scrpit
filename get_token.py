"""
Run this each morning to get a fresh Upstox access token (Upstox tokens
expire daily, roughly 3:30 AM IST -- there is no official headless/TOTP
login for Upstox the way Angel One's SmartAPI had, so this step needs a
human to click through the login once per day).

Usage:
    python get_token.py --client-id XXX --client-secret YYY --redirect-uri https://your-redirect

It will:
  1. Print a login URL -- open it in your browser and log in to Upstox.
  2. Upstox redirects you to your redirect_uri with ?code=... in the URL --
     copy that code value.
  3. Paste the code back into this script's prompt.
  4. It prints the access token to use for today's run_daily.py call.
"""

import argparse

from upstox_generator import get_login_url, get_access_token


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    p.add_argument("--redirect-uri", required=True,
                    help="Must exactly match the redirect URI registered in your Upstox app")
    args = p.parse_args()

    print("\n1. Open this URL in your browser and log in:\n")
    print(get_login_url(args.client_id, args.redirect_uri))
    print("\n2. After login, Upstox redirects you to your redirect_uri with ?code=...")
    print("   Copy just the code value from that URL.\n")

    auth_code = input("Paste the code here: ").strip()

    token = get_access_token(args.client_id, args.client_secret, args.redirect_uri, auth_code)
    print("\nAccess token for today:\n")
    print(token)
    print("\nExport it for run_daily.py, e.g.:\n")
    print(f'  export UPSTOX_ACCESS_TOKEN="{token}"\n')


if __name__ == "__main__":
    main()
