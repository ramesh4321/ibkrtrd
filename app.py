"""
╔══════════════════════════════════════════════════════════════╗
║   MarketPulse + IBKR  —  Streamlit Dashboard                ║
║   TradingView Signals + Interactive Brokers Auto Trader      ║
╚══════════════════════════════════════════════════════════════╝
Run locally:
    streamlit run app.py

Deploy to Streamlit Cloud:
    Push to GitHub → connect at share.streamlit.io
"""

import time
import random
import asyncio
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

import streamlit as st
import pandas as pd

# ── Python 3.10+ asyncio compatibility fix ────────────────────
# ib_insync (and eventkit) call get_event_loop() at import time,
# which raises RuntimeError in Python 3.10+ when there is no
# running loop in the main thread (e.g. on Streamlit Cloud).
# We create and set a new event loop before importing ib_insync.
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# ── Optional imports (graceful fallback to demo data) ──────────
try:
    from tradingview_ta import TA_Handler, Interval as TVInterval
    HAS_TA = True
except ImportError:
    HAS_TA = False

# ib_insync works only when running LOCALLY with IB Gateway on the same machine.
# On Streamlit Cloud it always runs in Simulation mode — that is intentional.
HAS_IB = False
IB = Stock = Forex = Crypto = MarketOrder = LimitOrder = util = None
try:
    import importlib
    _ib = importlib.import_module("ib_insync")
    IB           = _ib.IB
    Stock        = _ib.Stock
    Forex        = _ib.Forex
    Crypto       = _ib.Crypto
    MarketOrder  = _ib.MarketOrder
    LimitOrder   = _ib.LimitOrder
    util         = _ib.util
    HAS_IB = True
except Exception:
    pass   # Cloud / incompatible env → simulation mode, no error shown

# ─────────────────────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MarketPulse + IBKR",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
#  CUSTOM CSS
# ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');

html, body, [class*="css"] { font-family: 'Space Mono', monospace; }

/* Hide default streamlit elements */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.stDeployButton {display:none;}

