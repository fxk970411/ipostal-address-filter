import requests
from bs4 import BeautifulSoup
import json
import re
import time

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)

all_results = []
seen_keys = set()

def add_address(street, city, state, zip_code, price, platform, source_url):
    key = (street.lower().strip(), city.lower().strip(), state.strip(), zip_code.strip())
    if key not in seen_keys and len(street) > 3:
        seen_keys.add(key)
        all_results.append({
            "street": street.strip(),
            "city": city.strip(),
            "state": state.strip(),
            "zip": zip_code.strip(),
            "monthly_price": price,
            "platform": platform,
            "source": source_url
        })

# ==========================================
# 1. 抓取 Escapees RV Club (全美合规常住居住地平台)
# ==========================================
def scrape_escapees():
    print(">>> 正在收录 Escapees RV Club 官方常住地中心...")
    url = "https://www.escapees.com/mail-service/"
    # Escapees 设立了德州法案保护的专属民宅住所地，三大核心免税/免所得税州中心
    escapees_locations = [
        {"street": "100 Rainbow Dr", "city": "Livingston", "state": "TX", "zip": "77399", "price": 13.00},
        {"street": "2067 County Road 472", "city": "Bushnell", "state": "FL", "zip": "33513", "price": 13.00},
        {"street": "11116 US Highway 14A", "city": "Sturgis", "state": "SD", "zip": "57785", "price": 13.00}
    ]
    for loc in escapees_locations:
        add_address(loc["street"], loc["city"], loc["state"], loc["zip"], loc["price"], "Escapees RV Club", url)
        print(f"  [Escapees] {loc['street']}, {loc['city']}, {loc['state']} {loc['zip']}")

# ==========================================
# 2. 抓取 Sasquatch Mail (小众独立平房/写字楼地址)
# ==========================================
def scrape_sasquatch():
    print(">>> 正在抓取 Sasquatch Mail...")
    url = "https://www.sasquatchmail.com/locations/"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text("\n", strip=True)
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            
            csz_pattern = re.compile(r"^([A-Za-z\s]+),\s*([A-Z]{2})\s+(\d{5})")
            
            for i, line in enumerate(lines):
                match = csz_pattern.match(line)
                if match and i > 0:
                    city = match.group(1).strip()
                    state = match.group(2).strip()
                    zip_code = match.group(3).strip()
                    street = lines[i - 1].strip()
                    
                    if "Sasquatch" not in street and "Location" not in street and len(street) < 60:
                        add_address(street, city, state, zip_code, 9.00, "Sasquatch Mail", url)
                        print(f"  [Sasquatch] {street}, {city}, {state} {zip_code}")
    except Exception as e:
        print(f"Sasquatch 抓取失败: {e}")

# ==========================================
# 3. 抓取 Anytime Mailbox (偏远下沉小镇网点)
# ==========================================
def scrape_anytime():
    print(">>> 正在抓取 Anytime Mailbox 核心免税/申卡州...")
    target_states = ["texas", "florida", "wyoming", "nevada", "washington", "north-carolina", "delaware", "south-dakota"]
    
    csz_pattern = re.compile(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)")
    price_pattern = re.compile(r"\$(\d+(?:\.\d{2})?)")

    for state_slug in target_states:
        state_url = f"https://www.anytimemailbox.com/locations/usa/{state_slug}"
        try:
            resp = session.get(state_url, timeout=25)
            if resp.status_code != 200:
                continue
            
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".location-card, .listing-card, div[class*='location']")
            
            count = 0
            for card in cards:
                card_text = card.get_text("\n", strip=True)
                lines = [l.strip() for l in card_text.splitlines() if l.strip()]
                
                prices = price_pattern.findall(card_text)
                price = float(prices[0]) if prices else 9.99

                for idx, line in enumerate(lines):
                    match = csz_pattern.match(line)
                    if match and idx > 0:
                        city = match.group(1).strip()
                        state = match.group(2).strip()
                        zip_code = match.group(3).strip()
                        street = lines[idx - 1].strip()
                        
                        if not any(k in street for k in ["Mailbox", "Starting", "Price", "Locations"]):
                            add_address(street, city, state, zip_code, price, "Anytime Mailbox", state_url)
                            count += 1
                            break
            print(f"  [Anytime] {state_slug.upper()} 抓取到 {count} 个网点")
            time.sleep(0.4)
        except Exception as e:
            print(f"Anytime {state_slug} 失败: {e}")

# ==========================================
# 4. 合并现有 iPostal1 历史数据
# ==========================================
def merge_existing_ipostal():
    try:
        with open("addresses.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
            for item in existing:
                if not item.get("platform"):
                    item["platform"] = "iPostal1"
                key = (item["street"].lower().strip(), item["city"].lower().strip(), item["state"].strip(), item["zip"].strip())
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_results.append(item)
        print(f">>> 已合并已有地址库，当前总数: {len(all_results)}")
    except Exception as e:
        print(f"合并原数据提示: {e}")

if __name__ == "__main__":
    scrape_escapees()
    scrape_sasquatch()
    scrape_anytime()
    merge_existing_ipostal()

    all_results.sort(key=lambda x: (x["state"], x["city"], x["street"]))
    with open("addresses.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n四大平台已全量就绪！累计总地址数: {len(all_results)}")
