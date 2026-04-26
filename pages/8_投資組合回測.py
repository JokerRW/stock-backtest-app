import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objs as go
import plotly.express as px
import yfinance as yf
from strategy import apply_strategy, strategies, stock_list
from database import load_stock_prices, save_stock_prices
from risk import apply_friction_and_risk, calc_performance, build_risk_ui

st.title("💼 投資組合回測")
st.caption("同時持有多支股票，設定各自權重，計算整體投組報酬率、風險與大盤比較。")

TRADING_DAYS = 240
TWII_CODE    = "^TWII"

# =====================
# 股票與日期選擇
# =====================
st.markdown("## 📋 投資組合設定")

stock_options = [f"{name} ({code})" for code, name in stock_list.items()]

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
with col2:
    end_date   = st.date_input("結束日期", pd.to_datetime("today"))

selected_stocks = st.multiselect(
    "選擇投資組合股票（2～10 支）",
    stock_options,
    default=stock_options[:4],
    help="建議 2～10 支，太多會影響效能"
)

if not selected_stocks:
    st.warning("請至少選擇 2 支股票")
    st.stop()

stock_codes = [s.split("(")[-1].strip(")") for s in selected_stocks]
n_stocks    = len(stock_codes)

# =====================
# 權重設定
# =====================
st.markdown("### ⚖️ 持股權重設定")
st.caption("各股票權重總和須為 100%，超過或不足時系統自動提示。")

weight_mode = st.radio(
    "權重模式",
    ["等權重（自動平均）", "自訂權重"],
    horizontal=True
)

weights = {}
if weight_mode == "等權重（自動平均）":
    w = round(100 / n_stocks, 2)
    for code in stock_codes:
        weights[code] = w
    st.info(f"每支股票各佔 {w}%（共 {n_stocks} 支）")
else:
    cols = st.columns(min(n_stocks, 4))
    for i, code in enumerate(stock_codes):
        name = stock_list.get(code, code)
        default_w = round(100 / n_stocks, 1)
        weights[code] = cols[i % 4].number_input(
            f"{name}", min_value=0.0, max_value=100.0,
            value=default_w, step=1.0, format="%.1f",
            key=f"w_{code}"
        )
    total_w = sum(weights.values())
    if abs(total_w - 100) > 0.1:
        st.warning(f"⚠️ 目前權重總和：{total_w:.1f}%，請調整至 100%")
    else:
        st.success(f"✅ 權重總和：{total_w:.1f}%")

# 正規化權重
total_w = sum(weights.values())
if total_w > 0:
    norm_weights = {k: v / total_w for k, v in weights.items()}
else:
    norm_weights = {k: 1 / n_stocks for k in stock_codes}

# =====================
# 策略選擇（可選擇「買入持有」或套用策略）
# =====================
st.markdown("### 🔧 策略設定")

portfolio_mode = st.radio(
    "投組模式",
    ["買入持有（不擇時）", "套用技術策略（全部股票使用同一策略）"],
    horizontal=True
)

strategy_name = None
strategy_params = {}
if portfolio_mode == "套用技術策略（全部股票使用同一策略）":
    strategy_name = st.selectbox("選擇策略", list(strategies.keys()))
    st.caption(strategies[strategy_name]["description"])
    param_cols = st.columns(min(len(strategies[strategy_name]["parameters"]), 4))
    for i, (param, default) in enumerate(strategies[strategy_name]["parameters"].items()):
        with param_cols[i % len(param_cols)]:
            if isinstance(default, int):
                strategy_params[param] = st.number_input(param, value=default, step=1, key=f"pf_{param}")
            elif isinstance(default, float):
                strategy_params[param] = st.number_input(param, value=default, format="%.2f", key=f"pf_{param}")
            else:
                strategy_params[param] = st.text_input(param, value=str(default), key=f"pf_{param}")

# 摩擦成本設定
risk_cfg = build_risk_ui(prefix="pf_", market="stock")

st.markdown("---")

# =====================
# 輔助函式
# =====================
@st.cache_data(show_spinner=False)
def fetch_price(stock_code, start_date, end_date):
    df = load_stock_prices(stock_code, start_date, end_date)
    if not df.empty:
        df.index = pd.to_datetime(df.index)
        return df
    try:
        df = yf.download(stock_code, start=start_date, end=end_date,
                         auto_adjust=False, progress=False)
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

