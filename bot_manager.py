import logging
from instagram_bot import InstagramBot
import database

logger = logging.getLogger(__name__)

class BotManager:
    def __init__(self):
        self.active_bots = {}  # {account_id: InstagramBot()}

    def start_bot(self, account_id: int):
        if account_id in self.active_bots:
            bot = self.active_bots[account_id]
            if bot.worker_thread and bot.worker_thread.is_alive():
                logger.info(f"Bot for account {account_id} is already running.")
                return True
        else:
            bot = InstagramBot(account_id)
            self.active_bots[account_id] = bot
            
        bot.start_bot()
        logger.info(f"Bot manager started bot for account {account_id}.")
        return True

    def stop_bot(self, account_id: int):
        if account_id in self.active_bots:
            bot = self.active_bots[account_id]
            bot.stop_bot()
            logger.info(f"Bot manager stopped bot for account {account_id}.")
            return True
        return False

    def get_bot(self, account_id: int):
        if account_id not in self.active_bots:
            bot = InstagramBot(account_id)
            self.active_bots[account_id] = bot
        return self.active_bots[account_id]

    def start_all_enabled_bots(self):
        try:
            enabled_account_ids = database.get_all_enabled_accounts()
            for acc_id in enabled_account_ids:
                logger.info(f"Auto-starting enabled bot for account {acc_id}...")
                self.start_bot(acc_id)
        except Exception as e:
            logger.error(f"Error auto-starting bots: {e}")

bot_manager_instance = BotManager()
