import streamlit as st
import ccxt
import pandas as pd
import ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from datetime import datetime, timezone
import time
import json
import os
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="MACD Backtest", layout="wide", page_icon="📊")

# ─── Mobile detection ────────────────────────────────────────────────────────
st.markdown("""
<script>
const w = window.innerWidth;
const el = window.parent.document.querySelector('[data-testid="stApp"]');
if (el) el.setAttribute('data-mobile', w < 768 ? 'true' : 'false');
</script>
""", unsafe_allow_html=True)

IS_MOBILE = st.session_state.get("is_mobile", False)

st.markdown("""
<style>
    .stApp { background-color: #0d1117; }
    .metric-box { background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;text-align:center; }
    .metric-value { font-size:1.8rem;font-weight:bold;color:#58a6ff; }
    .metric-label { font-size:0.8rem;color:#8b949e;margin-top:4px; }
    .metric-value.green { color:#3fb950; }
    .metric-value.red   { color:#f85149; }
    .tag { display:inline-block;background:#21262d;border:1px solid #30363d;border-radius:4px;
           padding:1px 6px;font-size:0.7rem;color:#8b949e;margin-right:3px; }
    .saved-card { background:#161b22;border:1px solid #30363d;border-radius:8px;
                  padding:10px 12px;margin-bottom:6px; }
    .section-header { font-size:1.05rem;font-weight:600;color:#e6edf3;margin:20px 0 10px 0; }
    table.tv-table { width:100%;border-collapse:collapse;font-size:0.82rem; }
    table.tv-table th { color:#8b949e;font-weight:500;padding:8px 12px;
                        border-bottom:1px solid #30363d;text-align:left; }
    table.tv-table td { padding:7px 12px;border-bottom:1px solid #21262d;color:#e6edf3; }
    table.tv-table tr:hover td { background:#161b22; }
    .green { color:#3fb950; }
    .red   { color:#f85149; }
    /* Mobile styles */
    @media (max-width: 768px) {
        .metric-value { font-size:1.4rem; }
        .metric-box { padding:12px 8px; }
        table.tv-table { font-size:0.72rem; }
        table.tv-table th, table.tv-table td { padding:5px 6px; }
    }
</style>
""", unsafe_allow_html=True)

MAX_SAVES = 10

# ─── Google Sheets helpers ───────────────────────────────────────────────────
SHEET_ID = "1hkQv1MoSvCCm6DONovVuRXcZothNoZVHWVQk0VFyU7M"
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets"]

def get_sheet():
    # รองรับทั้ง local (ไฟล์ json) และ Streamlit Cloud (secrets)
    try:
        if os.path.exists("C:/btc_app/backtest-app-495606-f394e0e99674.json"):
            creds = Credentials.from_service_account_file(
                "C:/btc_app/backtest-app-495606-f394e0e99674.json", scopes=SCOPES)
        else:
            # Streamlit Cloud — ใช้ st.secrets
            creds = Credentials.from_service_account_info(
                st.secrets["gcp_service_account"], scopes=SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        try:
            ws = sh.worksheet("saves")
        except:
            ws = sh.add_worksheet("saves", rows=100, cols=5)
            ws.append_row(["id","time","config","stats","note"])
        return ws
    except Exception as e:
        st.error(f"เชื่อม Google Sheets ไม่ได้: {e}")
        return None

@st.cache_data(ttl=30, show_spinner=False)
def load_saves():
    ws = get_sheet()
    if ws is None: return []
    rows = ws.get_all_records()
    saves = []
    for r in rows:
        try:
            saves.append({
                "time":   r["time"],
                "config": json.loads(r["config"]),
                "stats":  json.loads(r["stats"]),
                "note":   r.get("note",""),
            })
        except: pass
    return saves

def write_saves(saves):
    ws = get_sheet()
    if ws is None: return
    ws.clear()
    ws.append_row(["id","time","config","stats","note"])
    for i, s in enumerate(saves):
        ws.append_row([
            i+1,
            s["time"],
            json.dumps(s["config"],  ensure_ascii=False),
            json.dumps(s["stats"],   ensure_ascii=False),
            s.get("note",""),
        ])
    load_saves.clear()  # clear cache

# ─── Fetch ───────────────────────────────────────────────────────────────────
ASSET_LIST = {
    "BTC-USD (Yahoo Finance)": {"type": "stock", "symbol": "BTC-USD"},
    "ETH-USD (Yahoo Finance)": {"type": "stock", "symbol": "ETH-USD"},
    "MSTR (MicroStrategy)":    {"type": "stock", "symbol": "MSTR"},
    "Metaplanet 3350 (JP)":    {"type": "stock", "symbol": "3350.T"},
    "กรอก ticker เอง":          {"type": "custom", "symbol": ""},
}

TF_YFINANCE = {"1d": "1d", "1W": "1wk", "3d": "1wk", "4h": "1h", "1h": "1h"}

@st.cache_data(show_spinner=False)
def fetch_ohlcv(asset_type, symbol, timeframe, years=8):
    # ใช้ yfinance ทั้งหมด — รองรับ crypto (BTC-USD) และหุ้น
    period_map = {1:"1y",2:"2y",3:"5y",4:"5y",5:"5y",6:"10y",7:"10y",8:"max"}
    period = period_map.get(years, "max")
    yf_tf  = TF_YFINANCE.get(timeframe, "1d")
    if yf_tf == "1h":
        period = "730d"
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=yf_tf, auto_adjust=True)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.columns = [c.lower() for c in df.columns]
    df = df[["open","high","low","close","volume"]].dropna()
    df.index.name = "timestamp"
    return df

