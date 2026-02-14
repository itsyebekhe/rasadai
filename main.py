import os
import json
import time
import logging
import cloudscraper
import html
import re
import random
import concurrent.futures
import feedparser
from urllib.parse import quote, unquote, urlparse, urlunparse
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from gnews import GNews
from ddgs import DDGS
from dateutil import parser

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency OR IRGC)',
    'TARGET_SOURCES': [
        'iranintl.com', 'bbc.com/persian', 'radiofarda.com', 'independentpersian.com',
        'dw.com/fa', 'presstv.ir', 'tasnimnews.com', 'farsnews.ir', 'irna.ir', 'mehrnews.com'
    ],
    'FILES': {
        'NEWS': 'news.json',
        'MARKET': 'market.json'
    },
    'TELEGRAM': {
        'BOT_TOKEN': os.environ.get('TG_BOT_TOKEN'), 
        'CHANNEL_ID': os.environ.get('TG_CHANNEL_ID') 
    },
    'PROXY_URL': 'https://raw.githubusercontent.com/itsyebekhe/MTProtoNexus/refs/heads/gh-pages/extracted_proxies.json',
    'TIMEOUT': 20,
    'MAX_WORKERS': 4,
    'POLLINATIONS_KEY': os.environ.get('POLLINATIONS_API_KEY'),
    'AI_RETRIES': 3,
    'MIN_TELEGRAM_URGENCY': 7,
    'MAX_NEWS_AGE_HOURS': 24, # Drop news older than this
    'HISTORY_SIZE': 300       # Keep last 300 items in history
}

