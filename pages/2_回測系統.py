import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objs as go
import plotly.express as px
from itertools import product
from strategy import apply_strategy, strategies, stock_list
from database import load_stock_prices, save_stock_prices, delete_stock_prices
import yfinance as yf
import os
import json

st.title("📈 台股策略回測系統")

TRADING_DAYS = 240
USER_PREF_FILE = "user_backtest_pref.json"

# =====================
# 使用者偏好
# =====================
stock_options = [f"{name} ({code})" for code, name in stock_list.items()]
if os.path.exists(USER_PREF_FILE):
    with open(USER_PREF_FILE, "r", encoding="utf-8") as f:
        user_pref = json.load(f)
    default_stock    = user_pref.get("stock", stock_options[0])
    default_strategy = user_pref.get("strategy", list(strategies.keys())[0])
else:
    default_stock    = stock_options[0]
    default_strategy = list(strategies.keys())[0]

stock_select  = st.selectbox("選擇股票", stock_options,
    index=stock_options.index(default_stock) if default_stock in stock_options else 0)
stock_code    = stock_select.split("(")[-1].strip(")")

col_d1, col_d2 = st.columns(2)
with col_d1:
    start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
with col_d2:
    end_date   = st.date_input("結束日期", pd.to_datetime("today"))

strategy_name = st.selectbox("選擇策略", list(strategies.keys()),
    index=list(strategies.keys()).index(default_strategy) if default_strategy in strategies else 0)
st.info(strategies[strategy_name]["description"])

# 手動參數（回測用）
params = {}
for param, default in strategies[strategy_name]["parameters"].items():
    if isinstance(default, int):
        params[param] = st.slider(param, min_value=1, max_value=200, value=default, step=1)
    elif isinstance(default, float):
        params[param] = st.number_input(param, value=default, format="%.2f")
    else:
        params[param] = st.text_input(param, value=str(default))

min_days_required = int(params.get("突破天數", 20)) + 5
if (end_date - start_date).days < min_days_required:
    st.warning(f"⚠️ 資料區間太短（{(end_date - start_date).days} 天），此策略至少需要 {min_days_required} 天")
    st.stop()

# =====================
# 輔助函式
# =====================
def fetch_stock_data_from_web(stock_code, start_date, end_date):
    df = yf.download(stock_code, start=start_date, end=end_date, auto_adjust=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    df.reset_index(inplace=True)
    df['Date'] = pd.to_datetime(df['Date'])
    df.set_index('Date', inplace=True)
    return df

def clean_price_data(df):
    df = df.sort_index()
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    return df[df['Close'].notna()].copy()

def run_backtest(df, strategy_name, params):
    """執行單次回測，回傳績效指標 dict；失敗回傳 None"""
    try:
        df_s = apply_strategy(df.copy(), strategy_name, params)
    except Exception:
        return None
    df_s['DailyReturn'] = df_s['Close'].pct_change()
    df_s['Strategy']    = df_s['Position'].shift(1) * df_s['DailyReturn']
    df_s = df_s.dropna(subset=['DailyReturn', 'Strategy'])
    df_s = df_s[df_s['DailyReturn'].abs() < 0.5]
    if df_s.empty:
        return None

    cum_s  = (1 + df_s['Strategy']).cumprod()
    cum_r  = cum_s.iloc[-1] - 1
    mdd    = ((cum_s - cum_s.cummax()) / cum_s.cummax()).min()
    sharpe = (df_s['Strategy'].mean() / df_s['Strategy'].std() * TRADING_DAYS ** 0.5
              if df_s['Strategy'].std() != 0 else 0)
    trades = int((df_s['Position'].diff().abs() > 0).sum())
    return {
        "累積報酬率(%)": round(cum_r * 100, 2),
        "夏普比率":       round(sharpe, 2),
        "最大回撤(%)":    round(mdd * 100, 2),
        "交易次數":       trades,
    }

def plot_candlestick(df):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'], name='價格'
    ))
    fig.update_layout(title="股票價格（蠟燭圖）", xaxis_title="日期", yaxis_title="價格")
    return fig

def plot_strategy_performance(df):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['BuyHoldCumulative'],
                             mode='lines', name='買入持有報酬率'))
    fig.add_trace(go.Scatter(x=df.index, y=df['StrategyCumulative'],
                             mode='lines', name='策略報酬率'))
    fig.update_layout(title="策略 vs 買入持有累積報酬率",
                      xaxis_title="日期", yaxis_title="累積報酬率")
    return fig

# =====================
# 參數最佳化設定
# =====================
st.markdown("---")
st.markdown("## 🔍 參數最佳化（Grid Search）")
st.caption("自動枚舉所有參數組合，找出最佳參數區間。參數範圍越大，耗時越長。")

