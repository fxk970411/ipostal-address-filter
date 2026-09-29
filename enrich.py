import json
import os
import time
import urllib.parse
import re
import requests

RAW_AUTH_IDS = [x.strip() for x in os.environ.get("SMARTY_AUTH_ID", "").split(",") if x.strip()]
RAW_AUTH_TOKENS = [x.strip() for x in os.environ.get("SMARTY_AUTH_TOKEN", "").split(",") if x.strip()]

INPUT_FILE = "addresses.json"
OUTPUT_FILE = "addresses.json"
API_URL = "https://us-street.api.smarty.com/street-address"

PRIORITY_PLATFORMS = {
    "St. Brendan's Isle", "Texas Home Base", "DakotaPost", 
    "America's Mailbox", "MyRVMail", "Escapees RV Club", 
    "Northwest Agent", "Expansive", "Opus Offices"
}

STRICT_COMMERCIAL_PATTERNS = re.compile(
    r"\b("
    r"PMB\s*\d+|"
    r"(?:Suite|Ste)\s*[1-9]\d{3,}|"
    r"Floor|Fl\s*\d+|"
    r"Tower|Towers|"
    r"Corporate\s*(?:Park|Center)"
    r")\b",
    re.IGNORECASE
)

FRANCHISE_MAILBOX_KEYWORDS = re.compile(
    r"\b(PostalAnnex|PostNet|Pak\s*Mail|Safe\s*Ship|Mail\s*Boxes|Goin\s*Postal|AIM\s*Mail|The\s*UPS\s*Store)\b",
    re.IGNORECASE
)

class SmartyKeyManager:
    def __init__(self, ids, tokens):
        self.pairs = list(zip(ids, tokens))
        self.current_idx = 0

    def get_current(self):
        if not self.pairs:
            return None, None
        return self.pairs[self.current_idx]

    def switch_next(self):
        if self.current_idx + 1 < len(self.pairs):
            self.current_idx += 1
            print(f">>> 切换到下一组 Smarty 凭据 [账号 #{self.current_idx + 1}]")
            return True
        return False

key_mgr = SmartyKeyManager(RAW_AUTH_IDS, RAW_AUTH_TOKENS)

def evaluate_address_potential(item):
    platform = item.get("platform", "")
    street = item.get("street", "")
    price = item.get("monthly_price", 9.99)
    score = 50

    if platform in PRIORITY_PLATFORMS:
        return -100, f"Priority niche platform: {platform}"

    if STRICT_COMMERCIAL_PATTERNS.search(street):
        return 999, "Commercial tower / PMB heuristic"

    if not re.search(r"\b(suite|ste|unit|#)\b", street, re.IGNORECASE):
        score -= 30
    elif re.search(r"\b(suite|ste|unit|#)\s*[A-E]\b", street, re.IGNORECASE):
        score -= 15
    elif re.search(r"\b(suite|ste|unit|#)\s*([1-9]|[1-2]\d{2})\b", street, re.IGNORECASE):
        score -= 10

    if price and price >= 35.0 and platform not in PRIORITY_PLATFORMS:
        score += 30

    return score, "Candidate"

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    addresses = json.load(f)

addresses.sort(key=lambda x: evaluate_address_potential(x)[0])

api_called = 0
processed_count = 0
auto_save_counter = 0

print(f"=== 开始全量地址校验任务 (待审总库: {len(addresses)} 条) ===")

for idx, item in enumerate(addresses):
    if item.get("rdi") and item.get("cmra") and ("c1_acceptable" in item) and item.get("c1_approval_tier") != "Uncertain":
        continue

    street = item.get("street", "")
    full_addr_str = f"{street}, {item.get('city')}, {item.get('state')} {item.get('zip')}"
    encoded_addr = urllib.parse.quote_plus(full_addr_str)

    score, reason = evaluate_address_potential(item)

    # 1. 商业死刑词：直接本地拦截，0 消耗 API
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

    # 2. 读取当前 Smarty Key 调用
    auth_id, auth_token = key_mgr.get_current()
    if not auth_id or not auth_token:
        print("未检测到有效的 SMARTY_AUTH_ID 或 SMARTY_AUTH_TOKEN，停止调用。")
        break

    params = {
        "auth-id": auth_id,
        "auth-token": auth_token,
        "street": street,
        "city": item.get("city", ""),
        "state": item.get("state", ""),
        "zipcode": item.get("zip", ""),
        "candidates": 1
    }

    try:
        response = requests.get(API_URL, params=params, timeout=20)
        
        # 额度用完自动切号
        if response.status_code in [401, 402]:
            print(f"当前 Smarty 账号配额已耗尽 (HTTP {response.status_code})。")
            if key_mgr.switch_next():
                auth_id, auth_token = key_mgr.get_current()
                params["auth-id"] = auth_id
                params["auth-token"] = auth_token
                response = requests.get(API_URL, params=params, timeout=20)
            else:
                print("所有 Smarty 账号额度均已耗尽，保存当前进度并退出。")
                break

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

            if cmra == "No" and dpv_match in ["Y", "D", "S"]:
                if is_franchise:
                    item["c1_acceptable"] = False
                    item["c1_approval_tier"] = "Review Needed"
                    item["c1_reason"] = "Matched known package store franchise keyword"
                else:
                    item["c1_acceptable"] = True
                    item["c1_approval_tier"] = "High (Residential Non-CMRA)" if rdi == "Residential" else "Medium (Business Non-CMRA)"
                    item["c1_reason"] = "USPS Non-CMRA active delivery point"
            elif cmra == "Yes":
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Rejected"
                item["c1_reason"] = "USPS CMRA Flagged"
            else:
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Uncertain"
                item["c1_reason"] = f"DPV status: {dpv_match}, CMRA: {cmra}"

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
        print(f"校验异常 {street}: {e}")

    processed_count += 1
    auto_save_counter += 1

    if processed_count % 20 == 0:
        print(f"进度: 已处理 {processed_count} 条 | 实际调用 API {api_called} 次 | 当前: {street} -> CMRA={item.get('cmra')} RDI={item.get('rdi')}")

    if auto_save_counter >= 50:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(addresses, f, ensure_ascii=False, indent=2)
        auto_save_counter = 0

    time.sleep(0.08)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"\n=== 全量校验执行完毕！共推进处理 {processed_count} 个地址，累计消耗 API {api_called} 次 ===")
