import requests
import os
import re
import html
import time
import sys

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
EARNKARO_TOKEN = os.environ.get("EARNKARO_TOKEN")
SUBREDDIT = "dealsforindia"
BATCH_LIMIT = 10 

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

def send_telegram(caption, image_url=None):
    print("   -> Sending to Telegram...")
    if len(caption) > 1000: caption = caption[:990] + "..."
    data = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
    if image_url:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        data["photo"] = image_url
    else:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data["text"] = caption
        del data["caption"]
    
    r = requests.post(url, data=data)
    print(f"   -> Telegram Response: {r.status_code} {r.text}")

def main():
    print("--- STARTING BOT ---")
    
    # 1. Read Memory
    print("1. Reading last_post.txt...")
    try:
        with open("last_post.txt", "r") as f: last_id = f.read().strip()
        print(f"   Last ID found: {last_id}")
    except: 
        last_id = None
        print("   No memory file found (First run?)")

    # 2. Connect to Reddit
    url = f"https://www.reddit.com/r/{SUBREDDIT}/new.json?limit={BATCH_LIMIT}"
    print(f"2. Connecting to Reddit: {url}")
    
    r = requests.get(url, headers=HEADERS)
    print(f"   Reddit Status Code: {r.status_code}")
    
    if r.status_code != 200:
        print(f"   CRITICAL ERROR: Reddit blocked the bot!")
        print(f"   Response: {r.text[:500]}")
        return

    # 3. Parse Data
    try:
        posts = r.json()['data']['children']
        print(f"3. Found {len(posts)} posts in the feed.")
    except Exception as e:
        print(f"   ERROR Parsing JSON: {e}")
        return

    new_posts = []
    for post in posts:
        p_id = post['data']['id']
        if p_id == last_id: 
            print(f"   Stopped at old post: {p_id}")
            break
        new_posts.append(post['data'])

    print(f"4. New posts to send: {len(new_posts)}")

    if not new_posts:
        print("   No new posts. Exiting.")
        return

    # 4. Process Posts
    for post_data in reversed(new_posts):
        title = html.unescape(post_data['title'])
        print(f"   Processing: {title}")
        
        # (Simplified logic for debug - if this works, we add the fancy stuff back)
        send_telegram(f"{title}\n\nhttps://reddit.com{post_data['permalink']}")
        
        # Save ID
        with open("last_post.txt", "w") as f: f.write(post_data['id'])
        time.sleep(2)

    print("--- FINISHED SUCCESSFULLY ---")

if __name__ == "__main__":
    main()