# ─── Signals ─────────────────────────────────────────────────────────────────
def compute_signals(df, fast=12, slow=26, signal=9, use_ma_filter=False, ma_period=128):
    df = df.copy()
    macd_obj = ta.trend.MACD(df["close"], window_fast=fast, window_slow=slow, window_sign=signal)
    df["macd"]        = macd_obj.macd()
    df["macd_signal"] = macd_obj.macd_signal()
    df["macd_hist"]   = macd_obj.macd_diff()
    if use_ma_filter:
        df["ma_trend"]       = ta.trend.SMAIndicator(df["close"], window=ma_period).sma_indicator()
        df["price_above_ma"] = df["close"] > df["ma_trend"]
    else:
        df["ma_trend"]       = np.nan
        df["price_above_ma"] = True

    df["macd_above_zero"] = df["macd"] > 0
    df["macd_cross_up"]   = (df["macd"].shift(1) < 0) & (df["macd"] >= 0)
    df["macd_cross_down"] = (df["macd"].shift(1) > 0) & (df["macd"] <= 0)

    trades = []
    in_trade = False
    entry_idx = entry_price = None

    for i in range(1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i-1]
        if not in_trade:
            if not bool(row["price_above_ma"]): continue
            just_macd = bool(row["macd_cross_up"])
            just_ma   = use_ma_filter and bool(row["price_above_ma"]) \
                        and not bool(prev["price_above_ma"]) and bool(row["macd_above_zero"])
            if just_macd or just_ma:
                in_trade = True; entry_idx = i; entry_price = row["close"]
        else:
            exit_ma   = use_ma_filter and not bool(row["price_above_ma"])
            exit_macd = bool(row["macd_cross_down"])
            if exit_ma or exit_macd:
                exit_price = row["close"]
                pnl = (exit_price - entry_price) / entry_price * 100
                # max favorable/adverse excursion — ใช้ high/low จริง (wick) ไม่ใช่ close
                high_slice = df["high"].iloc[entry_idx:i+1]
                low_slice  = df["low"].iloc[entry_idx:i+1]
                if len(high_slice) > 1:
                    mfe = (high_slice.max() - entry_price) / entry_price * 100
                    mae = (low_slice.min()  - entry_price) / entry_price * 100
                else:
                    mfe = mae = 0.0
                reasons = (["ราคาหลุด MA"] if exit_ma else []) + \
                          (["MACD cross down 0"] if exit_macd else [])
                trades.append({
                    "entry_time":  str(df.index[entry_idx]),
                    "exit_time":   str(df.index[i]),
                    "entry_price": entry_price,
                    "exit_price":  exit_price,
                    "pnl_pct":     pnl,
                    "hold_bars":   i - entry_idx,
                    "win":         pnl > 0,
                    "exit_reason": " + ".join(reasons),
                    "mfe_pct":     mfe,
                    "mae_pct":     mae,
                    "entry_idx":   entry_idx,
                    "exit_idx":    i,
                })
                in_trade = False

    return df, pd.DataFrame(trades)

# ─── Stats ───────────────────────────────────────────────────────────────────
def calc_stats(trades_df, initial_capital=100000):
    if trades_df.empty: return {}
    wins   = trades_df[trades_df["win"]]
    losses = trades_df[~trades_df["win"]]

    max_lose = cur = 0
    for w in trades_df["win"]:
        cur = 0 if w else cur+1; max_lose = max(max_lose, cur)
    max_win = cur = 0
    for w in trades_df["win"]:
        cur = cur+1 if w else 0; max_win = max(max_win, cur)

    # equity curve (compounding)
    equity_mult = (1 + trades_df["pnl_pct"]/100).cumprod()
    equity_val  = equity_mult * initial_capital
    peak        = equity_val.cummax()
    dd_series   = (equity_val - peak) / peak * 100

    gross_profit = wins["pnl_pct"].sum()   if len(wins)   > 0 else 0
    gross_loss   = abs(losses["pnl_pct"].sum()) if len(losses) > 0 else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    net_pnl_pct  = (equity_mult.iloc[-1] - 1) * 100
    net_pnl_usd  = equity_val.iloc[-1] - initial_capital

    return {
        "total_trades":      len(trades_df),
        "winning_trades":    len(wins),
        "losing_trades":     len(losses),
        "winrate":           len(wins)/len(trades_df)*100,
        "avg_win":           wins["pnl_pct"].mean()   if len(wins)>0   else 0,
        "avg_loss":          losses["pnl_pct"].mean() if len(losses)>0 else 0,
        "max_lose_streak":   max_lose,
        "max_win_streak":    max_win,
        "max_dd":            dd_series.min(),
        "max_dd_usd":        (equity_val - peak).min(),
        "avg_hold":          trades_df["hold_bars"].mean(),
        "avg_hold_win":      wins["hold_bars"].mean()   if len(wins)>0   else 0,
        "avg_hold_loss":     losses["hold_bars"].mean() if len(losses)>0 else 0,
        "total_return":      net_pnl_pct,
        "net_pnl_usd":       net_pnl_usd,
        "gross_profit_pct":  gross_profit,
        "gross_loss_pct":    gross_loss,
        "profit_factor":     profit_factor,
        "largest_win_pct":   wins["pnl_pct"].max()        if len(wins)>0   else 0,
        "largest_loss_pct":  losses["pnl_pct"].min()      if len(losses)>0 else 0,
        "ratio_win_loss":    abs(wins["pnl_pct"].mean() / losses["pnl_pct"].mean())
                             if len(wins)>0 and len(losses)>0 else 0,
        "equity":            equity_mult,
        "equity_val":        equity_val,
        "drawdown":          dd_series,
        "initial_capital":   initial_capital,
    }

