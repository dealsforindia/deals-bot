import feedparser
import requests
import os
import google.generativeai as genai
import time

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
# Using RSS feeds avoids Reddit API bans
RSS_URL = "https://www.reddit.com/r/deals+dealhunter+indianbeautydeals/new/.rss"

# --- AI SETUP ---
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

def get_last_processed_time():
    try:
        with open("last_post.txt", "r") as f:
            return float(f.read().strip())
    except:
        return 0.0

def save_last_processed_time(timestamp):
    with open("last_post.txt", "w") as f:
        f.write(str(timestamp))

def rewrite_with_ai(title):
    if not GOOGLE_API_KEY:
        return title
    try:
        # Simple prompt to make it catchy
        prompt = f"Rewrite this deal title for Telegram. Make it urgent, add 1 emoji. No links. Title: {title}"
        response = model.generate_content(prompt)
        return response.text.strip()
    except:
        return title

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "disable_web_page_preview": False
    }
    requests.post(url, json=payload)

def process_feed():
    last_time = get_last_processed_time()
    print(f"Checking RSS... Last time: {last_time}")
    
    feed = feedparser.parse(RSS_URL)
    new_entries = []

    # Filter for new posts
    for entry in feed.entries:
        # Convert struct_time to timestamp
        entry_time = time.mktime(entry.published_parsed)
        if entry_time > last_time:
            new_entries.append(entry)

    # Process oldest first
    new_entries.reverse()
    
    new_last_time = last_time

    for entry in new_entries:
        print(f"New Deal: {entry.title}")
        
        # 1. Clean/Rewrite Title with AI
        clean_title = rewrite_with_ai(entry.title)
        
        # 2. Get Link
        link = entry.link
        
        # 3. Send
        msg = f"{clean_title}\n\n{link}"
        send_telegram_msg(msg)
        
        entry_time = time.mktime(entry.published_parsed)
        new_last_time = entry_time
        time.sleep(2) # Be nice to Telegram API

    if new_last_time > last_time:
        save_last_processed_time(new_last_time)

if __name__ == "__main__":
    process_feed()
