import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import database
from bot_manager import bot_manager_instance
from telegram_manager import start_telegram_polling, bot_username

app = Flask(__name__)
# Generate a cryptographically secure 256-character secret key and save it locally
SESSION_KEY_PATH = os.path.join(app.root_path, '.session_key')
if os.path.exists(SESSION_KEY_PATH):
    try:
        with open(SESSION_KEY_PATH, 'r') as f:
            secret_key = f.read().strip()
    except Exception:
        import secrets
        secret_key = secrets.token_hex(128)
else:
    import secrets
    secret_key = secrets.token_hex(128)
    try:
        with open(SESSION_KEY_PATH, 'w') as f:
            f.write(secret_key)
    except Exception:
        pass

app.secret_key = os.environ.get("FLASK_SECRET_KEY", secret_key)

# Create template and static folders if they don't exist
os.makedirs(os.path.join(app.root_path, 'templates'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)

def get_current_user_id():
    return session.get('user_id')

def get_request_account_id():
    # Helper to retrieve account_id from JSON, query args, or form
    account_id = request.args.get('account_id', type=int)
    if not account_id and request.is_json:
        account_id = (request.json or {}).get('account_id')
    if not account_id:
        try:
            account_id = request.form.get('account_id', type=int)
        except Exception:
            pass
    return account_id

def verify_account_ownership(account_id: int):
    user_id = get_current_user_id()
    if not user_id:
        return False, "Unauthorized", 401
    
    # Check if suspended
    if database.get_user_status(user_id) == 0:
        session.clear()
        return False, "Your account has been suspended by the administrator.", 403
        
    # Super admin (user_id == 1) has access to all accounts
    if user_id == 1:
        return True, None, 200
        
    # Standard user check
    conn = database.get_db_connection()
    row = conn.execute('SELECT 1 FROM settings WHERE id = ? AND user_id = ?', (account_id, user_id)).fetchone()
    conn.close()
    if not row:
        return False, "Forbidden: You do not own this account", 403
        
    return True, None, 200

@app.route('/')
def index():
    return render_template('index.html')

# ----------------- User Auth Routes -----------------

@app.route('/api/register', methods=['POST'])
def api_register_user():
    data = request.json or {}
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400
    success, msg = database.register_user(username, password)
    return jsonify({"success": success, "message": msg})

@app.route('/api/login_user', methods=['POST'])
def api_login_user():
    data = request.json or {}
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400
    success, user_id, msg = database.authenticate_user(username, password)
    if success:
        if database.get_user_status(user_id) == 0:
            return jsonify({"success": False, "message": "Your account has been suspended by the administrator."}), 403
        session['user_id'] = user_id
        session['username'] = username
    return jsonify({"success": success, "message": msg})

@app.route('/api/logout_user', methods=['POST'])
def api_logout_user():
    session.clear()
    return jsonify({"success": True, "message": "Logged out from website successfully."})

@app.route('/api/me', methods=['GET'])
def api_me():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"logged_in": False})
    if database.get_user_status(user_id) == 0:
        session.clear()
        return jsonify({"logged_in": False, "message": "Account suspended."})
    return jsonify({
        "logged_in": True,
        "user_id": user_id,
        "username": session.get('username')
    })

# ----------------- Instagram Accounts Management APIs -----------------

@app.route('/api/accounts', methods=['GET'])
def api_get_accounts():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if database.get_user_status(user_id) == 0:
        session.clear()
        return jsonify({"success": False, "message": "Your account has been suspended by the administrator."}), 403
    accounts = database.get_user_accounts(user_id)
    result = []
    for acc in accounts:
        result.append({
            "id": acc["id"],
            "username": acc["username"] or "",
            "bot_enabled": acc["bot_enabled"],
            "telegram_chat_id": acc["telegram_chat_id"]
        })
    return jsonify({"success": True, "accounts": result})

