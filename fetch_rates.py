import json
import time
import requests
from bs4 import BeautifulSoup

# Try to use cloudscraper if installed; otherwise fall back to requests.Session
try:
    import cloudscraper
    session = cloudscraper.create_scraper()
except ImportError:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })


def fetch_market_rates():
    data = {
        "usd": "نامشخص",
        "oil": "نامشخص",
        "updated": "--:--"
    }

    # 1. Fetch USD / Toman rates via AED
    try:
        resp_usd = session.get(
            "https://alanchand.com/en/exchange-rates/aed-usd",
            timeout=15
        )
        resp_aed = session.get(
            "https://alanchand.com/en/currencies-price/aed",
            timeout=15
        )

        if resp_usd.status_code == 200 and resp_aed.status_code == 200:
            soup_usd = BeautifulSoup(resp_usd.text, "lxml")
            soup_aed = BeautifulSoup(resp_aed.text, "lxml")

            # Extract AED -> USD rate
            usd_rate_str = None
            usd_input = soup_usd.find("input", id="inputCalcValue") or soup_usd.find("input", id="outputCalcValue")
            if usd_input:
                usd_rate_str = usd_input.get("data-rate")
            if not usd_rate_str:
                dest_span = soup_usd.find(id="destinationAmount")
                if dest_span:
                    usd_rate_str = dest_span.get_text(strip=True)

            # Extract AED price in Iranian Rials
            aed_price_str = None
            aed_input = soup_aed.find("input", attrs={"data-curr": "tmn"})
            if aed_input:
                aed_price_str = aed_input.get("data-price") or aed_input.get("value")
            if not aed_price_str:
                tmn_span = soup_aed.find(id="tmn")
                if tmn_span:
                    aed_price_str = tmn_span.get_text(strip=True)

            # Compute USD / Toman
            if usd_rate_str and aed_price_str:
                aed_usd = float(str(usd_rate_str).replace(",", "").strip())
                aed_toman = float(str(aed_price_str).replace(",", "").strip()) / 10  # IRR to Toman

                if aed_usd > 0:
                    usd_toman = aed_toman / aed_usd
                    data["usd"] = f"{int(round(usd_toman)):,}"

    except Exception as e:
        print(f"Error fetching USD rate: {e}")

    # 2. Fetch Oil price
    try:
        resp_oil = session.get(
            "https://oilprice.com/oil-price-charts/46",
            timeout=15
        )
        if resp_oil.status_code == 200:
            soup_oil = BeautifulSoup(resp_oil.text, "lxml")
            oil_el = soup_oil.select_one(".last_price")
            if oil_el:
                data["oil"] = oil_el.get_text(strip=True)

    except Exception as e:
        print(f"Error fetching Oil price: {e}")

    data["updated"] = time.strftime("%H:%M")
    return data


def main():
    rates = fetch_market_rates()
    print("Fetched rates:", rates)

    with open("market.json", "w", encoding="utf-8") as f:
        json.dump(rates, f, ensure_ascii=False, indent=2)

    print("Updated market.json successfully.")


if __name__ == "__main__":
    main()