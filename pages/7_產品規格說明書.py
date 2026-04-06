import streamlit as st

# 設定頁面配置
st.set_page_config(page_title="產品規格說明書 v2.0", layout="wide")

def main():
    st.title("📑 台股策略回測系統 — 規格說明書 v2.0")
    
    st.info("💡 本頁面內容同步自專案 README.md，供使用者在系統內隨時查閱技術細節與指標定義。")

    # 使用 f-string 或原始字串確保內容完整，並修正您提到的引號/括號問題
    readme_content = """
**專案名稱**：台股策略回測系統  
**作者**：Richard Weng  
**版本**：v2.0  
**最後更新**：2026-04-06  

---

## 1. 系統概述
本系統為一套基於 **Python + Streamlit** 建構的量化交易回測平台，支援台股與虛擬貨幣的策略回測、多策略比較、財報分析、參數最佳化等功能。系統採用 SQLite 作為本地資料快取，透過 yfinance 抓取台股歷史股價，透過 ccxt 抓取幣安虛擬幣行情。

## 2. 技術架構
* **前端框架**: Streamlit
* **資料處理**: Pandas, NumPy
* **資料庫**: SQLite (SQLAlchemy 介面)
* **圖表套件**: Plotly (互動式 K 線圖、損益圖)
* **API 整合**: `yfinance`, `FinMind`, `ccxt`

## 3. 模組說明
* **策略引擎 (`strategy.py`)**: 包含均線交叉、布林通道、RSI、唐奇安通道等邏輯。
* **風險管理 (`risk.py`)**: 處理交易摩擦成本（手續費、稅金）、停損停利邏輯。
* **資料儲存 (`database.py`)**: 管理 SQLite 連線，自動切換雲端與本地路徑。

## 4. 績效指標定義 (Performance Metrics)

| 指標名稱 | 計算邏輯 / 公式 | 備註 |
| :--- | :--- | :--- |
| **累積報酬率** | `(期末淨值 / 期初淨值) - 1` | 包含手續費損耗 |
| **夏普比率** | `mean(r) / std(r) × √240` | 台股以 240 日年化計算 |
| **最大回撤 (MDD)** | `min((cum - cum.cummax()) / cum.cummax())` | 衡量策略最極端虧損 |
| **勝率** | `獲利交易次數 / 總交易次數` | 以已平倉交易為準 |
| **總手續費成本** | `sum(交易稅 + 手續費)` | 視覺化摩擦成本影響 |

---

## 5. 策略規格 (Strategies)
1. **均線策略 (MA Cross)**: 短天期均線與長天期均線的黃金/死亡交叉。
2. **布林通道 (Bollinger Bands)**: 股價觸碰下軌買入，觸碰上軌賣出。
3. **RSI 策略**: 強弱指標超買/超賣區間逆勢或順勢操作。
4. **唐奇安通道 (Donchian)**: 突破過去 N 日最高價買入，跌破 N 日最低價賣出。

## 6. 參數最佳化 (Optimization)
系統支援**網格搜索 (Grid Search)**，透過 `itertools.product` 窮舉所有參數組合，自動尋找最高報酬率或最高夏普比率的設定，並允許使用者一鍵儲存最佳參數至 `user_best_params.json`。

## 7. 已知限制
* **資料延遲**: yfinance 提供的資料非即時行情。
* **滑價風險**: 回測未考慮大單成交時對市場價格的衝擊。
* **生存者偏差**: 回測對象目前僅限於現有的上市股票，未包含已下市公司。

---
    """
    
    # 渲染 Markdown 內容
    st.markdown(readme_content)

    st.success("✅ 規格書讀取完畢")

if __name__ == "__main__":
    main()