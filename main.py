import os
import time
import json
import nltk
from gnews import GNews
from newspaper import Article
from deep_translator import GoogleTranslator

# --- CONFIGURATION ---
SEARCH_QUERY = 'Iran AND (Israel OR USA OR conflict OR protests OR nuclear)'
LANGUAGE = 'en'
COUNTRY = 'US'
PERIOD = '6h' 
MAX_RESULTS = 10 # Increased slightly since we aren't spamming Telegram
HISTORY_FILE = 'sent_news.txt'
JSON_FILE = 'news.json'

# Initialize NLTK
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', quiet=True)
    nltk.download('punkt_tab', quiet=True)

def get_seen_urls():
    if not os.path.exists(HISTORY_FILE):
        open(HISTORY_FILE, 'w').close()
        return []
    with open(HISTORY_FILE, 'r') as f:
        return f.read().splitlines()

def save_seen_url(url):
    with open(HISTORY_FILE, 'a') as f:
        f.write(url + '\n')

def load_news_data():
    if not os.path.exists(JSON_FILE):
        return []
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_news_data(new_entry):
    data = load_news_data()
    # Add new entry to the TOP of the list
    data.insert(0, new_entry)
    # Keep only the last 50 news items to keep the site fast
    data = data[:50]
    
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def summarize_url(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        article.nlp()
        return article.summary
    except:
        return None

def translate_to_persian(text):
    try:
        # Use simple 'fa' target
        return GoogleTranslator(source='auto', target='fa').translate(text)
    except Exception as e:
        print(f"Translation Error: {e}")
        return text

def main():
    print("Starting Iran Radar (Website Mode)...")
    
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    try:
        news_results = google_news.get_news(SEARCH_QUERY)
    except Exception as e:
        print(f"Error fetching news: {e}")
        return

    seen_urls = get_seen_urls()
    
    # Process newest to oldest in the loop
    for entry in reversed(news_results):
        url = entry.get('url')
        if url in seen_urls:
            continue

        title = entry.get('title')
        publisher = entry.get('publisher', {}).get('title', 'Source')
        published_date = entry.get('published date')

        print(f"Processing: {title}")

        # 1. Summarize
        summary_en = summarize_url(url)
        if not summary_en: 
            summary_en = "Content unavailable for automated summary."
        
        if len(summary_en) > 600: 
            summary_en = summary_en[:600] + "..."

        # 2. Translate
        title_fa = translate_to_persian(title)
        summary_fa = translate_to_persian(summary_en)

        # 3. Save to JSON (For the Website)
        news_item = {
            "title_fa": title_fa,
            "summary_fa": summary_fa,
            "title_en": title,
            "summary_en": summary_en,
            "url": url,
            "source": publisher,
            "date": published_date
        }
        
        save_news_data(news_item)
        save_seen_url(url)
        
        # Short sleep to be polite to the translation server
        time.sleep(2)

if __name__ == "__main__":
    main()