@app.route('/api/accounts/add', methods=['POST'])
def api_add_account():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if database.get_user_status(user_id) == 0:
        session.clear()
        return jsonify({"success": False, "message": "Your account has been suspended by the administrator."}), 403
    account_id = database.add_instagram_account(user_id)
    return jsonify({"success": True, "account_id": account_id, "message": "New Instagram account slot created."})

@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def api_delete_account(account_id):
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if database.get_user_status(user_id) == 0:
        session.clear()
        return jsonify({"success": False, "message": "Your account has been suspended by the administrator."}), 403
    
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    # Stop bot instance if running
    bot_manager_instance.stop_bot(account_id)
    
    deleted = database.delete_instagram_account(account_id, user_id)
    if deleted:
        return jsonify({"success": True, "message": "Instagram account connection removed successfully."})
    return jsonify({"success": False, "message": "Account not found."}), 404

# ----------------- Instagram Control & Scoped APIs -----------------

@app.route('/api/status', methods=['GET'])
def get_status():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
    
    settings = database.get_settings(account_id)
    check_interval = settings.get("check_interval_seconds", 300)

    safe_settings = {
        "bot_enabled": settings.get("bot_enabled", 0),
        "offline_start": settings.get("offline_start", "22:00"),
        "offline_end": settings.get("offline_end", "08:00"),
        "offline_message": settings.get("offline_message", ""),
        "check_interval": check_interval,
        "timezone": settings.get("timezone", "Asia/Dhaka"),
        "username": settings.get("username", ""),
        "proxy": settings.get("proxy", ""),
        "cooldown_hours": settings.get("cooldown_hours", 12),
        "seen_control": settings.get("seen_control", "seen"),
        "telegram_chat_id": settings.get("telegram_chat_id"),
        "telegram_link_token": settings.get("telegram_link_token")
    }
    
    bot_instance = bot_manager_instance.get_bot(account_id)
    bot_status = bot_instance.get_status()
    bot_status["telegram_bot_username"] = bot_username
    return jsonify({
        "settings": safe_settings,
        "bot": bot_status
    })

@app.route('/api/login', methods=['POST'])
def api_login():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    data = request.json or {}
    username = data.get('username')
    password = data.get('password')
    verification_code = data.get('verification_code')
    session_id = data.get('session_id')
    
    bot_instance = bot_manager_instance.get_bot(account_id)
    
    # Session ID login path
    if session_id:
        success, message = bot_instance.login_by_sessionid(session_id)
        if success:
            settings = database.get_settings(account_id)
            if settings.get('bot_enabled'):
                bot_manager_instance.start_bot(account_id)
        return jsonify({
            "success": success,
            "message": message,
            "needs_2fa": False
        })
        
    # Standard credentials login path
    if not username:
        return jsonify({"success": False, "message": "Username is required"}), 400
        
    # If standard login but password is empty, and we don't have stored pw
    if not password and not verification_code:
        return jsonify({"success": False, "message": "Password is required"}), 400
        
    # If this is a 2FA verification code submission, use stored username/password
    if verification_code and bot_instance.login_credentials:
        username, password = bot_instance.login_credentials
    elif not password:
        # Fallback to DB stored password if verification_code was sent but login_credentials cleared
        settings = database.get_settings(account_id)
        if settings.get('username') == username:
            password = settings.get('password')
            
    success, message = bot_instance.login(username, password, verification_code=verification_code)
    
    if success:
        # If logged in successfully, start bot automatically if enabled
        settings = database.get_settings(account_id)
        if settings.get('bot_enabled'):
            bot_manager_instance.start_bot(account_id)
            
    return jsonify({
        "success": success,
        "message": message,
        "needs_2fa": bot_instance.two_factor_info is not None
    })

@app.route('/api/logout', methods=['POST'])
def api_logout():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    bot_instance = bot_manager_instance.get_bot(account_id)
    bot_instance.logout()
    return jsonify({"success": True, "message": "Logged out successfully."})

