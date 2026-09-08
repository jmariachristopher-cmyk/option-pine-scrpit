"""
Options Reversal Zones — Upstox edition, Streamlit app.

Run this once each morning:
    streamlit run streamlit_app.py

WHY YOU STILL CLICK THROUGH THIS ONCE A DAY:
Upstox's access tokens expire daily (~3:30 AM IST), and Upstox has no
official headless/TOTP login the way Angel One's SmartAPI did. There is
no way — in this app or any other — to make that first login silent.
What this app DOES remove is everything after that: no manual data pulls,
no hand-editing strike counts, no copy-pasting numbers into a script.
"""

import streamlit as st

from upstox_generator import get_login_url, get_access_token, run_pipeline

st.set_page_config(page_title="Options Reversal Zones — Upstox", layout="wide")
st.title("Options Reversal Zones — Upstox live generator")

if "access_token" not in st.session_state:
    st.session_state.access_token = ""

# ── Step 1: paste today's access token ──────────────────────────────────────
# However you generate it (Upstox's own login page, Postman, curl, this
# app's optional helper below) -- this app just needs the final token
# string. Tokens expire daily (~3:30 AM IST); Upstox has no headless
# login, so a fresh token is required each morning regardless of method.
st.header("Step 1 — Paste today's Upstox access token")

token_input = st.text_input(
    "Access Token",
    value=st.session_state.access_token,
    type="password",
    placeholder="eyJ0eXAiOiJKV1Qi...",
)
if token_input:
    st.session_state.access_token = token_input.strip()

if st.session_state.access_token:
    st.success("Token set — you can generate scripts below.")
else:
    st.info("Paste your access token above to continue.")

with st.expander("Don't have a token yet? Optional built-in login helper"):
    st.caption(
        "This walks through Upstox's login flow if you don't already have a "
        "way to generate a token. If a `code` exchange fails with 401, the "
        "usual causes are: the code was pasted after it expired (they're "
        "valid only ~2 minutes and single-use), or Client ID / Client Secret "
        "/ Redirect URI don't match exactly what's registered on your Upstox "
        "app — double check for typos or a truncated paste."
    )
    default_client_id = st.secrets.get("UPSTOX_CLIENT_ID", "")
    default_client_secret = st.secrets.get("UPSTOX_CLIENT_SECRET", "")
    default_redirect_uri = st.secrets.get("UPSTOX_REDIRECT_URI", "https://localhost")

    c1, c2, c3 = st.columns(3)
    client_id = c1.text_input("Client ID", value=default_client_id)
    client_secret = c2.text_input("Client Secret", value=default_client_secret, type="password")
    redirect_uri = c3.text_input("Redirect URI", value=default_redirect_uri)

    if client_id and redirect_uri:
        login_url = get_login_url(client_id, redirect_uri)
        st.markdown(f"1. [Click here to log in to Upstox]({login_url})")
        st.caption(
            "2. Immediately after logging in, copy the `code=...` value from "
            "the redirected URL and paste it below within ~2 minutes."
        )
        auth_code = st.text_input("3. Paste the code here")

        if st.button("Exchange code for access token"):
            if not client_secret:
                st.error("Client Secret is required to exchange the code.")
            elif not auth_code:
                st.error("Paste the code from the redirect URL first.")
            else:
                try:
                    with st.spinner("Exchanging code for access token..."):
                        token = get_access_token(client_id, client_secret, redirect_uri, auth_code)
                    st.session_state.access_token = token
                    st.success("Access token acquired — filled into the box above.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Token exchange failed: {e}")

st.divider()

# ── Step 2: generate Pine scripts ───────────────────────────────────────────
st.header("Step 2 — Generate Pine Scripts")

if not st.session_state.access_token:
    st.warning("Complete Step 1 first — no access token yet.")
else:
    colA, colB, colC = st.columns(3)
    expiry_date = colA.date_input("Expiry date")
    n_each_side = colB.number_input("Strikes above/below ATM", value=10, min_value=1, max_value=25)
    preset = colC.selectbox(
        "Quick symbol preset",
        ["NIFTY + BANKNIFTY", "Custom list only"],
    )

    default_symbols = "NIFTY, BANKNIFTY" if preset == "NIFTY + BANKNIFTY" else ""
    symbols_raw = st.text_input(
        "Symbols (comma-separated — NSE trading symbols for indices/stocks)",
        value=default_symbols,
        placeholder="e.g. NIFTY, BANKNIFTY, RELIANCE, HDFCBANK, TCS",
    )
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]

    if st.button("Fetch & Generate", type="primary", disabled=not symbols):
        results = {}
        errors = {}
        progress = st.progress(0.0, text="Starting...")
        for i, symbol in enumerate(symbols):
            progress.progress((i) / len(symbols), text=f"Fetching {symbol}...")
            try:
                results[symbol] = run_pipeline(
                    st.session_state.access_token,
                    symbol,
                    expiry_date.strftime("%Y-%m-%d"),
                    int(n_each_side),
                )
            except Exception as e:
                errors[symbol] = str(e)
        progress.progress(1.0, text="Done.")

        st.session_state.results = results
        st.session_state.errors = errors

    # ── Show results, one tab per symbol ────────────────────────────────────
    if st.session_state.get("results"):
        results = st.session_state.results
        errors = st.session_state.get("errors", {})

        if errors:
            for symbol, msg in errors.items():
                st.error(f"[{symbol}] {msg}")

        if results:
            tabs = st.tabs(list(results.keys()))
            for tab, (symbol, result) in zip(tabs, results.items()):
                with tab:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Spot", result["spot"])
                    m2.metric("ATM", result["atm"])
                    m3.metric("Strike spacing", result["step"])
                    m4.metric("Strikes pulled", len(result["data"]))

                    st.subheader("Computed table")
                    st.code(result["csv_text"], language="text")

                    st.subheader("Pine Script — copy this into TradingView")
                    st.code(result["pine_text"], language="text")
                    st.download_button(
                        f"Download {symbol}.pine",
                        result["pine_text"],
                        file_name=f"{symbol}.pine",
                        key=f"dl_{symbol}",
                    )
                    st.caption(
                        "Add this to the underlying's chart (index spot/futures, or the "
                        "stock itself) — not an option contract's own chart."
                    )
