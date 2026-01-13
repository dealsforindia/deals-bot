import feedparser
import requests
import os
import re
import html
import time
import google.generativeai as genai

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
EARNKARO_TOKEN = os.environ.get("EARNKARO_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
SUBREDDIT = "dealsforindia"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}

# --- CONFIGURE AI ---
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
    except Exception as e:
        print(f"AI Setup Error: {e}")

def is_junk_hard_filter(title, body):
    """
    LAYER 1: HARD BLOCK
    Instantly blocks posts containing specific 'bad' words without waiting for AI.
    """
    text = (title + " " + body).lower()
    
    # If ANY of these words appear, the post is DELETED.
    bad_keywords = [
        "not working", "didn't work", "expired", "fake", "scam",
        "help me", "question", "suggestion", "request",
        "fuck", "shit", "stupid", "worst", "don't buy",
        "referral code", "refer code", # Blocks referral spam
        "codes are t working", # Specifically for the post you hated
        "refer_bot", # Blocks telegram bot spam links
        "parakeet ai" # Blocks the specific spam in your screenshot
    ]
    
    for word in bad_keywords:
        if word in text:
            print(f"🚫 Hard Blocked (Keyword: '{word}'): {title}")
            return True # It IS junk
    
    return False # Not junk (passed layer 1)

def is_valid_deal_ai(title, body):
    """
    LAYER 2: AI CHECK
    Uses Gemini to analyze context if the Hard Filter didn't catch it.
    """
    if not GOOGLE_API_KEY:
        print("⚠️ No API Key. Skipping AI.")
        return True 

    prompt = f"""
    You are a moderator for a Shopping Deals Telegram channel.
    Analyze this Reddit post.

    Title: {title}
    Body: {body}

    Answer "NO" if the post is:
    1. A complaint or rant (e.g., "Flipkart cheated me").
    2. A discussion or question (e.g., "Is this good?").
    3. Saying a code does NOT work.
    4. Just a referral code spam.

    Answer "YES" ONLY if it is a working DEAL, LOOT, or DISCOUNT.
    
    Reply ONLY with "YES" or "NO".
    """
    
    try:
        # FIXED: Changed model name to fix the 404 error
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        answer = response.text.strip().upper()
        
        if "NO" in answer:
            print(f"🤖 AI Blocked: {title}")
            return False
        
        print(f"✅ AI Approved: {title}")
        return True
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        # If AI fails, we allow it (fail-open) BUT the Hard Filter has already run
        return True

def get_earnkaro_link(deal_url):
    """Converts link to EarnKaro affiliate link."""
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
    """Removes HTML tags."""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = html.unescape(cleantext).strip()
    if "submitted by" in cleantext:
        cleantext = cleantext.split("submitted by")[0].strip()
    return cleantext

def process_text_links(text):
    """Finds URLs and converts them."""
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

def main():
    # 1. Read Memory
    try:
        with open("last_post.txt", "r") as f: last_id = f.read().strip()
    except: last_id = None

    # 2. Get Reddit Data
    rss_url = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
    try:
        r = requests.get(rss_url, headers=HEADERS)
        if r.status_code != 200: return
        feed = feedparser.parse(r.content)
    except: return

    new_posts = []
    for entry in feed.entries:
        if entry.id == last_id: break
        new_posts.append(entry)

    if not new_posts: return

    # 3. Process Posts
    for entry in reversed(new_posts):
        title = entry.title.strip()
        
        content = ""
        if hasattr(entry, 'content'): content = entry.content[0].value
        elif hasattr(entry, 'summary'): content = entry.summary
        
        clean_body = clean_html(content)
        
        # --- LAYER 1: HARD KEYWORD FILTER ---
        # This will catch 'BB codes not working' instantly.
        if is_junk_hard_filter(title, clean_body):
            # Blocked by keywords
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue

        # --- LAYER 2: AI FILTER ---
        if not is_valid_deal_ai(title, clean_body):
            # Blocked by AI
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue 

        # --- PREPARE & SEND ---
        image_url = None
        if hasattr(entry, 'media_thumbnail'):
             image_url = entry.media_thumbnail[0]['url']
        elif '<img src="' in content:
             match = re.search(r'<img src="(.*?)"', content)
             if match: image_url = match.group(1)

        final_body = process_text_links(clean_body)
        
        if final_body.lower().startswith(title.lower()):
            final_body = final_body[len(title):].strip()
            final_body = final_body.lstrip(" :-")

        caption = f"🔥 <b>{title}</b>\n\n{final_body}\n\n#Deal #Loot"
        
        send_telegram(caption, image_url)
        
        with open("last_post.txt", "w") as f: f.write(entry.id)
        time.sleep(2)

if __name__ == "__main__":
    main()
