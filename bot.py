import feedparser
import requests
import os
import re
import html
import time
import json
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
EARNKARO_TOKEN = os.environ.get("EARNKARO_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# All subreddits to monitor — each gets its own memory file
SUBREDDITS = [
    "dealsforindia",
    "dealsoffersfreebies",
    "lootdealsforindia",
]

# Maximum age of a post in seconds (2 hours)
MAX_POST_AGE = 7200

# How long to remember seen deals (48 hours in seconds)
SEEN_DEALS_EXPIRY = 48 * 60 * 60

SEEN_DEALS_FILE = "seen_deals.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}


# --- SEEN DEALS MEMORY ---

def load_seen_deals():
    """Loads seen deals from file, removing expired ones."""
    try:
        with open(SEEN_DEALS_FILE, "r") as f:
            data = json.load(f)
    except:
        return []
    now = time.time()
    return [d for d in data if now - d["timestamp"] < SEEN_DEALS_EXPIRY]

def save_seen_deals(deals):
    """Saves seen deals to file."""
    with open(SEEN_DEALS_FILE, "w") as f:
        json.dump(deals, f, indent=2)

def add_seen_deal(deals, url):
    """Adds a product URL to the seen list."""
    deals.append({"url": url, "timestamp": time.time()})
    return deals


# --- LINK EXTRACTION & NORMALIZATION ---

def extract_product_links(content):
    """Extracts all non-Reddit, non-image URLs from HTML content."""
    urls = re.findall(r'(https?://[^\s"<\]\)]+)', content)
    product_links = []
    for url in urls:
        if any(skip in url for skip in ["reddit.com", "preview.redd.it", "redd.it", "reddituploads"]):
            continue
        product_links.append(url)
    return list(dict.fromkeys(product_links))

def normalize_url(url):
    """Strips tracking parameters so same product with different affiliate tags still matches."""
    TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "tag", "linkCode", "linkId", "th", "smid", "psc",
        "ascsubtag", "subid", "affid", "aff_id", "clickid",
        "source", "sr", "keywords", "sprefix", "crid", "cv_ct_cx",
    }
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        clean_params = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
        clean_query = urlencode(clean_params, doseq=True)
        clean = parsed._replace(query=clean_query, fragment="")
        return urlunparse(clean).rstrip("/").lower()
    except:
        return url.lower().rstrip("/")

def is_duplicate_url(primary_link, seen_deals):
    """Returns True if the normalized URL was already seen in the last 48 hours."""
    norm_new = normalize_url(primary_link)
    for deal in seen_deals:
        if normalize_url(deal["url"]) == norm_new:
            print(f"[URL DUPLICATE SKIPPED] {primary_link}")
            return True
    return False


# --- GEMINI AI DUPLICATE DETECTION ---

def is_duplicate_ai(new_url, new_title, seen_deals):
    """
    Uses Gemini to smartly detect if a deal is a duplicate even if
    the URL is slightly different (e.g. different product variant pages
    for the same product, or shortened URLs).
    Falls back to URL matching if Gemini is unavailable.
    """
    if not seen_deals:
        return False

    # First do fast URL check — no API call needed
    if is_duplicate_url(new_url, seen_deals):
        return True

    # If URL didn't match, ask Gemini to check if it's the same product
    if not GEMINI_API_KEY:
        return False

    seen_urls = [d["url"] for d in seen_deals[-40:]]  # Last 40 to keep prompt small
    seen_list_str = "\n".join(f"- {u}" for u in seen_urls)

    prompt = f"""You are a duplicate deal detector for an Indian deals Telegram channel.

A new deal has arrived:
Title: "{new_title}"
URL: {new_url}

Here are product URLs posted in the last 48 hours:
{seen_list_str}

Is the new deal pointing to the same product as any URL in the list?
Consider duplicates if:
- Same product page, different tracking/affiliate parameters
- Same product, slightly different URL structure (e.g. /dp/ vs /gp/product/)
- Shortened or redirected URL for the same product

Reply with ONLY one word: YES or NO"""

    try:
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 5, "temperature": 0}
        }
        r = requests.post(api_url, json=payload, timeout=10)
        if r.status_code == 200:
            answer = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
            if answer.startswith("YES"):
                print(f"[AI DUPLICATE SKIPPED] {new_url}")
                return True
    except:
        pass

    return False


# --- CORE FUNCTIONS ---

