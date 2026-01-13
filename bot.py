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
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") # You added this to secrets
SUBREDDIT = "dealsforindia"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}

# --- CONFIGURE AI ---
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

def is_valid_deal_ai(title, body):
    """Asks Gemini if the post is a valid deal or just junk."""
    if not GOOGLE_API_KEY:
        return True # If no key, assume it's valid to be safe

    # This prompt is strictly designed to block the images you showed me
    prompt = f"""
    You are a moderator for a strict 'Shopping Deals' Telegram channel.
    Analyze this Reddit post to decide if it should be posted.
    
    Post Title: {title}
    Post Body: {body}

    RULES:
    1. REPLY "YES" ONLY if this is a valid shopping deal, discount, coupon, or price drop.
    2. REPLY "NO" if this is a RANT or COMPLAINT (e.g., "Fuck Flipkart", "Price didn't drop").
    3. REPLY "NO" if this is a QUESTION or DISCUSSION (e.g., "Is this good?", "Help me").
    4. REPLY "NO" if it says a code is NOT working (e.g., "Codes are not working for me").
    5. REPLY "NO" if it is spam or irrelevant.

    Reply ONLY with "YES" or "NO".
    """
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        answer = response.text.strip().upper()
        
        if "NO" in answer:
            print(f"AI Filtered out: {title}")
            return False
        return True
    except Exception as e:
        print(f"AI Error: {e}")
        return True # If AI fails, let the post through so we don't miss deals

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
    """Finds links and replaces them."""
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
        title = entry.title.strip()
        
        content = ""
        if hasattr(entry, 'content'): content = entry.content[0].value
        elif hasattr(entry, 'summary'): content = entry.summary
        
        clean_body = clean_html(content)
        
        # --- AI CHECK HERE ---
        # Before doing any link processing, we ask AI if this post is garbage.
        if not is_valid_deal_ai(title, clean_body):
            # If AI says NO, we save the ID (so we don't check it again) but we DO NOT send it.
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue 

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
