import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import json
import os
from datetime import datetime, date
from strategy import apply_strategy, strategies, stock_list
from database import load_stock_prices, save_stock_prices
from risk import apply_friction_and_risk, build_risk_ui

st.title("🔔 策略訊號推播")
st.caption("設定監控清單與策略，手動觸發或每日定時推播買賣訊號至 Line Notify 或 Email。")

# =====================
# 設定檔路徑
# =====================
_IS_CLOUD    = os.path.exists("/mount/src")
_DB_DIR      = "/tmp" if _IS_CLOUD else "."
MONITOR_FILE = os.path.join(_DB_DIR, "user_monitor.json")

# =====================
# 監控設定讀寫
# =====================
def load_monitor():
    if os.path.exists(MONITOR_FILE):
        try:
            with open(MONITOR_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_monitor(data: dict):
    with open(MONITOR_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =====================
# 左側 Sidebar：推播設定
# =====================
with st.sidebar:
    st.markdown("## 📡 推播設定")

    st.markdown("### Line Notify")
    st.markdown(
        "請至 [Line Notify](https://notify-bot.line.me/my/) 申請 Token，"
        "加入自己的群組或 1對1 聊天。"
    )
    line_token = st.text_input(
        "Line Notify Token",
        type="password",
        placeholder="貼上 Token...",
        help="Token 僅在此次 session 使用"
    )
    if line_token:
        st.success("✅ Line Token 已輸入")

    st.markdown("---")
    st.markdown("### Email（選填）")
    email_addr = st.text_input(
        "收件 Email",
        placeholder="your@email.com",
        help="目前版本透過 Gmail SMTP 發送，需在程式碼設定寄件帳號"
    )
    st.info("📌 Email 功能需自行設定 SMTP，Line Notify 較簡便。")

# =====================
# 抓股價（共用）
# =====================
def fetch_price(stock_code, days=120):
    """抓最近 N 天股價，先從 DB，沒有就從網路"""
    end   = date.today()
    start = pd.Timestamp(end) - pd.Timedelta(days=days)
    df    = load_stock_prices(stock_code, start.date(), end)
    if not df.empty:
        df.index = pd.to_datetime(df.index)
        return df
    try:
        df = yf.download(stock_code, start=start, end=end, auto_adjust=False, progress=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        df.reset_index(inplace=True)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        save_stock_prices(df, stock_code)
        return df
    except Exception:
        return pd.DataFrame()

def get_signal(stock_code, strategy_name, params, risk_cfg=None):
    """
    取得最新訊號：
    回傳 dict：signal（持有/買入/空手）、last_date、close、pnl_unrealized
    """
    df = fetch_price(stock_code)
    if df.empty or 'Close' not in df.columns:
        return None

    df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    df = df[df['Close'].notna()].sort_index()

    try:
        df_s = apply_strategy(df, strategy_name, params)
    except Exception:
        return None

    if risk_cfg:
        df_s = apply_friction_and_risk(df_s, **risk_cfg)

    pos_col = 'Position_adj' if 'Position_adj' in df_s.columns else 'Position'
    last_pos  = int(df_s[pos_col].iloc[-1])
    last_date = df_s.index[-1].date()
    last_close = float(df_s['Close'].iloc[-1])

    signal_text = "🟡 空手" if last_pos == 0 else ("🟢 持有（買入）" if last_pos == 1 else "🔴 放空")

    # 找最近買入點計算未實現損益
    buy_dates = df_s[df_s[pos_col].diff() == 1].index
    unrealized = None
    entry_price = None
    if last_pos == 1 and len(buy_dates) > 0:
        entry_price  = float(df_s.loc[buy_dates[-1], 'Close'])
        unrealized   = (last_close - entry_price) / entry_price * 100

    return {
        "stock_code":   stock_code,
        "stock_name":   stock_list.get(stock_code, stock_code),
        "strategy":     strategy_name,
        "signal":       signal_text,
        "last_pos":     last_pos,
        "last_date":    str(last_date),
        "close":        last_close,
        "entry_price":  entry_price,
        "unrealized":   unrealized,
    }

# =====================
# Line Notify 推播
# =====================
def send_line_notify(token: str, message: str) -> bool:
    try:
        resp = requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {token}"},
            data={"message": message},
            timeout=10
        )
        return resp.status_code == 200
    except Exception:
        return False

def build_notify_message(results: list, check_time: str) -> str:
    lines = [f"\n📊 台股策略訊號報告\n🕐 {check_time}\n{'─'*25}"]
    for r in results:
        lines.append(
            f"\n{r['stock_name']}（{r['stock_code']}）"
            f"\n策略：{r['strategy']}"
            f"\n訊號：{r['signal']}"
            f"\n收盤：{r['close']:.2f}"
        )
        if r['unrealized'] is not None:
            pnl_str = f"+{r['unrealized']:.2f}%" if r['unrealized'] >= 0 else f"{r['unrealized']:.2f}%"
            lines.append(f"未實現損益：{pnl_str}")
        lines.append("─" * 25)
    return "\n".join(lines)

# =====================
# 監控清單設定 UI
# =====================
st.markdown("## 📋 監控清單設定")

monitor_data = load_monitor()
stock_options = [f"{name} ({code})" for code, name in stock_list.items()]

# 載入上次設定
saved_stocks     = monitor_data.get("stocks", [])
saved_strategy   = monitor_data.get("strategy", list(strategies.keys())[0])
saved_params     = monitor_data.get("params", {})

# 過濾掉已不在清單中的選項
saved_stocks = [s for s in saved_stocks if s in stock_options]

col1, col2 = st.columns(2)
with col1:
    monitor_stocks = st.multiselect(
        "監控股票清單（可多選）",
        stock_options,
        default=saved_stocks if saved_stocks else stock_options[:3],
        help="加入要每日監控的股票"
    )
with col2:
    monitor_strategy = st.selectbox(
        "監控策略",
        list(strategies.keys()),
        index=list(strategies.keys()).index(saved_strategy) if saved_strategy in strategies else 0
    )

st.caption(strategies[monitor_strategy]["description"])

# 策略參數
st.markdown("#### 策略參數")
monitor_params = {}
param_cols = st.columns(min(len(strategies[monitor_strategy]["parameters"]), 4))
for i, (param, default) in enumerate(strategies[monitor_strategy]["parameters"].items()):
    saved_val = saved_params.get(param, default)
    with param_cols[i % len(param_cols)]:
        if isinstance(default, int):
            monitor_params[param] = st.number_input(
                param, value=int(saved_val), step=1, key=f"mp_{param}"
            )
        elif isinstance(default, float):
            monitor_params[param] = st.number_input(
                param, value=float(saved_val), format="%.2f", key=f"mp_{param}"
            )
        else:
            monitor_params[param] = st.text_input(param, value=str(saved_val), key=f"mp_{param}")

# 摩擦成本設定
risk_cfg = build_risk_ui(prefix="notify_", market="stock")

# 儲存監控設定
if st.button("💾 儲存監控設定"):
    save_monitor({
        "stocks":   monitor_stocks,
        "strategy": monitor_strategy,
        "params":   monitor_params,
    })
    st.success("✅ 監控設定已儲存")

st.markdown("---")

# =====================
# 手動觸發訊號檢查
# =====================
st.markdown("## 🔍 立即檢查訊號")

monitor_codes = [s.split("(")[-1].strip(")") for s in monitor_stocks]

if st.button("🔍 立即檢查所有股票訊號", type="primary"):
    if not monitor_codes:
        st.warning("請先設定監控股票清單")
        st.stop()

    results     = []
    buy_list    = []
    sell_list   = []
    hold_list   = []

    with st.spinner("檢查中..."):
        for code in monitor_codes:
            r = get_signal(code, monitor_strategy, monitor_params, risk_cfg)
            if r is None:
                st.warning(f"⚠️ {code} 無法取得訊號，跳過")
                continue
            results.append(r)
            if r['last_pos'] == 1:
                hold_list.append(r)
            else:
                sell_list.append(r) if r['last_pos'] == -1 else buy_list.append(r)

    if not results:
        st.error("❌ 無法取得任何股票訊號")
        st.stop()

    # ── 訊號摘要 ──
    check_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    st.markdown(f"### 📊 訊號摘要（{check_time}）")

    c1, c2, c3 = st.columns(3)
    c1.metric("🟢 持有中",  f"{len(hold_list)} 支")
    c2.metric("🟡 空手",    f"{len(buy_list)} 支")
    c3.metric("🔴 放空",    f"{len(sell_list)} 支")

    # ── 詳細訊號表 ──
    st.markdown("### 📋 詳細訊號")
    df_signals = pd.DataFrame([{
        "股票名稱":    r["stock_name"],
        "股票代號":    r["stock_code"],
        "訊號":        r["signal"],
        "收盤價":      r["close"],
        "買入成本":    f"{r['entry_price']:.2f}" if r['entry_price'] else "—",
        "未實現損益":  f"{r['unrealized']:+.2f}%" if r['unrealized'] is not None else "—",
        "資料日期":    r["last_date"],
    } for r in results])

    st.dataframe(df_signals, use_container_width=True)

    # ── 持倉中股票詳情 ──
    if hold_list:
        st.markdown("### 🟢 目前持倉股票")
        for r in hold_list:
            pnl = r['unrealized']
            pnl_str = f"{pnl:+.2f}%" if pnl is not None else "—"
            pnl_color = "🟢" if (pnl or 0) >= 0 else "🔴"
            st.info(
                f"**{r['stock_name']}（{r['stock_code']}）**　"
                f"收盤：{r['close']:.2f}　"
                f"買入成本：{r['entry_price']:.2f if r['entry_price'] else '—'}　"
                f"未實現損益：{pnl_color} {pnl_str}"
            )

    # ── Line Notify 推播 ──
    st.markdown("### 📡 推播通知")
    if line_token:
        if st.button("📲 發送 Line Notify"):
            message = build_notify_message(results, check_time)
            ok = send_line_notify(line_token, message)
            if ok:
                st.success("✅ Line Notify 推播成功！")
            else:
                st.error("❌ 推播失敗，請確認 Token 是否正確")

        # 預覽訊息內容
        with st.expander("👁️ 預覽推播內容"):
            st.text(build_notify_message(results, check_time))
    else:
        st.info("👈 在左側輸入 Line Notify Token 即可推播訊號")

    # ── Email（簡易版，需自行設定 SMTP）──
    if email_addr:
        st.info(
            f"📧 Email 推播目標：{email_addr}  \n"
            "目前版本 Email 推播需自行在程式碼中設定 SMTP 帳號密碼，"
            "建議改用 Line Notify 較簡便。"
        )

# =====================
# 自動排程說明
# =====================
st.markdown("---")
st.markdown("## ⏰ 自動排程設定")
st.info(
    "**Streamlit Cloud 不支援背景排程**，如需每日自動推播，有以下兩個方案：\n\n"
    "**方案 A — GitHub Actions**（推薦）：\n"
    "在 `.github/workflows/notify.yml` 設定每日定時執行一個獨立的 Python 腳本，"
    "腳本讀取監控設定並呼叫 Line Notify API，不依賴 Streamlit。\n\n"
    "**方案 B — 本機排程**：\n"
    "在本機使用 Windows 工作排程器或 cron，每日執行 `python notify_job.py`。"
)

with st.expander("📄 GitHub Actions 設定範例（.github/workflows/notify.yml）"):
    st.code("""
name: Daily Signal Notify

on:
  schedule:
    - cron: '30 7 * * 1-5'   # 台灣時間 15:30（收盤後），週一至週五
  workflow_dispatch:           # 允許手動觸發

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install pandas yfinance requests
      - run: python notify_job.py
        env:
          LINE_TOKEN: ${{ secrets.LINE_TOKEN }}
""", language="yaml")

with st.expander("📄 notify_job.py 獨立腳本範例"):
    st.code("""
# notify_job.py - 獨立執行，不依賴 Streamlit
import os, json, requests, pandas as pd, yfinance as yf
from strategy import apply_strategy, strategies, stock_list

LINE_TOKEN = os.environ.get("LINE_TOKEN", "")
MONITOR_FILE = "user_monitor.json"

def main():
    if not os.path.exists(MONITOR_FILE):
        print("無監控設定，結束")
        return

    with open(MONITOR_FILE) as f:
        cfg = json.load(f)

    stocks   = [s.split("(")[-1].strip(")") for s in cfg.get("stocks", [])]
    strategy = cfg.get("strategy", "MACD 策略")
    params   = cfg.get("params", strategies[strategy]["parameters"])

    lines = ["\\n📊 台股策略訊號（每日自動）"]
    for code in stocks:
        df = yf.download(code, period="3mo", auto_adjust=False, progress=False)
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        df_s = apply_strategy(df, strategy, params)
        pos  = int(df_s['Position'].iloc[-1])
        sig  = "🟢 持有" if pos == 1 else "🟡 空手"
        name = stock_list.get(code, code)
        close = float(df_s['Close'].iloc[-1])
        lines.append(f"\\n{name}（{code}）")
        lines.append(f"訊號：{sig}　收盤：{close:.2f}")

    msg = "\\n".join(lines)
    requests.post(
        "https://notify-api.line.me/api/notify",
        headers={"Authorization": f"Bearer {LINE_TOKEN}"},
        data={"message": msg}
    )
    print("推播完成")

if __name__ == "__main__":
    main()
""", language="python")