def get_earnkaro_link(deal_url):
    """Converts link. Returns ORIGINAL link if conversion fails."""
    if not EARNKARO_TOKEN: return deal_url

    api_url = "https://ekaro-api.affiliaters.in/api/converter/public"
    headers = {"Authorization": f"Bearer {EARNKARO_TOKEN}", "Content-Type": "application/json"}
    payload = {"deal": deal_url, "convert_option": "convert_only"}

    try:
        r = requests.post(api_url, headers=headers, json=payload, timeout=5)
        if r.status_code == 200 and r.json().get("success") == 1:
            data = r.json().get("data")
            if "We could not locate" in str(data):
                return deal_url
            return data
    except: pass
    return deal_url

def clean_html(raw_html):
    """Removes HTML and junk text."""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = html.unescape(cleantext).strip()
    if "submitted by" in cleantext:
        cleantext = cleantext.split("submitted by")[0].strip()
    return cleantext

def process_text_links(text):
    """Finds links and replaces them with affiliate links."""
    urls = re.findall(r'(https?://[^\s"<\]\)]+)', text)
    unique_urls = sorted(set(urls), key=urls.index)
    final_text = text
    for url in unique_urls:
        if "reddit.com" in url or "preview" in url: continue
        new_link = get_earnkaro_link(url)
        if new_link != url:
            final_text = final_text.replace(url, new_link)
    return final_text

def send_telegram(caption, image_url=None):
    if len(caption) > 1000: caption = caption[:990] + "..."
    data = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}

    if image_url:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        data["photo"] = image_url
        r = requests.post(url, data=data)
        if r.status_code == 200: return

    if "photo" in data: del data["photo"]
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data["text"] = caption
    del data["caption"]
    requests.post(url, data=data)


# --- PER SUBREDDIT PROCESSOR ---

def process_subreddit(subreddit, seen_deals):
    """Fetches and processes new posts for a single subreddit."""
    memory_file = f"last_post_{subreddit}.txt"

    try:
        with open(memory_file, "r") as f: last_id = f.read().strip()
    except: last_id = None

    rss_url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
    try:
        r = requests.get(rss_url, headers=HEADERS)
        if r.status_code != 200: return seen_deals
        feed = feedparser.parse(r.content)
    except: return seen_deals

    new_posts = []
    for entry in feed.entries:
        if entry.id == last_id: break
        new_posts.append(entry)

    if not new_posts: return seen_deals

    for entry in reversed(new_posts):

        # --- SPAM PROTECTION (TIME FILTER) ---
        if hasattr(entry, 'published_parsed'):
            post_timestamp = time.mktime(entry.published_parsed)
            if time.time() - post_timestamp > MAX_POST_AGE:
                with open(memory_file, "w") as f: f.write(entry.id)
                continue
        # -------------------------------------

        title = entry.title.strip()

        content = ""
        if hasattr(entry, 'content'): content = entry.content[0].value
        elif hasattr(entry, 'summary'): content = entry.summary

        # --- AI + URL DUPLICATE CHECK ---
        product_links = extract_product_links(content)

        if hasattr(entry, 'link') and entry.link and "reddit.com" not in entry.link:
            product_links.insert(0, entry.link)

        if product_links:
            primary_link = product_links[0]
            if is_duplicate_ai(primary_link, title, seen_deals):
                with open(memory_file, "w") as f: f.write(entry.id)
                continue
        # --------------------------------

        clean_body = clean_html(content)

        # --- IMAGE FINDER ---
        image_url = None
        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            image_url = entry.media_thumbnail[0]['url']
        elif hasattr(entry, 'media_content') and entry.media_content:
            image_url = entry.media_content[0]['url']

        if not image_url and content:
            match = re.search(r'<img[^>]+src="([^">]+)"', content)
            if match:
                temp_url = match.group(1)
                if temp_url.startswith('http'):
                    image_url = temp_url

        final_body = process_text_links(clean_body)

        if final_body.lower().startswith(title.lower()):
            final_body = final_body[len(title):].strip()
            final_body = final_body.lstrip(" :-")

        caption = f"🔥 <b>{title}</b>\n\n{final_body}\n\n#Deal #Loot"

        send_telegram(caption, image_url)

        # Remember this deal's URL
        if product_links:
            seen_deals = add_seen_deal(seen_deals, product_links[0])

        with open(memory_file, "w") as f: f.write(entry.id)
        time.sleep(2)

    return seen_deals


# --- MAIN ---

def main():
    seen_deals = load_seen_deals()

    for subreddit in SUBREDDITS:
        seen_deals = process_subreddit(subreddit, seen_deals)
        time.sleep(3)

    save_seen_deals(seen_deals)

if __name__ == "__main__":
    main()
