import feedparser
import requests
import os
import re
import html
import time
import json
import base64
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
EARNKARO_TOKEN = os.environ.get("EARNKARO_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

SUBREDDITS = [
    "dealsforindia",
    "dealsoffersfreebies",
    "Lootdealsforindia",
]

MAX_POST_AGE = 7200                # 2 hours — skip posts older than this
SEEN_DEALS_EXPIRY = 48 * 60 * 60  # Remember deals for 48 hours
MAX_POSTS_PER_SUBREDDIT = 5        # Flood protection per run
SEEN_DEALS_FILE = "seen_deals.json"
GEMINI_TIMEOUT = 15                # Seconds before Gemini call gives up
RSS_TIMEOUT = 10                   # Seconds before RSS fetch gives up
AFFILIATE_TIMEOUT = 5              # Seconds before EarnKaro call gives up
TELEGRAM_TIMEOUT = 10              # Seconds before Telegram call gives up

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}

# --- GEMINI RATE LIMIT TRACKING ---
# Gemini free tier: 15 requests/minute
gemini_call_times = []

def can_call_gemini():
    """Returns True if we are under the 15 calls/minute Gemini rate limit."""
    global gemini_call_times
    now = time.time()
    # Keep only calls from the last 60 seconds
    gemini_call_times = [t for t in gemini_call_times if now - t < 60]
    return len(gemini_call_times) < 14  # Stay safely under 15/min limit

def record_gemini_call():
    gemini_call_times.append(time.time())


# ─────────────────────────────────────────
# SEEN DEALS MEMORY
# ─────────────────────────────────────────

def load_seen_deals():
    """Loads seen deals, auto-repairs corrupted JSON."""
    try:
        with open(SEEN_DEALS_FILE, "r") as f:
            raw = f.read().strip()
            if not raw:
                return []
            data = json.loads(raw)
            if not isinstance(data, list):
                return []
    except (json.JSONDecodeError, FileNotFoundError):
        print("[MEMORY] seen_deals.json missing or corrupt — starting fresh")
        return []
    except Exception as e:
        print(f"[MEMORY ERROR] {e}")
        return []

    now = time.time()
    return [d for d in data if isinstance(d, dict) and now - d.get("timestamp", 0) < SEEN_DEALS_EXPIRY]

def save_seen_deals(deals):
    """Saves seen deals safely using a temp file to avoid corruption."""
    tmp_file = SEEN_DEALS_FILE + ".tmp"
    try:
        with open(tmp_file, "w") as f:
            json.dump(deals, f, indent=2)
        os.replace(tmp_file, SEEN_DEALS_FILE)  # Atomic replace
    except Exception as e:
        print(f"[SAVE ERROR] {e}")

def add_seen_deal(deals, url):
    deals.append({"url": url, "timestamp": time.time()})
    return deals


# ─────────────────────────────────────────
# URL HELPERS
# ─────────────────────────────────────────

def extract_product_links(content):
    """Extracts non-Reddit product URLs from HTML content."""
    if not content:
        return []
    urls = re.findall(r'(https?://[^\s"<\]\)]+)', content)
    product_links = []
    skip_domains = ["reddit.com", "preview.redd.it", "redd.it", "reddituploads", "redditmedia"]
    for url in urls:
        if any(skip in url for skip in skip_domains):
            continue
        # Skip image URLs
        if re.search(r'\.(jpg|jpeg|png|gif|webp)(\?|$)', url, re.IGNORECASE):
            continue
        product_links.append(url)
    return list(dict.fromkeys(product_links))

def normalize_url(url):
    """Strips tracking params for clean comparison."""
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
    """Fast URL-based duplicate check — no API call needed."""
    norm_new = normalize_url(primary_link)
    for deal in seen_deals:
        if normalize_url(deal.get("url", "")) == norm_new:
            return True
    return False


# ─────────────────────────────────────────
# IMAGE FETCHER
# ─────────────────────────────────────────

