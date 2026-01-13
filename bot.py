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

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}

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
        
        image_url = None
        if hasattr(entry, 'media_thumbnail'):
             image_url = entry.media_thumbnail[0]['url']
        elif '<img src="' in content:
             match = re.search(r'<img src="(.*?)"', content)
             if match: image_url = match.group(1)

        clean_body = clean_html(content)
        final_body = process_text_links(clean_body)
        
        # --- SMART DEDUPLICATION ---
        # If the body text is almost the same as the title, remove it to avoid repetition.
        if final_body.lower().startswith(title.lower()):
            final_body = final_body[len(title):].strip()
            # Remove any left-over punctuation like ": " or "- "
            final_body = final_body.lstrip(" :-")

        # --- JIOMART STYLE TITLE ---
        caption = f"🔥 <b>{title}</b>\n\n{final_body}\n\n#Deal #Loot"
        
        send_telegram(caption, image_url)
        
        with open("last_post.txt", "w") as f: f.write(entry.id)
        time.sleep(2)

if __name__ == "__main__":
    main()
