import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objs as go
import plotly.express as px
from itertools import product
from strategy import apply_strategy, strategies, stock_list
from database import load_stock_prices, save_stock_prices, delete_stock_prices
from risk import apply_friction_and_risk, calc_performance, build_risk_ui
import yfinance as yf
import os
import json

st.title("📈 台股策略回測系統")

TRADING_DAYS    = 240
USER_PREF_FILE  = "user_backtest_pref.json"
BEST_PARAM_FILE = "user_best_params.json"   # ✅ 最佳參數儲存檔

# =====================
# 最佳參數讀寫工具
# =====================
def load_best_params():
    if os.path.exists(BEST_PARAM_FILE):
        try:
            with open(BEST_PARAM_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_best_params(stock_code, strategy_name, params, metrics):
    data = load_best_params()
    key  = f"{stock_code}_{strategy_name}"
    data[key] = {
        "stock_code":    stock_code,
        "strategy_name": strategy_name,
        "params":        params,
        "metrics":       metrics,
        "saved_at":      pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
    }
    with open(BEST_PARAM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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

stock_select = st.selectbox("選擇股票", stock_options,
    index=stock_options.index(default_stock) if default_stock in stock_options else 0)
stock_code   = stock_select.split("(")[-1].strip(")")

col_d1, col_d2 = st.columns(2)
with col_d1:
    start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
with col_d2:
    end_date   = st.date_input("結束日期", pd.to_datetime("today"))

strategy_name = st.selectbox("選擇策略", list(strategies.keys()),
    index=list(strategies.keys()).index(default_strategy) if default_strategy in strategies else 0)
st.info(strategies[strategy_name]["description"])

# ✅ 最佳參數套用（用 session_state 管理，避免 checkbox 與 slider 渲染順序問題）
best_params_db = load_best_params()
best_key       = f"{stock_code}_{strategy_name}"

# session_state key：每個股票 × 策略組合獨立
ss_key = f"use_best_{best_key}"
if ss_key not in st.session_state:
    st.session_state[ss_key] = False

if best_key in best_params_db:
    saved = best_params_db[best_key]
    st.info(
        f"💾 此股票 × 策略已有儲存的最佳參數（{saved['saved_at']}）：" +
        "、".join([f"{k}={v}" for k, v in saved["params"].items()])
    )
    col_cb, col_btn = st.columns([3, 1])
    with col_cb:
        use_saved = st.checkbox(
            "✅ 套用已儲存的最佳參數",
            value=st.session_state[ss_key],
            key=f"cb_{ss_key}"
        )
    with col_btn:
        if st.button("🔄 套用並重新整理", key=f"apply_{ss_key}"):
            st.session_state[ss_key] = True
            st.rerun()
    # 同步 checkbox 狀態
    if use_saved != st.session_state[ss_key]:
        st.session_state[ss_key] = use_saved
        st.rerun()
else:
    use_saved = False

# 參數 widget：套用最佳時直接顯示數值（唯讀提示），否則顯示可調整的 slider
params = {}
saved_p = best_params_db.get(best_key, {}).get("params", {}) if use_saved else {}
for param, default in strategies[strategy_name]["parameters"].items():
    val = saved_p.get(param, default)
    if isinstance(default, int):
        if use_saved:
            # 套用最佳參數時：顯示固定值（不可調整，避免誤改）
            st.markdown(f"**{param}**：`{int(val)}`（最佳化參數）")
            params[param] = int(val)
        else:
            params[param] = st.slider(param, min_value=1, max_value=200,
                                      value=int(val), step=1)
    elif isinstance(default, float):
        if use_saved:
            st.markdown(f"**{param}**：`{float(val)}`（最佳化參數）")
            params[param] = float(val)
        else:
            params[param] = st.number_input(param, value=float(val), format="%.2f")
    else:
        params[param] = st.text_input(param, value=str(val))

min_days_required = int(params.get("突破天數", 20)) + 5
if (end_date - start_date).days < min_days_required:
    st.warning(f"⚠️ 資料區間太短（{(end_date - start_date).days} 天），至少需要 {min_days_required} 天")
    st.stop()

# ✅ 摩擦成本 & 停損停利設定
risk_cfg = build_risk_ui(prefix="bt_", market="stock")

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

def run_backtest(df, strategy_name, params, risk_cfg=None):
    try:
        df_s = apply_strategy(df.copy(), strategy_name, params)
    except Exception:
        return None
    if risk_cfg:
        df_s = apply_friction_and_risk(df_s, **risk_cfg)
    else:
        df_s['DailyReturn'] = df_s['Close'].pct_change()
        df_s['Strategy']    = df_s['Position'].shift(1) * df_s['DailyReturn']
    df_s['DailyReturn'] = df_s['Close'].pct_change()
    df_s = df_s.dropna(subset=['DailyReturn', 'Strategy'])
    df_s = df_s[df_s['DailyReturn'].abs() < 0.5]
    if df_s.empty:
        return None
    return calc_performance(df_s, TRADING_DAYS)

# ✅ 歷史買賣紀錄計算
def calc_trade_history(df_s):
    """
    從 Position 欄位的變化點抓出每筆進出場。
    回傳 DataFrame：買入日期、買入價、賣出日期、賣出價、持有天數、損益率
    """
    trades  = []
    pos     = df_s['Position'].values
    closes  = df_s['Close'].values
    dates   = df_s.index

    entry_date  = None
    entry_price = None

    for i in range(1, len(pos)):
        prev, curr = pos[i - 1], pos[i]

        # 進場：從 0 → 1
        if prev == 0 and curr == 1:
            entry_date  = dates[i]
            entry_price = closes[i]

        # 出場：從 1 → 0
        elif prev == 1 and curr == 0 and entry_date is not None:
            exit_date  = dates[i]
            exit_price = closes[i]
            hold_days  = (exit_date - entry_date).days
            pnl        = (exit_price - entry_price) / entry_price
            trades.append({
                "買入日期":  entry_date.date(),
                "買入價格":  round(float(entry_price), 2),
                "賣出日期":  exit_date.date(),
                "賣出價格":  round(float(exit_price), 2),
                "持有天數":  hold_days,
                "損益率(%)": round(pnl * 100, 2),
                "結果":      "✅ 獲利" if pnl > 0 else "❌ 虧損",
            })
            entry_date  = None
            entry_price = None

    # 尚未出場（仍持倉中）
    if entry_date is not None:
        last_price = closes[-1]
        pnl        = (last_price - entry_price) / entry_price
        trades.append({
            "買入日期":  entry_date.date(),
            "買入價格":  round(float(entry_price), 2),
            "賣出日期":  None,           # ✅ 用 None 避免型別混雜導致 pyarrow 報錯
            "賣出價格":  round(float(last_price), 2),
            "持有天數":  (dates[-1] - entry_date).days,
            "損益率(%)": round(pnl * 100, 2),
            "結果":      "🔄 持倉中",
        })

    df_t = pd.DataFrame(trades)
    if not df_t.empty:
        # 統一欄位型別：賣出日期轉字串，None 顯示為「持倉中」
        df_t["賣出日期"] = df_t["賣出日期"].apply(
            lambda x: str(x) if x is not None else "持倉中"
        )
        # 確保數值欄位是 float，避免 pyarrow 型別混雜
        df_t["買入價格"] = df_t["買入價格"].astype(float)
        df_t["賣出價格"] = df_t["賣出價格"].astype(float)
        df_t["持有天數"] = df_t["持有天數"].astype(int)
        df_t["損益率(%)"] = df_t["損益率(%)"].astype(float)
    return df_t

def plot_candlestick_with_signals(df_s):
    """蠟燭圖 + 買賣訊號標記"""
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df_s.index, open=df_s['Open'], high=df_s['High'],
        low=df_s['Low'],  close=df_s['Close'], name='價格',
        increasing_line_color='red', decreasing_line_color='green'
    ))
    # 買入點
    buy_pts  = df_s[df_s['Position'].diff() == 1]
    sell_pts = df_s[df_s['Position'].diff() == -1]
    fig.add_trace(go.Scatter(
        x=buy_pts.index, y=buy_pts['Close'], mode='markers', name='買入',
        marker=dict(symbol='triangle-up', size=12, color='red')
    ))
    fig.add_trace(go.Scatter(
        x=sell_pts.index, y=sell_pts['Close'], mode='markers', name='賣出',
        marker=dict(symbol='triangle-down', size=12, color='green')
    ))
    fig.update_layout(title="股票價格 + 買賣訊號", xaxis_title="日期", yaxis_title="價格",
                      xaxis_rangeslider_visible=False)
    return fig

