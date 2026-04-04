import streamlit as st
import pandas as pd
import datetime
import json
import os
import yfinance as yf
import plotly.graph_objs as go
import plotly.express as px
from plotly.subplots import make_subplots
from FinMind.data import DataLoader
from strategy import apply_strategy, strategies, stock_list

st.title("📈 台股財報 × 策略回測整合分析")

# =====================
# 使用者偏好設定讀寫
# =====================
PREF_FILE = "user_financial_pref.json"

def load_pref():
    if os.path.exists(PREF_FILE):
        try:
            with open(PREF_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_pref(data: dict):
    with open(PREF_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =====================
# 左側 Sidebar
# =====================
with st.sidebar:
    st.markdown("## 🔑 FinMind API 設定")
    st.markdown(
        "請先至 [FinMind 官網](https://finmindtrade.com) 註冊並取得 API Token，"
        "免費帳號每小時可查詢約 600 次。"
    )
    finmind_token = st.text_input(
        "輸入 FinMind API Token",
        type="password",
        placeholder="貼上你的 Token...",
        help="Token 僅在此次 session 使用，不會儲存"
    )
    if finmind_token:
        st.success("✅ Token 已輸入")
    else:
        st.warning("⚠️ 尚未輸入 Token，財報資料無法載入")

if not finmind_token:
    st.info("👈 請先在左側輸入 FinMind API Token 才能使用此功能")
    st.stop()

# =====================
# 科目名稱對應表（中英文）
# =====================
REVENUE_CANDIDATES      = ["營業收入", "營業收入合計", "收益合計", "Revenue", "Revenues"]
OPERATING_CANDIDATES    = ["營業利益（損失）", "營業利益", "營業損益", "OperatingIncome"]
NET_INCOME_CANDIDATES   = ["本期淨利（淨損）", "本期淨利", "稅後淨利", "本期損益",
                            "IncomeAfterTaxes", "IncomeFromContinuingOperations",
                            "TotalConsolidatedProfitForThePeriod"]
GROSS_PROFIT_CANDIDATES = ["營業毛利（毛損）", "營業毛利", "GrossProfit"]
EPS_CANDIDATES          = ["每股盈餘", "EPS", "基本每股盈餘（元）"]
PRETAX_CANDIDATES       = ["稅前淨利（淨損）", "稅前損益", "PreTaxIncome"]

# 財報指標清單（顯示名稱 → 對應候選欄位）
FINANCIAL_METRICS = {
    "營業收入":   REVENUE_CANDIDATES,
    "毛利":       GROSS_PROFIT_CANDIDATES,
    "營業利益":   OPERATING_CANDIDATES,
    "稅前淨利":   PRETAX_CANDIDATES,
    "稅後淨利":   NET_INCOME_CANDIDATES,
    "EPS":        EPS_CANDIDATES,
}

def find_column(df_pivot, candidates):
    for col in candidates:
        if col in df_pivot.columns:
            return pd.to_numeric(df_pivot[col], errors='coerce')
    return pd.Series([None] * len(df_pivot), dtype=float)

# =====================
# 股票 & 設定選擇（含偏好記憶）
# =====================
pref = load_pref()
stock_options = [f"{name} ({code})" for code, name in stock_list.items()]

default_stocks = [s for s in pref.get("stocks", stock_options[:3]) if s in stock_options]
if not default_stocks:
    default_stocks = stock_options[:3]

report_type_map = {"季報": "Q", "年報": "A"}
default_report = pref.get("report_type", "季報")
default_report_index = list(report_type_map.keys()).index(default_report) if default_report in report_type_map else 0

st.markdown("## 📋 分析設定")
col1, col2 = st.columns(2)
with col1:
    stocks_selected = st.multiselect("選擇股票（可多選）", stock_options, default=default_stocks)
    stock_codes = [s.split("(")[-1].strip(")") for s in stocks_selected]
with col2:
    report_type_label = st.selectbox("財報類型", list(report_type_map.keys()), index=default_report_index)
    report_type = report_type_map[report_type_label]

if not stock_codes:
    st.warning("請至少選擇一支股票")
    st.stop()

# 財報時間區間（固定近3年）
today = datetime.date.today()
fin_start = f"{today.year - 3}-01-01"
fin_end   = today.strftime("%Y-%m-%d")

# 回測時間區間
st.markdown("## 📅 回測區間")
col3, col4 = st.columns(2)
with col3:
    bt_start = st.date_input("開始日期", pd.to_datetime("2022-01-01"))
with col4:
    bt_end = st.date_input("結束日期", pd.to_datetime("today"))

# 財報指標選擇（要疊加在股價圖上的指標）
st.markdown("## 📊 財報指標疊加")
selected_metrics = st.multiselect(
    "選擇要疊加在股價圖上的財報指標",
    list(FINANCIAL_METRICS.keys()),
    default=["營業收入", "EPS"],
    help="財報資料為季/年頻率，會以垂直標記線疊加在股價走勢圖上"
)

# 策略選擇
st.markdown("## 🔧 技術策略設定")
strategy_name = st.selectbox("選擇策略", list(strategies.keys()))
st.caption(strategies[strategy_name]["description"])

params = {}
param_cols = st.columns(min(len(strategies[strategy_name]["parameters"]), 4))
for i, (param, default) in enumerate(strategies[strategy_name]["parameters"].items()):
    with param_cols[i % len(param_cols)]:
        if isinstance(default, int):
            params[param] = st.slider(param, min_value=1, max_value=200, value=default)
        elif isinstance(default, float):
            params[param] = st.number_input(param, value=default, format="%.2f")
        else:
            params[param] = st.text_input(param, value=str(default))

# =====================
# 資料抓取函式
# =====================
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_financial_data(token, stock_code, start_date, end_date):
    _api = DataLoader()
    _api.login_by_token(api_token=token)
    try:
        return _api.taiwan_stock_financial_statement(
            stock_id=stock_code, start_date=start_date, end_date=end_date
        )
    except Exception:
        return pd.DataFrame()

@st.cache_data(show_spinner=False)
def fetch_price_data(stock_code, start_date, end_date):
    df = yf.download(stock_code, start=start_date, end=end_date, auto_adjust=False)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    df = df.sort_index()
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    df = df[df['Close'].notna()]
    return df

def process_financial(df_fin, report_type):
    """財報 long format → wide format，回傳含各指標的 DataFrame"""
    if df_fin is None or df_fin.empty:
        return pd.DataFrame()
    if "type" in df_fin.columns:
        df_f = df_fin[df_fin["type"] == report_type]
        if df_f.empty:
            df_f = df_fin
    else:
        df_f = df_fin
    if "type" not in df_f.columns or "value" not in df_f.columns:
        return pd.DataFrame()
    try:
        df_pivot = df_f.pivot_table(
            index="date", columns="type", values="value", aggfunc="first"
        ).reset_index()
        df_pivot["date"] = pd.to_datetime(df_pivot["date"])
        return df_pivot
    except Exception:
        return pd.DataFrame()

# =====================
# 主圖表：股價 + 財報指標疊加 + 策略持倉
# =====================
def plot_integrated(df_price, df_fin_pivot, code, strategy_df, selected_metrics):
    """
    子圖一：股價蠟燭圖 + 財報指標（次座標軸）+ 策略買賣點標記
    子圖二：策略 vs 買入持有累積報酬率
    子圖三：財報趨勢折線圖（所有選取指標）
    """
    rows = 3
    row_heights = [0.45, 0.25, 0.30]
    specs = [[{"secondary_y": True}], [{"secondary_y": False}], [{"secondary_y": False}]]

    fig = make_subplots(
        rows=rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=row_heights,
        specs=specs,
        subplot_titles=[
            f"{code} 股價走勢 × 財報指標",
            "策略 vs 買入持有 累積報酬率",
            f"財報趨勢（{report_type_label}）"
        ]
    )

    # ── 子圖一：蠟燭圖 ──
    fig.add_trace(go.Candlestick(
        x=df_price.index,
        open=df_price['Open'], high=df_price['High'],
        low=df_price['Low'],   close=df_price['Close'],
        name='股價', increasing_line_color='red', decreasing_line_color='green'
    ), row=1, col=1, secondary_y=False)

    # 策略買入/賣出標記點
    if strategy_df is not None and not strategy_df.empty:
        buy_points  = strategy_df[strategy_df['Position'].diff() == 1]
        sell_points = strategy_df[strategy_df['Position'].diff() == -1]
        fig.add_trace(go.Scatter(
            x=buy_points.index, y=buy_points['Close'],
            mode='markers', name='買入',
            marker=dict(symbol='triangle-up', size=10, color='red')
        ), row=1, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(
            x=sell_points.index, y=sell_points['Close'],
            mode='markers', name='賣出',
            marker=dict(symbol='triangle-down', size=10, color='green')
        ), row=1, col=1, secondary_y=False)

    # 財報指標疊加（次座標軸，bar chart）
    metric_colors = px.colors.qualitative.Set2
    if df_fin_pivot is not None and not df_fin_pivot.empty and selected_metrics:
        first_metric = True
        for i, metric_name in enumerate(selected_metrics):
            candidates = FINANCIAL_METRICS[metric_name]
            series = find_column(df_fin_pivot, candidates)
            if series.isna().all():
                continue
            color = metric_colors[i % len(metric_colors)]
            fig.add_trace(go.Bar(
                x=df_fin_pivot["date"],
                y=series,
                name=f"{metric_name}（財報）",
                marker_color=color,
                opacity=0.5,
                showlegend=True,
            ), row=1, col=1, secondary_y=True)

    # ── 子圖二：累積報酬率 ──
    if strategy_df is not None and not strategy_df.empty:
        fig.add_trace(go.Scatter(
            x=strategy_df.index,
            y=strategy_df['BuyHoldCumulative'],
            mode='lines', name='買入持有', line=dict(color='gray', dash='dash')
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=strategy_df.index,
            y=strategy_df['StrategyCumulative'],
            mode='lines', name=f'{strategy_name}', line=dict(color='royalblue')
        ), row=2, col=1)

    # ── 子圖三：財報趨勢折線圖 ──
    if df_fin_pivot is not None and not df_fin_pivot.empty and selected_metrics:
        for i, metric_name in enumerate(selected_metrics):
            candidates = FINANCIAL_METRICS[metric_name]
            series = find_column(df_fin_pivot, candidates)
            if series.isna().all():
                continue
            color = metric_colors[i % len(metric_colors)]
            fig.add_trace(go.Scatter(
                x=df_fin_pivot["date"], y=series,
                mode='lines+markers', name=f"{metric_name}（趨勢）",
                line=dict(color=color), showlegend=False
            ), row=3, col=1)

    fig.update_layout(
        height=900,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        title_text=f"{code} 財報 × 策略整合分析",
    )
    fig.update_yaxes(title_text="股價 (元)", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="財報金額 (千元)", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="累積報酬率", row=2, col=1)
    fig.update_yaxes(title_text="財報金額", row=3, col=1)
    return fig

# =====================
# 財報彙總比較圖（多股票）
# =====================
def plot_financial_summary(all_fin_data):
    df = pd.DataFrame(all_fin_data)
    if df.empty:
        return None
    df_melt = df.melt(
        id_vars=["股票名稱"],
        value_vars=["平均營收", "平均毛利", "平均營業利益", "平均稅後淨利"],
        var_name="指標", value_name="數值"
    )
    fig = px.bar(
        df_melt, x="股票名稱", y="數值", color="指標",
        barmode="group", title="📊 多股票財報指標比較（近3年平均）",
        labels={"數值": "金額 (千元)"}
    )
    return fig

def plot_eps_comparison(all_eps_data):
    """EPS 趨勢折線比較（多股票）"""
    fig = go.Figure()
    for code, df_eps in all_eps_data.items():
        fig.add_trace(go.Scatter(
            x=df_eps["date"], y=df_eps["eps"],
            mode='lines+markers', name=code
        ))
    fig.update_layout(title="📈 EPS 趨勢比較", xaxis_title="日期", yaxis_title="EPS (元)")
    return fig

# =====================
# 回測 + 財報整合主流程
# =====================
if st.button("🚀 開始財報 × 策略整合分析"):
    save_pref({
        "stocks": stocks_selected,
        "report_type": report_type_label,
    })

    TRADING_DAYS = 240
    all_fin_summary = []
    all_eps_data    = {}
    backtest_results = []

    for code in stock_codes:
        st.markdown(f"---\n### 🏢 {stock_list.get(code, code)}（{code}）")
        fmind_code = code.replace(".TW", "").replace(".TWO", "")

        # ── 抓財報 ──
        with st.spinner(f"抓取 {code} 財報資料..."):
            df_fin_raw = fetch_financial_data(finmind_token, fmind_code, fin_start, fin_end)
        df_fin_pivot = process_financial(df_fin_raw, report_type)

        with st.expander(f"🔍 {code} 原始科目列表（除錯用）", expanded=False):
            if df_fin_pivot is not None and not df_fin_pivot.empty:
                st.write(sorted([c for c in df_fin_pivot.columns if c != "date"]))
            else:
                st.write("無法取得財報資料")

        # 計算各財報指標
        if df_fin_pivot is not None and not df_fin_pivot.empty:
            avg_revenue    = find_column(df_fin_pivot, REVENUE_CANDIDATES).mean()
            avg_gross      = find_column(df_fin_pivot, GROSS_PROFIT_CANDIDATES).mean()
            avg_operating  = find_column(df_fin_pivot, OPERATING_CANDIDATES).mean()
            avg_net_income = find_column(df_fin_pivot, NET_INCOME_CANDIDATES).mean()
            eps_series     = find_column(df_fin_pivot, EPS_CANDIDATES)

            all_fin_summary.append({
                "股票代號": code,
                "股票名稱": stock_list.get(code, code),
                "平均營收": avg_revenue,
                "平均毛利": avg_gross,
                "平均營業利益": avg_operating,
                "平均稅後淨利": avg_net_income,
            })

            if not eps_series.isna().all():
                all_eps_data[code] = pd.DataFrame({
                    "date": df_fin_pivot["date"],
                    "eps": eps_series
                }).dropna()

            # 顯示財報快速摘要
            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("平均營收（千元）", f"{avg_revenue:,.0f}" if not pd.isna(avg_revenue) else "N/A")
            col_b.metric("平均毛利（千元）", f"{avg_gross:,.0f}"   if not pd.isna(avg_gross)   else "N/A")
            col_c.metric("平均營業利益",     f"{avg_operating:,.0f}" if not pd.isna(avg_operating) else "N/A")
            col_d.metric("平均稅後淨利",     f"{avg_net_income:,.0f}" if not pd.isna(avg_net_income) else "N/A")
        else:
            st.warning(f"⚠️ {code} 無法取得財報資料")
            df_fin_pivot = None

        # ── 抓股價 ──
        with st.spinner(f"抓取 {code} 股價資料..."):
            df_price = fetch_price_data(code, bt_start, bt_end)

        if df_price.empty:
            st.warning(f"⚠️ {code} 無法取得股價資料，跳過回測")
            st.plotly_chart(
                plot_integrated(pd.DataFrame(), df_fin_pivot, code, None, selected_metrics),
                use_container_width=True
            )
            continue

        # ── 執行策略回測 ──
        try:
            df_strategy = apply_strategy(df_price.copy(), strategy_name, params)
        except Exception as e:
            st.warning(f"⚠️ {code} 策略執行失敗：{e}")
            continue

        df_strategy['DailyReturn'] = df_strategy['Close'].pct_change()
        df_strategy['Strategy']    = df_strategy['Position'].shift(1) * df_strategy['DailyReturn']
        df_strategy = df_strategy.dropna(subset=['DailyReturn', 'Strategy'])
        df_strategy = df_strategy[df_strategy['DailyReturn'].abs() < 0.5]

        if df_strategy.empty:
            st.warning(f"⚠️ {code} 回測結果為空")
            continue

        df_strategy['BuyHoldCumulative'] = (1 + df_strategy['DailyReturn']).cumprod() - 1
        df_strategy['StrategyCumulative'] = (1 + df_strategy['Strategy']).cumprod() - 1

        # ── 整合圖表 ──
        fig_integrated = plot_integrated(df_price, df_fin_pivot, code, df_strategy, selected_metrics)
        st.plotly_chart(fig_integrated, use_container_width=True)

        # ── 績效指標 ──
        cum_strategy  = df_strategy['StrategyCumulative'].iloc[-1]
        cum_buyhold   = df_strategy['BuyHoldCumulative'].iloc[-1]
        sharpe        = (df_strategy['Strategy'].mean() / df_strategy['Strategy'].std()) * (TRADING_DAYS ** 0.5) \
                        if df_strategy['Strategy'].std() != 0 else 0
        cum_s         = (1 + df_strategy['Strategy']).cumprod()
        mdd           = ((cum_s - cum_s.cummax()) / cum_s.cummax()).min()
        last_signal   = df_strategy['Position'].iloc[-1]
        signal_text   = "空手" if last_signal == 0 else ("持有（買入）" if last_signal == 1 else "放空")

        backtest_results.append({
            "股票": stock_list.get(code, code),
            "股票代號": code,
            "策略": strategy_name,
            "買入持有報酬率(%)": round(cum_buyhold * 100, 2),
            "策略報酬率(%)": round(cum_strategy * 100, 2),
            "年化波動(%)": round(df_strategy['Strategy'].std() * (TRADING_DAYS ** 0.5) * 100, 2),
            "最大回撤(%)": round(mdd * 100, 2),
            "夏普比率": round(sharpe, 2),
            "最新訊號": signal_text,
        })

    # =====================
    # 彙總區塊
    # =====================
    st.markdown("---")
    st.markdown("## 📊 多股票彙總比較")

    # 財報比較圖
    if all_fin_summary:
        fig_fin = plot_financial_summary(all_fin_summary)
        if fig_fin:
            st.plotly_chart(fig_fin, use_container_width=True)

        df_summary_table = pd.DataFrame(all_fin_summary)
        st.markdown("### 財報指標彙總表")
        st.dataframe(df_summary_table.style.format({
            "平均營收": "{:,.0f}",
            "平均毛利": "{:,.0f}",
            "平均營業利益": "{:,.0f}",
            "平均稅後淨利": "{:,.0f}",
        }), use_container_width=True)

    # EPS 趨勢比較
    if all_eps_data:
        st.plotly_chart(plot_eps_comparison(all_eps_data), use_container_width=True)

    # 回測績效彙總
    if backtest_results:
        st.markdown("### 策略回測績效彙總")
        df_bt = pd.DataFrame(backtest_results)
        st.dataframe(df_bt.style.format({
            "買入持有報酬率(%)": "{:.2f}%",
            "策略報酬率(%)": "{:.2f}%",
            "年化波動(%)": "{:.2f}%",
            "最大回撤(%)": "{:.2f}%",
            "夏普比率": "{:.2f}",
        }), use_container_width=True)

        fig_bt_bar = px.bar(
            df_bt, x="股票", y="策略報酬率(%)",
            color="股票", title=f"各股票 {strategy_name} 策略報酬率比較",
            text_auto=".1f"
        )
        st.plotly_chart(fig_bt_bar, use_container_width=True)
