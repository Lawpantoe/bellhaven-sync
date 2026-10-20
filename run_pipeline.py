import json
import subprocess
import sys
from pathlib import Path

from review_db import DATABASE_PATH, insert_new_proposals, list_proposals


def _run(script: str) -> None:
    subprocess.run([sys.executable, script], check=True)


def main() -> None:
    _run("collect.py")
    _run("matcher.py")

    proposals_path = Path("data") / "proposals.json"
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    if isinstance(proposals, dict):
        proposals = proposals.get("proposals")
    if not isinstance(proposals, list):
        raise RuntimeError("matcher.py output does not contain a proposals list.")

    counts = insert_new_proposals(proposals, DATABASE_PATH)
    pending_queue = sum(
        row["type"] != "confident_match"
        for row in list_proposals(status="pending", database_path=DATABASE_PATH)
    )
    print(
        "Pipeline summary: "
        f"new={counts['new']}, "
        f"already_decided={counts['already_decided']}, "
        f"pending={pending_queue}, "
        f"already_pending={counts['already_pending']}"
    )


if __name__ == "__main__":
    main()