def plot_strategy_performance(df_s):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_s.index, y=df_s['BuyHoldCumulative'],
                             mode='lines', name='買入持有報酬率', line=dict(dash='dash', color='gray')))
    fig.add_trace(go.Scatter(x=df_s.index, y=df_s['StrategyCumulative'],
                             mode='lines', name='策略報酬率', line=dict(color='royalblue')))
    fig.update_layout(title="策略 vs 買入持有累積報酬率",
                      xaxis_title="日期", yaxis_title="累積報酬率")
    return fig

# =====================
# 參數最佳化設定 UI
# =====================
st.markdown("---")
st.markdown("## 🔍 參數最佳化（Grid Search）")
st.caption("自動枚舉所有參數組合，找出最佳參數區間。參數範圍越大，耗時越長。")

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
            p_min  = c1.number_input("最小值", min_value=1, max_value=500, value=max(1, default - 10), step=1,  key=f"opt_min_{param}")
            p_max  = c2.number_input("最大值", min_value=1, max_value=500, value=default + 10,         step=1,  key=f"opt_max_{param}")
            p_step = c3.number_input("步長",   min_value=1, max_value=50,  value=5,                    step=1,  key=f"opt_step_{param}")
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

opt_target = st.selectbox("最佳化目標",
    ["夏普比率", "累積報酬率(%)", "最大回撤(%)（最小化）"],
    help="依照哪個指標選出最佳參數組合"
)

