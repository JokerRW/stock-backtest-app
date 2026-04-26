import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objs as go
import plotly.express as px
from itertools import product
from strategy import apply_strategy, strategies
from risk import apply_friction_and_risk, calc_performance, build_risk_ui
import ccxt
import time
import os
import json

st.title("💰 虛擬幣策略回測系統")

TRADING_DAYS    = 365
BEST_PARAM_FILE = "user_best_params.json"   # 與台股回測共用同一個檔案

# =====================
# 最佳參數讀寫工具（與台股回測共用）
# =====================
def load_best_params():
    if os.path.exists(BEST_PARAM_FILE):
        try:
            with open(BEST_PARAM_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_best_params(symbol, strategy_name, params, metrics):
    data = load_best_params()
    key  = f"{symbol}_{strategy_name}"
    data[key] = {
        "stock_code":    symbol,
        "strategy_name": strategy_name,
        "params":        params,
        "metrics":       metrics,
        "saved_at":      pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
    }
    with open(BEST_PARAM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =====================
# SMA/Hull 趨勢策略（虛擬幣專屬）
# =====================
def sma_hull_trend_strategy(df, params):
    type_ = params.get("type", "sma")
    n1    = int(params.get("n1", 30))
    n2    = int(params.get("n2", 130))
    df    = df.copy()
    if type_ == "sma":
        df['trend1'] = df['Close'].rolling(n1).mean()
        df['trend2'] = df['Close'].rolling(n2).mean()
    elif type_ == "hull":
        def WMA(series, n):
            weights = pd.Series(range(1, n + 1))
            return series.rolling(n).apply(lambda x: (x * weights).sum() / weights.sum(), raw=True)
        half1 = int(n1 / 2)
        df['trend1'] = WMA(2 * WMA(df['Close'], half1) - WMA(df['Close'], n1), int(n1 ** 0.5))
        half2 = int(n2 / 2)
        df['trend2'] = WMA(2 * WMA(df['Close'], half2) - WMA(df['Close'], n2), int(n2 ** 0.5))
    else:
        df['trend1'] = df['Close']
        df['trend2'] = df['Close']

    buy  = (df['trend1'] > df['trend2']) & (df['trend1'].shift(1) <= df['trend2'].shift(1))
    sell = (df['trend1'] < df['trend2']) & (df['trend1'].shift(1) >= df['trend2'].shift(1))
    position, positions = 0, []
    for i in range(len(df)):
        if sell.iloc[i]:   position = 0
        elif buy.iloc[i]:  position = 1
        positions.append(position)
    df['Position'] = positions
    return df

# =====================
# 新增虛擬幣專屬策略
# 根據 2025 年最熱門的量化策略研究整理
# =====================

def supertrend_strategy(df, params):
    """
    Supertrend 策略:結合 ATR 的趨勢追蹤指標。
    當收盤價在 Supertrend 線上方時持有，跌破時出場。
    2025 年虛擬幣最廣泛使用的趨勢策略之一。
    """
    period = int(params.get("ATR 週期", 10))
    mult   = float(params.get("ATR 倍數", 3.0))
    df     = df.copy()

    # 計算 ATR
    hl    = df['High'] - df['Low']
    hc    = (df['High'] - df['Close'].shift(1)).abs()
    lc    = (df['Low']  - df['Close'].shift(1)).abs()
    tr    = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr   = tr.rolling(period).mean()

    # 計算基礎上下軌
    hl2        = (df['High'] + df['Low']) / 2
    upper_band = hl2 + mult * atr
    lower_band = hl2 - mult * atr

    # 動態調整軌道（Supertrend 核心）
    supertrend = pd.Series(index=df.index, dtype=float)
    direction  = pd.Series(index=df.index, dtype=int)

    for i in range(1, len(df)):
        # 上軌
        if upper_band.iloc[i] < upper_band.iloc[i-1] or df['Close'].iloc[i-1] > upper_band.iloc[i-1]:
            upper_band.iloc[i] = upper_band.iloc[i]
        else:
            upper_band.iloc[i] = upper_band.iloc[i-1]
        # 下軌
        if lower_band.iloc[i] > lower_band.iloc[i-1] or df['Close'].iloc[i-1] < lower_band.iloc[i-1]:
            lower_band.iloc[i] = lower_band.iloc[i]
        else:
            lower_band.iloc[i] = lower_band.iloc[i-1]

    # 決定 Supertrend 方向
    st_line = lower_band.copy()
    dir_arr = pd.Series(1, index=df.index)
    for i in range(1, len(df)):
        if df['Close'].iloc[i] > upper_band.iloc[i]:
            dir_arr.iloc[i] = 1
        elif df['Close'].iloc[i] < lower_band.iloc[i]:
            dir_arr.iloc[i] = -1
        else:
            dir_arr.iloc[i] = dir_arr.iloc[i-1]

    buy  = (dir_arr == 1) & (dir_arr.shift(1) == -1)
    sell = (dir_arr == -1) & (dir_arr.shift(1) == 1)

    position, positions = 0, []
    for i in range(len(df)):
        if sell.iloc[i]:  position = 0
        elif buy.iloc[i]: position = 1
        positions.append(position)
    df['Position'] = positions
    return df


def stoch_rsi_strategy(df, params):
    """
    Stochastic RSI 策略:對 RSI 再做隨機指標計算。
    對超買超賣更敏感，適合虛擬幣高波動環境。
    %K 穿越 %D 且在低位（<20）時買入，高位（>80）時賣出。
    """
    rsi_period   = int(params.get("RSI 週期", 14))
    stoch_period = int(params.get("Stoch 週期", 14))
    k_period     = int(params.get("%K 平滑", 3))
    d_period     = int(params.get("%D 平滑", 3))
    buy_level    = float(params.get("買入閾值", 20))
    sell_level   = float(params.get("賣出閾值", 80))
    df           = df.copy()

    # RSI
    delta    = df['Close'].diff()
    gain     = delta.clip(lower=0).rolling(rsi_period).mean()
    loss     = (-delta.clip(upper=0)).rolling(rsi_period).mean()
    rs       = gain / loss
    rsi      = 100 - (100 / (1 + rs))

    # Stochastic of RSI
    rsi_min  = rsi.rolling(stoch_period).min()
    rsi_max  = rsi.rolling(stoch_period).max()
    stoch    = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10) * 100
    k        = stoch.rolling(k_period).mean()
    d        = k.rolling(d_period).mean()

    # 訊號：%K 穿越 %D 且在低/高區域
    buy  = (k > d) & (k.shift(1) <= d.shift(1)) & (k < buy_level)
    sell = (k < d) & (k.shift(1) >= d.shift(1)) & (k > sell_level)

    position, positions = 0, []
    for i in range(len(df)):
        if sell.iloc[i]:  position = 0
        elif buy.iloc[i]: position = 1
        positions.append(position)
    df['Position'] = positions
    return df


def atr_breakout_strategy(df, params):
    """
    ATR 波動突破策略:當價格突破前 N 日收盤均值 + ATR 倍數時買入。
    根據市場波動自動調整突破門檻，適合高波動的虛擬幣市場。
    """
    period = int(params.get("均線週期", 20))
    atr_p  = int(params.get("ATR 週期", 14))
    mult   = float(params.get("突破倍數", 1.5))
    df     = df.copy()

    ma  = df['Close'].rolling(period).mean()
    hl  = df['High'] - df['Low']
    hc  = (df['High'] - df['Close'].shift(1)).abs()
    lc  = (df['Low']  - df['Close'].shift(1)).abs()
    atr = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(atr_p).mean()

    upper = ma + mult * atr
    lower = ma - mult * atr

    buy  = (df['Close'] > upper.shift(1)).fillna(False)
    sell = (df['Close'] < lower.shift(1)).fillna(False)

    position, positions = 0, []
    for i in range(len(df)):
        if sell.iloc[i]:  position = 0
        elif buy.iloc[i]: position = 1
        positions.append(position)
    df['Position'] = positions
    return df


def dca_strategy(df, params):
    """
    DCA 定期買入策略（Dollar Cost Averaging）:
    每隔固定天數買入一次，持有到下次買入（不主動賣出）。
    適合長線看多、不想擇時的投資者。
    """
    interval = int(params.get("買入間隔（天）", 7))
    df = df.copy()

    positions = []
    for i in range(len(df)):
        if i % interval == 0:
            positions.append(1)
        else:
            positions.append(positions[-1] if positions else 0)
    df['Position'] = positions
    return df


def adx_trend_strategy(df, params):
    """
    ADX 趨勢強度過濾策略:
    用 ADX 判斷趨勢強度，只在趨勢明確（ADX > 閾值）時配合均線方向進場。
    ADX < 閾值時不進場（市場盤整），有效過濾震盪行情。
    """
    adx_period    = int(params.get("ADX 週期", 14))
    adx_threshold = float(params.get("ADX 閾值", 25))
    ma_period     = int(params.get("均線週期", 20))
    df            = df.copy()

    # 計算 ADX
    high, low, close = df['High'], df['Low'], df['Close']
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    atr      = tr.rolling(adx_period).mean()
    plus_di  = 100 * pd.Series(plus_dm,  index=df.index).rolling(adx_period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(adx_period).mean() / atr
    dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
    adx      = dx.rolling(adx_period).mean()

    ma = close.rolling(ma_period).mean()

    # 趨勢夠強（ADX > 閾值）且在均線上方才買入
    buy  = ((adx > adx_threshold) & (close > ma) & (close.shift(1) <= ma.shift(1))).fillna(False)
    sell = ((close < ma) & (close.shift(1) >= ma.shift(1))).fillna(False)

    position, positions = 0, []
    for i in range(len(df)):
        if sell.iloc[i]:  position = 0
        elif buy.iloc[i]: position = 1
        positions.append(position)
    df['Position'] = positions
    return df


def vwap_strategy(df, params):
    """
    VWAP 均值回歸策略（Volume Weighted Average Price）:
    結合成交量的加權均價，機構廣泛使用。
    收盤價從 VWAP 下方回升穿越時買入，跌破時賣出。
    """
    period = int(params.get("VWAP 週期", 20))
    df     = df.copy()

    # 滾動 VWAP
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    vwap = (typical_price * df['Volume']).rolling(period).sum() / df['Volume'].rolling(period).sum()

    buy  = ((df['Close'] > vwap) & (df['Close'].shift(1) <= vwap.shift(1))).fillna(False)
    sell = ((df['Close'] < vwap) & (df['Close'].shift(1) >= vwap.shift(1))).fillna(False)

    position, positions = 0, []
    for i in range(len(df)):
        if sell.iloc[i]:  position = 0
        elif buy.iloc[i]: position = 1
        positions.append(position)
    df['Position'] = positions
    return df


strategies.update({
    "SMA/Hull 趨勢策略": {
        "description": "短期均線與長期均線交叉策略 (SMA/HullMA)，可選 SMA 或 Hull 移動平均",
        "parameters":  {"type": "sma", "n1": 30, "n2": 130},
        "function":    sma_hull_trend_strategy
    },
    "Supertrend 策略": {
        "description": "ATR 基礎趨勢追蹤指標，收盤價突破 Supertrend 線時進出場。2025 年虛擬幣最熱門策略之一。",
        "parameters":  {"ATR 週期": 10, "ATR 倍數": 3.0},
        "function":    supertrend_strategy
    },
    "Stochastic RSI 策略": {
        "description": "對 RSI 再做隨機指標，對超買超賣更敏感。%K 穿越 %D 且在低位買入、高位賣出。",
        "parameters":  {"RSI 週期": 14, "Stoch 週期": 14, "%K 平滑": 3, "%D 平滑": 3, "買入閾值": 20.0, "賣出閾值": 80.0},
        "function":    stoch_rsi_strategy
    },
    "ATR 波動突破策略": {
        "description": "根據 ATR 市場波動自動調整突破門檻，高波動時門檻更高，適合虛擬幣高波動環境。",
        "parameters":  {"均線週期": 20, "ATR 週期": 14, "突破倍數": 1.5},
        "function":    atr_breakout_strategy
    },
    "DCA 定期買入策略": {
        "description": "每隔固定天數買入並持有（Dollar Cost Averaging），不主動賣出，適合長線看多。",
        "parameters":  {"買入間隔（天）": 7},
        "function":    dca_strategy
    },
    "ADX 趨勢強度過濾策略": {
        "description": "ADX > 閾值時確認趨勢明確，配合均線方向進場。盤整期不交易，有效降低假訊號。",
        "parameters":  {"ADX 週期": 14, "ADX 閾值": 25.0, "均線週期": 20},
        "function":    adx_trend_strategy
    },
    "VWAP 均值回歸策略": {
        "description": "成交量加權均價（機構廣泛使用），收盤價從 VWAP 下方穿越時買入，跌破時賣出。",
        "parameters":  {"VWAP 週期": 20},
        "function":    vwap_strategy
    },
})

# =====================
# 幣種清單
# =====================
crypto_list = {
    "BTC/USDT":  "比特幣 (BTC)",
    "ETH/USDT":  "以太坊 (ETH)",
    "XRP/USDT":  "瑞波幣 (XRP)",
    "ADA/USDT":  "艾達幣 (ADA)",
    "LINK/USDT": "Chainlink (LINK)",
    "VET/USDT":  "唯鏈 (VET)",
    "DOGE/USDT": "狗狗幣 (DOGE)",
    "ETH/BTC":   "以太坊 (ETH/BTC)",
    "XRP/BTC":   "瑞波幣 (XRP/BTC)",
    "ADA/BTC":   "艾達幣 (ADA/BTC)",
    "LINK/BTC":  "Chainlink (LINK/BTC)",
    "VET/BTC":   "唯鏈 (VET/BTC)",
    "DOGE/BTC":  "狗狗幣 (DOGE/BTC)",
}

# =====================
# UI 選項
# =====================
crypto_options   = [f"{name} ({code})" for code, name in crypto_list.items()]
selected_cryptos = st.multiselect("選擇交易對（可多選）", crypto_options, default=[crypto_options[0]])

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
with col2:
    end_date   = st.date_input("結束日期", pd.to_datetime("today"))

interval_options = {"日線": "1d", "4 小時線": "4h", "1 小時線": "1h", "30 分鐘線": "30m"}
interval_name    = st.selectbox("選擇 K 線週期", list(interval_options.keys()), index=0)
interval         = interval_options[interval_name]

# ✅ 摩擦成本 & 停損停利設定
risk_cfg = build_risk_ui(prefix="crypto_", market="crypto")

# 策略選擇（單選，供最佳化使用）
st.markdown("### 📌 選擇策略")
strategy_name = st.selectbox("回測 & 最佳化策略", list(strategies.keys()))
st.caption(strategies[strategy_name]["description"])

# =====================
# 套用最佳參數（與台股共用邏輯）
# 這裡以第一個選擇的交易對 × 策略作為最佳參數的索引
# =====================
best_params_db = load_best_params()
first_code     = selected_cryptos[0].split("(")[-1].strip(")") if selected_cryptos else ""
best_key       = f"{first_code}_{strategy_name}"

if best_key in best_params_db:
    saved = best_params_db[best_key]
    st.info(
        f"💾 **{first_code} × {strategy_name}** 已有儲存的最佳參數（{saved['saved_at']}）：" +
        "、".join([f"{k}={v}" for k, v in saved["params"].items()])
    )
    ss_key = f"use_best_{best_key}"
    if ss_key not in st.session_state:
        st.session_state[ss_key] = False

    col_cb, col_btn = st.columns([3, 1])
    with col_cb:
        use_saved = st.checkbox("✅ 套用已儲存的最佳參數", value=st.session_state[ss_key], key=f"cb_{ss_key}")
    with col_btn:
        if st.button("🔄 套用並重新整理", key=f"apply_{ss_key}"):
            st.session_state[ss_key] = True
            st.rerun()
    if use_saved != st.session_state[ss_key]:
        st.session_state[ss_key] = use_saved
        st.rerun()
else:
    use_saved = False

# 手動 / 最佳化參數 widget
saved_p = best_params_db.get(best_key, {}).get("params", {}) if use_saved else {}
params  = {}
for param, default in strategies[strategy_name]["parameters"].items():
    val = saved_p.get(param, default)
    if isinstance(default, int):
        if use_saved:
            st.markdown(f"**{param}**：`{int(val)}`（最佳化參數）")
            params[param] = int(val)
        else:
            params[param] = st.number_input(param, value=int(val), step=1, key=f"p_{param}")
    elif isinstance(default, float):
        if use_saved:
            st.markdown(f"**{param}**：`{float(val)}`（最佳化參數）")
            params[param] = float(val)
        else:
            params[param] = st.number_input(param, value=float(val), format="%.2f", key=f"p_{param}")
    else:
        params[param] = st.selectbox(param, options=["sma", "hull"],
                                     index=0 if str(val) == "sma" else 1, key=f"p_{param}")

# =====================
# 參數最佳化設定
# =====================
st.markdown("---")
st.markdown("## 🔍 參數最佳化（Grid Search）")
st.caption("針對第一個選定的交易對進行最佳化，找出最佳參數後可儲存套用至所有交易對。")

opt_ranges      = {}
has_optimizable = False
opt_cols        = st.columns(2)
col_idx         = 0

for param, default in strategies[strategy_name]["parameters"].items():
    if isinstance(default, int):
        has_optimizable = True
        with opt_cols[col_idx % 2]:
            st.markdown(f"**{param}**")
            c1, c2, c3 = st.columns(3)
            p_min  = c1.number_input("最小值", min_value=1,  max_value=500, value=max(1, int(default) - 10), step=1,  key=f"opt_min_{param}")
            p_max  = c2.number_input("最大值", min_value=1,  max_value=500, value=int(default) + 10,          step=1,  key=f"opt_max_{param}")
            p_step = c3.number_input("步長",   min_value=1,  max_value=50,  value=5,                          step=1,  key=f"opt_step_{param}")
        opt_ranges[param] = ("int", p_min, p_max, p_step)
        col_idx += 1
    elif isinstance(default, float):
        has_optimizable = True
        with opt_cols[col_idx % 2]:
            st.markdown(f"**{param}**")
            c1, c2, c3 = st.columns(3)
            p_min  = c1.number_input("最小值", value=round(default * 0.5, 2), step=0.1, format="%.2f", key=f"opt_min_{param}")
            p_max  = c2.number_input("最大值", value=round(default * 1.5, 2), step=0.1, format="%.2f", key=f"opt_max_{param}")
            p_step = c3.number_input("步長",   value=0.5,                     step=0.1, format="%.2f", key=f"opt_step_{param}")
        opt_ranges[param] = ("float", p_min, p_max, p_step)
        col_idx += 1

opt_target = st.selectbox("最佳化目標", ["夏普比率", "累積報酬率(%)", "最大回撤(%)（最小化）"])

if has_optimizable and opt_ranges:
    def estimate_combinations(opt_ranges):
        total = 1
        for _, (dtype, p_min, p_max, p_step) in opt_ranges.items():
            n = len(range(int(p_min), int(p_max) + 1, int(p_step))) if dtype == "int" \
                else len(np.arange(p_min, p_max + p_step * 0.5, p_step))
            total *= max(n, 1)
        return total
    est   = estimate_combinations(opt_ranges)
    color = "🟢" if est <= 100 else ("🟡" if est <= 500 else "🔴")
    st.caption(f"{color} 預估參數組合數：**{est}** 組")

if not has_optimizable:
    st.info("此策略無數值型參數，無法進行最佳化。")

st.markdown("---")

# =====================
# 輔助函式
# =====================
@st.cache_data
def fetch_crypto_data(symbol, start_date, end_date, interval):
    exchange = ccxt.binance()
    since    = int(time.mktime(pd.Timestamp(start_date).timetuple()) * 1000)
    all_ohlcv = []
    while True:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=interval, since=since, limit=1000)
        if not ohlcv:
            break
        all_ohlcv += ohlcv
        since = ohlcv[-1][0] + 1
        if pd.to_datetime(ohlcv[-1][0], unit='ms').date() >= end_date:
            break
        time.sleep(0.1)
    if not all_ohlcv:
        return pd.DataFrame()
    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['Date'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('Date', inplace=True)
    return df

def convert_to_usdt(df, symbol, start_date, end_date, interval):
    if symbol.endswith("/BTC"):
        btc_df = fetch_crypto_data("BTC/USDT", start_date, end_date, interval)
        if not btc_df.empty:
            df = df.merge(btc_df[['Close']], left_index=True, right_index=True,
                          how='left', suffixes=('', '_BTC'))
            df['Close'] = df['Close'] * df['Close_BTC']
            df.drop(columns=['Close_BTC'], inplace=True)
    return df

def run_strategy(df, strat_name, custom_params=None, risk_cfg=None):
    p = custom_params if custom_params else strategies[strat_name]["parameters"]
    if "function" in strategies[strat_name]:
        df = strategies[strat_name]["function"](df.copy(), p)
    else:
        df = apply_strategy(df.copy(), strat_name, p)
    # ✅ 套用摩擦成本 & 停損停利
    if risk_cfg:
        df = apply_friction_and_risk(df, **risk_cfg)
    else:
        df['DailyReturn'] = df['Close'].pct_change()
        df['Strategy']    = df['Position'].shift(1) * df['DailyReturn']
    df['DailyReturn'] = df['Close'].pct_change()
    df = df.dropna(subset=['DailyReturn', 'Strategy'])
    return df

def calc_metrics(df):
    cum_s  = (1 + df['Strategy']).cumprod()
    sharpe = (df['Strategy'].mean() / df['Strategy'].std() * TRADING_DAYS ** 0.5
              if df['Strategy'].std() != 0 else 0)
    return {
        "累積報酬率(%)": round((cum_s.iloc[-1] - 1) * 100, 2),
        "夏普比率":       round(sharpe, 2),
        "最大回撤(%)":    round(((cum_s - cum_s.cummax()) / cum_s.cummax()).min() * 100, 2),
        "交易次數":       int((df['Position'].diff().abs() > 0).sum()),
    }

def run_backtest_opt(df, strat_name, test_params, risk_cfg=None):
    try:
        df_s = run_strategy(df, strat_name, test_params, risk_cfg=risk_cfg)
        return calc_metrics(df_s) if not df_s.empty else None
    except Exception:
        return None

def plot_single(df, crypto_code, strat_name):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=(1+df['DailyReturn']).cumprod()-1,
                             mode='lines', name='買入持有', line=dict(dash='dash', color='gray')))
    fig.add_trace(go.Scatter(x=df.index, y=(1+df['Strategy']).cumprod()-1,
                             mode='lines', name=f'{strat_name}', line=dict(color='royalblue')))
    fig.update_layout(title=f"{crypto_code}｜{strat_name}（USDT 基準）",
                      xaxis_title="日期", yaxis_title="累積報酬率")
    return fig

