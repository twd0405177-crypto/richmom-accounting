import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import json
import datetime
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from PIL import Image

# 1. 設定網頁標題
st.set_page_config(page_title="RichMom 懶人記帳", page_icon="logo.png", layout="centered")
logo_url = "https://raw.githubusercontent.com/twd0405177-crypto/richmom-accounting/main/logo.png"

st.markdown(
    f"""
    <head>
        <link rel="apple-touch-icon" href="{logo_url}">
    </head>
    """,
    unsafe_allow_html=True
)
st.title("💰 RichMom 懶人記帳 (姊妹分享版)")

# --- 側邊欄：使用者設定 ---
with st.sidebar:
    st.header("⚙️ 設定區")
    user_api_key = st.text_input("1️⃣ Gemini API Key", type="password")
    user_sheet_name = st.text_input("2️⃣ Google 試算表名稱", placeholder="例如：2026年記帳本")
    
    st.divider()
    st.header("💳 帳戶與信用卡")
    default_cards = "台新Gogo卡, 富邦J卡, 國泰CUBE, 玉山Ubear, 中信LinePay"
    user_cards_str = st.text_area("常用信用卡", value=default_cards)
    user_cards = [x.strip() for x in user_cards_str.split(",") if x.strip()]
    
    default_banks = "中國信託, 郵局, 台新Richart"
    user_banks_str = st.text_area("常用網銀/銀行", value=default_banks)
    user_banks = [x.strip() for x in user_banks_str.split(",") if x.strip()]
    all_payment_methods = ["現金"] + user_cards + user_banks

    st.divider()
    st.header("🔄 固定支出設定")
    st.caption("格式：項目,金額 (一行一個)")
    default_fixed = "房租,15000\nNetflix,270\n手機費,999"
    fixed_expenses_str = st.text_area("每月固定支出", value=default_fixed, height=100)

    st.divider()
    st.header("🎯 預算與警示")
    monthly_budget = st.number_input("本月總預算 (元)", value=20000, step=1000)
    alert_threshold = st.number_input("🚨 單筆警示金額", value=3000, step=500)
    
    if "gcp_service_account" in st.secrets:
        bot_email = st.secrets["gcp_service_account"]["client_email"]
        with st.expander("🤖 查看機器人 Email"):
            st.code(bot_email, language="text")
    else:
        st.error("尚未設定 secrets.toml")
        st.stop()

if not user_api_key or not user_sheet_name:
    st.warning("👈 請在左側填寫 **API Key** 與 **試算表名稱** 才能開始使用喔！")
    st.stop()

# --- 初始化 AI ---
try:
    genai.configure(api_key=user_api_key)
    available_models = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
    except: pass

    target_model = "gemini-1.5-flash"
    for m in available_models:
        if "gemini-2.5" in m: target_model = m; break
        if "gemini-2.0" in m: target_model = m; break
    
    model = genai.GenerativeModel(target_model)
    st.sidebar.success(f"✅ AI 大腦：{target_model.replace('models/', '')}")
except Exception as e:
    st.sidebar.error(f"連線失敗: {e}")
    st.stop()

# --- 連線 Google Sheets ---
def get_google_sheet_client(sheet_name):
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    try:
        creds_dict = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open(sheet_name).sheet1
        return sheet
    except Exception as e:
        return None

sheet = get_google_sheet_client(user_sheet_name)

if sheet is None:
    st.error(f"❌ 找不到名稱為「{user_sheet_name}」的試算表！")
    st.stop()

# --- 讀取資料 ---
def load_data():
    try:
        data = sheet.get_all_records()
        expected_cols = ["日期", "項目", "金額", "付款方式"]
        if not data: return pd.DataFrame(columns=expected_cols)
        df = pd.DataFrame(data)
        if "付款方式" not in df.columns: df["付款方式"] = "現金"
        if not {"日期", "項目", "金額"}.issubset(df.columns): return pd.DataFrame(columns=expected_cols)
        
        if not df.empty:
            df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
            df['金額'] = pd.to_numeric(df['金額'], errors='coerce').fillna(0)
            df['年份月份'] = df['日期'].dt.strftime('%Y-%m')
        return df
    except:
        return pd.DataFrame(columns=["日期", "項目", "金額", "付款方式"])

df = load_data()
if not df.empty and '年份月份' in df.columns:
    all_months = sorted(df['年份月份'].dropna().unique(), reverse=True)
else:
    all_months = []

categories = [
    "早餐", "午餐", "晚餐", "飲料/咖啡", "交通/加油", "超市/買菜", 
    "日常用品", "治裝/衣服", "娛樂/聚餐", "醫療/保健", "房租/水電", 
    "投資/儲蓄", "學習/教育", "其他"
]

# --- 主功能區 ---
tab1, tab2, tab_inst, tab3, tab4 = st.tabs(["📸 AI 記帳", "✍️ 手動輸入", "💳 分期計算", "📊 財務分析", "📂 資料管理"])

