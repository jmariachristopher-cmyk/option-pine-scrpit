# Options Reversal Zones — Upstox + Streamlit

Generates ready-to-paste TradingView Pine Scripts for Nifty, BankNifty, or
any FNO stock, using live option-chain data from Upstox's API. Runs as a
Streamlit app — open it once each morning, log in, click Generate.

## Repo layout
```
streamlit_app.py          <- entry point, run this
upstox_generator.py       <- all Upstox API + zone-math logic
run_daily.py               <- optional CLI alternative (no UI)
get_token.py                <- optional CLI login helper
requirements.txt
.streamlit/secrets.toml.example   <- copy to secrets.toml, fill in, DON'T commit
```

## 1. Push to GitHub
```
git init
git add .
git commit -m "Options reversal zones - Upstox + Streamlit"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```
`.gitignore` already excludes `secrets.toml` and generated `data/` — your
Upstox credentials never get committed as long as you keep the `.example`
suffix on the template and create the real file only locally / in
Streamlit Cloud's secrets manager.

## 2. Create your Upstox app (one-time)
Go to https://developer.upstox.com/ → create an app → note the
**Client ID**, **Client Secret**, and set a **Redirect URI** (any URL
works, even `https://localhost` — you just need to see the address bar
after redirect to copy the `code` parameter).

## 3. Deploy on Streamlit Community Cloud
1. Go to https://share.streamlit.io → **New app**.
2. Point it at your GitHub repo, branch `main`, entry file `streamlit_app.py`.
3. Before or after first deploy: **App settings → Secrets**, paste:
   ```
   UPSTOX_CLIENT_ID = "your_client_id"
   UPSTOX_CLIENT_SECRET = "your_client_secret"
   UPSTOX_REDIRECT_URI = "https://localhost"
   ```
4. Deploy. The app pre-fills these three fields from secrets so you don't
   retype them each day — you'll still need to click the login link and
   paste the redirect code, since that's Upstox's requirement, not this
   app's.

## Daily use
1. Open the deployed app (bookmark it).
2. Step 1: click the login link, log into Upstox, paste the `code` back
   in, click "Exchange code for access token."
3. Step 2: pick expiry date, strike count, and symbols (comma-separated —
   `NIFTY, BANKNIFTY, RELIANCE, HDFCBANK, ...`), click **Fetch & Generate**.
4. Each symbol gets its own tab with the computed table and the Pine
   Script — copy or download it, then paste into TradingView's Pine
   Editor on that symbol's chart.

## Why the login step can't be removed
Upstox's access tokens expire daily (~3:30 AM IST) and Upstox has no
official headless/TOTP login the way Angel One's SmartAPI did — the
authorization step requires an actual browser session. This app removes
everything *after* that: no manual data pulls, no hand-typing strike
prices, no writing Pine code yourself.

## Local testing (optional, before deploying)
```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill it in
streamlit run streamlit_app.py
```

## CLI alternative (no UI)
If you'd rather script it than click through a UI:
```
python get_token.py --client-id XXX --client-secret YYY --redirect-uri https://localhost
export UPSTOX_ACCESS_TOKEN="paste_token_here"
python run_daily.py --expiry 2026-09-09 --symbols NIFTY BANKNIFTY RELIANCE
```