def plot_comparison_line(result_map):
    fig = go.Figure()
    for (crypto_code, strat_name), df in result_map.items():
        fig.add_trace(go.Scatter(x=df.index, y=(1+df['Strategy']).cumprod()-1,
                                 mode='lines', name=f"{crypto_code} × {strat_name}"))
    fig.update_layout(title="📈 多交易對 × 策略 累積報酬率比較（USDT 基準）",
                      xaxis_title="日期", yaxis_title="累積報酬率",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.4, xanchor="center", x=0.5))
    return fig

# =====================
# 一般回測
# =====================
if st.button("🚀 開始回測"):
    if not selected_cryptos:
        st.error("請至少選擇一個交易對"); st.stop()

    results    = []
    result_map = {}

    for crypto in selected_cryptos:
        crypto_code = crypto.split("(")[-1].strip(")")

        with st.spinner(f"下載 {crypto_code} 資料中..."):
            df_raw = fetch_crypto_data(crypto_code, start_date, end_date, interval)

        if df_raw.empty:
            st.warning(f"⚠️ {crypto_code} 無法取得資料，跳過"); continue

        df_raw = convert_to_usdt(df_raw, crypto_code, start_date, end_date, interval)

        st.markdown(f"---\n### 🪙 {crypto_code}")
        try:
            df = run_strategy(df_raw, strategy_name, params, risk_cfg=risk_cfg)
        except Exception as e:
            st.warning(f"⚠️ {crypto_code} × {strategy_name} 執行失敗：{e}"); continue

        if df.empty:
            st.warning(f"⚠️ {crypto_code} × {strategy_name} 結果為空，跳過"); continue

        result_map[(crypto_code, strategy_name)] = df
        st.plotly_chart(plot_single(df, crypto_code, strategy_name), use_container_width=True)

        m = calc_metrics(df)

        # ✅ 優先使用 Position_adj（停損停利調整後），否則用 Position
        pos_col   = 'Position_adj' if 'Position_adj' in df.columns else 'Position'
        last_pos  = int(df[pos_col].iloc[-1])
        last_date = df.index[-1]

        if last_pos == 1:
            signal_text = "📈 持有（買入）"
        elif last_pos == -1:
            signal_text = "📉 放空"
        else:
            signal_text = "🟡 空手"

        # 找最近買入日與未實現損益
        buy_idx    = df[df[pos_col].diff() == 1].index
        sell_idx   = df[df[pos_col].diff() == -1].index
        last_buy   = buy_idx[-1].strftime("%Y-%m-%d")  if len(buy_idx)  > 0 else "無"
        last_sell  = sell_idx[-1].strftime("%Y-%m-%d") if len(sell_idx) > 0 else "無"

        unrealized_str = "—"
        if last_pos == 1 and len(buy_idx) > 0:
            try:
                entry_price   = float(df.loc[buy_idx[-1], 'Close'])
                current_price = float(df['Close'].iloc[-1])
                unrealized    = (current_price - entry_price) / entry_price * 100
                unrealized_str = f"{unrealized:+.2f}%"
            except Exception:
                pass

        # ✅ 獨立顯示今日操作建議
        st.markdown("#### 🔔 今日操作建議")
        sig1, sig2, sig3, sig4 = st.columns(4)
        sig1.metric("資料最新日期", last_date.strftime("%Y-%m-%d"))
        sig2.metric("當前建議操作", signal_text)
        sig3.metric("最近買入日",   last_buy)
        sig4.metric("未實現損益",   unrealized_str)
        if last_sell != "無":
            st.caption(f"最近賣出日：{last_sell}")

        results.append({
            "交易對": crypto_code, "策略": strategy_name,
            "買入持有報酬率(%)": round((1+df['DailyReturn']).cumprod().iloc[-1]-1, 4)*100,
            "策略報酬率(%)": m["累積報酬率(%)"],
            "年化波動(%)": round(df['Strategy'].std() * TRADING_DAYS**0.5 * 100, 2),
            "最大回撤(%)": m["最大回撤(%)"],
            "夏普比率": m["夏普比率"],
            "最新訊號": signal_text,
        })

    if result_map:
        st.markdown("---\n## 📊 彙總比較")
        st.plotly_chart(plot_comparison_line(result_map), use_container_width=True)

    if results:
        df_r = pd.DataFrame(results)
        fig_bar = px.bar(df_r, x="交易對", y="策略報酬率(%)", color="策略",
                         barmode="group", title="各交易對策略報酬率比較", text_auto=".1f")
        st.plotly_chart(fig_bar, use_container_width=True)
        st.markdown("### 📋 策略績效總表")
        st.dataframe(df_r.style.format({
            "買入持有報酬率(%)": "{:.2f}%", "策略報酬率(%)": "{:.2f}%",
            "年化波動(%)": "{:.2f}%", "最大回撤(%)": "{:.2f}%", "夏普比率": "{:.2f}",
        }), use_container_width=True)
    else:
        st.warning("無可用結果，請檢查交易對及策略選擇")

