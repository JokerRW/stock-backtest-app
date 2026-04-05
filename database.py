# database.py
import os
import pandas as pd
from sqlalchemy import create_engine, text
import json

# =====================
# 路徑設定：自動偵測 Streamlit Cloud 環境
# Streamlit Cloud 將 repo 掛載在 /mount/src/（唯讀）
# SQLite 需要可寫目錄，Cloud 用 /tmp/，本機用當前目錄
# =====================
def _get_db_path() -> str:
    if os.path.exists("/mount/src"):
        # Streamlit Cloud：強制用 /tmp/
        return "sqlite:////tmp/stock_data.db"
    else:
        # 本機：使用當前工作目錄
        return "sqlite:///stock_data.db"

# Lazy engine：第一次呼叫時才建立，確保路徑判斷在執行時發生
_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_get_db_path(), echo=False, future=True)
    return _engine

# =====================
# 建立資料庫表格（若尚未存在）
# =====================
def init_db():
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS stock_price (
            Date TEXT NOT NULL,
            Open REAL,
            High REAL,
            Low REAL,
            Close REAL,
            Volume INTEGER,
            "Adj Close" REAL,
            stock_code TEXT NOT NULL,
            PRIMARY KEY (Date, stock_code)
        )
        """))
        conn.commit()

# =====================
# 儲存股票歷史價格
# =====================
def save_stock_prices(df: pd.DataFrame, stock_code: str):
    engine = _get_engine()
    init_db()  # 確保表格存在
    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    if 'Date' not in df.columns:
        df.reset_index(inplace=True)
    df['Date'] = pd.to_datetime(df['Date']).dt.normalize()
    if 'index' in df.columns:
        df.drop(columns=['index'], inplace=True)

    df['stock_code'] = stock_code

    with engine.begin() as conn:
        for _, row in df.iterrows():
            conn.execute(text("""
                INSERT OR REPLACE INTO stock_price (
                    Date, Open, High, Low, Close, Volume, "Adj Close", stock_code
                ) VALUES (
                    :Date, :Open, :High, :Low, :Close, :Volume, :AdjClose, :stock_code
                )
            """), {
                "Date":       row["Date"].strftime('%Y-%m-%d'),
                "Open":       row.get("Open",      None),
                "High":       row.get("High",      None),
                "Low":        row.get("Low",       None),
                "Close":      row.get("Close",     None),
                "Volume":     row.get("Volume",    None),
                "AdjClose":   row.get("Adj Close", None),
                "stock_code": stock_code,
            })

# =====================
# 讀取股票歷史價格
# =====================
def load_stock_prices(stock_code: str, start_date=None, end_date=None):
    engine = _get_engine()
    init_db()
    query  = "SELECT * FROM stock_price WHERE stock_code = :code"
    params = {"code": stock_code}
    if start_date:
        query += " AND Date >= :start_date"
        params["start_date"] = str(start_date)
    if end_date:
        query += " AND Date <= :end_date"
        params["end_date"] = str(end_date)
    query += " ORDER BY Date ASC"

    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn, params=params, parse_dates=["Date"])
    df.set_index("Date", inplace=True)
    return df

# =====================
# 刪除指定股票的所有快取資料
# =====================
def delete_stock_prices(stock_code: str):
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM stock_price WHERE stock_code = :code"
        ), {"code": stock_code})

# =====================
# 檢查股票的最新日期
# =====================
def get_latest_date(stock_code: str):
    engine = _get_engine()
    query  = "SELECT MAX(Date) as max_date FROM stock_price WHERE stock_code = :code"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"code": stock_code}).fetchone()
    return result.max_date if result and result.max_date else None

# =====================
# 儲存回測結果
# =====================
def save_strategy_result(stock_code: str, strategy_name: str, params: dict, df: pd.DataFrame):
    engine = _get_engine()
    df = df.copy()
    df.reset_index(inplace=True)
    df['stock_code']    = stock_code
    df['strategy_name'] = strategy_name
    df['params']        = json.dumps(params, ensure_ascii=False)

    delete_sql = """
    DELETE FROM strategy_result
    WHERE stock_code = :code AND strategy_name = :name AND params = :params
    """
    with engine.begin() as conn:
        conn.execute(text(delete_sql), {
            "code":   stock_code,
            "name":   strategy_name,
            "params": json.dumps(params, ensure_ascii=False),
        })

    df_to_save = df[["Date", "stock_code", "strategy_name", "params",
                     "Position", "Strategy", "DailyReturn"]]
    df_to_save.to_sql("strategy_result", con=engine, if_exists="append", index=False)

# =====================
# 讀取回測結果
# =====================
def load_strategy_result(stock_code: str, strategy_name: str, params: dict):
    engine     = _get_engine()
    params_json = json.dumps(params, ensure_ascii=False)
    query = """
    SELECT * FROM strategy_result
    WHERE stock_code = :code AND strategy_name = :name AND params = :params
    ORDER BY Date ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn, params={
            "code":   stock_code,
            "name":   strategy_name,
            "params": params_json,
        }, parse_dates=["Date"])
    df.set_index("Date", inplace=True)
    return df
