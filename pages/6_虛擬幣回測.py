import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objs as go
import plotly.express as px
from itertools import product
from strategy import apply_strategy, strategies
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

strategies.update({
    "SMA/Hull 趨勢策略": {
        "description": "短期均線與長期均線交叉策略 (SMA/HullMA)",
        "parameters":  {"type": "sma", "n1": 30, "n2": 130},
        "function":    sma_hull_trend_strategy
    }
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

def run_strategy(df, strat_name, custom_params=None):
    p = custom_params if custom_params else strategies[strat_name]["parameters"]
    if "function" in strategies[strat_name]:
        df = strategies[strat_name]["function"](df.copy(), p)
    else:
        df = apply_strategy(df.copy(), strat_name, p)
    df['DailyReturn'] = df['Close'].pct_change()
    df['Strategy']    = df['Position'].shift(1) * df['DailyReturn']
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

def run_backtest_opt(df, strat_name, test_params):
    try:
        df_s = run_strategy(df, strat_name, test_params)
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
            df = run_strategy(df_raw, strategy_name, params)
        except Exception as e:
            st.warning(f"⚠️ {crypto_code} × {strategy_name} 執行失敗：{e}"); continue

        if df.empty:
            st.warning(f"⚠️ {crypto_code} × {strategy_name} 結果為空，跳過"); continue

        result_map[(crypto_code, strategy_name)] = df
        st.plotly_chart(plot_single(df, crypto_code, strategy_name), use_container_width=True)

        m = calc_metrics(df)
        last_pos    = df['Position'].iloc[-1]
        signal_text = "空手" if last_pos == 0 else ("持有（買入）" if last_pos == 1 else "放空")
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
        m = run_backtest_opt(df_raw, strategy_name, test_params)
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
    orig_m = run_backtest_opt(df_raw, strategy_name, params)
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