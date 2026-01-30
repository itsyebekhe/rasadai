import os
import time
import json
import nltk
import requests
import concurrent.futures
from gnews import GNews
from newspaper import Article, Config
from deep_translator import GoogleTranslator

# --- CONFIGURATION ---
SEARCH_QUERY = 'Iran AND (Israel OR USA OR conflict OR protests OR nuclear)'
LANGUAGE = 'en'
COUNTRY = 'US'
PERIOD = '6h' 
MAX_RESULTS = 10 # Keep low to prevent ban
HISTORY_FILE = 'sent_news.txt'
JSON_FILE = 'news.json'
MAX_WORKERS = 4 

# --- NLTK ---
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', quiet=True)
    nltk.download('punkt_tab', quiet=True)

# --- HELPERS ---
def get_seen_urls():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return set(f.read().splitlines())

def append_seen_urls(new_urls):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        for url in new_urls: f.write(url + '\n')

def load_news_data():
    if not os.path.exists(JSON_FILE): return []
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except: return []

def save_news_batch(new_entries):
    if not new_entries: return
    current_data = load_news_data()
    combined_data = new_entries + current_data
    combined_data = combined_data[:40] # Keep file size manageable
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(combined_data, f, ensure_ascii=False, indent=4)
    print(f"Saved {len(new_entries)} new articles.")

def get_final_url(url):
    try:
        response = requests.head(url, allow_redirects=True, timeout=5)
        return response.url
    except: return url

def translate_text(text, retries=2):
    if not text: return ""
    for _ in range(retries):
        try:
            return GoogleTranslator(source='auto', target='fa').translate(text)
        except: time.sleep(1)
    return text

def translate_large_text(full_text):
    """
    Splits long text into paragraphs to avoid API limits.
    """
    if not full_text or len(full_text) < 10:
        return ""
    
    # Split by double newlines (paragraphs)
    paragraphs = full_text.split('\n\n')
    translated_paragraphs = []
    
    print(f"   > Translating {len(paragraphs)} paragraphs...")
    
    for p in paragraphs:
        if len(p.strip()) < 3: continue
        # Translate chunk
        trans = translate_text(p)
        if trans:
            translated_paragraphs.append(trans)
            
    return '\n\n'.join(translated_paragraphs)

def extract_and_process(entry):
    url = entry.get('url')
    title = entry.get('title')
    publisher = entry.get('publisher', {}).get('title', 'Source')
    date = entry.get('published date')

    print(f"Processing: {title[:40]}...")

    # 1. Download Content
    config = Config()
    config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/115.0.0.0 Safari/537.36'
    config.request_timeout = 10

    try:
        final_url = get_final_url(url)
        article = Article(final_url, config=config)
        article.download()
        article.parse()
        article.nlp()
        
        summary_en = article.summary
        full_text_en = article.text
    except:
        summary_en = entry.get('description', "")
        full_text_en = ""

    # Clean strings
    summary_en = summary_en.replace('\n', ' ').strip()
    
    # 2. Translate
    title_fa = translate_text(title)
    summary_fa = translate_text(summary_en[:600]) # Keep summary short
    
    # Translate Full Text (Heavy Operation)
    full_text_fa = translate_large_text(full_text_en)

    if not full_text_fa:
        full_text_fa = "متن کامل این مقاله قابل استخراج نبود یا دارای قفل محتوایی است."

    return {
        "title_fa": title_fa,
        "summary_fa": summary_fa,
        "full_text_fa": full_text_fa, # NEW FIELD
        "title_en": title,
        "url": url,
        "source": publisher,
        "date": date
    }

def main():
    print("Starting Iran Radar (Full Text Mode)...")
    google_news = GNews(language=LANGUAGE, country=COUNTRY, period=PERIOD, max_results=MAX_RESULTS)
    results = google_news.get_news(SEARCH_QUERY)
    
    seen = get_seen_urls()
    to_process = [x for x in results if x.get('url') not in seen]

    if not to_process:
        print("No new articles.")
        return

    processed = []
    new_urls = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(extract_and_process, entry): entry for entry in to_process}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res:
                    processed.append(res)
                    new_urls.append(res['url'])
            except Exception as e:
                print(f"Error: {e}")

    save_news_batch(processed)
    append_seen_urls(new_urls)

if __name__ == "__main__":
    main()
