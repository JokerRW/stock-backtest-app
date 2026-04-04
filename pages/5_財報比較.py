import streamlit as st
import pandas as pd
import datetime
from FinMind.data import DataLoader
import plotly.express as px
from strategy import stock_list

st.title("📈 台股財報比較與排名")

# =====================
# 左側 API Token 輸入
# =====================
with st.sidebar:
    st.markdown("## 🔑 FinMind API 設定")
    st.markdown(
        "請先至 [FinMind 官網](https://finmindtrade.com) 註冊並取得 API Token，"
        "免費帳號每天可查詢約 600 次。"
    )
    finmind_token = st.text_input(
        "輸入 FinMind API Token",
        type="password",
        placeholder="貼上你的 Token...",
        help="Token 僅在此次 session 使用，不會儲存"
    )
    if finmind_token:
        st.success("✅ Token 已輸入")
    else:
        st.warning("⚠️ 尚未輸入 Token，財報資料無法載入")

if not finmind_token:
    st.info("👈 請先在左側輸入 FinMind API Token 才能使用此功能")
    st.stop()

# =====================
# 股票與財報設定
# =====================
stock_options = [f"{name} ({code})" for code, name in stock_list.items()]
stocks_selected = st.multiselect("選擇股票（多選）", stock_options, default=stock_options[:5])
stock_codes = [s.split("(")[-1].strip(")") for s in stocks_selected]

report_type_map = {"季報": "Q", "年報": "A"}
report_type_label = st.selectbox("選擇財報類型", list(report_type_map.keys()), index=0)
report_type = report_type_map[report_type_label]

today = datetime.date.today()
start_date = f"{today.year - 3}-01-01"
end_date = today.strftime("%Y-%m-%d")

if not stock_codes:
    st.warning("請至少選擇一支股票")
    st.stop()

# =====================
# FinMind 綜合損益表回傳的是長格式（long format）
# type 欄位是科目名稱，value 欄位是數值
# 需要先篩選科目再 pivot 成寬格式
# =====================

# FinMind 損益表常見的 type 值（中文科目名稱）
REVENUE_TYPE      = "營業收入"
OPERATING_TYPE    = "營業利益（損失）"
NET_INCOME_TYPE   = "本期淨利（淨損）"

# 備用科目名稱（不同公司可能略有不同）
REVENUE_ALT       = ["營業收入合計", "收益合計"]
OPERATING_ALT     = ["營業利益", "營業損益"]
NET_INCOME_ALT    = ["本期淨利", "稅後淨利", "本期損益"]

def find_value(df_pivot, primary, alternates):
    """從 pivot 後的欄位中找到對應科目，優先用主要名稱，找不到用備用"""
    if primary in df_pivot.columns:
        return df_pivot[primary]
    for alt in alternates:
        if alt in df_pivot.columns:
            return df_pivot[alt]
    return pd.Series([None] * len(df_pivot))

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_financial_data(token: str, stock_code: str, start_date: str, end_date: str):
    _api = DataLoader()
    _api.login_by_token(api_token=token)
    try:
        df = _api.taiwan_stock_financial_statement(
            stock_id=stock_code,
            start_date=start_date,
            end_date=end_date,
        )
        return df
    except Exception as e:
        return pd.DataFrame()

if st.button("📥 開始抓取財報資料"):
    all_data = []

    with st.spinner("財報資料抓取中，請稍候..."):
        for code in stock_codes:
            # FinMind 用純數字代號
            fmind_code = code.replace(".TW", "").replace(".TWO", "")

            df_fin = fetch_financial_data(finmind_token, fmind_code, start_date, end_date)

            if df_fin is None or df_fin.empty:
                st.warning(f"⚠️ {code} 無法取得財報資料，可能超過每日額度或代號不支援")
                continue

            # 篩選季報或年報（type 欄位中有 Q/A 標記，有些版本用 date 季末判斷）
            if "type" in df_fin.columns:
                df_type_filtered = df_fin[df_fin["type"] == report_type]
                # 若篩選後空白，表示此股票 type 欄位格式不同，用全部資料
                if df_type_filtered.empty:
                    df_type_filtered = df_fin
            else:
                df_type_filtered = df_fin

            # 顯示實際科目供除錯
            with st.expander(f"🔍 {code} 原始科目列表（除錯用）", expanded=False):
                if "type" in df_type_filtered.columns:
                    st.write(sorted(df_type_filtered["type"].unique().tolist()))
                st.dataframe(df_type_filtered.head(5))

            # ✅ Long format → Wide format（pivot）
            # 欄位：date, stock_id, type, value
            if "type" not in df_type_filtered.columns or "value" not in df_type_filtered.columns:
                st.warning(f"⚠️ {code} 財報欄位格式不符預期，跳過")
                continue

            try:
                df_pivot = df_type_filtered.pivot_table(
                    index="date",
                    columns="type",
                    values="value",
                    aggfunc="first"
                ).reset_index()
            except Exception as e:
                st.warning(f"⚠️ {code} pivot 失敗：{e}，跳過")
                continue

            if df_pivot.empty:
                st.warning(f"⚠️ {code} pivot 後資料為空，跳過")
                continue

            # 取出三大指標
            revenue_series     = find_value(df_pivot, REVENUE_TYPE, REVENUE_ALT)
            operating_series   = find_value(df_pivot, OPERATING_TYPE, OPERATING_ALT)
            net_income_series  = find_value(df_pivot, NET_INCOME_TYPE, NET_INCOME_ALT)

            avg_revenue     = pd.to_numeric(revenue_series,    errors='coerce').mean()
            avg_operating   = pd.to_numeric(operating_series,  errors='coerce').mean()
            avg_net_income  = pd.to_numeric(net_income_series, errors='coerce').mean()

            if pd.isna(avg_revenue):
                st.warning(f"⚠️ {code} 無有效營收資料，請展開除錯視窗確認科目名稱")
                continue

            all_data.append({
                "股票代號": code,
                "股票名稱": stock_list.get(code, code),
                "平均營收": avg_revenue,
                "平均營業利益": avg_operating,
                "平均稅後淨利": avg_net_income,
            })

    if not all_data:
        st.error("❌ 無法取得任何股票的有效財報資料，請確認 Token 額度或展開除錯視窗查看科目名稱")
        st.stop()

    df_summary = pd.DataFrame(all_data)
    df_summary_sorted = df_summary.sort_values(by="平均營收", ascending=False)

    st.markdown(f"### 📊 財報指標排名（近3年{report_type_label}平均）")
    st.dataframe(df_summary_sorted.style.format({
        "平均營收": "{:,.0f}",
        "平均營業利益": "{:,.0f}",
        "平均稅後淨利": "{:,.0f}",
    }), use_container_width=True)

    df_plot = df_summary_sorted.melt(
        id_vars=["股票名稱"],
        value_vars=["平均營收", "平均營業利益", "平均稅後淨利"],
        var_name="指標",
        value_name="數值"
    )

    fig = px.bar(
        df_plot,
        x="股票名稱",
        y="數值",
        color="指標",
        barmode="group",
        title=f"{report_type_label} 財報指標平均值比較（近3年）",
        labels={"數值": "金額 (新台幣千元)", "股票名稱": "股票"}
    )
    st.plotly_chart(fig, use_container_width=True)