# ─── Chart helpers ───────────────────────────────────────────────────────────
def build_price_chart(df, trades, use_ma, ma_period, asset_symbol="BTC/USDT"):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.7,0.3], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name=asset_symbol,
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
    ), row=1, col=1)
    if use_ma and df["ma_trend"].notna().any():
        fig.add_trace(go.Scatter(x=df.index, y=df["ma_trend"],
            name=f"MA{ma_period}", line=dict(color="#d29922",width=1.5,dash="dot")), row=1, col=1)
    if not trades.empty:
        et = pd.to_datetime(trades["entry_time"]); ep = trades["entry_price"]
        fig.add_trace(go.Scatter(x=et, y=ep, mode="markers", name="Entry",
            marker=dict(symbol="triangle-up", size=10, color="#58a6ff",
                        line=dict(width=1,color="white"))), row=1, col=1)
        xt = pd.to_datetime(trades["exit_time"]); xp = trades["exit_price"]
        wm = trades["win"]
        if wm.any():
            fig.add_trace(go.Scatter(x=xt[wm], y=xp[wm], mode="markers", name="Exit (Win)",
                marker=dict(symbol="triangle-down",size=10,color="#3fb950")), row=1, col=1)
        if (~wm).any():
            fig.add_trace(go.Scatter(x=xt[~wm], y=xp[~wm], mode="markers", name="Exit (Loss)",
                marker=dict(symbol="triangle-down",size=10,color="#f85149")), row=1, col=1)
    ch = ["#3fb950" if v>=0 else "#f85149" for v in df["macd_hist"]]
    fig.add_trace(go.Bar(x=df.index, y=df["macd_hist"], name="Histogram",
                         marker_color=ch, opacity=0.6), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["macd"], name="MACD",
                             line=dict(color="#58a6ff",width=1)), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["macd_signal"], name="Signal",
                             line=dict(color="#f0883e",width=1)), row=2, col=1)
    fig.add_hline(y=0, line_dash="dash", line_color="#8b949e", row=2, col=1)
    fig.update_layout(height=700, template="plotly_dark",
        paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h",yanchor="bottom",y=1.02),
        margin=dict(l=0,r=0,t=10,b=0))
    return fig

def build_equity_chart(trades, stats):
    et = pd.to_datetime(trades["exit_time"])
    fig = make_subplots(rows=2,cols=1,shared_xaxes=True,
                        row_heights=[0.65,0.35],vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=et, y=stats["equity_val"],
        fill="tozeroy", name="Equity (USD)",
        line=dict(color="#3fb950"), fillcolor="rgba(63,185,80,0.08)"
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=et, y=stats["drawdown"],
        fill="tozeroy", name="Drawdown %",
        line=dict(color="#f85149"), fillcolor="rgba(248,81,73,0.15)"
    ), row=2, col=1)
    fig.update_layout(height=420, template="plotly_dark",
        paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        margin=dict(l=0,r=0,t=10,b=0))
    return fig

def build_pnl_dist(trades):
    bins = list(range(-100, 340, 20))
    wins   = trades[trades["win"]]["pnl_pct"]
    losses = trades[~trades["win"]]["pnl_pct"]
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=losses, xbins=dict(start=-100,end=320,size=20),
        name="Loss", marker_color="#f85149", opacity=0.8))
    fig.add_trace(go.Histogram(x=wins, xbins=dict(start=-100,end=320,size=20),
        name="Profit", marker_color="#3fb950", opacity=0.8))
    avg_loss = losses.mean() if len(losses)>0 else 0
    avg_win  = wins.mean()   if len(wins)>0   else 0
    fig.add_vline(x=avg_loss, line_dash="dash", line_color="#f85149",
                  annotation_text=f"Avg loss {avg_loss:.2f}%",
                  annotation_font_color="#f85149")
    fig.add_vline(x=avg_win,  line_dash="dash", line_color="#3fb950",
                  annotation_text=f"Avg profit {avg_win:.2f}%",
                  annotation_font_color="#3fb950")
    fig.update_layout(height=280, template="plotly_dark",
        paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        barmode="overlay", showlegend=True,
        margin=dict(l=0,r=0,t=20,b=0),
        xaxis_title="PnL %", yaxis_title="จำนวน Trade")
    return fig

def build_donut(wins, losses):
    fig = go.Figure(go.Pie(
        labels=["Wins","Losses"],
        values=[wins, losses],
        hole=0.6,
        marker_colors=["#3fb950","#f85149"],
        textinfo="none",
    ))
    fig.update_layout(height=280, template="plotly_dark",
        paper_bgcolor="#0d1117", margin=dict(l=0,r=0,t=20,b=0),
        annotations=[dict(text=f"{wins+losses}<br>Total", x=0.5, y=0.5,
                          font_size=14, showarrow=False, font_color="#e6edf3")])
    return fig

# ─── Metric card ─────────────────────────────────────────────────────────────
def metric(col, label, value, sub="", color=""):
    col.markdown(f"""
    <div class="metric-box">
        <div class="metric-value {color}">{value}</div>
        {"<div style='font-size:0.8rem;color:#3fb950'>"+sub+"</div>" if sub else ""}
        <div class="metric-label">{label}</div>
    </div>""", unsafe_allow_html=True)

