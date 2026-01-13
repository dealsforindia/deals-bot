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
    genai.configure(api_key=GOOGLE_API_KEY)

def is_valid_deal_ai(title, body):
    """
    Uses Gemini AI to check if a post is a valid deal.
    Filters out rants, questions, and 'not working' complaints.
    """
    if not GOOGLE_API_KEY:
        print("⚠️ No Google API Key found. Skipping AI check.")
        return True 

    # Strict prompt to catch the specific junk posts you showed me
    prompt = f"""
    You are a strict moderator for a 'Shopping Deals' channel. 
    Analyze this Reddit post.

    Title: {title}
    Body: {body}

    Your Task:
    Reply "YES" if this is a valid deal, offer, discount, or freebie.
    Reply "NO" if it falls into any of these 'JUNK' categories:
    - Rants or complaints (e.g., "Flipkart cheated me", "Price didn't drop").
    - "Help me" or "Question" posts (e.g., "Is this good?", "Suggest a phone").
    - "Code not working" posts (e.g., "BB codes are not working", "Coupon invalid").
    - Discussion threads without a specific deal link.

    Reply ONLY with "YES" or "NO".
    """
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        answer = response.text.strip().upper()
        
        # If the AI says NO, we block it.
        if "NO" in answer:
            print(f"🚫 AI Blocked: {title}")
            return False
        
        print(f"✅ AI Approved: {title}")
        return True
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        # If AI fails (server error), we default to True so we don't miss deals
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
    """Removes HTML tags and cleans up text."""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = html.unescape(cleantext).strip()
    if "submitted by" in cleantext:
        cleantext = cleantext.split("submitted by")[0].strip()
    return cleantext

def process_text_links(text):
    """Finds URLs in text and converts them to affiliate links."""
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
    """Sends the deal to Telegram."""
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
    # 1. Read the last processed post ID
    try:
        with open("last_post.txt", "r") as f: last_id = f.read().strip()
    except: last_id = None

    # 2. Fetch Reddit RSS Feed
    rss_url = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
    try:
        r = requests.get(rss_url, headers=HEADERS)
        if r.status_code != 200: 
            print("Error fetching RSS feed")
            return
        feed = feedparser.parse(r.content)
    except Exception as e: 
        print(f"Error parsing feed: {e}")
        return

    new_posts = []
    # 3. Collect only new posts
    for entry in feed.entries:
        if entry.id == last_id: break
        new_posts.append(entry)

    if not new_posts: 
        print("No new posts found.")
        return

    # 4. Process new posts (Oldest to Newest)
    for entry in reversed(new_posts):
        title = entry.title.strip()
        
        content = ""
        if hasattr(entry, 'content'): content = entry.content[0].value
        elif hasattr(entry, 'summary'): content = entry.summary
        
        clean_body = clean_html(content)
        
        # --- AI CHECK ---
        # If AI says it's junk, save ID and skip posting
        if not is_valid_deal_ai(title, clean_body):
            with open("last_post.txt", "w") as f: f.write(entry.id)
            continue 

        # --- PROCESS IMAGES & LINKS ---
        image_url = None
        if hasattr(entry, 'media_thumbnail'):
             image_url = entry.media_thumbnail[0]['url']
        elif '<img src="' in content:
             match = re.search(r'<img src="(.*?)"', content)
             if match: image_url = match.group(1)

        final_body = process_text_links(clean_body)
        
        # Remove body if it's identical to title (deduplication)
        if final_body.lower().startswith(title.lower()):
            final_body = final_body[len(title):].strip()
            final_body = final_body.lstrip(" :-")

        caption = f"🔥 <b>{title}</b>\n\n{final_body}\n\n#Deal #Loot"
        
        # --- SEND TO TELEGRAM ---
        print(f"Sending: {title}")
        send_telegram(caption, image_url)
        
        # Update last_post.txt immediately after sending
        with open("last_post.txt", "w") as f: f.write(entry.id)
        time.sleep(2)

if __name__ == "__main__":
    main()
