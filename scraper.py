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

# =====================================================================
# 1. 注入顶级小众 / 法定常住地 / 高客单价自持办公精确物理门牌
# =====================================================================
def inject_premium_and_niche_seeds():
    print(">>> 正在注入极小众、法定常住地与高端商务自持大楼物理地址...")
    niche_locations = [
        # 1. St. Brendan's Isle (佛州海员/水手法定常住地，零黑产污染)
        {"street": "411 Walnut St", "city": "Green Cove Springs", "state": "FL", "zip": "32043", "price": 13.99, "platform": "St. Brendan's Isle", "url": "https://www.sbimailservice.com"},

        # 2. Texas Home Base (德州免税本土自营)
        {"street": "1530 Midwestern Pkwy", "city": "Wichita Falls", "state": "TX", "zip": "76302", "price": 12.50, "platform": "Texas Home Base", "url": "https://texashomebase.com"},

        # 3. DakotaPost (南达科他免税法定居住地中心)
        {"street": "3916 N Pottstown Ave", "city": "Sioux Falls", "state": "SD", "zip": "57104", "price": 15.00, "platform": "DakotaPost", "url": "https://dakotapost.net"},

        # 4. America's Mailbox (南达科他旅居者合法落户点)
        {"street": "514 Americas Way", "city": "Box Elder", "state": "SD", "zip": "57719", "price": 17.00, "platform": "America's Mailbox", "url": "https://americasmailbox.com"},

        # 5. MyRVMail (Good Sam 官方合作房车俱乐部专属)
        {"street": "2260 S Ferdon Blvd", "city": "Crestview", "state": "FL", "zip": "32536", "price": 19.00, "platform": "MyRVMail", "url": "https://myrvmail.com"},

        # 6. Escapees RV Club (德州法案常住地中心三大基地)
        {"street": "100 Rainbow Dr", "city": "Livingston", "state": "TX", "zip": "77399", "price": 13.00, "platform": "Escapees RV Club", "url": "https://www.escapees.com"},
        {"street": "2067 County Road 472", "city": "Bushnell", "state": "FL", "zip": "33513", "price": 13.00, "platform": "Escapees RV Club", "url": "https://www.escapees.com"},
        {"street": "11116 US Highway 14A", "city": "Sturgis", "state": "SD", "zip": "57785", "price": 13.00, "platform": "Escapees RV Club", "url": "https://www.escapees.com"},

        # 7. Northwest Registered Agent (自购自营实体独栋大楼，核心免税/申卡州)
        {"street": "30 N Gould St Ste N", "city": "Sheridan", "state": "WY", "zip": "82801", "price": 29.00, "platform": "Northwest Agent", "url": "https://www.northwestregisteredagent.com"},
        {"street": "7901 4th St N Ste 300", "city": "St. Petersburg", "state": "FL", "zip": "33702", "price": 29.00, "platform": "Northwest Agent", "url": "https://www.northwestregisteredagent.com"},
        {"street": "522 W Riverside Ave Ste N", "city": "Spokane", "state": "WA", "zip": "99201", "price": 29.00, "platform": "Northwest Agent", "url": "https://www.northwestregisteredagent.com"},
        {"street": "8 The Green Ste A", "city": "Dover", "state": "DE", "zip": "19901", "price": 29.00, "platform": "Northwest Agent", "url": "https://www.northwestregisteredagent.com"},
        {"street": "5900 Balcones Dr Ste 100", "city": "Austin", "state": "TX", "zip": "78731", "price": 29.00, "platform": "Northwest Agent", "url": "https://www.northwestregisteredagent.com"},

        # 8. Expansive (买下整栋产权的甲级自持历史建筑/写字楼)
        {"street": "1801 Main St", "city": "Houston", "state": "TX", "zip": "77002", "price": 49.00, "platform": "Expansive", "url": "https://expansive.com"},
        {"street": "83 S King St", "city": "Seattle", "state": "WA", "zip": "98104", "price": 49.00, "platform": "Expansive", "url": "https://expansive.com"},
        {"street": "1630 Welton St", "city": "Denver", "state": "CO", "zip": "80202", "price": 49.00, "platform": "Expansive", "url": "https://expansive.com"},
        {"street": "200 E Robinson St", "city": "Orlando", "state": "FL", "zip": "32801", "price": 49.00, "platform": "Expansive", "url": "https://expansive.com"},

        # 9. Opus Virtual Offices (高端 $99/mo 固定商务中心)
        {"street": "1000 Brickell Ave", "city": "Miami", "state": "FL", "zip": "33131", "price": 99.00, "platform": "Opus Offices", "url": "https://www.opusvirtualoffices.com"},
        {"street": "539 W Commerce St", "city": "Dallas", "state": "TX", "zip": "75208", "price": 99.00, "platform": "Opus Offices", "url": "https://www.opusvirtualoffices.com"},
        {"street": "304 S Jones Blvd", "city": "Las Vegas", "state": "NV", "zip": "89107", "price": 99.00, "platform": "Opus Offices", "url": "https://www.opusvirtualoffices.com"},
        {"street": "1621 Central Ave", "city": "Cheyenne", "state": "WY", "zip": "82001", "price": 99.00, "platform": "Opus Offices", "url": "https://www.opusvirtualoffices.com"}
    ]

    for item in niche_locations:
        add_address(item["street"], item["city"], item["state"], item["zip"], item["price"], item["platform"], item["url"])

# =====================================================================
# 2. 抓取 Sasquatch Mail
# =====================================================================
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
    except Exception as e:
        print(f"Sasquatch 抓取跳过: {e}")

# =====================================================================
# 3. 抓取 Anytime Mailbox 核心免税/申卡州
# =====================================================================
def scrape_anytime():
    print(">>> 正在抓取 Anytime Mailbox 偏远下沉网点...")
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
                            break
            time.sleep(0.3)
        except Exception as e:
            print(f"Anytime {state_slug} 失败: {e}")

# =====================================================================
# 4. 合并现有历史数据 (如 iPostal1)
# =====================================================================
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
    except Exception as e:
        print(f"合并提示: {e}")

if __name__ == "__main__":
    inject_premium_and_niche_seeds()
    scrape_sasquatch()
    scrape_anytime()
    merge_existing_ipostal()

    with open("addresses.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n全部地址注入完毕，当前总库共有 {len(all_results)} 个地址已就绪！")
