import sqlite3
import os
import json
import logging
from datetime import datetime
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.key')

# Setup encryption key
if not os.path.exists(KEY_PATH):
    key = Fernet.generate_key()
    with open(KEY_PATH, 'wb') as key_file:
        key_file.write(key)
else:
    with open(KEY_PATH, 'rb') as key_file:
        key = key_file.read()

cipher_suite = Fernet(key)

def encrypt_password(password: str) -> str:
    if not password:
        return ""
    return cipher_suite.encrypt(password.encode()).decode()

def decrypt_password(encrypted_pw: str) -> str:
    if not encrypted_pw:
        return ""
    try:
        return cipher_suite.decrypt(encrypted_pw.encode()).decode()
    except Exception:
        return ""

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status INTEGER DEFAULT 1
    )
    ''')
    
    # Check if 'status' column exists in existing users table
    cursor.execute("PRAGMA table_info(users)")
    users_cols = [col['name'] for col in cursor.fetchall()]
    if 'status' not in users_cols:
        logger.info("Migrating database: Adding 'status' column to users table...")
        cursor.execute("ALTER TABLE users ADD COLUMN status INTEGER DEFAULT 1")
        conn.commit()
    
    # Check if we need to migrate from single-account settings (with UNIQUE(user_id)) to multi-account
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_log'")
    activity_log_exists = cursor.fetchone()
    
    needs_migration = False
    if activity_log_exists:
        cursor.execute("PRAGMA table_info(activity_log)")
        activity_log_cols = [col['name'] for col in cursor.fetchall()]
        # If 'account_id' is missing in existing log table, we must migrate
        if 'account_id' not in activity_log_cols:
            needs_migration = True

    if needs_migration:
        logger.info("Migrating SQLite tables to drop settings.user_id UNIQUE constraint and set up account_id routing...")
        
        # Step 1: Backup/Rename old tables
        cursor.execute("ALTER TABLE settings RENAME TO settings_old")
        cursor.execute("ALTER TABLE keyword_triggers RENAME TO keyword_triggers_old")
        cursor.execute("ALTER TABLE reply_history RENAME TO reply_history_old")
        cursor.execute("ALTER TABLE activity_log RENAME TO activity_log_old")
        
        # Step 2: Create new settings table (without UNIQUE constraint on user_id)
        cursor.execute('''
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bot_enabled INTEGER DEFAULT 0,
            offline_start TEXT DEFAULT '22:00',
            offline_end TEXT DEFAULT '08:00',
            offline_message TEXT DEFAULT 'Hello! I am currently offline and will get back to you soon.',
            check_interval_seconds INTEGER DEFAULT 300,
            timezone TEXT DEFAULT 'Asia/Dhaka',
            username TEXT DEFAULT NULL,
            encrypted_password TEXT DEFAULT '',
            session_settings TEXT DEFAULT '',
            proxy TEXT DEFAULT '',
            cooldown_hours INTEGER DEFAULT 12,
            seen_control TEXT DEFAULT 'seen',
            telegram_chat_id TEXT DEFAULT NULL,
            telegram_link_token TEXT DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        ''')
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_settings_user_username ON settings(user_id, username)")
        
        # Copy settings rows (their IDs will be kept the same, ensuring relationship mappings)
        cursor.execute('''
        INSERT INTO settings (
            id, user_id, bot_enabled, offline_start, offline_end, offline_message,
            check_interval_seconds, timezone, username, encrypted_password,
            session_settings, proxy, cooldown_hours, seen_control,
            telegram_chat_id, telegram_link_token
        )
        SELECT 
            id, user_id, bot_enabled, offline_start, offline_end, offline_message,
            check_interval_seconds, timezone, 
            (CASE WHEN username = '' THEN NULL ELSE username END), 
            encrypted_password, session_settings, proxy, cooldown_hours, seen_control,
            telegram_chat_id, telegram_link_token
        FROM settings_old
        ''')
        
        # Create other new tables referencing account_id
        cursor.execute('''
        CREATE TABLE keyword_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            reply_message TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES settings(id) ON DELETE CASCADE
        )
        ''')
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_keyword ON keyword_triggers(account_id, keyword)")
        
        cursor.execute('''
        CREATE TABLE reply_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            thread_id TEXT NOT NULL,
            last_replied_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES settings(id) ON DELETE CASCADE
        )
        ''')
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_thread ON reply_history(account_id, thread_id)")
        
        cursor.execute('''
        CREATE TABLE activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            username TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES settings(id) ON DELETE CASCADE
        )
        ''')
        
        # Step 3: Populate new tables mapping old user_id rows to settings.id
        # Historically, each user had exactly one settings row (id matches settings.id where settings.user_id = old.user_id)
        cursor.execute('''
        INSERT INTO keyword_triggers (account_id, keyword, reply_message)
        SELECT s.id, kt.keyword, kt.reply_message
        FROM keyword_triggers_old kt
        JOIN settings s ON s.user_id = kt.user_id
        ''')
        
        cursor.execute('''
        INSERT INTO reply_history (account_id, thread_id, last_replied_at)
        SELECT s.id, rh.thread_id, rh.last_replied_at
        FROM reply_history_old rh
        JOIN settings s ON s.user_id = rh.user_id
        ''')
        
        cursor.execute('''
        INSERT INTO activity_log (account_id, timestamp, username, thread_id, message)
        SELECT s.id, al.timestamp, al.username, al.thread_id, al.message
        FROM activity_log_old al
        JOIN settings s ON s.user_id = al.user_id
        ''')
        
        # Step 4: Drop old tables
        cursor.execute("DROP TABLE settings_old")
        cursor.execute("DROP TABLE keyword_triggers_old")
        cursor.execute("DROP TABLE reply_history_old")
        cursor.execute("DROP TABLE activity_log_old")
        
        logger.info("Database migration finished successfully.")
    else:
        # Create standard multi-account structures if starting fresh
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bot_enabled INTEGER DEFAULT 0,
            offline_start TEXT DEFAULT '22:00',
            offline_end TEXT DEFAULT '08:00',
            offline_message TEXT DEFAULT 'Hello! I am currently offline and will get back to you soon.',
            check_interval_seconds INTEGER DEFAULT 300,
            timezone TEXT DEFAULT 'Asia/Dhaka',
            username TEXT DEFAULT NULL,
            encrypted_password TEXT DEFAULT '',
            session_settings TEXT DEFAULT '',
            proxy TEXT DEFAULT '',
            cooldown_hours INTEGER DEFAULT 12,
            seen_control TEXT DEFAULT 'seen',
            telegram_chat_id TEXT DEFAULT NULL,
            telegram_link_token TEXT DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        ''')
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_settings_user_username ON settings(user_id, username)")
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS keyword_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            reply_message TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES settings(id) ON DELETE CASCADE
        )
        ''')
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_keyword ON keyword_triggers(account_id, keyword)")
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS reply_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            thread_id TEXT NOT NULL,
            last_replied_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES settings(id) ON DELETE CASCADE
        )
        ''')
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_thread ON reply_history(account_id, thread_id)")
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            username TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES settings(id) ON DELETE CASCADE
        )
        ''')

    conn.commit()
    conn.close()

# ----------------- User Auth Helpers -----------------

def register_user(username: str, password: str) -> tuple[bool, str]:
    username = username.strip().lower()
    if not username or not password:
        return False, "Username and password are required."
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check if username exists
        row = cursor.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone()
        if row:
            return False, "Username is already taken."
        
        pw_hash = generate_password_hash(password)
        now_str = datetime.now().isoformat()
        cursor.execute(
            'INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)',
            (username, pw_hash, now_str)
        )
        user_id = cursor.lastrowid
        conn.commit()
        
        # Automatically create their first (empty) settings connection
        add_instagram_account(user_id)
        
        return True, "User registered successfully."
    except Exception as e:
        return False, f"Registration failed: {str(e)}"
    finally:
        conn.close()

def authenticate_user(username: str, password: str) -> tuple[bool, int, str]:
    username = username.strip().lower()
    conn = get_db_connection()
    row = conn.execute('SELECT id, password_hash FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    
    if not row:
        return False, -1, "Invalid username or password."
    
    if check_password_hash(row['password_hash'], password):
        return True, row['id'], "Login successful."
    return False, -1, "Invalid username or password."

# ----------------- Multi-Account Helpers -----------------

def add_instagram_account(user_id: int, username: str = None) -> int:
    import secrets
    link_token = secrets.token_hex(16)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO settings (user_id, username, telegram_link_token) VALUES (?, ?, ?)',
            (user_id, username, link_token)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def get_user_accounts(user_id: int) -> list:
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM settings WHERE user_id = ? ORDER BY id ASC', (user_id,)).fetchall()
    conn.close()
    
    results = []
    for r in rows:
        d = dict(r)
        d['password'] = decrypt_password(d['encrypted_password'])
        results.append(d)
    return results

def delete_instagram_account(account_id: int, user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM settings WHERE id = ? AND user_id = ?', (account_id, user_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

# ----------------- Settings Helpers (Account Scoped) -----------------

def get_settings(account_id: int):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM settings WHERE id = ?', (account_id,)).fetchone()
    
    if not row:
        conn.close()
        return {}
        
    data = dict(row)
    # Check if telegram_link_token exists, if not generate and update
    if not data.get('telegram_link_token'):
        import secrets
        link_token = secrets.token_hex(16)
        conn.execute('UPDATE settings SET telegram_link_token = ? WHERE id = ?', (link_token, account_id))
        conn.commit()
        row = conn.execute('SELECT * FROM settings WHERE id = ?', (account_id,)).fetchone()
        data = dict(row)
        
    conn.close()
    data['password'] = decrypt_password(data['encrypted_password'])
    return data

def update_settings(account_id: int, updates: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    fields = []
    values = []
    
    for key, value in updates.items():
        if key == 'password':
            fields.append('encrypted_password = ?')
            values.append(encrypt_password(value))
        elif key in [
            'bot_enabled', 'offline_start', 'offline_end', 'offline_message', 
            'check_interval_seconds', 'timezone', 'username', 'session_settings', 
            'proxy', 'cooldown_hours', 'seen_control', 'telegram_chat_id'
        ]:
            fields.append(f'{key} = ?')
            values.append(value)
            
    if fields:
        values.append(account_id)
        query = f"UPDATE settings SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(query, values)
        conn.commit()
    
    conn.close()

# ----------------- Activity Logs (Account Scoped) -----------------

def log_activity(account_id: int, username: str, thread_id: str, message: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    now_str = datetime.now().isoformat()
    
    cursor.execute(
        'INSERT INTO activity_log (account_id, timestamp, username, thread_id, message) VALUES (?, ?, ?, ?, ?)',
        (account_id, now_str, username, thread_id, message)
    )
    
    # Update reply history
    cursor.execute(
        'INSERT OR REPLACE INTO reply_history (account_id, thread_id, last_replied_at) VALUES (?, ?, ?)',
        (account_id, thread_id, now_str)
    )
    
    conn.commit()
    conn.close()

def should_reply(account_id: int, thread_id: str, cooldown_hours: int = 12) -> bool:
    conn = get_db_connection()
    row = conn.execute('SELECT last_replied_at FROM reply_history WHERE account_id = ? AND thread_id = ?', (account_id, thread_id)).fetchone()
    conn.close()
    
    if not row:
        return True
        
    last_replied = datetime.fromisoformat(row['last_replied_at'])
    diff = datetime.now() - last_replied
    return (diff.total_seconds() / 3600.0) >= cooldown_hours

def get_logs(account_id: int, limit: int = 50):
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM activity_log WHERE account_id = ? ORDER BY id DESC LIMIT ?', (account_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_reply_history(account_id: int):
    conn = get_db_connection()
    conn.execute('DELETE FROM reply_history WHERE account_id = ?', (account_id,))
    conn.commit()
    conn.close()

# ----------------- Keyword Triggers Helpers (Account Scoped) -----------------

def add_keyword_trigger(account_id: int, keyword: str, reply_message: str) -> bool:
    kw = keyword.strip().lower()
    msg = reply_message.strip()
    if not kw or not msg:
        return False
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT OR REPLACE INTO keyword_triggers (account_id, keyword, reply_message) VALUES (?, ?, ?)',
            (account_id, kw, msg)
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def delete_keyword_trigger(account_id: int, trigger_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM keyword_triggers WHERE id = ? AND account_id = ?', (trigger_id, account_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()

def delete_keyword_trigger_by_keyword(account_id: int, keyword: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM keyword_triggers WHERE account_id = ? AND keyword = ?', (account_id, keyword.strip().lower()))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()

def get_keyword_triggers(account_id: int):
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM keyword_triggers WHERE account_id = ? ORDER BY keyword ASC', (account_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ----------------- Telegram Helpers -----------------

def get_user_by_telegram(telegram_chat_id: str) -> int:
    conn = get_db_connection()
    row = conn.execute('SELECT user_id FROM settings WHERE telegram_chat_id = ? LIMIT 1', (str(telegram_chat_id),)).fetchone()
    conn.close()
    if row:
        return row['user_id']
    return None

def get_accounts_by_telegram(telegram_chat_id: str) -> list:
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM settings WHERE telegram_chat_id = ?', (str(telegram_chat_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_user_by_telegram_token(token: str) -> int:
    conn = get_db_connection()
    row = conn.execute('SELECT user_id FROM settings WHERE telegram_link_token = ?', (token,)).fetchone()
    conn.close()
    if row:
        return row['user_id']
    return None

def get_account_by_telegram_token(token: str):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM settings WHERE telegram_link_token = ?', (token,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_enabled_accounts() -> list:
    conn = get_db_connection()
    rows = conn.execute('SELECT id FROM settings WHERE bot_enabled = 1 AND username IS NOT NULL AND username != ""').fetchall()
    conn.close()
    return [row['id'] for row in rows]

def get_system_stats():
    conn = get_db_connection()
    try:
        total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        active_bots = conn.execute('SELECT COUNT(*) FROM settings WHERE bot_enabled = 1 AND username IS NOT NULL AND username != ""').fetchone()[0]
        total_replies = conn.execute('SELECT COUNT(*) FROM activity_log').fetchone()[0]
    except Exception:
        total_users = 0
        active_bots = 0
        total_replies = 0
    finally:
        conn.close()
    return {
        "total_users": total_users,
        "active_bots": active_bots,
        "total_replies": total_replies
    }

def set_user_status(user_id: int, status: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('UPDATE users SET status = ? WHERE id = ?', (status, user_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()

def get_user_status(user_id: int) -> int:
    conn = get_db_connection()
    row = conn.execute('SELECT status FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if row:
        return row['status']
    return 1 # Default to Active

def get_all_linked_telegram_chats() -> list:
    conn = get_db_connection()
    rows = conn.execute('SELECT DISTINCT telegram_chat_id FROM settings WHERE telegram_chat_id IS NOT NULL AND telegram_chat_id != ""').fetchall()
    conn.close()
    return [row['telegram_chat_id'] for row in rows]

def prune_logs(days_threshold: int) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM activity_log WHERE datetime(timestamp) < datetime('now', '-' || ? || ' days')",
            (str(days_threshold),)
        )
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count
    except Exception as e:
        logger.error(f"Error pruning logs: {e}")
        return 0
    finally:
        conn.close()

# Initialize tables
init_db()
