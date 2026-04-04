from FinMind.data import DataLoader

api = DataLoader()

try:
    df = api.taiwan_stock_financial_statement(
        stock_id="2330",
        start_date="2020-01-01",
        end_date="2023-12-31",
        report_type="季報"  # 改成 report_type 參數
    )
    print(df.head())
except TypeError:
    # 如果還是出錯，嘗試不帶 report_type
    df = api.taiwan_stock_financial_statement(
        stock_id="2330",
        start_date="2020-01-01",
        end_date="2023-12-31"
    )
    print(df.head())