# ─── Session state ────────────────────────────────────────────────────────────
for k in ["last_df","last_trades","last_stats","last_config","view_idx","ran_once"]:
    if k not in st.session_state: st.session_state[k] = None

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ ตั้งค่า")

    st.subheader("📌 ทรัพย์สิน")
    asset_name = st.selectbox("เลือกทรัพย์สิน", list(ASSET_LIST.keys()), index=0)
    asset_info = ASSET_LIST[asset_name]
    if asset_info["type"] == "custom":
        custom_symbol = st.text_input("กรอก Ticker (เช่น NVDA, TSLA, 9984.T)", value="NVDA")
        asset_symbol = custom_symbol.upper().strip()
        asset_type   = "stock"
    else:
        asset_symbol = asset_info["symbol"]
        asset_type   = asset_info["type"]

    # 4h/1h สำหรับหุ้นดึงได้แค่ 730 วัน แจ้งเตือน
    st.markdown("---")
    timeframe = st.selectbox("Timeframe", ["1d","1W","3d","4h","1h"], index=0)
    if asset_type == "stock" and timeframe in ["4h","1h"]:
        st.warning("⚠️ หุ้น: Timeframe 1h/4h ดึงข้อมูลได้แค่ 730 วันล่าสุด")
    years = st.slider("ย้อนหลังกี่ปี", 1, 8, 8)
    initial_capital = st.number_input("เงินทุนเริ่มต้น (USD)", value=100000, min_value=1000, step=1000)

    st.markdown("---")
    st.subheader("📊 MACD")
    fast     = st.number_input("Fast",   value=12, min_value=2)
    slow     = st.number_input("Slow",   value=26, min_value=2)
    signal_p = st.number_input("Signal", value=9,  min_value=2)

    st.markdown("---")
    st.subheader("📈 MA Trend Filter")
    use_ma    = st.checkbox("เปิดใช้ MA Trend Filter", value=True)
    ma_period = st.number_input("MA Period", value=128, min_value=2) if use_ma else 128

    run_btn = st.button("🚀 รัน Backtest", use_container_width=True)

    saves = load_saves()
    if saves:
        st.markdown("---")
        st.subheader("💾 ผล Backtest ที่บันทึกไว้")
        for i, s in enumerate(saves):
            c=s["config"]; st_=s["stats"]
            wr_col  = "#3fb950" if st_["winrate"]>=50    else "#f85149"
            ret_col = "#3fb950" if st_["total_return"]>0 else "#f85149"
            ma_tag  = f"MA{c['ma_period']}" if c["use_ma"] else "ไม่ใช้ MA"
            asset_tag = c.get("asset_symbol", "BTC/USDT")
            st.markdown(f"""
            <div class="saved-card">
                <div style="font-size:0.72rem;color:#6e7681;margin-bottom:5px;">#{i+1} {s['time']}</div>
                <span class="tag">{asset_tag}</span>
                <span class="tag">{c['timeframe']}</span>
                <span class="tag">{c['years']}ปี</span>
                <span class="tag">MACD {c['fast']}/{c['slow']}/{c['signal']}</span>
                <span class="tag">{ma_tag}</span>
                <div style="margin-top:7px;display:flex;gap:10px;flex-wrap:wrap;">
                    <span style="color:#8b949e;font-size:0.73rem;">Trade:<b style="color:#e6edf3"> {st_['total_trades']}</b></span>
                    <span style="color:#8b949e;font-size:0.73rem;">WR:<b style="color:{wr_col}"> {st_['winrate']}%</b></span>
                    <span style="color:#8b949e;font-size:0.73rem;">DD:<b style="color:#f85149"> {st_['max_dd']}%</b></span>
                    <span style="color:#8b949e;font-size:0.73rem;">Return:<b style="color:{ret_col}"> {st_['total_return']}%</b></span>
                </div>
            </div>""", unsafe_allow_html=True)
            col_l, col_d = st.columns([3,1])
            with col_l:
                if st.button(f"📂 โหลด #{i+1}", key=f"load_{i}", use_container_width=True):
                    st.session_state["view_idx"] = i
            with col_d:
                if st.button("🗑️", key=f"del_{i}", use_container_width=True):
                    saves.pop(i); write_saves(saves)
                    if st.session_state["view_idx"]==i: st.session_state["view_idx"]=None
                    st.rerun()

# ─── Run backtest ─────────────────────────────────────────────────────────────
if run_btn:
    spinner_msg = f"กำลังดึงข้อมูล {asset_symbol}..."
    with st.spinner(spinner_msg):
        df_raw = fetch_ohlcv(asset_type, asset_symbol, timeframe, years)
    if df_raw.empty:
        st.error(f"ไม่พบข้อมูลสำหรับ {asset_symbol} — ตรวจสอบ ticker อีกครั้ง"); st.stop()
    with st.spinner("คำนวณ MACD และ Signal..."):
        df, trades = compute_signals(df_raw, fast=int(fast), slow=int(slow),
            signal=int(signal_p), use_ma_filter=use_ma, ma_period=int(ma_period))
    stats = calc_stats(trades, initial_capital=int(initial_capital))
    if trades.empty:
        st.warning("ไม่พบ signal ในช่วงเวลาที่เลือก"); st.stop()
    config = {"asset_name": asset_name, "asset_symbol": asset_symbol,
              "asset_type": asset_type,
              "timeframe":timeframe,"years":years,"fast":int(fast),"slow":int(slow),
              "signal":int(signal_p),"use_ma":use_ma,
              "ma_period":int(ma_period) if use_ma else None,
              "initial_capital":int(initial_capital)}
    st.session_state.update({"last_df":df,"last_trades":trades,"last_stats":stats,
                              "last_config":config,"ran_once":True,"view_idx":None})

# ─── Decide what to show ──────────────────────────────────────────────────────
view_idx = st.session_state["view_idx"]
if view_idx is not None:
    saves = load_saves()
    if view_idx < len(saves):
        saved = saves[view_idx]; cfg = saved["config"]
        st.info(f"📂 กำลังแสดงผล Backtest ที่บันทึกไว้ #{view_idx+1} — {saved['time']}")
        with st.spinner("โหลดข้อมูล..."):
            df_s = fetch_ohlcv(
                cfg.get("asset_type","crypto"),
                cfg.get("asset_symbol","BTC/USDT"),
                cfg["timeframe"], cfg["years"])
        df_s, trades_s = compute_signals(df_s, fast=cfg["fast"], slow=cfg["slow"],
            signal=cfg["signal"], use_ma_filter=cfg["use_ma"],
            ma_period=cfg["ma_period"] if cfg["ma_period"] else 128)
        stats_s = calc_stats(trades_s, initial_capital=cfg.get("initial_capital",100000))
        display_df=df_s; display_trades=trades_s; display_stats=stats_s; display_config=cfg
    else:
        st.error("ไม่พบ run นี้"); st.stop()
elif st.session_state["ran_once"]:
    display_df=st.session_state["last_df"]; display_trades=st.session_state["last_trades"]
    display_stats=st.session_state["last_stats"]; display_config=st.session_state["last_config"]
else:
    st.info("👈 ตั้งค่าทางซ้ายแล้วกด **รัน Backtest** ได้เลย")
    st.markdown("""
    **Logic การเทรด (เมื่อเปิด MA Filter)**
    - ✅ Entry = ราคา > MA และ MACD cross up 0
    - ❌ Exit = ราคาหลุดต่ำกว่า MA หรือ MACD cross down 0
    """); st.stop()

cfg=display_config; stats=display_stats; trades=display_trades; df=display_df
cap_usd = cfg.get("initial_capital", 100000)

