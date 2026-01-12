import feedparser
import requests
import html
import os
import re

# Load Keys
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
EARNKARO_TOKEN = os.environ.get("EARNKARO_TOKEN") 
SUBREDDIT = "dealsforindia" 

def send_telegram_photo(caption, image_url):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": CHANNEL_ID, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload)
        if r.status_code != 200: send_telegram_text(caption)
    except: send_telegram_text(caption)

def send_telegram_text(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": message, "parse_mode": "HTML"}
    requests.post(url, data=payload)

def get_earnkaro_link(deal_url):
    if not EARNKARO_TOKEN: return None
    api_url = "https://ekaro-api.affiliaters.in/api/converter/public"
    headers = {"Authorization": f"Bearer {EARNKARO_TOKEN}", "Content-Type": "application/json"}
    payload = {"deal": deal_url, "convert_option": "convert_only"}
    try:
        r = requests.post(api_url, headers=headers, json=payload, timeout=10)
        if r.status_code == 200 and r.json().get("success") == 1: return r.json().get("data")
    except: return None
    return None

def extract_image(entry):
    if hasattr(entry, 'media_content') and entry.media_content: return entry.media_content[0]['url']
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail: return entry.media_thumbnail[0]['url']
    if hasattr(entry, 'content'):
        match = re.search(r'src="(https://[^"]+\.(jpg|png|jpeg))"', entry.content[0].value)
        if match: return match.group(1)
    return None

def main():
    try:
        feed = feedparser.parse(requests.get(f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss", headers={"User-Agent": "Mozilla/5.0"}).content)
        if not feed.entries: return
        
        entry = feed.entries[0]
        title = html.unescape(entry.title)
        image_url = extract_image(entry)
        
        final_link = get_earnkaro_link(entry.link) or entry.link
        label = "EarnKaro" if final_link != entry.link else "Direct Deal"
        
        caption = f"<b>{title}</b>\n\n✅ <b>Link:</b> {final_link}\n\n#Deal #{label}"
        
        if image_url: send_telegram_photo(caption, image_url)
        else: send_telegram_text(caption)
        print("Success")
    except Exception as e: print(e)

if __name__ == "__main__":
    main()
