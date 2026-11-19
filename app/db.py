from contextlib import contextmanager
import hashlib
import json
import sqlite3
from datetime import date, timedelta
from .config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL,
 username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(user_id TEXT PRIMARY KEY REFERENCES users(id), balance_fen INTEGER NOT NULL CHECK(balance_fen>=0));
CREATE TABLE IF NOT EXISTS recipients(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL, phone_tail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recipient_details(recipient_id TEXT PRIMARY KEY REFERENCES recipients(id),
 user_id TEXT NOT NULL REFERENCES users(id), bank_name TEXT NOT NULL, account_number TEXT NOT NULL,
 UNIQUE(user_id,account_number));
CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, at TEXT NOT NULL,
 direction TEXT NOT NULL, amount_fen INTEGER NOT NULL CHECK(amount_fen>0), category TEXT NOT NULL,
 merchant TEXT NOT NULL, note TEXT NOT NULL, recipient_id TEXT, action_id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, merchant TEXT NOT NULL,
 amount_fen INTEGER NOT NULL, period TEXT NOT NULL, next_at TEXT NOT NULL, status TEXT NOT NULL, cancellable INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS customer_contexts(user_id TEXT PRIMARY KEY REFERENCES users(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, goal TEXT NOT NULL,
 status TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0, messages TEXT NOT NULL DEFAULT '[]',
 nodes TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, task_id TEXT NOT NULL,
 kind TEXT NOT NULL, params TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 status TEXT NOT NULL, risk TEXT NOT NULL, created_at TEXT NOT NULL,
 otp_hash TEXT, otp_expires REAL, otp_attempts INTEGER NOT NULL DEFAULT 0,
 verified_version INTEGER, result TEXT, UNIQUE(task_id,kind,params));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
 task_id TEXT, action_id TEXT, at TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transfer_repeats(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 task_id TEXT NOT NULL, source_action_id TEXT NOT NULL, source_version INTEGER NOT NULL,
 params TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
 result_action_id TEXT, created_at TEXT NOT NULL, UNIQUE(task_id,source_action_id));
CREATE TABLE IF NOT EXISTS risk_profiles(user_id TEXT PRIMARY KEY, loss_tolerance TEXT NOT NULL,
 experience TEXT NOT NULL, horizon_days INTEGER NOT NULL, risk_level INTEGER NOT NULL,
 version INTEGER NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS investment_lots(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 product_id TEXT NOT NULL, principal_fen INTEGER NOT NULL CHECK(principal_fen>=0),
 available_at TEXT NOT NULL, action_id TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, tail TEXT NOT NULL,
 status TEXT NOT NULL, credit_limit_fen INTEGER NOT NULL, used_fen INTEGER NOT NULL CHECK(used_fen>=0),
 single_limit_fen INTEGER NOT NULL, daily_limit_fen INTEGER NOT NULL, online_enabled INTEGER NOT NULL,
 version INTEGER NOT NULL, CHECK(credit_limit_fen>=used_fen));
CREATE INDEX IF NOT EXISTS entries_owner_date ON entries(user_id,at);
CREATE INDEX IF NOT EXISTS actions_owner_task ON actions(user_id,task_id);
"""

def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000).hex()

class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            if 'assessment_json' not in {r[1] for r in conn.execute('PRAGMA table_info(risk_profiles)')}:
                conn.execute("ALTER TABLE risk_profiles ADD COLUMN assessment_json TEXT")

    @contextmanager
    def connect(self, write=False):
        conn = sqlite3.connect(self.settings.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def seed(self):
        with self.connect(write=True) as conn:
            if conn.execute("SELECT count(*) FROM users").fetchone()[0]:
                for row in conn.execute("SELECT id FROM users").fetchall():
                    self.seed_user_extensions(conn,row[0])
                return
            for uid, username, name in [("alice", "alice", "林同学"), ("bob", "bob", "周同学")]:
                conn.execute("INSERT INTO users VALUES(?,?,?,?)", (uid, name, username, password_hash("demo2026", uid)))
                self.seed_user_ledger(conn,uid)

    def seed_user_ledger(self, conn, uid):
        """在现有事务内初始化单个模拟用户的账本，不改用户身份和登录会话。"""
        self.seed_user_extensions(conn,uid)
        today = date.fromisoformat(self.settings.business_date)
        end_month = today.year * 12 + today.month - 1
        conn.execute("INSERT INTO accounts VALUES(?,?)", (uid, 500000))
        for rid, rname, tail in [("li1", "李明", "1234"), ("li2", "李明", "5678"), ("wang", "王芳", "8899")]:
            conn.execute("INSERT INTO recipients VALUES(?,?,?,?)", (f"{uid}_{rid}", uid, rname, tail))
        for sid, merchant, amount in [("video", "星光视频", 2500), ("music", "悦耳音乐", 1500), ("cloud", "云盘空间", 1200)]:
            next_at = (today + timedelta(days={"video":2,"music":5,"cloud":15}[sid])).isoformat()
            conn.execute("INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?)", (f"{uid}_{sid}",uid,merchant,amount,"monthly",next_at,"active",1))
        for month_index in range(end_month - 6, end_month):
            year, month0 = divmod(month_index, 12)
            month = month0 + 1
            items = [(2,"in",800000,"收入","模拟工资","工资收入",None),
                     (5,"out",240000,"住房","房租收款","每月房租，周期付款不是可取消订阅",None),
                     (6,"out",2500,"订阅","星光视频","视频会员",None),
                     (9,"out",1500,"订阅","悦耳音乐","音乐会员",None),
                     (18,"out",1200,"订阅","云盘空间","云盘会员",None),
                     (20,"out",5900,"通讯","手机话费","周期支付，未在可取消订阅目录登记",None),
                     (11,"out",20000,"转账","李明","餐费分摊",f"{uid}_li1"),
                     (24,"out",30000,"转账","李明","上月约定还款",f"{uid}_li1")]
            items += [(day,"out",1800+day*50,"餐饮","食堂" if day%2 else "社区餐厅","日常餐饮",None) for day in (3,7,10,13,16,19,22,25,27)]
            items += [(14,"out",12800,"购物","生活超市","生活用品",None),(23,"out",4500,"交通","城市出行","交通支出",None)]
            if month_index == end_month - 1:
                items += [(26,"out",19900,"购物","数码商店","同日同商户同金额候选",None),
                          (26,"out",19900,"购物","数码商店","可能重复，需核对",None),
                          (28,"out",180000,"购物","家具商场","大额支出候选",None)]
            for n, (day,direction,amount,category,merchant,note,rid) in enumerate(items):
                at = f"{year:04d}-{month:02d}-{day:02d}T12:00:00+08:00"
                conn.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)", (f"seed_{uid}_{month_index}_{n}",uid,at,direction,amount,category,merchant,note,rid,None))
                conn.execute("UPDATE accounts SET balance_fen=balance_fen+? WHERE user_id=?", (amount if direction=="in" else -amount,uid))

    def seed_user_extensions(self, conn, uid):
        """增量初始化；不覆盖现有卡片状态或账户资金。"""
        conn.execute("INSERT OR IGNORE INTO cards VALUES(?,?,?,?,?,?,?,?,?,?)",(f"{uid}_card",uid,"6688" if uid=="alice" else "7788","active",500000,0,100000,200000,1,1))
