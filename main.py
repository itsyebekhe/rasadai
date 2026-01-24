import os
import time
import json
import requests
import nltk
from gnews import GNews
from newspaper import Article
from deep_translator import GoogleTranslator

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
SEARCH_QUERY = 'Iran AND (Israel OR USA OR conflict OR protests OR nuclear)'
LANGUAGE = 'en'
COUNTRY = 'US'
PERIOD = '6h' 
MAX_RESULTS = 5
HISTORY_FILE = 'sent_news.txt'
JSON_FILE = 'news.json'

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

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHANNEL_ID, 'text': message, 'parse_mode': 'HTML'}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

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
        return GoogleTranslator(source='auto', target='fa').translate(text)
    except:
        return text

def main():
    print("Starting News Bot...")
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    try:
        news_results = google_news.get_news(SEARCH_QUERY)
    except Exception as e:
        print(f"Error: {e}")
        return

    seen_urls = get_seen_urls()
    
    for entry in reversed(news_results):
        url = entry.get('url')
        if url in seen_urls:
            continue

        title = entry.get('title')
        publisher = entry.get('publisher', {}).get('title', 'Source')
        published_date = entry.get('published date')

        print(f"Processing: {title}")

        summary_en = summarize_url(url)
        if not summary_en: summary_en = "Content unavailable."
        if len(summary_en) > 500: summary_en = summary_en[:500] + "..."

        title_fa = translate_to_persian(title)
        summary_fa = translate_to_persian(summary_en)

        # 1. Send to Telegram
        message = (
            f"🇮🇷 <b>{title_fa}</b>\n\n"
            f"📝 <b>خلاصه:</b>\n{summary_fa}\n\n"
            f"🇺🇸 <b>Original:</b> {title}\n"
            f"🔗 <a href='{url}'>Read Full Article</a>"
        )
        send_telegram_message(message)

        # 2. Save to JSON for Website
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
        time.sleep(3)

if __name__ == "__main__":
    main()
