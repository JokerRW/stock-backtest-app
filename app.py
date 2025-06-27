import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
from database import init_db, save_stock_prices, load_stock_prices

import plotly.graph_objs as go
from plotly.subplots import make_subplots

# 初始化資料庫（如表格尚未存在）
init_db()

TWII_SYMBOL = "^TWII"
TWII_NAME = "台灣加權指數"

st.set_page_config(page_title="台股大盤即時資訊", layout="wide")
st.title(f"📊 {TWII_NAME} 即時顯示與資料儲存")

@st.cache_data(ttl=3600)
def fetch_twii():
    df = yf.download(TWII_SYMBOL, period="1y", auto_adjust=False)  # 不調整價，保留原始價
    df.reset_index(inplace=True)
    return df

df = fetch_twii()
save_stock_prices(df, TWII_SYMBOL)
df = load_stock_prices(TWII_SYMBOL)

if df.empty:
    st.error("❌ 找不到 TWII 資料，請稍後再試")
    st.stop()

# 顯示今日日期
today_str = datetime.now().strftime("%Y-%m-%d (%A)")
st.markdown(f"### 🗓️ 今日日期：{today_str}")

if df['Close'].isna().iloc[-1]:
    st.warning("⚠️ 今日尚未收盤，顯示為前一交易日資料")
    df = df[df['Close'].notna()]

latest = df.iloc[-1]
previous = df.iloc[-2]

latest_close = round(float(latest["Close"]), 2)
delta = latest_close - float(previous["Close"])

st.metric("📈 最新收盤價", f"{latest_close:.2f}", delta=f"{delta:.2f}")

# 計算技術指標
df['MA20'] = df['Close'].rolling(window=20).mean()
df['MA60'] = df['Close'].rolling(window=60).mean()

ema12 = df['Close'].ewm(span=12, adjust=False).mean()
ema26 = df['Close'].ewm(span=26, adjust=False).mean()
df['MACD'] = ema12 - ema26
df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
df['MACD_Hist'] = df['MACD'] - df['Signal']

# 建立含三個子圖的圖表（價格+量，MACD）
fig = make_subplots(
    rows=3, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=[0.5, 0.2, 0.3],
    specs=[[{"type": "candlestick"}],
           [{"type": "bar"}],
           [{"type": "scatter"}]]
)

# 蠟燭圖
fig.add_trace(go.Candlestick(
    x=df.index,
    open=df['Open'],
    high=df['High'],
    low=df['Low'],
    close=df['Close'],
    name='價格'
), row=1, col=1)

# 均線
fig.add_trace(go.Scatter(
    x=df.index,
    y=df['MA20'],
    mode='lines',
    line=dict(color='blue', width=1),
    name='MA20'
), row=1, col=1)

fig.add_trace(go.Scatter(
    x=df.index,
    y=df['MA60'],
    mode='lines',
    line=dict(color='orange', width=1),
    name='MA60'
), row=1, col=1)

# 成交量柱狀圖
fig.add_trace(go.Bar(
    x=df.index,
    y=df['Volume'],
    name='成交量',
    marker_color='lightblue'
), row=2, col=1)

# MACD 線
fig.add_trace(go.Scatter(
    x=df.index,
    y=df['MACD'],
    mode='lines',
    line=dict(color='green', width=1),
    name='MACD'
), row=3, col=1)

fig.add_trace(go.Scatter(
    x=df.index,
    y=df['Signal'],
    mode='lines',
    line=dict(color='red', width=1),
    name='訊號線'
), row=3, col=1)

fig.add_trace(go.Bar(
    x=df.index,
    y=df['MACD_Hist'],
    name='MACD柱狀圖',
    marker_color='gray'
), row=3, col=1)

fig.update_layout(
    height=800,
    xaxis_rangeslider_visible=False,
    title_text=f"{TWII_NAME} 一年走勢（含均線、成交量與MACD）",
    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1)
)

st.plotly_chart(fig, use_container_width=True)
