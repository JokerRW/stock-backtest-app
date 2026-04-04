import streamlit as st
import pandas as pd
import plotly.graph_objs as go
from strategy import apply_strategy, strategies  # 保留原有策略架構
import ccxt
import time

st.title("💰 虛擬幣策略回測系統（USDT 統一基準 + 多交易對 + 多時間週期）")

# 幣種清單
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

# 多選交易對
crypto_options = [f"{name} ({code})" for code, name in crypto_list.items()]
selected_cryptos = st.multiselect("選擇交易對（可多選）", crypto_options, default=[crypto_options[0]])

# 日期選擇
start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
end_date = st.date_input("結束日期", pd.to_datetime("today"))

# 選擇 K 線週期
interval_options = {
    "日線": "1d",
    "4 小時線": "4h",
    "1 小時線": "1h",
    "30 分鐘線": "30m"
}
interval_name = st.selectbox("選擇 K 線週期", list(interval_options.keys()), index=0)
interval = interval_options[interval_name]

# SMA/Hull 趨勢策略
def sma_hull_trend_strategy(df, params):
    type_ = params.get("type", "sma")
    n1 = params.get("n1", 30)
    n2 = params.get("n2", 130)
    df = df.copy()
    
    if type_ == "sma":
        df['trend1'] = df['Close'].rolling(n1).mean()
        df['trend2'] = df['Close'].rolling(n2).mean()
    elif type_ == "hull":
        def WMA(series, n):
            weights = pd.Series(range(1, n+1))
            return series.rolling(n).apply(lambda x: (x*weights).sum()/weights.sum(), raw=True)
        half = int(n1/2)
        df['trend1'] = WMA(2*WMA(df['Close'], half) - WMA(df['Close'], n1), int(n1**0.5))
        half = int(n2/2)
        df['trend2'] = WMA(2*WMA(df['Close'], half) - WMA(df['Close'], n2), int(n2**0.5))
    else:
        df['trend1'] = df['Close']
        df['trend2'] = df['Close']
    
    df['Position'] = 0
    df.loc[(df['trend1'] > df['trend2']) & (df['trend1'].shift() <= df['trend2'].shift()), 'Position'] = 1
    df.loc[(df['trend1'] < df['trend2']) & (df['trend1'].shift() >= df['trend2'].shift()), 'Position'] = -1
    df['Position'].ffill(inplace=True)
    return df

strategies.update({
    "SMA/Hull 趨勢策略": {
        "description": "短期均線與長期均線交叉策略 (SMA/HullMA)",
        "parameters": {"type": "sma", "n1": 30, "n2": 130},
        "function": sma_hull_trend_strategy
    }
})

# 選擇策略與參數
strategy_name = st.selectbox("選擇策略", list(strategies.keys()))
st.info(strategies[strategy_name]["description"])
params = {}
for param, default in strategies[strategy_name]["parameters"].items():
    if isinstance(default, int):
        params[param] = st.number_input(param, value=default, step=1)
    elif isinstance(default, float):
        params[param] = st.number_input(param, value=default, format="%.2f")
    else:
        params[param] = st.selectbox(param, options=["sma","hull"], index=0 if default=="sma" else 1)

