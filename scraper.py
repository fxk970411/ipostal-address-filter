import requests
from bs4 import BeautifulSoup
import json
import re

URL = "https://ipostal1.com/digital-mailbox-addresses.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120 Safari/537.36"
    )
}

response = requests.get(
    URL,
    headers=HEADERS,
    timeout=30
)

response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

text = soup.get_text("\n")

lines = [
    line.strip()
    for line in text.splitlines()
    if line.strip()
]

addresses = []

state_zip_pattern = re.compile(
    r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
)

for i, line in enumerate(lines):

    match = state_zip_pattern.match(line)

    if not match:
        continue

    city = match.group(1)
    state = match.group(2)
    zip_code = match.group(3)

    previous_lines = []

    j = i - 1

    while j >= 0 and len(previous_lines) < 3:

        candidate = lines[j]

        if (
            "Virtual Address in" not in candidate
            and not candidate.startswith("Virtual Addresses")
            and not state_zip_pattern.match(candidate)
        ):
            previous_lines.insert(0, candidate)

        j -= 1

    street = " ".join(previous_lines).strip()

    if not street:
        continue

    item = {
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "source": URL
    }

    if item not in addresses:
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
    f"Saved {len(addresses)} addresses "
    "to addresses.json"
)
