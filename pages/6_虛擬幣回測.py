import streamlit as st
import pandas as pd
import plotly.graph_objs as go
import plotly.express as px
from strategy import apply_strategy, strategies
import ccxt
import time

st.title("💰 虛擬幣策略回測系統（USDT 統一基準 + 多交易對 + 多策略比較）")

# =====================
# 幣種清單
# =====================
crypto_list = {
    "BTC/USDT": "比特幣 (BTC)",
    "ETH/USDT": "以太坊 (ETH)",
    "XRP/USDT": "瑞波幣 (XRP)",
    "ADA/USDT": "艾達幣 (ADA)",
    "LINK/USDT": "Chainlink (LINK)",
    "VET/USDT": "唯鏈 (VET)",
    "DOGE/USDT": "狗狗幣 (DOGE)",
    "ETH/BTC": "以太坊 (ETH/BTC)",
    "XRP/BTC": "瑞波幣 (XRP/BTC)",
    "ADA/BTC": "艾達幣 (ADA/BTC)",
    "LINK/BTC": "Chainlink (LINK/BTC)",
    "VET/BTC": "唯鏈 (VET/BTC)",
    "DOGE/BTC": "狗狗幣 (DOGE/BTC)"
}

# =====================
# SMA/Hull 趨勢策略（虛擬幣專屬）
# =====================
def sma_hull_trend_strategy(df, params):
    type_ = params.get("type", "sma")
    n1 = int(params.get("n1", 30))
    n2 = int(params.get("n2", 130))
    df = df.copy()

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

    # 使用狀態機（與 strategy.py 一致，不用 ffill）
    position = 0
    positions = []
    for i in range(len(df)):
        if sell.iloc[i]:
            position = 0
        elif buy.iloc[i]:
            position = 1
        positions.append(position)
    df['Position'] = positions
    return df

strategies.update({
    "SMA/Hull 趨勢策略": {
        "description": "短期均線與長期均線交叉策略 (SMA/HullMA)",
        "parameters": {"type": "sma", "n1": 30, "n2": 130},
        "function": sma_hull_trend_strategy
    }
})

# =====================
# UI 選項
# =====================
crypto_options = [f"{name} ({code})" for code, name in crypto_list.items()]
selected_cryptos = st.multiselect("選擇交易對（可多選）", crypto_options, default=[crypto_options[0]])

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
with col2:
    end_date = st.date_input("結束日期", pd.to_datetime("today"))

interval_options = {"日線": "1d", "4 小時線": "4h", "1 小時線": "1h", "30 分鐘線": "30m"}
interval_name = st.selectbox("選擇 K 線週期", list(interval_options.keys()), index=0)
interval = interval_options[interval_name]

# =====================
# 多策略選擇（核心新增）
# =====================
st.markdown("### 📌 選擇策略（可多選）")
strategies_selected = st.multiselect(
    "選擇策略（多選）",
    list(strategies.keys()),
    default=[list(strategies.keys())[0]]
)

if not strategies_selected:
    st.warning("請至少選擇一個策略")
    st.stop()

# 展示各策略說明
for s in strategies_selected:
    st.caption(f"**{s}**：{strategies[s]['description']}")

# =====================
# 資料抓取
# =====================
@st.cache_data
def fetch_crypto_data(symbol, start_date, end_date, interval):
    exchange = ccxt.binance()
    since = int(time.mktime(pd.Timestamp(start_date).timetuple()) * 1000)
    all_ohlcv = []
    limit = 1000
    while True:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=interval, since=since, limit=limit)
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
            df = df.merge(btc_df[['Close']], left_index=True, right_index=True, how='left', suffixes=('', '_BTC'))
            df['Close'] = df['Close'] * df['Close_BTC']
            df.drop(columns=['Close_BTC'], inplace=True)
    return df

def run_strategy(df, strat_name):
    """執行單一策略，回傳帶有 Position/DailyReturn/Strategy 的 df"""
    params = strategies[strat_name]["parameters"]
    if "function" in strategies[strat_name]:
        df = strategies[strat_name]["function"](df.copy(), params)
    else:
        df = apply_strategy(df.copy(), strat_name, params)
    df['DailyReturn'] = df['Close'].pct_change()
    df['Strategy'] = df['Position'].shift(1) * df['DailyReturn']
    df = df.dropna(subset=['DailyReturn', 'Strategy'])
    return df

# =====================
# 圖表函式
# =====================
def plot_single_crypto(df, crypto_code, strat_name):
    """單一交易對：買入持有 vs 策略"""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index,
        y=(1 + df['DailyReturn']).cumprod() - 1,
        mode='lines', name='買入持有'
    ))
    fig.add_trace(go.Scatter(
        x=df.index,
        y=(1 + df['Strategy']).cumprod() - 1,
        mode='lines', name=f'{strat_name} 策略'
    ))
    fig.update_layout(
        title=f"{crypto_code}｜{strat_name}（USDT 基準）",
        xaxis_title="日期", yaxis_title="累積報酬率"
    )
    return fig

