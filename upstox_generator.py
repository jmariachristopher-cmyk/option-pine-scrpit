"""
Core logic for pulling LIVE option-chain data from Upstox API v2 and
generating the same "Options Reversal Zones" Pine Script as the original
Angel One pipeline -- but simpler, because Upstox's /v2/option/chain
endpoint returns the ENTIRE chain (spot price + every strike's CE/PE) in
a single call. No manual instrument-token matching needed.

Works for NIFTY, BANKNIFTY, or any individual FNO stock: call
run_pipeline() once per symbol.
"""

import csv
import gzip
import io
import json
from datetime import datetime

import requests

UPSTOX_BASE = "https://api.upstox.com/v2"
INSTRUMENT_MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
)

# Instrument keys for the major indices. Confirm these against Upstox's
# current instrument master if index naming ever changes.
INDEX_INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
}


# ── OAuth (one-time, daily) ──────────────────────────────────────────
def get_login_url(client_id: str, redirect_uri: str) -> str:
    """Step 1: open this URL in a browser and log in. Upstox will redirect
    to redirect_uri with '?code=...' in the query string -- copy that code."""
    return (
        f"{UPSTOX_BASE}/login/authorization/dialog"
        f"?client_id={client_id}&redirect_uri={redirect_uri}"
    )


def get_access_token(client_id: str, client_secret: str, redirect_uri: str, auth_code: str) -> str:
    """Step 2: exchange the code from the redirect URL for today's access token.
    Upstox tokens expire daily (~3:30 AM IST) -- this whole two-step process
    needs to run again each morning; there is no official headless/TOTP
    login for Upstox the way there was for Angel One."""
    resp = requests.post(
        f"{UPSTOX_BASE}/login/authorization/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code": auth_code,
        },
        headers={"accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Upstox token exchange failed: {data}")
    return data["access_token"]


# ── Instrument master (only needed to resolve individual stocks) ────
_instrument_cache = None


def load_instrument_master():
    global _instrument_cache
    if _instrument_cache is not None:
        return _instrument_cache
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=120)
    resp.raise_for_status()
    raw = gzip.decompress(resp.content)
    _instrument_cache = json.loads(raw)
    return _instrument_cache


def resolve_equity_instrument_key(trading_symbol: str) -> str:
    """Look up an FNO stock's NSE_EQ instrument_key by trading symbol,
    e.g. 'RELIANCE' -> 'NSE_EQ|INE002A01018'."""
    instruments = load_instrument_master()
    tsym = trading_symbol.upper()
    for inst in instruments:
        if inst.get("segment") == "NSE_EQ" and inst.get("trading_symbol", "").upper() == tsym:
            return inst["instrument_key"]
    raise RuntimeError(
        f"Could not find an NSE_EQ instrument_key for '{trading_symbol}'. "
        "Check the spelling matches its NSE trading symbol exactly."
    )


def list_fno_underlyings():
    """Returns the current list of NSE stocks that have F&O contracts,
    derived directly from Upstox's own instrument master (refreshed daily
    by Upstox) rather than a hardcoded list that would drift out of date
    as NSE periodically adds/removes stocks from the F&O segment.

    Written defensively: Upstox's community has reported field-name
    inconsistencies between their bulk file and their API responses, so
    this tries a few plausible field names rather than assuming one.
    If it returns an empty list, use debug_sample_fo_instrument() below
    to see the raw field names and report back.
    """
    instruments = load_instrument_master()
    stocks = set()
    for inst in instruments:
        if inst.get("segment") != "NSE_FO":
            continue
        if inst.get("instrument_type") != "FUT":
            continue  # one FUT contract per underlying is enough to enumerate the universe
        u_type = (inst.get("underlying_type") or inst.get("instrument_type") or "").upper()
        if u_type == "INDEX":
            continue
        u_sym = (
            inst.get("underlying_symbol")
            or inst.get("asset_symbol")
            or inst.get("name")
        )
        if u_sym and u_sym.upper() not in INDEX_INSTRUMENT_KEYS:
            stocks.add(u_sym.upper())
    return sorted(stocks)


