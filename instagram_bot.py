import time
import threading
import logging
import random
from datetime import datetime, timedelta
import pytz
import json
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired, ChallengeRequired, LoginRequired

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import database

# Monkeypatch instagrapi MediaXma validation error (instagram:// URL scheme crash)
try:
    import instagrapi.extractors
    from instagrapi.types import MediaXma
    
    original_extract_media_v1_xma = instagrapi.extractors.extract_media_v1_xma

    def patched_extract_media_v1_xma(data):
        if data and isinstance(data, dict):
            # Check target_url
            target_url = data.get("target_url")
            if target_url and isinstance(target_url, str) and not target_url.startswith("http"):
                data = dict(data)
                data["target_url"] = "https://instagram.com/direct_media_placeholder"
            # Check preview_url
            preview_url = data.get("preview_url")
            if preview_url and isinstance(preview_url, str) and not preview_url.startswith("http"):
                data = dict(data)
                data["preview_url"] = "https://instagram.com/preview_placeholder"
                
        try:
            return original_extract_media_v1_xma(data)
        except Exception as e:
            logger.warning(f"Failed to extract MediaXma, using fallback: {e}")
            try:
                fallback_data = {
                    "video_url": "https://instagram.com/fallback",
                    "title": data.get("title_text", "Shared Media") if isinstance(data, dict) else "Shared Media"
                }
                return MediaXma(**fallback_data)
            except Exception:
                return None

    instagrapi.extractors.extract_media_v1_xma = patched_extract_media_v1_xma
    logger.info("Successfully applied instagrapi MediaXma monkeypatch.")
except Exception as patch_err:
    logger.error(f"Failed to apply instagrapi MediaXma patch: {patch_err}")


def to_utc_aware(dt):
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(pytz.utc)

def get_current_offline_start_utc(start_str, end_str, tz_name):
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone('Asia/Dhaka')
        
    now = datetime.now(tz)
    
    if start_str == end_str:
        return (now - timedelta(days=1)).astimezone(pytz.utc)
        
    try:
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()
    except ValueError:
        return None
        
    start_today = tz.localize(datetime.combine(now.date(), start_time))
    start_yesterday = tz.localize(datetime.combine(now.date() - timedelta(days=1), start_time))
    
    if start_time <= end_time:
        start_dt = start_today
    else:
        if now.time() >= start_time:
            start_dt = start_today
        else:
            start_dt = start_yesterday
            
    return start_dt.astimezone(pytz.utc)

