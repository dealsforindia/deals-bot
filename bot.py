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

# TARGET SUBREDDIT
SUBREDDIT = "dealsforindia"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}

# --- AI CONFIGURATION ---
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

def analyze_and_rewrite(title, body):
    """
    1. Checks if the post is 'useless' (question/rant).
    2. If it is a real deal, rewrites it.
    """
    if not GOOGLE_API_KEY:
        return title, body # Fallback if no AI key

    try:
        # Strict prompt to filter spam
        prompt = f"""
        Act as a strict Deal Moderator. Analyze this Reddit post.
        
        Title: {title}
        Body: {body}

        1. Is this a Help Request, Question, Rant, or Discussion? (e.g. "Where to buy?", "Review needed", "Is this good?").
           IF YES -> Reply ONLY with the word "SKIP".
        
        2. Is this a real Shopping Deal/Sale/Loot?
           IF YES -> Rewrite it for Telegram:
           - Line 1: Catchy Title (max 10 words, 1 emoji).
           - Line 2: Short Summary (max 15 words).
           - Do NOT include links.
        """
        
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        if "SKIP" in text:
            return None, None # AI says ignore this post

        lines = [l for l in text.split('\n') if l.strip()]
        if len(lines) >= 2:
            new_title = lines[0].replace("Title:", "").strip()
            new_summary = lines[1].replace("Summary:", "").strip()
            return new_title, new_summary
        else:
            return title, body # AI format failed, use original
            
    except Exception as e:
        print(f"AI Error: {e}")
        return title, body

def get_earnkaro_link(deal_url):
    """Converts link using your original logic."""
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

def extract_and_convert_links(text):
    """Finds links in body and converts them."""
    urls = re.findall(r'(https?://[^\s"<\]\)]+)', text)
    unique_urls = sorted(set(urls), key=urls.index)
    
    links_map = []
    for url in unique_urls:
        if "reddit.com" in url or "preview" in url: continue
        new_link = get_earnkaro_link(url)
        links_map.append((url, new_link))
        
    return links_map

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
        
        image_url = None
        if hasattr(entry, 'media_thumbnail'):
             image_url = entry.media_thumbnail[0]['url']
        elif '<img src="' in content:
             match = re.search(r'<img src="(.*?)"', content)
             if match: image_url = match.group(1)

        clean_body = clean_html(content)
        
        # 1. AI SPAM CHECK & REWRITE
        ai_title, ai_summary = analyze_and_rewrite(title, clean_body)
        
        # If AI returns None, it is a useless post -> SKIP
        if ai_title is None:
            print("Skipping: Not a deal.")
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue 

        # 2. CONVERT LINKS
        links_map = extract_and_convert_links(clean_body)
        
        # 3. BUILD CAPTION
        caption = f"🔥 <b>{ai_title}</b>\n\n{ai_summary}\n\n"
        
        if links_map:
            caption += "<b>👇 Grab Deal:</b>\n"
            for orig, converted in links_map:
                 caption += f"➜ <a href='{converted}'>Click Here to Buy</a>\n"
        else:
            main_link = get_earnkaro_link(entry.link)
            caption += f"➜ <a href='{main_link}'>Click Here to Buy</a>\n"

        caption += "\n#Deal #Loot"
        
        send_telegram(caption, image_url)
        print(f"Posted: {ai_title}")
        
        with open("last_post.txt", "w") as f: f.write(entry.id)
        time.sleep(2)

if __name__ == "__main__":
    main()