if not has_optimizable:
    st.info("此策略無數值型參數，無法進行最佳化。")

def estimate_combinations(opt_ranges):
    total = 1
    for _, (dtype, p_min, p_max, p_step) in opt_ranges.items():
        n = len(range(int(p_min), int(p_max) + 1, int(p_step))) if dtype == "int" \
            else len(np.arange(p_min, p_max + p_step * 0.5, p_step))
        total *= max(n, 1)
    return total

if has_optimizable and opt_ranges:
    est   = estimate_combinations(opt_ranges)
    color = "🟢" if est <= 100 else ("🟡" if est <= 500 else "🔴")
    st.caption(f"{color} 預估參數組合數：**{est}** 組")

# =====================
# 快取清除
# =====================
if st.button("🗑️ 清除此股票快取並重新下載"):
    delete_stock_prices(stock_code)
    st.success(f"✅ 已清除 {stock_code} 的快取資料")

st.markdown("---")

# =====================
# 一般回測
# =====================
if st.button("🚀 開始回測"):
    with open(USER_PREF_FILE, "w", encoding="utf-8") as f:
        json.dump({"stock": stock_select, "strategy": strategy_name}, f, ensure_ascii=False)

    with st.spinner("資料讀取中..."):
        df = load_price(stock_code, start_date, end_date)

    if df.empty:
        st.error("❌ 無法取得股票資料"); st.stop()
    if 'Close' not in df.columns:
        st.error("資料中沒有 Close 欄位"); st.stop()

    df = clean_price_data(df)
    if df.empty:
        st.error("❌ 清理後資料為空"); st.stop()

    try:
        df_s = apply_strategy(df, strategy_name, params)
    except Exception as e:
        st.error(f"策略執行失敗：{e}"); st.stop()

    # ✅ 套用摩擦成本 & 停損停利
    df_s = apply_friction_and_risk(
        df_s,
        buy_fee=risk_cfg["buy_fee"],
        sell_fee=risk_cfg["sell_fee"],
        sell_tax=risk_cfg["sell_tax"],
        stop_loss=risk_cfg["stop_loss"],
        take_profit=risk_cfg["take_profit"],
    )
    df_s['DailyReturn'] = df_s['Close'].pct_change()
    df_s = df_s.dropna(subset=['DailyReturn', 'Strategy'])
    abnormal = df_s['DailyReturn'].abs() >= 0.5
    if abnormal.any():
        st.warning(f"⚠️ 偵測到 {abnormal.sum()} 筆異常報酬率，已自動排除")
        df_s = df_s[~abnormal]
    if df_s.empty:
        st.error("❌ 回測結果為空"); st.stop()

    # 顯示停損停利觸發次數
    if 'StopTriggered' in df_s.columns:
        n_stop = df_s['StopTriggered'].sum()
        if n_stop > 0:
            st.info(f"🛑 停損/停利共觸發 {n_stop} 次")

    df_s['BuyHoldCumulative']  = (1 + df_s['DailyReturn']).cumprod() - 1
    df_s['StrategyCumulative'] = (1 + df_s['Strategy']).cumprod() - 1

    # ── 圖表 ──
    st.plotly_chart(plot_candlestick_with_signals(df_s), use_container_width=True)
    st.plotly_chart(plot_strategy_performance(df_s), use_container_width=True)

    # ── 績效總表 ──
    sharpe_ratio      = (df_s['Strategy'].mean() / df_s['Strategy'].std()) * TRADING_DAYS ** 0.5 \
                        if df_s['Strategy'].std() != 0 else 0
    cum_ret           = (1 + df_s['Strategy']).cumprod()
    strategy_drawdown = ((cum_ret - cum_ret.cummax()) / cum_ret.cummax()).min()
    strategy_risk     = df_s['Strategy'].std() * TRADING_DAYS ** 0.5

    st.markdown("### 📋 策略績效總表")
    total_cost = df_s['TradeCost'].sum() if 'TradeCost' in df_s.columns else 0
    st.table(pd.DataFrame({
        "項目": ["期間", "買入持有報酬率", "策略報酬率（含成本）", "策略風險（年化波動）", "最大回撤", "夏普比率", "總手續費成本"],
        "數值": [
            f"{df_s.index.min().date()} ~ {df_s.index.max().date()}",
            f"{df_s['BuyHoldCumulative'].iloc[-1]:.2%}",
            f"{df_s['StrategyCumulative'].iloc[-1]:.2%}",
            f"{strategy_risk:.2%}",
            f"{strategy_drawdown:.2%}",
            f"{sharpe_ratio:.2f}",
            f"{total_cost:.4%}",
        ]
    }))

    # ✅ 歷史買賣紀錄
    st.markdown("### 📒 歷史買賣紀錄")
    df_trades = calc_trade_history(df_s)

    if df_trades.empty:
        st.info("此期間無完整買賣紀錄")
    else:
        # 統計摘要
        closed = df_trades[df_trades["結果"] != "🔄 持倉中"]
        win_rate = (closed["損益率(%)"] > 0).mean() if len(closed) > 0 else 0
        avg_pnl  = closed["損益率(%)"].mean() if len(closed) > 0 else 0
        avg_hold = closed["持有天數"].mean()  if len(closed) > 0 else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("總交易次數", f"{len(closed)} 筆")
        m2.metric("勝率",       f"{win_rate:.1%}")
        m3.metric("平均損益率", f"{avg_pnl:.2f}%")
        m4.metric("平均持有天數", f"{avg_hold:.0f} 天")

        # 完整買賣紀錄表（色彩標示）
        def color_pnl(val):
            if isinstance(val, float):
                return "color: red" if val > 0 else "color: green"
            return ""

        st.dataframe(
            df_trades.style.map(color_pnl, subset=["損益率(%)"]),
            use_container_width=True
        )

        # 損益率分布圖
        if len(closed) > 0:
            fig_pnl = px.bar(
                closed, x="買入日期", y="損益率(%)",
                color="結果",
                color_discrete_map={"✅ 獲利": "red", "❌ 虧損": "green"},
                title="每筆交易損益率",
                text_auto=".1f"
            )
            fig_pnl.add_hline(y=0, line_dash="dash", line_color="gray")
            st.plotly_chart(fig_pnl, use_container_width=True)

    # ✅ 最新持倉狀態（優先使用 Position_adj，即套用停損停利後的持倉）
    st.markdown("---")
    st.markdown("### 🔔 今日操作建議")
    pos_col    = 'Position_adj' if 'Position_adj' in df_s.columns else 'Position'
    last_pos   = int(df_s[pos_col].iloc[-1])
    last_date  = df_s.index[-1].date()

    if last_pos == 1:
        signal_text  = "📈 持有（買入）"
        signal_color = "normal"
    elif last_pos == -1:
        signal_text  = "📉 放空中"
        signal_color = "inverse"
    else:
        signal_text  = "🟡 空手（無持倉）"
        signal_color = "off"

    # 找出最近一次買入/賣出日期
    buy_dates  = df_s[df_s[pos_col].diff() == 1].index
    sell_dates = df_s[df_s[pos_col].diff() == -1].index
    last_buy   = buy_dates[-1].date()  if len(buy_dates)  > 0 else "無"
    last_sell  = sell_dates[-1].date() if len(sell_dates) > 0 else "無"

    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("📅 資料最新日期", str(last_date))
    sc2.metric("🔔 當前建議操作", signal_text)
    sc3.metric("最近買入日",      str(last_buy))

    # 若目前持倉中，顯示未實現損益
    if last_pos == 1 and last_buy != "無":
        try:
            entry_price   = float(df_s.loc[buy_dates[-1], 'Close'])
            current_price = float(df_s['Close'].iloc[-1])
            unrealized    = (current_price - entry_price) / entry_price * 100
            pnl_str       = f"{unrealized:+.2f}%"
            sc3.metric("未實現損益",
                       pnl_str,
                       delta=pnl_str,
                       delta_color="normal" if unrealized >= 0 else "inverse")
        except Exception:
            pass

    # 最近賣出日另外顯示
    if last_sell != "無":
        st.caption(f"最近賣出日：{last_sell}")

    if last_pos == 1 and last_buy != "無":
        current_price = float(df_s['Close'].iloc[-1])
        entry_price   = float(df_s.loc[df_s.index[df_s.index.get_loc(buy_dates[-1])], 'Close'])
        unrealized    = (current_price - entry_price) / entry_price * 100
        st.metric("未實現損益",
                  f"{unrealized:+.2f}%",
                  delta_color="normal" if unrealized >= 0 else "inverse")