/* Dark background */
.stApp { background: #060a0f; }
section[data-testid="stSidebar"] { background: #0b1017; border-right: 1px solid #18243a; }

/* Metric cards */
[data-testid="metric-container"] {
    background: #0b1017;
    border: 1px solid #18243a;
    border-radius: 8px;
    padding: 12px 16px;
}
[data-testid="metric-container"] label { font-size: 9px !important; letter-spacing: 2px; color: #3d4f68 !important; }
[data-testid="metric-container"] [data-testid="stMetricValue"] { font-family: 'Syne', sans-serif; font-size: 22px !important; }

/* Dataframe tables */
.stDataFrame { border: 1px solid #18243a; border-radius: 8px; overflow: hidden; }
[data-testid="stDataFrameResizable"] { background: #0b1017; }

/* Headers */
h1, h2, h3 { font-family: 'Syne', sans-serif !important; }

/* Sidebar inputs */
.stSelectbox label, .stSlider label, .stCheckbox label, .stNumberInput label {
    font-size: 10px !important; letter-spacing: 1.5px; color: #6b7c9a !important;
}

/* Status badges inline */
.badge-sbuy  { background:rgba(0,255,136,.15);  color:#00ff88; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:700; }
.badge-buy   { background:rgba(34,197,94,.1);   color:#4ade80; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:700; }
.badge-neu   { background:rgba(251,191,36,.08); color:#fbbf24; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:700; }
.badge-sell  { background:rgba(239,68,68,.1);   color:#f87171; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:700; }
.badge-ssell { background:rgba(255,64,85,.15);  color:#ff4055; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:700; }

div[data-testid="stHorizontalBlock"] > div { gap: 8px; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
#  SESSION STATE INIT
# ─────────────────────────────────────────────────────────────
def init_state():
    defaults = {
        "trade_history":   [],
        "positions":       {},
        "daily_pnl":       0.0,
        "halted":          False,
        "ib_connected":    False,
        "last_signals":    {},
        "market_data":     {},
        "last_refresh":    None,
        "auto_trade":      False,
        "trade_log":       [],
        "ib":              None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ── Mode is shown cleanly in the header and sidebar only ─────


# ─────────────────────────────────────────────────────────────
#  CONFIG DATACLASS
# ─────────────────────────────────────────────────────────────
@dataclass
class Config:
    IB_HOST:           str   = "127.0.0.1"
    IB_PORT:           int   = 7497
    PAPER_TRADING:     bool  = True
    MAX_POSITION_USD:  float = 1000.0
    STOP_LOSS_PCT:     float = 2.0
    TAKE_PROFIT_PCT:   float = 4.0
    MAX_DAILY_LOSS:    float = 500.0
    MAX_POSITIONS:     int   = 5
    ORDER_TYPE:        str   = "LIMIT"
    MAX_RSI_BUY:       float = 65.0
    BUY_ON:            tuple = ("STRONG_BUY", "BUY")
    SELL_ON:           tuple = ("STRONG_SELL", "SELL")


# ─────────────────────────────────────────────────────────────
#  WATCHLIST
# ─────────────────────────────────────────────────────────────
WATCHLIST = [
    {"symbol":"AAPL",  "tv_exchange":"NASDAQ",  "tv_screener":"america","ib_sectype":"STK",   "ib_exchange":"SMART",    "ib_currency":"USD","name":"Apple Inc.",    "type":"stock",  "base":213.50},
    {"symbol":"MSFT",  "tv_exchange":"NASDAQ",  "tv_screener":"america","ib_sectype":"STK",   "ib_exchange":"SMART",    "ib_currency":"USD","name":"Microsoft",     "type":"stock",  "base":415.20},
    {"symbol":"NVDA",  "tv_exchange":"NASDAQ",  "tv_screener":"america","ib_sectype":"STK",   "ib_exchange":"SMART",    "ib_currency":"USD","name":"NVIDIA Corp.",  "type":"stock",  "base":875.40},
    {"symbol":"TSLA",  "tv_exchange":"NASDAQ",  "tv_screener":"america","ib_sectype":"STK",   "ib_exchange":"SMART",    "ib_currency":"USD","name":"Tesla Inc.",    "type":"stock",  "base":182.30},
    {"symbol":"AMZN",  "tv_exchange":"NASDAQ",  "tv_screener":"america","ib_sectype":"STK",   "ib_exchange":"SMART",    "ib_currency":"USD","name":"Amazon.com",    "type":"stock",  "base":198.60},
    {"symbol":"GOOGL", "tv_exchange":"NASDAQ",  "tv_screener":"america","ib_sectype":"STK",   "ib_exchange":"SMART",    "ib_currency":"USD","name":"Alphabet",      "type":"stock",  "base":172.80},
    {"symbol":"EUR",   "tv_exchange":"FX_IDC",  "tv_screener":"forex",  "ib_sectype":"CASH",  "ib_exchange":"IDEALPRO", "ib_currency":"USD","name":"EUR / USD",     "type":"forex",  "base":1.08420},
    {"symbol":"GBP",   "tv_exchange":"FX_IDC",  "tv_screener":"forex",  "ib_sectype":"CASH",  "ib_exchange":"IDEALPRO", "ib_currency":"USD","name":"GBP / USD",     "type":"forex",  "base":1.27310},
    {"symbol":"BTC",   "tv_exchange":"BINANCE", "tv_screener":"crypto", "ib_sectype":"CRYPTO","ib_exchange":"PAXOS",    "ib_currency":"USD","name":"Bitcoin",       "type":"crypto", "base":67450.0},
    {"symbol":"ETH",   "tv_exchange":"BINANCE", "tv_screener":"crypto", "ib_sectype":"CRYPTO","ib_exchange":"PAXOS",    "ib_currency":"USD","name":"Ethereum",      "type":"crypto", "base":3521.0},
    {"symbol":"SOL",   "tv_exchange":"BINANCE", "tv_screener":"crypto", "ib_sectype":"CRYPTO","ib_exchange":"PAXOS",    "ib_currency":"USD","name":"Solana",        "type":"crypto", "base":142.80},
    {"symbol":"SPY",   "tv_exchange":"AMEX",    "tv_screener":"america","ib_sectype":"STK",   "ib_exchange":"SMART",    "ib_currency":"USD","name":"S&P 500 ETF",   "type":"etf",    "base":524.80},
]


# ─────────────────────────────────────────────────────────────
#  DEMO DATA
# ─────────────────────────────────────────────────────────────
SIGNAL_POOL = ["STRONG_BUY","BUY","BUY","NEUTRAL","NEUTRAL","SELL","BUY","STRONG_BUY","NEUTRAL","BUY","SELL","NEUTRAL"]

def generate_demo_data():
    out = {}
    for i, w in enumerate(WATCHLIST):
        p = w["base"] * (1 + random.uniform(-0.025, 0.025))
        o = w["base"] * (1 + random.uniform(-0.012, 0.012))
        sig = SIGNAL_POOL[i % len(SIGNAL_POOL)]
        out[w["symbol"]] = {
            "name":    w["name"], "type": w["type"],
            "price":   round(p, 4), "open": round(o, 4),
            "high":    round(max(p, o) * 1.005, 4),
            "low":     round(min(p, o) * 0.995, 4),
            "volume":  random.randint(500_000, 80_000_000) if w["type"] != "forex" else 0,
            "rsi":     round(random.uniform(22, 74), 2),
            "macd":    round(random.uniform(-3, 3), 4),
            "signal":  sig,
            "buy":     random.randint(5, 16),
            "sell":    random.randint(2, 10),
            "neutral": random.randint(2, 8),
        }
    return out


# ─────────────────────────────────────────────────────────────
#  FETCH TRADINGVIEW SIGNALS
# ─────────────────────────────────────────────────────────────
def fetch_tv_signals():
    if not HAS_TA:
        return generate_demo_data()
    results = {}
    for w in WATCHLIST:
        tv_sym = w["symbol"]
        if w["ib_sectype"] == "CASH":   tv_sym = w["symbol"] + "USD"
        elif w["ib_sectype"] == "CRYPTO": tv_sym = w["symbol"] + "USDT"
        try:
            h = TA_Handler(symbol=tv_sym, exchange=w["tv_exchange"],
                           screener=w["tv_screener"], interval=TVInterval.INTERVAL_1_MINUTE, timeout=10)
            a = h.get_analysis()
            results[w["symbol"]] = {
                "name": w["name"], "type": w["type"],
                "price":   round(a.indicators.get("close", 0), 4),
                "open":    round(a.indicators.get("open", 0), 4),
                "high":    round(a.indicators.get("high", 0), 4),
                "low":     round(a.indicators.get("low", 0), 4),
                "volume":  int(a.indicators.get("volume", 0)),
                "rsi":     round(a.indicators.get("RSI", 50), 2),
                "macd":    round(a.indicators.get("MACD.macd", 0), 4),
                "signal":  a.summary["RECOMMENDATION"],
                "buy":     a.summary["BUY"],
                "sell":    a.summary["SELL"],
                "neutral": a.summary["NEUTRAL"],
            }
        except Exception:
            results[w["symbol"]] = generate_demo_data().get(w["symbol"], {})
    return results


# ─────────────────────────────────────────────────────────────
#  IB ORDER PLACEMENT
# ─────────────────────────────────────────────────────────────
def place_ib_order(cfg: Config, item: dict, action: str, price: float, signal: str, rsi: float):
    sym = item["symbol"]
    qty = max(1, int(cfg.MAX_POSITION_USD / price)) if price > 0 else 1
    if item["ib_sectype"] == "CRYPTO":
        qty = round(cfg.MAX_POSITION_USD / price, 6)

    sl = round(price * (1 - cfg.STOP_LOSS_PCT/100)   if action=="BUY" else price * (1 + cfg.STOP_LOSS_PCT/100), 4)
    tp = round(price * (1 + cfg.TAKE_PROFIT_PCT/100) if action=="BUY" else price * (1 - cfg.TAKE_PROFIT_PCT/100), 4)

    record = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "action": action, "symbol": sym, "qty": qty,
        "price": price, "value": round(qty * price, 2),
        "signal": signal, "rsi": rsi, "sl": sl, "tp": tp,
        "status": "SIMULATED", "pnl": 0.0,
    }

    if HAS_IB and st.session_state.ib_connected and st.session_state.ib:
        try:
            ib = st.session_state.ib
            if item["ib_sectype"] == "STK":
                contract = Stock(sym, item["ib_exchange"], item["ib_currency"])
            elif item["ib_sectype"] == "CASH":
                contract = Forex(sym + item["ib_currency"])
            else:
                contract = Crypto(sym, "PAXOS", "USD")

            bracket = ib.bracketOrder(
                action, qty,
                limitPrice=round(price * 1.001 if action=="BUY" else price * 0.999, 4),
                takeProfitPrice=tp,
                stopLossPrice=sl,
            )
            for o in bracket:
                ib.placeOrder(contract, o)
            record["status"] = "SUBMITTED"
            record["order_id"] = bracket[0].orderId
        except Exception as e:
            record["status"] = f"FAILED: {e}"

    st.session_state.trade_history.append(record)
    st.session_state.trade_log.append(
        f"[{record['time']}] {action} {qty} {sym} @ ${price:.4f}  SL=${sl}  TP=${tp}  [{record['status']}]"
    )

    if action == "BUY":
        st.session_state.positions[sym] = {"qty": qty, "avg_cost": price, "sl": sl, "tp": tp}
    elif action == "SELL" and sym in st.session_state.positions:
        pos = st.session_state.positions[sym]
        pnl = (price - pos["avg_cost"]) * pos["qty"]
        record["pnl"] = round(pnl, 2)
        st.session_state.daily_pnl += pnl
        del st.session_state.positions[sym]

    return record


# ─────────────────────────────────────────────────────────────
#  SIGNAL EVALUATOR
# ─────────────────────────────────────────────────────────────
def evaluate_and_trade(cfg: Config, mdata: dict):
    if st.session_state.halted:
        return
    executed = []
    for w in WATCHLIST:
        sym  = w["symbol"]
        d    = mdata.get(sym, {})
        if not d: continue
        sig   = d.get("signal", "NEUTRAL")
        rsi   = d.get("rsi", 50)
        price = d.get("price", 0)
        prev  = st.session_state.last_signals.get(sym)

        # BUY logic
        if sig in cfg.BUY_ON and prev not in cfg.BUY_ON:
            if rsi <= cfg.MAX_RSI_BUY:
                if sym not in st.session_state.positions:
                    if len(st.session_state.positions) < cfg.MAX_POSITIONS:
                        rec = place_ib_order(cfg, w, "BUY", price, sig, rsi)
                        executed.append(rec)

        # SELL logic
        elif sig in cfg.SELL_ON and prev not in cfg.SELL_ON:
            if sym in st.session_state.positions:
                rec = place_ib_order(cfg, w, "SELL", price, sig, rsi)
                executed.append(rec)

        st.session_state.last_signals[sym] = sig

    # Daily loss check
    if st.session_state.daily_pnl <= -cfg.MAX_DAILY_LOSS:
        st.session_state.halted = True

    return executed


# ─────────────────────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuration")

    paper = st.toggle("📄 Paper Trading Mode", value=True)
    st.caption("Always use Paper mode first!")

    st.divider()
    st.markdown("**🏦 IB Connection**")

    # ── Load credentials from st.secrets or manual entry ──────
    # On Streamlit Cloud: set secrets in the dashboard
    # Locally: add to .streamlit/secrets.toml
    _default_user = st.secrets.get("IB_USERNAME", "") if hasattr(st, "secrets") else ""
    _default_pass = st.secrets.get("IB_PASSWORD", "") if hasattr(st, "secrets") else ""
    _default_host = st.secrets.get("IB_HOST", "127.0.0.1") if hasattr(st, "secrets") else "127.0.0.1"
    _default_port = int(st.secrets.get("IB_PORT", 7497)) if hasattr(st, "secrets") else 7497

    with st.expander("🔐 IB Credentials", expanded=not st.session_state.ib_connected):
        ib_username = st.text_input("IBKR Username", value=_default_user,
                                    placeholder="your@email.com or username",
                                    help="Your Interactive Brokers login username")
        ib_password = st.text_input("IBKR Password", value=_default_pass,
                                    type="password",
                                    placeholder="••••••••",
                                    help="Your Interactive Brokers password")
        ib_host = st.text_input("TWS / Gateway Host", value=_default_host,
                                help="127.0.0.1 when running locally")
        ib_port = st.number_input("Port", value=_default_port, min_value=1000, max_value=9999,
                                  help="7497 = paper | 7496 = live | 4002 = IB Gateway paper | 4001 = IB Gateway live")
        paper_port = 7497 if paper else 7496
        st.caption(f"💡 For {'paper' if paper else 'live'} trading use port **{paper_port}**")

        if ib_username and ib_password:
            st.success("✅ Credentials loaded", icon="🔐")
        else:
            st.info("Enter credentials or add to `.streamlit/secrets.toml`", icon="ℹ️")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔌 Connect", use_container_width=True):
            if not HAS_IB:
                st.error("ib_insync not installed")
            elif not ib_username or not ib_password:
                st.error("Enter your IBKR username & password first")
            else:
                try:
                    # ib_insync connects to an already-running TWS/Gateway.
                    # Credentials are used by TWS/Gateway itself (not passed here).
                    # The username/password fields let you store them securely in
                    # st.secrets and display login instructions if Gateway is not running.
                    ib = IB()
                    ib.connect(ib_host, int(ib_port), clientId=1, readonly=False)
                    st.session_state.ib = ib
                    st.session_state.ib_connected = True
                    st.session_state.ib_username = ib_username
                    st.success(f"✅ Connected as {ib_username}!")
                except Exception as e:
                    err = str(e)
                    if "Connection refused" in err or "10061" in err:
                        st.error(
                            f"❌ Could not connect to TWS/Gateway at {ib_host}:{ib_port}\n\n"
                            "**Make sure:**\n"
                            "1. IB Gateway or TWS is open and logged in\n"
                            "2. API is enabled: Edit → Global Config → API → Settings\n"
                            "3. Port matches (7497 paper / 7496 live)\n"
                            "4. 'Read-Only API' is unchecked"
                        )
                    else:
                        st.error(f"❌ {err}")
    with col2:
        if st.button("⛔ Disconnect", use_container_width=True):
            if st.session_state.ib:
                try: st.session_state.ib.disconnect()
                except: pass
            st.session_state.ib_connected = False
            st.session_state.ib = None
            st.info("Disconnected")

    ib_user_label = st.session_state.get("ib_username", "")
    ib_status = f"🟢 {ib_user_label}" if st.session_state.ib_connected else "🔴 Disconnected"
    st.caption(f"Status: {ib_status}")

    st.divider()
    st.markdown("**💰 Risk Management**")
    max_pos   = st.number_input("Max Position ($)", 100, 100000, 1000, step=100)
    sl_pct    = st.slider("Stop Loss %",    0.5, 10.0, 2.0, 0.5)
    tp_pct    = st.slider("Take Profit %",  0.5, 20.0, 4.0, 0.5)
    max_loss  = st.number_input("Max Daily Loss ($)", 50, 10000, 500, step=50)
    max_open  = st.number_input("Max Open Positions", 1, 20, 5)
    max_rsi   = st.slider("RSI Buy Filter (max)", 50.0, 85.0, 65.0, 1.0)

    st.divider()
    st.markdown("**📋 Order Settings**")
    order_type = st.selectbox("Order Type", ["LIMIT", "MARKET"])
    interval   = st.selectbox("TV Interval", ["1m","5m","15m","1h"], index=0)

    st.divider()
    st.markdown("**🤖 Auto Trading**")
    auto_trade = st.toggle("Enable Auto Trading", value=False,
                           help="Automatically execute trades based on signals")
    if auto_trade and not paper:
        st.error("⚠️ LIVE mode — real money at risk!")
    refresh_sec = st.slider("Refresh interval (s)", 10, 300, 60)

    if st.session_state.halted:
        st.error("⛔ TRADING HALTED\nDaily loss limit hit")
        if st.button("🔓 Reset Halt"):
            st.session_state.halted = False
            st.session_state.daily_pnl = 0.0

    st.divider()
    if st.button("🗑️ Clear History", use_container_width=True):
        st.session_state.trade_history = []
        st.session_state.trade_log = []
        st.session_state.daily_pnl = 0.0
    if st.button("📤 Clear Positions", use_container_width=True):
        st.session_state.positions = {}


# ─────────────────────────────────────────────────────────────
#  BUILD CONFIG FROM SIDEBAR
# ─────────────────────────────────────────────────────────────
cfg = Config(
    IB_HOST=ib_host, IB_PORT=int(ib_port), PAPER_TRADING=paper,
    MAX_POSITION_USD=float(max_pos), STOP_LOSS_PCT=sl_pct, TAKE_PROFIT_PCT=tp_pct,
    MAX_DAILY_LOSS=float(max_loss), MAX_POSITIONS=int(max_open),
    ORDER_TYPE=order_type, MAX_RSI_BUY=float(max_rsi),
)


# ─────────────────────────────────────────────────────────────
#  HEADER
# ─────────────────────────────────────────────────────────────
c1, c2 = st.columns([3, 1])
with c1:
    if not HAS_IB:
        mode_color = "#3b8aff"
        mode_label = "🔵 SIMULATION (Cloud)"
    elif paper:
        mode_color = "#00ff88"
        mode_label = "📄 PAPER TRADING"
    else:
        mode_color = "#ff4055"
        mode_label = "⚡ LIVE TRADING"
    st.markdown(f"""
    <h1 style="font-family:'Syne',sans-serif;font-size:32px;color:#fff;margin:0">
        MARKET<span style="color:#00ff88">PULSE</span>
        <span style="color:#3b8aff;font-size:24px"> + IBKR</span>
    </h1>
    <p style="font-size:10px;letter-spacing:2px;color:#3d4f68;margin-top:4px">
        TRADINGVIEW SIGNALS · INTERACTIVE BROKERS AUTO TRADER
    </p>
    """, unsafe_allow_html=True)
with c2:
    halted_txt = " · ⛔ HALTED" if st.session_state.halted else ""
    st.markdown(f"""
    <div style="text-align:right;margin-top:8px">
        <div style="background:rgba(0,255,136,.08);border:1px solid rgba(0,255,136,.2);
             padding:6px 14px;border-radius:5px;font-size:11px;color:#00ff88;
             font-weight:700;letter-spacing:1.5px;display:inline-block">
            ● LIVE{halted_txt}
        </div>
        <div style="font-size:10px;color:#3d4f68;margin-top:5px">{datetime.now().strftime('%H:%M:%S')}</div>
        <div style="font-size:10px;color:{mode_color};margin-top:2px">{mode_label}</div>
    </div>
    """, unsafe_allow_html=True)

st.divider()


# ─────────────────────────────────────────────────────────────
#  REFRESH BUTTON + AUTO REFRESH
# ─────────────────────────────────────────────────────────────
col_r1, col_r2, col_r3 = st.columns([2, 1, 1])
with col_r1:
    refresh_btn = st.button("↻  Refresh Market Data", use_container_width=True, type="primary")
with col_r2:
    demo_mode = not HAS_TA
    st.caption(f"{'⚡ Live TV data' if HAS_TA else '🔵 Demo mode'} · {'🏦 IB ready' if HAS_IB else '🔵 IB simulated'}")
with col_r3:
    st.caption(f"Last refresh: {st.session_state.last_refresh or 'Never'}")

# Fetch data
if refresh_btn or not st.session_state.market_data:
    with st.spinner("Fetching signals..."):
        st.session_state.market_data = fetch_tv_signals()
        st.session_state.last_refresh = datetime.now().strftime("%H:%M:%S")
        if auto_trade:
            evaluate_and_trade(cfg, st.session_state.market_data)

mdata = st.session_state.market_data or generate_demo_data()


# ─────────────────────────────────────────────────────────────
#  STATS BAR
# ─────────────────────────────────────────────────────────────
n_buy  = sum(1 for d in mdata.values() if d.get("signal") in ("BUY","STRONG_BUY"))
n_sell = sum(1 for d in mdata.values() if d.get("signal") in ("SELL","STRONG_SELL"))
n_neu  = len(mdata) - n_buy - n_sell
n_pos  = len(st.session_state.positions)
n_trd  = len(st.session_state.trade_history)
dpnl   = st.session_state.daily_pnl

m1,m2,m3,m4,m5,m6 = st.columns(6)
m1.metric("🟢 BUY Signals",     n_buy)
m2.metric("🟡 Neutral",         n_neu)
m3.metric("🔴 SELL Signals",    n_sell)
m4.metric("🔒 Open Positions",  n_pos)
m5.metric("📋 Trades Today",    n_trd)
m6.metric("💰 Daily PnL",       f"${dpnl:+.2f}", delta=f"${dpnl:+.2f}")

st.divider()


# ─────────────────────────────────────────────────────────────
#  MARKET SIGNALS TABLE
# ─────────────────────────────────────────────────────────────
st.markdown("#### 📡 Market Signals  ·  1 Minute Interval")

SIGNAL_EMOJI = {
    "STRONG_BUY": "⬆⬆ STRONG BUY", "BUY": "▲ BUY",
    "NEUTRAL": "─ NEUTRAL", "SELL": "▼ SELL", "STRONG_SELL": "⬇⬇ STRONG SELL",
}
SIGNAL_COLOR = {
    "STRONG_BUY": "🟢", "BUY": "🟢", "NEUTRAL": "🟡", "SELL": "🔴", "STRONG_SELL": "🔴",
}

rows = []
for w in WATCHLIST:
    sym = w["symbol"]
    d   = mdata.get(sym, {})
    if not d: continue
    price = d["price"]; open_ = d["open"]
    chg  = price - open_; pct = chg / open_ * 100 if open_ else 0
    dec  = 5 if d["type"]=="forex" else (2 if d["type"]=="crypto" and price>100 else 4)
    fmt  = f"{{:.{dec}f}}"
    sig  = d.get("signal","NEUTRAL")
    pos  = st.session_state.positions.get(sym)
    pos_str = f"🔒 {pos['qty']}" if pos else ""
    vol = d.get("volume",0)
    vol_str = f"{vol/1e6:.1f}M" if vol >= 1e6 else (f"{vol/1e3:.0f}K" if vol >= 1e3 else "—")

    rows.append({
        "Symbol":   f"{SIGNAL_COLOR.get(sig,'⚪')} {sym}",
        "Name":     d["name"],
        "Type":     d["type"].upper(),
        "Price":    fmt.format(price),
        "Change":   f"{'▲' if chg>=0 else '▼'} {fmt.format(abs(chg))}",
        "Chg %":    f"{pct:+.2f}%",
        "High":     fmt.format(d["high"]),
        "Low":      fmt.format(d["low"]),
        "RSI":      d["rsi"],
        "Signal":   SIGNAL_EMOJI.get(sig, sig),
        "Buy/N/S":  f"{d['buy']}B {d['neutral']}N {d['sell']}S",
        "Position": pos_str,
    })

df_market = pd.DataFrame(rows)

def color_signal(val):
    if "BUY" in str(val) and "SELL" not in str(val): return "color: #00ff88; font-weight:bold"
    if "SELL" in str(val): return "color: #ff4055; font-weight:bold"
    return "color: #fbbf24"

def color_change(val):
    if str(val).startswith("▲"): return "color: #00ff88; font-weight:bold"
    if str(val).startswith("▼"): return "color: #ff4055; font-weight:bold"
    return ""

def color_pct(val):
    try:
        v = float(str(val).replace("%","").replace("+",""))
        return "color: #00ff88" if v > 0 else ("color: #ff4055" if v < 0 else "")
    except: return ""

styled = (df_market.style
    .applymap(color_signal, subset=["Signal"])
    .applymap(color_change, subset=["Change"])
    .applymap(color_pct,    subset=["Chg %"])
    .set_properties(**{"background-color": "#0b1017", "color": "#b8c5d6", "border": "1px solid #18243a"})
    .set_table_styles([{"selector":"th","props":[("background-color","#080d14"),("color","#6b7c9a"),("font-size","10px"),("letter-spacing","1.5px")]}])
)
st.dataframe(styled, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────
#  OPEN POSITIONS
# ─────────────────────────────────────────────────────────────
st.divider()
st.markdown(f"#### 🔒 Open Positions  ·  {n_pos} Active")

if st.session_state.positions:
    pos_cols = st.columns(min(len(st.session_state.positions), 4))
    for i, (sym, pos) in enumerate(st.session_state.positions.items()):
        price   = mdata.get(sym, {}).get("price", pos["avg_cost"])
        pnl     = (price - pos["avg_cost"]) * pos["qty"]
        pnl_pct = (price - pos["avg_cost"]) / pos["avg_cost"] * 100
        pnl_col = "#00ff88" if pnl >= 0 else "#ff4055"
        with pos_cols[i % 4]:
            st.markdown(f"""
            <div style="background:#0b1017;border:1px solid #18243a;border-radius:8px;
                        padding:14px;position:relative;overflow:hidden">
                <div style="position:absolute;top:0;left:0;right:0;height:3px;
                            background:linear-gradient(90deg,#3b8aff,#00ff88)"></div>
                <div style="display:flex;justify-content:space-between;margin-bottom:10px">
                    <span style="font-family:'Syne',sans-serif;font-size:18px;font-weight:800;color:#fff">{sym}</span>
                    <span style="color:{pnl_col};font-weight:700;font-size:13px">
                        {'▲' if pnl>=0 else '▼'} ${abs(pnl):.2f} ({pnl_pct:+.2f}%)
                    </span>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px">
                    <div style="background:#080d14;padding:7px 9px;border-radius:4px">
                        <div style="color:#3d4f68;font-size:8px;letter-spacing:1.5px">QTY</div>
                        <div style="color:#b8c5d6;font-weight:700">{pos['qty']}</div>
                    </div>
                    <div style="background:#080d14;padding:7px 9px;border-radius:4px">
                        <div style="color:#3d4f68;font-size:8px;letter-spacing:1.5px">AVG COST</div>
                        <div style="color:#b8c5d6;font-weight:700">${pos['avg_cost']:.4f}</div>
                    </div>
                    <div style="background:#080d14;padding:7px 9px;border-radius:4px">
                        <div style="color:#3d4f68;font-size:8px;letter-spacing:1.5px">STOP LOSS</div>
                        <div style="color:#ff4055;font-weight:700">${pos.get('sl',0):.4f}</div>
                    </div>
                    <div style="background:#080d14;padding:7px 9px;border-radius:4px">
                        <div style="color:#3d4f68;font-size:8px;letter-spacing:1.5px">TAKE PROFIT</div>
                        <div style="color:#00ff88;font-weight:700">${pos.get('tp',0):.4f}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
else:
    st.info("No open positions. Enable Auto Trading or place a manual order below.")


# ─────────────────────────────────────────────────────────────
#  MANUAL ORDER PANEL
# ─────────────────────────────────────────────────────────────
st.divider()
with st.expander("🖱️  Manual Order Entry", expanded=False):
    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    with mc1:
        m_sym    = st.selectbox("Symbol", [w["symbol"] for w in WATCHLIST])
    with mc2:
        m_action = st.selectbox("Action", ["BUY", "SELL"])
    with mc3:
        m_price  = st.number_input("Price (0=market)", min_value=0.0, value=0.0, format="%.4f")
    with mc4:
        m_qty    = st.number_input("Qty", min_value=0.001, value=1.0, format="%.4f")
    with mc5:
        st.markdown("<br>", unsafe_allow_html=True)
        place_btn = st.button("🚀 Place Order", use_container_width=True, type="primary")

    if place_btn:
        w_item = next((w for w in WATCHLIST if w["symbol"]==m_sym), None)
        if w_item:
            use_price = m_price if m_price > 0 else mdata.get(m_sym, {}).get("price", 0)
            rec = place_ib_order(cfg, w_item, m_action, use_price,
                                 "MANUAL", mdata.get(m_sym, {}).get("rsi", 50))
            st.success(f"✅ {m_action} {m_qty} {m_sym} @ ${use_price:.4f} → {rec['status']}")
            st.rerun()


# ─────────────────────────────────────────────────────────────
#  TRADE HISTORY
# ─────────────────────────────────────────────────────────────
st.divider()
st.markdown(f"#### 📋 Trade History  ·  {n_trd} Orders")

if st.session_state.trade_history:
    th = st.session_state.trade_history[::-1][:50]
    df_trades = pd.DataFrame(th)[["time","action","symbol","qty","price","value","signal","rsi","status","pnl"]]
    df_trades.columns = ["Time","Action","Symbol","Qty","Price","Value","Signal","RSI","Status","PnL"]
    df_trades["PnL"] = df_trades["PnL"].apply(lambda x: f"${x:+.2f}" if x != 0 else "—")
    df_trades["Value"] = df_trades["Value"].apply(lambda x: f"${x:,.2f}")

    def style_action(val):
        return "color:#00ff88;font-weight:bold" if val=="BUY" else "color:#ff4055;font-weight:bold"
    def style_pnl(val):
        if str(val).startswith("$+") or (str(val).startswith("$") and not str(val).startswith("$-")): return "color:#00ff88"
        if str(val).startswith("$-"): return "color:#ff4055"
        return ""

    styled_t = (df_trades.style
        .applymap(style_action, subset=["Action"])
        .applymap(color_signal, subset=["Signal"])
        .applymap(style_pnl,    subset=["PnL"])
        .set_properties(**{"background-color":"#0b1017","color":"#b8c5d6","border":"1px solid #18243a"})
        .set_table_styles([{"selector":"th","props":[("background-color","#080d14"),("color","#6b7c9a"),("font-size","10px"),("letter-spacing","1.5px")]}])
    )
    st.dataframe(styled_t, use_container_width=True, hide_index=True)

    # Export
    csv = df_trades.to_csv(index=False).encode()
    st.download_button("⬇️ Download Trade History CSV", csv, "trades.csv", "text/csv")
else:
    st.info("No trades yet. Enable Auto Trading or use Manual Order Entry above.")


# ─────────────────────────────────────────────────────────────
#  LOG CONSOLE
# ─────────────────────────────────────────────────────────────
with st.expander("🖥️  Order Log Console", expanded=False):
    if st.session_state.trade_log:
        log_text = "\n".join(reversed(st.session_state.trade_log[-50:]))
        st.code(log_text, language=None)
    else:
        st.caption("No log entries yet.")


# ─────────────────────────────────────────────────────────────
#  CONFIG SUMMARY
# ─────────────────────────────────────────────────────────────
st.divider()
with st.expander("⚙️  Active Configuration", expanded=False):
    cc1, cc2, cc3, cc4 = st.columns(4)
    with cc1:
        st.markdown(f"""
        **Trading Mode:** `{'PAPER' if paper else 'LIVE'}`  
        **IB Host:Port:** `{ib_host}:{ib_port}`  
        **IB Status:** `{ib_status}`  
        **Auto Trade:** `{'ON' if auto_trade else 'OFF'}`  
        """)
    with cc2:
        st.markdown(f"""
        **Max Position:** `${max_pos:,}`  
        **Stop Loss:** `{sl_pct}%`  
        **Take Profit:** `{tp_pct}%`  
        **Max Daily Loss:** `${max_loss:,}`  
        """)
    with cc3:
        st.markdown(f"""
        **Max Positions:** `{max_open}`  
        **Order Type:** `{order_type}`  
        **RSI Buy Filter:** `≤ {max_rsi}`  
        **TV Interval:** `{interval}`  
        """)
    with cc4:
        st.markdown(f"""
        **Auto BUY on:** `{', '.join(cfg.BUY_ON)}`  
        **Auto SELL on:** `{', '.join(cfg.SELL_ON)}`  
        **Symbols Tracked:** `{len(WATCHLIST)}`  
        **Refresh:** `{refresh_sec}s`  
        """)

st.caption(f"📡 TradingView via tradingview_ta · 🏦 Orders via Interactive Brokers ib_insync · Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ─────────────────────────────────────────────────────────────
#  AUTO REFRESH
# ─────────────────────────────────────────────────────────────
if auto_trade:
    time.sleep(refresh_sec)
    st.rerun()