# ─── Header ───────────────────────────────────────────────────────────────────
asset_label = cfg.get("asset_name", cfg.get("asset_symbol","BTC/USDT"))
st.title(f"📊 {asset_label} — MACD Strategy Report")
date_range = f"{pd.to_datetime(trades['entry_time'].iloc[0]).strftime('%b %d, %Y')} — {pd.to_datetime(trades['exit_time'].iloc[-1]).strftime('%b %d, %Y')}" if not trades.empty else ""
ma_info = f"MA{cfg['ma_period']}" if cfg["use_ma"] else "ไม่ใช้ MA"
st.caption(f"⚙️ {cfg['timeframe']} | {cfg['years']} ปี | MACD {cfg['fast']}/{cfg['slow']}/{cfg['signal']} | {ma_info} | {date_range}")

# Save button
if view_idx is None and st.session_state["ran_once"]:
    saves_now = load_saves()
    if len(saves_now) >= MAX_SAVES:
        st.error(f"⚠️ บันทึกครบ {MAX_SAVES} อันแล้ว กรุณาลบอันเก่าออกก่อน")
    else:
        if st.button("💾 Save ผลนี้"):
            saves_now.insert(0, {"time":datetime.now().strftime("%d/%m/%Y %H:%M"),
                "config":cfg, "stats":{
                    "total_trades":stats["total_trades"],
                    "winrate":round(stats["winrate"],1),
                    "max_lose_streak":stats["max_lose_streak"],
                    "max_win_streak":stats["max_win_streak"],
                    "max_dd":round(stats["max_dd"],1),
                    "total_return":round(stats["total_return"],1),
                    "profit_factor":round(stats["profit_factor"],3),
                    "avg_hold":round(stats["avg_hold"],1),
                    "avg_win":round(stats["avg_win"],2),
                    "avg_loss":round(stats["avg_loss"],2),
                }})
            write_saves(saves_now); st.success("✅ บันทึกแล้ว!"); st.rerun()

