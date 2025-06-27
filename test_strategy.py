import yfinance as yf
import pandas as pd
from strategy import apply_strategy

def test_cross_strategy():
    df = yf.download('2330.TW', start='2022-01-01', end='2022-12-31')
    params = {"短期均線": 20, "長期均線": 60}
    df = apply_strategy(df, "簡單均線交叉", params)
    assert 'Position' in df.columns
    print("✅ 簡單均線交叉策略測試通過")

def test_breakout_strategy():
    df = yf.download('0056.TW', start='2022-01-01', end='2022-12-31')
    params = {"突破天數": 20}
    df = apply_strategy(df, "突破策略", params)
    assert 'Position' in df.columns
    print("✅ 突破策略測試通過")

def test_rsi_macd():
    df = yf.download('00870.TW', start='2023-01-01', end='2024-01-01')
    rsi_params = {"RSI 期間": 14, "買入閾值": 30, "賣出閾值": 70}
    macd_params = {"短期 EMA": 12, "長期 EMA": 26, "訊號線": 9}
    df_rsi = apply_strategy(df.copy(), "RSI 策略", rsi_params)
    df_macd = apply_strategy(df.copy(), "MACD 策略", macd_params)
    assert 'Position' in df_rsi.columns
    assert 'Position' in df_macd.columns
    print("✅ RSI & MACD 策略測試通過")

if __name__ == "__main__":
    test_cross_strategy()
    test_breakout_strategy()
