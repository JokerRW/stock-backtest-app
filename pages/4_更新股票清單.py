import os
import streamlit as st
import pandas as pd
import sqlite3
import requests

# Streamlit Cloud 唯讀目錄，資料庫改存 /tmp/
_IS_CLOUD = os.path.exists("/mount/src")
_DB_DIR   = "/tmp" if _IS_CLOUD else "."
STOCKS_DB = os.path.join(_DB_DIR, "stocks.db")

st.set_page_config(page_title="更新台灣股票清單", layout="wide")
st.title("📈 更新台灣股票清單（含上市股票）")

@st.cache_data(ttl=3600)
def fetch_tw_stock_list():
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, verify=False, timeout=10)
        res.encoding = "big5"
        df = pd.read_html(res.text)[0]

        # 清理表格
        df.columns = df.iloc[0]  # 把第0列當欄名
        df = df.iloc[2:]         # 跳過前兩列（標題與分類）
        df = df[df['市場別'] == '上市']  # 只保留上市公司

        # 分割「有價證券代號及名稱」成代號與名稱
        df[['代號', '名稱']] = df['有價證券代號及名稱'].str.extract(r'(\d+)\s+(.+)')

        # 組出 code 與 name 欄位（與 yfinance 一致）
        df['code'] = df['代號'] + '.TW'
        df['name'] = df['名稱']

        return df[['code', 'name']].dropna()

    except Exception as e:
        st.error(f"抓取股票清單失敗: {e}")
        return pd.DataFrame()

def save_stock_list_to_db(df):
    conn = sqlite3.connect(STOCKS_DB)
    df.to_sql("stock_list", conn, if_exists="replace", index=False)
    conn.close()

# 顯示原始及清理後資料
if st.button("📥 抓取並更新股票清單"):
    with st.spinner("抓取中..."):
        df_stocks = fetch_tw_stock_list()
        if df_stocks.empty:
            st.warning("⚠️ 沒有抓到有效資料")
        else:
            st.success(f"✅ 共更新 {len(df_stocks)} 支股票")
            save_stock_list_to_db(df_stocks)
            st.subheader("📋 更新後資料（前10筆）：")
            st.dataframe(df_stocks.head(10), use_container_width=True)

# 顯示目前資料庫內容
conn = sqlite3.connect(STOCKS_DB)
try:
    df_db = pd.read_sql("SELECT * FROM stock_list", conn)
    st.subheader("🗂️ 資料庫中股票清單：")
    st.dataframe(df_db, use_container_width=True)
except Exception:
    st.info("資料庫中尚無資料，請先按上方按鈕進行更新")
conn.close()
