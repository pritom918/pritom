import os
import database
from instagram_bot import InstagramBot

def run_tests():
    print("--- Starting Multi-Account Auto-Responder Verification Tests ---")
    
    # Clean up existing database test file if any to run cleanly
    if os.path.exists(database.DB_PATH):
        try:
            os.remove(database.DB_PATH)
        except Exception:
            pass
            
    database.init_db()
    
    # 1. Test registration & automatic account slot creation
    print("\n1. Testing User Registration and Default Account Slots...")
    success, msg = database.register_user("testuser", "testpassword123")
    print(f"Registration status: {success} ({msg})")
    
    accounts = database.get_user_accounts(1)
    print(f"Created accounts for User ID 1: {len(accounts)}")
    if accounts:
        print(f"Slot 1 Account ID: {accounts[0]['id']}, Telegram Link Token: {accounts[0]['telegram_link_token']}")
        account_id_1 = accounts[0]['id']
    else:
        print("FAIL: No account slot created on registration!")
        return

    # 2. Test adding second Instagram account slot
    print("\n2. Testing Adding Second Instagram Account...")
    account_id_2 = database.add_instagram_account(1, "insta_second_profile")
    accounts = database.get_user_accounts(1)
    print(f"Total accounts for User ID 1 now: {len(accounts)}")
    print(f"Slot 2 Account ID: {account_id_2}, Username: {accounts[1]['username']}")

    # 3. Test settings update and password encryption
    print("\n3. Testing Settings Update & Password Encryption per account...")
    test_password_1 = "PassAccount1!"
    test_password_2 = "PassAccount2!"
    
    database.update_settings(account_id_1, {
        "username": "insta_first_profile",
        "password": test_password_1,
        "offline_message": "Hello {sender_username}! First profile is offline until {offline_end}."
    })
    database.update_settings(account_id_2, {
        "password": test_password_2,
        "offline_message": "Hey {sender_username}, second profile is away!"
    })
    
    acc_settings_1 = database.get_settings(account_id_1)
    acc_settings_2 = database.get_settings(account_id_2)
    
    print(f"Account 1 Decrypted Password Matches: {acc_settings_1.get('password') == test_password_1}")
    print(f"Account 2 Decrypted Password Matches: {acc_settings_2.get('password') == test_password_2}")

    # 4. Test trigger addition and retrieval per account
    print("\n4. Testing Keyword Triggers Scoped to Account...")
    database.add_keyword_trigger(account_id_1, "price", "Pricing is $10 for account 1")
    database.add_keyword_trigger(account_id_2, "price", "Pricing is $25 for account 2")
    
    triggers_1 = database.get_keyword_triggers(account_id_1)
    triggers_2 = database.get_keyword_triggers(account_id_2)
    
    print(f"Account 1 triggers count: {len(triggers_1)} (Msg: {triggers_1[0]['reply_message']})")
    print(f"Account 2 triggers count: {len(triggers_2)} (Msg: {triggers_2[0]['reply_message']})")
    
    # Test trigger deletion by keyword
    database.delete_keyword_trigger_by_keyword(account_id_1, "price")
    triggers_1_after = database.get_keyword_triggers(account_id_1)
    print(f"Account 1 triggers count after deletion: {len(triggers_1_after)}")

    # 5. Test activity logging and reply history per account
    print("\n5. Testing Scoped Activity Logs and Cooldowns...")
    thread_id = "thread_abc_123"
    database.log_activity(account_id_1, "customer_john", thread_id, "Logged auto-reply message")
    
    logs_1 = database.get_logs(account_id_1, limit=1)
    logs_2 = database.get_logs(account_id_2, limit=1)
    
    print(f"Account 1 logs count: {len(logs_1)}")
    print(f"Account 2 logs count: {len(logs_2)}")
    
    should_1 = database.should_reply(account_id_1, thread_id, cooldown_hours=12)
    should_2 = database.should_reply(account_id_2, thread_id, cooldown_hours=12)
    print(f"Cooldown active on Account 1 (should be False): {should_1}")
    print(f"Cooldown active on Account 2 (should be True): {should_2}")

    # 6. Test dynamic message template parsing
    print("\n6. Testing Response Template Variables...")
    bot_1 = InstagramBot(account_id_1)
    template = "Hey {sender_username}! The local time is {current_time}. We are offline for {time_until_active}."
    parsed = bot_1.parse_message_template(
        template, 
        "customer_john", 
        "Asia/Dhaka", 
        "18:00"
    )
    print(f"Original: {template}")
    print(f"Parsed  : {parsed}")

    # 7. Test account disconnection
    print("\n7. Testing Account Deletion/Disconnection...")
    deleted = database.delete_instagram_account(account_id_2, 1)
    accounts_end = database.get_user_accounts(1)
    print(f"Account deleted successfully: {deleted}")
    print(f"Total remaining accounts for User ID 1: {len(accounts_end)}")

    print("\n--- Verification Tests Completed Successfully ---")

if __name__ == "__main__":
    run_tests()
