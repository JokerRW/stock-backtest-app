# pages/3_策略比較.py
import streamlit as st
import pandas as pd
from strategy import apply_strategy, strategies, stock_list
from database import load_stock_prices, save_stock_prices
import yfinance as yf

st.title("📊 多股票多策略回測比較")

# 多選股票
stock_options = [f"{name} ({code})" for code, name in stock_list.items()]
stocks_selected = st.multiselect("選擇股票（多選）", stock_options, default=stock_options[:2])
stock_codes = [s.split("(")[-1].strip(")") for s in stocks_selected]

# 多選策略
strategy_names = list(strategies.keys())
strategies_selected = st.multiselect("選擇策略（多選）", strategy_names, default=strategy_names[:2])

# 選擇日期區間
start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
end_date = st.date_input("結束日期", pd.to_datetime("today"))

# 從網路抓資料函式（同主頁）
def fetch_stock_data_from_web(stock_code, start_date, end_date):
    df = yf.download(stock_code, start=start_date, end=end_date)
    if df.empty:
        return df
    df.reset_index(inplace=True)
    df['Date'] = pd.to_datetime(df['Date'])
    df.set_index('Date', inplace=True)
    return df

# 計算最大回撤
def max_drawdown(returns):
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    drawdown = (cum - peak) / peak
    return drawdown.min()

# 點擊執行回測
if st.button("執行回測比較"):
    if not stock_codes:
        st.error("請至少選擇一支股票")
        st.stop()
    if not strategies_selected:
        st.error("請至少選擇一個策略")
        st.stop()
    if end_date <= start_date:
        st.error("結束日期必須晚於開始日期")
        st.stop()

    results = []
    for stock_code in stock_codes:
        # 讀資料庫資料
        df = load_stock_prices(stock_code, start_date, end_date)
        if df.empty:
            st.info(f"資料庫無{stock_code}資料，從網路下載中...")
            df_web = fetch_stock_data_from_web(stock_code, start_date, end_date)
            if df_web.empty:
                st.warning(f"無法取得{stock_code}資料，跳過此股票")
                continue
            save_stock_prices(df_web, stock_code)
            df = load_stock_prices(stock_code, start_date, end_date)
        if df.empty:
            st.warning(f"{stock_code}無法取得有效資料，跳過")
            continue
        if 'Close' not in df.columns:
            st.warning(f"{stock_code}資料缺 Close 欄位，跳過")
            continue
        df.index = pd.to_datetime(df.index)

        for strat in strategies_selected:
            params = strategies[strat]["parameters"]
            # 因是比較功能，直接用預設參數，不用再輸入細節（可擴充）
            try:
                df_strategy = apply_strategy(df.copy(), strat, params)
            except Exception as e:
                st.warning(f"{stock_code} {strat} 策略套用失敗: {e}")
                continue

            df_strategy['DailyReturn'] = df_strategy['Close'].pct_change()
            df_strategy['Strategy'] = df_strategy['Position'].shift(1) * df_strategy['DailyReturn']
            df_strategy.dropna(subset=['DailyReturn', 'Strategy', 'Position'], inplace=True)
            if df_strategy.empty:
                st.warning(f"{stock_code} {strat} 策略結果為空，跳過")
                continue

            # 計算指標
            cum_return = df_strategy['Strategy'].sum()  # 累積報酬率
            sharpe_ratio = (df_strategy['Strategy'].mean() / df_strategy['Strategy'].std()) * (252 ** 0.5) if df_strategy['Strategy'].std() != 0 else 0
            mdd = max_drawdown(df_strategy['Strategy'])

            results.append({
                "股票": stock_list.get(stock_code, stock_code),
                "股票代號": stock_code,
                "策略": strat,
                "期間": f"{start_date} ~ {end_date}",
                "累積報酬率": cum_return,
                "夏普比率": sharpe_ratio,
                "最大回撤": mdd,
            })

    if results:
        df_results = pd.DataFrame(results)
        df_results['累積報酬率(%)'] = df_results['累積報酬率'] * 100
        df_results['最大回撤(%)'] = df_results['最大回撤'] * 100
        df_results = df_results[['股票', '股票代號', '策略', '期間', '累積報酬率(%)', '夏普比率', '最大回撤(%)']]
        st.dataframe(df_results.style.format({
            '累積報酬率(%)': '{:.2f}%',
            '夏普比率': '{:.2f}',
            '最大回撤(%)': '{:.2f}%',
        }))

        # 繪製簡單柱狀圖比較累積報酬率
        import plotly.express as px
        fig = px.bar(df_results, x='股票', y='累積報酬率(%)', color='策略',
                     barmode='group', title='累積報酬率比較')
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("無可用結果，請檢查股票及策略選擇")