# =====================
# 參數最佳化
# =====================
if has_optimizable and st.button("⚙️ 開始參數最佳化"):
    with st.spinner("載入股價資料..."):
        df = load_price(stock_code, start_date, end_date)
    if df.empty:
        st.error("❌ 無法取得股票資料"); st.stop()
    df = clean_price_data(df)
    if df.empty:
        st.error("❌ 清理後資料為空"); st.stop()

    param_names  = []
    param_values = []
    for param, (dtype, p_min, p_max, p_step) in opt_ranges.items():
        param_names.append(param)
        vals = list(range(int(p_min), int(p_max) + 1, int(p_step))) if dtype == "int" \
               else [round(v, 4) for v in np.arange(p_min, p_max + p_step * 0.5, p_step)]
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
        metrics = run_backtest(df, strategy_name, test_params)
        if metrics:
            results.append({**test_params, **metrics})
        progress_bar.progress((i + 1) / total)
        if (i + 1) % 20 == 0 or (i + 1) == total:
            status_text.text(f"進度：{i+1}/{total} 組完成，有效結果：{len(results)} 組")

    progress_bar.empty()
    status_text.empty()

    if not results:
        st.error("❌ 所有參數組合均無法產生有效結果"); st.stop()

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

    # ✅ 把最佳參數存進 session_state，讓儲存按鈕在 rerun 後仍能讀到
    best_params_to_save = {p: (int(best[p]) if isinstance(strategies[strategy_name]["parameters"][p], int)
                               else round(float(best[p]), 4))
                           for p in param_names}
    best_params_to_save.update(fixed_params)
    best_metrics = {
        "累積報酬率(%)": float(best["累積報酬率(%)"]),
        "夏普比率":       float(best["夏普比率"]),
        "最大回撤(%)":    float(best["最大回撤(%)"]),
    }
    # 存進 session_state 供儲存按鈕使用
    st.session_state["pending_best_params"]  = best_params_to_save
    st.session_state["pending_best_metrics"] = best_metrics
    st.session_state["pending_best_stock"]   = stock_code
    st.session_state["pending_best_strategy"]= strategy_name

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
    df_best = apply_strategy(df.copy(), strategy_name, best_params_to_save)
    df_best['DailyReturn']        = df_best['Close'].pct_change()
    df_best['Strategy']           = df_best['Position'].shift(1) * df_best['DailyReturn']
    df_best = df_best.dropna(subset=['DailyReturn', 'Strategy'])
    df_best = df_best[df_best['DailyReturn'].abs() < 0.5]
    df_best['BuyHoldCumulative']  = (1 + df_best['DailyReturn']).cumprod() - 1
    df_best['StrategyCumulative'] = (1 + df_best['Strategy']).cumprod() - 1
    st.plotly_chart(plot_strategy_performance(df_best), use_container_width=True)

    # 原始 vs 最佳比較
    orig_metrics = run_backtest(df, strategy_name, params)
    st.markdown("### ⚖️ 原始參數 vs 最佳化參數比較")
    st.table(pd.DataFrame({
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
    }))

    st.warning(
        "⚠️ **過度擬合警告**：參數最佳化基於歷史資料，最佳參數不代表未來同樣有效。"
        "建議搭配不同時間段驗證。"
    )