def fetch_image_base64(image_url):
    """Downloads image and returns base64. Returns None on any failure."""
    if not image_url:
        return None, None
    try:
        r = requests.get(image_url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            # Skip if image is too large (>3MB — Gemini limit)
            if len(r.content) > 3 * 1024 * 1024:
                print(f"[IMAGE] Too large, skipping vision")
                return None, None
            content_type = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            # Only accept image types
            if not content_type.startswith("image/"):
                return None, None
            encoded = base64.b64encode(r.content).decode("utf-8")
            return encoded, content_type
    except Exception as e:
        print(f"[IMAGE FETCH ERROR] {e}")
    return None, None


# ─────────────────────────────────────────
# EARNKARO AFFILIATE LINK
# ─────────────────────────────────────────

def get_earnkaro_link(deal_url):
    """Converts to affiliate link. Always returns a valid URL."""
    if not EARNKARO_TOKEN or not deal_url:
        return deal_url
    # Only convert known supported domains
    supported = ["amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa",
                 "snapdeal", "tatacliq", "croma", "reliancedigital", "vijaysales"]
    if not any(s in deal_url.lower() for s in supported):
        return deal_url
    try:
        api_url = "https://ekaro-api.affiliaters.in/api/converter/public"
        headers = {"Authorization": f"Bearer {EARNKARO_TOKEN}", "Content-Type": "application/json"}
        payload = {"deal": deal_url, "convert_option": "convert_only"}
        r = requests.post(api_url, headers=headers, json=payload, timeout=AFFILIATE_TIMEOUT)
        if r.status_code == 200 and r.json().get("success") == 1:
            data = r.json().get("data")
            if data and "We could not locate" not in str(data):
                return data
    except Exception as e:
        print(f"[EARNKARO ERROR] {e}")
    return deal_url


# ─────────────────────────────────────────
# GEMINI SMART PROCESSOR
# ─────────────────────────────────────────

def process_with_gemini(title, body, image_url, product_links, seen_deals):
    """
    Single Gemini call per post:
    - Validates it's a real deal
    - Checks duplicates
    - Extracts price/discount (even from images)
    - Rewrites message in affiliate style
    - Detects urgency
    - Suggests category hashtags
    """
    if not GEMINI_API_KEY:
        return fallback_process(title, body)

    if not can_call_gemini():
        print("[GEMINI] Rate limit reached, using fallback")
        return fallback_process(title, body)

    seen_urls = [d["url"] for d in seen_deals[-50:]]
    seen_list_str = "\n".join(f"- {u}" for u in seen_urls) if seen_urls else "None"
    primary_link = product_links[0] if product_links else "No link"

    prompt = f"""You are an expert Indian deals affiliate marketer. Analyze this Reddit deal post and respond ONLY in valid JSON.

POST TITLE: {title}
POST BODY: {body[:1000] if body else "No body text"}
PRODUCT LINK: {primary_link}

SEEN DEALS (last 48 hours) - check for duplicates:
{seen_list_str}

Your tasks:
1. Decide if this is a REAL deal (product discount, offer, freebie) or NOT (discussion, question, news, meme, rant)
2. If real deal, check if DUPLICATE against seen deals list (same product/offer)
3. Extract: product name, price (₹), discount percentage or amount
4. Detect if LIMITED TIME (flash sale, today only, limited stock, ends tonight etc.)
5. Rewrite as a punchy 2-3 line affiliate message in English. Be exciting. Mention product, price, discount. No fluff.
6. Suggest 2-3 relevant category hashtags from: #Amazon #Flipkart #Myntra #Ajio #Meesho #Nykaa #Swiggy #Zomato #Zepto #Blinkit #boAt #Samsung #Apple #Xiaomi #Realme #OnePlus #JBL #Laptop #Mobile #TV #Headphones #Earbuds #Fashion #Food #Grocery #Recharge #Cashback #Free #Loot #Electronics #HomeAppliance

Respond ONLY with this JSON (no markdown, no backticks):
{{
  "is_deal": true or false,
  "is_duplicate": true or false,
  "product_name": "short product name",
  "price": "₹999" or null,
  "discount": "60% off" or "₹500 off" or null,
  "is_limited_time": true or false,
  "rewritten_message": "your 2-3 line rewritten deal message",
  "category_tags": "#Amazon #Electronics"
}}"""

    parts = [{"text": prompt}]

    # Add image for vision analysis (image-only posts)
    if image_url:
        img_data, mime_type = fetch_image_base64(image_url)
        if img_data:
            parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": img_data
                }
            })
            parts.append({"text": "Also analyze the image above to extract any deal details, prices, or product information not visible in the text."})

    try:
        record_gemini_call()
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.3}
        }
        r = requests.post(api_url, json=payload, timeout=GEMINI_TIMEOUT)

        # Handle Gemini rate limit (429)
        if r.status_code == 429:
            print("[GEMINI] 429 rate limited — waiting 30s then fallback")
            time.sleep(30)
            return fallback_process(title, body)

        if r.status_code == 200:
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Strip markdown code fences if present
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            raw = raw.strip()
            result = json.loads(raw)
            # Validate result has required fields
            if not isinstance(result, dict):
                raise ValueError("Gemini returned non-dict")
            return result
        else:
            print(f"[GEMINI ERROR] Status {r.status_code}: {r.text[:200]}")

    except json.JSONDecodeError as e:
        print(f"[GEMINI JSON ERROR] Could not parse response: {e}")
    except Exception as e:
        print(f"[GEMINI ERROR] {e}")

    return fallback_process(title, body)