@app.route('/api/settings', methods=['POST'])
def api_settings():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    data = request.json or {}
    
    updates = {}
    if 'bot_enabled' in data:
        updates['bot_enabled'] = 1 if data['bot_enabled'] else 0
    if 'offline_start' in data:
        updates['offline_start'] = data['offline_start']
    if 'offline_end' in data:
        updates['offline_end'] = data['offline_end']
    if 'offline_message' in data:
        updates['offline_message'] = data['offline_message']
    if 'check_interval' in data:
        try:
            updates['check_interval_seconds'] = int(data['check_interval'])
        except ValueError:
            pass
    if 'timezone' in data:
        updates['timezone'] = data['timezone']
    if 'proxy' in data:
        updates['proxy'] = data['proxy']
        # Apply proxy to running client immediately
        bot_instance = bot_manager_instance.get_bot(account_id)
        if bot_instance.cl:
            try:
                bot_instance.cl.set_proxy(data['proxy'])
            except Exception:
                pass
    if 'cooldown_hours' in data:
        try:
            updates['cooldown_hours'] = int(data['cooldown_hours'])
        except ValueError:
            pass
    if 'seen_control' in data:
        updates['seen_control'] = data['seen_control']
        
    database.update_settings(account_id, updates)
    
    # Apply status change immediately
    settings = database.get_settings(account_id)
    if settings.get('bot_enabled'):
        bot_manager_instance.stop_bot(account_id)
        bot_manager_instance.start_bot(account_id)
    else:
        bot_manager_instance.stop_bot(account_id)
        
    return jsonify({"success": True, "message": "Settings updated successfully."})

@app.route('/api/check_now', methods=['POST'])
def api_check_now():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    settings = database.get_settings(account_id)
    if not settings.get('username'):
        return jsonify({"success": False, "message": "Bot is not logged in."}), 400
        
    # Run in thread so it doesn't block Flask request
    import threading
    bot_instance = bot_manager_instance.get_bot(account_id)
    threading.Thread(target=bot_instance.check_and_reply, daemon=True).start()
    return jsonify({"success": True, "message": "Manual DM check triggered in background."})

@app.route('/api/logs', methods=['GET'])
def api_logs():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    logs = database.get_logs(account_id, limit=50)
    return jsonify({"logs": logs})

# ----------------- Keyword Triggers Scoped APIs -----------------

@app.route('/api/triggers', methods=['GET'])
def api_triggers():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    triggers = database.get_keyword_triggers(account_id)
    return jsonify({"success": True, "triggers": triggers})

@app.route('/api/triggers/add', methods=['POST'])
def api_add_trigger():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    data = request.json or {}
    keyword = data.get('keyword')
    reply_message = data.get('reply_message')
    if not keyword or not reply_message:
        return jsonify({"success": False, "message": "Keyword and Reply Message are required."}), 400
    success = database.add_keyword_trigger(account_id, keyword, reply_message)
    if success:
        return jsonify({"success": True, "message": "Keyword trigger added successfully."})
    return jsonify({"success": False, "message": "Failed to add trigger (maybe duplicate keyword)."}), 400

@app.route('/api/triggers/delete', methods=['POST'])
def api_delete_trigger():
    account_id = get_request_account_id()
    if not account_id:
        return jsonify({"success": False, "message": "account_id is required"}), 400
        
    success, err, code = verify_account_ownership(account_id)
    if not success:
        return jsonify({"success": False, "message": err}), code
        
    data = request.json or {}
    trigger_id = data.get('id')
    if not trigger_id:
        return jsonify({"success": False, "message": "Trigger ID is required."}), 400
    success = database.delete_keyword_trigger(account_id, trigger_id)
    if success:
        return jsonify({"success": True, "message": "Trigger deleted successfully."})
    return jsonify({"success": False, "message": "Trigger not found or unauthorized."}), 404

# ----------------- Super Admin Web APIs & Pages (User ID 1 only) -----------------

@app.route('/admin')
def admin_page():
    user_id = get_current_user_id()
    if user_id != 1:
        return redirect(url_for('index'))
    return render_template('admin.html')

@app.route('/api/admin/stats', methods=['GET'])
def api_admin_stats():
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
    stats = database.get_system_stats()
    return jsonify({"success": True, "stats": stats})

