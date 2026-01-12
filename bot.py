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

# --- NEW HEADER (The Fix for Error 403) ---
# We use a unique ID so Reddit doesn't block us
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36/DealBotv2"}

def get_earnkaro_link(deal_url):
    if not EARNKARO_TOKEN: return deal_url
    api_url = "https://ekaro-api.affiliaters.in/api/converter/public"
    headers = {"Authorization": f"Bearer {EARNKARO_TOKEN}", "Content-Type": "application/json"}
    payload = {"deal": deal_url, "convert_option": "convert_only"}
    try:
        r = requests.post(api_url, headers=headers, json=payload, timeout=5)
        if r.status_code == 200 and r.json().get("success") == 1:
            return r.json().get("data")
    except: pass
    return deal_url 

def process_text_links(text):
    urls = re.findall(r'(https?://[^\s"<\]\)]+)', text)
    unique_urls = sorted(set(urls), key=urls.index)
    
    final_text = text
    for url in unique_urls:
        if "reddit.com" in url or "preview" in url: continue
        affiliate_link = get_earnkaro_link(url)
        if affiliate_link != url:
            final_text = final_text.replace(url, affiliate_link)
    return final_text

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
    print(f"   -> Telegram Response: {r.status_code}")

def process_single_post(post_data):
    title = html.unescape(post_data['title'])
    body_text = html.unescape(post_data.get('selftext', ''))
    permalink = post_data['permalink']
    
    image_url = post_data.get('url_overridden_by_dest')
    if not image_url or "reddit.com" in image_url:
        thumb = post_data.get('thumbnail', '')
        if "http" in thumb: image_url = thumb
        else: image_url = None

    final_body = process_text_links(body_text)

    comment_text = ""
    try:
        comments_url = f"https://www.reddit.com{permalink}.json"
        c_r = requests.get(comments_url, headers=HEADERS)
        if c_r.status_code == 200:
            comments_data = c_r.json()[1]['data']['children']
            for comment in comments_data:
                c_data = comment.get('data', {})
                if c_data.get('is_submitter') == True and c_data.get('body'):
                    processed_comment = process_text_links(html.unescape(c_data['body']))
                    comment_text += f"\n\n🔹 <b>Update:</b>\n{processed_comment}"
    except: pass

    full_caption = f"<b>{title}</b>\n\n{final_body}{comment_text}\n\n#Deal #Loot"
    
    print(f"   Processing: {title}")
    send_telegram(full_caption, image_url)

def main():
    print("--- STARTING BOT ---")
    
    try:
        with open("last_post.txt", "r") as f: last_id = f.read().strip()
        print(f"1. Last ID found: {last_id}")
    except: 
        last_id = None
        print("1. No memory file found.")

    url = f"https://www.reddit.com/r/{SUBREDDIT}/new.json?limit={BATCH_LIMIT}"
    print(f"2. Connecting to Reddit...")
    
    r = requests.get(url, headers=HEADERS)
    print(f"   Reddit Status Code: {r.status_code}")
    
    if r.status_code != 200:
        print(f"   CRITICAL ERROR: Reddit blocked the bot!")
        return

    posts = r.json()['data']['children']
    print(f"3. Found {len(posts)} posts.")

    new_posts = []
    for post in posts:
        p_id = post['data']['id']
        if p_id == last_id: break
        new_posts.append(post['data'])

    if not new_posts:
        print("   No new posts. Exiting.")
        return

    print(f"4. Sending {len(new_posts)} new posts...")
    
    for post_data in reversed(new_posts):
        process_single_post(post_data)
        with open("last_post.txt", "w") as f: f.write(post_data['id'])
        time.sleep(2)

    print("--- FINISHED SUCCESSFULLY ---")

if __name__ == "__main__":
    main()
