import os
import json
import time
import logging
import requests
import html
import concurrent.futures
from datetime import datetime
from dateutil import parser
from bs4 import BeautifulSoup
from gnews import GNews
from fake_useragent import UserAgent

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency OR IRGC)',
    'LANGUAGE': 'en',
    'COUNTRY': 'US',
    'PERIOD': '4h',
    'MAX_RESULTS': 30,
    'FILES': {
        'NEWS': 'news.json',
        'MARKET': 'market.json'
    },
    'TELEGRAM': {
        'BOT_TOKEN': os.environ.get('TG_BOT_TOKEN'), 
        'CHANNEL_ID': os.environ.get('TG_CHANNEL_ID') 
    },
    'TIMEOUT': 20,
    'MAX_WORKERS': 4,
    'POLLINATIONS_KEY': os.environ.get('POLLINATIONS_API_KEY')
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

class IranNewsRadar:
    def __init__(self):
        self.ua = UserAgent()
        self.api_key = CONFIG['POLLINATIONS_KEY']
        
        # Load existing data to check for duplicates
        self.existing_news = self._load_existing_news()
        self.seen_urls = {item.get('url') for item in self.existing_news if item.get('url')}

    def _get_headers(self):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Referer': 'https://www.google.com/',
        }

    def _load_existing_news(self):
        """Loads the JSON file to use as history."""
        if not os.path.exists(CONFIG['FILES']['NEWS']):
            return []
        try:
            with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error loading news.json: {e}")
            return []

    # --- TELEGRAM SENDER ---
    def send_to_telegram(self, item):
        token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHANNEL_ID']

        if not token or not chat_id:
            logger.warning("Telegram credentials missing.")
            return

        # 1. Prepare Data & Fix Types
        title_fa = str(item.get('title_fa', 'News Update'))
        url = str(item.get('url', ''))
        impact = str(item.get('impact', ''))
        
        # Fix Tag: Handle if AI returns a list OR a string
        raw_tag = item.get('tag')
        if isinstance(raw_tag, list):
            # If list ['Politics'], take first item
            tag_str = str(raw_tag[0]) if raw_tag else 'General'
        else:
            tag_str = str(raw_tag) if raw_tag else 'General'

        # 2. Escape HTML characters ( < > & )
        safe_title = html.escape(title_fa)
        safe_impact = html.escape(impact)
        safe_tag = html.escape(tag_str)
        
        # 3. Format Summary
        summary_list = item.get('summary', [])
        if isinstance(summary_list, str): summary_list = [summary_list] # Safety check
        safe_summary = "\n".join([f"• {html.escape(str(s))}" for s in summary_list])

        # 4. Construct Message HTML
        message_html = (
            f"<b><a href='{url}'>{safe_title}</a></b>\n\n"
            f"<blockquote>{safe_summary}\n\n"
            f"🎯 <b>تأثیر:</b> {safe_impact}</blockquote>\n\n"
            f"#{safe_tag.replace(' ', '_')}"
        )

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True 
        }

        try:
            resp = requests.post(api_url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Telegram Error {resp.status_code}: {resp.text}")
            else:
                logger.info(f" -> Sent to Telegram: {safe_title[:20]}")
        except Exception as e:
            logger.error(f"Telegram Exception: {e}")

    # --- 1. MARKET DATA ---
    def fetch_market_rates(self):
        url = "https://alanchand.com/en/currencies-price/usd"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                input_tag = soup.find('input', attrs={'data-curr': 'tmn'})
                if input_tag:
                    val = input_tag.get('data-price') or input_tag.get('value')
                    if val:
                        price = int(int(val.replace(',', '')) / 10)
                        return {"usd": f"{price:,}", "updated": time.strftime("%H:%M")}
        except: pass
        return {"usd": "N/A", "updated": "--:--"}

    # --- 2. SCRAPER ---
    def scrape_article(self, url):
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            final_url = resp.url
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "figure", "img"]): 
                tag.extract()
            
            paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text()) > 60]
            clean_text = " ".join(paragraphs)[:4000]
            
            return final_url, clean_text
        except:
            return url, ""

    # --- 3. AI ANALYST ---
    def analyze_with_ai(self, headline, full_text):
        if not self.api_key: return None
        context_text = full_text if len(full_text) > 100 else headline
        current_date_str = datetime.now().strftime("%Y-%m-%d")

        url = "https://gen.pollinations.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        
        system_prompt = (
            f"Current Date: {current_date_str}.\n"
            "CONTEXT: Donald Trump is the CURRENT President of the USA. "
            "Role: Intelligence Analyst. "
            "Output strictly valid JSON:\n"
            "1. 'title_fa': Professional Persian headline.\n"
            "2. 'summary': Array of 3 short Persian bullet points.\n"
            "3. 'impact': One sentence on strategic impact on Iran (Persian).\n"
            "4. 'sentiment': Float -1.0 to 1.0.\n"
            "5. 'tag': [نظامی, هسته‌ای, اقتصادی, سیاسی, اجتماعی].\n"
        )

        try:
            resp = requests.post(url, headers=headers, json={
                "model": "openai",
                "messages": [{"role": "system", "content": system_prompt}, 
                             {"role": "user", "content": f"HEADLINE: {headline}\nTEXT: {context_text}"}],
                "temperature": 0.1
            }, timeout=30)
            if resp.status_code == 200:
                raw = resp.json()['choices'][0]['message']['content']
                clean_raw = raw.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_raw)
        except Exception as e:
            logger.error(f"AI Error: {e}")
        return None

    # --- PROCESSOR ---
    def process_item(self, entry):
        orig_url = entry.get('url')
        
        if orig_url in self.seen_urls: 
            return None

        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher_name = entry.get('publisher', {}).get('title', 'Source')
        
        real_url, full_text = self.scrape_article(orig_url)
        
        if real_url in self.seen_urls: 
            return None

        ai = self.analyze_with_ai(raw_title, full_text)
        if not ai: 
            ai = {"title_fa": raw_title, "summary": ["تحلیل در دسترس نیست"], "impact": "بررسی نشده", "tag": "عمومی", "sentiment": 0}

        try:
            ts = parser.parse(entry.get('published date')).timestamp()
        except:
            ts = time.time()

        return {
            "title_fa": ai.get('title_fa'),
            "title_en": raw_title,
            "summary": ai.get('summary'),
            "impact": ai.get('impact'),
            "tag": ai.get('tag'),
            "sentiment": ai.get('sentiment'),
            "source": publisher_name,
            "url": real_url,
            "date": entry.get('published date'),
            "timestamp": ts
        }

    def run(self):
        logger.info(">>> Radar Started (History via news.json)...")
        
        # 1. Market
        with open(CONFIG['FILES']['MARKET'], 'w') as f: json.dump(self.fetch_market_rates(), f)

        # 2. News
        try:
            results = GNews(language=CONFIG['LANGUAGE'], country=CONFIG['COUNTRY'], 
                           period=CONFIG['PERIOD'], max_results=CONFIG['MAX_RESULTS']).get_news(CONFIG['SEARCH_QUERY'])
        except Exception as e:
            logger.error(f"GNews failed: {e}")
            return

        new_items = []

        # 3. Process
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
            futures = {exc.submit(self.process_item, i): i for i in results}
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                if res:
                    new_items.append(res)
                    logger.info(f" + Processed: {res['title_en'][:20]}")

        # 4. Send & Save
        if new_items:
            new_items.sort(key=lambda x: x.get('timestamp', 0))

            logger.info(f"Sending {len(new_items)} new items...")
            
            for item in new_items:
                self.send_to_telegram(item)
                time.sleep(2) 

            # Update Database
            updated_list = new_items + self.existing_news
            updated_list.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            final_list = updated_list[:100] 

            with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f: 
                json.dump(final_list, f, indent=4, ensure_ascii=False)
            
            logger.info(">>> News.json updated.")
        else:
            logger.info(">>> No new news found.")

if __name__ == "__main__":
    IranNewsRadar().run()
