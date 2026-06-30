import asyncio
import aiomysql
from datetime import datetime, timedelta

# إعدادات الاتصال بقاعدة البيانات القياسية والمصححة لأحرف صغيرة لـ aiomysql
DB_CONFIG = {
    'host': 'mysql.railway.internal',
    'port': 3306,
    'user': 'root',
    'password': 'ACkdOMkKZEqOSNdsXTDvdhalEMoLNQfq',
    'db': 'railway',
    'autocommit': True
}

class DatabaseManager:
    def init(self):  # تصحيح الـ init لتعمل بشكل صحيح عند الاستدعاء
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
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """)

                    # 2. جدول الاستثمارات
                    await cur.execute("""
                    CREATE TABLE IF NOT EXISTS investments (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT,
                        package_type VARCHAR(50),
                        amount DECIMAL(15, 4),
                        daily_profit_pct DECIMAL(5, 2),
                        status VARCHAR(20) DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_payout TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        next_payout TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(telegram_id)
                    )
                    """)
                    print("✅ تم التحقق من الجداول أو إنشاؤها بنجاح.")
                except Exception as e:
                    print(f"❌ خطأ أثناء إنشاء الجداول: {e}")

    async def add_user(self, telegram_id, username=None, first_name=None, referred_by=None):
        """إضافة مستخدم جديد أو تحديث بياناته (متوافقة مع اسم الدالة في main.py)"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # التحقق إذا كان المستخدم موجوداً مسبقاً لعدم تكراره
                await cur.execute("SELECT telegram_id FROM users WHERE telegram_id = %s", (telegram_id,))
                user = await cur.fetchone()
                if not user:
                    await cur.execute(
                        "INSERT INTO users (telegram_id, username, referred_by, tickets_count) VALUES (%s, %s, %s, 1)",
                        (telegram_id, username, referred_by)
                    )
                    return True
                return False

    async def get_user(self, telegram_id):
        """جلب بيانات المستخدم الكاملة برقم الآي دي"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
                return await cur.fetchone()

    async def open_lucky_ticket(self, telegram_id, reward_amount):
        """تفعيل تيكت الحظ وخصم تيكت وإضافة الجائزة للرصيد"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT tickets_count, balance FROM users WHERE telegram_id = %s", (telegram_id,))
                user = await cur.fetchone()
                
                if not user or user['tickets_count'] < 1:
                    return {"success": False, "message": "عذراً، لا تمتلك تيكت كافٍ!"}
                
                # خصم التيكت وإضافة المكافأة للرصيد
                new_tickets = user['tickets_count'] - 1
                new_balance = float(user['balance']) + float(reward_amount)
                
                await cur.execute(
                    "UPDATE users SET tickets_count = %s, balance = %s WHERE telegram_id = %s",
                    (new_tickets, new_balance, telegram_id)
                )
                return {"success": True, "reward": reward_amount, "new_balance": new_balance}

    async def buy_investment_package(self, telegram_id, package_type, amount):
        """شراء حزمة استثمارية وخصم المبلغ من الحساب وإنشاء سجل الاستثمار وبدء العداد"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT balance FROM users WHERE telegram_id = %s", (telegram_id,))
                user = await cur.fetchone()
                
                if not user or float(user['balance']) < float(amount):
                    return {"success": False, "message": "❌ رصيدك الحالي غير كافٍ لتفعيل هذه الباقة!"}
                
                # خصم قيمة الباقة من رصيد المستخدم
                await cur.execute("UPDATE users SET balance = balance - %s WHERE telegram_id = %s", (amount, telegram_id))
                
                # تحديد العائد اليومي والوقت المتبقي حسب نوع الباقة
                profit_pct = 10.0 if package_type == 'main' else (9.0 if package_type == 'plan_1' else 12.0)
                days_to_add = 1 if package_type in ['main', 'plan_1'] else 3
                next_payout = datetime.now() + timedelta(days=days_to_add)
                
                # إضافة سجل الاستثمار
                await cur.execute(
                    """INSERT INTO investments (user_id, package_type, amount, daily_profit_pct, next_payout) 
                       VALUES (%s, %s, %s, %s, %s)""",
                    (telegram_id, package_type, amount, profit_pct, next_payout)
                )
                return {"success": True}

    async def process_auto_payouts(self):
        """معالجة توزيع الأرباح التلقائية"""
        payouts_done = []
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM investments WHERE status = 'active' AND next_payout <= NOW()")
                active_investments = await cur.fetchall()

                for inv in active_investments:
                    profit = float(inv['amount']) * (float(inv['daily_profit_pct']) / 100.0)
                    
                    if inv['package_type'] in ['main', 'plan_1']:
                        next_payout_time = datetime.now() + timedelta(days=1)
                    else:
                        next_payout_time = datetime.now() + timedelta(days=3)

                    await cur.execute("UPDATE users SET balance = balance + %s WHERE telegram_id = %s", (profit, inv['user_id']))
                    await cur.execute(
                        "UPDATE investments SET last_payout = NOW(), next_payout = %s WHERE id = %s",
                        (next_payout_time, inv['id'])
                    )
                    payouts_done.append({"user_id": inv['user_id'], "profit": profit, "package": inv['package_type']})
                    
        return payouts_done

db = DatabaseManager()