# =====================
# 參數最佳化
# =====================
if has_optimizable and st.button("⚙️ 開始參數最佳化"):
    if not selected_cryptos:
        st.error("請至少選擇一個交易對"); st.stop()

    opt_symbol = selected_cryptos[0].split("(")[-1].strip(")")
    st.info(f"🔍 以 **{opt_symbol}** 進行參數掃描...")

    with st.spinner(f"下載 {opt_symbol} 資料中..."):
        df_raw = fetch_crypto_data(opt_symbol, start_date, end_date, interval)

    if df_raw.empty:
        st.error("❌ 無法取得資料"); st.stop()

    df_raw = convert_to_usdt(df_raw, opt_symbol, start_date, end_date, interval)

    # 建立參數網格
    param_names, param_values = [], []
    for param, (dtype, p_min, p_max, p_step) in opt_ranges.items():
        param_names.append(param)
        vals = list(range(int(p_min), int(p_max)+1, int(p_step))) if dtype == "int" \
               else [round(v, 4) for v in np.arange(p_min, p_max + p_step*0.5, p_step)]
        param_values.append(vals)

    fixed_params = {k: v for k, v in params.items() if k not in opt_ranges}
    all_combos   = list(product(*param_values))
    total        = len(all_combos)

    st.info(f"🔄 共 {total} 組參數組合，開始掃描...")
    progress_bar = st.progress(0)
    status_text  = st.empty()

    results = []
    for i, combo in enumerate(all_combos):
        test_params = dict(zip(param_names, combo))
        test_params.update(fixed_params)
        m = run_backtest_opt(df_raw, strategy_name, test_params, risk_cfg=risk_cfg)
        if m:
            results.append({**test_params, **m})
        progress_bar.progress((i+1)/total)
        if (i+1) % 20 == 0 or (i+1) == total:
            status_text.text(f"進度：{i+1}/{total} 完成，有效結果：{len(results)} 組")

    progress_bar.empty()
    status_text.empty()

    if not results:
        st.error("❌ 所有組合均無有效結果"); st.stop()

    df_opt = pd.DataFrame(results)
    if opt_target == "最大回撤(%)（最小化）":
        df_opt = df_opt.sort_values("最大回撤(%)", ascending=True)
    elif opt_target == "夏普比率":
        df_opt = df_opt.sort_values("夏普比率", ascending=False)
    else:
        df_opt = df_opt.sort_values("累積報酬率(%)", ascending=False)

    best = df_opt.iloc[0]
    st.success(f"✅ 掃描完成！共 {len(df_opt)} 組有效結果")

    # 最佳參數展示
    st.markdown("### 🏆 最佳參數組合")
    best_cols = st.columns(len(param_names) + 4)
    for i, p in enumerate(param_names):
        best_cols[i].metric(p, best[p])
    best_cols[len(param_names)].metric("累積報酬率",  f"{best['累積報酬率(%)']:.2f}%")
    best_cols[len(param_names)+1].metric("夏普比率",   f"{best['夏普比率']:.2f}")
    best_cols[len(param_names)+2].metric("最大回撤",   f"{best['最大回撤(%)']:.2f}%")
    best_cols[len(param_names)+3].metric("交易次數",   f"{int(best['交易次數'])}")

    # Top 20
    st.markdown("### 📋 Top 20 參數組合")
    fmt_dict = {"累積報酬率(%)": "{:.2f}%", "夏普比率": "{:.2f}", "最大回撤(%)": "{:.2f}%"}
    st.dataframe(
        df_opt.head(20).style.format(fmt_dict)
            .background_gradient(subset=["累積報酬率(%)"], cmap="RdYlGn")
            .background_gradient(subset=["夏普比率"],      cmap="RdYlGn")
            .background_gradient(subset=["最大回撤(%)"],   cmap="RdYlGn_r"),
        use_container_width=True
    )

    # 熱力圖
    int_params = [p for p in param_names if opt_ranges[p][0] == "int"]
    if len(int_params) >= 2:
        p1, p2     = int_params[0], int_params[1]
        metric_col = "夏普比率" if opt_target == "夏普比率" else "累積報酬率(%)"
        pivot      = df_opt.pivot_table(index=p1, columns=p2, values=metric_col, aggfunc="mean")
        fig_heat   = px.imshow(pivot, color_continuous_scale="RdYlGn",
                               title=f"{strategy_name} 參數熱力圖（{p1} × {p2}）", aspect="auto")
        st.plotly_chart(fig_heat, use_container_width=True)
    elif len(int_params) == 1:
        p1         = int_params[0]
        metric_col = "夏普比率" if opt_target == "夏普比率" else "累積報酬率(%)"
        df_line    = df_opt.groupby(p1)[metric_col].mean().reset_index()
        fig_line   = px.line(df_line, x=p1, y=metric_col, title=f"{p1} 對 {metric_col} 的影響", markers=True)
        fig_line.add_vline(x=best[p1], line_dash="dash", line_color="red",
                           annotation_text=f"最佳={best[p1]}")
        st.plotly_chart(fig_line, use_container_width=True)

    # 最佳參數回測圖
    st.markdown("### 📈 使用最佳參數執行回測")
    best_params_to_save = {p: (int(best[p]) if isinstance(strategies[strategy_name]["parameters"].get(p, 0), int)
                               else round(float(best[p]), 4))
                           for p in param_names}
    best_params_to_save.update(fixed_params)

    df_best = run_strategy(df_raw, strategy_name, best_params_to_save)
    if not df_best.empty:
        fig_best = go.Figure()
        fig_best.add_trace(go.Scatter(x=df_best.index,
                                      y=(1+df_best['DailyReturn']).cumprod()-1,
                                      mode='lines', name='買入持有', line=dict(dash='dash', color='gray')))
        fig_best.add_trace(go.Scatter(x=df_best.index,
                                      y=(1+df_best['Strategy']).cumprod()-1,
                                      mode='lines', name='最佳參數策略', line=dict(color='orange')))
        fig_best.update_layout(title=f"{opt_symbol} 最佳參數回測", xaxis_title="日期", yaxis_title="累積報酬率")
        st.plotly_chart(fig_best, use_container_width=True)

    # 原始 vs 最佳比較
    orig_m = run_backtest_opt(df_raw, strategy_name, params, risk_cfg=risk_cfg)
    st.markdown("### ⚖️ 原始參數 vs 最佳化參數比較")
    st.table(pd.DataFrame({
        "項目": ["累積報酬率(%)", "夏普比率", "最大回撤(%)", "交易次數"],
        "原始參數": [
            f"{orig_m['累積報酬率(%)']:.2f}%" if orig_m else "N/A",
            f"{orig_m['夏普比率']:.2f}"       if orig_m else "N/A",
            f"{orig_m['最大回撤(%)']:.2f}%"   if orig_m else "N/A",
            str(orig_m['交易次數'])            if orig_m else "N/A",
        ],
        "最佳化參數": [
            f"{best['累積報酬率(%)']:.2f}%",
            f"{best['夏普比率']:.2f}",
            f"{best['最大回撤(%)']:.2f}%",
            str(int(best['交易次數'])),
        ]
    }))

    # ✅ 存進 session_state，讓儲存按鈕在 rerun 後仍能讀到
    st.session_state["pending_best_params"]   = best_params_to_save
    st.session_state["pending_best_metrics"]  = {
        "累積報酬率(%)": float(best["累積報酬率(%)"]),
        "夏普比率":       float(best["夏普比率"]),
        "最大回撤(%)":    float(best["最大回撤(%)"]),
    }
    st.session_state["pending_best_stock"]    = opt_symbol
    st.session_state["pending_best_strategy"] = strategy_name

    st.warning(
        "⚠️ **過度擬合警告**：最佳參數基於歷史資料，不代表未來同樣有效。"
        "建議搭配不同時間段驗證。"
    )