# =====================
# 儲存最佳參數區塊（在最佳化 block 外，避免 rerun 後變數消失）
# =====================
if "pending_best_params" in st.session_state:
    pending_stock    = st.session_state.get("pending_best_stock", "")
    pending_strategy = st.session_state.get("pending_best_strategy", "")
    pending_params   = st.session_state["pending_best_params"]
    pending_metrics  = st.session_state["pending_best_metrics"]

    st.markdown("---")
    st.markdown("### 💾 儲存最佳化結果")
    st.info(
        f"**{pending_stock} × {pending_strategy}** 最佳參數：" +
        "、".join([f"{k}={v}" for k, v in pending_params.items()]) +
        f"　｜　報酬率 {pending_metrics['累積報酬率(%)']:.2f}%、"
        f"夏普 {pending_metrics['夏普比率']:.2f}、"
        f"回撤 {pending_metrics['最大回撤(%)']:.2f}%"
    )

    col_s1, col_s2 = st.columns([2, 1])
    with col_s1:
        if st.button("💾 儲存最佳參數（供回測 & 策略比較使用）", type="primary"):
            save_best_params(pending_stock, pending_strategy, pending_params, pending_metrics)
            # 儲存後更新 session_state，讓套用 checkbox 立即出現
            ss_key = f"use_best_{pending_stock}_{pending_strategy}"
            st.session_state[ss_key] = True
            del st.session_state["pending_best_params"]
            st.success("✅ 已儲存！頁面將重新整理，套用最佳參數進行回測。")
            st.rerun()
    with col_s2:
        if st.button("🗑️ 捨棄", help="不儲存此次最佳化結果"):
            del st.session_state["pending_best_params"]
            st.rerun()