class InstagramBot:
    def __init__(self, account_id=1):
        self.account_id = account_id
        self.cl = Client()
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.status = "Disconnected"
        self.last_run_time = None
        self.two_factor_info = None  # Stores details if 2FA is needed
        self.login_credentials = None # Temporary store for username/pw during 2FA
        self.sent_auto_message_ids = set() # Store sent auto-message IDs to avoid counting them as user activity
        self.offline_session_start_time = None # Start time of the active offline session

    def get_status(self):
        return {
            "status": self.status,
            "last_run": self.last_run_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_run_time else "Never",
            "needs_2fa": self.two_factor_info is not None
        }

    def send_alert_to_telegram(self, message: str):
        try:
            settings = database.get_settings(self.account_id)
            chat_id = settings.get('telegram_chat_id')
            if chat_id:
                from telegram_manager import send_telegram_notification
                send_telegram_notification(self.account_id, message)
        except Exception as e:
            logger.error(f"Failed to send Telegram alert for account {self.account_id}: {e}")

    def is_offline(self, start_str, end_str, tz_name):
        if start_str == end_str:
            return True
            
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone('Asia/Dhaka')
        
        now = datetime.now(tz)
        current_time = now.time()
        
        try:
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
        except ValueError:
            return False
            
        if start_time <= end_time:
            return start_time <= current_time <= end_time
        else: # Over midnight
            return current_time >= start_time or current_time <= end_time

    def parse_message_template(self, template_str: str, sender_username: str, timezone_name: str, offline_end_str: str) -> str:
        if not template_str:
            return ""
        
        # 1. Parse {sender_username}
        result = template_str.replace("{sender_username}", f"@{sender_username}" if sender_username else "there")
        
        # 2. Parse {current_time}
        try:
            tz = pytz.timezone(timezone_name)
        except Exception:
            tz = pytz.timezone('Asia/Dhaka')
        now_local = datetime.now(tz)
        current_time_str = now_local.strftime("%I:%M %p") # e.g. 10:30 PM
        result = result.replace("{current_time}", current_time_str)
        
        # 3. Parse {time_until_active}
        time_until_str = "0 minutes"
        try:
            end_time = datetime.strptime(offline_end_str, "%H:%M").time()
            end_dt = tz.localize(datetime.combine(now_local.date(), end_time))
            
            # If end_time is earlier in the day and we are over midnight, it might be tomorrow
            if end_dt < now_local:
                end_dt = tz.localize(datetime.combine(now_local.date() + timedelta(days=1), end_time))
            
            diff = end_dt - now_local
            total_seconds = int(diff.total_seconds())
            
            if total_seconds > 0:
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                parts = []
                if hours > 0:
                    parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes > 0 or not parts:
                    parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
                time_until_str = " and ".join(parts)
        except Exception as e:
            logger.error(f"Error calculating time_until_active: {e}")
            time_until_str = "a few hours"
            
        result = result.replace("{time_until_active}", time_until_str)
        return result

    def login(self, username, password, verification_code=None):
        self.status = "Logging In"
        self.two_factor_info = None
        self.login_credentials = (username, password)
        
        # Configure a realistic user agent and client settings
        self.cl = Client()
        
        # Check if we have a proxy
        settings = database.get_settings(self.account_id)
        proxy = settings.get('proxy')
        if proxy:
            try:
                logger.info(f"Using proxy: {proxy}")
                self.cl.set_proxy(proxy)
            except Exception as e:
                logger.error(f"Failed to set proxy: {e}")
        
        if password == "session_login_placeholder":
            logger.info("Session settings loading check.")
            saved_session = settings.get('session_settings')
            if saved_session:
                try:
                    logger.info("Attempting to restore Session ID login from saved settings.")
                    session_info = json.loads(saved_session)
                    self.cl.set_settings(session_info)
                    if not self.cl.user_id:
                        raise Exception("Session ID not found in settings.")
                    self.status = "Idle"
                    self.login_credentials = None
                    return True, "Session restored successfully."
                except Exception as e:
                    logger.error(f"Failed to restore Session ID: {e}")
            
            logger.error("Session expired and no password stored. Re-login using Session ID is required.")
            self.status = "Error: Session Expired. Re-login using Session ID."
            self.login_credentials = None
            self.send_alert_to_telegram(f"⚠️ *Instagram Session Expired!*\n\nPlease log in again with your credentials or Session ID on the website dashboard.")
            return False, "Session expired. Please log in again using a Session ID."
        
        # Check if we have a saved session in the DB
        saved_session = settings.get('session_settings')
        
        try:
            if saved_session and not verification_code:
                try:
                    logger.info("Attempting to login using saved session settings.")
                    session_info = json.loads(saved_session)
                    self.cl.set_settings(session_info)
                    self.cl.login(username, password)
                    self.status = "Idle"
                    self.login_credentials = None
                    return True, "Login successful using saved session."
                except Exception as e:
                    logger.warning(f"Failed to login with saved session: {e}. Falling back to standard login.")
                    # Reset client settings and re-apply proxy if available
                    self.cl = Client()
                    if proxy:
                        self.cl.set_proxy(proxy)
            
            if verification_code:
                logger.info("Attempting 2FA login.")
                self.cl.login(username, password, verification_code=verification_code)
            else:
                logger.info("Attempting standard password login.")
                self.cl.login(username, password)
                
            # If successful, save the session settings for future use
            session_settings = self.cl.get_settings()
            database.update_settings(self.account_id, {
                "username": username,
                "password": password, # Encrypted by DB update_settings
                "session_settings": json.dumps(session_settings)
            })
            
            self.status = "Idle"
            self.login_credentials = None
            return True, "Login successful."
            
        except TwoFactorRequired as e:
            logger.info("Two-Factor Authentication required.")
            self.status = "Verification Required"
            self.two_factor_info = {
                "username": username,
                "two_factor_identifier": getattr(e, 'two_factor_info', {}).get('two_factor_identifier', '')
            }
            self.send_alert_to_telegram(f"🔐 *Instagram 2FA Verification Required!*\n\nAccount: *@{username}*\nPlease log in via the website to submit the 2FA code.")
            return False, "2FA Required"
            
        except ChallengeRequired as e:
            logger.error(f"Challenge required by Instagram: {e}")
            self.status = "Error: Challenge Required. Please login on your phone first."
            self.login_credentials = None
            self.send_alert_to_telegram(f"⚠️ *Instagram Login Challenge Required!*\n\nAccount: *@{username}*\nPlease open Instagram on your phone and approve the login request.")
            return False, "Challenge Required. Open Instagram on your phone and approve login."
            
        except Exception as e:
            logger.error(f"Login failed: {e}")
            self.status = f"Error: {str(e)}"
            self.login_credentials = None
            self.send_alert_to_telegram(f"❌ *Instagram Login Failed!*\n\nAccount: *@{username}*\nError: `{str(e)}`")
            return False, str(e)

    def login_by_sessionid(self, session_id):
        self.status = "Logging In"
        self.two_factor_info = None
        self.login_credentials = None
        
        self.cl = Client()
        
        # Check if we have a proxy
        settings = database.get_settings(self.account_id)
        proxy = settings.get('proxy')
        if proxy:
            try:
                logger.info(f"Using proxy: {proxy}")
                self.cl.set_proxy(proxy)
            except Exception as e:
                logger.error(f"Failed to set proxy: {e}")
                
        try:
            logger.info("Attempting session ID login.")
            self.cl.login_by_sessionid(session_id)
            
            # Retrieve logged in account username
            user_info = self.cl.user_info(self.cl.user_id)
            username = user_info.username
            
            # Save to database
            session_settings = self.cl.get_settings()
            database.update_settings(self.account_id, {
                "username": username,
                "password": "session_login_placeholder",
                "session_settings": json.dumps(session_settings)
            })
            
            self.status = "Idle"
            return True, f"Login successful using Session ID for @{username}."
        except Exception as e:
            logger.error(f"Session ID login failed: {e}")
            self.status = f"Error: {str(e)}"
            self.send_alert_to_telegram(f"❌ *Instagram Session ID Login Failed!*\n\nError: `{str(e)}`")
            return False, str(e)

    def logout(self):
        self.status = "Disconnected"
        self.cl = Client()
        database.update_settings(self.account_id, {
            "username": "",
            "password": "",
            "session_settings": ""
        })
        self.stop_bot()

    def start_bot(self):
        if self.worker_thread and self.worker_thread.is_alive():
            logger.info("Bot is already running.")
            return True
            
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        logger.info("Background bot thread started.")
        return True

    def stop_bot(self):
        self.stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)
            logger.info("Background bot thread stopped.")

    def is_user_active(self, threads, activity_threshold_minutes=5):
        now_utc = datetime.now(pytz.utc)
        settings = database.get_settings(self.account_id)
        offline_msg = settings.get('offline_message', '')
        
        for thread in threads:
            if not thread.messages:
                continue
            for msg in thread.messages[:5]:
                if str(msg.user_id) == str(self.cl.user_id):
                    # Skip bot's own auto-replies
                    if str(msg.id) in self.sent_auto_message_ids or msg.text == offline_msg:
                        continue
                    msg_time = to_utc_aware(msg.timestamp)
                    diff = now_utc - msg_time
                    if diff.total_seconds() / 60.0 < activity_threshold_minutes:
                        logger.info(f"User was active {diff.total_seconds() / 60.0:.1f} minutes ago. Skipping auto-replies.")
                        return True
        return False

    def check_and_reply(self):
        settings = database.get_settings(self.account_id)
        if not settings.get('bot_enabled'):
            logger.info("Bot is disabled in settings.")
            self.status = "Idle (Disabled)"
            return
            
        username = settings.get('username')
        password = settings.get('password')
        
        if not username or not password:
            self.status = "Error: Missing credentials"
            logger.warning("Bot is enabled but username/password is missing.")
            return

        # Check if currently offline
        if not self.is_offline(settings.get('offline_start'), settings.get('offline_end'), settings.get('timezone')):
            logger.info("Currently outside offline hours. No auto-replies will be sent.")
            self.status = "Idle (Online Hours)"
            self.offline_session_start_time = None
            return

        if self.offline_session_start_time is None:
            self.offline_session_start_time = datetime.now(pytz.utc) - timedelta(minutes=5)
            logger.info(f"Offline session started. Tracking messages sent after {self.offline_session_start_time}")

        self.status = "Running"
        self.last_run_time = datetime.now()
        
        # Ensure logged in
        if not self.cl.user_id:
            logger.info("Client not logged in. Re-authenticating...")
            success, msg = self.login(username, password)
            if not success:
                logger.error(f"Re-authentication failed: {msg}")
                return
        
        try:
            logger.info("Fetching direct messages...")
            # Retrieve last 15 threads
            threads = self.cl.direct_threads(amount=15)
            
            # Skip if user is actively using Instagram (chatting in last 5 mins, or 1 min if checking fast)
            check_interval = settings.get('check_interval_seconds', 300)
            activity_threshold = 1 if check_interval < 60 else 5

            if self.is_user_active(threads, activity_threshold_minutes=activity_threshold):
                self.status = "Idle (User Active)"
                return
            
            for thread in threads:
                # Retrieve messages in this thread
                messages = thread.messages
                if not messages:
                    continue
                
                # Last message in the thread
                last_msg = messages[0]
                
                # Verify last message is from someone else (not the bot itself)
                if str(last_msg.user_id) != str(self.cl.user_id):
                    # Only reply to messages received AFTER the start of the current offline session
                    msg_time_utc = to_utc_aware(last_msg.timestamp)
                    session_start = self.offline_session_start_time or (datetime.now(pytz.utc) - timedelta(minutes=5))
                    
                    if msg_time_utc < session_start:
                        logger.info(f"Skipping thread {thread.id}: last message received at {msg_time_utc} is before offline session start {session_start}")
                        continue
                        
                    # Check if we should reply (cooldown prevents spamming)
                    cooldown_val = settings.get('cooldown_hours', 12)
                    if database.should_reply(self.account_id, thread.id, cooldown_hours=cooldown_val):
                        # Fetch latest settings to avoid race condition
                        latest_settings = database.get_settings(self.account_id)
                        reply_message = latest_settings.get('offline_message', '')
                        triggers = database.get_keyword_triggers(self.account_id)
                        last_msg_text = (last_msg.text or "").strip().lower()
                        for trig in triggers:
                            kw = trig['keyword'].strip().lower()
                            if kw in last_msg_text:
                                reply_message = trig['reply_message']
                                logger.info(f"Keyword match '{kw}' for account {self.account_id}. Using custom message.")
                                break
                        
                        receiver_username = ""
                        if thread.users:
                            receiver_username = thread.users[0].username
                        else:
                            receiver_username = f"User_{last_msg.user_id}"

                        # Parse response template strings
                        reply_message = self.parse_message_template(
                            reply_message,
                            receiver_username,
                            settings.get('timezone', 'Asia/Dhaka'),
                            settings.get('offline_end', '08:00')
                        )
                        
                        # Simulate human response delay (3-8 seconds)
                        delay = random.randint(3, 8)
                        logger.info(f"Simulating human delay. Waiting {delay} seconds before sending reply to thread {thread.id}...")
                        time.sleep(delay)
                        
                        logger.info(f"Sending auto-reply to thread {thread.id} (User ID: {last_msg.user_id})")
                        sent_msg = self.cl.direct_send(reply_message, thread_ids=[thread.id])
                        if sent_msg and hasattr(sent_msg, 'id'):
                            self.sent_auto_message_ids.add(str(sent_msg.id))
                        
                        # Log the reply in SQLite
                        database.log_activity(self.account_id, receiver_username, thread.id, reply_message)
                        
                        # Telegram Notification Alert
                        try:
                            from telegram_manager import send_telegram_notification
                            alert_msg = (
                                f"📩 *New DM Auto-Replied!*\n\n"
                                f"• *Account*: @{username}\n"
                                f"• *From*: @{receiver_username}\n"
                                f"• *Received*: \"{last_msg.text}\"\n"
                                f"• *Replied*: \"{reply_message}\""
                            )
                            send_telegram_notification(self.account_id, alert_msg)
                        except Exception as te:
                            logger.error(f"Failed to send Telegram alert for account {self.account_id}: {te}")
                            
                        # Seen/Unseen Control (Incognito Mode)
                        seen_control = settings.get('seen_control', 'seen')
                        if seen_control == 'unseen':
                            try:
                                logger.info(f"Incognito mode active: marking thread {thread.id} as unread.")
                                self.cl.direct_thread_mark_unread(thread.id)
                            except Exception as ue:
                                logger.error(f"Failed to mark thread {thread.id} as unread: {ue}")
                                
                        time.sleep(2)  # Short delay between DMs to avoid spam flags
                            
        except LoginRequired:
            logger.warning("Session expired. Resetting client and will retry login next cycle.")
            self.cl = Client()
            self.status = "Session Expired"
            self.send_alert_to_telegram(f"⚠️ *Instagram Connection Lost!*\n\nSession expired for @{username}. Attempting to re-authenticate on next checking cycle...")
        except Exception as e:
            logger.error(f"Error during message checking: {e}")
            self.status = f"Error: {str(e)}"

    def _run_loop(self):
        logger.info("Bot loop started.")
        while not self.stop_event.is_set():
            try:
                self.check_and_reply()
            except Exception as e:
                logger.error(f"Unexpected error in run loop: {e}")
                self.status = f"Error: {str(e)}"
                
            # Fetch checking interval from db (fallback to 5 minutes)
            settings = database.get_settings(self.account_id)
            interval_seconds = settings.get('check_interval_seconds', 300)
            # Sleep in small 1-second increments to allow instant stopping
            for _ in range(int(interval_seconds)):
                if self.stop_event.is_set():
                    break
                time.sleep(1)
                
        logger.info("Bot loop exited.")