# =====================
# 儲存最佳參數（在最佳化 block 外，避免 rerun 後變數消失）
# =====================
if "pending_best_params" in st.session_state:
    pending_symbol   = st.session_state.get("pending_best_stock", "")
    pending_strategy = st.session_state.get("pending_best_strategy", "")
    pending_params   = st.session_state["pending_best_params"]
    pending_metrics  = st.session_state["pending_best_metrics"]

    st.markdown("---")
    st.markdown("### 💾 儲存最佳化結果")
    st.info(
        f"**{pending_symbol} × {pending_strategy}** 最佳參數：" +
        "、".join([f"{k}={v}" for k, v in pending_params.items()]) +
        f"　｜　報酬率 {pending_metrics['累積報酬率(%)']:.2f}%、"
        f"夏普 {pending_metrics['夏普比率']:.2f}、"
        f"回撤 {pending_metrics['最大回撤(%)']:.2f}%"
    )

    col_s1, col_s2 = st.columns([2, 1])
    with col_s1:
        if st.button("💾 儲存最佳參數（套用至回測 & 策略比較）", type="primary"):
            save_best_params(pending_symbol, pending_strategy, pending_params, pending_metrics)
            ss_key = f"use_best_{pending_symbol}_{pending_strategy}"
            st.session_state[ss_key] = True
            del st.session_state["pending_best_params"]
            st.success("✅ 已儲存！頁面將重新整理，可套用最佳參數進行回測。")
            st.rerun()
    with col_s2:
        if st.button("🗑️ 捨棄"):
            del st.session_state["pending_best_params"]
            st.rerun()


