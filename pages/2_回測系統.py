import streamlit as st
import pandas as pd
import plotly.graph_objs as go
from strategy import apply_strategy, strategies, stock_list
from database import load_stock_prices, save_stock_prices
import yfinance as yf

st.title("📈 台股策略回測系統")

# 股票選擇
stock_options = [f"{name} ({code})" for code, name in stock_list.items()]
stock_select = st.selectbox("選擇股票", stock_options)
stock_code = stock_select.split("(")[-1].strip(")")

# 日期選擇
start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
end_date = st.date_input("結束日期", pd.to_datetime("today"))

# 策略選擇與參數
strategy_name = st.selectbox("選擇策略", list(strategies.keys()))
st.info(strategies[strategy_name]["description"])
params = {}
for param, default in strategies[strategy_name]["parameters"].items():
    if isinstance(default, int):
        params[param] = st.number_input(param, value=default, step=1)
    elif isinstance(default, float):
        params[param] = st.number_input(param, value=default, format="%.2f")
    else:
        params[param] = st.text_input(param, value=str(default))

# 策略最低資料天數檢查
min_days_required = int(params.get("突破天數", 20)) + 5
if (end_date - start_date).days < min_days_required:
    st.warning(f"⚠️ 資料區間太短（{(end_date - start_date).days} 天），此策略至少需要 {min_days_required} 天")
    st.stop()

# 從網路抓資料
def fetch_stock_data_from_web(stock_code, start_date, end_date):
    df = yf.download(stock_code, start=start_date, end=end_date)
    if df.empty:
        return df
    df.reset_index(inplace=True)
    df['Date'] = pd.to_datetime(df['Date'])
    df.set_index('Date', inplace=True)
    return df

# 畫蠟燭圖
def plot_candlestick(df):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'],
        high=df['High'],
        low=df['Low'],
        close=df['Close'],
        name='價格'
    ))
    fig.update_layout(title="股票價格（蠟燭圖）", xaxis_title="日期", yaxis_title="價格")
    return fig

# 畫策略績效圖
def plot_strategy_performance(df):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['DailyReturn'].cumsum(), mode='lines', name='買入持有報酬率'))
    fig.add_trace(go.Scatter(x=df.index, y=df['Strategy'].cumsum(), mode='lines', name='策略報酬率'))
    fig.update_layout(title="策略 vs 買入持有累積報酬率", xaxis_title="日期", yaxis_title="累積報酬率")
    return fig

# 點擊回測按鈕
if st.button("開始回測"):
    with st.spinner("資料讀取中..."):
        df = load_stock_prices(stock_code, start_date, end_date)
        if not df.empty:
            df.index = pd.to_datetime(df.index)

        if df.empty:
            st.info("資料庫沒有該區間資料，正在從網路下載...")
            df_web = fetch_stock_data_from_web(stock_code, start_date, end_date)
            if df_web.empty:
                st.error("❌ 從網路無法取得股票資料，請稍後再試或換其他條件")
                st.stop()
            save_stock_prices(df_web, stock_code)
            df = load_stock_prices(stock_code, start_date, end_date)
            if not df.empty:
                df.index = pd.to_datetime(df.index)

        if df.empty:
            st.warning("⚠️ 沒有取得股票資料，請調整日期區間或股票代碼")
            st.stop()

    if 'Close' not in df.columns:
        st.error("資料中沒有 Close 欄位，無法回測")
        st.stop()

    try:
        df = apply_strategy(df, strategy_name, params)
        st.write("策略後資料筆數：", len(df))
        st.dataframe(df[['Close', 'Position']].tail(10))
    except Exception as e:
        st.error(f"策略執行失敗：{e}")
        st.stop()

    df['DailyReturn'] = df['Close'].pct_change()
    df['Strategy'] = df['Position'].shift(1) * df['DailyReturn']
    df.dropna(subset=['DailyReturn', 'Strategy', 'Position'], inplace=True)

    if df.empty:
        st.error("❌ 回測結果為空，請檢查策略參數或資料")
        st.stop()

    # 畫圖
    st.plotly_chart(plot_candlestick(df), use_container_width=True)
    st.plotly_chart(plot_strategy_performance(df), use_container_width=True)

    # 顯示夏普比率
    sharpe_ratio = (df['Strategy'].mean() / df['Strategy'].std()) * (252 ** 0.5) if df['Strategy'].std() != 0 else 0
    st.markdown(f"### 📊 策略夏普比率（Sharpe Ratio）：{sharpe_ratio:.2f}")

    # 顯示績效總表
    period_str = f"{df.index.min().date()} ~ {df.index.max().date()}"
    buy_hold_return = df['DailyReturn'].cumsum().iloc[-1]
    strategy_return = df['Strategy'].cumsum().iloc[-1]
    strategy_risk = df['Strategy'].std() * (252 ** 0.5)
    strategy_drawdown = (df['Strategy'].cumsum().cummax() - df['Strategy'].cumsum()).max()

    summary_df = pd.DataFrame({
        "項目": ["期間", "買入持有報酬率", "策略報酬率", "策略風險（年化波動）", "最大回撤"],
        "數值": [
            period_str,
            f"{buy_hold_return:.2%}",
            f"{strategy_return:.2%}",
            f"{strategy_risk:.2%}",
            f"{-strategy_drawdown:.2%}"
        ]
    })

    st.markdown("### 📋 策略績效總表")
    st.table(summary_df)

    # 顯示最新交易訊號
    if not df.empty:
        last_pos = df['Position'].iloc[-1]
        signal_text = "持有"
        if last_pos == 1:
            signal_text = "買入"
        elif last_pos == -1:
            signal_text = "賣出"
        st.markdown(f"### 🔔 最新交易訊號：**{signal_text}**")