def get_stock_return_series(stock_code, start_date, end_date, strategy_name, params, risk_cfg):
    """取得單一股票的日報酬率序列"""
    df = fetch_price(stock_code, start_date, end_date)
    if df.empty or 'Close' not in df.columns:
        return None

    df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    df = df[df['Close'].notna()].sort_index()

    if strategy_name:
        try:
            df = apply_strategy(df, strategy_name, params)
        except Exception:
            return None
        df = apply_friction_and_risk(df, **risk_cfg)
        df['DailyReturn'] = df['Close'].pct_change()
        return_col = 'Strategy'
    else:
        # 買入持有：全程持有，只計算日報酬
        df['DailyReturn'] = df['Close'].pct_change()
        df['Strategy']    = df['DailyReturn']
        return_col = 'Strategy'

    df = df.dropna(subset=['DailyReturn', return_col])
    df = df[df['DailyReturn'].abs() < 0.5]
    return df[[return_col, 'DailyReturn', 'Close']].rename(columns={return_col: 'Return'})

def plot_portfolio_performance(df_portfolio, df_twii=None):
    """投組累積報酬率 + 大盤比較"""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_portfolio.index,
        y=df_portfolio['Portfolio_Cumulative'],
        mode='lines', name='投資組合',
        line=dict(color='royalblue', width=2)
    ))
    if df_twii is not None and not df_twii.empty:
        fig.add_trace(go.Scatter(
            x=df_twii.index,
            y=df_twii['TWII_Cumulative'],
            mode='lines', name='台灣加權指數',
            line=dict(color='gray', dash='dash')
        ))
    fig.update_layout(
        title="💼 投資組合 vs 大盤 累積報酬率",
        xaxis_title="日期", yaxis_title="累積報酬率",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return fig

def plot_individual_contribution(df_contributions):
    """各股票貢獻度長條圖"""
    fig = px.bar(
        df_contributions,
        x="股票", y="貢獻報酬率(%)",
        color="貢獻報酬率(%)",
        color_continuous_scale="RdYlGn",
        title="各股票對投組的報酬貢獻",
        text_auto=".2f"
    )
    fig.update_layout(coloraxis_showscale=False)
    return fig

def plot_weight_pie(weights_dict, stock_list):
    """權重圓餅圖"""
    labels = [f"{stock_list.get(k, k)}" for k in weights_dict]
    values = list(weights_dict.values())
    fig = px.pie(
        values=values, names=labels,
        title="投資組合權重分配",
        hole=0.35
    )
    return fig

def plot_drawdown(df_portfolio):
    """最大回撤走勢圖"""
    cum = df_portfolio['Portfolio_Cumulative'] + 1
    peak = cum.cummax()
    dd   = (cum - peak) / peak * 100
    fig  = go.Figure()
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values,
        mode='lines', fill='tozeroy',
        name='回撤(%)', line=dict(color='red')
    ))
    fig.update_layout(
        title="投資組合回撤走勢",
        xaxis_title="日期", yaxis_title="回撤(%)"
    )
    return fig

def plot_correlation_heatmap(return_df):
    """各股票報酬率相關係數熱力圖"""
    corr = return_df.corr()
    fig = px.imshow(
        corr,
        color_continuous_scale="RdBu_r",
        zmin=-1, zmax=1,
        title="各股票報酬率相關係數",
        text_auto=".2f"
    )
    return fig

def plot_monthly_heatmap(df_portfolio):
    """月度報酬率熱力圖"""
    monthly = df_portfolio['Portfolio_Return'].resample('ME').apply(
        lambda x: (1 + x).prod() - 1
    ) * 100
    monthly_df = monthly.reset_index()
    monthly_df.columns = ['Date', 'Return']
    monthly_df['Year']  = monthly_df['Date'].dt.year
    monthly_df['Month'] = monthly_df['Date'].dt.month
    pivot = monthly_df.pivot(index='Year', columns='Month', values='Return')
    pivot.columns = ['1月','2月','3月','4月','5月','6月',
                     '7月','8月','9月','10月','11月','12月'][:len(pivot.columns)]
    fig = px.imshow(
        pivot, color_continuous_scale="RdYlGn",
        title="月度報酬率熱力圖（%）", text_auto=".1f",
        zmin=-10, zmax=10
    )
    return fig

