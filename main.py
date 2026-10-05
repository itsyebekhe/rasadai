import os
import json
import time
import logging
import html
import re
import random
import tempfile
import math
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import cloudscraper
import trafilatura
import concurrent.futures
import feedparser
from urllib.parse import quote, unquote, urlparse, urlunparse
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from gnews import GNews
from ddgs import DDGS
from dateutil import parser
import hashlib

# --- CONFIGURATION ---
CONFIG = {
    'SEARCH_QUERY': 'Iran AND (Israel OR USA OR nuclear OR conflict OR sanctions OR currency OR IRGC)',
    'SEARCH_QUERIES': [
        'Iran (Israel OR Gaza OR Hezbollah OR Houthis) (attack OR strike OR missile OR drone)',
        'Iran (nuclear OR IAEA OR enrichment OR sanctions)',
        'Iran (dollar OR rial OR currency OR IRGC OR economy)',
        '(Trump OR "Donald Trump") (Iran OR "regime change" OR sanctions OR nuclear OR Israel)',
        '(Netanyahu OR "Benjamin Netanyahu") (Iran OR strike OR nuclear OR Hezbollah OR IRGC)',
        '("Reza Pahlavi" OR "شاهزاده رضا پهلوی" OR "Pahlavi") (Iran OR opposition OR transition OR speech)',
        '("IRGC" OR "Qhalibaf" OR "Qaani" OR "سپاه پاسداران") (Iran OR missile OR proxies OR threat)'
    ],
    'SOURCE_PRIORITY': {
        'bbc.com': 10, 'radiofarda.com': 10, 'iranintl.com': 9,
        'independentpersian.com': 8, 'dw.com': 8,
        'reuters.com': 9, 'apnews.com': 8, 'aljazeera.com': 7,
        'theguardian.com': 7, 'nytimes.com': 7,
        'tasnimnews.com': 4, 'farsnews.ir': 4, 'irna.ir': 4,
        'mehrnews.com': 4, 'presstv.ir': 3,
    },
    'FILES': {
        'NEWS': 'news.json',
        'MARKET': 'market.json',
        'DAILY_SUMMARY': 'daily_summary.json',
        'SCHEDULE_STATE': 'schedule_state.json',
        'EMBEDDINGS_CACHE': 'embeddings_cache.json'
    },
    'TELEGRAM': {
        'BOT_TOKEN': os.environ.get('TG_BOT_TOKEN'),
        'CHANNEL_ID': os.environ.get('TG_CHANNEL_ID')
    },
    'PROXY_URL': 'https://raw.githubusercontent.com/itsyebekhe/MTProtoNexus/refs/heads/gh-pages/extracted_proxies.json',
    'TIMEOUT': 12,
    'AI_TIMEOUT': 35,
    'MAX_WORKERS': 4,
    'MAX_CANDIDATES': 14,
    'MAX_TEXT_CHARS': 1800,
    'MIN_TEXT_LEN': 100,
    'GEMINI_KEY': os.environ.get('GEMINI_API_KEY'),
    'GEMINI_MODEL': os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash'),
    'GEMINI_EMBED_MODEL': 'text-embedding-004',
    'AI_RETRIES': 3,
    'MIN_TELEGRAM_URGENCY': 7,
    'MAX_NEWS_AGE_HOURS': 18,
    'HISTORY_SIZE': 300,
    'SEMANTIC_DEDUPE_THRESHOLD': 0.83,
}

BAD_IMAGE_HOSTS = (
    'lh3.googleusercontent.com', 'lh4.googleusercontent.com', 'lh5.googleusercontent.com',
    'lh6.googleusercontent.com', 'encrypted-tbn0.gstatic.com', 'encrypted-tbn1.gstatic.com',
    'encrypted-tbn2.gstatic.com', 'encrypted-tbn3.gstatic.com', 'news.google.com',
    'www.google.com', 'google.com'
)

PROXY_NAMES = [
    "کوروش", "داریوش", "کاوه", "رستم", "آرش", "سیاوش", "بابک",
    "خشایار", "سورنا", "آریوبرزن", "میترا", "آناهیتا", "فریدون",
    "جمشید", "زال", "بهرام", "شاپور", "آرتابان", "پیروز", "مازیار",
    "تهمینه", "گردآفرید", "سهراب", "آتوسا", "رکسانا", "ماندانا"
]

# --- GEMINI STRUCTURED SCHEMAS ---
BATCH_ANALYSIS_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "index": {"type": "INTEGER"},
            "title_fa": {"type": "STRING"},
            "summary": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            },
            "impact": {"type": "STRING"},
            "tag": {"type": "STRING"},
            "urgency": {"type": "INTEGER"},
            "sentiment": {"type": "NUMBER"}
        },
        "required": ["index", "title_fa", "summary", "impact", "tag", "urgency", "sentiment"]
    }
}

DAILY_SUMMARY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "date": {"type": "STRING"},
        "executive_tldr": {"type": "STRING"},
        "themes": {
            "type": "ARRAY",
            "items": {"type": "STRING"}
        },
        "regime_vulnerabilities": {
            "type": "OBJECT",
            "properties": {
                "regime_internal_friction": {"type": "STRING"},
                "infrastructure_vulnerability": {"type": "STRING"},
                "sanctions_evasion_watch": {"type": "STRING"}
            },
            "required": ["regime_internal_friction", "infrastructure_vulnerability", "sanctions_evasion_watch"]
        },
        "proxy_network_status": {"type": "STRING"},
        "opposition_momentum": {"type": "STRING"},
        "regime_narrative": {"type": "STRING"},
        "predicted_regime_response": {"type": "STRING"},
        "forecast": {
            "type": "OBJECT",
            "properties": {
                "most_likely_scenario": {"type": "STRING"},
                "regime_worst_case_scenario": {"type": "STRING"},
                "flashpoint_indicator": {"type": "STRING"}
            },
            "required": ["most_likely_scenario", "regime_worst_case_scenario", "flashpoint_indicator"]
        },
        "probability_matrix": {
            "type": "OBJECT",
            "properties": {
                "military_escalation_percent": {"type": "INTEGER"},
                "economic_shock_percent": {"type": "INTEGER"},
                "domestic_unrest_percent": {"type": "INTEGER"},
                "regime_defection_risk_percent": {"type": "INTEGER"}
            },
            "required": ["military_escalation_percent", "economic_shock_percent", "domestic_unrest_percent", "regime_defection_risk_percent"]
        },
        "key_figures_in_focus": {
            "type": "ARRAY",
            "items": {"type": "STRING"}
        },
        "strategic_assessment": {"type": "STRING"},
        "market_impact": {"type": "STRING"},
        "currency_outlook": {"type": "STRING"},
        "risk_level": {"type": "INTEGER"},
        "change_from_previous": {"type": "STRING"}
    },
    "required": [
        "date", "executive_tldr", "themes", "regime_vulnerabilities", "proxy_network_status",
        "opposition_momentum", "regime_narrative", "predicted_regime_response", "forecast",
        "probability_matrix", "key_figures_in_focus", "strategic_assessment", "market_impact",
        "currency_outlook", "risk_level", "change_from_previous"
    ]
}

