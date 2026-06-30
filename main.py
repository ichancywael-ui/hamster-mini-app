import asyncio
import logging
import random
import json
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn

# استيراد كائن قاعدة البيانات من ملف db_manager.py
from db_manager import db

# ⚠️ ضع توكن البوت الخاص بك هنا من BotFather
BOT_TOKEN = "8229255088:AAEAByd0v43wMpUvZMrKHP0A0BFpmIEwvQE"

# إنشاء تطبيق FastAPI
app = FastAPI()

# تفعيل الـ CORS لكي يثق السيرفر بالطلبات القادمة من المتصفحات والواجهات
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# تهيئة البوت والـ Dispatcher لـ aiogram
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ----------------- أقسام الـ الـ API لخدمة الواجهة -----------------

@app.get("/")
async def serve_index():
    """تقديم صفحة الـ HTML مباشرة من السيرفر كواجهة رئيسية للميني أب"""
    return FileResponse("index.html")

@app.get("/api/user/{telegram_id}")
async def get_user_data(telegram_id: int):
    """نقطة اتصال لجلب بيانات المستخدم الحية للـ Mini App عند الفتح"""
    user = await db.get_user(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "balance": float(user["balance"]),
        "tickets_count": user["tickets_count"]
    }

@app.post("/api/open-ticket/{telegram_id}")
async def api_open_ticket(telegram_id: int):
    """فتح تيكت من داخل الـ Mini App مباشرة وتحديث البيانات حركياً دون إغلاق الواجهة"""
    random_reward = round(random.uniform(0.50, 10.00), 2)
    result = await db.open_lucky_ticket(telegram_id, random_reward)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
        
    # جلب بيانات العميل المحدثة لإعادتها للواجهة فوراً
    user = await db.get_user(telegram_id)
    
    return {
        "reward": result["reward"],
        "new_balance": float(user["balance"]),
        "tickets_count": user["tickets_count"]
    }

# ----------------- أقسام الـ الـ Handlers الخاصة بالبوت -----------------

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """أمر انطلاق البوت وتسجيل المستخدم في قاعدة البيانات"""
    telegram_id = message.from_user.id
    username = message.from_user.username or f"user_{telegram_id}"
    first_name = message.from_user.first_name or "المستثمر"
    
    # محاولة إضافة المستخدم إذا لم يكن موجوداً
    await db.add_user(telegram_id, username, first_name)
    
    # استخدام رابط السيرفر لفتح تطبيق الويب المصغر
    # (بما أن الـ HTML أصبح على ريلواي، نضع رابط السيرفر نفسه هنا)
    web_app_url = "https://hamster-mini-app-production.up.railway.app/" # قم بتحديث هذا الرابط برابط ريلواي الجديد الخاص بك لاحقاً إذا تغير
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="🚀 افتح التطبيق المصغر",
        web_app=WebAppInfo(url=web_app_url)
    ))
    
    await message.answer(
        f"👋 أهلاً بك يا {first_name} في بوت الاستثمار الحركي المتكامل!\n\n"
        f"اضغط على الزر أدناه لفتح تطبيق الويب المصغر، وتفقد رصيدك وتحديثات استثمارك بشكل مباشر.",
        reply_markup=builder.as_markup()
    )

@dp.message(lambda message: message.web_app_data is not None)
async def handle_mini_app_data(message: types.Message):
    """استقبال البيانات الصامتة القادمة من الواجهة (مثل طلب شراء الباقات)"""
    telegram_id = message.from_user.id
    
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get("action")
        
        if action == "buy_package":
            package_type = data.get("package")
            amounts = {'main': 500.0, 'plan_1': 25.0, 'plan_2': 50.0}
            amount = amounts.get(package_type, 0.0)
            
            result = await db.buy_investment_package(telegram_id, package_type, amount)
            
            if result["success"]:
                await message.answer(
                    f"✅ تم تفعيل الباقة ({package_type}) بنجاح!\n"
                    f"خصم مبلغ: {amount}$ من رصيدك، وبدأت الأرباح بالعمل الحسابي التلقائي.",
                    parse_mode="Markdown"
                )
            else:
                await message.answer(result["message"])
                
    except Exception as e:
        logging.error(f"Error handling web app data: {e}")
        await message.answer(f"❌ حدث خطأ أثناء معالجة البيانات: {e}")

# ----------------- محرك الأرباح التلقائي المستمر -----------------

async def auto_payout_monitor():
    """محرك خلفي مستمر يعمل كل دقيقة للتحقق من أرباح الاستثمارات وصبها للحسابات"""
    while True:
        try:
            # دالة توزيع الأرباح الموجودة في db_manager
            await db.process_auto_payouts()
        except Exception as e:
            logging.error(f"❌ خطأ في محرك الأرباح التلقائي: {e}")
        await asyncio.sleep(60) # التحقق يتكرر كل 60 ثانية

# ----------------- الدالة الرئيسية لإقلاع المنظومة -----------------

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # 1. تهيئة والاتصال بقاعدة بيانات MySQL وإنشاء الجداول
    await db.initialize()
    
    # 2. تشغيل محرك الأرباح التلقائي في الخلفية
    asyncio.create_task(auto_payout_monitor())
    
    # 3. جلب البورت الديناميكي الذي تفرضه منصة Railway تلقائياً
    railway_port = int(os.environ.get("PORT", 8000))
    
    # تشغيل خادم FastAPI لخدمة الـ API وصفحة الـ HTML معاً
    config = uvicorn.Config(app, host="0.0.0.0", port=railway_port, log_level="info")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    
    print("🤖 البوت والسيرفر الـ API يعملان معاً بنجاح على Railway...")
    
    # 4. بدء استقبال رسائل البوت (Polling)
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
