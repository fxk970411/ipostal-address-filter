import json
import os
import time
import urllib.parse
import re
import requests

CLIENT_ID = os.environ.get("USPS_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("USPS_CLIENT_SECRET", "").strip()

INPUT_FILE = "addresses.json"
OUTPUT_FILE = "addresses.json"

# 官方规范标准生产 Endpoint (注意 apis 带 s)
TOKEN_URL = "https://apis.usps.com/oauth2/v3/token"
STANDARDIZE_URL = "https://apis.usps.com/addresses/v3/address"

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

class USPSAuthManager:
    """遵循 USPS OpenAPI 规范的 Client Credentials 鉴权管理器"""
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None
        self.expire_time = 0

    def get_token(self):
        now = time.time()
        if self.token and now < self.expire_time - 60:
            return self.token

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        self.token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        self.expire_time = now + expires_in
        print(">>> 成功获取 USPS 官方 Access Token")
        return self.token

def evaluate_address_potential(item):
    platform = item.get("platform", "")
    street = item.get("street", "")
    price = item.get("monthly_price", 9.99)
    score = 50

    if platform in PRIORITY_PLATFORMS:
        return -100, f"Priority platform: {platform}"

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

if not CLIENT_ID or not CLIENT_SECRET:
    print("错误: 未检测到 USPS_CLIENT_ID 或 USPS_CLIENT_SECRET，请在 GitHub Secrets 中配置。")
    exit(1)

auth_mgr = USPSAuthManager(CLIENT_ID, CLIENT_SECRET)

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    addresses = json.load(f)

# 优先级排序
addresses.sort(key=lambda x: evaluate_address_potential(x)[0])

processed_count = 0
api_called = 0
auto_save_counter = 0

print(f"=== 开始 USPS 官方接口地址全量核查 (总数据: {len(addresses)} 条) ===")

for item in addresses:
    # 已完成明确核验的跳过，避免重复调用
    if item.get("rdi") and item.get("cmra") and ("c1_acceptable" in item) and item.get("c1_approval_tier") != "Uncertain":
        continue

    street = item.get("street", "")
    full_addr_str = f"{street}, {item.get('city')}, {item.get('state')} {item.get('zip')}"
    encoded_addr = urllib.parse.quote_plus(full_addr_str)

    score, reason = evaluate_address_potential(item)

    # 1. 规则预检拦截
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

    # 2. 调用 USPS 官方 Addresses 3.0 API
    try:
        token = auth_mgr.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        params = {
            "streetAddress": street,
            "city": item.get("city", ""),
            "state": item.get("state", ""),
            "ZIPCode": item.get("zip", "")
        }

        resp = requests.get(STANDARDIZE_URL, headers=headers, params=params, timeout=20)
        api_called += 1

        if resp.status_code == 200:
            data = resp.json()
            addr_info = data.get("address", {})
            extra_info = data.get("additionalInfo", {})

            # 遵循 OpenAPI 规范解析 RDI 属性:
            # 优先根据 DPVUsageCode (A=住宅, B=商业, C=偏住宅) 或 DPVBusiness (N=住宅)
            usage_code = extra_info.get("DPVUsageCode", "").strip()
            dpv_business = extra_info.get("DPVBusiness", "")
            
            if usage_code == "A" or dpv_business == "N":
                rdi = "Residential"
            elif usage_code in ["B", "D"] or dpv_business == "Y":
                rdi = "Commercial"
            elif usage_code == "C":
                rdi = "Residential"  # 商住混合但以住宅为主
            else:
                rdi = "Unknown"
            item["rdi"] = rdi

            # CMRA 标记解析
            raw_cmra = extra_info.get("DPVCMRA", "")
            cmra = "Yes" if raw_cmra == "Y" else ("No" if raw_cmra == "N" else "Unknown")
            item["cmra"] = cmra

            # DPV 确认状态
            dpv_confirm = extra_info.get("DPVConfirmation", "")
            item["dpv_match_code"] = dpv_confirm

            # 检查是否包含 PMB 转运代收盒号
            has_pmb = bool(extra_info.get("parsedPMBDesignator") or extra_info.get("parsedPMBNumber"))

            line1 = addr_info.get("streetAddress", street)
            sec = addr_info.get("secondaryAddress", "")
            item["validated_address"] = f"{line1} {sec}".strip() if sec else line1

            is_franchise = bool(FRANCHISE_MAILBOX_KEYWORDS.search(street) or FRANCHISE_MAILBOX_KEYWORDS.search(item["validated_address"]))

            # C1 风控准入判定
            if cmra == "No" and not has_pmb and dpv_confirm in ["Y", "S", "D"]:
                if is_franchise:
                    item["c1_acceptable"] = False
                    item["c1_approval_tier"] = "Review Needed"
                    item["c1_reason"] = "Matched known package store keyword"
                else:
                    item["c1_acceptable"] = True
                    item["c1_approval_tier"] = "High (Residential Non-CMRA)" if rdi == "Residential" else "Medium (Business Non-CMRA)"
                    item["c1_reason"] = "USPS Official Non-CMRA Delivery Point"
            elif cmra == "Yes" or has_pmb:
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Rejected"
                item["c1_reason"] = "USPS Official CMRA / PMB Flagged"
            else:
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Uncertain"
                item["c1_reason"] = f"DPV: {dpv_confirm}, CMRA: {cmra}"

        elif resp.status_code == 404:
            item["rdi"] = "Unknown"
            item["cmra"] = "Unknown"
            item["c1_acceptable"] = False
            item["c1_approval_tier"] = "Rejected"
            item["c1_reason"] = "Address Not Recognized by USPS"
        else:
            print(f"USPS 返回状态码 {resp.status_code}: {resp.text}")

        item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"
        item["google_street_view_url"] = f"https://www.google.com/maps/@?api=1&map_action=pano&query={encoded_addr}"

    except Exception as e:
        print(f"校验异常 {street}: {e}")

    processed_count += 1
    auto_save_counter += 1

    if processed_count % 20 == 0:
        print(f"进度: 已推进 {processed_count} 条 | API 调用 {api_called} 次 | 当前: {street} -> CMRA={item.get('cmra')} RDI={item.get('rdi')} C1={item.get('c1_acceptable')}")

    # 每验证 50 条自动落盘保存一次
    if auto_save_counter >= 50:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(addresses, f, ensure_ascii=False, indent=2)
        auto_save_counter = 0

    time.sleep(0.08)

# 最终全量落盘
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"\n=== 全量核查完成！共推进处理 {processed_count} 条地址，调用 USPS API {api_called} 次 ===")