def plot_strategy_comparison_line(result_map):
    """多策略累積報酬率折線比較圖（所有交易對 × 策略）"""
    fig = go.Figure()
    for (crypto_code, strat_name), df in result_map.items():
        fig.add_trace(go.Scatter(
            x=df.index,
            y=(1 + df['Strategy']).cumprod() - 1,
            mode='lines',
            name=f"{crypto_code} × {strat_name}"
        ))
    fig.update_layout(
        title="📈 多交易對 × 多策略 累積報酬率比較（USDT 基準）",
        xaxis_title="日期", yaxis_title="累積報酬率",
        legend=dict(orientation="h", yanchor="bottom", y=-0.4, xanchor="center", x=0.5)
    )
    return fig

def plot_summary_bar(df_results):
    """策略績效總表長條圖"""
    fig = px.bar(
        df_results,
        x="交易對",
        y="策略報酬率(%)",
        color="策略",
        barmode="group",
        title="📊 各交易對 × 各策略 累積報酬率比較",
        text_auto=".1f"
    )
    return fig

# =====================
# 回測主流程
# =====================
if st.button("🚀 開始回測"):
    if not selected_cryptos:
        st.error("請至少選擇一個交易對")
        st.stop()

    TRADING_DAYS = 365
    results = []
    result_map = {}   # key: (crypto_code, strat_name) → df

    for crypto in selected_cryptos:
        crypto_code = crypto.split("(")[-1].strip(")")

        with st.spinner(f"下載 {crypto_code} 資料中..."):
            df_raw = fetch_crypto_data(crypto_code, start_date, end_date, interval)

        if df_raw.empty:
            st.warning(f"⚠️ {crypto_code} 無法取得資料，跳過")
            continue

        df_raw = convert_to_usdt(df_raw, crypto_code, start_date, end_date, interval)

        # 每個交易對跑所有選擇的策略
        st.markdown(f"---\n### 🪙 {crypto_code}")
        for strat_name in strategies_selected:
            try:
                df = run_strategy(df_raw, strat_name)
            except Exception as e:
                st.warning(f"⚠️ {crypto_code} × {strat_name} 執行失敗：{e}")
                continue

            if df.empty:
                st.warning(f"⚠️ {crypto_code} × {strat_name} 結果為空，跳過")
                continue

            result_map[(crypto_code, strat_name)] = df

            # 單一交易對 × 單一策略績效圖
            st.plotly_chart(plot_single_crypto(df, crypto_code, strat_name), use_container_width=True)

            # 計算績效指標
            cum_strategy  = (1 + df['Strategy']).cumprod().iloc[-1] - 1
            cum_buyhold   = (1 + df['DailyReturn']).cumprod().iloc[-1] - 1
            sharpe        = (df['Strategy'].mean() / df['Strategy'].std()) * (TRADING_DAYS ** 0.5) if df['Strategy'].std() != 0 else 0
            cum_ret_series = (1 + df['Strategy']).cumprod()
            mdd           = ((cum_ret_series - cum_ret_series.cummax()) / cum_ret_series.cummax()).min()
            last_pos      = df['Position'].iloc[-1]
            signal_text   = "空手" if last_pos == 0 else ("持有（買入）" if last_pos == 1 else "放空")

            results.append({
                "交易對": crypto_code,
                "策略": strat_name,
                "買入持有報酬率(%)": round(cum_buyhold * 100, 2),
                "策略報酬率(%)": round(cum_strategy * 100, 2),
                "年化波動(%)": round(df['Strategy'].std() * (TRADING_DAYS ** 0.5) * 100, 2),
                "最大回撤(%)": round(mdd * 100, 2),
                "夏普比率": round(sharpe, 2),
                "最新訊號": signal_text,
            })

    # =====================
    # 彙總比較圖表
    # =====================
    if result_map:
        st.markdown("---")
        st.markdown("## 📊 多交易對 × 多策略 彙總比較")

        # 折線圖：所有組合累積報酬率
        st.plotly_chart(plot_strategy_comparison_line(result_map), use_container_width=True)

    if results:
        df_results = pd.DataFrame(results)

        # 長條圖：各交易對各策略報酬率
        st.plotly_chart(plot_summary_bar(df_results), use_container_width=True)

        # 績效總表
        st.markdown("### 📋 策略績效總表")
        st.dataframe(
            df_results.style.format({
                "買入持有報酬率(%)": "{:.2f}%",
                "策略報酬率(%)": "{:.2f}%",
                "年化波動(%)": "{:.2f}%",
                "最大回撤(%)": "{:.2f}%",
                "夏普比率": "{:.2f}",
            }),
            use_container_width=True
        )
    else:
        st.warning("無可用結果，請檢查交易對及策略選擇")