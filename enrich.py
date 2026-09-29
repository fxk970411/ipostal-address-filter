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

# 每次执行最多调用的 API 次数（保护月度额度）
BATCH_SIZE = 100

# 1. 明确的商业摩天楼 / 虚拟办公室特征（直接本地剔除，0 消耗 API）
STRICT_COMMERCIAL_PATTERNS = re.compile(
    r"\b("
    r"PMB\s*\d+|"                               # 法定转运信箱
    r"(?:Suite|Ste)\s*[1-9]\d{3,}|"             # 四位数以上大写字间 (如 Suite 1200)
    r"Floor|Fl\s*\d+|"                          # 楼层 (如 4th Floor, Fl 12)
    r"Tower|Towers|"                            # 大厦
    r"Corporate\s*(?:Park|Center)|"             # 科技/企业园区
    r"Executive\s*Suite"                        # 虚拟办公室
    r")\b",
    re.IGNORECASE
)

# 2. 常见隐蔽连锁代收点名称（即便 USPS 库滞后显示 CMRA=No，也进行风控降级）
FRANCHISE_MAILBOX_KEYWORDS = re.compile(
    r"\b(PostalAnnex|PostNet|Pak\s*Mail|Safe\s*Ship|Mail\s*Boxes|Goin\s*Postal|AIM\s*Mail|The\s*UPS\s*Store)\b",
    re.IGNORECASE
)

def evaluate_address_potential(item):
    """
    计算地址住宅潜力分（分数越低，越像低密度民宅或街区小门面，排在越前优先调用 API）
    """
    street = item.get("street", "")
    price = item.get("monthly_price", 9.99)
    score = 50

    # 包含不可逆的死刑词 -> 999 分（直接本地拦截，不调 API）
    if STRICT_COMMERCIAL_PATTERNS.search(street):
        return 999, "Definite commercial tower/PMB"

    # 无任何 Suite/Unit 后缀的纯门牌 -> 潜力最高 (-30 分)
    if not re.search(r"\b(suite|ste|unit|#)\b", street, re.IGNORECASE):
        score -= 30
    
    # 含有字母 Suite (如 Ste A, Unit B) -> 极为常见的平房/排屋分租写法 (-15 分)
    elif re.search(r"\b(suite|ste|unit|#)\s*[A-E]\b", street, re.IGNORECASE):
        score -= 15
        
    # 含有小号 Suite (如 Ste 1, Suite 101) -> 一层低矮沿街房 (-10 分)
    elif re.search(r"\b(suite|ste|unit|#)\s*([1-9]|[1-2]\d{2})\b", street, re.IGNORECASE):
        score -= 10

    # 常见住宅后缀 Ln, Ct, Way, Rd -> 加分项 (-5 分)
    if re.search(r"\b(Ln|Lane|Ct|Court|Way|Dr|Drive|Rd|Road)\b", street, re.IGNORECASE):
        score -= 5

    # 价格过高（$25+）的大概率是 CBD 虚拟办公室，降级
    if price and price >= 25.0:
        score += 40

    return score, "Candidate"

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    addresses = json.load(f)

# 按照住宅潜力分排序，把最有希望过审的地址排在最前面
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

    # 1. 命中死刑词：直接本地拦截，不耗 API 额度
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
        print(f"[SKIP API - Heavy Commercial] {street}")
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
            
            # 细节修复：拼接 line 1 和 line 2，防止遗漏 Suite 分室号
            line1 = candidate.get("delivery_line_1", "").strip()
            line2 = candidate.get("delivery_line_2", "").strip()
            validated_addr = f"{line1} {line2}".strip() if line2 else (line1 or street)
            item["validated_address"] = validated_addr

            # 连锁代收品牌二次过滤
            is_franchise = bool(FRANCHISE_MAILBOX_KEYWORDS.search(street) or FRANCHISE_MAILBOX_KEYWORDS.search(validated_addr))

            # C1 准入评估逻辑
            if cmra == "No" and dpv_match == "Y":
                if is_franchise:
                    item["c1_acceptable"] = False
                    item["c1_approval_tier"] = "Review Needed (Franchise Risk)"
                    item["c1_reason"] = "Matched known package store franchise keyword"
                else:
                    item["c1_acceptable"] = True
                    if rdi == "Residential":
                        item["c1_approval_tier"] = "High (Residential Non-CMRA)"
                    else:
                        item["c1_approval_tier"] = "Medium (Mixed/Office Non-CMRA)"
                    item["c1_reason"] = "USPS: Active delivery point & Non-CMRA"
            elif cmra == "Yes":
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Rejected"
                item["c1_reason"] = "USPS: Registered CMRA"
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
            item["c1_reason"] = "Address not found by USPS"

        item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"

    except Exception as e:
        print(f"Error {street}: {e}")
        item["rdi"] = "Error"
        item["cmra"] = "Error"

    processed_count += 1
    print(f"[API #{api_called}] {street} | CMRA={item.get('cmra')} | RDI={item.get('rdi')} | Tier={item.get('c1_approval_tier')}")

    if api_called >= BATCH_SIZE:
        print(f"Reached batch limit of {BATCH_SIZE} calls.")
        break

    time.sleep(0.15)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"Done. Processed {processed_count} addresses (API calls: {api_called}).")
