import pandas as pd
import sqlite3

# 從資料庫讀取股票清單 (code + name)
def load_stock_list_from_db(db_path="stocks.db"):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT code, name FROM stock_list", conn)
    conn.close()
    return df

# 根據關鍵字搜尋股票清單 (code 或 name)
def search_stocks(keyword, df_stock):
    if not keyword:
        return df_stock
    kw = keyword.lower()
    mask = df_stock['code'].str.lower().str.contains(kw) | df_stock['name'].str.lower().str.contains(kw)
    return df_stock[mask]

stock_df = load_stock_list_from_db()

# 建立字典方便原本用法 (code:name)
stock_list = dict(zip(stock_df['code'], stock_df['name']))

strategies = {
    "簡單均線交叉": {
        "description": "當短期均線上穿長期均線時買入，下穿時賣出。",
        "parameters": {
            "短期均線": 20,
            "長期均線": 60,
        }
    },
    "反轉策略": {
        "description": "當股價連跌多天、跌幅超過指定比例時，進場買入。",
        "parameters": {
            "觀察天數": 3,
            "跌幅閾值（％）": 5.0
        }
    },
    "突破策略": {
        "description": "當收盤價突破 N 日高點時買入，跌破 N 日低點時賣出。",
        "parameters": {
            "突破天數": 20
        }
    },
    "RSI 策略": {
        "description": "當 RSI 低於買入閾值時進場，高於賣出閾值時出場。",
        "parameters": {
            "RSI 期間": 14,
            "買入閾值": 30,
            "賣出閾值": 70
        }
    },
    "MACD 策略": {
        "description": "當 MACD 線上穿訊號線時買入，下穿時賣出。",
        "parameters": {
            "短期 EMA": 12,
            "長期 EMA": 26,
            "訊號線": 9
        }
    },
    "布林通道策略": {
        "description": "當收盤價上穿布林通道上軌時買入，下穿下軌時賣出。",
        "parameters": {
            "期間": 20,
            "標準差倍數": 2.0
        }
    },
        "黃金交叉 EMA 策略": {
        "description": "短期 EMA 上穿長期 EMA 為黃金交叉（買入），反之為死亡交叉（賣出）。",
        "parameters": {
            "短期 EMA": 12,
            "長期 EMA": 26
        }
    },
        "唐奇安通道策略": {
        "description": "收盤價突破過去 N 日最高價買入，跌破最低價賣出。",
        "parameters": {
            "期間": 20
        }
    },
}

def apply_strategy(df, strategy_name, params):
    df = df.copy()
    buy = pd.Series(False, index=df.index)
    sell = pd.Series(False, index=df.index)

    if strategy_name == "簡單均線交叉":
        short = int(params["短期均線"])
        long = int(params["長期均線"])
        df['SMA_short'] = df['Close'].rolling(window=short).mean()
        df['SMA_long'] = df['Close'].rolling(window=long).mean()
        buy = (df['SMA_short'] > df['SMA_long']) & (df['SMA_short'].shift(1) <= df['SMA_long'].shift(1))
        sell = (df['SMA_short'] < df['SMA_long']) & (df['SMA_short'].shift(1) >= df['SMA_long'].shift(1))

    elif strategy_name == "反轉策略":
        days = int(params["觀察天數"])
        threshold = float(params["跌幅閾值（％）"]) / 100
        df['Return'] = df['Close'].pct_change(periods=days)
        buy = df['Return'] <= -threshold
        sell = ~buy

    elif strategy_name == "突破策略":
        period = int(params["突破天數"])
        if len(df) < period + 5:
            raise ValueError(f"\U0001F4C9 資料天數過短（目前 {len(df)} 天），「突破策略」至少需要 {period + 5} 天。")
        df['High_N'] = df['Close'].rolling(window=period, min_periods=period).max()
        df['Low_N'] = df['Close'].rolling(window=period, min_periods=period).min()
        buy = (df['Close'] > df['High_N'].shift(1)).fillna(False)
        sell = (df['Close'] < df['Low_N'].shift(1)).fillna(False)

    elif strategy_name == "RSI 策略":
        rsi_period = int(params["RSI 期間"])
        buy_level = float(params["買入閾值"])
        sell_level = float(params["賣出閾值"])
        delta = df['Close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(rsi_period).mean()
        avg_loss = loss.rolling(rsi_period).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        buy = (df['RSI'] < buy_level).fillna(False)
        sell = (df['RSI'] > sell_level).fillna(False)

    elif strategy_name == "MACD 策略":
        short_ema = int(params["短期 EMA"])
        long_ema = int(params["長期 EMA"])
        signal_period = int(params["訊號線"])
        df['EMA_short'] = df['Close'].ewm(span=short_ema, adjust=False).mean()
        df['EMA_long'] = df['Close'].ewm(span=long_ema, adjust=False).mean()
        df['MACD'] = df['EMA_short'] - df['EMA_long']
        df['Signal'] = df['MACD'].ewm(span=signal_period, adjust=False).mean()
        buy = ((df['MACD'] > df['Signal']) & (df['MACD'].shift(1) <= df['Signal'].shift(1))).fillna(False)
        sell = ((df['MACD'] < df['Signal']) & (df['MACD'].shift(1) >= df['Signal'].shift(1))).fillna(False)

    elif strategy_name == "布林通道突破策略":
        period = int(params["期間"])
        std_mult = float(params["標準差倍數"])
        df['MA'] = df['Close'].rolling(window=period).mean()
        df['STD'] = df['Close'].rolling(window=period).std()
        df['Upper'] = df['MA'] + std_mult * df['STD']
        df['Lower'] = df['MA'] - std_mult * df['STD']
        buy = df['Close'] < df['Lower']
        sell = df['Close'] > df['Upper']

    elif strategy_name == "黃金交叉 EMA 策略":
        short = int(params["短期 EMA"])
        long = int(params["長期 EMA"])
        df['EMA_short'] = df['Close'].ewm(span=short, adjust=False).mean()
        df['EMA_long'] = df['Close'].ewm(span=long, adjust=False).mean()
        buy = (df['EMA_short'] > df['EMA_long']) & (df['EMA_short'].shift(1) <= df['EMA_long'].shift(1))
        sell = (df['EMA_short'] < df['EMA_long']) & (df['EMA_short'].shift(1) >= df['EMA_long'].shift(1))

    elif strategy_name == "唐奇安通道策略":
        period = int(params["期間"])
        df['Donchian_High'] = df['High'].rolling(window=period).max()
        df['Donchian_Low'] = df['Low'].rolling(window=period).min()
        buy = df['Close'] > df['Donchian_High'].shift(1)
        sell = df['Close'] < df['Donchian_Low'].shift(1)

    df['Position'] = 0
    df.loc[buy, 'Position'] = 1
    df.loc[sell, 'Position'] = -1
    df['Position'] = df['Position'].replace(0, pd.NA).ffill().fillna(0).astype(int)

    return df


if __name__ == "__main__":
    # 範例測試搜尋功能
    print("=== 搜尋關鍵字 '台積' ===")
    print(search_stocks("台積", stock_df))

    # 範例印出目前股票清單
    print(f"\n股票清單共 {len(stock_list)} 支")