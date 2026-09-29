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

# 每次处理的最大 API 额度（你可以按需调整，比如 200 或 500）
BATCH_SIZE = 100

# 1. 绝对一眼商业/高层写字楼死刑词（命中直接标记 CMRA，不耗费 API）
STRICT_COMMERCIAL_PATTERNS = re.compile(
    r"\b("
    r"PMB\s*\d+|"                               # 法定转运信箱
    r"(?:Suite|Ste)\s*[1-9]\d{3,}|"             # 四位数以上大写字间 (如 Suite 1200, Ste 4000)
    r"Floor|Fl\s*\d+|"                          # 楼层 (如 4th Floor, Fl 12)
    r"Tower|Towers|"                            # 大厦
    r"Corporate\s*(?:Park|Center)|"             # 科技/企业园区
    r"Executive\s*Suite"                        # 虚拟办公室专用名
    r")\b",
    re.IGNORECASE
)

def evaluate_address_potential(item):
    """
    计算地址的住宅潜力分（分数越低，越像低密度独立屋/小分租房，排在越前面送检 API）
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

    # 价格筛选：$39.99 几乎都是中心商业区写字楼，给高分降优先级
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
    # 已经具有最终校验结果的跳过
    if item.get("rdi") and item.get("cmra") and "c1_acceptable" in item:
        continue

    street = item.get("street", "")
    full_addr_str = f"{street}, {item.get('city')}, {item.get('state')} {item.get('zip')}"
    encoded_addr = urllib.parse.quote_plus(full_addr_str)

    score, reason = evaluate_address_potential(item)

    # 命中 999 分死刑词：直接本地剔除，省下一次 API 调用！
    if score >= 900:
        item["rdi"] = "Commercial"
        item["cmra"] = "Yes"
        item["dpv_match_code"] = "Heuristic-Reject"
        item["c1_acceptable"] = False
        item["c1_approval_tier"] = "Rejected"
        item["c1_reason"] = f"Filtered: {reason}"
        item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"
        item["google_street_view_url"] = f"https://www.google.com/maps/@?api=1&map_action=pano&query={encoded_addr}"
        processed_count += 1
        print(f"[SKIP API - Dead giveaway] {street}")
        continue

    # 真正有价值的候选地址（包括潜在商住两用 Suite） -> 发往 Smarty 验证
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
            item["validated_address"] = candidate.get("delivery_line_1", street)

            # C1 准入评级
            if cmra == "No" and dpv_match == "Y":
                item["c1_acceptable"] = True
                if rdi == "Residential":
                    item["c1_approval_tier"] = "High (Residential Non-CMRA)"
                else:
                    item["c1_approval_tier"] = "Medium (Mixed/Office Non-CMRA)"
                item["c1_reason"] = "USPS: Non-CMRA & Active delivery point"
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
    print(f"[API {api_called}] {street} | CMRA={item.get('cmra')} | RDI={item.get('rdi')} | Tier={item.get('c1_approval_tier')}")

    if api_called >= BATCH_SIZE:
        print(f"Reached batch limit of {BATCH_SIZE} calls.")
        break

    time.sleep(0.15)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"Completed: processed {processed_count} addresses, used {api_called} API calls.")
