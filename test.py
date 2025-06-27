import yfinance as yf
df = yf.download("^TWII", period="5d", auto_adjust=True)
print(df.tail())