# =====================
# 匯出交易機器人 Python 檔（資產管理模式）
# =====================
st.markdown("---")
st.markdown("## 🤖 匯出樹莓派自動交易機器人")
st.caption("根據回測策略與參數，產生以「目標持倉數量」為基準的資產管理交易腳本。")

with st.expander("⚙️ 交易機器人設定", expanded=True):
    bot_col1, bot_col2 = st.columns(2)
    with bot_col1:
        bot_symbols = st.multiselect(
            "交易對（機器人監控）",
            list(crypto_list.keys()),
            default=[list(crypto_list.keys())[0]],
            help="選擇機器人要自動交易的幣對"
        )
        bot_freq = st.selectbox(
            "執行週期",
            ["4h", "1h", "1d", "30m"],
            index=0,
        )
        bot_lookback = st.number_input(
            "回溯 K 棒數量",
            min_value=50, max_value=2000, value=250, step=50,
        )

    with bot_col2:
        bot_mode = st.selectbox(
            "下單模式",
            ["TEST（測試，不真實下單）", "MARKET（市價單）"],
            index=0,
        )
        bot_min_threshold = st.number_input(
            "最小交易門檻（USDT）",
            min_value=1.0, max_value=1000.0, value=5.0, step=1.0,
            help="持倉差異價值超過此門檻才執行交易，節省手續費"
        )
        bot_name = st.text_input(
            "策略名稱（用於 log 識別）",
            value=f"crypto-bot-{strategy_name.replace(' ', '-').replace('/', '-')}",
        )

    # 各交易對目標持倉數量
    st.markdown("#### 🎯 各交易對目標持倉數量（看多時持有）")
    st.caption("看多訊號時機器人會校正至此數量，看空時清倉（目標=0）。")
    target_qty_cfg = {}
    qty_cols = st.columns(min(len(bot_symbols), 4)) if bot_symbols else []
    for i, sym in enumerate(bot_symbols):
        with qty_cols[i % 4] if qty_cols else st.container():
            target_qty_cfg[sym] = st.number_input(
                f"{sym} 目標數量",
                min_value=0.0, value=0.001, step=0.0001,
                format="%.6f",
                key=f"tgt_{sym}",
                help=f"看多時持有的 {sym.split('/')[0]} 數量"
            )

    st.markdown("#### 🔑 幣安 API")
    bot_key_col1, bot_key_col2 = st.columns(2)
    with bot_key_col1:
        bot_api_key    = st.text_input("Binance API Key",    type="password", placeholder="你的 API Key")
    with bot_key_col2:
        bot_api_secret = st.text_input("Binance API Secret", type="password", placeholder="你的 API Secret")

    st.warning("⚠️ API Key 會寫入匯出的 Python 檔，請勿上傳至公開平台（如 GitHub）。")