if "ocr_result" not in st.session_state:
    st.session_state.ocr_result = None

# === Tab 1: AI 記帳 ===
with tab1:
    st.subheader("🤖 AI 智慧記帳")
    text_input = st.text_input("輸入內容 (文字或照片)", key="ai_input")
    uploaded_file = st.file_uploader("上傳照片", type=["jpg", "png"])
    if uploaded_file: st.image(uploaded_file, width=200)

    if st.button("✨ AI 分析", type="primary"):
        if text_input or uploaded_file:
            today_str = str(datetime.date.today())
            methods_str = ", ".join(all_payment_methods)
            prompt = f"""
            今天是 {today_str}。付款方式清單：{methods_str}。
            任務：提取記帳資訊。日期若為過去(如昨天、上週五)請推算。
            回傳 JSON: {{ "date": "YYYY-MM-DD", "item": "項目", "amount": 100, "method": "付款方式" }}
            輸入：{text_input}
            """
            with st.spinner("AI 分析中..."):
                try:
                    inputs = [prompt]
                    if uploaded_file: inputs.append(Image.open(uploaded_file))
                    response = model.generate_content(inputs)
                    result = json.loads(response.text.replace("```json", "").replace("```", "").strip())
                    st.session_state.ocr_result = result
                    st.success("分析成功！")
                except: st.error("AI 讀取失敗")

    if st.session_state.ocr_result:
        with st.container(border=True):
            current_amount = st.session_state.ocr_result.get("amount", 0)
            if current_amount >= alert_threshold: st.error(f"⚠️ 嗶嗶！${current_amount} 超過警示線！")
            
            c1, c2 = st.columns(2)
            new_date = c1.text_input("日期", st.session_state.ocr_result.get("date"))
            new_item = c2.text_input("項目", st.session_state.ocr_result.get("item"))
            c3, c4 = st.columns(2)
            new_amount = c3.number_input("金額", value=current_amount)
            
            ai_method = st.session_state.ocr_result.get("method", "現金")
            if ai_method not in all_payment_methods: ai_method = "現金"
            try: idx = all_payment_methods.index(ai_method)
            except: idx = 0
            new_method = c4.selectbox("付款方式", all_payment_methods, index=idx)
            
            if st.button("✅ 確認寫入"):
                try:
                    sheet.append_row([new_date, new_item, new_amount, new_method])
                    st.toast("已儲存！")
                    st.session_state.ocr_result = None
                    st.rerun()
                except Exception as e: st.error(f"寫入失敗: {e}")

# === Tab 2: 手動輸入 ===
with tab2:
    st.subheader("✍️ 手動輸入")
    
    with st.expander("🔄 一鍵加入固定支出 (可選日期)", expanded=False):
        st.caption(f"將自動加入：\n{fixed_expenses_str}")
        
        fixed_date = st.date_input("📅 請選擇入帳日期", datetime.date.today(), key="fixed_date_picker")
        
        if st.button("🚀 確認加入固定支出"):
            try:
                target_date_str = str(fixed_date)
                lines = fixed_expenses_str.split('\n')
                rows_to_add = []
                for line in lines:
                    if "," in line:
                        item, amt = line.split(',')
                        rows_to_add.append([target_date_str, item.strip(), int(amt.strip()), "現金"])
                
                if rows_to_add:
                    sheet.append_rows(rows_to_add)
                    st.success(f"已成功將 {len(rows_to_add)} 筆支出加入至 {target_date_str} ！")
                    st.rerun()
            except Exception as e: st.error(f"失敗: {e}")
            
    st.divider()
    
    with st.form("manual"):
        c1, c2 = st.columns(2)
        m_date = c1.date_input("日期", datetime.date.today())
        m_category = c2.selectbox("項目", categories)
        m_custom = c2.text_input("詳細說明 (優先使用)")
        c3, c4 = st.columns(2)
        m_amount = c3.number_input("金額", min_value=0)
        m_method = c4.selectbox("付款方式", all_payment_methods)
        if st.form_submit_button("✅ 新增"):
            final_item = m_custom if m_custom else m_category
            if m_amount >= alert_threshold: st.warning(f"⚠️ 大額消費：${m_amount}")
            sheet.append_row([str(m_date), final_item, m_amount, m_method])
            st.success("已新增")
            st.rerun()

