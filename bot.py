import feedparser
import requests
import os
import re
import html
import time

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
EARNKARO_TOKEN = os.environ.get("EARNKARO_TOKEN")
SUBREDDIT = "dealsforindia"

# --- HEADERS ---
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}

def get_earnkaro_link(deal_url):
    """Converts link to EarnKaro."""
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

def clean_html(raw_html):
    """Removes HTML tags from RSS content to leave just text."""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return html.unescape(cleantext).strip()

def process_text_links(text):
    """Finds links in the text and replaces them."""
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
    
    # Try sending with image first
    if image_url:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        data["photo"] = image_url
        r = requests.post(url, data=data)
        if r.status_code == 200: return # Success
    
    # Fallback to text only if image fails or no image
    if "photo" in data: del data["photo"]
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data["text"] = caption
    del data["caption"]
    requests.post(url, data=data)

def main():
    print("--- STARTING BOT (RSS MODE) ---")
    
    # 1. Read Memory
    try:
        with open("last_post.txt", "r") as f: last_id = f.read().strip()
        print(f"1. Last ID: {last_id}")
    except: last_id = None

    # 2. Fetch RSS (Bypasses the JSON Block)
    rss_url = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
    print(f"2. Fetching RSS: {rss_url}")
    
    # We use requests to get the content with headers, then feedparser to read it
    try:
        r = requests.get(rss_url, headers=HEADERS)
        if r.status_code != 200:
            print(f"   Error: Reddit RSS returned {r.status_code}")
            return
        feed = feedparser.parse(r.content)
    except Exception as e:
        print(f"   Error fetching feed: {e}")
        return

    print(f"3. Found {len(feed.entries)} posts.")
    
    new_posts = []
    for entry in feed.entries:
        if entry.id == last_id: break
        new_posts.append(entry)

    if not new_posts:
        print("   No new posts.")
        return

    # 3. Process & Send
    print(f"4. Sending {len(new_posts)} posts...")
    for entry in reversed(new_posts):
        title = entry.title
        print(f"   Processing: {title}")
        
        # Get Body Content
        content = ""
        if hasattr(entry, 'content'): content = entry.content[0].value
        elif hasattr(entry, 'summary'): content = entry.summary
        
        # Extract Image from HTML content or metadata
        image_url = None
        if hasattr(entry, 'media_thumbnail'):
             image_url = entry.media_thumbnail[0]['url']
        elif '<img src="' in content:
             match = re.search(r'<img src="(.*?)"', content)
             if match: image_url = match.group(1)

        # Clean text and swap links
        clean_body = clean_html(content)
        final_body = process_text_links(clean_body)
        
        caption = f"<b>{title}</b>\n\n{final_body}\n\n#Deal #Loot"
        
        send_telegram(caption, image_url)
        
        # Save memory
        with open("last_post.txt", "w") as f: f.write(entry.id)
        time.sleep(2)

    print("--- SUCCESS ---")

if __name__ == "__main__":
    main()
