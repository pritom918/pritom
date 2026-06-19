import os
import telebot
from telebot import types
import database
import logging
import threading
import time

logger = logging.getLogger(__name__)

# Load Telegram Token from env or a local token file
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.telegram_token')
if not TOKEN and os.path.exists(TOKEN_PATH):
    try:
        with open(TOKEN_PATH, 'r') as f:
            TOKEN = f.read().strip()
    except Exception as e:
        logger.error(f"Failed to read .telegram_token file: {e}")

# Initialize bot client
bot = None
bot_username = None
if TOKEN:
    try:
        bot = telebot.TeleBot(TOKEN)
        logger.info("Telegram Bot client initialized successfully.")
        try:
            bot_user = bot.get_me()
            bot_username = bot_user.username
            logger.info(f"Telegram Bot username: @{bot_username}")
        except Exception as me_err:
            logger.error(f"Failed to fetch Telegram bot username: {me_err}")
    except Exception as e:
        logger.error(f"Failed to initialize Telegram Bot: {e}")
else:
    logger.warning("No TELEGRAM_BOT_TOKEN found. Telegram features are disabled.")

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    for char in ['_', '*', '`']:
        text = text.replace(char, f"\\{char}")
    return text

# Send notification to a specific user's Telegram chat
def send_telegram_notification(account_id: int, message: str):
    if not bot:
        return False
    try:
        settings = database.get_settings(account_id)
        chat_id = settings.get('telegram_chat_id')
        if chat_id:
            bot.send_message(chat_id, message, parse_mode="Markdown")
            return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification to account {account_id}: {e}")
    return False

