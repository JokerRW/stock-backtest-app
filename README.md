# 台股策略回測系統 — 規格說明書 v2.0

**專案名稱**：台股策略回測系統  
**作者**：Richard Weng  
**版本**：v2.0  
**最後更新**：2026-04-06  

---

## 目錄

1. [系統概述](#1-系統概述)
2. [技術架構](#2-技術架構)
3. [模組說明](#3-模組說明)
4. [頁面功能規格](#4-頁面功能規格)
5. [策略規格](#5-策略規格)
6. [摩擦成本與風險管理規格](#6-摩擦成本與風險管理規格)
7. [參數最佳化規格](#7-參數最佳化規格)
8. [AI 分析規格](#8-ai-分析規格)
9. [資料庫規格](#9-資料庫規格)
10. [資料來源](#10-資料來源)
11. [績效指標定義](#11-績效指標定義)
12. [部署說明](#12-部署說明)
13. [已知限制與注意事項](#13-已知限制與注意事項)

---

## 1. 系統概述

本系統為一套基於 Python + Streamlit 建構的量化交易回測平台，支援台股與虛擬貨幣的策略回測、多策略比較、財報分析、參數最佳化、AI 分析摘要等功能。系統採用 SQLite 作為本地資料快取，透過 yfinance 抓取台股歷史股價，透過 ccxt 抓取幣安虛擬幣行情，並整合 FinMind API 提供財報基本面資料。

### 系統目標

- 提供個人投資者快速驗證交易策略的工具
- 支援多股票、多策略並行比較，縮短回測決策時間
- 透過摩擦成本與停損停利模擬真實交易環境
- 透過參數最佳化（Grid Search）自動找出最佳參數區間
- 整合 Gemini AI 提供策略分析摘要

---

## 2. 技術架構

```
台股策略回測系統
│
├── app.py                  # 首頁（台灣加權指數即時顯示）
├── strategy.py             # 策略邏輯核心（8種策略）
├── database.py             # 資料庫存取層（SQLite + SQLAlchemy）
├── risk.py                 # 摩擦成本 & 停損停利共用模組
├── requirements.txt        # 套件相依清單
│
├── pages/
│   ├── 1_關於我.py
│   ├── 2_回測系統.py       # 單股票單策略回測 + 參數最佳化
│   ├── 3_策略比較.py       # 多股票多策略比較 + AI 分析
│   ├── 4_更新股票清單.py    # 從 TWSE 更新股票清單
│   ├── 5_財報比較.py       # FinMind 財報分析 + 策略回測整合
│   └── 6_虛擬幣回測.py     # 虛擬幣多策略回測 + 參數最佳化
│
└── data/（本機）/ /tmp/（Streamlit Cloud）
    ├── stock_data.db           # 股票歷史價格快取（SQLite）
    ├── stocks.db               # 上市股票清單（SQLite）
    ├── user_backtest_pref.json # 回測頁使用者偏好
    ├── user_selection.json     # 策略比較頁使用者偏好
    ├── user_financial_pref.json# 財報比較頁使用者偏好
    └── user_best_params.json   # 最佳化參數儲存檔（跨頁共用）
```

### 技術棧

| 層級 | 技術 |
|------|------|
| 前端介面 | Streamlit |
| 資料處理 | Pandas、NumPy |
| 圖表視覺化 | Plotly |
| 台股股價 | yfinance（auto_adjust=False 保留原始市價） |
| 虛擬幣資料 | ccxt（Binance） |
| 財報資料 | FinMind API |
| AI 分析 | Google Gemini 2.5 Flash（SSE Streaming） |
| 資料庫 | SQLite + SQLAlchemy（Lazy Engine 初始化） |
| 股票清單 | TWSE ISIN 公開資料 |

---

## 3. 模組說明

### 3.1 `database.py` — 資料存取層

採用 Lazy Engine 初始化，第一次呼叫時才建立 SQLAlchemy engine，確保 Streamlit Cloud 環境下路徑判斷正確。

**環境自動偵測**：
```python
_IS_CLOUD = os.path.exists("/mount/src")   # Streamlit Cloud 唯讀目錄
_DB_DIR   = "/tmp" if _IS_CLOUD else "."   # Cloud 用 /tmp，本機用當前目錄
```

| 函式 | 說明 |
|------|------|
| `init_db()` | 建立 stock_price 資料表（若不存在） |
| `save_stock_prices(df, stock_code)` | 儲存股票歷史價格，INSERT OR REPLACE |
| `load_stock_prices(stock_code, start, end)` | 讀取指定股票與日期區間的價格資料 |
| `delete_stock_prices(stock_code)` | 刪除指定股票快取（強制重新下載用） |
| `get_latest_date(stock_code)` | 查詢該股票最新資料日期 |

### 3.2 `strategy.py` — 策略邏輯核心

**股票清單載入**：從 `stocks.db` 讀取，若 DB 不存在自動 fallback 到內建 20 支預設股票清單，確保 Streamlit Cloud 重啟後不當機。

**持倉狀態機 `_build_position(buy, sell)`**：

```
規則：
  buy 訊號  → position = 1（持有，直到 sell 訊號）
  sell 訊號 → position = 0（空手）
  兩者同時  → sell 優先（保守原則）
  初始狀態  → 0（空手）
```

### 3.3 `risk.py` — 摩擦成本與風險管理

所有回測頁面共用的核心模組。

| 函式 | 說明 |
|------|------|
| `apply_friction_and_risk(df, ...)` | 套用手續費、交易稅、停損停利，回傳含 Strategy 欄位的 df |
| `calc_performance(df, trading_days)` | 統一計算績效指標 dict |
| `build_risk_ui(prefix, market)` | 在 Streamlit 頁面渲染設定 UI，回傳設定 dict |

**`apply_friction_and_risk()` 輸出欄位**：

| 欄位 | 說明 |
|------|------|
| `Position_adj` | 套用停損停利後調整的持倉 |
| `Strategy` | 考慮手續費、交易稅後的實際日報酬 |
| `TradeCost` | 當日產生的交易成本 |
| `StopTriggered` | 是否由停損/停利觸發出場 |

---

## 4. 頁面功能規格

### 4.1 首頁 `app.py` — 台灣加權指數

顯示台灣加權指數（^TWII）近一年走勢，含蠟燭圖、MA20/MA60、成交量、MACD 三層子圖。快取 TTL 3600 秒。

### 4.2 回測系統 `2_回測系統.py`

**完整功能列表**：

| 功能 | 說明 |
|------|------|
| 股票 + 策略選擇 | 支援偏好記憶（user_backtest_pref.json） |
| 套用最佳化參數 | 偵測 user_best_params.json，勾選後自動帶入 |
| 摩擦成本設定 | 手續費、交易稅可自訂（expander 收合） |
| 停損停利設定 | 可啟用停損（%）/ 停利（%），觸發時強制出場 |
| 蠟燭圖 + 買賣訊號 | 買入 ▲（紅）、賣出 ▽（綠）標記在蠟燭圖上 |
| 策略 vs 買入持有報酬率圖 | 含手續費後的實際累積報酬率 |
| 策略績效總表 | 含總手續費成本欄位 |
| 歷史買賣紀錄 | 每筆交易：買賣日期、價格、持有天數、損益率、勝率 |
| 損益率長條圖 | 獲利紅色 / 虧損綠色 |
| 當前持倉狀態 | 今日狀態、最近買入/賣出日、未實現損益 |
| 停損停利觸發通知 | 顯示共觸發幾次 |
| 快取清除 | 強制重新從 yfinance 下載 |
| 參數最佳化 | Grid Search，詳見第7節 |

**資料清理流程**：
```
sort_index() → Close 轉數字 → 移除 NaN → 過濾 |DailyReturn| >= 0.5
```

**歷史買賣紀錄欄位型別規範**（避免 pyarrow 錯誤）：
- 賣出日期：字串型（持倉中顯示「持倉中」，不混用 date/str）
- 買入/賣出價格：float
- 持有天數：int
- 損益率：float

### 4.3 策略比較 `3_策略比較.py`

| 功能 | 說明 |
|------|------|
| 多股票多策略選擇 | Multiselect，偏好記憶 |
| 使用最佳化參數 | 勾選後自動查找 user_best_params.json |
| 摩擦成本設定 | 同回測系統，共用 risk.py |
| 停損停利設定 | 同回測系統 |
| 績效表 | 含總手續費欄位 |
| 累積報酬率長條圖 | 各股票 × 各策略並排 |
| 夏普比率長條圖 | 各股票 × 各策略並排 |
| Gemini AI 分析 | 詳見第8節 |

### 4.4 更新股票清單 `4_更新股票清單.py`

從 TWSE ISIN 公開資料抓取上市股票清單，寫入 stocks.db。環境感知路徑（本機 `./`，Cloud `/tmp/`）。

### 4.5 財報比較 `5_財報比較.py`

| 功能 | 說明 |
|------|------|
| FinMind Token 登入 | Sidebar 密碼輸入框，Session 內有效 |
| 財報類型選擇 | 季報（Q）/ 年報（A） |
| 多股票財報抓取 | Long format → Wide format pivot |
| 中英文科目自動對應 | 優先中文，fallback 英文 |
| 財報指標疊加 | 營收、毛利、營業利益、稅前淨利、稅後淨利、EPS |
| 策略回測整合 | 三層整合圖：蠟燭圖 + 財報、累積報酬率、財報趨勢 |
| 買賣點標記 | 蠟燭圖上標記策略進出場點 |
| EPS 趨勢比較 | 多股票 EPS 折線圖 |
| 財報摘要 metric | 平均營收、毛利、營業利益、稅後淨利 |
| 偏好記憶 | user_financial_pref.json |

**FinMind 安裝保護**：
```python
try:
    from FinMind.data import DataLoader
    FINMIND_AVAILABLE = True
except ImportError:
    FINMIND_AVAILABLE = False
```

### 4.6 虛擬幣回測 `6_虛擬幣回測.py`

| 功能 | 說明 |
|------|------|
| 多交易對選擇 | USDT 及 BTC 計價，BTC 計價自動換算 USDT 基準 |
| K 線週期 | 日線、4小時、1小時、30分鐘 |
| 策略選擇 | 所有台股策略 + 虛擬幣專屬 SMA/Hull 趨勢策略 |
| 套用最佳化參數 | 同台股，共用 user_best_params.json |
| 摩擦成本設定 | 預設幣安現貨 0.1%，無交易稅 |
| 停損停利設定 | 同台股 |
| 個別績效圖 | 每個交易對的買入持有 vs 策略 |
| 彙總折線比較圖 | 所有交易對累積報酬率 |
| 彙總長條圖 | 各交易對最終報酬率 |
| 績效總表 | 報酬率、波動、回撤、夏普比率、最新訊號 |
| 參數最佳化 | 針對第一個選擇的交易對，結果可儲存套用 |

---

## 5. 策略規格

### 5.1 策略清單

| 策略名稱 | 買入條件 | 賣出條件 | 主要參數 |
|---------|---------|---------|---------|
| 簡單均線交叉 | 短期 SMA 上穿長期 SMA | 短期 SMA 下穿長期 SMA | 短期均線、長期均線 |
| 反轉策略 | N 日跌幅 ≥ 閾值 | N 日跌幅 < 閾值 | 觀察天數、跌幅閾值 |
| 突破策略 | 收盤價突破 N 日高點 | 收盤價跌破 N 日低點 | 突破天數 |
| RSI 策略 | RSI 從超賣回升穿越買入閾值 | RSI 高於賣出閾值 | RSI 期間、買入閾值、賣出閾值 |
| MACD 策略 | MACD 線上穿訊號線 | MACD 線下穿訊號線 | 短期 EMA、長期 EMA、訊號線 |
| 布林通道策略 | 收盤價跌破下軌 | 收盤價突破上軌 | 期間、標準差倍數 |
| 黃金交叉 EMA 策略 | 短期 EMA 上穿長期 EMA | 短期 EMA 下穿長期 EMA | 短期 EMA、長期 EMA |
| 唐奇安通道策略 | 收盤價突破 N 日最高價 | 收盤價跌破 N 日最低價 | 期間 |
| SMA/Hull 趨勢策略 | 短趨勢線上穿長趨勢線 | 短趨勢線下穿長趨勢線 | type（sma/hull）、n1、n2 |

### 5.2 持倉值定義

| Position 值 | 意義 |
|------------|------|
| `1` | 持有（多單） |
| `0` | 空手（無部位） |

### 5.3 報酬率計算（含手續費）

```python
# 策略日報酬 = 昨日持倉 × 今日價格變化 − 今日交易成本
daily_return   = (price_t - price_t1) / price_t1
trade_cost     = buy_fee（進場日）or sell_fee + sell_tax（出場日）
strategy_return = daily_return - trade_cost   # 持倉日才計算
```

`shift(1)` 使用前一日持倉，避免未來函數偏差。

---

## 6. 摩擦成本與風險管理規格

### 6.1 手續費設定

| 市場 | 買入手續費 | 賣出手續費 | 交易稅 | 備註 |
|------|----------|----------|--------|------|
| 台股（一般股票） | 0.1425% | 0.1425% | 0.3% | 可自訂 |
| 台股（ETF） | 0.1425% | 0.1425% | 0.1% | 手動改交易稅 |
| 虛擬幣（Binance） | 0.1% | 0.1% | 0% | 可自訂 |

### 6.2 停損停利邏輯

採用逐筆迴圈計算（非向量化），每日檢查持倉中的浮動損益：

```
if 持倉中 and (price - entry_price) / entry_price <= -stop_loss:
    → 強制出場（停損），StopTriggered = True
if 持倉中 and (price - entry_price) / entry_price >= take_profit:
    → 強制出場（停利），StopTriggered = True
```

停損停利出場與策略訊號出場相同：同日若策略也有出場訊號，以停損/停利優先。

### 6.3 UI 設定

所有回測頁面都有 `⚙️ 摩擦成本 & 停損停利設定` expander，預設收合。各頁面用不同的 `prefix` 避免 widget key 衝突：

| 頁面 | prefix |
|------|--------|
| 回測系統 | `bt_` |
| 策略比較 | `cmp_` |
| 虛擬幣回測 | `crypto_` |

---

## 7. 參數最佳化規格

### 7.1 演算法

Grid Search（窮舉法）：對每個數值型參數設定「最小值、最大值、步長」，枚舉所有組合執行回測，依目標指標排序。

### 7.2 最佳化目標選項

| 目標 | 排序方式 |
|------|---------|
| 夏普比率 | 降序（越大越好） |
| 累積報酬率(%) | 降序（越大越好） |
| 最大回撤(%)（最小化） | 升序（越小越好） |

### 7.3 輸出結果

| 輸出 | 說明 |
|------|------|
| 最佳參數展示 | metric 卡片顯示各參數與績效指標 |
| Top 20 表格 | 帶 RdYlGn 色彩漸層 |
| 熱力圖 | 兩個 int 參數時顯示，X × Y → 指標值 |
| 折線圖 | 單一 int 參數時顯示，參數值 → 指標趨勢 |
| 最佳參數回測圖 | 用最佳參數直接跑回測並顯示累積報酬曲線 |
| 原始 vs 最佳比較表 | 改善幅度對比 |
| 儲存按鈕 | 寫入 user_best_params.json |
| 過度擬合警告 | 提醒樣本內最佳不代表未來有效 |

### 7.4 最佳參數跨頁共用

**儲存格式**（user_best_params.json）：
```json
{
  "2330.TW_MACD 策略": {
    "stock_code": "2330.TW",
    "strategy_name": "MACD 策略",
    "params": {"短期 EMA": 10, "長期 EMA": 22, "訊號線": 7},
    "metrics": {"累積報酬率(%)": 45.2, "夏普比率": 1.3, "最大回撤(%)": -18.5},
    "saved_at": "2026-04-06 10:30"
  }
}
```

**套用流程**：
```
回測系統最佳化 → 💾 儲存 → session_state → st.rerun()
→ 頁面頂部出現 checkbox → 勾選 → 參數固定顯示 → 🚀 開始回測
```

策略比較頁面：勾選「🏆 使用已儲存的最佳化參數」後，對每個「股票代號_策略名稱」自動查找，找到就用最佳參數，找不到就用預設參數。

---

## 8. AI 分析規格

### 8.1 適用頁面

策略比較（`3_策略比較.py`），回測完成後自動觸發。

### 8.2 模型

Google Gemini 2.5 Flash，使用 SSE Streaming 接收（`streamGenerateContent?alt=sse`）。

**選用 Streaming 的原因**：Gemini 2.5 Flash 有強制思考機制（thoughtsTokenCount 約 980），若使用一次性回應（`generateContent`），思考 token 會佔用輸出空間導致截斷。Streaming 方式思考與輸出分開串流，過濾 `thought=True` 的 chunk，只保留實際回答。

### 8.3 API 規格

```
端點：https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse
認證：x-goog-api-key header
溫度：0.4
maxOutputTokens：4096
重試：429 Rate Limit 時自動重試，15s / 30s / 45s 間隔
```

### 8.4 Prompt 結構

```
你是一位專業的台股量化交易分析師。

回測期間：{start} 至 {end}
回測績效表：{df_results.to_string()}

請依照以下結構回答：
1. 整體表現總結
2. 最佳策略推薦（依夏普比率 + 報酬率）
3. 風險提示（最大回撤）
4. 操作建議
```

### 8.5 安全性

API Key 建議存放於 Streamlit Secrets（`st.secrets["GEMINI_API_KEY"]`），不應硬寫在程式碼中。

---

## 9. 資料庫規格

### 9.1 `stock_data.db` — 股票歷史價格

**資料表**：`stock_price`

| 欄位 | 類型 | 說明 |
|------|------|------|
| Date | TEXT | 日期（YYYY-MM-DD），主鍵之一 |
| Open | REAL | 開盤價（原始市價，auto_adjust=False） |
| High | REAL | 最高價 |
| Low | REAL | 最低價 |
| Close | REAL | 收盤價 |
| Volume | INTEGER | 成交量 |
| Adj Close | REAL | 還原權值後收盤價（備存，不用於回測） |
| stock_code | TEXT | 股票代號，主鍵之一 |

**主鍵**：`(Date, stock_code)`，INSERT OR REPLACE 處理重複。

### 9.2 `stocks.db` — 股票清單

**資料表**：`stock_list`

| 欄位 | 類型 | 說明 |
|------|------|------|
| code | TEXT | 股票代號（如 `2330.TW`） |
| name | TEXT | 股票名稱（如 `台積電`） |

---

## 10. 資料來源

| 資料 | 來源 | 更新頻率 | 備註 |
|------|------|---------|------|
| 台股歷史股價 | yfinance | 每日收盤後 | `auto_adjust=False` 保留原始市價 |
| 台灣加權指數 | yfinance（`^TWII`） | TTL 3600 秒 | |
| 虛擬幣 OHLCV | ccxt / Binance | 即時抓取 | `@st.cache_data` 快取 |
| 上市股票清單 | TWSE ISIN | 手動觸發更新 | `https://isin.twse.com.tw` |
| 財報資料 | FinMind API | TTL 3600 秒 | 需 Token，免費帳號 600 次/hr |

---

## 11. 績效指標定義

| 指標 | 公式 | 說明 |
|------|------|------|
| 累積報酬率 | `(1 + r).cumprod() - 1` | 幾何累積，含手續費 |
| 夏普比率（台股） | `mean(r) / std(r) × √240` | 240 交易日年化 |
| 夏普比率（虛擬幣） | `mean(r) / std(r) × √365` | 365 天年化 |
| 年化波動率 | `std(r) × √240` 或 `× √365` | 依市場別調整 |
| 最大回撤（MDD） | `min((cum - cum.cummax()) / cum.cummax())` | 策略淨值計算 |
| 總手續費成本 | `sum(TradeCost)` | 所有進出場成本加總 |
| 勝率 | `count(pnl > 0) / count(closed trades)` | 已平倉交易計算 |
| 平均持有天數 | `mean(hold_days)` | 已平倉交易計算 |

---

## 12. 部署說明

### 12.1 本機執行

```bash
pip install -r requirements.txt
streamlit run app.py
```

### 12.2 Streamlit Cloud 部署

**必要步驟**：

1. 所有 `.py` 檔案（含 `risk.py`）Push 至 GitHub repo 根目錄
2. `pages/` 目錄放所有分頁
3. `requirements.txt` 確保包含：`streamlit、pandas、numpy、plotly、yfinance、sqlalchemy、requests、finmind、ccxt`

**路徑注意事項**：

Streamlit Cloud 的 repo 掛載於 `/mount/src/`（唯讀），所有需要寫入的檔案（SQLite、JSON）自動存放於 `/tmp/`，重啟後清空。

**Gemini API Key 安全存放**：

在 Streamlit Cloud `Manage app → Secrets` 加入：
```toml
GEMINI_API_KEY = "你的key"
```

程式碼改用：
```python
gemini_api_key = st.secrets.get("GEMINI_API_KEY", "")
```

### 12.3 Python 版本

建議使用 Python 3.11，相容性最佳。Python 3.13 部分套件（如 FinMind）可能有安裝問題。

---

## 13. 已知限制與注意事項

### 資料面

- yfinance 資料不包含盤中即時報價，最快 T+1 更新
- FinMind 免費帳號每小時限制 600 次，大量財報查詢建議分批
- 部分公司財報科目名稱（中英文）不同，可展開除錯 expander 確認

### 回測面

- 回測為收盤價回測，不考慮盤中滑價
- 手續費按設定比例扣除，不考慮最低手續費門檻（實際台股最低 20 元）
- 停損停利以收盤價觸發，不考慮盤中最高/最低價觸發
- 未模擬資金管理（全倉操作）
- 參數最佳化基於歷史資料，有過度擬合風險

### 系統面

- Streamlit Cloud 重啟後 `/tmp/` 資料消失，需重新下載股價快取
- `user_best_params.json` 存於本機時可持久保存，Cloud 環境重啟後消失
- SQLite 不支援多人同時寫入

---

*本文件依據 v2.0 程式碼撰寫，如有功能異動請同步更新。*