# 僅針對 int/float 參數開放最佳化設定
opt_ranges = {}
has_optimizable = False
opt_cols = st.columns(2)
col_idx  = 0

for param, default in strategies[strategy_name]["parameters"].items():
    if isinstance(default, int):
        has_optimizable = True
        with opt_cols[col_idx % 2]:
            st.markdown(f"**{param}**")
            c1, c2, c3 = st.columns(3)
            p_min  = c1.number_input(f"最小值", min_value=1,   max_value=500, value=max(1, default - 10),  step=1,  key=f"opt_min_{param}")
            p_max  = c2.number_input(f"最大值", min_value=1,   max_value=500, value=default + 10,          step=1,  key=f"opt_max_{param}")
            p_step = c3.number_input(f"步長",   min_value=1,   max_value=50,  value=5,                     step=1,  key=f"opt_step_{param}")
        opt_ranges[param] = ("int", p_min, p_max, p_step)
        col_idx += 1

    elif isinstance(default, float):
        has_optimizable = True
        with opt_cols[col_idx % 2]:
            st.markdown(f"**{param}**")
            c1, c2, c3 = st.columns(3)
            p_min  = c1.number_input(f"最小值", value=round(default * 0.5, 2), step=0.1, format="%.2f", key=f"opt_min_{param}")
            p_max  = c2.number_input(f"最大值", value=round(default * 1.5, 2), step=0.1, format="%.2f", key=f"opt_max_{param}")
            p_step = c3.number_input(f"步長",   value=0.5,                     step=0.1, format="%.2f", key=f"opt_step_{param}")
        opt_ranges[param] = ("float", p_min, p_max, p_step)
        col_idx += 1

# 最佳化目標
opt_target = st.selectbox(
    "最佳化目標",
    ["夏普比率", "累積報酬率(%)", "最大回撤(%)（最小化）"],
    help="依照哪個指標選出最佳參數組合"
)

if not has_optimizable:
    st.info("此策略無數值型參數，無法進行最佳化。")

# 預估組合數
def estimate_combinations(opt_ranges):
    total = 1
    for param, (dtype, p_min, p_max, p_step) in opt_ranges.items():
        if dtype == "int":
            n = len(range(int(p_min), int(p_max) + 1, int(p_step)))
        else:
            n = len(np.arange(p_min, p_max + p_step * 0.5, p_step))
        total *= max(n, 1)
    return total

if has_optimizable and opt_ranges:
    est = estimate_combinations(opt_ranges)
    color = "🟢" if est <= 100 else ("🟡" if est <= 500 else "🔴")
    st.caption(f"{color} 預估參數組合數：**{est}** 組（建議 500 組以內，避免等待過久）")

# =====================
# 快取清除
# =====================
if st.button("🗑️ 清除此股票快取並重新下載"):
    delete_stock_prices(stock_code)
    st.success(f"✅ 已清除 {stock_code} 的快取資料")

st.markdown("---")

# =====================
# 載入股價（共用）
# =====================
@st.cache_data(show_spinner=False)
def load_price(stock_code, start_date, end_date):
    df = load_stock_prices(stock_code, start_date, end_date)
    if not df.empty:
        df.index = pd.to_datetime(df.index)
    if df.empty:
        df = fetch_stock_data_from_web(stock_code, start_date, end_date)
        if not df.empty:
            save_stock_prices(df, stock_code)
    return df

