#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自動交易機器人 - 資產管理模式（由虛擬幣回測系統匯出）
策略    : SMA/Hull 趨勢策略
參數    : {'type': 'hull', 'n1': 20, 'n2': 140}
交易對  : ['BTC/USDT', 'ETH/USDT']
產生時間: 2026-04-26 22:37

核心邏輯: 以固定「目標持倉數量」為基準進行持倉校正
  - 策略看多 -> 持倉校正至目標數量
  - 策略看空 -> 清倉（目標數量 = 0）
  - 差異價值 < 門檻 -> 不交易（節省手續費）

樹莓派部署:
  pip install ccxt pandas numpy
  crontab: 0 */4 * * * /usr/bin/python3 /home/pi/trading_bot.py >> /home/pi/bot.log 2>&1

警告: 請勿將此檔案上傳至公開平台，API Key 已內嵌。
"""

import ccxt
import pandas as pd
import numpy as np
import datetime
import json
import os
import time

# =====================
# 設定區
# =====================
API_KEY    = "My key"
API_SECRET = "My secret"
MODE       = "TEST"       # TEST (僅模擬) / MARKET (實盤下單)

# 各交易對目標持倉數量（看多時持有，看空時清倉）
TARGET_CONFIG = {'BTC/USDT': 0.0006, 'ETH/USDT': 0.0075}

SYMBOLS              = list(TARGET_CONFIG.keys())
FREQ                 = "4h"
LOOKBACK             = 250
MIN_VALUE_THRESHOLD  = 5.0   # 差異價值超過此 USDT 才執行交易
BOT_NAME             = "crypto-bot-SMA-Hull-趨勢策略"
PARAMS               = {'type': 'hull', 'n1': 20, 'n2': 140}
STATE_FILE           = "bot_state_crypto-bot-SMA-Hull-趨勢策略.json"

# =====================
# 工具函式
# =====================
def log(msg):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

def display_account_summary(exchange):
    """顯示帳戶中所有非零持倉及其估值"""
    log("🔍 掃描帳戶總持倉...")
    try:
        balance = exchange.fetch_balance()
        total   = balance["total"]
        summary = []
        for asset, qty in total.items():
            if qty <= 0:
                continue
            if asset == "USDT":
                summary.append({"資產": asset, "數量": f"{qty:.4f}", "估值": f"{qty:.2f} USDT"})
                continue
            try:
                ticker     = exchange.fetch_ticker(f"{asset}/USDT")
                value_usdt = qty * ticker["last"]
                if value_usdt > 1.0:
                    summary.append({"資產": asset, "數量": f"{qty:.6f}", "估值": f"{value_usdt:.2f} USDT"})
            except Exception:
                summary.append({"資產": asset, "數量": f"{qty:.6f}", "估值": "未知"})
        if summary:
            print("\n" + "="*45)
            print(f"{'資產':<10} {'數量':<15} {'估值 (USDT)':<15}")
            print("-" * 45)
            for item in summary:
                print(f"{item['資產']:<10} {item['數量']:<15} {item['估值']:<15}")
            print("="*45 + "\n")
        else:
            log("ℹ️ 帳戶無顯著資產")
    except Exception as e:
        log(f"⚠️ 讀取帳戶失敗: {e}")

def get_ohlcv(exchange, symbol, freq, lookback):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=freq, limit=lookback)
    df    = pd.DataFrame(ohlcv, columns=["timestamp","Open","High","Low","Close","Volume"])
    df["Date"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("Date", inplace=True)
    return df

def get_signal(df, p):
    """回傳 is_bullish: True=看多, False=看空"""
    close  = df["Close"]
    high   = df["High"]
    low    = df["Low"]
    volume = df["Volume"]
    is_bullish = False
    n1, n2 = int(p.get("n1", 30)), int(p.get("n2", 130))
    def wma(s, n):
        w = pd.Series(range(1, n+1))
        return s.rolling(n).apply(lambda x: (x*w).sum()/w.sum(), raw=True)
    def hma(s, n):
        h = int(n/2)
        return wma(2*wma(s, h) - wma(s, n), int(n**0.5))
    t1, t2 = hma(close, n1), hma(close, n2)
    is_bullish = bool(t1.iloc[-1] > t2.iloc[-1])
    return is_bullish

# =====================
# 主要執行邏輯
# =====================
def run():
    log(f"=== {BOT_NAME} 啟動 (模式: {MODE}) ===")

    exchange = ccxt.binance({
        "apiKey":          API_KEY,
        "secret":          API_SECRET,
        "enableRateLimit": True,
        "options":         {"defaultType": "spot"},
    })

    try:
        exchange.load_markets()
    except Exception as e:
        log(f"❌ 交易所連線失敗: {e}")
        return

    # 顯示帳戶總覽
    display_account_summary(exchange)

    try:
        balance = exchange.fetch_balance()
    except Exception as e:
        log(f"❌ 讀取餘額失敗: {e}")
        return

    for symbol in SYMBOLS:
        log(f"--- 策略檢查: {symbol} ---")
        base_asset = symbol.split("/")[0]

        # 1. 取得實際持倉數量
        actual_qty = float(balance.get(base_asset, {}).get("total", 0))

        try:
            # 2. 取得市價與 K 線資料
            ticker     = exchange.fetch_ticker(symbol)
            curr_price = float(ticker["last"])
            df         = get_ohlcv(exchange, symbol, FREQ, LOOKBACK)
            is_bullish = get_signal(df, PARAMS)
        except Exception as e:
            log(f"❌ 數據讀取失敗 {symbol}: {e}")
            continue

        # 3. 決定目標持倉數量
        target_qty = TARGET_CONFIG.get(symbol, 0.0) if is_bullish else 0.0

        # 4. 計算差額
        diff_qty   = target_qty - actual_qty
        diff_value = abs(diff_qty * curr_price)

        log(f"趨勢分析: {'🟢 看多' if is_bullish else '🔴 看空'}")
        log(f"持倉狀態: 實際 {actual_qty:.6f} | 目標 {target_qty:.6f}")
        log(f"待校正量: {diff_qty:.6f} {base_asset} (約 {diff_value:.2f} USDT)")

        # 5. 差異超過門檻才交易
        if diff_value > MIN_VALUE_THRESHOLD:
            side      = "buy" if diff_qty > 0 else "sell"
            order_qty = float(exchange.amount_to_precision(symbol, abs(diff_qty)))
            if order_qty > 0:
                log(f"⚠️ 觸發部位校正 -> {side.upper()} {order_qty} {base_asset}")
                if MODE == "MARKET":
                    try:
                        order = exchange.create_order(symbol, "market", side, order_qty)
                        log(f"✅ {side.upper()} 執行成功！單號: {order['id']}")
                    except Exception as e:
                        log(f"❌ 交易失敗: {e}")
                else:
                    log(f"🧪 [TEST] 擬執行 {side} {order_qty} {base_asset} @ {curr_price:.4f}")
            else:
                log("ℹ️ 修正數量過小，跳過")
        else:
            log(f"⏸️ 差異 ({diff_value:.2f} USDT) 未達門檻 {MIN_VALUE_THRESHOLD} USDT，無需調整")

        time.sleep(1)

    log("=== 任務完畢 ===")

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log(f"❌ 系統層級錯誤: {e}")
