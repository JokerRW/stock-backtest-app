# risk.py
# 摩擦成本 + 停損停利 共用模組
# 所有回測頁面（台股回測、策略比較、虛擬幣回測）都從這裡 import

import pandas as pd
import numpy as np

# =====================
# 預設手續費設定
# =====================
DEFAULT_FEE_STOCK  = 0.001425   # 台股：單邊手續費 0.1425%
DEFAULT_TAX_STOCK  = 0.003      # 台股：賣出交易稅 0.3%
DEFAULT_FEE_CRYPTO = 0.001      # 虛擬幣：單邊手續費 0.1%（Binance 現貨）


def apply_friction_and_risk(
    df: pd.DataFrame,
    buy_fee: float   = DEFAULT_FEE_STOCK,
    sell_fee: float  = DEFAULT_FEE_STOCK,
    sell_tax: float  = DEFAULT_TAX_STOCK,
    stop_loss: float = 0.0,      # 停損比例，0 = 不啟用（例如 0.05 = -5%）
    take_profit: float = 0.0,    # 停利比例，0 = 不啟用（例如 0.10 = +10%）
) -> pd.DataFrame:
    """
    在已有 Position 欄位的 df 上，套用摩擦成本與停損停利，
    回傳加上 Strategy（實際策略日報酬）欄位的 df。

    參數說明：
      buy_fee    : 買入手續費（單邊），例如 0.001425
      sell_fee   : 賣出手續費（單邊），例如 0.001425
      sell_tax   : 賣出交易稅，台股 0.003，虛擬幣 0
      stop_loss  : 持倉中跌幅達此比例強制出場（0 = 不啟用）
      take_profit: 持倉中漲幅達此比例強制出場（0 = 不啟用）

    回傳欄位：
      Position_adj  : 套用停損停利後調整的持倉（可能提前出場）
      Strategy      : 考慮手續費、交易稅後的實際日報酬
      TradeCost     : 當日產生的交易成本（進出場日才有值）
      StopTriggered : 是否由停損停利觸發出場
    """
    df = df.copy()
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    df = df[df['Close'].notna()].copy()

    closes      = df['Close'].values
    positions   = df['Position'].values.copy()
    n           = len(df)

    # 輸出欄位
    position_adj   = np.zeros(n, dtype=int)
    daily_strategy = np.zeros(n, dtype=float)
    trade_cost     = np.zeros(n, dtype=float)
    stop_triggered = np.zeros(n, dtype=bool)

    current_pos    = 0
    entry_price    = 0.0

    for i in range(n):
        price       = closes[i]
        raw_signal  = positions[i]
        prev_pos    = current_pos

        # ── 停損停利檢查（持倉中才檢查）──
        sl_hit = tp_hit = False
        if current_pos == 1 and entry_price > 0:
            pnl_ratio = (price - entry_price) / entry_price
            if stop_loss   > 0 and pnl_ratio <= -stop_loss:
                sl_hit = True
            if take_profit > 0 and pnl_ratio >= take_profit:
                tp_hit = True

        # ── 決定今日持倉 ──
        if sl_hit or tp_hit:
            # 停損停利強制出場
            current_pos       = 0
            stop_triggered[i] = True
        elif raw_signal == 1 and prev_pos == 0:
            # 策略買入
            current_pos = 1
            entry_price = price
        elif raw_signal == 0 and prev_pos == 1:
            # 策略出場
            current_pos = 0
        # 其餘維持不變

        position_adj[i] = current_pos

        # ── 計算日報酬（使用前一日持倉，避免未來函數）──
        if i == 0:
            daily_strategy[i] = 0.0
            continue

        prev_price = closes[i - 1]
        if prev_price <= 0:
            daily_strategy[i] = 0.0
            continue

        raw_return = (price - prev_price) / prev_price

        # 前一日持倉為 1 → 今日有部位報酬
        if position_adj[i - 1] == 1:
            cost = 0.0

            # 今日進場（昨天 0 → 今天 1）→ 扣買入手續費
            if i > 0 and position_adj[i - 1] == 0 and current_pos == 1:
                cost += buy_fee

            # 今日出場（昨天 1 → 今天 0）→ 扣賣出手續費 + 交易稅
            if prev_pos == 1 and current_pos == 0:
                cost += sell_fee + sell_tax

            trade_cost[i]     = cost
            daily_strategy[i] = raw_return - cost
        else:
            daily_strategy[i] = 0.0

    # ── 補捉進場日的手續費（position 從 0→1 的那一天）──
    for i in range(1, n):
        if position_adj[i - 1] == 0 and position_adj[i] == 1:
            trade_cost[i]     += buy_fee
            daily_strategy[i] -= buy_fee

    df['Position_adj']   = position_adj
    df['Strategy']       = daily_strategy
    df['TradeCost']      = trade_cost
    df['StopTriggered']  = stop_triggered

    return df


