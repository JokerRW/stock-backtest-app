import requests
import pandas as pd

url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"

# 加入 verify=False 避免 SSL 憑證驗證失敗
response = requests.get(url, verify=False)
response.encoding = 'big5'

df = pd.read_html(response.text)[0]
df = df.dropna(how='all')
df.columns = df.iloc[0]
df = df[1:]

print(df.head())