mode_map     = {"TEST（測試，不真實下單）": "TEST", "MARKET（市價單）": "MARKET"}
bot_mode_str = mode_map[bot_mode]


def generate_bot_code(
    strategy_name, params, symbols, target_qty_cfg,
    freq, lookback, mode, min_threshold, bot_name,
    api_key, api_secret
):
    params_repr     = repr(params)
    symbols_repr    = repr(symbols)
    target_cfg_repr = repr(target_qty_cfg)

    strategy_logic_map = {
        "簡單均線交叉":
"""    short = int(p.get("短期均線", 20))
    long_ = int(p.get("長期均線", 60))
    sma_s = close.rolling(short).mean()
    sma_l = close.rolling(long_).mean()
    is_bullish = bool(sma_s.iloc[-1] > sma_l.iloc[-1])""",
        "MACD 策略":
"""    short_ema = int(p.get("短期 EMA", 12))
    long_ema  = int(p.get("長期 EMA", 26))
    signal_p  = int(p.get("訊號線", 9))
    ema_s  = close.ewm(span=short_ema, adjust=False).mean()
    ema_l  = close.ewm(span=long_ema,  adjust=False).mean()
    macd   = ema_s - ema_l
    signal = macd.ewm(span=signal_p, adjust=False).mean()
    is_bullish = bool(macd.iloc[-1] > signal.iloc[-1])""",
        "RSI 策略":
"""    rsi_p  = int(p.get("RSI 期間", 14))
    buy_lv = float(p.get("買入閾值", 30))
    delta  = close.diff()
    gain   = delta.clip(lower=0).rolling(rsi_p).mean()
    loss   = (-delta.clip(upper=0)).rolling(rsi_p).mean()
    rsi    = 100 - (100 / (1 + gain/loss))
    is_bullish = bool(rsi.iloc[-1] > buy_lv)""",
        "布林通道策略":
"""    period   = int(p.get("期間", 20))
    std_mult = float(p.get("標準差倍數", 2.0))
    ma    = close.rolling(period).mean()
    std   = close.rolling(period).std()
    lower = ma - std_mult * std
    is_bullish = bool(close.iloc[-1] > lower.iloc[-1])""",
        "Supertrend 策略":
"""    atr_p = int(p.get("ATR 週期", 10))
    mult  = float(p.get("ATR 倍數", 3.0))
    hl2   = (high + low) / 2
    tr    = pd.concat([high-low,(high-close.shift(1)).abs(),(low-close.shift(1)).abs()],axis=1).max(axis=1)
    atr   = tr.rolling(atr_p).mean()
    upper_b = hl2 + mult * atr
    lower_b = hl2 - mult * atr
    dir_ = pd.Series(1, index=close.index)
    for i in range(1, len(close)):
        if   close.iloc[i] > upper_b.iloc[i]: dir_.iloc[i] = 1
        elif close.iloc[i] < lower_b.iloc[i]: dir_.iloc[i] = -1
        else:                                  dir_.iloc[i] = dir_.iloc[i-1]
    is_bullish = bool(dir_.iloc[-1] == 1)""",
        "Stochastic RSI 策略":
"""    rsi_p   = int(p.get("RSI 週期", 14))
    stoch_p = int(p.get("Stoch 週期", 14))
    k_p     = int(p.get("%K 平滑", 3))
    buy_lv  = float(p.get("買入閾值", 20))
    delta   = close.diff()
    gain    = delta.clip(lower=0).rolling(rsi_p).mean()
    loss    = (-delta.clip(upper=0)).rolling(rsi_p).mean()
    rsi     = 100 - (100 / (1 + gain/loss))
    stoch   = (rsi - rsi.rolling(stoch_p).min()) / (rsi.rolling(stoch_p).max() - rsi.rolling(stoch_p).min() + 1e-10) * 100
    k       = stoch.rolling(k_p).mean()
    is_bullish = bool(k.iloc[-1] < buy_lv)""",
        "VWAP 均值回歸策略":
"""    period = int(p.get("VWAP 週期", 20))
    tp     = (high + low + close) / 3
    vwap   = (tp * volume).rolling(period).sum() / volume.rolling(period).sum()
    is_bullish = bool(close.iloc[-1] > vwap.iloc[-1])""",
        "SMA/Hull 趨勢策略":
"""    n1, n2 = int(p.get("n1", 30)), int(p.get("n2", 130))
    def wma(s, n):
        w = pd.Series(range(1, n+1))
        return s.rolling(n).apply(lambda x: (x*w).sum()/w.sum(), raw=True)
    def hma(s, n):
        h = int(n/2)
        return wma(2*wma(s, h) - wma(s, n), int(n**0.5))
    t1, t2 = hma(close, n1), hma(close, n2)
    is_bullish = bool(t1.iloc[-1] > t2.iloc[-1])""",
        "ATR 波動突破策略":
"""    period = int(p.get("均線週期", 20))
    atr_p  = int(p.get("ATR 週期", 14))
    mult   = float(p.get("突破倍數", 1.5))
    ma     = close.rolling(period).mean()
    tr     = pd.concat([high-low,(high-close.shift(1)).abs(),(low-close.shift(1)).abs()],axis=1).max(axis=1)
    atr    = tr.rolling(atr_p).mean()
    is_bullish = bool(close.iloc[-1] > (ma + mult*atr).iloc[-1])""",
        "ADX 趨勢強度過濾策略":
"""    adx_p  = int(p.get("ADX 週期", 14))
    adx_th = float(p.get("ADX 閾值", 25))
    ma_p   = int(p.get("均線週期", 20))
    up     = high.diff(); down = -low.diff()
    plus   = pd.Series(np.where((up>down)&(up>0), up, 0), index=close.index)
    minus  = pd.Series(np.where((down>up)&(down>0), down, 0), index=close.index)
    tr     = pd.concat([high-low,(high-close.shift(1)).abs(),(low-close.shift(1)).abs()],axis=1).max(axis=1)
    atr    = tr.rolling(adx_p).mean()
    pdi    = 100 * plus.rolling(adx_p).mean() / atr
    mdi    = 100 * minus.rolling(adx_p).mean() / atr
    adx    = (100*(pdi-mdi).abs()/(pdi+mdi+1e-10)).rolling(adx_p).mean()
    ma     = close.rolling(ma_p).mean()
    is_bullish = bool((adx.iloc[-1] > adx_th) and (close.iloc[-1] > ma.iloc[-1]))""",
        "DCA 定期買入策略":
"""    import datetime as dt_
    interval   = int(p.get("買入間隔（天）", 7))
    is_bullish = (dt_.datetime.utcnow().timetuple().tm_yday % interval == 0)""",
    }

    strategy_logic = strategy_logic_map.get(strategy_name, strategy_logic_map["SMA/Hull 趨勢策略"])

    import datetime as _dt
    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    template = """\
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
\"\"\"
自動交易機器人 - 資產管理模式（由虛擬幣回測系統匯出）
策略    : {strategy_name}
參數    : {params_repr}
交易對  : {symbols_repr}
產生時間: {generated_at}

核心邏輯: 以固定「目標持倉數量」為基準進行持倉校正
  - 策略看多 -> 持倉校正至目標數量
  - 策略看空 -> 清倉（目標數量 = 0）
  - 差異價值 < 門檻 -> 不交易（節省手續費）

樹莓派部署:
  pip install ccxt pandas numpy
  crontab: 0 */4 * * * /usr/bin/python3 /home/pi/trading_bot.py >> /home/pi/bot.log 2>&1

警告: 請勿將此檔案上傳至公開平台，API Key 已內嵌。
\"\"\"

import ccxt
import pandas as pd
import numpy as np
import datetime
import json
import os
import time

# =====================
# 設定區
# =====================
API_KEY    = "{api_key}"
API_SECRET = "{api_secret}"
MODE       = "{mode}"       # TEST (僅模擬) / MARKET (實盤下單)

# 各交易對目標持倉數量（看多時持有，看空時清倉）
TARGET_CONFIG = {target_cfg_repr}

SYMBOLS              = list(TARGET_CONFIG.keys())
FREQ                 = "{freq}"
LOOKBACK             = {lookback}
MIN_VALUE_THRESHOLD  = {min_threshold}   # 差異價值超過此 USDT 才執行交易
BOT_NAME             = "{bot_name}"
PARAMS               = {params_repr}
STATE_FILE           = "bot_state_{bot_name_safe}.json"

# =====================
# 工具函式
# =====================
def log(msg):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{{now}}] {{msg}}")

def display_account_summary(exchange):
    \"\"\"顯示帳戶中所有非零持倉及其估值\"\"\"
    log("🔍 掃描帳戶總持倉...")
    try:
        balance = exchange.fetch_balance()
        total   = balance["total"]
        summary = []
        for asset, qty in total.items():
            if qty <= 0:
                continue
            if asset == "USDT":
                summary.append({{"資產": asset, "數量": f"{{qty:.4f}}", "估值": f"{{qty:.2f}} USDT"}})
                continue
            try:
                ticker     = exchange.fetch_ticker(f"{{asset}}/USDT")
                value_usdt = qty * ticker["last"]
                if value_usdt > 1.0:
                    summary.append({{"資產": asset, "數量": f"{{qty:.6f}}", "估值": f"{{value_usdt:.2f}} USDT"}})
            except Exception:
                summary.append({{"資產": asset, "數量": f"{{qty:.6f}}", "估值": "未知"}})
        if summary:
            print("\\n" + "="*45)
            print(f"{{'資產':<10}} {{'數量':<15}} {{'估值 (USDT)':<15}}")
            print("-" * 45)
            for item in summary:
                print(f"{{item['資產']:<10}} {{item['數量']:<15}} {{item['估值']:<15}}")
            print("="*45 + "\\n")
        else:
            log("ℹ️ 帳戶無顯著資產")
    except Exception as e:
        log(f"⚠️ 讀取帳戶失敗: {{e}}")

def get_ohlcv(exchange, symbol, freq, lookback):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=freq, limit=lookback)
    df    = pd.DataFrame(ohlcv, columns=["timestamp","Open","High","Low","Close","Volume"])
    df["Date"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("Date", inplace=True)
    return df

def get_signal(df, p):
    \"\"\"回傳 is_bullish: True=看多, False=看空\"\"\"
    close  = df["Close"]
    high   = df["High"]
    low    = df["Low"]
    volume = df["Volume"]
    is_bullish = False
{strategy_logic}
    return is_bullish

# =====================
# 主要執行邏輯
# =====================
def run():
    log(f"=== {{BOT_NAME}} 啟動 (模式: {{MODE}}) ===")

    exchange = ccxt.binance({{
        "apiKey":          API_KEY,
        "secret":          API_SECRET,
        "enableRateLimit": True,
        "options":         {{"defaultType": "spot"}},
    }})

    try:
        exchange.load_markets()
    except Exception as e:
        log(f"❌ 交易所連線失敗: {{e}}")
        return

    # 顯示帳戶總覽
    display_account_summary(exchange)

    try:
        balance = exchange.fetch_balance()
    except Exception as e:
        log(f"❌ 讀取餘額失敗: {{e}}")
        return

    for symbol in SYMBOLS:
        log(f"--- 策略檢查: {{symbol}} ---")
        base_asset = symbol.split("/")[0]

        # 1. 取得實際持倉數量
        actual_qty = float(balance.get(base_asset, {{}}).get("total", 0))

        try:
            # 2. 取得市價與 K 線資料
            ticker     = exchange.fetch_ticker(symbol)
            curr_price = float(ticker["last"])
            df         = get_ohlcv(exchange, symbol, FREQ, LOOKBACK)
            is_bullish = get_signal(df, PARAMS)
        except Exception as e:
            log(f"❌ 數據讀取失敗 {{symbol}}: {{e}}")
            continue

        # 3. 決定目標持倉數量
        target_qty = TARGET_CONFIG.get(symbol, 0.0) if is_bullish else 0.0

        # 4. 計算差額
        diff_qty   = target_qty - actual_qty
        diff_value = abs(diff_qty * curr_price)

        log(f"趨勢分析: {{'🟢 看多' if is_bullish else '🔴 看空'}}")
        log(f"持倉狀態: 實際 {{actual_qty:.6f}} | 目標 {{target_qty:.6f}}")
        log(f"待校正量: {{diff_qty:.6f}} {{base_asset}} (約 {{diff_value:.2f}} USDT)")

        # 5. 差異超過門檻才交易
        if diff_value > MIN_VALUE_THRESHOLD:
            side      = "buy" if diff_qty > 0 else "sell"
            order_qty = float(exchange.amount_to_precision(symbol, abs(diff_qty)))
            if order_qty > 0:
                log(f"⚠️ 觸發部位校正 -> {{side.upper()}} {{order_qty}} {{base_asset}}")
                if MODE == "MARKET":
                    try:
                        order = exchange.create_order(symbol, "market", side, order_qty)
                        log(f"✅ {{side.upper()}} 執行成功！單號: {{order['id']}}")
                    except Exception as e:
                        log(f"❌ 交易失敗: {{e}}")
                else:
                    log(f"🧪 [TEST] 擬執行 {{side}} {{order_qty}} {{base_asset}} @ {{curr_price:.4f}}")
            else:
                log("ℹ️ 修正數量過小，跳過")
        else:
            log(f"⏸️ 差異 ({{diff_value:.2f}} USDT) 未達門檻 {{MIN_VALUE_THRESHOLD}} USDT，無需調整")

        time.sleep(1)

    log("=== 任務完畢 ===")

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log(f"❌ 系統層級錯誤: {{e}}")
"""

    return template.format(
        strategy_name   = strategy_name,
        params_repr     = params_repr,
        symbols_repr    = symbols_repr,
        target_cfg_repr = target_cfg_repr,
        generated_at    = generated_at,
        api_key         = api_key,
        api_secret      = api_secret,
        mode            = mode,
        freq            = freq,
        lookback        = lookback,
        min_threshold   = min_threshold,
        bot_name        = bot_name,
        bot_name_safe   = bot_name.replace("/", "-").replace(" ", "_").replace("\\", "-"),
        strategy_logic  = strategy_logic,
    )