# === Tab 3: 分期計算 (已修正：包含銀行帳戶) ===
with tab_inst:
    st.subheader("💳 分期與定期扣款計算機")
    with st.container(border=True):
        i_col1, i_col2 = st.columns(2)
        i_item = i_col1.text_input("商品名稱", placeholder="例如：iPhone 16")
        
        # 這裡修改了：合併信用卡和銀行帳戶
        installment_sources = user_cards + user_banks
        i_card = i_col2.selectbox("扣款方式 (信用卡/銀行)", installment_sources if installment_sources else ["信用卡"])
        
        i_col3, i_col4 = st.columns(2)
        i_price = i_col3.number_input("總金額", min_value=0, step=100, value=30000)
        i_months = i_col4.selectbox("期數", [3, 6, 12, 18, 24, 30, 36], index=2)

        st.markdown("---")
        interest_mode = st.radio("利息計算", ["零利率", "固定手續費", "利率 (%)"], horizontal=True)
        
        total_pay = i_price
        if interest_mode == "固定手續費":
            interest_amt = st.number_input("總手續費", min_value=0)
            total_pay += interest_amt
        elif interest_mode == "利率 (%)":
            rate = st.number_input("總利率 %", min_value=0.0, step=0.1)
            total_pay += int(i_price * (rate / 100))

        monthly_pay = int(total_pay / i_months)
        start_date = st.date_input("首期扣款日", datetime.date.today())
        
        st.info(f"📊 每期約 **${monthly_pay:,}** (總額 ${total_pay:,})")

        if st.button("📝 生成分期並寫入", type="primary"):
            if i_item and total_pay > 0:
                try:
                    rows_to_add = []
                    curr = start_date
                    for m in range(1, i_months + 1):
                        name = f"{i_item} ({m}/{i_months})"
                        pay = monthly_pay + (total_pay - monthly_pay * i_months) if m==1 else monthly_pay
                        rows_to_add.append([str(curr), name, pay, i_card])
                        curr += relativedelta(months=1)
                    sheet.append_rows(rows_to_add)
                    st.balloons()
                    st.success("已寫入分期資料！")
                    st.rerun()
                except Exception as e: st.error(f"失敗: {e}")

# === Tab 4: 財務分析 ===
with tab3:
    if not df.empty and all_months:
        month = st.selectbox("📅 選擇月份", all_months)
        m_df = df[df['年份月份'] == month]
        total_spend = int(m_df['金額'].sum())
        remaining = monthly_budget - total_spend
        usage_percent = min(total_spend / monthly_budget, 1.0) if monthly_budget > 0 else 0
        
        st.subheader(f"💰 {month} 財務概況")
        c_m1, c_m2, c_m3 = st.columns(3)
        c_m1.metric("預算", f"${monthly_budget:,}")
        c_m2.metric("支出", f"${total_spend:,}", delta=f"-{int(usage_percent*100)}%")
        c_m3.metric("剩餘", f"${remaining:,}", delta_color="normal" if remaining > 0 else "inverse")
        st.progress(usage_percent, text=f"使用率：{int(usage_percent*100)}%")
        
        st.divider()
        if st.button("🤖 呼叫 AI 教練"):
            with st.spinner("AI 思考中..."):
                try:
                    cat_sum = m_df.groupby('項目')['金額'].sum().to_dict()
                    top = max(cat_sum, key=cat_sum.get) if cat_sum else "無"
                    prompt = f"使用者 {month} 消費：預算{monthly_budget}, 支出{total_spend}, 花最多{top}。請給予毒舌但中肯的理財建議 (100字內)。"
                    res = model.generate_content(prompt)
                    st.info(res.text)
                except: st.error("AI 忙線中")

        st.divider()
        if total_spend > 0:
            c1, c2 = st.columns(2)
            with c1: st.plotly_chart(px.pie(m_df, values='金額', names='項目', hole=0.4), use_container_width=True)
            with c2:
                if "付款方式" in m_df.columns: st.plotly_chart(px.pie(m_df, values='金額', names='付款方式', hole=0.4), use_container_width=True)
    else: st.info("無資料")

# === Tab 5: 資料管理 ===
with tab4:
    st.subheader("📂 資料管理")
    if not df.empty:
        search_term = st.text_input("🔍 搜尋資料", placeholder="關鍵字...")
        df_edit = df.copy()
        df_edit["🗑️ 刪除"] = False
        
        if search_term:
            mask = df_edit.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
            df_edit = df_edit[mask]
        
        cols = ["🗑️ 刪除", "日期", "項目", "金額", "付款方式"]
        for c in cols:
            if c not in df_edit.columns: df_edit[c] = ""
            
        edited_df = st.data_editor(
            df_edit[cols], 
            num_rows="dynamic", use_container_width=True,
            column_config={"🗑️ 刪除": st.column_config.CheckboxColumn("刪除?", default=False)}
        )
        
        if st.button("💾 儲存變更"):
            if search_term: st.warning("搜尋模式下無法儲存")
            else:
                try:
                    rows = edited_df[edited_df["🗑️ 刪除"] == False].drop(columns=["🗑️ 刪除"])
                    header = ["日期", "項目", "金額", "付款方式"]
                    rows = rows.reindex(columns=header)
                    rows['日期'] = pd.to_datetime(rows['日期']).dt.strftime('%Y-%m-%d')
                    rows['付款方式'] = rows['付款方式'].fillna("現金")
                    sheet.clear()
                    sheet.update([header] + rows.values.tolist())
                    st.success("更新成功")
                    st.rerun()
                except Exception as e: st.error(f"失敗: {e}")
    else: st.info("無資料")


