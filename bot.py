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
BATCH_LIMIT = 10  # Checks last 10 posts so we never miss one

# --- HEADERS (To look like a real browser) ---
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

def get_earnkaro_link(deal_url):
    """Converts a link to Affiliate. Returns original if failed."""
    if not EARNKARO_TOKEN: return deal_url
    api_url = "https://ekaro-api.affiliaters.in/api/converter/public"
    headers = {"Authorization": f"Bearer {EARNKARO_TOKEN}", "Content-Type": "application/json"}
    payload = {"deal": deal_url, "convert_option": "convert_only"}
    try:
        r = requests.post(api_url, headers=headers, json=payload, timeout=5)
        if r.status_code == 200 and r.json().get("success") == 1:
            return r.json().get("data")
    except: pass
    return deal_url 

def process_text_links(text):
    """Finds links inside text and swaps them IN PLACE."""
    # 1. Find all links
    urls = re.findall(r'(https?://[^\s"<\]\)]+)', text)
    unique_urls = sorted(set(urls), key=urls.index) # Keep order
    
    final_text = text
    
    # 2. Loop through each link found
    for url in unique_urls:
        if "reddit.com" in url or "preview" in url: continue
        
        # 3. Convert it
        affiliate_link = get_earnkaro_link(url)
        
        # 4. Swap it in the text (Preserves "Men's : ", "Women's : " etc)
        if affiliate_link != url:
            final_text = final_text.replace(url, affiliate_link)
            
    return final_text

def send_telegram(caption, image_url=None):
    # Telegram Limit is 1024 chars for captions
    if len(caption) > 1000: caption = caption[:997] + "..."
    
    data = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
    
    if image_url:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        data["photo"] = image_url
    else:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data["text"] = caption
        del data["caption"]

    requests.post(url, data=data)

def process_single_post(post_data):
    title = html.unescape(post_data['title'])
    body_text = html.unescape(post_data.get('selftext', ''))
    permalink = post_data['permalink']
    
    # Get Image
    image_url = post_data.get('url_overridden_by_dest')
    if not image_url or "reddit.com" in image_url:
        # Try thumbnail if main url is not an image
        thumb = post_data.get('thumbnail', '')
        if "http" in thumb: image_url = thumb
        else: image_url = None

    # --- STEP 1: Process Body Text ---
    # This keeps the "Men's : link" format but swaps the link
    final_body = process_text_links(body_text)

    # --- STEP 2: Process Comments (The "OP" Check) ---
    comment_text = ""
    try:
        comments_url = f"https://www.reddit.com{permalink}.json"
        c_r = requests.get(comments_url, headers=HEADERS)
        if c_r.status_code == 200:
            comments_data = c_r.json()[1]['data']['children']
            for comment in comments_data:
                c_data = comment.get('data', {})
                # Only grab comments from the OP (Submitter)
                if c_data.get('is_submitter') == True and c_data.get('body'):
                    processed_comment = process_text_links(html.unescape(c_data['body']))
                    comment_text += f"\n\n🔹 <b>Update:</b>\n{processed_comment}"
    except: pass

    # Combine everything
    full_caption = f"<b>{title}</b>\n\n{final_body}{comment_text}\n\n#Deal #Loot"
    
    print(f"Sending: {title}")
    send_telegram(full_caption, image_url)

def main():
    try:
        # 1. Read Memory
        try:
            with open("last_post.txt", "r") as f: last_id = f.read().strip()
        except: last_id = None

        # 2. Get Recent Posts (Batch of 10)
        url = f"https://www.reddit.com/r/{SUBREDDIT}/new.json?limit={BATCH_LIMIT}"
        r = requests.get(url, headers=HEADERS)
        if r.status_code != 200: return
        
        posts = r.json()['data']['children']
        
        new_posts = []
        for post in posts:
            p_id = post['data']['id']
            if p_id == last_id: break # Stop if we hit the old post
            new_posts.append(post['data'])

        if not new_posts: return

        # 3. Process Oldest -> Newest (So they appear in order)
        for post_data in reversed(new_posts):
            process_single_post(post_data)
            
            # Save ID immediately
            with open("last_post.txt", "w") as f: f.write(post_data['id'])
            time.sleep(2) # Short pause

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
