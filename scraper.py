import requests
from bs4 import BeautifulSoup
import json
import re

URL = "https://ipostal1.com/digital-mailbox-addresses.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140 Safari/537.36"
    )
}

response = requests.get(URL, headers=HEADERS, timeout=60)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")
text = soup.get_text("\n", strip=True)

lines = [line.strip() for line in text.splitlines() if line.strip()]

# 匹配美国城市、州简称、邮编
city_pattern = re.compile(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")
unit_pattern = re.compile(r"^(Suite|Ste|Unit|PMB|#)\s*.+$", re.IGNORECASE)
price_pattern = re.compile(r"\$(\d+(?:\.\d{2})?)")

addresses = []

for i, line in enumerate(lines):
    match = city_pattern.match(line)
    if not match:
        continue

    city = match.group(1).strip()
    state = match.group(2).strip()
    zip_code = match.group(3).strip()

    if not state or not zip_code:
        continue

    address_parts = []

    # 向上寻找街道信息
    if i >= 1:
        previous = lines[i - 1].strip()
        if unit_pattern.match(previous):
            address_parts.insert(0, previous)
            if i >= 2:
                street = lines[i - 2].strip()
                if "Virtual Address in" not in street and "Virtual Addresses" not in street:
                    address_parts.insert(0, street)
        else:
            if "Virtual Address in" not in previous and "Virtual Addresses" not in previous:
                address_parts.append(previous)

    street = " ".join(address_parts).strip()
    if not street or "Virtual Address" in street or "Choose a highlighted state" in street:
        continue

    # 在上下文 5 行内搜索价格（例如 $9.99/mo）
    price = 9.99  # iPostal1 默认起步价
    nearby_context = " ".join(lines[max(0, i - 4): min(len(lines), i + 4)])
    found_prices = price_pattern.findall(nearby_context)
    if found_prices:
        # 排除非月租的大额数字，选取符合常见月租的价格
        valid_prices = [float(p) for p in found_prices if float(p) in [9.99, 14.99, 39.99]]
        if valid_prices:
            price = valid_prices[0]
        else:
            price = float(found_prices[0])

    item = {
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "monthly_price": price,
        "source": URL
    }

    # 查重逻辑
    key = (street.lower(), city.lower(), state, zip_code)
    if not any((x["street"].lower(), x["city"].lower(), x["state"], x["zip"]) == key for x in addresses):
        addresses.append(item)

addresses.sort(key=lambda x: (x["state"], x["city"], x["street"]))

with open("addresses.json", "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"Saved {len(addresses)} US addresses with pricing.")