def debug_sample_fo_instrument():
    """Returns one raw NSE_FO/FUT record as-is, so you can see the actual
    field names Upstox is currently using if list_fno_underlyings() comes
    back empty or wrong."""
    instruments = load_instrument_master()
    for inst in instruments:
        if inst.get("segment") == "NSE_FO" and inst.get("instrument_type") == "FUT":
            return inst
    return None



    if symbol.upper() in INDEX_INSTRUMENT_KEYS:
        return INDEX_INSTRUMENT_KEYS[symbol.upper()]
    return resolve_equity_instrument_key(symbol)


# ── Expiry discovery (fixes symbols silently returning empty chains) ───
def get_available_expiries(access_token: str, instrument_key: str):
    """Different instruments have different expiry cycles -- Nifty is
    weekly, BankNifty and most individual stocks are monthly only. Using
    one manually-typed date for every symbol will silently return an
    empty chain for anything that doesn't expire that day. This calls
    Upstox's /v2/option/contract endpoint, which lists every real expiry
    for that specific instrument, so nothing is guessed."""
    resp = requests.get(
        f"{UPSTOX_BASE}/option/contract",
        params={"instrument_key": instrument_key},
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Upstox option contract call failed: {payload}")
    expiries = sorted({item["expiry"] for item in payload["data"] if item.get("expiry")})
    return expiries


def get_nearest_expiry(access_token: str, instrument_key: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    expiries = get_available_expiries(access_token, instrument_key)
    upcoming = [e for e in expiries if e >= today]
    if not upcoming:
        raise RuntimeError(f"No upcoming expiries found for {instrument_key}.")
    return upcoming[0]


def fetch_option_chain(access_token: str, instrument_key: str, expiry_date: str):
    """expiry_date format: YYYY-MM-DD. Returns Upstox's chain list -- one
    entry per strike, each already containing CE and PE LTP."""
    resp = requests.get(
        f"{UPSTOX_BASE}/option/chain",
        params={"instrument_key": instrument_key, "expiry_date": expiry_date},
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Upstox option chain call failed: {payload}")
    return payload["data"]


# ── Build the ATM-centered strike window (spacing inferred, not guessed) ─
def build_rows(chain, n_each_side: int):
    if not chain:
        raise RuntimeError("Empty option chain returned -- check symbol/expiry_date.")

    spot = chain[0]["underlying_spot_price"]
    strikes = sorted(chain, key=lambda r: r["strike_price"])

    # Infer the real strike spacing directly from the returned data --
    # no per-symbol guessing needed, unlike a price-band heuristic.
    diffs = [strikes[i + 1]["strike_price"] - strikes[i]["strike_price"]
             for i in range(len(strikes) - 1)]
    step = min(diffs) if diffs else 1

    atm = round(spot / step) * step
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i]["strike_price"] - atm))

    lo = max(0, atm_idx - n_each_side)
    hi = min(len(strikes), atm_idx + n_each_side + 1)
    window = strikes[lo:hi]

    rows = []
    for item in window:
        ce = (item.get("call_options", {}).get("market_data", {}) or {}).get("ltp") or 0.0
        pe = (item.get("put_options", {}).get("market_data", {}) or {}).get("ltp") or 0.0
        rows.append({"strike": item["strike_price"], "ce": float(ce), "pe": float(pe)})
    return rows, spot, atm, step


# ── Zone math -- identical formulas to the original pipeline ───────────
def compute_zones(rows):
    n = len(rows)
    out = []
    for i, r in enumerate(rows):
        ce, pe = r["ce"], r["pe"]
        avg = (ce + pe) / 2
        if i >= 2 and i + 2 < n:
            bl = (rows[i + 2]["ce"] + rows[i - 2]["pe"]) / 2
        else:
            bl = None
        out.append({
            "strike": r["strike"], "ce": ce, "pe": pe, "avg": avg, "bl": bl,
            "uce133": ce * 1.33, "uce15": ce * 1.5,
            "upe133": pe * 1.33, "upe15": pe * 1.5,
            "lce15": ce / 1.5, "lce2": ce / 3,
            "lpe15": pe / 1.5, "lpe2": pe / 3,
        })
    return out


def fv(v):
    return "0" if v is None else f"{v:.4f}"


