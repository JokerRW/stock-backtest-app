# pages/3_策略比較.py
import streamlit as st
import pandas as pd
import json
import os
import yfinance as yf
import plotly.express as px
import requests
from strategy import apply_strategy, strategies, stock_list
from database import load_stock_prices, save_stock_prices

BEST_PARAM_FILE = "user_best_params.json"

def load_best_params():
    if os.path.exists(BEST_PARAM_FILE):
        try:
            with open(BEST_PARAM_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

st.title("📊 多股票多策略回測比較")

# =====================
# 使用者選擇儲存與載入
# =====================
SELECTION_FILE = "user_selection.json"

def load_user_selection():
    if os.path.exists(SELECTION_FILE):
        try:
            with open(SELECTION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_user_selection(stocks, strats):
    with open(SELECTION_FILE, "w", encoding="utf-8") as f:
        json.dump({"stocks": stocks, "strategies": strats}, f, ensure_ascii=False, indent=2)

# =====================
# 左側 Sidebar：Gemini API Key
# =====================
with st.sidebar:
    st.markdown("## 🤖 AI 分析設定（選填）")
    st.markdown(
        "輸入 [Google Gemini API Key](https://aistudio.google.com/app/apikey) "
        "後，回測完成可自動產生 AI 摘要分析。"
    )
    gemini_api_key = st.text_input(
        "Gemini API Key",
        type="password",
        placeholder="AIza...",
        help="Key 僅在此次 session 使用，不會儲存"
    )
    if gemini_api_key:
        st.success("✅ API Key 已輸入，回測後將產生 AI 分析")
    else:
        st.info("未輸入 Key，將跳過 AI 分析")

    st.markdown("---")
    if st.button("🗑️ 清除所有快取", help="若 AI 回應異常或模型沒更新，請點此清除"):
        st.cache_data.clear()
        st.success("✅ 快取已清除，請重新執行回測")

# =====================
# 股票與策略選項
# =====================
stock_options = [f"{name} ({code})" for code, name in stock_list.items()]
strategy_names = list(strategies.keys())

user_selection = load_user_selection()
default_stocks = user_selection.get("stocks", stock_options[:2])
default_strategies = user_selection.get("strategies", strategy_names[:2])

# 過濾掉已不在清單中的選項
default_stocks = [s for s in default_stocks if s in stock_options]
default_strategies = [s for s in default_strategies if s in strategy_names]

stocks_selected = st.multiselect("選擇股票（多選）", stock_options, default=default_stocks)
strategies_selected = st.multiselect("選擇策略（多選）", strategy_names, default=default_strategies)
stock_codes = [s.split("(")[-1].strip(")") for s in stocks_selected]

# ✅ 最佳參數選項
best_params_db = load_best_params()
use_best_params = st.checkbox(
    "🏆 使用已儲存的最佳化參數（從回測系統儲存）",
    value=False,
    help="若回測系統已執行參數最佳化並儲存，勾選此項可自動套用最佳參數"
)

# 顯示目前已有最佳參數的組合
if use_best_params and best_params_db:
    available = []
    for key, val in best_params_db.items():
        available.append(
            f"✅ {val['stock_code']} × {val['strategy_name']}：" +
            "、".join([f"{k}={v}" for k, v in val["params"].items()]) +
            f"（{val['saved_at']}）"
        )
    with st.expander("📋 已儲存的最佳參數清單", expanded=True):
        for a in available:
            st.caption(a)
elif use_best_params and not best_params_db:
    st.warning("⚠️ 尚無儲存的最佳參數，請先至「回測系統」執行參數最佳化並儲存。")

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("開始日期", pd.to_datetime("2024-01-01"))
with col2:
    end_date = st.date_input("結束日期", pd.to_datetime("today"))

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

def calc_cumulative_return(returns: pd.Series) -> float:
    return (1 + returns).cumprod().iloc[-1] - 1

def max_drawdown(returns: pd.Series) -> float:
    cum = (1 + returns).cumprod()
    return ((cum - cum.cummax()) / cum.cummax()).min()

# =====================
# Gemini AI 分析
# =====================
def build_prompt(df_results: pd.DataFrame, start_date, end_date) -> str:
    """將回測結果表格轉成 Gemini prompt"""
    period = f"{start_date} 至 {end_date}"
    table_str = df_results.to_string(index=False)

    prompt = f"""
你是一位專業的台股量化交易分析師，請根據以下回測結果，用繁體中文撰寫一份簡明的策略分析摘要。

回測期間：{period}

回測績效表：
{table_str}

請依照以下結構回答，每個段落保持簡潔（2～4句話）：

1. **整體表現總結**：哪些股票或策略組合表現最佳？整體市場環境如何？
2. **最佳策略推薦**：根據夏普比率與累積報酬率，推薦哪個股票 × 策略組合，並說明原因。
3. **風險提示**：最大回撤最嚴重的組合是哪個？投資人應注意什麼風險？
4. **操作建議**：根據最新數據，給投資人一個簡單明確的操作方向建議。

注意：此分析僅供參考，不構成實際投資建議。
""".strip()
    return prompt

def call_gemini_stream(api_key: str, prompt: str, max_retries: int = 3) -> str:
    """
    呼叫 Gemini 2.5 Flash Streaming API。
    2.5-flash 有強制思考機制，思考 token 會佔用輸出空間導致截斷。
    改用 streamGenerateContent（SSE），思考與輸出分開串流，
    只收集 role=model 的 text parts，完整拼接後回傳。
    """
    import time
    import json as _json

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:streamGenerateContent?alt=sse"
    )
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 4096,
        }
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                url, json=payload, headers=headers,
                timeout=60, stream=True
            )

            if resp.status_code == 429:
                wait_sec = 15 * (attempt + 1)
                st.warning(f"⏳ API 請求頻率超限，{wait_sec} 秒後自動重試（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(wait_sec)
                continue

            resp.raise_for_status()

            # SSE 串流：逐行解析 data: {...} 事件
            full_text = ""
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line.startswith("data:"):
                    continue
                json_str = line[len("data:"):].strip()
                if json_str == "[DONE]":
                    break
                try:
                    chunk = _json.loads(json_str)
                except _json.JSONDecodeError:
                    continue

                # 只取 model 角色的 text，跳過思考用的 thought parts
                candidates = chunk.get("candidates", [])
                for candidate in candidates:
                    parts = candidate.get("content", {}).get("parts", [])
                    for part in parts:
                        # thought=True 的是思考內容，跳過
                        if part.get("thought", False):
                            continue
                        if "text" in part:
                            full_text += part["text"]

            if full_text.strip():
                return full_text
            return "❌ Gemini 回傳的文字內容為空。"

        except requests.exceptions.HTTPError:
            return f"❌ API 呼叫失敗（HTTP {resp.status_code}）：Key 錯誤或帳號額度已用盡。"
        except Exception as e:
            return f"❌ 發生錯誤：{e}"

    return "❌ 已重試 3 次仍失敗，請稍後再試或確認免費額度是否已用盡。"

# =====================
# 回測主流程
# =====================
if st.button("🚀 執行回測比較"):
    if not stock_codes:
        st.error("請至少選擇一支股票")
        st.stop()
    if not strategies_selected:
        st.error("請至少選擇一個策略")
        st.stop()
    if end_date <= start_date:
        st.error("結束日期必須晚於開始日期")
        st.stop()

    save_user_selection(stocks_selected, strategies_selected)

    TRADING_DAYS = 240
    results = []

    for stock_code in stock_codes:
        df = load_stock_prices(stock_code, start_date, end_date)
        if not df.empty:
            df.index = pd.to_datetime(df.index)

        if df.empty:
            st.info(f"資料庫無 {stock_code} 資料，從網路下載中...")
            df_web = fetch_stock_data_from_web(stock_code, start_date, end_date)
            if df_web.empty:
                st.warning(f"無法取得 {stock_code} 資料，跳過此股票")
                continue
            save_stock_prices(df_web, stock_code)
            df = df_web

        if 'Close' not in df.columns:
            st.warning(f"{stock_code} 資料缺 Close 欄位，跳過")
            continue

        df = clean_price_data(df)
        if df.empty:
            st.warning(f"{stock_code} 清理後資料為空，跳過")
            continue

        for strat in strategies_selected:
            # ✅ 優先使用最佳化參數，否則使用預設參數
            best_key = f"{stock_code}_{strat}"
            if use_best_params and best_key in best_params_db:
                params = best_params_db[best_key]["params"]
                st.caption(f"🏆 {stock_code} × {strat} 使用最佳化參數：" +
                           "、".join([f"{k}={v}" for k, v in params.items()]))
            else:
                params = strategies[strat]["parameters"]
            try:
                df_strategy = apply_strategy(df.copy(), strat, params)
            except Exception as e:
                st.warning(f"{stock_code} × {strat} 策略套用失敗: {e}")
                continue

            df_strategy['DailyReturn'] = df_strategy['Close'].pct_change()
            df_strategy['Strategy'] = df_strategy['Position'].shift(1) * df_strategy['DailyReturn']
            df_strategy = df_strategy.dropna(subset=['DailyReturn', 'Strategy'])
            df_strategy = df_strategy[df_strategy['DailyReturn'].abs() < 0.5]

            if df_strategy.empty:
                st.warning(f"{stock_code} × {strat} 策略結果為空，跳過")
                continue

            cum_return   = calc_cumulative_return(df_strategy['Strategy'])
            sharpe_ratio = (
                (df_strategy['Strategy'].mean() / df_strategy['Strategy'].std()) * (TRADING_DAYS ** 0.5)
                if df_strategy['Strategy'].std() != 0 else 0
            )
            mdd = max_drawdown(df_strategy['Strategy'])

            results.append({
                "股票": stock_list.get(stock_code, stock_code),
                "股票代號": stock_code,
                "策略": strat,
                "期間": f"{start_date} ~ {end_date}",
                "累積報酬率(%)": round(cum_return * 100, 2),
                "夏普比率": round(sharpe_ratio, 2),
                "最大回撤(%)": round(mdd * 100, 2),
            })

    # =====================
    # 輸出結果
    # =====================
    if not results:
        st.warning("無可用結果，請檢查股票及策略選擇")
        st.stop()

    df_results = pd.DataFrame(results)

    st.markdown("### 📋 策略回測績效表")
    st.dataframe(df_results.style.format({
        '累積報酬率(%)': '{:.2f}%',
        '夏普比率': '{:.2f}',
        '最大回撤(%)': '{:.2f}%'
    }), use_container_width=True)

    fig = px.bar(
        df_results, x='股票', y='累積報酬率(%)', color='策略',
        barmode='group', title='各股票 × 各策略 累積報酬率比較',
        text_auto=".1f"
    )
    st.plotly_chart(fig, use_container_width=True)

    fig_sharpe = px.bar(
        df_results, x='股票', y='夏普比率', color='策略',
        barmode='group', title='各股票 × 各策略 夏普比率比較',
        text_auto=".2f"
    )
    st.plotly_chart(fig_sharpe, use_container_width=True)

    # =====================
    # Gemini AI 分析
    # =====================
    if gemini_api_key:
        st.markdown("---")
        st.markdown("## 🤖 AI 策略分析摘要")
        st.caption("由 Google Gemini 2.5 Flash 根據回測結果自動生成，僅供參考。")

        with st.spinner("AI 分析中，請稍候（最多等待約 45 秒）..."):
            prompt = build_prompt(df_results, start_date, end_date)
            ai_response = call_gemini_stream(gemini_api_key, prompt)

        st.markdown(ai_response)
    else:
        st.info("💡 在左側輸入 Gemini API Key，即可獲得 AI 自動分析摘要。")
