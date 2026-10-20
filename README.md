# bellhaven-sync

Collect Bellhaven community and CRM account data, generate match proposals,
and review proposed changes before any CRM write is made.

## How to run (Windows)

Open PowerShell and run:

```powershell
git clone https://github.com/Lawpantoe/bellhaven-sync.git
cd bellhaven-sync
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `CRM_BASE`, your real `CRM_TOKEN`, and `SITE_BASE`. Keep
`.env` private; it is excluded from Git.

Run collection and proposal generation:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py
```

Start the review app:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local URL printed by Streamlit. CRM changes are attempted only when
you approve an actionable proposal in the app. The app re-fetches the CRM
account before applying a proposal, rejects stale snapshots, and verifies
successful writes. `needs_review` proposals are recorded for manual handling
and do not write to the CRM. The CRM browser link includes the configured token
in its URL.

## How it works

- **Scraper:** visits the community directory, its detail pages, and community
  links on the homepage and About page; saves location details and URL evidence.
- **Matcher:** compares collected locations to CRM accounts and produces
  proposals without writing to the CRM.
- **Review app:** displays proposal details, evidence, and current CRM values;
  approval is required before an applicable CRM write.
- **`state.db`:** local SQLite proposal history. New proposal keys are inserted
  once, so rerunning the pipeline does not reset decisions. Confident matches
  are stored but excluded from the pending review queue.
- **CHOW rule:** when both lifetime revenue and outstanding AR are positive,
  the approved proposal creates a new account under Bellhaven and links the old
  account with `chow_current_account` only. Otherwise, the proposal reparents
  the existing account.

Generated `data/` files and `state.db` are local and git-ignored.

## Daily pipeline scheduling

For a host with cron, this runs the pipeline daily at 6 AM in the host's local
timezone (replace the working directory as needed):

```cron
0 6 * * * cd /path/to/bellhaven-sync && .venv/bin/python run_pipeline.py
```

Alternatively, `.github/workflows/daily.yml` provides a manual
`workflow_dispatch` run on a self-hosted runner. It is not scheduled or active
by default. Install the dependencies on that runner, set `CRM_BASE`,
`CRM_TOKEN`, and `SITE_BASE` in the runner's local environment, and keep its
checkout and ignored `state.db` persistent. The workflow intentionally uses no
GitHub secrets and does not upload CRM data. To run it daily, add an appropriate
schedule after confirming the runner is trusted and online.