# =====================
# 執行投組回測
# =====================
if st.button("🚀 開始投組回測", type="primary"):
    if len(stock_codes) < 2:
        st.error("請至少選擇 2 支股票")
        st.stop()

    all_returns  = {}
    failed_codes = []

    with st.spinner("下載股票資料並計算回測中..."):
        for code in stock_codes:
            r = get_stock_return_series(
                code, start_date, end_date,
                strategy_name, strategy_params, risk_cfg
            )
            if r is None or r.empty:
                failed_codes.append(code)
                st.warning(f"⚠️ {code} 無法取得有效資料，跳過")
            else:
                all_returns[code] = r['Return']

    if len(all_returns) < 2:
        st.error("❌ 有效股票不足 2 支，無法建立投資組合")
        st.stop()

    # 對齊日期（取交集）
    return_df = pd.DataFrame(all_returns).dropna()

    if return_df.empty:
        st.error("❌ 各股票日期無法對齊，請調整日期區間")
        st.stop()

    # 計算加權投組日報酬
    valid_codes = list(all_returns.keys())
    valid_weights = {k: norm_weights[k] for k in valid_codes}
    total_valid_w = sum(valid_weights.values())
    norm_valid_w  = {k: v / total_valid_w for k, v in valid_weights.items()}

    portfolio_return = sum(
        return_df[code] * norm_valid_w[code]
        for code in valid_codes
    )

    df_portfolio = pd.DataFrame({
        'Portfolio_Return':     portfolio_return,
        'Portfolio_Cumulative': (1 + portfolio_return).cumprod() - 1
    })

    # 大盤對比
    df_twii = None
    try:
        twii_raw = yf.download(TWII_CODE, start=start_date, end=end_date,
                               auto_adjust=False, progress=False)
        if not twii_raw.empty:
            if isinstance(twii_raw.columns, pd.MultiIndex):
                twii_raw.columns = [col[0] for col in twii_raw.columns]
            twii_raw['Close'] = pd.to_numeric(twii_raw['Close'], errors='coerce')
            twii_raw = twii_raw[twii_raw['Close'].notna()]
            twii_r   = twii_raw['Close'].pct_change().dropna()
            # 對齊到投組日期
            twii_aligned = twii_r.reindex(df_portfolio.index).fillna(0)
            df_twii = pd.DataFrame({
                'TWII_Return':     twii_aligned,
                'TWII_Cumulative': (1 + twii_aligned).cumprod() - 1
            })
    except Exception:
        pass

    # =====================
    # 輸出結果
    # =====================
    st.success(f"✅ 投組回測完成！共 {len(valid_codes)} 支股票，日期對齊後共 {len(df_portfolio)} 個交易日")

    # 權重圓餅圖
    col_pie, col_metrics = st.columns([1, 2])
    with col_pie:
        st.plotly_chart(plot_weight_pie(norm_valid_w, stock_list), use_container_width=True)

    # 績效指標
    with col_metrics:
        st.markdown("### 📊 投組績效指標")
        cum_r   = df_portfolio['Portfolio_Cumulative'].iloc[-1]
        sharpe  = (portfolio_return.mean() / portfolio_return.std() * TRADING_DAYS ** 0.5
                   if portfolio_return.std() != 0 else 0)
        cum_s   = (1 + portfolio_return).cumprod()
        mdd     = ((cum_s - cum_s.cummax()) / cum_s.cummax()).min()
        vol     = portfolio_return.std() * TRADING_DAYS ** 0.5
        pos_days = (portfolio_return > 0).sum()
        win_rate = pos_days / len(portfolio_return)

        m1, m2 = st.columns(2)
        m1.metric("累積報酬率", f"{cum_r:.2%}")
        m2.metric("夏普比率",   f"{sharpe:.2f}")
        m1.metric("最大回撤",   f"{mdd:.2%}")
        m2.metric("年化波動",   f"{vol:.2%}")
        m1.metric("勝率（日）", f"{win_rate:.1%}")

        # 大盤比較
        if df_twii is not None:
            twii_cum = df_twii['TWII_Cumulative'].iloc[-1]
            alpha    = cum_r - twii_cum
            st.markdown("---")
            st.markdown("**vs 大盤**")
            c1, c2 = st.columns(2)
            c1.metric("大盤報酬率", f"{twii_cum:.2%}")
            c2.metric("超額報酬（Alpha）",
                      f"{alpha:+.2%}",
                      delta_color="normal" if alpha >= 0 else "inverse")

    # 累積報酬率圖
    st.plotly_chart(plot_portfolio_performance(df_portfolio, df_twii), use_container_width=True)

    # 回撤走勢圖
    st.plotly_chart(plot_drawdown(df_portfolio), use_container_width=True)

    # 月度熱力圖
    st.plotly_chart(plot_monthly_heatmap(df_portfolio), use_container_width=True)

    # 各股票貢獻度
    st.markdown("### 📊 各股票報酬貢獻分析")
    contributions = []
    for code in valid_codes:
        stock_cum  = (1 + return_df[code]).cumprod().iloc[-1] - 1
        weighted   = stock_cum * norm_valid_w[code]
        name       = stock_list.get(code, code)
        contributions.append({
            "股票":        f"{name}（{code}）",
            "個股報酬率(%)":  round(stock_cum * 100, 2),
            "權重(%)":     round(norm_valid_w[code] * 100, 2),
            "貢獻報酬率(%)": round(weighted * 100, 2),
        })

    df_contrib = pd.DataFrame(contributions).sort_values("貢獻報酬率(%)", ascending=False)
    st.plotly_chart(plot_individual_contribution(df_contrib), use_container_width=True)
    st.dataframe(
        df_contrib.style.format({
            "個股報酬率(%)":  "{:.2f}%",
            "權重(%)":        "{:.2f}%",
            "貢獻報酬率(%)":  "{:.2f}%",
        }).background_gradient(subset=["貢獻報酬率(%)"], cmap="RdYlGn"),
        use_container_width=True
    )

    # 相關係數熱力圖
    if len(valid_codes) >= 2:
        st.markdown("### 🔗 各股票報酬率相關係數")
        st.caption("相關係數接近 1 代表高度正相關，接近 -1 代表負相關，0 代表無相關。分散投資應尋找低相關性的組合。")
        corr_df = return_df[valid_codes].copy()
        corr_df.columns = [stock_list.get(c, c) for c in valid_codes]
        st.plotly_chart(plot_correlation_heatmap(corr_df), use_container_width=True)

    # 個股與投組明細對比表
    st.markdown("### 📋 個股 vs 投組績效總表")
    summary_rows = []
    for code in valid_codes:
        r_series = return_df[code]
        c = (1 + r_series).cumprod().iloc[-1] - 1
        s = r_series.mean() / r_series.std() * TRADING_DAYS ** 0.5 if r_series.std() != 0 else 0
        cum_r_s = (1 + r_series).cumprod()
        m = ((cum_r_s - cum_r_s.cummax()) / cum_r_s.cummax()).min()
        summary_rows.append({
            "股票": f"{stock_list.get(code, code)}（{code}）",
            "累積報酬率(%)": round(c * 100, 2),
            "夏普比率": round(s, 2),
            "最大回撤(%)": round(m * 100, 2),
            "權重(%)": round(norm_valid_w[code] * 100, 2),
        })

    # 加入投組整體列
    summary_rows.append({
        "股票": "💼 投資組合整體",
        "累積報酬率(%)": round(cum_r * 100, 2),
        "夏普比率": round(sharpe, 2),
        "最大回撤(%)": round(mdd * 100, 2),
        "權重(%)": 100.0,
    })
    if df_twii is not None:
        twii_r_series = df_twii['TWII_Return']
        twii_s = twii_r_series.mean() / twii_r_series.std() * TRADING_DAYS**0.5 if twii_r_series.std() != 0 else 0
        twii_cum_s = (1 + twii_r_series).cumprod()
        twii_mdd = ((twii_cum_s - twii_cum_s.cummax()) / twii_cum_s.cummax()).min()
        summary_rows.append({
            "股票": "📈 台灣加權指數（基準）",
            "累積報酬率(%)": round(twii_cum * 100, 2),
            "夏普比率": round(twii_s, 2),
            "最大回撤(%)": round(twii_mdd * 100, 2),
            "權重(%)": "—",
        })

    df_summary = pd.DataFrame(summary_rows)
    st.dataframe(
        df_summary.style.format({
            "累積報酬率(%)": "{:.2f}%",
            "夏普比率": "{:.2f}",
            "最大回撤(%)": "{:.2f}%",
        }),
        use_container_width=True
    )
