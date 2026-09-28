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

response = requests.get(
    URL,
    headers=HEADERS,
    timeout=60
)

response.raise_for_status()

soup = BeautifulSoup(
    response.text,
    "html.parser"
)

text = soup.get_text(
    "\n",
    strip=True
)

lines = [
    line.strip()
    for line in text.splitlines()
    if line.strip()
]

# US city/state/ZIP
city_pattern = re.compile(
    r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
)

# Address unit lines
unit_pattern = re.compile(
    r"^(Suite|Ste|Unit|PMB|#)\s*.+$",
    re.IGNORECASE
)

addresses = []

for i, line in enumerate(lines):

    match = city_pattern.match(line)

    if not match:
        continue

    city = match.group(1).strip()
    state = match.group(2).strip()
    zip_code = match.group(3).strip()

    # Only US addresses
    if not state or not zip_code:
        continue

    address_parts = []

    # City line前面一行通常是街道，
    # 如果有 Suite / Ste，则再向前取一行
    if i >= 1:
        previous = lines[i - 1].strip()

        if unit_pattern.match(previous):

            address_parts.insert(
                0,
                previous
            )

            if i >= 2:

                street = lines[i - 2].strip()

                # 排除栏目标题
                if (
                    "Virtual Address in" not in street
                    and "Virtual Addresses" not in street
                ):
                    address_parts.insert(
                        0,
                        street
                    )

        else:

            if (
                "Virtual Address in" not in previous
                and "Virtual Addresses" not in previous
            ):
                address_parts.append(
                    previous
                )

    street = " ".join(
        address_parts
    ).strip()

    if not street:
        continue

    # 排除明显不是地址的数据
    if (
        "Virtual Address" in street
        or "Choose a highlighted state" in street
    ):
        continue

    item = {
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "source": URL
    }

    # 去重
    key = (
        street.lower(),
        city.lower(),
        state,
        zip_code
    )

    if not any(
        (
            x["street"].lower(),
            x["city"].lower(),
            x["state"],
            x["zip"]
        ) == key
        for x in addresses
    ):
        addresses.append(item)


addresses.sort(
    key=lambda x: (
        x["state"],
        x["city"],
        x["street"]
    )
)

with open(
    "addresses.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        addresses,
        f,
        ensure_ascii=False,
        indent=2
    )


print(
    f"Saved {len(addresses)} US addresses"
)
