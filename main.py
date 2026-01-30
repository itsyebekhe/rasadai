import os
import json
import time
import requests
from bs4 import BeautifulSoup
from gnews import GNews
from deep_translator import GoogleTranslator
from textblob import TextBlob
from urllib.parse import urljoin

# --- CONFIG ---
SEARCH_QUERY = 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency)'
LANGUAGE = 'en'
COUNTRY = 'US'
PERIOD = '6h'
MAX_RESULTS = 15
NEWS_FILE = 'news.json'
MARKET_FILE = 'market.json'
HISTORY_FILE = 'seen_news.txt'

# Robust Headers to look like a real browser (Essential for images)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.google.com/'
}

def get_seen():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return set(f.read().splitlines())

def save_seen(urls):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        for url in urls: f.write(url + '\n')

def fetch_market_rates():
    print(">>> Fetching Dollar Price...")
    url = "https://alanchand.com/en/currencies-price/usd"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Method 1: Input
            price_toman = 0
            input_tag = soup.find('input', attrs={'data-curr': 'tmn'})
            if input_tag:
                if input_tag.has_attr('data-price'):
                    price_toman = int(int(input_tag['data-price']) / 10)
                elif input_tag.has_attr('value'):
                     price_toman = int(int(input_tag['value'].replace(',','')) / 10)

            # Method 2: JSON-LD
            if price_toman == 0:
                scripts = soup.find_all('script', type='application/ld+json')
                for s in scripts:
                    if '"sku":"USD"' in s.text:
                        data = json.loads(s.text)
                        if 'offers' in data and 'price' in data['offers']:
                            price_toman = int(float(data['offers']['price']) / 10)
                            break
            
            if price_toman > 0:
                print(f"   > Success! Price: {price_toman}")
                return {"usd": f"{price_toman:,}", "updated": time.strftime("%H:%M")}
                
    except Exception as e:
        print(f"   > Market Error: {e}")
    
    return {"usd": "Check Source", "updated": "--:--"}

def get_category_and_sentiment(text):
    t = text.lower()
    tag, color = 'سیاسی', 'primary'
    if 'nuclear' in t or 'atomic' in t: tag, color = 'هسته‌ای', 'warning'
    elif 'attack' in t or 'war' in t or 'military' in t or 'strike' in t: tag, color = 'نظامی', 'danger'
    elif 'oil' in t or 'currency' in t or 'economy' in t or 'sanction' in t: tag, color = 'اقتصادی', 'success'
    
    blob = TextBlob(text)
    return tag, color, blob.sentiment.polarity

# --- IMPROVED IMAGE EXTRACTOR ---
def fetch_article_image(url):
    """
    Forces a visit to the site to get the High-Res OpenGraph image.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=4) # 4s timeout to keep it fast
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # List of meta tags to check in order of quality
            meta_checks = [
                {'property': 'og:image'},
                {'name': 'twitter:image'},
                {'property': 'og:image:secure_url'},
                {'name': 'thumbnail'}
            ]
            
            image_url = None
            for check in meta_checks:
                tag = soup.find('meta', check)
                if tag and tag.get('content'):
                    image_url = tag['content']
                    break
            
            # If we found an image, make sure it's a full URL
            if image_url:
                # Fix relative URLs (e.g. "/uploads/img.jpg" -> "https://site.com/uploads/img.jpg")
                return urljoin(url, image_url)
                
    except Exception:
        pass 
    return None

def main():
    print(">>> Starting Radar...")
    
    # 1. MARKET
    market_data = fetch_market_rates()
    try:
        with open(MARKET_FILE, 'w', encoding='utf-8') as f: json.dump(market_data, f)
    except: pass

    # 2. NEWS
    print(">>> Fetching News...")
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    
    try:
        results = google_news.get_news(SEARCH_QUERY)
    except Exception as e:
        print(f"News API Error: {e}")
        return

    seen = get_seen()
    new_entries = []
    new_urls = []
    translator = GoogleTranslator(source='auto', target='fa')

    for entry in results:
        url = entry.get('url')
        if url and not url.startswith('http'): url = 'https://' + url
        
        if url in seen: continue
        
        raw_title = entry.get('title').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Source')
        date = entry.get('published date')
        
        print(f"   > Processing: {raw_title[:30]}...")

        try:
            # A. Translate
            title_fa = translator.translate(raw_title)
            
            # B. Sentiment
            tag, color, sentiment = get_category_and_sentiment(raw_title)
            
            # C. Images (THE FIX)
            # 1. Attempt to Scrape Real Image first (High Priority)
            print("     - Scraping high-res image...")
            final_image = fetch_article_image(url)
            
            # 2. If Scrape fails, use Google Thumbnail (Low Priority)
            if not final_image:
                final_image = entry.get('image')
                
            # 3. If both fail, use placeholder
            if not final_image:
                final_image = "https://placehold.co/600x400?text=No+Image"

            new_entries.append({
                "title_fa": title_fa,
                "title_en": raw_title,
                "source": publisher,
                "url": url,
                "image": final_image, 
                "date": date,
                "tag": tag,
                "tag_color": color,
                "sentiment": sentiment
            })
            new_urls.append(url)
        except Exception as e:
            print(f"     - Error: {e}")

    # 3. SAVE
    if new_entries:
        try:
            with open(NEWS_FILE, 'r', encoding='utf-8') as f: old_data = json.load(f)
        except: old_data = []
        
        final_data = new_entries + old_data
        final_data = final_data[:60]
        
        with open(NEWS_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=4)
        
        save_seen(new_urls)
        print(f">>> Added {len(new_entries)} news items.")
    else:
        print(">>> No new news.")

if __name__ == "__main__":
    main()
