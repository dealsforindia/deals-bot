import os
import re
import html
import time
import json
import hashlib
import requests
import feedparser
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# --- CONFIGURATION & SECRETS ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
EARNKARO_TOKEN = os.environ.get("EARNKARO_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FIREBASE_URL = os.environ.get("FIREBASE_URL")
FIREBASE_SECRET = os.environ.get("FIREBASE_SECRET")

SUBREDDITS = ["dealsforindia", "dealsoffersfreebies", "Lootdealsforindia"]
MAX_POST_AGE = 7200                # 2 hours
MAX_POSTS_PER_SUBREDDIT = 5        # Flood protection
GEMINI_TIMEOUT = 15
RSS_TIMEOUT = 10
TELEGRAM_TIMEOUT = 10

# Compliant API Header to prevent Reddit IP bans
HEADERS = {
    "User-Agent": "Python:DealsForIndiaTracker:v2.1 (by Deals For India)"
}

# --- GEMINI RATE LIMIT TRACKER ---
gemini_call_times = []

def can_call_gemini():
    global gemini_call_times
    now = time.time()
    gemini_call_times = [t for t in gemini_call_times if now - t < 60]
    return len(gemini_call_times) < 14 

def record_gemini_call():
    gemini_call_times.append(time.time())

# --- FIREBASE GLOBAL MEMORY ---
def _get_url_hash(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()

def is_duplicate_url(url):
    if not FIREBASE_URL or not FIREBASE_SECRET: return False
    url_hash = _get_url_hash(url)
    endpoint = f"{FIREBASE_URL.rstrip('/')}/seen_deals/{url_hash}.json?auth={FIREBASE_SECRET}"
    try:
        r = requests.get(endpoint, timeout=5)
        if r.status_code == 200 and r.json():
            data = r.json()
            if time.time() - data.get("timestamp", 0) < (48 * 60 * 60):
                return True
    except: pass
    return False

def save_seen_deal(url):
    if not FIREBASE_URL or not FIREBASE_SECRET: return
    url_hash = _get_url_hash(url)
    endpoint = f"{FIREBASE_URL.rstrip('/')}/seen_deals/{url_hash}.json?auth={FIREBASE_SECRET}"
    try:
        requests.put(endpoint, json={"url": url, "timestamp": time.time()}, timeout=5)
    except: pass

def get_last_post(subreddit):
    if not FIREBASE_URL or not FIREBASE_SECRET: return None
    endpoint = f"{FIREBASE_URL.rstrip('/')}/last_posts/{subreddit}.json?auth={FIREBASE_SECRET}"
    try:
        r = requests.get(endpoint, timeout=5)
        if r.status_code == 200 and r.json():
            return r.json().get("last_id")
    except: pass
    return None

def set_last_post(subreddit, post_id):
    if not FIREBASE_URL or not FIREBASE_SECRET: return
    endpoint = f"{FIREBASE_URL.rstrip('/')}/last_posts/{subreddit}.json?auth={FIREBASE_SECRET}"
    try:
        requests.put(endpoint, json={"last_id": post_id}, timeout=5)
    except: pass

# --- PARSING HELPERS ---
def extract_product_links(content):
    if not content: return []
    urls = re.findall(r'(https?://[^\s"<\]\)]+)', content)
    valid_links = []
    skip_domains = ["reddit.com", "preview.redd.it", "redd.it"]
    for url in urls:
        if any(skip in url for skip in skip_domains): continue
        if re.search(r'\.(jpg|jpeg|png|gif|webp)(\?|$)', url, re.IGNORECASE): continue
        valid_links.append(url)
    return list(dict.fromkeys(valid_links))

def clean_html_text(raw_html):
    if not raw_html: return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return html.unescape(cleantext).strip().split("submitted by")[0].strip()

# --- THE COMMENT SCRAPER ---
def get_link_from_comments(subreddit, post_id):
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/.json?limit=10&sort=top"
    try:
        time.sleep(2)
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200: return []
        data = r.json()
        if len(data) < 2: return []
        
        for child in data[1].get("data", {}).get("children", []):
            comment_body = child.get("data", {}).get("body", "")
            if not comment_body or comment_body in ("[deleted]", "[removed]"): continue
            
            found_links = extract_product_links(comment_body)
            if found_links:
                print(f"[COMMENTS] Found link in comments for post {post_id}")
                return found_links
    except Exception as e:
        print(f"[COMMENT FETCH ERROR] {e}")
    return []

# --- EXTERNAL APIS ---
def get_earnkaro_link(deal_url):
    if not EARNKARO_TOKEN or not deal_url: return deal_url
    supported = ["amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa", "snapdeal", "croma", "tatacliq", "vijaysales"]
    if not any(s in deal_url.lower() for s in supported): return deal_url
    try:
        api_url = "https://ekaro-api.affiliaters.in/api/converter/public"
        headers = {"Authorization": f"Bearer {EARNKARO_TOKEN}", "Content-Type": "application/json"}
        r = requests.post(api_url, headers=headers, json={"deal": deal_url, "convert_option": "convert_only"}, timeout=8)
        
        if r.status_code == 200:
            resp = r.json()
            if resp.get("success") == 1:
                data = resp.get("data")
                if data and "We could not locate" not in str(data): return data
            else:
                print(f"[EARNKARO REJECTED] {resp}")
        else:
            print(f"[EARNKARO API DOWN] HTTP {r.status_code}")
    except Exception as e: 
        print(f"[EARNKARO ERROR] {e}")
    return deal_url

def process_with_gemini(title, body, product_links):
    if not GEMINI_API_KEY or not can_call_gemini():
        return {"is_deal": True, "is_duplicate": False, "product_name": title, "price": None, "discount": None, "is_limited_time": False, "rewritten_message": body[:300], "category_tags": ""}

    primary_link = product_links[0] if product_links else "No link"
    prompt = f"""You are an Indian deals affiliate marketer. Analyze this deal and respond ONLY in valid JSON.
    TITLE: {title}
    BODY: {body[:1000]}
    LINK: {primary_link}
    
    1. is_deal: true/false
    2. is_duplicate: false
    3. product_name: short name
    4. price: "₹999" or null
    5. discount: "60% off" or null
    6. is_limited_time: true/false
    7. rewritten_message: Exciting 2-line message mentioning product, price, discount. No fluff.
    8. category_tags: #Amazon #Flipkart etc.
    """
    
    try:
        record_gemini_call()
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 500, "temperature": 0.3}}
        r = requests.post(api_url, json=payload, timeout=GEMINI_TIMEOUT)
        
        if r.status_code == 429:
            print("[GEMINI] 429 rate limited — waiting 15s")
            time.sleep(15)
            return {"is_deal": True, "is_duplicate": False, "product_name": title, "price": None, "discount": None, "is_limited_time": False, "rewritten_message": body[:300], "category_tags": ""}
            
        if r.status_code == 200:
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            return json.loads(raw.strip())
    except Exception as e:
        print(f"[GEMINI ERROR] {e}")
        
    return {"is_deal": True, "is_duplicate": False, "product_name": title, "price": None, "discount": None, "is_limited_time": False, "rewritten_message": body[:300], "category_tags": ""}