PROXY_NAMES = [
    "Kourosh", "Dariush", "Kaveh", "Rostam", "Arash", "Siavash", "Babak", 
    "Khashayar", "Sorena", "Ariobarzan", "Mithra", "Anahita", "Faridun", 
    "Jamshid", "Zal", "Bahram", "Shapur", "Artaban", "Pirooz", "Maziar",
    "Tahmineh", "Gordafarid", "Cassandan", "Atusa", "Roxana", "Mandana"
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

class IranNewsRadar:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(browser='chrome') 
        self.api_key = CONFIG['POLLINATIONS_KEY']
        self.existing_news = self._load_existing_news()
        
        self.seen_urls = set()
        self.seen_titles = set()
        
        # Populate history sets
        for item in self.existing_news:
            if item.get('url'):
                self.seen_urls.add(self._clean_url(item['url']))
            if item.get('title_en'):
                self.seen_titles.add(self._normalize_text(item['title_en']))
            if item.get('title_fa'):
                self.seen_titles.add(self._normalize_text(item['title_fa']))
        
        self.gnews_en = GNews(language='en', country='US', period='4h', max_results=5)

    def _clean_url(self, url):
        """Removes query parameters to prevent duplicates based on ?utm_source etc."""
        if not url: return ""
        try:
            parsed = urlparse(url)
            # Rebuild url without query params
            clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
            return clean.rstrip('/')
        except:
            return url

    def _normalize_text(self, text):
        if not text: return ""
        return re.sub(r'\W+', '', text).lower()

    def _get_tokens(self, text):
        stop_words = {'a', 'an', 'the', 'and', 'or', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'news', 'report', 'breaking'}
        if not text: return set()
        clean = re.sub(r'[^\w\s]', '', text.lower())
        words = set(clean.split())
        return words - stop_words

    def _is_duplicate_fuzzy(self, new_title, comparison_pool):
        norm_title = self._normalize_text(new_title)
        if norm_title in self.seen_titles: return True
        
        new_tokens = self._get_tokens(new_title)
        if len(new_tokens) < 3: return False # Too short to judge

        for item in comparison_pool:
            existing_title = item.get('title_en', item.get('title', ''))
            existing_tokens = self._get_tokens(existing_title)
            
            if not existing_tokens: continue
            
            intersection = new_tokens.intersection(existing_tokens)
            union = new_tokens.union(existing_tokens)
            
            if not union: continue
            similarity = len(intersection) / len(union)
            
            # If 50% similar words, it's a duplicate
            if similarity > 0.5:
                return True
        return False

    def _load_existing_news(self):
        if not os.path.exists(CONFIG['FILES']['NEWS']): return []
        try:
            with open(CONFIG['FILES']['NEWS'], 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except: return []

    # --- PROXIES & MARKET ---
    def fetch_best_proxies(self):
        try:
            resp = self.scraper.get(CONFIG['PROXY_URL'], timeout=10)
            if resp.status_code != 200: return []
            data = resp.json()
            online = [p for p in data if p.get('status') == 'Online']
            online.sort(key=lambda x: x.get('latency') if x.get('latency') is not None else 99999)
            return online[:9]
        except: return []

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

    # --- NEWS FETCHING ---
    def fetch_gnews(self):
        results = []
        try:
            results = self.gnews_en.get_news(CONFIG['SEARCH_QUERY'])
        except Exception as e:
            logger.error(f"GNews Error: {e}")
        return results

    def fetch_duckduckgo(self, query, region='wt-wt'):
        results = []
        try:
            ddgs = DDGS()
            # Changed timelimit to 'd' (day)
            ddg_gen = ddgs.news(query=query, region=region, safesearch="off", timelimit="d", max_results=10)
            for r in ddg_gen:
                results.append({
                    'title': r.get('title'),
                    'url': r.get('url'),
                    'publisher': {'title': r.get('source')},
                    'published date': r.get('date'),
                    'description': r.get('body'),
                    'image': r.get('image')
                })
        except Exception as e:
            logger.error(f"DDG Error ({query}): {e}")
        return results

    def fetch_bing_rss(self, query):
        results = []
        try:
            encoded_query = quote(query)
            url = f"https://www.bing.com/news/search?q={encoded_query}&format=rss"
            feed = feedparser.parse(url)
            
            for entry in feed.entries:
                publisher = "Bing News"
                if hasattr(entry, 'news_source'): publisher = entry.news_source
                elif hasattr(entry, 'source') and hasattr(entry.source, 'title'): publisher = entry.source.title

                final_link = entry.link
                if "apiclick.aspx" in final_link:
                    match = re.search(r'[?&]url=([^&]+)', final_link)
                    if match: final_link = unquote(match.group(1))

                image_url = None
                try:
                    if hasattr(entry, 'news_image'):
                        raw_url = entry.news_image
                        if '{0}' in raw_url:
                            image_url = raw_url.replace('{0}', '700').replace('{1}', '400')
                        else:
                            image_url = raw_url
                except Exception:
                    pass

                results.append({
                    'title': entry.title,
                    'url': final_link,
                    'publisher': {'title': publisher},
                    'published date': entry.published,
                    'description': entry.summary if hasattr(entry, 'summary') else entry.title,
                    'image': image_url
                })
        except Exception as e:
            logger.error(f"Bing RSS Error: {e}")
        return results

    # --- MANUAL URL ---
    def fetch_manual_url(self, url):
        try:
            resp = self.scraper.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, 'html.parser')
            title = "Unknown Title"
            if soup.title: title = soup.title.string
            og_title = soup.find("meta", property="og:title")
            if og_title: title = og_title.get("content")
            
            publisher = "Manual Source"
            og_site = soup.find("meta", property="og:site_name")
            if og_site: publisher = og_site.get("content")
            
            image = None
            og_image = soup.find("meta", property="og:image")
            if og_image: image = og_image.get("content")

            return [{
                'title': title,
                'url': url,
                'publisher': {'title': publisher},
                'published date': datetime.now(timezone.utc).isoformat(),
                'description': "Manual Submission",
                'image': image
            }]
        except Exception as e:
            logger.error(f"Manual Fetch Error: {e}")
            return []

    def get_combined_news(self):
        all_entries = []
        all_entries.extend(self.fetch_gnews())
        all_entries.extend(self.fetch_bing_rss(CONFIG['SEARCH_QUERY']))
        all_entries.extend(self.fetch_duckduckgo(CONFIG['SEARCH_QUERY'], region='wt-wt'))
        
        # Reduced external sites to prevent timeout, focus on quality
        for domain in CONFIG['TARGET_SOURCES'][:5]: 
            try:
                query = f"site:{domain} Iran"
                if any(x in domain for x in ['tasnim', 'fars', 'irna', 'bbc.com', 'radiofarda']):
                    query = f"site:{domain} ایران"
                site_res = self.fetch_duckduckgo(query, region='wt-wt')
                all_entries.extend(site_res)
                time.sleep(0.5) 
            except: pass
        return all_entries

    # --- PROCESSING ---
    def _resolve_final_url(self, gnews_url):
        if not gnews_url: return None
        if "news.google.com" not in gnews_url: return gnews_url
        try:
            resp = self.scraper.get(gnews_url, allow_redirects=True, timeout=10, stream=True)
            return resp.url
        except: return gnews_url

    def scrape_article_text(self, final_url, fallback_snippet):
        try:
            if final_url.lower().endswith('.pdf'): return fallback_snippet
            resp = self.scraper.get(final_url, timeout=15)
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(["script", "style", "nav", "footer", "header", "form", "iframe"]): tag.extract()
            article_body = soup.find('div', class_=re.compile(r'(article|story|body|content|entry)'))
            if article_body:
                text = article_body.get_text(separator=' ').strip()
            else:
                text = " ".join([p.get_text().strip() for p in soup.find_all('p')])
            
            clean_text = re.sub(r'\s+', ' ', text)
            return clean_text[:2500] if len(clean_text) > 100 else fallback_snippet
        except: return fallback_snippet

    def analyze_with_ai(self, headline, full_text, source_name):
        if not self.api_key: return None
        
        is_regime = any(x in source_name.lower() for x in ['tasnim', 'fars', 'irna', 'press', 'mehr'])
        
        regime_instruction = ""
        if is_regime:
            regime_instruction = "CRITICAL: The source is Iranian State Media. Expose propaganda. "

        system_prompt = (
            "You are a Strategic Analyst for the Iranian Nationalist Opposition. Analyze news with realism.\n\n"
            f"{regime_instruction}"
            "STRICT GUIDELINES FOR URGENCY SCORE (1-10):\n"
            "- 9-10: Immediate War, Major Protests, Death of Leader.\n"
            "- 7-8: New Sanctions, Currency Collapse, Proxy Strikes.\n"
            "- 1-6: Standard politics, Routine news, Opinions.\n\n"
            "INSTRUCTIONS:\n"
            "1. Output in PERSIAN (Farsi).\n"
            "2. Summary: 3 bullet points, factual, relevant.\n"
            "3. No generic lectures. Analyze THIS specific event.\n"
            "JSON: {title_fa, summary[list], impact, tag, urgency(int), sentiment(float)}"
        )

        current_text = full_text

        for attempt in range(CONFIG['AI_RETRIES']):
            try:
                if attempt > 0: current_text = headline + " " + full_text[:800]
                
                resp = self.scraper.post(
                    "https://gen.pollinations.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "openai",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"SOURCE: {source_name}\nHEADLINE: {headline}\nTEXT: {current_text}"}
                        ],
                        "temperature": 0.25 
                    }, timeout=45
                )
                
                if resp.status_code == 200:
                    raw = resp.json()['choices'][0]['message']['content']
                    clean = re.sub(r'```json\s*|```', '', raw).strip()
                    data = json.loads(clean)
                    if 'title_fa' in data and 'summary' in data: return data
                time.sleep(1)
            except Exception as e:
                logger.error(f"AI Attempt {attempt+1} failed: {e}")
                time.sleep(2)

        return None

    def process_item(self, entry):
        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Unknown')
        
        # Resolve URL first to check for duplicates
        final_url = self._resolve_final_url(entry.get('url'))
        clean_final_url = self._clean_url(final_url)

        if not os.environ.get('MANUAL_URL'):
            if clean_final_url in self.seen_urls:
                return None
            if self._is_duplicate_fuzzy(raw_title, self.existing_news):
                return None

        logger.info(f"Processing: {publisher} | {raw_title[:20]}...")
        
        snippet = entry.get('description', raw_title)
        text = self.scrape_article_text(final_url, snippet)
        
        ai = self.analyze_with_ai(raw_title, text, publisher)
        if not ai: return None
        
        try: urgency_val = int(ai.get('urgency', 3))
        except: urgency_val = 3

        try: ts = parser.parse(entry.get('published date')).timestamp()
        except: ts = time.time()

        return {
            "title_fa": ai.get('title_fa', raw_title),
            "title_en": raw_title,
            "summary": ai.get('summary', [snippet]),
            "impact": ai.get('impact', '...'),
            "tag": ai.get('tag', 'General'),
            "urgency": urgency_val,
            "sentiment": ai.get('sentiment', 0),
            "source": publisher,
            "url": final_url, # Store original for clicking
            "clean_url": clean_final_url, # Store for dedup
            "image": entry.get('image'),
            "timestamp": ts
        }

    def send_digest_to_telegram(self, items):
        token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id or not items: return

        try:
            with open(CONFIG['FILES']['MARKET'], 'r') as f: mkt = json.load(f)
            market_text = f"💵 <b>دلار:</b> {mkt.get('usd')} | 🛢 <b>نفت:</b> {mkt.get('oil')}"
        except: market_text = ""

        proxies = self.fetch_best_proxies()
        reply_markup = None
        if proxies:
            keyboard = []
            row = []
            names_pool = random.sample(PROXY_NAMES, min(len(proxies), len(PROXY_NAMES)))
            for i, p in enumerate(proxies):
                proxy_name = names_pool[i]
                latency = p.get('latency', '?')
                btn_text = f"🛡 {proxy_name} ({latency}ms)"
                row.append({"text": btn_text, "url": p['tg_url']})
                if len(row) == 2: # Max 2 per row for better mobile view
                    keyboard.append(row)
                    row = []
            if row: keyboard.append(row)
            reply_markup = {"inline_keyboard": keyboard}

        utc_now = datetime.now(timezone.utc)
        ir_time = utc_now.astimezone(timezone(timedelta(hours=3, minutes=30))).strftime("%H:%M")
        
        header = f"🚨 <b>رادار اخبار مهم ایران</b> | ⏱ {ir_time}\n{market_text}\n➖➖➖➖➖➖➖➖➖➖\n\n"
        footer = "\n🆔 @RasadAIOfficial\n📊 <a href='https://itsyebekhe.github.io/rasadai/'>مشاهده اخبار بیشتر در سایت</a>"

        messages_to_send = []
        current_chunk = header
        
        # Sort by urgency for the message
        items.sort(key=lambda x: x['urgency'], reverse=True)

        for item in items:
            title = str(item.get('title_fa', item.get('title_en')))
            source = str(item.get('source', 'Unknown'))
            url = str(item.get('url', ''))
            impact = str(item.get('impact', ''))
            urgency = item.get('urgency', 3)
            img_link = item.get('image', '')
            
            icon = "🔹"
            if urgency >= 9: icon = "🔥🔴"
            elif urgency >= 7: icon = "🚨"

            is_regime = any(x in source.lower() for x in ['tasnim', 'fars', 'irna', 'press', 'mehr'])
            safe_source = html.escape(source)
            if is_regime: safe_source += " (State Media 🚫)"

            summary_raw = item.get('summary', [])
            if isinstance(summary_raw, str): summary_raw = [summary_raw]
            safe_summary = "\n".join([f"▪️ {html.escape(str(s))}" for s in summary_raw])
            
            # Invisible link for preview if available
            hidden_image = f"<a href='{img_link}'>&#8205;</a>" if img_link else ""

            item_html = (
                f"{icon} {hidden_image}<b><a href='{url}'>{html.escape(title)}</a></b>\n"
                f"🗞 <i>منبع: {safe_source}</i>\n\n"
                f"📝 <b>تحلیل:</b>\n{safe_summary}\n\n"
                f"🎯 <b>تأثیر:</b> {html.escape(impact)}\n\n"
                f"#{html.escape(str(item.get('tag', 'General'))).replace(' ', '_')}\n"
                f"〰️〰️〰️〰️〰️〰️〰️\n\n"
            )

            if len(current_chunk) + len(item_html) + len(footer) > 3900:
                messages_to_send.append(current_chunk + footer)
                current_chunk = header + item_html
            else:
                current_chunk += item_html

        if current_chunk != header:
            messages_to_send.append(current_chunk + footer)

        # Send messages
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        sc = cloudscraper.create_scraper()
        
        for i, msg in enumerate(messages_to_send):
            payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": False}
            # Only add proxy buttons to the very last message
            if i == len(messages_to_send) - 1 and reply_markup:
                payload["reply_markup"] = reply_markup
            
            try:
                sc.post(api_url, json=payload)
                time.sleep(1.5)
            except Exception as e:
                logger.error(f"TG Send Error: {e}")

    def save_news(self, new_items):
        """Merges new items with old items and saves to file safely."""
        try:
            # Combine
            all_news = new_items + self.existing_news
            
            # Remove strict duplicates based on URL
            seen_u = set()
            unique_news = []
            for item in all_news:
                u = self._clean_url(item.get('url'))
                if u and u not in seen_u:
                    seen_u.add(u)
                    unique_news.append(item)
            
            # Sort by timestamp desc
            unique_news.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            
            # Trim history
            final_list = unique_news[:CONFIG['HISTORY_SIZE']]
            
            with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f: 
                json.dump(final_list, f, indent=4, ensure_ascii=False)
            
            logger.info(">>> news.json updated successfully.")
            return final_list
        except Exception as e:
            logger.error(f"Save Failed: {e}")
            return self.existing_news

    def run(self):
        logger.info(">>> Radar Started...")
        
        # Update Market Data
        with open(CONFIG['FILES']['MARKET'], 'w') as f: 
            json.dump(self.fetch_market_rates(), f)

        manual_url = os.environ.get('MANUAL_URL')
        
        # --- 1. FETCHING ---
        if manual_url and manual_url.strip():
            logger.info(f"!!! MANUAL MODE: {manual_url} !!!")
            results = self.fetch_manual_url(manual_url)
            candidates = results
        else:
            results = self.get_combined_news()
            candidates = []
            seen_batch_titles = set()
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(hours=CONFIG['MAX_NEWS_AGE_HOURS'])
            
            for item in results:
                # 1. Check Date
                try:
                    p_date = item.get('published date')
                    if p_date:
                        dt = parser.parse(p_date)
                        # Make naive datetime aware (assume UTC if missing)
                        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff_date: continue # SKIP OLD NEWS
                except: pass # If date parse fails, assume recent

                # 2. Check Deduplication
                raw_url = item.get('url', '')
                clean_u = self._clean_url(raw_url)
                if clean_u in self.seen_urls: continue

                t = item.get('title', '').rsplit(' - ', 1)[0]
                norm_t = self._normalize_text(t)
                
                if norm_t in self.seen_titles: continue
                if norm_t in seen_batch_titles: continue
                if self._is_duplicate_fuzzy(t, self.existing_news): continue

                seen_batch_titles.add(norm_t)
                candidates.append(item)

        logger.info(f"Total Fetched: {len(results)} | Candidates (New & Recent): {len(candidates)}")

        # --- 2. PROCESSING ---
        new_processed_items = []
        if candidates:
            with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
                futures = {exc.submit(self.process_item, i): i for i in candidates}
                for fut in concurrent.futures.as_completed(futures):
                    res = fut.result()
                    if res:
                        new_processed_items.append(res)
                        # Add to seen immediately so we don't double process if logic expands
                        self.seen_urls.add(res['clean_url'])

        # --- 3. SAVING & SENDING ---
        if new_processed_items:
            # SAVE FIRST to prevent duplicates if sending fails
            self.existing_news = self.save_news(new_processed_items)
            
            # Prepare Telegram List
            telegram_items = []
            min_urgency = CONFIG['MIN_TELEGRAM_URGENCY']
            
            for item in new_processed_items:
                urgency = item.get('urgency', 0)
                tag = str(item.get('tag', '')).lower()
                is_conflict = any(w in tag for w in ['war', 'conflict', 'military', 'strike', 'attack', 'nuclear'])
                
                if urgency >= min_urgency:
                    telegram_items.append(item)
                elif urgency >= 6 and is_conflict:
                    telegram_items.append(item)

            if telegram_items:
                logger.info(f"Sending {len(telegram_items)} items to Telegram.")
                self.send_digest_to_telegram(telegram_items)
            else:
                logger.info("New items saved, but urgency too low for Telegram.")
        else:
            logger.info(">>> No valid new items found.")

if __name__ == "__main__":
    IranNewsRadar().run()