# ccxt 下載資料
@st.cache_data
def fetch_crypto_data(symbol, start_date, end_date, interval):
    exchange = ccxt.binance()
    since = int(time.mktime(pd.Timestamp(start_date).timetuple()) * 1000)
    df = pd.DataFrame()
    limit = 1000
    all_ohlcv = []
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
        return df
    df = pd.DataFrame(all_ohlcv, columns=['timestamp','Open','High','Low','Close','Volume'])
    df['Date'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('Date', inplace=True)
    return df

# BTC 計價交易對轉 USDT
def convert_to_usdt(df, symbol, start_date, end_date, interval):
    if symbol.endswith("/BTC"):
        btc_df = fetch_crypto_data("BTC/USDT", start_date, end_date, interval)
        df = df.merge(btc_df[['Close']], left_index=True, right_index=True, how='left', suffixes=('','_BTC'))
        df['Close'] = df['Close'] * df['Close_BTC']
        df.drop(columns=['Close_BTC'], inplace=True)
    return df

# 畫單交易對策略績效
def plot_strategy_performance(df, crypto_code):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=(1+df['Close'].pct_change()).cumprod()-1, mode='lines', name='買入持有'))
    fig.add_trace(go.Scatter(x=df.index, y=(1+df['Position'].shift(1)*df['Close'].pct_change()).cumprod()-1, mode='lines', name='策略'))
    fig.update_layout(title=f"{crypto_code} 累積報酬率 (USDT 基準)", xaxis_title="日期", yaxis_title="累積報酬率")
    return fig

# 畫多交易對比較圖
def plot_multi_crypto_comparison(result_dfs):
    fig = go.Figure()
    for crypto_code, df in result_dfs.items():
        fig.add_trace(go.Scatter(
            x=df.index,
            y=(1+df['Position'].shift(1)*df['Close'].pct_change()).cumprod()-1,
            mode='lines',
            name=crypto_code
        ))
    fig.update_layout(title="多交易對策略累積報酬率比較 (USDT 基準)", xaxis_title="日期", yaxis_title="累積報酬率")
    return fig

# 回測按鈕
if st.button("開始回測"):
    TRADING_DAYS = 365
    results = []
    result_dfs = {}

    for crypto in selected_cryptos:
        crypto_code = crypto.split("(")[-1].strip(")")
        df = fetch_crypto_data(crypto_code, start_date, end_date, interval)
        if df.empty:
            st.warning(f"{crypto_code} 無法取得資料，跳過")
            continue

        df = convert_to_usdt(df, crypto_code, start_date, end_date, interval)

        try:
            # 判斷策略類型
            if "function" in strategies[strategy_name]:
                df = strategies[strategy_name]["function"](df, params)
            else:
                df = apply_strategy(df, strategy_name, params)
        except Exception as e:
            st.warning(f"{crypto_code} 策略執行失敗：{e}")
            continue
        
        df['DailyReturn'] = df['Close'].pct_change()
        df['Strategy'] = df['Position'].shift(1) * df['DailyReturn']
        df.dropna(subset=['DailyReturn','Strategy'], inplace=True)
        result_dfs[crypto_code] = df.copy()
        
        cum_return = (1 + df['Strategy']).cumprod()
        drawdown = (cum_return - cum_return.cummax()) / cum_return.cummax()
        sharpe_ratio = (df['Strategy'].mean()/df['Strategy'].std())*(TRADING_DAYS**0.5) if df['Strategy'].std()!=0 else 0
        
        results.append({
            "交易對": crypto_code,
            "買入持有報酬率": f"{(1+df['DailyReturn']).cumprod().iloc[-1]-1:.2%}",
            "策略報酬率": f"{(1+df['Strategy']).cumprod().iloc[-1]-1:.2%}",
            "策略風險(年化波動)": f"{df['Strategy'].std()*(TRADING_DAYS**0.5):.2%}",
            "最大回撤": f"{drawdown.min():.2%}",
            "夏普比率": f"{sharpe_ratio:.2f}",
            "最新訊號": "持有" if df['Position'].iloc[-1]==0 else ("買入" if df['Position'].iloc[-1]==1 else "賣出")
        })
        
        st.plotly_chart(plot_strategy_performance(df, crypto_code), use_container_width=True)
    
    # 多交易對比較圖
    if result_dfs:
        st.plotly_chart(plot_multi_crypto_comparison(result_dfs), use_container_width=True)

    # 多交易對策略比較表
    if results:
        st.markdown("### 📋 多交易對策略比較表（USDT 基準）")
        st.table(pd.DataFrame(results))
