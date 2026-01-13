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

# --- AI SETUP ---
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

def ai_spam_check(title, body):
    """
    Returns (True, None, None) if SPAM.
    Returns (False, NewTitle, NewSummary) if VALID DEAL.
    """
    if not GOOGLE_API_KEY:
        return False, title, body # No AI? Process everything (unsafe mode)

    try:
        # STRICT PROMPT to kill spam
        prompt = f"""
        Analyze this Reddit post for a Deal Channel.
        
        Input Title: {title}
        Input Body: {body}

        1. IS THIS SPAM? 
           (Spam = Referral codes, "Help me find", "Is this legit?", Questions, Rants, Discussion, "Coupon needed").
           IF YES -> Reply ONLY "SKIP".
        
        2. IF IT IS A REAL DEAL:
           Reply in this format:
           Title: [Catchy Title with 1 Emoji]
           Summary: [Short summary under 15 words]
        """
        
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        if "SKIP" in text or "Referral" in text or "referral" in text:
            return True, None, None
            
        # Parse the AI Rewrite
        lines = [l for l in text.split('\n') if l.strip()]
        new_title = title
        new_summary = body
        
        for line in lines:
            if line.startswith("Title:"):
                new_title = line.replace("Title:", "").strip()
            elif line.startswith("Summary:"):
                new_summary = line.replace("Summary:", "").strip()
                
        return False, new_title, new_summary

    except Exception:
        return False, title, body # On error, allow post

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

def extract_links(text):
    """Finds all links in the text."""
    return re.findall(r'(https?://[^\s"<\]\)]+)', text)

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
        
        # Image extraction
        image_url = None
        if hasattr(entry, 'media_thumbnail'):
             image_url = entry.media_thumbnail[0]['url']
        elif '<img src="' in content:
             match = re.search(r'<img src="(.*?)"', content)
             if match: image_url = match.group(1)

        clean_body = clean_html(content)
        
        # 1. AI CHECK (SPAM FILTER)
        # This will filter out referral codes, questions, and non-deals
        is_spam, ai_title, ai_summary = ai_spam_check(title, clean_body)
        
        if is_spam:
            print("Skipped: Detected as Spam/Referral/Question")
            # Mark as read so we don't check it again
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue

        # 2. LINK PROCESSING (On original body to ensure we catch the real link)
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

        # 3. BUILD CAPTION
        caption = f"🔥 <b>{ai_title}</b>\n\n{ai_summary}\n\n"
        
        if converted_links:
            caption += "<b>👇 Grab Deal:</b>\n"
            for link in converted_links:
                caption += f"➜ <a href='{link}'>Click Here to Buy</a>\n"
        
        caption += "\n#Deal #Loot"
        
        send_telegram(caption, image_url)
        print(f"Posted: {ai_title}")
        
        with open("last_post.txt", "w") as f: f.write(entry.id)
        time.sleep(2)

if __name__ == "__main__":
    main()
