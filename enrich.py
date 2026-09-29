import json
import os
import time
import urllib.parse
import requests

AUTH_ID = os.environ["SMARTY_AUTH_ID"]
AUTH_TOKEN = os.environ["SMARTY_AUTH_TOKEN"]

INPUT_FILE = "addresses.json"
OUTPUT_FILE = "addresses.json"

API_URL = "https://us-street.api.smarty.com/street-address"

# 每次处理数量（根据需要调整）
BATCH_SIZE = 50

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    addresses = json.load(f)

processed = 0

for item in addresses:
    # 已经处理且具有 C1 判定和街景信息的跳过
    if item.get("rdi") and item.get("cmra") and "c1_acceptable" in item:
        continue

    params = {
        "auth-id": AUTH_ID,
        "auth-token": AUTH_TOKEN,
        "street": item.get("street", ""),
        "city": item.get("city", ""),
        "state": item.get("state", ""),
        "zipcode": item.get("zip", ""),
        "candidates": 1
    }

    try:
        response = requests.get(API_URL, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()

        if data:
            candidate = data[0]
            metadata = candidate.get("metadata", {})
            analysis = candidate.get("analysis", {})

            # 1. 基础邮政属性
            rdi = metadata.get("rdi", "Unknown")
            item["rdi"] = rdi

            dpv_cmra = analysis.get("dpv_cmra", "")
            if dpv_cmra == "Y":
                cmra = "Yes"
            elif dpv_cmra == "N":
                cmra = "No"
            else:
                cmra = "Unknown"
            item["cmra"] = cmra

            dpv_match = analysis.get("dpv_match_code", "")
            item["dpv_match_code"] = dpv_match
            
            validated_addr = candidate.get("delivery_line_1", item.get("street", ""))
            item["validated_address"] = validated_addr

            # 2. C1 预审接受度判定
            if cmra == "No" and dpv_match == "Y":
                item["c1_acceptable"] = True
                if rdi == "Residential":
                    item["c1_approval_tier"] = "High (Residential Non-CMRA)"
                else:
                    item["c1_approval_tier"] = "Medium (Commercial Non-CMRA)"
                item["c1_reason"] = "Valid delivery point and not flagged as CMRA"
            elif cmra == "Yes":
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Rejected"
                item["c1_reason"] = "Failed: Address is registered as CMRA (Virtual Mailbox)"
            else:
                item["c1_acceptable"] = False
                item["c1_approval_tier"] = "Uncertain"
                item["c1_reason"] = "Incomplete DPV or CMRA match"

            # 3. 谷歌地图与街景信息生成
            full_addr_str = f"{validated_addr}, {item.get('city')}, {item.get('state')} {item.get('zip')}"
            encoded_addr = urllib.parse.quote_plus(full_addr_str)
            item["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"

            # 若 Smarty 返回了精确经纬度，使用坐标定位街景；否则使用地址关键词
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

    except Exception as e:
        print(f"Error processing {item.get('street')}: {e}")
        item["rdi"] = "Error"
        item["cmra"] = "Error"
        item["c1_acceptable"] = False
        item["c1_approval_tier"] = "Error"

    processed += 1
    print(
        f"{processed}: {item.get('street')} | "
        f"CMRA={item.get('cmra')} | "
        f"C1_Pass={item.get('c1_acceptable')} | "
        f"Price=${item.get('monthly_price', 9.99)}"
    )

    if processed >= BATCH_SIZE:
        break

    time.sleep(0.2)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(addresses, f, ensure_ascii=False, indent=2)

print(f"Finished. Processed {processed} addresses.")