if st.button("🤖 產生樹莓派交易機器人腳本", type="primary"):
    if not bot_symbols:
        st.error("請至少選擇一個交易對")
        st.stop()

    bot_code = generate_bot_code(
        strategy_name  = strategy_name,
        params         = params,
        symbols        = bot_symbols,
        target_qty_cfg = target_qty_cfg,
        freq           = bot_freq,
        lookback       = int(bot_lookback),
        mode           = bot_mode_str,
        min_threshold  = float(bot_min_threshold),
        bot_name       = bot_name,
        api_key        = bot_api_key    or "YOUR_API_KEY",
        api_secret     = bot_api_secret or "YOUR_API_SECRET",
    )

    with st.expander("👁️ 預覽腳本內容（前 60 行）", expanded=False):
        st.code("\n".join(bot_code.split("\n")[:60]), language="python")

    st.download_button(
        label     = "⬇️ 下載 trading_bot.py",
        data      = bot_code.encode("utf-8"),
        file_name = f"trading_bot_{strategy_name.replace(' ', '_').replace('/', '-')}.py",
        mime      = "text/plain",
        type      = "primary",
    )

    st.success("✅ 腳本產生成功！")
    st.markdown("""
**📋 樹莓派部署步驟：**

1. 下載腳本，上傳至樹莓派（例如 `/home/pi/trading_bot.py`）
2. 安裝套件：
   ```bash
   pip install ccxt pandas numpy
   ```
3. 先用 **TEST 模式**確認訊號與數量正確：
   ```bash
   python3 /home/pi/trading_bot.py
   ```
4. 確認無誤後改 `MODE = "MARKET"` 開始實盤
5. 設定 cron 每4小時自動執行：
   ```bash
   crontab -e
   0 */4 * * * /usr/bin/python3 /home/pi/trading_bot.py >> /home/pi/bot.log 2>&1
   ```
6. 查看 log：
   ```bash
   tail -f /home/pi/bot.log
   ```
""")
