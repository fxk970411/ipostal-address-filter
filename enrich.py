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

# 严格检查 Secrets 注入状态，若为空直接阻断报错
if not RAW_AUTH_IDS or not RAW_AUTH_TOKENS:
    print(f"❌ 严重错误: 未检测到有效 Smarty 凭据！RAW_AUTH_IDS 数量: {len(RAW_AUTH_IDS)}。请检查 GitHub Secrets 配置。")
    exit(1)

# 精准拦截词：仅封杀真实商业实体、仓储与快递代收店（不误杀普通 Unit/Apt）
STOREFRONT_KILL_PATTERNS = re.compile(
    r"\b("
    r"Storage|Self\s*Storage|Mini\s*Storage|Vault|"      # 自助仓储
    r"The\s*UPS\s*Store|PostalAnnex|PostNet|Pak\s*Mail|" # 知名快递连锁加盟
    r"Pack\s*(&|and)?\s*Ship|Parcel|Mail\s*Drop|"      # 打包转运铺面
    r"Plaza|Centre|Center|Mall|Galleria|Square|"        # 购物广场/商业中心
    r"Tower|Towers|Corporate\s*Park|Industrial\s*Park|" # 摩天写字楼/工业园
    r"PMB\s*\d+|"                                       # 明确标记为 PMB 盒号
    r"(?:Suite|Ste)\s*[1-9]\d{3,}"                      # 4位数及以上高层商务大厦房号
    r")\b",
    re.IGNORECASE
)

PRIORITY_PLATFORMS = {
    "St. Brendan's Isle", "Texas Home Base", "DakotaPost", 
    "America's Mailbox", "MyRVMail", "Escapees RV Club", 
    "Northwest Agent", "Expansive", "Opus Offices"
}

class SmartyKeyManager:
    def __init__(self, ids, tokens):
        self.pairs = list(zip(ids, tokens))
        self.current_idx = 0
        print(f"✅ 成功装载 {len(self.pairs)} 组 Smarty 凭据")

    def get_current(self):
        if self.current_idx >= len(self.pairs):
            return None, None
        return self.pairs[self.current_idx]

    def switch_next(self):
        self.current_idx += 1
        if self.current_idx < len(self.pairs):
            print(f">>> 切换至备用凭据 [账号 #{self.current_idx + 1}]")
            return True
        return False

key_mgr = SmartyKeyManager(RAW_AUTH_IDS, RAW_AUTH_TOKENS)

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    addresses = json.load(f)

print(f"=== 开始全库智能清洗与 API 校验（总数据: {len(addresses)} 条） ===")

api_queue = []
local_kill = 0

for item in addresses:
    street = item.get("street", "")
    platform = item.get("platform", "")
    full_text = f"{street} {platform}"

    # 1. 明确命中商铺/仓储/广场死刑词：直接本地拦截
    if STOREFRONT_KILL_PATTERNS.search(full_text):
        if item.get("c1_approval_tier") != "Rejected":
            item["rdi"] = "Commercial"
            item["cmra"] = "Yes"
            item["c1_acceptable"] = False
            item["c1_approval_tier"] = "Rejected"
            item["c1_reason"] = "Local Heuristic: Matched Storage / Storefront / Plaza"
        local_kill += 1
        continue

    # 2. 如果已经由 API 成功拿到确切 DPV 结果，且不是脏数据，跳过不重复耗额度
    if item.get("dpv_match_code") in ["Y", "D", "S"] and item.get("cmra") in ["Yes", "No"] and item.get("rdi") in ["Residential", "Commercial"]:
        continue

    # 3. 计算优先级打分（纯门牌号、无四位数大厦特征排在最前）
    score = 50
    if platform in PRIORITY_PLATFORMS:
        score -= 40
    if not re.search(r"\b(suite|ste|unit|#)\b", street, re.IGNORECASE):
        score -= 25
    elif re.search(r"\b(apt|unit|#)\s*([1-9]|[1-9]\d{0,1}[a-z]?)\b", street, re.IGNORECASE):
        score -= 10

    api_queue.append((score, item))