def build_csv_text(data) -> str:
    cols = ["strike", "ce", "pe", "avg", "bl", "uce133", "uce15",
            "upe133", "upe15", "lce15", "lce2", "lpe15", "lpe2"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for d in data:
        w.writerow([d[c] if d[c] is not None else "" for c in cols])
    return buf.getvalue()


def build_pine_text(data, symbol, expiry) -> str:
    strikes = [f'"{d["strike"]:g}"' for d in data]
    default_s = f'"{data[len(data) // 2]["strike"]:g}"'
    today = datetime.now().strftime("%d %b %Y")

    def arr(key):
        return ", ".join(fv(d[key]) for d in data)

    L = []
    L.append("//@version=5")
    L.append(f'indicator("Options Reversal Zones - {symbol} {today} | Expiry: {expiry}", '
              f'overlay=true, max_lines_count=500, max_labels_count=500)')
    L.append("")
    L.append("// -- Add this to the UNDERLYING chart (Nifty/BankNifty spot or futures, or")
    L.append("// the stock itself) -- not an option contract's own chart. Auto-ATM needs")
    L.append("// the underlying's live close price to find the nearest strike.")
    L.append('auto_atm = input.bool(true, "Auto-track ATM to live price")')
    L.append(f'selected = input.string({default_s}, "Manual Strike (used when Auto-track is off)", options=[{", ".join(strikes)}])')
    L.append("")
    L.append('show_avg    = input.bool(true,  "Individual Average")')
    L.append('show_bl     = input.bool(true,  "Boundary Line")')
    L.append('show_uce    = input.bool(true,  "Upper Reversal Zone CE")')
    L.append('show_upe    = input.bool(true,  "Upper Reversal Zone PE")')
    L.append('show_lce    = input.bool(true,  "Lower Reversal Zone CE")')
    L.append('show_lpe    = input.bool(true,  "Lower Reversal Zone PE")')
    L.append('show_lbl    = input.bool(true,  "Show Labels")')
    L.append('lw          = input.int(1, "Line Width", minval=1, maxval=4)')
    L.append("")
    L.append(f'var string[] strike_arr  = array.from({", ".join(strikes)})')
    L.append(f'var float[]  avg_arr     = array.from({arr("avg")})')
    L.append(f'var float[]  bl_arr      = array.from({arr("bl")})')
    L.append(f'var float[]  uce133_arr  = array.from({arr("uce133")})')
    L.append(f'var float[]  uce15_arr   = array.from({arr("uce15")})')
    L.append(f'var float[]  upe133_arr  = array.from({arr("upe133")})')
    L.append(f'var float[]  upe15_arr   = array.from({arr("upe15")})')
    L.append(f'var float[]  lce15_arr   = array.from({arr("lce15")})')
    L.append(f'var float[]  lce2_arr    = array.from({arr("lce2")})')
    L.append(f'var float[]  lpe15_arr   = array.from({arr("lpe15")})')
    L.append(f'var float[]  lpe2_arr    = array.from({arr("lpe2")})')
    L.append("")
    L.append("f_line(y, col, w) =>")
    L.append("    line.new(bar_index-1, y, bar_index, y, extend=extend.both, color=col, width=w)")
    L.append("")
    L.append("f_lbl(y, txt, col) =>")
    L.append("    if show_lbl")
    L.append("        label.new(bar_index, y, txt, xloc=xloc.bar_index, yloc=yloc.price,")
    L.append("                  style=label.style_label_left, color=color.new(col,80),")
    L.append("                  textcolor=col, size=size.small)")
    L.append("")
    L.append("f_nearest_idx() =>")
    L.append("    var int best = 0")
    L.append("    minDiff = 1e10")
    L.append("    for i = 0 to array.size(strike_arr) - 1")
    L.append("        sk = str.tonumber(array.get(strike_arr, i))")
    L.append("        d = math.abs(close - sk)")
    L.append("        if d < minDiff")
    L.append("            minDiff := d")
    L.append("            best := i")
    L.append("    best")
    L.append("")
    L.append("idx = auto_atm ? f_nearest_idx() : array.indexof(strike_arr, selected)")
    L.append("")
    L.append("if barstate.islast and idx >= 0")
    L.append("    s = array.get(strike_arr, idx)")
    L.append("    if show_avg")
    L.append("        v = array.get(avg_arr, idx)")
    L.append("        f_line(v, color.new(color.gray, 20), lw)")
    L.append('        f_lbl(v, s + " Avg " + str.tostring(v, "#.00"), color.gray)')
    L.append("    if show_bl")
    L.append("        v = array.get(bl_arr, idx)")
    L.append("        f_line(v, color.new(color.orange, 20), lw)")
    L.append('        f_lbl(v, s + " BL " + str.tostring(v, "#.00"), color.orange)')
    L.append("    if show_uce")
    L.append("        v = array.get(uce133_arr, idx)")
    L.append("        f_line(v, color.new(color.red, 20), lw)")
    L.append('        f_lbl(v, s + " UCEx1.33 " + str.tostring(v, "#.00"), color.red)')
    L.append("        v := array.get(uce15_arr, idx)")
    L.append("        f_line(v, color.new(color.red, 40), lw)")
    L.append('        f_lbl(v, s + " UCEx1.5 " + str.tostring(v, "#.00"), color.red)')
    L.append("    if show_upe")
    L.append("        v = array.get(upe133_arr, idx)")
    L.append("        f_line(v, color.new(color.purple, 20), lw)")
    L.append('        f_lbl(v, s + " UPEx1.33 " + str.tostring(v, "#.00"), color.purple)')
    L.append("        v := array.get(upe15_arr, idx)")
    L.append("        f_line(v, color.new(color.purple, 40), lw)")
    L.append('        f_lbl(v, s + " UPEx1.5 " + str.tostring(v, "#.00"), color.purple)')
    L.append("    if show_lce")
    L.append("        v = array.get(lce15_arr, idx)")
    L.append("        f_line(v, color.new(color.teal, 20), lw)")
    L.append('        f_lbl(v, s + " LCEx1.5 " + str.tostring(v, "#.00"), color.teal)')
    L.append("        v := array.get(lce2_arr, idx)")
    L.append("        f_line(v, color.new(color.teal, 40), lw)")
    L.append('        f_lbl(v, s + " LCEx2 " + str.tostring(v, "#.00"), color.teal)')
    L.append("    if show_lpe")
    L.append("        v = array.get(lpe15_arr, idx)")
    L.append("        f_line(v, color.new(color.blue, 20), lw)")
    L.append('        f_lbl(v, s + " LPEx1.5 " + str.tostring(v, "#.00"), color.blue)')
    L.append("        v := array.get(lpe2_arr, idx)")
    L.append("        f_line(v, color.new(color.blue, 40), lw)")
    L.append('        f_lbl(v, s + " LPEx2 " + str.tostring(v, "#.00"), color.blue)')
    L.append("")
    L.append('plot(na, "Individual Average",     color=color.gray)')
    L.append('plot(na, "Boundary Line",          color=color.orange)')
    L.append('plot(na, "Upper Reversal Zone CE", color=color.red)')
    L.append('plot(na, "Upper Reversal Zone PE", color=color.purple)')
    L.append('plot(na, "Lower Reversal Zone CE", color=color.teal)')
    L.append('plot(na, "Lower Reversal Zone PE", color=color.blue)')
    return "\n".join(L)


def build_multi_pine_text(results_by_symbol: dict) -> str:
    """One Pine script covering every symbol you fetched, with a dropdown
    to pick which one's zones to show. All symbols' strike data are
    concatenated into flat arrays (Pine has no array-of-arrays), with an
    offset/count per symbol so the script only searches within that
    symbol's own slice when finding the nearest strike to price.

    IMPORTANT: auto-ATM only makes sense if this indicator is applied to
    the SAME symbol's own chart as whatever you pick in the dropdown --
    e.g. select "BANKNIFTY" while viewing the BankNifty chart. Picking
    "RELIANCE" while looking at a Nifty chart will show Reliance's price
    zones plotted against Nifty's price, which is meaningless.
    """
    symbols = list(results_by_symbol.keys())
    if not symbols:
        raise RuntimeError("No symbols to build a combined script from.")

    flat = {k: [] for k in
            ["strike", "avg", "bl", "uce133", "uce15", "upe133", "upe15",
             "lce15", "lce2", "lpe15", "lpe2"]}
    offsets, counts, expiries = [], [], []
    cursor = 0
    for sym in symbols:
        data = results_by_symbol[sym]["data"]
        offsets.append(cursor)
        counts.append(len(data))
        expiries.append(results_by_symbol[sym]["expiry"])
        for d in data:
            for k in flat:
                flat[k].append(d[k])
        cursor += len(data)

    def arr(key):
        return ", ".join(fv(v) for v in flat[key])

    symbol_opts = ", ".join(f'"{s}"' for s in symbols)
    offset_str = ", ".join(str(o) for o in offsets)
    count_str = ", ".join(str(c) for c in counts)
    today = datetime.now().strftime("%d %b %Y")
    expiry_note = "; ".join(f"{s}:{e}" for s, e in zip(symbols, expiries))

    L = []
    L.append("//@version=5")
    L.append(f'indicator("Options Reversal Zones - Multi Symbol {today}", '
              f'overlay=true, max_lines_count=500, max_labels_count=500)')
    L.append("")
    L.append("// -- IMPORTANT: apply this to the UNDERLYING chart matching whatever you")
    L.append("// pick below (e.g. select BANKNIFTY while viewing the BankNifty chart).")
    L.append(f"// Expiries used when this was generated -- {expiry_note}")
    L.append("")
    L.append(f'symbol_sel = input.string("{symbols[0]}", "Symbol", options=[{symbol_opts}])')
    L.append("")
    L.append('show_avg    = input.bool(true,  "Individual Average")')
    L.append('show_bl     = input.bool(true,  "Boundary Line")')
    L.append('show_uce    = input.bool(true,  "Upper Reversal Zone CE")')
    L.append('show_upe    = input.bool(true,  "Upper Reversal Zone PE")')
    L.append('show_lce    = input.bool(true,  "Lower Reversal Zone CE")')
    L.append('show_lpe    = input.bool(true,  "Lower Reversal Zone PE")')
    L.append('show_lbl    = input.bool(true,  "Show Labels")')
    L.append('lw          = input.int(1, "Line Width", minval=1, maxval=4)')
    L.append("")
    L.append(f'var string[] symbol_names = array.from({symbol_opts})')
    L.append(f'var int[]    sym_offset   = array.from({offset_str})')
    L.append(f'var int[]    sym_count    = array.from({count_str})')
    L.append(f'var float[]  strike_arr   = array.from({arr("strike")})')
    L.append(f'var float[]  avg_arr      = array.from({arr("avg")})')
    L.append(f'var float[]  bl_arr       = array.from({arr("bl")})')
    L.append(f'var float[]  uce133_arr   = array.from({arr("uce133")})')
    L.append(f'var float[]  uce15_arr    = array.from({arr("uce15")})')
    L.append(f'var float[]  upe133_arr   = array.from({arr("upe133")})')
    L.append(f'var float[]  upe15_arr    = array.from({arr("upe15")})')
    L.append(f'var float[]  lce15_arr    = array.from({arr("lce15")})')
    L.append(f'var float[]  lce2_arr     = array.from({arr("lce2")})')
    L.append(f'var float[]  lpe15_arr    = array.from({arr("lpe15")})')
    L.append(f'var float[]  lpe2_arr     = array.from({arr("lpe2")})')
    L.append("")
    L.append("f_line(y, col, w) =>")
    L.append("    line.new(bar_index-1, y, bar_index, y, extend=extend.both, color=col, width=w)")
    L.append("")
    L.append("f_lbl(y, txt, col) =>")
    L.append("    if show_lbl")
    L.append("        label.new(bar_index, y, txt, xloc=xloc.bar_index, yloc=yloc.price,")
    L.append("                  style=label.style_label_left, color=color.new(col,80),")
    L.append("                  textcolor=col, size=size.small)")
    L.append("")
    L.append("sym_idx = array.indexof(symbol_names, symbol_sel)")
    L.append("offset  = array.get(sym_offset, sym_idx)")
    L.append("count   = array.get(sym_count, sym_idx)")
    L.append("")
    L.append("// Nearest strike to live price, searched only within the selected")
    L.append("// symbol's own slice of the flat arrays.")
    L.append("f_nearest_idx(off, cnt) =>")
    L.append("    var int best = off")
    L.append("    minDiff = 1e10")
    L.append("    for i = off to off + cnt - 1")
    L.append("        d = math.abs(close - array.get(strike_arr, i))")
    L.append("        if d < minDiff")
    L.append("            minDiff := d")
    L.append("            best := i")
    L.append("    best")
    L.append("")
    L.append("idx = f_nearest_idx(offset, count)")
    L.append("")
    L.append("if barstate.islast and idx >= 0")
    L.append("    s_strike = array.get(strike_arr, idx)")
    L.append("    s_lbl = symbol_sel + \" \" + str.tostring(s_strike, \"#\")")
    L.append("    if show_avg")
    L.append("        v = array.get(avg_arr, idx)")
    L.append("        f_line(v, color.new(color.gray, 20), lw)")
    L.append('        f_lbl(v, s_lbl + " Avg " + str.tostring(v, "#.00"), color.gray)')
    L.append("    if show_bl")
    L.append("        v = array.get(bl_arr, idx)")
    L.append("        f_line(v, color.new(color.orange, 20), lw)")
    L.append('        f_lbl(v, s_lbl + " BL " + str.tostring(v, "#.00"), color.orange)')
    L.append("    if show_uce")
    L.append("        v = array.get(uce133_arr, idx)")
    L.append("        f_line(v, color.new(color.red, 20), lw)")
    L.append('        f_lbl(v, s_lbl + " UCEx1.33 " + str.tostring(v, "#.00"), color.red)')
    L.append("        v := array.get(uce15_arr, idx)")
    L.append("        f_line(v, color.new(color.red, 40), lw)")
    L.append('        f_lbl(v, s_lbl + " UCEx1.5 " + str.tostring(v, "#.00"), color.red)')
    L.append("    if show_upe")
    L.append("        v = array.get(upe133_arr, idx)")
    L.append("        f_line(v, color.new(color.purple, 20), lw)")
    L.append('        f_lbl(v, s_lbl + " UPEx1.33 " + str.tostring(v, "#.00"), color.purple)')
    L.append("        v := array.get(upe15_arr, idx)")
    L.append("        f_line(v, color.new(color.purple, 40), lw)")
    L.append('        f_lbl(v, s_lbl + " UPEx1.5 " + str.tostring(v, "#.00"), color.purple)')
    L.append("    if show_lce")
    L.append("        v = array.get(lce15_arr, idx)")
    L.append("        f_line(v, color.new(color.teal, 20), lw)")
    L.append('        f_lbl(v, s_lbl + " LCEx1.5 " + str.tostring(v, "#.00"), color.teal)')
    L.append("        v := array.get(lce2_arr, idx)")
    L.append("        f_line(v, color.new(color.teal, 40), lw)")
    L.append('        f_lbl(v, s_lbl + " LCEx2 " + str.tostring(v, "#.00"), color.teal)')
    L.append("    if show_lpe")
    L.append("        v = array.get(lpe15_arr, idx)")
    L.append("        f_line(v, color.new(color.blue, 20), lw)")
    L.append('        f_lbl(v, s_lbl + " LPEx1.5 " + str.tostring(v, "#.00"), color.blue)')
    L.append("        v := array.get(lpe2_arr, idx)")
    L.append("        f_line(v, color.new(color.blue, 40), lw)")
    L.append('        f_lbl(v, s_lbl + " LPEx2 " + str.tostring(v, "#.00"), color.blue)')
    L.append("")
    L.append('plot(na, "Individual Average",     color=color.gray)')
    L.append('plot(na, "Boundary Line",          color=color.orange)')
    L.append('plot(na, "Upper Reversal Zone CE", color=color.red)')
    L.append('plot(na, "Upper Reversal Zone PE", color=color.purple)')
    L.append('plot(na, "Lower Reversal Zone CE", color=color.teal)')
    L.append('plot(na, "Lower Reversal Zone PE", color=color.blue)')
    return "\n".join(L)


def run_pipeline(access_token: str, symbol: str, expiry_date: str = None, n_each_side: int = 10):
    """One call per symbol. expiry_date format: YYYY-MM-DD, or leave as
    None to auto-pick that symbol's own nearest real expiry (recommended
    when generating multiple symbols at once, since indices and stocks
    don't share the same expiry cycle)."""
    instrument_key = resolve_instrument_key(symbol)
    if not expiry_date:
        expiry_date = get_nearest_expiry(access_token, instrument_key)
    chain = fetch_option_chain(access_token, instrument_key, expiry_date)
    rows, spot, atm, step = build_rows(chain, n_each_side)
    data = compute_zones(rows)
    return {
        "symbol": symbol,
        "expiry": expiry_date,
        "spot": spot,
        "atm": atm,
        "step": step,
        "data": data,
        "csv_text": build_csv_text(data),
        "pine_text": build_pine_text(data, symbol, expiry_date),
        "generated_at": datetime.now().isoformat(),
    }
