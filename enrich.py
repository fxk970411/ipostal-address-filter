import json
import os
import time
import urllib.parse
import re
import requests

AUTH_ID = os.environ.get("SMARTY_AUTH_ID")
AUTH_TOKEN = os.environ.get("SMARTY_AUTH_TOKEN")

INPUT_FILE = "addresses.json"
OUTPUT_FILE = "addresses.json"
API_URL = "https://us-street.api.smarty.com/street-address"

# 每次执行验证的最大 API 调用数 (可设为 100~500)
BATCH_SIZE = 150

# 极小众与高端商务平台清单 (拥有顶级 API 验证优先级)
PRIORITY_PLATFORMS = {
    "St. Brendan's Isle", "Texas Home Base", "DakotaPost", 
    "America's Mailbox", "MyRVMail", "Escapees RV Club", 
    "Northwest Agent", "Expansive", "Opus Offices"
}

# 明确的商业摩天楼 / 虚拟代收特征 (直接本地排除，0 消耗 API)
STRICT_COMMERCIAL_PATTERNS = re.compile(
    r"\b("
    r"PMB\s*\d+|"                               # 显式转运信箱
    r"(?:Suite|Ste)\s*[1-9]\d{3,}|"             # 四位数以上大写字间
    r"Floor|Fl\s*\d+|"                          # 楼层
    r"Tower|Towers|"                            # 大厦
    r"Corporate\s*(?:Park|Center)"              # 科技园区
    r")\b",
    re.IGNORECASE
)

# 常见隐蔽连锁代收点
FRANCHISE_MAILBOX_KEYWORDS = re.compile(
    r"\b(PostalAnnex|PostNet|Pak\s*Mail|Safe\s*Ship|Mail\s*Boxes|Goin\s*Postal|AIM\s*Mail|The\s*UPS\s*Store)\b",
    re.IGNORECASE
)

def evaluate_address_potential(item):
    """
    计算优先级分 (分数越低，越优先调用 Smarty API 校验)
    """
    platform = item.get("platform", "")
    street = item.get("street", "")
    price = item.get("monthly_price", 9.99)
    score = 50

    # 1. 极小众 / 高端自持平台 -> 直接给予最高优先级 (-100 分)
    if platform in PRIORITY_PLATFORMS:
        return -100, f"High priority niche platform: {platform}"

    # 2. 命中死刑词 -> 999 分 (本地拦截，不耗 API)
    if STRICT_COMMERCIAL_PATTERNS.search(street):
        return 999, "Definite commercial tower/PMB"

    # 3. 独立纯门牌 -> (-30 分)
    if not re.search(r"\b(suite|ste|unit|#)\b", street, re.IGNORECASE):
        score -= 30
    # 字母 Suite -> (-15 分)
    elif re.search(r"\b(suite|ste|unit|#)\s*[A-E]\b", street, re.IGNORECASE):
        score -= 15
    # 小号 Suite -> (-10 分)
    elif re.search(r"\b(suite|ste|unit|#)\s*([1-9]|[1-2]\d{2})\b", street, re.IGNORECASE):
        score -= 10

    if price and price >= 35.0 and platform not in PRIORITY_PLATFORMS:
        score += 30

    return score, "Candidate"

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    addresses = json.load(f)

# 排序：极小众平台与高潜力独栋排在最前列
addresses.sort(key=lambda x: evaluate_address_potential(x)[0])

api_called = 0
processed_count = 0

for item in addresses:
    # 已经处理且具有 C1 最终结果的跳过
    if item.get("rdi") and item.get("cmra") and "c1_acceptable" in item:
        continue

    street = item.get("street", "")
    full_addr_str = f"{street}, {item.get('city')}, {item.get('state')} {item.get('zip')}"
    encoded_addr = urllib.parse.quote_plus(full_addr_str)

    score, reason = evaluate_address_potential(item)

    # 1. 命中死刑词：直接本地剔除，省下一次 API
    if score >= 900:
        item["rdi"] = "Commercial"
        item["cmra"] = "Yes"
        item["dpv_match_code"] = "Heuristic-Reject"
        item["c1_acceptable"] = False
        item["c1_approval_tier"] = "Rejected"
        item["c1_reason"] = f"Pre-filtered: {reason}"
        item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"
        item["google_street_view_url"] = f"https://www.google.com/maps/@?api=1&map_action=pano&query={encoded_addr}"
        processed_count += 1
        continue

    # 2. 调用 Smarty 验证高价值候选地址
    params = {
        "auth-id": AUTH_ID,
        "auth-token": AUTH_TOKEN,
        "street": street,
        "city": item.get("city", ""),
        "state": item.get("state", ""),
        "zipcode": item.get("zip", ""),
        "candidates": 1
    }

    try:
        response = requests.get(API_URL, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        api_called += 1

        if data:
            candidate = data[0]
            metadata = candidate.get("metadata", {})
            analysis = candidate.get("analysis", {})

            rdi = metadata.get("rdi", "Unknown")
            item["rdi"] = rdi

            dpv_cmra = analysis.get("dpv_cmra", "")
            cmra = "Yes" if dpv_cmra == "Y" else ("No" if dpv_cmra == "N" else "Unknown")
            item["cmra"] = cmra

            dpv_match = analysis.get("dpv_match_code", "")
            item["dpv_match_code"] = dpv_match
            
            line1 = candidate.get("delivery_line_1", "").strip()
            line2 = candidate.get("delivery_line_2", "").strip()
            item["validated_address"] = f"{line1} {line2}".strip() if line2 else (line1 or street)

            is_franchise = bool(FRANCHISE_MAILBOX_KEYWORDS.search(street) or FRANCHISE_MAILBOX_KEYWORDS.search(item["validated_address"]))

            # C1 准入评估
            if cmra == "No" and dpv_match == "Y":
                if is_franchise:
                    item["c1_acceptable"] = False
                    item["c1_approval_tier"] = "Review Needed"
                    item["c1_reason"] = "Known package store keyword"
                else:
                    item["c1_acceptable"] = True
                    if rdi == "Residential":
                        item["c1_approval_tier"] = "High (Residential Non-CMRA)"
                    else:
                        item["c1_approval_tier"] = "Medium (Business Non-CMRA)"
                    item["c1_reason"] = "USPS Non-CMRA active delivery point"
            elif cmra == "Yes":
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Rejected"
                item["c1_reason"] = "USPS CMRA Flagged"
            else:
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Uncertain"
                item["c1_reason"] = "Incomplete DPV match"

            lat = metadata.get("latitude")
            lon = metadata.get("longitude")
            if lat and lon:
                item["google_street_view_url"] = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"
            else:
                item["google_street_view_url"] = f"https://www.google.com/maps/@?api=1&map_action=pano&query={encoded_addr}"
        else:
            item["rdi"] = "Unknown"
            item["cmra"] = "Unknown"
            item["c1_acceptable"] = False
            item["c1_approval_tier"] = "Rejected"

        item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"

    except Exception as e:
        print(f"Error {street}: {e}")
        item["rdi"] = "Error"
        item["cmra"] = "Error"

    processed_count += 1
    print(f"[{item.get('platform')}] {street} | CMRA={item.get('cmra')} | RDI={item.get('rdi')} | C1={item.get('c1_acceptable')}")

    if api_called >= BATCH_SIZE:
        print(f"达到单次批处理上限: {BATCH_SIZE}")
        break

    time.sleep(0.12)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"处理完成！本次耗费 API: {api_called} 次，总计推进: {processed_count} 条。")
