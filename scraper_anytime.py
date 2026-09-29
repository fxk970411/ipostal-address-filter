import requests
from bs4 import BeautifulSoup
import json
import re
import time

BASE_URL = "https://www.anytimemailbox.com"
USA_LOCATIONS_URL = f"{BASE_URL}/locations/usa"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)

def get_state_urls():
    """获取所有美国州的链接"""
    print("正在获取美国各州列表...")
    resp = session.get(USA_LOCATIONS_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    
    state_links = []
    # 查找所有指向 /locations/usa/xx 的链接
    for a in soup.select('a[href^="/locations/usa/"]'):
        href = a.get("href")
        full_url = BASE_URL + href if href.startswith("/") else href
        if full_url not in state_links and full_url != USA_LOCATIONS_URL:
            state_links.append(full_url)
            
    print(f"共发现 {len(state_links)} 个州的地址页面。")
    return state_links

def scrape_state_locations(state_url):
    """抓取单州所有具体地址及月租价格"""
    resp = session.get(state_url, timeout=30)
    if resp.status_code != 200:
        return []
    
    soup = BeautifulSoup(resp.text, "html.parser")
    locations = []
    
    # Anytime Mailbox 的卡片或列表项解析
    cards = soup.select(".location-card, .listing-card, .location-item, div[class*='location']")
    
    # 正则匹配城市、州简称、邮编 (如: Charlotte, NC 28202)
    csz_pattern = re.compile(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)")
    price_pattern = re.compile(r"\$(\d+(?:\.\d{2})?)")

    for card in cards:
        text = card.get_text("\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        
        # 寻找价格
        found_prices = price_pattern.findall(text)
        price = float(found_prices[0]) if found_prices else 9.99

        # 寻找地址结构
        for idx, line in enumerate(lines):
            match = csz_pattern.match(line)
            if match and idx > 0:
                city = match.group(1).strip()
                state = match.group(2).strip()
                zip_code = match.group(3).strip()
                street = lines[idx - 1].strip()
                
                # 排除无效非地址文本
                if "Mailbox" in street or "Starting" in street or "Locations" in street:
                    continue
                
                locations.append({
                    "street": street,
                    "city": city,
                    "state": state,
                    "zip": zip_code,
                    "monthly_price": price,
                    "source": "Anytime Mailbox"
                })
                break
                
    return locations

def main():
    state_urls = get_state_urls()
    all_addresses = []
    
    for idx, s_url in enumerate(state_urls):
        state_name = s_url.rstrip("/").split("/")[-1]
        print(f"[{idx+1}/{len(state_urls)}] 正在抓取: {state_name}...")
        try:
            locs = scrape_state_locations(s_url)
            all_addresses.extend(locs)
            print(f" -> 抓取到 {len(locs)} 个地址")
        except Exception as e:
            print(f"抓取 {state_name} 失败: {e}")
        time.sleep(0.5)

    # 去重
    unique_addresses = []
    seen = set()
    for item in all_addresses:
        key = (item["street"].lower(), item["city"].lower(), item["state"], item["zip"])
        if key not in seen:
            seen.add(key)
            unique_addresses.append(item)

    # 保存文件
    with open("addresses.json", "w", encoding="utf-8") as f:
        json.dump(unique_addresses, f, ensure_ascii=False, indent=2)

    print(f"\n全部完成！共抓取 Anytime Mailbox 地址 {len(unique_addresses)} 个，已保存至 addresses.json。")

if __name__ == "__main__":
    main()