# =====================
# 一般回測
# =====================
if st.button("🚀 開始回測"):
    with open(USER_PREF_FILE, "w", encoding="utf-8") as f:
        json.dump({"stock": stock_select, "strategy": strategy_name}, f, ensure_ascii=False)

    with st.spinner("資料讀取中..."):
        df = load_price(stock_code, start_date, end_date)

    if df.empty:
        st.error("❌ 無法取得股票資料")
        st.stop()
    if 'Close' not in df.columns:
        st.error("資料中沒有 Close 欄位")
        st.stop()

    df = clean_price_data(df)
    if df.empty:
        st.error("❌ 清理後資料為空")
        st.stop()

    try:
        df_s = apply_strategy(df, strategy_name, params)
        st.write("策略後資料筆數：", len(df_s))
        st.dataframe(df_s[['Close', 'Position']].tail(10))
    except Exception as e:
        st.error(f"策略執行失敗：{e}")
        st.stop()

    df_s['DailyReturn'] = df_s['Close'].pct_change()
    df_s['Strategy']    = df_s['Position'].shift(1) * df_s['DailyReturn']
    df_s = df_s.dropna(subset=['DailyReturn', 'Strategy'])

    abnormal = df_s['DailyReturn'].abs() >= 0.5
    if abnormal.any():
        st.warning(f"⚠️ 偵測到 {abnormal.sum()} 筆異常報酬率，已自動排除")
        df_s = df_s[~abnormal]

    if df_s.empty:
        st.error("❌ 回測結果為空")
        st.stop()

    df_s['BuyHoldCumulative'] = (1 + df_s['DailyReturn']).cumprod() - 1
    df_s['StrategyCumulative'] = (1 + df_s['Strategy']).cumprod() - 1

    st.plotly_chart(plot_candlestick(df_s), use_container_width=True)
    st.plotly_chart(plot_strategy_performance(df_s), use_container_width=True)

    sharpe_ratio    = (df_s['Strategy'].mean() / df_s['Strategy'].std()) * TRADING_DAYS ** 0.5 \
                      if df_s['Strategy'].std() != 0 else 0
    cum_ret         = (1 + df_s['Strategy']).cumprod()
    strategy_drawdown = ((cum_ret - cum_ret.cummax()) / cum_ret.cummax()).min()

    period_str      = f"{df_s.index.min().date()} ~ {df_s.index.max().date()}"
    buy_hold_return = df_s['BuyHoldCumulative'].iloc[-1]
    strategy_return = df_s['StrategyCumulative'].iloc[-1]
    strategy_risk   = df_s['Strategy'].std() * TRADING_DAYS ** 0.5

    st.markdown(f"### 📊 策略夏普比率：{sharpe_ratio:.2f}")
    summary_df = pd.DataFrame({
        "項目": ["期間", "買入持有報酬率", "策略報酬率", "策略風險（年化波動）", "最大回撤"],
        "數值": [period_str, f"{buy_hold_return:.2%}", f"{strategy_return:.2%}",
                 f"{strategy_risk:.2%}", f"{strategy_drawdown:.2%}"]
    })
    st.markdown("### 📋 策略績效總表")
    st.table(summary_df)

    last_pos    = df_s['Position'].iloc[-1]
    last_date   = df_s.index[-1].date()
    signal_text = "空手" if last_pos == 0 else ("持有（買入）" if last_pos == 1 else "放空")
    st.markdown(f"### 🔔 最新交易訊號：**{signal_text}** （日期：{last_date}）")

