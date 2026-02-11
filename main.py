import os
import json
import time
import logging
import cloudscraper
import html
import re
import concurrent.futures
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from gnews import GNews
from fake_useragent import UserAgent
from dateutil import parser

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency OR IRGC)',
    'LANGUAGE': 'en',
    'COUNTRY': 'US',
    'PERIOD': '1h',
    'MAX_RESULTS': 10, 
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
        self.scraper = cloudscraper.create_scraper(browser='chrome') 
        self.api_key = CONFIG['POLLINATIONS_KEY']
        self.existing_news = self._load_existing_news()
        
        # Load seen URLs
        self.seen_urls = {item.get('url') for item in self.existing_news if item.get('url')}
        self.seen_titles = {self._normalize_text(item.get('title_en', '')) for item in self.existing_news}
        
        self.stop_words = {
            'a', 'an', 'the', 'and', 'or', 'but', 'if', 'because', 'as', 'what',
            'when', 'where', 'how', 'of', 'at', 'by', 'for', 'with', 'about',
            'against', 'between', 'into', 'through', 'during', 'before', 'after',
            'above', 'below', 'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off',
            'over', 'under', 'again', 'further', 'then', 'once', 'here', 'there',
            'why', 'so', 'news', 'report', 'live', 'update', 'analysis'
        }

    def _normalize_text(self, text):
        if not text: return ""
        return re.sub(r'\W+', '', text).lower()

    def _get_tokens(self, text):
        if not text: return set()
        clean = re.sub(r'[^\w\s]', '', text.lower())
        words = set(clean.split())
        return words - self.stop_words

    def _is_duplicate_fuzzy(self, new_title, comparison_pool):
        new_tokens = self._get_tokens(new_title)
        if not new_tokens: return False

        for item in comparison_pool:
            existing_title = item.get('title', item.get('title_en', ''))
            existing_tokens = self._get_tokens(existing_title)
            if not existing_tokens: continue

            intersection = new_tokens.intersection(existing_tokens)
            union = new_tokens.union(existing_tokens)
            
            if not union: continue
            
            similarity = len(intersection) / len(union)

            if similarity > 0.35 or len(intersection) >= 4:
                logger.info(f"🚫 Fuzzy Duplicate: '{new_title}' ~= '{existing_title}'")
                return True
        return False

    def _load_existing_news(self):
        if not os.path.exists(CONFIG['FILES']['NEWS']): return []
        try:
            with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except: return []

    # --- URL RESOLVER ---
    def _resolve_final_url(self, gnews_url):
        if not gnews_url: return None
        try:
            resp = self.scraper.get(gnews_url, allow_redirects=True, timeout=10, stream=True)
            final_url = resp.url
            resp.close()
            
            if "news.google.com" in final_url and len(final_url) < 100:
                return gnews_url
                
            return final_url
        except Exception as e:
            logger.warning(f"⚠️ Could not resolve URL {gnews_url}: {e}")
            return gnews_url

    # --- MARKET DATA ---
    def fetch_market_rates(self):
        data = {"usd": "نامشخص", "oil": "نامشخص", "updated": "--:--"}
        try:
            resp = self.scraper.get("https://alanchand.com/en/currencies-price/usd", timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                usd = soup.find('input', attrs={'data-curr': 'tmn'})
                if usd:
                    val = usd.get('data-price') or usd.get('value')
                    if val: data["usd"] = f"{int(int(val.replace(',', '')) / 10):,}"
        except: pass

        try:
            resp = self.scraper.get("https://oilprice.com/oil-price-charts/46", timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
            oil = soup.select_one(".last_price")
            if oil: data["oil"] = oil.get_text().strip()
        except: pass

        data["updated"] = time.strftime("%H:%M")
        return data

    # --- TELEGRAM SENDER ---
    def send_digest_to_telegram(self, items):
        token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id: return

        try:
            with open(CONFIG['FILES']['MARKET'], 'r') as f: mkt = json.load(f)
            usd_price = mkt.get('usd', 'نامشخص')
            oil_price = mkt.get('oil', 'نامشخص')
            market_text = f"💵 <b>دلار:</b> {usd_price} تومان | 🛢 <b>نفت:</b> {oil_price} دلار"
        except: market_text = ""

        utc_now = datetime.now(timezone.utc)
        iran_offset = timezone(timedelta(hours=3, minutes=30))
        current_time = utc_now.astimezone(iran_offset).strftime("%H:%M")
        
        header = (
            f"📡 <b>رادار هوشمند اخبار ایران</b> | ⏱ {current_time}\n"
            f"{market_text}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n\n"
        )
        
        footer = (
            "\n🆔 @RasadAIOfficial"
            "\n📊 <a href='https://itsyebekhe.github.io/rasadai/'>مشاهده داشبورد کامل</a>"
        )

        messages_to_send = []
        current_chunk = header

        for item in items:
            title = str(item.get('title_fa', item.get('title_en')))
            source = str(item.get('source', 'Unknown'))
            url = str(item.get('url', ''))
            impact = str(item.get('impact', ''))
            urgency = item.get('urgency', 3)
            
            icon = "🔹"
            if urgency >= 8: icon = "🚨"
            elif urgency >= 6: icon = "⚠️"

            raw_tag = item.get('tag', 'General')
            tag_str = str(raw_tag[0]) if isinstance(raw_tag, list) and raw_tag else str(raw_tag)

            safe_title = html.escape(title)
            safe_source = html.escape(source)
            safe_impact = html.escape(impact)
            safe_tag = html.escape(tag_str).replace(' ', '_')
            
            summary_raw = item.get('summary', [])
            if isinstance(summary_raw, str): summary_raw = [summary_raw]
            safe_summary = "\n".join([f"▪️ {html.escape(str(s))}" for s in summary_raw])

            item_html = (
                f"{icon} <b><a href='{url}'>{safe_title}</a></b>\n"
                f"🗞 <i>منبع: {safe_source}</i>\n\n"
                f"📝 <b>تحلیل:</b>\n{safe_summary}\n\n"
                f"🎯 <b>تأثیر:</b> {safe_impact}\n\n"
                f"#{safe_tag}\n"
                f"〰️〰️〰️〰️〰️〰️〰️\n\n"
            )

            if len(current_chunk) + len(item_html) + len(footer) > 3900:
                messages_to_send.append(current_chunk + footer)
                current_chunk = header + item_html
            else:
                current_chunk += item_html

        if current_chunk != header:
            messages_to_send.append(current_chunk + footer)

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        for i, msg in enumerate(messages_to_send):
            try:
                cloudscraper.create_scraper().post(api_url, json={
                    "chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True
                })
                time.sleep(1.5)
            except Exception as e:
                logger.error(f"❌ Send Fail: {e}")

    # --- SCRAPER & AI ---
    def scrape_article_text(self, final_url, fallback_snippet):
        try:
            if final_url.lower().endswith('.pdf'): return fallback_snippet
            resp = self.scraper.get(final_url, timeout=15)
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(["script", "style", "nav", "footer", "header", "form", "iframe"]): tag.extract()
            paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text()) > 50]
            text = " ".join(paragraphs)[:4500]
            
            if len(text) < 100: return fallback_snippet
            return text
        except: return fallback_snippet

    def analyze_with_ai(self, headline, full_text):
        if not self.api_key: return None
        context = full_text if len(full_text) > 100 else headline
        fallback = {"title_fa": headline, "summary": ["تحلیل در دسترس نیست"], "impact": "بررسی نشده", "tag": "News", "urgency": 3, "sentiment": 0}

        # --- UPDATED PROMPT FOR OPPOSITION / SHAHIST PERSPECTIVE ---
        system_prompt = (
            "You are a Senior Strategic Analyst for the Iranian Opposition (Pro-Pahlavi/Nationalist view). "
            "Analyze the news from the perspective of Iran's National Interest, distinguishing it from the 'Islamic Republic Regime's interest'. "
            "Guidelines: "
            "1. Terminology: Refer to the government as 'The Regime' (رژیم) or 'The Clerical Rule'. Do not use honorifics for IR officials. "
            "2. Focus: Highlight economic incompetence, IRGC corruption/terrorism, and the regime's isolation. "
            "3. Tone: Serious, Critical, Patriotic (Iran-First). "
            "4. Language: Persian (Farsi). "
            "5. Output valid JSON: {title_fa, summary[3 bullet points], impact(1 sentence critical of regime's effect on people), tag(1 word), urgency(1-10), sentiment(float -1.0 to 1.0)}."
        )

        try:
            resp = self.scraper.post(
                "https://gen.pollinations.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": "openai",
                    "messages": [{
                        "role": "system", 
                        "content": system_prompt
                    }, {
                        "role": "user", 
                        "content": f"HEADLINE: {headline}\nTEXT: {context}"
                    }],
                    "temperature": 0.2 # Slightly higher creativity for political nuance
                }, timeout=30
            )
            if resp.status_code == 200:
                clean = resp.json()['choices'][0]['message']['content'].replace('```json','').replace('```','').strip()
                return json.loads(clean)
        except: pass
        return fallback

    def process_item(self, entry):
        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        
        # 1. Resolve URL FIRST
        gnews_url = entry.get('url')
        logger.info(f"Resolving: {raw_title[:30]}...")
        final_url = self._resolve_final_url(gnews_url)
        
        # 2. Check Resolved URL against history
        if final_url in self.seen_urls:
            logger.info(f"🚫 Duplicate URL found: {final_url}")
            return None

        # 3. Check Title against history
        if self._normalize_text(raw_title) in self.seen_titles: return None
        
        # 4. Scrape content
        snippet = entry.get('description', raw_title)
        text = self.scrape_article_text(final_url, snippet)
        
        # 5. Analyze
        ai = self.analyze_with_ai(raw_title, text)
        if not ai: ai = {}

        try: ts = parser.parse(entry.get('published date')).timestamp()
        except: ts = time.time()

        return {
            "title_fa": ai.get('title_fa', raw_title),
            "title_en": raw_title,
            "summary": ai.get('summary', [snippet]),
            "impact": ai.get('impact', '...'),
            "tag": ai.get('tag', 'General'),
            "urgency": ai.get('urgency', 3),
            "sentiment": ai.get('sentiment', 0), 
            "source": entry.get('publisher', {}).get('title', 'Source'),
            "url": final_url, 
            "date": entry.get('published date'),
            "timestamp": ts
        }

    def run(self):
        logger.info(">>> Radar Started...")
        with open(CONFIG['FILES']['MARKET'], 'w') as f: json.dump(self.fetch_market_rates(), f)

        try:
            results = GNews(language=CONFIG['LANGUAGE'], country=CONFIG['COUNTRY'], 
                           period=CONFIG['PERIOD'], max_results=CONFIG['MAX_RESULTS']).get_news(CONFIG['SEARCH_QUERY'])
        except Exception as e:
            logger.error(f"GNews Error: {e}")
            return

        # --- BATCH PRE-FILTERING ---
        unique_batch_results = []
        for item in results:
            title = item.get('title', '').rsplit(' - ', 1)[0]
            if self._is_duplicate_fuzzy(title, self.existing_news): continue
            if self._is_duplicate_fuzzy(title, unique_batch_results): continue
            unique_batch_results.append(item)

        logger.info(f"Raw: {len(results)} | Unique Titles: {len(unique_batch_results)}")

        # --- PROCESSING ---
        new_items = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
            futures = {exc.submit(self.process_item, i): i for i in unique_batch_results}
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                if res:
                    new_items.append(res)
                    self.seen_titles.add(self._normalize_text(res['title_en']))
                    self.seen_urls.add(res['url'])
                    self.existing_news.append(res)

        if new_items:
            new_items.sort(key=lambda x: (x.get('urgency', 0), x.get('timestamp', 0)), reverse=True)
            self.send_digest_to_telegram(new_items)

            all_news = self._load_existing_news()
            existing_urls_file = {x.get('url') for x in all_news}
            for ni in new_items:
                if ni['url'] not in existing_urls_file:
                    all_news.append(ni)

            all_news.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            
            with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f: 
                json.dump(all_news[:100], f, indent=4, ensure_ascii=False)
            logger.info(">>> Done.")
        else:
            logger.info(">>> No unique news.")

if __name__ == "__main__":
    IranNewsRadar().run()
