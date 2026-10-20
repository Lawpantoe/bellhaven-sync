import json
from pathlib import Path

from crm import get_all_accounts
from scraper import get_website_locations


def main() -> None:
    accounts = get_all_accounts()
    locations = get_website_locations()

    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    (data_dir / "crm_accounts.json").write_text(
        json.dumps(accounts, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (data_dir / "website_locations.json").write_text(
        json.dumps(locations, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Saved {len(accounts)} CRM accounts to data/crm_accounts.json")
    print(
        f"Saved {len(locations)} website locations to data/website_locations.json"
    )


if __name__ == "__main__":
    main()