# Show administrative controls using plain text command instructions (no buttons)
def show_admin_panel(chat_id, account_id):
    if not bot:
        return
        
    try:
        settings = database.get_settings(account_id)
        bot_enabled = "🟢 Active" if settings.get('bot_enabled') else "🔴 Disabled"
        insta_user = escape_markdown(settings.get('username') or "Not Connected")
        
        menu_text = (
            f"🛠️ *PTM Command Menu*\n\n"
            f"• *Instagram Account*: @{insta_user} (Account ID: `{account_id}`)\n"
            f"• *Auto-Responder Status*: {bot_enabled}\n"
            f"• *Timezone*: {settings.get('timezone')}\n\n"
            f"🤖 *Available Commands*:\n"
            f"• `/status` - Check current bot status\n"
            f"• `/toggle` - Enable/disable auto-responder\n"
            f"• `/logs` - View recent activity logs\n"
            f"• `/msg` - View current reply message\n"
            f"• `/setmsg <text>` - Update your reply message\n"
            f"• `/triggers` - View keyword triggers\n"
            f"• `/addtrigger <keyword> -> <reply>` - Add a trigger\n"
            f"• `/deltrigger <keyword>` - Delete a trigger\n"
            f"• `/disconnect` - Disconnect Instagram account\n"
        )
        
        user_id = settings.get('user_id')
        if user_id == 1:
            menu_text += (
                f"\n👑 *Super Admin Commands*:\n"
                f"• `/users` - List registered users and their accounts\n"
                f"• `/user_status <account_id>` - Check status of an Instagram bot\n"
                f"• `/user_toggle <account_id>` - Toggle an Instagram bot ON/OFF\n"
                f"• `/user_logs <account_id>` - View activity logs for a bot\n"
                f"• `/user_disconnect <account_id>` - Disconnect a bot's Instagram\n"
                f"• `/user_clear <account_id>` - Clear reply history/cooldown for a bot\n"
            )
            
        bot.send_message(chat_id, menu_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error rendering Telegram admin panel for account {account_id}: {e}")

def show_dashboard_accounts(chat_id, accounts):
    if not bot:
        return
    msg = "📱 *Your Linked Instagram Accounts*:\n\n"
    for acc in accounts:
        status = "🟢 Active" if acc.get('bot_enabled') else "🔴 Disabled"
        username = escape_markdown(acc.get('username') or 'Pending Connection')
        msg += f"• *@{username}* (Account ID: `{acc['id']}`) - {status}\n"
    msg += (
        "\n🤖 *Commands you can run*:\n"
        "• `/status <username>` or `/status` (all)\n"
        "• `/toggle <username>` - Toggle responder\n"
        "• `/logs <username>` - View recent logs\n"
        "• `/msg <username>` - View current reply message\n"
        "• `/setmsg <username> <text>` - Update reply message\n"
        "• `/triggers <username>` - List keyword triggers\n"
        "• `/addtrigger <username> <keyword> -> <message>`\n"
        "• `/deltrigger <username> <keyword>`\n"
        "• `/disconnect <username>` - Disconnect Instagram"
    )
    bot.send_message(chat_id, msg, parse_mode="Markdown")

# Register Telegram bot handlers if client is active
if bot:
    def resolve_account(message, args):
        chat_id = str(message.chat.id)
        accounts = database.get_accounts_by_telegram(chat_id)
        
        if not accounts:
            bot.reply_to(message, "❌ You must link your account first by using the `/start <token>` command from your web dashboard.")
            return None, None
            
        # If there's only one account, return it
        if len(accounts) == 1:
            return accounts[0], args
            
        # Multiple accounts: check if the first arg is a username (optional prefix '@')
        if args:
            potential_username = args[0].strip().lower().lstrip('@')
            for acc in accounts:
                if acc.get('username') and acc.get('username').lower() == potential_username:
                    # Found! Remove the username from the args and return
                    return acc, args[1:]
                    
        # Username not provided or not matched: show list of accounts and prompt
        show_dashboard_accounts(chat_id, accounts)
        return None, None

    def verify_super_admin(message):
        chat_id = str(message.chat.id)
        user_id = database.get_user_by_telegram(chat_id)
        if user_id != 1:
            bot.reply_to(message, "❌ Access Denied: You are not authorized to run admin commands.")
            return False
        return True

    @bot.message_handler(commands=['start'])
    def handle_start(message):
        args = message.text.split()
        chat_id = str(message.chat.id)
        
        if len(args) > 1:
            try:
                token = args[1].strip()
                account = database.get_account_by_telegram_token(token)
                if not account:
                    bot.reply_to(message, "❌ Invalid or expired Telegram linking token.")
                    return
                # Link this Telegram chat ID to this account row in settings
                database.update_settings(account['id'], {"telegram_chat_id": chat_id})
                bot.reply_to(
                    message, 
                    f"🎉 *Account Connected Successfully!*\n\nYour Telegram account has been linked to *@{escape_markdown(account.get('username') or 'Pending Connection')}* (Account ID `{account['id']}`).\nYou will now receive direct notification alerts here.",
                    parse_mode="Markdown"
                )
                show_admin_panel(message.chat.id, account['id'])
            except Exception as e:
                bot.reply_to(message, f"❌ Linking failed: {str(e)}")
        else:
            # Check if Telegram chat is already linked to some accounts
            accounts = database.get_accounts_by_telegram(chat_id)
            if accounts:
                show_dashboard_accounts(chat_id, accounts)
            else:
                bot.reply_to(
                    message, 
                    "👋 *Welcome to PTM Auto-Responder Bot!*\n\nTo link your account, please log into your website dashboard, copy your Telegram linking command, and paste it here.",
                    parse_mode="Markdown"
                )

    @bot.message_handler(commands=['status'])
    def handle_status(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        from bot_manager import bot_manager_instance
        bot_instance = bot_manager_instance.get_bot(account['id'])
        status_info = bot_instance.get_status()
        bot.reply_to(
            message,
            f"🔍 *Bot Status Update (@{escape_markdown(account['username'] or 'Pending')})*:\n\n"
            f"• *Status*: `{status_info['status']}`\n"
            f"• *Last Checked*: `{status_info['last_run']}`\n"
            f"• *Needs 2FA*: `{'Yes' if status_info['needs_2fa'] else 'No'}`",
            parse_mode="Markdown"
        )

    @bot.message_handler(commands=['toggle'])
    def handle_toggle(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        from bot_manager import bot_manager_instance
        is_enabled = not account.get('bot_enabled')
        database.update_settings(account['id'], {"bot_enabled": 1 if is_enabled else 0})
        
        if is_enabled:
            bot_manager_instance.start_bot(account['id'])
            bot.reply_to(message, f"🟢 *Auto-Responder Activated* for @{escape_markdown(account['username'] or 'Pending')}.")
        else:
            bot_manager_instance.stop_bot(account['id'])
            bot.reply_to(message, f"🔴 *Auto-Responder Disabled* for @{escape_markdown(account['username'] or 'Pending')}.")

    @bot.message_handler(commands=['logs'])
    def handle_logs(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        logs = database.get_logs(account['id'], limit=5)
        if not logs:
            bot.reply_to(message, f"📋 No auto-replies logged yet for @{escape_markdown(account['username'] or 'Pending')}.")
        else:
            log_lines = []
            for log in logs:
                t_str = log['timestamp'].split('T')[1][:8] if 'T' in log['timestamp'] else log['timestamp']
                user_val = escape_markdown(log['username'])
                msg_val = escape_markdown(log['message'])
                log_lines.append(f"⏱️ `{t_str}` ➡️ @{user_val}: _{msg_val}_")
            bot.reply_to(
                message, 
                f"📋 *Recent Activities (@{escape_markdown(account['username'] or 'Pending')})*:\n\n" + "\n".join(log_lines), 
                parse_mode="Markdown"
            )

    @bot.message_handler(commands=['msg'])
    def handle_msg(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        bot.reply_to(
            message,
            f"💬 *Current Auto-Reply Message (@{escape_markdown(account['username'] or 'Pending')})*:\n\n"
            f"`{escape_markdown(account.get('offline_message'))}`\n\n"
            f"To change this message, use command:\n"
            f"`/setmsg <your new message>` or `/setmsg @username <new message>`",
            parse_mode="Markdown"
        )

    @bot.message_handler(commands=['setmsg'])
    def handle_setmsg(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        new_msg = " ".join(remaining).strip()
        if not new_msg:
            bot.reply_to(message, "❌ Please specify the new message. Example: `/setmsg Hello! I am offline.`")
            return
            
        database.update_settings(account['id'], {"offline_message": new_msg})
        
        # Restart the account's bot immediately to apply settings
        from bot_manager import bot_manager_instance
        if account.get('bot_enabled'):
            bot_manager_instance.stop_bot(account['id'])
            bot_manager_instance.start_bot(account['id'])
            
        bot.reply_to(message, f"✅ *Auto-Reply Message Updated!* for @{escape_markdown(account['username'] or 'Pending')}::\n\n`{escape_markdown(new_msg)}`", parse_mode="Markdown")

    @bot.message_handler(commands=['disconnect'])
    def handle_disconnect(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        from bot_manager import bot_manager_instance
        bot_instance = bot_manager_instance.get_bot(account['id'])
        bot_instance.logout()
        bot.reply_to(message, f"❌ Connected Instagram account *@{escape_markdown(account['username'] or 'Pending')}* has been logged out/disconnected.")

    @bot.message_handler(commands=['triggers'])
    def handle_triggers(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        triggers = database.get_keyword_triggers(account['id'])
        if not triggers:
            bot.reply_to(message, f"📋 No keyword triggers configured for @{escape_markdown(account['username'] or 'Pending')}.")
        else:
            trig_lines = []
            for t in triggers:
                kw = escape_markdown(t['keyword'])
                rep = escape_markdown(t['reply_message'])
                trig_lines.append(f"• `{kw}` ➔ \"{rep}\"")
            bot.reply_to(
                message,
                f"🔑 *Active Keyword Triggers (@{escape_markdown(account['username'] or 'Pending')})*:\n\n" + "\n".join(trig_lines),
                parse_mode="Markdown"
            )

    @bot.message_handler(commands=['addtrigger'])
    def handle_addtrigger(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        payload = " ".join(remaining).strip()
        if not payload or "->" not in payload:
            bot.reply_to(
                message, 
                "❌ Invalid format. Use:\n`/addtrigger <keyword> -> <reply message>`\n"
                "Example: `/addtrigger price -> Item price is $15.`"
            )
            return
            
        parts = payload.split("->", 1)
        keyword = parts[0].strip()
        reply_msg = parts[1].strip()
        
        if not keyword or not reply_msg:
            bot.reply_to(message, "❌ Keyword and reply message cannot be empty.")
            return
            
        success = database.add_keyword_trigger(account['id'], keyword, reply_msg)
        if success:
            bot.reply_to(
                message,
                f"✅ *Keyword Trigger Added/Updated* for @{escape_markdown(account['username'] or 'Pending')}:\n\n"
                f"• *Keyword*: `{escape_markdown(keyword)}`\n"
                f"• *Reply*: \"{escape_markdown(reply_msg)}\"",
                parse_mode="Markdown"
            )
        else:
            bot.reply_to(message, "❌ Failed to add keyword trigger.")

    @bot.message_handler(commands=['deltrigger'])
    def handle_deltrigger(message):
        args = message.text.split()[1:]
        account, remaining = resolve_account(message, args)
        if not account:
            return
            
        keyword = " ".join(remaining).strip()
        if not keyword:
            bot.reply_to(message, "❌ Please specify the keyword to delete. Example: `/deltrigger price`")
            return
            
        success = database.delete_keyword_trigger_by_keyword(account['id'], keyword)
        if success:
            bot.reply_to(message, f"🧹 *Keyword Trigger Deleted* for @{escape_markdown(account['username'] or 'Pending')}: `{escape_markdown(keyword)}`", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"❌ Keyword trigger `{escape_markdown(keyword)}` not found for @{escape_markdown(account['username'] or 'Pending')}.")

    # --- Super Admin commands ---

    @bot.message_handler(commands=['users'])
    def handle_admin_users(message):
        if not verify_super_admin(message):
            return
            
        conn = database.get_db_connection()
        users = conn.execute('SELECT id, username FROM users WHERE id != 1').fetchall()
        conn.close()
        
        if not users:
            bot.reply_to(message, "👥 No other users registered on the website yet.")
            return
            
        user_lines = []
        for u in users:
            accounts = database.get_user_accounts(u['id'])
            user_lines.append(f"👤 *User ID {u['id']}*: {escape_markdown(u['username'])}")
            if not accounts:
                user_lines.append("  ↳ _No Instagram accounts connected_")
            else:
                for acc in accounts:
                    ig_user = f"@{escape_markdown(acc.get('username') or 'Pending Connection')}"
                    bot_status = "🟢 ON" if acc.get('bot_enabled') else "🔴 OFF"
                    user_lines.append(f"  ↳ *Account ID {acc['id']}*: {ig_user} | Bot: {bot_status}")
            
        bot.reply_to(message, "👥 *Registered Website Users & Accounts*:\n\n" + "\n".join(user_lines), parse_mode="Markdown")

    @bot.message_handler(commands=['user_status'])
    def handle_admin_user_status(message):
        if not verify_super_admin(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "❌ Please specify an account ID. Example: `/user_status 2`")
            return
            
        try:
            target_id = int(args[1])
        except ValueError:
            bot.reply_to(message, "❌ Account ID must be an integer.")
            return
            
        settings = database.get_settings(target_id)
        if not settings:
            bot.reply_to(message, f"❌ Account ID `{target_id}` not found.")
            return
            
        from bot_manager import bot_manager_instance
        bot_instance = bot_manager_instance.get_bot(target_id)
        status_info = bot_instance.get_status()
        insta = escape_markdown(settings.get('username') or "Not Connected")
        
        bot.reply_to(
            message,
            f"🔍 *Bot Status for Account ID {target_id}* (@{insta}):\n\n"
            f"• *Status*: `{status_info['status']}`\n"
            f"• *Last Checked*: `{status_info['last_run']}`\n"
            f"• *Needs 2FA*: `{'Yes' if status_info['needs_2fa'] else 'No'}`",
            parse_mode="Markdown"
        )

    @bot.message_handler(commands=['user_toggle'])
    def handle_admin_user_toggle(message):
        if not verify_super_admin(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "❌ Please specify an account ID. Example: `/user_toggle 2`")
            return
            
        try:
            target_id = int(args[1])
        except ValueError:
            bot.reply_to(message, "❌ Account ID must be an integer.")
            return
            
        settings = database.get_settings(target_id)
        if not settings:
            bot.reply_to(message, f"❌ Account ID `{target_id}` not found.")
            return
            
        from bot_manager import bot_manager_instance
        is_enabled = not settings.get('bot_enabled')
        database.update_settings(target_id, {"bot_enabled": 1 if is_enabled else 0})
        
        if is_enabled:
            bot_manager_instance.start_bot(target_id)
            bot.reply_to(message, f"🟢 *Auto-Responder Activated* for Account ID {target_id}.")
        else:
            bot_manager_instance.stop_bot(target_id)
            bot.reply_to(message, f"🔴 *Auto-Responder Disabled* for Account ID {target_id}.")

    @bot.message_handler(commands=['user_logs'])
    def handle_admin_user_logs(message):
        if not verify_super_admin(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "❌ Please specify an account ID. Example: `/user_logs 2`")
            return
            
        try:
            target_id = int(args[1])
        except ValueError:
            bot.reply_to(message, "❌ Account ID must be an integer.")
            return
            
        logs = database.get_logs(target_id, limit=5)
        if not logs:
            bot.reply_to(message, f"📋 No activities logged yet for Account ID {target_id}.")
        else:
            log_lines = []
            for log in logs:
                t_str = log['timestamp'].split('T')[1][:8] if 'T' in log['timestamp'] else log['timestamp']
                user_val = escape_markdown(log['username'])
                msg_val = escape_markdown(log['message'])
                log_lines.append(f"⏱️ `{t_str}` ➡️ @{user_val}: _{msg_val}_")
            bot.reply_to(message, f"📋 *Recent Activities (Account ID {target_id})*:\n\n" + "\n".join(log_lines), parse_mode="Markdown")

    @bot.message_handler(commands=['user_disconnect'])
    def handle_admin_user_disconnect(message):
        if not verify_super_admin(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "❌ Please specify an account ID. Example: `/user_disconnect 2`")
            return
            
        try:
            target_id = int(args[1])
        except ValueError:
            bot.reply_to(message, "❌ Account ID must be an integer.")
            return
            
        from bot_manager import bot_manager_instance
        bot_instance = bot_manager_instance.get_bot(target_id)
        bot_instance.logout()
        bot.reply_to(message, f"❌ Instagram disconnected for Account ID {target_id}.")

    @bot.message_handler(commands=['user_clear'])
    def handle_admin_user_clear(message):
        if not verify_super_admin(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "❌ Please specify an account ID. Example: `/user_clear 2`")
            return
            
        try:
            target_id = int(args[1])
        except ValueError:
            bot.reply_to(message, "❌ Account ID must be an integer.")
            return
            
        database.clear_reply_history(target_id)
        bot.reply_to(message, f"🧹 *Reply history (cooldowns) cleared* for Account ID {target_id}.")

def start_telegram_polling():
    if not bot:
        logger.warning("Telegram Bot Token is not configured. Telegram bot features will be disabled.")
        return
        
    def poll():
        logger.info("Starting Telegram Bot polling thread...")
        while True:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=10)
            except Exception as e:
                logger.error(f"Telegram polling error: {e}. Retrying in 10 seconds...")
                time.sleep(10)
                
    t = threading.Thread(target=poll, daemon=True)
    t.start()
