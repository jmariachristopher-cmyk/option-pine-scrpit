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


def safe_secret(key, default=""):
    """st.secrets.get() raises StreamlitSecretNotFoundError -- not just
    returning the default -- when no secrets.toml exists at all anywhere
    (not even an empty one). That happens on any fresh deploy before
    secrets are configured, so a plain st.secrets.get(...) call would
    crash the whole app before it ever renders anything."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

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
    default_client_id = safe_secret("UPSTOX_CLIENT_ID", "")
    default_client_secret = safe_secret("UPSTOX_CLIENT_SECRET", "")
    default_redirect_uri = safe_secret("UPSTOX_REDIRECT_URI", "https://localhost")

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
    auto_expiry = colA.checkbox(
        "Auto-detect nearest expiry per symbol (recommended)", value=True,
        help="Nifty is usually weekly; BankNifty and most stocks are monthly only. "
             "Auto-detect avoids picking a date that doesn't exist for a given symbol.",
    )
    manual_expiry = colA.date_input("Manual expiry (used only if auto-detect is off)")
    n_each_side = colB.number_input("Strikes above/below ATM", value=10, min_value=1, max_value=25)
    preset = colC.selectbox(
        "Quick symbol preset",
        ["NIFTY + BANKNIFTY", "Custom list only"],
    )

    default_symbols = "NIFTY, BANKNIFTY" if preset == "NIFTY + BANKNIFTY" else ""

    fo_col1, fo_col2 = st.columns([1, 1])
    if fo_col1.button("Load all NSE F&O stocks"):
        try:
            with st.spinner("Reading Upstox's instrument file for the current F&O list..."):
                from upstox_generator import list_fno_underlyings
                fno_stocks = list_fno_underlyings()
            if fno_stocks:
                st.session_state.fno_symbols_text = ", ".join(["NIFTY", "BANKNIFTY"] + fno_stocks)
                st.success(f"Loaded {len(fno_stocks)} F&O stocks (plus NIFTY + BANKNIFTY).")
            else:
                st.warning(
                    "Got an empty list back. Click 'Debug: show raw F&O record' to see "
                    "the actual field names Upstox is using right now, and send that to me."
                )
        except Exception as e:
            st.error(f"Couldn't load the F&O list: {e}")

    if fo_col2.button("Debug: show raw F&O record"):
        try:
            from upstox_generator import debug_sample_fo_instrument
            sample = debug_sample_fo_instrument()
            st.json(sample if sample else {"result": "No NSE_FO/FUT record found at all."})
        except Exception as e:
            st.error(f"Debug call failed: {e}")

    st.caption(
        "Warning: fetching 180+ stocks takes a while (one option-chain call per symbol) "
        "and may hit Upstox's rate limits. Consider trimming the list before generating."
    )

    symbols_raw = st.text_input(
        "Symbols (comma-separated — NSE trading symbols for indices/stocks)",
        value=st.session_state.get("fno_symbols_text", default_symbols),
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
                expiry_arg = None if auto_expiry else manual_expiry.strftime("%Y-%m-%d")
                results[symbol] = run_pipeline(
                    st.session_state.access_token,
                    symbol,
                    expiry_arg,
                    int(n_each_side),
                )
            except Exception as e:
                errors[symbol] = str(e)
        progress.progress(1.0, text="Done.")

        st.session_state.results = results
        st.session_state.errors = errors

        if not results and not errors:
            st.warning(
                "Fetch finished but returned nothing for any symbol, and no error "
                "was recorded either — this shouldn't normally happen. Try again, "
                "and if it repeats, check that your access token hasn't expired."
            )

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

        # ── Step 3: one combined script with a dropdown, instead of one file per symbol ─
        if results:
            st.divider()
            st.header("Step 3 — Combined script (one file, dropdown to pick symbol)")
            st.caption(
                "Bundles everything fetched above into a single Pine script with a "
                "Symbol dropdown in its settings, instead of separate files per symbol. "
                "Still apply it to the matching underlying's own chart and select that "
                "same symbol in the dropdown — the ATM auto-tracking only makes sense "
                "against that symbol's own live price."
            )
            if st.button("Build combined dropdown script"):
                from upstox_generator import build_multi_pine_text
                try:
                    combined_pine = build_multi_pine_text(results)
                    st.session_state.combined_pine = combined_pine
                except Exception as e:
                    st.error(f"Couldn't build combined script: {e}")

            if st.session_state.get("combined_pine"):
                st.code(st.session_state.combined_pine, language="text")
                st.download_button(
                    "Download combined.pine",
                    st.session_state.combined_pine,
                    file_name="reversal_zones_multi_symbol.pine",
                )