def send_telegram(caption, buy_url=None, image_url=None):
    if not caption: return
    caption = re.sub(r'<(?!/?(b|i|u|s|a|code|pre)\b)[^>]*>', '', caption[:1020])
    
    data = {"chat_id": CHANNEL_ID, "parse_mode": "HTML"}
    if buy_url and buy_url.startswith("http"):
        data["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": "🛒 Buy Now", "url": buy_url}]]})
        
    # 1. Try sending Photo
    if image_url:
        try:
            photo_data = dict(data)
            photo_data["caption"] = caption
            photo_data["photo"] = image_url
            r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=photo_data, timeout=TELEGRAM_TIMEOUT)
            if r.status_code == 200: return # Success!
            print(f"[TELEGRAM PHOTO ERROR] {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[TELEGRAM PHOTO EXCEPTION] {e}")

    # 2. Fallback to Text Message if image fails or doesn't exist
    try:
        data["text"] = caption
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data, timeout=TELEGRAM_TIMEOUT)
        if r.status_code != 200:
            data["parse_mode"] = ""
            data["text"] = re.sub(r'<[^>]+>', '', caption)
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data, timeout=TELEGRAM_TIMEOUT)
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

# --- MAIN LOOP ---
def process_subreddit(subreddit):
    last_id = get_last_post(subreddit)
    feed = None
    
    for url in [f"https://www.reddit.com/r/{subreddit}/new/.rss", f"https://www.reddit.com/r/{subreddit.lower()}/new/.rss"]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=RSS_TIMEOUT)
            if r.status_code == 200:
                feed = feedparser.parse(r.content)
                break
        except: pass
        
    if not feed or not feed.entries: return
    
    new_posts = []
    for entry in feed.entries:
        if entry.id == last_id: break
        new_posts.append(entry)
        
    posts_sent = 0
    for entry in reversed(new_posts):
        entry_id = entry.id
        short_id = entry_id.split("_")[-1] if "_" in entry_id else entry_id
        
        if posts_sent >= MAX_POSTS_PER_SUBREDDIT:
            set_last_post(subreddit, entry_id)
            break
            
        title = getattr(entry, 'title', '').strip()
        content = getattr(entry, 'content', [{'value': ''}])[0].value if hasattr(entry, 'content') else getattr(entry, 'summary', '')
        body = clean_html_text(content)
        
        # --- IMAGE EXTRACTION (WITH URL CLEANER) ---
        image_url = None
        try:
            if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0].get('url')
            elif hasattr(entry, 'media_content') and entry.media_content:
                image_url = entry.media_content[0].get('url')
            
            if not image_url and content:
                match = re.search(r'<img[^>]+src="([^">]+)"', content)
                if match:
                    image_url = match.group(1)
            
            # The Fix: Decode URL &amp; so Telegram can process it properly
            if image_url:
                image_url = html.unescape(image_url)
                if not image_url.startswith("http"):
                    image_url = None
        except: pass

        # 1. Look for links in the main post
        product_links = extract_product_links(content)
        if hasattr(entry, 'link') and entry.link and "reddit.com" not in entry.link:
            product_links.insert(0, entry.link)
            
        # 2. If no links, legally scrape the comments!
        if not product_links:
            product_links = get_link_from_comments(subreddit, short_id)
            
        if not product_links:
            set_last_post(subreddit, entry_id)
            continue
            
        # 3. Check Firebase memory
        if is_duplicate_url(product_links[0]):
            print(f"[GLOBAL DUPLICATE] Skipped: {title[:40]}")
            set_last_post(subreddit, entry_id)
            continue
            
        # 4. Gemini Formatting
        result = process_with_gemini(title, body, product_links)
        if not result.get("is_deal", True) or result.get("is_duplicate", False):
            set_last_post(subreddit, entry_id)
            continue
            
        # 5. Build Caption & Convert Link
        lines = [f"🔥 <b>{title}</b>"]
        price, discount = result.get("price"), result.get("discount")
        if price or discount:
            lines.append("  |  ".join(filter(None, [f"💰 {price}" if price else None, f"🏷️ {discount}" if discount else None])))
        if result.get("is_limited_time"): lines.append("⏰ <b>Limited Time Deal!</b>")
        if result.get("rewritten_message"): lines.append(result.get("rewritten_message").strip())
        lines.append(f"#Deal #Loot {result.get('category_tags', '')} #{subreddit}".strip())
        
        buy_url = get_earnkaro_link(product_links[0])
        
        # 6. Send and Save to Firebase
        send_telegram("\n\n".join(lines), buy_url=buy_url, image_url=image_url)
        save_seen_deal(product_links[0])
        set_last_post(subreddit, entry_id)
        
        posts_sent += 1
        print(f"[POSTED] {title[:40]}")
        time.sleep(3)

def main():
    if not all([BOT_TOKEN, CHANNEL_ID]):
        print("[FATAL] Missing Telegram credentials")
        return
    for subreddit in SUBREDDITS:
        print(f"\n[PROCESSING] r/{subreddit}")
        process_subreddit(subreddit)
    print("\n[DONE]")

if __name__ == "__main__":
    main()
