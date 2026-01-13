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

# --- AI CONFIGURATION ---
# We use a try-except block to prevent crashing if keys are wrong
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
    except:
        model = None
else:
    model = None

def is_spam_keywords(text):
    """
    Step 1: FAST filter. If these words exist, delete the post immediately.
    """
    text = text.lower()
    bad_words = [
        "referral", "referal", "invite code", "help me", "question", 
        "suggestion", "review needed", "any coupon", "coupon code for",
        "looking for", "does anyone", "is this legit", "fake", "scam"
    ]
    for word in bad_words:
        if word in text:
            return True
    return False

def ai_rewrite_and_filter(title, body):
    """
    Step 2: AI filter. Rewrites title if good, returns None if spam.
    """
    if not model:
        return title, body # If AI is broken, just post original (fallback)

    try:
        prompt = f"""
        You are a strict Deal Bot. Analyze this Reddit post.
        Title: {title}
        Body: {body}

        1. IS THIS SPAM? (Spam = Referral codes, Questions, Rants, Discussion).
           IF YES -> Reply "SKIP"
        
        2. IF REAL DEAL -> Rewrite the Title to be catchy (Max 10 words, 1 Emoji).
           Reply format: "Title: [Your New Title]"
        """
        
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        if "SKIP" in text:
            return None, None
            
        if "Title:" in text:
            new_title = text.split("Title:")[1].strip()
            return new_title, body
            
        return title, body # Fallback
        
    except Exception as e:
        print(f"AI Error: {e}")
        return title, body

def get_earnkaro_link(deal_url):
    """Your original money-making link converter."""
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
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = html.unescape(cleantext).strip()
    if "submitted by" in cleantext:
        cleantext = cleantext.split("submitted by")[0].strip()
    return cleantext

def extract_links(text):
    return re.findall(r'(https?://[^\s"<\]\)]+)', text)

def send_telegram(caption, image_url=None):
    # Shorten caption if too long
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
    try:
        with open("last_post.txt", "r") as f: last_id = f.read().strip()
    except: last_id = None

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

    for entry in reversed(new_posts):
        print(f"Checking: {entry.title}")
        title = entry.title.strip()
        
        content = ""
        if hasattr(entry, 'content'): content = entry.content[0].value
        elif hasattr(entry, 'summary'): content = entry.summary
        
        image_url = None
        if hasattr(entry, 'media_thumbnail'):
             image_url = entry.media_thumbnail[0]['url']
        elif '<img src="' in content:
             match = re.search(r'<img src="(.*?)"', content)
             if match: image_url = match.group(1)

        clean_body = clean_html(content)
        
        # --- FILTER 1: Keyword Check (Fast) ---
        if is_spam_keywords(title) or is_spam_keywords(clean_body):
            print("Skipped: Keyword Filter (Referral/Question)")
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue
            
        # --- FILTER 2: AI Check (Smart) ---
        ai_title, ai_body = ai_rewrite_and_filter(title, clean_body)
        
        if ai_title is None:
            print("Skipped: AI Filter (Not a deal)")
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue

        # --- PROCESS LINKS ---
        raw_links = extract_links(clean_body)
        if not raw_links and hasattr(entry, 'link'):
            raw_links = [entry.link]
            
        converted_links = []
        seen_links = set()
        
        for url in raw_links:
            if "reddit.com" in url or "preview" in url: continue
            if url in seen_links: continue
            
            new_link = get_earnkaro_link(url)
            converted_links.append(new_link)
            seen_links.add(url)

        # --- SEND ---
        caption = f"🔥 <b>{ai_title}</b>\n\n"
        
        if converted_links:
            caption += "<b>👇 Grab Deal:</b>\n"
            for link in converted_links:
                caption += f"➜ <a href='{link}'>Click Here to Buy</a>\n"
        else:
             # Fallback link
             main_link = get_earnkaro_link(entry.link)
             caption += f"➜ <a href='{main_link}'>Click Here to Buy</a>\n"
        
        caption += "\n#Deal #Loot"
        
        send_telegram(caption, image_url)
        print(f"Posted: {ai_title}")
        
        with open("last_post.txt", "w") as f: f.write(entry.id)
        time.sleep(2)

if __name__ == "__main__":
    main()