def fallback_process(title, body):
    """Used when Gemini is unavailable — still posts the deal."""
    return {
        "is_deal": True,
        "is_duplicate": False,
        "product_name": title,
        "price": None,
        "discount": None,
        "is_limited_time": False,
        "rewritten_message": body[:300] if body else "",
        "category_tags": ""
    }


# ─────────────────────────────────────────
# HTML CLEANER
# ─────────────────────────────────────────

def clean_html_text(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = html.unescape(cleantext).strip()
    if "submitted by" in cleantext:
        cleantext = cleantext.split("submitted by")[0].strip()
    return cleantext


# ─────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────

def send_telegram(caption, buy_url=None, image_url=None):
    """Sends to Telegram with inline Buy Now button. Multiple fallbacks."""
    if not caption:
        return
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    # Sanitize caption — remove any invalid HTML tags Telegram doesn't support
    allowed_tags = ['b', 'i', 'u', 's', 'a', 'code', 'pre']
    caption = re.sub(r'<(?!/?({})\b)[^>]*>'.format('|'.join(allowed_tags)), '', caption)

    reply_markup = None
    if buy_url:
        # Validate URL before putting in button
        if buy_url.startswith("http"):
            reply_markup = json.dumps({
                "inline_keyboard": [[
                    {"text": "🛒 Buy Now", "url": buy_url}
                ]]
            })

    data = {"chat_id": CHANNEL_ID, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup

    # Try sending as photo first
    if image_url:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            photo_data = dict(data)
            photo_data["caption"] = caption
            photo_data["photo"] = image_url
            r = requests.post(url, data=photo_data, timeout=TELEGRAM_TIMEOUT)
            if r.status_code == 200:
                return
            print(f"[TELEGRAM PHOTO ERROR] {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[TELEGRAM PHOTO EXCEPTION] {e}")

    # Fallback: send as text message
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        msg_data = dict(data)
        msg_data["text"] = caption
        r = requests.post(url, data=msg_data, timeout=TELEGRAM_TIMEOUT)
        if r.status_code != 200:
            print(f"[TELEGRAM MSG ERROR] {r.status_code}: {r.text[:200]}")
            # Last resort: try without HTML parsing
            msg_data["parse_mode"] = ""
            msg_data["text"] = re.sub(r'<[^>]+>', '', caption)  # strip all HTML
            requests.post(url, data=msg_data, timeout=TELEGRAM_TIMEOUT)
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")


# ─────────────────────────────────────────
# PER SUBREDDIT PROCESSOR
# ─────────────────────────────────────────

def fetch_rss(subreddit):
    """Fetches RSS with fallback for capitalisation issues."""
    urls_to_try = [
        f"https://www.reddit.com/r/{subreddit}/new/.rss",
        f"https://www.reddit.com/r/{subreddit.lower()}/new/.rss",
        f"https://www.reddit.com/r/{subreddit.upper()}/new/.rss",
    ]
    for url in urls_to_try:
        try:
            r = requests.get(url, headers=HEADERS, timeout=RSS_TIMEOUT)
            if r.status_code == 200:
                feed = feedparser.parse(r.content)
                if feed.entries:
                    print(f"[RSS OK] {subreddit} — {len(feed.entries)} entries")
                    return feed
                else:
                    print(f"[RSS EMPTY] {url}")
            else:
                print(f"[RSS {r.status_code}] {url}")
        except Exception as e:
            print(f"[RSS ERROR] {url}: {e}")
    return None


def process_subreddit(subreddit, seen_deals):
    memory_file = f"last_post_{subreddit}.txt"

    try:
        with open(memory_file, "r") as f:
            last_id = f.read().strip()
    except:
        last_id = None

    feed = fetch_rss(subreddit)
    if not feed:
        return seen_deals

    # Collect unseen posts
    new_posts = []
    for entry in feed.entries:
        if entry.id == last_id:
            break
        new_posts.append(entry)

    if not new_posts:
        print(f"[{subreddit}] No new posts")
        return seen_deals

    print(f"[{subreddit}] {len(new_posts)} new posts to process")

    posts_sent = 0

    for entry in reversed(new_posts):

        # Always update memory even if we skip
        entry_id = entry.id

        # --- FLOOD PROTECTION ---
        if posts_sent >= MAX_POSTS_PER_SUBREDDIT:
            print(f"[FLOOD LIMIT] {subreddit}: hit {MAX_POSTS_PER_SUBREDDIT} limit this run")
            with open(memory_file, "w") as f: f.write(entry_id)
            break

        # --- TIME FILTER ---
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            try:
                post_timestamp = time.mktime(entry.published_parsed)
                if time.time() - post_timestamp > MAX_POST_AGE:
                    print(f"[STALE] {entry.title[:50]}")
                    with open(memory_file, "w") as f: f.write(entry_id)
                    continue
            except Exception as e:
                print(f"[TIME PARSE ERROR] {e}")

        title = getattr(entry, 'title', '').strip()
        if not title:
            with open(memory_file, "w") as f: f.write(entry_id)
            continue

        # --- GET CONTENT ---
        content = ""
        try:
            if hasattr(entry, 'content') and entry.content:
                content = entry.content[0].value
            elif hasattr(entry, 'summary'):
                content = entry.summary
        except:
            pass

        body = clean_html_text(content)

        # --- PRODUCT LINKS ---
        product_links = extract_product_links(content)
        try:
            if hasattr(entry, 'link') and entry.link and "reddit.com" not in entry.link:
                product_links.insert(0, entry.link)
        except:
            pass

        # --- IMAGE ---
        image_url = None
        try:
            if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0].get('url')
            elif hasattr(entry, 'media_content') and entry.media_content:
                image_url = entry.media_content[0].get('url')
            if not image_url and content:
                match = re.search(r'<img[^>]+src="([^">]+)"', content)
                if match:
                    temp_url = match.group(1)
                    if temp_url.startswith('http'):
                        image_url = temp_url
        except:
            pass

        # --- FAST URL DUPLICATE CHECK ---
        if product_links and is_duplicate_url(product_links[0], seen_deals):
            print(f"[URL DUPLICATE] {title[:60]}")
            with open(memory_file, "w") as f: f.write(entry_id)
            continue

        # --- GEMINI CALL ---
        result = process_with_gemini(title, body, image_url, product_links, seen_deals)

        if not result:
            result = fallback_process(title, body)

        if not result.get("is_deal", True):
            print(f"[NOT A DEAL] {title[:60]}")
            with open(memory_file, "w") as f: f.write(entry_id)
            continue

        if result.get("is_duplicate", False):
            print(f"[AI DUPLICATE] {title[:60]}")
            with open(memory_file, "w") as f: f.write(entry_id)
            continue

        # --- BUILD CAPTION ---
        lines = []
        lines.append(f"🔥 <b>{title}</b>")

        price = result.get("price")
        discount = result.get("discount")
        if price or discount:
            pd_parts = []
            if price: pd_parts.append(f"💰 {price}")
            if discount: pd_parts.append(f"🏷️ {discount}")
            lines.append("  |  ".join(pd_parts))

        if result.get("is_limited_time"):
            lines.append("⏰ <b>Limited Time Deal!</b>")

        rewritten = result.get("rewritten_message", "").strip()
        if rewritten:
            lines.append(rewritten)

        category_tags = result.get("category_tags", "")
        subreddit_tag = f"#{subreddit}"
        lines.append(f"#Deal #Loot {category_tags} {subreddit_tag}".strip())

        caption = "\n\n".join(lines)

        # --- AFFILIATE LINK ---
        buy_url = None
        if product_links:
            buy_url = get_earnkaro_link(product_links[0])

        # --- SEND ---
        send_telegram(caption, buy_url=buy_url, image_url=image_url)
        posts_sent += 1
        print(f"[POSTED] {title[:60]}")

        # --- SAVE IMMEDIATELY after posting ---
        if product_links:
            seen_deals = add_seen_deal(seen_deals, product_links[0])
        save_seen_deals(seen_deals)
        with open(memory_file, "w") as f: f.write(entry_id)

        time.sleep(3)

    return seen_deals


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    # Validate required env vars
    if not BOT_TOKEN:
        print("[FATAL] BOT_TOKEN not set")
        return
    if not CHANNEL_ID:
        print("[FATAL] CHANNEL_ID not set")
        return

    seen_deals = load_seen_deals()
    print(f"[START] Loaded {len(seen_deals)} seen deals")

    for subreddit in SUBREDDITS:
        print(f"\n[PROCESSING] r/{subreddit}")
        try:
            seen_deals = process_subreddit(subreddit, seen_deals)
        except Exception as e:
            print(f"[SUBREDDIT ERROR] r/{subreddit} crashed: {e}")
            continue  # Don't let one subreddit crash kill the others
        time.sleep(3)

    print("\n[DONE]")

if __name__ == "__main__":
    main()