@app.route('/api/admin/users', methods=['GET'])
def api_admin_users():
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    conn = database.get_db_connection()
    # Query all users and join with settings to get individual connections
    rows = conn.execute('''
        SELECT 
            s.id AS account_id,
            u.id AS user_id,
            u.username AS web_username,
            u.created_at AS web_created_at,
            u.status AS web_status,
            s.username AS insta_username,
            s.bot_enabled,
            s.check_interval_seconds,
            s.telegram_chat_id
        FROM users u
        LEFT JOIN settings s ON u.id = s.user_id
    ''').fetchall()
    conn.close()
    
    users_list = []
    for row in rows:
        acc_id = row['account_id']
        if not acc_id:
            continue
            
        bot_instance = bot_manager_instance.get_bot(acc_id)
        bot_status = bot_instance.get_status()
        
        users_list.append({
            "id": acc_id,  # Admin UI treats ID as target
            "username": f"{row['web_username']} [Account ID: {acc_id}]",
            "created_at": row['web_created_at'],
            "web_status": row['web_status'],
            "insta_username": row['insta_username'] or "",
            "bot_enabled": row['bot_enabled'] or 0,
            "check_interval": row['check_interval_seconds'] or 300,
            "telegram_chat_id": row['telegram_chat_id'],
            "bot_status": bot_status["status"]
        })
        
    return jsonify({"success": True, "users": users_list})

