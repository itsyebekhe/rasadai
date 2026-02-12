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
from urllib.parse import quote, unquote
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
    # New Config: Minimum urgency score required to send to Telegram (1-10)
    'MIN_TELEGRAM_URGENCY': 7 
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
        
        for item in self.existing_news:
            if item.get('url'):
                self.seen_urls.add(item['url'])
            if item.get('title_en'):
                self.seen_titles.add(self._normalize_text(item['title_en']))
        
        self.gnews_en = GNews(language='en', country='US', period='1h', max_results=5)

    def _normalize_text(self, text):
        if not text: return ""
        return re.sub(r'\W+', '', text).lower()

    def _get_tokens(self, text):
        stop_words = {'a', 'an', 'the', 'and', 'or', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'news', 'report'}
        if not text: return set()
        clean = re.sub(r'[^\w\s]', '', text.lower())
        words = set(clean.split())
        return words - stop_words

    def _is_duplicate_fuzzy(self, new_title, comparison_pool):
        norm_title = self._normalize_text(new_title)
        if norm_title in self.seen_titles: return True
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
                        width = getattr(entry, 'news_imagemaxwidth', '700')
                        height = getattr(entry, 'news_imagemaxheight', '400')
                        if '{0}' in raw_url:
                            image_url = raw_url.replace('{0}', str(width)).replace('{1}', str(height))
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
        all_entries.extend(self.fetch_duckduckgo("ایران AND (آمریکا OR اسرائیل OR دلار OR جنگ)", region='ir-ir'))

        for domain in CONFIG['TARGET_SOURCES']:
            try:
                query = f"site:{domain} Iran"
                if any(x in domain for x in ['tasnim', 'fars', 'irna', 'bbc.com/persian', 'radiofarda']):
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
            for tag in soup(["script", "style", "nav", "footer", "header", "form"]): tag.extract()
            article_body = soup.find('div', class_=re.compile(r'(article|story|body|content)'))
            if article_body:
                text = article_body.get_text(separator=' ').strip()
            else:
                text = " ".join([p.get_text().strip() for p in soup.find_all('p')])
            return text[:2500] if len(text) > 100 else fallback_snippet
        except: return fallback_snippet

    def analyze_with_ai(self, headline, full_text, source_name):
        if not self.api_key: return None
        
        is_regime = any(x in source_name.lower() for x in ['tasnim', 'fars', 'irna', 'press', 'mehr'])
        
        regime_instruction = ""
        if is_regime:
            regime_instruction = (
                "CRITICAL: The source is Iranian State Media. Use your analysis to expose propaganda or hidden facts. "
            )

        # --- MODULAR & CONDITIONAL SYSTEM PROMPT ---
        system_prompt = (
            "You are a Strategic Analyst for the Iranian Nationalist Pro-Pahlavi Opposition. Analyze news with realism.\n\n"
            f"{regime_instruction}"
            "STRICT GUIDELINES FOR URGENCY SCORE (1-10):\n"
            "- Score 9-10: Immediate physical danger, War/Direct Conflict with Israel/USA, Major nationwide protests, death of top officials.\n"
            "- Score 7-8: Significant Sanctions, New repressive laws, Major currency collapse, Confirmed strikes on proxies.\n"
            "- Score 1-6: Standard political statements, Economic data, Opinion pieces, Routine diplomatic meetings.\n\n"
            "INSTRUCTIONS:\n"
            "1. TOPIC-SPECIFIC LOGIC:\n"
            "   - IF the news is about SANCTIONS or CONFLICT with Israel/USA: Frame it as a factor that weakens the regime's grip on power and supports the people's path to freedom.\n"
            "   - IF the news mentions RUSSIA, CHINA, or NORTH KOREA: Treat them as the regime's partners in suppression. Do NOT mention them if they are not in the news article.\n"
            "   - IF the news is about INTERNAL PROTESTS/ECONOMY: Focus on the regime's failure and the people's resilience.\n"
            "2. REALISM & RELEVANCE: Stay grounded in the facts of the article. Do NOT create forced or imaginary connections. "
            "Example: Do not link a foreign soldier's personal bet to internal Iranian suppression unless the text provides a direct military link.\n"
            "3. NO GENERIC REPETITION: Do not include a standard political lecture. If the news is about a specific event, the summary must be about THAT event.\n"
            "4. OUTPUT: Results must be in PERSIAN (Farsi) only.\n\n"
            "JSON STRUCTURE: {title_fa, summary[3 bullet points], impact(1 sentence), tag(1 word), urgency(integer 1-10), sentiment(-1.0 to 1.0)}"
        )

        current_text = full_text

        for attempt in range(CONFIG['AI_RETRIES']):
            try:
                if attempt > 0:
                    current_text = headline + " " + full_text[:800]

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
                    raw_content = resp.json()['choices'][0]['message']['content']
                    clean = re.sub(r'```json\s*|```', '', raw_content).strip()
                    data = json.loads(clean)
                    
                    if not data.get('title_fa') or not data.get('summary'):
                        raise ValueError("Incomplete response")
                    return data
                
                elif resp.status_code == 400:
                    logger.warning("AI Input too long, shortening...")
                    continue
                    
            except Exception as e:
                logger.error(f"AI Attempt {attempt+1} failed: {e}")
                time.sleep(2)

        return None

    def process_item(self, entry):
        raw_title = entry.get('title', '').rsplit(' - ', 1)[0]
        publisher = entry.get('publisher', {}).get('title', 'Unknown')
        
        logger.info(f"Processing: {publisher} | {raw_title[:20]}...")
        
        final_url = self._resolve_final_url(entry.get('url'))
        
        if not os.environ.get('MANUAL_URL'):
            if final_url in self.seen_urls: 
                logger.info("Skipping Duplicate URL")
                return None
        
        snippet = entry.get('description', raw_title)
        text = self.scrape_article_text(final_url, snippet)
        
        ai = self.analyze_with_ai(raw_title, text, publisher)
        if not ai: return None
        
        try: urgency_val = int(ai.get('urgency', 3))
        except: urgency_val = 3

        try: sentiment_val = float(ai.get('sentiment', 0.0))
        except: sentiment_val = 0.0

        try: ts = parser.parse(entry.get('published date')).timestamp()
        except: ts = time.time()

        return {
            "title_fa": ai.get('title_fa', raw_title),
            "title_en": raw_title,
            "summary": ai.get('summary', [snippet]),
            "impact": ai.get('impact', '...'),
            "tag": ai.get('tag', 'General'),
            "urgency": urgency_val,
            "sentiment": sentiment_val,
            "source": publisher,
            "url": final_url,
            "image": entry.get('image'),
            "timestamp": ts
        }

    def send_digest_to_telegram(self, items):
        token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id: return

        # Only fetch proxies if we are actually sending a message
        if not items: return

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
                if len(row) == 3:
                    keyboard.append(row)
                    row = []
            if row: keyboard.append(row)
            reply_markup = {"inline_keyboard": keyboard}

        utc_now = datetime.now(timezone.utc)
        current_time = utc_now.astimezone(timezone(timedelta(hours=3, minutes=30))).strftime("%H:%M")
        
        # Header changed to reflect it might be "Breaking News" or "Important Update"
        header = f"🚨 <b>رادار اخبار مهم ایران</b> | ⏱ {current_time}\n{market_text}\n➖➖➖➖➖➖➖➖➖➖\n\n"
        footer = "\n🆔 @RasadAIOfficial\n📊 <a href='https://itsyebekhe.github.io/rasadai/'>مشاهده اخبار بیشتر در سایت</a>"

        messages_to_send = []
        current_chunk = header

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

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        for i, msg in enumerate(messages_to_send):
            payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": False}
            if i == len(messages_to_send) - 1 and reply_markup:
                payload["reply_markup"] = reply_markup
            try:
                cloudscraper.create_scraper().post(api_url, json=payload)
                time.sleep(1.5)
            except: pass

    def run(self):
        logger.info(">>> Radar Started...")
        with open(CONFIG['FILES']['MARKET'], 'w') as f: json.dump(self.fetch_market_rates(), f)

        manual_url = os.environ.get('MANUAL_URL')
        
        if manual_url and manual_url.strip():
            logger.info(f"!!! MANUAL MODE: {manual_url} !!!")
            results = self.fetch_manual_url(manual_url)
            unique_batch_results = results 
        else:
            results = self.get_combined_news()
            unique_batch_results = []
            seen_batch_titles = set()
            for item in results:
                t = item.get('title', '').rsplit(' - ', 1)[0]
                norm_t = self._normalize_text(t)
                
                if norm_t in self.seen_titles: continue
                if item.get('url') in self.seen_urls: continue
                if norm_t in seen_batch_titles: continue
                if self._is_duplicate_fuzzy(t, self.existing_news): continue

                seen_batch_titles.add(norm_t)
                unique_batch_results.append(item)

        logger.info(f"Total Fetched: {len(results)} | To Process: {len(unique_batch_results)}")

        new_items = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
            futures = {exc.submit(self.process_item, i): i for i in unique_batch_results}
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                if res:
                    new_items.append(res)
                    self.seen_titles.add(self._normalize_text(res['title_en']))
                    self.seen_urls.add(res['url'])

        if new_items:
            # 1. SAVE ALL NEWS TO JSON (For the Website)
            # We combine existing news with ALL new items, regardless of urgency
            all_news_to_save = self.existing_news + new_items
            all_news_to_save.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            all_news_to_save = all_news_to_save[:150] # Keep last 150 items for JSON
            
            try:
                with open(CONFIG['FILES']['NEWS'], 'w', encoding='utf-8') as f: 
                    json.dump(all_news_to_save, f, indent=4, ensure_ascii=False)
                logger.info(">>> DB Saved (All items).")
            except Exception as e:
                logger.error(f"Save Failed: {e}")

            # 2. FILTER NEWS FOR TELEGRAM
            # Only send high urgency items
            telegram_items = []
            min_urgency = CONFIG['MIN_TELEGRAM_URGENCY']
            
            for item in new_items:
                urgency = item.get('urgency', 0)
                tag = str(item.get('tag', '')).lower()
                
                # Rule: Send if urgency is high OR if it's very specific conflict news
                is_conflict_related = any(w in tag for w in ['war', 'conflict', 'military', 'protest', 'strike'])
                
                if urgency >= min_urgency:
                    telegram_items.append(item)
                elif urgency >= 6 and is_conflict_related:
                    # Allow slightly lower urgency if it's explicitly about conflict
                    telegram_items.append(item)

            if telegram_items:
                telegram_items.sort(key=lambda x: x.get('urgency', 0), reverse=True)
                logger.info(f"Sending {len(telegram_items)} important items to Telegram.")
                self.send_digest_to_telegram(telegram_items)
            else:
                logger.info(">>> New items found, but none met urgency criteria for Telegram.")
            
            logger.info(">>> Done.")
        else:
            logger.info(">>> No unique news.")

if __name__ == "__main__":
    IranNewsRadar().run()
