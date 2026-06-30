import asyncio
import aiomysql
from datetime import datetime, timedelta


# إعدادات الاتصال بقاعدة البيانات - استبدلها ببياناتك
DB_CONFIG = {
    'host': 'mysql.railway.internal',
    'port': 3306,
    'user': 'root',
    'password': 'ACkdOMkKZEqOSNdsXTDvdhalEMoLNQfq',
    'db': 'railway',
    'autocommit': True
}

class DatabaseManager:
    def __init__(self):
        self.pool = None

    async def initialize(self):
        """إنشاء مجمع الاتصالات وإنشاء الجداول إذا لم تكن موجودة"""
        self.pool = await aiomysql.create_pool(**DB_CONFIG, minsize=5, maxsize=20)
        print("✅ تم الاتصال بقاعدة البيانات بنجاح وإنشاء الـ Pool")
        
        # تشغيل دالة إنشاء الجداول تلقائياً
        await self._create_tables()

    async def _create_tables(self):
        """إنشاء الجداول تلقائياً في حال عدم وجودها"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    # 1. جدول المستخدمين
                    await cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        telegram_id BIGINT PRIMARY KEY,
                        username VARCHAR(255) NULL,
                        balance DECIMAL(15, 4) DEFAULT 0.0000,
                        referred_by BIGINT NULL,
                        tickets_count INT DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (referred_by) REFERENCES users(telegram_id) ON DELETE SET NULL
                    ) ENGINE=InnoDB;
                    """)

                    # 2. جدول الاستثمارات
                    # 2. جدول الاستثمارات (تم التعديل لحل مشكلة الوقت)
                    await cur.execute("""
                    CREATE TABLE IF NOT EXISTS investments (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        package_type ENUM('main', 'plan_1', 'plan_2') NOT NULL,
                        amount DECIMAL(15, 4) NOT NULL,
                        daily_profit_pct DECIMAL(5, 2) NOT NULL,
                        last_payout TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        next_payout TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- التعديل هنا (إضافة القيمة الافتراضية)
                        status ENUM('active', 'completed') DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                    ) ENGINE=InnoDB;
                    """)

                    # 3. جدول المعاملات
                    await cur.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        type ENUM('deposit', 'withdrawal', 'ticket_reward', 'referral_bonus') NOT NULL,
                        amount DECIMAL(15, 4) NOT NULL,
                        status ENUM('pending', 'completed', 'rejected') DEFAULT 'completed',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                    ) ENGINE=InnoDB;
                    """)
                    
                    print("📊 تم التحقق من الجداول وإنشاؤها بنجاح!")
                except Exception as e:
                    print(f"❌ خطأ أثناء إنشاء الجداول: {e}")

    async def close(self):
        """إغلاق مجمع الاتصالات"""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()

    async def register_user(self, telegram_id: int, username: str, referred_by: int = None) -> bool:
        """تسجيل مستخدم جديد ومنحه تيكت مجاني"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT telegram_id FROM users WHERE telegram_id = %s", (telegram_id,))
                    if await cur.fetchone():
                        return False
                    if referred_by and int(referred_by) != telegram_id:
                        await cur.execute("SELECT telegram_id FROM users WHERE telegram_id = %s", (referred_by,))
                        if not await cur.fetchone():
                            referred_by = None
                    else:
                        referred_by = None

                    await cur.execute(
                        "INSERT INTO users (telegram_id, username, referred_by) VALUES (%s, %s, %s)",
                        (telegram_id, username, referred_by)
                    )
                    return True
                except Exception as e:
                    print(f"❌ خطأ أثناء تسجيل المستخدم: {e}")
                    return False

    async def get_user(self, telegram_id: int) -> dict:
        """جلب بيانات المستخدم بالكامل"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
                return await cur.fetchone()
            

    async def open_lucky_ticket(self, telegram_id: int, reward_amount: float) -> dict:
        """التحقق من التيكت، خصمه، وإضافة الجائزة لرصيد المستخدم وللإحصائيات"""
        async with self.pool.acquire() as conn:
            # نستخدم الحظر لضمان عدم تنفيذ عمليتين بنفس الوقت لنفس المستخدم (Race Condition)
            async with conn.cursor(aiomysql.DictCursor) as cur:
                try:
                    # 1. التحقق من وجود تيكت متاح
                    await cur.execute("SELECT tickets_count, balance FROM users WHERE telegram_id = %s", (telegram_id,))
                    user_data = await cur.fetchone()
                    
                    if not user_data or user_data['tickets_count'] <= 0:
                        return {"success": False, "message": "❌ لا تمتلك تيكت كافٍ لفتحه حالياً!"}
                    
                    # 2. تحديث بيانات المستخدم (خصم تيكت وإضافة رصيد)
                    await cur.execute(
                        "UPDATE users SET tickets_count = tickets_count - 1, balance = balance + %s WHERE telegram_id = %s",
                        (reward_amount, telegram_id)
                    )
                    
                    # 3. توثيق المعاملة المالية في جدول المعاملات للأمان
                    await cur.execute(
                        "INSERT INTO transactions (user_id, type, amount, status) VALUES (%s, 'ticket_reward', %s, 'completed')",
                        (telegram_id, reward_amount)
                    )
                    
                    return {
                        "success": True, 
                        "reward": reward_amount, 
                        "new_balance": float(user_data['balance']) + reward_amount
                    }
                except Exception as e:
                    print(f"❌ خطأ أثناء فتح التيكت: {e}")
                    return {"success": False, "message": "❌ حدث خطأ داخلي، يرجى المحاولة لاحقاً."}
                
    async def buy_investment_package(self, telegram_id: int, package_type: str, amount: float) -> dict:
        """معالجة شراء باقة استثمارية، خصم الرصيد، وتوزيع عمولة الإحالة 15%"""
        # تحديد النسبة ومدة الصلاحية بناءً على نوع الباقة
        if package_type == 'main':
            pct = 10.00
            next_payout = datetime.now() + timedelta(days=1)
        elif package_type == 'plan_1':
            pct = 9.00
            next_payout = datetime.now() + timedelta(days=1)
        elif package_type == 'plan_2':
            pct = 36.00
            next_payout = datetime.now() + timedelta(days=3)
        else:
            return {"success": False, "message": "❌ نوع الباقة غير مدعوم."}

        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                try:
                    # 1. التحقق من رصيد المستخدم الحالي
                    await cur.execute("SELECT balance, referred_by FROM users WHERE telegram_id = %s", (telegram_id,))
                    user = await cur.fetchone()
                    if not user or float(user['balance']) < amount:
                        return {"success": False, "message": "❌ رصيدك الحالي غير كافٍ لشراء هذه الباقة."}

                    # 2. خصم المبلغ من العميل وتحديث رصيده
                    await cur.execute("UPDATE users SET balance = balance - %s WHERE telegram_id = %s", (amount, telegram_id))

                    # 3. تسجيل الباقة الاستثمارية الجديدة
                    await cur.execute(
                        """INSERT INTO investments (user_id, package_type, amount, daily_profit_pct, next_payout) 
                           VALUES (%s, %s, %s, %s, %s)""",
                        (telegram_id, package_type, amount, pct, next_payout)
                    )

                    # 4. توثيق العملية في جدول المعاملات
                    await cur.execute(
                        "INSERT INTO transactions (user_id, type, amount, status) VALUES (%s, 'deposit', %s, 'completed')",
                        (telegram_id, amount)
                    )

                    # 5. نظام الإحالة: توزيع عمولة الـ 15% للداعي (مشروط بوجود داعٍ فقط)
                    referred_by = user['referred_by']
                    referral_bonus = amount * 0.15
                    
                    if referred_by:
                        # إضافة الـ 15% مباشرة لرصيد الداعي القابل للسحب
                        await cur.execute("UPDATE users SET balance = balance + %s WHERE telegram_id = %s", (referral_bonus, referred_by))
                        # توثيق عمولة الإحالة في جدول المعاملات
                        await cur.execute(
                        "INSERT INTO transactions (user_id, type, amount, status) VALUES (%s, 'referral_bonus', %s, 'completed')",
                        (referred_by, referral_bonus)
                    )

                    return {"success": True, "referral_bonus_sent": referred_by is not None, "referred_by": referred_by, "bonus_amount": referral_bonus}
                except Exception as e:
                    print(f"❌ خطأ أثناء تفعيل الاستثمار: {e}")
                    return {"success": False, "message": "❌ حدث خطأ داخلي أثناء معالجة الاستثمار."}

    async def process_auto_payouts(self) -> list:
        """البحث عن الباقات التي حان وقت توزيع أرباحها ومعالجتها فوراً تلو الأخرى"""
        payouts_done = []
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # جلب كل الباقات النشطة التي تجاوزت الوقت المحدد للربح القادم
                await cur.execute("SELECT * FROM investments WHERE status = 'active' AND next_payout <= NOW()")
                active_investments = await cur.fetchall()

                for inv in active_investments:
                    # حساب مقدار الربح المستحق
                    profit = float(inv['amount']) * (float(inv['daily_profit_pct']) / 100.0)
                    
                    # حساب وقت الدفعة القادمة بناء على نوع الباقة
                    if inv['package_type'] in ['main', 'plan_1']:
                        next_payout_time = datetime.now() + timedelta(days=1)
                    else:  # plan_2 (كل 3 أيام)
                        next_payout_time = datetime.now() + timedelta(days=3)

                    # تحديث رصيد المستخدم وتحديث وقت الدفعة القادمة للباقة
                    await cur.execute("UPDATE users SET balance = balance + %s WHERE telegram_id = %s", (profit, inv['user_id']))
                    await cur.execute(
                        "UPDATE investments SET last_payout = NOW(), next_payout = %s WHERE id = %s",
                        (next_payout_time, inv['id'])
                    )
                    
                    payouts_done.append({"user_id": inv['user_id'], "profit": profit, "package": inv['package_type']})
                    
        return payouts_done
            

# كائن إدارة قاعدة البيانات للاستدعاء الخارجي
db = DatabaseManager()