BULLETIN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "edition": {"type": "STRING"},
        "title": {"type": "STRING"},
        "time": {"type": "STRING"},
        "date": {"type": "STRING"},
        "bullets": {
            "type": "ARRAY",
            "items": {"type": "STRING"}
        },
        "bottom_line": {"type": "STRING"}
    },
    "required": ["edition", "title", "time", "date", "bullets", "bottom_line"]
}

SPECIAL_REPORT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "topic_tag": {"type": "STRING"},
        "headline": {"type": "STRING"},
        "lead_paragraph": {"type": "STRING"},
        "key_findings": {
            "type": "ARRAY",
            "items": {"type": "STRING"}
        },
        "regime_vs_reality": {"type": "STRING"},
        "strategic_outlook": {"type": "STRING"}
    },
    "required": ["topic_tag", "headline", "lead_paragraph", "key_findings", "regime_vs_reality", "strategic_outlook"]
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()


class IranNewsRadar:
    def __init__(self):
        # 1. Scraper for general websites (handles Cloudflare)
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
        )
        self.scraper.headers.update({
            'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8',
            'Cache-Control': 'no-cache',
        })

        # 2. Optimized, low-overhead session specifically for Gemini APIs
        self.ai_session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        self.ai_session.mount('https://', adapter)
        self.ai_session.headers.update({'Content-Type': 'application/json'})

        self.existing_news = self._load_existing_news()
        self.embeddings_cache = self._load_embeddings_cache()

        self.seen_urls = set()
        self.seen_titles = set()
        self.recent_title_hashes = set()
        self.failed_hosts = set()

        for item in self.existing_news:
            if item.get('url'):
                self.seen_urls.add(self._clean_url(item['url']))
            for key in ('title_en', 'title_fa'):
                if item.get(key):
                    self.seen_titles.add(self._normalize_text(item[key]))
                    self.recent_title_hashes.add(self._title_hash(item[key]))

        if len(self.recent_title_hashes) > 200:
            self.recent_title_hashes = set(list(self.recent_title_hashes)[-150:])

        self.gnews_en = GNews(language='en', country='US', period='4h', max_results=5)

    # ───────────────────────── AI API Core ─────────────────────────

    def _call_gemini(self, system_prompt, user_prompt, schema=None, temperature=0.2):
        if not CONFIG.get('GEMINI_KEY'):
            logger.error("GEMINI_API_KEY is not configured.")
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{CONFIG['GEMINI_MODEL']}:generateContent?key={CONFIG['GEMINI_KEY']}"

        gen_config = {"temperature": temperature}
        if schema:
            gen_config["response_mime_type"] = "application/json"
            gen_config["response_schema"] = schema

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": gen_config
        }

        for attempt in range(CONFIG['AI_RETRIES']):
            try:
                resp = self.ai_session.post(url, json=payload, timeout=CONFIG['AI_TIMEOUT'])
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data['candidates'][0]['content']['parts'][0]['text']
                    return json.loads(raw)
                else:
                    logger.error(f"Gemini API Error [{resp.status_code}]: {resp.text[:250]}")
            except Exception as e:
                logger.warning(f"Gemini API attempt {attempt + 1} failed: {e}")
                time.sleep(1.5 * (attempt + 1))
        return None

    def get_embedding(self, text):
        """Fetch normalized vector embedding via text-embedding-004."""
        if not text or not CONFIG.get('GEMINI_KEY'):
            return None

        cache_key = hashlib.md5(text.strip().lower().encode('utf-8')).hexdigest()
        if cache_key in self.embeddings_cache:
            return self.embeddings_cache[cache_key]

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{CONFIG['GEMINI_EMBED_MODEL']}:embedContent?key={CONFIG['GEMINI_KEY']}"
        payload = {
            "model": f"models/{CONFIG['GEMINI_EMBED_MODEL']}",
            "content": {"parts": [{"text": text[:600]}]}
        }

        try:
            resp = self.ai_session.post(url, json=payload, timeout=8)
            if resp.status_code == 200:
                values = resp.json().get('embedding', {}).get('values', [])
                if values:
                    # Cache up to 600 embeddings
                    if len(self.embeddings_cache) > 600:
                        self.embeddings_cache.pop(next(iter(self.embeddings_cache)))
                    self.embeddings_cache[cache_key] = values
                    return values
        except Exception as e:
            logger.debug(f"Embedding request failed: {e}")
        return None

    @staticmethod
    def _cosine_similarity(vec1, vec2):
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    # ───────────────────────── Helpers ─────────────────────────

    def _get_tehran_time(self):
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo("Asia/Tehran"))
        except ImportError:
            return datetime.now(timezone(timedelta(hours=3, minutes=30)))

    def _load_existing_news(self):
        path = CONFIG['FILES']['NEWS']
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _load_embeddings_cache(self):
        path = CONFIG['FILES']['EMBEDDINGS_CACHE']
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_embeddings_cache(self):
        self._atomic_json_dump(CONFIG['FILES']['EMBEDDINGS_CACHE'], self.embeddings_cache)

    def _is_schedule_already_sent(self, slot_key):
        path = CONFIG['FILES']['SCHEDULE_STATE']
        if not os.path.exists(path):
            return False
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f).get(slot_key, False)
        except Exception:
            return False

    def _mark_schedule_as_sent(self, slot_key):
        path = CONFIG['FILES']['SCHEDULE_STATE']
        data = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[slot_key] = True
        self._atomic_json_dump(path, data)

    def _clean_url(self, url):
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
            return clean.rstrip('/')
        except Exception:
            return url

    def _normalize_text(self, text):
        if not text:
            return ""
        text = text.replace('ي', 'ی').replace('ك', 'ک').replace('\u200c', ' ')
        clean = re.sub(r'[^\w\s]', '', text.lower())
        return re.sub(r'\s+', '', clean)

    def _title_hash(self, title):
        return hashlib.md5(self._normalize_text(title).encode('utf-8')).hexdigest()

    def _is_duplicate_hybrid(self, new_title, comparison_pool):
        """Combined Semantic Embedding + Jaccard Token Deduplication."""
        norm_title = self._normalize_text(new_title)
        if norm_title in self.seen_titles:
            return True

        new_emb = self.get_embedding(new_title)
        pool = comparison_pool[:100]

        for item in pool:
            existing_title = item.get('title_en') or item.get('title_fa') or item.get('title', '')
            if not existing_title:
                continue

            # 1. Semantic Vector Match
            if new_emb:
                existing_emb = self.get_embedding(existing_title)
                if existing_emb:
                    sim = self._cosine_similarity(new_emb, existing_emb)
                    if sim >= CONFIG['SEMANTIC_DEDUPE_THRESHOLD']:
                        return True

            # 2. Token overlap fallback
            tokens_new = set(norm_title.split())
            tokens_exist = set(self._normalize_text(existing_title).split())
            if tokens_new and tokens_exist:
                overlap = len(tokens_new.intersection(tokens_exist)) / max(len(tokens_new), 1)
                if overlap > 0.75:
                    return True

        return False

    def _domain_score(self, url, publisher=""):
        try:
            host = urlparse(url or '').netloc.lower().replace('www.', '')
            for domain, score in CONFIG['SOURCE_PRIORITY'].items():
                if domain in host:
                    return score
        except Exception:
            pass
        pub = (publisher or '').lower()
        for domain, score in CONFIG['SOURCE_PRIORITY'].items():
            if domain.split('.')[0] in pub:
                return score
        return 3

    def _cheap_urgency_hint(self, title, publisher=""):
        t = (title or '').lower()
        score = 3
        high = ['attack', 'strike', 'missile', 'killed', 'nuclear', 'drone', 'war', 'حمله', 'موشک', 'هسته‌ای', 'پهپاد', 'کشته', 'انفجار']
        mid = ['sanction', 'dollar', 'currency', 'irgc', 'protest', 'تحریم', 'دلار', 'ارز', 'سپاه', 'اعتراض']
        if any(w in t for w in high):
            score += 3
        if any(w in t for w in mid):
            score += 2
        if self._domain_score('', publisher) >= 8:
            score += 1
        return min(score, 9)

    def _generate_news_id(self, clean_url):
        return hashlib.md5((clean_url or str(time.time())).encode('utf-8')).hexdigest()[:10]

    def _is_valid_image_url(self, url):
        if not url or not isinstance(url, str):
            return False
        u = url.strip()
        if not u.startswith(('http://', 'https://')) or u.startswith('data:'):
            return False
        try:
            host = urlparse(u).netloc.lower().replace('www.', '')
            if any(bad in host for bad in BAD_IMAGE_HOSTS):
                return False
            if 'googleusercontent.com' in host and ('=s0' in u or 'w300' in u or '-rw' in u):
                return False
        except Exception:
            return False
        return True

    def _get_fallback_image(self, text_or_tag):
        t = str(text_or_tag).lower()
        if any(w in t for w in ['ship', 'navy', 'sea', 'strait', 'hormuz', 'دریایی', 'کشتی', 'خلیج']):
            return 'https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?auto=format&fit=crop&w=1200&q=80'
        if any(w in t for w in ['missile', 'strike', 'war', 'army', 'military', 'نظامی', 'موشک', 'پهپاد', 'حمله']):
            return 'https://images.unsplash.com/photo-1585829365295-ab7cd400c167?auto=format&fit=crop&w=1200&q=80'
        if any(w in t for w in ['nuclear', 'atomic', 'iaea', 'هسته‌ای', 'غنی‌سازی']):
            return 'https://images.unsplash.com/photo-1581092160607-ee22621dd758?auto=format&fit=crop&w=1200&q=80'
        if any(w in t for w in ['currency', 'dollar', 'economy', 'تومان', 'دلار', 'تحریم', 'ارز']):
            return 'https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=1200&q=80'
        return 'https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&w=1200&q=80'

    def _pick_image(self, *candidates, fallback_text=''):
        for c in candidates:
            if self._is_valid_image_url(c):
                return c
        return self._get_fallback_image(fallback_text)

    # ───────────────────────── Proxies & Market ─────────────────────────

    def fetch_best_proxies(self):
        try:
            resp = self.scraper.get(CONFIG['PROXY_URL'], timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                online = [p for p in data if p.get('status') == 'Online']
                online.sort(key=lambda x: x.get('latency') if x.get('latency') is not None else 99999)
                return online[:9]
        except Exception:
            pass
        return []

    def fetch_market_rates(self):
        data = {"usd": "نامشخص", "oil": "نامشخص", "updated": "--:--"}
        try:
            resp = self.scraper.get("https://alanchand.com/en/currencies-price/usd", timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'lxml')
                usd = soup.find('input', attrs={'data-curr': 'tmn'})
                if usd:
                    val = usd.get('data-price') or usd.get('value')
                    if val:
                        data["usd"] = f"{int(int(val.replace(',', '')) / 10):,}"
        except Exception:
            pass
        try:
            resp = self.scraper.get("https://oilprice.com/oil-price-charts/46", timeout=10)
            soup = BeautifulSoup(resp.text, 'lxml')
            oil = soup.select_one(".last_price")
            if oil:
                data["oil"] = oil.get_text().strip()
        except Exception:
            pass
        data["updated"] = time.strftime("%H:%M")
        return data

    # ───────────────────────── Web Scraping & Content ─────────────────────────

    def fetch_gnews(self):
        try:
            return self.gnews_en.get_news(CONFIG['SEARCH_QUERY']) or []
        except Exception as e:
            logger.error(f"GNews Error: {e}")
            return []

    def fetch_duckduckgo(self, query, region='wt-wt', max_results=8):
        results = []
        try:
            ddgs = DDGS()
            for r in ddgs.news(query=query, region=region, safesearch="off", timelimit="d", max_results=max_results):
                results.append({
                    'title': r.get('title'),
                    'url': r.get('url'),
                    'publisher': {'title': r.get('source')},
                    'published date': r.get('date'),
                    'description': r.get('body'),
                    'image': r.get('image')
                })
        except Exception:
            return self.fetch_bing_rss(query)
        return results

    def fetch_bing_rss(self, query):
        results = []
        try:
            feed = feedparser.parse(f"https://www.bing.com/news/search?q={quote(query)}&format=rss")
            for entry in feed.entries:
                pub = getattr(entry, 'news_source', getattr(entry.source, 'title', 'Bing News') if hasattr(entry, 'source') else 'Bing News')
                link = entry.link
                if "apiclick.aspx" in link:
                    m = re.search(r'[?&]url=([^&]+)', link)
                    if m:
                        link = unquote(m.group(1))
                img = None
                if hasattr(entry, 'news_image'):
                    img = entry.news_image.replace('{0}', '700').replace('{1}', '400') if '{0}' in entry.news_image else entry.news_image

                results.append({
                    'title': entry.title,
                    'url': link,
                    'publisher': {'title': pub},
                    'published date': entry.published,
                    'description': entry.summary if hasattr(entry, 'summary') else entry.title,
                    'image': img
                })
        except Exception as e:
            logger.error(f"Bing RSS Error: {e}")
        return results

    def get_combined_news(self):
        all_entries = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [
                ex.submit(self.fetch_gnews),
                ex.submit(self.fetch_bing_rss, CONFIG['SEARCH_QUERY'])
            ]
            for q in CONFIG.get('SEARCH_QUERIES', []):
                futs.append(ex.submit(self.fetch_duckduckgo, q, 'wt-wt', 6))

            for f_q in [
                'site:independentpersian.com شاهزاده رضا پهلوی OR ترامپ OR نتانیاهو',
                'site:iranintl.com ترامپ OR نتانیاهو OR پهلوی OR سپاه',
                'site:radiofarda.com رضا پهلوی OR ترامپ OR نتانیاهو'
            ]:
                futs.append(ex.submit(self.fetch_duckduckgo, f_q, 'wt-wt', 4))

            for fut in concurrent.futures.as_completed(futs):
                try:
                    all_entries.extend(fut.result() or [])
                except Exception as e:
                    logger.warning(f"Search fetch error: {e}")

        logger.info(f"Raw search results gathered: {len(all_entries)}")
        return all_entries

    def _resolve_final_url(self, url, raw_title=None):
        if not url:
            return None
        if "news.google.com" not in url:
            return url
        try:
            m = re.search(r'articles/([^?&]+)', url)
            if m:
                padded = m.group(1) + '=' * (-len(m.group(1)) % 4)
                import base64
                decoded = base64.urlsafe_b64decode(padded.encode('ascii'))
                for u in re.findall(rb'https?://[a-zA-Z0-9.\-_~:/?#[\]@!$&\'()*+,;=%]+', decoded):
                    u_str = u.decode('utf-8', errors='ignore')
                    if "google.com" not in u_str:
                        return u_str
        except Exception:
            pass

        try:
            resp = self.scraper.get(url, allow_redirects=True, timeout=8)
            if resp.status_code == 200 and "news.google.com" not in resp.url:
                return resp.url
        except Exception:
            pass
        return url

    def scrape_article_data(self, final_url, fallback_snippet, raw_image=None):
        if not final_url or final_url.lower().endswith('.pdf'):
            return fallback_snippet, self._get_fallback_image(fallback_snippet)

        host = urlparse(final_url).netloc.lower()
        if host in self.failed_hosts:
            return fallback_snippet, self._pick_image(raw_image, fallback_text=fallback_snippet)

        extracted_text = fallback_snippet
        extracted_image = raw_image if self._is_valid_image_url(raw_image) else None
        max_chars = CONFIG.get('MAX_TEXT_CHARS', 1800)

        try:
            downloaded = trafilatura.fetch_url(final_url)
            if downloaded:
                text = trafilatura.extract(downloaded, include_comments=False, favor_precision=True)
                if text and len(text.strip()) > CONFIG['MIN_TEXT_LEN']:
                    extracted_text = re.sub(r'\s+', ' ', text).strip()[:max_chars]
                meta = trafilatura.extract_metadata(downloaded)
                if meta and getattr(meta, 'image', None) and self._is_valid_image_url(meta.image):
                    extracted_image = extracted_image or meta.image
        except Exception:
            self.failed_hosts.add(host)

        if not extracted_image or len(extracted_text) < CONFIG['MIN_TEXT_LEN']:
            try:
                resp = self.scraper.get(final_url, timeout=CONFIG['TIMEOUT'])
                soup = BeautifulSoup(resp.text, 'lxml')
                if len(extracted_text) < CONFIG['MIN_TEXT_LEN']:
                    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                        tag.decompose()
                    clean = ' '.join([p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 40][:12])
                    if len(clean) > CONFIG['MIN_TEXT_LEN']:
                        extracted_text = clean[:max_chars]

                if not extracted_image:
                    for prop in [('property', 'og:image'), ('name', 'twitter:image')]:
                        tag = soup.find('meta', attrs={prop[0]: prop[1]})
                        if tag and tag.get('content') and self._is_valid_image_url(tag['content']):
                            extracted_image = tag['content'].strip()
                            break
            except Exception:
                self.failed_hosts.add(host)

        return extracted_text, self._pick_image(extracted_image, raw_image, fallback_text=extracted_text)

    # ───────────────────────── AI Analysis Core & Deduplication ─────────────────────────

    def batch_analyze_with_gemini(self, candidates_data):
        """Analyzes multiple candidate news articles in a single request with guaranteed schema output."""
        if not candidates_data or not CONFIG.get('GEMINI_KEY'):
            return {}

        system_prompt = (
            "تو یک تحلیل‌گر ارشد و تیزبین ژئوپلیتیک، مسلط به زبان فارسی روان و ضربتی هستی.\n"
            "وظیفه تو استخراج چکیده، ارزیابی دقیق فوریت و اثرگذاری رویدادها است.\n\n"
            "🔴 **قوانین اعتبارسنجی و جلوگیری از توهم تحلیلی (Grounding & Anti-Hallucination):**\n"
            "۱. تمام ادعاها و فکت‌ها باید منحصراً ریشه در متن ورودی (TEXT) داشته باشند. هیچ سناریوی خیالی یا فرضی نساز.\n"
            "۲. اگر خبری جزئیات کافی ندارد، خلاصه را کوتاه نگه دار و از زیاده‌گویی بپرهیز.\n\n"
            "🎯 **پوشش مواضع چهره‌ها:**\n"
            "- سخنان دونالد ترامپ، بنیامین نتانیاهو، و شاهزاده رضا پهلوی را صریح، شفاف و بدون تغییر سوگیری پوشش بده.\n"
            "- ادعاهای پروپاگاندای فرماندهان سپاه را با عیار واقعیت میدانی بسنج.\n\n"
            "⚖️ **معیار کالیبراسیون دقیق نمره فوریت (Urgency 1-10):**\n"
            "- نمره ۱۰: حملات مستقیم موشکی/نظامی به مواضع یکدیگر، اصابت سنگین، اعلام جنگ رسمی.\n"
            "- نمره ۸-۹: ترور یا هلاکت فرماندهان ارشد، تصویب تحریم‌های سنگین جدید نفتی/بانکی، غنی‌سازی ۹۰٪.\n"
            "- نمره ۶-۷: تحرکات ناوگان‌ها، مانورهای هشدارآمیز، تهدید مستقیم لفظی سران کشورها، جهش ناگهانی ارز.\n"
            "- نمره ۴-۵: دیپلماسی روتین منطقه‌ای، مذاکرات فنی آژانس اتمی، موضع‌گیری‌های معمول اداری.\n"
            "- نمره ۱-۳: تعارفات دیپلماتیک و گزارش‌های تکراری و کم‌اثر.\n\n"
            "قواعد نگارش:\n"
            "- کلمات ممنوعه: ('به نظر می‌رسد'، 'نشان‌دهنده این است که'، 'شایان ذکر است'، 'در نهایت').\n"
            "- زبان کاملاً روان، پرانرژی و جذاب ژورنالیستی فارسی."
        )

        items_input = []
        for item in candidates_data:
            items_input.append(
                f"--- ITEM INDEX: {item['index']} ---\n"
                f"SOURCE: {item['source']}\n"
                f"HEADLINE: {item['headline']}\n"
                f"TEXT: {item['text'][:1100]}\n"
            )

        user_prompt = "لطفاً تمامی آیتم‌های زیر را دقیق تحلیل کن و مستقیماً در قالب آرایه JSON با اسکیما خروجی بده:\n\n" + "\n".join(items_input)

        data = self._call_gemini(system_prompt, user_prompt, schema=BATCH_ANALYSIS_SCHEMA, temperature=0.15)
        if isinstance(data, list):
            return {item['index']: item for item in data if 'index' in item}
        return {}

    def generate_daily_summary(self):
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        todays_items = [
            item for item in self.existing_news
            if datetime.fromtimestamp(item.get("timestamp", 0), timezone.utc) >= today_start
        ]
        if len(todays_items) < 3:
            return None

        todays_items.sort(key=lambda x: x.get("urgency", 0), reverse=True)
        news_context = [
            f"Title: {item.get('title_en')}\nSource: {item.get('source')}\n"
            f"Urgency: {item.get('urgency')}\nTag: {item.get('tag')}\n"
            f"Impact: {item.get('impact')}\nSummary: {' '.join(item.get('summary', []))}"
            for item in todays_items[:22]
        ]
        news_block = "\n\n".join(news_context)

        system_prompt = (
            "You are a top-tier geopolitical strategist analyzing developments regarding the Iranian regime, opposition, and regional conflicts.\n"
            "GROUNDING RULES:\n"
            "- Base every assessment strictly on today's monitored events. DO NOT hallucinate regime factional splits or shadow actions unless evidenced.\n"
            "- If no data is observed for a vulnerability field, explicitly write 'موردی در داده‌های امروز رصد نشد'.\n"
            "- Output strictly in Persian adhering to the structured JSON schema."
        )

        user_prompt = f"TODAYS EVENTS:\n{news_block}"
        return self._call_gemini(system_prompt, user_prompt, schema=DAILY_SUMMARY_SCHEMA, temperature=0.2)

    def generate_scheduled_bulletin(self):
        tehran_time = self._get_tehran_time()
        hour = tehran_time.hour
        edition_key, edition_title = (
            ("morning", "بولتـن صبحگاهی") if 6 <= hour < 12 else
            ("midday", "بولتـن نیمروزی") if 12 <= hour < 18 else
            ("evening", "بولتـن شبانگاهی (جمع‌بندی روز)")
        )

        top_items = sorted(self.existing_news, key=lambda x: x.get('urgency', 0), reverse=True)[:5]
        if not top_items:
            return None

        news_text = "\n".join([f"- {i.get('title_fa')}: {' '.join(i.get('summary', []))}" for i in top_items])
        system_prompt = f"تو سردبیر ارشد هستی. برای '{edition_title}' یک مرور خبری ۳ دقیقه‌ای، صریح و شفاف به زبان فارسی آماده کن."
        user_prompt = f"زمان: {tehran_time.strftime('%H:%M')} | اخبار برتر:\n{news_text}"

        data = self._call_gemini(system_prompt, user_prompt, schema=BULLETIN_SCHEMA, temperature=0.2)
        if data:
            self._atomic_json_dump('bulletins.json', data)
        return data

    def generate_special_topic_report(self):
        """Identifies the single biggest strategic theme using semantic clustering and compiles a deep report."""
        if len(self.existing_news) < 4:
            return None

        recent_news = self.existing_news[:25]
        headlines = "\n".join([f"{idx}: [{i.get('tag')}] {i.get('title_fa')}" for idx, i in enumerate(recent_news)])

        # Step 1: AI selects the most crucial story thread
        cluster_prompt = (
            "از میان تیترهای زیر، بحرانی‌ترین و مهم‌ترین پرونده خبری روز را انتخاب کن "
            "و اندیس موارد مرتبط را در یک سطر بنویس (مثلاً: 0, 3, 5). فقط اندیس‌ها:"
        )
        try:
            sel = self._call_gemini(cluster_prompt, headlines, temperature=0.1)
            # Fallback if raw text returned
            indices = [int(n) for n in re.findall(r'\d+', str(sel))]
            cluster_items = [recent_news[i] for i in indices if i < len(recent_news)]
        except Exception:
            cluster_items = recent_news[:5]

        if len(cluster_items) < 2:
            cluster_items = recent_news[:4]

        cluster_context = "\n---\n".join([
            f"تیتر: {i.get('title_fa')}\nتحلیل: {i.get('impact')}\nخلاصه: {' '.join(i.get('summary', []))}"
            for i in cluster_items
        ])

        system_prompt = (
            "تو هیئت تحریریه گزارش‌های ویژه رصد هستی. برای پرونده منتخب، گزارشی عمیق، خواندنی و مستند بر اساس فکت‌ها بنویس.\n"
            "قانون: هرگز از عبارات مجهول و حدس‌های بی‌اساس استفاده نکن. مقایسه ادعای حاکمیت با واقعیت را کاملاً مستدل بنویس."
        )

        data = self._call_gemini(system_prompt, cluster_context, schema=SPECIAL_REPORT_SCHEMA, temperature=0.2)
        if data:
            self._atomic_json_dump('special_reports.json', data)
        return data

    # ───────────────────────── Telegram Formatting & Dispatch ─────────────────────────

    def send_special_report_to_telegram(self, report):
        token, chat_id = CONFIG['TELEGRAM']['BOT_TOKEN'], CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id or not report:
            return False

        def esc(s): return html.escape(str(s or ''), quote=False)
        tehran_now = self._get_tehran_time()
        time_str = tehran_now.strftime("%H:%M")
        date_str = tehran_now.strftime("%Y/%m/%d")

        headline = esc(report.get('headline'))
        tag = esc(report.get('topic_tag', 'پرونده_ویژه')).replace(' ', '_')
        findings_li = "".join([f"<li>🔹 {esc(f)}</li>\n" for f in report.get('key_findings', [])])

        rich_html = (
            f"<h1>📂 پرونده ویژه شبانگاهی: {headline}</h1>\n"
            f"<p>⏱ <b>زمان صدور:</b> {time_str} — {date_str} (تهران) | 🏷 #{tag}</p>\n<hr/>\n"
            f"<p>📌 <b>اصل ماجرا:</b> {esc(report.get('lead_paragraph'))}</p>\n"
            f"<h2>🔍 یافته‌های کلیدی و مستند</h2>\n<ul>\n{findings_li}</ul>\n<hr/>\n"
            f"<h2>⚔️ ادعای حکومت در برابر واقعیت میدانی</h2>\n<p>{esc(report.get('regime_vs_reality'))}</p>\n"
            f"<h2>🔮 چشم‌انداز استراتژیک</h2>\n<p>{esc(report.get('strategic_outlook'))}</p>\n"
            f"<footer><p>📊 <a href=\"https://itsyebekhe.github.io/rasadai/\">مشاهده داشبورد رصد</a> | 🆔 @RasadAIOfficial</p></footer>\n"
        )

        reply_markup = {"inline_keyboard": [[
            {"text": "📊 مطالعه پرونده در داشبورد", "url": "https://itsyebekhe.github.io/rasadai/"},
            {"text": "🛡 پروکسی‌های فعال", "url": "https://itsyebekhe.github.io/MTProtoNexus/"}
        ]]}

        try:
            resp = self.scraper.post(f"https://api.telegram.org/bot{token}/sendRichMessage", json={
                "chat_id": chat_id,
                "rich_message": {"html": rich_html, "is_rtl": True},
                "reply_markup": reply_markup
            }, timeout=25)
            if resp.status_code == 200:
                return True
        except Exception:
            pass

        # Standard Fallback
        findings_text = "".join([f"🔹 {esc(f)}\n" for f in report.get('key_findings', [])])
        fallback = (
            f"📂 <b>پرونده ویژه شبانگاهی: {headline}</b>\n"
            f"⏱ {time_str} — {date_str} | 🏷 #{tag}\n\n"
            f"📌 <b>اصل ماجرا:</b>\n{esc(report.get('lead_paragraph'))}\n\n"
            f"🔍 <b>یافته‌های کلیدی:</b>\n{findings_text}\n"
            f"⚔️ <b>واقعیت میدانی:</b>\n{esc(report.get('regime_vs_reality'))}\n\n"
            f"🔮 <b>چشم‌انداز:</b>\n{esc(report.get('strategic_outlook'))}\n\n"
            f"📊 <a href=\"https://itsyebekhe.github.io/rasadai/\">مشاهده در داشبورد</a> | 🆔 @RasadAIOfficial"
        )
        resp2 = self.scraper.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            "chat_id": chat_id, "text": fallback, "parse_mode": "HTML",
            "disable_web_page_preview": True, "reply_markup": reply_markup
        }, timeout=20)
        return resp2.status_code == 200

    def send_bulletin_to_telegram(self, bulletin):
        token, chat_id = CONFIG['TELEGRAM']['BOT_TOKEN'], CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id or not bulletin:
            return False

        def esc(s): return html.escape(str(s or ''), quote=False)
        title = esc(bulletin.get('title'))
        bullets_li = "".join([f"<li>🔹 {esc(b)}</li>\n" for b in bulletin.get('bullets', [])])

        rich_html = (
            f"<h1>🗞 {title}</h1>\n"
            f"<p>⏱ <b>زمان:</b> {esc(bulletin.get('time'))} — {esc(bulletin.get('date'))} (تهران)</p>\n<hr/>\n"
            f"<h2>📌 سرخط نکات کلیدی</h2>\n<ul>\n{bullets_li}</ul>\n<hr/>\n"
            f"<p>💡 <b>جمع‌بندی تحلیلی:</b> {esc(bulletin.get('bottom_line'))}</p>\n"
            f"<footer><p>📊 <a href=\"https://itsyebekhe.github.io/rasadai/\">مشاهده در داشبورد</a> | 🆔 @RasadAIOfficial</p></footer>\n"
        )

        reply_markup = {"inline_keyboard": [[
            {"text": "📊 مطالعه کامل بولتن", "url": "https://itsyebekhe.github.io/rasadai/"},
            {"text": "🛡 پروکسی‌های فعال", "url": "https://itsyebekhe.github.io/MTProtoNexus/"}
        ]]}

        try:
            resp = self.scraper.post(f"https://api.telegram.org/bot{token}/sendRichMessage", json={
                "chat_id": chat_id,
                "rich_message": {"html": rich_html, "is_rtl": True},
                "reply_markup": reply_markup
            }, timeout=25)
            if resp.status_code == 200:
                return True
        except Exception:
            pass

        bullets_txt = "".join([f"🔹 {esc(b)}\n\n" for b in bulletin.get('bullets', [])])
        fallback = (
            f"🗞 <b>{title}</b>\n⏱ {esc(bulletin.get('time'))} — {esc(bulletin.get('date'))}\n\n"
            f"{bullets_txt}💡 <b>جمع‌بندی:</b>\n{esc(bulletin.get('bottom_line'))}\n\n"
            f"📊 <a href=\"https://itsyebekhe.github.io/rasadai/\">داشبورد زنده</a> | 🆔 @RasadAIOfficial"
        )
        resp2 = self.scraper.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            "chat_id": chat_id, "text": fallback, "parse_mode": "HTML",
            "disable_web_page_preview": True, "reply_markup": reply_markup
        }, timeout=20)
        return resp2.status_code == 200

    def send_digest_to_telegram(self, items):
        token, chat_id = CONFIG['TELEGRAM']['BOT_TOKEN'], CONFIG['TELEGRAM']['CHANNEL_ID']
        if not token or not chat_id or not items:
            return

        def to_farsi_num(n): return str(n).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
        def esc(s): return html.escape(str(s or ''), quote=False)

        now_ir = self._get_tehran_time()
        time_str = to_farsi_num(now_ir.strftime("%H:%M"))
        date_str = to_farsi_num(now_ir.strftime("%Y/%m/%d"))
        base_site = "https://itsyebekhe.github.io/rasadai/"

        # Media Gallery Preparation
        photo_urls = [it['image'] for it in items if self._is_valid_image_url(it.get('image'))]
        if not photo_urls:
            photo_urls = [self._get_fallback_image(items[0].get('title_en', 'news'))]

        media_html = (
            f"<figure><img src=\"{esc(photo_urls[0])}\"/><figcaption>رادار رصد — {time_str}</figcaption></figure>\n"
            if len(photo_urls) == 1 else
            f"<tg-collage>{''.join(f'<img src=\"{esc(u)}\"/>' for u in photo_urls[:4])}<figcaption>تصاویر رویدادهای مهم</figcaption></tg-collage>\n"
        )

        # Market Ticker
        market_html = ""
        try:
            with open(CONFIG['FILES']['MARKET'], 'r', encoding='utf-8') as f:
                mkt = json.load(f)
            market_html = (
                "<table bordered striped>\n"
                "<tr><th>💵 دلار</th><th>🛢 نفت</th><th>⏱ زمان</th></tr>\n"
                f"<tr><td align='center'>{esc(mkt.get('usd'))}</td><td align='center'>{esc(mkt.get('oil'))}</td><td align='center'>{esc(mkt.get('updated'))}</td></tr>\n"
                "</table>\n"
            )
        except Exception:
            pass

        # Headlines List
        headlines_li = []
        for it in items[:10]:
            title = esc(it.get('title_fa') or it.get('title_en'))
            u = it.get('urgency', 3)
            icon = "🔥" if u >= 9 else ("🚨" if u >= 7 else "🔹")
            deep = f"{base_site}?id={it.get('id', '')}"
            headlines_li.append(f"<li>{icon} <a href=\"{esc(deep)}\">{title}</a> <i>({esc(it.get('source'))})</i></li>")
        headlines_html = "<ul>\n" + "\n".join(headlines_li) + "\n</ul>\n"

        # Item Details
        details_parts = []
        for i, it in enumerate(items[:5], 1):
            title = esc(it.get('title_fa') or it.get('title_en'))
            deep = f"{base_site}?id={it.get('id', '')}"
            summary_li = "".join([f"<li>{esc(s)}</li>" for s in it.get('summary', [])])
            details_parts.append(
                f"<details{' open' if i == 1 else ''}>\n"
                f"<summary><b>{to_farsi_num(i)}. {title}</b></summary>\n"
                f"<ul>{summary_li}</ul>\n"
                f"<p>🎯 <b>اثرگذاری:</b> {esc(it.get('impact'))}</p>\n"
                f"<p>🔗 <a href=\"{esc(deep)}\">گزارش تحلیلی</a> | <a href=\"{esc(it.get('url'))}\">منبع خبر</a></p>\n"
                f"</details>\n<hr/>\n"
            )

        full_html = (
            f"<h1>🚨 رادار اخبار مهم و فوری</h1>\n"
            f"<p>⏱ <b>بروزرسانی:</b> {time_str} — {date_str} (تهران)</p>\n"
            f"{market_html}<hr/>\n{media_html}"
            f"<h2>📌 سرخط مهم‌ترین رویدادها</h2>\n{headlines_html}<hr/>\n"
            f"<h2>📋 تحلیل و جزئیات رخدادها</h2>\n{''.join(details_parts)}"
            f"<footer><p>📊 <a href=\"{base_site}\">داشبورد زنده رصد</a> | 🆔 @RasadAIOfficial</p></footer>\n"
        )

        reply_markup = {"inline_keyboard": [[
            {"text": "📊 داشبورد و رادار زنده", "url": base_site},
            {"text": "🛡 پروکسی‌های فعال", "url": "https://itsyebekhe.github.io/MTProtoNexus/"}
        ]]}

        try:
            resp = self.scraper.post(f"https://api.telegram.org/bot{token}/sendRichMessage", json={
                "chat_id": chat_id,
                "rich_message": {"html": full_html[:30000], "is_rtl": True},
                "reply_markup": reply_markup
            }, timeout=30)
            if resp.status_code == 200:
                logger.info(">>> Digest dispatched via Telegram Rich Message.")
                return
        except Exception as e:
            logger.warning(f"Rich message error: {e}")

        # Fallback Photo Dispatch
        caption = f"🚨 <b>رادار اخبار مهم ایران</b>\n⏱ {time_str}\n\n" + "\n".join([
            f"{'🔥' if it.get('urgency',3)>=9 else '🔹'} {esc(it.get('title_fa') or it.get('title_en'))}"
            for it in items[:5]
        ]) + f"\n\n<a href=\"{base_site}\">📊 مشاهده داشبورد تحلیلی</a>"
        self.scraper.post(f"https://api.telegram.org/bot{token}/sendPhoto", json={
            "chat_id": chat_id, "photo": photo_urls[0], "caption": caption[:1024],
            "parse_mode": "HTML", "reply_markup": reply_markup
        }, timeout=20)

    # ───────────────────────── State & Execution Pipeline ─────────────────────────

    def _atomic_json_dump(self, file_path, data):
        dir_name = os.path.dirname(file_path) or '.'
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8') as tf:
                json.dump(data, tf, indent=2, ensure_ascii=False)
                temp_name = tf.name
            os.replace(temp_name, file_path)
        except Exception as e:
            logger.error(f"Atomic file write failed for {file_path}: {e}")
            if temp_name and os.path.exists(temp_name):
                os.remove(temp_name)

    def save_news(self, new_items):
        try:
            all_news = new_items + self.existing_news
            seen_u = set()
            unique_news = []
            for item in all_news:
                u = self._clean_url(item.get('url'))
                if u and u not in seen_u:
                    seen_u.add(u)
                    unique_news.append(item)
            unique_news.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            final_list = unique_news[:CONFIG['HISTORY_SIZE']]
            self._atomic_json_dump(CONFIG['FILES']['NEWS'], final_list)
            return final_list
        except Exception as e:
            logger.error(f"Failed to persist news: {e}")
            return self.existing_news

    def run(self):
        logger.info(">>> Starting Geopolitical Intelligence Radar with Optimized AI Engine...")

        # 1. Update Market Baseline
        self._atomic_json_dump(CONFIG['FILES']['MARKET'], self.fetch_market_rates())

        # 2. Gather candidates
        results = self.get_combined_news()
        cutoff_date = datetime.now(timezone.utc) - timedelta(hours=CONFIG['MAX_NEWS_AGE_HOURS'])
        filtered_candidates = []

        for item in results:
            try:
                p_date = item.get('published date')
                if p_date:
                    dt = parser.parse(p_date)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff_date:
                        continue
            except Exception:
                pass

            clean_u = self._clean_url(item.get('url', ''))
            if clean_u in self.seen_urls:
                continue

            title = item.get('title', '').rsplit(' - ', 1)[0].strip()
            if self._title_hash(title) in self.recent_title_hashes:
                continue

            filtered_candidates.append(item)

        # 3. Sort by priority & Semantic deduplication
        filtered_candidates.sort(key=lambda x: self._domain_score(x.get('url'), x.get('publisher', {}).get('title', '')), reverse=True)

        accepted_candidates = []
        for cand in filtered_candidates:
            t = cand.get('title', '').rsplit(' - ', 1)[0].strip()
            if not self._is_duplicate_hybrid(t, self.existing_news) and not self._is_duplicate_hybrid(t, accepted_candidates):
                accepted_candidates.append(cand)
            if len(accepted_candidates) >= CONFIG['MAX_CANDIDATES']:
                break

        logger.info(f"Candidates selected for deep scrape & AI analysis: {len(accepted_candidates)}")

        # 4. Scrape candidates in parallel
        scraped_items = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['MAX_WORKERS']) as exc:
            future_to_cand = {}
            for idx, cand in enumerate(accepted_candidates):
                raw_title = cand.get('title', '').rsplit(' - ', 1)[0].strip()
                pub = cand.get('publisher', {}).get('title', 'Unknown')
                final_u = self._resolve_final_url(cand.get('url'), raw_title)
                if not final_u:
                    continue
                future_to_cand[exc.submit(self.scrape_article_data, final_u, cand.get('description', raw_title), cand.get('image'))] = (idx, cand, raw_title, pub, final_u)

            for fut in concurrent.futures.as_completed(future_to_cand):
                idx, cand, raw_title, pub, final_u = future_to_cand[fut]
                try:
                    text, photo = fut.result()
                    scraped_items.append({
                        'index': idx, 'cand': cand, 'headline': raw_title,
                        'source': pub, 'url': final_u, 'clean_url': self._clean_url(final_u),
                        'snippet': cand.get('description', raw_title), 'text': text, 'photo': photo
                    })
                except Exception as e:
                    logger.error(f"Scraper thread failed: {e}")

        # 5. Single Batch AI Inference with Strict Schema
        new_processed_items = []
        if scraped_items:
            ai_batch = self.batch_analyze_with_gemini(scraped_items)
            for item in scraped_items:
                ai = ai_batch.get(item['index'])
                if not ai:
                    continue
                try:
                    ts = parser.parse(item['cand'].get('published date')).timestamp()
                except Exception:
                    ts = time.time()

                res = {
                    "id": self._generate_news_id(item['clean_url']),
                    "title_fa": ai.get('title_fa', item['headline']),
                    "title_en": item['headline'],
                    "summary": ai.get('summary', [item['snippet']]),
                    "impact": ai.get('impact', '...'),
                    "tag": ai.get('tag', 'General'),
                    "urgency": int(ai.get('urgency', 3)),
                    "sentiment": float(ai.get('sentiment', 0)),
                    "source": item['source'],
                    "url": item['url'],
                    "clean_url": item['clean_url'],
                    "image": item['photo'],
                    "timestamp": ts
                }
                new_processed_items.append(res)
                self.seen_urls.add(res['clean_url'])
                self.recent_title_hashes.add(self._title_hash(res['title_en']))

        # 6. Save news & Dispatches
        if new_processed_items:
            self.existing_news = self.save_news(new_processed_items)
            self._save_embeddings_cache()

            # Filter for Telegram Broadcast
            urgent_items = [
                it for it in new_processed_items
                if it.get('urgency', 0) >= CONFIG['MIN_TELEGRAM_URGENCY'] or
                (it.get('urgency', 0) >= 6 and any(k in str(it.get('tag')).lower() for k in ['war', 'military', 'strike', 'nuclear', 'نظامی', 'حمله', 'هسته‌ای']))
            ]
            if urgent_items:
                logger.info(f"Dispatching {len(urgent_items)} urgent items to Telegram.")
                self.send_digest_to_telegram(urgent_items)

        # 7. Scheduled Reports
        tehran_now = self._get_tehran_time()
        curr_hour = tehran_now.hour
        today_date_str = tehran_now.strftime("%Y-%m-%d")
        effective_date = (tehran_now - timedelta(days=1)).strftime("%Y-%m-%d") if 0 <= curr_hour < 2 else today_date_str

        # Nightly Special Report (20:00 - 02:00)
        if curr_hour >= 20 or curr_hour < 2:
            slot = f"special_report_night_{effective_date}"
            if not self._is_schedule_already_sent(slot):
                rep = self.generate_special_topic_report()
                if rep and self.send_special_report_to_telegram(rep):
                    self._mark_schedule_as_sent(slot)

        # 23:00 Bulletin (22:00 - 02:00)
        if curr_hour >= 22 or curr_hour < 2:
            b_slot = f"bulletin_23_{effective_date}"
            if not self._is_schedule_already_sent(b_slot):
                blt = self.generate_scheduled_bulletin()
                if blt and self.send_bulletin_to_telegram(blt):
                    self._mark_schedule_as_sent(b_slot)

        # Daily strategic summary saved for local dashboard
        daily_summary = self.generate_daily_summary()
        if daily_summary:
            self._atomic_json_dump(CONFIG['FILES']['DAILY_SUMMARY'], daily_summary)

        logger.info(f">>> Execution completed. Processed: {len(new_processed_items)} items.")


if __name__ == "__main__":
    IranNewsRadar().run()