def calc_performance(df: pd.DataFrame, trading_days: int = 240) -> dict:
    """
    計算績效指標，輸入 df 需含 Strategy、DailyReturn 欄位。
    回傳 dict：累積報酬率、夏普比率、最大回撤、年化波動、交易次數、總手續費成本
    """
    s = df['Strategy'].dropna()
    r = df['DailyReturn'].dropna() if 'DailyReturn' in df.columns else s

    cum_s    = (1 + s).cumprod()
    cum_r    = cum_s.iloc[-1] - 1
    mdd      = ((cum_s - cum_s.cummax()) / cum_s.cummax()).min()
    vol      = s.std() * (trading_days ** 0.5)
    sharpe   = (s.mean() / s.std() * trading_days ** 0.5) if s.std() != 0 else 0

    pos_col  = 'Position_adj' if 'Position_adj' in df.columns else 'Position'
    trades   = int((df[pos_col].diff().abs() > 0).sum()) if pos_col in df.columns else 0
    total_cost = df['TradeCost'].sum() if 'TradeCost' in df.columns else 0.0

    return {
        "累積報酬率(%)":    round(cum_r * 100, 2),
        "夏普比率":          round(sharpe, 2),
        "最大回撤(%)":      round(mdd * 100, 2),
        "年化波動(%)":      round(vol * 100, 2),
        "交易次數":          trades,
        "總手續費成本(%)":  round(total_cost * 100, 4),
    }


def build_risk_ui(prefix: str = "", market: str = "stock") -> dict:
    """
    在 Streamlit 頁面上渲染摩擦成本與停損停利的設定 UI。
    prefix : 避免多個頁面 widget key 衝突（例如 "bt_"、"cmp_"）
    market : "stock"（台股）或 "crypto"（虛擬幣）

    回傳 dict：
      buy_fee, sell_fee, sell_tax, stop_loss, take_profit
    """
    import streamlit as st

    with st.expander("⚙️ 摩擦成本 & 停損停利設定", expanded=False):
        st.caption("台股預設：手續費 0.1425%（買賣各一次）、賣出交易稅 0.3%。虛擬幣預設：手續費 0.1%、無交易稅。")

        col1, col2, col3 = st.columns(3)

        if market == "stock":
            default_buy  = 0.1425
            default_sell = 0.1425
            default_tax  = 0.30
        else:
            default_buy  = 0.10
            default_sell = 0.10
            default_tax  = 0.0

        buy_fee_pct = col1.number_input(
            "買入手續費（%）", min_value=0.0, max_value=5.0,
            value=default_buy, step=0.01, format="%.4f",
            key=f"{prefix}buy_fee"
        )
        sell_fee_pct = col2.number_input(
            "賣出手續費（%）", min_value=0.0, max_value=5.0,
            value=default_sell, step=0.01, format="%.4f",
            key=f"{prefix}sell_fee"
        )
        sell_tax_pct = col3.number_input(
            "賣出交易稅（%）", min_value=0.0, max_value=5.0,
            value=default_tax, step=0.01, format="%.4f",
            key=f"{prefix}sell_tax",
            help="台股賣出交易稅 0.3%，ETF 減半 0.15%，虛擬幣填 0"
        )

        st.markdown("---")
        col4, col5 = st.columns(2)
        use_sl = col4.checkbox("啟用停損", value=False, key=f"{prefix}use_sl")
        use_tp = col5.checkbox("啟用停利", value=False, key=f"{prefix}use_tp")

        stop_loss_pct   = 0.0
        take_profit_pct = 0.0

        if use_sl:
            stop_loss_pct = col4.number_input(
                "停損比例（%）", min_value=0.1, max_value=50.0,
                value=5.0, step=0.5, format="%.1f",
                key=f"{prefix}sl_pct",
                help="持倉虧損達此比例時強制出場，例如 5 = 跌 5% 出場"
            )
        if use_tp:
            take_profit_pct = col5.number_input(
                "停利比例（%）", min_value=0.1, max_value=200.0,
                value=10.0, step=0.5, format="%.1f",
                key=f"{prefix}tp_pct",
                help="持倉獲利達此比例時強制出場，例如 10 = 漲 10% 出場"
            )

        # 顯示目前設定摘要
        summary = f"手續費：買 {buy_fee_pct:.4f}% / 賣 {sell_fee_pct:.4f}% + 稅 {sell_tax_pct:.2f}%"
        if use_sl:
            summary += f"　｜　停損：-{stop_loss_pct:.1f}%"
        if use_tp:
            summary += f"　｜　停利：+{take_profit_pct:.1f}%"
        st.caption(f"📌 目前設定：{summary}")

    return {
        "buy_fee":    buy_fee_pct   / 100,
        "sell_fee":   sell_fee_pct  / 100,
        "sell_tax":   sell_tax_pct  / 100,
        "stop_loss":  stop_loss_pct  / 100,
        "take_profit": take_profit_pct / 100,
    }