st.markdown("---")

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📊 Metrics", "📈 Price Chart", "📋 List of Trades", "🔬 MAE Analysis"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — METRICS
# ══════════════════════════════════════════════════════════════════════════════
with tab1:

    # Top summary
    c1,c2,c3,c4,c5 = st.columns(5)
    ret_clr = "#3fb950" if stats["total_return"]>0  else "#f85149"
    wr_clr  = "#3fb950" if stats["winrate"]>=50     else "#f85149"
    pf_clr  = "#3fb950" if stats["profit_factor"]>1 else "#f85149"
    ret_sub = f"+{stats['total_return']:.2f}%" if stats["total_return"]>0 else f"{stats['total_return']:.2f}%"

    cards = [
        (c1, f"${stats['net_pnl_usd']:,.0f}",     ret_sub,  "Total P&L",           ret_clr),
        (c2, f"${abs(stats['max_dd_usd']):,.0f}",  f"{stats['max_dd']:.2f}%", "Max Equity Drawdown", "#f85149"),
        (c3, str(stats["total_trades"]),            "",       "Total Trades",        "#58a6ff"),
        (c4, f"{stats['winrate']:.2f}%",            f"{stats['winning_trades']}/{stats['total_trades']}", "Profitable Trades", wr_clr),
        (c5, f"{stats['profit_factor']:.3f}",       "",       "Profit Factor",       pf_clr),
    ]
    for col, val, sub, lbl, vc in cards:
        if sub:
            sub_html = "<div style='font-size:0.8rem;color:" + vc + ";opacity:0.85'>" + sub + "</div>"
        else:
            sub_html = ""
        html = (
            "<div class='metric-box'>"
            "<div style='font-size:1.6rem;font-weight:bold;color:" + vc + "'>" + val + "</div>"
            + sub_html +
            "<div style='font-size:0.8rem;color:#8b949e;margin-top:4px'>" + lbl + "</div>"
            "</div>"
        )
        col.markdown(html, unsafe_allow_html=True)

    st.markdown("---")

    # Equity chart
    st.markdown('<div class="section-header">Equity Chart</div>', unsafe_allow_html=True)
    st.plotly_chart(build_equity_chart(trades, stats), use_container_width=True)

    st.markdown("---")

    # Performance — P&L Distribution + Donut
    st.markdown('<div class="section-header">Performance</div>', unsafe_allow_html=True)
    col_dist, col_donut = st.columns([3,2])
    with col_dist:
        st.caption("P&L Distribution")
        st.plotly_chart(build_pnl_dist(trades), use_container_width=True)
    with col_donut:
        st.caption("Win/Loss Ratio")
        st.plotly_chart(build_donut(stats["winning_trades"], stats["losing_trades"]),
                        use_container_width=True)
        wl = stats["winning_trades"]; ll = stats["losing_trades"]; tot = stats["total_trades"]
        st.markdown(f"""
        <div style="font-size:0.82rem;margin-top:8px;">
            <span style="color:#3fb950;">●</span> Wins &nbsp;&nbsp; {wl} trades &nbsp;&nbsp; {wl/tot*100:.2f}%<br>
            <span style="color:#f85149;">●</span> Losses &nbsp; {ll} trades &nbsp;&nbsp; {ll/tot*100:.2f}%<br>
            <span style="color:#d29922;">●</span> Break even &nbsp; 0 trades &nbsp;&nbsp; 0.00%
        </div>""", unsafe_allow_html=True)

    st.markdown("---")

    # Returns table
    st.markdown('<div class="section-header">Returns</div>', unsafe_allow_html=True)
    returns_data = [
        ("Initial capital",         f"${cap_usd:,.2f} USD", "", ""),
        ("Net P&L",                  f"<span class='green'>${stats['net_pnl_usd']:,.2f} USD</span>",
                                     f"<span class='green'>+{stats['total_return']:.2f}%</span>", ""),
        ("Gross profit",             f"{stats['gross_profit_pct']:.2f}%", "", ""),
        ("Gross loss",               f"<span class='red'>{stats['gross_loss_pct']:.2f}%</span>", "", ""),
        ("Profit factor",            f"{stats['profit_factor']:.3f}", "", ""),
        ("Expected payoff (avg P&L)",f"{(stats['total_return']/stats['total_trades']):.2f}%","",""),
    ]
    rows_html = "".join([f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td></tr>" for r in returns_data])
    st.markdown(f"""
    <table class="tv-table">
        <tr><th>Metric</th><th>All</th><th></th></tr>
        {rows_html}
    </table>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Details table
    st.markdown('<div class="section-header">Details</div>', unsafe_allow_html=True)
    details_data = [
        ("Total trades",                  stats["total_trades"],    ""),
        ("Winning trades",                stats["winning_trades"],  ""),
        ("Losing trades",                 stats["losing_trades"],   ""),
        ("Percent profitable",            f"{stats['winrate']:.2f}%",""),
        ("Avg winning trade",             f"{stats['avg_win']:.2f}%",""),
        ("Avg losing trade",              f"<span class='red'>{stats['avg_loss']:.2f}%</span>",""),
        ("Ratio avg win / avg loss",      f"{stats['ratio_win_loss']:.3f}",""),
        ("Largest winning trade",         f"<span class='green'>{stats['largest_win_pct']:.2f}%</span>",""),
        ("Largest losing trade",          f"<span class='red'>{stats['largest_loss_pct']:.2f}%</span>",""),
        ("Max consecutive wins",          stats["max_win_streak"],  ""),
        ("Max consecutive losses",        stats["max_lose_streak"], ""),
        ("Avg # bars in trades",          f"{stats['avg_hold']:.0f}",""),
        ("Avg # bars in winning trades",  f"{stats['avg_hold_win']:.0f}",""),
        ("Avg # bars in losing trades",   f"{stats['avg_hold_loss']:.0f}",""),
        ("Max drawdown",                  f"<span class='red'>{stats['max_dd']:.2f}%</span>",""),
    ]
    rows_html2 = "".join([f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in details_data])
    st.markdown(f"""
    <table class="tv-table">
        <tr><th>Metric</th><th>All</th></tr>
        {rows_html2}
    </table>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PRICE CHART
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.plotly_chart(build_price_chart(df, trades, cfg["use_ma"],
                    cfg["ma_period"] if cfg["ma_period"] else 128,
                    cfg.get("asset_symbol","BTC/USDT")),
                    use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — LIST OF TRADES
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    if not trades.empty:
        display = trades.copy()
        display.index = range(1, len(display)+1)
        display["entry_time"]  = pd.to_datetime(display["entry_time"]).dt.strftime("%b %d, %Y")
        display["exit_time"]   = pd.to_datetime(display["exit_time"]).dt.strftime("%b %d, %Y")
        display["entry_price"] = display["entry_price"].round(2)
        display["exit_price"]  = display["exit_price"].round(2)
        display["pnl_pct"]     = display["pnl_pct"].round(2)
        display["mfe_pct"]     = display["mfe_pct"].round(2)
        display["mae_pct"]     = display["mae_pct"].round(2)

        # ── Filter bar ──────────────────────────────────────────────────────
        f1, f2, f3 = st.columns([2, 2, 3])
        with f1:
            result_filter = st.multiselect(
                "🏆 ผลการเทรด",
                options=["Win", "Loss"],
                default=["Win", "Loss"],
                key="filter_result"
            )
        with f2:
            all_reasons = sorted(display["exit_reason"].unique().tolist())
            reason_filter = st.multiselect(
                "🚪 เหตุผล Exit",
                options=all_reasons,
                default=all_reasons,
                key="filter_reason"
            )
        with f3:
            pnl_range = st.slider(
                "📊 กรอง Net P&L % ระหว่าง",
                min_value=float(display["pnl_pct"].min()),
                max_value=float(display["pnl_pct"].max()),
                value=(float(display["pnl_pct"].min()), float(display["pnl_pct"].max())),
                key="filter_pnl"
            )

        # ── Sort bar ────────────────────────────────────────────────────────
        sort_by_trade = st.radio("เรียงตาม", [
            "ลำดับไม้ (เก่าสุด→ใหม่สุด)",
            "Net P&L % (มากสุดก่อน)",
            "Net P&L % (น้อยสุดก่อน)",
            "Bars (มากสุดก่อน)",
            "Bars (น้อยสุดก่อน)",
            "Win ก่อน",
            "Loss ก่อน",
            "Exit Reason",
        ], horizontal=True, key="trade_sort")

        sort_trade_map = {
            "ลำดับไม้ (เก่าสุด→ใหม่สุด)": ("trade_no_raw", True),
            "Net P&L % (มากสุดก่อน)":      ("pnl_pct",      False),
            "Net P&L % (น้อยสุดก่อน)":     ("pnl_pct",      True),
            "Bars (มากสุดก่อน)":            ("hold_bars",    False),
            "Bars (น้อยสุดก่อน)":           ("hold_bars",    True),
            "Win ก่อน":                     ("win",          False),
            "Loss ก่อน":                    ("win",          True),
            "Exit Reason":                  ("exit_reason",  True),
        }

        # Apply filters
        display["trade_no_raw"] = range(1, len(display)+1)
        mask = (
            display["win"].map({True: "Win", False: "Loss"}).isin(result_filter) &
            display["exit_reason"].isin(reason_filter) &
            display["pnl_pct"].between(pnl_range[0], pnl_range[1])
        )
        filtered = display[mask]

        # Apply sort
        sk, sa = sort_trade_map[sort_by_trade]
        filtered = filtered.sort_values(sk, ascending=sa)

        # Summary stats of filtered
        total_shown = len(filtered)
        wins_shown  = filtered["win"].sum()
        wr_shown    = wins_shown / total_shown * 100 if total_shown > 0 else 0
        st.caption(f"แสดง **{total_shown}** ไม้ จากทั้งหมด {len(display)} ไม้ | Win {wins_shown} ไม้ | Winrate {wr_shown:.1f}%")

        show = filtered[["entry_time","exit_time","entry_price","exit_price",
                          "pnl_pct","hold_bars","mfe_pct","mae_pct","win","exit_reason"]].copy()
        show.columns = ["Entry Date","Exit Date","Entry Price","Exit Price",
                        "Net P&L %","Bars","Max Fav. Exc. %","Max Adv. Exc. %","Win","Exit Reason"]
        show.index = filtered["trade_no_raw"].values
        show.index.name = "Trade #"

        def style_pnl(val):
            return f"color: {'#3fb950' if val>0 else '#f85149'}"

        st.dataframe(
            show.style
                .map(style_pnl, subset=["Net P&L %","Max Adv. Exc. %"])
                .map(lambda v: "color:#3fb950" if v else "color:#f85149", subset=["Win"]),
            use_container_width=True, height=600
        )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MAE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    if trades.empty:
        st.warning("ไม่มีข้อมูล trade")
    else:
        t = trades.copy()
        t["trade_no"] = range(1, len(t)+1)
        t["entry_dt"] = pd.to_datetime(t["entry_time"])
        t["exit_dt"]  = pd.to_datetime(t["exit_time"])
        t["outcome"]  = t["win"].map({True:"Win", False:"Loss"})

        wins_df   = t[t["win"] == True]
        losses_df = t[t["win"] == False]
        total_n   = len(t)

        # ── MAE Summary Table ────────────────────────────────────────────────
        st.markdown('<div class="section-header">MAE Table สรุป</div>', unsafe_allow_html=True)
        st.caption("MAE = % ที่ราคาลงต่ำสุดระหว่างถือ trade นับจาก entry (ค่าติดลบ = ราคาลงจากจุด entry)")

        def mae_worst_trade(df_group):
            if df_group.empty: return "—", "—"
            idx = df_group["mae_pct"].idxmin()
            row = df_group.loc[idx]
            return row["entry_dt"].strftime("%d %b %Y"), f"ไม้ #{int(row['trade_no'])}"

        def mae_90th(df_group):
            if df_group.empty: return "—", "—"
            p90 = np.percentile(df_group["mae_pct"], 10)
            n_within = (df_group["mae_pct"] >= p90).sum()
            return f"{p90:.2f}%", f"{n_within} ไม้ จาก {len(df_group)} ไม้ ({n_within/len(df_group)*100:.0f}%)"

        w_date, w_trade    = mae_worst_trade(wins_df)
        l_date, l_trade    = mae_worst_trade(losses_df)
        w_p90, w_p90_count = mae_90th(wins_df)
        l_p90, l_p90_count = mae_90th(losses_df)

        w_avg = f"{wins_df['mae_pct'].mean():.2f}%"   if not wins_df.empty   else "—"
        l_avg = f"{losses_df['mae_pct'].mean():.2f}%" if not losses_df.empty else "—"
        w_max = f"{wins_df['mae_pct'].min():.2f}%"    if not wins_df.empty   else "—"
        l_max = f"{losses_df['mae_pct'].min():.2f}%"  if not losses_df.empty else "—"

        st.markdown(f"""
        <table class="tv-table">
            <tr>
                <th>Metric</th>
                <th style="color:#3fb950">Win ({len(wins_df)} ไม้)</th>
                <th style="color:#f85149">Loss ({len(losses_df)} ไม้)</th>
            </tr>
            <tr><td>MAE เฉลี่ย</td><td class="red">{w_avg}</td><td class="red">{l_avg}</td></tr>
            <tr><td>MAE ลึกสุด (Worst)</td><td class="red">{w_max}</td><td class="red">{l_max}</td></tr>
            <tr>
                <td>เกิดขึ้นเมื่อ</td>
                <td style="color:#8b949e">{w_date} &nbsp; {w_trade}</td>
                <td style="color:#8b949e">{l_date} &nbsp; {l_trade}</td>
            </tr>
            <tr>
                <td>MAE ที่ 90th percentile<br><span style="font-size:0.72rem;color:#6e7681">(90% ของ trade อยู่เหนือระดับนี้)</span></td>
                <td><span class="red">{w_p90}</span><br><span style="font-size:0.72rem;color:#8b949e">{w_p90_count}</span></td>
                <td><span class="red">{l_p90}</span><br><span style="font-size:0.72rem;color:#8b949e">{l_p90_count}</span></td>
            </tr>
        </table>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── MAE Distribution Chart ───────────────────────────────────────────
        st.markdown('<div class="section-header">MAE Distribution — Win vs Loss</div>', unsafe_allow_html=True)
        st.caption("จุดที่สองกลุ่มแยกกันชัดคือ SL ที่เหมาะสม — เส้นประสีเขียวคือ Win 90th percentile")

        fig_mae = go.Figure()
        if not wins_df.empty:
            fig_mae.add_trace(go.Histogram(x=wins_df["mae_pct"], name="Win",
                marker_color="#3fb950", opacity=0.75, xbins=dict(size=2)))
        if not losses_df.empty:
            fig_mae.add_trace(go.Histogram(x=losses_df["mae_pct"], name="Loss",
                marker_color="#f85149", opacity=0.75, xbins=dict(size=2)))
        if not wins_df.empty:
            p90_win = np.percentile(wins_df["mae_pct"], 10)
            fig_mae.add_vline(x=p90_win, line_dash="dash", line_color="#3fb950",
                annotation_text=f"Win 90th pct: {p90_win:.1f}%",
                annotation_font_color="#3fb950")
        fig_mae.update_layout(height=320, template="plotly_dark",
            paper_bgcolor="#0d1117", plot_bgcolor="#161b22", barmode="overlay",
            xaxis_title="MAE %", yaxis_title="จำนวน Trade",
            margin=dict(l=0,r=0,t=20,b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_mae, use_container_width=True)

        st.markdown("---")

        # ── SL Simulator ────────────────────────────────────────────────────
        st.markdown('<div class="section-header">🎯 SL Simulator</div>', unsafe_allow_html=True)
        st.caption("จำลองว่าถ้าตั้ง Stop Loss ที่ % นี้ Winrate และ Return จะเปลี่ยนอย่างไร")

        sl_pct = st.slider("ตั้ง Stop Loss ที่ (-%)", min_value=1.0, max_value=50.0,
                            value=10.0, step=0.5)

        sim = t.copy()
        sim["sl_triggered"] = sim["mae_pct"] <= -sl_pct
        sim["sim_pnl"]      = np.where(sim["sl_triggered"], -sl_pct, sim["pnl_pct"])
        sim["sim_win"]      = sim["sim_pnl"] > 0

        sim_wins   = sim[sim["sim_win"]]
        sim_wr     = len(sim_wins) / len(sim) * 100
        sim_equity = (1 + sim["sim_pnl"]/100).cumprod()
        sim_return = (sim_equity.iloc[-1] - 1) * 100
        sim_dd     = ((sim_equity - sim_equity.cummax()) / sim_equity.cummax() * 100).min()
        sl_hit_n   = sim["sl_triggered"].sum()

        sc1,sc2,sc3,sc4,sc5 = st.columns(5)
        sl_color   = "color:#d29922"
        hit_color  = "color:#f85149" if sl_hit_n>0 else "color:#3fb950"
        wr_color   = "color:#3fb950" if sim_wr>=50 else "color:#f85149"
        ret_color  = "color:#3fb950" if sim_return>0 else "color:#f85149"
        for col, val, lbl, clr in [
            (sc1, f"-{sl_pct}%",         "SL ที่ตั้ง",            sl_color),
            (sc2, f"{sl_hit_n} ไม้",     "SL โดนกี่ครั้ง",        hit_color),
            (sc3, f"{sim_wr:.1f}%",      "Winrate (หลัง SL)",     wr_color),
            (sc4, f"{sim_return:.1f}%",  "Total Return (หลัง SL)", ret_color),
            (sc5, f"{sim_dd:.1f}%",      "Max Drawdown (หลัง SL)", "color:#f85149"),
        ]:
            col.markdown(f"""
            <div class="metric-box">
                <div class="metric-value" style="{clr}">{val}</div>
                <div class="metric-label">{lbl}</div>
            </div>""", unsafe_allow_html=True)

        orig_wr = stats["winrate"]; orig_return = stats["total_return"]; orig_dd = stats["max_dd"]

        def delta_color(new, old, higher_better=True):
            diff = new - old
            if abs(diff) < 0.01: return "#8b949e", "±0"
            good  = diff > 0 if higher_better else diff < 0
            color = "#3fb950" if good else "#f85149"
            return color, f"{'+'if diff>0 else ''}{diff:.1f}"

        wr_col,  wr_d  = delta_color(sim_wr,    orig_wr,    True)
        ret_col, ret_d = delta_color(sim_return, orig_return, True)
        dd_col,  dd_d  = delta_color(sim_dd,     orig_dd,    False)

        st.markdown(f"""
        <br>
        <table class="tv-table">
            <tr><th>Metric</th><th>ไม่มี SL (เดิม)</th><th>ใช้ SL -{sl_pct}%</th><th>เปลี่ยนแปลง</th></tr>
            <tr><td>Winrate</td><td>{orig_wr:.1f}%</td><td>{sim_wr:.1f}%</td>
                <td style="color:{wr_col}">{wr_d}%</td></tr>
            <tr><td>Total Return</td><td>{orig_return:.1f}%</td><td>{sim_return:.1f}%</td>
                <td style="color:{ret_col}">{ret_d}%</td></tr>
            <tr><td>Max Drawdown</td><td>{orig_dd:.1f}%</td><td>{sim_dd:.1f}%</td>
                <td style="color:{dd_col}">{dd_d}%</td></tr>
            <tr><td>SL โดน</td><td>0 ครั้ง</td>
                <td>{sl_hit_n} ครั้ง ({sl_hit_n/total_n*100:.1f}%)</td>
                <td style="color:#8b949e">—</td></tr>
        </table>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── MAE รายไม้ ───────────────────────────────────────────────────────
        st.markdown('<div class="section-header">MAE รายละเอียดทุก Trade</div>', unsafe_allow_html=True)

        sort_col, order_col = st.columns([3, 2])
        with sort_col:
            sort_by = st.radio("เรียงตาม", [
                "ลำดับไม้การเทรด",
                "MAE % (ลึกสุดก่อน)",
                "MAE % (น้อยสุดก่อน)",
                "ผลการเทรด (Win ก่อน)",
                "ผลการเทรด (Loss ก่อน)",
                "PnL % (มากสุดก่อน)",
                "PnL % (น้อยสุดก่อน)",
            ], horizontal=True, key="mae_sort")

        mae_detail = t[["trade_no","entry_dt","exit_dt","entry_price","exit_price",
                         "pnl_pct","mae_pct","mfe_pct","outcome"]].copy()

        sort_map = {
            "ลำดับไม้การเทรด":       ("trade_no",  True),
            "MAE % (ลึกสุดก่อน)":    ("mae_pct",   True),
            "MAE % (น้อยสุดก่อน)":   ("mae_pct",   False),
            "ผลการเทรด (Win ก่อน)":  ("outcome",   False),
            "ผลการเทรด (Loss ก่อน)": ("outcome",   True),
            "PnL % (มากสุดก่อน)":    ("pnl_pct",   False),
            "PnL % (น้อยสุดก่อน)":   ("pnl_pct",   True),
        }
        sort_key, asc = sort_map[sort_by]
        mae_detail = mae_detail.sort_values(sort_key, ascending=asc)

        mae_detail["entry_dt"]    = mae_detail["entry_dt"].dt.strftime("%d %b %Y")
        mae_detail["exit_dt"]     = mae_detail["exit_dt"].dt.strftime("%d %b %Y")
        mae_detail["entry_price"] = mae_detail["entry_price"].round(2)
        mae_detail["exit_price"]  = mae_detail["exit_price"].round(2)
        mae_detail["pnl_pct"]     = mae_detail["pnl_pct"].round(2)
        mae_detail["mae_pct"]     = mae_detail["mae_pct"].round(2)
        mae_detail["mfe_pct"]     = mae_detail["mfe_pct"].round(2)
        mae_detail.columns = ["ไม้ #","Entry","Exit","Entry Price","Exit Price",
                               "PnL %","MAE %","MFE %","ผล"]
        mae_detail = mae_detail.set_index("ไม้ #")

        def style_mae(val):
            return f"color: {'#f85149' if val < 0 else '#3fb950'}"
        def style_outcome(val):
            return "color:#3fb950" if val=="Win" else "color:#f85149"

        st.dataframe(
            mae_detail.style
                .map(style_mae,     subset=["MAE %","MFE %","PnL %"])
                .map(style_outcome, subset=["ผล"]),
            use_container_width=True, height=500
        )