@app.route('/api/admin/users/<int:target_id>/settings', methods=['GET'])
def api_admin_get_settings(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    settings = database.get_settings(target_id)
    check_interval = settings.get("check_interval_seconds", 300)
    safe_settings = {
        "bot_enabled": settings.get("bot_enabled", 0),
        "offline_start": settings.get("offline_start", "22:00"),
        "offline_end": settings.get("offline_end", "08:00"),
        "offline_message": settings.get("offline_message", ""),
        "check_interval": check_interval,
        "timezone": settings.get("timezone", "Asia/Dhaka"),
        "username": settings.get("username", ""),
        "proxy": settings.get("proxy", ""),
        "cooldown_hours": settings.get("cooldown_hours", 12),
        "seen_control": settings.get("seen_control", "seen"),
        "telegram_chat_id": settings.get("telegram_chat_id"),
        "telegram_link_token": settings.get("telegram_link_token")
    }
    bot_instance = bot_manager_instance.get_bot(target_id)
    bot_status = bot_instance.get_status()
    web_status = database.get_user_status(settings.get('user_id')) if settings.get('user_id') else 1
    return jsonify({
        "success": True,
        "settings": safe_settings,
        "bot": bot_status,
        "web_status": web_status
    })

@app.route('/api/admin/users/<int:target_id>/settings', methods=['POST'])
def api_admin_save_settings(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    data = request.json or {}
    updates = {}
    if 'bot_enabled' in data:
        updates['bot_enabled'] = 1 if data['bot_enabled'] else 0
    if 'offline_start' in data:
        updates['offline_start'] = data['offline_start']
    if 'offline_end' in data:
        updates['offline_end'] = data['offline_end']
    if 'offline_message' in data:
        updates['offline_message'] = data['offline_message']
    if 'check_interval' in data:
        try:
            updates['check_interval_seconds'] = int(data['check_interval'])
        except ValueError:
            pass
    if 'timezone' in data:
        updates['timezone'] = data['timezone']
    if 'proxy' in data:
        updates['proxy'] = data['proxy']
        # Apply proxy to running client immediately
        bot_instance = bot_manager_instance.get_bot(target_id)
        if bot_instance.cl:
            try:
                bot_instance.cl.set_proxy(data['proxy'])
            except Exception:
                pass
    if 'cooldown_hours' in data:
        try:
            updates['cooldown_hours'] = int(data['cooldown_hours'])
        except ValueError:
            pass
    if 'seen_control' in data:
        updates['seen_control'] = data['seen_control']
        
    database.update_settings(target_id, updates)
    
    # Apply status change immediately
    settings = database.get_settings(target_id)
    if settings.get('bot_enabled'):
        bot_manager_instance.stop_bot(target_id)
        bot_manager_instance.start_bot(target_id)
    else:
        bot_manager_instance.stop_bot(target_id)
        
    return jsonify({"success": True, "message": f"Settings updated successfully for Account ID {target_id}."})

@app.route('/api/admin/users/<int:target_id>/triggers', methods=['GET'])
def api_admin_get_triggers(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
    triggers = database.get_keyword_triggers(target_id)
    return jsonify({"success": True, "triggers": triggers})

@app.route('/api/admin/users/<int:target_id>/triggers/add', methods=['POST'])
def api_admin_add_trigger(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
    data = request.json or {}
    keyword = data.get('keyword')
    reply_message = data.get('reply_message')
    if not keyword or not reply_message:
        return jsonify({"success": False, "message": "Keyword and Reply Message are required."}), 400
    success = database.add_keyword_trigger(target_id, keyword, reply_message)
    if success:
        return jsonify({"success": True, "message": "Keyword trigger added successfully."})
    return jsonify({"success": False, "message": "Failed to add trigger."}), 400

@app.route('/api/admin/users/<int:target_id>/triggers/delete', methods=['POST'])
def api_admin_delete_trigger(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
    data = request.json or {}
    trigger_id = data.get('id')
    if not trigger_id:
        return jsonify({"success": False, "message": "Trigger ID is required."}), 400
    success = database.delete_keyword_trigger(target_id, trigger_id)
    if success:
        return jsonify({"success": True, "message": "Trigger deleted successfully."})
    return jsonify({"success": False, "message": "Trigger not found or unauthorized."}), 404

@app.route('/api/admin/users/<int:target_id>/toggle', methods=['POST'])
def api_admin_toggle_user(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    settings = database.get_settings(target_id)
    is_enabled = not settings.get('bot_enabled')
    database.update_settings(target_id, {"bot_enabled": 1 if is_enabled else 0})
    
    if is_enabled:
        bot_manager_instance.start_bot(target_id)
    else:
        bot_manager_instance.stop_bot(target_id)
        
    return jsonify({"success": True, "message": f"Bot status updated for Account ID {target_id}."})

@app.route('/api/admin/users/<int:target_id>/disconnect', methods=['POST'])
def api_admin_disconnect_user(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    bot_instance = bot_manager_instance.get_bot(target_id)
    bot_instance.logout()
    return jsonify({"success": True, "message": f"Instagram disconnected for Account ID {target_id}."})

@app.route('/api/admin/users/<int:target_id>/clear_history', methods=['POST'])
def api_admin_clear_history(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
    database.clear_reply_history(target_id)
    return jsonify({"success": True, "message": f"Reply cooldown history cleared for Account ID {target_id}."})

@app.route('/api/admin/users/<int:target_id>/login_instagram', methods=['POST'])
def api_admin_login_instagram(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    data = request.json or {}
    username = data.get('username')
    password = data.get('password')
    verification_code = data.get('verification_code')
    session_id = data.get('session_id')
    
    bot_instance = bot_manager_instance.get_bot(target_id)
    
    # Session ID login path
    if session_id:
        success, message = bot_instance.login_by_sessionid(session_id)
        if success:
            settings = database.get_settings(target_id)
            if settings.get('bot_enabled'):
                bot_manager_instance.start_bot(target_id)
        return jsonify({
            "success": success,
            "message": message,
            "needs_2fa": False
        })
        
    # Standard credentials login path
    if not username:
        return jsonify({"success": False, "message": "Username is required"}), 400
    if not password and not verification_code:
        return jsonify({"success": False, "message": "Password is required"}), 400
        
    if verification_code and bot_instance.login_credentials:
        username, password = bot_instance.login_credentials
        
    success, message = bot_instance.login(username, password, verification_code=verification_code)
    
    if success:
        settings = database.get_settings(target_id)
        if settings.get('bot_enabled'):
            bot_manager_instance.start_bot(target_id)
            
    return jsonify({
        "success": success,
        "message": message,
        "needs_2fa": bot_instance.two_factor_info is not None
    })

@app.route('/api/admin/users/<int:target_id>/logs', methods=['GET'])
def api_admin_user_logs(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    logs = database.get_logs(target_id, limit=30)
    return jsonify({"success": True, "logs": logs})

@app.route('/api/admin/users/<int:target_id>/status', methods=['POST'])
def api_admin_user_status_toggle(target_id):
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    data = request.json or {}
    status = data.get('status') # 1 for Active, 0 for Suspended
    if status not in [0, 1]:
        return jsonify({"success": False, "message": "Invalid status value."}), 400
        
    settings = database.get_settings(target_id)
    target_user_id = settings.get('user_id')
    if not target_user_id:
        return jsonify({"success": False, "message": "Account not found."}), 404
        
    if target_user_id == 1:
        return jsonify({"success": False, "message": "Cannot suspend the Super Admin account."}), 400
        
    success = database.set_user_status(target_user_id, status)
    if success:
        action_name = "suspended" if status == 0 else "reactivated"
        if status == 0:
            accounts = database.get_user_accounts(target_user_id)
            for acc in accounts:
                bot_manager_instance.stop_bot(acc['id'])
        return jsonify({"success": True, "message": f"User account has been successfully {action_name}."})
    return jsonify({"success": False, "message": "Failed to update user status."}), 500

@app.route('/api/admin/broadcast', methods=['POST'])
def api_admin_broadcast():
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    data = request.json or {}
    message = data.get('message', '').strip()
    if not message:
        return jsonify({"success": False, "message": "Broadcast message cannot be empty."}), 400
        
    chats = database.get_all_linked_telegram_chats()
    if not chats:
        return jsonify({"success": True, "message": "No users have linked Telegram chats.", "sent_count": 0})
        
    sent_count = 0
    from telegram_manager import bot
    if bot:
        for chat_id in chats:
            try:
                formatted_msg = f"📢 *System Announcement*:\n\n{message}"
                bot.send_message(chat_id, formatted_msg, parse_mode="Markdown")
                sent_count += 1
            except Exception as e:
                app.logger.error(f"Failed to send broadcast to chat {chat_id}: {e}")
                
    return jsonify({"success": True, "message": f"Broadcast sent successfully to {sent_count} chats.", "sent_count": sent_count})

@app.route('/api/admin/maintenance/clean_logs', methods=['POST'])
def api_admin_clean_logs():
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    data = request.json or {}
    days = data.get('days')
    if days is None:
        return jsonify({"success": False, "message": "days parameter is required."}), 400
        
    try:
        days = int(days)
    except ValueError:
        return jsonify({"success": False, "message": "days must be an integer."}), 400
        
    deleted = database.prune_logs(days)
    
    # Optionally vacuum database to reclaim unused disk space
    try:
        conn = database.get_db_connection()
        conn.execute("VACUUM")
        conn.close()
    except Exception:
        pass
        
    return jsonify({"success": True, "message": f"Successfully deleted {deleted} log entries older than {days} days."})

@app.route('/api/admin/system_health', methods=['GET'])
def api_admin_system_health():
    user_id = get_current_user_id()
    if user_id != 1:
        return jsonify({"success": False, "message": "Access Denied"}), 403
        
    db_size_bytes = 0
    if os.path.exists(database.DB_PATH):
        db_size_bytes = os.path.getsize(database.DB_PATH)
    db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
    
    import threading
    active_threads = threading.active_count()
    
    import sys
    import platform
    python_version = sys.version.split()[0]
    os_info = f"{platform.system()} {platform.release()}"
    
    return jsonify({
        "success": True,
        "db_size": f"{db_size_mb} MB",
        "active_threads": active_threads,
        "python_version": python_version,
        "os": os_info
    })


# Startup: Start Telegram bot polling & restore all enabled user bots
start_telegram_polling()
bot_manager_instance.start_all_enabled_bots()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