api_queue.sort(key=lambda x: x[0])

print(f"[*] 本地拦截过滤商业/仓储: {local_kill} 条")
print(f"[*] 进入 API 实查队列的高潜候选: {len(api_queue)} 条")

api_called = 0
processed_count = 0
auto_save_counter = 0

for score, item in api_queue:
    auth_id, auth_token = key_mgr.get_current()
    if not auth_id or not auth_token:
        print("⚠️ 凭据配额已全部用完，结束请求。")
        break

    street = item.get("street", "")
    full_addr = f"{street}, {item.get('city')}, {item.get('state')} {item.get('zip')}"
    encoded = urllib.parse.quote_plus(full_addr)

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
        resp = requests.get(API_URL, params=params, timeout=15)
        if resp.status_code in [401, 402]:
            print(f"⚠️ 账号 #{key_mgr.current_idx + 1} 额度已用尽，切换下一组...")
            if key_mgr.switch_next():
                auth_id, auth_token = key_mgr.get_current()
                params["auth-id"] = auth_id
                params["auth-token"] = auth_token
                resp = requests.get(API_URL, params=params, timeout=15)
            else:
                break

        resp.raise_for_status()
        data = resp.json()
        api_called += 1

        if data:
            cand = data[0]
            meta = cand.get("metadata", {})
            analysis = cand.get("analysis", {})

            rdi = meta.get("rdi", "Unknown")
            raw_cmra = analysis.get("dpv_cmra", "")
            cmra = "Yes" if raw_cmra == "Y" else ("No" if raw_cmra == "N" else "Unknown")
            dpv = analysis.get("dpv_match_code", "")

            item["rdi"] = rdi
            item["cmra"] = cmra
            item["dpv_match_code"] = dpv

            line1 = cand.get("delivery_line_1", "").strip()
            line2 = cand.get("delivery_line_2", "").strip()
            item["validated_address"] = f"{line1} {line2}".strip() if line2 else (line1 or street)

            # C1 风控判定
            if cmra == "No" and rdi == "Residential" and dpv in ["Y", "D", "S"]:
                item["c1_acceptable"] = True
                item["c1_approval_tier"] = "High (Residential Non-CMRA)"
                item["c1_reason"] = "USPS Official Clean Residential"
            elif cmra == "No" and rdi == "Commercial" and dpv in ["Y", "D", "S"]:
                item["c1_acceptable"] = True
                item["c1_approval_tier"] = "Medium (Business Non-CMRA)"
                item["c1_reason"] = "USPS Non-CMRA Commercial"
            else:
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Rejected"
                item["c1_reason"] = f"USPS Flag: CMRA={cmra}, RDI={rdi}"

            lat = meta.get("latitude")
            lon = meta.get("longitude")
            if lat and lon:
                item["google_street_view_url"] = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"
            else:
                item["google_street_view_url"] = f"https://www.google.com/maps/@?api=1&map_action=pano&query={encoded}"
        else:
            item["rdi"] = "Unknown"
            item["cmra"] = "Unknown"
            item["c1_acceptable"] = False
            item["c1_approval_tier"] = "Rejected"
            item["c1_reason"] = "Address Not Found in USPS"

        item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded}"

    except Exception as e:
        print(f"查询异常: {street} -> {e}")

    processed_count += 1
    auto_save_counter += 1

    if processed_count % 25 == 0:
        print(f"进度: 已处理 {processed_count}/{len(api_queue)} | API 调用 {api_called} 次 | 当前: {street} -> RDI={item.get('rdi')} CMRA={item.get('cmra')}")

    if auto_save_counter >= 50:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(addresses, f, ensure_ascii=False, indent=2)
        auto_save_counter = 0

    time.sleep(0.08)

# 最终全量落盘
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"\n=== 执行完成！本次实调 API: {api_called} 次，累计处理: {processed_count} 条 ===")
