import json
import os
import time
import requests

AUTH_ID = os.environ["SMARTY_AUTH_ID"]
AUTH_TOKEN = os.environ["SMARTY_AUTH_TOKEN"]

INPUT_FILE = "addresses.json"
OUTPUT_FILE = "addresses.json"

API_URL = "https://us-street.api.smarty.com/street-address"

# 第一次先只处理 50 个，确认没问题后再加大
BATCH_SIZE = 50

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    addresses = json.load(f)

processed = 0

for item in addresses:

    # 已经处理过的跳过
    if item.get("rdi") and item.get("cmra"):
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
        response = requests.get(
            API_URL,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if data:
            candidate = data[0]

            metadata = candidate.get("metadata", {})
            analysis = candidate.get("analysis", {})

            item["rdi"] = metadata.get(
                "rdi",
                "Unknown"
            )

            dpv_cmra = analysis.get(
                "dpv_cmra",
                ""
            )

            if dpv_cmra == "Y":
                item["cmra"] = "Yes"
            elif dpv_cmra == "N":
                item["cmra"] = "No"
            else:
                item["cmra"] = "Unknown"

            item["dpv_match_code"] = analysis.get(
                "dpv_match_code",
                ""
            )

            item["validated_address"] = candidate.get(
                "delivery_line_1",
                ""
            )

        else:
            item["rdi"] = "Unknown"
            item["cmra"] = "Unknown"

    except Exception as e:
        print(
            f"Error processing {item.get('street')}: {e}"
        )

        item["rdi"] = "Error"
        item["cmra"] = "Error"

    processed += 1

    print(
        f"{processed}: "
        f"{item.get('street')} | "
        f"RDI={item.get('rdi')} | "
        f"CMRA={item.get('cmra')}"
    )

    if processed >= BATCH_SIZE:
        break

    time.sleep(0.2)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(
        addresses,
        f,
        ensure_ascii=False,
        indent=2
    )

print(
    f"Finished. Processed {processed} addresses."
)
