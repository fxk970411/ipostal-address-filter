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

# 强力商铺/仓储/快递店死刑词（本地直接剔除，0 消耗 API）
STOREFRONT_KILL_PATTERNS = re.compile(
    r"\b("
    r"Storage|Self\s*Storage|Mini\s*Storage|Vault|"      # 仓储类（假住宅重灾区）
    r"Pack|Ship|Postal|Mail|Parcel|Express|Drop|"      # 打包快递加盟店
    r"Print|Sign|Graphic|Copy|"                        # 图文快印
    r"Auto|Motors|Tire|Repair|Hardware|Lumber|"        # 汽修五金
    r"Pharmacy|Market|Liquor|Grocers|Cleaners|"        # 杂货干洗
    r"Depot|Square|Plaza|Center|Centre|Mall|Galleria|" # 广场商业中心
    r"Parkway|Pkwy|Industrial|Commerce|"               # 工业园区
    r"Suites|Executive|Coworking|Offices|Workspace|"   # 集中写字楼
    r"PMB\s*\d+|Box\s*\d+|"                            # 邮箱代收盒号
    r"(?:Suite|Ste|Unit|Dept|Rm|Room)\s*[1-9]\d{1,}"   # 两位数及以上商业房号
    r")\b",
    re.IGNORECASE
)

RESIDENTIAL_ROAD_TYPES = re.compile(
    r"\b(Lane|Ln|Court|Ct|Circle|Cir|Terrace|Ter|Way|Trail|Trl|Drive|Dr|Place|Pl)\b",
    re.IGNORECASE
)

class SmartyKeyManager:
    def __init__(self, ids, tokens):
        self.pairs = list(zip(ids, tokens))
        self.current_idx = 0
        print(f"[*] 成功装载 {len(self.pairs)} 组 Smarty API 凭据")

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

total_count = len(addresses)
print(f"=== 开始全库 4,000+ 地毯式重筛（总数据量: {total_count} 条） ===")

# ----------------- 阶段 1：本地硬规则秒级全量清洗 -----------------
local_rejected = 0
candidates = []

for item in addresses:
    street = item.get("street", "")
    platform = item.get("platform", "")
    full_str = f"{street} {platform}"

    # 命中任何商业门面/仓储/广场词，立即本地裁决
    if STOREFRONT_KILL_PATTERNS.search(full_str):
        item["rdi"] = "Commercial"
        item["cmra"] = "Yes"
        item["c1_acceptable"] = False
        item["c1_approval_tier"] = "Rejected"
        item["c1_reason"] = "Local Heuristic: Commercial / Storage / Pack & Ship"
        local_rejected += 1
    else:
        # 计算住宅优先权重（纯门牌号优先、低密度道路类型优先）
        weight = 50
        if platform in PRIORITY_PLATFORMS:
            weight -= 40
        if not re.search(r"\b(suite|ste|unit|#|apt)\b", street, re.IGNORECASE):
            weight -= 30  # 纯门牌单栋
        if RESIDENTIAL_ROAD_TYPES.search(street):
            weight -= 20
        candidates.append((weight, item))

print(f"[阶段 1 完成] 本地规则成功拦截剔除: {local_rejected} 条商业/仓储地址（0 消耗 API）")
print(f"[阶段 2 启动] 剩余进入 API 深度核验的真正高潜地址: {len(candidates)} 条")

# ----------------- 阶段 2：高质量候选地址 API 核查 -----------------
candidates.sort(key=lambda x: x[0])  # 最像纯独栋住宅的排在最前先查

api_called = 0
processed_api = 0
auto_save = 0

for weight, item in candidates:
    # 之前如果已经由 API 成功确认过且非模糊状态的，不重复扣额度
    if item.get("dpv_match_code") in ["Y", "D", "S"] and item.get("cmra") in ["Yes", "No"]:
        # 针对之前误判的 7 个假住宅做复核拦截
        if STOREFRONT_KILL_PATTERNS.search(item.get("street", "")):
            item["c1_acceptable"] = False
            item["c1_approval_tier"] = "Rejected"
            item["c1_reason"] = "Revoked: Matched storefront / warehouse pattern"
        continue

    street = item.get("street", "")
    full_addr = f"{street}, {item.get('city')}, {item.get('state')} {item.get('zip')}"
    encoded = urllib.parse.quote_plus(full_addr)

    auth_id, auth_token = key_mgr.get_current()
    if not auth_id or not auth_token:
        print("[!] 警告: 所有 Smarty 凭据额度均已耗尽，中断请求并保存进度。")
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
        resp = requests.get(API_URL, params=params, timeout=15)
        if resp.status_code in [401, 402]:
            print(f"[!] 凭据 #{key_mgr.current_idx + 1} 额度已用尽，准备切号...")
            if key_mgr.switch_next():
                auth_id, auth_token = key_mgr.get_current()
                params["auth-id"] = auth_id
                params["auth-token"] = auth_token
                resp = requests.get(API_URL, params=params, timeout=15)
            else:
                print("[!] 全部凭据额度消耗完毕，保存并退出。")
                break

        resp.raise_for_status()
        data = resp.json()
        api_called += 1

        if data:
            candidate = data[0]
            meta = candidate.get("metadata", {})
            analysis = candidate.get("analysis", {})

            rdi = meta.get("rdi", "Unknown")
            item["rdi"] = rdi

            dpv_cmra = analysis.get("dpv_cmra", "")
            cmra = "Yes" if dpv_cmra == "Y" else ("No" if dpv_cmra == "N" else "Unknown")
            item["cmra"] = cmra

            dpv_match = analysis.get("dpv_match_code", "")
            item["dpv_match_code"] = dpv_match

            # 最终 C1 准入核验
            if cmra == "No" and rdi == "Residential" and dpv_match in ["Y", "D", "S"]:
                item["c1_acceptable"] = True
                item["c1_approval_tier"] = "High (Residential Non-CMRA)"
                item["c1_reason"] = "USPS Official Clean Residential Delivery Point"
            elif cmra == "No" and rdi == "Commercial" and dpv_match in ["Y", "D", "S"]:
                item["c1_acceptable"] = True
                item["c1_approval_tier"] = "Medium (Business Non-CMRA)"
                item["c1_reason"] = "USPS Non-CMRA Commercial Facility"
            else:
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Rejected"
                item["c1_reason"] = f"USPS CMRA={cmra}, RDI={rdi}, DPV={dpv_match}"

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
            item["c1_reason"] = "Address Not Found in USPS Database"

        item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded}"

    except Exception as e:
        print(f"[-] 查询异常: {street} -> {e}")

    processed_api += 1
    auto_save += 1

    if processed_api % 25 == 0:
        print(f"进度: 候选推进 {processed_api}/{len(candidates)} | 消耗 API {api_called} 次 | 当前: {street} -> RDI={item.get('rdi')} CMRA={item.get('cmra')}")

    if auto_save >= 50:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(addresses, f, ensure_ascii=False, indent=2)
        auto_save = 0

    time.sleep(0.08)

# 最终全量落盘
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"\n=== 全量重筛圆满完成！本地清洗 {local_rejected} 条，API 实查 {api_called} 条 ===")