# =====================
# 參數最佳化
# =====================
if has_optimizable and st.button("⚙️ 開始參數最佳化"):
    with st.spinner("載入股價資料..."):
        df = load_price(stock_code, start_date, end_date)

    if df.empty:
        st.error("❌ 無法取得股票資料")
        st.stop()

    df = clean_price_data(df)
    if df.empty:
        st.error("❌ 清理後資料為空")
        st.stop()

    # 建立參數網格
    param_names  = []
    param_values = []
    for param, (dtype, p_min, p_max, p_step) in opt_ranges.items():
        param_names.append(param)
        if dtype == "int":
            vals = list(range(int(p_min), int(p_max) + 1, int(p_step)))
        else:
            vals = [round(v, 4) for v in np.arange(p_min, p_max + p_step * 0.5, p_step)]
        param_values.append(vals)

    # 非最佳化參數（字串類型）維持原值
    fixed_params = {k: v for k, v in params.items() if k not in opt_ranges}

    all_combos = list(product(*param_values))
    total      = len(all_combos)

    st.info(f"🔄 共 {total} 組參數組合，開始掃描...")
    progress_bar = st.progress(0)
    status_text  = st.empty()

    results = []
    for i, combo in enumerate(all_combos):
        test_params = dict(zip(param_names, combo))
        test_params.update(fixed_params)

        metrics = run_backtest(df, strategy_name, test_params)
        if metrics:
            row = {**test_params, **metrics}
            results.append(row)

        progress_bar.progress((i + 1) / total)
        if (i + 1) % 20 == 0 or (i + 1) == total:
            status_text.text(f"進度：{i+1}/{total} 組完成，有效結果：{len(results)} 組")

    progress_bar.empty()
    status_text.empty()

    if not results:
        st.error("❌ 所有參數組合均無法產生有效結果，請調整參數範圍")
        st.stop()

    df_opt = pd.DataFrame(results)

    # 依最佳化目標排序
    if opt_target == "最大回撤(%)（最小化）":
        df_opt = df_opt.sort_values("最大回撤(%)", ascending=True)   # 回撤越小越好
    elif opt_target == "夏普比率":
        df_opt = df_opt.sort_values("夏普比率", ascending=False)
    else:
        df_opt = df_opt.sort_values("累積報酬率(%)", ascending=False)

    best   = df_opt.iloc[0]
    worst  = df_opt.iloc[-1]

    st.success(f"✅ 掃描完成！共 {len(df_opt)} 組有效結果")

    # ── 最佳參數展示 ──
    st.markdown("### 🏆 最佳參數組合")
    best_cols = st.columns(len(param_names) + 4)
    for i, p in enumerate(param_names):
        best_cols[i].metric(p, best[p])
    best_cols[len(param_names)].metric("累積報酬率", f"{best['累積報酬率(%)']:.2f}%")
    best_cols[len(param_names)+1].metric("夏普比率",  f"{best['夏普比率']:.2f}")
    best_cols[len(param_names)+2].metric("最大回撤",  f"{best['最大回撤(%)']:.2f}%")
    best_cols[len(param_names)+3].metric("交易次數",  f"{int(best['交易次數'])}")

    # ── Top 20 結果表 ──
    st.markdown("### 📋 Top 20 參數組合")
    fmt_dict = {"累積報酬率(%)": "{:.2f}%", "夏普比率": "{:.2f}", "最大回撤(%)": "{:.2f}%"}
    st.dataframe(
        df_opt.head(20).style.format(fmt_dict)
            .background_gradient(subset=["累積報酬率(%)"], cmap="RdYlGn")
            .background_gradient(subset=["夏普比率"],      cmap="RdYlGn")
            .background_gradient(subset=["最大回撤(%)"],   cmap="RdYlGn_r"),
        use_container_width=True
    )

    # ── 熱力圖（限兩個 int 參數時顯示）──
    int_params = [p for p in param_names if opt_ranges[p][0] == "int"]
    if len(int_params) >= 2:
        p1, p2 = int_params[0], int_params[1]
        metric_col = "夏普比率" if opt_target == "夏普比率" else "累積報酬率(%)"
        st.markdown(f"### 🗺️ 參數熱力圖（{p1} × {p2} → {metric_col}）")

        pivot = df_opt.pivot_table(
            index=p1, columns=p2, values=metric_col, aggfunc="mean"
        )
        fig_heat = px.imshow(
            pivot,
            labels=dict(x=p2, y=p1, color=metric_col),
            color_continuous_scale="RdYlGn",
            title=f"{strategy_name} 參數熱力圖",
            aspect="auto"
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    # ── 單參數折線圖 ──
    if len(int_params) == 1:
        p1 = int_params[0]
        metric_col = "夏普比率" if opt_target == "夏普比率" else "累積報酬率(%)"
        df_line = df_opt.groupby(p1)[metric_col].mean().reset_index()
        fig_line = px.line(df_line, x=p1, y=metric_col,
                           title=f"{p1} 對 {metric_col} 的影響",
                           markers=True)
        fig_line.add_vline(x=best[p1], line_dash="dash", line_color="red",
                           annotation_text=f"最佳={best[p1]}")
        st.plotly_chart(fig_line, use_container_width=True)

    # ── 用最佳參數直接跑回測 ──
    st.markdown("### 📈 使用最佳參數執行回測")
    best_params = {p: best[p] for p in param_names}
    best_params.update(fixed_params)

    df_best = apply_strategy(df.copy(), strategy_name, best_params)
    df_best['DailyReturn']       = df_best['Close'].pct_change()
    df_best['Strategy']          = df_best['Position'].shift(1) * df_best['DailyReturn']
    df_best = df_best.dropna(subset=['DailyReturn', 'Strategy'])
    df_best = df_best[df_best['DailyReturn'].abs() < 0.5]
    df_best['BuyHoldCumulative'] = (1 + df_best['DailyReturn']).cumprod() - 1
    df_best['StrategyCumulative'] = (1 + df_best['Strategy']).cumprod() - 1

    st.plotly_chart(plot_strategy_performance(df_best), use_container_width=True)

    # 最佳 vs 原始參數比較表
    orig_metrics = run_backtest(df, strategy_name, params)
    compare_df = pd.DataFrame({
        "項目": ["累積報酬率(%)", "夏普比率", "最大回撤(%)", "交易次數"],
        "原始參數": [
            f"{orig_metrics['累積報酬率(%)']:.2f}%" if orig_metrics else "N/A",
            f"{orig_metrics['夏普比率']:.2f}"       if orig_metrics else "N/A",
            f"{orig_metrics['最大回撤(%)']:.2f}%"   if orig_metrics else "N/A",
            str(orig_metrics['交易次數'])            if orig_metrics else "N/A",
        ],
        "最佳化參數": [
            f"{best['累積報酬率(%)']:.2f}%",
            f"{best['夏普比率']:.2f}",
            f"{best['最大回撤(%)']:.2f}%",
            str(int(best['交易次數'])),
        ]
    })
    st.markdown("### ⚖️ 原始參數 vs 最佳化參數比較")
    st.table(compare_df)

    st.warning(
        "⚠️ **過度擬合警告**：參數最佳化是基於歷史資料，最佳參數在樣本內表現好，"
        "不代表未來同樣有效。建議搭配樣本外驗證（Walk-Forward）使用。"
    )