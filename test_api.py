import os, json, requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("CRM_TOKEN")
BASE = os.getenv("CRM_BASE")
headers = {"Authorization": f"Bearer {TOKEN}"}

# Who am I? (confirms the token works)
r = requests.get(f"{BASE}/me", headers=headers)
print("ME:", r.status_code, r.json())

# Look at a couple of Bellhaven accounts
r = requests.get(f"{BASE}/accounts", headers=headers,
                 params={"q": "bellhaven", "page_size": 2})
print(json.dumps(r.json(), indent=2))
